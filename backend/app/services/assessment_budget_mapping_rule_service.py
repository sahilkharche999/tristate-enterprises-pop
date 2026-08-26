"""Reusable DRE-derived budget mapping rules.

This service persists stable setup-level hints from an approved DRE
extraction. Annual ``budget_line_pool_mappings`` are still the concrete
current-year rows; these rules are the reusable evidence used to create
or suggest those rows later.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Optional

from app.dre_extraction.schemas import DRESetupExtraction


_LABEL_TOKEN_RE = re.compile(r"[^a-z0-9]+")
_ACCOUNT_PREFIX_RE = re.compile(r"^\s*\d{3,}(?:-\d+)?\s*[-–:]\s*")
_GENERIC_LABEL_TOKENS = {
    "general",
    "service",
    "services",
    "fee",
    "fees",
    "maint",
    "maintenance",
    "repairs",
}

BudgetLineEligibility = str
_BLOCKED_REGULAR_MAPPING_ASSESSMENT_TYPES = {
    "exemption_credit",
    "subsidy_credit",
    "pass_through",
    "reserve_component",
    "excluded_or_informational",
}
_RULE_SOURCE_RANK = {
    "dre_mapping_evidence": 0,
    "dre_included_budget_line": 1,
    "carried_forward": 2,
}
_REGULAR_REVIEW_ROW_ROLE = "current_year_operating_budget_line"
_RESERVE_CONTRIBUTION_REVIEW_ROW_ROLE = "current_year_reserve_contribution_line"
# Rows that feed assessment-schedule pool dollars (ops + reserve contribution).
# Reserve *component detail* stays outside — those are spend lines, not dues.
_SCHEDULE_BASIS_ROW_ROLES = {
    _REGULAR_REVIEW_ROW_ROLE,
    _RESERVE_CONTRIBUTION_REVIEW_ROW_ROLE,
}
_REVIEWABLE_ROW_ROLES = {
    _REGULAR_REVIEW_ROW_ROLE,
    _RESERVE_CONTRIBUTION_REVIEW_ROW_ROLE,
    "reserve_component_detail",
    "reserve_cashflow_detail",
    "pass_through_or_reimbursement",
    "unknown_needs_review",
}
# Reserve component/cashflow lines default to a "Reserve Detail" disposition in
# the review so the operator isn't forced to click through each one.
# The reserve *contribution / transfer* line is schedule-basis: it must be
# assignable to the reserve_contributions pool so the assessment schedule can
# split operating vs reserve dues (not Note 6 alone).
_RESERVE_REVIEW_ROW_ROLES = {
    "reserve_component_detail",
    "reserve_cashflow_detail",
}
_REVIEW_ROW_ROLE_REASONS = {
    _REGULAR_REVIEW_ROW_ROLE: "eligible current-year operating budget line",
    _RESERVE_CONTRIBUTION_REVIEW_ROW_ROLE: (
        "current-year reserve contribution line — assign to the reserve "
        "contributions pool for the assessment schedule split"
    ),
    "reserve_component_detail": "reserve component detail line",
    "reserve_cashflow_detail": "reserve cashflow detail line",
    "pass_through_or_reimbursement": "pass-through or reimbursement line",
    "unknown_needs_review": "row needs operator review",
}


def _is_reserve_pool_option(option: dict[str, object]) -> bool:
    key = str(option.get("pool_key") or "").lower()
    name = str(option.get("pool_name") or "").lower()
    return "reserve" in key or "reserve" in name


def _pool_options_for_row_role(
    row_role: str,
    valid_pool_options: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Prefer reserve pools for contribution lines; fall back to all pools."""
    if row_role != _RESERVE_CONTRIBUTION_REVIEW_ROW_ROLE:
        return list(valid_pool_options)
    reserve_opts = [opt for opt in valid_pool_options if _is_reserve_pool_option(opt)]
    return reserve_opts or list(valid_pool_options)


def _preferred_reserve_pool_key(
    valid_pool_options: list[dict[str, object]],
) -> Optional[str]:
    """Pick the best reserve pool key for contribution-line suggestions."""
    if not valid_pool_options:
        return None
    for preferred in (
        "reserve_contributions",
        "reserve_contribution",
        "replacement_fund",
        "reserves",
    ):
        for opt in valid_pool_options:
            if str(opt.get("pool_key") or "").lower() == preferred:
                return str(opt["pool_key"])
    for opt in valid_pool_options:
        if _is_reserve_pool_option(opt):
            return str(opt["pool_key"])
    return None


@dataclass(frozen=True)
class BudgetLineClassification:
    line_key: tuple[str, str, str, str, Optional[str]]
    line_label: str
    amount: Optional[Decimal]
    eligibility: BudgetLineEligibility
    requires_mapping: bool
    reason: str
    canonical: bool = True


@dataclass(frozen=True)
class DuplicateBudgetLineConflict:
    normalized_label: str
    line_labels: list[str]
    amounts: list[Decimal]


@dataclass(frozen=True)
class BudgetLineClassificationResult:
    classifications: list[BudgetLineClassification]
    duplicate_conflicts: list[DuplicateBudgetLineConflict]


@dataclass(frozen=True)
class MappingReconciliation:
    mapped_pool_total: Decimal
    assessment_target: Decimal
    selected_budget_source_total: Decimal
    excluded_total: Decimal
    offset_total: Decimal
    schedule_annual_total: Decimal
    tolerance: Decimal
    passed: bool
    failures: list[str]


@dataclass(frozen=True)
class LineReviewCandidate:
    rule_id: int
    pool_key: str
    pool_name: str
    score: float
    match_reason: str
    decision_level: str
    source_pages: list[int]
    source_evidence_text: str
    review_reason: str
    match_label: str
    rule_source: str
    budget_line_derivation: str


def normalize_budget_label(label: str) -> str:
    """Normalize a budget-line label for stable matching."""
    normalized = _LABEL_TOKEN_RE.sub(" ", str(label or "").lower())
    return " ".join(normalized.split())


def _label_without_account_prefix(label: str) -> str:
    return _ACCOUNT_PREFIX_RE.sub("", str(label or "")).strip()


def _semantic_label_tokens(label: str) -> frozenset[str]:
    text = normalize_budget_label(_label_without_account_prefix(label))
    return frozenset(
        token for token in text.split()
        if token and token not in _GENERIC_LABEL_TOKENS
    )


def _canonical_display_score(line: dict) -> tuple[int, int]:
    label = str(line.get("label") or line.get("normalized_label") or "")
    has_account_prefix = 1 if _ACCOUNT_PREFIX_RE.match(label) else 0
    has_account_code = 1 if line.get("account_code") not in (None, "") else 0
    return (has_account_prefix, has_account_code)


def canonicalize_budget_lines_for_mapping(budget_lines: list[dict]) -> list[dict]:
    """Collapse raw + client-style versions of the same economic row.

    Example: ``55000 - General Insurance`` and ``Insurance`` with the same
    amount are one budget item. Keep the display-friendly label while
    preserving account code/source metadata from the raw row.
    """
    grouped: dict[tuple[frozenset[str], Optional[Decimal], str, str], list[dict]] = {}
    passthrough: list[dict] = []
    for line in budget_lines:
        label = str(line.get("label") or line.get("normalized_label") or "")
        tokens = _semantic_label_tokens(label)
        amount = _decimal_or_none(line.get("amount"))
        category = str(line.get("category") or "")
        fund_type = str(line.get("fund_type") or "")
        if not tokens:
            passthrough.append(line)
            continue
        grouped.setdefault((tokens, amount, category, fund_type), []).append(line)

    canonicalized: list[dict] = []
    for lines in grouped.values():
        if len(lines) == 1:
            canonicalized.append(lines[0])
            continue
        normalized_labels = {
            normalize_budget_label(str(line.get("label") or line.get("normalized_label") or ""))
            for line in lines
        }
        if len(normalized_labels) == 1:
            canonicalized.extend(lines)
            continue
        canonical = dict(sorted(lines, key=_canonical_display_score)[0])
        for source in lines:
            if canonical.get("account_code") in (None, "") and source.get("account_code") not in (None, ""):
                canonical["account_code"] = source.get("account_code")
            if "source_label" not in canonical:
                source_label = str(source.get("label") or "")
                if source_label and source_label != canonical.get("label"):
                    canonical["source_label"] = source_label
        canonicalized.append(canonical)
    return [*canonicalized, *passthrough]


def _decimal_or_none(value: object) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def select_assessment_mapping_amount(
    line: dict,
) -> tuple[Optional[Decimal], str]:
    """Pick one canonical current-year amount for assessment mapping review."""
    explicit_amount = _decimal_or_none(line.get("assessment_mapping_amount"))
    if explicit_amount is not None:
        return explicit_amount, str(line.get("source_column_used") or "assessment_mapping_amount")

    for field, source in (
        ("proposed_amount", "proposed_amount"),
        ("proposedAmount", "proposed_amount"),
        ("annual_budget", "annual_budget"),
        ("projection", "projection"),
        ("amount", "amount"),
    ):
        amount = _decimal_or_none(line.get(field))
        if amount is not None:
            return amount, source

    return None, "none"


def active_budget_lines_for_property(
    connection: sqlite3.Connection,
    *,
    property_id: int,
) -> tuple[Optional[int], list[dict]]:
    """Load the active draft in the canonical shape used by mapping review.

    Every consumer that evaluates allocation readiness must inspect the same
    current-year lines as the operator mapping screen. Returning the draft id
    alongside the lines also prevents a caller from accidentally mixing an
    older draft with current setup data.
    """
    draft = connection.execute(
        """
        SELECT id, line_items_json
          FROM budget_drafts
         WHERE property_id = ? AND status = 'active'
         ORDER BY updated_at DESC, id DESC
         LIMIT 1
        """,
        (property_id,),
    ).fetchone()
    if draft is None:
        return None, []
    try:
        line_items = json.loads(draft[1] or "[]")
    except (TypeError, json.JSONDecodeError):
        return int(draft[0]), []
    lines: list[dict] = []
    for item in line_items:
        if not isinstance(item, dict):
            continue
        raw_category = str(item.get("category") or "").lower()
        category = (
            "income"
            if raw_category == "income"
            else "reserve_income"
            if raw_category == "reserve_income"
            else "reserve_expense"
            if raw_category in {"reserve", "reserve_expense"}
            else "operating"
        )
        label = str(item.get("label") or item.get("line_item_key") or "")
        normalized_label = normalize_budget_label(label)
        section = str((item.get("raw") or {}).get("section") or category)
        fund_type = (
            "reserve" if category in {"reserve_income", "reserve_expense"}
            else "operating"
        )
        account_code = (
            str(item["account_code"])
            if item.get("account_code") not in (None, "")
            else None
        )
        amount, source_column_used = select_assessment_mapping_amount(item)
        lines.append(
            {
                "label": label,
                "normalized_label": normalized_label,
                "section": section,
                "category": category,
                "fund_type": fund_type,
                "account_code": account_code,
                "source_line_key": build_budget_line_slice_key(
                    normalized_label=normalized_label,
                    section=section,
                    category=category,
                    fund_type=fund_type,
                    account_code=account_code,
                ),
                "annual_budget": item.get("annual_budget"),
                "proposed_amount": (
                    item.get("proposed_amount")
                    if item.get("proposed_amount") is not None
                    else item.get("proposedAmount")
                ),
                "projection": item.get("projection"),
                "assessment_mapping_amount": (
                    float(amount) if amount is not None else None
                ),
                "source_column_used": source_column_used,
                "amount": float(amount) if amount is not None else None,
                "reserve_group": item.get("reserve_group") or item.get("reserveGroup"),
                "active": not bool(item.get("inactive")),
            }
        )
    return int(draft[0]), lines


