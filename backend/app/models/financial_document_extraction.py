"""Canonical contracts for multi-format financial document extraction."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..services.income_statement_parser import SECTION_KINDS


class FinancialDocumentRoute(BaseModel):
    """Centralized route decision for uploaded financial documents."""

    path: Literal["excel_deterministic", "pdf_vlm"]
    family: Optional[str] = None
    reason: str
    is_supported: bool = True
    requires_review_on_low_confidence: bool = True


class ExtractedStatementLineItem(BaseModel):
    """Canonical extracted line item shared by deterministic and VLM paths."""

    account_code_text: Optional[str] = None
    label: str = Field(min_length=1)
    section_label: Optional[str] = None
    section_kind: Optional[str] = None
    current_actual: Optional[float] = None
    current_budget: Optional[float] = None
    current_variance: Optional[float] = None
    ytd_actual: Optional[float] = None
    ytd_budget: Optional[float] = None
    ytd_variance: Optional[float] = None
    annual_budget: Optional[float] = None
    page_number: Optional[int] = Field(default=None, ge=1)
    evidence: Optional[dict[str, Any]] = None

    @field_validator("section_kind", mode="before")
    @classmethod
    def _normalize_section_kind(cls, v: Any) -> Optional[str]:
        """Catch section_kind drift at the API boundary.

        Accepts one of SECTION_KINDS or None. Silently coerces legacy
        3-value "reserve" → None for transitional compatibility. Raises
        ValueError on any other string so bad Gemini outputs fail fast.
        """
        if v is None or v in SECTION_KINDS:
            return v
        if v == "reserve":  # legacy 3-value taxonomy
            return None
        raise ValueError(
            f"section_kind must be one of {SECTION_KINDS} or null, got {v!r}"
        )


class ExtractedFinancialStatement(BaseModel):
    """Canonical extracted statement shape before downstream normalization.

    A valid financial statement extraction must contain a meaningful number of
    line items. Single-row or empty extractions indicate a failed parse, not a
    real statement.
    """

    model_config = ConfigDict(extra="forbid")

    document_family: str = Field(min_length=1)
    report_type: str = Field(min_length=1)
    statement_period: Optional[str] = None
    line_items: list[ExtractedStatementLineItem] = Field(
        default_factory=list,
        min_length=2,
        description="Every detail row from the statement. Must include all visible line items, not a summary.",
    )
    totals: list[dict[str, Any]] = Field(default_factory=list)
    validation_issues: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    # Free-form metadata about HOW this extraction was produced. Populated
    # by extractors that take a degraded path (e.g. vision-only fallback for
    # scanned PDFs) so that downstream code can surface a quality warning to
    # the end user. NOT used by the validator — purely informational.
    extraction_metadata: dict[str, Any] = Field(default_factory=dict)


class ExtractedFinancialStatementPage(BaseModel):
    """Per-page extraction result — no min_length on line_items.

    Used during parallel per-page extraction where a single page may have
    0 or 1 items. The min_length=2 check runs on the merged result instead.
    """

    model_config = ConfigDict(extra="forbid")

    document_family: str = Field(min_length=1)
    report_type: str = Field(min_length=1)
    statement_period: Optional[str] = None
    line_items: list[ExtractedStatementLineItem] = Field(default_factory=list)
    totals: list[dict[str, Any]] = Field(default_factory=list)
    validation_issues: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class DocumentExtractionFailure(BaseModel):
    """Structured failure contract for review-needed document extraction."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)

