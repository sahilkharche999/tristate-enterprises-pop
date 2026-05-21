"""
Unit tests for backend/app/services/income_statement_parser.py

Tests cover:
- Section state machine classification
- Column auto-detection (3-tier: alias, LLM, hardcoded fallback)
- Financial float parsing
- .xls file reading (xlrd)
- PDF text extraction (pdfplumber)
- Row validation
- Integration: parse_income_statement end-to-end
"""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock, AsyncMock
from typing import Any, Optional

# Ensure backend root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services import income_statement_parser
from app.services.income_statement_parser import (
    _cell_text,
    _parse_financial_float,
    _match_section_header,
    _safe_get,
    detect_columns,
    parse_rows_with_sections,
    _FALLBACK_COLUMNS,
)


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------

def _make_row(num_cols: int = 45, **kwargs: Any) -> list:
    """Create a row of None values with specified columns set."""
    row = [None] * num_cols
    for col_idx, val in kwargs.items():
        row[col_idx] = val
    return row


def _section_header_row(text: str, num_cols: int = 45) -> list:
    """Row with section header in col 0 (col A), col 1 empty."""
    row = _make_row(num_cols)
    row[0] = text
    return row


def _line_item_row(label: str, ytd: float = 0.0, annual: float = 0.0,
                   variance: float = 0.0, num_cols: int = 45) -> list:
    """Row with line item in col 1 (col B), col 0 empty."""
    row = _make_row(num_cols)
    row[1] = label          # col B = label
    row[19] = ytd           # default ytd_actual col (FALLBACK_COLUMNS["ytd_actual"])
    row[32] = annual        # default annual_budget col (FALLBACK_COLUMNS["annual_budget"])
    row[26] = variance      # default variance col (FALLBACK_COLUMNS["variance"])
    return row


def _build_esprit_park_header_rows() -> list:
    """
    Simulate Esprit Park Aug 2025 multi-row header layout:
      row 3 (index 3): col 7="Current Period", col 23="Year To Date", col 35="Annual Budget"
      row 4 (index 4): col 4="Actual", col 11="Budget", col 16="Variance",
                       col 21="Actual", col 26="Budget", col 29="Variance"

    The column detection should resolve:
      ytd_actual  = col 20 (index 20, within the "Year To Date" group, at "Actual")
      annual_budget = col 32 (index 32 — inside "Annual Budget" group)
      variance    = col 26 (index 26 — inside "Year To Date" group, at "Variance" or nearby)
    """
    rows = [_make_row(45) for _ in range(10)]
    # Row 3 — group headers
    rows[3][7] = "Current Period"
    rows[3][23] = "Year To Date"
    rows[3][35] = "Annual Budget"
    # Row 4 — detail headers
    rows[4][4] = "Actual"
    rows[4][11] = "Budget"
    rows[4][16] = "Variance"
    rows[4][21] = "Actual"    # within "Year To Date" group -> ytd_actual
    rows[4][26] = "Budget"    # within "Year To Date" group -> ytd_budget (skip)
    rows[4][29] = "Variance"  # within "Year To Date" group -> ytd_variance
    rows[4][37] = "Actual"    # within "Annual Budget" group
    return rows


# ---------------------------------------------------------------------------
# Section State Machine
# ---------------------------------------------------------------------------

