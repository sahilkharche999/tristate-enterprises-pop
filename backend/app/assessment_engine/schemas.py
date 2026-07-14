"""Engine input/output contracts.

The engine takes a ``CalcInput`` (AssessmentSetup + pools + recipient set +
budget line items + approved revenue target) and emits a ``CalcResultSet``
(per-recipient-per-pool components + per-recipient totals + reconciliation
deltas). See ``specs/assessment-engine/spec.md`` for the full contract.
"""

from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from .models import AllocationMethod, PoolSource, RecipientScope, SetupType


class RecipientReference(BaseModel):
    """Stable handle for a calculation recipient.

    For grouped HOAs, ``ref_type='group'`` and ``unit_count`` is set; for
    per-unit HOAs, ``ref_type='unit'`` and ``unit_count`` is 1.

    ``parking_spaces`` drives the ``parking_users`` recipient scope —
    units with > 0 parking spaces participate in parking pools.
    """

    model_config = ConfigDict(frozen=True)

    ref_type: Literal["group", "unit"]
    ref_id: int
    label: str
    unit_count: int = 1
    square_feet: Optional[Decimal] = None
    ownership_percent: Optional[Decimal] = None
    category: Optional[Literal["residential", "commercial", "mixed"]] = None
    parking_spaces: int = 0


class PoolDefinition(BaseModel):
    """Engine-facing view of a pool — already joined with its denominator
    and per-unit overrides if any. The engine never reads the underlying
    AllocationPool row directly; the resolver hands it this shape."""

    model_config = ConfigDict(from_attributes=True)

    pool_id: int
    pool_key: str
    pool_name: str
    allocation_method: AllocationMethod
    recipient_scope: RecipientScope
    denominator_value: Optional[Decimal] = None
    include_in_pdf: bool = True
    display_order: int = 0
    # Structural classifier (add-variable-special-assessments). None = ordinary
    # pool; "separately_billed_special_assessment" = a special assessment the
    # engine allocates as a one-time total and diverts out of monthly dues.
    # Never derived from pool_name.
    pool_kind: Optional[str] = None


class RecipientSet(BaseModel):
    """All recipients that may appear in a calc run. The engine filters by
    each pool's ``recipient_scope`` to derive its applicable subset."""

    recipients: list[RecipientReference]


class BudgetLineInput(BaseModel):
    """One promoted budget line consumed by the engine.

    ``amount`` is ALWAYS the annual amount by invariant (enforced at parser
    promotion; see spec §Budget Line Amount Convention). The engine never
    interprets ``amount`` as monthly.
    """

    model_config = ConfigDict(from_attributes=True)

    line_id: int
    normalized_label: str
    section: str
    category: Literal["income", "operating", "reserve_income", "reserve_expense"]
    fund_type: Literal["operating", "reserve"]
    account_code: Optional[str] = None
    amount: Decimal


class BudgetLineMappingInput(BaseModel):
    """One BudgetLinePoolMapping row as the engine consumes it.

    The engine matches each ``BudgetLineInput`` to one of these by the
    disambiguating key
    ``(normalized_label, section, category, fund_type, account_code)``;
    only ``active=True`` mappings participate.
    """

    model_config = ConfigDict(from_attributes=True)

    budget_line_normalized_label: str
    section: str
    category: Literal["income", "operating", "reserve_income", "reserve_expense"]
    fund_type: Literal["operating", "reserve"]
    account_code: Optional[str] = None
    pool_key: str
    active: bool = True


class AssessmentOverride(BaseModel):
    """Operator-applied override that wins over engine-calculated math.

    Applied AFTER recipient summation and rounding, BEFORE reconciliation.
    Internal-only; never rendered in the homeowner PDF.
    """

    model_config = ConfigDict(from_attributes=True)

    scope: Literal["package", "group", "unit", "pool"]
    scope_ref_id: Optional[int] = None
    override_type: Literal[
        "board_approved", "rounding", "manual_correction", "spreadsheet"
    ]
    override_monthly_amount: Decimal
    reason: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None


class SpecialAssessmentInput(BaseModel):
    """One ``hoa_settings.special_assessments_json`` entry as the engine
    consumes it. Mirrors the JSON shape extended in Phase 4.4 plus a
    pre-resolved ``status`` (caller runs ``infer_special_assessment_status``
    before handing it to the engine — keeps the engine decoupled from
    the preflight module).
    """

    model_config = ConfigDict(extra="ignore")

    status: Literal["none", "approved_scheduled", "possible_disclosure_only"]
    amount_per_unit: Decimal = Decimal("0")
    included_in_regular_monthly: bool = False
    recipient_scope: Optional[
        Literal[
            "all_units",
            "residential_only",
            "commercial_only",
            "parking_users",
            "custom_unit_list",
        ]
    ] = None
    custom_unit_ids: list[int] = []
    label: Optional[str] = None
    display_language: Optional[str] = None
    due_date: Optional[str] = None


class CalcInput(BaseModel):
    """Complete input bundle for one engine run.

    ``mappings`` routes budget lines to pools (one-to-one match on the
    disambiguating key). ``specified_value_lookup`` supplies the
    (unit_id, pool_key) → monthly amount table for ``specified_value``
    pools. ``pool_custom_recipients`` supplies per-pool unit-id lists
    for the ``custom_unit_list`` scope (keyed by pool_key).
    ``special_assessments`` are applied per the Phase 4.4 behavior
    matrix between pool math and recipient summation.
    """

    setup_type: SetupType
    pools: list[PoolDefinition]
    recipient_set: RecipientSet
    budget_lines: list[BudgetLineInput]
    mappings: list[BudgetLineMappingInput] = []
    approved_assessment_revenue_annual: Decimal
    specified_value_lookup: dict[tuple[int, str], Decimal] = {}
    pool_custom_recipients: dict[str, list[int]] = {}
    overrides: list[AssessmentOverride] = []
    special_assessments: list[SpecialAssessmentInput] = []
    package_id: Optional[int] = None


