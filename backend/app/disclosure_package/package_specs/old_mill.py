"""Old Mill 2026 disclosure-package literal (CONTEXT D-04).

Hardcoded per-HOA values that are NOT in the runtime data schemas. Phase 12+
will move these into a database-backed admin-input form and seed Old Mill's
row from this literal.

Sources:
  * `static_data` values: RESEARCH § 'Inputs to Hardcode for Old Mill' +
    PDF transcription pages 1-25 of the golden 2026 disclosure.
  * `entries` order + page_count_hint values: RESEARCH § 'Merge order'
    (lines 459-500) reconciled against the golden's 109-page total.

DEVIATIONS from RESEARCH list (Rule 1 — Bug):
  * `thirty_year_funding_plan` page_count_hint 4 → 5.
    RESEARCH lines 1043-1046 says plan 11-04 generates pages 27-31, which
    is 5 pages, not 4.
  * `thirty_year_plan_extra.pdf` page_count_hint 15 → 14.
    RESEARCH lines 1046 says 11-05 extracts pages 32-45 to this static
    appendix; 45 - 32 + 1 = 14 pages.
  * `adr_disclosure.pdf` page_count_hint 7 → 6.
    RESEARCH page-by-page table says pages 50-55 (six pages), not seven.
  * Added `appendix_pages_74_87.pdf` with page_count_hint=14.
    RESEARCH § 'Static appendix pages' jumps from page 73 to 88 — pages
    74-87 (14 pages) are uncategorized in the original list. Without this
    entry the entries sum to 96 instead of the golden's 109. Plan 11-05
    Task 2 will identify the actual sub-documents in this range during
    raster-diff Wave 0; for now it is a placeholder.

Net effect: sum(entry.page_count_hint) == 109, matching the golden PDF
page count and the plan-02 verify gate. SUMMARY tracks each adjustment
for downstream reconciliation.
"""
from __future__ import annotations

from decimal import Decimal

from ..schemas import (
    GeneratedPage,
    HOAStaticData,
    PackageSpec,
    StaticAppendix,
)


_OLD_MILL_STATIC = HOAStaticData(
    hoa_legal_name="Old Mill Homeowners Association",
    address_line_1="c/o Tri-State Enterprises, Inc.",
    address_line_2="2133 Leghorn Street",
    city="Mountain View",
    state="CA",
    zip="94043",
    management_company="Tri-State Enterprises, Inc.",
    management_company_address="2133 Leghorn Street, Mountain View, CA 94043",
    cpa_firm_name="Levy, Erlanger & Company LLP",
    cpa_firm_address="100 Montgomery Street, Suite 715, San Francisco, California 94104",
    reserve_study_expert_name="SMA Reserves of San Jose",
    assessment_model="flat",
    monthly_assessment_per_unit_current=Decimal("605.00"),
    monthly_assessment_per_unit_prior=Decimal("605.00"),
    reserve_cash_balance_eoy_prior=Decimal("2600000.00"),
    bank_cd_balance_for_interest=Decimal("3611111.11"),  # back-computed: $65,000 / 0.018
    income_tax_provision_estimate=Decimal("18200.00"),
    interest_rate_after_tax=Decimal("0.018"),
    replacement_cost_increase_rate=Decimal("0.03"),
    assessment_increase_schedule=[
        (2026, 2035, Decimal("0.03")),
        (2036, 2045, Decimal("0.03")),
        (2046, 2055, Decimal("0.00")),
    ],
    letter_date="Tuesday November 18, 2025",
    letter_signed_by="Board of Directors",
    fund_balance_boy_operations=Decimal("0"),
)