class TestSectionStateMachine(unittest.TestCase):

    def _rows_for(self, *row_specs):
        """Build rows list from specs: (section_header, line_items...)"""
        rows = []
        for spec in row_specs:
            if isinstance(spec, str):
                rows.append(_section_header_row(spec))
            else:
                rows.append(spec)
        return rows

    def test_allocation_to_reserves_is_operating(self):
        """90000 - Reserve - Allocation/Transfer under Operating Expense stays category=operating."""
        rows = [
            _section_header_row("Operating Expense"),
            _section_header_row("Allocation to Reserves"),    # sub-section, still operating
            _line_item_row("90000 - Reserve - Allocation/Transfer", ytd=1000.0, annual=1200.0),
        ]
        items = parse_rows_with_sections(rows, _FALLBACK_COLUMNS)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["category"], "operating")
        self.assertFalse(items[0]["read_only"])

    def test_reserve_expense_items_read_only(self):
        """Items inside Reserve Expenses (Per Reserve Study) section are reserve + read_only."""
        rows = [
            _section_header_row("Reserve Expense"),
            _section_header_row("Reserve Expenses (Per Reserve Study)"),
            _line_item_row("91228 - Exposed Brick", ytd=500.0, annual=600.0),
        ]
        items = parse_rows_with_sections(rows, _FALLBACK_COLUMNS)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["category"], "reserve_expense")
        self.assertTrue(items[0]["read_only"])

    def test_operating_income_items(self):
        """Items under Operating Income > Income produce category=income."""
        rows = [
            _section_header_row("Operating Income"),
            _section_header_row("Income"),
            _line_item_row("40000 - Assessment Income", ytd=10000.0, annual=12000.0),
        ]
        items = parse_rows_with_sections(rows, _FALLBACK_COLUMNS)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["category"], "income")

    def test_operating_expense_items(self):
        """Items under Operating Expense > Administration Expenses are category=operating, read_only=False."""
        rows = [
            _section_header_row("Operating Expense"),
            _section_header_row("Administration Expenses"),
            _line_item_row("50050 - Management Fee", ytd=2000.0, annual=2400.0),
        ]
        items = parse_rows_with_sections(rows, _FALLBACK_COLUMNS)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["category"], "operating")
        self.assertFalse(items[0]["read_only"])

    def test_reserve_income_items_are_read_only(self):
        """Items under Reserve Income are category=reserve_income AND read_only.

        Reserve income rows are flagged read_only because they come from the
        reserve study extraction, not operator-editable budget input. The
        ``READ_ONLY_SECTIONS`` set in ``income_statement_parser`` is the
        source of truth.
        """
        rows = [
            _section_header_row("Reserve Income"),
            _line_item_row("45000 - Reserve Interest", ytd=300.0, annual=300.0),
        ]
        items = parse_rows_with_sections(rows, _FALLBACK_COLUMNS)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["category"], "reserve_income")
        self.assertTrue(items[0]["read_only"])

    def test_total_rows_skipped(self):
        """Rows starting with 'Total ' are excluded from line items."""
        rows = [
            _section_header_row("Operating Expense"),
            _line_item_row("50050 - Management Fee", ytd=2000.0, annual=2400.0),
            _line_item_row("Total Operating Expense", ytd=5000.0, annual=6000.0),
        ]
        items = parse_rows_with_sections(rows, _FALLBACK_COLUMNS)
        # Only the non-Total row should be returned
        self.assertEqual(len(items), 1)
        self.assertNotIn("Total", items[0]["label"])

    def test_section_default_is_operating(self):
        """When no section header encountered, items default to category=operating."""
        rows = [
            _line_item_row("50050 - Management Fee", ytd=500.0, annual=600.0),
        ]
        items = parse_rows_with_sections(rows, _FALLBACK_COLUMNS)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["category"], "operating")


class TestMatchSectionHeader(unittest.TestCase):

    def test_operating_income_variants(self):
        self.assertEqual(_match_section_header("Operating Income"), "income")
        self.assertEqual(_match_section_header("Operating Incomes"), "income")
        self.assertEqual(_match_section_header("OPERATING INCOME"), "income")

    def test_operating_expense_variants(self):
        self.assertEqual(_match_section_header("Operating Expense"), "operating")
        self.assertEqual(_match_section_header("Operating Expenses"), "operating")

    def test_reserve_income(self):
        self.assertEqual(_match_section_header("Reserve Income"), "reserve_income")

    def test_reserve_expense_variants(self):
        self.assertEqual(_match_section_header("Reserve Expense"), "reserve_expense")
        self.assertEqual(_match_section_header("Reserve Expenses"), "reserve_expense")
        self.assertEqual(_match_section_header("Reserve Expenses (Per Reserve Study)"), "reserve_expense")

    def test_non_section_headers_return_none(self):
        self.assertIsNone(_match_section_header("Administration Expenses"))
        self.assertIsNone(_match_section_header("Utilities"))
        self.assertIsNone(_match_section_header("Allocation to Reserves"))

    def test_none_and_empty_return_none(self):
        self.assertIsNone(_match_section_header(""))
        self.assertIsNone(_match_section_header("   "))


# ---------------------------------------------------------------------------
# Column Auto-Detection
# ---------------------------------------------------------------------------

