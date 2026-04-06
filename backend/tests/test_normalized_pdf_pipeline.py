from pathlib import Path

from app.generate_budget_pipeline import BudgetPipeline
from app.models.financial_document_extraction import ExtractedFinancialStatement
from app.services.normalized_statement_workbook import build_normalized_statement_workbook


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
    )

    pipeline.run()

    assert Path(intermediate_path).exists()
