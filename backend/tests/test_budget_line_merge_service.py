from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.services.budget_line_merge_service import (
    GLIdentity,
    auto_apply_merges_on_upload,
    commit_merge,
    finalize_applied_merges,
    list_merges,
    suggest_merges,
    unmerge_merge,
)


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"
)


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute("INSERT INTO properties (name, units) VALUES ('A', 10)")
    property_id = conn.execute("SELECT id FROM properties").fetchone()[0]
    conn.execute(
        """
        INSERT INTO assessment_setups (property_id, setup_type, display_mode, status)
        VALUES (?, 'fixed', 'fixed', 'approved')
        """,
        (property_id,),
    )
    setup_id = conn.execute("SELECT id FROM assessment_setups").fetchone()[0]
    conn.execute(
        "UPDATE properties SET default_assessment_setup_id = ? WHERE id = ?",
        (setup_id, property_id),
    )
    yield conn
    conn.close()


def _ids(db: sqlite3.Connection) -> tuple[int, int]:
    property_id = db.execute("SELECT id FROM properties").fetchone()[0]
    setup_id = db.execute("SELECT id FROM assessment_setups").fetchone()[0]
    return property_id, setup_id


def _primary() -> GLIdentity:
    return GLIdentity(
        account_code="5010",
        label="Elevators",
        normalized_label="elevators",
        line_item_key="5010",
        section="expense",
        category="operating",
        fund_type="operating",
    )


def _secondary() -> GLIdentity:
    return GLIdentity(
        account_code="5015",
        label="Elevator Service",
        normalized_label="elevator service",
        line_item_key="5015",
        section="expense",
        category="operating",
        fund_type="operating",
    )


def _line_items() -> list[dict[str, object]]:
    return [
        {
            "line_item_key": "5010",
            "account_code": "5010",
            "label": "Elevators",
            "normalized_label": "elevators",
            "section": "expense",
            "category": "operating",
            "fund_type": "operating",
            "current_period": 100.0,
            "ytd": 200.0,
            "annual_budget": 2400.0,
            "projection": 2500.0,
            "variance": 100.0,
        },
        {
            "line_item_key": "5015",
            "account_code": "5015",
            "label": "Elevator Service",
            "normalized_label": "elevator service",
            "section": "expense",
            "category": "operating",
            "fund_type": "operating",
            "current_period": 50.0,
            "ytd": 75.0,
            "annual_budget": 1800.0,
            "projection": 1900.0,
            "variance": 100.0,
        },
    ]


def _seed_draft(db: sqlite3.Connection, *, version: int = 0) -> int:
    property_id, _setup_id = _ids(db)
    db.execute(
        """
        INSERT INTO budget_drafts (
            property_id, status, line_items_json, version_int, actor_name
        )
        VALUES (?, 'active', ?, ?, 'tester')
        """,
        (property_id, json.dumps(_line_items(), sort_keys=True), version),
    )
    return int(db.execute("SELECT id FROM budget_drafts").fetchone()[0])


def _seed_side_effect_rows(db: sqlite3.Connection) -> None:
    property_id, setup_id = _ids(db)
    db.execute(
        """
        INSERT INTO budget_line_pool_mappings (
            property_id, assessment_setup_id, budget_line_normalized_label,
            section, category, fund_type, account_code, pool_key,
            approval_status, review_state, approved_by, active
        )
        VALUES (?, ?, 'elevator service', 'expense', 'operating', 'operating',
                '5015', 'variable_expense', 'approved', 'ready', 'bob', 1)
        """,
        (property_id, setup_id),
    )
    db.execute(
        """
        INSERT INTO assessment_mapping_aliases (
            property_id, assessment_setup_id, pool_key, dre_label,
            normalized_dre_label, budget_label, normalized_budget_label,
            account_code, approval_status, active, decided_by, decided_at
        )
        VALUES (?, ?, 'variable_expense', 'Elevator Service',
                'elevator service', 'Elevator Service', 'elevator service',
                '5015', 'approved', 1, 'bob', '2026-01-01T00:00:00')
        """,
        (property_id, setup_id),
    )