class TestColumnDetection(unittest.TestCase):

    @staticmethod
    def _row_with_data_at(label: str, col_ytd: int = 15, col_ab: int = 28, col_var: int = 22, num_cols: int = 40) -> list:
        row = [None] * num_cols
        row[1] = label
        row[col_ytd] = 100.0
        row[col_ab] = 200.0
        row[col_var] = -50.0
        return row

    @classmethod
    def _unrecognizable_rows_with_data(cls, col_ytd: int = 15, col_ab: int = 28, col_var: int = 22) -> list:
        return [
            [None] * 40,
            ["Foo", "Bar", "Baz"] + [None] * 37,
            ["Alpha", "Beta", "Gamma"] + [None] * 37,
        ] + [
            cls._row_with_data_at(f"5000{i} - test", col_ytd=col_ytd, col_ab=col_ab, col_var=col_var)
            for i in range(7)
        ]

    def test_safe_get_negative_index_returns_none(self):
        self.assertIsNone(_safe_get(["a", "b", "c"], -1))

    def test_cell_text_negative_index_returns_empty_string(self):
        self.assertEqual(_cell_text(["a", "b", "c"], -1), "")

    def test_column_detection_esprit_park(self):
        """Esprit Park-style multi-row header: YTD group + Annual Budget group."""
        header_rows = _build_esprit_park_header_rows()
        # Add some data rows so detect_columns has sample rows
        data_rows = [
            _line_item_row("50050 - Management Fee", ytd=1000.0, annual=1200.0)
            for _ in range(5)
        ]
        all_rows = header_rows + data_rows

        result = detect_columns(all_rows)

        # Should detect ytd_actual = 21 (Actual within YTD group span)
        # annual_budget somewhere in the Annual Budget group
        # At minimum: result should be a dict with the required keys
        self.assertIn("ytd_actual", result)
        self.assertIn("annual_budget", result)
        self.assertIn("variance", result)
        # Values should be reasonable (0-based integers)
        self.assertIsInstance(result["ytd_actual"], int)
        self.assertIsInstance(result["annual_budget"], int)

    def test_column_detection_alias_fallback_triggers_llm(self):
        """When alias matching finds fewer than 2 columns, LLM fallback is called."""
        unrecognizable_rows = self._unrecognizable_rows_with_data()

        llm_return = {"ytd_actual": 15, "annual_budget": 28, "variance": 22}

        with patch.object(income_statement_parser, "_llm_column_fallback", return_value=llm_return) as mock_llm:
            result = detect_columns(unrecognizable_rows)

        mock_llm.assert_called_once()
        self.assertEqual(result["ytd_actual"], 15)
        self.assertEqual(result["annual_budget"], 28)
        self.assertEqual(result["variance"], 22)

    def test_column_detection_llm_failure_falls_back_to_hardcoded(self):
        """When alias matching fails AND LLM returns None, use hardcoded fallback."""
        unrecognizable_rows = [
            [None] * 40,
            ["Foo", "Bar", "Baz"] + [None] * 37,
        ]

        with patch.object(income_statement_parser, "_llm_column_fallback", return_value=None):
            result = detect_columns(unrecognizable_rows)

        self.assertEqual(result["ytd_actual"], _FALLBACK_COLUMNS["ytd_actual"])
        self.assertEqual(result["annual_budget"], _FALLBACK_COLUMNS["annual_budget"])
        self.assertEqual(result["_detection_tier"], 3)

    def test_column_detection_partial_match_triggers_llm(self):
        """When only 1 column recognized (fewer than 2), triggers LLM fallback tier."""
        # Only "Annual Budget" header present — not enough for alias match (need >= 2)
        partial_rows = [
            [None] * 40,
            [None] * 10 + ["Annual Budget"] + [None] * 29,
        ] + [_line_item_row("50000 - test") for _ in range(5)]

        llm_return = {"ytd_actual": 10, "annual_budget": 10, "variance": 11}

        with patch.object(income_statement_parser, "_llm_column_fallback", return_value=llm_return) as mock_llm:
            result = detect_columns(partial_rows)

        mock_llm.assert_called_once()
        self.assertIn("ytd_actual", result)

    def test_column_detection_rejects_negative_llm_indices(self):
        rows = self._unrecognizable_rows_with_data()
        llm_return = {"ytd_actual": -1, "annual_budget": 28, "variance": 22}

        with patch.object(income_statement_parser, "_llm_column_fallback", return_value=llm_return):
            result = detect_columns(rows)

        self.assertEqual(result["ytd_actual"], _FALLBACK_COLUMNS["ytd_actual"])
        self.assertEqual(result["annual_budget"], _FALLBACK_COLUMNS["annual_budget"])

    def test_column_detection_rejects_out_of_range_llm_indices(self):
        rows = self._unrecognizable_rows_with_data()
        llm_return = {"ytd_actual": 15, "annual_budget": 41, "variance": 22}

        with patch.object(income_statement_parser, "_llm_column_fallback", return_value=llm_return):
            result = detect_columns(rows)

        self.assertEqual(result["ytd_actual"], _FALLBACK_COLUMNS["ytd_actual"])
        self.assertEqual(result["annual_budget"], _FALLBACK_COLUMNS["annual_budget"])

    def test_column_detection_rejects_duplicate_llm_indices(self):
        rows = self._unrecognizable_rows_with_data()
        llm_return = {"ytd_actual": 15, "annual_budget": 15, "variance": 22}

        with patch.object(income_statement_parser, "_llm_column_fallback", return_value=llm_return):
            result = detect_columns(rows)

        self.assertEqual(result["ytd_actual"], _FALLBACK_COLUMNS["ytd_actual"])
        self.assertEqual(result["annual_budget"], _FALLBACK_COLUMNS["annual_budget"])

    def test_column_detection_rejects_low_confidence_llm_mapping(self):
        rows = self._unrecognizable_rows_with_data()
        llm_return = {"ytd_actual": 3, "annual_budget": 4, "variance": 5}

        with patch.object(income_statement_parser, "_llm_column_fallback", return_value=llm_return):
            result = detect_columns(rows)

        self.assertEqual(result["ytd_actual"], _FALLBACK_COLUMNS["ytd_actual"])
        self.assertEqual(result["annual_budget"], _FALLBACK_COLUMNS["annual_budget"])

    def test_column_detection_repairs_llm_header_positions_leftward(self):
        rows = self._unrecognizable_rows_with_data(col_ytd=19, col_ab=32, col_var=26)
        llm_return = {"ytd_actual": 20, "annual_budget": 34, "variance": 28}

        with patch.object(income_statement_parser, "_llm_column_fallback", return_value=llm_return):
            result = detect_columns(rows)

        self.assertEqual(result["ytd_actual"], 19)
        self.assertEqual(result["annual_budget"], 32)
        self.assertEqual(result["variance"], 26)


