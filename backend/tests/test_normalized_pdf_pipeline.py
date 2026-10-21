from pathlib import Path

from openpyxl import load_workbook

from app.generate_budget_pipeline import BudgetPipeline
from app.models.financial_document_extraction import ExtractedFinancialStatement
from app.services.budget_history_service import _table_to_line_items
from app.services import macros_service
from app.services.income_statement_parser import READ_ONLY_SECTIONS, SECTION_KINDS
from app.services.normalized_statement_workbook import build_normalized_statement_workbook


def test_normalized_workbook_preserves_four_category_taxonomy_end_to_end(tmp_path):
    """Full round-trip: canonical statement → normalized workbook → enricher
    → read_sheet_as_table → _table_to_line_items must preserve each item's
    canonical section_kind, even when Gemini's free-text section_label would
    not match the parser's narrow prefix list.
    """
    statement = ExtractedFinancialStatement.model_validate(
        {
            "document_family": "pdf_visual_document",
            "report_type": "income_statement",
            "line_items": [
                # Free-text section_label that does NOT match parser prefixes
                # (regression guard — this is exactly what Gemini returns for
                # real-world HOA PDFs like Crestview).
                {
                    "account_code_text": "41-4110",
                    "label": "Assessments: Regular",
                    "section_label": "ASSESSMENT INCOME",
                    "section_kind": "income",
                    "ytd_actual": 521843.0,
                    "annual_budget": 695790.0,
                    "page_number": 1,
                    "income_role": "regular_assessment",
                },
                {
                    "account_code_text": "65-6510",
                    "label": "Gas & Electric",
                    "section_label": "EXPENSES: UTILITIES",
                    "section_kind": "operating",
                    "ytd_actual": 33372.0,
                    "annual_budget": 52500.0,
                    "page_number": 1,
                },
                {
                    "account_code_text": "70-7010",
                    "label": "Reserve Transfer",
                    "section_label": "Reserve Funding",
                    "section_kind": "reserve_income",
                    "ytd_actual": 123089.0,
                    "annual_budget": 164119.0,
                    "page_number": 1,
                },
                {
                    "account_code_text": "80-8010",
                    "label": "5 yr Fire Alarm System - Inspection",
                    "section_label": "Reserve Expenses",
                    "section_kind": "reserve_expense",
                    "ytd_actual": 1125.0,
                    "annual_budget": 0.0,
                    "page_number": 1,
                },
            ],
            "totals": [],
            "validation_issues": [],
            "confidence": 0.95,
        }
    )

    normalized_path = build_normalized_statement_workbook(statement, str(tmp_path / "normalized.xlsx"))
    intermediate_path = str(tmp_path / "Income_Statement_Enriched.xlsx")
    output_path = str(tmp_path / "Budget_Pipeline.xlsx")

    pipeline = BudgetPipeline(
        input_path=normalized_path,
        intermediate_path=intermediate_path,
        output_path=output_path,
        growth_factor=12.0 / 9.0,
        growth_factor_note="auto annualization 12/9 from statement_period",
        enrich_only=False,
        known_columns={"ytd_actual": 6, "annual_budget": 9},
    )
    pipeline.run()

    enriched = macros_service.read_sheet_as_table(intermediate_path, "Income Statement")
    preview = macros_service.read_first_sheet_preview(output_path, 200)
    line_items, warnings = _table_to_line_items(enriched)

    # Exactly 4 line items — no ghost rows from the section header markers
    assert len(line_items) == 4, f"Expected 4 items, got {len(line_items)}: {[li['label'] for li in line_items]}"
    assert not any(li["label"].startswith("Line Item ") for li in line_items), (
        "Ghost section-header rows leaked into line_items"
    )

    # Each canonical category must survive the round-trip
    by_label = {li["label"]: li for li in line_items}
    assert by_label["Assessments: Regular"]["category"] == "income"
    assert by_label["Gas & Electric"]["category"] == "operating"
    assert by_label["Reserve Transfer"]["category"] == "reserve_income"
    assert by_label["5 yr Fire Alarm System - Inspection"]["category"] == "reserve_expense"
    # Gemini's income role rides through the workbook (column K) to the draft line.
    assert by_label["Assessments: Regular"]["income_role"] == "regular_assessment"
    assert by_label["Gas & Electric"]["income_role"] is None

    # All categories must be in the canonical taxonomy — no drift
    for li in line_items:
        assert li["category"] in SECTION_KINDS, f"{li['label']} has non-canonical category {li['category']!r}"

    # Reserve items must be read-only; operating/income must not
    assert by_label["Reserve Transfer"]["read_only"] is True
    assert by_label["5 yr Fire Alarm System - Inspection"]["read_only"] is True
    assert by_label["Assessments: Regular"]["read_only"] is False
    assert by_label["Gas & Electric"]["read_only"] is False

    # READ_ONLY_SECTIONS must match the actual read-only set
    read_only_cats = {li["category"] for li in line_items if li["read_only"]}
    assert read_only_cats.issubset(READ_ONLY_SECTIONS)

    preview_rows = {row[1]: row for row in preview["rows"] if len(row) > 1 and row[1]}
    assert preview_rows["Total Income"][5] == 695790
    assert preview_rows["Total Operating Expense"][5] == 52500
    assert preview_rows["Net Operating Income"][5] == 643290


