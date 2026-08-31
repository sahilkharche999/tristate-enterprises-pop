"""Narrative document registry, storage, and layered resolution.

add-full-document-editor makes every narrative disclosure document one
operator-editable rich-text body. Content is *data*, not template logic:
each document's baseline ships as a chip-bearing HTML file in the repo
(``app/disclosure_package/content/standard/<doc_id>.html``, git-reviewed so
legally-vetted prose stays code-reviewed), and the matching template
collapses to ``{{ narrative.<doc_id> | safe_html }}``.

Two override layers sit above the baseline in ``narrative_overrides``:

    HOA row (scope='hoa', scope_id=property_id)
      → firm row (scope='firm', scope_id IS NULL)
        → repo baseline

"Reset to default" is a row DELETE, which is why there is no third
``system`` layer — the repo file already is the immutable baseline and a
redeploy restores it.

Computed financial schedules and the §5570 statutory form are deliberately
absent from ``DOCUMENT_REGISTRY``: they stay their own untouched templates
and are never writable through this module.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from sqlalchemy import text as sql_text

from . import boilerplate_sanitize, boilerplate_variables

CONTENT_ROOT = (
    Path(__file__).resolve().parents[1] / "disclosure_package" / "content"
)

FIRM_SCOPE = "firm"
HOA_SCOPE = "hoa"
VALID_SCOPES = (FIRM_SCOPE, HOA_SCOPE)


class UnknownNarrativeDocument(ValueError):
    """Raised when a doc_id isn't in DOCUMENT_REGISTRY (incl. computed pages)."""


class UnknownNarrativeScope(ValueError):
    """Raised when a scope isn't 'firm' or 'hoa', or scope_id doesn't match it."""


class MissingRequiredBlock(ValueError):
    """Raised when a document drops a block chip listed in REQUIRED_BLOCKS."""


@dataclass(frozen=True)
class NarrativeDocument:
    """One editable document: its baseline file, template, and hard constraints."""

    doc_id: str
    template: str
    label: str
    #: Block chips that MUST survive every edit. Deleting one is an operator
    #: mistake with statutory consequences (§5300), so it blocks finalize
    #: rather than silently dropping required disclosure language.
    required_blocks: frozenset[str] = field(default_factory=frozenset)

    @property
    def baseline_path(self) -> Path:
        return CONTENT_ROOT / "standard" / f"{self.doc_id}.html"


# Registry of editable documents. Package / editor order comes from the firm
# section catalog (`section_order.py`); computed pages interleave from
# COMPUTED_PLACEHOLDERS keyed by template.
_DOCUMENTS: tuple[NarrativeDocument, ...] = (
    NarrativeDocument(
        doc_id="cover_letter",
        template="cover_letter.html",
        label="Cover letter",
        required_blocks=frozenset({"special_assessment_disclosure"}),
    ),
    NarrativeDocument(
        doc_id="annual_budget_cover",
        template="annual_budget_report_cover.html",
        label="Annual budget report — cover",
    ),
    NarrativeDocument(
        doc_id="budget_toc",
        template="annual_budget_report_toc.html",
        label="Table of contents",
        required_blocks=frozenset({"appendix_toc_rows", "package_toc_rows"}),
    ),
    NarrativeDocument(
        doc_id="forecasted_title",
        template="forecasted_statement_title.html",
        label="Forecasted statement — title page",
    ),
    NarrativeDocument(
        doc_id="compilation_report",
        template="compilation_report.html",
        label="Accountants' compilation report",
    ),
    NarrativeDocument(
        doc_id="note_1_3",
        template="notes_1_to_3.html",
        label="Notes 1–3",
    ),
    NarrativeDocument(
        doc_id="note_4_5",
        template="note_4_5.html",
        label="Notes 4–5",
    ),
    NarrativeDocument(
        doc_id="note_6",
        template="note_6_funding_plan.html",
        label="Note 6 — Funding plan",
        required_blocks=frozenset({"contribution_increase_schedule"}),
    ),
    NarrativeDocument(
        doc_id="note_7",
        template="note_7.html",
        label="Note 7 — Significant assumptions",
        required_blocks=frozenset({"significant_assumptions_variance"}),
    ),
    NarrativeDocument(
        doc_id="note_8",
        template="note_8.html",
        label="Note 8 — Outstanding loans",
        required_blocks=frozenset({"outstanding_loan_note"}),
    ),
    NarrativeDocument(
        doc_id="reserve_schedule_title",
        template="reserve_component_schedule_title.html",
        label="Reserve component schedule — title page",
    ),
    NarrativeDocument(
        doc_id="insurance_cover",
        template="insurance_disclosure_cover.html",
        label="Insurance disclosure — cover",
    ),
    NarrativeDocument(
        doc_id="thirty_year_title",
        template="thirty_year_study_title.html",
        label="30-year funding study — title page",
    ),
    NarrativeDocument(
        doc_id="thirty_year_compilation",
        template="thirty_year_study_compilation.html",
        label="Compilation report — 30-year study",
    ),
)

