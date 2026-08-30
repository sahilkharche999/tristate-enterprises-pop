"""Universal assessment-schedule matrix builder.

This module is the rendering boundary for the assessment section.  The
assessment engine still owns all math; the matrix builder only reshapes
``CalcResultSet`` values into explicit rows, columns, notes, and layout hints
that a single HTML template can loop over.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import sqlite3
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from app.assessment_mode import (
    ASSESSMENT_MODE_FIXED,
    AssessmentMode,
    normalize_assessment_mode,
)
from app.assessment_engine import (
    BudgetLineInput,
    CalcInput,
    CalcResultSet,
    PoolDefinition,
    RecipientReference,
    RecipientSet,
    SetupType,
)
from app.assessment_engine.engine import (
    SPECIAL_ASSESSMENT_POOL_KIND,
    _allocate_special_assessment,
    run as run_assessment_engine,
)
from app.assessment_engine.pools import ownership_weight_sum_is_valid
from app.assessment_engine.recipients import resolve_recipients
from app.assessment_engine.schemas import (
    SpecialAssessmentAllocation,
    SpecialAssessmentAllocationEntry,
)
from app.assessment_engine.errors import EngineSetupError, NeedsHumanReview
from app.assessment_engine.percent_form import (
    AmbiguousPercentColumn,
    normalize_percent_value,
    resolve_percent_divisor,
)
from app.assessment_engine.schemas import BudgetLineMappingInput
from app.dre_extraction.promotion import (
    apply_review_edits_to_extraction,
    parse_extraction_payload,
)
from app.services.assessment_budget_mapping_rule_service import (
    build_assessment_mapping_review_blockers,
    build_assessment_mapping_review_rows,
    build_assessment_mapping_review_summary,
    normalize_budget_label,
    build_budget_line_slice_key,
    resolve_active_assessment_setup_id,
    select_assessment_mapping_amount,
)
from app.allocation_resolution.service import (
    list_current_resolutions,
    list_slices,
)
from app.services.assessment_mapping_category import (
    _assessment_mapping_category,
    _assessment_mapping_fund_type,
)
from app.services.ccr_approval_service import (
    get_operator_unit_factors,
    merge_operator_factors,
)
from app.services.dre_review_service import list_review_edits

from .reconciliation import build_pool_line_fund_totals_from_mapped_rows
from .schemas import PreflightError


RecipientGrain = Literal["summary", "group", "unit", "manual_review"]
ColumnKind = Literal["recipient", "basis", "component", "total", "optional"]
EvidenceSourceType = Literal["visual_page", "operator_approval"]
FooterRowKind = Literal["total", "rounding_adjustment", "reconciliation_difference"]
LayoutOrientation = Literal["portrait", "landscape"]
SplitStrategy = Literal["none", "by_component_group", "continuation_pages"]

COMPARISON_OPTIONAL_KEYS = {
    "prior_year_assessment",
    "current_year_assessment",
    "difference",
    "percent_change",
}

LIABILITY_OPTIONAL_KEYS = {
    "unfunded_liability",
    "underfunded_liability",
    "reserve_deficit",
    "reserve_surplus",
    "special_liability",
}


class EvidenceRef(BaseModel):
    """Audit trace for a matrix decision.

    ``visual_page`` means a scanned/rendered page was reviewed.  ``operator``
    approval means a human explicitly supplied or confirmed the decision.
    OCR-only hints are intentionally not represented as approving evidence.
    """

    field: str
    source_type: EvidenceSourceType
    page: Optional[int] = None
    operator_approval_ref: Optional[str] = None
    approved_by_operator: bool = False


class ColumnDescriptor(BaseModel):
    key: str
    label: str
    kind: ColumnKind
    parent_key: Optional[str] = None
    parent_label: Optional[str] = None
    value_type: Literal["text", "count", "currency", "number", "percent"] = "text"


class ComponentColumnGroup(BaseModel):
    parent_key: str
    label: str
    child_keys: list[str]


class MethodSummary(BaseModel):
    assessment_method: str
    display_basis: str
    source_pages: list[int] = []
    homeowner_visible_notes: list[str] = []
    render_source_pages: bool = False


class ComponentSummaryRow(BaseModel):
    component_key: str
    component_label: str
    allocation_method: str
    recipient_scope: str
    denominator_basis: Optional[str] = None
    annual_amount: Optional[Union[Decimal, str]] = None
    monthly_amount: Optional[Union[Decimal, str]] = None
    parent_pool_key: Optional[str] = None


class ReviewNote(BaseModel):
    message: str
    severity: Literal["info", "warning", "blocking"] = "info"
    homeowner_visible: bool = False


class LayoutHints(BaseModel):
    orientation: LayoutOrientation = "portrait"
    split_strategy: SplitStrategy = "none"
    repeat_recipient_columns: bool = False
    visible_column_count: int = 0


class SummaryAssessmentRow(BaseModel):
    recipient_grain: Literal["summary"] = "summary"
    recipient_label: str = "All Units"
    unit_count: Optional[int] = None
    basis_values: dict[str, Any] = {}
    component_values_monthly_per_recipient: dict[str, Decimal] = {}
    total_monthly_per_recipient: Decimal = Decimal("0")
    annual_assessment_per_recipient: Decimal = Decimal("0")
    total_annual_revenue: Decimal = Decimal("0")
    optional_values: dict[str, Any] = {}


class GroupAssessmentRow(BaseModel):
    recipient_grain: Literal["group"] = "group"
    recipient_label: str
    unit_count: int
    basis_values: dict[str, Any] = {}
    component_values_monthly_per_recipient: dict[str, Decimal] = {}
    total_monthly_per_recipient: Decimal = Decimal("0")
    total_monthly_budget: Decimal = Decimal("0")
    annual_total: Decimal = Decimal("0")
    optional_values: dict[str, Any] = {}


class UnitAssessmentRow(BaseModel):
    recipient_grain: Literal["unit"] = "unit"
    recipient_label: str
    basis_values: dict[str, Any] = {}
    component_values_monthly: dict[str, Decimal] = {}
    total_monthly_assessment: Decimal = Decimal("0")
    annual_total: Decimal = Decimal("0")
    optional_values: dict[str, Any] = {}


class ManualReviewAssessmentRow(BaseModel):
    recipient_grain: Literal["manual_review"] = "manual_review"
    recipient_label: str = "Manual review required"
    missing_basis_reason: str
    basis_values: dict[str, Any] = {}
    optional_values: dict[str, Any] = {}


MatrixRow = Union[
    SummaryAssessmentRow,
    GroupAssessmentRow,
    UnitAssessmentRow,
    ManualReviewAssessmentRow,
]


class FooterRow(BaseModel):
    kind: FooterRowKind
    label: str
    values: dict[str, Any] = {}


class SpecialAssessmentAllocationRow(BaseModel):
    recipient_label: str
    amount: Decimal


class SpecialAssessmentDisclosureBlock(BaseModel):
    label: str
    amount_per_unit: Optional[Decimal] = None
    due_date: Optional[str] = None
    display_language: Optional[str] = None
    # Pool-based special assessments (add-variable-special-assessments): the
    # per-recipient allocation table plus its total and basis. Empty on the
    # legacy settings-json disclosure blocks (which carry only amount_per_unit).
    pool_key: Optional[str] = None
    total: Optional[Decimal] = None
    allocation_method: Optional[str] = None
    allocations: list[SpecialAssessmentAllocationRow] = []


class AssessmentScheduleMatrix(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    title: str
    hoa: dict[str, Any]
    fiscal_year: int
    recipient_grain: RecipientGrain
    method_summary: MethodSummary
    component_summary_rows: list[ComponentSummaryRow] = []
    basis_columns: list[ColumnDescriptor] = []
    component_columns: list[ColumnDescriptor] = []
    component_column_groups: list[ComponentColumnGroup] = []
    total_columns: list[ColumnDescriptor] = []
    optional_columns: list[ColumnDescriptor] = []
    evidence_refs: list[EvidenceRef] = []
    rows: list[MatrixRow] = Field(default_factory=list)
    footer_rows: list[FooterRow] = []
    homeowner_visible_notes: list[str] = []
    internal_review_notes: list[ReviewNote] = []
    preflight_issues: list[PreflightError] = []
    layout_hints: LayoutHints = Field(default_factory=LayoutHints)
    special_assessment_blocks: list[SpecialAssessmentDisclosureBlock] = []
    source_pages_visible: bool = False
    # Option B: per-pool mapped line ops vs replacement-share totals for the
    # dual-fund P&L (not used by the homeowner schedule grid). Snapshotted with
    # the matrix so finalize does not need live mapping rows.
    pool_line_fund_totals: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @property
    def is_final_renderable(self) -> bool:
        return self.recipient_grain != "manual_review" and not any(
            issue.severity == "blocking" for issue in self.preflight_issues
        )


def _zero() -> Decimal:
    return Decimal("0")


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _money(value: Any) -> Decimal:
    if value is None:
        return _zero()
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


# C8: the per-value `>1 → points` heuristic that used to live here
# (`_percentage`) is retired from every calculation path. Percent form is
# resolved COLUMN-level via assessment_engine.percent_form — a points-form
# column whose individual values are below 1 (e.g. 150 units × ~0.667)
# is exactly the case the per-value guess mis-read as fractions (~100×
# over-assessment). Do not reintroduce per-value magnitude guessing.


def _monthly(value: Decimal) -> Decimal:
    return (value / Decimal("12")).quantize(Decimal("0.01"))


def _safe_divide(value: Decimal, divisor: int | Decimal | None) -> Decimal:
    if not divisor:
        return _zero()
    return value / Decimal(str(divisor))


def _title_for(hoa_name: str, fiscal_year: int, grain: RecipientGrain) -> str:
    if grain == "summary":
        subtitle = "Regular Assessment Summary"
    elif grain == "group":
        subtitle = "Assessments Per Unit Type Per Month"
    elif grain == "unit":
        subtitle = "Assessments Per Unit Per Month"
    else:
        subtitle = "Assessment Schedule Pending Review"
    return f"{hoa_name}\n{fiscal_year} {subtitle}"


def _method_summary_for(
    *,
    grain: RecipientGrain,
    setup_type: SetupType,
    component_columns: list[ColumnDescriptor],
    basis_columns: list[ColumnDescriptor],
    source_pages: list[int],
    homeowner_visible_notes: list[str],
    render_source_pages: bool,
) -> MethodSummary:
    component_count = len(component_columns)
    basis_keys = {col.key for col in basis_columns}

    if grain == "manual_review":
        method = "Assessment basis is not visible or has not been approved."
        display_basis = "Manual review required before final rendering."
    elif grain == "summary":
        method = "All units pay the same regular monthly assessment."
        display_basis = "Fixed summary."
    elif grain == "group" and "avg_sq_ft" in basis_keys and component_count > 1:
        method = (
            "Assessments are calculated using a square-footage variable "
            "component plus an equal/base component."
        )
        display_basis = "Grouped schedule."
    elif grain == "group":
        method = "Each approved group pays the applicable monthly assessment for that type."
        display_basis = "Grouped schedule."
    elif component_count > 1 and grain == "unit":
        method = "Assessments are calculated from multiple approved components."
        display_basis = "Per-unit multi-component schedule."
    elif "percent_of_total" in basis_keys:
        method = "Assessments are allocated by each unit's approved ownership percentage."
        display_basis = "Per-unit schedule."
    elif "sq_ft" in basis_keys or "avg_sq_ft" in basis_keys:
        method = "Assessments use approved square-footage or size factors."
        display_basis = "Grouped schedule by unit type." if grain == "group" else "Per-unit schedule."
    elif "category" in basis_keys or "unit_type" in basis_keys:
        method = "Assessments are shown by approved category or unit type."
        display_basis = "Grouped schedule by unit type."
    else:
        method = "Assessments follow the approved allocation setup."
        display_basis = f"{grain.replace('_', ' ').title()} schedule."

    return MethodSummary(
        assessment_method=method,
        display_basis=display_basis,
        source_pages=source_pages,
        homeowner_visible_notes=homeowner_visible_notes,
        render_source_pages=render_source_pages,
    )


def _pool_key(pool: Any) -> str:
    return str(_get(pool, "pool_key"))


def _pool_label(pool: Any) -> str:
    return str(_get(pool, "pool_name") or _get(pool, "label") or _pool_key(pool))


def _pool_display_order(pool: Any) -> int:
    return int(_get(pool, "display_order", 0) or 0)


def _parent_pool_key(pool: Any) -> Optional[str]:
    value = _get(pool, "parent_pool_key")
    return str(value) if value else None


def _pool_is_visible(pool: Any) -> bool:
    # A special-assessment pool is never a regular monthly column — it renders in
    # its own allocation table. Checked BEFORE include_in_pdf (which is always
    # non-None on PoolDefinition, so the hidden_kinds branch below would never
    # otherwise run for it).
    if str(_get(pool, "pool_kind", "") or "") == SPECIAL_ASSESSMENT_POOL_KIND:
        return False

    explicit = _get(pool, "include_in_pdf")
    if explicit is not None:
        return bool(explicit)

    pool_kind = str(_get(pool, "pool_kind", "") or _get(pool, "event_type", ""))
    hidden_kinds = {
        "unresolved_pass_through",
        "unresolved_reimbursement",
        "separately_billed_special_assessment",
        "internal_offset",
        "reconciliation",
    }
    return pool_kind not in hidden_kinds


def _column_value_type(key: str, label: str) -> Literal["text", "count", "currency", "number", "percent"]:
    normalized = f"{key} {label}".lower()
    if "percent" in normalized:
        return "percent"
    if "count" in normalized or "spaces" in normalized or "units" in normalized:
        return "count"
    if any(word in normalized for word in ("assessment", "budget", "amount", "liability", "deficit", "surplus", "total")):
        return "currency"
    if "sq ft" in normalized or "factor" in normalized:
        return "number"
    return "text"


def _basis_values_for_ref(ref: Any, *, grain: RecipientGrain) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if grain in {"summary", "group"} and _get(ref, "unit_count") is not None:
        values["unit_count"] = _get(ref, "unit_count")
    if grain == "group" and _get(ref, "label"):
        values["unit_type"] = _get(ref, "label")
    if _get(ref, "square_feet") is not None:
        values["avg_sq_ft" if grain == "group" else "sq_ft"] = _get(ref, "square_feet")
    ownership_percent = _get(ref, "ownership_percent")
    if ownership_percent is not None and _money(ownership_percent) != Decimal("0"):
        # ownership_percent is a normalized fraction (e.g. 0.1315). The
        # "percent" display formatter appends "%" to the value as-is, so store
        # the percentage number (13.15) here — otherwise it renders "0.1315%".
        values["percent_of_total"] = _money(ownership_percent) * Decimal("100")
    if _get(ref, "category"):
        values["category"] = _get(ref, "category")
    if _get(ref, "parking_spaces", 0):
        values["parking_spaces"] = _get(ref, "parking_spaces")
    return values


def _basis_columns(
    *,
    grain: RecipientGrain,
    rows_basis: list[dict[str, Any]],
    visible_pools: list[Any],
) -> list[ColumnDescriptor]:
    keys: set[str] = set()
    for basis in rows_basis:
        keys.update(k for k, v in basis.items() if v not in (None, "", Decimal("0"), 0))

    methods = {str(_get(pool, "allocation_method", "")) for pool in visible_pools}
    scopes = {str(_get(pool, "recipient_scope", "")) for pool in visible_pools}
    if "square_footage" in methods:
        keys.add("avg_sq_ft" if grain == "group" else "sq_ft")
    if "ownership_percentage" in methods:
        keys.add("percent_of_total")
    if "custom_factor" in methods:
        keys.add("custom_factor")
    if "parking_users" in scopes:
        keys.add("parking_spaces")
    if grain in {"summary", "group"} and any("unit_count" in b for b in rows_basis):
        keys.add("unit_count")

    labels = {
        "unit_count": "Unit Count",
        "sq_ft": "Sq Ft",
        "avg_sq_ft": "Avg Sq Ft",
        "percent_of_total": "Percent of Total",
        "category": "Category",
        "unit_type": "Unit Type",
        "parking_spaces": "Parking Spaces",
        "custom_factor": "Custom Factor",
    }
    order = [
        "unit_count",
        "category",
        "unit_type",
        "sq_ft",
        "avg_sq_ft",
        "percent_of_total",
        "parking_spaces",
        "custom_factor",
    ]
    return [
        ColumnDescriptor(
            key=key,
            label=labels.get(key, key.replace("_", " ").title()),
            kind="basis",
            value_type=_column_value_type(key, labels.get(key, key)),
        )
        for key in order
        if key in keys
    ]


def _optional_columns(optional_values_by_recipient: dict[Any, dict[str, Any]]) -> list[ColumnDescriptor]:
    keys: set[str] = set()
    for values in optional_values_by_recipient.values():
        keys.update(k for k, v in values.items() if v not in (None, ""))

    labels = {
        "unfunded_liability": "Unfunded Liability",
        "underfunded_liability": "Underfunded Liability",
        "reserve_deficit": "Reserve Deficit",
        "reserve_surplus": "Reserve Surplus",
        "special_liability": "Special Liability",
        "prior_year_assessment": "Prior Year Assessment",
        "current_year_assessment": "Current Year Assessment",
        "difference": "Difference",
        "percent_change": "Percent Change",
    }
    order = [
        "unfunded_liability",
        "underfunded_liability",
        "reserve_deficit",
        "reserve_surplus",
        "special_liability",
        "prior_year_assessment",
        "current_year_assessment",
        "difference",
        "percent_change",
    ]
    return [
        ColumnDescriptor(
            key=key,
            label=labels[key],
            kind="optional",
            value_type=_column_value_type(key, labels[key]),
        )
        for key in order
        if key in keys
    ]


def _component_columns(visible_pools: list[Any]) -> tuple[list[ColumnDescriptor], list[ComponentColumnGroup]]:
    columns: list[ColumnDescriptor] = []
    grouped: dict[str, list[str]] = defaultdict(list)
    parent_labels: dict[str, str] = {}

    for pool in sorted(visible_pools, key=_pool_display_order):
        key = _pool_key(pool)
        parent_key = _parent_pool_key(pool)
        columns.append(
            ColumnDescriptor(
                key=key,
                label=_pool_label(pool),
                kind="component",
                parent_key=parent_key,
                parent_label=(
                    str(_get(pool, "parent_pool_label"))
                    if _get(pool, "parent_pool_label")
                    else parent_key.replace("_", " ").title()
                    if parent_key
                    else None
                ),
                value_type="currency",
            )
        )
        if parent_key:
            grouped[parent_key].append(key)
            parent_labels[parent_key] = str(
                _get(pool, "parent_pool_label") or parent_key.replace("_", " ").title()
            )

    groups = [
        ComponentColumnGroup(
            parent_key=parent_key,
            label=parent_labels[parent_key],
            child_keys=child_keys,
        )
        for parent_key, child_keys in grouped.items()
    ]
    return columns, groups


def _total_columns_for(grain: RecipientGrain) -> list[ColumnDescriptor]:
    if grain == "summary":
        return [
            ColumnDescriptor(
                key="total_monthly_per_recipient",
                label="Monthly Assessment",
                kind="total",
                value_type="currency",
            ),
            ColumnDescriptor(
                key="annual_assessment_per_recipient",
                label="Annual Assessment",
                kind="total",
                value_type="currency",
            ),
            ColumnDescriptor(
                key="total_annual_revenue",
                label="Total Annual Revenue",
                kind="total",
                value_type="currency",
            ),
        ]
    if grain == "group":
        return [
            ColumnDescriptor(
                key="total_monthly_per_recipient",
                label="Monthly Assessment Per Unit/Type",
                kind="total",
                value_type="currency",
            ),
            ColumnDescriptor(
                key="total_monthly_budget",
                label="Total Monthly Budget",
                kind="total",
                value_type="currency",
            ),
            ColumnDescriptor(
                key="annual_total",
                label="Annual Total",
                kind="total",
                value_type="currency",
            ),
        ]
    if grain == "unit":
        return [
            ColumnDescriptor(
                key="total_monthly_assessment",
                label="Total Monthly Assessment",
                kind="total",
                value_type="currency",
            ),
            ColumnDescriptor(
                key="annual_total",
                label="Annual Assessment",
                kind="total",
                value_type="currency",
            ),
        ]
    return []


def _component_summary_row(pool: Any, annual_by_pool: dict[str, Decimal]) -> ComponentSummaryRow:
    key = _pool_key(pool)
    allocation_method = str(_get(pool, "allocation_method") or "")
    annual_amount = annual_by_pool.get(key)
    monthly_amount = _monthly(annual_amount) if annual_amount is not None else None

    if allocation_method == "specified_value" and not bool(_get(pool, "reliable_pool_total", False)):
        annual_display: Union[Decimal, str, None] = "Varies by recipient"
        monthly_display: Union[Decimal, str, None] = "Varies by recipient"
    else:
        annual_display = annual_amount
        monthly_display = monthly_amount

    denominator = _get(pool, "denominator_value")
    denominator_basis = str(denominator) if denominator is not None else None
    return ComponentSummaryRow(
        component_key=key,
        component_label=_pool_label(pool),
        allocation_method=allocation_method,
        recipient_scope=str(_get(pool, "recipient_scope") or ""),
        denominator_basis=denominator_basis,
        annual_amount=annual_display,
        monthly_amount=monthly_display,
        parent_pool_key=_parent_pool_key(pool),
    )


def _layout_hints(
    *,
    basis_count: int,
    component_count: int,
    total_count: int,
    optional_count: int,
    has_component_groups: bool,
) -> LayoutHints:
    visible_count = 1 + basis_count + component_count + total_count + optional_count
    if has_component_groups:
        split_strategy: SplitStrategy = "by_component_group"
    elif visible_count > 9:
        split_strategy = "continuation_pages"
    else:
        split_strategy = "none"

    return LayoutHints(
        orientation="landscape" if visible_count > 7 else "portrait",
        split_strategy=split_strategy,
        repeat_recipient_columns=visible_count > 9 or split_strategy != "none",
        visible_column_count=visible_count,
    )


def _manual_review_matrix(
    *,
    hoa_name: str,
    fiscal_year: int,
    reason: str,
    source_pages: list[int],
    homeowner_visible_notes: list[str],
    internal_review_notes: list[ReviewNote],
    evidence_refs: list[EvidenceRef],
    source_pages_visible: bool,
) -> AssessmentScheduleMatrix:
    issue = PreflightError(
        field_path="assessment_schedule.recipient_grain",
        message=reason,
        severity="blocking",
    )
    method_summary = _method_summary_for(
        grain="manual_review",
        setup_type="fixed",
        component_columns=[],
        basis_columns=[],
        source_pages=source_pages,
        homeowner_visible_notes=homeowner_visible_notes,
        render_source_pages=source_pages_visible,
    )
    if "budget lines are not mapped" in reason.lower():
        method_summary = MethodSummary(
            assessment_method=(
                "Approved DRE assessment setup is present, but current-year "
                "budget pool mappings are incomplete."
            ),
            display_basis="Budget mapping review required before final rendering.",
            source_pages=source_pages,
            homeowner_visible_notes=homeowner_visible_notes,
            render_source_pages=source_pages_visible,
        )
    return AssessmentScheduleMatrix(
        title=_title_for(hoa_name, fiscal_year, "manual_review"),
        hoa={"name": hoa_name},
        fiscal_year=fiscal_year,
        recipient_grain="manual_review",
        method_summary=method_summary,
        evidence_refs=evidence_refs,
        rows=[ManualReviewAssessmentRow(missing_basis_reason=reason)],
        homeowner_visible_notes=homeowner_visible_notes,
        internal_review_notes=internal_review_notes,
        preflight_issues=[issue],
        layout_hints=LayoutHints(visible_column_count=1),
        source_pages_visible=source_pages_visible,
    )


def _build_evidence_refs(source_pages: list[int]) -> list[EvidenceRef]:
    if not source_pages:
        return []
    first = source_pages[0]
    return [
        EvidenceRef(field="recipient_grain", source_type="visual_page", page=first),
        EvidenceRef(field="basis_columns", source_type="visual_page", page=first),
        EvidenceRef(field="component_columns", source_type="visual_page", page=first),
        EvidenceRef(field="optional_columns", source_type="visual_page", page=first),
    ]


def _money_routing_issue_messages(result: Any) -> list[str]:
    """Turn the engine's H1/H2 money-routing reports into named,
    operator-resolvable messages.

    Each message names the specific budget line(s), the specific assessment
    category, the
    dollars at stake, and the in-app action to take — so the operator can
    resolve it on the Assessment Mapping Review screen rather than
    re-running the exact same failing generation (an unresolvable loop).
    """
    messages: list[str] = []
    for orphan in getattr(result, "orphaned_pool_lines", []) or []:
        lines = ", ".join(orphan.contributing_line_labels) or "(unnamed lines)"
        messages.append(
            f"Budget line(s) [{lines}] are mapped to assessment category "
            f"'{orphan.pool_key}', which no longer exists in the approved "
            f"setup (${orphan.annual_total} annual). Remap them to a current "
            f"assessment category or exclude them on the Assessment Mapping Review screen."
        )
    for zero in getattr(result, "zero_recipient_pools", []) or []:
        lines = ", ".join(zero.contributing_line_labels) or "(unnamed lines)"
        # A generated Assessment Income component can preserve a DRE split for
        # a category that this year's roster does not use (for example, a
        # parking-only category in an HOA with no parking users). It is not an
        # operator-routed budget line; direct mappings still fail loudly below.
        if zero.contributing_line_labels and all(
            label.startswith(
                ("assessment_revenue_component:", "generated_assessment_revenue:")
            )
            for label in zero.contributing_line_labels
        ):
            continue
        messages.append(
            f"Assessment category '{zero.pool_key}' (scope '{zero.recipient_scope}') carries "
            f"${zero.annual_total} annual from budget line(s) [{lines}] but no "
            f"units match its scope, so those dollars cannot be billed. Remap "
            f"the line(s) to an assessment category that has recipients, exclude them, or fix "
            f"the unit categories in the DRE Review Workbench and repromote."
        )
    return messages


def _child_pool_mapping_issues(
    pool_definitions: list[Any],
    pool_totals_annual: dict[str, Decimal] | None = None,
) -> list[PreflightError]:
    issues: list[PreflightError] = []
    for pool in pool_definitions:
        parent_key = _parent_pool_key(pool)
        if not parent_key:
            continue
        if pool_totals_annual is not None and category_is_idle_this_year(
            mapped_annual=pool_totals_annual.get(_pool_key(pool)),
        ):
            continue
        included_lines = _get(pool, "included_budget_lines", None)
        mapping_status = str(_get(pool, "child_mapping_status", "") or "")
        approved = bool(_get(pool, "child_mapping_approved", False))
        needs_dollars = bool(_get(pool, "needs_current_year_dollars", True))
        if needs_dollars and (included_lines is None or mapping_status == "copied_from_parent"):
            issues.append(PreflightError(
                field_path=f"assessment_schedule.component_pools.{_pool_key(pool)}",
                message=(
                    f"Child assessment category {_pool_label(pool)!r} needs approved child-level "
                    "budget-line mappings before final rendering."
                ),
                severity="blocking",
            ))
        elif needs_dollars and included_lines and not approved:
            issues.append(PreflightError(
                field_path=f"assessment_schedule.component_pools.{_pool_key(pool)}",
                message=(
                    f"Child assessment category {_pool_label(pool)!r} has budget lines but they "
                    "are not approved for child-level display."
                ),
                severity="blocking",
            ))
    return issues


def build_universal_assessment_matrix(
    result: CalcResultSet,
    *,
    setup_type: SetupType,
    hoa_name: str,
    fiscal_year: int,
    pool_definitions: list[Any] | None = None,
    approved_visual_basis: bool = True,
    source_pages: list[int] | None = None,
    evidence_refs: list[EvidenceRef] | None = None,
    source_pages_visible: bool = False,
    homeowner_visible_notes: list[str] | None = None,
    internal_review_notes: list[str | ReviewNote] | None = None,
    optional_values_by_recipient: dict[Any, dict[str, Any]] | None = None,
    footer_rows: list[FooterRow] | None = None,
    pending_review_issues: list[PreflightError] | None = None,
    manual_review_reason: str = "Assessment allocation basis is not visible or approved.",
    pool_line_fund_totals: dict[str, dict[str, Any]] | None = None,
) -> AssessmentScheduleMatrix:
    """Build one universal assessment schedule matrix from engine output.

    The builder consumes pool allocation rows for component values and
    recipient totals for bottom-line values.  It does not sum visible rows to
    produce footer totals; callers may supply footer rows when reconciliation
    display is needed.
    """
    source_pages = source_pages or []
    homeowner_visible_notes = homeowner_visible_notes or []
    optional_values_by_recipient = optional_values_by_recipient or {}
    pool_definitions = pool_definitions or []
    evidence_refs = evidence_refs if evidence_refs is not None else _build_evidence_refs(source_pages)
    normalized_internal_notes = [
        note if isinstance(note, ReviewNote) else ReviewNote(message=str(note), severity="warning")
        for note in (internal_review_notes or [])
    ]
    normalized_internal_notes.extend(
        ReviewNote(message=warning, severity="warning")
        for warning in result.warnings
    )

    if not approved_visual_basis:
        return _manual_review_matrix(
            hoa_name=hoa_name,
            fiscal_year=fiscal_year,
            reason=manual_review_reason,
            source_pages=source_pages,
            homeowner_visible_notes=homeowner_visible_notes,
            internal_review_notes=normalized_internal_notes,
            evidence_refs=evidence_refs,
            source_pages_visible=source_pages_visible,
        )

    grain: RecipientGrain = {
        "fixed": "summary",
        "grouped": "group",
        "per_unit": "unit",
    }[setup_type]

    allocation_keys = {row.pool_key for row in result.pool_allocations}
    pool_by_key: dict[str, Any] = {_pool_key(pool): pool for pool in pool_definitions}
    synthetic_pools: list[PoolDefinition] = []
    for pool_key in sorted(allocation_keys - set(pool_by_key)):
        synthetic_pools.append(PoolDefinition(
            pool_id=0,
            pool_key=pool_key,
            pool_name=pool_key.replace("_", " ").title(),
            allocation_method="specified_value",
            recipient_scope="all_units",
            include_in_pdf=True,
            display_order=len(pool_by_key) + len(synthetic_pools),
        ))
    all_pools: list[Any] = [*pool_definitions, *synthetic_pools]
    visible_pools = [pool for pool in all_pools if _pool_is_visible(pool)]
    visible_pool_keys = {_pool_key(pool) for pool in visible_pools}

    components_by_recipient: dict[tuple[str, int], dict[str, Decimal]] = defaultdict(
        lambda: defaultdict(_zero)
    )
    annual_by_pool: dict[str, Decimal] = defaultdict(_zero)
    for row in result.pool_allocations:
        if row.pool_key not in visible_pool_keys:
            continue
        ref_key = (row.recipient_ref.ref_type, row.recipient_ref.ref_id)
        amount = row.unrounded_component_monthly
        components_by_recipient[ref_key][row.pool_key] += amount
        annual_by_pool[row.pool_key] += amount * Decimal("12")

    rows: list[MatrixRow] = []
    rows_basis: list[dict[str, Any]] = []
    matching_totals = [
        total for total in result.recipient_totals
        if (grain == "summary" and total.recipient_ref.ref_type == "unit")
        or total.recipient_ref.ref_type == grain
    ]

    if grain == "summary":
        unit_count = len(matching_totals)
        monthly_counts = Counter(t.rounded_monthly_total for t in matching_totals)
        monthly_per_recipient = monthly_counts.most_common(1)[0][0] if monthly_counts else _zero()
        annual_per_recipient = monthly_per_recipient * Decimal("12")
        total_annual = sum((t.annual_total for t in matching_totals), start=_zero())
        component_values: dict[str, Decimal] = {}
        if matching_totals:
            first_ref = matching_totals[0].recipient_ref
            first_components = components_by_recipient[(first_ref.ref_type, first_ref.ref_id)]
            component_values = {
                key: first_components.get(key, _zero())
                for key in visible_pool_keys
            }
        basis = {"unit_count": unit_count}
        rows_basis.append(basis)
        rows.append(SummaryAssessmentRow(
            unit_count=unit_count,
            basis_values=basis,
            component_values_monthly_per_recipient=component_values,
            total_monthly_per_recipient=monthly_per_recipient,
            annual_assessment_per_recipient=annual_per_recipient,
            total_annual_revenue=total_annual,
        ))

    elif grain == "group":
        for total in matching_totals:
            ref = total.recipient_ref
            unit_count = int(ref.unit_count or 1)
            basis = _basis_values_for_ref(ref, grain=grain)
            rows_basis.append(basis)
            raw_components = components_by_recipient[(ref.ref_type, ref.ref_id)]
            components = {
                key: _safe_divide(raw_components.get(key, _zero()), unit_count)
                for key in visible_pool_keys
            }
            rows.append(GroupAssessmentRow(
                recipient_label=ref.label,
                unit_count=unit_count,
                basis_values=basis,
                component_values_monthly_per_recipient=components,
                total_monthly_per_recipient=_safe_divide(total.rounded_monthly_total, unit_count),
                total_monthly_budget=total.rounded_monthly_total,
                annual_total=total.annual_total,
                optional_values=optional_values_by_recipient.get(ref.ref_id, {}),
            ))

    elif grain == "unit":
        for total in matching_totals:
            ref = total.recipient_ref
            basis = _basis_values_for_ref(ref, grain=grain)
            rows_basis.append(basis)
            raw_components = components_by_recipient[(ref.ref_type, ref.ref_id)]
            rows.append(UnitAssessmentRow(
                recipient_label=ref.label,
                basis_values=basis,
                component_values_monthly={
                    key: raw_components.get(key, _zero())
                    for key in visible_pool_keys
                },
                total_monthly_assessment=total.rounded_monthly_total,
                annual_total=total.annual_total,
                optional_values=optional_values_by_recipient.get(ref.ref_id, {}),
            ))

    basis_columns = _basis_columns(
        grain=grain,
        rows_basis=rows_basis,
        visible_pools=visible_pools,
    )
    component_columns, component_groups = _component_columns(visible_pools)
    total_columns = _total_columns_for(grain)
    optional_columns = _optional_columns(optional_values_by_recipient)
    component_summary_rows = [
        _component_summary_row(pool, annual_by_pool)
        for pool in visible_pools
    ]

    mapped_annual_all: dict[str, Decimal] = defaultdict(_zero)
    for row in result.pool_allocations:
        mapped_annual_all[row.pool_key] += row.unrounded_component_monthly * Decimal("12")

    preflight_issues = list(pending_review_issues or [])
    preflight_issues.extend(
        _child_pool_mapping_issues(
            pool_definitions,
            pool_totals_annual=mapped_annual_all,
        )
    )
    if not evidence_refs:
        preflight_issues.append(PreflightError(
            field_path="assessment_schedule.evidence_refs",
            message=(
                "Assessment matrix needs visual source evidence or operator "
                "approval before final rendering."
            ),
            severity="blocking",
        ))

    layout_hints = _layout_hints(
        basis_count=len(basis_columns),
        component_count=len(component_columns),
        total_count=len(total_columns),
        optional_count=len(optional_columns),
        has_component_groups=bool(component_groups),
    )
    method_summary = _method_summary_for(
        grain=grain,
        setup_type=setup_type,
        component_columns=component_columns,
        basis_columns=basis_columns,
        source_pages=source_pages,
        homeowner_visible_notes=homeowner_visible_notes,
        render_source_pages=source_pages_visible,
    )

    special_blocks = [
        SpecialAssessmentDisclosureBlock(
            label=event.label or "Special Assessment",
            amount_per_unit=event.amount_per_unit,
            due_date=event.due_date,
            display_language=event.display_language,
        )
        for event in result.special_assessment_events
        if event.kind == "separate_disclosure_block"
    ]
    # Pool-based special assessments: one block per special-kind pool, carrying
    # the per-recipient allocation table (the single render channel — no separate
    # matrix surface). For an equal split every row is the same; for a variable
    # split the rows differ and the template shows the table, not one per-unit
    # figure.
    special_blocks.extend(
        SpecialAssessmentDisclosureBlock(
            label=allocation.label or "Special Assessment",
            pool_key=allocation.pool_key,
            total=allocation.total,
            allocation_method=allocation.allocation_method,
            allocations=[
                SpecialAssessmentAllocationRow(
                    recipient_label=entry.recipient_ref.label,
                    amount=entry.amount,
                )
                for entry in allocation.entries
            ],
        )
        for allocation in result.special_assessment_allocations
    )

    return AssessmentScheduleMatrix(
        title=_title_for(hoa_name, fiscal_year, grain),
        hoa={"name": hoa_name},
        fiscal_year=fiscal_year,
        recipient_grain=grain,
        method_summary=method_summary,
        component_summary_rows=component_summary_rows,
        basis_columns=basis_columns,
        component_columns=component_columns,
        component_column_groups=component_groups,
        total_columns=total_columns,
        optional_columns=optional_columns,
        pool_line_fund_totals=dict(pool_line_fund_totals or {}),
        evidence_refs=evidence_refs,
        rows=rows,
        footer_rows=footer_rows or [],
        homeowner_visible_notes=homeowner_visible_notes,
        internal_review_notes=normalized_internal_notes,
        preflight_issues=preflight_issues,
        layout_hints=layout_hints,
        special_assessment_blocks=special_blocks,
        source_pages_visible=source_pages_visible,
    )


def validate_assessment_matrix_finalization(
    matrix: AssessmentScheduleMatrix,
    *,
    dre_setup_approved: bool = True,
    required_budget_lines_unmapped: bool = False,
    mapping_review_blockers: dict[str, list[str]] | None = None,
    reconciliation_failures: list[str] | None = None,
    special_assessment_settings_complete: bool = True,
    unit_count_mismatch_unresolved: bool = False,
) -> list[PreflightError]:
    """Return final-render blockers for the assessment matrix.

    This helper is deliberately small and deterministic so package preflight
    can call it without understanding raw DRE extraction or renderer details.
    """
    errors = list(matrix.preflight_issues)
    if not dre_setup_approved:
        errors.append(PreflightError(
            field_path="assessment_setup.status",
            message="DRE assessment setup must be approved before final rendering.",
            severity="blocking",
        ))
    if matrix.recipient_grain == "manual_review":
        errors.append(PreflightError(
            field_path="assessment_schedule.recipient_grain",
            message="Assessment schedule is pending manual review.",
            severity="blocking",
        ))
    if not matrix.evidence_refs:
        errors.append(PreflightError(
            field_path="assessment_schedule.evidence_refs",
            message="Visual evidence or operator approval is required.",
            severity="blocking",
        ))
    if required_budget_lines_unmapped:
        errors.append(PreflightError(
            field_path="assessment_schedule.budget_line_mappings",
            message="Required budget lines are not mapped to assessment categories.",
            severity="blocking",
        ))
    for category, details in (mapping_review_blockers or {}).items():
        if not details:
            continue
        errors.append(PreflightError(
            field_path=f"assessment_schedule.mapping_review.{category}",
            message=(
                f"Assessment mapping review blocker: {category}: "
                f"{', '.join(str(detail) for detail in details)}"
            ),
            severity="blocking",
        ))
    for failure in reconciliation_failures or []:
        errors.append(PreflightError(
            field_path="assessment_schedule.reconciliation",
            message=f"Assessment mapping reconciliation failed: {failure}",
            severity="blocking",
        ))
    if not special_assessment_settings_complete:
        errors.append(PreflightError(
            field_path="hoa_settings.special_assessments_json",
            message="Special assessment settings are incomplete.",
            severity="blocking",
        ))
    if unit_count_mismatch_unresolved:
        errors.append(PreflightError(
            field_path="assessment_schedule.unit_count",
            message="Unit-count mismatch must be resolved before final rendering.",
            severity="blocking",
        ))
    displayed_pool_keys = {col.key for col in matrix.component_columns}
    summary_pool_keys = {row.component_key for row in matrix.component_summary_rows}
    missing_pool_sources = displayed_pool_keys - summary_pool_keys
    for pool_key in sorted(missing_pool_sources):
        errors.append(PreflightError(
            field_path=f"assessment_schedule.component_columns.{pool_key}",
            message=f"Displayed component {pool_key!r} has no matching pool/source summary.",
            severity="blocking",
        ))
    return errors


def _line_to_engine_input(line_id: int, line: Any) -> BudgetLineInput:
    label = str(_get(line, "label"))
    raw_category = str(_get(line, "category", "") or "").strip()
    is_revenue = bool(_get(line, "is_revenue", False))
    is_reserve = bool(_get(line, "is_reserve", False))
    if raw_category in {"income", "operating", "reserve_income", "reserve_expense"}:
        category = raw_category
    elif is_revenue:
        category = "reserve_income" if is_reserve else "income"
    else:
        category = "reserve_expense" if is_reserve else "operating"
    amount, _source_column_used = select_assessment_mapping_amount(
        {
            "assessment_mapping_amount": _get(line, "assessment_mapping_amount"),
            "source_column_used": _get(line, "source_column_used"),
            "proposed_amount": _get(line, "proposed_amount"),
            "proposedAmount": _get(line, "proposedAmount"),
            "annual_budget": _get(line, "annual_budget"),
            "projection": _get(line, "projection"),
            "amount": _get(line, "amount"),
        }
    )
    raw = _get(line, "raw", {}) or {}
    section = (
        _get(line, "section")
        or (raw.get("section") if isinstance(raw, dict) else None)
        or (raw.get("Section") if isinstance(raw, dict) else None)
        or ("income" if category == "income" else "operating")
    )
    account_code = _get(line, "account_code")
    normalized_label = normalize_budget_label(label)
    fund_type = (
        "reserve"
        if is_reserve or category in {"reserve_income", "reserve_expense"}
        else "operating"
    )
    source_line_key = build_budget_line_slice_key(
        normalized_label=normalized_label,
        section=str(section),
        category=category,
        fund_type=fund_type,
        account_code=(
            str(account_code) if account_code not in (None, "") else None
        ),
    )
    return BudgetLineInput(
        line_id=line_id,
        normalized_label=normalized_label,
        section=str(section),
        category=category,  # type: ignore[arg-type]
        fund_type=fund_type,
        account_code=str(account_code) if account_code not in (None, "") else None,
        amount=amount if amount is not None else _zero(),
        source_line_key=source_line_key,
    )


def _fallback_matrix_for_db_issue(
    *,
    hoa_name: str,
    fiscal_year: int,
    reason: str,
    approved_at: Optional[str] = None,
) -> AssessmentScheduleMatrix:
    evidence_refs = []
    if approved_at:
        evidence_refs.append(
            EvidenceRef(
                field="recipient_grain",
                source_type="operator_approval",
                operator_approval_ref=approved_at,
                approved_by_operator=True,
            )
        )
    return _manual_review_matrix(
        hoa_name=hoa_name,
        fiscal_year=fiscal_year,
        reason=reason,
        source_pages=[],
        homeowner_visible_notes=[],
        internal_review_notes=[ReviewNote(message=reason, severity="blocking")],
        evidence_refs=evidence_refs,
        source_pages_visible=False,
    )


def _blocking_matrix_for_issue(
    *,
    hoa_name: str,
    fiscal_year: int,
    field_path: str,
    reason: str,
) -> AssessmentScheduleMatrix:
    matrix = _fallback_matrix_for_db_issue(
        hoa_name=hoa_name,
        fiscal_year=fiscal_year,
        reason=reason,
    )
    matrix.preflight_issues = [
        PreflightError(
            field_path=field_path,
            message=reason,
            severity="blocking",
        )
    ]
    matrix.rows = [ManualReviewAssessmentRow(missing_basis_reason=reason)]
    return matrix


def _variable_mode_missing_setup_reason(
    *,
    connection: sqlite3.Connection,
    property_id: int,
) -> str:
    has_dre_upload = connection.execute(
        """
        SELECT 1
          FROM dre_documents
         WHERE property_id = ?
           AND status IN ('active', 'superseded')
         LIMIT 1
        """,
        (property_id,),
    ).fetchone()
    if has_dre_upload is None:
        return (
            "No approved DRE assessment setup was found for this HOA. "
            "Upload the DRE packet before final rendering."
        )
    return (
        "A DRE packet exists, but no approved DRE assessment setup was found for this HOA. "
        "Complete DRE review and approval before final rendering."
    )


def _build_matrix_for_fixed_mode(
    *,
    fiscal_year: int,
    hoa_name: str,
    unit_count: int,
    approved_assessment_revenue_annual: Decimal,
) -> AssessmentScheduleMatrix:
    if int(unit_count or 0) <= 0:
        return _blocking_matrix_for_issue(
            hoa_name=hoa_name,
            fiscal_year=fiscal_year,
            field_path="hoa_metadata.units",
            reason="Fixed assessment mode requires a valid HOA unit count before final rendering.",
        )
    if approved_assessment_revenue_annual <= Decimal("0"):
        return _blocking_matrix_for_issue(
            hoa_name=hoa_name,
            fiscal_year=fiscal_year,
            field_path="annual_package.approved_assessment_revenue_annual",
            reason="Fixed assessment mode requires approved annual assessment revenue before final rendering.",
        )

    recipients = [
        RecipientReference(ref_type="unit", ref_id=i, label=f"Unit {i}")
        for i in range(1, int(unit_count) + 1)
    ]
    pools = [
        PoolDefinition(
            pool_id=0,
            pool_key="equal_costs",
            pool_name="Equal Costs",
            allocation_method="equal",
            recipient_scope="all_units",
            include_in_pdf=True,
            display_order=1,
        )
    ]
    budget_lines = [
        BudgetLineInput(
            line_id=1,
            normalized_label=normalize_budget_label("Assessment Income"),
            section="income",
            category="income",
            fund_type="operating",
            account_code="40000",
            amount=approved_assessment_revenue_annual,
        )
    ]
    mappings = [
        BudgetLineMappingInput(
            budget_line_normalized_label=normalize_budget_label("Assessment Income"),
            section="income",
            category="income",
            fund_type="operating",
            account_code="40000",
            pool_key="equal_costs",
            active=True,
        )
    ]
    result = run_assessment_engine(
        CalcInput(
            setup_type="fixed",
            pools=pools,
            recipient_set=RecipientSet(recipients=recipients),
            budget_lines=budget_lines,
            mappings=mappings,
            approved_assessment_revenue_annual=approved_assessment_revenue_annual,
        )
    )
    return build_universal_assessment_matrix(
        result,
        setup_type="fixed",
        hoa_name=hoa_name,
        fiscal_year=fiscal_year,
        pool_definitions=pools,
        evidence_refs=[
            EvidenceRef(
                field="recipient_grain",
                source_type="operator_approval",
                operator_approval_ref="assessment_mode=fixed",
                approved_by_operator=True,
            ),
        ],
        homeowner_visible_notes=[
            "All units are charged the same regular assessment amount in this HOA.",
        ],
    )


def build_matrix_for_assessment_mode(
    *,
    connection: sqlite3.Connection,
    property_id: int,
    fiscal_year: int,
    budget_draft: Any,
    hoa_name: str,
    unit_count: int,
    approved_assessment_revenue_annual: Decimal,
    assessment_mode: AssessmentMode,
) -> AssessmentScheduleMatrix:
    if normalize_assessment_mode(assessment_mode) == ASSESSMENT_MODE_FIXED:
        return _build_matrix_for_fixed_mode(
            fiscal_year=fiscal_year,
            hoa_name=hoa_name,
            unit_count=unit_count,
            approved_assessment_revenue_annual=approved_assessment_revenue_annual,
        )

    active_setup_id = resolve_active_assessment_setup_id(
        connection,
        property_id=property_id,
    )
    setup_row = (
        connection.execute(
            """
            SELECT id
              FROM assessment_setups
             WHERE id = ? AND property_id = ? AND status = 'approved'
            """,
            (active_setup_id, property_id),
        ).fetchone()
        if active_setup_id is not None
        else None
    )
    if setup_row is None:
        return _blocking_matrix_for_issue(
            hoa_name=hoa_name,
            fiscal_year=fiscal_year,
            field_path="assessment_setup.status",
            reason=_variable_mode_missing_setup_reason(
                connection=connection,
                property_id=property_id,
            ),
        )

    return build_matrix_from_approved_assessment_setup(
        connection=connection,
        property_id=property_id,
        fiscal_year=fiscal_year,
        budget_draft=budget_draft,
        hoa_name=hoa_name,
        unit_count=unit_count,
        approved_assessment_revenue_annual=approved_assessment_revenue_annual,
    )


def _parse_sql_timestamp(value: Optional[str]) -> Optional[datetime]:
    """Parse either timestamp format this codebase stores, as UTC.

    ``dre_review_edits.edited_at`` defaults to sqlite's ``datetime('now')``
    (``"YYYY-MM-DD HH:MM:SS"``, naive, implicitly UTC); ``dre_extraction_runs
    .promoted_at`` is set via Python's ``_now_iso()`` (``"YYYY-MM-DDTHH:MM:SS
    +00:00"``, explicit UTC). Comparing these as raw strings is wrong — the
    space-vs-``T`` separator byte makes ``edited_at`` sort as "less than"
    ``promoted_at`` for same-day timestamps almost by accident, which would
    mask exactly the case this comparison exists to catch (an edit added
    *after* promotion, same day). Parse both into comparable datetimes.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _payload_for_promoted_setup(
    *,
    connection: sqlite3.Connection,
    property_id: int,
    setup_id: int,
) -> dict[str, Any]:
    """Reconstruct the extraction as it was at this setup's last promotion.

    Re-parsing ``dre_extraction_runs.parsed_json`` alone would return the
    original, never-mutated extraction — Review Workbench edits are applied
    to ``allocation_pools``/``assessment_units`` at promotion time via
    ``apply_review_edits_to_extraction``, but this render path previously
    never applied them, so a corrected unit percentage would land in the
    promoted DB tables while the rendered PDF kept computing off the stale
    original value (see ``_per_unit_factor_value_lookup_from_payload``,
    which reads this payload's ``pool_factors`` for pools the DB tables
    can't represent — multiple ``ownership_percentage`` pools needing
    different per-unit percentages).

    Edits are filtered to ``edited_at <= promoted_at`` so this matches what
    was actually live as of the last (re-)promotion — a newer, not-yet-
    promoted edit shouldn't leak into the render when the rest of it
    (``allocation_pools``/``assessment_units``) doesn't reflect that edit
    either until the operator re-promotes.
    """
    row = connection.execute(
        """
        SELECT id, parsed_json, promoted_at, document_type
          FROM dre_extraction_runs
         WHERE property_id = ? AND promoted_setup_id = ?
         ORDER BY id DESC LIMIT 1
        """,
        (property_id, setup_id),
    ).fetchone()
    if row is None or not row[1]:
        return {}
    run_id, parsed_json_text, promoted_at, document_type = row

    extraction = parse_extraction_payload(parsed_json_text)
    if extraction is None:
        return {}

    promoted_at_dt = _parse_sql_timestamp(promoted_at)
    edits = [
        edit
        for edit in list_review_edits(dre_extraction_run_id=run_id, connection=connection)
        if promoted_at_dt is None
        or (_parse_sql_timestamp(edit.edited_at) or promoted_at_dt) <= promoted_at_dt
    ]
    # Deliberately not caught: if the same edits that already succeeded once
    # at promotion fail to reapply here against the same immutable original,
    # that is a real data-consistency anomaly worth surfacing loudly, not a
    # reason to silently fall back to stale data.
    extraction = apply_review_edits_to_extraction(extraction, edits)

    if document_type == "ccr":
        operator_factors = get_operator_unit_factors(
            extraction_run_id=run_id, connection=connection
        )
        if operator_factors:
            extraction = merge_operator_factors(extraction, operator_factors)

    return extraction.model_dump(mode="json")


