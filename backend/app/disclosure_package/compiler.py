"""End-to-end disclosure-package compiler (Phase 11 plan 11-05 Task 3).

Pipeline:
    1. validate_inputs (preflight) — raises if blocking errors (REQ-D11-008)
    2. compute all formula values inside an audit_context — captures the
       audit log (CONTEXT D-05, D-15; threat T-11-04)
    3. render every GeneratedPage entry to per-template PDF bytes
       (delegates to render.render_template)
    4. concat the per-template PDFs into the intermediate generated.pdf
    5. interleave generated PDFs with StaticAppendix paths in spec.entries
       order, then merge into package.pdf
    6. qpdf_check on package.pdf (REQ-D11-007)
    7. write audit.json beside package.pdf (REQ-D11-011 stub — full
       integration test in plan 11-06)

Threat model (from plan 11-05):
    T-11-04 (audit-log tampering): mitigated — audit.json captures the
        entire input_snapshot and every formula call. A modified
        regeneration with the same inputs produces deterministic
        formula outputs (timestamps differ).
    T-11-05 (path traversal in storage): ACCEPTED at this layer. The
        caller is responsible for sanitizing hoa_id and fiscal_year
        before constructing output_dir. Plan 11-06 (router) is the
        sanitization site. compile_package treats output_dir as a
        trusted Path argument.

Public surface:
    compile_package(*, spec, budget_draft, reserve_snapshot, hoa_metadata,
                    output_dir, appendices_root=None) -> CompileResult
    CompileResult        — pydantic model with output paths + sha256.
    CompileError         — raised when preflight fails or merge errors.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from .audit import audit_context
from .formulas import (
    excess_revenues_over_expenses_operations,
    excess_revenues_over_expenses_replacement,
    expenses_administration_operating,
    expenses_maintenance_operating,
    expenses_replacement,
    expenses_utilities_operating,
    fund_balance_eoy_operations,
    fund_balance_eoy_replacement,
    percent_funded,
    total_estimated_liability,
    total_expenses,
    total_expenses_operations,
    total_revenues_operations,
    total_revenues_replacement,
    total_year_replacement_provision,
    under_funded_balance_per_unit,
    under_funded_balance_total,
)
from .merge import (
    merge_pdfs,
    qpdf_check,
    stamp_absolute_page_numbers,
    write_atomic_bytes,
)
from .preflight import infer_special_assessment_status, validate_inputs
from .render import render_template
from .reconciliation import (
    build_annual_statement_facts,
    parse_optional_decimal_setting,
    resolve_assessment_presentation_facts,
    resolve_assessment_facts,
    resolve_packet_archetype_facts,
    resolve_reserve_funding_facts,
    resolve_reserve_interest_tax_facts,
    resolve_reserve_liability_facts,
)
from .assessment_schedule_matrix import (
    AssessmentScheduleMatrix,
    build_universal_assessment_matrix,
)
from .schemas import (
    BudgetDraft,
    GeneratedPage,
    HOAMetadata,
    PackageSpec,
    PreflightError,
    ReserveStudySnapshot,
    StaticAppendix,
)

logger = logging.getLogger(__name__)

APPENDICES_DIR = Path(__file__).parent / "appendices"

# Templates whose content displays a page number belonging to *another*
# section (TOC entries, the §5570 page-reference grid) — these render
# twice: once in pass 1 (to get a page-count-accurate placeholder render),
# then again in pass 2 once every section's real page count is known.
_PAGE_NUMBER_DEPENDENT_TEMPLATES = {
    "annual_budget_report_toc.html",
    "pro_forma_disclosure_summary.html",
}


def _pdf_page_count(pdf_bytes: bytes) -> int:
    """Real page count of a single rendered-template PDF (task 3.1) —
    replaces the static ``page_count_hint`` estimate for TOC/page-reference
    offset computation."""
    from pypdf import PdfReader

    return len(PdfReader(BytesIO(pdf_bytes)).pages)


def _humanize_filename_title(filename: str) -> str:
    """Fallback TOC label for an appendix with no operator-set display
    title (static/ad-hoc appendices — DB-manifest appendices always carry
    a real ``display_title``). Purely mechanical (strip extension,
    separators -> spaces, title-case); not tuned to any specific file
    name, so it degrades gracefully for any HOA's uploaded appendix set."""
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    words = re.split(r"[_\-]+", stem)
    return " ".join(w for w in words if w).strip().title() or filename


_LOGO_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
}


def _hoa_logo_data_uri(logo_filename: Optional[str]) -> Optional[str]:
    """Resolve a stored per-HOA logo to an inline base64 data URI, or
    ``None`` when unset/missing so ``_base.html`` falls back to the
    default inline mark (task 2.3). See the "Per-HOA logo" comment at the
    call site for why this is a data URI rather than a file:// <img src>.
    """
    if not logo_filename:
        return None
    from app.services import hoa_logo_storage

    if not hoa_logo_storage.hoa_logo_exists(logo_filename):
        logger.warning(
            "compiler: hoa_settings.logo_filename=%r does not exist on disk; "
            "falling back to default logo", logo_filename,
        )
        return None
    import base64

    path = hoa_logo_storage.hoa_logo_path(logo_filename)
    mime = _LOGO_MIME_TYPES.get(path.suffix.lower())
    if mime is None:
        logger.warning(
            "compiler: unrecognized logo file extension %r; falling back "
            "to default logo", path.suffix,
        )
        return None
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


class CompileError(RuntimeError):
    """Raised when compile_package cannot produce a valid PDF.

    Carries the full structured PreflightError list so the caller can
    surface human-readable messages and suggested fixes in the UI.
    ``field_paths`` is kept as a derived convenience property for back-compat.
    """

    def __init__(
        self,
        message: str,
        *,
        field_paths: Optional[list[str]] = None,
        errors: Optional[list[PreflightError]] = None,
    ):
        super().__init__(message)
        self._errors: list[PreflightError] = list(errors or [])
        self._field_paths_override: Optional[list[str]] = field_paths

    @property
    def errors(self) -> list[PreflightError]:
        return self._errors

    @property
    def field_paths(self) -> list[str]:
        if self._field_paths_override is not None:
            return self._field_paths_override
        return [e.field_path for e in self._errors]


