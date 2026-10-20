"""C5/C6 — financial-cell parsing and statement-totals validation.

Covers the two criticals closed by the fix-critical-disclosure-integrity
change's Track A:

- C5 (`financial-cell-parsing` spec): no numeric cell ever silently becomes
  $0; negative forms parse WITH sign; unparseable promoted cells surface a
  review question instead of a fake zero.
- C6 (`statement-totals-validation` spec): the document's own "Total …" rows
  are captured, and the subtotal cross-check actually runs on the
  deterministic parse path.
"""
from app.services.income_statement_parser import (
    parse_financial_cell,
)
from app.services.budget_history_service import (
    _canonical_statement_from_line_items,
    _parse_float,
)
from app.services.financial_statement_validation import (
    has_blocking_validation_issues,
    validate_extracted_statement,
)


# ---------------------------------------------------------------------------
# C5 — parse_financial_cell normalizer
# ---------------------------------------------------------------------------


class TestParseFinancialCell:
    def test_parenthesized_with_currency_symbol_is_negative(self):
        # The review's headline sign-loss bug: "($1,234.56)" silently became 0.
        cell = parse_financial_cell("($1,234.56)")
        assert cell.kind == "ok"
        assert cell.value == -1234.56

    def test_parenthesized_plain_is_negative(self):
        assert parse_financial_cell("(1,500)").value == -1500.0

    def test_trailing_minus_is_negative(self):
        cell = parse_financial_cell("1,234.56-")
        assert cell.kind == "ok"
        assert cell.value == -1234.56

    def test_leading_minus_is_negative(self):
        assert parse_financial_cell("-1234").value == -1234.0

    def test_currency_and_thousands_strip(self):
        assert parse_financial_cell("$1,043,706.24").value == 1043706.24

    def test_true_zero_parses_as_zero(self):
        cell = parse_financial_cell("0.00")
        assert cell.kind == "ok"
        assert cell.value == 0.0

    def test_dash_variants_are_dash_kind_not_zero(self):
        for dash in ("-", "—", "–"):
            cell = parse_financial_cell(dash)
            assert cell.kind == "dash"
            assert cell.value is None

    def test_none_and_blank_are_empty(self):
        assert parse_financial_cell(None).kind == "empty"
        assert parse_financial_cell("   ").kind == "empty"

    def test_ocr_noise_is_unparseable_not_zero(self):
        cell = parse_financial_cell("1,234.5.6")
        assert cell.kind == "unparseable"
        assert cell.value is None
        assert cell.raw == "1,234.5.6"

    def test_numeric_passthrough(self):
        assert parse_financial_cell(5).value == 5.0
        assert parse_financial_cell(5.5).value == 5.5

    def test_bool_is_not_money(self):
        assert parse_financial_cell(True).kind == "unparseable"

    def test_budget_history_float_parsing_matches_the_cell_parser(self):
        # Same value for the same text on every path that reads money.
        for text in ("($1,234.56)", "1,234.56-", "$99.10"):
            assert parse_financial_cell(text).value == _parse_float(text)


# ---------------------------------------------------------------------------
# C6 — canonical statement totals + subtotal cross-check
# ---------------------------------------------------------------------------


def _line_items(*, landscaping: float = 400.0, insurance: float = 800.0):
    return [
        {"label": "Landscaping", "category": "operating", "section": "operating",
         "annual_budget": landscaping, "account_code": None, "raw": {}},
        {"label": "Insurance", "category": "operating", "section": "operating",
         "annual_budget": insurance, "account_code": None, "raw": {}},
    ]