DOCUMENT_REGISTRY: dict[str, NarrativeDocument] = {
    doc.doc_id: doc for doc in _DOCUMENTS
}

#: template filename → doc_id, for the compiler's per-page lookup.
TEMPLATE_TO_DOCUMENT: dict[str, str] = {
    doc.template: doc.doc_id for doc in _DOCUMENTS
}

#: Computed pages, keyed by template, with the doc_id they follow in package
#: order. The editor renders these as read-only placeholder cards so the
#: report reads in order without needing a live render (design.md D6).
COMPUTED_PLACEHOLDERS: tuple[dict[str, Any], ...] = (
    {
        "template": "assessment_schedule/universal.html",
        "label": "Assessment schedule",
        "after": "budget_toc",
        "page_count_hint": 2,
    },
    {
        "template": "pro_forma_disclosure_summary.html",
        "label": "Pro forma disclosure summary (§5570 statutory form)",
        "after": "budget_toc",
        "page_count_hint": 4,
    },
    {
        "template": "forecasted_income_statement.html",
        "label": "Forecasted statement of revenues and expenses",
        "after": "compilation_report",
        "page_count_hint": 2,
    },
    {
        "template": "reserve_component_schedule.html",
        "label": "Schedule of major component replacement provision",
        "after": "reserve_schedule_title",
        "page_count_hint": 5,
    },
    {
        "template": "thirty_year_cash_flow_panel.html",
        "label": "30-year cash flow forecast",
        "after": "thirty_year_compilation",
        "page_count_hint": 3,
    },
    {
        "template": "major_component_schedule.html",
        "label": "Major component repair and replacement costs",
        "after": "thirty_year_compilation",
        "page_count_hint": 14,
    },
)

# Both block-chip carriers (div and li) — shared with the resolver so a chip
# the resolver would substitute can never read as "absent" to the
# required-block check.
_BLOCK_RE = boilerplate_variables.BLOCK_CARRIER_RE


# ── registry helpers ────────────────────────────────────────────────────────


def require_document(doc_id: str) -> NarrativeDocument:
    doc = DOCUMENT_REGISTRY.get(doc_id)
    if doc is None:
        raise UnknownNarrativeDocument(f"Unknown narrative document: {doc_id!r}")
    return doc


def document_ids() -> list[str]:
    """Editable document ids in package order."""
    return [doc.doc_id for doc in _DOCUMENTS]


def blocks_present(html: Optional[str]) -> set[str]:
    """`data-block` names appearing in a document body, on either carrier."""
    if not html:
        return set()
    return {name for _tag, name in _BLOCK_RE.findall(html)}


# ── baselines ───────────────────────────────────────────────────────────────


