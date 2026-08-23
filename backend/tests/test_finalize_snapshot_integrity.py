"""C1/C2/C3 — finalize/render integrity (fix-critical-disclosure-integrity).

Covers the `package-finalization-integrity` and `finalized-package-rendering`
specs:

- C2: finalize snapshots are assembled SERVER-side; a client body cannot
  influence the frozen record (legacy bodies are ignored, not persisted).
- C3: finalize enforces blocking preflight server-side → HTTP 422 with
  field paths, and writes nothing on a failed gate.
- H4 (finalize half): the freeze UPDATE is compare-and-set on version_int.
- C1: a finalized package with valid snapshots renders from the frozen
  columns — live edits after finalization do not change a re-render; a
  missing frozen appendix hard-fails; legacy stub-finalized packages fall
  back to live with a warning (branch selection unit-tested).
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app.disclosure_package import service as dp_service
from app.disclosure_package.compile_inputs import should_use_snapshots
from app.disclosure_package.schemas import PreflightError


_SERVER_SNAPSHOTS = {
    "assessment_setup": {"setup": {"setup_type": "per_unit"}, "pools": []},
    "budget": {"line_items": [{"label": "Dues", "amount": "60000"}]},
    "reserve": {"study_date": "2026-01-01", "components": []},
    "appendix_manifest": [
        {"appendix_id": 1, "file_id": "ap/1/bylaws.pdf", "file_name": "bylaws.pdf",
         "display_title": "Bylaws", "display_order": 1, "source": "default"},
    ],
    "compile_context": {
        "assessment_matrix": {"kind": "frozen"},
        "hoa_metadata": {"hoa_id": 1, "name": "Test HOA", "units": 10,
                         "fiscal_year_start_month": 1, "fiscal_year_end_month": 12},
        "hoa_settings_overrides": {},
        "assessment_revenue_annual": "60000",
        "assessment_mode": "variable",
    },
}


def _pkg_version(client, package_id: int) -> int:
    """Current version_int of a package (H4: approve/finalize now require an
    If-Match header carrying this value)."""
    return _snapshot_columns(client, package_id)[6]


def _finalize(client, package_id: int, hoa_id: int = 1, **kwargs):
    """POST finalize with the required If-Match header (H4)."""
    headers = kwargs.pop("headers", {})
    headers.setdefault("If-Match", str(_pkg_version(client, package_id)))
    return client.post(
        f"/hoa/{hoa_id}/annual-packages/{package_id}/finalize",
        headers=headers,
        **kwargs,
    )


def _make_package(client, hoa_id: int = 1, fiscal_year: int = 2031) -> int:
    created = client.post(
        f"/hoa/{hoa_id}/annual-packages",
        json={"budget_year": fiscal_year, "fiscal_year": fiscal_year},
    )
    assert created.status_code == 200, created.text
    package_id = created.json()["package_id"]
    approved = client.post(
        f"/hoa/{hoa_id}/annual-packages/{package_id}/approve",
        json={"approved_assessment_revenue_annual": "60000"},
        headers={"If-Match": str(_pkg_version(client, package_id))},
    )
    assert approved.status_code == 200, approved.text
    return package_id


def _snapshot_columns(client, package_id: int):
    import sqlite3

    conn = sqlite3.connect(client.app.state.test_db_path)
    try:
        return conn.execute(
            "SELECT assessment_setup_snapshot_json, budget_snapshot_json, "
            "reserve_snapshot_json, appendix_manifest_snapshot_json, "
            "compile_context_snapshot_json, status, version_int "
            "FROM annual_packages WHERE id = ?",
            (package_id,),
        ).fetchone()
    finally:
        conn.close()


class TestFinalizeServerSideAssembly:
    def test_client_payload_cannot_influence_frozen_record(self, client, monkeypatch):
        """C2: garbage in the legacy body fields is ignored — the frozen
        columns hold the SERVER-assembled payloads."""
        monkeypatch.setattr(
            dp_service, "run_preflight", lambda *a, **k: ([], []),
        )
        monkeypatch.setattr(
            dp_service, "assemble_finalize_snapshots",
            lambda *a, **k: dict(_SERVER_SNAPSHOTS),
        )
        package_id = _make_package(client)

        response = _finalize(
            client, package_id,
            json={
                "assessment_setup": {"FORGED": "assessment"},
                "budget": {"FORGED": "budget", "line_items": [{"amount": "1"}]},
                "reserve": {"FORGED": True},
                "appendix_manifest": ["FORGED"],
            },
        )
        assert response.status_code == 200, response.text
        row = _snapshot_columns(client, package_id)
        for column_value in row[:5]:
            assert "FORGED" not in (column_value or "")
        assert json.loads(row[1]) == _SERVER_SNAPSHOTS["budget"]
        assert json.loads(row[4]) == _SERVER_SNAPSHOTS["compile_context"]
        assert row[5] == "finalized"

    def test_finalize_with_no_body_works(self, client, monkeypatch):
        """New frontend sends an empty body — the retired fields are optional."""
        monkeypatch.setattr(dp_service, "run_preflight", lambda *a, **k: ([], []))
        monkeypatch.setattr(
            dp_service, "assemble_finalize_snapshots",
            lambda *a, **k: dict(_SERVER_SNAPSHOTS),
        )
        package_id = _make_package(client)
        response = _finalize(client, package_id, json={})
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "finalized"


class TestFinalizePreflightGate:
    def test_blocking_preflight_returns_422_and_writes_nothing(
        self, client, monkeypatch
    ):
        """C3: a blocking preflight error stops finalize server-side."""
        blocking = [
            PreflightError(
                field_path="hoa_settings.reserve_study_date",
                message="Reserve study is older than 3 years.",
                severity="blocking",
            )
        ]
        monkeypatch.setattr(
            dp_service, "run_preflight", lambda *a, **k: (blocking, []),
        )
        assemble_calls = []
        monkeypatch.setattr(
            dp_service, "assemble_finalize_snapshots",
            lambda *a, **k: assemble_calls.append(1) or dict(_SERVER_SNAPSHOTS),
        )
        package_id = _make_package(client)

        response = _finalize(client, package_id, json={})
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert detail["field_paths"] == ["hoa_settings.reserve_study_date"]
        # Preflight runs BEFORE assembly; nothing is assembled or written.
        assert assemble_calls == []
        row = _snapshot_columns(client, package_id)
        assert all(v is None for v in row[:5])
        assert row[5] == "approved"

    def test_real_preflight_blocks_unready_hoa(self, client):
        """C3 end-to-end: with no active budget draft, the REAL preflight
        (no monkeypatch) blocks finalize with 422."""
        package_id = _make_package(client)
        response = _finalize(client, package_id, json={})
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["field_paths"]


class TestFinalizeConcurrency:
    def test_concurrent_finalize_loses_with_409(self, client, monkeypatch):
        """H4 (finalize half): the freeze UPDATE is compare-and-set — a
        writer that bumps version_int between the read and the freeze
        makes the freeze lose with 409, never last-writer-wins."""
        import sqlite3

        monkeypatch.setattr(dp_service, "run_preflight", lambda *a, **k: ([], []))

        package_id = _make_package(client)
        db_path = client.app.state.test_db_path

        def _assemble_and_race(*a, **k):
            # Simulate a concurrent writer between the service's version
            # read and the freeze UPDATE.
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    "UPDATE annual_packages SET version_int = version_int + 1 "
                    "WHERE id = ?",
                    (package_id,),
                )
                conn.commit()
            finally:
                conn.close()
            return dict(_SERVER_SNAPSHOTS)

        monkeypatch.setattr(
            dp_service, "assemble_finalize_snapshots", _assemble_and_race,
        )
        response = _finalize(client, package_id, json={})
        assert response.status_code == 409, response.text
        row = _snapshot_columns(client, package_id)
        assert row[5] == "approved"  # freeze did not land


class TestSnapshotBranchSelection:
    def test_stub_finalized_package_reads_as_no_snapshot(self, client):
        """Legacy packages froze client-sent '{}' stubs — they must route
        to the live branch (with the caller's warning), never render an
        empty frozen package."""
        import sqlite3

        conn = sqlite3.connect(client.app.state.test_db_path)
        try:
            cur = conn.execute(
                "INSERT INTO annual_packages (property_id, budget_year, "
                "fiscal_year, status, assessment_setup_snapshot_json, "
                "budget_snapshot_json, reserve_snapshot_json, "
                "appendix_manifest_snapshot_json, compile_context_snapshot_json) "
                "VALUES (1, 2033, 2033, 'finalized', '{}', '{}', '{}', '{}', '{}')",
            )
            conn.commit()
            assert should_use_snapshots(package_id=cur.lastrowid, connection=conn) is False
        finally:
            conn.close()

    def test_snapshot_branch_wiring_present(self):
        """Source-level wiring assertions (same convention as
        test_compile_appendix_wiring): the render job selects the snapshot
        branch, never mutates live state on it, and hard-fails on a
        missing frozen appendix."""
        from pathlib import Path

        src = Path(dp_service.__file__).read_text()
        assert "should_use_snapshots(" in src
        assert "load_package_snapshots(" in src
        assert "Finalized package appendix is missing from storage" in src
        # the live-mutating materialization must be in the LIVE branch only
        snapshot_branch = src.split("Frozen-snapshot branch")[1].split("Live branch")[0]
        assert "_materialize_assessment_mappings_for_budget_draft" not in snapshot_branch
        assert "check_specified_value_placeholders" not in snapshot_branch


class TestSnapshotSourcedRender:
    def test_finalized_render_uses_frozen_inputs_not_live(
        self, client, monkeypatch, tmp_path
    ):
        """The C1 contract test the review found missing: finalize →
        mutate live data → re-render → every compile input comes from the
        frozen snapshot, and the audit records the snapshot branch."""
        from datetime import datetime, timezone

        from app.ai_implementation.db import session as session_module
        from app.disclosure_package.appendix_storage import appendix_file_path
        from app.disclosure_package.assessment_schedule_matrix import (
            build_universal_assessment_matrix,
        )
        from app.assessment_engine import CalcResultSet
        from app.disclosure_package.compiler import CompileResult
        from app.disclosure_package.snapshots import freeze_package_snapshots

        # -- freeze a real, model-validatable snapshot set --------------
        matrix = build_universal_assessment_matrix(
            CalcResultSet(
                pool_allocations=[], recipient_totals=[],
                rounding_delta_annual=Decimal("0"),
                rounding_delta_monthly=Decimal("0"),
                rounding_delta_percent=Decimal("0"),
                pool_sum_annual=Decimal("0"),
            ),
            setup_type="fixed",
            hoa_name="FROZEN HOA NAME",
            fiscal_year=2034,
            approved_visual_basis=False,
            manual_review_reason="frozen-fixture",
        )
        frozen = {
            "assessment_setup": {"setup": {"setup_type": "fixed"}},
            "budget": {
                "line_items": [
                    {"label": "FROZEN Dues", "amount": "60000",
                     "section": "income", "category": "income",
                     "is_revenue": True},
                ]
            },
            "reserve": {"study_date": "2026-01-01", "components": []},
            "appendix_manifest": [],
            "compile_context": {
                "assessment_matrix": matrix.model_dump(mode="json"),
                "hoa_metadata": {
                    "hoa_id": 1, "name": "FROZEN HOA NAME", "units": 10,
                    "fiscal_year_start_month": 1, "fiscal_year_end_month": 12,
                },
                "hoa_settings_overrides": {"management_company": "Frozen Mgmt"},
                "assessment_revenue_annual": "60000",
                "assessment_mode": "variable",
            },
        }

        package_id = _make_package(client, fiscal_year=2034)
        session = session_module.SessionLocal()
        try:
            raw = session.connection().connection
            freeze_package_snapshots(
                package_id=package_id,
                assessment_setup=frozen["assessment_setup"],
                budget=frozen["budget"],
                reserve=frozen["reserve"],
                appendix_manifest=frozen["appendix_manifest"],
                compile_context=frozen["compile_context"],
                connection=raw,
            )
        finally:
            session.close()

        # -- create the render job over the finalized package -----------
        import sqlite3
        import uuid

        job_id = str(uuid.uuid4())
        conn = sqlite3.connect(client.app.state.test_db_path)
        try:
            probe = conn.execute(
                "SELECT status, "
                "  length(assessment_setup_snapshot_json), "
                "  length(budget_snapshot_json), "
                "  length(reserve_snapshot_json), "
                "  length(appendix_manifest_snapshot_json), "
                "  length(compile_context_snapshot_json) "
                "FROM annual_packages WHERE id = ?",
                (package_id,),
            ).fetchone()
            assert should_use_snapshots(package_id=package_id, connection=conn), (
                f"snapshots not usable after freeze: {probe}"
            )
            conn.execute(
                "INSERT INTO disclosure_package_jobs "
                "(id, property_id, fiscal_year, status, annual_package_id) "
                "VALUES (?, 1, 2034, 'pending', ?)",
                (job_id, package_id),
            )
            conn.commit()
        finally:
            conn.close()

        # -- capture compile inputs instead of rendering -----------------
        captured: dict = {}

        def fake_compile(**kwargs):
            captured.update(kwargs)
            return CompileResult(
                output_path=tmp_path / "package.pdf",
                audit_path=tmp_path / "audit.json",
                intermediate_path=tmp_path / "generated.pdf",
                page_count=1,
                sha256="x" * 64,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        monkeypatch.setattr(dp_service, "compile_package", fake_compile)

        # NOTE: live state has NO active budget draft at all — if the
        # render read live state it would fail with LookupError. It must
        # succeed purely from the frozen snapshots.
        dp_service.run_render_job(
            "%s" % job_id, 1, 2034,
            session_factory=session_module.SessionLocal,
            annual_package_id=package_id,
        )

        conn = sqlite3.connect(client.app.state.test_db_path)
        try:
            debug_row = conn.execute(
                "SELECT status, error_message FROM disclosure_package_jobs "
                "WHERE id = ?",
                (job_id,),
            ).fetchone()
        finally:
            conn.close()
        assert captured, f"compile_package was never invoked; job={debug_row}"
        assert [li.label for li in captured["budget_draft"].line_items] == [
            "FROZEN Dues"
        ]
        assert captured["hoa_metadata"].name == "FROZEN HOA NAME"
        assert captured["hoa_settings_overrides"] == {
            "management_company": "Frozen Mgmt"
        }
        assert captured["audit_extra"]["compile_branch"] == "snapshot"
        assert captured["audit_extra"]["annual_package_id"] == package_id

        conn = sqlite3.connect(client.app.state.test_db_path)
        try:
            status_row = conn.execute(
                "SELECT status, error_message FROM disclosure_package_jobs "
                "WHERE id = ?",
                (job_id,),
            ).fetchone()
        finally:
            conn.close()
        assert status_row[0] == "completed"
        assert status_row[1] is None  # no stub warning on a real snapshot

    def test_missing_frozen_appendix_hard_fails_render(
        self, client, monkeypatch, tmp_path
    ):
        """A finalized package must never silently shrink: a frozen
        manifest entry whose file is gone fails the job loudly."""
        from decimal import Decimal as D

        from app.ai_implementation.db import session as session_module
        from app.assessment_engine import CalcResultSet
        from app.disclosure_package.assessment_schedule_matrix import (
            build_universal_assessment_matrix,
        )
        from app.disclosure_package.snapshots import freeze_package_snapshots

        matrix = build_universal_assessment_matrix(
            CalcResultSet(
                pool_allocations=[], recipient_totals=[],
                rounding_delta_annual=D("0"), rounding_delta_monthly=D("0"),
                rounding_delta_percent=D("0"), pool_sum_annual=D("0"),
            ),
            setup_type="fixed", hoa_name="X", fiscal_year=2035,
            approved_visual_basis=False, manual_review_reason="fixture",
        )
        frozen_manifest = [
            {"appendix_id": 9, "file_id": "appendices/1/9_pruned.pdf",
             "file_name": "pruned.pdf", "display_title": "Pruned Insurance Cert",
             "display_order": 1, "source": "default"},
        ]
        package_id = _make_package(client, fiscal_year=2035)
        session = session_module.SessionLocal()
        try:
            freeze_package_snapshots(
                package_id=package_id,
                assessment_setup={"setup": {}},
                budget={"line_items": [{"label": "Dues", "amount": "1"}]},
                reserve={"study_date": "2026-01-01", "components": []},
                appendix_manifest=frozen_manifest,
                compile_context={
                    "assessment_matrix": matrix.model_dump(mode="json"),
                    "hoa_metadata": {
                        "hoa_id": 1, "name": "X", "units": 10,
                        "fiscal_year_start_month": 1,
                        "fiscal_year_end_month": 12,
                    },
                    "hoa_settings_overrides": {},
                    "assessment_revenue_annual": "1",
                    "assessment_mode": "variable",
                },
                connection=session.connection().connection,
            )
        finally:
            session.close()

        import sqlite3
        import uuid

        job_id = str(uuid.uuid4())
        conn = sqlite3.connect(client.app.state.test_db_path)
        try:
            conn.execute(
                "INSERT INTO disclosure_package_jobs "
                "(id, property_id, fiscal_year, status, annual_package_id) "
                "VALUES (?, 1, 2035, 'pending', ?)",
                (job_id, package_id),
            )
            conn.commit()
        finally:
            conn.close()

        dp_service.run_render_job(
            job_id, 1, 2035,
            session_factory=session_module.SessionLocal,
            annual_package_id=package_id,
        )

        conn = sqlite3.connect(client.app.state.test_db_path)
        try:
            status_row = conn.execute(
                "SELECT status, error_message FROM disclosure_package_jobs "
                "WHERE id = ?",
                (job_id,),
            ).fetchone()
        finally:
            conn.close()
        assert status_row[0] == "failed"
        assert "Pruned Insurance Cert" in (status_row[1] or "")
