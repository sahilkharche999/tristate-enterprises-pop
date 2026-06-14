from app.ai_implementation.db import BUDGET_DRAFT_ACTIVE


def _upload_budget_with_mode(
    client,
    *,
    assessment_mode: str,
    source_mode: str = "income_statement",
):
    return client.post(
        "/hoa/1/budget/upload",
        data={
            "source_mode": source_mode,
            "assessment_mode": assessment_mode,
        },
        files={
            "file": (
                "income-statement.xlsx",
                b"placeholder workbook bytes",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )


def _generate_version(client, draft):
    return client.post(
        "/hoa/1/budget/generate",
        json={
            "draft_id": draft["id"],
            "line_items": draft["line_items"],
            "global_note": "Generated for assessment-mode test",
        },
    )


def test_budget_upload_persists_assessment_mode_and_updates_hoa(
    client,
    budget_history_test_harness,
    db_session,
):
    response = _upload_budget_with_mode(client, assessment_mode="fixed")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["draft"]["assessment_mode"] == "fixed"

    raw = db_session.connection().connection
    assert raw.execute(
        "SELECT assessment_mode FROM properties WHERE id = 1"
    ).fetchone()[0] == "fixed"
    assert raw.execute(
        "SELECT assessment_mode FROM budget_uploads WHERE property_id = 1 ORDER BY id DESC LIMIT 1"
    ).fetchone()[0] == "fixed"
    assert raw.execute(
        "SELECT assessment_mode FROM budget_drafts WHERE property_id = 1 AND status = ?",
        (BUDGET_DRAFT_ACTIVE,),
    ).fetchone()[0] == "fixed"

    hoa_response = client.get("/hoa/1")
    assert hoa_response.status_code == 200
    assert hoa_response.json()["assessment_mode"] == "fixed"


def test_generated_versions_and_reopened_drafts_preserve_assessment_mode(
    client,
    budget_history_test_harness,
):
    upload_response = _upload_budget_with_mode(client, assessment_mode="fixed")
    assert upload_response.status_code == 200, upload_response.text
    draft = upload_response.json()["draft"]

    generate_response = _generate_version(client, draft)
    assert generate_response.status_code == 200, generate_response.text
    generated_payload = generate_response.json()
    version = generated_payload["version"]

    assert generated_payload["draft"]["assessment_mode"] == "fixed"
    assert version["assessment_mode"] == "fixed"

    history_response = client.get("/hoa/1/history")
    assert history_response.status_code == 200, history_response.text
    history_payload = history_response.json()
    assert history_payload["versions"][0]["assessment_mode"] == "fixed"

    reopen_response = client.post(f"/hoa/1/versions/{version['id']}/reopen")
    assert reopen_response.status_code == 200, reopen_response.text
    reopened_payload = reopen_response.json()
    assert reopened_payload["draft"]["assessment_mode"] == "fixed"

    refreshed_history = client.get("/hoa/1/history")
    assert refreshed_history.status_code == 200
    assert refreshed_history.json()["active_draft"]["assessment_mode"] == "fixed"
