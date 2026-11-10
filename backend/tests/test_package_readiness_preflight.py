"""Package readiness: mapping gate, zero-cash warning, steps payload."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.disclosure_package.schemas import PreflightError
from app.disclosure_package import service as dp_service


def test_attach_fix_links_mapping_path():
    errs = [
        PreflightError(
            field_path="assessment_mapping_review",
            message="blocked",
            severity="blocking",
            code="assessment_mapping_blocked",
        )
    ]
    out = dp_service._attach_fix_links(errs, hoa_id=4)
    assert out[0].fix_path == "/hoa/4/assessment-mapping-review"
    assert out[0].fix_label


def test_build_readiness_steps_mapping_not_required_for_fixed():
    steps = dp_service._build_readiness_steps(
        hoa_id=1,
        blocking=[],
        warnings=[],
        mapping_step_status="not_required",
        has_budget=True,
        has_reserve_components=True,
    )
    mapping = next(s for s in steps if s["id"] == "assessment_mapping")
    assert mapping["status"] == "not_required"
    budget = next(s for s in steps if s["id"] == "budget_draft")
    assert budget["status"] == "done"


def test_attach_fix_links_reserve_findings():
    out = dp_service._attach_fix_links(
        [
            PreflightError(
                field_path="reserve_study_snapshot.funding_plan_rows",
                message="missing row",
                severity="warning",
            ),
            PreflightError(field_path="reserve_funding.source", message="conflict", severity="blocking"),
            PreflightError(field_path="reserve_interest_income", message="missing", severity="warning"),
        ],
        hoa_id=4,
    )
    assert out[0].fix_path == "/hoa/4?view=reserve"
    assert "field=reserve_funding_source" in out[1].fix_path
    assert "field=reserve_interest_income_override" in out[2].fix_path


def _readiness_step(step_id, *, blocking=(), warnings=(), has_reserve_components=True):
    steps = dp_service._build_readiness_steps(
        hoa_id=2,
        blocking=list(blocking),
        warnings=list(warnings),
        mapping_step_status="not_required",
        has_budget=True,
        has_reserve_components=has_reserve_components,
    )
    return next(s for s in steps if s["id"] == step_id)


def test_reserve_card_names_the_real_problem_when_a_study_is_attached():
    warning = PreflightError(
        field_path="reserve_study_snapshot.funding_plan_rows",
        message="No funding plan row for 2027",
        severity="warning",
    )
    reserve = _readiness_step("reserve_study", warnings=[warning])
    assert reserve["status"] == "warning"
    assert reserve["detail"] == "No funding plan row for 2027"
    # A reserve-study finding is not a Settings problem.
    assert _readiness_step("disclosure_settings", warnings=[warning])["status"] == "done"


def test_reserve_card_needs_action_when_a_reserve_finding_blocks():
    finding = PreflightError(
        field_path="reserve_study_snapshot.components",
        message="Row was dropped",
        severity="blocking",
    )
    reserve = _readiness_step("reserve_study", blocking=[finding])
    assert reserve["status"] == "needs_action"
    assert reserve["detail"] == "Row was dropped"


def test_settings_card_names_its_finding():
    finding = PreflightError(
        field_path="hoa_settings.reserve_study_date",
        message="Reserve study date is missing.",
        severity="warning",
    )
    settings = _readiness_step("disclosure_settings", warnings=[finding])
    assert settings["status"] == "warning"
    assert settings["detail"] == "Reserve study date is missing."


def test_build_readiness_steps_budget_needs_action():
    blocking = [
        PreflightError(
            field_path="budget_draft.line_items",
            message="No draft",
            severity="blocking",
        )
    ]
    steps = dp_service._build_readiness_steps(
        hoa_id=2,
        blocking=blocking,
        warnings=[],
        mapping_step_status="needs_action",
        has_budget=False,
        has_reserve_components=False,
    )
    assert next(s for s in steps if s["id"] == "budget_draft")["status"] == "needs_action"


def test_assessment_mapping_fixed_mode_not_required(monkeypatch):
    session = MagicMock()
    conn = MagicMock()
    session.connection.return_value.connection = conn
    conn.execute.return_value.fetchone.return_value = ("fixed", None)

    errors, status = dp_service._assessment_mapping_preflight_errors(
        session, hoa_id=9, fiscal_year=2026
    )
    assert errors == []
    assert status == "not_required"


def test_assessment_mapping_variable_missing_setup(monkeypatch):
    session = MagicMock()
    conn = MagicMock()
    session.connection.return_value.connection = conn

    def _execute(sql, params=None):
        m = MagicMock()
        sql_l = str(sql).lower()
        if "assessment_mode" in sql_l:
            m.fetchone.return_value = ("variable", None)
        elif "assessment_setups" in sql_l:
            m.fetchone.return_value = None
        else:
            m.fetchone.return_value = None
        return m

    conn.execute.side_effect = _execute
    errors, status = dp_service._assessment_mapping_preflight_errors(
        session, hoa_id=9, fiscal_year=2026
    )
    assert status == "needs_action"
    assert errors and errors[0].code == "assessment_setup_missing"


def test_zero_cash_is_warning_not_in_blocking_partition():
    from app.disclosure_package.preflight import partition_errors

    errors = [
        PreflightError(
            field_path="hoa_settings.reserve_cash_balance_eoy_prior",
            message="cash is 0",
            severity="warning",
            code="reserve_cash_zero",
        )
    ]
    blocking, warnings = partition_errors(errors)
    assert blocking == []
    assert len(warnings) == 1