@lru_cache(maxsize=None)
def _read_baseline(doc_id: str) -> str:
    path = require_document(doc_id).baseline_path
    if not path.is_file():
        raise FileNotFoundError(
            f"Missing narrative baseline for {doc_id!r}: {path}"
        )
    return path.read_text(encoding="utf-8").strip()


def baseline_html(doc_id: str) -> str:
    """The shipped, git-reviewed body for a document."""
    return _read_baseline(doc_id)


# ── storage ─────────────────────────────────────────────────────────────────


def _check_scope(scope: str, scope_id: Optional[int]) -> None:
    if scope not in VALID_SCOPES:
        raise UnknownNarrativeScope(f"Unknown narrative scope: {scope!r}")
    if scope == FIRM_SCOPE and scope_id is not None:
        raise UnknownNarrativeScope("Firm-scope overrides must not carry a scope_id")
    if scope == HOA_SCOPE and scope_id is None:
        raise UnknownNarrativeScope("HOA-scope overrides require a scope_id")


def _fetch(session: Any, scope: str, scope_id: Optional[int]) -> dict[str, str]:
    """All stored bodies at one scope, keyed by document_id."""
    if scope == FIRM_SCOPE:
        rows = session.execute(
            sql_text(
                "SELECT document_id, body_html FROM narrative_overrides "
                "WHERE scope = 'firm'"
            )
        ).fetchall()
    else:
        rows = session.execute(
            sql_text(
                "SELECT document_id, body_html FROM narrative_overrides "
                "WHERE scope = 'hoa' AND scope_id = :sid"
            ),
            {"sid": scope_id},
        ).fetchall()
    return {row[0]: row[1] for row in rows if row[1]}


def heal_document_html(doc_id: str, html: Optional[str]) -> str:
    """Restore system chips that older saved copies predate.

    ``package_toc_rows`` was added after firm/HOA TOC overrides were already
    stored. Those copies still have the old hardcoded rows and would block
    every generate. The shipped baseline is the source of truth for that
    chip; operator wording on other documents is left alone.
    """
    body = html or ""
    if doc_id != "budget_toc":
        return body
    if "package_toc_rows" in blocks_present(body):
        return body
    return baseline_html(doc_id)


def resolve_document(
    session: Any, doc_id: str, hoa_id: Optional[int] = None
) -> str:
    """One document's effective body: HOA row → firm row → repo baseline."""
    require_document(doc_id)
    if hoa_id is not None:
        hoa_rows = _fetch(session, HOA_SCOPE, hoa_id)
        if doc_id in hoa_rows:
            return heal_document_html(doc_id, hoa_rows[doc_id])
    firm_rows = _fetch(session, FIRM_SCOPE, None)
    if doc_id in firm_rows:
        return heal_document_html(doc_id, firm_rows[doc_id])
    return baseline_html(doc_id)


def effective_scope(
    session: Any, doc_id: str, hoa_id: Optional[int] = None
) -> str:
    """Which layer a document currently resolves from: 'hoa'|'firm'|'baseline'."""
    require_document(doc_id)
    if hoa_id is not None and doc_id in _fetch(session, HOA_SCOPE, hoa_id):
        return HOA_SCOPE
    if doc_id in _fetch(session, FIRM_SCOPE, None):
        return FIRM_SCOPE
    return "baseline"


def resolve_all(session: Any, hoa_id: Optional[int] = None) -> dict[str, str]:
    """The full ``narrative`` map for one compile pass — every document resolved.

    Every registry key is always present, so ``StrictUndefined`` templates can
    reference ``narrative.<doc_id>`` unconditionally.
    """
    firm_rows = _fetch(session, FIRM_SCOPE, None)
    hoa_rows = _fetch(session, HOA_SCOPE, hoa_id) if hoa_id is not None else {}
    return {
        doc_id: heal_document_html(
            doc_id,
            hoa_rows.get(doc_id) or firm_rows.get(doc_id) or baseline_html(doc_id),
        )
        for doc_id in DOCUMENT_REGISTRY
    }