# ---------------------------------------------------------------------------
# Financial Float Parsing
# ---------------------------------------------------------------------------

def test_parse_financial_float():
    """Standalone pytest function testing all _parse_financial_float cases."""
    assert abs(_parse_financial_float("(133.86)") - (-133.86)) < 1e-6
    assert abs(_parse_financial_float("$1,500.00") - 1500.0) < 1e-6
    assert abs(_parse_financial_float("1,043,706.24") - 1043706.24) < 1e-6
    assert _parse_financial_float("-") == 0.0
    assert _parse_financial_float("") == 0.0
    assert _parse_financial_float(None) == 0.0
    assert abs(_parse_financial_float(42.5) - 42.5) < 1e-6
    assert abs(_parse_financial_float("(1,500)") - (-1500.0)) < 1e-6


class TestParseFinancialFloat(unittest.TestCase):

    def test_parentheses_negative(self):
        self.assertAlmostEqual(_parse_financial_float("(133.86)"), -133.86)

    def test_parentheses_with_comma_negative(self):
        self.assertAlmostEqual(_parse_financial_float("(1,500)"), -1500.0)

    def test_dollar_sign(self):
        self.assertAlmostEqual(_parse_financial_float("$1,500.00"), 1500.0)

    def test_large_number_with_commas(self):
        self.assertAlmostEqual(_parse_financial_float("1,043,706.24"), 1043706.24)

    def test_dash_returns_zero(self):
        self.assertEqual(_parse_financial_float("-"), 0.0)

    def test_empty_string_returns_zero(self):
        self.assertEqual(_parse_financial_float(""), 0.0)

    def test_none_returns_zero(self):
        self.assertEqual(_parse_financial_float(None), 0.0)

    def test_float_passthrough(self):
        self.assertAlmostEqual(_parse_financial_float(42.5), 42.5)

    def test_int_passthrough(self):
        self.assertAlmostEqual(_parse_financial_float(100), 100.0)

    def test_em_dash_returns_zero(self):
        self.assertEqual(_parse_financial_float("\u2014"), 0.0)


# ---------------------------------------------------------------------------
# .xls File Reading (xlrd)
# ---------------------------------------------------------------------------

