"""Preflight: reserve study staleness (Phase 5.7, CA §5550).

Adopts the existing ``PreflightError`` data shape used by
``disclosure_package.preflight`` so the new check composes cleanly
with the older budget/reserve/HOA checks.
"""
from __future__ import annotations

from app.disclosure_package.preflight import check_reserve_study_age
from app.disclosure_package.schemas import PreflightError


def _codes_and_severities(results: list[PreflightError]) -> list[tuple[str, str]]:
    """Compact view for assertion: (field_path, severity) per error."""
    return [(r.field_path, r.severity) for r in results]


class TestReserveStudyAge:
    def test_fresh_study_no_findings(self) -> None:
        assert (
            check_reserve_study_age(
                reserve_study_date="2025-04-15",
                package_fiscal_year=2026,
            )
            == []
        )

    def test_two_year_old_emits_warning(self) -> None:
        results = check_reserve_study_age(
            reserve_study_date="2024-04-15",
            package_fiscal_year=2026,
        )
        assert _codes_and_severities(results) == [
            ("hoa_settings.reserve_study_date", "warning")
        ]
        assert "2 years old" in results[0].message

    def test_three_year_old_emits_warning_not_block(self) -> None:
        results = check_reserve_study_age(
            reserve_study_date="2023-01-01",
            package_fiscal_year=2026,
        )
        assert _codes_and_severities(results) == [
            ("hoa_settings.reserve_study_date", "warning")
        ]
        assert "exactly 3 years old" in results[0].message

    def test_over_three_years_blocks(self) -> None:
        results = check_reserve_study_age(
            reserve_study_date="2022-04-15",
            package_fiscal_year=2026,
        )
        assert _codes_and_severities(results) == [
            ("hoa_settings.reserve_study_date", "blocking")
        ]
        assert "§5550" in results[0].message

    def test_missing_date_warns(self) -> None:
        results = check_reserve_study_age(
            reserve_study_date=None,
            package_fiscal_year=2026,
        )
        assert _codes_and_severities(results) == [
            ("hoa_settings.reserve_study_date", "warning")
        ]
        assert "missing" in results[0].message.lower()

    def test_empty_string_date_warns(self) -> None:
        results = check_reserve_study_age(
            reserve_study_date="   ",
            package_fiscal_year=2026,
        )
        assert results[0].severity == "warning"
        assert "missing" in results[0].message.lower()

    def test_unparseable_date_warns(self) -> None:
        results = check_reserve_study_age(
            reserve_study_date="last Tuesday",
            package_fiscal_year=2026,
        )
        assert results[0].severity == "warning"
        assert "unparseable" in results[0].message.lower()

    def test_us_format_date_accepted(self) -> None:
        # MM/DD/YYYY: 04/15/2025 → fresh for FY 2026
        assert (
            check_reserve_study_age(
                reserve_study_date="04/15/2025",
                package_fiscal_year=2026,
            )
            == []
        )

    def test_calendar_year_difference_used(self) -> None:
        # 2023 study at FY 2027 → 4 calendar years apart → blocking,
        # regardless of in-year month/day. This is the conservative
        # statutory reading: a 2023 study can be used in FY 2026 but
        # not FY 2027.
        results = check_reserve_study_age(
            reserve_study_date="2023-06-01",
            package_fiscal_year=2027,
        )
        assert results[0].severity == "blocking"
