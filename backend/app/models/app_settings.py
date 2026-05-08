"""Pydantic schemas for app-level settings endpoints."""
from pydantic import BaseModel, Field


class AppSettingsPayload(BaseModel):
    global_reserve_inflation_rate: float = Field(default=0.0, ge=0)