class CompileResult(BaseModel):
    """Successful compile_package return value."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    output_path: Path
    audit_path: Path
    intermediate_path: Path
    page_count: int
    sha256: str
    completed_at: str


# ─────────────────────────────────────────────────────────────────────────────
# Internal — formula composition
# ─────────────────────────────────────────────────────────────────────────────


_HORIZON_YEARS = 30


def _per_component_expenditures(
    components: Any, fiscal_year_start: int
) -> list[dict[str, Any]]:
    """One dict per reserve-study component with current-dollar expenditures by year.

    Real Old Mill convention (drifting-puzzling-grove rebuild):
        - Replacement events fall at offsets {RL, RL+UL, RL+2·UL, ...} truncated
          to < 30. The last replacement event in the window is the largest such
          offset ≤ 29.
        - At each event, the cell shows the component's *current* replacement
          cost — NO inflation applied. (Inflation only enters the cash-flow
          aggregate; see ``_aggregate_expenditures_inflated``.)
        - Components with remaining_life ≥ 30 contribute zero events but still
          appear in the table (empty expenditure columns, Total = 0).

    Header rows (``row_type == 'header'`` from the reserve-study extractor)
    pass through as dicts with ``is_header: True`` so the major-component
    schedule template can render them as full-width section breaks.
    """
    out: list[dict[str, Any]] = []
    for c in components or []:
        # Surface upstream header rows verbatim — major_component_schedule.html
        # renders them as a colspan'd section title.
        if getattr(c, "is_header", False) or getattr(c, "row_type", None) == "header":
            out.append({
                "is_header": True,
                "line_item": str(getattr(c, "line_item", "") or ""),
                "expenditures_by_year": [Decimal("0")] * _HORIZON_YEARS,
                "total_expenditures": Decimal("0"),
            })
            continue

        useful_life = getattr(c, "useful_life", None)
        replacement_cost = getattr(c, "replacement_cost", None)
        remaining_life = getattr(c, "remaining_life", None)
        line_item = str(getattr(c, "line_item", "") or "")

        expenditures = [Decimal("0")] * _HORIZON_YEARS
        total = Decimal("0")

        if useful_life and replacement_cost is not None:
            ul = int(useful_life)
            cost = Decimal(str(replacement_cost))
            rl = int(remaining_life) if remaining_life is not None else ul
            offset = max(rl, 0)
            while offset < _HORIZON_YEARS:
                expenditures[offset] = cost
                total += cost
                if ul <= 0:
                    break
                offset += ul

        year_provision = (
            (Decimal(str(replacement_cost)) / Decimal(useful_life)).quantize(Decimal("1"))
            if useful_life and replacement_cost is not None
            else Decimal("0")
        )
        # Liability already accrued: cost * (UL - RL) / UL. Clamp at 0 when
        # remaining_life exceeds useful_life (zero-age component).
        if useful_life and replacement_cost is not None and remaining_life is not None:
            accrued_fraction = max(
                Decimal("0"),
                (Decimal(useful_life) - Decimal(remaining_life)) / Decimal(useful_life),
            )
            estimated_liability = (Decimal(str(replacement_cost)) * accrued_fraction).quantize(
                Decimal("1")
            )
        else:
            estimated_liability = Decimal("0")

        out.append({
            "is_header": False,
            "line_item": line_item,
            "useful_life": int(useful_life) if useful_life else 0,
            "remaining_life": int(remaining_life) if remaining_life is not None else 0,
            "replacement_cost": Decimal(str(replacement_cost)) if replacement_cost is not None else Decimal("0"),
            "year_replacement_provision": year_provision,
            "estimated_liability_through_year_25": estimated_liability,
            "year_new": getattr(c, "year_new", None),
            "expenditures_by_year": expenditures,
            "total_expenditures": total,
        })
    return out


def _aggregate_expenditures_inflated(
    per_component: list[dict[str, Any]], inflation: Decimal
) -> list[Decimal]:
    """Sum per-component current-dollar expenditures by year, inflating each
    year's sum to nominal dollars (cost × (1 + inflation)^offset).

    This is the cash-flow forecast's "Repair and replacement costs" row —
    the only place inflation enters the 30-year section. Per-component
    cells remain in current dollars.
    """
    inflated = [Decimal("0")] * _HORIZON_YEARS
    for offset in range(_HORIZON_YEARS):
        factor = (Decimal("1") + inflation) ** offset
        current_dollar_sum = sum(
            (row["expenditures_by_year"][offset] for row in per_component if not row.get("is_header")),
            Decimal("0"),
        )
        inflated[offset] = (current_dollar_sum * factor).quantize(Decimal("1"))
    return inflated


def _bracket_rate_for_year(year: int, schedule: list[dict[str, Any]]) -> Decimal:
    """Return the assessment-increase rate that applies to ``year``.

    ``schedule`` is the parsed ``assessment_increase_schedule_json`` list of
    ``{start_year, end_year, rate}``. Returns Decimal("0") if no bracket
    matches (i.e. the schedule has gaps or ends before ``year``).
    """
    for bracket in schedule or []:
        start = int(bracket.get("start_year") or 0)
        end = int(bracket.get("end_year") or 0)
        if start <= year <= end:
            return Decimal(str(bracket.get("rate") or 0))
    return Decimal("0")


def _cash_flow_rows(
    *,
    units: int,
    base_monthly_per_unit: Decimal,
    assessment_schedule: list[dict[str, Any]],
    special_assessments_per_unit_by_year: list[Decimal],
    reserve_cash_balance_eoy_prior: Decimal,
    interest_rate: Decimal,
    inflation: Decimal,
    replacement_costs_by_year_inflated: list[Decimal],
    board_deferrals_by_year: list[Decimal],
    fiscal_year_start: int,
) -> dict[str, Any]:
    """Build the pivoted cash-flow matrix that the cash-flow panel template renders.

    Returns a dict with these length-30 list[Decimal] keys plus scalar fields.
    """
    years = [fiscal_year_start + i for i in range(_HORIZON_YEARS)]
    number_of_units = [Decimal(units)] * _HORIZON_YEARS
    after_tax_interest_decimal = [interest_rate.quantize(Decimal("0.001"))] * _HORIZON_YEARS

    # Walk the escalation schedule year-by-year. The base value applies to
    # year 0; subsequent years compound at the bracket rate for that year.
    monthly_per_unit_by_year: list[Decimal] = []
    monthly = base_monthly_per_unit
    for offset in range(_HORIZON_YEARS):
        if offset > 0:
            rate = _bracket_rate_for_year(years[offset], assessment_schedule)
            monthly = monthly * (Decimal("1") + rate)
        monthly_per_unit_by_year.append(monthly.quantize(Decimal("0.01")))

    regular_assessments = [
        (monthly_per_unit_by_year[i] * Decimal(units) * Decimal(12)).quantize(Decimal("1"))
        for i in range(_HORIZON_YEARS)
    ]
    special_assessments_row = [
        (special_assessments_per_unit_by_year[i] * Decimal(units)).quantize(Decimal("1"))
        for i in range(_HORIZON_YEARS)
    ]
    repair_replacement_costs = [v.quantize(Decimal("1")) for v in replacement_costs_by_year_inflated]
    board_approved_deferral = [v.quantize(Decimal("1")) for v in board_deferrals_by_year]

    # Cash flow chain — interest is computed on the average balance, matching
    # standard reserve-study convention and the prior _build_thirty_year_plan
    # implementation.
    interest_income: list[Decimal] = []
    total_cash_receipts: list[Decimal] = []
    total_cash_disbursements: list[Decimal] = []
    cash_flow_deficiency: list[Decimal] = []
    cash_balance_beginning: list[Decimal] = []
    cash_balance_end: list[Decimal] = []

    balance = reserve_cash_balance_eoy_prior
    for i in range(_HORIZON_YEARS):
        receipts_excl_interest = regular_assessments[i] + special_assessments_row[i]
        disbursements = repair_replacement_costs[i] - board_approved_deferral[i]
        avg_balance = balance + (receipts_excl_interest - disbursements) / Decimal("2")
        interest = (avg_balance * interest_rate).quantize(Decimal("1"))
        receipts_total = receipts_excl_interest + interest
        deficiency = receipts_total - disbursements

        cash_balance_beginning.append(balance.quantize(Decimal("1")))
        interest_income.append(interest)
        total_cash_receipts.append(receipts_total)
        total_cash_disbursements.append(disbursements)
        cash_flow_deficiency.append(deficiency)

        balance = balance + deficiency
        cash_balance_end.append(balance.quantize(Decimal("1")))

    # Bracket label rows for the template ("Annual assmnt increase rate
    # at 3.0% thru 2035", etc.).
    increase_brackets = [
        {
            "start_year": int(b.get("start_year") or 0),
            "end_year": int(b.get("end_year") or 0),
            "rate": Decimal(str(b.get("rate") or 0)),
        }
        for b in (assessment_schedule or [])
    ]

    return {
        "years": years,
        "number_of_units": number_of_units,
        "replace_fund_assmnt_per_unit_per_mo": monthly_per_unit_by_year,
        "replace_fund_special_assmnt_per_unit_per_yr": [
            v.quantize(Decimal("0.01")) for v in special_assessments_per_unit_by_year
        ],
        "after_tax_interest_decimal": after_tax_interest_decimal,
        "regular_assessments": regular_assessments,
        "special_assessments_row": special_assessments_row,
        "interest_income": interest_income,
        "total_cash_receipts": total_cash_receipts,
        "repair_replacement_costs": repair_replacement_costs,
        "board_approved_deferral": board_approved_deferral,
        "total_cash_disbursements": total_cash_disbursements,
        "cash_flow_deficiency": cash_flow_deficiency,
        "cash_balance_beginning": cash_balance_beginning,
        "cash_balance_end": cash_balance_end,
        "increase_brackets": increase_brackets,
        "after_tax_interest_rate": interest_rate,
        "replacement_cost_increase_rate": inflation,
    }


def _legacy_funding_plan(
    *,
    cash_flow: dict[str, Any],
    per_component: list[dict[str, Any]],
    inflation: Decimal,
    total_estimated_liability_now: Decimal,
) -> list[dict[str, Any]]:
    """Derive the legacy ``thirty_year_funding_plan`` list-of-dicts shape so
    ``pro_forma_disclosure_summary.html:165`` keeps working without changes.

    Keys: year, beginning_balance, annual_contribution, annual_expenditure,
    interest, ending_balance, estimated_liability, percent_funded.
    """
    rows: list[dict[str, Any]] = []
    for offset in range(_HORIZON_YEARS):
        factor = (Decimal("1") + inflation) ** offset
        liability = (total_estimated_liability_now * factor).quantize(Decimal("1"))
        ending = cash_flow["cash_balance_end"][offset]
        pct = (
            (ending / liability * Decimal("100")).quantize(Decimal("1"))
            if liability
            else Decimal("0")
        )
        rows.append({
            "year": cash_flow["years"][offset],
            "beginning_balance": int(cash_flow["cash_balance_beginning"][offset]),
            "annual_contribution": int(cash_flow["regular_assessments"][offset]),
            "annual_expenditure": int(cash_flow["repair_replacement_costs"][offset]),
            "interest": int(cash_flow["interest_income"][offset]),
            "ending_balance": int(ending),
            "estimated_liability": int(liability),
            "percent_funded": int(pct),
        })
    return rows


def _build_thirty_year_plan(
    *,
    spec: PackageSpec,
    hoa_metadata: HOAMetadata,
    components: Any,  # Sequence[ReserveStudyComponent]
    total_estimated_liability: Decimal,
    total_year_replacement_provision: Decimal,
    cash_eoy_prior: Decimal,
    fiscal_year_start: int,
    inflation_rate: Optional[Decimal] = None,
    interest_rate: Optional[Decimal] = None,
    assessment_schedule: Optional[list[dict[str, Any]]] = None,
    base_replacement_fund_monthly_per_unit: Optional[Decimal] = None,
    special_assessments: Optional[list[dict[str, Any]]] = None,
    board_deferrals: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """30-year reserve funding study outputs (drifting-puzzling-grove rebuild).

    Returns a dict with three keys:
        thirty_year_cash_flow:
            Pivoted matrix (rows × 30 cols) consumed by the cash-flow panel
            template. See ``_cash_flow_rows`` for the row vocabulary.
        major_component_expenditure_schedule:
            list[dict] — one row per reserve-study component, with a length-30
            ``expenditures_by_year`` array in *current* dollars (no inflation),
            consumed by the major-component schedule template.
        thirty_year_funding_plan:
            list[dict] — back-compat shape preserved for
            ``pro_forma_disclosure_summary.html:165``.

    Real Old Mill 2026 convention: per-component cells = current dollars,
    cash-flow "Repair and replacement costs" = inflated nominal dollars.
    """
    inflation = (
        inflation_rate
        if inflation_rate is not None
        else spec.static_data.replacement_cost_increase_rate or Decimal("0.03")
    )
    rate = (
        interest_rate
        if interest_rate is not None
        else spec.static_data.interest_rate_after_tax or Decimal("0.018")
    )

    per_component = _per_component_expenditures(components, fiscal_year_start)
    inflated_aggregate = _aggregate_expenditures_inflated(per_component, inflation)

    # Base replacement-fund monthly per-unit: operator override wins, else
    # derive from (annual provision / units / 12).
    if base_replacement_fund_monthly_per_unit is not None and base_replacement_fund_monthly_per_unit > 0:
        base_monthly = Decimal(str(base_replacement_fund_monthly_per_unit)).quantize(Decimal("0.01"))
    elif hoa_metadata.units > 0 and total_year_replacement_provision > 0:
        base_monthly = (
            Decimal(str(total_year_replacement_provision))
            / Decimal(hoa_metadata.units)
            / Decimal(12)
        ).quantize(Decimal("0.01"))
    else:
        base_monthly = Decimal("0.00")

    # Special assessments and board deferrals: map operator-entered structured
    # lists into length-30 per-year arrays.
    special_per_unit_by_year = [Decimal("0")] * _HORIZON_YEARS
    for entry in special_assessments or []:
        year = entry.get("year")
        per_unit = entry.get("per_unit") or entry.get("amount_per_unit") or entry.get("amount")
        if year is None or per_unit is None:
            continue
        offset = int(year) - fiscal_year_start
        if 0 <= offset < _HORIZON_YEARS:
            special_per_unit_by_year[offset] = Decimal(str(per_unit))

    deferrals_by_year = [Decimal("0")] * _HORIZON_YEARS
    for entry in board_deferrals or []:
        year = entry.get("year")
        amount = entry.get("amount")
        if year is None or amount is None:
            continue
        offset = int(year) - fiscal_year_start
        if 0 <= offset < _HORIZON_YEARS:
            deferrals_by_year[offset] = Decimal(str(amount))

    cash_flow = _cash_flow_rows(
        units=hoa_metadata.units,
        base_monthly_per_unit=base_monthly,
        assessment_schedule=assessment_schedule or [],
        special_assessments_per_unit_by_year=special_per_unit_by_year,
        reserve_cash_balance_eoy_prior=cash_eoy_prior,
        interest_rate=rate,
        inflation=inflation,
        replacement_costs_by_year_inflated=inflated_aggregate,
        board_deferrals_by_year=deferrals_by_year,
        fiscal_year_start=fiscal_year_start,
    )

    legacy = _legacy_funding_plan(
        cash_flow=cash_flow,
        per_component=per_component,
        inflation=inflation,
        total_estimated_liability_now=total_estimated_liability or Decimal("0"),
    )

    return {
        "thirty_year_cash_flow": cash_flow,
        "major_component_expenditure_schedule": per_component,
        "thirty_year_funding_plan": legacy,
    }


def _normalize_special_assessment_for_render(entry: dict[str, Any]) -> dict[str, Any]:
    """Give a special-assessment entry every field the disclosure templates read.

    The disclosure templates (cover_letter.html, pro_forma_disclosure_summary.html)
    run under Jinja StrictUndefined, so a *missing* key raises rather than rendering
    blank. Operators may enter a basic row with only some fields, and may use alternate
    key names (``description``/``purpose`` for the label, ``per_unit``/``amount`` for the
    amount). Normalize by meaning — derive the canonical keys from any alias and default
    the rest — so a minimally-filled row never crashes the render.
    """
    normalized = dict(entry)
    normalized["status"] = infer_special_assessment_status(normalized)
    normalized["label"] = entry.get("label") or entry.get("description") or entry.get("purpose")

    amount = entry.get("amount_per_unit")
    if amount is None:
        amount = entry.get("per_unit")
    if amount is None:
        amount = entry.get("amount")
    try:
        normalized["amount_per_unit"] = float(amount) if amount is not None else 0.0
    except (TypeError, ValueError):
        normalized["amount_per_unit"] = 0.0

    normalized.setdefault("due_date", None)
    normalized.setdefault("display_language", None)
    normalized.setdefault("purpose", None)
    normalized.setdefault("frequency", None)
    normalized.setdefault("included_in_regular_monthly", False)
    return normalized


def _compute_all(
    spec: PackageSpec,
    budget_draft: BudgetDraft,
    reserve_snapshot: ReserveStudySnapshot,
    hoa_metadata: HOAMetadata,
    effective_hoa_settings: Optional[dict[str, Any]] = None,
    assessment_matrix: Optional[AssessmentScheduleMatrix] = None,
) -> dict[str, Any]:
    """Materialize every value the templates reference.

    All formula calls happen inside the active audit_context so each one
    is recorded exactly once in the audit log (re-entrancy guard in
    audit.py keeps nested decorated calls from double-recording).

    The ``effective_hoa_settings`` dict (built once in compile_package
    by overlaying per-HOA overrides on spec.static_data) is consulted for
    every config-style numeric input — reserve cash balance, fund
    balance BOY, prior-year monthly assessment, after-tax interest rate,
    replacement-cost inflation. This keeps the audit log, the formula
    inputs, and the rendered template all reading the same value.
    """
    settings = effective_hoa_settings or {}

    def _setting_decimal(name: str) -> Optional[Decimal]:
        """Pull a numeric setting as Decimal, falling back to spec.static_data."""
        raw = settings.get(name)
        if raw is None:
            raw = getattr(spec.static_data, name)
        return parse_optional_decimal_setting(raw)
    operating_lis = [li for li in budget_draft.line_items if not li.is_reserve]
    reserve_lis = [li for li in budget_draft.line_items if li.is_reserve]

    # Section-grouped expenses & revenues — keyed on the raw Excel header
    # (LineItem.section) so the income-statement template can render
    # whatever sections the data provides instead of keyword-matching
    # labels against a hardcoded bucket list.
    expenses_by_section: dict[str, dict[str, Any]] = {}
    for li in operating_lis:
        if li.is_revenue:
            continue
        section_name = (li.section or "Uncategorized").strip()
        bucket = expenses_by_section.setdefault(
            section_name, {"rows": [], "total": Decimal(0)}
        )
        bucket["rows"].append({"label": li.label, "amount": li.amount or Decimal(0)})
        bucket["total"] = bucket["total"] + (li.amount or Decimal(0))

    revenues_by_section: dict[str, dict[str, Any]] = {}
    for li in operating_lis:
        if not li.is_revenue:
            continue
        section_name = (li.section or "Operating Income").strip()
        bucket = revenues_by_section.setdefault(
            section_name, {"rows": [], "total": Decimal(0)}
        )
        bucket["rows"].append({"label": li.label, "amount": li.amount or Decimal(0)})
        bucket["total"] = bucket["total"] + (li.amount or Decimal(0))

    total_rev_op = total_revenues_operations(operating_line_items=operating_lis)
    total_rev_rep = total_revenues_replacement(reserve_line_items=reserve_lis)
    # The keyword-matched formulas below keep firing so the audit log captures
    # values for the legacy section labels ('Maintenance and operations', etc.),
    # but they are NOT the source of truth for the income-statement total —
    # sections in the active draft may use any label the operator's parser
    # produced. Real total is summed from `expenses_by_section` below.
    exp_maint = expenses_maintenance_operating(operating_line_items=operating_lis)
    exp_util = expenses_utilities_operating(operating_line_items=operating_lis)
    exp_admin = expenses_administration_operating(operating_line_items=operating_lis)
    exp_rep = expenses_replacement(reserve_line_items=reserve_lis)
    total_exp_op = sum(
        (Decimal(str(b["total"])) for b in expenses_by_section.values()),
        Decimal("0"),
    )
    total_exp_all = total_expenses(operations=total_exp_op, replacement=exp_rep)
    excess_op = excess_revenues_over_expenses_operations(
        revenues=total_rev_op, expenses=total_exp_op
    )
    excess_rep = excess_revenues_over_expenses_replacement(
        revenues=total_rev_rep, expenses=exp_rep
    )

    total_liab = total_estimated_liability(components=reserve_snapshot.components)
    total_prov = total_year_replacement_provision(components=reserve_snapshot.components)

    # Data gaps: track every value that fell back to a default because the
    # operator's draft / settings did not supply it. Surfaced at the top of
    # the cover letter so the PDF cannot silently mislead the board.
    data_gaps: list[str] = []

    if not reserve_snapshot.components:
        data_gaps.append(
            "No reserve study components found — replacement provision, "
            "30-year funding plan, and percent funded all default to $0/0%."
        )
    if not reserve_snapshot.study_date:
        data_gaps.append(
            "Reserve study date is unknown — Notes show a blank date. "
            "Set 'reserve_study_date' under Disclosure Settings, or re-upload "
            "the reserve study so the date is auto-extracted."
        )

    cash_setting = parse_optional_decimal_setting(settings.get("reserve_cash_balance_eoy_prior"))
    if cash_setting is None:
        cash = Decimal("0")
        data_gaps.append(
            "Disclosure setting 'reserve_cash_balance_eoy_prior' is unset — "
            "reserve cash balance treated as $0 for funding calculations."
        )
    else:
        cash = cash_setting
    fund_balance_boy_op = _setting_decimal("fund_balance_boy_operations") or Decimal("0")
    pct = percent_funded(cash_reserves=cash, estimated_liability=total_liab)
    under_total = under_funded_balance_total(
        estimated_liability=total_liab, cash_reserves=cash
    )
    under_per_unit = under_funded_balance_per_unit(
        estimated_liability=total_liab,
        cash_reserves=cash,
        units=hoa_metadata.units,
    )

    reserve_funding_facts = resolve_reserve_funding_facts(
        funding_source=settings.get("reserve_funding_source"),
        manual_annual_amount=settings.get("reserve_funding_manual_amount"),
        budget_line_items=budget_draft.line_items,
        reserve_funding_plan_rows=reserve_snapshot.funding_plan_rows,
        component_annual_provision=total_prov or Decimal("0"),
        units=hoa_metadata.units,
        fiscal_year=spec.fiscal_year,
    )
    data_gaps.extend(reserve_funding_facts.warnings)
    monthly_replacement_contribution_total = reserve_funding_facts.monthly_total
    base_2026_monthly = reserve_funding_facts.monthly_per_unit

    assessment_facts = resolve_assessment_facts(
        budget_line_items=operating_lis,
        approved_monthly_assessment_per_unit=settings.get("approved_monthly_assessment_per_unit"),
        units=hoa_metadata.units,
    )
    monthly_assessment_per_unit_current = assessment_facts.monthly_assessment_per_unit_current
    annual_assessment_revenue = assessment_facts.approved_annual_assessment_revenue
    data_gaps.extend(assessment_facts.warnings)
    if assessment_facts.source == "missing":
        data_gaps.append(
            "No operating revenue line item with 'assessment' in its label — "
            "monthly assessment per unit could not be derived from the active draft. "
            "Set 'approved_monthly_assessment_per_unit' under Disclosure Settings to override."
        )

    packet_archetype_facts = resolve_packet_archetype_facts(
        packet_archetype_setting=settings.get("financial_packet_archetype"),
    )

    # Pro-forma footnote counts — currently a strict subset of the reserve
    # study. Phase 12 adds a board-deferral / signed-contracts admin form;
    # for now we surface the one we can derive (useful_life missing) and
    # default the others to 0 so the rendered page reads cleanly.
    useful_life_not_disclosed_count = sum(
        1 for c in reserve_snapshot.components
        if not c.useful_life or int(c.useful_life) <= 0
    )

    reserve_interest_tax_facts = resolve_reserve_interest_tax_facts(
        reserve_interest_income_override=settings.get("reserve_interest_income_override"),
        income_tax_provision_override=settings.get("income_tax_provision_override"),
        budget_line_items=budget_draft.line_items,
        reserve_funding_plan_rows=reserve_snapshot.funding_plan_rows,
        fiscal_year=spec.fiscal_year,
    )
    data_gaps.extend(reserve_interest_tax_facts.warnings)
    interest_revenue_total = reserve_interest_tax_facts.reserve_interest_income
    income_tax_provision = reserve_interest_tax_facts.reserve_tax_provision

    # Parse Priority-A structured inputs (special assessments / additional
    # assessments / outstanding loan) from JSON-stored settings. Templates
    # iterate over the lists; empty list → render "None scheduled" wording;
    # outstanding_loan == None → render "no loans" wording.
    def _parse_json_list(name: str) -> list[dict[str, Any]]:
        raw = settings.get(name)
        if raw in (None, "", "[]"):
            return []
        if isinstance(raw, list):
            return raw
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    def _parse_json_object(name: str) -> Optional[dict[str, Any]]:
        raw = settings.get(name)
        if raw in (None, ""):
            return None
        if isinstance(raw, dict):
            return raw
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, TypeError):
            return None

    special_assessments = []
    for entry in _parse_json_list("special_assessments_json"):
        if not isinstance(entry, dict):
            continue
        special_assessments.append(_normalize_special_assessment_for_render(entry))
    additional_assessments_needed = _parse_json_list("additional_assessments_needed_json")
    outstanding_loan = _parse_json_object("outstanding_loan_json")

    # 30-year reserve funding study inputs (drifting-puzzling-grove rebuild).
    # When the settings JSON is unset, fall back to spec.static_data's
    # assessment_increase_schedule (list[tuple[int,int,Decimal]]).
    assessment_schedule_raw = _parse_json_list("assessment_increase_schedule_json")
    if not assessment_schedule_raw and getattr(spec.static_data, "assessment_increase_schedule", None):
        assessment_schedule_raw = [
            {"start_year": int(s), "end_year": int(e), "rate": float(r)}
            for s, e, r in spec.static_data.assessment_increase_schedule
        ]
    board_deferrals_list = _parse_json_list("board_deferrals_json")
    replacement_fund_monthly_raw = settings.get("replacement_fund_monthly_assessment_per_unit")
    base_replacement_fund_monthly = parse_optional_decimal_setting(
        replacement_fund_monthly_raw
    )

    prior_assessment_raw = settings.get("monthly_assessment_per_unit_prior")
    if prior_assessment_raw is not None:
        try:
            prior_assessment = Decimal(str(prior_assessment_raw)).quantize(Decimal("0.01"))
        except (ArithmeticError, ValueError):
            prior_assessment = None
    else:
        prior_assessment = None
    presentation_facts = resolve_assessment_presentation_facts(
        recipient_grain=assessment_matrix.recipient_grain if assessment_matrix else None,
        monthly_assessment_per_unit_current=monthly_assessment_per_unit_current,
        monthly_assessment_per_unit_prior=prior_assessment,
    )
    assessment_change_phrase = presentation_facts.assessment_change_phrase

    reserve_liability_facts = resolve_reserve_liability_facts(
        cash_reserve_balance_eoy_prior=cash,
        total_estimated_liability=total_liab,
        under_funded_balance_total=under_total,
        under_funded_balance_per_unit=under_per_unit,
        percent_funded=pct,
        annual_replacement_provision=total_prov,
    )
    other_operating_revenue = sum(
        (
            Decimal(li.amount or 0)
            for li in operating_lis
            if li.is_revenue
            and li.label
            and "assessment" not in li.label.lower()
            and "interest" not in li.label.lower()
        ),
        Decimal("0"),
    )
    other_replacement_revenue = sum(
        (
            Decimal(li.amount or 0)
            for li in reserve_lis
            if li.is_revenue and li.label and "interest" not in li.label.lower()
        ),
        Decimal("0"),
    )
    annual_statement_facts = build_annual_statement_facts(
        packet_archetype=packet_archetype_facts.archetype,
        total_regular_assessment_revenue=annual_assessment_revenue,
        reserve_assessment_revenue=reserve_funding_facts.annual_contribution,
        reserve_interest_income=reserve_interest_tax_facts.reserve_interest_income,
        reserve_tax_provision=reserve_interest_tax_facts.reserve_tax_provision,
        other_operating_revenue=other_operating_revenue,
        other_replacement_revenue=other_replacement_revenue,
        total_operating_expenses=total_exp_op,
        beginning_balance_operations=fund_balance_boy_op,
        reserve_liability_facts=reserve_liability_facts,
    )

    return {
        "computed": {
            "total_revenues_operations": total_rev_op,
            "total_revenues_replacement": total_rev_rep,
            "total_revenues": total_rev_op + total_rev_rep,
            "operating_revenues": [li for li in operating_lis if li.is_revenue],
            "replacement_revenues": [li for li in reserve_lis if li.is_revenue],
            "operating_expenses": [li for li in operating_lis if not li.is_revenue],
            "replacement_expenses": [li for li in reserve_lis if not li.is_revenue],
            "expenses_maintenance_operating": exp_maint,
            "expenses_utilities_operating": exp_util,
            "expenses_administration_operating": exp_admin,
            "total_expenses_operations": total_exp_op,
            "total_expenses_replacement": exp_rep,
            "total_expenses": total_exp_all,
            "excess_revenues_over_expenses_operations": excess_op,
            "excess_revenues_over_expenses_replacement": excess_rep,
            "fund_balance_eoy_operations": fund_balance_eoy_operations(
                beginning_balance=fund_balance_boy_op,
                excess=excess_op,
            ),
            "fund_balance_eoy_replacement": fund_balance_eoy_replacement(
                cash_balance_eoy_prior=cash, excess=excess_rep
            ),
            "total_estimated_liability": total_liab,
            "total_year_replacement_provision": total_prov,
            "percent_funded": pct,
            "under_funded_balance_total": under_total,
            "under_funded_balance_per_unit": under_per_unit,
            "monthly_replacement_contribution_per_unit_2026": base_2026_monthly,
            "monthly_replacement_revenue_total": total_rev_rep,
            "monthly_replacement_contribution_total": monthly_replacement_contribution_total,
            "monthly_assessment_per_unit_current": monthly_assessment_per_unit_current,
            "annual_assessment_revenue": Decimal(annual_assessment_revenue) if annual_assessment_revenue else Decimal(0),
            "interest_revenue_total": Decimal(interest_revenue_total) if interest_revenue_total else Decimal(0),
            "income_tax_provision": income_tax_provision,
            "useful_life_not_disclosed_count": useful_life_not_disclosed_count,
            "board_deferral_count": 0,
            "signed_contracts_count": 0,
            "reserve_components": [
                {
                    "line_item": c.line_item,
                    "useful_life": c.useful_life,
                    "remaining_life": c.remaining_life,
                    "year_new": c.year_new,
                    "replacement_cost": c.replacement_cost,
                    "year_replacement_provision": (
                        c.replacement_cost / c.useful_life if c.useful_life else 0
                    ),
                    "estimated_liability": (
                        c.replacement_cost
                        * (c.useful_life - c.remaining_life)
                        / c.useful_life
                        if c.useful_life
                        else 0
                    ),
                }
                for c in reserve_snapshot.components
            ],
            # Plan 11-06 / 11-09 may extend; placeholder for templates that
            # reference the field. StrictUndefined fails loudly if a
            # template references a missing key, so always emit the slot.
            "thirty_year_projections": [],
            **_build_thirty_year_plan(
                spec=spec,
                hoa_metadata=hoa_metadata,
                components=reserve_snapshot.components,
                total_estimated_liability=total_liab,
                total_year_replacement_provision=total_prov,
                cash_eoy_prior=cash,
                fiscal_year_start=spec.fiscal_year,
                inflation_rate=_setting_decimal("replacement_cost_increase_rate"),
                interest_rate=_setting_decimal("interest_rate_after_tax"),
                assessment_schedule=assessment_schedule_raw,
                base_replacement_fund_monthly_per_unit=(
                    base_replacement_fund_monthly
                    if base_replacement_fund_monthly is not None
                    else reserve_funding_facts.monthly_per_unit
                ),
                special_assessments=special_assessments,
                board_deferrals=board_deferrals_list,
            ),
            "data_gaps": data_gaps,
            # Priority-A structured inputs (drifting-puzzling-grove) — templates
            # iterate over these. Lists are empty when the operator hasn't
            # provided any → templates render "None scheduled" / "None at this
            # time". outstanding_loan is None when no loan exists → templates
            # render the "no outstanding loans" boilerplate.
            "special_assessments": special_assessments,
            "additional_assessments_needed": additional_assessments_needed,
            "outstanding_loan": outstanding_loan,
            "packet_archetype_facts": packet_archetype_facts.model_dump(),
            "presentation_facts": presentation_facts.model_dump(),
            "reserve_funding_source": reserve_funding_facts.source,
            "reserve_funding_source_label": reserve_funding_facts.source_label,
            "reserve_funding_facts": reserve_funding_facts.model_dump(),
            "reserve_interest_tax_facts": reserve_interest_tax_facts.model_dump(),
            "reserve_liability_facts": reserve_liability_facts.model_dump(),
            "annual_statement_facts": annual_statement_facts.model_dump(),
            "assessment_facts": assessment_facts.model_dump(),
            "assessment_change_phrase": assessment_change_phrase,
            "assessment_change_disclosure": (
                "0%"
                if spec.static_data.monthly_assessment_per_unit_current
                == spec.static_data.monthly_assessment_per_unit_prior
                else "increase"
            ),
            "expenses_by_section": {k: {
                "rows": [
                    {"label": it["label"], "amount": float(it["amount"] or 0)}
                    for it in v["rows"]
                ],
                "total": float(v["total"]),
            } for k, v in expenses_by_section.items()},
            "revenues_by_section": {k: {
                "rows": [
                    {"label": it["label"], "amount": float(it["amount"] or 0)}
                    for it in v["rows"]
                ],
                "total": float(v["total"]),
            } for k, v in revenues_by_section.items()},
        },
        "reserve_study_snapshot": reserve_snapshot,
        "budget_draft": budget_draft,
        "hoa_metadata": hoa_metadata,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public — orchestrator
# ─────────────────────────────────────────────────────────────────────────────


def compile_package(
    *,
    spec: PackageSpec,
    budget_draft: BudgetDraft,
    reserve_snapshot: ReserveStudySnapshot,
    hoa_metadata: HOAMetadata,
    output_dir: Path,
    appendices_root: Optional[Path] = None,
    hoa_settings_overrides: Optional[dict] = None,
    extra_appendix_paths: Optional[list[Path]] = None,
    extra_appendix_titles: Optional[dict[str, str]] = None,
    assessment_matrix: Optional[AssessmentScheduleMatrix] = None,
) -> CompileResult:
    """Run the full disclosure-package compilation pipeline.

    Args:
        spec: PackageSpec (e.g. OLD_MILL_2026).
        budget_draft: typed budget input from adapters.budget_draft_from_record.
        reserve_snapshot: typed reserve-study input from
            adapters.reserve_snapshot_from_extraction.
        hoa_metadata: typed HOA input from adapters.hoa_metadata_from_property.
        output_dir: directory where ``package.pdf``, ``generated.pdf`` and
            ``audit.json`` will be written. The router (plan 11-06) is
            responsible for sanitizing hoa_id / fiscal_year before
            building this path; compile_package treats it as trusted.
        appendices_root: directory containing static appendix files.
            Defaults to ``compiler.APPENDICES_DIR``. In the DRE-driven
            architecture the per-HOA appendix manifest is driven by the
            ``appendix_documents`` + ``annual_package_appendices`` tables
            (see ``compile_inputs.resolve_compile_appendix_paths``); this
            fallback only matters for legacy callers + tests.

    Returns:
        CompileResult with output paths, page count, and SHA-256 of the
        bytes on disk.

    Raises:
        CompileError: preflight returned a blocking error. The
            ``field_paths`` attribute lists the offending fields.
        RuntimeError: ``qpdf --check`` rejected the merged output.

    Static appendices are best-effort: missing files are skipped with a
    log warning, and any extra PDFs in ``appendices_root`` (filenames not
    in ``spec.entries``) are appended after the spec entries in sorted
    order. This lets operators drop ad-hoc appendix PDFs in without
    updating the PackageSpec.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if appendices_root is None:
        appendices_root = APPENDICES_DIR

    # 1. Preflight gate (REQ-D11-008) — fail fast with field paths.
    errors = validate_inputs(
        spec=spec,
        budget_draft=budget_draft,
        reserve_snapshot=reserve_snapshot,
        hoa_metadata=hoa_metadata,
        appendices_root=appendices_root,
        hoa_settings_overrides=hoa_settings_overrides,
    )
    blocking = [e for e in errors if e.severity == "blocking"]
    if blocking:
        raise CompileError(
            f"Preflight blocked compilation: {len(blocking)} error(s)",
            errors=blocking,
        )
    if assessment_matrix is not None:
        matrix_blocking = [
            issue for issue in assessment_matrix.preflight_issues
            if issue.severity == "blocking"
        ]
        if matrix_blocking:
            raise CompileError(
                f"Preflight blocked compilation: {len(matrix_blocking)} error(s)",
                errors=matrix_blocking,
            )

    # 2. Build effective hoa_settings dict — spec.static_data defaults
    #    overlaid with any operator-saved overrides from the hoa_settings
    #    table (Phase 11 plan 11-08 Task 4). Templates and the audit log
    #    consume this dict in preference to spec.static_data so a settings
    #    edit changes the rendered package without a code change.
    # Effective hoa_settings: text/contact defaults from spec.static_data
    # are fine fallbacks (they don't lie about money). Numeric fields that
    # represent operator-required disclosure inputs (cash balance, fund
    # balance, prior assessment) are NOT pre-loaded — they must come from
    # the operator-saved hoa_settings row. Otherwise the data-gap detection
    # in `_compute_all` can't tell whether the operator supplied a value.
    # Rate defaults (interest, inflation) stay pre-loaded since they have
    # sensible spec defaults (1.8% / 3%) and operators rarely change them.
    effective_hoa_settings: dict[str, Any] = {
        "management_company": spec.static_data.management_company,
        "management_company_address": spec.static_data.management_company_address,
        "management_company_phone": "650.210.0085",
        "management_company_fax": "650.210.0086",
        "management_company_web": "www.3state.net",
        "cpa_firm_name": spec.static_data.cpa_firm_name,
        "cpa_firm_address": spec.static_data.cpa_firm_address,
        "reserve_study_expert_name": spec.static_data.reserve_study_expert_name,
        "reserve_study_date": "",
        "interest_rate_after_tax": float(spec.static_data.interest_rate_after_tax),
        "replacement_cost_increase_rate": float(spec.static_data.replacement_cost_increase_rate),
        "letter_signed_by": spec.static_data.letter_signed_by,
        # Phase 1 boilerplate fields — pre-seeded as None so Jinja StrictUndefined
        # template lookups (e.g. `hoa_settings.letter_date`) resolve cleanly to
        # None and the templates fall through to their `or` defaults instead of
        # raising UndefinedError. Operator-set values overlay these below.
        "letter_date": None,
        "letter_signed_by_title": None,
        "accountant_report_date": None,
        "reserve_funding_plan_date": None,
        "hoa_state": None,
        "hoa_entity_type": None,
        "hoa_incorporation_year": None,
        # Priority-A settings — same pre-seed for template safety.
        "approved_monthly_assessment_per_unit": None,
        "financial_packet_archetype": "dual-fund",
        "reserve_interest_income_override": None,
        "income_tax_provision_override": None,
        "reserve_funding_source": "auto",
        "reserve_funding_manual_amount": None,
        "special_assessments_json": "[]",
        "additional_assessments_needed_json": "[]",
        "outstanding_loan_json": None,
        "monthly_assessment_per_unit_prior": float(spec.static_data.monthly_assessment_per_unit_prior),
        # Numeric template-input pre-seeds. Several templates format these
        # directly via ``'{:,.0f}'.format(hoa_settings.X)`` with no ``or 0``
        # fallback, so a missing/None value raises a StrictUndefined
        # TypeError at render time. Operator-saved values overlay these.
        "reserve_cash_balance_eoy_prior": 0.0,
        "fund_balance_boy_operations": 0.0,
        "management_company_phone": "",
        "management_company_fax": "",
        "management_company_web": "",
    }
    if hoa_settings_overrides:
        for key, value in hoa_settings_overrides.items():
            if value is not None:
                effective_hoa_settings[key] = value

    # 2b. Per-HOA logo (task 2.3): resolve the stored file to an inline
    #     base64 data URI rather than a file:// <img src>. render.py's
    #     url_fetcher (T-11-03/_deny_url_fetcher) only permits local file:
    #     paths INSIDE the templates directory — a per-HOA upload living
    #     under BUDGET_STORAGE_ROOT is outside that sandbox by design, so a
    #     direct file:// reference would be rejected. Embedding as a data
    #     URI avoids ever needing a network/file fetch during render.
    hoa_logo_data_uri = _hoa_logo_data_uri(effective_hoa_settings.get("logo_filename"))

    # 3. Pre-compute section-grouped expenses/revenues so we can capture
    #    them in the audit input_snapshot (the snapshot is serialized at
    #    audit_context open time, so they cannot be added retroactively
    #    from inside _compute_all). _compute_all also surfaces them under
    #    `computed` for the income-statement template to iterate over.
    _operating_lis = [li for li in budget_draft.line_items if not li.is_reserve]
    _expenses_by_section: dict[str, dict[str, Any]] = {}
    for _li in _operating_lis:
        if _li.is_revenue:
            continue
        _section = (_li.section or "Uncategorized").strip()
        _bucket = _expenses_by_section.setdefault(
            _section, {"rows": [], "total": 0.0}
        )
        _amount = float(_li.amount or 0)
        _bucket["rows"].append({"label": _li.label, "amount": _amount})
        _bucket["total"] = _bucket["total"] + _amount
    _revenues_by_section: dict[str, dict[str, Any]] = {}
    for _li in _operating_lis:
        if not _li.is_revenue:
            continue
        _section = (_li.section or "Operating Income").strip()
        _bucket = _revenues_by_section.setdefault(
            _section, {"rows": [], "total": 0.0}
        )
        _amount = float(_li.amount or 0)
        _bucket["rows"].append({"label": _li.label, "amount": _amount})
        _bucket["total"] = _bucket["total"] + _amount

    # 4. Capture the input snapshot for the audit log (CONTEXT D-15).
    input_snapshot: dict[str, Any] = {
        "spec_hoa_id": spec.hoa_id,
        "fiscal_year": spec.fiscal_year,
        "budget_draft": budget_draft.model_dump(mode="json"),
        "reserve_snapshot": reserve_snapshot.model_dump(mode="json"),
        "hoa_metadata": hoa_metadata.model_dump(mode="json"),
        "static_data": spec.static_data.model_dump(mode="json"),
        "hoa_settings": effective_hoa_settings,
        "assessment_matrix": (
            assessment_matrix.model_dump(mode="json") if assessment_matrix else None
        ),
        "expenses_by_section": _expenses_by_section,
        "revenues_by_section": _revenues_by_section,
    }

    audit_log_ref = None
    intermediate_pdfs: list[Path] = []

    with audit_context(input_snapshot) as audit_log:
        audit_log_ref = audit_log

        # 3. Compute every formula value (each call is recorded once by
        #    the @audit_formula decorators).
        computed = _compute_all(
            spec, budget_draft, reserve_snapshot, hoa_metadata,
            effective_hoa_settings=effective_hoa_settings,
            assessment_matrix=assessment_matrix,
        )
        if assessment_matrix is None:
            # Backward-compatible fallback for tests and legacy callers.
            # The live render job passes a DB-backed matrix built from the
            # approved AssessmentSetup before entering compile_package.
            from app.assessment_engine import CalcResultSet

            assessment_matrix = build_universal_assessment_matrix(
                CalcResultSet(
                    pool_allocations=[],
                    recipient_totals=[],
                    rounding_delta_annual=Decimal("0"),
                    rounding_delta_monthly=Decimal("0"),
                    rounding_delta_percent=Decimal("0"),
                    pool_sum_annual=Decimal("0"),
                ),
                setup_type="fixed",
                hoa_name=hoa_metadata.name,
                fiscal_year=spec.fiscal_year,
                approved_visual_basis=False,
                manual_review_reason=(
                    "Assessment matrix was not supplied by the render job."
                ),
            )
        ctx_full: dict[str, Any] = {
            "spec": spec,
            "static_data": spec.static_data,
            "fiscal_year": spec.fiscal_year,
            "hoa": hoa_metadata,  # Property-record-derived; templates should
                                  # prefer hoa.name over static_data.hoa_legal_name
                                  # so the rendered name reflects the live DB row.
            "matrix": assessment_matrix,
            "today": datetime.now(timezone.utc).strftime("%A %B %-d, %Y"),
            "today_iso": datetime.now(timezone.utc).date().isoformat(),
            "hoa_settings": effective_hoa_settings,
            "hoa_logo_data_uri": hoa_logo_data_uri,
            **computed,
        }

        # 3b. Resolve the appendix merge order + display titles ONCE, before
        #     any rendering — this is the single source of truth both the
        #     TOC page-number computation below and the final merge order
        #     (step 5) read from, so the two can never drift apart.
        #     Static (spec-declared) appendices keep their position relative
        #     to spec.entries (interleaving with GeneratedPage entries is
        #     supported, even though no shipped PackageSpec does this
        #     today); ad-hoc directory extras and DB-manifest appendices
        #     always trail after everything else, matching the previous
        #     5b/5c/5d behavior.
        extra_appendix_titles = extra_appendix_titles or {}
        spec_appendix_paths: dict[str, tuple[Path, str]] = {}
        seen_appendix_names: set[str] = set()
        for entry in spec.entries:
            if isinstance(entry, StaticAppendix):
                appendix_path = appendices_root / entry.file
                if appendix_path.exists():
                    title = extra_appendix_titles.get(entry.file) or _humanize_filename_title(entry.file)
                    spec_appendix_paths[entry.file] = (appendix_path, title)
                    seen_appendix_names.add(entry.file)
                else:
                    logger.info(
                        "compiler: skipping missing static appendix %s", entry.file,
                    )

        adhoc_appendix_entries: list[tuple[Path, str]] = []
        if appendices_root.is_dir():
            for extra in sorted(
                p for p in appendices_root.glob("*.pdf")
                if p.name not in seen_appendix_names
            ):
                title = extra_appendix_titles.get(extra.name) or _humanize_filename_title(extra.name)
                adhoc_appendix_entries.append((extra, title))
                seen_appendix_names.add(extra.name)
                logger.info("compiler: appending ad-hoc appendix %s", extra.name)

        manifest_appendix_entries: list[tuple[Path, str]] = []
        if extra_appendix_paths:
            for extra in extra_appendix_paths:
                if extra.name in seen_appendix_names:
                    logger.info(
                        "compiler: skipping duplicate manifest appendix %s",
                        extra.name,
                    )
                    continue
                if not extra.exists():
                    logger.warning(
                        "compiler: manifest appendix file missing on disk: %s",
                        extra,
                    )
                    continue
                title = extra_appendix_titles.get(extra.name) or _humanize_filename_title(extra.name)
                manifest_appendix_entries.append((extra, title))
                seen_appendix_names.add(extra.name)
                logger.info("compiler: appending manifest appendix %s", extra.name)

        trailing_appendix_entries = [*adhoc_appendix_entries, *manifest_appendix_entries]
        appendix_page_counts: dict[str, int] = {
            str(path): _pdf_page_count(path.read_bytes())
            for path, _title in [*spec_appendix_paths.values(), *trailing_appendix_entries]
        }

        # 4. Render every GeneratedPage entry (pass 1), then re-render the
        #    TOC/page-reference templates with real page numbers computed
        #    from pass 1's actual output (pass 2) — see design.md Decision
        #    4. Each section is still rendered as an independent standalone
        #    PDF (unchanged architecture); only these specific templates
        #    render twice.
        #
        #    ``toc_page_numbers``/``appendix_toc_entries`` are seeded before
        #    pass 1 so every template (StrictUndefined) can safely reference
        #    them via ``.get(...)``/iterate them even before real page
        #    numbers are known. Appendix titles (but not pages) are already
        #    final at this point, so pass 1 renders the TOC with the correct
        #    number of appendix rows — critical, since the TOC's own page
        #    count feeds every later page number; only adding rows in pass 2
        #    would silently miscount if they spilled onto another page.
        ctx_full["toc_page_numbers"] = {}
        ctx_full["appendix_toc_entries"] = [
            {"title": title, "page": "—"}
            for _path, title in [*spec_appendix_paths.values(), *trailing_appendix_entries]
        ]
        generated_order: list[str] = [
            entry.template for entry in spec.entries if isinstance(entry, GeneratedPage)
        ]
        pdf_bytes_by_template: dict[str, bytes] = {}
        for template_name in generated_order:
            pdf_bytes_by_template[template_name] = render_template(
                template_name=template_name,
                context=ctx_full,
            )

        page_counts = {
            name: _pdf_page_count(b) for name, b in pdf_bytes_by_template.items()
        }

        # Walk spec.entries in true order (generated pages interleaved with
        # spec-declared appendices exactly as authored) to compute real
        # cumulative starting pages, then continue through the always-
        # trailing ad-hoc/manifest appendices.
        toc_page_numbers: dict[str, int] = {}
        appendix_toc_entries: list[dict[str, Any]] = []
        running_page = 1
        for entry in spec.entries:
            if isinstance(entry, GeneratedPage):
                toc_page_numbers[entry.template] = running_page
                running_page += page_counts[entry.template]
            elif isinstance(entry, StaticAppendix) and entry.file in spec_appendix_paths:
                path, title = spec_appendix_paths[entry.file]
                appendix_toc_entries.append({"title": title, "page": running_page})
                running_page += appendix_page_counts[str(path)]
        for path, title in trailing_appendix_entries:
            appendix_toc_entries.append({"title": title, "page": running_page})
            running_page += appendix_page_counts[str(path)]

        ctx_full["toc_page_numbers"] = toc_page_numbers
        ctx_full["appendix_toc_entries"] = appendix_toc_entries
        for template_name in _PAGE_NUMBER_DEPENDENT_TEMPLATES & set(generated_order):
            new_bytes = render_template(template_name=template_name, context=ctx_full)
            new_count = _pdf_page_count(new_bytes)
            if new_count != page_counts[template_name]:
                logger.warning(
                    "compiler: '%s' page count changed from %d to %d after "
                    "injecting real page numbers; TOC offsets computed from "
                    "pass 1 may be off by %d page(s) for later entries",
                    template_name, page_counts[template_name], new_count,
                    new_count - page_counts[template_name],
                )
            pdf_bytes_by_template[template_name] = new_bytes

        # Each is written via write_atomic_bytes so a partial render cannot
        # leak an inconsistent file at the temp path. The intermediate files
        # use a leading "." prefix so they do not collide with package.pdf
        # or any user-facing artifact.
        for entry in spec.entries:
            if isinstance(entry, GeneratedPage):
                tmp_path = output_dir / f".gen_{entry.template}.pdf"
                write_atomic_bytes(tmp_path, pdf_bytes_by_template[entry.template])
                intermediate_pdfs.append(tmp_path)

        # 5a. Concat generated pages into intermediate generated.pdf for
        #     debugging (kept on disk after compile so an operator can
        #     inspect just the system-generated portion in isolation).
        generated_path = output_dir / "generated.pdf"
        merge_pdfs(intermediate_pdfs, generated_path)

        # 5b. Build full merge order from the SAME resolved appendix data
        #     used for the TOC page-number computation above (step 3b) —
        #     single source of truth, so the merge order and the TOC's
        #     claimed page numbers can never drift apart. GeneratedPage
        #     entries and spec-declared appendices merge in spec.entries
        #     order (interleaved as authored); ad-hoc directory extras and
        #     DB-manifest appendices always trail after everything else.
        full_paths: list[Path] = []
        gen_index = 0
        for entry in spec.entries:
            if isinstance(entry, GeneratedPage):
                full_paths.append(intermediate_pdfs[gen_index])
                gen_index += 1
            elif isinstance(entry, StaticAppendix) and entry.file in spec_appendix_paths:
                full_paths.append(spec_appendix_paths[entry.file][0])
        for path, _title in trailing_appendix_entries:
            full_paths.append(path)

        package_path = output_dir / "package.pdf"
        merge_pdfs(full_paths, package_path)

        # 6. Last-mile structural validator (REQ-D11-007).
        qpdf_check(package_path)

        # 6b. Stamp the true absolute page number into every page's footer
        #     (task 3.5) — must run after the merge since it needs the real
        #     total page count. Re-validate structure after rewriting.
        stamp_absolute_page_numbers(package_path)
        qpdf_check(package_path)

    # 7. Write audit.json beside package.pdf (REQ-D11-011 stub).
    #    Outside the with-block so completed_at is finalized.
    audit_path = output_dir / "audit.json"
    assert audit_log_ref is not None  # populated inside the with-block
    write_atomic_bytes(
        audit_path, audit_log_ref.model_dump_json(indent=2).encode("utf-8")
    )

    # Page count + SHA-256 for the response. Reading bytes once and
    # using PyMuPDF avoids re-opening the file twice.
    package_bytes = package_path.read_bytes()
    sha256 = hashlib.sha256(package_bytes).hexdigest()

    import fitz  # local import — keeps top-level import cycle clean
    doc = fitz.open(stream=package_bytes, filetype="pdf")
    try:
        page_count = doc.page_count
    finally:
        doc.close()

    # Best-effort cleanup of the per-template intermediates. generated.pdf
    # stays for debugging. Any file we cannot remove is non-fatal — the
    # operator will see it on the next run and can clean up by hand.
    for p in intermediate_pdfs:
        try:
            p.unlink()
        except OSError:  # pragma: no cover — defensive
            logger.warning("Could not unlink intermediate %s", p)

    return CompileResult(
        output_path=package_path,
        audit_path=audit_path,
        intermediate_path=generated_path,
        page_count=page_count,
        sha256=sha256,
        completed_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    )


__all__ = [
    "compile_package",
    "CompileResult",
    "CompileError",
    "APPENDICES_DIR",
]
