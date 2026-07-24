"""Preflight gate (CONTEXT D-19).

Returns ``list[PreflightError]`` with stable field paths matching UI-SPEC §9.3.
Render is gated on zero blocking errors. Errors map cleanly to the
``DisclosurePreflightChecklist`` UI rows; the field_path strings are the
verbatim row keys the frontend reads.

Field path contract (REQ-D11-004, REQ-D11-005):
  * budget_draft.line_items
  * reserve_study_snapshot.components
  * hoa_metadata.fiscal_year_end_month
  * reserve_cash_balance.amount

Gate evaluation order is deterministic (above). When multiple gates fail,
errors are returned in declaration order so the UI can render a stable list.

Static appendix files are intentionally NOT preflight-blocking. Whatever PDFs
exist in the appendices directory at compile time are merged in; missing
spec entries are skipped (compiler.py logs a warning). This keeps local-dev
generation working without the full Old Mill legal-review extraction, and
lets operators drop ad-hoc appendix PDFs in without updating the PackageSpec.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

from .schemas import (
    BudgetDraft,
    HOAMetadata,
    PackageSpec,
    PreflightError,
    ReserveStudySnapshot,
)
from .formulas import total_year_replacement_provision
from .reconciliation import (
    normalize_packet_archetype,
    parse_optional_decimal_setting,
    resolve_assessment_facts,
    resolve_reserve_funding_facts,
    resolve_reserve_interest_tax_facts,
)


# Accepted reserve-study-date formats. ISO + US numeric are most common;
# month-name formats ("September 2025", "Sep 2025", "Sep 1, 2025") are
# accepted because operators commonly type reserve-study dates that way
# (the reserve-study cover page itself usually shows month + year).
_RESERVE_STUDY_DATE_FORMATS = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%Y/%m/%d",
    "%B %d, %Y",   # "September 1, 2025"
    "%b %d, %Y",   # "Sep 1, 2025"
    "%B %Y",       # "September 2025" → first of month
    "%b %Y",       # "Sep 2025" → first of month
    "%B, %Y",      # "September, 2025"
)


def _parse_reserve_study_date(value: str) -> Optional[date]:
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    for fmt in _RESERVE_STUDY_DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def _reserve_funding_row_issue_details(warning: str) -> tuple[str, str]:
    if "implies" in warning and "reserve-study units" in warning:
        return (
            "unit_count_conflict",
            "Confirm whether the reserve study unit count or HOA assessment unit count should drive disclosures.",
        )
    return (
        "reserve_funding_row_math_mismatch",
        "Review the extracted reserve funding plan row against the source reserve study.",
    )


def _reserve_funding_issue_details(warning: str) -> tuple[str, str]:
    if "component annual provision" in warning:
        return (
            "reserve_funding_component_fallback",
            "Enter a manual reserve funding amount or upload a budget/reserve study funding row.",
        )
    return (
        "reserve_funding_conflict",
        "Choose the board-approved reserve funding source.",
    )


def check_reserve_study_age(
    *,
    reserve_study_date: Optional[str],
    package_fiscal_year: int,
) -> list[PreflightError]:
    """California Civil Code §5550: the reserve study used in an annual
    disclosure package must be ≤ 3 years old.

    Emits:
    - ``blocking`` when the study is > 3 years old as of the package's
      fiscal year start (Jan 1 of the package year).
    - ``warning`` when the study is exactly 3 years old (refresh
      overdue next package year).
    - ``warning`` when the study is 2 years old (refresh due next year).
    - ``warning`` when ``reserve_study_date`` is missing/unparseable —
      operator must supply a date before package generation.

    All findings reference ``hoa_settings.reserve_study_date`` so the
    Disclosure Settings form can deep-link the operator to the field.
    """
    field_path = "hoa_settings.reserve_study_date"

    if not reserve_study_date or not str(reserve_study_date).strip():
        return [
            PreflightError(
                field_path=field_path,
                message=(
                    "Reserve study date is missing. California Civil Code "
                    "§5550 requires a reserve study dated within the past 3 years."
                ),
                severity="warning",
            )
        ]

    parsed = _parse_reserve_study_date(str(reserve_study_date))
    if parsed is None:
        return [
            PreflightError(
                field_path=field_path,
                message=(
                    f"Reserve study date {reserve_study_date!r} is unparseable. "
                    "Use YYYY-MM-DD or MM/DD/YYYY."
                ),
                severity="warning",
            )
        ]

    # Use calendar-year arithmetic — matches the statute's plain reading
    # ("dated within the past 3 years") and avoids quirky month-day anchor
    # edge cases that would let a study dated, say, June 2023 pass for an
    # entire 2026 package because the package "starts" before June.
    age_years = package_fiscal_year - parsed.year

    if age_years > 3:
        return [
            PreflightError(
                field_path=field_path,
                message=(
                    f"Reserve study dated {reserve_study_date} is {age_years} "
                    "years old at the start of the package fiscal year. CA §5550 "
                    "requires ≤ 3 years."
                ),
                severity="blocking",
            )
        ]
    if age_years == 3:
        return [
            PreflightError(
                field_path=field_path,
                message=(
                    f"Reserve study dated {reserve_study_date} is exactly 3 "
                    "years old; next package year will require a refresh."
                ),
                severity="warning",
            )
        ]
    if age_years == 2:
        return [
            PreflightError(
                field_path=field_path,
                message=(
                    f"Reserve study dated {reserve_study_date} is 2 years old; "
                    "refresh due next package year."
                ),
                severity="warning",
            )
        ]
    return []


def check_boilerplate_tokens(
    boilerplate_overrides: Optional[dict],
) -> list[PreflightError]:
    """Blocking gate: every ``data-var`` token in a boilerplate slot must be
    in ``boilerplate_variables.TOKEN_CATALOG``.

    Defense in depth alongside the save-time check in
    ``hoa_boilerplate.merge_overrides`` — this catches an unknown/misspelled
    token from any source (a snapshot frozen before this gate existed, a
    direct DB edit) so it can never reach the finished PDF as raw
    ``data-var`` markup.
    """
    if not boilerplate_overrides:
        return []
    from ..services import boilerplate_variables

    out: list[PreflightError] = []
    for slot_id, value in boilerplate_overrides.items():
        if not value:
            continue
        unknown = boilerplate_variables.find_unknown_tokens(value)
        for token in unknown:
            out.append(PreflightError(
                field_path=f"boilerplate.{slot_id}",
                message=(
                    f"Unknown variable token {token!r} in the {slot_id!r} "
                    "boilerplate slot. Remove it or pick a valid variable "
                    "from the insert-variable menu."
                ),
                severity="blocking",
                code="unknown_boilerplate_token",
                suggested_fix=(
                    "Open the boilerplate editor and replace the token with "
                    "one from the variable picker."
                ),
            ))
    return out


def validate_inputs(
    *,
    spec: PackageSpec,
    budget_draft: BudgetDraft,
    reserve_snapshot: ReserveStudySnapshot,
    hoa_metadata: HOAMetadata,
    appendices_root: Optional[Path] = None,
    hoa_settings_overrides: Optional[dict] = None,
    boilerplate_overrides: Optional[dict] = None,
) -> list[PreflightError]:
    """Return the list of blocking + warning errors. Empty list = ready to render.

    Args:
        spec: PackageSpec for the target HOA (provides static_data and entries).
        budget_draft: BudgetDraft with operating + reserve line items.
        reserve_snapshot: ReserveStudySnapshot from Phase 10 extractor.
        hoa_metadata: HOAMetadata from properties table.
        appendices_root: accepted for backward compatibility; no longer used.
            Static appendix existence is no longer a preflight concern —
            see compiler.py for the merge-time skip-and-warn behaviour.
        hoa_settings_overrides: operator-saved per-HOA settings. In the
            DRE-driven model the numeric disclosure inputs (reserve cash
            balance, etc.) live here, not in ``spec.static_data``.
        boilerplate_overrides: resolved (registry-keyed) rich-text slot
            values, checked for unknown variable tokens.
    """
    del appendices_root  # appendix existence is checked at merge time, not here

    overrides = hoa_settings_overrides or {}
    errors: list[PreflightError] = list(check_boilerplate_tokens(boilerplate_overrides))
    packet_archetype = normalize_packet_archetype(
        overrides.get("financial_packet_archetype")
    )

    # 1. Budget line items present (REQ-D11-005)
    if not budget_draft.line_items:
        errors.append(PreflightError(
            field_path="budget_draft.line_items",
            message="At least one line item is required",
            severity="blocking",
        ))

    # 2. Reserve study components present. Income-statement-only PDFs can still
    # render a package; the compiler surfaces this as a data gap and defaults
    # reserve-derived calculations to $0/0%.
    if not reserve_snapshot.components:
        errors.append(PreflightError(
            field_path="reserve_study_snapshot.components",
            message=(
                "Reserve study has no components — reserve-derived schedules "
                "will render with default $0/0% values"
            ),
            severity="warning",
        ))

    # 3. HOA fiscal year end month set + valid (1-12)
    fy_end = hoa_metadata.fiscal_year_end_month
    if fy_end is None or not (1 <= fy_end <= 12):
        errors.append(PreflightError(
            field_path="hoa_metadata.fiscal_year_end_month",
            message="HOA fiscal_year_end_month must be 1-12",
            severity="blocking",
        ))

    # 4. Reserve cash balance set (REQ-D11-004). In the DRE-driven model the
    #    operator-supplied value lives in ``hoa_settings``; the spec carries
    #    only a sentinel default. Preflight checks the *effective* value
    #    (override if present, else spec.static_data) so a missing setting
    #    surfaces as a data_gap downstream rather than blocking compile.
    override_cash = parse_optional_decimal_setting(
        overrides.get("reserve_cash_balance_eoy_prior")
    )
    effective_cash = (
        override_cash
        if override_cash is not None
        else spec.static_data.reserve_cash_balance_eoy_prior
    )
    if effective_cash < 0:
        errors.append(PreflightError(
            field_path="reserve_cash_balance.amount",
            message="Reserve cash balance for end of prior year must be >= 0",
            severity="blocking",
        ))

    # 5. Reserve study staleness (CA §5550; Phase 5.7 of
    #    dre-driven-assessment-engine).
    errors.extend(
        check_reserve_study_age(
            reserve_study_date=reserve_snapshot.study_date,
            package_fiscal_year=spec.fiscal_year,
        )
    )
    if (
        1900 <= spec.fiscal_year <= 3000
        and reserve_snapshot.funding_plan_rows
        and not any(
            row.year == spec.fiscal_year for row in reserve_snapshot.funding_plan_rows
        )
    ):
        errors.append(PreflightError(
            field_path="reserve_study_snapshot.funding_plan_rows",
            message=(
                "No reserve-study funding plan row was found for fiscal year "
                f"{spec.fiscal_year}; reserve funding may be pulled from a "
                "different year or fallback source."
            ),
            severity="warning",
            code="fiscal_year_source_mismatch",
            affected_value={"package_fiscal_year": spec.fiscal_year},
            suggested_fix=(
                "Upload a reserve-study funding plan for the package year or "
                "choose a manual/budget reserve funding source."
            ),
        ))
    for row in reserve_snapshot.funding_plan_rows:
        for warning in row.consistency_warnings(units=hoa_metadata.units):
            code, suggested_fix = _reserve_funding_row_issue_details(warning)
            errors.append(PreflightError(
                field_path="reserve_study_snapshot.funding_plan_rows",
                message=warning,
                severity="warning",
                code=code,
                affected_value={"row_year": row.year, "hoa_units": hoa_metadata.units},
                suggested_fix=suggested_fix,
            ))

    # 6. Reconciliation warnings. These do not block generation by default,
    # but they make source-of-truth conflicts visible before the PDF renders.
    reserve_funding_facts = resolve_reserve_funding_facts(
        funding_source=overrides.get("reserve_funding_source"),
        manual_annual_amount=overrides.get("reserve_funding_manual_amount"),
        budget_line_items=budget_draft.line_items,
        reserve_funding_plan_rows=reserve_snapshot.funding_plan_rows,
        component_annual_provision=total_year_replacement_provision(
            components=reserve_snapshot.components
        ),
        units=hoa_metadata.units,
        fiscal_year=spec.fiscal_year,
    )
    if packet_archetype == "dual-fund" and reserve_funding_facts.source == "missing":
        errors.append(PreflightError(
            field_path="reserve_funding.source",
            message=(
                "Reserve funding could not be resolved for a dual-fund packet. "
                "Choose a reserve funding source or provide the missing amount."
            ),
            severity="blocking",
            code="reserve_funding_missing",
            affected_value=reserve_funding_facts.model_dump(mode="json"),
            suggested_fix=(
                "Set a manual reserve funding amount, upload a reserve-funding line, "
                "or supply a reserve-study funding-plan row for the package year."
            ),
        ))
    for warning in reserve_funding_facts.warnings:
        code, suggested_fix = _reserve_funding_issue_details(warning)
        errors.append(PreflightError(
            field_path="reserve_funding.source",
            message=warning,
            severity="warning",
            code=code,
            affected_value=reserve_funding_facts.model_dump(mode="json"),
            suggested_fix=suggested_fix,
        ))

    assessment_facts = resolve_assessment_facts(
        budget_line_items=[
            item for item in budget_draft.line_items if not item.is_reserve
        ],
        approved_monthly_assessment_per_unit=overrides.get(
            "approved_monthly_assessment_per_unit"
        ),
        units=hoa_metadata.units,
    )
    for warning in assessment_facts.warnings:
        errors.append(PreflightError(
            field_path="assessment.annual_revenue",
            message=warning,
            severity="warning",
            code="assessment_override_revenue_mismatch",
            affected_value=assessment_facts.model_dump(mode="json"),
            suggested_fix=(
                "Update the budget assessment revenue or confirm the approved "
                "monthly override."
            ),
        ))

    reserve_interest_tax_facts = resolve_reserve_interest_tax_facts(
        reserve_interest_income_override=overrides.get("reserve_interest_income_override"),
        income_tax_provision_override=overrides.get("income_tax_provision_override"),
        budget_line_items=budget_draft.line_items,
        reserve_funding_plan_rows=reserve_snapshot.funding_plan_rows,
        fiscal_year=spec.fiscal_year,
    )
    should_validate_interest_tax = (
        overrides.get("reserve_interest_income_override") not in (None, "")
        or overrides.get("income_tax_provision_override") not in (None, "")
        or any(
            item.is_revenue and item.label and "interest" in item.label.lower()
            for item in budget_draft.line_items
        )
        or any(
            row.year == spec.fiscal_year and row.interest_income is not None
            for row in reserve_snapshot.funding_plan_rows
        )
    )
    if should_validate_interest_tax:
        for warning in reserve_interest_tax_facts.warnings:
            errors.append(PreflightError(
                field_path="reserve_interest_income",
                message=warning,
                severity="warning",
                code="reserve_interest_or_tax_missing",
                affected_value=reserve_interest_tax_facts.model_dump(mode="json"),
                suggested_fix=(
                    "Provide a reserve interest income override or confirm the reserve-study / budget source rows."
                ),
            ))

    return errors


# -- Orchestration helpers (Phase 5.1) -------------------------------------


def partition_errors(
    errors: list[PreflightError],
) -> tuple[list[PreflightError], list[PreflightError]]:
    """Split a flat error list into (blocking, warnings).

    Callers that only need to surface warnings (e.g., ``computed.data_gaps``
    in the audit JSON) consume the second tuple element; the first
    element is what halts compilation.
    """
    blocking: list[PreflightError] = []
    warnings: list[PreflightError] = []
    for e in errors:
        if e.severity == "blocking":
            blocking.append(e)
        else:
            warnings.append(e)
    return blocking, warnings


# -- Appendix cadence preflight (Phase 5.6) --------------------------------


def check_appendix_cadence(
    *,
    appendix_documents: list[dict],
    package_fiscal_year: int,
) -> list[PreflightError]:
    """Validate the per-HOA appendix manifest for the package year.

    Each ``appendix_documents[i]`` dict must carry at minimum
    ``display_title``, ``cadence``, ``annual_year`` (nullable),
    ``valid_through_year`` (nullable), and ``required_flag``. The check
    enforces:

    - Annual appendix missing for the package year — emit blocking when
      ``required_flag=1`` (insurance disclosures and the like); warning
      otherwise.
    - Package year exceeds ``valid_through_year`` for any appendix —
      blocking (operator must refresh).

    The check is data-driven (takes a plain list of dicts) so the
    compile pipeline can adapt the AppendixDocument ORM rows into the
    expected shape without coupling preflight to a particular ORM.
    """
    out: list[PreflightError] = []

    has_annual_for_year = False
    annual_required = False
    for a in appendix_documents:
        cadence = (a.get("cadence") or "persistent").strip().lower()
        title = a.get("display_title") or a.get("file_name") or "(unnamed)"
        required = bool(a.get("required_flag"))
        annual_year = a.get("annual_year")
        valid_through = a.get("valid_through_year")

        if cadence == "annual":
            if required:
                annual_required = True
            if annual_year == package_fiscal_year:
                has_annual_for_year = True

        if valid_through is not None and package_fiscal_year > int(valid_through):
            out.append(
                PreflightError(
                    field_path=f"appendix.{title}.valid_through_year",
                    message=(
                        f"Appendix {title!r} expired at end of fiscal year "
                        f"{valid_through}; package year {package_fiscal_year} requires a refresh."
                    ),
                    severity="blocking",
                )
            )

    if annual_required and not has_annual_for_year:
        out.append(
            PreflightError(
                field_path="appendix.annual_required_missing",
                message=(
                    f"A required annual-cadence appendix is missing for "
                    f"fiscal year {package_fiscal_year}. Upload this year's "
                    "version (e.g. annual insurance disclosure) before generating "
                    "the package."
                ),
                severity="blocking",
            )
        )

    return out


# -- Special assessments (Phase 4.4) ---------------------------------------


SpecialAssessmentStatus = "approved_scheduled | possible_disclosure_only | none"


def infer_special_assessment_status(entry: dict) -> str:
    """Resolve the status of a ``hoa_settings.special_assessments_json`` entry.

    The JSON shape was extended with an optional ``status`` field as part
    of Phase 4.4. Legacy rows without that field are mapped to a status
    via these rules (also documented in tasks.md §4.4):

    - ``amount_per_unit > 0`` → ``approved_scheduled`` (scheduled charge)
    - ``amount_per_unit == 0 && display_language present`` → ``possible_disclosure_only``
    - else → ``none``

    Returns one of ``approved_scheduled`` / ``possible_disclosure_only``
    / ``none``. Honors an explicit ``status`` field when present.
    """
    explicit = (entry.get("status") or "").strip().lower()
    if explicit in ("approved_scheduled", "possible_disclosure_only", "none"):
        return explicit

    amount = entry.get("amount_per_unit")
    if amount is None:
        amount = entry.get("per_unit")
    if amount is None:
        amount = entry.get("amount")

    try:
        numeric = float(amount) if amount is not None else 0.0
    except (TypeError, ValueError):
        numeric = 0.0

    if numeric > 0:
        return "approved_scheduled"
    # Pool-based special assessment (add-variable-special-assessments): a total
    # amount and/or a pool link is a scheduled charge even without a per-unit
    # figure (the amount lives on the pool, allocated by its basis).
    total = entry.get("total_amount")
    try:
        if total is not None and float(total) > 0:
            return "approved_scheduled"
    except (TypeError, ValueError):
        pass
    if entry.get("pool_key"):
        return "approved_scheduled"
    display = (entry.get("display_language") or "").strip()
    if display:
        return "possible_disclosure_only"
    return "none"


def check_special_assessments(
    *,
    entries: list[dict],
) -> list[PreflightError]:
    """Validate ``hoa_settings.special_assessments_json`` entries.

    Per Phase 4.4 preflight rules:

    - ``status='approved_scheduled'`` requires ``amount_per_unit > 0``.
    - ``status='approved_scheduled'`` requires ``due_date`` OR a
      non-empty ``due_dates`` array (either satisfies; multi-date wins
      when both present).
    - ``status='possible_disclosure_only'`` requires non-empty
      ``display_language``.

    Entries without an explicit status are checked against the
    inferred status from ``infer_special_assessment_status``.
    """
    out: list[PreflightError] = []
    for i, entry in enumerate(entries):
        label = entry.get("label") or entry.get("description") or f"entry[{i}]"
        status = infer_special_assessment_status(entry)

        if status == "approved_scheduled":
            amount = entry.get("amount_per_unit")
            if amount is None:
                amount = entry.get("per_unit") or entry.get("amount")
            try:
                numeric = float(amount) if amount is not None else 0.0
            except (TypeError, ValueError):
                numeric = 0.0
            try:
                total_amount = float(entry.get("total_amount")) if entry.get("total_amount") is not None else 0.0
            except (TypeError, ValueError):
                total_amount = 0.0
            # A pool-linked special assessment's dollars live on the pool (mapped
            # lines or an operator total) and are validated in the matrix builder,
            # so a per-unit figure is not required here.
            has_amount = numeric > 0 or total_amount > 0 or bool(entry.get("pool_key"))
            if not has_amount:
                out.append(
                    PreflightError(
                        field_path=f"special_assessments[{i}].amount_per_unit",
                        message=(
                            f"Special assessment {label!r} status='approved_scheduled' "
                            "but has no amount_per_unit, total_amount, or pool link."
                        ),
                        severity="blocking",
                    )
                )

            has_due_date = bool((entry.get("due_date") or "").strip()) if isinstance(
                entry.get("due_date"), str
            ) else bool(entry.get("due_date"))
            has_due_dates = bool(entry.get("due_dates"))
            if not (has_due_date or has_due_dates):
                out.append(
                    PreflightError(
                        field_path=f"special_assessments[{i}].due_date",
                        message=(
                            f"Special assessment {label!r} status='approved_scheduled' "
                            "needs either due_date or due_dates."
                        ),
                        severity="blocking",
                    )
                )

        elif status == "possible_disclosure_only":
            display = (entry.get("display_language") or "").strip()
            if not display:
                out.append(
                    PreflightError(
                        field_path=f"special_assessments[{i}].display_language",
                        message=(
                            f"Special assessment {label!r} "
                            "status='possible_disclosure_only' needs display_language."
                        ),
                        severity="blocking",
                    )
                )

    return out


def check_specified_value_placeholders(
    *,
    property_id: int,
    connection,
) -> list[PreflightError]:
    """Blocking gate for unresolved specified-value placeholders (C7).

    Promotion tags auto-generated equal splits on ``specified_value`` pools
    with ``source='equal_split_placeholder'`` (never ``'dre'``). Those rows
    are synthetic — presenting them in a disclosure as each unit's
    DRE-specified amount would be wrong for any HOA whose real per-unit
    values are not equal. This check blocks package generation (and, via
    the finalize preflight, finalization) until the operator resolves every
    placeholder in the Review Workbench.

    Scopes to the property's default (active) assessment setup.
    """
    rows = connection.execute(
        """
        SELECT aupa.pool_key,
               COUNT(*) AS placeholder_count
          FROM assessment_unit_pool_allocations aupa
          JOIN properties p
            ON p.default_assessment_setup_id = aupa.assessment_setup_id
         WHERE p.id = ?
           AND aupa.source = 'equal_split_placeholder'
         GROUP BY aupa.pool_key
         ORDER BY aupa.pool_key
        """,
        (property_id,),
    ).fetchall()

    out: list[PreflightError] = []
    for pool_key, placeholder_count in rows:
        out.append(
            PreflightError(
                field_path=f"assessment_setup.pools.{pool_key}.unit_allocations",
                message=(
                    f"Pool {pool_key!r} uses specified per-unit values, but "
                    f"{placeholder_count} unit(s) still carry the auto-generated "
                    "equal-split placeholder. Enter each unit's actual amount "
                    "in the Review Workbench before generating the package."
                ),
                severity="blocking",
                code="specified_value_placeholder",
                suggested_fix=(
                    "Open the DRE Review Workbench and enter the per-unit "
                    "specified amounts for this pool, then re-approve."
                ),
            )
        )
    return out
