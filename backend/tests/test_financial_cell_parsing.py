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
    parse_rows_with_sections,
    _parse_financial_float,
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

    def test_float_wrapper_keeps_zero_collapse_but_gains_sign(self):
        # Wrapper behavior contract: unparseable/dash/empty -> 0.0 (legacy
        # arithmetic sites), negatives now carry sign.
        assert _parse_financial_float("($1,234)") == -1234.0
        assert _parse_financial_float("1,234-") == -1234.0
        assert _parse_financial_float("junk") == 0.0
        assert _parse_financial_float("-") == 0.0

    def test_all_entry_points_agree(self):
        # C5 spec: income-statement path and budget-history path produce the
        # same value for the same text.
        for text in ("($1,234.56)", "1,234.56-", "$99.10", "-", "junk"):
            assert _parse_financial_float(text) == _parse_float(text)


# ---------------------------------------------------------------------------
# C5/C6 — row parsing: null propagation, review questions, totals capture
# ---------------------------------------------------------------------------


def _rows_with_annual_at_2(annual_cells: dict[str, object], totals: bool = True):
    """Minimal statement layout: col0=section/blank, col1=label, col2=annual."""
    rows = [
        ["Operating Income", None, None],
        [None, "Assessment Income", annual_cells.get("income", "1200.00")],
        (["Total Operating Income", None, "1200.00"] if totals else [None, None, None]),
        ["Operating Expense", None, None],
        [None, "Landscaping", annual_cells.get("landscaping", "400.00")],
        [None, "Insurance", annual_cells.get("insurance", "800.00")],
        (["Total Operating Expense", None, "1200.00"] if totals else [None, None, None]),
    ]
    col_indices = {"ytd_actual": 2, "annual_budget": 2, "variance": 2,
                   "projection": 2, "percent_change": 2,
                   "_real_matched_keys": ["annual_budget"]}
    return rows, col_indices


class TestRowParsingNullPropagation:
    def test_dash_annual_becomes_none_not_zero(self):
        rows, cols = _rows_with_annual_at_2({"landscaping": "-"})
        items = parse_rows_with_sections(rows, cols)
        by_label = {i["label"]: i for i in items}
        assert by_label["Landscaping"]["annual_budget"] is None

    def test_unparseable_annual_records_review_question(self):
        rows, cols = _rows_with_annual_at_2({"insurance": "1,2X4.00"})
        capture: dict = {}
        items = parse_rows_with_sections(rows, cols, capture=capture)
        by_label = {i["label"]: i for i in items}
        assert by_label["Insurance"]["annual_budget"] is None
        questions = capture.get("review_questions", [])
        assert len(questions) == 1
        assert questions[0]["label"] == "Insurance"
        assert questions[0]["raw_text"] == "1,2X4.00"
        assert questions[0]["column"] == "annual_budget"

    def test_negative_annual_keeps_sign(self):
        rows, cols = _rows_with_annual_at_2({"landscaping": "($400.00)"})
        items = parse_rows_with_sections(rows, cols)
        by_label = {i["label"]: i for i in items}
        assert by_label["Landscaping"]["annual_budget"] == -400.0


class TestTotalsCapture:
    def test_total_rows_captured_and_excluded_from_items(self):
        rows, cols = _rows_with_annual_at_2({})
        capture: dict = {}
        items = parse_rows_with_sections(rows, cols, capture=capture)
        labels = [i["label"] for i in items]
        assert "Total Operating Expense" not in labels
        stated = capture["stated_totals"]
        assert {t["label"] for t in stated} == {
            "Total Operating Income",
            "Total Operating Expense",
        }
        expense_total = next(t for t in stated if t["section"] == "operating")
        assert expense_total["annual_budget"] == 1200.0

    def test_no_capture_dict_keeps_legacy_shape(self):
        rows, cols = _rows_with_annual_at_2({})
        items = parse_rows_with_sections(rows, cols)
        assert all(not i["label"].lower().startswith("total ") for i in items)


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

    def test_mismatch_blocks_with_document_source(self):
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
        assert mismatches[0]["severity"] == "error"
        assert mismatches[0]["details"]["totals_source"] == "document"
        assert mismatches[0]["details"]["expected_total"] == 1200.0
        assert mismatches[0]["details"]["actual_total"] == 600.0
        assert has_blocking_validation_issues(issues)

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
