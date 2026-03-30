import sqlite3
from pathlib import Path

from app.services.budget_history_service import _table_to_line_items


def _upload_income_statement(client):
    return client.post(
        "/hoa/1/budget/upload",
        files={
            "file": (
                "income-statement.xlsx",
                b"placeholder workbook bytes",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )


def _generate_version(client, draft, *, global_note="Generated for test"):
    return client.post(
        "/hoa/1/budget/generate",
        json={
            "draft_id": draft["id"],
            "line_items": draft["line_items"],
            "global_note": global_note,
        },
    )


def _sheet_row(*values, width: int = 39):
    row = [None] * width
    for index, value in enumerate(values):
        row[index] = value
    return row


def test_table_to_line_items_supports_headerless_income_statement_layout():
    table = {
        "headers": ["Esprit Park Owners Association"] + [None] * 38,
        "rows": [
            _sheet_row("Statement of Revenues and Expenses 8/1/2025 - 8/31/2025"),
            _sheet_row(),
            _sheet_row(None, None, None, None, None, None, "Current Period"),
            _sheet_row(None, None, None, "Actual", None, None, None, None, None, None, "Budget"),
            _sheet_row("Operating Income"),
            _sheet_row(None, "41170 - Utility Refund (Conservice)", None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, 24340.0, None, None, None, None, None, None, None, None, None, None, None, None, 50000.0, None, None, None, None, 36510.0, 0),
            _sheet_row(None, "40000 - Assessment Income", None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, 765386.88, None, None, None, None, None, None, None, None, None, None, None, None, 1148080.32, None, None, None, None, 1148080.32, 0),
            _sheet_row(None, "51610 - Bank Charges", None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, 30.0, None, None, None, None, None, None, None, None, None, None, None, None, "-", None, None, None, None, 45.0, 0),
            _sheet_row("Total Administration Expenses", None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, 150497.98, None, None, None, None, None, None, None, None, None, None, None, None, 287748, None, None, None, None, 223346.22, None),
            _sheet_row("Allocation to Reserves"),
            _sheet_row(None, "90000 - Reserve - Allocation/Transfer", None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, 177478.0, None, None, None, None, None, None, None, None, None, None, None, None, 266217.0, None, None, None, None, None, None),
            _sheet_row("Reserve Income"),
            _sheet_row(None, "45000 - Reserve Income", None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, 177478.0, None, None, None, None, None, None, None, None, None, None, None, None, 266217.0, None, None, None, None, None, None),
            _sheet_row(None, "47001 - Change in Asset Value", None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, 806.84, None, None, None, None, None, None, None, None, None, None, None, None, 7000.0, None, None, None, None, None, None),
            _sheet_row("Reserve Expenses (Per Reserve Study)"),
            _sheet_row(None, "95220 - Roof", None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, 1500.0, None, None, None, None, None, None, None, None, None, None, None, None, 3000.0, None, None, None, None, 2250.0, 0),
        ],
    }

    line_items = _table_to_line_items(table)

    assert [item["label"] for item in line_items[:2]] == [
        "41170 - Utility Refund (Conservice)",
        "40000 - Assessment Income",
    ]
    utility_refund = next(item for item in line_items if item["label"] == "41170 - Utility Refund (Conservice)")
    assert utility_refund["category"] == "income"
    assert utility_refund["annual_budget"] == 50000.0
    assert utility_refund["read_only"] is False
    assert [item["label"] for item in line_items[1:3]] == [
        "40000 - Assessment Income",
        "51610 - Bank Charges",
    ]
    assessment_income = next(item for item in line_items if item["label"] == "40000 - Assessment Income")
    assert assessment_income["account_code"] == 40000
    assert assessment_income["ytd_actual"] == 765386.88
    assert assessment_income["annual_budget"] == 1148080.32
    assert assessment_income["projection"] == 1148080.32
    assert assessment_income["percent_change"] == 0
    assert assessment_income["read_only"] is False
    reserve_transfer = next(item for item in line_items if item["label"] == "90000 - Reserve - Allocation/Transfer")
    assert reserve_transfer["read_only"] is True
    reserve_income = next(item for item in line_items if item["label"] == "45000 - Reserve Income")
    assert reserve_income["read_only"] is True
    reserve_asset_value = next(item for item in line_items if item["label"] == "47001 - Change in Asset Value")
    assert reserve_asset_value["read_only"] is True
    assert all(item["label"] != "Total Administration Expenses" for item in line_items)
    reserve_item = next(item for item in line_items if item["label"] == "95220 - Roof")
    assert reserve_item["read_only"] is True
    assert reserve_item["category"] == "reserve"


def test_table_to_line_items_marks_reserve_expense_block_rows_as_reserve_components():
    table = {
        "headers": ["Income Statement"] + [None] * 38,
        "rows": [
            _sheet_row("Reserve Expenses (Per Reserve Study)"),
            _sheet_row(None, "91432 - Circulation Pump 5 H.P.", None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, 0.0, None, None, None, None, None, None, None, None, None, None, None, None, 109008.0, None, None, None, None, None, None),
            _sheet_row(None, "95220 - Roof", None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, 217758.0, None, None, None, None, None, None, None, None, None, None, None, None, 0.0, None, None, None, None, None, None),
            _sheet_row(None, "47000 - Interest Earned Reserve", None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, 6322.0, None, None, None, None, None, None, None, None, None, None, None, None, 8600.0, None, None, None, None, None, None),
        ],
    }

    line_items = _table_to_line_items(table)

    circulation_pump = next(item for item in line_items if item["label"] == "91432 - Circulation Pump 5 H.P.")
    roof = next(item for item in line_items if item["label"] == "95220 - Roof")
    reserve_interest = next(item for item in line_items if item["label"] == "47000 - Interest Earned Reserve")

    assert circulation_pump["category"] == "reserve"
    assert circulation_pump["read_only"] is True
    assert roof["category"] == "reserve"
    assert roof["read_only"] is True
    assert reserve_interest["category"] == "reserve"
    assert reserve_interest["read_only"] is True


def test_upload_creates_history(client, budget_history_test_harness):
    response = _upload_income_statement(client)

    assert response.status_code == 200
    payload = response.json()
    assert payload["upload_id"] > 0
    assert payload["draft"]["source_upload_id"] == payload["upload_id"]
    assert payload["draft"]["status"] == "active"
    assert payload["draft"]["enriched_file_available"] is True
    assert payload["timeline_event"]["event_type"] == "upload_received"

    history_response = client.get("/hoa/1/history")
    assert history_response.status_code == 200
    history_payload = history_response.json()

    assert history_payload["active_draft"]["id"] == payload["draft"]["id"]
    assert history_payload["drafts"][0]["id"] == payload["draft"]["id"]
    assert history_payload["drafts"][0]["enriched_file_available"] is True
    assert {event["event_type"] for event in history_payload["timeline"]} >= {
        "upload_received",
        "enrichment_completed",
    }
    retained_files = list(Path(budget_history_test_harness["storage_root"]).rglob("*"))
    assert any(path.is_file() for path in retained_files)


def test_generate_creates_version_snapshot(client, budget_history_test_harness):
    upload_response = _upload_income_statement(client)
    assert upload_response.status_code == 200
    upload_payload = upload_response.json()
    draft = upload_payload["draft"]

    generate_response = _generate_version(
        client,
        draft,
        global_note="Initial board-review draft",
    )

    assert generate_response.status_code == 200
    payload = generate_response.json()
    assert payload["version"]["version_number"] == 1
    assert payload["version"]["version_code"] == "V1"
    assert payload["version"]["stage"] == "Interim"
    assert payload["version"]["growth_factor"] == budget_history_test_harness["expected_growth_factor"]
    assert payload["timeline_event"]["event_type"] == "budget_generated"

    version_response = client.get(f"/hoa/1/versions/{payload['version']['id']}")
    assert version_response.status_code == 200
    version_payload = version_response.json()
    assert version_payload["id"] == payload["version"]["id"]
    assert version_payload["budget_preview"]["sheet"] == "Budget"
    assert len(version_payload["line_items"]) == len(draft["line_items"])

    history_response = client.get("/hoa/1/history")
    assert history_response.status_code == 200
    history_payload = history_response.json()
    assert history_payload["versions"][0]["version_code"] == "V1"
    assert {event["event_type"] for event in history_payload["timeline"]} >= {"budget_generated"}


def test_note_save_and_manual_override_audit(client, budget_history_test_harness):
    upload_response = _upload_income_statement(client)
    assert upload_response.status_code == 200
    draft = upload_response.json()["draft"]

    updated_line_items = [
        {
            **draft["line_items"][0],
            "proposed_amount": 129500.0,
            "manual_override": True,
        },
        *draft["line_items"][1:],
    ]

    draft_response = client.patch(
        "/hoa/1/budget/draft",
        json={
            "draft_id": draft["id"],
            "line_items": updated_line_items,
            "global_note": "Reviewed with updated assessment assumption",
            "statement_month": 12,
            "growth_factor": budget_history_test_harness["expected_growth_factor"],
            "growth_factor_note": "manual confirmation",
        },
    )

    assert draft_response.status_code == 200
    draft_payload = draft_response.json()
    assert draft_payload["draft"]["id"] == draft["id"]
    assert draft_payload["timeline_event"]["event_type"] == "manual_overrides_saved"

    note_response = client.post(
        "/hoa/1/budget/notes",
        json={
            "draft_id": draft["id"],
            "note_scope": "line_item",
            "line_item_key": str(updated_line_items[0].get("line_item_key") or updated_line_items[0].get("label")),
            "title": "Insurance renewal",
            "body": "Carrier communicated a likely 10 percent increase.",
        },
    )

    assert note_response.status_code == 200
    note_payload = note_response.json()
    assert note_payload["note"]["draft_id"] == draft["id"]
    assert note_payload["timeline_event"]["event_type"] == "note_saved"

    history_response = client.get("/hoa/1/history")
    assert history_response.status_code == 200
    history_payload = history_response.json()
    assert {event["event_type"] for event in history_payload["timeline"]} >= {
        "manual_overrides_saved",
        "note_saved",
    }
    assert history_payload["notes"][0]["title"] == "Insurance renewal"


def test_history_screen_payload_shape(client, budget_history_test_harness):
    upload_response = _upload_income_statement(client)
    assert upload_response.status_code == 200
    draft = upload_response.json()["draft"]

    note_response = client.post(
        "/hoa/1/budget/notes",
        json={
            "draft_id": draft["id"],
            "note_scope": "global",
            "title": "Fiscal strategy",
            "body": "Board requested conservative operating assumptions.",
        },
    )
    assert note_response.status_code == 200

    generate_response = _generate_version(
        client,
        draft,
        global_note="History payload smoke coverage",
    )
    assert generate_response.status_code == 200
    version_id = generate_response.json()["version"]["id"]

    history_response = client.get("/hoa/1/history")
    assert history_response.status_code == 200
    payload = history_response.json()

    assert set(payload) == {"active_draft", "timeline", "versions", "notes", "drafts"}
    assert isinstance(payload["timeline"], list)
    assert isinstance(payload["drafts"], list)
    assert isinstance(payload["versions"], list)
    assert isinstance(payload["notes"], list)
    assert payload["notes"][0]["note_scope"] == "global"
    assert payload["versions"][0]["id"] == version_id
    assert payload["active_draft"] is None or payload["active_draft"]["id"] == draft["id"]


def test_generate_version_uses_draft_percent_changes_in_persisted_output(
    client,
    budget_history_test_harness,
    monkeypatch,
):
    preview_by_output_path = {}
    changes_by_input_path = {}

    class PercentAwarePipeline:
        def __init__(self, **kwargs):
            self.input_path = kwargs["input_path"]
            self.intermediate_path = kwargs["intermediate_path"]
            self.output_path = kwargs["output_path"]

        def run(self):
            Path(self.intermediate_path).write_bytes(b"fake enriched workbook")
            changes = changes_by_input_path.get(self.input_path, {})
            total_income = 126000.0
            total_expense = 35200.0 + round(sum(changes.values()) * 1000, 2)
            preview_by_output_path[self.output_path] = {
                "sheet": "Budget",
                "headers": ["Account Code", "Line Item", "YTD Actual", "Annual Budget",
                             "% Change", "Proposed Budget", "Monthly", "Notes"],
                "rows": [
                    ["", "Total Income", None, total_income, None, total_income, round(total_income / 12, 2), None],
                    ["", "Total Expense", None, total_expense, None, total_expense, round(total_expense / 12, 2), None],
                    ["", "Net Operating Income", None, total_income - total_expense, None, total_income - total_expense, round((total_income - total_expense) / 12, 2), None],
                ],
            }
            Path(self.output_path).write_text(str(total_expense), encoding="utf-8")

    def percent_aware_preview(path: str, max_rows: int):
        return preview_by_output_path[path]

    def capture_percent_changes(path: str, sheet: str, changes):
        changes_by_input_path[path] = dict(changes)

    monkeypatch.setattr(
        "app.services.budget_history_service.BudgetPipeline",
        PercentAwarePipeline,
    )
    monkeypatch.setattr(
        "app.services.budget_history_service.macros_service.read_first_sheet_preview",
        percent_aware_preview,
    )
    monkeypatch.setattr(
        "app.services.budget_history_service.macros_service.write_percent_changes_by_label",
        capture_percent_changes,
    )

    upload_response = _upload_income_statement(client)
    assert upload_response.status_code == 200
    draft = upload_response.json()["draft"]

    baseline_generate = _generate_version(client, draft, global_note="Baseline")
    assert baseline_generate.status_code == 200
    baseline_version = baseline_generate.json()["version"]
    baseline_download = client.post(f"/hoa/1/versions/{baseline_version['id']}/download")
    assert baseline_download.status_code == 200

    second_upload = _upload_income_statement(client)
    assert second_upload.status_code == 200
    second_draft = second_upload.json()["draft"]
    edited_items = [{**item} for item in second_draft["line_items"]]
    edited_items[1]["percent_change"] = 250

    save_response = client.patch(
        "/hoa/1/budget/draft",
        json={
            "draft_id": second_draft["id"],
            "line_items": edited_items,
            "global_note": "Edited percent change",
            "statement_month": second_draft["statement_month"],
            "growth_factor": second_draft["growth_factor"],
            "growth_factor_note": second_draft["growth_factor_note"],
        },
    )
    assert save_response.status_code == 200

    edited_generate = client.post(
        "/hoa/1/budget/generate",
        json={
            "draft_id": second_draft["id"],
            "line_items": edited_items,
            "global_note": "Edited percent change",
        },
    )
    assert edited_generate.status_code == 200
    edited_version = edited_generate.json()["version"]
    edited_download = client.post(f"/hoa/1/versions/{edited_version['id']}/download")
    assert edited_download.status_code == 200

    assert edited_version["total_expense"] > baseline_version["total_expense"]
    assert edited_version["net_operating_income"] < baseline_version["net_operating_income"]
    assert baseline_download.content != edited_download.content


def test_non_active_draft_mutations_are_rejected(client, budget_history_test_harness):
    upload_response = _upload_income_statement(client)
    assert upload_response.status_code == 200
    draft = upload_response.json()["draft"]

    generate_response = _generate_version(client, draft, global_note="Initial version")
    assert generate_response.status_code == 200

    save_response = client.patch(
        "/hoa/1/budget/draft",
        json={
            "draft_id": draft["id"],
            "line_items": draft["line_items"],
            "global_note": "Should not save",
            "statement_month": draft["statement_month"],
            "growth_factor": draft["growth_factor"],
            "growth_factor_note": draft["growth_factor_note"],
        },
    )
    assert save_response.status_code == 409

    note_response = client.post(
        "/hoa/1/budget/notes",
        json={
            "draft_id": draft["id"],
            "note_scope": "global",
            "title": "Should fail",
            "body": "Old generated drafts should not accept notes.",
        },
    )
    assert note_response.status_code == 409

    regenerate_response = client.post(
        "/hoa/1/budget/generate",
        json={
            "draft_id": draft["id"],
            "line_items": draft["line_items"],
            "global_note": "Should not generate",
        },
    )
    assert regenerate_response.status_code == 409


def test_requested_draft_id_loads_exact_active_draft(client, budget_history_test_harness):
    first_upload = _upload_income_statement(client)
    assert first_upload.status_code == 200
    first_draft = first_upload.json()["draft"]

    second_upload = _upload_income_statement(client)
    assert second_upload.status_code == 200
    second_draft = second_upload.json()["draft"]

    active_response = client.get("/hoa/1/budget/draft")
    assert active_response.status_code == 200
    assert active_response.json()["id"] == second_draft["id"]

    exact_active_response = client.get(f"/hoa/1/budget/drafts/{second_draft['id']}")
    assert exact_active_response.status_code == 200
    assert exact_active_response.json()["id"] == second_draft["id"]

    superseded_response = client.get(f"/hoa/1/budget/drafts/{first_draft['id']}")
    assert superseded_response.status_code == 409


def test_download_enriched_draft_artifact_supports_active_generated_and_superseded_statuses(
    client,
    budget_history_test_harness,
):
    first_upload = _upload_income_statement(client)
    assert first_upload.status_code == 200
    first_draft = first_upload.json()["draft"]

    active_download = client.get(f"/hoa/1/budget/drafts/{first_draft['id']}/download-enriched")
    assert active_download.status_code == 200

    second_upload = _upload_income_statement(client)
    assert second_upload.status_code == 200
    second_draft = second_upload.json()["draft"]

    superseded_download = client.get(f"/hoa/1/budget/drafts/{first_draft['id']}/download-enriched")
    assert superseded_download.status_code == 200

    generate_response = _generate_version(client, second_draft, global_note="Generated status draft")
    assert generate_response.status_code == 200

    generated_download = client.get(f"/hoa/1/budget/drafts/{second_draft['id']}/download-enriched")
    assert generated_download.status_code == 200

    history_response = client.get("/hoa/1/history")
    assert history_response.status_code == 200
    timeline_types = [event["event_type"] for event in history_response.json()["timeline"]]
    assert timeline_types.count("draft_enriched_downloaded") == 3


def test_save_draft_refreshes_persisted_enriched_download(
    client,
    budget_history_test_harness,
    monkeypatch,
):
    enriched_bytes_by_output_path = {}
    changes_by_input_path = {}

    class PercentAwarePipeline:
        def __init__(self, **kwargs):
            self.input_path = kwargs["input_path"]
            self.intermediate_path = kwargs["intermediate_path"]
            self.output_path = kwargs["output_path"]

        def run(self):
            changes = changes_by_input_path.get(self.input_path, {})
            total_expense = 35200.0 + round(sum(changes.values()) * 1000, 2)
            enriched_bytes = f"enriched:{sorted(changes.items())}".encode("utf-8")
            enriched_bytes_by_output_path[self.intermediate_path] = enriched_bytes
            Path(self.intermediate_path).write_bytes(enriched_bytes)
            Path(self.output_path).write_text(str(total_expense), encoding="utf-8")

    def percent_aware_preview(path: str, max_rows: int):
        total_expense = float(Path(path).read_text(encoding="utf-8"))
        total_income = 126000.0
        return {
            "sheet": "Budget",
            "headers": ["Account Code", "Line Item", "YTD Actual", "Annual Budget",
                         "% Change", "Proposed Budget", "Monthly", "Notes"],
            "rows": [
                ["", "Total Income", None, total_income, None, total_income, round(total_income / 12, 2), None],
                ["", "Total Expense", None, total_expense, None, total_expense, round(total_expense / 12, 2), None],
                ["", "Net Operating Income", None, total_income - total_expense, None, total_income - total_expense, round((total_income - total_expense) / 12, 2), None],
            ],
        }

    def capture_percent_changes(path: str, sheet: str, changes):
        changes_by_input_path[path] = dict(changes)

    monkeypatch.setattr(
        "app.services.budget_history_service.BudgetPipeline",
        PercentAwarePipeline,
    )
    monkeypatch.setattr(
        "app.services.budget_history_service.macros_service.read_first_sheet_preview",
        percent_aware_preview,
    )
    monkeypatch.setattr(
        "app.services.budget_history_service.macros_service.write_percent_changes_by_label",
        capture_percent_changes,
    )

    upload_response = _upload_income_statement(client)
    assert upload_response.status_code == 200
    draft = upload_response.json()["draft"]

    baseline_download = client.get(f"/hoa/1/budget/drafts/{draft['id']}/download-enriched")
    assert baseline_download.status_code == 200

    edited_items = [{**item} for item in draft["line_items"]]
    edited_items[1]["percent_change"] = 250

    save_response = client.patch(
        "/hoa/1/budget/draft",
        json={
            "draft_id": draft["id"],
            "line_items": edited_items,
            "global_note": "Edited percent change",
            "statement_month": draft["statement_month"],
            "growth_factor": draft["growth_factor"],
            "growth_factor_note": draft["growth_factor_note"],
        },
    )
    assert save_response.status_code == 200
    assert save_response.json()["draft"]["enriched_file_available"] is True

    edited_download = client.get(f"/hoa/1/budget/drafts/{draft['id']}/download-enriched")
    assert edited_download.status_code == 200

    assert edited_download.content != baseline_download.content


def test_legacy_draft_download_lazily_rebuilds_missing_enriched_artifact(
    client,
    budget_history_test_harness,
):
    upload_response = _upload_income_statement(client)
    assert upload_response.status_code == 200
    draft = upload_response.json()["draft"]

    initial_history = client.get("/hoa/1/history")
    assert initial_history.status_code == 200
    initial_draft_summary = next(
        item for item in initial_history.json()["drafts"] if item["id"] == draft["id"]
    )
    assert initial_draft_summary["enriched_file_available"] is True

    draft_path = (
        Path(budget_history_test_harness["storage_root"])
        / "hoa"
        / "1"
        / "drafts"
        / str(draft["id"])
        / "enriched.xlsx"
    )
    assert draft_path.exists()

    db_path = Path(client.app.state.test_db_path)
    with sqlite3.connect(db_path) as connection:
      connection.execute(
          "UPDATE budget_drafts SET enriched_storage_key = NULL WHERE id = ?",
          (draft["id"],),
      )
      connection.commit()
    draft_path.unlink()

    rebuild_download = client.get(f"/hoa/1/budget/drafts/{draft['id']}/download-enriched")
    assert rebuild_download.status_code == 200
    assert draft_path.exists()

    history_response = client.get("/hoa/1/history")
    assert history_response.status_code == 200
    rebuilt_summary = next(
        item for item in history_response.json()["drafts"] if item["id"] == draft["id"]
    )
    assert rebuilt_summary["enriched_file_available"] is True


def test_compare_and_reopen_preserve_history(client, budget_history_test_harness):
    upload_response = _upload_income_statement(client)
    assert upload_response.status_code == 200
    initial_draft = upload_response.json()["draft"]

    first_generate_response = _generate_version(
        client,
        initial_draft,
        global_note="Initial interim version",
    )
    assert first_generate_response.status_code == 200
    first_version = first_generate_response.json()["version"]

    second_upload_response = _upload_income_statement(client)
    assert second_upload_response.status_code == 200
    second_draft = second_upload_response.json()["draft"]

    second_generate_response = _generate_version(
        client,
        second_draft,
        global_note="Second interim version",
    )
    assert second_generate_response.status_code == 200
    second_version = second_generate_response.json()["version"]

    duplicate_compare = client.get(
        f"/hoa/1/versions/compare?left={first_version['id']}&right={first_version['id']}"
    )
    assert duplicate_compare.status_code == 400

    cross_hoa_compare = client.get(
        f"/hoa/1/versions/compare?left={first_version['id']}&right=99999"
    )
    assert cross_hoa_compare.status_code in {400, 404}

    compare_response = client.get(
        f"/hoa/1/versions/compare?left={first_version['id']}&right={second_version['id']}"
    )
    assert compare_response.status_code == 200
    compare_payload = compare_response.json()

    assert [version["id"] for version in compare_payload["versions"]] == [
        first_version["id"],
        second_version["id"],
    ]
    for version in compare_payload["versions"]:
        assert set(version) >= {
            "id",
            "version_code",
            "stage",
            "label",
            "created_at",
            "created_by_name",
            "source_upload_filename",
            "total_income",
            "total_expense",
            "net_operating_income",
            "growth_factor",
            "growth_factor_note",
            "statement_month",
            "fiscal_year_start_month",
            "fiscal_year_end_month",
        }

    reopen_response = client.post(f"/hoa/1/versions/{first_version['id']}/reopen")
    assert reopen_response.status_code == 200
    reopen_payload = reopen_response.json()

    assert reopen_payload["draft"]["status"] == "active"
    assert reopen_payload["draft"]["reopened_from_version_id"] == first_version["id"]
    assert reopen_payload["timeline_event"]["event_type"] == "version_reopened"
    assert reopen_payload["timeline_event"]["payload"]["source_version_id"] == first_version["id"]
    assert reopen_payload["timeline_event"]["payload"]["new_draft_id"] == reopen_payload["draft"]["id"]

    history_response = client.get("/hoa/1/history")
    assert history_response.status_code == 200
    history_payload = history_response.json()

    assert history_payload["active_draft"]["id"] == reopen_payload["draft"]["id"]
    assert [version["id"] for version in history_payload["versions"]] == [
        second_version["id"],
        first_version["id"],
    ]
    reopened_event = next(
        event for event in history_payload["timeline"] if event["event_type"] == "version_reopened"
    )
    assert reopened_event["payload"]["source_version_id"] == first_version["id"]
    assert reopened_event["payload"]["new_draft_id"] == reopen_payload["draft"]["id"]


def test_version_metadata_update_and_download_are_audited(client, budget_history_test_harness):
    upload_response = _upload_income_statement(client)
    assert upload_response.status_code == 200
    draft = upload_response.json()["draft"]

    generate_response = _generate_version(
        client,
        draft,
        global_note="Ready for metadata update",
    )
    assert generate_response.status_code == 200
    version = generate_response.json()["version"]

    metadata_response = client.patch(
        f"/hoa/1/versions/{version['id']}",
        json={
            "stage": "Final",
            "label": "Board Review Draft",
            "summary_note": "Ready for board packet download.",
        },
    )
    assert metadata_response.status_code == 200
    metadata_payload = metadata_response.json()
    assert metadata_payload["version"]["stage"] == "Final"
    assert metadata_payload["version"]["label"] == "Board Review Draft"
    assert metadata_payload["version"]["summary_note"] == "Ready for board packet download."

    invalid_metadata_response = client.patch(
        f"/hoa/1/versions/{version['id']}",
        json={"version_code": "V99"},
    )
    assert invalid_metadata_response.status_code == 422

    download_response = client.post(f"/hoa/1/versions/{version['id']}/download")
    assert download_response.status_code == 200
    assert (
        download_response.headers["content-type"]
        == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert download_response.content

    version_response = client.get(f"/hoa/1/versions/{version['id']}")
    assert version_response.status_code == 200
    version_payload = version_response.json()
    assert version_payload["stage"] == "Final"
    assert version_payload["label"] == "Board Review Draft"
    assert version_payload["summary_note"] == "Ready for board packet download."

    history_response = client.get("/hoa/1/history")
    assert history_response.status_code == 200
    history_payload = history_response.json()
    assert {event["event_type"] for event in history_payload["timeline"]} >= {
        "version_marked_final",
        "file_downloaded",
    }
