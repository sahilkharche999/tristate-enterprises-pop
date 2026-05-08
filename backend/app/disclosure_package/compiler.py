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
from datetime import datetime, timezone
from decimal import Decimal
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
from .merge import merge_pdfs, qpdf_check, write_atomic_bytes
from .preflight import validate_inputs
from .render import render_template
from .schemas import (
    BudgetDraft,
    GeneratedPage,
    HOAMetadata,
    PackageSpec,
    ReserveStudySnapshot,
    StaticAppendix,
)

logger = logging.getLogger(__name__)

APPENDICES_DIR = Path(__file__).parent / "appendices"


class CompileError(RuntimeError):
    """Raised when compile_package cannot produce a valid PDF.

    Carries the failing PreflightError field paths so the caller can
    surface them in the DisclosurePreflightChecklist UI (UI-SPEC §9.3).
    """

    def __init__(self, message: str, *, field_paths: Optional[list[str]] = None):
        super().__init__(message)
        self.field_paths = field_paths or []


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


def _build_thirty_year_plan(
    *,
    spec: PackageSpec,
    hoa_metadata: HOAMetadata,
    total_estimated_liability: Decimal,
    total_year_replacement_provision: Decimal,
    cash_eoy_prior: Decimal,
    fiscal_year_start: int,
) -> list[dict[str, Any]]:
    """Project beginning balance, contribution, expenditures, interest, and
    ending balance for 30 years starting at ``fiscal_year_start``.

    This is a deliberately simple roll-forward: each year contributes the
    constant `total_year_replacement_provision` (with a 3% inflation factor
    applied to the cost basis, mirroring `replacement_cost_increase_rate`),
    earns interest on the average balance at the configured after-tax rate,
    and is debited the same provision as a notional expenditure flat across
    the horizon. Phase 12+ will replace this with per-component scheduled
    expenditures from the reserve study.
    """
    rows: list[dict[str, Any]] = []
    balance = cash_eoy_prior
    inflation = spec.static_data.replacement_cost_increase_rate or Decimal("0.03")
    interest_rate = spec.static_data.interest_rate_after_tax or Decimal("0.018")
    base_provision = total_year_replacement_provision or Decimal("0")
    base_liability = total_estimated_liability or Decimal("0")

    for offset in range(30):
        year = fiscal_year_start + offset
        # Contribution grows with inflation; expenditure equals contribution
        # for the simple roll-forward (net change ~ interest only).
        factor = (Decimal("1") + inflation) ** offset
        annual_contribution = (base_provision * factor).quantize(Decimal("1"))
        annual_expenditure = annual_contribution
        avg_balance = balance + (annual_contribution - annual_expenditure) / Decimal("2")
        interest = (avg_balance * interest_rate).quantize(Decimal("1"))
        ending = (balance + annual_contribution - annual_expenditure + interest).quantize(
            Decimal("1")
        )
        liability = (base_liability * factor).quantize(Decimal("1"))
        pct = (
            (ending / liability * Decimal("100")).quantize(Decimal("1"))
            if liability
            else Decimal("0")
        )
        rows.append({
            "year": year,
            "beginning_balance": int(balance),
            "annual_contribution": int(annual_contribution),
            "annual_expenditure": int(annual_expenditure),
            "interest": int(interest),
            "ending_balance": int(ending),
            "estimated_liability": int(liability),
            "percent_funded": int(pct),
        })
        balance = ending
    return rows