def _source_pages_from_payload(payload: dict[str, Any]) -> list[int]:
    pages: list[int] = []
    setup = payload.get("assessment_setup") or {}
    for page in setup.get("source_pages") or []:
        try:
            pages.append(int(page))
        except (TypeError, ValueError):
            continue
    for pool in payload.get("allocation_pools") or []:
        for page in pool.get("source_pages") or []:
            try:
                pages.append(int(page))
            except (TypeError, ValueError):
                continue
    return sorted(set(pages))


def _fallback_recipients_from_payload(
    payload: dict[str, Any],
) -> tuple[SetupType | None, list[RecipientReference]]:
    unit_structure = payload.get("unit_structure") or {}
    # C8: payload values are verbatim printed form — resolve the column's
    # form once (honoring the operator's audited decision) instead of
    # guessing per value. AmbiguousPercentColumn propagates to the caller,
    # which degrades to the operator-review fallback matrix.
    forced_form = str(unit_structure.get("ownership_percent_form") or "unknown")

    def _raw_percent(row: dict[str, Any]) -> Optional[Decimal]:
        value = row.get("ownership_percent")
        return _money(value) if value is not None else None

    groups = unit_structure.get("groups") or []
    if groups:
        divisor = resolve_percent_divisor(
            [_raw_percent(g) for g in groups],
            column_label="payload.groups.ownership_percent",
            forced_form=forced_form,
        )
        recipients = [
            RecipientReference(
                ref_type="group",
                ref_id=idx,
                label=str(group.get("label") or group.get("group_id") or f"Group {idx}"),
                unit_count=int(group.get("unit_count") or 1),
                square_feet=(
                    _money(group.get("average_square_feet"))
                    if group.get("average_square_feet") is not None
                    else None
                ),
                ownership_percent=normalize_percent_value(_raw_percent(group), divisor),
            )
            for idx, group in enumerate(groups, start=1)
            if int(group.get("unit_count") or 0) > 0
        ]
        if recipients:
            return "grouped", recipients

    units = unit_structure.get("units") or []
    if units:
        divisor = resolve_percent_divisor(
            [_raw_percent(u) for u in units],
            column_label="payload.units.ownership_percent",
            forced_form=forced_form,
        )
        recipients = [
            RecipientReference(
                ref_type="unit",
                ref_id=idx,
                label=str(unit.get("unit_number") or f"Unit {idx}"),
                square_feet=(
                    _money(unit.get("square_feet"))
                    if unit.get("square_feet") is not None
                    else None
                ),
                ownership_percent=normalize_percent_value(_raw_percent(unit), divisor),
                category=unit.get("category") or None,
                parking_spaces=int(unit.get("parking_spaces") or 0),
            )
            for idx, unit in enumerate(units, start=1)
        ]
        if recipients:
            return "per_unit", recipients

    return None, []


