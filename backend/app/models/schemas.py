from pydantic import BaseModel
from typing import Any, List, Optional


class TableResponse(BaseModel):
    sheet: str
    headers: List[Optional[Any]]
    rows: List[List[Optional[Any]]]


class SumResponse(BaseModel):
    sheet: str
    sum_cell: str
    sum: float


class DupsResponse(BaseModel):
    duplicates: List[dict]


class SimpleResponse(BaseModel):
    message: str
