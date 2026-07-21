"""Pure helpers for assessment-mapping category / fund_type normalization.

Leaf module: stdlib only. Shared by budget history, disclosure service, and
assessment schedule matrix (ponytail Tier D #19). Do not import god modules.
"""

from __future__ import annotations


def _assessment_mapping_category(raw_category: object) -> str:
    category = str(raw_category or "").lower()
    if category == "income":
        return "income"
    if category == "reserve_income":
        return "reserve_income"
    if category in {"reserve", "reserve_expense"}:
        return "reserve_expense"
    return "operating"


def _assessment_mapping_fund_type(category: str) -> str:
    return "reserve" if category in {"reserve_income", "reserve_expense"} else "operating"