def _generated_revenue_split_by_dre_pool_proportions(
    *,
    payload: dict[str, Any],
    pools: list[PoolDefinition],
    approved_assessment_revenue_annual: Decimal,
) -> tuple[list[BudgetLineInput], list[BudgetLineMappingInput]] | None:
    """Create engine inputs when current-year dollars exist only as revenue.

    Some generated budgets provide one approved Assessment Income total, while
    the DRE provides the permanent component split (for example variable vs
    equal costs). In that case, the current-year total still comes from the
    generated budget; the DRE annual pool amounts are used only as proportions.
    """
    if approved_assessment_revenue_annual <= Decimal("0"):
        return None

    payload_pool_amounts: dict[str, Decimal] = {}
    for pool in payload.get("allocation_pools") or []:
        pool_key = str(pool.get("pool_key") or "")
        if not pool_key:
            continue
        annual = pool.get("annual_amount")
        if annual in (None, ""):
            continue
        amount = _money(annual)
        if amount > Decimal("0"):
            payload_pool_amounts[pool_key] = amount

    visible_pools = [pool for pool in pools if _pool_is_visible(pool)]
    if not visible_pools:
        return None
    if any(pool.pool_key not in payload_pool_amounts for pool in visible_pools):
        return None

    dre_total = sum(
        (payload_pool_amounts[pool.pool_key] for pool in visible_pools),
        start=Decimal("0"),
    )
    if dre_total <= Decimal("0"):
        return None

    budget_lines: list[BudgetLineInput] = []
    mappings: list[BudgetLineMappingInput] = []
    remaining = approved_assessment_revenue_annual
    ordered_pools = sorted(visible_pools, key=lambda pool: (pool.display_order, pool.pool_id))
    for idx, pool in enumerate(ordered_pools, start=1):
        if idx == len(ordered_pools):
            amount = remaining
        else:
            amount = (
                approved_assessment_revenue_annual
                * payload_pool_amounts[pool.pool_key]
                / dre_total
            ).quantize(Decimal("0.01"))
            remaining -= amount
        label = f"generated_assessment_revenue:{pool.pool_key}"
        budget_lines.append(
            BudgetLineInput(
                line_id=idx,
                normalized_label=label,
                section="income",
                category="income",
                fund_type="operating",
                account_code=None,
                amount=amount,
            )
        )
        mappings.append(
            BudgetLineMappingInput(
                budget_line_normalized_label=label,
                section="income",
                category="income",
                fund_type="operating",
                account_code=None,
                pool_key=pool.pool_key,
            )
        )

    return budget_lines, mappings


