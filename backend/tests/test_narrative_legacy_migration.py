"""add-full-document-editor: migrating the three retired cover-letter slots.

Spec: "Legacy slots migrate into cover_letter" and "HOAs with no legacy
overrides are untouched". The property that matters most is that nothing an
operator already typed is lost — including when the composition can't find
its anchor.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import text as sql_text

from app.services import narrative_content as nc

INTRO = "<p>Our own custom intro paragraph.</p>"
ENCLOSED = "<ol><li>Custom document one</li><li>Custom document two</li></ol>"
CLOSING = "<p>Warmly, the Board.</p>"


# ── composition ─────────────────────────────────────────────────────────────


def test_no_slots_composes_to_none():
    assert nc.compose_legacy_cover_letter({}) is None
    assert nc.compose_legacy_cover_letter(
        {"cover_letter_intro": None, "enclosed_documents_list": ""}
    ) is None


def test_all_three_slots_compose_in_reading_order():
    composed = nc.compose_legacy_cover_letter(
        {
            "cover_letter_intro": INTRO,
            "enclosed_documents_list": ENCLOSED,
            "cover_letter_closing": CLOSING,
        }
    )
    assert "Our own custom intro paragraph." in composed
    assert "Custom document one" in composed
    assert "Warmly, the Board." in composed

    assert composed.index("custom intro") < composed.index("Custom document one")
    assert composed.index("Custom document one") < composed.index("Warmly")


def test_composed_slots_replace_the_baseline_prose_they_overrode():
    composed = nc.compose_legacy_cover_letter({"cover_letter_intro": INTRO})
    assert "Thank you for the prompt payment" not in composed
    # ...but only the paragraph that slot owned.
    assert "Please find the following documents enclosed" in composed
    assert "As per civil code" in composed


def test_untouched_slots_keep_their_baseline_prose():
    composed = nc.compose_legacy_cover_letter({"cover_letter_closing": CLOSING})
    assert "Thank you for the prompt payment" in composed
    assert "On behalf of the Board of Directors" not in composed
    assert "Warmly, the Board." in composed


def test_composed_letter_keeps_the_required_5300_block():
    composed = nc.compose_legacy_cover_letter(
        {"cover_letter_intro": INTRO, "cover_letter_closing": CLOSING}
    )
    assert "special_assessment_disclosure" in nc.blocks_present(composed)
    # ...so it is valid input to the real save path.
    assert nc.validate_document_html("cover_letter", composed) == composed


def test_plain_text_slot_is_wrapped_not_dropped():
    composed = nc.compose_legacy_cover_letter(
        {"cover_letter_intro": "Bare text with no markup"}
    )
    assert "Bare text with no markup" in composed


def test_content_survives_even_when_the_anchor_is_gone(monkeypatch):
    """A baseline reworded since the slots were saved must not lose work."""
    monkeypatch.setattr(
        nc, "baseline_html", lambda doc_id: "<p>Completely different baseline.</p>"
    )
    composed = nc.compose_legacy_cover_letter({"cover_letter_intro": INTRO})
    assert "Our own custom intro paragraph." in composed
    assert "Completely different baseline." in composed


def test_composed_output_is_sanitized():
    composed = nc.compose_legacy_cover_letter(
        {"cover_letter_intro": "<p>ok<script>evil()</script></p>"}
    )
    assert "script" not in composed
    assert "ok" in composed


# ── the migration itself ────────────────────────────────────────────────────


def _run_migration(session):
    from app.ai_implementation import database as database_module

    database_module.migrate_legacy_boilerplate_slots()
    session.commit()


def _seed_settings(session, property_id: int, slots: dict) -> None:
    session.execute(
        sql_text(
            "INSERT INTO hoa_settings (property_id, boilerplate_overrides_json) "
            "VALUES (:pid, :json)"
        ),
        {"pid": property_id, "json": json.dumps(slots)},
    )
    session.commit()


def _rows(session) -> list:
    return session.execute(
        sql_text(
            "SELECT scope_id, body_html FROM narrative_overrides "
            "WHERE scope='hoa' AND document_id='cover_letter'"
        )
    ).fetchall()


def test_migration_composes_legacy_slots(client, db_session):
    from app.ai_implementation.db.models import Property

    prop = Property(name="Legacy HOA", units=5, hoa_code="LEG1")
    db_session.add(prop)
    db_session.commit()
    _seed_settings(
        db_session,
        prop.id,
        {"cover_letter_intro": INTRO, "cover_letter_closing": CLOSING},
    )

    _run_migration(db_session)

    rows = [r for r in _rows(db_session) if r[0] == prop.id]
    assert len(rows) == 1
    body = rows[0][1]
    assert "Our own custom intro paragraph." in body
    assert "Warmly, the Board." in body
    assert nc.resolve_document(db_session, "cover_letter", prop.id) == body


def test_hoas_with_no_legacy_overrides_get_no_row(client, db_session):
    from app.ai_implementation.db.models import Property

    prop = Property(name="Clean HOA", units=5, hoa_code="CLN1")
    db_session.add(prop)
    db_session.commit()

    _run_migration(db_session)

    assert [r for r in _rows(db_session) if r[0] == prop.id] == []
    assert nc.effective_scope(db_session, "cover_letter", prop.id) == "baseline"


def test_migration_is_idempotent_and_never_clobbers_later_edits(client, db_session):
    from app.ai_implementation.db.models import Property

    prop = Property(name="Rerun HOA", units=5, hoa_code="RER1")
    db_session.add(prop)
    db_session.commit()
    _seed_settings(db_session, prop.id, {"cover_letter_intro": INTRO})

    _run_migration(db_session)
    first = [r for r in _rows(db_session) if r[0] == prop.id][0][1]

    # The operator edits after the migration; re-running must not revert it.
    edited = (
        "<p>Edited after migrating.</p>"
        '<ol><li data-block="special_assessment_disclosure"></li></ol>'
    )
    nc.save_document(db_session, "cover_letter", "hoa", prop.id, edited)
    db_session.commit()

    _run_migration(db_session)

    rows = [r for r in _rows(db_session) if r[0] == prop.id]
    assert len(rows) == 1
    assert rows[0][1] == edited
    assert rows[0][1] != first


def test_migration_leaves_the_legacy_column_in_place(client, db_session):
    """The old column is the rollback path until the release after this one."""
    from app.ai_implementation.db.models import Property

    prop = Property(name="Rollback HOA", units=5, hoa_code="RBK1")
    db_session.add(prop)
    db_session.commit()
    _seed_settings(db_session, prop.id, {"cover_letter_intro": INTRO})

    _run_migration(db_session)

    stored = db_session.execute(
        sql_text(
            "SELECT boilerplate_overrides_json FROM hoa_settings WHERE property_id=:pid"
        ),
        {"pid": prop.id},
    ).scalar()
    assert INTRO in stored
