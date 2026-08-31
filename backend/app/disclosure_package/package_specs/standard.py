"""Standard disclosure-package spec (DRE-driven assessment engine).

Defines the universal template chain used for every HOA. Per-HOA
variance comes from:

  * ``properties`` table (name, units, fiscal year, address)
  * ``hoa_settings`` table (management company, CPA firm, dates, prior assessment)
  * ``assessment_setups`` + ``allocation_pools`` (the assessment math)
  * ``reserve_study_extractions`` (the reserve study rows)
  * ``appendix_documents`` + ``annual_package_appendices`` (the appendix manifest)

The ``entries`` list below is the template + static-appendix order
every HOA's package goes through. Operators can override individual
appendices via the per-HOA Settings → Appendices tab; the static
appendix entries here are fallback file names the legacy compile path
looks for in ``appendices/<hoa_code>/``.

Previously this file was ``old_mill.py`` and the constant was
``OLD_MILL_2026`` — a literal that hardcoded one specific HOA's name,
management company, CPA, and reserve study expert as the system-wide
defaults. That was wrong: those values belong in ``hoa_settings``,
where the operator can edit them per HOA. This module now ships
empty/None defaults so the only HOA-specific data in code is the
universal template chain.
"""

from __future__ import annotations

from decimal import Decimal

from ..schemas import (
    HOAStaticData,
    PackageSpec,
    StaticAppendix,
)
from ..section_order import default_generated_pages


_STANDARD_STATIC_DEFAULTS = HOAStaticData(
    hoa_legal_name="",
    address_line_1="",
    address_line_2="",
    city="",
    state="CA",
    zip="",
    management_company="",
    management_company_address="",
    cpa_firm_name="",
    cpa_firm_address="",
    reserve_study_expert_name="",
    assessment_model="flat",
    monthly_assessment_per_unit_current=Decimal("0"),
    monthly_assessment_per_unit_prior=Decimal("0"),
    reserve_cash_balance_eoy_prior=Decimal("0"),
    bank_cd_balance_for_interest=Decimal("0"),
    income_tax_provision_estimate=Decimal("0"),
    interest_rate_after_tax=Decimal("0.018"),
    replacement_cost_increase_rate=Decimal("0.03"),
    assessment_increase_schedule=[],
    letter_date="",
    letter_signed_by="Board of Directors",
    fund_balance_boy_operations=Decimal("0"),
)


_STANDARD_STATIC_APPENDICES = [
    # Fallback for HOAs that don't use the appendix_documents table yet —
    # operators should migrate to the per-HOA appendix manifest in Settings.
    StaticAppendix(file="insurance_certificate.pdf", page_count_hint=3),
    StaticAppendix(file="annual_policy_statement_cover.pdf", page_count_hint=1),
    StaticAppendix(file="adr_disclosure.pdf", page_count_hint=6),
    StaticAppendix(file="collection_policy.pdf", page_count_hint=3),
    StaticAppendix(file="enforcement_fine_policy.pdf", page_count_hint=4),
    StaticAppendix(file="rules_restrictions.pdf", page_count_hint=4),
    StaticAppendix(file="open_forum_resolution.pdf", page_count_hint=3),
    StaticAppendix(file="electronic_consent_form.pdf", page_count_hint=1),
    StaticAppendix(file="signoff.pdf", page_count_hint=1),
]

_STANDARD_ENTRIES = [
    *default_generated_pages(),
    *_STANDARD_STATIC_APPENDICES,
]


# Sentinel hoa_id/fiscal_year — the resolver overrides both with the
# caller's actual values via ``model_copy(update={...})``.
STANDARD_PACKAGE_SPEC = PackageSpec(
    hoa_id=0,
    fiscal_year=0,
    jurisdiction="california",
    static_data=_STANDARD_STATIC_DEFAULTS,
    entries=_STANDARD_ENTRIES,
)


# Backward-compat alias for code still referencing the old name.
# Tests + a small number of resolver callers used ``OLD_MILL_2026``
# while the rename was in flight; this alias keeps them compiling
# until they migrate. New code should use ``STANDARD_PACKAGE_SPEC``.
OLD_MILL_2026 = STANDARD_PACKAGE_SPEC


__all__ = ["OLD_MILL_2026", "STANDARD_PACKAGE_SPEC"]