def _rebase_component_dollars_to_assessment_revenue(
    *,
    budget_lines: list[BudgetLineInput],
    mappings: list[BudgetLineMappingInput],
    pools: list[PoolDefinition],
    approved_assessment_revenue_annual: Decimal,
) -> tuple[list[BudgetLineInput], list[BudgetLineMappingInput]] | None:
    """Rescale the regular (non-special) pool component dollars so they sum to
    the approved Assessment Income, preserving the DRE pool split.

    The per-unit assessment schedule is driven by the Assessment Income line, not
    by the sum of mapped operating expenses (client requirement, July 2026): a
    10% change to Assessment Income moves every unit 10%, and expense edits do
    not move the schedule. The split across pools/units (ownership %, sqft,
    equal) is unchanged — only the level is rebased, by one global factor.

    Special-assessment pools are left untouched (separately billed). Returns
    ``None`` — keep today's expense-sum behavior — when there is no positive
    Assessment Income or no positive regular basis to scale.
    """
    if approved_assessment_revenue_annual <= Decimal("0"):
        return None

    visible_regular_keys = {
        pool.pool_key for pool in pools if _pool_is_visible(pool)
    }
    if not visible_regular_keys:
        return None

    pool_totals = _pool_totals_annual_for_mappings(
        budget_lines=budget_lines, mappings=mappings,
    )
    regular_totals = {
        key: amount
        for key, amount in pool_totals.items()
        if key in visible_regular_keys and amount > Decimal("0")
    }
    current_sum = sum(regular_totals.values(), start=Decimal("0"))
    if current_sum <= Decimal("0"):
        return None

    routing = {
        (
            mapping.budget_line_normalized_label,
            mapping.section,
            mapping.category,
            mapping.fund_type,
            mapping.account_code,
        ): mapping.pool_key
        for mapping in mappings
        if mapping.active
    }

    def _line_pool_key(line: BudgetLineInput) -> Optional[str]:
        return routing.get(
            (
                line.normalized_label,
                line.section,
                line.category,
                line.fund_type,
                line.account_code,
            )
        )

    # Keep every line/mapping that does NOT route to a rescaled regular pool
    # (special-assessment pools, hidden pools, unmapped lines) verbatim.
    kept_lines = [
        line for line in budget_lines if _line_pool_key(line) not in regular_totals
    ]
    kept_mappings = [
        mapping for mapping in mappings if mapping.pool_key not in regular_totals
    ]

    display_order_by_key = {pool.pool_key: pool.display_order for pool in pools}
    ordered_keys = sorted(
        regular_totals,
        key=lambda key: (display_order_by_key.get(key, 0), key),
    )
    synthetic_lines: list[BudgetLineInput] = []
    synthetic_mappings: list[BudgetLineMappingInput] = []
    start_line_id = max((line.line_id for line in kept_lines), default=0) + 1
    remaining = approved_assessment_revenue_annual
    for idx, key in enumerate(ordered_keys):
        if idx == len(ordered_keys) - 1:
            amount = remaining
        else:
            amount = (
                approved_assessment_revenue_annual
                * regular_totals[key]
                / current_sum
            ).quantize(Decimal("0.01"))
            remaining -= amount
        label = f"assessment_revenue_component:{key}"
        synthetic_lines.append(
            BudgetLineInput(
                line_id=start_line_id + idx,
                normalized_label=label,
                section="income",
                category="income",
                fund_type="operating",
                account_code=None,
                amount=amount,
            )
        )
        synthetic_mappings.append(
            BudgetLineMappingInput(
                budget_line_normalized_label=label,
                section="income",
                category="income",
                fund_type="operating",
                account_code=None,
                pool_key=key,
            )
        )

    return kept_lines + synthetic_lines, kept_mappings + synthetic_mappings


