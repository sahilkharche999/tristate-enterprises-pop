"""H4 — approve/finalize are If-Match-guarded and version-atomic.

- The HTTP endpoints require If-Match (428 when missing).
- A stale version loses with 409 carrying the fresh version.
- The approve UPDATE enforces the version predicate in SQL, so a second
  approve at the old version cannot silently re-win.
"""
from __future__ import annotations

import sqlite3

from app.services.annual_package_service import (
    PackageVersionMismatch,
    approve_annual_package,
)


def _make_draft(client, hoa_id: int = 1, fiscal_year: int = 2032) -> int:
    created = client.post(
        f"/hoa/{hoa_id}/annual-packages",
        json={"budget_year": fiscal_year, "fiscal_year": fiscal_year},
    )
    assert created.status_code == 200, created.text
    return created.json()["package_id"]


def _version(client, package_id: int) -> int:
    conn = sqlite3.connect(client.app.state.test_db_path)
    try:
        return conn.execute(
            "SELECT version_int FROM annual_packages WHERE id = ?", (package_id,)
        ).fetchone()[0]
    finally:
        conn.close()


def test_approve_without_if_match_returns_428(client):
    package_id = _make_draft(client)
    resp = client.post(
        f"/hoa/1/annual-packages/{package_id}/approve",
        json={"approved_assessment_revenue_annual": "60000"},
    )
    assert resp.status_code == 428, resp.text


def test_finalize_without_if_match_returns_428(client):
    package_id = _make_draft(client)
    resp = client.post(f"/hoa/1/annual-packages/{package_id}/finalize", json={})
    assert resp.status_code == 428, resp.text


def test_stale_if_match_on_approve_returns_409(client):
    package_id = _make_draft(client)
    good = _version(client, package_id)
    ok = client.post(
        f"/hoa/1/annual-packages/{package_id}/approve",
        json={"approved_assessment_revenue_annual": "60000"},
        headers={"If-Match": str(good)},
    )
    assert ok.status_code == 200, ok.text
    # Re-approve at the now-stale version → 409.
    stale = client.post(
        f"/hoa/1/annual-packages/{package_id}/approve",
        json={"approved_assessment_revenue_annual": "60000"},
        headers={"If-Match": str(good)},
    )
    assert stale.status_code == 409, stale.text


def test_approve_sql_predicate_blocks_second_write_at_stale_version(client):
    """Even at the service layer with expected_version=None (CAS-on-read),
    a write against a bumped row loses. Here we bump the row out-of-band,
    then approve with the pre-bump expected_version → mismatch."""
    package_id = _make_draft(client)
    conn = sqlite3.connect(client.app.state.test_db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        current = conn.execute(
            "SELECT version_int FROM annual_packages WHERE id = ?", (package_id,)
        ).fetchone()[0]
        # Simulate a concurrent writer bumping the version after the client read.
        conn.execute(
            "UPDATE annual_packages SET version_int = version_int + 1 WHERE id = ?",
            (package_id,),
        )
        conn.commit()
        raised = False
        try:
            approve_annual_package(
                property_id=1, package_id=package_id,
                approved_assessment_revenue_annual="60000",  # type: ignore[arg-type]
                approved_by="ops@example.com",
                connection=conn,
                expected_version=current,  # stale
            )
        except PackageVersionMismatch:
            raised = True
        assert raised
    finally:
        conn.close()
