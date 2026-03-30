from app.routers import macros as macros_router


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