def classify_assessment_mapping_review_row_role(line: dict) -> str:
    label = str(line.get("label") or line.get("normalized_label") or "")
    normalized = normalize_budget_label(label)
    section = normalize_budget_label(str(line.get("section") or ""))
    category = str(line.get("category") or "").lower()
    fund_type = str(line.get("fund_type") or "").lower()
    reserve_group = str(
        line.get("reserve_group")
        or line.get("reserveGroup")
        or ""
    ).lower()
    amount, _source_column_used = select_assessment_mapping_amount(line)

    if line.get("active") is False:
        return "inactive"
    if not normalized:
        return "unknown_needs_review"
    if amount is None or amount == 0:
        return "zero_or_blank"
    if (
        normalized.startswith("total ")
        or normalized.startswith("subtotal ")
        or normalized.endswith(" total")
        or " subtotal " in normalized
    ):
        return "subtotal_or_total"
    if (
        "pass through" in normalized
        or "pass-through" in label.lower()
        or "reimburs" in normalized
    ):
        return "pass_through_or_reimbursement"
    if category in {"income", "reserve_income"}:
        if "interest" in normalized:
            return "interest_income"
        if "assessment" in normalized or "dues" in normalized:
            return "assessment_revenue_tieout"
        return "other_income"
    if (
        "reserve" in normalized
        and any(
            marker in normalized
            for marker in ("allocation", "transfer", "contribution", "funding")
        )
    ):
        return "current_year_reserve_contribution_line"
    if reserve_group in {"component", "transfer"} or category in {"reserve", "reserve_expense"} or fund_type == "reserve":
        if reserve_group == "transfer" or any(
            marker in normalized
            for marker in ("allocation", "transfer", "contribution", "funding")
        ):
            return "current_year_reserve_contribution_line"
        if "cash flow" in normalized or "cashflow" in normalized:
            return "reserve_cashflow_detail"
        return "reserve_component_detail"
    if category in {"operating", "expense"} or fund_type == "operating":
        return _REGULAR_REVIEW_ROW_ROLE
    return "unknown_needs_review"


def build_assessment_mapping_review_line_key(
    line: dict,
    *,
    row_role: str,
    source_column_used: str,
) -> str:
    normalized, section, category, fund_type, account_code = _line_key(line)
    return "|".join(
        [
            normalized,
            normalize_budget_label(section),
            category,
            fund_type,
            account_code or "",
            row_role,
            source_column_used,
        ]
    )


def build_budget_line_slice_key(
    *,
    normalized_label: str,
    section: str,
    category: str,
    fund_type: str,
    account_code: Optional[str],
) -> str:
    """Build the durable identity used to attach slices to one budget row."""
    return json.dumps(
        [
            normalize_budget_label(normalized_label),
            normalize_budget_label(section),
            str(category or "").strip().lower(),
            str(fund_type or "").strip().lower(),
            str(account_code or ""),
        ],
        separators=(",", ":"),
    )


def _with_assessment_mapping_amounts(budget_lines: list[dict]) -> list[dict]:
    enriched: list[dict] = []
    for line in budget_lines:
        enriched_line = dict(line)
        amount, source_column_used = select_assessment_mapping_amount(enriched_line)
        if enriched_line.get("assessment_mapping_amount") in (None, ""):
            enriched_line["assessment_mapping_amount"] = (
                float(amount) if amount is not None else None
            )
        if not enriched_line.get("source_column_used"):
            enriched_line["source_column_used"] = source_column_used
        if enriched_line.get("amount") in (None, ""):
            enriched_line["amount"] = float(amount) if amount is not None else None
        enriched.append(enriched_line)
    return canonicalize_budget_lines_for_mapping(enriched)


def _saved_slices_for_line(
    *,
    connection: sqlite3.Connection,
    assessment_setup_id: int,
    normalized_label: str,
    account_code: Optional[str],
    source_line_key: Optional[str],
) -> list[dict[str, object]]:
    """Return the current saved split rows for one source budget line.

    Allocation-resolution owns the slice row model. This adapter deliberately
    keeps the mapping-review service independent of its Pydantic types while
    still applying the same normalized-label/account identity used by the
    active budget mapping.
    """
    if not _sqlite_table_exists(connection, "budget_line_allocation_slices"):
        return []
    from app.allocation_resolution.service import list_slices

    normalized_account = (
        str(account_code) if account_code not in (None, "") else None
    )
    slices = list_slices(
        connection,
        assessment_setup_id=assessment_setup_id,
        source_line_normalized_label=normalized_label,
        source_line_key=source_line_key,
        source_line_account_code=normalized_account,
    )
    if not slices and source_line_key is not None:
        # Keep slices created before durable row identities were introduced.
        slices = list_slices(
            connection,
            assessment_setup_id=assessment_setup_id,
            source_line_normalized_label=normalized_label,
            source_line_account_code=normalized_account,
        )
        slices = [item for item in slices if not item.source_line_key]
    return [
        item.model_dump(mode="json")
        for item in slices
    ]


def _combined_line_categories(
    *,
    connection: sqlite3.Connection,
    assessment_setup_id: int,
    line_label: str,
) -> list[str]:
    """Find governing-document categories that only partially match a line."""
    if not _sqlite_table_exists(connection, "allocation_resolutions"):
        return []
    from app.allocation_resolution.semantic_mapping import classify_label_match
    from app.allocation_resolution.service import list_current_resolutions

    categories: list[str] = []
    for resolution in list_current_resolutions(
        connection,
        assessment_setup_id=assessment_setup_id,
    ):
        for category in resolution.included_categories:
            if (
                classify_label_match(str(category), line_label) == "combined"
                and str(category) not in categories
            ):
                categories.append(str(category))
    return categories


def _sqlite_table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        """
        SELECT 1
          FROM sqlite_master
         WHERE type = 'table'
           AND name = ?
         LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    if not _sqlite_table_exists(connection, table_name):
        return set()
    return {
        str(row[1])
        for row in connection.execute(
            f"PRAGMA table_info({table_name})"
        ).fetchall()
    }


def _review_scope(
    *,
    property_id: int,
    connection: sqlite3.Connection,
    budget_year: Optional[int] = None,
    budget_draft_id: Optional[int] = None,
) -> tuple[Optional[int], Optional[int]]:
    resolved_budget_year = budget_year
    resolved_budget_draft_id = budget_draft_id
    if resolved_budget_year is None and _sqlite_table_exists(connection, "properties"):
        property_row = connection.execute(
            "SELECT portfolio_year FROM properties WHERE id = ?",
            (property_id,),
        ).fetchone()
        if property_row is not None and property_row[0] is not None:
            resolved_budget_year = int(property_row[0])
    if resolved_budget_draft_id is None and _sqlite_table_exists(connection, "budget_drafts"):
        draft_row = connection.execute(
            """
            SELECT id
              FROM budget_drafts
             WHERE property_id = ?
               AND status = 'active'
             ORDER BY updated_at DESC, id DESC
             LIMIT 1
            """,
            (property_id,),
        ).fetchone()
        if draft_row is not None:
            resolved_budget_draft_id = int(draft_row[0])
    return resolved_budget_year, resolved_budget_draft_id


def resolve_active_assessment_setup_id(
    connection: sqlite3.Connection,
    *,
    property_id: int,
) -> Optional[int]:
    """Resolve the setup shared by mapping, readiness, and finalization."""
    row = connection.execute(
        """
        SELECT s.id
          FROM assessment_setups AS s
          JOIN properties AS p ON p.default_assessment_setup_id = s.id
         WHERE p.id = ?
           AND s.property_id = p.id
           AND s.status IN ('approved', 'draft')
         LIMIT 1
        """,
        (property_id,),
    ).fetchone()
    if row is not None:
        return int(row[0])
    row = connection.execute(
        """
        SELECT id
          FROM assessment_setups
         WHERE property_id = ?
           AND status IN ('approved', 'draft')
         ORDER BY CASE status WHEN 'approved' THEN 0 ELSE 1 END, id DESC
         LIMIT 1
        """,
        (property_id,),
    ).fetchone()
    return int(row[0]) if row is not None else None


def _review_row_dispositions_by_line_key(
    *,
    property_id: int,
    assessment_setup_id: int,
    budget_year: Optional[int],
    budget_draft_id: Optional[int],
    connection: sqlite3.Connection,
) -> dict[str, dict[str, object]]:
    if not _sqlite_table_exists(connection, "assessment_review_row_dispositions"):
        return {}
    rows = connection.execute(
        """
        SELECT review_line_key, disposition_state, notes, decided_by, decided_at
          FROM assessment_review_row_dispositions
         WHERE property_id = ?
           AND assessment_setup_id = ?
           AND COALESCE(budget_year, -1) = COALESCE(?, -1)
           AND COALESCE(budget_draft_id, -1) = COALESCE(?, -1)
        """,
        (
            property_id,
            assessment_setup_id,
            budget_year,
            budget_draft_id,
        ),
    ).fetchall()
    return {
        str(row[0]): {
            "disposition_state": str(row[1] or "clear"),
            "notes": str(row[2] or ""),
            "decided_by": str(row[3] or ""),
            "decided_at": row[4],
        }
        for row in rows
    }


def _insert_review_row_audit_event(
    *,
    property_id: int,
    assessment_setup_id: int,
    budget_year: Optional[int],
    budget_draft_id: Optional[int],
    line_key: str,
    normalized_label: str,
    line_label: str,
    change_type: str,
    previous_value: Optional[str],
    new_value: Optional[str],
    pool_key: Optional[str],
    actor: str,
    reason: str,
    source: str,
    connection: sqlite3.Connection,
) -> None:
    if not _sqlite_table_exists(connection, "assessment_review_row_audit_events"):
        return
    connection.execute(
        """
        INSERT INTO assessment_review_row_audit_events (
            property_id, assessment_setup_id, budget_year, budget_draft_id,
            review_line_key, normalized_label, line_label, change_type,
            previous_value, new_value, pool_key, actor, reason, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            property_id,
            assessment_setup_id,
            budget_year,
            budget_draft_id,
            line_key,
            normalized_label,
            line_label,
            change_type,
            previous_value,
            new_value,
            pool_key,
            actor,
            reason,
            source,
        ),
    )


