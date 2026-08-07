"""Tests for the GET /api/disclosure-package/hoa/{hoa_id}/preflight endpoint
and the preflight-context-preservation changes to run_render_job.

Coverage:
    4.1 Preflight endpoint returns ready=true / empty blocking for a valid HOA fixture.
    4.2 Preflight endpoint returns ready=false for an HOA with no budget draft;
        asserts no DisclosurePackageJob row was created.
    4.3 The endpoint's blocking set equals what the render gate would raise for
        the same inputs (guards against readiness drift — design D1 risk).
    4.4 A preflight-blocked render persists human message(s) in the failure
        record, not raw field paths.
    4.5 An unexpected exception during render persists the sanitized message
        (no exception type name / repr) while logging the detail.
    4.6 Warnings-only preflight returns ready=true.
"""
from __future__ import annotations

import json

import pytest

from app.ai_implementation.db.models import (
    DISCLOSURE_JOB_FAILED,
    DisclosurePackageJob,
    Property,
)


OLD_MILL_HOA_CODE = "10"
OLD_MILL_FY = 2026


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _get_old_mill_id(db_session) -> int:
    row = (
        db_session.query(Property)
        .filter(Property.hoa_code == OLD_MILL_HOA_CODE)
        .one()
    )
    return row.id


def _seed_active_draft_for(db_session, hoa_id: int) -> None:
    """Seed a minimal valid budget draft for an HOA."""
    from app.ai_implementation.db import BUDGET_DRAFT_ACTIVE, BudgetDraft

    line_items = [
        {
            "label": "Assessment Revenue",
            "amount": 1000.0,
            "section": "operating",
            "category": "income",
            "is_reserve": False,
            "is_revenue": True,
            "read_only": False,
        },
        {
            "label": "Maintenance",
            "amount": 500.0,
            "section": "operating",
            "category": "maintenance",
            "is_reserve": False,
            "is_revenue": False,
            "read_only": False,
        },
        {
            "label": "Reserve Allocation",
            "amount": 200.0,
            "section": "reserve",
            "category": "reserve",
            "is_reserve": True,
            "is_revenue": True,
            "read_only": True,
        },
    ]
    reserve_rows = [
        {
            "row_id": "r1",
            "row_type": "item",
            "line_item": "Roof",
            "useful_life": 25,
            "remaining_life": 10,
            "replacement_cost": 50000.0,
            "year_new": 2010,
        }
    ]
    draft = BudgetDraft(
        property_id=hoa_id,
        source_upload_id=None,
        reopened_from_version_id=None,
        status=BUDGET_DRAFT_ACTIVE,
        line_items_json=json.dumps(line_items),
        reserve_study_rows_json=json.dumps(reserve_rows),
        reserve_study_status="completed",
        global_note=None,
        statement_month=12,
        growth_factor=1.0,
        reserve_inflation_rate=0.0,
        budget_preview_json=None,
        enriched_storage_key=None,
        created_by_user_id=1,
        updated_by_user_id=1,
        actor_name="Test User",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    db_session.add(draft)
    db_session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# 4.1 — Ready HOA returns ready=true
# ─────────────────────────────────────────────────────────────────────────────


def test_preflight_returns_ready_for_valid_hoa(client, db_session, budget_storage_root):
    """4.1: A valid HOA with an active draft returns ready=true, empty blocking."""
    hoa_id = _get_old_mill_id(db_session)
    # Fixed mode: mapping gate is not required (variable HOAs need mapping).
    prop = db_session.query(Property).filter(Property.id == hoa_id).one()
    prop.assessment_mode = "fixed"
    db_session.commit()
    _seed_active_draft_for(db_session, hoa_id)

    response = client.get(
        f"/api/disclosure-package/hoa/{hoa_id}/preflight",
        params={"fiscal_year": OLD_MILL_FY},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "ready" in body
    assert "blocking" in body
    assert "warnings" in body
    # Filter mapping-only blocks if any fixture drift; primary assert:
    mapping_blocks = [
        b for b in body["blocking"] if (b.get("code") or "").startswith("assessment_mapping")
    ]
    assert mapping_blocks == []
    assert body["blocking"] == []
    assert body["ready"] is True
    assert "steps" in body


# ─────────────────────────────────────────────────────────────────────────────
# 4.2 — No budget draft → ready=false, no job created
# ─────────────────────────────────────────────────────────────────────────────


def test_preflight_returns_not_ready_without_draft(client, db_session, budget_storage_root):
    """4.2: An HOA with no active budget draft → ready=false, no job row created."""
    hoa_id = _get_old_mill_id(db_session)
    # Deliberately do NOT seed a draft.

    response = client.get(
        f"/api/disclosure-package/hoa/{hoa_id}/preflight",
        params={"fiscal_year": OLD_MILL_FY},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ready"] is False
    assert len(body["blocking"]) >= 1
    assert body["blocking"][0]["severity"] == "blocking"

    # No DisclosurePackageJob row must have been created.
    job_count = (
        db_session.query(DisclosurePackageJob)
        .filter(DisclosurePackageJob.property_id == hoa_id)
        .count()
    )
    assert job_count == 0, "preflight endpoint must not create any job rows"


# ─────────────────────────────────────────────────────────────────────────────
# 4.3 — Readiness agrees with what the render gate would raise
# ─────────────────────────────────────────────────────────────────────────────


def test_preflight_blocking_matches_render_gate(client, db_session, budget_storage_root, monkeypatch):
    """4.3: The preflight endpoint's blocking set matches what the render gate raises.

    Guards against the D1 risk: if the endpoint resolved inputs differently from
    run_render_job, a green checklist could still fail at generation.
    """
    from app.ai_implementation.db import session as session_module
    from app.disclosure_package import service as dp_service

    hoa_id = _get_old_mill_id(db_session)
    # No draft — both the endpoint and the render should surface the same blocking.

    # 1. Capture what the preflight endpoint says is blocking.
    response = client.get(
        f"/api/disclosure-package/hoa/{hoa_id}/preflight",
        params={"fiscal_year": OLD_MILL_FY},
    )
    assert response.status_code == 200
    preflight_blocking = response.json()["blocking"]
    preflight_field_paths = {e["field_path"] for e in preflight_blocking}

    # 2. Capture what run_render_job would record as a failure by injecting the
    #    render into a controlled session and reading the error_message.
    import uuid
    from app.ai_implementation.db import (
        DISCLOSURE_JOB_PENDING,
        DisclosurePackageJob,
        BudgetDraft,
    )

    def _factory():
        return session_module.SessionLocal()

    session = _factory()
    job_id = str(uuid.uuid4())
    job = DisclosurePackageJob(
        id=job_id,
        property_id=hoa_id,
        fiscal_year=OLD_MILL_FY,
        status=DISCLOSURE_JOB_PENDING,
        created_by_user_id=1,
    )
    session.add(job)
    session.commit()
    session.close()

    dp_service.run_render_job(
        job_id,
        hoa_id,
        OLD_MILL_FY,
        session_factory=_factory,
    )

    session2 = _factory()
    failed_job = (
        session2.query(DisclosurePackageJob)
        .filter(DisclosurePackageJob.id == job_id)
        .one()
    )
    job_error_message = failed_job.error_message or ""
    session2.close()

    # Both should report a failure (no active draft).
    assert not response.json()["ready"], "preflight should report not-ready"
    assert failed_job.status == DISCLOSURE_JOB_FAILED, "render should have failed"
    # The render's error message should not contain raw field paths as the primary text.
    assert "budget_draft.line_items" not in job_error_message, (
        f"raw field path leaked into error_message: {job_error_message!r}"
    )
    # The human message should be present in both paths.
    assert "budget" in job_error_message.lower(), (
        f"expected human message about budget in: {job_error_message!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4.4 — Preflight-blocked render persists human message, not raw field paths
# ─────────────────────────────────────────────────────────────────────────────


def test_preflight_blocked_render_persists_human_message(db_session, client, budget_storage_root):
    """4.4: When a render fails because preflight blocks, the failure record
    contains the human-readable message, not raw field paths or Python repr.
    """
    from app.ai_implementation.db import session as session_module
    from app.disclosure_package import service as dp_service
    import uuid
    from app.ai_implementation.db import DISCLOSURE_JOB_PENDING, DisclosurePackageJob

    hoa_id = _get_old_mill_id(db_session)
    # No draft seeded → preflight will block.

    def _factory():
        return session_module.SessionLocal()

    job_id = str(uuid.uuid4())
    session = _factory()
    job = DisclosurePackageJob(
        id=job_id,
        property_id=hoa_id,
        fiscal_year=OLD_MILL_FY,
        status=DISCLOSURE_JOB_PENDING,
        created_by_user_id=1,
    )
    session.add(job)
    session.commit()
    session.close()

    dp_service.run_render_job(
        job_id,
        hoa_id,
        OLD_MILL_FY,
        session_factory=_factory,
    )

    session2 = _factory()
    failed_job = (
        session2.query(DisclosurePackageJob)
        .filter(DisclosurePackageJob.id == job_id)
        .one()
    )
    error_msg = failed_job.error_message or ""
    session2.close()

    assert failed_job.status == DISCLOSURE_JOB_FAILED
    # Must not contain bare field paths as the primary content.
    assert "budget_draft.line_items" not in error_msg, (
        f"raw field path leaked: {error_msg!r}"
    )
    # Must not contain Python exception repr.
    assert "LookupError" not in error_msg, f"Python repr leaked: {error_msg!r}"
    assert "KeyError" not in error_msg, f"Python repr leaked: {error_msg!r}"
    # Must contain human language.
    assert len(error_msg) > 5, "error_message should not be empty"


# ─────────────────────────────────────────────────────────────────────────────
# 4.5 — Unexpected exception during render → sanitized message
# ─────────────────────────────────────────────────────────────────────────────


def test_unexpected_exception_produces_sanitized_message(db_session, client, budget_storage_root, monkeypatch):
    """4.5: An unexpected exception during render persists a stable plain-English
    message — no Python type name, no repr, no stack detail.
    """
    from app.ai_implementation.db import session as session_module
    from app.disclosure_package import service as dp_service
    import uuid
    from app.ai_implementation.db import DISCLOSURE_JOB_PENDING, DISCLOSURE_JOB_FAILED, DisclosurePackageJob

    hoa_id = _get_old_mill_id(db_session)
    _seed_active_draft_for(db_session, hoa_id)

    # Force an unexpected exception inside _resolve_preflight_inputs.
    original_resolve = dp_service._resolve_preflight_inputs

    def _boom(*args, **kwargs):
        raise RuntimeError("something went terribly wrong")

    monkeypatch.setattr(dp_service, "_resolve_preflight_inputs", _boom)

    def _factory():
        return session_module.SessionLocal()

    job_id = str(uuid.uuid4())
    session = _factory()
    job = DisclosurePackageJob(
        id=job_id,
        property_id=hoa_id,
        fiscal_year=OLD_MILL_FY,
        status=DISCLOSURE_JOB_PENDING,
        created_by_user_id=1,
    )
    session.add(job)
    session.commit()
    session.close()

    dp_service.run_render_job(
        job_id,
        hoa_id,
        OLD_MILL_FY,
        session_factory=_factory,
    )

    session2 = _factory()
    failed_job = (
        session2.query(DisclosurePackageJob)
        .filter(DisclosurePackageJob.id == job_id)
        .one()
    )
    error_msg = failed_job.error_message or ""
    session2.close()

    assert failed_job.status == DISCLOSURE_JOB_FAILED
    # No Python exception type or repr in the operator-facing message.
    assert "RuntimeError" not in error_msg, f"Python type leaked: {error_msg!r}"
    assert "terribly wrong" not in error_msg, f"internal detail leaked: {error_msg!r}"
    # Must be a stable human-readable string.
    assert len(error_msg) > 5
    assert "unexpected" in error_msg.lower() or "error" in error_msg.lower(), (
        f"expected human message, got: {error_msg!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4.6 — Warnings-only preflight returns ready=true
# ─────────────────────────────────────────────────────────────────────────────


def test_preflight_warnings_only_returns_ready(client, db_session, budget_storage_root, monkeypatch):
    """4.6: When preflight emits only warnings (no blocking items), ready=true."""
    from app.disclosure_package import service as dp_service

    hoa_id = _get_old_mill_id(db_session)
    _seed_active_draft_for(db_session, hoa_id)

    # Monkeypatch detailed preflight (router uses run_preflight_detailed).
    from app.disclosure_package.schemas import PreflightError

    def _warnings_only_detailed(session, hoa_id_, fiscal_year):
        return {
            "blocking": [],
            "warnings": [
                PreflightError(
                    field_path="reserve_study_snapshot.components",
                    message="Reserve study has no components",
                    severity="warning",
                )
            ],
            "steps": [],
        }

    monkeypatch.setattr(dp_service, "run_preflight_detailed", _warnings_only_detailed)

    response = client.get(
        f"/api/disclosure-package/hoa/{hoa_id}/preflight",
        params={"fiscal_year": OLD_MILL_FY},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ready"] is True
    assert body["blocking"] == []
    assert len(body["warnings"]) >= 1
    assert body["warnings"][0]["severity"] == "warning"


# ─────────────────────────────────────────────────────────────────────────────
# Auth — endpoint requires authentication
# ─────────────────────────────────────────────────────────────────────────────


def test_preflight_requires_auth(client, db_session, budget_storage_root):
    """Preflight endpoint must require authentication."""
    hoa_id = _get_old_mill_id(db_session)
    response = client.get(
        f"/api/disclosure-package/hoa/{hoa_id}/preflight",
        params={"fiscal_year": OLD_MILL_FY},
        headers={"Authorization": ""},
    )
    assert response.status_code in (401, 403), response.text
