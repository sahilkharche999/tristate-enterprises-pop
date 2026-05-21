"""Appendix-document service tests (Phase 5.4).

Direct sqlite + filesystem coverage: upload, list, update (with
optimistic-lock), retire (soft delete preserving prior-package
references via the junction table).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.services.appendix_service import (
    AppendixDocumentResponse,
    AppendixNotFound,
    PropertyNotFound,
    list_appendices,
    retire_appendix,
    update_appendix,
    upload_appendix,
)


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"
)


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute(
        "INSERT INTO properties (name, units, hoa_code) "
        "VALUES ('Old Mill', 279, '10')"
    )
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def storage_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "storage"
    monkeypatch.setattr("app.config.settings.BUDGET_STORAGE_ROOT", str(root))
    return root


def _property_id(db: sqlite3.Connection) -> int:
    return db.execute("SELECT id FROM properties").fetchone()[0]


class TestUploadAppendix:
    def test_creates_row_and_saves_file(
        self, db: sqlite3.Connection, storage_root: Path
    ) -> None:
        pid = _property_id(db)
        resp = upload_appendix(
            property_id=pid,
            file_bytes=b"%PDF-1.4 ADR Policy",
            original_filename="ADR Policy.pdf",
            display_title="ADR/IDR Policy",
            cadence="persistent",
            required_flag=True,
            uploaded_by="ops@example.com",
            connection=db,
        )
        assert resp.property_id == pid
        assert resp.display_title == "ADR/IDR Policy"
        assert resp.cadence == "persistent"
        assert resp.required_flag is True
        assert resp.include_by_default is True
        assert resp.status == "active"
        assert resp.version_int == 0
        # File saved
        assert (storage_root / resp.file_id).read_bytes() == b"%PDF-1.4 ADR Policy"

    def test_annual_cadence_with_year(
        self, db: sqlite3.Connection, storage_root: Path
    ) -> None:
        pid = _property_id(db)
        resp = upload_appendix(
            property_id=pid,
            file_bytes=b"%PDF",
            original_filename="2026 Insurance.pdf",
            display_title="2026 Annual Insurance Disclosure",
            cadence="annual",
            annual_year=2026,
            required_flag=True,
            uploaded_by="ops",
            connection=db,
        )
        assert resp.cadence == "annual"
        assert resp.annual_year == 2026

    def test_unknown_cadence_rejected(
        self, db: sqlite3.Connection, storage_root: Path
    ) -> None:
        pid = _property_id(db)
        with pytest.raises(ValueError, match="Unknown cadence"):
            upload_appendix(
                property_id=pid, file_bytes=b"x",
                original_filename="x.pdf", display_title="X",
                cadence="not_a_cadence",  # type: ignore[arg-type]
                uploaded_by="u", connection=db,
            )

    def test_missing_property_raises(
        self, db: sqlite3.Connection, storage_root: Path
    ) -> None:
        with pytest.raises(PropertyNotFound):
            upload_appendix(
                property_id=99999, file_bytes=b"x",
                original_filename="x.pdf", display_title="X",
                uploaded_by="u", connection=db,
            )
        # No orphan rows
        assert db.execute("SELECT COUNT(*) FROM appendix_documents").fetchone()[0] == 0


class TestListAppendices:
    def test_active_only_by_default(
        self, db: sqlite3.Connection, storage_root: Path
    ) -> None:
        pid = _property_id(db)
        a = upload_appendix(
            property_id=pid, file_bytes=b"x",
            original_filename="a.pdf", display_title="A",
            uploaded_by="u", connection=db,
        )
        b = upload_appendix(
            property_id=pid, file_bytes=b"y",
            original_filename="b.pdf", display_title="B",
            uploaded_by="u", connection=db,
        )
        retire_appendix(
            property_id=pid, appendix_id=b.appendix_id, connection=db,
        )
        active = list_appendices(property_id=pid, connection=db)
        assert [r.appendix_id for r in active] == [a.appendix_id]

        all_rows = list_appendices(
            property_id=pid, connection=db, include_retired=True
        )
        assert {r.appendix_id for r in all_rows} == {a.appendix_id, b.appendix_id}

    def test_ordered_by_display_order(
        self, db: sqlite3.Connection, storage_root: Path
    ) -> None:
        pid = _property_id(db)
        r1 = upload_appendix(
            property_id=pid, file_bytes=b"x",
            original_filename="z.pdf", display_title="Z",
            uploaded_by="u", connection=db,
        )
        r2 = upload_appendix(
            property_id=pid, file_bytes=b"y",
            original_filename="a.pdf", display_title="A",
            uploaded_by="u", connection=db,
        )
        update_appendix(
            property_id=pid, appendix_id=r2.appendix_id,
            expected_version=r2.version_int,
            default_display_order=0,
            connection=db,
        )
        update_appendix(
            property_id=pid, appendix_id=r1.appendix_id,
            expected_version=r1.version_int,
            default_display_order=1,
            connection=db,
        )
        results = list_appendices(property_id=pid, connection=db)
        assert [r.display_title for r in results] == ["A", "Z"]


class TestUpdateAppendix:
    def test_partial_update_changes_only_supplied_fields(
        self, db: sqlite3.Connection, storage_root: Path
    ) -> None:
        pid = _property_id(db)
        r = upload_appendix(
            property_id=pid, file_bytes=b"x",
            original_filename="x.pdf", display_title="Old Title",
            cadence="persistent",
            uploaded_by="u", connection=db,
        )
        updated = update_appendix(
            property_id=pid, appendix_id=r.appendix_id,
            expected_version=r.version_int,
            display_title="New Title",
            connection=db,
        )
        assert updated.display_title == "New Title"
        assert updated.cadence == "persistent"  # unchanged
        assert updated.version_int == r.version_int + 1

    def test_version_mismatch_raises(
        self, db: sqlite3.Connection, storage_root: Path
    ) -> None:
        pid = _property_id(db)
        r = upload_appendix(
            property_id=pid, file_bytes=b"x",
            original_filename="x.pdf", display_title="A",
            uploaded_by="u", connection=db,
        )
        # First edit succeeds, version bumps
        update_appendix(
            property_id=pid, appendix_id=r.appendix_id,
            expected_version=r.version_int,
            display_title="B",
            connection=db,
        )
        # Second edit with stale version should fail
        with pytest.raises(AppendixNotFound, match="version mismatch"):
            update_appendix(
                property_id=pid, appendix_id=r.appendix_id,
                expected_version=r.version_int,  # stale
                display_title="C",
                connection=db,
            )

    def test_cadence_change_to_annual(
        self, db: sqlite3.Connection, storage_root: Path
    ) -> None:
        pid = _property_id(db)
        r = upload_appendix(
            property_id=pid, file_bytes=b"x",
            original_filename="ins.pdf", display_title="Insurance",
            cadence="persistent",  # incorrectly set initially
            uploaded_by="u", connection=db,
        )
        updated = update_appendix(
            property_id=pid, appendix_id=r.appendix_id,
            expected_version=r.version_int,
            cadence="annual",
            annual_year=2026,
            required_flag=True,
            needs_cadence_review=False,
            connection=db,
        )
        assert updated.cadence == "annual"
        assert updated.annual_year == 2026
        assert updated.required_flag is True
        assert updated.needs_cadence_review is False

    def test_empty_update_is_noop(
        self, db: sqlite3.Connection, storage_root: Path
    ) -> None:
        pid = _property_id(db)
        r = upload_appendix(
            property_id=pid, file_bytes=b"x",
            original_filename="x.pdf", display_title="A",
            uploaded_by="u", connection=db,
        )
        same = update_appendix(
            property_id=pid, appendix_id=r.appendix_id,
            expected_version=r.version_int,
            connection=db,
        )
        assert same.version_int == r.version_int

    def test_missing_appendix_raises(
        self, db: sqlite3.Connection, storage_root: Path
    ) -> None:
        pid = _property_id(db)
        with pytest.raises(AppendixNotFound):
            update_appendix(
                property_id=pid, appendix_id=99999,
                expected_version=0,
                display_title="X",
                connection=db,
            )


class TestRetireAppendix:
    def test_retires_without_deleting(
        self, db: sqlite3.Connection, storage_root: Path
    ) -> None:
        pid = _property_id(db)
        r = upload_appendix(
            property_id=pid, file_bytes=b"x",
            original_filename="x.pdf", display_title="A",
            uploaded_by="u", connection=db,
        )
        retired = retire_appendix(
            property_id=pid, appendix_id=r.appendix_id, connection=db,
        )
        assert retired.status == "retired"
        # Row still exists (soft delete)
        count = db.execute(
            "SELECT COUNT(*) FROM appendix_documents WHERE id = ?",
            (r.appendix_id,),
        ).fetchone()[0]
        assert count == 1
        # File still exists too (operator may un-retire later)
        assert (storage_root / r.file_id).exists()

    def test_retired_excluded_from_default_list(
        self, db: sqlite3.Connection, storage_root: Path
    ) -> None:
        pid = _property_id(db)
        r = upload_appendix(
            property_id=pid, file_bytes=b"x",
            original_filename="x.pdf", display_title="A",
            uploaded_by="u", connection=db,
        )
        retire_appendix(
            property_id=pid, appendix_id=r.appendix_id, connection=db,
        )
        active = list_appendices(property_id=pid, connection=db)
        assert active == []


class TestPriorPackageReferencesPreserved:
    """Retired appendices must still be reachable through the
    ``annual_package_appendices`` junction so already-finalized
    packages render the same files they did at finalization.
    """

    def test_junction_row_survives_appendix_retirement(
        self, db: sqlite3.Connection, storage_root: Path
    ) -> None:
        pid = _property_id(db)
        r = upload_appendix(
            property_id=pid, file_bytes=b"x",
            original_filename="x.pdf", display_title="A",
            uploaded_by="u", connection=db,
        )
        # Build an annual_packages row + junction entry
        db.execute(
            "INSERT INTO annual_packages "
            "(property_id, budget_year, fiscal_year, status) "
            "VALUES (?, 2026, 2026, 'finalized')",
            (pid,),
        )
        package_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute(
            "INSERT INTO annual_package_appendices "
            "(package_id, appendix_id, display_order, included) "
            "VALUES (?, ?, 1, 1)",
            (package_id, r.appendix_id),
        )
        db.commit()

        retire_appendix(
            property_id=pid, appendix_id=r.appendix_id, connection=db,
        )

        # Junction row + retired appendix_documents row both still queryable
        junction = db.execute(
            "SELECT appendix_id FROM annual_package_appendices "
            "WHERE package_id = ?",
            (package_id,),
        ).fetchone()
        assert junction is not None
        assert junction[0] == r.appendix_id
        appendix_status = db.execute(
            "SELECT status FROM appendix_documents WHERE id = ?",
            (r.appendix_id,),
        ).fetchone()
        assert appendix_status == ("retired",)