class PoolAllocationResult(BaseModel):
    """One (recipient, pool) component row. Maps to
    AssessmentPoolAllocationResult in the DB."""

    recipient_ref: RecipientReference
    pool_id: Optional[int]
    pool_key: str
    unrounded_component_monthly: Decimal
    source: PoolSource = "pool_math"


class RecipientTotalResult(BaseModel):
    """One per-recipient bottom-line row. Maps to
    AssessmentRecipientTotalResult in the DB.

    Rounding happens here, once, after summing all components for the
    recipient — never at the pool level.
    """

    recipient_ref: RecipientReference
    raw_monthly_total: Decimal
    rounded_monthly_total: Decimal
    annual_total: Decimal
    rounding_delta_contribution: Decimal


class SpecialAssessmentRendererEvent(BaseModel):
    """Engine → renderer signal for a special assessment that doesn't
    flow into recipient monthlies. Two kinds:

    - ``separate_disclosure_block``: ``approved_scheduled`` +
      ``included_in_regular_monthly=False`` — render a separate
      "Special Assessment Schedule" block with amount + due date.
    - ``disclosure_language``: ``possible_disclosure_only`` — render
      ``display_language`` verbatim in the cover letter + §5570
      summary.

    These never affect reconciliation. The engine emits them so the
    renderer doesn't have to re-parse ``special_assessments_json`` on
    its own.
    """

    kind: Literal["separate_disclosure_block", "disclosure_language"]
    label: Optional[str] = None
    amount_per_unit: Optional[Decimal] = None
    due_date: Optional[str] = None
    display_language: Optional[str] = None


class AppliedOverrideEntry(BaseModel):
    """One audit entry per applied AssessmentOverride (Phase 4.5 task 121).

    Recorded by the engine so the operator UI + internal audit JSON can
    show: which override fired, what the engine had computed before the
    override won, and what the operator-supplied amount was. Never
    rendered into the homeowner-visible PDF.
    """

    scope: Literal["package", "group", "unit", "pool"]
    scope_ref_id: Optional[int] = None
    override_type: Literal[
        "board_approved", "rounding", "manual_correction", "spreadsheet"
    ]
    original_calculated_monthly: Optional[Decimal] = None
    override_monthly: Decimal
    delta_monthly: Optional[Decimal] = None
    reason: Optional[str] = None
    approved_by: Optional[str] = None


class OrphanedPoolReport(BaseModel):
    """A ``pool_key`` that budget-line mappings route dollars to, but which
    has no ``PoolDefinition`` in the current setup (H1).

    Without this report those dollars would be aggregated into the routing
    dict and then never read back out by ``run()``'s pool loop — silently
    dropped from both distribution and the ``pool_sum_annual`` diagnostic.
    The caller surfaces this as a resolvable Assessment Mapping Review row
    (remap the line to a current pool, or exclude it).
    """

    pool_key: str
    annual_total: Decimal
    contributing_line_labels: list[str] = []


class ZeroRecipientPoolReport(BaseModel):
    """A pool that receives nonzero mapped dollars but whose
    ``recipient_scope`` resolves to zero recipients (H2) — e.g. a
    ``residential_only`` pool in an all-commercial building, or a
    ``parking_users`` pool where no unit has parking.

    Without this report the money is counted into ``pool_sum_annual`` and
    then the pool is skipped with a silent ``continue`` — the dollars
    disappear from distribution on a plausibly-valid setup. The caller
    surfaces this so the operator can remap/exclude the feeding lines, or
    fix the unit roster in the Review Workbench and repromote.
    """

    pool_key: str
    recipient_scope: str
    annual_total: Decimal
    contributing_line_labels: list[str] = []


class SpecialAssessmentAllocationEntry(BaseModel):
    """One recipient's share of a special-assessment pool's total."""

    recipient_ref: RecipientReference
    amount: Decimal


class SpecialAssessmentAllocation(BaseModel):
    """A ``separately_billed_special_assessment`` pool's total allocated once
    across recipients (a one-time lump — NOT annualized/÷12), by the pool's
    basis. Produced by ``run()`` for special-kind pools and kept OUT of
    ``recipient_totals`` / reconciliation (separately billed). The matrix maps
    it into ``special_assessment_blocks`` for rendering.
    """

    pool_key: str
    label: str
    allocation_method: AllocationMethod
    total: Decimal
    entries: list[SpecialAssessmentAllocationEntry] = []


class CalcResultSet(BaseModel):
    """Full engine output. Templates that need per-pool columns read
    ``pool_allocations``; templates that need bottom-line dues read
    ``recipient_totals``. Reconciliation always uses ``recipient_totals``."""

    pool_allocations: list[PoolAllocationResult]
    recipient_totals: list[RecipientTotalResult]
    rounding_delta_annual: Decimal
    rounding_delta_monthly: Decimal
    rounding_delta_percent: Decimal
    pool_sum_annual: Decimal
    warnings: list[str] = []
    special_assessment_events: list[SpecialAssessmentRendererEvent] = []
    # Pool-based special assessments (separately billed, allocated once).
    # Empty on setups with no special-kind pool, so existing output is
    # byte-unchanged.
    special_assessment_allocations: list[SpecialAssessmentAllocation] = []
    applied_overrides: list[AppliedOverrideEntry] = []
    # Money-routing integrity reports (H1/H2). Empty on a clean run, so
    # output bytes are unchanged for setups with no routing issues.
    orphaned_pool_lines: list[OrphanedPoolReport] = []
    zero_recipient_pools: list[ZeroRecipientPoolReport] = []
