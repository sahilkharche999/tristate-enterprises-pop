"""Pydantic schemas for the AI Budget Pipeline API and internal pipeline."""
from typing import Optional
from pydantic import BaseModel, Field


# ── Input models ──────────────────────────────────────────────────────────────

class LineItemInput(BaseModel):
    account_code: int
    account_name: str
    label: str
    category: Optional[str] = "operating"
    ytd_actual: float
    annual_budget: float
    projection: Optional[float] = None
    current_pct_change: Optional[float] = 0.0


class SuggestRequest(BaseModel):
    property_name: str
    line_items: list[LineItemInput]
    total_annual_budget: float
    total_ytd_actuals: float
    pct_year_elapsed: float
    fiscal_year: int
    statement_month: int = Field(..., ge=1, le=12)
    growth_factor: float = 1.0


class FeedbackDecision(BaseModel):
    feedback_case_id: int
    decision: str = Field(..., pattern="^(accepted|modified|rejected)$")
    final_pct_change: float
    note: Optional[str] = ""


class FeedbackRequest(BaseModel):
    run_id: int
    decisions: list[FeedbackDecision]


# ── Output models ─────────────────────────────────────────────────────────────

class SuggestionItem(BaseModel):
    id: int                              # feedback_case_id
    account_code: int
    account_name: str
    suggested_pct_change: float
    reason: str
    confidence: float
    revised_by_pass2: bool = False
    cbr_match: Optional[float] = None
    ml_baseline: Optional[float] = None


class FlaggedItem(BaseModel):
    account_code: int
    issue: str
    revised_pct_change: float
    revised_reason: str


class SuggestResponse(BaseModel):
    run_id: int
    suggestions: list[SuggestionItem]
    executive_summary: str
    coherence_score: str = "medium"
    total_budget_impact: str
    flagged_items: list[FlaggedItem] = []


class FeedbackResponse(BaseModel):
    updated: int
    total_cases: int
    catboost_active: bool


class StatsResponse(BaseModel):
    total_cases: int
    catboost_active: bool
    last_training: Optional[str] = None
    properties: list[str] = []
    sop_rules: int


# ── Internal pipeline models ───────────────────────────────────────────────────

class EnrichedLineItem(BaseModel):
    # Original fields
    account_code: int
    account_name: str
    label: str
    category: Optional[str] = "operating"
    ytd_actual: float
    annual_budget: float
    projection: Optional[float] = None
    current_pct_change: Optional[float] = 0.0

    # Hierarchical encoding
    account_level_1: int = 0
    account_level_2: int = 0
    account_level_3: int = 0
    is_income: bool = False
    is_reserve: bool = False
    is_admin: bool = False

    # Seasonality and adjusted features
    seasonality_index: float = 0.25
    adjusted_projection: Optional[float] = None
    adjusted_pct_diff: float = 0.0
    adjusted_coverage_ratio: float = 0.0
    pct_diff: float = 0.0
    coverage_ratio: float = 0.0
    normalized_annual_budget: float = 0.0

    # Flags
    read_only: bool = False


class LLMPass1Result(BaseModel):
    account_code: int
    suggested_pct_change: float = Field(..., ge=-0.30, le=0.30)
    reason: str
    confidence: float = Field(..., ge=0.0, le=1.0)


class LLMPass2FlaggedItem(BaseModel):
    account_code: int
    issue: str
    revised_pct_change: float
    revised_reason: str


class LLMPass2Result(BaseModel):
    executive_summary: str
    coherence_score: str = "medium"
    flagged_items: list[LLMPass2FlaggedItem] = []
