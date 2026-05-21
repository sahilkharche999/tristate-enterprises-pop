"""Snapshot finalization tests (Phase 4.8).

The byte-for-byte re-render contract requires the snapshot serializer
to be DETERMINISTIC: same input → byte-equal JSON. These tests guard
that contract directly (no Gemini, no rendering) so a future
regression in dict-ordering or whitespace handling is caught at the
serializer level instead of much later in the re-render PDF diff.
"""
from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from app.disclosure_package.snapshots import (
    freeze_package_snapshots,
    load_package_snapshots,
    serialize_snapshot,
)


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"
)


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute(
        "INSERT INTO properties (name, units) VALUES ('Test', 10)"
    )
    pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO annual_packages "
        "(property_id, budget_year, fiscal_year, status) "
        "VALUES (?, 2026, 2026, 'approved')",
        (pid,),
    )
    conn.commit()
    yield conn
    conn.close()


class TestDeterministicSerialization:
    def test_dict_key_order_does_not_affect_output(self) -> None:
        a = {"alpha": 1, "beta": 2, "gamma": 3}
        b = {"gamma": 3, "beta": 2, "alpha": 1}
        assert serialize_snapshot(a) == serialize_snapshot(b)

    def test_no_whitespace_between_keys(self) -> None:
        out = serialize_snapshot({"a": 1, "b": 2})
        assert " " not in out
        assert "\n" not in out

    def test_decimal_preserved_as_string(self) -> None:
        out = serialize_snapshot({"amount": Decimal("1234.567")})
        assert '"amount":"1234.567"' in out
        # Round-trips back as a string (caller converts to Decimal again)
        decoded = json.loads(out)
        assert decoded["amount"] == "1234.567"

    def test_nested_dicts_also_sorted(self) -> None:
        a = {"outer": {"z": 1, "a": 2}}
        b = {"outer": {"a": 2, "z": 1}}
        assert serialize_snapshot(a) == serialize_snapshot(b)

    def test_audit_fields_present_in_budget_snapshot(self) -> None:
        # The Phase 1.4 audit fields (source_column, source_page_or_cell)
        # MUST round-trip through the serializer.
        budget = {
            "line_items": [
                {
                    "label": "HOA Dues",
                    "amount": Decimal("60000"),
                    "source_column": "annual_budget",
                    "source_page_or_cell": "page 7 row 3",
                }
            ]
        }
        out = serialize_snapshot(budget)
        decoded = json.loads(out)
        item = decoded["line_items"][0]
        assert item["source_column"] == "annual_budget"
        assert item["source_page_or_cell"] == "page 7 row 3"


class TestFreezeAndLoad:
    def _package_id(self, db: sqlite3.Connection) -> int:
        return db.execute("SELECT id FROM annual_packages LIMIT 1").fetchone()[0]

    def test_freeze_writes_all_four_snapshots_atomically(
        self, db: sqlite3.Connection
    ) -> None:
        package_id = self._package_id(db)
        freeze_package_snapshots(
            package_id=package_id,
            assessment_setup={"setup_type": "fixed", "pool_count": 1},
            budget={"line_items": [{"label": "Dues", "amount": Decimal("60000")}]},
            reserve={"components": [{"name": "Roof"}]},
            appendix_manifest={"appendices": [{"display_title": "ADR"}]},
            connection=db,
        )
        snaps = load_package_snapshots(package_id=package_id, connection=db)
        assert snaps["assessment_setup"]["setup_type"] == "fixed"
        assert snaps["budget"]["line_items"][0]["amount"] == "60000"
        assert snaps["reserve"]["components"][0]["name"] == "Roof"
        assert snaps["appendix_manifest"]["appendices"][0]["display_title"] == "ADR"
        assert snaps["finalized_at"] is not None
        assert snaps["status"] == "finalized"

    def test_freeze_bumps_version_int(self, db: sqlite3.Connection) -> None:
        package_id = self._package_id(db)
        v0 = db.execute(
            "SELECT version_int FROM annual_packages WHERE id = ?", (package_id,)
        ).fetchone()[0]
        freeze_package_snapshots(
            package_id=package_id,
            assessment_setup={}, budget={}, reserve={}, appendix_manifest={},
            connection=db,
        )
        v1 = db.execute(
            "SELECT version_int FROM annual_packages WHERE id = ?", (package_id,)
        ).fetchone()[0]
        assert v1 == v0 + 1

    def test_re_freeze_produces_byte_equal_columns_for_same_inputs(
        self, db: sqlite3.Connection
    ) -> None:
        package_id = self._package_id(db)
        setup = {"a": 1, "b": [2, 3]}
        budget = {"line_items": [{"amount": Decimal("100"), "label": "x"}]}
        reserve = {"components": []}
        manifest = {"appendices": []}

        freeze_package_snapshots(
            package_id=package_id,
            assessment_setup=setup, budget=budget,
            reserve=reserve, appendix_manifest=manifest,
            connection=db,
        )
        first = db.execute(
            "SELECT assessment_setup_snapshot_json, budget_snapshot_json, "
            "reserve_snapshot_json, appendix_manifest_snapshot_json "
            "FROM annual_packages WHERE id = ?",
            (package_id,),
        ).fetchone()

        # Reorder dict keys in-memory; serializer should still produce
        # byte-equal output
        reordered_setup = {"b": [2, 3], "a": 1}
        reordered_budget = {"line_items": [{"label": "x", "amount": Decimal("100")}]}
        freeze_package_snapshots(
            package_id=package_id,
            assessment_setup=reordered_setup, budget=reordered_budget,
            reserve=reserve, appendix_manifest=manifest,
            connection=db,
        )
        second = db.execute(
            "SELECT assessment_setup_snapshot_json, budget_snapshot_json, "
            "reserve_snapshot_json, appendix_manifest_snapshot_json "
            "FROM annual_packages WHERE id = ?",
            (package_id,),
        ).fetchone()
        assert first == second

    def test_load_returns_none_for_unfrozen_snapshots(
        self, db: sqlite3.Connection
    ) -> None:
        package_id = self._package_id(db)
        snaps = load_package_snapshots(package_id=package_id, connection=db)
        assert snaps["assessment_setup"] is None
        assert snaps["budget"] is None
        assert snaps["reserve"] is None
        assert snaps["appendix_manifest"] is None
        assert snaps["finalized_at"] is None
        assert snaps["status"] == "approved"

    def test_missing_package_raises(self, db: sqlite3.Connection) -> None:
        with pytest.raises(LookupError):
            load_package_snapshots(package_id=99999, connection=db)


class TestUnserializableTypeRejected:
    def test_object_without_coercion_raises(self) -> None:
        class Weird:
            pass

        with pytest.raises(TypeError, match="not JSON-serializable"):
            serialize_snapshot({"x": Weird()})
