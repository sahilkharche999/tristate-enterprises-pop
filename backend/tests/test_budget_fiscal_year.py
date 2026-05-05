from app.routers import macros as macros_router
from app.models.financial_document_extraction import ExtractedFinancialStatement


def test_generate_budget_uses_supplied_fiscal_year_start_month(client, monkeypatch):
    captured: dict[str, object] = {}

    def fake_infer_growth_factor_from_input(input_path: str, fiscal_year_start_month: int):
        captured["input_path"] = input_path
        captured["fiscal_year_start_month"] = fiscal_year_start_month
        return 1.25, 6, "stub"

    class FakeBudgetPipeline:
        def __init__(self, **kwargs):
            captured["pipeline_kwargs"] = kwargs

        def run(self):
            return None

    def fake_read_sheet_as_table(path: str, sheet: str):
        return {"sheet": sheet, "headers": [], "rows": []}

    def fake_read_first_sheet_preview(path: str, max_rows: int):
        return {
            "sheet": "Budget",
            "headers": ["Account Code", "Line Item", "YTD Actual", "Annual Budget",
                         "% Change", "Proposed Budget", "Monthly", "Notes"],
            "rows": [["", "Total Income", None, 1000, None, 1000, 83.33, None]],
        }

    monkeypatch.setattr(macros_router, "infer_growth_factor_from_input", fake_infer_growth_factor_from_input)
    monkeypatch.setattr(macros_router, "BudgetPipeline", FakeBudgetPipeline)
    monkeypatch.setattr(macros_router.macros_service, "read_sheet_as_table", fake_read_sheet_as_table)
    monkeypatch.setattr(macros_router.macros_service, "read_first_sheet_preview", fake_read_first_sheet_preview)

    response = client.post(
        "/macros/generate-budget",
        files={
            "file": (
                "income-statement.xlsx",
                b"placeholder workbook bytes",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        data={
            "fiscal_year_start_month": "7",
            "enrich_only": "false",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert captured["fiscal_year_start_month"] == 7
    assert captured["pipeline_kwargs"]["growth_factor"] == 1.25
    assert payload["statement_month"] == 12
    assert payload["growth_factor"] == 1.25


def test_generate_budget_pdf_uses_vlm_statement_period_before_workbook_inference(client, monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    async def fake_extract_pdf_statement(path: str):
        return ExtractedFinancialStatement.model_validate(
            {
                "document_family": "pdf_visual_document",
                "report_type": "income_statement",
                "statement_period": "08/21/2025 to 09/20/2025",
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
                "confidence": 0.9,
            }
        )

    def fake_normalize(statement):
        output_path = tmp_path / "normalized.xlsx"
        output_path.write_bytes(b"normalized workbook")
        return str(output_path)

    def unexpected_infer_growth_factor_from_input(*args, **kwargs):
        raise AssertionError("workbook inference should not run when statement_period is available")

    class FakeBudgetPipeline:
        def __init__(self, **kwargs):
            captured["pipeline_kwargs"] = kwargs

        def run(self):
            return None

    def fake_read_sheet_as_table(path: str, sheet: str):
        return {"sheet": sheet, "headers": [], "rows": []}

    def fake_read_first_sheet_preview(path: str, max_rows: int):
        return {
            "sheet": "Budget",
            "headers": ["Account Code", "Line Item", "YTD Actual", "Annual Budget", "% Change", "Proposed Budget", "Monthly", "Notes"],
            "rows": [["30000-00", "Member Assessments", 327695.22, 436932.0, None, 436932.0, 36411.0, None]],
        }

    monkeypatch.setattr(macros_router, "extract_pdf_statement", fake_extract_pdf_statement)
    monkeypatch.setattr(macros_router, "build_normalized_statement_workbook", fake_normalize)
    monkeypatch.setattr(macros_router, "infer_growth_factor_from_input", unexpected_infer_growth_factor_from_input)
    monkeypatch.setattr(macros_router, "BudgetPipeline", FakeBudgetPipeline)
    monkeypatch.setattr(macros_router.macros_service, "read_sheet_as_table", fake_read_sheet_as_table)
    monkeypatch.setattr(macros_router.macros_service, "read_first_sheet_preview", fake_read_first_sheet_preview)

    response = client.post(
        "/macros/generate-budget",
        files={"file": ("2238 Market.pdf", b"placeholder pdf bytes", "application/pdf")},
        data={"fiscal_year_start_month": "1", "enrich_only": "false"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert captured["pipeline_kwargs"]["growth_factor"] == 12.0 / 9.0
    assert payload["statement_month"] == 9
    assert payload["growth_factor"] == 12.0 / 9.0


def test_generate_budget_pdf_uses_pdf_header_period_hint_when_vlm_omits_period(client, monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    async def fake_extract_pdf_statement(path: str):
        return ExtractedFinancialStatement.model_validate(
            {
                "document_family": "pdf_visual_document",
                "report_type": "income_statement",
                "statement_period": None,
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
                "confidence": 0.9,
            }
        )

    def fake_normalize(statement):
        output_path = tmp_path / "normalized.xlsx"
        output_path.write_bytes(b"normalized workbook")
        return str(output_path)

    def unexpected_infer_growth_factor_from_input(*args, **kwargs):
        raise AssertionError("workbook inference should not run when PDF header hint is available")

    class FakeBudgetPipeline:
        def __init__(self, **kwargs):
            captured["pipeline_kwargs"] = kwargs

        def run(self):
            return None

    def fake_read_sheet_as_table(path: str, sheet: str):
        return {"sheet": sheet, "headers": [], "rows": []}

    def fake_read_first_sheet_preview(path: str, max_rows: int):
        return {
            "sheet": "Budget",
            "headers": ["Account Code", "Line Item", "YTD Actual", "Annual Budget", "% Change", "Proposed Budget", "Monthly", "Notes"],
            "rows": [["30000-00", "Member Assessments", 327695.22, 436932.0, None, 436932.0, 36411.0, None]],
        }

    monkeypatch.setattr(macros_router, "extract_pdf_statement", fake_extract_pdf_statement)
    monkeypatch.setattr(macros_router, "build_normalized_statement_workbook", fake_normalize)
    monkeypatch.setattr(macros_router, "extract_pdf_statement_period_hint", lambda path: "08/21/2025 to 09/20/2025")
    monkeypatch.setattr(macros_router, "infer_growth_factor_from_input", unexpected_infer_growth_factor_from_input)
    monkeypatch.setattr(macros_router, "BudgetPipeline", FakeBudgetPipeline)
    monkeypatch.setattr(macros_router.macros_service, "read_sheet_as_table", fake_read_sheet_as_table)
    monkeypatch.setattr(macros_router.macros_service, "read_first_sheet_preview", fake_read_first_sheet_preview)

    response = client.post(
        "/macros/generate-budget",
        files={"file": ("2238 Market.pdf", b"placeholder pdf bytes", "application/pdf")},
        data={"fiscal_year_start_month": "1", "enrich_only": "false"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert captured["pipeline_kwargs"]["growth_factor"] == 12.0 / 9.0
    assert payload["statement_month"] == 9
    assert payload["growth_factor"] == 12.0 / 9.0


def test_select_statement_period_hint_prefers_date_range_over_report_timestamp():
    text = (
        "Income Statement - Operating Date: 10/21/2025\n"
        "2238 Market Condominium Association Time: 10:26 am\n"
        "08/21/2025 to 09/20/2025 Page: 1\n"
    )

    hint = macros_router.select_statement_period_hint(text)

    assert hint == "08/21/2025 to 09/20/2025 Page: 1"