def validate_document_html(doc_id: str, html: str) -> str:
    """Sanitize + reject unknown chips and missing required blocks.

    Returns the sanitized HTML to store. Raises
    ``UnknownBoilerplateToken`` / ``MissingRequiredBlock`` on rejection —
    both surface as HTTP 400 at the API boundary and as blocking preflight
    errors at compile time.
    """
    doc = require_document(doc_id)
    sanitized = boilerplate_sanitize.sanitize_slot_html(html) or ""
    unknown = boilerplate_variables.find_unknown_tokens(sanitized)
    if unknown:
        raise boilerplate_variables.UnknownBoilerplateToken(
            f"Unknown token(s) in document {doc_id!r}: {', '.join(unknown)}"
        )
    non_empty = boilerplate_variables.find_non_empty_blocks(sanitized)
    if non_empty:
        raise boilerplate_variables.UnknownBoilerplateToken(
            f"Block(s) in document {doc_id!r} must be empty placeholders: "
            f"{', '.join(non_empty)}"
        )
    missing = sorted(doc.required_blocks - blocks_present(sanitized))
    if missing:
        raise MissingRequiredBlock(
            f"Document {doc_id!r} is missing required block(s): {', '.join(missing)}"
        )
    return sanitized


def save_document(
    session: Any,
    doc_id: str,
    scope: str,
    scope_id: Optional[int],
    html: str,
    updated_by: Optional[str] = None,
) -> str:
    """Sanitize, validate, and upsert one document body at one scope."""
    require_document(doc_id)
    _check_scope(scope, scope_id)
    sanitized = validate_document_html(doc_id, html)

    # Upsert without ON CONFLICT: the uniqueness constraint lives in two
    # partial indexes (SQLite treats NULL scope_id as distinct), and partial
    # indexes are not valid conflict targets.
    reset_document(session, doc_id, scope, scope_id)
    session.execute(
        sql_text(
            "INSERT INTO narrative_overrides "
            "(scope, scope_id, document_id, body_html, updated_at, updated_by) "
            "VALUES (:scope, :sid, :doc, :body, datetime('now'), :who)"
        ),
        {
            "scope": scope,
            "sid": scope_id,
            "doc": doc_id,
            "body": sanitized,
            "who": updated_by,
        },
    )
    return sanitized


def reset_document(
    session: Any, doc_id: str, scope: str, scope_id: Optional[int]
) -> bool:
    """Delete one document's override at one scope. True if a row was removed."""
    require_document(doc_id)
    _check_scope(scope, scope_id)
    if scope == FIRM_SCOPE:
        result = session.execute(
            sql_text(
                "DELETE FROM narrative_overrides "
                "WHERE scope = 'firm' AND document_id = :doc"
            ),
            {"doc": doc_id},
        )
    else:
        result = session.execute(
            sql_text(
                "DELETE FROM narrative_overrides "
                "WHERE scope = 'hoa' AND scope_id = :sid AND document_id = :doc"
            ),
            {"sid": scope_id, "doc": doc_id},
        )
    return bool(result.rowcount)


def delete_hoa_overrides(session: Any, hoa_id: int) -> int:
    """Drop every HOA-scope row for one property.

    ``scope_id`` is polymorphic across scopes, so it carries no foreign key and
    a property delete will not cascade here. There is no property-delete path
    in the app today; this exists so that whoever adds one has the cleanup to
    hand rather than leaving orphaned rows that a recycled id would inherit.
    """
    result = session.execute(
        sql_text(
            "DELETE FROM narrative_overrides WHERE scope = 'hoa' AND scope_id = :sid"
        ),
        {"sid": hoa_id},
    )
    return int(result.rowcount or 0)


# ── render-path resolution ──────────────────────────────────────────────────