_OLD_MILL_ENTRIES = [
    # ─── Generated pages (cover letter through 30-year funding plan) ────────
    GeneratedPage(template="cover_letter.html", page_count_hint=2),                           # pages  1-2
    GeneratedPage(template="annual_budget_report_cover.html", page_count_hint=1),             # page   3
    GeneratedPage(template="annual_budget_report_toc.html", page_count_hint=1),               # page   4
    GeneratedPage(template="pro_forma_disclosure_summary.html", page_count_hint=4),           # pages  5-8
    GeneratedPage(template="forecasted_statement_title.html", page_count_hint=1),             # page   9
    GeneratedPage(template="forecasted_statement_toc.html", page_count_hint=1),               # page  10
    GeneratedPage(template="compilation_report.html", page_count_hint=1),                     # page  11
    GeneratedPage(template="forecasted_income_statement.html", page_count_hint=2),            # pages 12-13
    GeneratedPage(template="notes_1_to_3.html", page_count_hint=2),                           # pages 14-15
    GeneratedPage(template="note_4_5.html", page_count_hint=1),                               # page  16
    GeneratedPage(template="note_6_funding_plan.html", page_count_hint=1),                    # page  17
    GeneratedPage(template="note_7.html", page_count_hint=1),                                 # page  18
    GeneratedPage(template="note_8.html", page_count_hint=1),                                 # page  19
    GeneratedPage(template="reserve_component_schedule_title.html", page_count_hint=1),       # page  20
    GeneratedPage(template="reserve_component_schedule.html", page_count_hint=5),             # pages 21-25
    GeneratedPage(template="insurance_disclosure_cover.html", page_count_hint=1),             # page  26
    GeneratedPage(template="thirty_year_funding_plan.html", page_count_hint=5),               # pages 27-31 (was 4 in RESEARCH)
    # ─── Static appendices ──────────────────────────────────────────────────
    StaticAppendix(file="thirty_year_plan_extra.pdf", page_count_hint=14),                    # pages 32-45 (was 15 in RESEARCH)
    StaticAppendix(file="insurance_certificate.pdf", page_count_hint=3),                      # pages 46-48
    StaticAppendix(file="annual_policy_statement_cover.pdf", page_count_hint=1),              # page  49
    StaticAppendix(file="adr_disclosure.pdf", page_count_hint=6),                             # pages 50-55 (was 7 in RESEARCH)
    StaticAppendix(file="collection_policy.pdf", page_count_hint=3),                          # pages 56-58
    StaticAppendix(file="enforcement_fine_policy.pdf", page_count_hint=4),                    # pages 59-62
    StaticAppendix(file="hard_surface_flooring.pdf", page_count_hint=2),                      # pages 63-64
    StaticAppendix(file="window_patio_door.pdf", page_count_hint=2),                          # pages 65-66
    StaticAppendix(file="garage_door_guidelines.pdf", page_count_hint=2),                     # pages 67-68
    StaticAppendix(file="satellite_dish.pdf", page_count_hint=5),                             # pages 69-73 (incl. visual guide)
    StaticAppendix(file="appendix_pages_74_87.pdf", page_count_hint=14),                      # pages 74-87 (NEW; uncategorized)
    StaticAppendix(file="rules_restrictions.pdf", page_count_hint=4),                         # pages 88-91
    StaticAppendix(file="pool_rules.pdf", page_count_hint=1),                                 # page  92
    StaticAppendix(file="parking_rules.pdf", page_count_hint=1),                              # page  93
    StaticAppendix(file="water_intrusion.pdf", page_count_hint=3),                            # pages 94-96
    StaticAppendix(file="clubhouse_rentals.pdf", page_count_hint=2),                          # pages 97-98
    StaticAppendix(file="open_house_policy.pdf", page_count_hint=1),                          # page  99
    StaticAppendix(file="move_in_out.pdf", page_count_hint=1),                                # page 100
    StaticAppendix(file="quiet_hours.pdf", page_count_hint=1),                                # page 101
    StaticAppendix(file="storage_container.pdf", page_count_hint=1),                          # page 102
    StaticAppendix(file="emergency_shutoff.pdf", page_count_hint=2),                          # pages 103-104
    StaticAppendix(file="open_forum_resolution.pdf", page_count_hint=3),                      # pages 105-107
    StaticAppendix(file="electronic_consent_form.pdf", page_count_hint=1),                    # page 108
    StaticAppendix(file="signoff.pdf", page_count_hint=1),                                    # page 109
]


# Sentinel hoa_id; the runtime adapter resolves the actual property row id.
# Plan 11-01 seeded Old Mill in the portfolio; the resolver will look it up
# by hoa_code='10' / name='Old Mill Homeowners Association'.
OLD_MILL_2026 = PackageSpec(
    hoa_id=1,
    fiscal_year=2026,
    jurisdiction="california",
    static_data=_OLD_MILL_STATIC,
    entries=_OLD_MILL_ENTRIES,
)


__all__ = ["OLD_MILL_2026"]
