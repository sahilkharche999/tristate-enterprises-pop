"""GET /hoa/{id}/documents/chip-values — the editor's chip popover data.

This endpoint answers "what will actually print here?" for a chip the operator
is looking at. It is deliberately more forgiving than generation: an HOA with
no budget yet still gets its name, dates and CPA details back. What it must
never do is invent a figure — `build_var_map` renders unknown money as
`$0.00`, and an operator reading that as real would be worse off than seeing
nothing at all.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.ai_implementation.db.models import Property
from app.disclosure_package import service as dp_service
from app.disclosure_package.schemas import (
    BudgetDraft,
    HOAMetadata,
    LineItem,
    ReserveStudySnapshot,
    ReserveStudyComponent,
)
from app.services import boilerplate_variables as bv


@pytest.fixture
def hoa(db_session):
    row = Property(name="Chip Preview HOA", units=10, hoa_code="CHIP", city="San Jose")
    db_session.add(row)
    db_session.commit()
    return row


def _values(client, hoa_id, **params):
    r = client.get(f"/hoa/{hoa_id}/documents/chip-values", params=params)
    assert r.status_code == 200, r.text
    return r.json()


# ── degraded tier: no budget draft ──────────────────────────────────────────


def test_no_budget_still_returns_the_facts_that_need_no_compute(client, hoa):
    body = _values(client, hoa.id)
    assert body["computed_available"] is False
    assert body["unavailable_reason"]

    values = body["values"]
    assert values["hoa_name"] == "Chip Preview HOA"
    assert values["hoa_city"] == "San Jose"
    assert values["hoa_units"] == "10"
    assert values["hoa_units_word"] == "units"


def test_computed_figures_are_withheld_not_zeroed(client, hoa):
    """The whole point: `$0.00` and "we don't know" must not look the same."""
    values = _values(client, hoa.id)["values"]
    for chip in (
        "reserve_monthly_contribution",
        "percent_funded",
        "under_funded_balance",
        "total_estimated_liability",
        "income_tax_provision",
    ):
        assert chip not in values, f"{chip} previewed a placeholder figure"


def test_matrix_dependent_sentences_are_withheld(client, hoa):
    values = _values(client, hoa.id)["values"]
    assert "assessment_line" not in values
    assert "assessment_basis_sentence" not in values


def test_settings_values_appear_once_saved(client, hoa):
    client.put(
        f"/hoa/{hoa.id}/settings/disclosure",
        json={"cpa_firm_name": "Chip & Co LLP", "management_company": "Chip Mgmt"},
    )
    values = _values(client, hoa.id)["values"]
    assert values["cpa_firm_name"] == "Chip & Co LLP"
    assert values["cpa_firm_name_short"] == "Chip & Co"
    assert values["management_company"] == "Chip Mgmt"


def test_fiscal_year_defaults_and_is_overridable(client, hoa):
    from datetime import datetime

    assert _values(client, hoa.id)["fiscal_year"] == datetime.now().year

    body = _values(client, hoa.id, fiscal_year=2031)
    assert body["fiscal_year"] == 2031
    assert body["values"]["fiscal_year"] == "2031"
    assert body["values"]["prior_year"] == "2030"
    assert body["values"]["final_forecast_year"] == "2060"


def test_page_numbers_preview_as_pending(client, hoa):
    """They are only real after a render pass; an em dash says so."""
    assert _values(client, hoa.id)["values"]["page_note_6"] == "—"


def test_unknown_hoa_is_404(client):
    assert client.get("/hoa/999999/documents/chip-values").status_code == 404


# ── full tier: compute succeeds ─────────────────────────────────────────────


@pytest.fixture
def with_compute(monkeypatch, hoa):
    """Stand in a resolvable input bundle so `_compute_all` actually runs."""
    from app.disclosure_package.package_specs import OLD_MILL_2026

    bundle = dp_service._PreflightInputBundle(
        spec=OLD_MILL_2026.model_copy(
            update={"hoa_id": hoa.id, "fiscal_year": 2026}
        ),
        budget_draft=BudgetDraft(
            line_items=[
                LineItem(
                    label="Member assessments",
                    amount=Decimal("120000"),
                    is_revenue=True,
                ),
                LineItem(
                    label="Replacement contributions",
                    amount=Decimal("60000"),
                    is_reserve=True,
                    is_revenue=True,
                ),
                LineItem(
                    label="Landscaping",
                    amount=Decimal("40000"),
                    section="Maintenance and operations",
                ),
            ]
        ),
        reserve_snapshot=ReserveStudySnapshot(
            study_date="September 2025",
            components=[
                ReserveStudyComponent(
                    line_item="Roofing",
                    useful_life=25,
                    remaining_life=10,
                    replacement_cost=Decimal("500000"),
                    year_new=2010,
                )
            ],
        ),
        hoa_metadata=HOAMetadata(
            hoa_id=hoa.id,
            name="Chip Preview HOA",
            units=10,
            fiscal_year_start_month=1,
            fiscal_year_end_month=12,
        ),
        overrides={"cpa_firm_name": "Chip & Co LLP"},
        narrative={},
    )
    monkeypatch.setattr(
        dp_service, "_resolve_preflight_inputs", lambda *a, **k: bundle
    )
    return hoa


def test_computed_figures_appear_once_the_compute_succeeds(client, with_compute):
    body = _values(client, with_compute.id, fiscal_year=2026)
    assert body["computed_available"] is True
    assert body["unavailable_reason"] is None

    values = body["values"]
    assert values["reserve_monthly_contribution"].startswith("$")
    assert values["percent_funded"].endswith("%")
    assert values["cpa_firm_name"] == "Chip & Co LLP"


def test_matrix_dependent_sentences_stay_withheld_even_with_a_compute(
    client, with_compute
):
    """The matrix needs a write to build, which a GET may not do — so these
    two stay unpreviewed rather than defaulting to the wrong sentence."""
    values = _values(client, with_compute.id, fiscal_year=2026)["values"]
    for chip in bv.MATRIX_DEPENDENT_CHIPS:
        assert chip not in values


def test_a_broken_compute_degrades_instead_of_500ing(client, with_compute, monkeypatch):
    """Inputs resolve but the math throws. A popover must not take the page
    down, and must not present the resulting zeros as figures."""
    def _boom(*a, **k):
        raise RuntimeError("reserve math exploded")

    monkeypatch.setattr(dp_service, "_compute_all", _boom)

    body = _values(client, with_compute.id, fiscal_year=2026)
    assert body["computed_available"] is False
    assert "exploded" in body["unavailable_reason"]
    # Settings-sourced facts still come back; computed ones do not.
    assert body["values"]["cpa_firm_name"] == "Chip & Co LLP"
    assert "percent_funded" not in body["values"]


# ── provenance rides along on the document payload ──────────────────────────


def test_document_payload_carries_chip_provenance(client, hoa):
    body = client.get(f"/hoa/{hoa.id}/documents").json()
    by_id = {v["id"]: v for v in body["variables"]}

    cpa = by_id["cpa_firm_name"]
    assert cpa["source"] == "settings"
    assert cpa["settings_field"] == "cpa_firm_name"
    assert cpa["settings_tab"] == "disclosure"
    assert cpa["source_note"]

    name = by_id["hoa_name"]
    assert name["source"] == "property"
    assert name["settings_tab"] == "database"

    year = by_id["fiscal_year"]
    assert year["source"] == "derived"
    assert year["settings_field"] is None
    assert year["settings_tab"] is None

    blocks = {b["id"]: b for b in body["blocks"]}
    assert blocks["special_assessment_disclosure"]["source"] == "computed"
    assert blocks["special_assessment_disclosure"]["source_note"]
