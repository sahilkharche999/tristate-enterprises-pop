"""Bug 2 — replace the budget source file without deleting the package/draft.

Exercises the new POST /hoa/{id}/budget/drafts/{draft_id}/source/upload:
- works after a version was generated (the exact case Discard 409s on),
- preserves the attached reserve study + draft settings,
- leaves previously generated versions intact,
- leaves the property assessment mode unchanged,
- guards: non-active draft -> 409, missing draft -> 404.
"""
from __future__ import annotations

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _upload(client, filename="income-statement.xlsx"):
    return client.post(
        "/hoa/1/budget/upload",
        data={"source_mode": "income_statement"},
        files={"file": (filename, b"placeholder bytes", XLSX)},
    )


def _replace(client, draft_id, filename="income-statement-v2.xlsx"):
    return client.post(
        f"/hoa/1/budget/drafts/{draft_id}/source/upload",
        files={"file": (filename, b"placeholder bytes", XLSX)},
    )


def _generate_version(client, draft_id):
    return client.post(
        "/hoa/1/budget/generate",
        json={"draft_id": draft_id, "line_items": [], "global_note": None},
    )


def _active_draft_row(db_session, hoa_id=1):
    raw = db_session.connection().connection
    return raw.execute(
        "SELECT id, status, reserve_study_status, reserve_study_upload_id, "
        "global_note FROM budget_drafts WHERE property_id = ? AND status = 'active' "
        "ORDER BY updated_at DESC LIMIT 1",
        (hoa_id,),
    ).fetchone()


def test_replace_source_after_version_generated(client, budget_history_test_harness, db_session):
    up = _upload(client)
    assert up.status_code == 200, up.text
    draft_id = up.json()["draft"]["id"]

    gen = _generate_version(client, draft_id)
    assert gen.status_code == 200, gen.text
    raw = db_session.connection().connection
    versions_before = raw.execute("SELECT COUNT(*) FROM budget_versions").fetchone()[0]
    assert versions_before == 1

    # Replace the source file — no deletion needed, succeeds even though a
    # version has been generated (the case the old flow could only escape by
    # deleting the package/HOA).
    rep = _replace(client, draft_id)
    assert rep.status_code == 200, rep.text
    body = rep.json()
    assert body["review_required"] is False
    assert body["draft"]["status"] == "active"

    # The previously generated version is untouched.
    versions_after = db_session.connection().connection.execute(
        "SELECT COUNT(*) FROM budget_versions"
    ).fetchone()[0]
    assert versions_after == versions_before

    # An active draft still exists (the current budget now reflects the new file).
    row = _active_draft_row(db_session)
    assert row is not None and row[1] == "active"


def test_replace_preserves_reserve_study_and_notes(client, budget_history_test_harness, db_session):
    up = _upload(client)
    draft_id = up.json()["draft"]["id"]

    # Simulate an attached reserve study + operator note on the draft.
    raw = db_session.connection().connection
    raw.execute(
        "UPDATE budget_drafts SET reserve_study_status = 'completed', "
        "reserve_study_rows_json = ?, global_note = ? WHERE id = ?",
        ('[{"line_item": "Roof"}]', "keep me", draft_id),
    )
    db_session.connection().connection.commit()

    rep = _replace(client, draft_id)
    assert rep.status_code == 200, rep.text

    row = _active_draft_row(db_session)
    # reserve_study_status + note carried onto the rebuilt active draft.
    assert row[2] == "completed"
    assert row[4] == "keep me"


def test_replace_does_not_change_property_assessment_mode(client, budget_history_test_harness, db_session):
    up = _upload(client)
    draft_id = up.json()["draft"]["id"]
    raw = db_session.connection().connection
    mode_before = raw.execute("SELECT assessment_mode FROM properties WHERE id = 1").fetchone()[0]

    rep = _replace(client, draft_id)
    assert rep.status_code == 200, rep.text

    mode_after = db_session.connection().connection.execute(
        "SELECT assessment_mode FROM properties WHERE id = 1"
    ).fetchone()[0]
    assert mode_after == mode_before


def test_replace_missing_draft_returns_404(client, budget_history_test_harness):
    resp = _replace(client, 999999)
    assert resp.status_code == 404, resp.text


def test_replace_superseded_draft_returns_409(client, budget_history_test_harness, db_session):
    up = _upload(client)
    draft_id = up.json()["draft"]["id"]
    # A second upload supersedes the first draft.
    up2 = _upload(client)
    assert up2.status_code == 200
    # Replacing against the now-superseded draft id is rejected.
    resp = _replace(client, draft_id)
    assert resp.status_code == 409, resp.text
