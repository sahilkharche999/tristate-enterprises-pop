"""Money chips must resolve to the computed figures, not to zero.

`_compute_all` returns a *wrapper* — ``{computed, budget_draft, hoa_metadata,
reserve_study_snapshot}`` — and the render context splats it (``**computed``)
rather than nesting it. `build_var_map` wants the facts themselves: it reads
``reserve_liability_facts`` / ``reserve_funding_facts`` / ``presentation_facts``
as top-level keys.

Handing it the wrapper does not raise. Every lookup misses, `_money` turns the
resulting ``None`` into ``"0.00"``, and Note 5 renders a statutory reserve
disclosure reading $0 across the board — silently, in a legal document.

That is exactly what shipped, so these tests assert the figures, not just that
resolution succeeded. `test_baselines_render_real_figures` is the one that
would have caught it.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.disclosure_package.compiler import _compute_all
from app.disclosure_package.package_specs import OLD_MILL_2026
from app.disclosure_package.schemas import (
    BudgetDraft,
    HOAMetadata,
    LineItem,
    ReserveStudyComponent,
    ReserveStudySnapshot,
)
from app.services import boilerplate_variables as bv

MONEY_CHIPS = [
    "total_estimated_liability",
    "under_funded_balance",
    "under_funded_balance_per_unit",
    "reserve_monthly_contribution",
    "reserve_monthly_per_unit",
]


@pytest.fixture(scope="module")
def wrapper():
    """The real `_compute_all` output for a small but complete HOA."""
    return _compute_all(
        OLD_MILL_2026,
        BudgetDraft(
            line_items=[
                LineItem(
                    label="Member assessments",
                    amount=Decimal("600000"),
                    is_revenue=True,
                ),
                LineItem(
                    label="Replacement contributions",
                    amount=Decimal("120000"),
                    is_reserve=True,
                    is_revenue=True,
                ),
                LineItem(
                    label="Landscaping",
                    amount=Decimal("400000"),
                    section="Maintenance and operations",
                ),
                LineItem(
                    label="Roof replacement",
                    amount=Decimal("90000"),
                    is_reserve=True,
                ),
            ]
        ),
        ReserveStudySnapshot(
            study_date="September 2025",
            components=[
                ReserveStudyComponent(
                    line_item="Roofing",
                    useful_life=25,
                    remaining_life=10,
                    replacement_cost=Decimal("1000000"),
                    year_new=2010,
                ),
                ReserveStudyComponent(
                    line_item="Asphalt",
                    useful_life=20,
                    remaining_life=5,
                    replacement_cost=Decimal("400000"),
                    year_new=2010,
                ),
            ],
        ),
        HOAMetadata(
            hoa_id=1,
            name="Chip Money HOA",
            units=100,
            fiscal_year_start_month=1,
            fiscal_year_end_month=12,
        ),
        effective_hoa_settings={"reserve_cash_balance_eoy_prior": 250000},
    )


def _var_map(computed):
    return bv.build_var_map(
        hoa=HOAMetadata(
            hoa_id=1,
            name="Chip Money HOA",
            units=100,
            fiscal_year_start_month=1,
            fiscal_year_end_month=12,
        ),
        fiscal_year=2026,
        hoa_settings={"reserve_cash_balance_eoy_prior": 250000},
        computed=computed,
        static_data=OLD_MILL_2026.static_data,
        today="Monday January 1, 2026",
    )


# ── the shape contract ──────────────────────────────────────────────────────


def test_compute_all_returns_a_wrapper_around_the_facts(wrapper):
    """Pin the shape both call sites depend on. If `_compute_all` is ever
    flattened, this fails loudly instead of the chips going quietly to zero."""
    assert set(wrapper) == {
        "computed",
        "budget_draft",
        "hoa_metadata",
        "reserve_study_snapshot",
    }
    facts = wrapper["computed"]
    assert "reserve_liability_facts" in facts
    assert "reserve_funding_facts" in facts
    assert "presentation_facts" in facts


# ── the bug that shipped ────────────────────────────────────────────────────


def test_baselines_render_real_figures(wrapper):
    """The regression test proper: resolve against the facts and every money
    chip must carry a real number."""
    values = _var_map(wrapper["computed"])
    for chip in MONEY_CHIPS:
        assert values[chip] not in ("$0", "$0.00", "$", ""), (
            f"{chip} resolved to {values[chip]!r} — the reserve facts did not "
            "reach build_var_map"
        )
    assert values["percent_funded"] not in ("%", "0%", "")


def test_passing_the_wrapper_is_what_zeroed_the_chips(wrapper):
    """Documents the failure mode. Not a guard on production code — a guard on
    the explanation, so the next person sees why the unwrap matters."""
    values = _var_map(wrapper)
    assert values["total_estimated_liability"] == "$0"
    assert values["under_funded_balance"] == "$0"
    assert values["percent_funded"] == "%"


def test_the_two_shapes_disagree(wrapper):
    """If these ever agree, the wrapper has been flattened and the unwrap in
    `compiler._resolve_narrative` should be removed rather than left to rot."""
    assert _var_map(wrapper) != _var_map(wrapper["computed"])


# ── figures match their source facts ────────────────────────────────────────


def test_liability_chips_match_reserve_liability_facts(wrapper):
    facts = wrapper["computed"]["reserve_liability_facts"]
    values = _var_map(wrapper["computed"])
    assert values["total_estimated_liability"] == "${:,.0f}".format(
        Decimal(str(facts["total_estimated_liability"]))
    )
    assert values["under_funded_balance"] == "${:,.0f}".format(
        Decimal(str(facts["under_funded_balance_total"]))
    )
    assert values["percent_funded"] == f"{facts['percent_funded']}%"


def test_reserve_funding_chips_match_reserve_funding_facts(wrapper):
    facts = wrapper["computed"]["reserve_funding_facts"]
    values = _var_map(wrapper["computed"])
    assert values["reserve_monthly_contribution"] == "${:,.2f}".format(
        Decimal(str(facts["monthly_total"]))
    )
    assert values["reserve_monthly_per_unit"] == "${:,.2f}".format(
        Decimal(str(facts["monthly_per_unit"]))
    )


# ── end-to-end through the real compiler path ───────────────────────────────
#
# The unit tests above cover `build_var_map`. This one covers the line that
# actually broke: `_resolve_narrative` in `compile_package`, which is what
# feeds the rendered templates. It asserts on the narrative HTML the compiler
# hands to Jinja, so a future refactor that reintroduces the wrapper is caught
# where it matters rather than in a helper.


def test_compiler_resolves_narrative_money_to_real_figures(
    monkeypatch, tmp_path, qpdf_required
):
    from tests.test_disclosure_package_compiler import (
        _budget_draft,
        _hoa_metadata,
        _patch_render_capture,
        _reserve_snapshot,
        _seed_appendices,
    )
    from app.disclosure_package.compiler import compile_package

    appendices = tmp_path / "appendices"
    _seed_appendices(appendices)
    contexts = _patch_render_capture(monkeypatch)

    compile_package(
        spec=OLD_MILL_2026,
        budget_draft=_budget_draft(),
        reserve_snapshot=_reserve_snapshot(),
        hoa_metadata=_hoa_metadata(),
        output_dir=tmp_path / "out",
        appendices_root=appendices,
        hoa_settings_overrides={"reserve_cash_balance_eoy_prior": 250000},
    )

    narrative = contexts["note_4_5.html"]["narrative"]
    note_5 = narrative["note_4_5"]

    # Note 5 is the reserve disclosure that shipped reading $0 across the
    # board. Every row must now carry a real figure.
    assert "$0.00" not in note_5 and "$0<" not in note_5, note_5[:600]
    assert ">%<" not in note_5, "percent funded resolved empty"

    cover = narrative["cover_letter"]
    assert "$0.00" not in cover, cover[:600]