def for_render(
    *,
    use_snapshots: bool,
    frozen: Optional[Mapping[str, Any]],
    session: Any = None,
    hoa_id: Optional[int] = None,
) -> dict[str, str]:
    """Narrative map for a package render.

    Snapshot branch: use the frozen ``compile_context["narrative"]`` only, so a
    finalized package re-renders byte-equal no matter how firm or HOA content
    changed afterwards. A document absent from an older snapshot falls back to
    its baseline rather than to current live content — the snapshot is the
    authority for everything it does carry.
    """
    if use_snapshots:
        frozen_map = frozen if isinstance(frozen, dict) else {}
        return {
            doc_id: heal_document_html(
                doc_id, str(frozen_map.get(doc_id) or baseline_html(doc_id))
            )
            for doc_id in DOCUMENT_REGISTRY
        }
    return resolve_all(session, hoa_id)


# ── legacy migration ────────────────────────────────────────────────────────

#: Where each retired cover-letter slot's content sat in the letter. The
#: anchors are the baseline markup immediately following the slot, so a slot's
#: saved wording lands exactly where it used to render.
_LEGACY_SLOT_ANCHORS: tuple[tuple[str, str], ...] = (
    ("cover_letter_intro", '<ul class="letter-bullets">'),
    ("enclosed_documents_list", "<p>As per civil code"),
    ("cover_letter_closing", '<div class="letter-signature">'),
)

#: The baseline paragraphs each slot used to replace. Removed when that slot
#: carried a value, so the operator's wording substitutes rather than
#: duplicates — the same substitution the retired template branches performed.
_LEGACY_SLOT_REPLACES: dict[str, tuple[str, str]] = {
    "cover_letter_intro": ("<p>Thank you for the prompt payment", "</p>"),
    "enclosed_documents_list": ('<ol class="enclosed-list">', "</ol>"),
    "cover_letter_closing": ('<p class="letter-closing">', "</p>"),
}


def compose_legacy_cover_letter(
    slots: Mapping[str, Optional[str]]
) -> Optional[str]:
    """Fold the three retired cover-letter slots into one `cover_letter` body.

    Returns ``None`` when no slot carries content (nothing to migrate). Each
    slot's saved HTML replaces the baseline paragraph it used to override, in
    the position it used to render, so the composed letter reads exactly as the
    operator's did before this change.

    If an anchor can't be found — a baseline reworded since the slots were
    saved — the slot's content is appended to the end rather than dropped.
    Losing operator work silently is the one outcome this must never have.
    """
    if not any(slots.get(slot) for slot, _ in _LEGACY_SLOT_ANCHORS):
        return None

    body = baseline_html("cover_letter")
    orphaned: list[str] = []

    for slot, anchor in _LEGACY_SLOT_ANCHORS:
        value = (slots.get(slot) or "").strip()
        if not value:
            continue
        if not value.lstrip().startswith("<"):
            value = f"<p>{value}</p>"

        start_marker, end_marker = _LEGACY_SLOT_REPLACES[slot]
        start = body.find(start_marker)
        end = body.find(end_marker, start + len(start_marker)) if start != -1 else -1
        if start != -1 and end != -1:
            body = body[:start] + value + body[end + len(end_marker):]
            continue

        position = body.find(anchor)
        if position != -1:
            body = body[:position] + value + "\n" + body[position:]
        else:
            orphaned.append(value)

    if orphaned:
        body = body + "\n" + "\n".join(orphaned)

    return boilerplate_sanitize.sanitize_slot_html(body)