def _compute_all(
    spec: PackageSpec,
    budget_draft: BudgetDraft,
    reserve_snapshot: ReserveStudySnapshot,
    hoa_metadata: HOAMetadata,
) -> dict[str, Any]:
    """Materialize every value the templates reference.

    All formula calls happen inside the active audit_context so each one
    is recorded exactly once in the audit log (re-entrancy guard in
    audit.py keeps nested decorated calls from double-recording).
    """
    operating_lis = [li for li in budget_draft.line_items if not li.is_reserve]
    reserve_lis = [li for li in budget_draft.line_items if li.is_reserve]

    total_rev_op = total_revenues_operations(operating_line_items=operating_lis)
    total_rev_rep = total_revenues_replacement(reserve_line_items=reserve_lis)
    exp_maint = expenses_maintenance_operating(operating_line_items=operating_lis)
    exp_util = expenses_utilities_operating(operating_line_items=operating_lis)
    exp_admin = expenses_administration_operating(operating_line_items=operating_lis)
    exp_rep = expenses_replacement(reserve_line_items=reserve_lis)
    total_exp_op = total_expenses_operations(
        maintenance=exp_maint, utilities=exp_util, administration=exp_admin
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
    cash = spec.static_data.reserve_cash_balance_eoy_prior
    pct = percent_funded(cash_reserves=cash, estimated_liability=total_liab)
    under_total = under_funded_balance_total(
        estimated_liability=total_liab, cash_reserves=cash
    )
    under_per_unit = under_funded_balance_per_unit(
        estimated_liability=total_liab,
        cash_reserves=cash,
        units=hoa_metadata.units,
    )

    # Cover-letter "Reserves are being funded in the amount of $X monthly" comes
    # from the reserve-study annual provision divided by 12 (NOT the budget-side
    # reserve revenue rows, which is what the original implementation used and
    # which was zero whenever the operator did not flag any draft line items as
    # is_reserve revenue). The provision is the source of truth per § 5550.
    monthly_replacement_contribution_total = (
        (total_prov / Decimal(12)).quantize(Decimal("0.01"))
        if total_prov > 0
        else Decimal("0.00")
    )

    if hoa_metadata.units > 0 and total_prov > 0:
        base_2026_monthly = (
            total_prov / Decimal(hoa_metadata.units) / Decimal(12)
        ).quantize(Decimal("0.01"))
    else:
        base_2026_monthly = Decimal("0.00")

    # Pro-forma footnote counts — currently a strict subset of the reserve
    # study. Phase 12 adds a board-deferral / signed-contracts admin form;
    # for now we surface the one we can derive (useful_life missing) and
    # default the others to 0 so the rendered page reads cleanly.
    useful_life_not_disclosed_count = sum(
        1 for c in reserve_snapshot.components
        if not c.useful_life or int(c.useful_life) <= 0
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
                beginning_balance=spec.static_data.fund_balance_boy_operations,
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
            "thirty_year_funding_plan": _build_thirty_year_plan(
                spec=spec,
                hoa_metadata=hoa_metadata,
                total_estimated_liability=total_liab,
                total_year_replacement_provision=total_prov,
                cash_eoy_prior=cash,
                fiscal_year_start=spec.fiscal_year,
            ),
            "assessment_change_disclosure": (
                "0%"
                if spec.static_data.monthly_assessment_per_unit_current
                == spec.static_data.monthly_assessment_per_unit_prior
                else "increase"
            ),
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
            Defaults to ``compiler.APPENDICES_DIR / 'old_mill'`` —
            override in tests.

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
        appendices_root = APPENDICES_DIR / "old_mill"

    # 1. Preflight gate (REQ-D11-008) — fail fast with field paths.
    errors = validate_inputs(
        spec=spec,
        budget_draft=budget_draft,
        reserve_snapshot=reserve_snapshot,
        hoa_metadata=hoa_metadata,
        appendices_root=appendices_root,
    )
    blocking = [e for e in errors if e.severity == "blocking"]
    if blocking:
        raise CompileError(
            f"Preflight blocked compilation: {len(blocking)} error(s)",
            field_paths=[e.field_path for e in blocking],
        )

    # 2. Capture the input snapshot for the audit log (CONTEXT D-15).
    input_snapshot: dict[str, Any] = {
        "spec_hoa_id": spec.hoa_id,
        "fiscal_year": spec.fiscal_year,
        "budget_draft": budget_draft.model_dump(mode="json"),
        "reserve_snapshot": reserve_snapshot.model_dump(mode="json"),
        "hoa_metadata": hoa_metadata.model_dump(mode="json"),
        "static_data": spec.static_data.model_dump(mode="json"),
    }

    audit_log_ref = None
    intermediate_pdfs: list[Path] = []

    with audit_context(input_snapshot) as audit_log:
        audit_log_ref = audit_log

        # 3. Compute every formula value (each call is recorded once by
        #    the @audit_formula decorators).
        computed = _compute_all(spec, budget_draft, reserve_snapshot, hoa_metadata)
        ctx_full: dict[str, Any] = {
            "spec": spec,
            "static_data": spec.static_data,
            "fiscal_year": spec.fiscal_year,
            **computed,
        }

        # 4. Render every GeneratedPage entry → per-template PDF on disk.
        #    Each is written via write_atomic_bytes so a partial render
        #    cannot leak an inconsistent file at the temp path. The
        #    intermediate files use a leading "." prefix so they do not
        #    collide with package.pdf or any user-facing artifact.
        for entry in spec.entries:
            if isinstance(entry, GeneratedPage):
                pdf_bytes = render_template(
                    template_name=entry.template,
                    context=ctx_full,
                )
                tmp_path = output_dir / f".gen_{entry.template}.pdf"
                write_atomic_bytes(tmp_path, pdf_bytes)
                intermediate_pdfs.append(tmp_path)

        # 5a. Concat generated pages into intermediate generated.pdf for
        #     debugging (kept on disk after compile so an operator can
        #     inspect just the system-generated portion in isolation).
        generated_path = output_dir / "generated.pdf"
        merge_pdfs(intermediate_pdfs, generated_path)

        # 5b. Build full merge order: walk spec.entries; GeneratedPage
        #     entries always merge in; StaticAppendix entries merge in
        #     when the file exists on disk and are silently skipped
        #     otherwise (compile keeps working with 0-N appendix PDFs).
        full_paths: list[Path] = []
        spec_appendix_files: set[str] = set()
        gen_index = 0
        for entry in spec.entries:
            if isinstance(entry, GeneratedPage):
                full_paths.append(intermediate_pdfs[gen_index])
                gen_index += 1
            elif isinstance(entry, StaticAppendix):
                spec_appendix_files.add(entry.file)
                appendix_path = appendices_root / entry.file
                if appendix_path.exists():
                    full_paths.append(appendix_path)
                else:
                    logger.info(
                        "compiler: skipping missing static appendix %s",
                        entry.file,
                    )

        # 5c. Append any extra PDFs the operator dropped into the appendix
        #     directory that aren't named in spec.entries — sorted so the
        #     order is deterministic across runs. This is the "drop a
        #     random appendix in and have it included" path.
        if appendices_root.is_dir():
            extras = sorted(
                p for p in appendices_root.glob("*.pdf")
                if p.name not in spec_appendix_files
            )
            for extra in extras:
                logger.info("compiler: appending ad-hoc appendix %s", extra.name)
                full_paths.append(extra)

        package_path = output_dir / "package.pdf"
        merge_pdfs(full_paths, package_path)

        # 6. Last-mile structural validator (REQ-D11-007).
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
