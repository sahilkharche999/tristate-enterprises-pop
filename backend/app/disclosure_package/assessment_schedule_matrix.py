"""Universal assessment-schedule matrix builder.

This module is the rendering boundary for the assessment section.  The
assessment engine still owns all math; the matrix builder only reshapes
``CalcResultSet`` values into explicit rows, columns, notes, and layout hints
that a single HTML template can loop over.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal
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
from app.assessment_engine.engine import run as run_assessment_engine
from app.assessment_engine.errors import NeedsHumanReview
from app.assessment_engine.schemas import BudgetLineMappingInput
from app.services.assessment_budget_mapping_rule_service import (
    build_assessment_mapping_review_blockers,
    build_assessment_mapping_review_rows,
    build_assessment_mapping_review_summary,
    normalize_budget_label,
    select_assessment_mapping_amount,
)

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


class SpecialAssessmentDisclosureBlock(BaseModel):
    label: str
    amount_per_unit: Optional[Decimal] = None
    due_date: Optional[str] = None
    display_language: Optional[str] = None


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


def _percentage(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    pct = _money(value)
    if pct > Decimal("1"):
        return pct / Decimal("100")
    return pct


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
        values["percent_of_total"] = ownership_percent
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


def _child_pool_mapping_issues(pool_definitions: list[Any]) -> list[PreflightError]:
    issues: list[PreflightError] = []
    for pool in pool_definitions:
        parent_key = _parent_pool_key(pool)
        if not parent_key:
            continue
        included_lines = _get(pool, "included_budget_lines", None)
        mapping_status = str(_get(pool, "child_mapping_status", "") or "")
        approved = bool(_get(pool, "child_mapping_approved", False))
        needs_dollars = bool(_get(pool, "needs_current_year_dollars", True))
        if needs_dollars and (included_lines is None or mapping_status == "copied_from_parent"):
            issues.append(PreflightError(
                field_path=f"assessment_schedule.component_pools.{_pool_key(pool)}",
                message=(
                    f"Child pool {_pool_label(pool)!r} needs approved child-level "
                    "budget-line mappings before final rendering."
                ),
                severity="blocking",
            ))
        elif needs_dollars and included_lines and not approved:
            issues.append(PreflightError(
                field_path=f"assessment_schedule.component_pools.{_pool_key(pool)}",
                message=(
                    f"Child pool {_pool_label(pool)!r} has budget lines but they "
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

    preflight_issues = list(pending_review_issues or [])
    preflight_issues.extend(_child_pool_mapping_issues(pool_definitions))
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
            message="Required budget lines are not mapped to assessment pools.",
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
    return BudgetLineInput(
        line_id=line_id,
        normalized_label=normalize_budget_label(label),
        section=str(section),
        category=category,  # type: ignore[arg-type]
        fund_type="reserve" if is_reserve else "operating",
        account_code=str(account_code) if account_code not in (None, "") else None,
        amount=amount if amount is not None else _zero(),
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

    setup_row = connection.execute(
        """
        SELECT id
          FROM assessment_setups
         WHERE property_id = ? AND status = 'approved'
         ORDER BY id DESC LIMIT 1
        """,
        (property_id,),
    ).fetchone()
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


def _payload_for_promoted_setup(
    *,
    connection: sqlite3.Connection,
    property_id: int,
    setup_id: int,
) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT parsed_json
          FROM dre_extraction_runs
         WHERE property_id = ? AND promoted_setup_id = ?
         ORDER BY id DESC LIMIT 1
        """,
        (property_id, setup_id),
    ).fetchone()
    if row is None or not row[0]:
        return {}
    try:
        payload = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


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
    groups = unit_structure.get("groups") or []
    if groups:
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
                ownership_percent=_percentage(group.get("ownership_percent")),
            )
            for idx, group in enumerate(groups, start=1)
            if int(group.get("unit_count") or 0) > 0
        ]
        if recipients:
            return "grouped", recipients

    units = unit_structure.get("units") or []
    if units:
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
                ownership_percent=_percentage(unit.get("ownership_percent")),
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

        pool_values: dict[int, Decimal] = {}
        saw_factor = False
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
                saw_factor = True
                try:
                    share = _money(factor_value)
                except Exception:
                    continue
                pool_values[unit_id] = (
                    pool_monthly_total * share
                ).quantize(Decimal("0.01"))
                break
        if saw_factor and pool_values:
            converted_pool_keys.add(pool.pool_key)
            lookup.update({(unit_id, pool.pool_key): value for unit_id, value in pool_values.items()})

    return lookup, converted_pool_keys


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


