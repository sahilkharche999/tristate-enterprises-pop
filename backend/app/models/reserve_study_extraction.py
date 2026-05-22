"""Canonical contracts for reserve-study discovery and extraction."""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ReserveStudyPageRole(str, Enum):
    RESERVE_TABLE = "reserve_table"
    RESERVE_CONTEXT = "reserve_context"
    UNRELATED = "unrelated"


class ReserveStudyPageClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_number: int = Field(ge=1)
    role: ReserveStudyPageRole
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    ui_fields_present: list[str] = Field(default_factory=list)
    is_primary_ui_table: bool = False
    same_table_as_previous: bool = False
    same_table_as_next: bool = False
    table_title_hint: Optional[str] = None
    is_tabular_schedule: bool = False
    is_component_detail_appendix: bool = False
    adds_new_component_rows: bool = False
    is_duplicate_component_repeat_page: bool = False
    is_year_provision_or_liability_schedule: bool = False


class ReserveStudyPageSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_page: int = Field(ge=1)
    end_page: int = Field(ge=1)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ReserveStudyDiscoveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classifications: list[ReserveStudyPageClassification] = Field(default_factory=list)
    page_spans: list[ReserveStudyPageSpan] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    extraction_metadata: dict[str, Any] = Field(default_factory=dict)


class ExtractedReserveStudyRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_id: str = Field(min_length=1)
    row_type: Literal["item", "header"] = "item"
    line_item: str = Field(min_length=1)
    useful_life: Optional[int] = Field(default=None, ge=0)
    remaining_life: Optional[int] = Field(default=None, ge=0)
    quantity: Optional[str] = None
    replacement_cost: Optional[float] = Field(default=None, ge=0.0)
    year_new: Optional[int] = Field(default=None, ge=1900, le=3000)
    reference_year: Optional[int] = Field(default=None, ge=1900, le=3000)
    year_replacement_provision: Optional[int] = Field(default=None, ge=0)
    estimated_liability: Optional[int] = Field(default=None, ge=0)
    source_page: Optional[int] = Field(default=None, ge=1)
    flags: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _blank_invalid_negative_amounts(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        normalized = dict(data)
        flags = list(normalized.get("flags") or [])
        for field_name in (
            "useful_life",
            "remaining_life",
            "replacement_cost",
            "year_replacement_provision",
            "estimated_liability",
        ):
            value = normalized.get(field_name)
            if isinstance(value, (int, float)) and value < 0:
                normalized[field_name] = None
                flags.append(f"invalid_negative_{field_name}")
        normalized["flags"] = list(dict.fromkeys(flags))
        return normalized

    @field_validator("quantity", mode="before")
    @classmethod
    def _normalize_quantity(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return str(value).strip() or None


class ExtractedReserveStudyPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    study_date: Optional[str] = None
    study_year: Optional[int] = Field(default=None, ge=1900, le=3000)
    applicable_fiscal_year: Optional[int] = Field(default=None, ge=1900, le=3000)
    reference_year: Optional[int] = Field(default=None, ge=1900, le=3000)
    first_forecast_year: Optional[int] = Field(default=None, ge=1900, le=3000)
    rows: list[ExtractedReserveStudyRow] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ExtractedReserveStudyDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    study_date: Optional[str] = None
    study_year: Optional[int] = Field(default=None, ge=1900, le=3000)
    applicable_fiscal_year: Optional[int] = Field(default=None, ge=1900, le=3000)
    reference_year: Optional[int] = Field(default=None, ge=1900, le=3000)
    classifications: list[ReserveStudyPageClassification] = Field(default_factory=list)
    page_spans: list[ReserveStudyPageSpan] = Field(default_factory=list)
    rows: list[ExtractedReserveStudyRow] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    extraction_metadata: dict[str, Any] = Field(default_factory=dict)