def _per_unit_factor_value_lookup_from_payload(
    *,
    payload: dict[str, Any],
    pools: list[PoolDefinition],
    unit_id_by_number: dict[str, int],
    pool_totals_annual: dict[str, Decimal],
) -> tuple[dict[tuple[int, str], Decimal], set[str]]:
    """Build runtime per-unit pool amounts from multi-factor DRE payloads.

    Some per-unit DREs, including 800 High-style packets, store pool-specific
    participation factors on each unit instead of one global ownership percent.
    The current engine cannot consume a different ownership percentage per
    pool, but it *can* consume per-unit specified values. For those multi-
    factor pools we therefore convert:

      current budget pool monthly total × unit pool factor = unit monthly amount

    and feed the result through ``specified_value_lookup`` at runtime.
    """
    lookup: dict[tuple[int, str], Decimal] = {}
    converted_pool_keys: set[str] = set()

    for pool in pools:
        if pool.allocation_method != "ownership_percentage":
            continue
        annual_total = pool_totals_annual.get(pool.pool_key)
        if annual_total in (None, ""):
            continue
        try:
            pool_monthly_total = _money(annual_total) / Decimal("12")
        except Exception:
            continue
        if pool_monthly_total <= Decimal("0"):
            continue

        # C8: collect the pool's percent-factor column FIRST, resolve its
        # form once (fraction vs points) by column sum, THEN convert. The
        # previous code multiplied by the raw printed value — a points-form
        # factor column (e.g. "13.15" meaning 13.15%) produced a 100×
        # over-assessment. AmbiguousPercentColumn propagates to the caller,
        # which degrades to the operator-review fallback matrix.
        raw_shares: dict[int, Decimal] = {}
        for unit in ((payload.get("unit_structure") or {}).get("units") or []):
            unit_number = str(unit.get("unit_number") or "")
            unit_id = unit_id_by_number.get(unit_number)
            if unit_id is None:
                continue
            for factor in unit.get("pool_factors") or []:
                if str(factor.get("pool_key") or "") != pool.pool_key:
                    continue
                if str(factor.get("factor_type") or "") != "percent":
                    continue
                factor_value = factor.get("factor_value")
                if factor_value in (None, ""):
                    continue
                try:
                    raw_shares[unit_id] = _money(factor_value)
                except Exception:
                    continue
                break
        if not raw_shares:
            continue
        divisor = resolve_percent_divisor(
            list(raw_shares.values()),
            column_label=f"pool_factors[{pool.pool_key}].percent",
        )
        pool_values = {
            unit_id: (
                pool_monthly_total * normalize_percent_value(share, divisor)
            ).quantize(Decimal("0.01"))
            for unit_id, share in raw_shares.items()
        }
        converted_pool_keys.add(pool.pool_key)
        lookup.update({(unit_id, pool.pool_key): value for unit_id, value in pool_values.items()})

    return lookup, converted_pool_keys


def _absent_or_zero_money(value: Any) -> bool:
    if value in (None, "", "-"):
        return True
    try:
        return Decimal(str(value)) == 0
    except (InvalidOperation, TypeError, ValueError):
        return False


def category_is_idle_this_year(
    *,
    mapped_annual: Any = None,
    operator_total: Any = None,
    documented_annual: Any = None,
    documented_monthly: Any = None,
) -> bool:
    """True when a category has no this-year dollars to bill.

    Idle categories are omitted from the package. They must not invent
    payers or totals, and they must not fail generate. A category with
    real mapped, operator-entered, or documented dollars is not idle.
    """
    return all(
        _absent_or_zero_money(value)
        for value in (
            mapped_annual,
            operator_total,
            documented_annual,
            documented_monthly,
        )
    )


def _empty_special_assessment_issues(
    pools: list[Any],
    pool_totals_annual: dict[str, Decimal] | None = None,
    operator_totals: dict[str, Decimal] | None = None,
) -> list[PreflightError]:
    """Idle specials (no mapped dollars, no operator total) are omitted.

    A $0 special is unused this year — not a missing amount. Non-idle
    specials already have dollars, so they never emit this gap.
    """
    totals = pool_totals_annual or {}
    operators = operator_totals or {}
    for pool in pools:
        if str(_get(pool, "pool_kind", "") or "") != SPECIAL_ASSESSMENT_POOL_KIND:
            continue
        if category_is_idle_this_year(
            mapped_annual=totals.get(_pool_key(pool)),
            operator_total=operators.get(_pool_key(pool)),
        ):
            continue
    return []


def _pool_custom_recipient_ids_from_payload(
    *,
    payload: dict[str, Any],
    unit_id_by_number: dict[str, int],
) -> dict[str, list[int]]:
    """Resolve reviewed participant unit numbers to promoted engine IDs."""
    result: dict[str, list[int]] = {}
    for pool in payload.get("allocation_pools") or []:
        scope = str(pool.get("recipient_scope") or "")
        if scope in {"", "all_units"}:
            continue
        pool_key = str(pool.get("pool_key") or "")
        participants = [
            str(value).strip()
            for value in (
                pool.get("selected_unit_numbers")
                or pool.get("participant_unit_numbers")
                or []
            )
            if str(value).strip()
        ]
        if not participants and scope in {
            "residential_only",
            "commercial_only",
            "parking_users",
        }:
            for unit in (payload.get("unit_structure") or {}).get("units") or []:
                unit_number = str(unit.get("unit_number") or "").strip()
                category = str(
                    unit.get("category")
                    or unit.get("residential_commercial_flag")
                    or ""
                ).strip().lower()
                parking = str(
                    unit.get("parking_flag")
                    or unit.get("parking_spaces")
                    or ""
                ).strip().lower()
                if scope == "residential_only" and category.startswith("res"):
                    participants.append(unit_number)
                elif scope == "commercial_only" and category.startswith("com"):
                    participants.append(unit_number)
                elif scope == "parking_users" and parking not in {
                    "",
                    "0",
                    "false",
                    "no",
                    "none",
                }:
                    participants.append(unit_number)
            participants = [value for value in participants if value]
        if not pool_key or not participants:
            # Documented $0 / no printed dollars this year: do not invent
            # payers and do not block generation. Positive dollars still
            # require a reviewed home list.
            if category_is_idle_this_year(
                documented_annual=pool.get("annual_amount"),
                documented_monthly=pool.get("monthly_amount"),
            ):
                continue
            raise ValueError(
                "A selected-home category has no reviewed participating homes."
            )
        missing = [
            unit_number
            for unit_number in participants
            if unit_number not in unit_id_by_number
        ]
        if missing:
            raise ValueError(
                "Selected-home category references homes that are not in the "
                f"promoted setup: {', '.join(missing)}"
            )
        result[pool_key] = [
            unit_id_by_number[unit_number] for unit_number in participants
        ]
    return result


def _pool_totals_annual_for_mappings(
    *,
    budget_lines: list[BudgetLineInput],
    mappings: list[BudgetLineMappingInput],
) -> dict[str, Decimal]:
    routing = {
        (
            mapping.budget_line_normalized_label,
            mapping.section,
            mapping.category,
            mapping.fund_type,
            mapping.account_code,
        ): mapping.pool_key
        for mapping in mappings
        if mapping.active
    }
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for line in budget_lines:
        pool_key = routing.get(
            (
                line.normalized_label,
                line.section,
                line.category,
                line.fund_type,
                line.account_code,
            )
        )
        if pool_key:
            totals[pool_key] += line.amount
    return dict(totals)


def _pool_line_fund_totals_for_dual_fund_split(
    *,
    budget_lines: list[BudgetLineInput],
    mappings: list[BudgetLineMappingInput],
) -> dict[str, dict[str, Any]]:
    """Serialize Option B per-pool ops/reserve mapped totals onto the matrix."""
    routing = {
        (
            mapping.budget_line_normalized_label,
            mapping.section,
            mapping.category,
            mapping.fund_type,
            mapping.account_code,
        ): mapping.pool_key
        for mapping in mappings
        if mapping.active
    }
    rows: list[dict[str, Any]] = []
    for line in budget_lines:
        pool_key = routing.get(
            (
                line.normalized_label,
                line.section,
                line.category,
                line.fund_type,
                line.account_code,
            )
        )
        if not pool_key:
            continue
        rows.append(
            {
                "pool_key": pool_key,
                "amount": line.amount,
                "fund_type": line.fund_type,
                "category": line.category,
                "label": line.normalized_label,
                "account_code": line.account_code,
            }
        )
    built = build_pool_line_fund_totals_from_mapped_rows(rows)
    return {
        key: {
            "operating_mapped": str(val.operating_mapped),
            "reserve_mapped": str(val.reserve_mapped),
        }
        for key, val in built.items()
    }


