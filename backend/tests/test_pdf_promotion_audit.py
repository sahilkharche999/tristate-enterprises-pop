"""Phase 1.4 task 19 — PDF promotion records the source column.

When ``ExtractedStatementLineItem`` rows (from ``pdf_vlm_extractor``)
are promoted into ``BudgetDraft.line_items``, the resulting line items
must carry ``source_column = 'annual_budget'`` so downstream
consumers can prove the engine's annual-by-invariant amount came from
the right field. Optional ``source_page_or_cell`` tracks where in the
source document the value lived.

The promotion happens inside ``budget_history_service._table_to_line_items``
when the table has a ``Label`` column (the post-extraction "enriched"
shape produced from PDF Gemini-Vision output).
"""
from __future__ import annotations

from app.services.budget_history_service import _table_to_line_items


class TestPDFPromotionAuditFields:
    def test_promoted_line_item_carries_annual_budget_source(self) -> None:
        table = {
            "headers": [
                "Section",
                "Label",
                "Account Code",
                "Current Actual",
                "YTD Actual",
                "Annual Budget",
            ],
            "rows": [
                ["Operating Income", "HOA Dues", "5000", 5000.0, 45000.0, 60000.0],
                ["Operating Expense", "Insurance", "6100", 1200.0, 10800.0, 14400.0],
            ],
        }
        line_items, warnings = _table_to_line_items(table)

        assert len(line_items) == 2
        for item in line_items:
            assert item["source_column"] == "annual_budget"

        dues = next(i for i in line_items if "Dues" in str(i["label"]))
        assert dues["annual_budget"] == 60000.0
        insurance = next(i for i in line_items if "Insurance" in str(i["label"]))
        assert insurance["annual_budget"] == 14400.0

    def test_source_page_or_cell_populated_when_provided(self) -> None:
        table = {
            "headers": ["Label", "Annual Budget", "Source Page"],
            "rows": [
                ["HOA Dues", 60000.0, "page 7 row 3"],
            ],
        }
        line_items, _ = _table_to_line_items(table)
        assert line_items[0]["source_page_or_cell"] == "page 7 row 3"

    def test_source_page_or_cell_none_when_not_provided(self) -> None:
        table = {
            "headers": ["Label", "Annual Budget"],
            "rows": [
                ["HOA Dues", 60000.0],
            ],
        }
        line_items, _ = _table_to_line_items(table)
        assert line_items[0]["source_page_or_cell"] is None

    def test_section_header_marker_rows_skipped(self) -> None:
        # Normalized workbook inserts marker rows where col A has the
        # section header text and other cols are None. These must NOT
        # produce ghost line items with source_column metadata.
        table = {
            "headers": ["Section", "Label", "Annual Budget"],
            "rows": [
                ["Operating Income", None, None],   # marker, skip
                ["Operating Income", "HOA Dues", 60000.0],
                ["Operating Expense", None, None],  # marker, skip
                ["Operating Expense", "Insurance", 14400.0],
            ],
        }
        line_items, _ = _table_to_line_items(table)
        labels = [i["label"] for i in line_items]
        assert "HOA Dues" in labels
        assert "Insurance" in labels
        assert all(item["source_column"] == "annual_budget" for item in line_items)