class TestSubtotalCrossCheck:
    def test_document_totals_reach_statement(self):
        statement = _canonical_statement_from_line_items(
            _line_items(),
            family="known_clean_excel_workbook",
            stated_totals=[
                {"section": "operating", "label": "Total Operating Expense",
                 "annual_budget": 1200.0},
            ],
        )
        assert statement.totals == [
            {"section_kind": "operating", "label": "Total Operating Expense",
             "amount": 1200.0, "source": "document"},
        ]

    def test_grand_total_wins_over_subtotal(self):
        # Sub-totals partition the section; the max-|amount| row is the
        # grand total and must be the one compared against.
        statement = _canonical_statement_from_line_items(
            _line_items(),
            family="known_clean_excel_workbook",
            stated_totals=[
                {"section": "operating", "label": "Total Utilities",
                 "annual_budget": 400.0},
                {"section": "operating", "label": "Total Operating Expense",
                 "annual_budget": 1200.0},
            ],
        )
        assert len(statement.totals) == 1
        assert statement.totals[0]["amount"] == 1200.0

    def test_mismatch_is_flagged_not_blocking(self):
        # Decided 2026-10-20: a totals mismatch creates the draft and is shown
        # to the operator, it does not reject the upload.
        statement = _canonical_statement_from_line_items(
            _line_items(insurance=200.0),  # lines sum to 600
            family="known_clean_excel_workbook",
            stated_totals=[
                {"section": "operating", "label": "Total Operating Expense",
                 "annual_budget": 1200.0},
            ],
        )
        issues = validate_extracted_statement(statement)
        mismatches = [i for i in issues if i["code"] == "subtotal_mismatch"]
        assert len(mismatches) == 1
        assert mismatches[0]["severity"] == "warning"
        assert mismatches[0]["details"]["totals_source"] == "document"
        assert mismatches[0]["details"]["expected_total"] == 1200.0
        assert mismatches[0]["details"]["actual_total"] == 600.0
        assert not has_blocking_validation_issues(issues)

    def test_within_one_dollar_tolerance_passes(self):
        statement = _canonical_statement_from_line_items(
            _line_items(insurance=800.60),  # lines sum to 1200.60
            family="known_clean_excel_workbook",
            stated_totals=[
                {"section": "operating", "label": "Total Operating Expense",
                 "annual_budget": 1200.0},
            ],
        )
        issues = validate_extracted_statement(statement)
        assert not [i for i in issues if i["code"] == "subtotal_mismatch"]

    def test_no_stated_totals_no_check(self):
        statement = _canonical_statement_from_line_items(
            _line_items(),
            family="known_clean_excel_workbook",
        )
        issues = validate_extracted_statement(statement)
        assert not [i for i in issues if i["code"] == "subtotal_mismatch"]

    def test_model_totals_labeled_self_consistency(self):
        # Gemini-path totals (no source tag) must be distinguishable.
        statement = _canonical_statement_from_line_items(
            _line_items(insurance=200.0),
            family="pdf_visual_document",
        )
        statement.totals.append(
            {"section_kind": "operating", "amount": 1200.0}
        )
        issues = validate_extracted_statement(statement)
        mismatches = [i for i in issues if i["code"] == "subtotal_mismatch"]
        assert len(mismatches) == 1
        assert mismatches[0]["details"]["totals_source"] == "model"


class TestStandardTotalPresentations:
    """Real statements print a section's total in two standard ways; neither is a mismatch."""

    def _statement(self, items, totals):
        from app.models.financial_document_extraction import ExtractedFinancialStatement

        return ExtractedFinancialStatement.model_validate(
            {"document_family": "excel_budget_workbook", "report_type": "final_budget",
             "line_items": items, "totals": totals}
        )

    def test_operating_total_printed_without_the_reserve_contribution(self):
        # Old Mill 2027: TOTAL OPERATING EXPENSES excludes the contribution row.
        statement = self._statement(
            [{"label": "Management", "section_kind": "operating", "annual_budget": 1225993.64},
             {"label": "MONTHLY CONTRIBUTION TO RESERVE", "section_kind": "operating", "annual_budget": 808916.36}],
            [{"section_kind": "operating", "amount": 1225993.64, "source": "document"}],
        )
        assert not [i for i in validate_extracted_statement(statement) if i["code"] == "subtotal_mismatch"]

    def test_revenue_total_printed_with_reserve_interest(self):
        # Levy forecasts: Total revenues includes interest on the replacement fund.
        statement = self._statement(
            [{"label": "Assessments", "section_kind": "income", "annual_budget": 2034910.0},
             {"label": "Interest income", "section_kind": "reserve_income", "annual_budget": 67800.0}],
            [{"section_kind": "income", "amount": 2102710.0, "source": "document"}],
        )
        assert not [i for i in validate_extracted_statement(statement) if i["code"] == "subtotal_mismatch"]

    def test_a_dropped_row_is_still_flagged(self):
        statement = self._statement(
            [{"label": "Assessments", "section_kind": "income", "annual_budget": 2000000.0},
             {"label": "Interest income", "section_kind": "reserve_income", "annual_budget": 67800.0}],
            [{"section_kind": "income", "amount": 2102710.0, "source": "document"}],
        )
        assert [i for i in validate_extracted_statement(statement) if i["code"] == "subtotal_mismatch"]
