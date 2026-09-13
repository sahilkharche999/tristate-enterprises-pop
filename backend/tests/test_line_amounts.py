from decimal import Decimal

from app.disclosure_package.line_amounts import resolve_canonical_line_amount
from app.services.assessment_budget_mapping_rule_service import (
    select_assessment_mapping_amount,
)


def test_proposed_amount_wins_on_mapping_and_disclosure_paths() -> None:
    line = {"annual_budget": "10000", "proposed_amount": "11000", "amount": "9000"}
    mapping_amount, mapping_source = select_assessment_mapping_amount(line)
    disclosure_amount, disclosure_source = resolve_canonical_line_amount(line)
    assert mapping_amount == Decimal("11000")
    assert disclosure_amount == Decimal("11000")
    assert mapping_source == disclosure_source == "proposed_amount"


def test_annual_budget_applies_percent_change() -> None:
    amount, source = resolve_canonical_line_amount(
        {"annual_budget": "10000", "percent_change": "10"}
    )
    assert amount == Decimal("11000")
    assert source == "annual_budget_percent_change"
