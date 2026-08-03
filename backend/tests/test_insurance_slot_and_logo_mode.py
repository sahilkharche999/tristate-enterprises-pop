"""Insurance package_role + letterhead_logo_mode smoke tests."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.services.appendix_service import (
    PACKAGE_ROLE_INSURANCE,
    list_appendices,
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
        "INSERT INTO properties (name, units, hoa_code) VALUES ('Test HOA', 10, 'T1')"
    )
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def storage_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "storage"
    monkeypatch.setattr("app.config.settings.BUDGET_STORAGE_ROOT", str(root))
    return root


def _pid(db: sqlite3.Connection) -> int:
    return int(db.execute("SELECT id FROM properties").fetchone()[0])


def test_upload_insurance_role_and_uniqueness(db, storage_root):
    pid = _pid(db)
    a = upload_appendix(
        property_id=pid,
        file_bytes=b"%PDF-1.4 ins1",
        original_filename="ins1.pdf",
        display_title="Insurance 2026",
        package_role="insurance",
        uploaded_by="ops",
        connection=db,
    )
    assert a.package_role == PACKAGE_ROLE_INSURANCE
    b = upload_appendix(
        property_id=pid,
        file_bytes=b"%PDF-1.4 ins2",
        original_filename="ins2.pdf",
        display_title="Insurance replacement",
        package_role="insurance",
        uploaded_by="ops",
        connection=db,
    )
    assert b.package_role == PACKAGE_ROLE_INSURANCE
    rows = list_appendices(property_id=pid, connection=db)
    insurance_rows = [r for r in rows if r.package_role == "insurance"]
    assert len(insurance_rows) == 1
    assert insurance_rows[0].appendix_id == b.appendix_id


def test_toggle_insurance_role_off(db, storage_root):
    pid = _pid(db)
    a = upload_appendix(
        property_id=pid,
        file_bytes=b"%PDF",
        original_filename="ins.pdf",
        display_title="Ins",
        package_role="insurance",
        uploaded_by="ops",
        connection=db,
    )
    cleared = update_appendix(
        property_id=pid,
        appendix_id=a.appendix_id,
        expected_version=a.version_int,
        package_role=None,
        package_role_set=True,
        connection=db,
    )
    assert cleared.package_role is None


def test_manifest_includes_package_role(db, storage_root):
    from app.disclosure_package.appendix_manifest import resolve_appendix_manifest

    pid = _pid(db)
    upload_appendix(
        property_id=pid,
        file_bytes=b"%PDF",
        original_filename="ins.pdf",
        display_title="Annual Insurance",
        package_role="insurance",
        include_by_default=True,
        uploaded_by="ops",
        connection=db,
    )
    upload_appendix(
        property_id=pid,
        file_bytes=b"%PDF",
        original_filename="adr.pdf",
        display_title="ADR",
        include_by_default=True,
        uploaded_by="ops",
        connection=db,
    )
    resolved = resolve_appendix_manifest(
        property_id=pid, package_id=None, connection=db
    )
    roles = {r.display_title: r.package_role for r in resolved}
    assert roles["Annual Insurance"] == "insurance"
    assert roles["ADR"] is None


def test_letterhead_logo_mode_in_base_template():
    tpl = Path(
        "app/disclosure_package/templates/standard/_base.html"
    ).read_text(encoding="utf-8")
    assert "logo_only" in tpl
    assert "letterhead_logo_mode" in tpl
    css = Path(
        "app/disclosure_package/templates/standard/_shared.css"
    ).read_text(encoding="utf-8")
    assert "letterhead-logo-cell--wide" in css
