"""Pydantic schemas for app-level settings endpoints."""
from typing import Optional

from pydantic import BaseModel, Field


class SectionCatalogItem(BaseModel):
    template: str
    label: str
    required: bool
    hidden: bool = False


class AppSettingsPayload(BaseModel):
    global_reserve_inflation_rate: Optional[float] = Field(default=None, ge=0)
    disclosure_section_order: Optional[list[str]] = None
    disclosure_hidden_sections: Optional[list[str]] = None
    section_catalog: list[SectionCatalogItem] = Field(default_factory=list)
    has_firm_signature: bool = False
