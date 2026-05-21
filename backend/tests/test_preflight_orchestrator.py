"""Preflight orchestrator tests (Phase 5.1).

Verifies that ``validate_inputs`` composes the existing budget/reserve/
HOA checks with the new ``check_reserve_study_age``, and that
``partition_errors`` + ``raise_if_blocking`` give callers a clean
halt-or-surface API.
"""
from __future__ import annotations

import pytest

from app.disclosure_package.preflight import (
    PreflightBlockedError,
    partition_errors,
    raise_if_blocking,
)
from app.disclosure_package.schemas import PreflightError


def _err(severity: str = "blocking", field: str = "x", msg: str = "") -> PreflightError:
    return PreflightError(field_path=field, message=msg, severity=severity)  # type: ignore[arg-type]


class TestPartitionErrors:
    def test_empty_input(self) -> None:
        assert partition_errors([]) == ([], [])

    def test_separates_blocking_from_warning(self) -> None:
        errs = [
            _err("blocking", "a"),
            _err("warning", "b"),
            _err("blocking", "c"),
            _err("warning", "d"),
        ]
        blocking, warnings = partition_errors(errs)
        assert [e.field_path for e in blocking] == ["a", "c"]
        assert [e.field_path for e in warnings] == ["b", "d"]

    def test_preserves_order_within_each_bucket(self) -> None:
        errs = [
            _err("warning", "w1"),
            _err("warning", "w2"),
            _err("blocking", "b1"),
            _err("blocking", "b2"),
        ]
        blocking, warnings = partition_errors(errs)
        assert [e.field_path for e in blocking] == ["b1", "b2"]
        assert [e.field_path for e in warnings] == ["w1", "w2"]


class TestRaiseIfBlocking:
    def test_returns_warnings_when_no_blocking(self) -> None:
        warnings = raise_if_blocking([_err("warning", "w1"), _err("warning", "w2")])
        assert [e.field_path for e in warnings] == ["w1", "w2"]

    def test_raises_with_field_paths(self) -> None:
        with pytest.raises(PreflightBlockedError) as ctx:
            raise_if_blocking([
                _err("warning", "ok"),
                _err("blocking", "reserve_study", msg="too old"),
                _err("blocking", "budget_draft", msg="missing"),
            ])
        assert ctx.value.field_paths == ("reserve_study", "budget_draft")
        assert len(ctx.value.blocking) == 2
        # Message includes each blocker's path + message
        assert "reserve_study: too old" in str(ctx.value)
        assert "budget_draft: missing" in str(ctx.value)


class TestValidateInputsComposesReserveStudyCheck:
    """The existing ``validate_inputs`` orchestrator MUST now include
    the §5550 reserve-study age check alongside its original four
    gates. Build a minimal valid input set and flip just the reserve
    study date to confirm the new check fires.
    """

    def _build_inputs(self, *, study_date: str, fiscal_year: int):
        # Local imports to avoid touching the rest of the schemas
        # surface for tests that don't need them.
        from decimal import Decimal
        from app.disclosure_package.schemas import (
            BudgetDraft,
            HOAMetadata,
            HOAStaticData,
            LineItem,
            PackageSpec,
            ReserveStudyComponent,
            ReserveStudySnapshot,
            GeneratedPage,
        )

        spec = PackageSpec(
            hoa_id=1,
            fiscal_year=fiscal_year,
            static_data=HOAStaticData(
                hoa_legal_name="Test HOA",
                address_line_1="1 Main",
                address_line_2="",
                city="X",
                state="CA",
                zip="00000",
                management_company="MC",
                management_company_address="MC addr",
                cpa_firm_name="CPA",
                cpa_firm_address="CPA addr",
                reserve_study_expert_name="RSE",
                monthly_assessment_per_unit_current=Decimal("100"),
                monthly_assessment_per_unit_prior=Decimal("100"),
                reserve_cash_balance_eoy_prior=Decimal("50000"),
                bank_cd_balance_for_interest=Decimal("0"),
                income_tax_provision_estimate=Decimal("0"),
                interest_rate_after_tax=Decimal("0.01"),
                replacement_cost_increase_rate=Decimal("0.03"),
                assessment_increase_schedule=[(fiscal_year, fiscal_year + 9, Decimal("0.03"))],
                letter_date="2026-01-01",
                letter_signed_by="Board",
            ),
            entries=[GeneratedPage(template="x.html", page_count_hint=1)],
        )
        budget = BudgetDraft(line_items=[LineItem(label="HOA Dues", amount=Decimal("12000"))])
        reserve = ReserveStudySnapshot(
            study_date=study_date,
            components=[
                ReserveStudyComponent(
                    line_item="Roof",
                    remaining_life=10,
                    useful_life=20,
                    replacement_cost=Decimal("100000"),
                )
            ],
        )
        hoa = HOAMetadata(
            hoa_id=1, name="Test", units=10,
            fiscal_year_start_month=1, fiscal_year_end_month=12,
        )
        return spec, budget, reserve, hoa

    def test_old_reserve_study_blocks(self) -> None:
        from app.disclosure_package.preflight import validate_inputs

        spec, budget, reserve, hoa = self._build_inputs(
            study_date="2020-01-01", fiscal_year=2026,
        )
        errors = validate_inputs(
            spec=spec, budget_draft=budget,
            reserve_snapshot=reserve, hoa_metadata=hoa,
        )
        blocking_paths = [e.field_path for e in errors if e.severity == "blocking"]
        assert "hoa_settings.reserve_study_date" in blocking_paths

    def test_fresh_reserve_study_passes_check(self) -> None:
        from app.disclosure_package.preflight import validate_inputs

        spec, budget, reserve, hoa = self._build_inputs(
            study_date="2025-06-15", fiscal_year=2026,
        )
        errors = validate_inputs(
            spec=spec, budget_draft=budget,
            reserve_snapshot=reserve, hoa_metadata=hoa,
        )
        # No findings from the §5550 check; other gates also pass with this fixture
        reserve_findings = [
            e for e in errors if e.field_path == "hoa_settings.reserve_study_date"
        ]
        assert reserve_findings == []