class TestXlsRowNormalization(unittest.TestCase):

    def test_xls_row_normalization(self):
        """Reading a .xls file produces rows with None for blank cells (not empty string '')."""
        # Mock xlrd.open_workbook and sheet
        mock_cell = MagicMock()
        mock_cell.value = ""  # xlrd returns "" for blank cells

        mock_sheet = MagicMock()
        mock_sheet.nrows = 2
        mock_sheet.ncols = 3
        mock_sheet.cell_value.side_effect = lambda r, c: "" if (r == 0 and c == 1) else f"val_{r}_{c}"

        mock_wb = MagicMock()
        mock_wb.sheet_by_name.return_value = mock_sheet

        with patch("xlrd.open_workbook", return_value=mock_wb):
            from app.services.income_statement_parser import _read_xls_rows
            rows = _read_xls_rows("/fake/path.xls", "Income Statement")

        # Blank cell (r=0, c=1) should become None
        self.assertIsNone(rows[0][1])
        # Non-blank cells should be preserved
        self.assertEqual(rows[0][0], "val_0_0")

    def test_xls_format_detected(self):
        """Calling parse_income_statement with .xls path dispatches to xlrd reader."""
        mock_rows = [
            [None] * 40,
            _section_header_row("Operating Income"),
            _line_item_row("40000 - Assessment", ytd=1000.0, annual=1200.0),
        ]

        with patch.object(income_statement_parser, "_read_xls_rows", return_value=mock_rows) as mock_xls:
            result = income_statement_parser.parse_income_statement("/fake/path.xls")

        mock_xls.assert_called_once_with("/fake/path.xls", "Income Statement")
        self.assertIsInstance(result, list)


# ---------------------------------------------------------------------------
# PDF Text Extraction (pdfplumber)
# ---------------------------------------------------------------------------

class TestPdfExtraction(unittest.TestCase):

    def test_pdf_extraction_basic(self):
        """Given mock pdfplumber output, produces rows with section headers and line items."""
        # Simulate pdfplumber words for a simple income statement page
        # x0 < 20 = section header, x0 >= 20 = line item
        words = []
        # Section header: "Operating Income" at x0=10
        for i, word in enumerate(["Operating", "Income"]):
            words.append({
                "text": word,
                "x0": 10.0 + i * 60,
                "top": 50.0,
                "x1": 10.0 + i * 60 + 55,
            })
        # Line item: "40000 - Assessment Income" at x0=25
        for i, word in enumerate(["40000", "-", "Assessment"]):
            words.append({
                "text": word,
                "x0": 25.0 + i * 50,
                "top": 70.0,
                "x1": 25.0 + i * 50 + 45,
            })
        # Add enough words to pass the >20 words check
        for j in range(25):
            words.append({
                "text": f"word{j}",
                "x0": 25.0,
                "top": 100.0 + j * 10,
                "x1": 75.0,
            })

        mock_page = MagicMock()
        mock_page.extract_words.return_value = words

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)

        with patch("pdfplumber.open", return_value=mock_pdf):
            from app.services.income_statement_parser import _read_pdf_rows
            rows = _read_pdf_rows("/fake/path.pdf")

        self.assertIsInstance(rows, list)
        self.assertGreater(len(rows), 0)

    def test_scanned_pdf_raises_error(self):
        """When pdfplumber extracts fewer than 20 words from page 1, raises ValueError."""
        # Only 5 words — clearly a scanned PDF
        sparse_words = [{"text": f"w{i}", "x0": 10.0, "top": 10.0, "x1": 40.0} for i in range(5)]

        mock_page = MagicMock()
        mock_page.extract_words.return_value = sparse_words

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)

        with patch("pdfplumber.open", return_value=mock_pdf):
            from app.services.income_statement_parser import _read_pdf_rows
            with self.assertRaises(ValueError) as ctx:
                _read_pdf_rows("/fake/scan.pdf")

        self.assertIn("no text layer", str(ctx.exception).lower())


# ---------------------------------------------------------------------------
# Row Validation
# ---------------------------------------------------------------------------

# TestRowValidation removed — validation_warning was comparing YTD values
# to Annual Budget (cross-period), producing false positives on every row.
# Deferred until YTD Budget column is also extracted for same-period validation.


# ---------------------------------------------------------------------------
# Integration: parse_income_statement end-to-end
# ---------------------------------------------------------------------------

