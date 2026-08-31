"""Firm-level catalog and resolver for disclosure-package generated pages.

The default Tri-State order puts assessments immediately after the TOC.
Saved ``app_settings`` lists reorder / hide optional pages. Required pages
cannot be hidden. Unknown saved keys are dropped; catalog keys that a saved
list does not yet know about are appended (forward compatible).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

from .schemas import GeneratedPage, PackageSpec, StaticAppendix

ORDER_SETTING_KEY = "disclosure_section_order_json"
HIDDEN_SETTING_KEY = "disclosure_hidden_sections_json"
FIRM_SIGNATURE_SETTING_KEY = "firm_signature_filename"

_LEGACY_NOTE_TEMPLATES = frozenset(
    {
        "notes_1_to_3.html",
        "note_4_5.html",
        "note_6_funding_plan.html",
        "note_7.html",
        "note_8.html",
    }
)

NOTE_BUNDLE = ("note_1_3", "note_4_5", "note_6", "note_7", "note_8")

NOTE_TOC_ROWS: tuple[tuple[str, str], ...] = (
    ("Note 1 — The Association", "page_notes_1_to_3"),
    ("Note 2 — Forecast Period", "page_notes_1_to_3"),
    ("Note 3 — Basis of Presentation", "page_notes_1_to_3"),
    ("Note 4 — Revenues", "page_note_4_5"),
    ("Note 5 — Replacement Fund Balance", "page_note_4_5"),
    ("Note 6 — Funding Plan", "page_note_6"),
    ("Note 7 — Significant Assumptions", "page_note_7"),
    ("Note 8 — Outstanding Loans", "page_note_8"),
)


@dataclass(frozen=True)
class SectionCatalogEntry:
    template: str
    label: str
    required: bool
    page_count_hint: int = 1
    toc_title: Optional[str] = None
    bundle: tuple[str, ...] = ()


SECTION_CATALOG: tuple[SectionCatalogEntry, ...] = (
    SectionCatalogEntry("cover_letter.html", "Cover letter", True, 2),
    SectionCatalogEntry(
        "annual_budget_report_cover.html",
        "Annual budget report — cover",
        True,
        1,
    ),
    SectionCatalogEntry("annual_budget_report_toc.html", "Table of contents", True, 1),
    SectionCatalogEntry(
        "assessment_schedule/universal.html",
        "Assessment schedule",
        True,
        2,
        toc_title="Assessment Schedule",
    ),
    SectionCatalogEntry(
        "pro_forma_disclosure_summary.html",
        "Pro forma disclosure summary (§5570)",
        True,
        4,
        toc_title="Pro Forma Operating Budget & Reserve Funding Disclosure Summary",
    ),
    SectionCatalogEntry(
        "forecasted_statement_title.html",
        "Forecasted statement — title",
        False,
        1,
        toc_title="Forecasted Statement of Revenues & Expenses",
    ),
    SectionCatalogEntry(
        "compilation_report.html",
        "Accountants' compilation report",
        False,
        1,
        toc_title="Accountant's Compilation Report",
    ),
    SectionCatalogEntry(
        "forecasted_income_statement.html",
        "Forecasted statement of revenues and expenses",
        False,
        2,
        toc_title="Forecasted Statement of Revenues and Expenses",
    ),
    SectionCatalogEntry(
        "notes_packed.html",
        "Notes to financial statements",
        True,
        6,
        bundle=NOTE_BUNDLE,
    ),
    SectionCatalogEntry(
        "reserve_component_schedule_title.html",
        "Reserve component schedule — title",
        False,
        1,
        toc_title="Forecasted Schedule of Major Component Replacement Provision",
    ),
    SectionCatalogEntry(
        "reserve_component_schedule.html",
        "Reserve component schedule",
        False,
        5,
    ),
    SectionCatalogEntry(
        "insurance_disclosure_cover.html",
        "Insurance disclosure",
        False,
        1,
        toc_title="Insurance Disclosure",
    ),
    SectionCatalogEntry(
        "thirty_year_study_title.html",
        "30-year funding study — title",
        False,
        1,
        toc_title="30-Year Reserve Funding Plan",
    ),
    SectionCatalogEntry(
        "thirty_year_study_compilation.html",
        "Compilation report — 30-year study",
        False,
        1,
        toc_title="Accountants' Compilation Report — 30-Year Funding Study",
    ),
    SectionCatalogEntry(
        "thirty_year_cash_flow_panel.html",
        "30-year cash flow forecast",
        False,
        3,
        toc_title="Cash Flow Forecast",
    ),
    SectionCatalogEntry(
        "major_component_schedule.html",
        "Major component repair and replacement costs",
        False,
        14,
        toc_title="Major Component Repair and Replacement Costs",
    ),
)

CATALOG_BY_TEMPLATE: dict[str, SectionCatalogEntry] = {
    entry.template: entry for entry in SECTION_CATALOG
}
DEFAULT_SECTION_ORDER: list[str] = [entry.template for entry in SECTION_CATALOG]


def normalize_template_key(key: str) -> Optional[str]:
    if key in CATALOG_BY_TEMPLATE:
        return key
    if key in _LEGACY_NOTE_TEMPLATES:
        return "notes_packed.html"
    return None


def resolve_generated_templates(
    saved_order: Optional[Sequence[str]] = None,
    hidden: Optional[Iterable[str]] = None,
) -> list[str]:
    hidden_set: set[str] = set()
    for raw in hidden or ():
        key = normalize_template_key(str(raw))
        if key is None:
            continue
        if CATALOG_BY_TEMPLATE[key].required:
            continue
        hidden_set.add(key)

    ordered: list[str] = []
    seen: set[str] = set()
    for raw in saved_order or ():
        key = normalize_template_key(str(raw))
        if key is None or key in seen:
            continue
        ordered.append(key)
        seen.add(key)
    for key in DEFAULT_SECTION_ORDER:
        if key not in seen:
            ordered.append(key)
            seen.add(key)
    return [key for key in ordered if key not in hidden_set]


def default_generated_pages() -> list[GeneratedPage]:
    return [
        GeneratedPage(template=entry.template, page_count_hint=entry.page_count_hint)
        for entry in SECTION_CATALOG
    ]


def apply_to_spec(
    spec: PackageSpec,
    *,
    saved_order: Optional[Sequence[str]] = None,
    hidden: Optional[Iterable[str]] = None,
) -> PackageSpec:
    templates = resolve_generated_templates(saved_order, hidden)
    generated = [
        GeneratedPage(
            template=template,
            page_count_hint=CATALOG_BY_TEMPLATE[template].page_count_hint,
        )
        for template in templates
    ]
    static = [entry for entry in spec.entries if isinstance(entry, StaticAppendix)]
    return spec.model_copy(update={"entries": [*generated, *static]})


def _parse_json_list(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def load_saved_lists_from_session(session: Any) -> tuple[list[str], list[str]]:
    """Read saved order/hidden lists from a SQLAlchemy session."""

    def _read(key: str) -> list[str]:
        try:
            from app.ai_implementation.db.models import AppSetting

            row = session.get(AppSetting, key)
        except Exception:
            return []
        if row is None:
            return []
        return _parse_json_list(getattr(row, "value_text", None))

    return _read(ORDER_SETTING_KEY), _read(HIDDEN_SETTING_KEY)


def load_saved_lists(connection: Any) -> tuple[list[str], list[str]]:
    """Read saved order/hidden lists from a sqlite3-style connection."""

    def _read(key: str) -> list[str]:
        try:
            row = connection.execute(
                "SELECT value_text FROM app_settings WHERE key = ?",
                (key,),
            ).fetchone()
        except Exception:
            return []
        if not row:
            return []
        return _parse_json_list(row[0])

    return _read(ORDER_SETTING_KEY), _read(HIDDEN_SETTING_KEY)


def catalog_for_api(
    saved_order: Optional[Sequence[str]] = None,
    hidden: Optional[Iterable[str]] = None,
) -> list[dict[str, Any]]:
    """All catalog rows in display order, including hidden optionals."""
    hidden_set = set()
    for raw in hidden or ():
        key = normalize_template_key(str(raw))
        if key and not CATALOG_BY_TEMPLATE[key].required:
            hidden_set.add(key)
    display = resolve_generated_templates(saved_order, hidden=())
    return [
        {
            "template": template,
            "label": CATALOG_BY_TEMPLATE[template].label,
            "required": CATALOG_BY_TEMPLATE[template].required,
            "hidden": template in hidden_set,
        }
        for template in display
    ]


def narrative_doc_ids_in_package_order(
    saved_order: Optional[Sequence[str]] = None,
    hidden: Optional[Iterable[str]] = None,
) -> list[str]:
    """Editable narrative doc_ids following the firm catalog order."""
    from app.services.narrative_content import TEMPLATE_TO_DOCUMENT

    out: list[str] = []
    for template in resolve_generated_templates(saved_order, hidden=()):
        entry = CATALOG_BY_TEMPLATE[template]
        if entry.bundle:
            out.extend(entry.bundle)
            continue
        doc_id = TEMPLATE_TO_DOCUMENT.get(template)
        if doc_id:
            out.append(doc_id)
    return out
