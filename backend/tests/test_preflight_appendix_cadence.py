"""Preflight: appendix cadence + annual replacement (Phase 5.6)."""
from __future__ import annotations

import pytest

from app.disclosure_package.preflight import check_appendix_cadence


def _doc(**kwargs) -> dict:
    """Build a minimal appendix-document dict with sensible defaults."""
    return {
        "display_title": kwargs.get("display_title", "Doc"),
        "file_name": kwargs.get("file_name", "doc.pdf"),
        "cadence": kwargs.get("cadence", "persistent"),
        "annual_year": kwargs.get("annual_year"),
        "valid_through_year": kwargs.get("valid_through_year"),
        "required_flag": kwargs.get("required_flag", False),
    }


class TestPersistentAppendicesAlwaysOK:
    def test_no_findings_when_only_persistent(self) -> None:
        docs = [
            _doc(display_title="ADR Policy"),
            _doc(display_title="Election Rules"),
        ]
        assert check_appendix_cadence(
            appendix_documents=docs, package_fiscal_year=2026
        ) == []


class TestAnnualMissingForPackageYear:
    def test_required_annual_missing_blocks(self) -> None:
        docs = [
            _doc(
                display_title="Annual Insurance Disclosure",
                cadence="annual",
                annual_year=2025,  # last year's, not this year's
                required_flag=True,
            ),
        ]
        results = check_appendix_cadence(
            appendix_documents=docs, package_fiscal_year=2026
        )
        assert len(results) == 1
        assert results[0].severity == "blocking"
        assert "annual_required_missing" in results[0].field_path
        assert "2026" in results[0].message

    def test_required_annual_for_year_present_passes(self) -> None:
        docs = [
            _doc(
                display_title="Annual Insurance Disclosure",
                cadence="annual",
                annual_year=2026,
                required_flag=True,
            ),
        ]
        assert check_appendix_cadence(
            appendix_documents=docs, package_fiscal_year=2026
        ) == []

    def test_optional_annual_missing_does_not_block(self) -> None:
        # required_flag=False — operator can ship without uploading this year's copy
        docs = [
            _doc(
                display_title="Optional Annual Memo",
                cadence="annual",
                annual_year=2025,
                required_flag=False,
            ),
        ]
        assert check_appendix_cadence(
            appendix_documents=docs, package_fiscal_year=2026
        ) == []


class TestValidThroughYear:
    def test_expired_appendix_blocks(self) -> None:
        docs = [
            _doc(
                display_title="Election Rules",
                valid_through_year=2024,
            ),
        ]
        results = check_appendix_cadence(
            appendix_documents=docs, package_fiscal_year=2026
        )
        assert len(results) == 1
        assert results[0].severity == "blocking"
        assert "valid_through_year" in results[0].field_path
        assert "Election Rules" in results[0].message

    def test_current_year_within_valid_through_passes(self) -> None:
        docs = [
            _doc(display_title="Rules", valid_through_year=2028),
        ]
        assert check_appendix_cadence(
            appendix_documents=docs, package_fiscal_year=2026
        ) == []

    def test_package_year_equal_to_valid_through_passes(self) -> None:
        # boundary: valid_through_year=2026 means "still valid through end of 2026"
        docs = [
            _doc(display_title="Rules", valid_through_year=2026),
        ]
        assert check_appendix_cadence(
            appendix_documents=docs, package_fiscal_year=2026
        ) == []


class TestMixedManifest:
    def test_one_missing_annual_plus_one_expired_two_findings(self) -> None:
        docs = [
            _doc(
                display_title="Annual Insurance",
                cadence="annual",
                annual_year=2024,
                required_flag=True,
            ),
            _doc(display_title="ADR Policy", valid_through_year=2024),
            _doc(display_title="Election Rules"),  # persistent, OK
        ]
        results = check_appendix_cadence(
            appendix_documents=docs, package_fiscal_year=2026
        )
        assert len(results) == 2
        codes = {r.field_path for r in results}
        assert "appendix.ADR Policy.valid_through_year" in codes
        assert "appendix.annual_required_missing" in codes