def resolve_for_context(
    context: Mapping[str, Any], bodies: Optional[Mapping[str, str]] = None
) -> dict[str, str]:
    """Resolve every document's chips from an assembled render context.

    ``compile_package`` builds the narrative map itself (it must resolve twice
    — once before pass 1, once after real page numbers exist). This is the
    fallback for callers that render a single template directly with a
    hand-assembled context: without it, every such caller would have to
    duplicate the chip-resolution wiring just to satisfy ``StrictUndefined``.
    """
    computed = context.get("computed")
    if not isinstance(computed, Mapping):
        # compile_package splats `computed` into the context rather than
        # nesting it; treat the context itself as the fact source.
        computed = context

    var_map = boilerplate_variables.build_var_map(
        hoa=context.get("hoa"),
        fiscal_year=context.get("fiscal_year") or 0,
        hoa_settings=context.get("hoa_settings"),
        computed=computed,
        matrix=context.get("matrix"),
        static_data=context.get("static_data"),
        today=context.get("today") or "",
        reserve_study_snapshot=context.get("reserve_study_snapshot"),
        toc_page_numbers=context.get("toc_page_numbers") or {},
    )
    block_map = boilerplate_variables.build_block_map(
        fiscal_year=context.get("fiscal_year") or 0,
        computed=computed,
        matrix=context.get("matrix"),
        static_data=context.get("static_data"),
        appendix_toc_entries=context.get("appendix_toc_entries") or [],
        toc_page_numbers=context.get("toc_page_numbers") or {},
        package_templates=context.get("package_templates"),
        section_order=context.get("section_order"),
        hidden_sections=context.get("hidden_sections"),
    )
    source = bodies or {}
    return {
        doc_id: boilerplate_variables.resolve(
            source.get(doc_id) or baseline_html(doc_id), var_map, block_map
        )
        for doc_id in DOCUMENT_REGISTRY
    }


def documents_for_api(
    session: Any, hoa_id: Optional[int] = None
) -> list[dict[str, Any]]:
    """Editable documents in package order, with computed placeholders interleaved."""
    from app.disclosure_package.section_order import (
        CATALOG_BY_TEMPLATE,
        load_saved_lists_from_session,
        resolve_generated_templates,
    )

    firm_rows = _fetch(session, FIRM_SCOPE, None)
    hoa_rows = _fetch(session, HOA_SCOPE, hoa_id) if hoa_id is not None else {}
    saved_order, _hidden = load_saved_lists_from_session(session)
    # Hidden optionals stay in the editor so wording can still be maintained.
    templates = resolve_generated_templates(saved_order, hidden=())
    computed_by_template = {item["template"]: item for item in COMPUTED_PLACEHOLDERS}

    def _editable_row(doc: NarrativeDocument) -> dict[str, Any]:
        if doc.doc_id in hoa_rows:
            scope, body = HOA_SCOPE, hoa_rows[doc.doc_id]
        elif doc.doc_id in firm_rows:
            scope, body = FIRM_SCOPE, firm_rows[doc.doc_id]
        else:
            scope, body = "baseline", baseline_html(doc.doc_id)
        return {
            "kind": "editable",
            "id": doc.doc_id,
            "label": doc.label,
            "html": heal_document_html(doc.doc_id, body),
            "effective_scope": scope,
            "has_firm_override": doc.doc_id in firm_rows,
            "has_hoa_override": doc.doc_id in hoa_rows,
            "required_blocks": sorted(doc.required_blocks),
        }

    out: list[dict[str, Any]] = []
    seen_editable: set[str] = set()
    for template in templates:
        entry = CATALOG_BY_TEMPLATE[template]
        if entry.bundle:
            for doc_id in entry.bundle:
                doc = DOCUMENT_REGISTRY[doc_id]
                out.append(_editable_row(doc))
                seen_editable.add(doc.doc_id)
            continue
        doc_id = TEMPLATE_TO_DOCUMENT.get(template)
        if doc_id:
            out.append(_editable_row(DOCUMENT_REGISTRY[doc_id]))
            seen_editable.add(doc_id)
            continue
        placeholder = computed_by_template.get(template)
        if placeholder:
            out.append(
                {
                    "kind": "computed",
                    "id": placeholder["template"],
                    "label": placeholder["label"],
                    "page_count_hint": placeholder["page_count_hint"],
                }
            )
    for doc in _DOCUMENTS:
        if doc.doc_id not in seen_editable:
            out.append(_editable_row(doc))
    return out
