"""Pydantic schemas for HOA list/detail/update endpoints."""
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from ..assessment_mode import AssessmentMode, ASSESSMENT_MODE_VARIABLE


class PortfolioNextAction(BaseModel):
    label: str
    href: str
    code: str = ""


class HOAListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    hoa_code: str = ""
    name: str
    tax_id: str = ""
    units: int = 0
    reserve_inflation_rate: float = 0.0
    fiscal_year_start_month: int = 1
    fiscal_year_end_month: int = 12
    city: str = ""
    portfolio_year: Optional[int] = None
    workflow_status: str = "Not Started"
    assessment_mode: AssessmentMode = ASSESSMENT_MODE_VARIABLE
    # Derived portfolio readiness (package year) — optional for older clients
    portfolio_status: Optional[str] = None
    readiness_pct: Optional[int] = None
    readiness_done: Optional[int] = None
    readiness_total: Optional[int] = None
    next_action: Optional[PortfolioNextAction] = None
    last_worked_at: Optional[str] = None
    has_active_draft: Optional[bool] = None
    latest_budget_version_id: Optional[int] = None


class HOADetail(HOAListItem):
    created_at: Optional[str] = None


class HOACreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    # Optional at create time — the DRE extraction is the source of truth for
    # unit count. The HOA can be created with units=None (interpreted as 0,
    # "Pending DRE" in the UI); dre_approval_service writes the extracted
    # ``document_metadata.total_units`` back to ``properties.units`` once an
    # operator approves the run.
    units: Optional[int] = Field(default=None, ge=0, le=100000)
    fiscal_year_start_month: int = Field(ge=1, le=12)
    fiscal_year_end_month: Optional[int] = Field(default=None, ge=1, le=12)
    city: Optional[str] = Field(default=None, max_length=255)
    assessment_mode: AssessmentMode = ASSESSMENT_MODE_VARIABLE
    # Package / disclosure year (e.g. 2026). When omitted, create uses the
    # current calendar year. Distinct from fiscal_year_start_month (calendar).
    portfolio_year: Optional[int] = Field(default=None, ge=1990, le=2100)


class HOAUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    hoa_code: Optional[str] = Field(default=None, max_length=64)
    tax_id: Optional[str] = Field(default=None, max_length=64)
    units: Optional[int] = Field(default=None, ge=1, le=100000)
    fiscal_year_start_month: int = Field(ge=1, le=12)
    fiscal_year_end_month: Optional[int] = Field(default=None, ge=1, le=12)
    city: Optional[str] = Field(default=None, max_length=255)
    reserve_inflation_rate: Optional[float] = Field(default=None, ge=0, le=1)
    assessment_mode: Optional[AssessmentMode] = None
    # Package / disclosure year shown on PDFs and used as generate default.
    portfolio_year: Optional[int] = Field(default=None, ge=1990, le=2100)
