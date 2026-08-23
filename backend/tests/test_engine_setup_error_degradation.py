"""H3 — engine setup errors degrade to the review fallback, not a crash.

`UnsupportedAllocationMethod`, `IncompleteSetupError`, and
`MissingSpecifiedValue` share the `EngineSetupError` base, and the matrix
boundary catches that base so a bad setup produces the operator-review
fallback matrix (with a named reason) instead of an unhandled exception
that fails the render job.
"""
from __future__ import annotations

import app.disclosure_package.assessment_schedule_matrix as matrix_mod
from app.assessment_engine.errors import (
    EngineSetupError,
    IncompleteSetupError,
    UnsupportedAllocationMethod,
)
from app.assessment_engine.pools import MissingSpecifiedValue

from tests.test_assessment_schedule_matrix import (
    _build_800_high_connection,
    _run_800_high_matrix,
)


def test_engine_setup_errors_share_base_class() -> None:
    assert issubclass(UnsupportedAllocationMethod, EngineSetupError)
    assert issubclass(IncompleteSetupError, EngineSetupError)
    assert issubclass(MissingSpecifiedValue, EngineSetupError)
    # Still ordinary exceptions, so existing `except Exception` paths hold.
    assert issubclass(EngineSetupError, Exception)


def _assert_manual_review_named(monkeypatch, exc: Exception, needle: str) -> None:
    def _boom(*_args, **_kwargs):
        raise exc

    monkeypatch.setattr(matrix_mod, "run_assessment_engine", _boom)
    conn = _build_800_high_connection()
    matrix = _run_800_high_matrix(conn)
    assert matrix.recipient_grain == "manual_review"
    reason = matrix.rows[0].missing_basis_reason
    assert needle in reason
    # A blocking issue is present so the compiler gate stops the render with
    # this named message (not an unhandled crash).
    assert any(i.severity == "blocking" for i in matrix.preflight_issues)


def test_missing_specified_value_degrades_with_named_reason(monkeypatch) -> None:
    _assert_manual_review_named(
        monkeypatch,
        MissingSpecifiedValue(unit_id=101, pool_key="ownership_pool"),
        needle="ownership_pool",
    )


def test_unsupported_allocation_method_degrades_with_named_reason(monkeypatch) -> None:
    _assert_manual_review_named(
        monkeypatch,
        UnsupportedAllocationMethod("mystery_method", "some_pool"),
        needle="mystery_method",
    )
