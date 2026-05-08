"""Pydantic schemas for HOA list/detail/update endpoints."""
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


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


class HOADetail(HOAListItem):
    created_at: Optional[str] = None


class HOACreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    units: int = Field(ge=1, le=100000)
    fiscal_year_start_month: int = Field(ge=1, le=12)
    fiscal_year_end_month: Optional[int] = Field(default=None, ge=1, le=12)
    city: Optional[str] = Field(default=None, max_length=255)


class HOAUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    hoa_code: Optional[str] = Field(default=None, max_length=64)
    tax_id: Optional[str] = Field(default=None, max_length=64)
    units: Optional[int] = Field(default=None, ge=1, le=100000)
    fiscal_year_start_month: int = Field(ge=1, le=12)
    fiscal_year_end_month: Optional[int] = Field(default=None, ge=1, le=12)
    city: Optional[str] = Field(default=None, max_length=255)
