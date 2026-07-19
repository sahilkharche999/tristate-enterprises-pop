"""Domain models for the assessment engine.

These mirror the DB tables defined in design.md (AssessmentSetup,
AllocationPool, BudgetLinePoolMapping). Engine output assembly uses
schemas.CalcResultSet / PoolAllocationResult, not models here.
"""

from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


SetupType = Literal["fixed", "grouped", "per_unit"]

AllocationMethod = Literal[
    "equal",
    "square_footage",
    "ownership_percentage",
    "specified_value",
]

RecipientScope = Literal[
    "all_units",
    "residential_only",
    "commercial_only",
    "parking_users",
    "custom_unit_list",
]

PoolSource = Literal["pool_math", "special_assessment", "override"]


class AssessmentSetup(BaseModel):
    """A saved DRE-derived setup for one HOA. One row per HOA per supersession.

    Mirrors the AssessmentSetup table; ``status='approved'`` rows are the
    only ones the engine consumes.
    """

    model_config = ConfigDict(from_attributes=True)

    assessment_setup_id: int
    property_id: int
    source_dre_document_id: Optional[int] = None
    setup_type: SetupType
    display_mode: SetupType
    effective_from: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[str] = None
    approved_at: Optional[str] = None
    status: Literal["draft", "approved", "superseded"] = "draft"


class AllocationPool(BaseModel):
    """One bucket of assessment dollars allocated by a single method.

    ``pool_key`` is the stable identifier that survives setup supersessions;
    ``denominator_value`` is frozen from the DRE — never recomputed by the
    engine.

    ``escalation_schedule_json`` + ``starting_monthly_per_unit`` are the
    pool's 30-year-forecast inputs (Task #185 of dre-driven-assessment-engine).
    Both are operator-edited and supersede the deprecated hoa_settings
    columns of the same purpose when set.
    """

    model_config = ConfigDict(from_attributes=True)

    pool_id: int
    assessment_setup_id: int
    pool_key: str
    pool_name: str
    allocation_method: AllocationMethod
    recipient_scope: RecipientScope
    denominator_source: Literal["dre_value", "calculated", "manual"]
    denominator_value: Optional[Decimal] = None
    variable_flag: bool = False
    display_order: int = 0
    include_in_pdf: bool = True
    escalation_schedule_json: Optional[str] = None
    starting_monthly_per_unit: Optional[Decimal] = None


class BudgetLinePoolMapping(BaseModel):
    """Routes a budget line to a pool. AI-suggested + human-approved.

    The disambiguating key is
    ``(property_id, assessment_setup_id, normalized_label, section, category, fund_type)``
    plus ``active=True``; ``pool_key`` carries the routing target across
    setup supersessions.
    """

    model_config = ConfigDict(from_attributes=True)

    mapping_id: int
    property_id: int
    assessment_setup_id: int
    budget_line_normalized_label: str
    section: str
    category: Literal["income", "operating", "reserve_income", "reserve_expense"]
    fund_type: Literal["operating", "reserve"]
    account_code: Optional[str] = None
    pool_key: str
    pool_id: int
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    active: bool = True