def _special_assessment_operator_totals(
    connection: sqlite3.Connection, property_id: int
) -> dict[str, Decimal]:
    """Operator-entered one-time totals for special-assessment pools, read from
    ``hoa_settings.special_assessments_json`` and keyed by the linked
    ``pool_key``. Used only for special pools that have no mapped budget lines
    (a pure-disclosure levy). Tolerant of legacy entries with no ``pool_key``."""
    try:
        row = connection.execute(
            "SELECT special_assessments_json FROM hoa_settings WHERE property_id = ?",
            (property_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        # hoa_settings table/column absent (hand-rolled test schema) — no totals.
        return {}
    if not row or not row[0]:
        return {}
    try:
        entries = json.loads(row[0])
    except (TypeError, ValueError):
        return {}
    totals: dict[str, Decimal] = {}
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        pool_key = entry.get("pool_key")
        amount = entry.get("total_amount")
        if not pool_key or amount in (None, ""):
            continue
        try:
            totals[str(pool_key)] = Decimal(str(amount))
        except (InvalidOperation, TypeError, ValueError):
            continue
    return totals


def manual_special_key(index: int) -> str:
    """Stable synthetic pool_key for a MANUAL (pool-free) special-assessment
    entry, by its index in ``special_assessments_json``. Shared with the compiler
    join (``compiler._apply_special_assessment_allocations``). Cannot collide with
    a real pool_key (``manual:`` prefix)."""
    return f"manual:{index}"


def _special_assessments_json_entries(
    connection: sqlite3.Connection, property_id: int
) -> list[dict]:
    """Raw ``special_assessments_json`` entries (list of dicts). Tolerant of a
    missing table/column and malformed JSON (returns [])."""
    try:
        row = connection.execute(
            "SELECT special_assessments_json FROM hoa_settings WHERE property_id = ?",
            (property_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return []
    if not row or not row[0]:
        return []
    try:
        entries = json.loads(row[0])
    except (TypeError, ValueError):
        return []
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


_MANUAL_SPECIAL_BASES = {"equal", "square_footage", "ownership_percentage"}


def _manual_special_assessment_allocations(
    entries: list[dict],
    recipients: list[RecipientReference],
) -> tuple[list[SpecialAssessmentAllocation], list[PreflightError]]:
    """Allocate each MANUAL special-assessment entry (``total_amount`` +
    ``allocation_basis``, NO ``pool_key``) across the HOA's existing recipients by
    the chosen basis, reusing the engine's pool-free allocation primitive. Returns
    the allocations plus blocking preflight issues.

    One-time only: renders as a separate §5570 allocation table, NOT folded into
    monthly dues (``included_in_regular_monthly`` is ignored for manual entries).

    Basis-data guards BLOCK (never raise, never render a table that doesn't sum to
    its total). They fire ONLY because the operator chose that basis for THIS
    assessment — i.e. ownership-drift is flagged only when using ownership % is
    compulsory (operator-selected) — so general rendering is unaffected.
    """
    allocations: list[SpecialAssessmentAllocation] = []
    issues: list[PreflightError] = []
    for i, entry in enumerate(entries):
        if entry.get("pool_key"):
            continue  # pool-linked entries use the synthetic-line path
        basis = str(entry.get("allocation_basis") or "").strip()
        raw_total = entry.get("total_amount")
        if basis not in _MANUAL_SPECIAL_BASES or raw_total in (None, ""):
            continue
        try:
            total = Decimal(str(raw_total))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if total <= 0:
            continue

        label = str(entry.get("label") or entry.get("purpose") or "Special Assessment")
        scope = str(entry.get("recipient_scope") or "all_units")
        scoped = resolve_recipients(
            RecipientSet(recipients=recipients), scope,
            custom_unit_ids=entry.get("custom_unit_ids") or None,
        )
        if not scoped:
            continue

        field = f"special_assessments[{i}].allocation_basis"
        if basis == "square_footage" and any(r.square_feet is None for r in scoped):
            issues.append(PreflightError(
                field_path=field, severity="blocking",
                message=(
                    f"Special assessment {label!r} is set to allocate by square "
                    "footage, but this HOA has no per-unit square footage. Choose "
                    "Equal or Ownership %, or add square footage in DRE Review."
                ),
            ))
            continue
        if basis == "ownership_percentage":
            if any(r.ownership_percent is None for r in scoped):
                issues.append(PreflightError(
                    field_path=field, severity="blocking",
                    message=(
                        f"Special assessment {label!r} is set to allocate by "
                        "ownership %, but this HOA's ownership percentages are not "
                        "usable (missing or ambiguous). Choose Equal, or fix the "
                        "ownership roster in DRE Review."
                    ),
                ))
                continue
            # Accept either recipient_share (Σ pct ≈ 1) or per_unit_interest
            # (Σ pct×unit_count ≈ 1). Bare sum alone false-blocks Sharon Ridge–
            # style groups that store per-unit undivided interest on group rows.
            ok, bare_sum, weighted_sum, form = ownership_weight_sum_is_valid(scoped)
            if not ok:
                issues.append(PreflightError(
                    field_path=field, severity="blocking",
                    message=(
                        f"Special assessment {label!r} allocates by ownership %, but "
                        f"the ownership percentages do not sum to 100% under either "
                        f"form (bare Σ={bare_sum}, weighted Σ pct×units={weighted_sum}, "
                        f"detected form={form}) — the per-unit amounts would not add "
                        "up to the total. Fix the ownership roster or choose a "
                        "different basis."
                    ),
                ))
                continue

        denominator = None
        if basis == "square_footage":
            denominator = sum(
                ((r.square_feet or Decimal("0")) * Decimal(r.unit_count) for r in scoped),
                start=Decimal("0"),
            )
        shares, _warnings = _allocate_special_assessment(
            total, scoped, pool=None, method=basis,
            scope=scope, label=label, denominator=denominator,
        )
        allocations.append(SpecialAssessmentAllocation(
            pool_key=manual_special_key(i),
            label=label,
            allocation_method=basis,
            total=total,
            entries=[
                SpecialAssessmentAllocationEntry(
                    recipient_ref=r, amount=shares[(r.ref_type, r.ref_id)],
                )
                for r in scoped
            ],
        ))
    return allocations, issues


def _synthetic_special_assessment_lines(
    *,
    pools: list[PoolDefinition],
    mappings: list[BudgetLineMappingInput],
    operator_totals: dict[str, Decimal],
    start_line_id: int,
) -> tuple[list[BudgetLineInput], list[BudgetLineMappingInput]]:
    """For each special-assessment pool with an operator total but NO mapped
    budget lines, synthesize one budget line + mapping routing the total to that
    pool_key, so the engine's ``_aggregate_by_pool`` picks it up (the matrix-level
    ``pool_totals_annual`` dict is not passed to the engine). Budget-line-derived
    special pools already have their total via real mappings and are left alone."""
    mapped_keys = {m.pool_key for m in mappings if m.active}
    lines: list[BudgetLineInput] = []
    new_mappings: list[BudgetLineMappingInput] = []
    line_id = start_line_id
    for pool in pools:
        if pool.pool_kind != SPECIAL_ASSESSMENT_POOL_KIND:
            continue
        if pool.pool_key in mapped_keys:
            continue
        total = operator_totals.get(pool.pool_key)
        if total is None or total == 0:
            continue
        label = normalize_budget_label(f"__special_assessment__{pool.pool_key}")
        lines.append(
            BudgetLineInput(
                line_id=line_id,
                normalized_label=label,
                section="special_assessment",
                category="operating",
                fund_type="operating",
                account_code=None,
                amount=total,
            )
        )
        new_mappings.append(
            BudgetLineMappingInput(
                budget_line_normalized_label=label,
                section="special_assessment",
                category="operating",
                fund_type="operating",
                account_code=None,
                pool_key=pool.pool_key,
                active=True,
            )
        )
        line_id += 1
    return lines, new_mappings


def _line_item_to_review_budget_line(item: Any) -> dict[str, Any]:
    label = str(_get(item, "label", "") or "")
    category = _assessment_mapping_category(_get(item, "category", None))
    account_code = _get(item, "account_code", None)
    amount, source_column_used = select_assessment_mapping_amount(
        {
            "assessment_mapping_amount": _get(item, "assessment_mapping_amount"),
            "source_column_used": _get(item, "source_column_used"),
            "proposed_amount": _get(item, "proposed_amount"),
            "proposedAmount": _get(item, "proposedAmount"),
            "annual_budget": _get(item, "annual_budget"),
            "projection": _get(item, "projection"),
            "amount": _get(item, "amount"),
        }
    )
    raw = _get(item, "raw", {}) or {}
    normalized_label = normalize_budget_label(label)
    section = str(
        _get(item, "section", None)
        or (raw.get("section") if isinstance(raw, dict) else None)
        or (raw.get("Section") if isinstance(raw, dict) else None)
        or category
    )
    fund_type = _assessment_mapping_fund_type(category)
    normalized_account_code = (
        str(account_code) if account_code not in (None, "") else None
    )
    return {
        "label": label,
        "normalized_label": normalized_label,
        "section": section,
        "category": category,
        "fund_type": fund_type,
        "account_code": normalized_account_code,
        "source_line_key": build_budget_line_slice_key(
            normalized_label=normalized_label,
            section=section,
            category=category,
            fund_type=fund_type,
            account_code=normalized_account_code,
        ),
        "amount": float(amount) if amount is not None else None,
        "annual_budget": _get(item, "annual_budget", None),
        "proposed_amount": (
            _get(item, "proposed_amount", None)
            if _get(item, "proposed_amount", None) is not None
            else _get(item, "proposedAmount", None)
        ),
        "projection": _get(item, "projection", None),
        "assessment_mapping_amount": float(amount) if amount is not None else None,
        "source_column_used": source_column_used,
        "reserve_group": _get(item, "reserve_group", None) or _get(item, "reserveGroup", None),
        "active": not bool(_get(item, "inactive", False)),
    }


def _ownership_divisor_or_drop(
    values: list[Optional[Decimal]],
    *,
    column_label: str,
    forced_form: str,
    ownership_used: bool,
) -> tuple[Optional[Decimal], bool]:
    """Resolve the ownership-% column's divisor, degrading gracefully when the
    column is decorative.

    Returns ``(divisor, drop_ownership)``.

    - When a pool actually allocates by ownership percentage (``ownership_used``),
      an ambiguous column still raises ``AmbiguousPercentColumn`` — the operator
      MUST resolve it because homeowner dollars depend on it.
    - When NO pool allocates by ownership (the column is display-only), an
      ambiguous column returns ``(None, True)`` so the caller omits ownership from
      the schedule with a review note, instead of hard-blocking the PDF over a
      column that feeds no math. Clean/unambiguous columns are unaffected in both
      cases.
    """
    try:
        return (
            resolve_percent_divisor(
                values, column_label=column_label, forced_form=forced_form
            ),
            False,
        )
    except AmbiguousPercentColumn:
        if ownership_used:
            raise
        return None, True


# Bob (Jul 27): per-association assessment table presentation —
# fixed (summary), individual (each unit), or group (unit types).
# Stored on properties.assessment_schedule_presentation.
PRESENTATION_AUTO = "auto"
PRESENTATION_INDIVIDUAL = "individual"
PRESENTATION_GROUP = "group"
PRESENTATION_COLUMN = "assessment_schedule_presentation"


def _normalize_presentation(value: Any) -> str:
    raw = str(value or PRESENTATION_AUTO).strip().lower()
    if raw in {PRESENTATION_INDIVIDUAL, "per_unit", "unit", "units"}:
        return PRESENTATION_INDIVIDUAL
    if raw in {PRESENTATION_GROUP, "grouped", "groups", "unit_type"}:
        return PRESENTATION_GROUP
    return PRESENTATION_AUTO


def load_assessment_schedule_presentation(
    connection: sqlite3.Connection,
    *,
    property_id: int,
) -> str:
    """Return auto | individual | group for this HOA (default auto)."""
    try:
        cols = {
            str(r[1])
            for r in connection.execute("PRAGMA table_info(properties)").fetchall()
        }
    except sqlite3.Error:
        return PRESENTATION_AUTO
    if PRESENTATION_COLUMN not in cols:
        return PRESENTATION_AUTO
    try:
        row = connection.execute(
            f"SELECT {PRESENTATION_COLUMN} FROM properties WHERE id = ?",
            (property_id,),
        ).fetchone()
    except sqlite3.Error:
        return PRESENTATION_AUTO
    if not row:
        return PRESENTATION_AUTO
    return _normalize_presentation(row[0])


def save_assessment_schedule_presentation(
    connection: sqlite3.Connection,
    *,
    property_id: int,
    presentation: str,
) -> str:
    """Persist presentation mode; returns normalized value."""
    value = _normalize_presentation(presentation)
    cols = {
        str(r[1])
        for r in connection.execute("PRAGMA table_info(properties)").fetchall()
    }
    if PRESENTATION_COLUMN not in cols:
        connection.execute(
            f"ALTER TABLE properties ADD COLUMN {PRESENTATION_COLUMN} TEXT "
            f"NOT NULL DEFAULT '{PRESENTATION_AUTO}'"
        )
    connection.execute(
        f"UPDATE properties SET {PRESENTATION_COLUMN} = ? WHERE id = ?",
        (value, property_id),
    )
    connection.commit()
    return value


def _ownership_match_key(value: Optional[Decimal]) -> Optional[str]:
    """Normalize ownership % (points or fraction) for roster matching."""
    if value is None:
        return None
    try:
        v = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if v > 1:
        v = v / Decimal("100")
    return format(v.quantize(Decimal("0.000001")), "f")


def _unit_label_buckets_from_prior_seed(
    connection: sqlite3.Connection,
    *,
    property_id: int,
) -> dict[str, list[str]]:
    """Map ownership-match-key → ordered unit labels from prior-year seed."""
    try:
        cols = {
            str(r[1])
            for r in connection.execute("PRAGMA table_info(properties)").fetchall()
        }
    except sqlite3.Error:
        return {}
    if "prior_assessment_schedule_json" not in cols:
        return {}
    try:
        row = connection.execute(
            "SELECT prior_assessment_schedule_json FROM properties WHERE id = ?",
            (property_id,),
        ).fetchone()
    except sqlite3.Error:
        return {}
    if not row or not row[0]:
        return {}
    try:
        payload = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return {}
    rows = payload if isinstance(payload, list) else payload.get("rows") or []
    buckets: dict[str, list[str]] = defaultdict(list)
    for item in rows:
        if not isinstance(item, dict):
            continue
        label = str(
            item.get("recipient_label")
            or item.get("unit")
            or item.get("label")
            or ""
        ).strip()
        if not label:
            continue
        raw_pct = item.get("percent_of_total")
        if raw_pct is None:
            raw_pct = item.get("ownership_percent")
        if raw_pct is None or str(raw_pct).strip() == "":
            continue
        try:
            key = _ownership_match_key(Decimal(str(raw_pct).replace("%", "").strip()))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if key:
            buckets[key].append(label)
    return buckets


def _expand_group_recipients_to_units(
    groups: list[RecipientReference],
    *,
    unit_labels_by_pct: Optional[dict[str, list[str]]] = None,
) -> list[RecipientReference]:
    """Expand group recipients into one unit row each (same ownership %).

    Prefer labels from prior-year seed (matched by ownership %). Otherwise
    label as \"{group} unit {i}\". Engine treats each as unit_count=1 with
    per-unit interest form when Σ pct×count ≈ 1.
    """
    labels_by_pct = unit_labels_by_pct or {}
    used_index: dict[str, int] = defaultdict(int)
    units: list[RecipientReference] = []
    next_id = 1
    for group in groups:
        n = max(int(group.unit_count or 1), 1)
        key = _ownership_match_key(group.ownership_percent)
        roster = labels_by_pct.get(key or "", [])
        for i in range(n):
            if key and used_index[key] < len(roster):
                label = roster[used_index[key]]
                used_index[key] += 1
            else:
                label = f"{group.label} unit {i + 1}"
            units.append(
                RecipientReference(
                    ref_type="unit",
                    ref_id=next_id,
                    label=label,
                    unit_count=1,
                    square_feet=group.square_feet,
                    ownership_percent=group.ownership_percent,
                )
            )
            next_id += 1
    return units


def _resolve_presentation_for_setup(
    *,
    property_presentation: str,
    setup_type: str,
    setup_display_mode: Optional[str],
) -> str:
    """Decide auto/individual/group for this package.

    Property setting wins when not auto. Otherwise setup display_mode, then
    setup_type (grouped → group, per_unit → individual, fixed → auto/summary).
    """
    prop = _normalize_presentation(property_presentation)
    if prop != PRESENTATION_AUTO:
        return prop
    mode = _normalize_presentation(setup_display_mode)
    if mode != PRESENTATION_AUTO:
        return mode
    st = str(setup_type or "").strip().lower()
    if st in {"per_unit", "individual_unit"}:
        return PRESENTATION_INDIVIDUAL
    if st in {"grouped", "grouped_category"}:
        return PRESENTATION_GROUP
    return PRESENTATION_AUTO


def _apply_approved_allocation_resolutions(
    *,
    connection: sqlite3.Connection,
    setup_id: int,
    pools: list[PoolDefinition],
    recipients: list[RecipientReference],
    pool_custom_recipients: dict[str, list[int]],
    pool_totals_annual: Optional[dict[str, Decimal]] = None,
) -> tuple[
    list[PoolDefinition],
    dict[str, dict[tuple[str, int], Decimal]],
    dict[tuple[int, str], Decimal],
    set[str],
    list[str],
]:
    """Apply approved rule snapshots before the engine receives its inputs."""
    has_resolution_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'allocation_resolutions'"
    ).fetchone()
    resolutions = (
        {
            resolution.pool_key: resolution
            for resolution in list_current_resolutions(
                connection,
                assessment_setup_id=setup_id,
            )
            if resolution.status == "approved" and resolution.resolved_method
        }
        if has_resolution_table
        else {}
    )
    updated_pools: list[PoolDefinition] = []
    recipient_weights: dict[str, dict[tuple[str, int], Decimal]] = {}
    specified_values: dict[tuple[int, str], Decimal] = {}
    audit_notes: list[str] = []
    recipient_by_key = {
        key: recipient
        for recipient in recipients
        for key in (recipient.label, str(recipient.ref_id))
    }

    for pool in pools:
        resolution = resolutions.get(pool.pool_key)
        if resolution is None:
            updated_pools.append(pool)
            continue
        snapshot = resolution.factor_snapshot
        if pool.recipient_scope == "custom_unit_list":
            custom_ids = set(pool_custom_recipients.get(pool.pool_key, []))
            scoped_recipients = [
                recipient
                for recipient in recipients
                if recipient.ref_type == "unit" and recipient.ref_id in custom_ids
            ]
            if not scoped_recipients:
                if pool_totals_annual is not None and category_is_idle_this_year(
                    mapped_annual=pool_totals_annual.get(pool.pool_key),
                ):
                    pool_custom_recipients[pool.pool_key] = []
                    updated_pools.append(pool)
                    audit_notes.append(
                        f"Selected-home assessment category '{pool.pool_key}' "
                        "has no reviewed homes and no this-year dollars; skipped."
                    )
                    continue
                raise EngineSetupError(
                    f"Selected-home assessment category '{pool.pool_key}' "
                    "has no approved recipient identifiers"
                )
        else:
            scoped_recipients = resolve_recipients(
                RecipientSet(recipients=recipients),
                pool.recipient_scope,
            )
        updated_pools.append(
            pool.model_copy(
                update={
                    "allocation_method": resolution.resolved_method,
                    "denominator_value": (
                        snapshot.denominator_value
                        if snapshot.denominator_value is not None
                        else pool.denominator_value
                    ),
                }
            )
        )
        if resolution.resolved_method == "equal":
            audit_notes.append(
                f"Approved allocation resolution applied for {pool.pool_key}: equal."
            )
            continue
        if resolution.resolved_method == "specified_value":
            missing = [
                recipient.label
                for recipient in scoped_recipients
                if recipient.ref_type != "unit"
                or (
                    recipient.label not in snapshot.recipients
                    and str(recipient.ref_id) not in snapshot.recipients
                )
            ]
            if missing:
                raise EngineSetupError(
                    f"Approved specified values for assessment category '{pool.pool_key}' "
                    f"are missing recipient(s): {', '.join(missing)}"
                )
            for recipient in scoped_recipients:
                raw_value = snapshot.recipients.get(
                    recipient.label,
                    snapshot.recipients.get(str(recipient.ref_id)),
                )
                if raw_value is not None:
                    specified_values[(recipient.ref_id, pool.pool_key)] = _money(raw_value)
            audit_notes.append(
                f"Approved allocation resolution applied for {pool.pool_key}: "
                "specified recipient values."
            )
            continue
        if not snapshot.recipients:
            raise EngineSetupError(
                f"Approved allocation resolution for assessment category '{pool.pool_key}' "
                "has no recipient factor snapshot"
            )
        weights: dict[tuple[str, int], Decimal] = {}
        missing: list[str] = []
        scoped_keys = {
            (recipient.ref_type, recipient.ref_id)
            for recipient in scoped_recipients
        }
        for snapshot_key, value in snapshot.recipients.items():
            recipient = recipient_by_key.get(str(snapshot_key))
            if (
                recipient is not None
                and (recipient.ref_type, recipient.ref_id) in scoped_keys
            ):
                weights[(recipient.ref_type, recipient.ref_id)] = _money(value)
        for recipient in scoped_recipients:
            if (
                _get(recipient, "ref_type") in {"unit", "group"}
                and (recipient.ref_type, recipient.ref_id) not in weights
            ):
                missing.append(recipient.label)
        if missing:
            raise EngineSetupError(
                f"Approved factor snapshot for assessment category '{pool.pool_key}' is missing "
                f"recipient(s): {', '.join(missing)}"
            )
        if sum(
            (
                weights[(recipient.ref_type, recipient.ref_id)]
                for recipient in scoped_recipients
                if (recipient.ref_type, recipient.ref_id) in weights
            ),
            start=Decimal("0"),
        ) <= 0:
            raise EngineSetupError(
                f"Approved factor snapshot for assessment category '{pool.pool_key}' has no "
                "positive recipient weights"
            )
        recipient_weights[pool.pool_key] = weights
        audit_notes.append(
            f"Approved allocation resolution applied for {pool.pool_key}: "
            f"{resolution.resolved_method} recipient snapshot."
        )
    return (
        updated_pools,
        recipient_weights,
        specified_values,
        set(resolutions),
        audit_notes,
    )


def _expand_approved_line_slices(
    *,
    connection: sqlite3.Connection,
    setup_id: int,
    budget_lines: list[BudgetLineInput],
    mappings: list[BudgetLineMappingInput],
) -> tuple[list[BudgetLineInput], list[BudgetLineMappingInput], list[str]]:
    """Replace approved combined source lines with uniquely routed engine lines."""
    has_slice_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'budget_line_allocation_slices'"
    ).fetchone()
    if not has_slice_table:
        return budget_lines, mappings, []
    approved_slices = list_slices(
        connection,
        assessment_setup_id=setup_id,
        statuses=("approved",),
    )
    if not approved_slices:
        return budget_lines, mappings, []

    by_source: dict[tuple[str, Optional[str]], list[Any]] = defaultdict(list)
    by_source_key: dict[str, list[Any]] = defaultdict(list)
    for slice_row in approved_slices:
        if slice_row.source_line_key:
            by_source_key[slice_row.source_line_key].append(slice_row)
        else:
            by_source[
                (
                    slice_row.source_line_normalized_label,
                    slice_row.source_line_account_code,
                )
            ].append(slice_row)

    expanded: list[BudgetLineInput] = []
    expanded_mappings: list[BudgetLineMappingInput] = []
    audit_notes: list[str] = []
    next_line_id = max((line.line_id for line in budget_lines), default=0) + 1
    legacy_source_counts = Counter(
        (line.normalized_label, line.account_code)
        for line in budget_lines
        if line.source_line_key
    )
    for line in budget_lines:
        key = (line.normalized_label, line.account_code)
        source_slices = (
            by_source_key.get(line.source_line_key or "")
            if line.source_line_key
            else by_source.get(key)
        )
        if (
            not source_slices
            and line.source_line_key
            and legacy_source_counts[key] == 1
        ):
            source_slices = by_source.get(key)
        if not source_slices:
            expanded.append(line)
            expanded_mappings.extend(
                mapping
                for mapping in mappings
                if (
                    mapping.budget_line_normalized_label == line.normalized_label
                    and mapping.section == line.section
                    and mapping.category == line.category
                    and mapping.fund_type == line.fund_type
                    and mapping.account_code == line.account_code
                )
            )
            continue
        if any(
            slice_row.source_annual_amount != line.amount
            for slice_row in source_slices
        ) or sum(
            (slice_row.slice_annual_amount for slice_row in source_slices),
            start=Decimal("0"),
        ) != line.amount:
            raise EngineSetupError(
                f"Approved slices for '{line.normalized_label}' do not match "
                "the active source amount"
            )

        for slice_row in source_slices:
            routed_label = (
                f"{line.normalized_label} allocation slice {slice_row.id or next_line_id}"
            )
            expanded.append(
                line.model_copy(
                    update={
                        "line_id": next_line_id,
                        "normalized_label": routed_label,
                        "amount": slice_row.slice_annual_amount,
                    }
                )
            )
            expanded_mappings.append(
                BudgetLineMappingInput(
                    budget_line_normalized_label=routed_label,
                    section=line.section,
                    category=line.category,
                    fund_type=line.fund_type,
                    account_code=line.account_code,
                    pool_key=slice_row.pool_key,
                    active=True,
                )
            )
            next_line_id += 1
        audit_notes.append(
            f"Approved slices replaced source line '{line.normalized_label}' "
            f"with {len(source_slices)} uniquely routed engine lines."
        )
    return expanded, expanded_mappings, audit_notes


def build_matrix_from_approved_assessment_setup(
    *,
    connection: sqlite3.Connection,
    property_id: int,
    fiscal_year: int,
    budget_draft: Any,
    hoa_name: str,
    unit_count: int,
    approved_assessment_revenue_annual: Decimal,
) -> AssessmentScheduleMatrix:
    """Resolve the approved DB setup, run the engine, and build a matrix.

    This is the live compile bridge. It intentionally returns a manual-review
    matrix instead of raising for missing setup/mapping data so the generated
    package makes the missing assessment basis visible during preview.
    """
    has_setup_display_mode = any(
        row[1] == "display_mode"
        for row in connection.execute("PRAGMA table_info(assessment_setups)").fetchall()
    )
    display_mode_col = "display_mode" if has_setup_display_mode else "NULL AS display_mode"
    try:
        active_setup_id = resolve_active_assessment_setup_id(
            connection,
            property_id=property_id,
        )
    except sqlite3.OperationalError:
        # Keep isolated legacy fixtures usable when they omit the properties
        # table; production databases always go through the shared resolver.
        active_setup_id = None
    if active_setup_id is None:
        setup = connection.execute(
            f"""
            SELECT id, setup_type, approved_at, {display_mode_col}
              FROM assessment_setups
             WHERE property_id = ? AND status = 'approved'
             ORDER BY id DESC LIMIT 1
            """,
            (property_id,),
        ).fetchone()
    else:
        setup = connection.execute(
            f"""
            SELECT id, setup_type, approved_at, {display_mode_col}
              FROM assessment_setups
             WHERE id = ?
            """,
            (active_setup_id,),
        ).fetchone()
    if setup is None:
        return _fallback_matrix_for_db_issue(
            hoa_name=hoa_name,
            fiscal_year=fiscal_year,
            reason="No approved DRE assessment setup was found for this HOA.",
        )

    setup_id, setup_type, approved_at, setup_display_mode = setup

    payload = _payload_for_promoted_setup(
        connection=connection,
        property_id=property_id,
        setup_id=setup_id,
    )

    # ``pool_kind`` is a brownfield-migrated column; tolerate a DB where the
    # migration hasn't run (or a hand-rolled schema) by degrading to NULL rather
    # than raising, so a special-assessment classification is simply absent.
    has_pool_kind = any(
        r[1] == "pool_kind"
        for r in connection.execute("PRAGMA table_info(allocation_pools)").fetchall()
    )
    pool_kind_col = "pool_kind" if has_pool_kind else "NULL AS pool_kind"
    pool_rows = connection.execute(
        f"""
        SELECT id, pool_key, pool_name, allocation_method, recipient_scope,
               denominator_value, include_in_pdf, display_order, {pool_kind_col}
          FROM allocation_pools
         WHERE assessment_setup_id = ?
         ORDER BY display_order, id
        """,
        (setup_id,),
    ).fetchall()
    pools = [
        PoolDefinition(
            pool_id=row[0],
            pool_key=row[1],
            pool_name=row[2],
            allocation_method=row[3],
            recipient_scope=row[4],
            denominator_value=_money(row[5]) if row[5] is not None else None,
            include_in_pdf=bool(row[6]),
            display_order=int(row[7] or 0),
            pool_kind=row[8],
        )
        for row in pool_rows
        if str(row[3] or "") != "unresolved"
    ]
    if not pools:
        return _fallback_matrix_for_db_issue(
            hoa_name=hoa_name,
            fiscal_year=fiscal_year,
            reason="Approved DRE setup has no allocation pools.",
            approved_at=approved_at,
        )

    effective_setup_type: SetupType = setup_type
    recipients: list[RecipientReference] = []
    # C8: the operator's audited form decision travels in the (edited)
    # extraction payload; the read-side resolver honors it for both DB rows
    # and payload fallbacks.
    percent_form_decision = str(
        (payload.get("unit_structure") or {}).get("ownership_percent_form")
        or "unknown"
    )
    # Ownership % is load-bearing only when a pool actually allocates by it.
    # When it is not, an ambiguous ownership column is decorative and must not
    # hard-block the PDF — we drop the column instead (see
    # _ownership_divisor_or_drop). Checked against the original pool definitions
    # (before any specified_value rewrite), which is the conservative choice.
    ownership_used = any(
        str(_get(pool, "allocation_method", "")) == "ownership_percentage"
        for pool in pools
    )
    ownership_dropped = False
    try:
        if setup_type == "fixed":
            recipients = [
                RecipientReference(ref_type="unit", ref_id=i, label=f"Unit {i}")
                for i in range(1, max(int(unit_count or 0), 0) + 1)
            ]
        elif setup_type == "grouped":
            rows = connection.execute(
                """
                SELECT id, group_name, unit_count, average_square_feet,
                       ownership_percent
                  FROM assessment_groups
                 WHERE assessment_setup_id = ?
                 ORDER BY display_order, id
                """,
                (setup_id,),
            ).fetchall()
            # C8: column-level form resolution — robust to legacy
            # verbatim-stored points rows (sum≈100 → ÷100) AND rows
            # normalized at promotion (sum≈1 → no-op). Never per-value.
            divisor, ownership_dropped = _ownership_divisor_or_drop(
                [_money(row[4]) if row[4] is not None else None for row in rows],
                column_label="assessment_groups.ownership_percent",
                forced_form=percent_form_decision,
                ownership_used=ownership_used,
            )
            recipients = [
                RecipientReference(
                    ref_type="group",
                    ref_id=row[0],
                    label=row[1],
                    unit_count=int(row[2] or 1),
                    square_feet=_money(row[3]) if row[3] is not None else None,
                    ownership_percent=None if ownership_dropped else normalize_percent_value(
                        _money(row[4]) if row[4] is not None else None, divisor
                    ),
                )
                for row in rows
            ]
        else:
            rows = connection.execute(
                """
                SELECT id, unit_number, square_feet, ownership_percent, category,
                       parking_spaces
                  FROM assessment_units
                 WHERE assessment_setup_id = ?
                 ORDER BY id
                """,
                (setup_id,),
            ).fetchall()
            divisor, ownership_dropped = _ownership_divisor_or_drop(
                [_money(row[3]) if row[3] is not None else None for row in rows],
                column_label="assessment_units.ownership_percent",
                forced_form=percent_form_decision,
                ownership_used=ownership_used,
            )
            recipients = [
                RecipientReference(
                    ref_type="unit",
                    ref_id=row[0],
                    label=row[1],
                    square_feet=_money(row[2]) if row[2] is not None else None,
                    ownership_percent=None if ownership_dropped else normalize_percent_value(
                        _money(row[3]) if row[3] is not None else None, divisor
                    ),
                    category=row[4],
                    parking_spaces=int(row[5] or 0),
                )
                for row in rows
            ]
        if not recipients:
            fallback_setup_type, fallback_recipients = _fallback_recipients_from_payload(
                payload
            )
            if fallback_setup_type and fallback_recipients:
                effective_setup_type = fallback_setup_type
                recipients = fallback_recipients
    except AmbiguousPercentColumn as exc:
        # Never render a guessed percent form into a legal disclosure —
        # degrade to the operator-review fallback matrix.
        return _fallback_matrix_for_db_issue(
            hoa_name=hoa_name,
            fiscal_year=fiscal_year,
            reason=f"Assessment matrix needs operator review before rendering: {exc}",
            approved_at=approved_at,
        )

    # Presentation mode (Bob): individual unit table vs unit-type groups.
    # Math stays the same; we only expand/collapse the recipient grain shown.
    presentation = _resolve_presentation_for_setup(
        property_presentation=load_assessment_schedule_presentation(
            connection, property_id=property_id
        ),
        setup_type=str(setup_type or ""),
        setup_display_mode=str(setup_display_mode or ""),
    )
    if (
        presentation == PRESENTATION_INDIVIDUAL
        and recipients
        and all(_get(r, "ref_type") == "group" for r in recipients)
    ):
        label_buckets = _unit_label_buckets_from_prior_seed(
            connection, property_id=property_id
        )
        recipients = _expand_group_recipients_to_units(
            recipients,
            unit_labels_by_pct=label_buckets,
        )
        effective_setup_type = "per_unit"

    if not recipients:
        return _fallback_matrix_for_db_issue(
            hoa_name=hoa_name,
            fiscal_year=fiscal_year,
            reason="Approved DRE setup has no recipients for the assessment schedule.",
            approved_at=approved_at,
        )

    mapping_rows = connection.execute(
        """
        SELECT budget_line_normalized_label, section, category, fund_type,
               account_code, pool_key, active
          FROM budget_line_pool_mappings
         WHERE property_id = ? AND assessment_setup_id = ? AND active = 1
        """,
        (property_id, setup_id),
    ).fetchall()
    review_budget_lines = [
        _line_item_to_review_budget_line(line)
        for line in (_get(budget_draft, "line_items", []) or [])
    ]
    review_rows = build_assessment_mapping_review_rows(
        property_id=property_id,
        assessment_setup_id=setup_id,
        budget_lines=review_budget_lines,
        budget_year=fiscal_year,
        connection=connection,
    )
    review_summary = build_assessment_mapping_review_summary(review_rows)
    review_blockers = build_assessment_mapping_review_blockers(
        property_id=property_id,
        assessment_setup_id=setup_id,
        review_rows=review_rows,
        connection=connection,
    )
    regular_review_keys = {
        (
            str(row["normalized_label"]),
            str(row["section"]),
            str(row["category"]),
            str(row["fund_type"]),
            row["account_code"],
        )
        for row in review_rows
        if bool(row["included_in_regular_basis"])
    }
    mappings = [
        BudgetLineMappingInput(
            budget_line_normalized_label=row[0],
            section=row[1],
            category=row[2],
            fund_type=row[3],
            account_code=row[4],
            pool_key=row[5],
            active=bool(row[6]),
        )
        for row in mapping_rows
        if (
            str(row[0]),
            str(row[1]),
            str(row[2]),
            str(row[3]),
            row[4],
        ) in regular_review_keys
    ]
    if review_summary["final_render_blocked"] or review_summary["reconciliation_failures"] or review_blockers:
        blocker_messages: list[str] = []
        if review_summary["unresolved_required_rows"]:
            blocker_messages.append(
                "Unresolved required rows: "
                + ", ".join(str(item) for item in review_summary["unresolved_required_rows"])
            )
        if review_summary["pending_split_total"]:
            blocker_messages.append(
                f"Pending split total: {review_summary['pending_split_total']}"
            )
        if review_summary["reconciliation_failures"]:
            blocker_messages.append(
                "Reconciliation failures: "
                + ", ".join(str(item) for item in review_summary["reconciliation_failures"])
            )
        for category, details in review_blockers.items():
            if details:
                blocker_messages.append(
                    f"{category}: {', '.join(str(detail) for detail in details)}"
                )
        return _fallback_matrix_for_db_issue(
            hoa_name=hoa_name,
            fiscal_year=fiscal_year,
            reason=(
                "Assessment mapping review required before final rendering. "
                + " ".join(blocker_messages)
            ).strip(),
            approved_at=approved_at,
        )

    internal_review_notes: list[ReviewNote] = []
    if ownership_dropped:
        # Not silent: the ownership column was omitted (ambiguous form, and no
        # pool allocates by ownership) rather than blocking the PDF. Assessments
        # are unaffected — they derive from unit count / square footage.
        internal_review_notes.append(
            ReviewNote(
                message=(
                    "Ownership-interest column omitted from the assessment schedule: "
                    "its values could not be read unambiguously (e.g. a phased/merged "
                    "association whose per-increment percentages total ~200%), and no "
                    "pool allocates by ownership percentage. Assessment amounts are "
                    "unchanged (based on unit count / square footage). Supply the "
                    "merged ownership percentages in the Review Workbench if the "
                    "column is required on the disclosure."
                ),
                severity="warning",
            )
        )
    budget_lines = [
        _line_to_engine_input(idx, line)
        for idx, line in enumerate(_get(budget_draft, "line_items", []) or [], start=1)
    ]
    budget_lines = [
        line
        for line in budget_lines
        if (
            normalize_budget_label(str(line.normalized_label)),
            str(line.section),
            str(line.category),
            str(line.fund_type),
            line.account_code,
        ) in regular_review_keys
    ]
    try:
        budget_lines, mappings, slice_audit_notes = _expand_approved_line_slices(
            connection=connection,
            setup_id=setup_id,
            budget_lines=budget_lines,
            mappings=mappings,
        )
    except EngineSetupError as exc:
        return _fallback_matrix_for_db_issue(
            hoa_name=hoa_name,
            fiscal_year=fiscal_year,
            reason=f"Assessment matrix needs operator review before rendering: {exc}",
            approved_at=approved_at,
        )
    internal_review_notes.extend(
        ReviewNote(message=note, severity="info")
        for note in slice_audit_notes
    )
    pool_totals_before_rebase = _pool_totals_annual_for_mappings(
        budget_lines=budget_lines,
        mappings=mappings,
    )
    if not mappings:
        generated_revenue_split = _generated_revenue_split_by_dre_pool_proportions(
            payload=payload,
            pools=pools,
            approved_assessment_revenue_annual=approved_assessment_revenue_annual,
        )
        if generated_revenue_split is None:
            return _fallback_matrix_for_db_issue(
                hoa_name=hoa_name,
                fiscal_year=fiscal_year,
                reason=(
                    "Current-year budget lines are not mapped to assessment pools. "
                    "The DRE setup can explain how to allocate, but it cannot supply "
                    "the 2026 dollars for the final homeowner schedule."
                ),
                approved_at=approved_at,
            )
        budget_lines, mappings = generated_revenue_split
        internal_review_notes.append(
            ReviewNote(
                message=(
                    "Current-year component dollars were derived from generated "
                    "assessment revenue using approved DRE pool proportions because "
                    "detailed budget line-to-pool mappings are not saved."
                ),
                severity="warning",
            )
        )
    else:
        # The homeowner schedule is driven by the Assessment Income line, not the
        # sum of mapped operating expenses: rebase the regular pool dollars to
        # Assessment Income while preserving the DRE pool/unit split. No income
        # line (or no assessable basis) → keep today's expense-sum behavior.
        rebased = _rebase_component_dollars_to_assessment_revenue(
            budget_lines=budget_lines,
            mappings=mappings,
            pools=pools,
            approved_assessment_revenue_annual=approved_assessment_revenue_annual,
        )
        if rebased is not None:
            budget_lines, mappings = rebased
            internal_review_notes.append(
                ReviewNote(
                    message=(
                        "Component dollars scaled to approved Assessment Income; "
                        "DRE pool split preserved."
                    ),
                    severity="info",
                )
            )

    # Operator-entered totals for special-assessment pools with no mapped budget
    # lines: fed as synthetic lines so the engine aggregates them (the engine
    # re-derives pool totals from budget_lines+mappings, not from the matrix-level
    # pool_totals_annual dict below).
    sa_synthetic_lines, sa_synthetic_mappings = _synthetic_special_assessment_lines(
        pools=pools,
        mappings=mappings,
        operator_totals=_special_assessment_operator_totals(connection, property_id),
        start_line_id=max((line.line_id for line in budget_lines), default=0) + 1,
    )
    budget_lines = budget_lines + sa_synthetic_lines
    mappings = mappings + sa_synthetic_mappings

    unit_id_by_number = {
        str(_get(recipient, "label") or ""): int(_get(recipient, "ref_id"))
        for recipient in recipients
        if _get(recipient, "ref_type") == "unit"
    }
    try:
        pool_custom_recipients = _pool_custom_recipient_ids_from_payload(
            payload=payload,
            unit_id_by_number=unit_id_by_number,
        )
    except ValueError as exc:
        return _fallback_matrix_for_db_issue(
            hoa_name=hoa_name,
            fiscal_year=fiscal_year,
            reason=f"Assessment matrix needs operator review before rendering: {exc}",
            approved_at=approved_at,
        )
    pools = [
        pool.model_copy(update={"recipient_scope": "custom_unit_list"})
        if pool.pool_key in pool_custom_recipients
        else pool
        for pool in pools
    ]
    pool_totals_annual = _pool_totals_annual_for_mappings(
        budget_lines=budget_lines,
        mappings=mappings,
    )
    try:
        (
            engine_pools,
            approved_pool_weights,
            approved_specified_values,
            approved_resolution_pool_keys,
            resolution_audit_notes,
        ) = (
            _apply_approved_allocation_resolutions(
                connection=connection,
                setup_id=setup_id,
                pools=pools,
                recipients=recipients,
                pool_custom_recipients=pool_custom_recipients,
                pool_totals_annual=pool_totals_annual,
            )
        )
    except (EngineSetupError, ValueError) as exc:
        return _fallback_matrix_for_db_issue(
            hoa_name=hoa_name,
            fiscal_year=fiscal_year,
            reason=f"Assessment matrix needs operator review before rendering: {exc}",
            approved_at=approved_at,
        )
    internal_review_notes.extend(
        ReviewNote(message=note, severity="info")
        for note in resolution_audit_notes
    )
    try:
        factor_pool_totals = dict(pool_totals_annual)
        factor_pool_totals.update(pool_totals_before_rebase)
        payload_factor_lookup, payload_factor_pool_keys = _per_unit_factor_value_lookup_from_payload(
            payload=payload,
            pools=pools,
            unit_id_by_number=unit_id_by_number,
            pool_totals_annual=factor_pool_totals,
        )
    except AmbiguousPercentColumn as exc:
        return _fallback_matrix_for_db_issue(
            hoa_name=hoa_name,
            fiscal_year=fiscal_year,
            reason=f"Assessment matrix needs operator review before rendering: {exc}",
            approved_at=approved_at,
        )

    specified_lookup = {
        (row[0], row[1]): _money(row[2])
        for row in connection.execute(
            """
            SELECT assessment_unit_id, pool_key, specified_monthly_amount
              FROM assessment_unit_pool_allocations
             WHERE assessment_setup_id = ?
            """,
            (setup_id,),
        ).fetchall()
    }
    specified_lookup.update(payload_factor_lookup)
    specified_lookup.update(approved_specified_values)

    engine_pools = [
        pool.model_copy(
            update={"allocation_method": "specified_value"}
        ) if (
            pool.pool_key in payload_factor_pool_keys
            and pool.pool_key not in approved_resolution_pool_keys
            and pool.pool_kind != SPECIAL_ASSESSMENT_POOL_KIND
        ) else pool
        for pool in engine_pools
    ]

    try:
        result = run_assessment_engine(
            CalcInput(
                setup_type=effective_setup_type,
                pools=engine_pools,
                recipient_set=RecipientSet(recipients=recipients),
                budget_lines=budget_lines,
                mappings=mappings,
                approved_assessment_revenue_annual=approved_assessment_revenue_annual,
                specified_value_lookup=specified_lookup,
                pool_recipient_weights=approved_pool_weights,
                pool_custom_recipients=pool_custom_recipients,
            )
        )
    except (NeedsHumanReview, EngineSetupError, ValueError) as exc:
        # H3: EngineSetupError (unsupported allocation method, missing
        # denominator, missing specified value) is a bad-setup signal, not a
        # software fault — degrade to the operator-review fallback with the
        # exact reason so the operator fixes the setup in the Review Workbench
        # and repromotes, instead of the render job dying with an unhandled
        # exception.
        return _fallback_matrix_for_db_issue(
            hoa_name=hoa_name,
            fiscal_year=fiscal_year,
            reason=f"Assessment matrix needs operator review before rendering: {exc}",
            approved_at=approved_at,
        )

    # H1/H2: the engine reports budget dollars that would otherwise vanish
    # silently — mapped to a pool that no longer exists (orphaned), or mapped
    # to a pool whose scope resolves zero recipients. Never render a schedule
    # that dropped money; degrade to the review fallback naming the exact
    # lines/pool and the in-app action (remap, exclude, or fix the roster).
    routing_issue_messages = _money_routing_issue_messages(result)
    if routing_issue_messages:
        return _fallback_matrix_for_db_issue(
            hoa_name=hoa_name,
            fiscal_year=fiscal_year,
            reason=(
                "Assessment mapping review required before final rendering. "
                + " ".join(routing_issue_messages)
            ).strip(),
            approved_at=approved_at,
        )

    # Idle specials (CC&R documents the method; this year's budget has no
    # levy) are omitted. Do not invent a total and do not fail generate.
    empty_special_pool_issues = _empty_special_assessment_issues(
        pools,
        pool_totals_annual,
        operator_totals=_special_assessment_operator_totals(connection, property_id),
    )

    # Manual (pool-free) special assessments: allocate an operator-entered total
    # across the HOA's existing units by the chosen basis, and surface them via the
    # same allocation-block channel as pool-based specials. Any basis-data guard
    # failures become blocking, actionable issues (never a silent wrong table).
    manual_allocs, manual_issues = _manual_special_assessment_allocations(
        _special_assessments_json_entries(connection, property_id),
        recipients,
    )
    result.special_assessment_allocations.extend(manual_allocs)

    return build_universal_assessment_matrix(
        result,
        setup_type=effective_setup_type,
        hoa_name=hoa_name,
        fiscal_year=fiscal_year,
        pool_definitions=pools,
        pending_review_issues=empty_special_pool_issues + manual_issues,
        source_pages=_source_pages_from_payload(payload),
        internal_review_notes=internal_review_notes,
        pool_line_fund_totals=_pool_line_fund_totals_for_dual_fund_split(
            budget_lines=budget_lines,
            mappings=mappings,
        ),
        evidence_refs=[
            EvidenceRef(
                field="recipient_grain",
                source_type="operator_approval",
                operator_approval_ref=str(approved_at or setup_id),
                approved_by_operator=True,
            ),
            EvidenceRef(
                field="basis_columns",
                source_type="operator_approval",
                operator_approval_ref=str(approved_at or setup_id),
                approved_by_operator=True,
            ),
            EvidenceRef(
                field="component_columns",
                source_type="operator_approval",
                operator_approval_ref=str(approved_at or setup_id),
                approved_by_operator=True,
            ),
        ],
    )
