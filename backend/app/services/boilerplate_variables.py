"""Chip catalogs + resolver for operator-authored narrative documents.

Two chip kinds, split by *shape* rather than by trust (design.md D4):

* **Value chips** — ``<span data-var="NAME"></span>``. A scalar, always
  HTML-escaped on resolution. A value chip MAY resolve to ``""``; that is how
  inline optional clauses (``incorporation_clause``, Note 7's study-date
  clause) survive without new machinery.
* **Block chips** — ``<div data-block="NAME"></div>`` or
  ``<li data-block="NAME"></li>``. System-generated *trusted* HTML emitted as
  ``Markup``, for multi-paragraph conditional wording and loop-generated
  tables the operator cannot author as static content. The ``li`` carrier
  exists so a conditional list item can vanish entirely when its chip
  resolves to empty, instead of leaving a stray bullet.

Both are resolved from the disclosure compute context at compile time via
fixed whitelist dicts — NEVER through Jinja/``Environment.from_string`` — so
operator-authored content can never reach template evaluation. Interpolated
values inside block HTML are escaped individually; only the surrounding
markup, which this module authors, is trusted.

A chip name outside the catalogs is never rendered literally: it is caught by
``find_unknown_tokens`` at save time and by the preflight gate at compile
time, and ``resolve`` raises rather than emit it if it ever reaches this far.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Optional

from markupsafe import Markup, escape

# ── value chips ─────────────────────────────────────────────────────────────

# id -> human label, for the frontend "insert variable" picker.
TOKEN_CATALOG: dict[str, str] = {
    # identity / period
    "hoa_name": "HOA name",
    "hoa_name_upper": "HOA name (upper case)",
    "hoa_city": "HOA city",
    "hoa_state": "HOA state",
    "hoa_units": "Number of units",
    "hoa_units_word": "“unit” / “units” (matches unit count)",
    "hoa_entity_type": "Entity type",
    "incorporation_clause": "“, created in YYYY” (empty if unknown)",
    "fiscal_year": "Fiscal year",
    "prior_year": "Prior year",
    "final_forecast_year": "Final forecast year (fiscal year + 29)",
    "effective_date": "Budget effective date",
    "today": "Today’s date",
    # letter / signature
    "letter_date": "Letter date (falls back to today)",
    "signed_by": "Signed by",
    "signature_title": "Signature title (falls back to management company)",
    # management + CPA
    "management_company": "Management company name",
    "management_company_address": "Management company address",
    "cpa_firm_name": "CPA firm name",
    "cpa_firm_name_upper": "CPA firm name (upper case)",
    "cpa_firm_name_short": "CPA firm name (no “LLP”)",
    "cpa_firm_address": "CPA firm address",
    "accountant_report_date": "Accountant’s report date",
    "reserve_funding_plan_date": "Reserve funding plan date",
    # reserve study
    "reserve_study_expert_name": "Reserve study preparer",
    "reserve_study_date": "Reserve study date",
    "reserve_study_date_clause": "“ dated <date>” (empty if unknown)",
    "reserve_funding_plan_clause": "“, with a funding plan dated <date>” (empty if unknown)",
    # assessments
    "assessment_line": "Assessment amount / variance sentence (cover letter)",
    "assessment_basis_sentence": "Assessment-basis sentence (Note 4)",
    "assessment_change_phrase": "Assessment change phrase (“increases to”, …)",
    "monthly_assessment_per_unit": "Monthly assessment per unit",
    # reserve money
    "reserve_monthly_contribution": "Monthly reserve contribution (association)",
    "reserve_monthly_per_unit": "Monthly reserve contribution (per unit)",
    "reserve_funding_source_label": "Reserve funding source label",
    "cash_reserve_balance": "Estimated cash reserves (end of prior year)",
    "total_estimated_liability": "Estimated total replacement liability",
    "under_funded_balance": "Under-funded balance",
    "under_funded_balance_per_unit": "Under-funded balance per unit",
    "percent_funded": "Percent funded",
    # rates
    "replacement_cost_increase_rate": "Replacement-cost inflation rate",
    "interest_rate_after_tax": "After-tax interest rate",
    "income_tax_provision": "Income tax provision",
}

# The table-of-contents rows carry a live page number each. They are value
# chips (not one block chip) so the operator can retitle any TOC row while
# its page number stays computed — the page numbers come from a real first
# render pass, so they self-correct as edits change pagination.
TOC_PAGE_TOKENS: dict[str, str] = {
    "page_pro_forma_disclosure_summary": "pro_forma_disclosure_summary.html",
    "page_assessment_schedule": "assessment_schedule/universal.html",
    "page_forecasted_statement_title": "forecasted_statement_title.html",
    "page_compilation_report": "compilation_report.html",
    "page_forecasted_income_statement": "forecasted_income_statement.html",
    "page_notes_1_to_3": "notes_1_to_3.html",
    "page_note_4_5": "note_4_5.html",
    "page_note_6": "note_6_funding_plan.html",
    "page_note_7": "note_7.html",
    "page_note_8": "note_8.html",
    "page_reserve_component_schedule_title": "reserve_component_schedule_title.html",
    "page_insurance_disclosure_cover": "insurance_disclosure_cover.html",
    "page_thirty_year_study_title": "thirty_year_study_title.html",
    "page_thirty_year_study_compilation": "thirty_year_study_compilation.html",
    "page_thirty_year_cash_flow_panel": "thirty_year_cash_flow_panel.html",
    "page_major_component_schedule": "major_component_schedule.html",
}

TOKEN_CATALOG.update(
    {name: f"Page number — {template}" for name, template in TOC_PAGE_TOKENS.items()}
)


# ── chip provenance ─────────────────────────────────────────────────────────
#
# Where each chip's value comes from, so the editor can answer "what will
# print here, and where do I change it?" without the operator guessing. This
# is documentation of `build_var_map` below, and the test suite asserts the
# two stay in step (every catalog chip needs an entry).
#
# `field` is only set when a *rendered* input exists to jump to. Several
# values live on a settings row but have no form control (letter_signed_by_title,
# replacement_cost_increase_rate, hoa_state); those carry field=None and say so
# in `note`, because a dead link is worse than no link.


@dataclass(frozen=True)
class ChipSource:
    """Provenance of one chip, for the editor's chip popover.

    kind:
        ``settings``  — typed into the HOA's disclosure settings.
        ``property``  — typed into the HOA record (name, units, city).
        ``computed``  — falls out of the budget / reserve study. A `field` here
                        is an *override*, not the source.
        ``derived``   — mechanical from the package itself (fiscal year, page
                        numbers). Nothing to edit, ever.
    """

    kind: str
    field: Optional[str] = None
    note: str = ""

    @property
    def tab(self) -> Optional[str]:
        """Which settings tab hosts `field` (matches SettingsScreen's values)."""
        if self.field is None:
            return None
        return "database" if self.kind == "property" else "disclosure"


_SETTINGS_NOTE = "Typed into this HOA's disclosure settings."
_COMPUTED_NOTE = "Computed from the budget and reserve study."
_DERIVED_NOTE = "Derived from the package itself — nothing to edit."

CHIP_SOURCES: dict[str, ChipSource] = {
    # ── identity: the HOA record ────────────────────────────────────────────
    "hoa_name": ChipSource("property", "hoaName", "The HOA's name on its record."),
    "hoa_name_upper": ChipSource(
        "property", "hoaName", "The HOA's name, upper-cased for title pages."
    ),
    "hoa_city": ChipSource("property", "city", "The HOA's city on its record."),
    "hoa_units": ChipSource("property", "units", "The HOA's unit count."),
    "hoa_units_word": ChipSource(
        "property", "units", "Reads “unit” or “units” to match the unit count."
    ),
    "hoa_state": ChipSource(
        "property", None, "Stored on the HOA record; defaults to CA. No field yet."
    ),
    "hoa_entity_type": ChipSource(
        "property",
        None,
        "Stored on the HOA record. Defaults to “non-profit mutual benefit "
        "corporation”. No field yet.",
    ),
    "incorporation_clause": ChipSource(
        "property",
        None,
        "Prints “, created in YYYY” from the HOA's incorporation year, and "
        "disappears entirely when that is blank. No field yet.",
    ),
    # ── period: mechanical ──────────────────────────────────────────────────
    "fiscal_year": ChipSource("derived", None, "The package's budget year."),
    "prior_year": ChipSource("derived", None, "The budget year minus one."),
    "final_forecast_year": ChipSource(
        "derived", None, "The budget year plus 29 — the end of the 30-year study."
    ),
    "effective_date": ChipSource(
        "derived", None, "January 1 of the budget year."
    ),
    "today": ChipSource("derived", None, "The date the package is generated."),
    # ── letter / signature ──────────────────────────────────────────────────
    "letter_date": ChipSource(
        "settings",
        "letter_date",
        "The cover-letter date. Falls back to today's date when blank.",
    ),
    "signed_by": ChipSource("settings", "letter_signed_by", _SETTINGS_NOTE),
    "signature_title": ChipSource(
        "settings",
        None,
        "Falls back to the management company name — there is no separate "
        "field for the signature title.",
    ),
    # ── management + CPA ────────────────────────────────────────────────────
    "management_company": ChipSource("settings", "management_company", _SETTINGS_NOTE),
    "management_company_address": ChipSource(
        "settings", "management_company_address", _SETTINGS_NOTE
    ),
    "cpa_firm_name": ChipSource("settings", "cpa_firm_name", _SETTINGS_NOTE),
    "cpa_firm_name_upper": ChipSource(
        "settings", "cpa_firm_name", "The CPA firm name, upper-cased."
    ),
    "cpa_firm_name_short": ChipSource(
        "settings", "cpa_firm_name", "The CPA firm name with “ LLP” removed."
    ),
    "cpa_firm_address": ChipSource("settings", "cpa_firm_address", _SETTINGS_NOTE),
    "accountant_report_date": ChipSource(
        "settings",
        "accountant_report_date",
        "Falls back to today's date when blank.",
    ),
    "reserve_funding_plan_date": ChipSource(
        "settings",
        "reserve_funding_plan_date",
        "Falls back to the accountants' report date, then today.",
    ),
    "reserve_funding_plan_clause": ChipSource(
        "settings",
        "reserve_funding_plan_date",
        "Prints “, with a funding plan dated <date>”, and disappears entirely "
        "when the date is blank.",
    ),
    # ── reserve study ───────────────────────────────────────────────────────
    "reserve_study_expert_name": ChipSource(
        "settings", "reserve_study_expert_name", _SETTINGS_NOTE
    ),
    "reserve_study_date": ChipSource(
        "settings",
        "reserve_study_date",
        "Taken from the uploaded reserve study when it carries a date; the "
        "settings field is the fallback.",
    ),
    "reserve_study_date_clause": ChipSource(
        "settings",
        "reserve_study_date",
        "Prints “ dated <date>”, and disappears entirely when no date is known.",
    ),
    # ── assessments: computed ───────────────────────────────────────────────
    "assessment_line": ChipSource(
        "computed",
        "approved_monthly_assessment_per_unit",
        "A whole sentence, written two ways: one amount when every unit pays "
        "the same, otherwise a pointer to the assessment schedule.",
    ),
    "assessment_basis_sentence": ChipSource(
        "computed",
        None,
        "A whole sentence describing how assessments are apportioned, built "
        "from the assessment setup.",
    ),
    "assessment_change_phrase": ChipSource(
        "computed",
        None,
        "Reads “increases to”, “decreases to” or “remains at”, from this year's "
        "assessment against last year's.",
    ),
    "monthly_assessment_per_unit": ChipSource(
        "computed",
        "approved_monthly_assessment_per_unit",
        "Derived from the budget. The settings field overrides it outright.",
    ),
    # ── reserve money: computed ─────────────────────────────────────────────
    "reserve_monthly_contribution": ChipSource(
        "computed",
        "reserve_funding_source",
        "The association's monthly reserve funding. Which figure drives it is "
        "chosen by the reserve funding source setting.",
    ),
    "reserve_monthly_per_unit": ChipSource(
        "computed",
        "reserve_funding_source",
        "The monthly reserve funding divided across units.",
    ),
    "reserve_funding_source_label": ChipSource(
        "computed",
        "reserve_funding_source",
        "Names which figure drove the reserve funding number.",
    ),
    "cash_reserve_balance": ChipSource(
        "computed",
        "reserve_cash_balance_eoy_prior",
        "From the reserve study; the settings field overrides it.",
    ),
    "total_estimated_liability": ChipSource("computed", None, _COMPUTED_NOTE),
    "under_funded_balance": ChipSource("computed", None, _COMPUTED_NOTE),
    "under_funded_balance_per_unit": ChipSource("computed", None, _COMPUTED_NOTE),
    "percent_funded": ChipSource("computed", None, _COMPUTED_NOTE),
    # ── rates ───────────────────────────────────────────────────────────────
    "replacement_cost_increase_rate": ChipSource(
        "settings",
        None,
        "An industry-standard default seeded once per HOA. No field — it does "
        "not change year to year.",
    ),
    "interest_rate_after_tax": ChipSource(
        "settings", "interest_rate_after_tax", _SETTINGS_NOTE
    ),
    "income_tax_provision": ChipSource(
        "computed",
        "income_tax_provision_override",
        "Derived from interest revenue. The settings field overrides it.",
    ),
}

CHIP_SOURCES.update(
    {
        name: ChipSource(
            "derived",
            None,
            "A live page number, filled in from a real render pass — it "
            "corrects itself as your edits change the pagination.",
        )
        for name in TOC_PAGE_TOKENS
    }
)

# Block chips are system-generated wording; none of them has a single field.
BLOCK_SOURCES: dict[str, ChipSource] = {
    "special_assessment_disclosure": ChipSource(
        "computed",
        "special_assessments_json",
        "Civil Code §5300 wording, chosen from whether special assessments are "
        "scheduled for this year.",
    ),
    "outstanding_loan_note": ChipSource(
        "computed",
        "outstanding_loan_json",
        "Written from the outstanding loan on file, and omitted entirely when "
        "there is none.",
    ),
    "contribution_increase_schedule": ChipSource(
        "computed",
        "assessment_increase_schedule_json",
        "A table built from the scheduled contribution increases.",
    ),
    "reserve_only_note": ChipSource(
        "computed",
        "financial_packet_archetype",
        "Appears only for a reserve-only accountant statement.",
    ),
    "reserve_only_assumption": ChipSource(
        "computed",
        "financial_packet_archetype",
        "Appears only for a reserve-only accountant statement.",
    ),
    "significant_assumptions_variance": ChipSource(
        "computed",
        None,
        "Written from how assessments vary across this HOA's units.",
    ),
    "appendix_toc_rows": ChipSource(
        "computed",
        None,
        "One table-of-contents row per uploaded appendix. Manage these on the "
        "Appendices tab.",
    ),
}


def chip_source(name: str) -> ChipSource:
    """Provenance for a value or block chip; unknown names read as computed."""
    return (
        CHIP_SOURCES.get(name)
        or BLOCK_SOURCES.get(name)
        or ChipSource("computed", None, _COMPUTED_NOTE)
    )


# Chips whose wording is chosen by the assessment matrix, not by `computed`.
# `_assessments_vary` reads `matrix.recipient_grain`, so without a built matrix
# these silently take the every-unit-pays-the-same branch — which is the wrong
# sentence for exactly the HOAs where it matters most.
MATRIX_DEPENDENT_CHIPS: frozenset[str] = frozenset(
    {"assessment_line", "assessment_basis_sentence"}
)


def previewable_values(
    var_map: Mapping[str, str],
    *,
    computed_available: bool,
    matrix_available: bool = False,
) -> dict[str, str]:
    """Filter a var map down to the values it is honest to show as a preview.

    ``build_var_map`` formats missing money as ``"0.00"`` and a missing percent
    as ``"0.0%"`` — correct for rendering (a template must print something) and
    actively misleading as a preview, because "we could not compute this" and
    "this really is zero" come out identical.

    So when the compute context could not be built (no active budget, no reserve
    study), every ``computed`` chip is dropped rather than shown as zero.
    Settings-, property- and derived-sourced chips need no compute and are
    always returned.

    ``MATRIX_DEPENDENT_CHIPS`` are dropped unless the caller built a real
    assessment matrix: their fallback is a plausible-looking *wrong* sentence
    rather than an obvious blank, so guessing is worse than staying quiet.
    """
    return {
        name: value
        for name, value in var_map.items()
        if (computed_available or chip_source(name).kind != "computed")
        and (matrix_available or name not in MATRIX_DEPENDENT_CHIPS)
    }


# ── block chips ─────────────────────────────────────────────────────────────

BLOCK_CATALOG: dict[str, str] = {
    "special_assessment_disclosure": "§5300 special-assessment disclosure",
    "outstanding_loan_note": "Outstanding-loan paragraph (Note 8)",
    "contribution_increase_schedule": "Contribution increase schedule table (Note 6)",
    "reserve_only_note": "Reserve-only packet note — paragraph (Note 4)",
    "reserve_only_assumption": "Reserve-only packet note — list item (Note 7)",
    "significant_assumptions_variance": "Assessment-level assumption (Note 7)",
    "appendix_toc_rows": "Table-of-contents rows for uploaded appendices",
}

# Matches an (empty) value-chip span, tolerant of attribute order and any
# extra attributes the editor's NodeView adds (e.g. contenteditable="false").
_VAR_SPAN_RE = re.compile(
    r'<span\b[^>]*\bdata-var="([^"]*)"[^>]*>.*?</span>',
    re.IGNORECASE | re.DOTALL,
)

# Block chips are carried by a div or an li; the whole element is replaced,
# so a chip resolving to "" removes its carrier along with it. Exported as
# BLOCK_CARRIER_RE because narrative_content's required-block check must ask
# the same question the resolver answers — a chip the resolver substitutes
# can never read as "absent" to the preflight gate.
#
# The carrier is required to be EMPTY. A non-greedy `.*?` across a nested
# element would stop at the inner closing tag and leave an orphaned `</div>`
# in the output, so rather than trusting that chips happen to be empty (they
# are — the editor node is an atom), the invariant is enforced: this pattern
# only matches empty carriers, and `find_non_empty_blocks` rejects the rest at
# save time instead of letting them through unmatched.
BLOCK_CARRIER_RE = re.compile(
    r'<(div|li)\b[^>]*\bdata-block="([^"]*)"[^>]*>\s*</\1>',
    re.IGNORECASE,
)
_BLOCK_RE = BLOCK_CARRIER_RE

# Any block carrier, empty or not — used only to detect the invalid case.
_ANY_BLOCK_CARRIER_RE = re.compile(
    r'<(div|li)\b[^>]*\bdata-block="([^"]*)"[^>]*>',
    re.IGNORECASE,
)


class UnresolvedBoilerplateToken(ValueError):
    """Raised by `resolve` when a chip name isn't in the catalogs."""


class UnknownBoilerplateToken(ValueError):
    """Raised at save time when content references a chip outside the catalogs.

    Defined here rather than in ``hoa_boilerplate`` so ``narrative_content``
    can raise it without importing that module (which imports this one).
    ``hoa_boilerplate`` re-exports it, so existing import sites are unaffected.
    """


# ── formatting helpers ──────────────────────────────────────────────────────


def _money(value: Any) -> str:
    if value is None:
        return "0.00"
    try:
        return "{:,.2f}".format(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return "0.00"


def _money0(value: Any) -> str:
    """Whole dollars — matches the templates' ``'{:,.0f}'.format(...)``."""
    if value is None:
        return "0"
    try:
        return "{:,.0f}".format(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return "0"


def _percent(value: Any, places: int = 1) -> str:
    if value is None:
        return "0.0%" if places else "0%"
    try:
        return "{:.{p}%}".format(float(value), p=places)
    except (TypeError, ValueError):
        return "0.0%" if places else "0%"


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    """Read from an object or a mapping — compute facts arrive as both."""
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _assessments_vary(computed: Mapping[str, Any], matrix: Any) -> bool:
    """Single source of truth for "do assessments differ by unit?".

    Sourced from the same facts the templates branched on before this change
    (``matrix.recipient_grain`` / ``presentation_facts.assessments_vary``) so
    the chips never invent a second answer.
    """
    presentation = computed.get("presentation_facts") or {}
    grain = getattr(matrix, "recipient_grain", None) if matrix is not None else None
    return grain in ("group", "unit") or bool(presentation.get("assessments_vary"))


# ── value-chip map ──────────────────────────────────────────────────────────


def build_var_map(
    *,
    hoa: Any,
    fiscal_year: int,
    hoa_settings: Optional[Mapping[str, Any]],
    computed: Mapping[str, Any],
    matrix: Any = None,
    static_data: Any = None,
    today: str = "",
    reserve_study_snapshot: Any = None,
    toc_page_numbers: Optional[Mapping[str, Any]] = None,
) -> dict[str, str]:
    """Build the {chip_name: resolved_value} map for one compile pass.

    Every value is a plain string; ``resolve`` escapes it. Optional-clause
    chips resolve to ``""`` when their underlying fact is absent, which is
    how the templates' inline ``{% if %}`` fragments became editable prose.
    """
    hoa_settings = hoa_settings or {}
    toc_page_numbers = toc_page_numbers or {}
    presentation = computed.get("presentation_facts") or {}
    reserve_funding = computed.get("reserve_funding_facts") or {}
    reserve_liability = computed.get("reserve_liability_facts") or {}

    units = _attr(hoa, "units")
    incorporation_year = _attr(hoa, "incorporation_year")
    study_date = _attr(reserve_study_snapshot, "study_date") or hoa_settings.get(
        "reserve_study_date"
    )
    funding_plan_date = hoa_settings.get("reserve_funding_plan_date")
    cpa_name = _text(hoa_settings.get("cpa_firm_name"))

    # Fall back through the same chain the templates used: live property row
    # first, spec static_data second.
    city = _text(_attr(hoa, "city") or _attr(static_data, "city"))
    state = _text(_attr(hoa, "state") or _attr(static_data, "state"))

    change_phrase = _text(
        presentation.get("assessment_change_phrase")
        or computed.get("assessment_change_phrase")
    )
    monthly_per_unit = computed.get("monthly_assessment_per_unit_current")

    if _assessments_vary(computed, matrix):
        assessment_line = (
            f"Monthly assessments for {fiscal_year} vary by ownership interest "
            "and are shown in the assessment schedule included in this package."
        )
    else:
        assessment_line = (
            f"The monthly assessment per unit for {fiscal_year} {change_phrase} "
            f"${_money(monthly_per_unit)}."
        )

    reserve_per_unit = (
        reserve_funding.get("monthly_per_unit")
        if reserve_funding
        else computed.get("monthly_replacement_contribution_per_unit_2026")
    )
    reserve_total = (
        reserve_funding.get("monthly_total")
        if reserve_funding
        else computed.get("monthly_replacement_contribution_total")
    )
    source_label = _text(
        reserve_funding.get("source_label")
        if reserve_funding
        else computed.get("reserve_funding_source_label")
    )

    def _liability(key: str, computed_key: str, settings_key: str = "") -> Any:
        if reserve_liability:
            return reserve_liability.get(key)
        if computed_key in computed:
            return computed.get(computed_key)
        return hoa_settings.get(settings_key) if settings_key else None

    hoa_name = _text(_attr(hoa, "name"))

    var_map = {
        # identity / period
        "hoa_name": hoa_name,
        "hoa_name_upper": hoa_name.upper(),
        "hoa_city": city,
        "hoa_state": state,
        "hoa_units": _text(units),
        "hoa_units_word": "unit" if units == 1 else "units",
        "hoa_entity_type": _text(
            _attr(hoa, "entity_type") or "non-profit mutual benefit corporation"
        ),
        "incorporation_clause": (
            f", created in {incorporation_year}" if incorporation_year else ""
        ),
        "fiscal_year": str(fiscal_year),
        "prior_year": str(fiscal_year - 1),
        "final_forecast_year": str(fiscal_year + 29),
        "effective_date": f"January 1, {fiscal_year}",
        "today": _text(today),
        # letter / signature
        "letter_date": _text(hoa_settings.get("letter_date") or today),
        "signed_by": _text(hoa_settings.get("letter_signed_by")),
        "signature_title": _text(
            hoa_settings.get("letter_signed_by_title")
            or hoa_settings.get("management_company")
        ),
        # management + CPA
        "management_company": _text(hoa_settings.get("management_company")),
        "management_company_address": _text(
            hoa_settings.get("management_company_address")
        ),
        "cpa_firm_name": cpa_name,
        "cpa_firm_name_upper": cpa_name.upper(),
        "cpa_firm_name_short": cpa_name.replace(" LLP", ""),
        "cpa_firm_address": _text(hoa_settings.get("cpa_firm_address")),
        "accountant_report_date": _text(
            hoa_settings.get("accountant_report_date") or today
        ),
        "reserve_funding_plan_date": _text(
            funding_plan_date or hoa_settings.get("accountant_report_date") or today
        ),
        # reserve study
        "reserve_study_expert_name": _text(
            hoa_settings.get("reserve_study_expert_name")
        ),
        "reserve_study_date": _text(study_date),
        "reserve_study_date_clause": f" dated {study_date}" if study_date else "",
        "reserve_funding_plan_clause": (
            f", with a funding plan dated {funding_plan_date}"
            if funding_plan_date
            else ""
        ),
        # assessments
        "assessment_line": assessment_line,
        "assessment_basis_sentence": _assessment_basis_sentence(
            computed, _assessments_vary(computed, matrix), change_phrase
        ),
        "assessment_change_phrase": change_phrase,
        "monthly_assessment_per_unit": f"${_money(monthly_per_unit)}",
        # reserve money
        "reserve_monthly_contribution": f"${_money(reserve_total)}",
        "reserve_monthly_per_unit": f"${_money(reserve_per_unit)}",
        "reserve_funding_source_label": source_label,
        "cash_reserve_balance": "${}".format(
            _money0(
                _liability(
                    "cash_reserve_balance_eoy_prior",
                    "cash_reserve_balance_eoy_prior",
                    "reserve_cash_balance_eoy_prior",
                )
            )
        ),
        "total_estimated_liability": "${}".format(
            _money0(
                _liability("total_estimated_liability", "total_estimated_liability")
            )
        ),
        "under_funded_balance": "${}".format(
            _money0(
                _liability("under_funded_balance_total", "under_funded_balance_total")
            )
        ),
        "under_funded_balance_per_unit": "${}".format(
            _money0(
                _liability(
                    "under_funded_balance_per_unit", "under_funded_balance_per_unit"
                )
            )
        ),
        "percent_funded": "{}%".format(
            _text(_liability("percent_funded", "percent_funded"))
        ),
        # rates
        "replacement_cost_increase_rate": _percent(
            hoa_settings.get("replacement_cost_increase_rate"), places=0
        ),
        "interest_rate_after_tax": _percent(
            hoa_settings.get("interest_rate_after_tax"), places=1
        ),
        "income_tax_provision": "${}".format(
            _money0(computed.get("income_tax_provision"))
        ),
    }

    for token, template_name in TOC_PAGE_TOKENS.items():
        page = toc_page_numbers.get(template_name)
        var_map[token] = str(page) if page is not None else "—"

    return var_map


# ── block-chip map ──────────────────────────────────────────────────────────


def _special_assessment_disclosure(
    computed: Mapping[str, Any], fiscal_year: int, assessments_vary: bool
) -> Markup:
    """§5300 wording — the three-way branch, driven by inferred SA status.

    Status comes from the Phase 4.4 ``infer_special_assessment_status`` output
    on ``computed.special_assessments[*].status``.
    """
    sa_list = computed.get("special_assessments") or []
    scheduled = [sa for sa in sa_list if _attr(sa, "status") == "approved_scheduled"]
    disclosure_only = [
        sa for sa in sa_list if _attr(sa, "status") == "possible_disclosure_only"
    ]

    if scheduled:
        items: list[str] = []
        for sa in scheduled:
            label = escape(_attr(sa, "label") or "Special assessment")
            total = _attr(sa, "total_amount")
            # A variable (by sqft / ownership) split has no single per-unit
            # figure, so show the total and point at the allocation schedule.
            if _attr(sa, "is_variable_allocation") and total is not None:
                amount = (
                    f"${_money(total)} total, allocated per the assessment schedule"
                )
            elif total is not None:
                amount = f"${_money(total)} total, allocated equally across units"
            else:
                amount = f"${_money(_attr(sa, 'amount_per_unit'))} per unit"
            parts = [f"<strong>{label}</strong>: {escape(amount)}"]
            due = _attr(sa, "due_date")
            if due:
                parts.append(f"due {escape(due)}")
            if _attr(sa, "included_in_regular_monthly"):
                parts.append(
                    "— included in the regular monthly assessment schedule."
                    if assessments_vary
                    else "— included in the regular monthly assessment shown above."
                )
            else:
                parts.append("— billed separately from the regular monthly assessment.")
            items.append(f"<li>{' '.join(parts)}</li>")
        return Markup(
            "<li>5300: A special assessment has been approved and scheduled for "
            f"the {fiscal_year} calendar year:<ul>{''.join(items)}</ul></li>"
        )

    if disclosure_only:
        items = []
        for sa in disclosure_only:
            language = _attr(sa, "display_language")
            if language:
                body = escape(language)
            else:
                label = escape(_attr(sa, "label") or "Possible special assessment")
                body = Markup(
                    f"{label} — formal Board approval and notice required "
                    "before any charge."
                )
            items.append(f"<li>{body}</li>")
        return Markup(
            "<li>5300: The Board discloses the following possible special "
            f"assessment(s) for the {fiscal_year} calendar year:"
            f"<ul>{''.join(items)}</ul></li>"
        )

    return Markup(
        "<li>5300: The Board does not anticipate that there is a possibility of "
        f"a special assessment that will be required during the {fiscal_year} "
        "calendar year to repair, replace, or restore any major component or to "
        "provide adequate reserves therefore. (See Above)</li>"
    )


def _outstanding_loan_note(
    computed: Mapping[str, Any], fiscal_year: int
) -> Markup:
    loan = computed.get("outstanding_loan")
    prior_year = fiscal_year - 1
    if not loan:
        return Markup(
            "<p>There is no outstanding current or projected loan balance as of "
            f"December 31, {prior_year}. The Association has not entered into any "
            "borrowing arrangements that would result in a loan obligation during "
            f"the {fiscal_year} forecast period.</p>"
        )

    sentence = [
        "The Association has an outstanding loan balance of "
        f'<span class="bold">${_money(_attr(loan, "balance"))}</span> as of '
        f"December 31, {prior_year}"
    ]
    lender = _attr(loan, "lender")
    if lender:
        sentence.append(f" with {escape(lender)}")
    sentence.append(".")
    original = _attr(loan, "original_amount")
    if original:
        sentence.append(f" The original principal was ${_money(original)}.")
    rate = _attr(loan, "interest_rate")
    if rate is not None:
        sentence.append(f" The interest rate is {_percent(rate, places=2)}.")
    payoff = _attr(loan, "payoff_date")
    if payoff:
        sentence.append(f" Scheduled payoff: {escape(payoff)}.")
    purpose = _attr(loan, "purpose")
    if purpose:
        sentence.append(f" Purpose: {escape(purpose)}.")

    return Markup(
        f"<p>{''.join(str(part) for part in sentence)}</p>"
        f"<p>Loan-service obligations are reflected in the {fiscal_year} operating "
        "budget in accordance with the loan agreement and California Civil Code "
        "&sect; 5505 (notice of obligations).</p>"
    )


def _assessment_basis_sentence(
    computed: Mapping[str, Any], assessments_vary: bool, change_phrase: str
) -> str:
    """Note 4's assessment sentence.

    A *value* chip, not a block chip: it is inline prose that sits mid-sentence
    inside a ``<p>``, where a block carrier would be invalid markup. It carries
    no markup of its own, so an escaped scalar is the right shape.
    """
    if assessments_vary:
        return (
            "Current monthly assessments vary by ownership interest as shown in "
            "the assessment schedule included in this package."
        )
    amount = _money(computed.get("monthly_assessment_per_unit_current"))
    return f"The current monthly assessment per unit is {change_phrase} ${amount}."


def _contribution_increase_schedule(static_data: Any) -> Markup:
    schedule = _attr(static_data, "assessment_increase_schedule") or []
    rows = "".join(
        f"<tr><td>{escape(start)} &ndash; {escape(end)}</td>"
        f'<td class="right">{_percent(rate, places=1)}</td></tr>'
        for start, end, rate in schedule
    )
    return Markup(
        '<table class="form-5570-table"><thead><tr>'
        '<th class="left">Period</th>'
        '<th class="right">Annual Contribution Increase</th>'
        f"</tr></thead><tbody>{rows}</tbody></table>"
    )


def _is_reserve_only(computed: Mapping[str, Any]) -> bool:
    packet = computed.get("packet_archetype_facts") or {}
    return packet.get("archetype") == "reserve-only"


def _reserve_only_note(computed: Mapping[str, Any]) -> Markup:
    """Paragraph form (Note 4). Empty outside a reserve-only packet."""
    if not _is_reserve_only(computed):
        return Markup("")
    return Markup(
        "<p>The accompanying forecast pages present replacement-fund activity "
        "only; operations-fund activities are not included in the "
        "accountant-style financial statement pages.</p>"
    )


def _reserve_only_assumption(computed: Mapping[str, Any]) -> Markup:
    """List-item form (Note 7's assumptions list). Empty outside reserve-only.

    A separate chip rather than a variant of ``reserve_only_note`` because
    the carrier element is replaced wholesale — a paragraph substituted into
    a ``<ul>`` would be invalid markup.
    """
    if not _is_reserve_only(computed):
        return Markup("")
    return Markup(
        '<li><span class="bold">Reserve-only packet:</span> the '
        "accountant-style forecast statement pages present replacement-fund "
        "activity only and exclude operations-fund activity.</li>"
    )


def _significant_assumptions_variance(
    computed: Mapping[str, Any], assessments_vary: bool
) -> Markup:
    if assessments_vary:
        body = (
            "regular monthly assessments vary by ownership interest as shown in "
            "the assessment schedule."
        )
    else:
        amount = _money(computed.get("monthly_assessment_per_unit_current"))
        body = (
            f"regular monthly assessments are forecast at the current rate of "
            f"${amount} per unit per month."
        )
    return Markup(
        f'<li><span class="bold">Assessment level:</span> {body}</li>'
    )


def _appendix_toc_rows(entries: Optional[Iterable[Mapping[str, Any]]]) -> Markup:
    rows = "".join(
        f'<li><span class="toc-entry">{escape(entry.get("title"))}</span>'
        f'<span class="toc-page">{escape(entry.get("page"))}</span></li>'
        for entry in (entries or [])
    )
    return Markup(rows)


def build_block_map(
    *,
    fiscal_year: int,
    computed: Mapping[str, Any],
    matrix: Any = None,
    static_data: Any = None,
    appendix_toc_entries: Optional[Iterable[Mapping[str, Any]]] = None,
) -> dict[str, Markup]:
    """Build the {block_name: trusted HTML} map for one compile pass.

    Every interpolated value is escaped individually; only the structural
    markup authored in this module is trusted. ``reserve_only_note`` resolves
    to ``""`` outside a reserve-only packet, which removes its carrier element
    entirely rather than leaving an empty paragraph or bullet.
    """
    assessments_vary = _assessments_vary(computed, matrix)

    return {
        "special_assessment_disclosure": _special_assessment_disclosure(
            computed, fiscal_year, assessments_vary
        ),
        "outstanding_loan_note": _outstanding_loan_note(computed, fiscal_year),
        "contribution_increase_schedule": _contribution_increase_schedule(static_data),
        "reserve_only_note": _reserve_only_note(computed),
        "reserve_only_assumption": _reserve_only_assumption(computed),
        "significant_assumptions_variance": _significant_assumptions_variance(
            computed, assessments_vary
        ),
        "appendix_toc_rows": _appendix_toc_rows(appendix_toc_entries),
    }


# ── validation + resolution ─────────────────────────────────────────────────


def find_unknown_tokens(html: Optional[str]) -> list[str]:
    """Chip names referenced by content that aren't in either catalog."""
    if not html:
        return []
    unknown = {
        name for name in _VAR_SPAN_RE.findall(html) if name not in TOKEN_CATALOG
    }
    # Every carrier, not just the empty ones: an unknown name inside a
    # malformed carrier must still be reported rather than slipping past both
    # this check and the resolver.
    unknown |= {
        name
        for _tag, name in _ANY_BLOCK_CARRIER_RE.findall(html)
        if name not in BLOCK_CATALOG
    }
    return sorted(unknown)


def find_non_empty_blocks(html: Optional[str]) -> list[str]:
    """Block chips that carry content — always an error.

    A block chip is a placeholder the system fills in; its carrier must be
    empty. Content inside one means the element would not match the resolver's
    pattern and would render as raw `data-block` markup in the finished PDF.
    """
    if not html:
        return []
    empty = {match.start() for match in _BLOCK_RE.finditer(html)}
    return sorted(
        {
            match.group(2)
            for match in _ANY_BLOCK_CARRIER_RE.finditer(html)
            if match.start() not in empty
        }
    )


def resolve(
    html: Optional[str],
    var_map: Mapping[str, str],
    block_map: Optional[Mapping[str, Any]] = None,
) -> Optional[str]:
    """Replace every chip with its resolved value.

    Value chips are HTML-escaped. Block chips are inserted as-is because this
    module authored them (their interpolated values were escaped at build
    time). Operator HTML is never evaluated as a template in either path.
    """
    if not html:
        return html
    block_map = block_map or {}

    def _sub_var(match: "re.Match[str]") -> str:
        name = match.group(1)
        if name not in var_map:
            raise UnresolvedBoilerplateToken(f"Unknown boilerplate token: {name!r}")
        return str(escape(var_map[name]))

    def _sub_block(match: "re.Match[str]") -> str:
        name = match.group(2)
        if name not in block_map:
            raise UnresolvedBoilerplateToken(f"Unknown boilerplate block: {name!r}")
        return str(block_map[name])

    resolved = _BLOCK_RE.sub(_sub_block, html)
    return _VAR_SPAN_RE.sub(_sub_var, resolved)