class TestParseIncomeStatementIntegration(unittest.TestCase):

    def test_parse_income_statement_xlsx(self):
        """Given a mocked .xlsx file path, returns list of dicts with required keys."""
        expected_keys = {
            "line_item_key", "account_code", "category", "label",
            "ytd_actual", "annual_budget", "projection", "percent_change",
            "read_only", "section", "raw",
        }

        mock_rows = [
            _section_header_row("Operating Income"),
            _line_item_row("40000 - Assessment Income", ytd=10000.0, annual=12000.0),
            _section_header_row("Operating Expense"),
            _line_item_row("50050 - Management Fee", ytd=2000.0, annual=2400.0),
            _section_header_row("Reserve Expense"),
            _section_header_row("Reserve Expenses (Per Reserve Study)"),
            _line_item_row("91228 - Exposed Brick", ytd=500.0, annual=600.0),
        ]

        with patch.object(income_statement_parser, "_read_xlsx_rows", return_value=mock_rows):
            result = income_statement_parser.parse_income_statement("/fake/path.xlsx")

        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

        # Check all required keys are present
        for item in result:
            for key in expected_keys:
                self.assertIn(key, item, f"Missing key: {key}")

        # Check specific items
        income_items = [i for i in result if i["category"] == "income"]
        operating_items = [i for i in result if i["category"] == "operating"]
        reserve_items = [i for i in result if i["category"] in ("reserve", "reserve_income", "reserve_expense")]

        self.assertGreater(len(income_items), 0)
        self.assertGreater(len(operating_items), 0)
        self.assertGreater(len(reserve_items), 0)

        # Reserve study items must be read_only
        reserve_read_only = [i for i in reserve_items if i["read_only"]]
        self.assertGreater(len(reserve_read_only), 0)

    def test_parse_income_statement_unsupported_format(self):
        """Unsupported file extension raises ValueError."""
        with self.assertRaises(ValueError) as ctx:
            income_statement_parser.parse_income_statement("/fake/path.csv")
        self.assertIn("Unsupported", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# Integration Tests: Realistic In-Memory Fixtures (Wave 3 / Plan 07-03)
# ---------------------------------------------------------------------------
#
# These tests simulate the full structure of real Esprit Park income statements
# using in-memory row data that matches the layouts confirmed in RESEARCH.md.
# No actual file I/O to Tri State Documents — all fixtures are hand-crafted
# in-memory representations.
# ---------------------------------------------------------------------------

import pytest


@pytest.fixture
def esprit_park_full_rows():
    """Simulates Esprit Park Aug 2025 income statement structure.

    Verified section structure from RESEARCH.md Pattern 1.

    Column layout (0-based, matching what detect_columns returns for this layout):
      col 0  (col A): section/sub-section header text
      col 1  (col B): line item label
      col 19: YTD Actual  (detected from "Actual" within "Year To Date" group span)
      col 16: Variance    (detected from "Variance" in detail row)
      col 35: Annual Budget (detected from "Annual Budget" group header text at col 35)

    Header rows include multi-row group headers so detect_columns can detect
    columns using the alias/group-span approach.
    """

    def make_row(col_a=None, col_b=None, ytd=None, annual=None, variance=None):
        row = [None] * 40
        row[0] = col_a
        row[1] = col_b
        row[19] = ytd       # 0-based col 19 = YTD Actual (detected from "Actual" within YTD span)
        row[35] = annual    # 0-based col 35 = Annual Budget (detected from "Annual Budget" group header)
        row[16] = variance  # 0-based col 16 = Variance (detected from "Variance" in detail row)
        return row

    # Header rows (rows 0-4)
    header_rows = [
        [None] * 40,  # row 0: blank
        [None] * 40,  # row 1: HOA name
        [None] * 40,  # row 2: date range
    ]
    # Row 3: group headers
    group_row = [None] * 40
    group_row[7] = "Current Period"
    group_row[23] = "Year To Date"
    group_row[35] = "Annual Budget"
    header_rows.append(group_row)
    # Row 4: detail headers
    detail_row = [None] * 40
    detail_row[4] = "Actual"
    detail_row[11] = "Budget"
    detail_row[16] = "Variance"
    detail_row[20] = "Actual"     # within "Year To Date" group
    detail_row[25] = "Budget"     # within "Year To Date" group
    detail_row[28] = "Variance"   # within "Year To Date" group
    header_rows.append(detail_row)

    data_rows = [
        # Operating Income section
        make_row(col_a="Operating Income"),
        make_row(col_a="Income"),
        make_row(col_b="40000 - Assessment Income", ytd=86975.52, annual=1043706.24, variance=0),
        make_row(col_b="40100 - Late Fees", ytd=1200.0, annual=1500.0, variance=-300),
        make_row(col_a="Total Income"),

        # Operating Expense section
        make_row(col_a="Operating Expense"),
        make_row(col_a="Administration Expenses"),
        make_row(col_b="50050 - Management Fee", ytd=25000.0, annual=30000.0, variance=5000),
        make_row(col_b="50100 - Audit", ytd=5000.0, annual=5000.0, variance=0),
        make_row(col_a="Total Administration Expenses"),
        make_row(col_a="Utilities"),
        make_row(col_b="60010 - Water & Sewer", ytd=12000.0, annual=15000.0, variance=3000),
        make_row(col_a="Total Utilities"),

        # Allocation to Reserves sub-section (INSIDE Operating Expense)
        make_row(col_a="Allocation to Reserves"),
        make_row(col_b="90000 - Reserve - Allocation/Transfer", ytd=50000.0, annual=60000.0, variance=10000),
        make_row(col_a="Total Allocation to Reserves"),
        make_row(col_a="Total Operating Expense"),

        # Reserve Income section
        make_row(col_a="Reserve Income"),
        make_row(col_b="45000 - Reserve Interest Income", ytd=500.0, annual=600.0, variance=100),
        make_row(col_a="Total Reserve Income"),

        # Reserve Expense section
        make_row(col_a="Reserve Expense"),
        make_row(col_a="Reserve Expenses (Per Reserve Study)"),
        make_row(col_b="91228 - Exposed Brick Repointing", ytd=0.0, annual=15000.0, variance=-15000),
        make_row(col_b="91300 - Roof Replacement Fund", ytd=0.0, annual=25000.0, variance=-25000),
        make_row(col_a="Total Reserve Expenses"),
    ]

    return header_rows + data_rows


def test_full_pipeline_esprit_park_structure(esprit_park_full_rows):
    """Full pipeline test against Esprit Park fixture structure.

    Bob's critical requirement: 90000 - Reserve - Allocation/Transfer must be
    category=operating and read_only=False because it appears under the
    'Allocation to Reserves' sub-section, which is inside Operating Expense.
    """
    from app.services.income_statement_parser import parse_rows_with_sections, detect_columns

    col_indices = detect_columns(esprit_park_full_rows)
    items = parse_rows_with_sections(esprit_park_full_rows, col_indices)

    # Build lookup by label for easy assertions
    items_by_label = {i["label"]: i for i in items}

    # Bob's critical requirement: 90000 under Allocation to Reserves = operating
    assert "90000 - Reserve - Allocation/Transfer" in items_by_label, \
        "90000 - Reserve - Allocation/Transfer not found in parsed items"
    alloc = items_by_label["90000 - Reserve - Allocation/Transfer"]
    assert alloc["category"] == "operating", \
        "Allocation/Transfer must be operating, not reserve"
    assert alloc["read_only"] is False, \
        "Allocation/Transfer must be editable (read_only=False)"

    # Operating income items
    assert "40000 - Assessment Income" in items_by_label
    assessment = items_by_label["40000 - Assessment Income"]
    assert assessment["category"] == "income"
    assert assessment["read_only"] is False

    # Regular operating expense items
    assert "50050 - Management Fee" in items_by_label
    mgmt = items_by_label["50050 - Management Fee"]
    assert mgmt["category"] == "operating"
    assert mgmt["read_only"] is False

    # Reserve income items — read_only (sourced from reserve study, not
    # editable as budget input). Matches READ_ONLY_SECTIONS in the parser.
    assert "45000 - Reserve Interest Income" in items_by_label
    reserve_interest = items_by_label["45000 - Reserve Interest Income"]
    assert reserve_interest["category"] in ("reserve", "reserve_income", "reserve_expense")
    assert reserve_interest["read_only"] is True

    # Reserve expense (per reserve study) items — read_only
    assert "91228 - Exposed Brick Repointing" in items_by_label
    brick = items_by_label["91228 - Exposed Brick Repointing"]
    assert brick["category"] in ("reserve", "reserve_income", "reserve_expense")
    assert brick["read_only"] is True

    assert "91300 - Roof Replacement Fund" in items_by_label
    roof = items_by_label["91300 - Roof Replacement Fund"]
    assert roof["category"] in ("reserve", "reserve_income", "reserve_expense")
    assert roof["read_only"] is True

    # Verify no Total rows leaked into output
    for item in items:
        assert not item["label"].startswith("Total "), \
            f"Total row leaked into output: {item['label']}"

    # Verify financial values parsed correctly (using fallback column positions)
    assert abs(assessment["ytd_actual"] - 86975.52) < 0.01, \
        f"YTD actual mismatch: {assessment['ytd_actual']}"
    assert abs(assessment["annual_budget"] - 1043706.24) < 0.01, \
        f"Annual budget mismatch: {assessment['annual_budget']}"


def test_full_pipeline_no_keyword_leakage(esprit_park_full_rows):
    """Verify section-based classification produces no keyword-based leakage.

    Every item under Operating Expense (including 90000 - Reserve - Allocation/Transfer)
    must be operating, regardless of having 'reserve' in the label.
    """
    from app.services.income_statement_parser import parse_rows_with_sections, detect_columns

    col_indices = detect_columns(esprit_park_full_rows)
    items = parse_rows_with_sections(esprit_park_full_rows, col_indices)

    for item in items:
        # 90000 under Operating Expense must not be misclassified as reserve
        if item["label"] == "90000 - Reserve - Allocation/Transfer":
            assert item["category"] == "operating", \
                "90000 - Reserve - Allocation/Transfer classified by keyword instead of section"

        # Every item with section=operating must have category=operating
        if item["section"] == "operating":
            assert item["category"] == "operating", \
                f"Item '{item['label']}' in operating section has wrong category: {item['category']}"


def test_column_detection_with_real_header_layout(esprit_park_full_rows):
    """Column detection on Esprit Park multi-row header layout resolves correct columns.

    The fixture uses:
      - Group row: col 23="Year To Date", col 35="Annual Budget"
      - Detail row: col 20="Actual" (within YTD span), col 28="Variance"
    Detected ytd_actual must be in a reasonable range (19-21).
    Detected annual_budget must be in a reasonable range (32-37).
    """
    from app.services.income_statement_parser import detect_columns

    col_indices = detect_columns(esprit_park_full_rows)

    # YTD Actual: fallback is 19, detected from header may be 20 or 21
    assert col_indices["ytd_actual"] in (19, 20, 21), \
        f"ytd_actual at unexpected column: {col_indices['ytd_actual']}"
    # Annual Budget: fallback is 32; group header is at 35
    assert col_indices["annual_budget"] in (32, 33, 34, 35, 36, 37), \
        f"annual_budget at unexpected column: {col_indices['annual_budget']}"


def test_table_to_line_items_delegates_to_parser(esprit_park_full_rows):
    """Verify _table_to_line_items uses the new section-aware parser for raw layouts.

    When the table has no 'Label' header column, the function must delegate to
    parse_rows_with_sections via detect_columns.
    """
    from app.services.budget_history_service import _table_to_line_items

    # Raw layout: headers are all None (no "Label" column)
    # Pass the data rows (skipping header rows which are already included
    # in the first 10 rows that detect_columns scans from the combined list)
    table = {
        "headers": [None] * 40,  # raw layout — no "Label" header
        "rows": esprit_park_full_rows[5:],   # data rows only (skip 5 header rows)
    }
    items, warnings = _table_to_line_items(table)

    # Should produce items without errors
    assert isinstance(items, list)
    assert len(items) > 0, "_table_to_line_items returned empty list for raw layout"

    # Should use section-based classification
    labels = {i["label"] for i in items}
    if "90000 - Reserve - Allocation/Transfer" in labels:
        alloc = next(i for i in items if i["label"] == "90000 - Reserve - Allocation/Transfer")
        assert alloc["category"] == "operating", \
            "_table_to_line_items classified 90000 as reserve instead of operating"


def test_pipeline_reserve_section_from_section_field():
    """PARSE-11: Verify generate_budget_pipeline uses _match_section_header,
    not RESERVE_SECTION_START_TITLES.

    After Plan 02 refactoring:
    - IncomeStatementEnricher should NOT have RESERVE_SECTION_START_TITLES attribute
    - IncomeStatementEnricher should NOT have _is_reserve_section_start method
    - IncomeStatementEnricher should NOT have _is_reserve_label method
    - The module should import _match_section_header from income_statement_parser
    - process_line_items must call _match_section_header, not RESERVE_SECTION_START_TITLES
    """
    import inspect
    from app import generate_budget_pipeline
    from app.generate_budget_pipeline import IncomeStatementEnricher

    # Verify old constants/methods are GONE
    assert not hasattr(IncomeStatementEnricher, "RESERVE_SECTION_START_TITLES"), \
        "RESERVE_SECTION_START_TITLES should be removed from IncomeStatementEnricher"
    assert not hasattr(IncomeStatementEnricher, "_is_reserve_section_start"), \
        "_is_reserve_section_start should be removed from IncomeStatementEnricher"
    assert not hasattr(IncomeStatementEnricher, "_is_reserve_label"), \
        "_is_reserve_label should be removed from IncomeStatementEnricher"

    # Verify _match_section_header is imported in the module
    source = inspect.getsource(generate_budget_pipeline)
    assert "_match_section_header" in source, \
        "generate_budget_pipeline must import _match_section_header from income_statement_parser"

    # Verify process_line_items uses _match_section_header
    process_source = inspect.getsource(IncomeStatementEnricher.process_line_items)
    assert "_match_section_header" in process_source, \
        "process_line_items must call _match_section_header for section detection"
    assert "RESERVE_SECTION_START_TITLES" not in process_source, \
        "process_line_items must NOT reference RESERVE_SECTION_START_TITLES"