def _deactivate_mapping_for_line(
    *,
    property_id: int,
    assessment_setup_id: int,
    row: dict[str, object],
    connection: sqlite3.Connection,
) -> int:
    cur = connection.execute(
        """
        UPDATE budget_line_pool_mappings
           SET active = 0,
               review_state = 'stale'
         WHERE property_id = ?
           AND assessment_setup_id = ?
           AND budget_line_normalized_label = ?
           AND section = ?
           AND category = ?
           AND fund_type = ?
           AND COALESCE(account_code, '') = COALESCE(?, '')
           AND active = 1
        """,
        (
            property_id,
            assessment_setup_id,
            str(row["normalized_label"]),
            str(row["section"]),
            str(row["category"]),
            str(row["fund_type"]),
            row["account_code"],
        ),
    )
    return cur.rowcount


def _is_zero_or_blank_amount(value: object) -> bool:
    amount = _decimal_or_none(value)
    return amount is None or amount == 0


def _line_eligibility(line: dict) -> tuple[BudgetLineEligibility, str]:
    if line.get("active") is False:
        return "inactive", "inactive budget row"
    label = str(line.get("label") or line.get("normalized_label") or "")
    normalized = normalize_budget_label(label)
    if not normalized:
        return "unknown", "missing label"
    if _is_zero_or_blank_amount(line.get("amount")):
        return "zero_or_blank", "zero or blank amount"

    category = str(line.get("category") or "").lower()
    lowered = label.lower()
    if category in {"income", "reserve_income"}:
        if "interest" in lowered:
            return "interest_income", "interest income"
        if "late fee" in lowered or "late fees" in lowered:
            return "late_fee_income", "late fee income"
        if "assessment" in lowered or "dues" in lowered:
            return "assessment_revenue_tieout", "assessment revenue tie-out"
        return "assessment_revenue_tieout", "revenue tie-out"
    if "pass through" in lowered or "pass-through" in lowered:
        return "pass_through", "pass-through line"
    if "reimbursement" in lowered or "reimburs" in lowered:
        return "reimbursement", "reimbursement/offset line"
    if "outside scope" in lowered or "non-assessment" in lowered:
        return "outside_assessment_scope", "outside assessment scope"
    if category in {"operating", "reserve_expense", "expense", "reserve"}:
        return "assessable_expense", "assessable expense"
    return "unknown", "classification unknown"


def classify_budget_lines_for_mapping(
    budget_lines: list[dict],
) -> BudgetLineClassificationResult:
    """Classify current-year lines before unresolved mapping checks."""
    budget_lines = canonicalize_budget_lines_for_mapping(budget_lines)
    raw: list[tuple[dict, tuple[str, str, str, str, Optional[str]], Optional[Decimal]]] = []
    for line in budget_lines:
        raw.append((line, _line_key(line), _decimal_or_none(line.get("amount"))))

    by_duplicate_key: dict[tuple[str, str, str, str, Optional[str]], list[int]] = {}
    for idx, (_, key, _) in enumerate(raw):
        by_duplicate_key.setdefault(key, []).append(idx)

    duplicate_noncanonical: set[int] = set()
    duplicate_conflict_indexes: set[int] = set()
    conflicts: list[DuplicateBudgetLineConflict] = []
    for key, indexes in by_duplicate_key.items():
        if len(indexes) < 2 or not key[0]:
            continue
        amounts = [raw[idx][2] for idx in indexes]
        distinct_amounts = {amount for amount in amounts if amount is not None}
        if len(distinct_amounts) > 1:
            duplicate_conflict_indexes.update(indexes)
            conflicts.append(
                DuplicateBudgetLineConflict(
                    normalized_label=key[0],
                    line_labels=[
                        str(raw[idx][0].get("label") or raw[idx][0].get("normalized_label") or "")
                        for idx in indexes
                    ],
                    amounts=sorted(distinct_amounts),
                )
            )
        else:
            duplicate_noncanonical.update(indexes[1:])

    classifications: list[BudgetLineClassification] = []
    for idx, (line, key, amount) in enumerate(raw):
        label = str(line.get("label") or line.get("normalized_label") or "")
        if idx in duplicate_conflict_indexes:
            eligibility, reason, requires_mapping, canonical = (
                "unknown",
                "duplicate amount conflict",
                True,
                True,
            )
        elif idx in duplicate_noncanonical:
            eligibility, reason, requires_mapping, canonical = (
                "duplicate_raw_or_normalized",
                "duplicate of canonical row",
                False,
                False,
            )
        else:
            eligibility, reason = _line_eligibility(line)
            requires_mapping = eligibility in {"assessable_expense", "unknown"}
            canonical = True
        classifications.append(
            BudgetLineClassification(
                line_key=key,
                line_label=label,
                amount=amount,
                eligibility=eligibility,
                requires_mapping=requires_mapping,
                reason=reason,
                canonical=canonical,
            )
        )
    return BudgetLineClassificationResult(
        classifications=classifications,
        duplicate_conflicts=conflicts,
    )


def _pool_id_by_key(
    *,
    assessment_setup_id: int,
    connection: sqlite3.Connection,
) -> dict[str, int]:
    rows = connection.execute(
        """
        SELECT pool_key, id
          FROM allocation_pools
         WHERE assessment_setup_id = ?
        """,
        (assessment_setup_id,),
    ).fetchall()
    return {str(row[0]): int(row[1]) for row in rows}


def _pool_signature_by_key(
    *,
    assessment_setup_id: int,
    connection: sqlite3.Connection,
) -> dict[str, str]:
    rows = connection.execute(
        """
        SELECT pool_key, allocation_method, recipient_scope,
               denominator_source, budget_line_derivation
          FROM allocation_pools
         WHERE assessment_setup_id = ?
        """,
        (assessment_setup_id,),
    ).fetchall()
    return {
        str(row[0]): "|".join(str(part or "") for part in row[1:])
        for row in rows
    }


def _included_line_rules(
    extraction: DRESetupExtraction,
) -> Iterable[tuple[str, str, list[int], float, str]]:
    for pool in extraction.allocation_pools:
        for label in pool.included_budget_lines:
            normalized = normalize_budget_label(label)
            if not normalized:
                continue
            yield (
                pool.pool_key,
                label,
                pool.source_pages,
                pool.confidence,
                pool.budget_line_derivation,
            )


def _mapping_evidence_rules(
    extraction: DRESetupExtraction,
) -> Iterable[dict[str, object]]:
    for item in extraction.budget_line_mapping_evidence:
        normalized = normalize_budget_label(item.source_label)
        if not normalized or not item.assessment_pool_key:
            continue
        yield {
            "pool_key": item.assessment_pool_key,
            "match_label": item.source_label,
            "normalized_label": normalized,
            "account_code": item.account_code or None,
            "source_pages": [item.source_page] if item.source_page is not None else [],
            "confidence": item.match_confidence,
            "source_parent_category": item.parent_category or None,
            "assessment_type": item.assessment_type,
            "review_required": item.review_required,
            "review_reason": item.review_reason,
            "source_evidence_text": item.source_evidence_text,
        }


def _is_exemption_pool(pool_key: str, pool_name: str) -> bool:
    text = f"{pool_key} {pool_name}".lower()
    return "exempt" in text or "2792.16" in text


def is_remainder_eligible_budget_line(
    line: dict,
    *,
    already_mapped_normalized_labels: set[str],
) -> bool:
    """Return whether a current-year line may be swept into a residual pool."""
    if line.get("active") is False:
        return False
    label = str(line.get("label") or line.get("normalized_label") or "")
    normalized = normalize_budget_label(label)
    if normalized in already_mapped_normalized_labels:
        return False
    if classify_assessment_mapping_review_row_role(line) != _REGULAR_REVIEW_ROW_ROLE:
        return False
    amount = line.get("amount")
    if amount in (None, "", 0, 0.0):
        return False
    category = str(line.get("category") or "").lower()
    if category in {"income", "reserve_income"}:
        return False
    unsafe_label_markers = (
        "pass through",
        "pass-through",
        "reimbursement",
        "individually billed",
        "sub-metered",
        "third-party billed",
        "surcharge",
        "special assessment",
    )
    lowered_label = label.lower()
    return not any(marker in lowered_label for marker in unsafe_label_markers)


