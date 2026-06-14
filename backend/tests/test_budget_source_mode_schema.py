import asyncio
from pathlib import Path

from sqlalchemy import create_engine

from app.ai_implementation import database as database_module
from app.ai_implementation.db import session as session_module
from app.models.financial_document_extraction import ExtractedFinancialStatementPage
from app.services.budget_history_service import (
    _extract_excel_workbook_prompt_text,
    _extract_proforma_excel_statement,
    _parse_proforma_excel_source,
)


def test_legacy_source_mode_columns_backfill_to_income_statement(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy-source-mode.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        echo=False,
    )

    raw_conn = engine.raw_connection()
    try:
        raw_conn.executescript(
            """
            CREATE TABLE budget_uploads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_role TEXT,
                original_filename TEXT
            );
            INSERT INTO budget_uploads (document_role, original_filename)
            VALUES ('budget_source', 'legacy-upload.xlsx');

            CREATE TABLE budget_drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT,
                line_items_json TEXT
            );
            INSERT INTO budget_drafts (status, line_items_json)
            VALUES ('active', '[]');

            CREATE TABLE budget_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version_number INTEGER,
                version_code TEXT
            );
            INSERT INTO budget_versions (version_number, version_code)
            VALUES (1, 'V1');
            """
        )
        raw_conn.commit()
    finally:
        raw_conn.close()

    monkeypatch.setattr(database_module, "engine", engine)
    monkeypatch.setattr(session_module, "engine", engine)

    database_module.ensure_budget_upload_columns()
    database_module.ensure_budget_draft_columns()
    database_module.ensure_budget_version_columns()

    verify_conn = engine.raw_connection()
    try:
        assert verify_conn.execute("SELECT source_mode FROM budget_uploads").fetchone()[0] == "income_statement"
        assert verify_conn.execute("SELECT source_mode FROM budget_drafts").fetchone()[0] == "income_statement"
        assert verify_conn.execute("SELECT source_mode FROM budget_versions").fetchone()[0] == "income_statement"
    finally:
        verify_conn.close()


def test_parse_proforma_excel_source_reads_real_month_grid_budget_workbook():
    workbook_path = (
        Path(__file__).resolve().parents[2]
        / "Tri State Documents"
        / "Esprit Park"
        / "401 Esprit Park 2025 Budget .xlsx"
    )

    line_items = _parse_proforma_excel_source(str(workbook_path))

    assessments = next(item for item in line_items if item["label"] == "Assessments")
    reserve_transfer = next(
        item for item in line_items if item["label"] == "MONTHLY CONTRIBUTION TO RESERVE"
    )

    assert assessments["annual_budget"] == 1148083.2
    assert assessments["source_column"] == "annual_budget"
    assert assessments["category"] == "income"
    assert reserve_transfer["annual_budget"] == 266217
    assert reserve_transfer["category"] == "reserve_expense"
    assert reserve_transfer["reserve_group"] == "transfer"


def test_extract_excel_workbook_prompt_text_preserves_cells_for_gemini(tmp_path):
    from openpyxl import Workbook

    workbook_path = tmp_path / "synonym-budget.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Final Budget"
    worksheet["A1"] = "GL"
    worksheet["B1"] = "Line Item"
    worksheet["C1"] = "Approved Total"
    worksheet["A2"] = "40000"
    worksheet["B2"] = "Assessments"
    worksheet["C2"] = 1148083.2
    workbook.save(workbook_path)
    workbook.close()

    prompt_text = _extract_excel_workbook_prompt_text(str(workbook_path))

    assert "Sheet: Final Budget" in prompt_text
    assert "A1=GL" in prompt_text
    assert "B2=Assessments" in prompt_text
    assert "C2=1148083.2" in prompt_text


def test_extract_proforma_excel_statement_uses_gemini_schema(monkeypatch, tmp_path):
    from openpyxl import Workbook

    workbook_path = tmp_path / "synonym-budget.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Budget"
    worksheet.append(["Account", "Description", "Approved Total"])
    worksheet.append(["40000", "Assessments", 1148083.2])
    worksheet.append(["50050", "Management Service Contract", 61740.0])
    workbook.save(workbook_path)
    workbook.close()
    captured: dict[str, object] = {}

    async def _fake_call_llm(messages, response_schema, temperature=0.0, timeout=10.0):
        captured["messages"] = messages
        captured["schema"] = response_schema
        captured["temperature"] = temperature
        captured["timeout"] = timeout
        return ExtractedFinancialStatementPage.model_validate(
            {
                "document_family": "excel_budget_workbook",
                "report_type": "income_statement",
                "line_items": [
                    {
                        "account_code_text": "40000",
                        "label": "Assessments",
                        "section_kind": "income",
                        "annual_budget": 1148083.2,
                        "evidence": {"source_column": "approved_total"},
                    },
                    {
                        "account_code_text": "50050",
                        "label": "Management Service Contract",
                        "section_kind": "operating",
                        "annual_budget": 61740.0,
                        "evidence": {"source_column": "approved_total"},
                    },
                ],
                "confidence": 0.91,
            }
        )

    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client.call_llm", _fake_call_llm)

    result = asyncio.run(
        _extract_proforma_excel_statement(
            str(workbook_path),
            original_filename="synonym-budget.xlsx",
        )
    )

    assert result.document_family == "excel_budget_workbook"
    assert result.extraction_metadata["extractor"] == "gemini_excel_text"
    assert result.line_items[0].label == "Assessments"
    assert captured["schema"] is ExtractedFinancialStatementPage
    assert "Approved Total" in captured["messages"][1]["content"]
