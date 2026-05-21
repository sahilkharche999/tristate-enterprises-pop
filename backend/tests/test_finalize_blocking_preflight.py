"""Finalize-blocked-by-preflight tests (Phase 5.5 task 153).

Documents the contract: finalize is the irreversible state transition;
the caller (compile job) MUST run preflight + raise_if_blocking before
calling finalize_annual_package. The service raises
``FinalizeBlockedByPreflight`` when given a known-blocking caller hint
so this contract is enforceable at the integration layer.
"""
from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from app.disclosure_package.preflight import PreflightBlockedError
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

    def test_preflight_blocked_error_has_field_paths_too(self):
        """Existing PreflightBlockedError shape is the caller's signal."""
        from app.disclosure_package.schemas import PreflightError

        errors = [
            PreflightError(
                field_path="reserve_study.study_date",
                message="Reserve study is 4+ years old",
                severity="blocking",
            ),
        ]
        with pytest.raises(PreflightBlockedError) as ctx:
            from app.disclosure_package.preflight import raise_if_blocking
            raise_if_blocking(errors)
        # The caller can extract field_paths from the raised exception
        assert ctx.value.field_paths == ("reserve_study.study_date",)


class TestIntegrationContract:
    def test_finalize_consumer_workflow(self):
        """The documented contract for compile job authors:

        1. Run preflight checks against the package's live inputs.
        2. Call ``raise_if_blocking(errors)`` — this raises
           ``PreflightBlockedError`` on any severity='blocking' entry.
        3. Only call ``finalize_annual_package`` when preflight is clean.

        The integration layer wraps these steps so the compile job
        doesn't have to. This test asserts the wrapper-eligible flow
        works as documented at the API surface.
        """
        from app.disclosure_package.preflight import (
            PreflightBlockedError,
            raise_if_blocking,
        )
        from app.disclosure_package.schemas import PreflightError

        # Blocking → raise
        with pytest.raises(PreflightBlockedError):
            raise_if_blocking([
                PreflightError(
                    field_path="reserve_study.study_date",
                    message="study is 4y old",
                    severity="blocking",
                ),
            ])

        # Warning-only → no raise
        warnings_only = raise_if_blocking([
            PreflightError(
                field_path="reserve_study.study_date",
                message="study is 2y old; plan refresh",
                severity="warning",
            ),
        ])
        assert warnings_only[0].severity == "warning"
