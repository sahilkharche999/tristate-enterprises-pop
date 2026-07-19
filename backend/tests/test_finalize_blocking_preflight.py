"""Finalize-blocked-by-preflight tests (Phase 5.5 task 153).

Documents the contract: finalize is the irreversible state transition;
the caller MUST run preflight (via ``run_preflight`` / ``partition_errors``)
before calling finalize_annual_package. The service raises
``FinalizeBlockedByPreflight`` when given a known-blocking caller hint
so this contract is enforceable at the integration layer.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.services.annual_package_service import (
    FinalizeBlockedByPreflight,
)


SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "ai_implementation" / "schema.sql"
)


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.executescript(SCHEMA_PATH.read_text())
    yield conn
    conn.close()


class TestPreflightBlockingErrorShape:
    def test_finalize_blocked_carries_field_paths(self):
        err = FinalizeBlockedByPreflight(
            package_id=42,
            field_paths=["reserve_study.study_date", "appendix.annual_required_missing"],
        )
        assert err.package_id == 42
        assert "reserve_study" in err.field_paths[0]
        assert "42" in str(err)

    def test_partition_errors_splits_blocking_for_finalize_gate(self):
        """Live finalize path uses partition_errors / run_preflight, not raise_if_blocking."""
        from app.disclosure_package.preflight import partition_errors
        from app.disclosure_package.schemas import PreflightError

        errors = [
            PreflightError(
                field_path="reserve_study.study_date",
                message="Reserve study is 4+ years old",
                severity="blocking",
            ),
            PreflightError(
                field_path="other",
                message="warn",
                severity="warning",
            ),
        ]
        blocking, warnings = partition_errors(errors)
        assert [e.field_path for e in blocking] == ["reserve_study.study_date"]
        assert [e.field_path for e in warnings] == ["other"]