def test_budget_pipeline_accepts_normalized_pdf_workbook_without_header_row_above_data(tmp_path):
    statement = ExtractedFinancialStatement.model_validate(
        {
            "document_family": "pdf_visual_document",
            "report_type": "income_statement",
            "line_items": [
                {
                    "account_code_text": "30000-00",
                    "label": "Member Assessments",
                    "section_kind": "income",
                    "ytd_actual": 327695.22,
                    "annual_budget": 436932.0,
                    "page_number": 1,
                },
                {
                    "account_code_text": "50100-00",
                    "label": "Management Fee",
                    "section_kind": "operating",
                    "ytd_actual": 18000.0,
                    "annual_budget": 24000.0,
                    "page_number": 1,
                },
            ],
            "totals": [],
            "validation_issues": [],
            "confidence": 0.95,
        }
    )

    normalized_path = build_normalized_statement_workbook(statement, str(tmp_path / "normalized.xlsx"))
    intermediate_path = str(tmp_path / "Income_Statement_Enriched.xlsx")
    output_path = str(tmp_path / "Budget_Pipeline.xlsx")

    pipeline = BudgetPipeline(
        input_path=normalized_path,
        intermediate_path=intermediate_path,
        output_path=output_path,
        growth_factor=12.0 / 9.0,
        growth_factor_note="auto annualization 12/9 from statement_period",
        enrich_only=True,
        known_columns={"ytd_actual": 6, "annual_budget": 9},
    )

    pipeline.run()

    assert Path(intermediate_path).exists()


def test_rows_without_numeric_account_codes_survive_enrichment_and_generation(tmp_path):
    """Final budgets like Old Mill's have no GL codes, and some PDFs print dashed
    codes. Both used to be read as section titles (dropped from the generated
    budget) or labelled with the code instead of the name."""
    statement = ExtractedFinancialStatement.model_validate(
        {
            "document_family": "excel_budget_workbook",
            "report_type": "final_budget",
            "line_items": [
                {"label": "Assessments", "section_kind": "income", "annual_budget": 2025540.0},
                {"label": "Clubhouse Rental Income", "section_kind": "income", "annual_budget": 1810.0},
                {"account_code_text": "50100-00", "label": "Management Fee", "section_kind": "operating", "annual_budget": 93744.0},
                {"label": "Total Care Landscaping", "section_kind": "operating", "annual_budget": 52200.0},
            ],
        }
    )

    normalized_path = build_normalized_statement_workbook(statement, str(tmp_path / "normalized.xlsx"))
    intermediate_path = str(tmp_path / "Income_Statement_Enriched.xlsx")
    output_path = str(tmp_path / "Budget_Pipeline.xlsx")
    BudgetPipeline(
        input_path=normalized_path,
        intermediate_path=intermediate_path,
        output_path=output_path,
        growth_factor=1.0,
        growth_factor_note="pro forma / final budget annual basis",
        enrich_only=False,
        known_columns={"ytd_actual": 6, "annual_budget": 9},
    ).run()

    line_items, _ = _table_to_line_items(macros_service.read_sheet_as_table(intermediate_path, "Income Statement"))
    assert [li["label"] for li in line_items] == [
        "Assessments", "Clubhouse Rental Income", "Management Fee", "Total Care Landscaping",
    ]
    # The enricher computed Proposed for every row, coded or not.
    assert [li["raw"]["Proposed"] for li in line_items] == [2025540.0, 1810.0, 93744.0, 52200.0]

    preview = macros_service.read_first_sheet_preview(output_path, 200)
    labels = [row[1] for row in preview["rows"] if len(row) > 1 and row[1]]
    assert {"Assessments", "Clubhouse Rental Income", "Management Fee", "Total Care Landscaping"} <= set(labels)
    assert "50100-00" not in labels
    preview_rows = {row[1]: row for row in preview["rows"] if len(row) > 1 and row[1]}
    assert preview_rows["Total Income"][5] == 2027350
    assert preview_rows["Total Operating Expense"][5] == 145944


def test_draft_snapshot_render_applies_operator_percent_change(tmp_path):
    """The re-render builds a fresh normalized workbook; the operator's % change
    must land in the column the enricher reads (it used to go to column 39)."""
    from types import SimpleNamespace

    from app.services.budget_history_service import _render_draft_snapshot_from_upload

    upload = SimpleNamespace(original_filename="Income Statement.pdf", content_type="application/pdf", storage_key="x")
    line_items = [
        {"line_item_key": "40000", "account_code": 40000, "label": "Assessment Income", "category": "income",
         "ytd_actual": 900.0, "annual_budget": 1200.0, "percent_change": 0},
        {"line_item_key": "Management Service", "label": "Management Service", "category": "operating",
         "ytd_actual": 600.0, "annual_budget": 1000.0, "percent_change": 10},
    ]
    enriched_bytes, _ = _render_draft_snapshot_from_upload(
        upload, line_items=line_items, growth_factor=1.0, growth_factor_note="test",
    )
    path = tmp_path / "enriched.xlsx"
    path.write_bytes(enriched_bytes)
    rows, _ = _table_to_line_items(macros_service.read_sheet_as_table(str(path), "Income Statement"))
    proposed = {row["label"]: row["raw"]["Proposed"] for row in rows}
    assert proposed == {"Assessment Income": 1200.0, "Management Service": 1100.0}
