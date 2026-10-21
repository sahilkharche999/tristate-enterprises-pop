"""Gemini reads every budget-source workbook; code checks what it returns."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import Workbook
from pydantic import BaseModel

from app.ai_implementation.pipeline.llm_client import _make_gemini_schema
from app.models.financial_document_extraction import (
    ExcelSourceExtraction,
    ExtractedFinancialStatement,
    IncomeRowRoles,
)
from app.services import budget_history_service as svc
from app.services.financial_statement_validation import totals_for_validation

DATA = Path(__file__).resolve().parents[2] / "data"


def _line(label, row, *, sheet="Budget", kind="operating", annual=None, ytd=None):
    return {
        "label": label, "account_code_text": None, "section_kind": kind, "source_sheet": sheet,
        "source_row": row, "current_actual": None, "current_budget": None, "ytd_actual": ytd,
        "ytd_budget": None, "annual_budget": annual,
    }


def _reading(kind="final_budget", lines=None, totals=None, facts=None):
    return ExcelSourceExtraction.model_validate(
        {
            "document_kind": kind,
            "kind_evidence": "OPERATING AND CASH FLOW STATEMENT FOR 2027",
            "association_name": "Old Mill Homeowners Association",
            "primary_sheet": "Budget",
            "statement_period": "2027",
            "period_end": None,
            "months_covered": None,
            "fiscal_year": 2027,
            "line_items": lines if lines is not None else [
                _line("Assessments", 2, kind="income", annual=2025540.0),
                _line("Management Service", 3, annual=93744.0),
            ],
            "stated_totals": totals or [],
            "facts": facts,
        }
    )


def _budget_workbook(path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Budget"
    sheet.append(["Line", "Jan", "Total"])
    sheet.append(["Assessments", 168795, 2025540])
    sheet.append(["Management Service", 7812, 93744])
    sheet.append(["TOTAL", None, 2119284])
    workbook.save(path)
    return str(path)


def _fake_call_llm(replies, calls):
    async def fake(messages, response_schema, temperature=0.3, timeout=10.0, **kwargs):
        calls.append({"messages": messages, "schema": response_schema, **kwargs})
        return replies[min(len(calls), len(replies)) - 1]

    return fake


# --- serializer --------------------------------------------------------------

def test_dump_reads_exports_that_report_no_dimensions_in_read_only_mode():
    # This accounting-system export made the old read_only serializer emit
    # "(empty)" for every sheet.
    text, numbers = svc._excel_cell_dump(str(DATA / "Income Statement Esprit Park Aug 2025.xlsx"))
    assert "Statement of Revenues and Expenses" in text
    assert any(765386.88 in values for values in numbers.values())


def test_dump_skips_hidden_sheets_rows_and_columns(tmp_path):
    workbook = Workbook()
    visible = workbook.active
    visible.title = "2027 Approved"
    visible.append(["Line", "Old", "Budget"])
    visible.append(["Insurance", 999, 54000])
    visible.append(["Scratch", 1, 2])
    visible.column_dimensions["B"].hidden = True
    visible.row_dimensions[3].hidden = True
    old = workbook.create_sheet("2025")
    old.append(["Insurance", 41000])
    old.sheet_state = "hidden"
    path = tmp_path / "hidden.xlsx"
    workbook.save(path)

    text, numbers = svc._excel_cell_dump(str(path))

    assert "Sheet: 2027 Approved" in text and "Sheet: 2025" not in text
    assert "C2=54000" in text
    assert "999" not in text and "Scratch" not in text
    assert numbers[("2027 Approved", 2)] == [54000.0]


def test_dump_reads_every_sheet_of_an_xls_workbook():
    text, numbers = svc._excel_cell_dump(str(DATA / "Campbell Court Income Statement Jan 2026.xls"))
    assert text.startswith("Sheet: Income Statement")
    assert any(91008.0 in values for values in numbers.values())


def test_dump_refuses_oversized_workbooks_instead_of_truncating(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_EXCEL_PROMPT_MAX_CHARS", 50)
    with pytest.raises(ValueError, match="too large"):
        svc._excel_cell_dump(_budget_workbook(tmp_path / "budget.xlsx"))


# --- Gemini reading ------------------------------------------------------------

def test_final_budget_reading_becomes_a_final_budget_statement(tmp_path, monkeypatch):
    calls = []
    reading = _reading(totals=[{"label": "TOTAL", "section_kind": None, "source_row": 4,
                                "annual_budget": 2119284.0, "ytd_actual": None}])
    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client.call_llm", _fake_call_llm([reading], calls))

    result = asyncio.run(svc._extract_excel_source(_budget_workbook(tmp_path / "b.xlsx"), original_filename="442.xlsx"))

    assert isinstance(result, ExtractedFinancialStatement)
    assert result.report_type == "final_budget"
    assert [item.label for item in result.line_items] == ["Assessments", "Management Service"]
    assert result.line_items[0].evidence == {"source_cell": "Budget!R2"}
    assert result.extraction_metadata["kind_evidence"].startswith("OPERATING AND CASH FLOW")
    assert calls[0]["schema"] is ExcelSourceExtraction
    assert calls[0]["thinking_budget"] == 0
    assert "B2=168795" in calls[0]["messages"][1]["content"]


def test_a_facts_form_is_not_a_budget_source(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.ai_implementation.pipeline.llm_client.call_llm",
        _fake_call_llm([_reading(kind="other", lines=[])], []),
    )

    result = asyncio.run(svc._extract_excel_source(_budget_workbook(tmp_path / "b.xlsx"), original_filename="prep.xlsx"))

    assert result.code == "not_a_budget_source"
    assert "prep.xlsx doesn't look like an income statement or a budget" in result.message


def test_operator_override_wins_over_the_detected_kind(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.ai_implementation.pipeline.llm_client.call_llm",
        _fake_call_llm([_reading(kind="income_statement")], []),
    )

    result = asyncio.run(
        svc._extract_excel_source(
            _budget_workbook(tmp_path / "b.xlsx"), kind_override=svc.SOURCE_MODE_PROFORMA_FINAL_BUDGET
        )
    )

    assert result.report_type == "final_budget"


def test_values_missing_from_their_row_get_one_retry_then_block(tmp_path, monkeypatch):
    calls = []
    wrong = _reading(lines=[_line("Assessments", 2, kind="income", annual=2025540.0),
                            _line("Management Service", 3, annual=7812.0 * 13)])
    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client.call_llm", _fake_call_llm([wrong, wrong], calls))

    result = asyncio.run(svc._extract_excel_source(_budget_workbook(tmp_path / "b.xlsx")))

    assert result.code == "value_not_in_cited_row"
    assert len(calls) == 2
    assert "not in the rows it cited" in calls[1]["messages"][1]["content"]
    assert result.details["problems"] == ["'Management Service': annual_budget=101,556.00 is not in row 3"]


def test_values_check_passes_on_the_retry_when_gemini_corrects_itself(tmp_path, monkeypatch):
    calls = []
    wrong = _reading(lines=[_line("Assessments", 2, kind="income", annual=1.0), _line("Management Service", 3, annual=93744.0)])
    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client.call_llm", _fake_call_llm([wrong, _reading()], calls))

    result = asyncio.run(svc._extract_excel_source(_budget_workbook(tmp_path / "b.xlsx")))

    assert isinstance(result, ExtractedFinancialStatement)
    assert len(calls) == 2


# --- checks on what was read ------------------------------------------------------

def test_totals_for_validation_compares_against_each_sections_grand_total():
    totals = totals_for_validation([
        {"label": "Total Utilities", "section_kind": "operating", "annual_budget": 400.0},
        {"label": "Total Operating Expense", "section_kind": "operating", "annual_budget": 1200.0},
        {"label": "Net Income", "section_kind": None, "annual_budget": 50.0},
    ])
    assert totals == [{"section_kind": "operating", "label": "Total Operating Expense", "amount": 1200.0,
                       "source": "document"}]


def test_reserve_contribution_is_a_transfer_however_it_was_tagged():
    statement = ExtractedFinancialStatement.model_validate(
        {
            "document_family": "excel_budget_workbook",
            "report_type": "final_budget",
            "line_items": [
                {"label": "Assessments", "section_kind": "income", "annual_budget": 10.0},
                {"label": "MONTHLY CONTRIBUTION TO RESERVE", "section_kind": "operating", "annual_budget": 5.0},
                {"label": "Roof Replacement", "section_kind": "reserve_expense", "annual_budget": 3.0},
            ],
        }
    )
    items = {item["label"]: item for item in svc._statement_to_budget_line_items(statement, source_mode="proforma_final_budget")}
    assert (items["MONTHLY CONTRIBUTION TO RESERVE"]["category"], items["MONTHLY CONTRIBUTION TO RESERVE"]["reserve_group"]) == ("reserve_expense", "transfer")
    assert items["Roof Replacement"]["reserve_group"] == "component"


@pytest.mark.parametrize(
    "printed, hoa, warns",
    [
        ("Esprit Park Owners Association", "800 High Street Condominiums Association", True),
        ("Old Mill HOA", "Old Mill Homeowners Association", False),
        (None, "Old Mill Homeowners Association", False),
    ],
)
def test_wrong_association_is_flagged(printed, hoa, warns):
    statement = ExtractedFinancialStatement.model_validate(
        {
            "document_family": "excel_budget_workbook",
            "report_type": "final_budget",
            "line_items": [{"label": "A", "annual_budget": 1.0}, {"label": "B", "annual_budget": 2.0}],
            "extraction_metadata": {"association_name": printed},
        }
    )
    assert bool(svc._source_document_warnings(statement, SimpleNamespace(name=hoa))) is warns


def test_months_check_catches_a_wrong_fiscal_year_start():
    # Campbell Court: a January statement whose YTD assessments are 10/12 of the year.
    statement = ExtractedFinancialStatement.model_validate(
        {
            "document_family": "excel_budget_workbook",
            "report_type": "income_statement",
            "line_items": [
                {"label": "40000 - Assessment Income", "section_kind": "income", "ytd_actual": 75840.0, "annual_budget": 91008.0},
                {"label": "41000 - Late Fees", "section_kind": "income", "ytd_actual": 1209.66, "annual_budget": 1400.0},
            ],
        }
    )
    assert "looks like 10 months" in svc._months_sanity_warning(statement, detected_months=1)
    assert svc._months_sanity_warning(statement, detected_months=10) is None


def test_gemini_schema_keeps_fields_named_like_schema_keywords():
    class Page(BaseModel):
        title: str
        default: int

    schema = _make_gemini_schema(Page)
    assert set(schema["properties"]) == {"title", "default"}
    assert schema["required"] == ["title", "default"]


def test_legacy_raw_layout_enriched_workbook_is_rebuilt_before_version_generation(tmp_path, monkeypatch):
    # Drafts made by the retired Excel parser persisted the raw statement layout.
    raw = Workbook()
    raw.active.title = "Income Statement"
    raw.active.append(["Los Altos Village Square", None, None])
    raw_path = tmp_path / "enriched.xlsx"
    raw.save(raw_path)
    rebuilt = []
    monkeypatch.setattr(svc, "_storage_file_available", lambda key: True)
    monkeypatch.setattr(svc, "_budget_storage_path", lambda key: raw_path)
    monkeypatch.setattr(svc, "_get_upload", lambda session, upload_id: SimpleNamespace(id=upload_id))
    monkeypatch.setattr(svc, "_refresh_draft_snapshot_from_upload", lambda session, draft, upload: rebuilt.append(draft))
    draft = SimpleNamespace(enriched_storage_key="hoa/11/drafts/11/enriched.xlsx", source_upload_id=5)

    assert svc._ensure_draft_enriched_workbook(None, draft) == raw_path  # downloads keep the file
    assert rebuilt == []
    svc._ensure_draft_enriched_workbook(None, draft, require_normalized=True)
    assert rebuilt == [draft]


def test_facts_survive_bare_values_nulls_and_missing_keys():
    # Seen on the VM key: Bob's prep sheet came back as
    # "reserve_balance_start_of_budget_year": 2260449.87 instead of an object.
    from app.models.financial_document_extraction import DisclosureFacts

    facts = DisclosureFacts.model_validate(
        {"budget_fiscal_year": 2027, "reserve_balance_start_of_budget_year": 2260449.87, "percent_funded": None}
    )
    assert facts.reserve_balance_start_of_budget_year.number == 2260449.87
    assert facts.percent_funded.printed is None
    assert facts.planned_assessment_increases == []

    reading = _reading(kind="other", lines=[])
    garbage = ExcelSourceExtraction.model_validate({**reading.model_dump(), "facts": {"budget_fiscal_year": "n/a"}})
    assert garbage.facts is None  # facts are dropped, the reading is kept


def test_a_facts_form_without_facts_is_read_once_more_with_thinking(tmp_path, monkeypatch):
    from app.models.financial_document_extraction import DisclosureFacts

    found = DisclosureFacts.model_validate(
        {"budget_fiscal_year": 2027,
         "reserve_balance_start_of_budget_year": {"number": 2260449.87, "as_of_date": "2026-12-31", "page": 3}}
    )
    calls = []
    replies = [_reading(kind="other", lines=[]), _reading(kind="other", lines=[], facts=found.model_dump())]
    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client.call_llm", _fake_call_llm(replies, calls))

    result = asyncio.run(svc._extract_excel_source(_budget_workbook(tmp_path / "prep.xlsx"), original_filename="prep.xlsx"))

    assert result.code == "not_a_budget_source"
    assert len(calls) == 2 and "thinking_budget" not in calls[1]
    assert result.details["disclosure_facts"]["reserve_balance_start_of_budget_year"]["as_of_date"] == "2026-12-31"


def test_version_totals_read_the_generators_total_operating_expense_row():
    # BudgetGenerator writes "Total Operating Expense"; versions used to store 0.
    preview = {"rows": [
        ["", "Total Income", None, 2034910.0, None, 2034910.0],
        ["", "Total Operating Expense", None, 1225993.64, None, 1235368.04],
        ["", "Net Operating Income", None, 808916.36, None, 799541.96],
    ]}
    assert svc._extract_totals_from_preview(preview) == (2034910.0, 1235368.04, 808916.36)


def test_income_roles_reach_the_final_budget_lines(tmp_path, monkeypatch):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Budget"
    sheet.append(["Line", "Total"])
    sheet.append(["Assessments", 2025540])
    sheet.append(["Parking Garage Assessment Income", 7560])
    sheet.append(["Management Service", 93744])
    path = tmp_path / "442.xlsx"
    workbook.save(path)
    lines = [
        {**_line("Assessments", 2, kind="income", annual=2025540.0), "income_role": "regular_assessment"},
        {**_line("Parking Garage Assessment Income", 3, kind="income", annual=7560.0), "income_role": "other_assessment"},
        # Gemini is told roles are for income rows; one on an expense row is dropped.
        {**_line("Management Service", 4, annual=93744.0), "income_role": "other_income"},
    ]
    monkeypatch.setattr(
        "app.ai_implementation.pipeline.llm_client.call_llm", _fake_call_llm([_reading(lines=lines)], [])
    )

    statement = asyncio.run(svc._extract_excel_source(str(path), original_filename="442.xlsx"))
    draft_lines = svc._statement_to_budget_line_items(
        statement, source_mode=svc.SOURCE_MODE_PROFORMA_FINAL_BUDGET
    )

    assert [line["income_role"] for line in draft_lines] == ["regular_assessment", "other_assessment", None]


def _pdf_statement():
    return ExtractedFinancialStatement.model_validate({
        "document_family": "pdf_visual_document",
        "report_type": "income_statement",
        "line_items": [
            {"label": "Assessment Income", "section_kind": "income", "annual_budget": 2025540.0},
            {"label": "Parking Space Income", "section_kind": "income", "annual_budget": 7560.0},
            {"label": "Reserve Income", "section_kind": "reserve_income", "annual_budget": 672886.0},
            {"label": "Management Service", "section_kind": "operating", "annual_budget": 93744.0},
        ],
    })


def test_pdf_income_rows_get_roles_from_one_quick_call(monkeypatch):
    calls = []
    answer = IncomeRowRoles.model_validate({"rows": [
        {"row": 1, "income_role": "regular_assessment"},
        {"row": 2, "income_role": "other_income"},
    ]})
    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client.call_llm", _fake_call_llm([answer], calls))

    tagged = asyncio.run(svc._tag_income_roles(_pdf_statement()))

    # Only operating income rows are asked about; reserve rows keep the label rule.
    assert [item.income_role for item in tagged.line_items] == ["regular_assessment", "other_income", None, None]
    assert "1. Assessment Income" in calls[0]["messages"][1]["content"]
    assert "Reserve Income" not in calls[0]["messages"][1]["content"]
    assert calls[0]["thinking_budget"] == 0


def test_failed_role_call_leaves_the_statement_untagged(monkeypatch):
    monkeypatch.setattr("app.ai_implementation.pipeline.llm_client.call_llm", _fake_call_llm([None], []))
    tagged = asyncio.run(svc._tag_income_roles(_pdf_statement()))
    assert all(item.income_role is None for item in tagged.line_items)


def test_reserve_fund_rows_never_keep_a_role():
    # A reserve "Reserve Income" is the transfer from operating; read as dues
    # it would double the assessment in the fixed-mode schedule.
    statement = _pdf_statement()
    statement.line_items[2] = statement.line_items[2].model_copy(update={"income_role": "regular_assessment"})
    lines = svc._statement_to_budget_line_items(statement, source_mode=svc.SOURCE_MODE_INCOME_STATEMENT)
    assert lines[2]["category"] == "reserve_income" and lines[2]["income_role"] is None


def test_pdf_page_schema_does_not_ask_for_roles():
    from app.services.pdf_vlm_extractor import StatementPageWithTotals

    assert "income_role" not in str(StatementPageWithTotals.model_json_schema())