def derive_rules_from_dre_extraction(
    *,
    property_id: int,
    assessment_setup_id: int,
    source_dre_extraction_run_id: Optional[int],
    extraction: DRESetupExtraction,
    connection: sqlite3.Connection,
    commit: bool = True,
) -> int:
    """Persist reusable rules from DRE ``included_budget_lines``.

    Returns the number of newly inserted rows. Existing rules are left in
    place so the function can safely run during retries/backfills.
    """
    pool_ids = _pool_id_by_key(
        assessment_setup_id=assessment_setup_id,
        connection=connection,
    )
    inserted = 0
    for (
        pool_key,
        match_label,
        source_pages,
        confidence,
        derivation,
    ) in _included_line_rules(extraction):
        cur = connection.execute(
            """
            INSERT OR IGNORE INTO assessment_budget_mapping_rules (
                property_id, assessment_setup_id, pool_key, pool_id,
                match_label, normalized_label, match_type, rule_source,
                approval_status, review_state,
                source_dre_extraction_run_id, source_pages_json,
                confidence, budget_line_derivation
            ) VALUES (?, ?, ?, ?, ?, ?, 'exact_label',
                      'dre_included_budget_line', 'suggested',
                      'pending_review', ?, ?, ?, ?)
            """,
            (
                property_id,
                assessment_setup_id,
                pool_key,
                pool_ids.get(pool_key),
                match_label,
                normalize_budget_label(match_label),
                source_dre_extraction_run_id,
                json.dumps(source_pages),
                confidence,
                derivation,
            ),
        )
        inserted += cur.rowcount
    for evidence in _mapping_evidence_rules(extraction):
        cur = connection.execute(
            """
            INSERT OR IGNORE INTO assessment_budget_mapping_rules (
                property_id, assessment_setup_id, pool_key, pool_id,
                match_label, normalized_label, account_code, match_type,
                rule_source, approval_status, review_state,
                source_dre_extraction_run_id, source_pages_json,
                confidence, source_parent_category, assessment_type,
                review_required, review_reason, source_evidence_text,
                budget_line_derivation
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'normalized_label',
                      'dre_mapping_evidence', 'suggested',
                      'pending_review', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                property_id,
                assessment_setup_id,
                str(evidence["pool_key"]),
                pool_ids.get(str(evidence["pool_key"])),
                str(evidence["match_label"]),
                str(evidence["normalized_label"]),
                evidence["account_code"],
                source_dre_extraction_run_id,
                json.dumps(evidence["source_pages"]),
                float(evidence["confidence"] or 0.0),
                evidence["source_parent_category"],
                str(evidence["assessment_type"]),
                1 if bool(evidence["review_required"]) else 0,
                str(evidence["review_reason"] or ""),
                str(evidence["source_evidence_text"] or ""),
                "explicit_lines",
            ),
        )
        inserted += cur.rowcount
    for pool in extraction.allocation_pools:
        if pool.budget_line_derivation != "residual_default":
            continue
        cur = connection.execute(
            """
            INSERT OR IGNORE INTO assessment_budget_mapping_rules (
                property_id, assessment_setup_id, pool_key, pool_id,
                match_type, rule_source, approval_status, review_state,
                source_dre_extraction_run_id, source_pages_json,
                confidence, budget_line_derivation,
                residual_after_pool_keys_json, residual_exclusions_json
            ) VALUES (?, ?, ?, ?, 'remainder', 'system_remainder',
                      'suggested', 'pending_review', ?, ?, ?, ?,
                      ?, ?)
            """,
            (
                property_id,
                assessment_setup_id,
                pool.pool_key,
                pool_ids.get(pool.pool_key),
                source_dre_extraction_run_id,
                json.dumps(pool.source_pages),
                pool.confidence,
                pool.budget_line_derivation,
                json.dumps(pool.residual_after_pool_keys),
                json.dumps(pool.residual_exclusions),
            ),
        )
        inserted += cur.rowcount
    if commit:
        connection.commit()
    return inserted


def ensure_exemption_decisions_from_dre_extraction(
    *,
    property_id: int,
    assessment_setup_id: int,
    budget_year: Optional[int],
    budget_draft_id: Optional[int],
    extraction: DRESetupExtraction,
    connection: sqlite3.Connection,
    commit: bool = True,
) -> int:
    """Create pending current-year exemption decisions for exemption pools."""
    inserted = 0
    for pool in extraction.allocation_pools:
        if not _is_exemption_pool(pool.pool_key, pool.pool_name):
            continue
        cur = connection.execute(
            """
            INSERT OR IGNORE INTO assessment_exemption_decisions (
                property_id, assessment_setup_id, budget_year,
                budget_draft_id, pool_key, exemption_state,
                evidence_ref_json
            ) VALUES (?, ?, ?, ?, ?, 'pending_review', ?)
            """,
            (
                property_id,
                assessment_setup_id,
                budget_year,
                budget_draft_id,
                pool.pool_key,
                json.dumps({"source_pages": pool.source_pages}),
            ),
        )
        inserted += cur.rowcount
    if commit:
        connection.commit()
    return inserted


def set_exemption_decision_state(
    *,
    property_id: int,
    assessment_setup_id: int,
    budget_year: Optional[int],
    budget_draft_id: Optional[int],
    pool_key: str,
    exemption_state: str,
    decided_by: str,
    notes: str = "",
    connection: sqlite3.Connection,
    commit: bool = True,
) -> int:
    """Set the current-year exemption state for an existing decision row."""
    cur = connection.execute(
        """
        UPDATE assessment_exemption_decisions
           SET exemption_state = ?,
               decided_by = ?,
               decided_at = datetime('now'),
               notes = ?,
               updated_at = datetime('now')
         WHERE property_id = ?
           AND assessment_setup_id = ?
           AND pool_key = ?
           AND COALESCE(budget_year, -1) = COALESCE(?, -1)
           AND COALESCE(budget_draft_id, -1) = COALESCE(?, -1)
        """,
        (
            exemption_state,
            decided_by,
            notes,
            property_id,
            assessment_setup_id,
            pool_key,
            budget_year,
            budget_draft_id,
        ),
    )
    if commit:
        connection.commit()
    return cur.rowcount


def record_scoped_alias(
    *,
    property_id: int,
    assessment_setup_id: int,
    pool_key: str,
    dre_label: str,
    budget_label: str,
    account_code: Optional[str],
    actor: str,
    note: str,
    connection: sqlite3.Connection,
    commit: bool = True,
) -> int:
    """Persist one approved HOA/setup/pool-scoped alias."""
    normalized_dre_label = normalize_budget_label(dre_label)
    normalized_budget_label = normalize_budget_label(budget_label)
    cur = connection.execute(
        """
        INSERT INTO assessment_mapping_aliases (
            property_id, assessment_setup_id, pool_key,
            dre_label, normalized_dre_label,
            budget_label, normalized_budget_label,
            account_code, decided_by, note
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(property_id, assessment_setup_id, pool_key,
                    normalized_dre_label, normalized_budget_label,
                    COALESCE(account_code, ''))
        DO UPDATE SET approval_status = 'approved',
                      active = 1,
                      decided_by = excluded.decided_by,
                      decided_at = datetime('now'),
                      note = excluded.note,
                      updated_at = datetime('now')
        """,
        (
            property_id,
            assessment_setup_id,
            pool_key,
            dre_label,
            normalized_dre_label,
            budget_label,
            normalized_budget_label,
            account_code,
            actor,
            note,
        ),
    )
    if commit:
        connection.commit()
    return cur.lastrowid or 0


def _approved_aliases(
    *,
    property_id: int,
    assessment_setup_id: int,
    connection: sqlite3.Connection,
) -> list[sqlite3.Row]:
    if not _sqlite_table_exists(connection, "assessment_mapping_aliases"):
        return []
    return connection.execute(
        """
        SELECT id, pool_key, normalized_budget_label, account_code
          FROM assessment_mapping_aliases
         WHERE property_id = ?
           AND assessment_setup_id = ?
           AND active = 1
           AND approval_status = 'approved'
        """,
        (property_id, assessment_setup_id),
    ).fetchall()


def _active_rule_rows(
    *,
    property_id: int,
    assessment_setup_id: int,
    connection: sqlite3.Connection,
) -> list[sqlite3.Row]:
    if not _sqlite_table_exists(connection, "assessment_budget_mapping_rules"):
        return []
    return connection.execute(
        """
        SELECT id, pool_key, match_label, normalized_label, account_code,
               match_type, rule_source, approval_status, review_state,
               confidence, budget_line_derivation, source_pages_json,
               source_parent_category, assessment_type, review_required,
               review_reason, source_evidence_text
          FROM assessment_budget_mapping_rules
         WHERE property_id = ?
           AND assessment_setup_id = ?
           AND active = 1
           AND approval_status != 'disabled'
           AND review_state != 'disabled'
        """,
        (property_id, assessment_setup_id),
    ).fetchall()


def _pool_name_by_key(
    *,
    assessment_setup_id: int,
    connection: sqlite3.Connection,
) -> dict[str, str]:
    rows = connection.execute(
        """
        SELECT pool_key, pool_name
          FROM allocation_pools
         WHERE assessment_setup_id = ?
        """,
        (assessment_setup_id,),
    ).fetchall()
    return {str(row[0]): str(row[1] or row[0]) for row in rows}


def _json_list(value: object) -> list[int]:
    if not value:
        return []
    try:
        decoded = json.loads(str(value))
    except Exception:
        return []
    if not isinstance(decoded, list):
        return []
    out: list[int] = []
    for item in decoded:
        try:
            out.append(int(item))
        except Exception:
            continue
    return out


def _token_overlap_score(
    left: frozenset[str],
    right: frozenset[str],
) -> float:
    if not left or not right:
        return 0.0
    overlap = len(left & right)
    return overlap / max(len(left), len(right))


def _rank_line_review_candidates(
    *,
    line: dict,
    classification: BudgetLineClassification,
    rules: list[sqlite3.Row],
    pool_names: dict[str, str],
) -> list[LineReviewCandidate]:
    if classification.eligibility != "assessable_expense":
        return []
    if str(line.get("fund_type") or "") != "operating":
        return []

    normalized, _section, _category, _fund_type, account_code = _line_key(line)
    line_tokens = _semantic_label_tokens(
        str(line.get("label") or line.get("normalized_label") or "")
    )
    candidates: list[LineReviewCandidate] = []

    for rule in rules:
        budget_line_derivation = str(rule[10] or "unknown")
        if str(rule[5]) == "remainder" or budget_line_derivation == "residual_default":
            candidates.append(
                LineReviewCandidate(
                    rule_id=int(rule[0]),
                    pool_key=str(rule[1]),
                    pool_name=pool_names.get(str(rule[1]), str(rule[1])),
                    score=0.25,
                    match_reason="residual/base pool candidate",
                    decision_level="review_required_suggestion",
                    source_pages=_json_list(rule[11]),
                    source_evidence_text=str(rule[16] or ""),
                    review_reason=str(rule[15] or "Operator must confirm residual/base assignment per row."),
                    match_label=str(rule[2] or rule[3] or ""),
                    rule_source=str(rule[6]),
                    budget_line_derivation=budget_line_derivation,
                )
            )
            continue
        if str(rule[6]) not in {
            "dre_mapping_evidence",
            "dre_included_budget_line",
            "carried_forward",
        }:
            continue

        assessment_type = str(rule[13] or "unknown_needs_review")
        if assessment_type in _BLOCKED_REGULAR_MAPPING_ASSESSMENT_TYPES:
            continue

        match_label = str(rule[2] or rule[3] or "")
        rule_normalized = str(rule[3] or "")
        rule_account_code = (
            str(rule[4]) if rule[4] not in (None, "") else None
        )
        reasons: list[str] = []
        score = 0.0

        from app.allocation_resolution.semantic_mapping import classify_label_match

        match_kind = classify_label_match(
            match_label or rule_normalized,
            str(line.get("label") or line.get("normalized_label") or ""),
        )
        if account_code and rule_account_code and str(account_code) == rule_account_code:
            score = 1.0
            reasons.append("exact account code")
        else:
            if normalized and rule_normalized:
                if normalized == rule_normalized or match_kind == "exact":
                    score = max(score, 0.95)
                    reasons.append("exact normalized label")
                elif match_kind == "combined":
                    # Category is one named part of the budget description
                    # (gas ⊂ Electricity & Gas). Assign the whole source
                    # amount to that pool unless the operator starts a split.
                    score = max(score, 0.70)
                    reasons.append("combined_line_whole_assignment")
                else:
                    overlap = _token_overlap_score(
                        line_tokens,
                        _semantic_label_tokens(match_label or rule_normalized),
                    )
                    if overlap > 0:
                        score = max(score, 0.5 + overlap * 0.35)
                        reasons.append("semantic label overlap")
                    if match_kind != "combined" and (
                        rule_normalized in normalized or normalized in rule_normalized
                    ):
                        score = max(score, 0.8)
                        reasons.append("label contains DRE term")

        parent_category = normalize_budget_label(str(rule[12] or ""))
        if parent_category:
            parent_tokens = _semantic_label_tokens(parent_category)
            if line_tokens & parent_tokens:
                score = min(1.0, score + 0.1)
                reasons.append("parent category aligns")

        review_required = (
            bool(rule[14])
            or assessment_type == "unknown_needs_review"
            or match_kind == "combined"
        )
        threshold = 0.3 if review_required else 0.55
        if score < threshold:
            continue

        candidates.append(
            LineReviewCandidate(
                rule_id=int(rule[0]),
                pool_key=str(rule[1]),
                pool_name=pool_names.get(str(rule[1]), str(rule[1])),
                score=round(score, 3),
                match_reason=", ".join(dict.fromkeys(reasons)) or "DRE mapping evidence",
                decision_level=(
                    "review_required_suggestion"
                    if review_required
                    else "safe_suggestion"
                ),
                source_pages=_json_list(rule[11]),
                source_evidence_text=str(rule[16] or ""),
                review_reason=str(rule[15] or ""),
                match_label=match_label,
                rule_source=str(rule[6]),
                budget_line_derivation=budget_line_derivation,
            )
        )

    candidates.sort(
        key=lambda item: (
            -item.score,
            _RULE_SOURCE_RANK.get(item.rule_source, 99),
            item.pool_key,
            item.rule_id,
        )
    )
    return candidates


def build_assessment_mapping_review_rows(
    *,
    property_id: int,
    assessment_setup_id: int,
    budget_lines: list[dict],
    connection: sqlite3.Connection,
    budget_year: Optional[int] = None,
    budget_draft_id: Optional[int] = None,
) -> list[dict[str, object]]:
    budget_lines = _with_assessment_mapping_amounts(budget_lines)
    budget_year, budget_draft_id = _review_scope(
        property_id=property_id,
        connection=connection,
        budget_year=budget_year,
        budget_draft_id=budget_draft_id,
    )
    classification_result = classify_budget_lines_for_mapping(budget_lines)
    rules = _active_rule_rows(
        property_id=property_id,
        assessment_setup_id=assessment_setup_id,
        connection=connection,
    )
    pool_names = _pool_name_by_key(
        assessment_setup_id=assessment_setup_id,
        connection=connection,
    )
    pool_rows = connection.execute(
        """
        SELECT pool_key, pool_name
          FROM allocation_pools
         WHERE assessment_setup_id = ?
         ORDER BY display_order, id
        """,
        (assessment_setup_id,),
    ).fetchall()
    mapping_columns = _table_columns(connection, "budget_line_pool_mappings")
    mapping_source_sql = "mapping_source" if "mapping_source" in mapping_columns else "'operator'"
    review_state_sql = "review_state" if "review_state" in mapping_columns else "'ready'"
    mapped_rows = connection.execute(
        f"""
        SELECT budget_line_normalized_label, section, category, fund_type,
               account_code, pool_key, {mapping_source_sql}, {review_state_sql}
          FROM budget_line_pool_mappings
         WHERE property_id = ?
           AND assessment_setup_id = ?
           AND active = 1
        """,
        (property_id, assessment_setup_id),
    ).fetchall()
    mapped_by_key = {
        (str(row[0]), str(row[1]), str(row[2]), str(row[3]), row[4]): row
        for row in mapped_rows
    }
    dispositions_by_key = _review_row_dispositions_by_line_key(
        property_id=property_id,
        assessment_setup_id=assessment_setup_id,
        budget_year=budget_year,
        budget_draft_id=budget_draft_id,
        connection=connection,
    )
    valid_pool_options = [
        {"pool_key": str(row[0]), "pool_name": str(row[1])}
        for row in pool_rows
    ]
    valid_pool_keys = {str(row[0]) for row in pool_rows}

    rows: list[dict[str, object]] = []
    for idx, line in enumerate(budget_lines):
        classification = classification_result.classifications[idx]
        if not classification.canonical:
            continue

        row_role = classify_assessment_mapping_review_row_role(line)
        if row_role not in _REVIEWABLE_ROW_ROLES:
            continue

        amount, source_column_used = select_assessment_mapping_amount(line)
        line_key = build_assessment_mapping_review_line_key(
            line,
            row_role=row_role,
            source_column_used=source_column_used,
        )
        normalized, section, category, fund_type, account_code = _line_key(line)
        key = (normalized, section, category, fund_type, account_code)
        mapped = _lookup_mapping_row(mapped_by_key, key=key)
        # Distinguish never-touched (missing disposition row) from an
        # operator Clear that UPSERTed disposition_state="clear". Without
        # that distinction, Clear on default reserve detail is a no-op
        # (July 2026 client dogfood).
        disposition = _lookup_disposition_for_line(
            dispositions_by_key,
            line_key=line_key,
            normalized_label=normalized,
            row_role=row_role,
            account_code=account_code,
        )
        has_explicit_disposition = disposition is not None
        disposition = disposition or {}
        disposition_state = str(disposition.get("disposition_state") or "clear")
        # Contribution/transfer lines are schedule-basis; "reserve_detail" is
        # never a valid end state for them (maps dues into reserves, not spend).
        if (
            row_role == _RESERVE_CONTRIBUTION_REVIEW_ROW_ROLE
            and disposition_state == "reserve_detail"
        ):
            disposition_state = "clear"
        # Schedule-basis: operating + reserve contribution when clear, OR a
        # reserve component/cashflow line the operator cleared (opt-in to map).
        included_in_regular_basis = disposition_state == "clear" and (
            row_role in _SCHEDULE_BASIS_ROW_ROLES
            or (
                row_role in _RESERVE_REVIEW_ROW_ROLES
                and has_explicit_disposition
            )
        )
        current_pool_key = str(mapped[5]) if mapped else None
        # H1: the line's active mapping points at a pool_key that no longer
        # exists in the current setup (e.g. setup supersession removed the
        # pool but left the mapping active). Without this, the row reads as
        # "mapped" and its dollars silently vanish in the engine. Flag it so
        # the operator remaps to a current pool (valid_pool_options) or
        # excludes the line — the same in-place action unmapped rows already
        # offer.
        stale_pool_mapping = bool(
            current_pool_key and current_pool_key not in valid_pool_keys
        )
        row_pool_options = _pool_options_for_row_role(row_role, valid_pool_options)
        preferred_reserve_key = _preferred_reserve_pool_key(row_pool_options)
        candidates: list[LineReviewCandidate] = []
        status = "mapped" if mapped else "needs_disposition"
        if (
            not mapped
            and disposition_state == "clear"
            and row_role in _RESERVE_REVIEW_ROW_ROLES
            and not has_explicit_disposition
        ):
            # Component/cashflow reserve lines default to "Reserve Detail" so
            # the operator isn't forced to click through every spend line.
            # Contribution/transfer lines are schedule-basis (above) and do
            # NOT take this default — they must be assigned to a pool.
            # Only when never dispositioned — operator Clear writes an
            # explicit clear row and opts the line into schedule mapping.
            status = "reserve_detail"
        if disposition_state == "pending_split":
            status = "pending_split"
        elif disposition_state == "excluded_non_regular":
            status = "excluded_non_regular"
        elif disposition_state == "reserve_detail":
            status = "reserve_detail"
        elif stale_pool_mapping and included_in_regular_basis:
            candidates = _rank_line_review_candidates(
                line=line,
                classification=classification,
                rules=rules,
                pool_names=pool_names,
            )
            status = "stale_pool_mapping"
        elif included_in_regular_basis and not mapped:
            candidates = _rank_line_review_candidates(
                line=line,
                classification=classification,
                rules=rules,
                pool_names=pool_names,
            )
            if (
                row_role == _RESERVE_CONTRIBUTION_REVIEW_ROW_ROLE
                and preferred_reserve_key
                and not candidates
            ):
                status = "suggested"
            else:
                status = "suggested" if candidates else "unresolved"

        recommended_pool_key: Optional[str] = None
        if candidates:
            recommended_pool_key = candidates[0].pool_key
        elif (
            row_role == _RESERVE_CONTRIBUTION_REVIEW_ROW_ROLE
            and preferred_reserve_key
            and included_in_regular_basis
            and not mapped
        ):
            recommended_pool_key = preferred_reserve_key

        combined_categories = _combined_line_categories(
            connection=connection,
            assessment_setup_id=assessment_setup_id,
            line_label=classification.line_label,
        ) if included_in_regular_basis else []
        saved_slices = _saved_slices_for_line(
            connection=connection,
            assessment_setup_id=assessment_setup_id,
            normalized_label=normalized,
            account_code=account_code,
            source_line_key=build_budget_line_slice_key(
                normalized_label=normalized,
                section=str(line.get("section") or ""),
                category=str(line.get("category") or ""),
                fund_type=str(line.get("fund_type") or ""),
                account_code=account_code,
            ),
        )
        split_balanced = bool(saved_slices)
        split_balance: Optional[Decimal] = None
        split_approved = False
        if split_balanced:
            from app.allocation_resolution.service import validate_slice_sum

            source_amount = Decimal(str(amount or 0))
            split_balance = validate_slice_sum(
                source_amount,
                [
                    Decimal(str(item.get("slice_annual_amount") or 0))
                    for item in saved_slices
                ],
            )
            split_balanced = (
                all(
                    Decimal(str(item.get("source_annual_amount") or 0))
                    == source_amount
                    for item in saved_slices
                )
                and split_balance == Decimal("0")
            )
            split_approved = split_balanced and all(
                str(item.get("status") or "") == "approved"
                for item in saved_slices
            )
        operator_started_split = bool(saved_slices) or disposition_state == "pending_split"
        allocation_mode = "split_required" if operator_started_split else "whole_line"
        split_status = (
            "approved" if split_approved
            else "draft" if split_balanced
            else "required" if operator_started_split
            else "not_applicable"
        )
        if operator_started_split and included_in_regular_basis:
            status = "split_saved" if split_balanced else "split_required"

        rows.append(
            {
                "line_key": line_key,
                "line_label": classification.line_label,
                "normalized_label": normalized,
                "section": section,
                "category": category,
                "fund_type": fund_type,
                "account_code": account_code,
                "assessment_mapping_amount": float(amount) if amount is not None else None,
                "source_column_used": source_column_used,
                "amount": float(amount) if amount is not None else None,
                "row_role": row_role,
                "eligibility": classification.eligibility,
                "included_in_regular_basis": included_in_regular_basis,
                "reason": _REVIEW_ROW_ROLE_REASONS.get(row_role, classification.reason),
                "status": status,
                "current_status": status,
                "disposition_state": disposition_state,
                "disposition_note": str(disposition.get("notes") or ""),
                "has_explicit_disposition": has_explicit_disposition,
                "pool_key": current_pool_key,
                "current_pool_key": current_pool_key,
                "stale_pool_mapping": stale_pool_mapping,
                "mapping_source": str(mapped[6] or "") if mapped else None,
                "review_state": str(mapped[7] or "") if mapped else None,
                "valid_pool_options": row_pool_options,
                "recommended_pool_key": recommended_pool_key,
                "allocation_mode": allocation_mode,
                "split_status": split_status,
                "split_saved": split_balanced,
                "split_approved": split_approved,
                "split_balance": (
                    float(split_balance) if split_balance is not None else None
                ),
                "split_balance_status": (
                    "balanced" if split_balanced
                    else "unbalanced" if saved_slices
                    else "not_applicable"
                ),
                "source_annual_amount": float(amount) if amount is not None else None,
                "combined_categories": combined_categories,
                "saved_slices": saved_slices,
                "candidates": [
                    {
                        "rule_id": candidate.rule_id,
                        "pool_key": candidate.pool_key,
                        "pool_name": candidate.pool_name,
                        "score": candidate.score,
                        "match_reason": candidate.match_reason,
                        "decision_level": candidate.decision_level,
                        "source_pages": candidate.source_pages,
                        "source_evidence_text": candidate.source_evidence_text,
                        "review_reason": candidate.review_reason,
                        "match_label": candidate.match_label,
                        "rule_source": candidate.rule_source,
                        "budget_line_derivation": candidate.budget_line_derivation,
                    }
                    for candidate in candidates
                ],
            }
        )
    return rows


def build_line_review_items(
    *,
    property_id: int,
    assessment_setup_id: int,
    budget_lines: list[dict],
    connection: sqlite3.Connection,
) -> list[dict[str, object]]:
    rows = build_assessment_mapping_review_rows(
        property_id=property_id,
        assessment_setup_id=assessment_setup_id,
        budget_lines=budget_lines,
        connection=connection,
    )
    return [
        {
            "line_key": row["line_key"],
            "line_label": row["line_label"],
            "normalized_label": row["normalized_label"],
            "section": row["section"],
            "category": row["category"],
            "fund_type": row["fund_type"],
            "account_code": row["account_code"],
            "amount": row["assessment_mapping_amount"],
            "assessment_mapping_amount": row["assessment_mapping_amount"],
            "source_column_used": row["source_column_used"],
            "row_role": row["row_role"],
            "eligibility": row["eligibility"],
            "included_in_regular_basis": row["included_in_regular_basis"],
            "reason": row["reason"],
            "status": row["status"],
            "pool_key": row["pool_key"],
            "candidates": row["candidates"],
        }
        for row in rows
        if row["row_role"] == _REGULAR_REVIEW_ROW_ROLE
    ]


def build_assessment_mapping_review_summary(
    review_rows: list[dict[str, object]],
    *,
    tolerance: object = "0.01",
) -> dict[str, object]:
    allowed_delta = Decimal(str(tolerance or "0.01"))
    mapped_regular_total = Decimal("0")
    pending_split_total = Decimal("0")
    excluded_non_regular_total = Decimal("0")
    target_regular_assessment_basis = Decimal("0")
    unresolved_required_rows: list[str] = []

    for row in review_rows:
        amount = Decimal(str(row.get("assessment_mapping_amount") or 0))
        disposition_state = str(row.get("disposition_state") or "clear")
        included_in_regular_basis = bool(row.get("included_in_regular_basis"))
        current_pool_key = row.get("current_pool_key")
        # H1: a stale-mapped row has a current_pool_key, but it points at a
        # pool that no longer exists — so it must NOT count toward the mapped
        # total; treat it as unresolved so reconciliation blocks until the
        # operator remaps or excludes it.
        stale_pool_mapping = bool(row.get("stale_pool_mapping"))
        split_required = row.get("allocation_mode") == "split_required"
        split_approved = row.get("split_status") == "approved"

        if included_in_regular_basis:
            target_regular_assessment_basis += amount
            if split_required and split_approved:
                mapped_regular_total += amount
            elif current_pool_key and not stale_pool_mapping and not split_required:
                mapped_regular_total += amount
            else:
                unresolved_required_rows.append(str(row.get("line_label") or ""))
                if split_required:
                    pending_split_total += amount
        elif disposition_state == "pending_split":
            pending_split_total += amount
        else:
            excluded_non_regular_total += amount

    difference = target_regular_assessment_basis - mapped_regular_total
    reconciliation_failures: list[str] = []
    if abs(difference) > allowed_delta:
        reconciliation_failures.append("mapped_pool_total_mismatch")
    final_render_blocked = bool(
        unresolved_required_rows or pending_split_total or reconciliation_failures
    )
    return {
        "mapped_regular_total": float(mapped_regular_total),
        "pending_split_total": float(pending_split_total),
        "excluded_non_regular_total": float(excluded_non_regular_total),
        "target_regular_assessment_basis": float(target_regular_assessment_basis),
        "difference": float(difference),
        "reconciliation_failures": reconciliation_failures,
        "unresolved_required_rows": unresolved_required_rows,
        "final_render_blocked": final_render_blocked,
    }


def build_assessment_mapping_review_blockers(
    *,
    property_id: int,
    assessment_setup_id: int,
    review_rows: list[dict[str, object]],
    connection: sqlite3.Connection,
) -> dict[str, list[str]]:
    blockers: dict[str, list[str]] = {}
    unresolved_regular_rows = [
        str(row["line_label"])
        for row in review_rows
        if bool(row["included_in_regular_basis"])
        and (
            (
                not row.get("current_pool_key")
                and not (
                    row.get("allocation_mode") == "split_required"
                    and row.get("split_status") == "approved"
                )
            )
            or (
                row.get("allocation_mode") == "split_required"
                and row.get("split_status") != "approved"
            )
        )
    ]
    if unresolved_regular_rows:
        blockers["unresolved_eligible_lines"] = unresolved_regular_rows

    # H1: lines whose active mapping targets a category that no longer exists.
    # Name the line and the stale category so the operator knows exactly what to
    # remap (or exclude) on the review screen — not a generic "review needed".
    stale_mapping_rows = [
        f"{row.get('line_label')} (mapped to removed assessment category "
        f"'{row.get('current_pool_key')}' — remap to a current category or exclude)"
        for row in review_rows
        if row.get("stale_pool_mapping")
    ]
    if stale_mapping_rows:
        blockers["stale_pool_mapping"] = stale_mapping_rows

    pending_split_rows = [
        str(row["line_label"])
        for row in review_rows
        if (
            str(row.get("disposition_state") or "") == "pending_split"
            or (
                row.get("allocation_mode") == "split_required"
                and row.get("split_status") != "approved"
            )
        )
    ]
    if pending_split_rows:
        blockers["pending_split"] = pending_split_rows

    return blockers


def set_assessment_review_row_disposition(
    *,
    property_id: int,
    assessment_setup_id: int,
    budget_year: Optional[int],
    budget_draft_id: Optional[int],
    row: dict[str, object],
    disposition_state: str,
    actor: str,
    note: str,
    connection: sqlite3.Connection,
    commit: bool = True,
) -> dict[str, object]:
    # Reserve contribution/transfer funds dues into reserves — never "reserve detail".
    if (
        str(row.get("row_role") or "") == _RESERVE_CONTRIBUTION_REVIEW_ROW_ROLE
        and disposition_state == "reserve_detail"
    ):
        raise ValueError(
            "Reserve contribution/transfer lines cannot be marked reserve detail; "
            "assign them to the reserve contributions category so the PDF reserve total matches."
        )
    previous_rows = _review_row_dispositions_by_line_key(
        property_id=property_id,
        assessment_setup_id=assessment_setup_id,
        budget_year=budget_year,
        budget_draft_id=budget_draft_id,
        connection=connection,
    )
    previous_state = str(
        previous_rows.get(str(row["line_key"]), {}).get("disposition_state") or "clear"
    )
    # Prefer prior state from relaxed identity match when exact key drifted.
    if previous_state == "clear" and str(row["line_key"]) not in previous_rows:
        prior = _lookup_disposition_for_line(
            previous_rows,
            line_key=str(row["line_key"]),
            normalized_label=str(row.get("normalized_label") or ""),
            row_role=str(row.get("row_role") or ""),
            account_code=(
                str(row["account_code"])
                if row.get("account_code") not in (None, "")
                else None
            ),
        )
        if prior:
            previous_state = str(prior.get("disposition_state") or "clear")
    connection.execute(
        """
        INSERT INTO assessment_review_row_dispositions (
            property_id, assessment_setup_id, budget_year, budget_draft_id,
            review_line_key, normalized_label, line_label, row_role,
            disposition_state, decided_by, decided_at, notes, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?, datetime('now'))
        ON CONFLICT(property_id, assessment_setup_id, review_line_key,
                    COALESCE(budget_year, -1), COALESCE(budget_draft_id, -1))
        DO UPDATE SET disposition_state = excluded.disposition_state,
                      decided_by = excluded.decided_by,
                      decided_at = excluded.decided_at,
                      notes = excluded.notes,
                      updated_at = excluded.updated_at
        """,
        (
            property_id,
            assessment_setup_id,
            budget_year,
            budget_draft_id,
            str(row["line_key"]),
            str(row["normalized_label"]),
            str(row["line_label"]),
            str(row["row_role"]),
            disposition_state,
            actor,
            note,
        ),
    )
    if disposition_state != "clear":
        _deactivate_mapping_for_line(
            property_id=property_id,
            assessment_setup_id=assessment_setup_id,
            row=row,
            connection=connection,
        )
    _insert_review_row_audit_event(
        property_id=property_id,
        assessment_setup_id=assessment_setup_id,
        budget_year=budget_year,
        budget_draft_id=budget_draft_id,
        line_key=str(row["line_key"]),
        normalized_label=str(row["normalized_label"]),
        line_label=str(row["line_label"]),
        change_type="disposition",
        previous_value=previous_state,
        new_value=disposition_state,
        pool_key=None,
        actor=actor,
        reason=note,
        source="operator",
        connection=connection,
    )
    if commit:
        connection.commit()
    return {
        "line_key": row["line_key"],
        "disposition_state": disposition_state,
        "previous_disposition_state": previous_state,
    }


def assign_assessment_review_row_pool(
    *,
    property_id: int,
    assessment_setup_id: int,
    budget_year: Optional[int],
    budget_draft_id: Optional[int],
    row: dict[str, object],
    pool_key: str,
    actor: str,
    note: str,
    connection: sqlite3.Connection,
    commit: bool = True,
) -> dict[str, object]:
    if row.get("allocation_mode") == "split_required":
        raise ValueError(
            "This budget line contains multiple assessment categories; save and approve a split instead."
        )
    valid_pool_keys = {
        str(option.get("pool_key") or "")
        for option in row.get("valid_pool_options", [])
        if isinstance(option, dict)
    }
    if pool_key not in valid_pool_keys:
        raise ValueError(
            f"Assessment category {pool_key!r} is not available for this setup."
        )
    existing = connection.execute(
        """
        SELECT pool_key
          FROM budget_line_pool_mappings
         WHERE property_id = ?
           AND assessment_setup_id = ?
           AND budget_line_normalized_label = ?
           AND section = ?
           AND category = ?
           AND fund_type = ?
           AND COALESCE(account_code, '') = COALESCE(?, '')
           AND active = 1
        """,
        (
            property_id,
            assessment_setup_id,
            str(row["normalized_label"]),
            str(row["section"]),
            str(row["category"]),
            str(row["fund_type"]),
            row["account_code"],
        ),
    ).fetchone()
    previous_pool_key = str(existing[0]) if existing is not None else None
    if _sqlite_table_exists(connection, "budget_line_allocation_slices"):
        connection.execute(
            """
            UPDATE budget_line_allocation_slices
               SET status = 'superseded'
             WHERE assessment_setup_id = ?
               AND source_line_normalized_label = ?
               AND COALESCE(source_line_account_code, '') = COALESCE(?, '')
               AND status IN ('draft', 'approved')
            """,
            (
                assessment_setup_id,
                str(row["normalized_label"]),
                row["account_code"],
            ),
        )
    connection.execute(
        """
        INSERT INTO budget_line_pool_mappings (
            property_id, assessment_setup_id,
            budget_line_normalized_label, section, category, fund_type,
            account_code, pool_key, source_rule_id, mapping_source,
            match_method, approval_status, review_state, budget_line_amount,
            approved_by, approved_at, active
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 'operator',
                  'direct_assignment', 'approved', 'ready', ?, ?, datetime('now'), 1)
        ON CONFLICT(property_id, assessment_setup_id,
                    budget_line_normalized_label, section, category,
                    fund_type, COALESCE(account_code, ''))
        DO UPDATE SET pool_key = excluded.pool_key,
                      mapping_source = excluded.mapping_source,
                      match_method = excluded.match_method,
                      approval_status = excluded.approval_status,
                      review_state = excluded.review_state,
                      budget_line_amount = excluded.budget_line_amount,
                      approved_by = excluded.approved_by,
                      approved_at = excluded.approved_at,
                      active = 1
        """,
        (
            property_id,
            assessment_setup_id,
            str(row["normalized_label"]),
            str(row["section"]),
            str(row["category"]),
            str(row["fund_type"]),
            row["account_code"],
            pool_key,
            row["assessment_mapping_amount"],
            actor,
        ),
    )
    connection.execute(
        """
        INSERT INTO assessment_budget_mapping_rules (
            property_id, assessment_setup_id, pool_key, match_label,
            normalized_label, account_code, match_type, rule_source,
            approval_status, review_state, confidence, budget_line_derivation,
            assessment_type, review_required, review_reason,
            source_evidence_text
        ) VALUES (?, ?, ?, ?, ?, ?, 'normalized_label', 'operator',
                  'approved', 'ready', 1.0, 'explicit_lines',
                  'unknown_needs_review', 0, '', ?)
        ON CONFLICT(property_id, assessment_setup_id, pool_key, match_type,
                    COALESCE(normalized_label, ''), COALESCE(account_code, ''))
        DO UPDATE SET match_label = excluded.match_label,
                      approval_status = 'approved',
                      review_state = 'ready',
                      active = 1,
                      confidence = excluded.confidence,
                      budget_line_derivation = 'explicit_lines',
                      source_evidence_text = excluded.source_evidence_text,
                      updated_at = datetime('now')
        """,
        (
            property_id,
            assessment_setup_id,
            pool_key,
            str(row["line_label"]),
            str(row["normalized_label"]),
            row["account_code"],
            f"Operator assigned {row['line_label']} to {pool_key}.",
        ),
    )
    if _sqlite_table_exists(connection, "assessment_review_row_dispositions"):
        connection.execute(
            """
            UPDATE assessment_review_row_dispositions
               SET disposition_state = 'clear',
                   decided_by = ?,
                   decided_at = datetime('now'),
                   notes = ?,
                   updated_at = datetime('now')
             WHERE property_id = ?
               AND assessment_setup_id = ?
               AND review_line_key = ?
               AND COALESCE(budget_year, -1) = COALESCE(?, -1)
               AND COALESCE(budget_draft_id, -1) = COALESCE(?, -1)
            """,
            (
                actor,
                "Assignment clears prior disposition." if note else "",
                property_id,
                assessment_setup_id,
                str(row["line_key"]),
                budget_year,
                budget_draft_id,
            ),
        )
    _insert_review_row_audit_event(
        property_id=property_id,
        assessment_setup_id=assessment_setup_id,
        budget_year=budget_year,
        budget_draft_id=budget_draft_id,
        line_key=str(row["line_key"]),
        normalized_label=str(row["normalized_label"]),
        line_label=str(row["line_label"]),
        change_type="assignment",
        previous_value=previous_pool_key,
        new_value=pool_key,
        pool_key=pool_key,
        actor=actor,
        reason=note,
        source="operator",
        connection=connection,
    )
    if commit:
        connection.commit()
    return {
        "line_key": row["line_key"],
        "pool_key": pool_key,
        "previous_pool_key": previous_pool_key,
        "current_status": "mapped",
    }


def carry_forward_reusable_mapping_rules_across_setups(
    *,
    property_id: int,
    old_setup_id: int,
    new_setup_id: int,
    connection: sqlite3.Connection,
    commit: bool = True,
) -> int:
    """Carry reusable rules to a new setup, downgrading incompatible pools."""
    old_signatures = _pool_signature_by_key(
        assessment_setup_id=old_setup_id,
        connection=connection,
    )
    new_signatures = _pool_signature_by_key(
        assessment_setup_id=new_setup_id,
        connection=connection,
    )
    new_pool_ids = _pool_id_by_key(
        assessment_setup_id=new_setup_id,
        connection=connection,
    )
    rules = connection.execute(
        """
        SELECT pool_key, match_label, normalized_label, account_code,
               match_type, approval_status, review_state, source_pages_json,
               confidence, budget_line_derivation,
               residual_after_pool_keys_json, residual_exclusions_json,
               source_parent_category, assessment_type, review_required,
               review_reason, source_evidence_text
          FROM assessment_budget_mapping_rules
         WHERE property_id = ?
           AND assessment_setup_id = ?
           AND active = 1
        """,
        (property_id, old_setup_id),
    ).fetchall()
    inserted = 0
    for rule in rules:
        pool_key = str(rule[0])
        compatible = (
            old_signatures.get(pool_key)
            and old_signatures.get(pool_key) == new_signatures.get(pool_key)
        )
        approval_status = str(rule[5]) if compatible else "suggested"
        review_state = str(rule[6]) if compatible else "pending_review"
        cur = connection.execute(
            """
            INSERT OR IGNORE INTO assessment_budget_mapping_rules (
                property_id, assessment_setup_id, pool_key, pool_id,
                match_label, normalized_label, account_code, match_type,
                rule_source, approval_status, review_state,
                source_pages_json, confidence, budget_line_derivation,
                residual_after_pool_keys_json, residual_exclusions_json,
                structural_signature, source_parent_category,
                assessment_type, review_required, review_reason,
                source_evidence_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'carried_forward',
                      ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                property_id,
                new_setup_id,
                pool_key,
                new_pool_ids.get(pool_key),
                rule[1],
                rule[2],
                rule[3],
                rule[4],
                approval_status,
                review_state,
                rule[7],
                rule[8],
                rule[9],
                rule[10],
                rule[11],
                new_signatures.get(pool_key),
                rule[12],
                rule[13],
                rule[14],
                rule[15],
                rule[16],
            ),
        )
        inserted += cur.rowcount
    if commit:
        connection.commit()
    return inserted


def backfill_rules_for_promoted_extraction_run(
    *,
    extraction_run_id: int,
    connection: sqlite3.Connection,
    commit: bool = True,
) -> int:
    """Create reusable rules for an already-promoted run's stored parsed JSON."""
    row = connection.execute(
        """
        SELECT property_id, promoted_setup_id, parsed_json
          FROM dre_extraction_runs
         WHERE id = ?
           AND review_status = 'promoted'
           AND promoted_setup_id IS NOT NULL
        """,
        (extraction_run_id,),
    ).fetchone()
    if row is None or not row[2]:
        return 0
    extraction = DRESetupExtraction.model_validate_json(row[2])
    return derive_rules_from_dre_extraction(
        property_id=int(row[0]),
        assessment_setup_id=int(row[1]),
        source_dre_extraction_run_id=extraction_run_id,
        extraction=extraction,
        connection=connection,
        commit=commit,
    )


def _line_key(line: dict) -> tuple[str, str, str, str, Optional[str]]:
    normalized = normalize_budget_label(
        str(line.get("normalized_label") or line.get("label") or "")
    )
    account_code = line.get("account_code")
    return (
        normalized,
        str(line.get("section") or ""),
        str(line.get("category") or ""),
        str(line.get("fund_type") or ""),
        str(account_code) if account_code not in (None, "") else None,
    )


def _lookup_mapping_row(
    mapped_by_key: dict[tuple[str, str, str, str, Optional[str]], object],
    *,
    key: tuple[str, str, str, str, Optional[str]],
) -> Optional[object]:
    """Exact mapping key, then fallback by account_code / normalized label.

    Budget lines often lose section/fund_type on re-parse while saved mappings
    still have section='operating' fund_type='operating', so exact 5-tuple
    match fails and a correctly mapped Reserve Transfer looks unmapped.
    """
    hit = mapped_by_key.get(key)
    if hit is not None:
        return hit
    normalized, _section, category, _fund, account_code = key
    if account_code not in (None, ""):
        for mkey, row in mapped_by_key.items():
            if mkey[4] == account_code and mkey[0] == normalized:
                return row
            if mkey[4] == account_code and (
                not category or mkey[2] == category or not mkey[2]
            ):
                return row
    for mkey, row in mapped_by_key.items():
        if mkey[0] == normalized:
            return row
    return None


def _lookup_disposition_for_line(
    dispositions_by_key: dict[str, dict[str, object]],
    *,
    line_key: str,
    normalized_label: str,
    row_role: str,
    account_code: Optional[str],
) -> Optional[dict[str, object]]:
    """Exact review_line_key, then role+account / role+label fallback.

    Disposition rows are keyed by a full line_key that embeds section/fund_type.
    When those fields change on re-upload, Clear/Reserve detail state is lost
    or mis-applied unless we fall back to stable identity.
    """
    hit = dispositions_by_key.get(line_key)
    if hit is not None:
        return hit
    acct = str(account_code) if account_code not in (None, "") else ""
    for stored_key, payload in dispositions_by_key.items():
        parts = str(stored_key).split("|")
        if len(parts) < 6:
            continue
        stored_norm, _s, _c, _f, stored_acct, stored_role = parts[:6]
        if stored_role != row_role:
            continue
        if acct and stored_acct == acct:
            return payload
        if stored_norm == normalized_label:
            return payload
    return None


def _existing_mapping_keys(
    *,
    property_id: int,
    assessment_setup_id: int,
    connection: sqlite3.Connection,
) -> set[tuple[str, str, str, str, Optional[str]]]:
    rows = connection.execute(
        """
        SELECT budget_line_normalized_label, section, category, fund_type,
               account_code
          FROM budget_line_pool_mappings
         WHERE property_id = ?
           AND assessment_setup_id = ?
           AND active = 1
        """,
        (property_id, assessment_setup_id),
    ).fetchall()
    return {
        (str(row[0]), str(row[1]), str(row[2]), str(row[3]), row[4])
        for row in rows
    }


def _mark_stale_auto_mappings(
    *,
    property_id: int,
    assessment_setup_id: int,
    current_keys: set[tuple[str, str, str, str, Optional[str]]],
    connection: sqlite3.Connection,
) -> int:
    rows = connection.execute(
        """
        SELECT id, budget_line_normalized_label, section, category, fund_type,
               account_code
          FROM budget_line_pool_mappings
         WHERE property_id = ?
           AND assessment_setup_id = ?
           AND active = 1
           AND mapping_source != 'operator'
        """,
        (property_id, assessment_setup_id),
    ).fetchall()
    stale_ids = [
        int(row[0])
        for row in rows
        if (
            str(row[1]),
            str(row[2]),
            str(row[3]),
            str(row[4]),
            row[5],
        )
        not in current_keys
    ]
    if not stale_ids:
        return 0
    placeholders = ",".join("?" for _ in stale_ids)
    connection.execute(
        f"""
        UPDATE budget_line_pool_mappings
           SET active = 0,
               review_state = 'stale'
         WHERE id IN ({placeholders})
        """,
        stale_ids,
    )
    return len(stale_ids)


def _insert_materialized_mapping(
    *,
    property_id: int,
    assessment_setup_id: int,
    line: dict,
    pool_key: str,
    source_rule_id: Optional[int],
    mapping_source: str,
    match_method: str,
    connection: sqlite3.Connection,
) -> None:
    normalized, section, category, fund_type, account_code = _line_key(line)
    amount, _source_column_used = select_assessment_mapping_amount(line)
    connection.execute(
        """
        INSERT INTO budget_line_pool_mappings (
            property_id, assessment_setup_id,
            budget_line_normalized_label, section, category,
            fund_type, account_code, pool_key, source_rule_id,
            mapping_source, match_method, approval_status, review_state,
            budget_line_amount, approved_by, approved_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'auto_approved',
                  'ready', ?, 'system', datetime('now'))
        ON CONFLICT(property_id, assessment_setup_id,
                    budget_line_normalized_label, section, category,
                    fund_type, COALESCE(account_code, ''))
        DO NOTHING
        """,
        (
            property_id,
            assessment_setup_id,
            normalized,
            section,
            category,
            fund_type,
            account_code,
            pool_key,
            source_rule_id,
            mapping_source,
            match_method,
            float(amount) if amount is not None else None,
        ),
    )


def materialize_budget_line_pool_mappings(
    *,
    property_id: int,
    assessment_setup_id: int,
    budget_lines: list[dict],
    connection: sqlite3.Connection,
    commit: bool = True,
) -> dict[str, int]:
    """Create annual mappings from approved reusable rules."""
    budget_lines = _with_assessment_mapping_amounts(budget_lines)
    counts = {
        "auto_approved": 0,
        "manual_preserved": 0,
        "suggested": 0,
        "conflict": 0,
        "unmatched": 0,
        "non_blocking": 0,
        "stale": 0,
    }
    classification_result = classify_budget_lines_for_mapping(budget_lines)
    current_keys = {
        item.line_key for item in classification_result.classifications
        if item.canonical
    }
    counts["stale"] = _mark_stale_auto_mappings(
        property_id=property_id,
        assessment_setup_id=assessment_setup_id,
        current_keys=current_keys,
        connection=connection,
    )
    existing_keys = _existing_mapping_keys(
        property_id=property_id,
        assessment_setup_id=assessment_setup_id,
        connection=connection,
    )
    rules = _active_rule_rows(
        property_id=property_id,
        assessment_setup_id=assessment_setup_id,
        connection=connection,
    )
    pool_names = _pool_name_by_key(
        assessment_setup_id=assessment_setup_id,
        connection=connection,
    )
    account_rules = [r for r in rules if r[5] == "account_code" and r[4]]
    label_rules = [
        r
        for r in rules
        if r[5] in {"normalized_label", "exact_label"} and r[3]
    ]
    remainder_rules = [r for r in rules if r[5] == "remainder"]
    aliases = _approved_aliases(
        property_id=property_id,
        assessment_setup_id=assessment_setup_id,
        connection=connection,
    )
    review_rows = build_assessment_mapping_review_rows(
        property_id=property_id,
        assessment_setup_id=assessment_setup_id,
        budget_lines=budget_lines,
        connection=connection,
    )
    regular_review_keys = {
        (
            str(row["normalized_label"]),
            str(row["section"]),
            str(row["category"]),
            str(row["fund_type"]),
            row["account_code"],
        )
        for row in review_rows
        if row["included_in_regular_basis"]
    }
    non_regular_review_keys = {
        (
            str(row["normalized_label"]),
            str(row["section"]),
            str(row["category"]),
            str(row["fund_type"]),
            row["account_code"],
        )
        for row in review_rows
        if not row["included_in_regular_basis"]
    }
    claimed_labels: set[str] = set()

    for idx, line in enumerate(budget_lines):
        key = _line_key(line)
        normalized = key[0]
        classification = classification_result.classifications[idx]
        if not classification.canonical:
            counts["non_blocking"] += 1
            continue
        if key in non_regular_review_keys:
            counts["non_blocking"] += 1
            continue
        if key not in regular_review_keys:
            counts["non_blocking"] += 1
            continue
        if classification and not classification.requires_mapping:
            counts["non_blocking"] += 1
            continue
        if key in existing_keys:
            counts["manual_preserved"] += 1
            claimed_labels.add(normalized)
            continue
        account_code = key[4]
        alias_candidates = [
            alias for alias in aliases
            if str(alias[2]) == normalized
            and (alias[3] in (None, "") or str(alias[3]) == str(account_code))
        ]
        candidates = []
        match_method = "approved_alias"
        mapping_source = "alias"
        if len(alias_candidates) > 1:
            counts["conflict"] += 1
            continue
        if alias_candidates:
            alias = alias_candidates[0]
            _insert_materialized_mapping(
                property_id=property_id,
                assessment_setup_id=assessment_setup_id,
                line=line,
                pool_key=str(alias[1]),
                source_rule_id=None,
                mapping_source=mapping_source,
                match_method=match_method,
                connection=connection,
            )
            existing_keys.add(key)
            claimed_labels.add(normalized)
            counts["auto_approved"] += 1
            continue
        candidates = [
            r
            for r in account_rules
            if str(r[4]) == str(account_code)
            and r[7] in {"approved", "auto_approved"}
            and r[8] == "ready"
        ]
        match_method = "account_code"
        mapping_source = "account_code"
        if not candidates:
            label_matches = [r for r in label_rules if r[3] == normalized]
            approved_label_matches = [
                r
                for r in label_matches
                if r[7] in {"approved", "auto_approved"} and r[8] == "ready"
            ]
            if len(approved_label_matches) > 1:
                counts["conflict"] += 1
                continue
            if approved_label_matches:
                candidates = approved_label_matches
                match_method = str(candidates[0][5])
                mapping_source = "normalized_label"
        if len(candidates) > 1:
            counts["conflict"] += 1
            continue
        if candidates:
            rule = candidates[0]
            _insert_materialized_mapping(
                property_id=property_id,
                assessment_setup_id=assessment_setup_id,
                line=line,
                pool_key=str(rule[1]),
                source_rule_id=int(rule[0]),
                mapping_source=mapping_source,
                match_method=match_method,
                connection=connection,
            )
            existing_keys.add(key)
            claimed_labels.add(normalized)
            counts["auto_approved"] += 1
            continue

    approved_remainders = [
        r
        for r in remainder_rules
        if r[7] in {"approved", "auto_approved"} and r[8] == "ready"
    ]
    if approved_remainders:
        for idx, line in enumerate(budget_lines):
            key = _line_key(line)
            normalized = key[0]
            classification = classification_result.classifications[idx]
            if key not in regular_review_keys:
                continue
            if classification and not classification.requires_mapping:
                continue
            if key in existing_keys:
                continue
            if not is_remainder_eligible_budget_line(
                line,
                already_mapped_normalized_labels=claimed_labels,
            ):
                continue
            if len(approved_remainders) > 1:
                counts["conflict"] += 1
                continue
            rule = approved_remainders[0]
            source = (
                "residual_default"
                if str(rule[10]) == "residual_default"
                else "remainder"
            )
            _insert_materialized_mapping(
                property_id=property_id,
                assessment_setup_id=assessment_setup_id,
                line=line,
                pool_key=str(rule[1]),
                source_rule_id=int(rule[0]),
                mapping_source=source,
                match_method="remainder",
                connection=connection,
            )
            existing_keys.add(key)
            claimed_labels.add(normalized)
            counts["auto_approved"] += 1

    for idx, line in enumerate(budget_lines):
        key = _line_key(line)
        normalized = key[0]
        classification = classification_result.classifications[idx]
        if key not in regular_review_keys:
            continue
        if classification and not classification.requires_mapping:
            continue
        if key not in existing_keys and normalized not in claimed_labels:
            suggested_candidates = _rank_line_review_candidates(
                line=line,
                classification=classification,
                rules=rules,
                pool_names=pool_names,
            )
            if suggested_candidates:
                counts["suggested"] += 1
            else:
                counts["unmatched"] += 1
    if commit:
        connection.commit()
    return counts


def get_mapping_reconciliation(
    *,
    property_id: int,
    assessment_setup_id: int,
    assessment_target: object,
    selected_budget_source_total: object,
    excluded_total: object = 0,
    offset_total: object = 0,
    schedule_annual_total: object = 0,
    tolerance: object = "0.01",
    connection: sqlite3.Connection,
) -> MappingReconciliation:
    """Tie mapped dollars and schedule totals before final rendering."""
    row = connection.execute(
        """
        SELECT COALESCE(SUM(COALESCE(budget_line_amount, 0)), 0)
          FROM budget_line_pool_mappings
         WHERE property_id = ?
           AND assessment_setup_id = ?
           AND active = 1
        """,
        (property_id, assessment_setup_id),
    ).fetchone()
    mapped_total = Decimal(str(row[0] or 0))
    target = Decimal(str(assessment_target or 0))
    source_total = Decimal(str(selected_budget_source_total or 0))
    excluded = Decimal(str(excluded_total or 0))
    offsets = Decimal(str(offset_total or 0))
    schedule_total = Decimal(str(schedule_annual_total or 0))
    allowed_delta = Decimal(str(tolerance or "0.01"))

    failures: list[str] = []
    if abs(mapped_total - target) > allowed_delta:
        failures.append("mapped_pool_total_mismatch")
    if abs(schedule_total - target) > allowed_delta:
        failures.append("schedule_total_mismatch")
    if abs((mapped_total + excluded + offsets) - source_total) > allowed_delta:
        failures.append("budget_source_total_mismatch")

    return MappingReconciliation(
        mapped_pool_total=mapped_total,
        assessment_target=target,
        selected_budget_source_total=source_total,
        excluded_total=excluded,
        offset_total=offsets,
        schedule_annual_total=schedule_total,
        tolerance=allowed_delta,
        passed=not failures,
        failures=failures,
    )


__all__ = [
    "BudgetLineClassification",
    "BudgetLineClassificationResult",
    "BudgetLineEligibility",
    "DuplicateBudgetLineConflict",
    "LineReviewCandidate",
    "MappingReconciliation",
    "backfill_rules_for_promoted_extraction_run",
    "build_assessment_mapping_review_line_key",
    "build_assessment_mapping_review_rows",
    "build_line_review_items",
    "classify_assessment_mapping_review_row_role",
    "canonicalize_budget_lines_for_mapping",
    "classify_budget_lines_for_mapping",
    "carry_forward_reusable_mapping_rules_across_setups",
    "derive_rules_from_dre_extraction",
    "ensure_exemption_decisions_from_dre_extraction",
    "get_mapping_reconciliation",
    "is_remainder_eligible_budget_line",
    "materialize_budget_line_pool_mappings",
    "normalize_budget_label",
    "resolve_active_assessment_setup_id",
    "record_scoped_alias",
    "select_assessment_mapping_amount",
    "set_exemption_decision_state",
]