def test_commit_merge_rewrites_draft_and_side_effects(db: sqlite3.Connection):
    property_id, _setup_id = _ids(db)
    _seed_draft(db)
    _seed_side_effect_rows(db)

    result = commit_merge(
        property_id=property_id,
        primary=_primary(),
        secondary=_secondary(),
        source="manual",
        actor="bob",
        expected_draft_version=0,
        db_conn=db,
    )

    draft = db.execute(
        "SELECT line_items_json, version_int FROM budget_drafts WHERE status = 'active'"
    ).fetchone()
    line_items = json.loads(draft["line_items_json"])
    assert draft["version_int"] == 1
    assert [line["label"] for line in line_items] == ["Elevators"]
    assert line_items[0]["annual_budget"] == 4200.0
    assert line_items[0]["projection"] == 4400.0
    assert result.application.status == "applied"

    assert db.execute("SELECT COUNT(*) FROM budget_line_merges").fetchone()[0] == 1
    assert (
        db.execute("SELECT COUNT(*) FROM budget_line_merge_applications").fetchone()[0]
        == 1
    )

    mapping = db.execute(
        """
        SELECT budget_line_normalized_label, account_code, pool_key, active
          FROM budget_line_pool_mappings
        """
    ).fetchone()
    assert dict(mapping) == {
        "budget_line_normalized_label": "elevators",
        "account_code": "5010",
        "pool_key": "variable_expense",
        "active": 1,
    }

    alias = db.execute(
        """
        SELECT normalized_budget_label, account_code, approval_status, decided_by
          FROM assessment_mapping_aliases
        """
    ).fetchone()
    assert dict(alias) == {
        "normalized_budget_label": "elevators",
        "account_code": "5010",
        "approval_status": "approved",
        "decided_by": "bob",
    }

    assert (
        db.execute("SELECT event_type FROM budget_audit_events").fetchone()[0]
        == "merge_committed"
    )


def test_commit_merge_rejects_stale_draft_version(db: sqlite3.Connection):
    property_id, _setup_id = _ids(db)
    _seed_draft(db, version=7)

    with pytest.raises(HTTPException) as exc_info:
        commit_merge(
            property_id=property_id,
            primary=_primary(),
            secondary=_secondary(),
            source="manual",
            actor="bob",
            expected_draft_version=6,
            db_conn=db,
        )

    assert exc_info.value.status_code == 412
    assert db.execute("SELECT COUNT(*) FROM budget_line_merges").fetchone()[0] == 0
    assert db.execute("SELECT version_int FROM budget_drafts").fetchone()[0] == 7


def test_unmerge_restores_draft_and_side_effects(db: sqlite3.Connection):
    property_id, _setup_id = _ids(db)
    _seed_draft(db)
    _seed_side_effect_rows(db)
    before_draft = db.execute("SELECT line_items_json FROM budget_drafts").fetchone()[0]

    committed = commit_merge(
        property_id=property_id,
        primary=_primary(),
        secondary=_secondary(),
        source="manual",
        actor="bob",
        expected_draft_version=0,
        db_conn=db,
    )

    unmerge_merge(
        application_id=committed.application.id,
        actor="bob",
        expected_draft_version=1,
        db_conn=db,
    )

    draft = db.execute("SELECT line_items_json, version_int FROM budget_drafts").fetchone()
    assert draft["line_items_json"] == before_draft
    assert draft["version_int"] == 2
    assert (
        db.execute("SELECT status FROM budget_line_merge_applications").fetchone()[0]
        == "unmerged"
    )
    assert tuple(
        db.execute(
            "SELECT budget_line_normalized_label, account_code FROM budget_line_pool_mappings"
        ).fetchone()
    ) == ("elevator service", "5015")
    assert tuple(
        db.execute(
            "SELECT normalized_budget_label, account_code FROM assessment_mapping_aliases"
        ).fetchone()
    ) == ("elevator service", "5015")


def test_finalize_locks_application(db: sqlite3.Connection):
    property_id, _setup_id = _ids(db)
    draft_id = _seed_draft(db)
    committed = commit_merge(
        property_id=property_id,
        primary=_primary(),
        secondary=_secondary(),
        source="manual",
        actor="bob",
        expected_draft_version=0,
        db_conn=db,
    )

    finalized = finalize_applied_merges(
        property_id=property_id,
        budget_draft_id=draft_id,
        db_conn=db,
    )

    assert finalized == 1
    assert list_merges(property_id=property_id, db_conn=db)[0].application_status == "finalized"
    assert (
        db.execute("SELECT status FROM budget_line_merges").fetchone()[0]
        == "active"
    )
    with pytest.raises(HTTPException) as exc_info:
        unmerge_merge(
            application_id=committed.application.id,
            actor="bob",
            expected_draft_version=1,
            db_conn=db,
        )
    assert exc_info.value.status_code == 409


def _seed_durable_rule(db: sqlite3.Connection) -> int:
    property_id, _setup_id = _ids(db)
    db.execute(
        """
        INSERT INTO budget_line_merges (
            property_id, primary_account_code, primary_label,
            primary_normalized_label, secondary_account_code, secondary_label,
            secondary_normalized_label, decision_source, actor
        )
        VALUES (?, '5010', 'Elevators', 'elevators', '5015',
                'Elevator Service', 'elevator service', 'manual', 'bob')
        """,
        (property_id,),
    )
    return int(db.execute("SELECT id FROM budget_line_merges").fetchone()[0])