def _assessment_mapping_category(raw_category: object) -> str:
    category = str(raw_category or "").lower()
    if category == "income":
        return "income"
    if category == "reserve_income":
        return "reserve_income"
    if category in {"reserve", "reserve_expense"}:
        return "reserve_expense"
    return "operating"


def _assessment_mapping_fund_type(category: str) -> str:
    return "reserve" if category in {"reserve_income", "reserve_expense"} else "operating"


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
    return {
        "label": label,
        "normalized_label": normalize_budget_label(label),
        "section": str(
            _get(item, "section", None)
            or (raw.get("section") if isinstance(raw, dict) else None)
            or (raw.get("Section") if isinstance(raw, dict) else None)
            or category
        ),
        "category": category,
        "fund_type": _assessment_mapping_fund_type(category),
        "account_code": str(account_code) if account_code not in (None, "") else None,
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
    setup = connection.execute(
        """
        SELECT id, setup_type, approved_at
          FROM assessment_setups
         WHERE property_id = ? AND status = 'approved'
         ORDER BY id DESC LIMIT 1
        """,
        (property_id,),
    ).fetchone()
    if setup is None:
        return _fallback_matrix_for_db_issue(
            hoa_name=hoa_name,
            fiscal_year=fiscal_year,
            reason="No approved DRE assessment setup was found for this HOA.",
        )

    setup_id, setup_type, approved_at = setup

    payload = _payload_for_promoted_setup(
        connection=connection,
        property_id=property_id,
        setup_id=setup_id,
    )

    pool_rows = connection.execute(
        """
        SELECT id, pool_key, pool_name, allocation_method, recipient_scope,
               denominator_value, include_in_pdf, display_order
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
        )
        for row in pool_rows
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
        recipients = [
            RecipientReference(
                ref_type="group",
                ref_id=row[0],
                label=row[1],
                unit_count=int(row[2] or 1),
                square_feet=_money(row[3]) if row[3] is not None else None,
                ownership_percent=_percentage(row[4]),
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
        recipients = [
            RecipientReference(
                ref_type="unit",
                ref_id=row[0],
                label=row[1],
                square_feet=_money(row[2]) if row[2] is not None else None,
                ownership_percent=_percentage(row[3]),
                category=row[4],
                parking_spaces=int(row[5] or 0),
            )
            for row in rows
        ]
    if not recipients:
        fallback_setup_type, fallback_recipients = _fallback_recipients_from_payload(payload)
        if fallback_setup_type and fallback_recipients:
            effective_setup_type = fallback_setup_type
            recipients = fallback_recipients

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

    unit_id_by_number = {
        str(_get(recipient, "label") or ""): int(_get(recipient, "ref_id"))
        for recipient in recipients
        if _get(recipient, "ref_type") == "unit"
    }
    pool_totals_annual = _pool_totals_annual_for_mappings(
        budget_lines=budget_lines,
        mappings=mappings,
    )
    payload_factor_lookup, payload_factor_pool_keys = _per_unit_factor_value_lookup_from_payload(
        payload=payload,
        pools=pools,
        unit_id_by_number=unit_id_by_number,
        pool_totals_annual=pool_totals_annual,
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

    engine_pools = [
        pool.model_copy(
            update={"allocation_method": "specified_value"}
        ) if pool.pool_key in payload_factor_pool_keys else pool
        for pool in pools
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
            )
        )
    except (NeedsHumanReview, ValueError) as exc:
        return _fallback_matrix_for_db_issue(
            hoa_name=hoa_name,
            fiscal_year=fiscal_year,
            reason=f"Assessment matrix needs operator review before rendering: {exc}",
            approved_at=approved_at,
        )

    return build_universal_assessment_matrix(
        result,
        setup_type=effective_setup_type,
        hoa_name=hoa_name,
        fiscal_year=fiscal_year,
        pool_definitions=pools,
        source_pages=_source_pages_from_payload(payload),
        internal_review_notes=internal_review_notes,
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
