"""
Unit tests for backend/app/services/income_statement_parser.py

The module no longer detects columns (every budget source is read by Gemini);
what remains is the section-header matcher used by the normalized workbook and
the .xls reader used to show uploaded files.
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Ensure backend root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.income_statement_parser import _match_section_header


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


class TestXlsRowNormalization(unittest.TestCase):

    def test_xls_row_normalization(self):
        """Reading a .xls file produces rows with None for blank cells (not empty string '')."""
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


class TestEnsureXlsxRejectsPdf(unittest.TestCase):
    def test_ensure_xlsx_raises_on_bare_pdf(self):
        from app.services.budget_history_service import _ensure_xlsx

        with self.assertRaises(ValueError) as ctx:
            _ensure_xlsx("/tmp/statement.pdf")
        self.assertIn("VLM", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