def test_auto_apply_uses_existing_rule_without_duplicate(db: sqlite3.Connection):
    property_id, _setup_id = _ids(db)
    draft_id = _seed_draft(db)
    merge_id = _seed_durable_rule(db)

    applied = auto_apply_merges_on_upload(
        property_id=property_id,
        budget_draft_id=draft_id,
        new_draft_line_items=_line_items(),
        db_conn=db,
    )

    assert applied == 1
    assert db.execute("SELECT COUNT(*) FROM budget_line_merges").fetchone()[0] == 1
    application = db.execute(
        "SELECT merge_id, source, match_strategy FROM budget_line_merge_applications"
    ).fetchone()
    assert tuple(application) == (merge_id, "auto_applied", "account_code")
    line_items = json.loads(
        db.execute("SELECT line_items_json FROM budget_drafts").fetchone()[0]
    )
    assert [line["label"] for line in line_items] == ["Elevators"]
    assert (
        db.execute("SELECT event_type FROM budget_audit_events").fetchone()[0]
        == "merge_auto_applied"
    )


def test_auto_apply_skips_ambiguous_label_match(db: sqlite3.Connection):
    property_id, _setup_id = _ids(db)
    ambiguous_items = [
        {**_line_items()[0], "account_code": None, "line_item_key": "a"},
        {**_line_items()[0], "account_code": None, "line_item_key": "b"},
        {**_line_items()[1], "account_code": None, "line_item_key": "c"},
    ]
    db.execute(
        """
        INSERT INTO budget_drafts (
            property_id, status, line_items_json, version_int, actor_name
        )
        VALUES (?, 'active', ?, 0, 'tester')
        """,
        (property_id, json.dumps(ambiguous_items, sort_keys=True)),
    )
    draft_id = int(db.execute("SELECT id FROM budget_drafts").fetchone()[0])
    _seed_durable_rule(db)

    applied = auto_apply_merges_on_upload(
        property_id=property_id,
        budget_draft_id=draft_id,
        new_draft_line_items=ambiguous_items,
        db_conn=db,
    )

    assert applied == 0
    assert db.execute("SELECT COUNT(*) FROM budget_line_merge_applications").fetchone()[0] == 0
    assert (
        db.execute("SELECT event_type FROM budget_audit_events").fetchone()[0]
        == "merge_auto_apply_skipped"
    )
    assert (
        db.execute("SELECT line_items_json FROM budget_drafts").fetchone()[0]
        == json.dumps(ambiguous_items, sort_keys=True)
    )


def test_gl_merge_prompt_is_behavior_only():
    from app.dre_extraction.prompts.gl_merge_suggester import PROMPT_TEXT

    lowered = PROMPT_TEXT.lower()
    assert "396 first" not in lowered
    assert "5010" not in lowered
    assert "elevator service" not in lowered


def test_suggest_merges_uses_gemini_structured_response(
    db: sqlite3.Connection,
    monkeypatch,
):
    from app.dre_extraction.wire_schemas import WireMergeSuggestion, WireMergeSuggestionList

    property_id, _setup_id = _ids(db)
    _seed_draft(db)

    async def _fake_call_llm(messages, response_schema, temperature=0.3, timeout=10.0):
        assert response_schema is WireMergeSuggestionList
        return WireMergeSuggestionList(
            suggestions=[
                WireMergeSuggestion(
                    primary_account_code="5010",
                    secondary_account_code="5015",
                    confidence=0.93,
                    reason="Labels describe the same GL service family.",
                )
            ]
        )

    monkeypatch.setattr(
        "app.services.budget_line_merge_service.llm_client.call_llm",
        _fake_call_llm,
    )

    suggestions = suggest_merges(property_id=property_id, db_conn=db)

    assert len(suggestions) == 1
    assert suggestions[0].primary_label == "Elevators"
    assert suggestions[0].secondary_label == "Elevator Service"
    assert suggestions[0].confidence == 0.93
    assert suggestions[0].local_only is False
    assert suggestions[0].wire_schema_sha256


def test_suggest_merges_falls_back_to_local_shortlist(
    db: sqlite3.Connection,
    monkeypatch,
):
    property_id, _setup_id = _ids(db)
    _seed_draft(db)

    async def _failing_call_llm(messages, response_schema, temperature=0.3, timeout=10.0):
        raise RuntimeError("Gemini unavailable")

    monkeypatch.setattr(
        "app.services.budget_line_merge_service.llm_client.call_llm",
        _failing_call_llm,
    )

    suggestions = suggest_merges(property_id=property_id, db_conn=db)

    assert len(suggestions) == 1
    assert suggestions[0].primary_account_code == "5010"
    assert suggestions[0].secondary_account_code == "5015"
    assert suggestions[0].local_only is True
    assert "local" in suggestions[0].reason.lower()
