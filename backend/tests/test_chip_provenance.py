"""Chip provenance — what the editor's chip popover is allowed to claim.

The popover answers two questions: what will print here, and where do I change
it. Both answers come from `CHIP_SOURCES`, which is hand-written alongside
`build_var_map`. These tests are what stops the two drifting: a new chip with
no source entry, or an "Edit in settings" link pointing at a field that isn't
rendered, would each ship a confidently wrong answer.
"""
from __future__ import annotations

import pytest

from app.services import boilerplate_variables as bv

# Every key in HOADisclosureSettingsForm's `scalarFields`, plus the two
# selects it renders below them. A `settings_field` outside this set has no
# input to scroll to, so the link would dead-end.
DISCLOSURE_FORM_FIELDS = {
    "management_company",
    "management_company_address",
    "management_company_phone",
    "management_company_fax",
    "management_company_web",
    "cpa_firm_name",
    "cpa_firm_address",
    "reserve_study_expert_name",
    "reserve_study_date",
    "letter_signed_by",
    "reserve_cash_balance_eoy_prior",
    "fund_balance_boy_operations",
    "monthly_assessment_per_unit_prior",
    "interest_rate_after_tax",
    "replacement_fund_monthly_assessment_per_unit",
    "approved_monthly_assessment_per_unit",
    "reserve_interest_income_override",
    "income_tax_provision_override",
    "reserve_funding_manual_amount",
    "letter_date",
    "accountant_report_date",
    "reserve_funding_plan_date",
    "financial_packet_archetype",
    "reserve_funding_source",
    # Rendered by the form's own sub-editors rather than the scalar loop.
    "special_assessments_json",
    "additional_assessments_needed_json",
    "outstanding_loan_json",
    "assessment_increase_schedule_json",
}

# Input ids on the HOA record form (SettingsScreen's "database" tab).
PROPERTY_FORM_FIELDS = {"hoaName", "units", "city", "taxId", "fiscalYearStart"}

VALID_KINDS = {"settings", "property", "computed", "derived"}


# ── the registry covers the catalogs ────────────────────────────────────────


@pytest.mark.parametrize("chip", sorted(bv.TOKEN_CATALOG))
def test_every_value_chip_has_a_source(chip):
    assert chip in bv.CHIP_SOURCES, (
        f"{chip!r} is in TOKEN_CATALOG but has no CHIP_SOURCES entry — its "
        "popover would claim it is computed when it may not be."
    )


@pytest.mark.parametrize("chip", sorted(bv.BLOCK_CATALOG))
def test_every_block_chip_has_a_source(chip):
    assert chip in bv.BLOCK_SOURCES


def test_no_source_entry_outlives_its_chip():
    assert set(bv.CHIP_SOURCES) == set(bv.TOKEN_CATALOG)
    assert set(bv.BLOCK_SOURCES) == set(bv.BLOCK_CATALOG)


# ── each entry is internally coherent ───────────────────────────────────────


@pytest.mark.parametrize("chip", sorted(bv.TOKEN_CATALOG) + sorted(bv.BLOCK_CATALOG))
def test_source_kind_and_note_are_populated(chip):
    source = bv.chip_source(chip)
    assert source.kind in VALID_KINDS
    assert source.note.strip(), f"{chip!r} has no note to show in the popover"


@pytest.mark.parametrize("chip", sorted(bv.TOKEN_CATALOG) + sorted(bv.BLOCK_CATALOG))
def test_settings_field_points_at_a_rendered_input(chip):
    """A dead "Edit in settings" link is worse than no link at all."""
    source = bv.chip_source(chip)
    if source.field is None:
        return
    allowed = (
        PROPERTY_FORM_FIELDS if source.kind == "property" else DISCLOSURE_FORM_FIELDS
    )
    assert source.field in allowed, (
        f"{chip!r} links to {source.field!r}, which no form renders"
    )


def test_derived_chips_never_offer_an_edit_link():
    """Fiscal year and page numbers fall out of the package; nothing to edit."""
    for chip, source in bv.CHIP_SOURCES.items():
        if source.kind == "derived":
            assert source.field is None, f"{chip} offers a link but is derived"


def test_tab_matches_kind():
    for chip in bv.TOKEN_CATALOG:
        source = bv.chip_source(chip)
        if source.field is None:
            assert source.tab is None
        elif source.kind == "property":
            assert source.tab == "database"
        else:
            assert source.tab == "disclosure"


def test_page_number_chips_are_derived():
    for chip in bv.TOC_PAGE_TOKENS:
        assert bv.chip_source(chip).kind == "derived"


def test_unknown_chip_degrades_to_computed_without_a_link():
    source = bv.chip_source("not_a_chip")
    assert source.kind == "computed"
    assert source.field is None


# ── previewable_values: the "$0.00 means unknown" trap ──────────────────────


def _var_map_without_compute():
    """The map `build_var_map` produces when nothing has been computed."""
    class _Hoa:
        name, city, state, units = "Preview HOA", "San Jose", "CA", 10
        entity_type = incorporation_year = None

    return bv.build_var_map(
        hoa=_Hoa(), fiscal_year=2026, hoa_settings={"cpa_firm_name": "Some CPA LLP"},
        computed={}, today="Monday January 1, 2026",
    )


def test_uncomputed_money_formats_as_zero():
    """The behavior `previewable_values` exists to defend against — if this
    ever stops being true, the filtering below can be relaxed."""
    assert _var_map_without_compute()["reserve_monthly_contribution"] == "$0.00"
    assert _var_map_without_compute()["percent_funded"] == "%"


def test_computed_chips_are_withheld_rather_than_shown_as_zero():
    values = bv.previewable_values(
        _var_map_without_compute(), computed_available=False
    )
    assert "reserve_monthly_contribution" not in values
    assert "percent_funded" not in values
    assert "under_funded_balance" not in values


def test_settings_and_property_chips_survive_without_a_compute():
    values = bv.previewable_values(
        _var_map_without_compute(), computed_available=False
    )
    assert values["hoa_name"] == "Preview HOA"
    assert values["cpa_firm_name"] == "Some CPA LLP"
    assert values["fiscal_year"] == "2026"


def test_computed_chips_return_once_a_compute_succeeded():
    values = bv.previewable_values(
        _var_map_without_compute(), computed_available=True
    )
    assert "reserve_monthly_contribution" in values


def test_matrix_dependent_chips_are_withheld_without_a_matrix():
    """Their fallback is a plausible *wrong* sentence, not a blank — so an
    unbuilt matrix must withhold them even when everything else computed."""
    values = bv.previewable_values(
        _var_map_without_compute(), computed_available=True, matrix_available=False
    )
    for chip in bv.MATRIX_DEPENDENT_CHIPS:
        assert chip not in values

    with_matrix = bv.previewable_values(
        _var_map_without_compute(), computed_available=True, matrix_available=True
    )
    assert bv.MATRIX_DEPENDENT_CHIPS <= set(with_matrix)
