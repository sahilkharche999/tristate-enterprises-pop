"""Phase 3.1 — DRE upload service tests.

Exercises ``upload_dre_document`` end-to-end (DB row + on-disk file)
against a temp SQLite DB and a temp storage root. Doesn't exercise the
FastAPI route layer — that's a thin pass-through.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz")  # PyMuPDF needed to build a valid PDF

from app.dre_extraction import storage as storage_module
from app.services.dre_upload_service import (
    PropertyNotFound,
    upload_dre_document,
)


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"
)


def _synth_pdf_bytes(page_count: int) -> bytes:
    doc = fitz.open()
    for i in range(page_count):
        page = doc.new_page(width=612, height=792)
        page.insert_text(fitz.Point(50, 100), f"DRE PAGE {i + 1}", fontsize=24)
    data = doc.tobytes()
    doc.close()
    return data


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


class TestUploadDREDocument:
    def test_creates_row_and_saves_file(
        self, db: sqlite3.Connection, storage_root: Path
    ) -> None:
        pdf_bytes = _synth_pdf_bytes(page_count=5)
        property_id = db.execute("SELECT id FROM properties").fetchone()[0]

        response = upload_dre_document(
            property_id=property_id,
            file_bytes=pdf_bytes,
            original_filename="Old Mill 2026 DRE.pdf",
            uploaded_by="ops@example.com",
            connection=db,
        )

        assert response.property_id == property_id
        assert response.file_name == "Old Mill 2026 DRE.pdf"
        assert response.page_count == 5
        assert response.status == "active"
        assert response.dre_document_id > 0
        # File exists at the reported file_id
        absolute = storage_root / response.file_id
        assert absolute.exists()
        assert absolute.read_bytes() == pdf_bytes
        # DB row has the right file_id
        db_row = db.execute(
            "SELECT file_id, status, page_count FROM dre_documents WHERE id = ?",
            (response.dre_document_id,),
        ).fetchone()
        assert db_row == (response.file_id, "active", 5)

    def test_second_upload_supersedes_first(
        self, db: sqlite3.Connection, storage_root: Path
    ) -> None:
        property_id = db.execute("SELECT id FROM properties").fetchone()[0]
        first = upload_dre_document(
            property_id=property_id,
            file_bytes=_synth_pdf_bytes(2),
            original_filename="2025.pdf",
            uploaded_by="u1",
            connection=db,
        )
        second = upload_dre_document(
            property_id=property_id,
            file_bytes=_synth_pdf_bytes(3),
            original_filename="2026.pdf",
            uploaded_by="u1",
            connection=db,
        )
        statuses = dict(
            db.execute(
                "SELECT id, status FROM dre_documents WHERE property_id = ?",
                (property_id,),
            ).fetchall()
        )
        assert statuses[first.dre_document_id] == "superseded"
        assert statuses[second.dre_document_id] == "active"

    def test_missing_property_raises_not_found(
        self, db: sqlite3.Connection, storage_root: Path
    ) -> None:
        with pytest.raises(PropertyNotFound):
            upload_dre_document(
                property_id=99999,
                file_bytes=_synth_pdf_bytes(1),
                original_filename="x.pdf",
                uploaded_by="u",
                connection=db,
            )
        # And no rows were inserted
        count = db.execute("SELECT COUNT(*) FROM dre_documents").fetchone()[0]
        assert count == 0

    def test_corrupt_pdf_still_uploads_with_null_page_count(
        self, db: sqlite3.Connection, storage_root: Path
    ) -> None:
        # Random bytes — PyMuPDF should fail to open and we record page_count=None
        property_id = db.execute("SELECT id FROM properties").fetchone()[0]
        response = upload_dre_document(
            property_id=property_id,
            file_bytes=b"not a real pdf",
            original_filename="garbage.pdf",
            uploaded_by="u",
            connection=db,
        )
        assert response.page_count is None
        # The file still saved (operator can see the upload and decide to delete)
        assert (storage_root / response.file_id).exists()
