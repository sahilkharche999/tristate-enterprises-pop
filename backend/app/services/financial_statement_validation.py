"""Deterministic validation for extracted financial statements."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..models.financial_document_extraction import ExtractedFinancialStatement


def _is_effectively_zero(value: Any) -> bool:
    return value is None or abs(float(value)) < 1e-9


def validate_extracted_statement(statement: ExtractedFinancialStatement) -> list[dict[str, Any]]:
    """Validate extracted statements conservatively across Excel and PDF families."""

    issues: list[dict[str, Any]] = []
    line_items = statement.line_items

    if not line_items:
        return [
            {
                "code": "missing_line_items",
                "severity": "error",
                "message": "No line items were extracted from the statement.",
            }
        ]

    populated_numeric_rows = 0
    zero_only_rows = 0
    annual_budget_populated_rows = 0
    seen_keys: set[tuple[str, str]] = set()
    duplicate_keys: set[tuple[str, str]] = set()

    for item in line_items:
        numeric_values = (
            item.current_actual,
            item.current_budget,
            item.current_variance,
            item.ytd_actual,
            item.ytd_budget,
            item.ytd_variance,
            item.annual_budget,
        )
        has_non_zero_numeric = any(value is not None and not _is_effectively_zero(value) for value in numeric_values)
        if has_non_zero_numeric:
            populated_numeric_rows += 1
        else:
            zero_only_rows += 1

        # Track annual_budget coverage separately. The downstream tool's whole
        # job is to propose next year's annual budget — a parse with no annual
        # budget data is unusable even if other numeric columns survived.
        if item.annual_budget is not None and not _is_effectively_zero(item.annual_budget):
            annual_budget_populated_rows += 1

        dup_key = ((item.account_code_text or "").strip().lower(), item.label.strip().lower())
        if dup_key in seen_keys:
            duplicate_keys.add(dup_key)
        else:
            seen_keys.add(dup_key)

    if populated_numeric_rows == 0:
        issues.append(
            {
                "code": "missing_numeric_coverage",
                "severity": "error",
                "message": "All extracted line items are missing usable numeric values.",
            }
        )
    elif zero_only_rows / max(len(line_items), 1) >= 0.7:
        issues.append(
            {
                "code": "suspicious_zero_heavy_output",
                "severity": "error",
                "message": "Most extracted line items are zero-only, which suggests a low-confidence parse.",
                "details": {"zero_only_rows": zero_only_rows, "line_item_count": len(line_items)},
            }
        )

    # Reject parses with no usable annual budget data. The upload flow is
    # specifically designed to take an income statement and suggest next
    # year's annual budget per line item. Without annual budget figures the
    # downstream draft is empty in the only column that matters, so it is
    # better to reject the upload with a clear message than to silently
    # create a useless draft. Threshold: at least 30% of rows must have a
    # non-zero annual_budget value.
    annual_budget_coverage = annual_budget_populated_rows / max(len(line_items), 1)
    if annual_budget_coverage < 0.30:
        issues.append(
            {
                "code": "missing_annual_budget_coverage",
                "severity": "error",
                "message": (
                    "This statement does not include annual budget figures, so the "
                    "tool cannot use it to draft next year's budget. Please upload a "
                    "year-end income statement (or one that explicitly shows the annual "
                    "budget column for every line item)."
                ),
                "details": {
                    "annual_budget_populated_rows": annual_budget_populated_rows,
                    "line_item_count": len(line_items),
                    "coverage_ratio": round(annual_budget_coverage, 4),
                },
            }
        )

    for account_code_text, label in sorted(duplicate_keys):
        issues.append(
            {
                "code": "duplicate_line_item",
                "severity": "warning",
                "message": f"Duplicate line item detected for '{label}'.",
                "details": {"account_code_text": account_code_text or None, "label": label},
            }
        )

    # C6: cross-check parsed line items against stated totals. Entries
    # tagged source='document' were captured verbatim from the document's
    # own "Total …" rows (ground truth); untagged entries come from the
    # model extraction and are only a self-consistency check — the tag lets
    # operators tell the two apart. Tolerance is $1.00 per section to
    # absorb source-document rounding of individual lines.
    totals_by_section: dict[str, tuple[float, str]] = {}
    for total in statement.totals:
        section_kind = str(total.get("section_kind") or total.get("section") or "").strip().lower()
        amount = total.get("amount")
        source = str(total.get("source") or "model")
        if section_kind and amount is not None:
            totals_by_section[section_kind] = (float(amount), source)

    if totals_by_section:
        sum_by_section: dict[str, float] = defaultdict(float)
        transfers_by_section: dict[str, float] = defaultdict(float)
        for item in line_items:
            section_kind = (item.section_kind or "").strip().lower()
            if section_kind and item.annual_budget is not None:
                sum_by_section[section_kind] += float(item.annual_budget)
                if is_reserve_transfer_label(item.label):
                    transfers_by_section[section_kind] += float(item.annual_budget)

        for section_kind, (expected_total, source) in totals_by_section.items():
            actual_total = sum_by_section.get(section_kind, 0.0)
            # Statements print the same section two standard ways: operating
            # expenses with or without the contribution to reserves (Tri-State
            # budgets print it below the operating total), and revenues with or
            # without reserve interest (CPA forecasts total all funds). A
            # dropped or doubled row breaks every variant.
            variants = (
                actual_total,
                actual_total - transfers_by_section.get(section_kind, 0.0),
                actual_total + sum_by_section.get(f"reserve_{section_kind}", 0.0),
            )
            if all(abs(variant - expected_total) > 1.00 for variant in variants):
                stated_by = (
                    "the document states"
                    if source == "document"
                    else "the extraction reported"
                )
                # A warning, not a block: the draft is still created and the
                # operator sees the printed total next to the sum (decided
                # 2026-10-20; Gemini's section tagging of totals is imperfect).
                issues.append(
                    {
                        "code": "subtotal_mismatch",
                        "severity": "warning",
                        "message": (
                            f"Subtotal mismatch for section '{section_kind}': "
                            f"line items sum to {actual_total:,.2f} but "
                            f"{stated_by} {expected_total:,.2f}."
                        ),
                        "details": {
                            "section_kind": section_kind,
                            "expected_total": expected_total,
                            "actual_total": actual_total,
                            "totals_source": source,
                        },
                    }
                )

    return issues


def derive_statement_confidence(
    statement: ExtractedFinancialStatement,
    issues: list[dict[str, Any]] | None = None,
) -> float:
    """Compute a local confidence score from structural and numeric coverage."""

    issues = issues if issues is not None else validate_extracted_statement(statement)
    line_items = statement.line_items
    if not line_items:
        return 0.0

    numeric_rows = 0
    account_code_rows = 0
    section_rows = 0

    for item in line_items:
        numeric_values = (
            item.current_actual,
            item.current_budget,
            item.current_variance,
            item.ytd_actual,
            item.ytd_budget,
            item.ytd_variance,
            item.annual_budget,
        )
        if any(value is not None and not _is_effectively_zero(value) for value in numeric_values):
            numeric_rows += 1
        if (item.account_code_text or "").strip():
            account_code_rows += 1
        if (item.section_kind or "").strip():
            section_rows += 1

    row_count = max(len(line_items), 1)
    numeric_coverage = numeric_rows / row_count
    account_code_coverage = account_code_rows / row_count
    section_coverage = section_rows / row_count

    warning_penalty = 0.08 * sum(1 for issue in issues if issue.get("severity") == "warning")
    error_penalty = 0.25 * sum(1 for issue in issues if issue.get("severity") == "error")

    score = (
        0.15
        + 0.45 * numeric_coverage
        + 0.20 * account_code_coverage
        + 0.15 * section_coverage
        + 0.05 * (1.0 if statement.totals else 0.0)
        - warning_penalty
        - error_penalty
    )
    return round(max(0.0, min(0.99, score)), 4)


def has_blocking_validation_issues(issues: list[dict[str, Any]]) -> bool:
    return any(issue.get("severity") == "error" for issue in issues)


# Labels of the operating-side contribution to the reserve fund.
RESERVE_TRANSFER_PHRASES = (
    "contribution to reserve",
    "reserve contribution",
    "transfer to reserve",
    "allocation to reserve",
    "reserve allocation",
    "reserve transfer",
    "allocation/transfer",
    "contribution to replacement",
    "transfer to replacement",
    "replacement fund contribution",
)


def is_reserve_transfer_label(label: Any) -> bool:
    lowered = " ".join(str(label or "").lower().split())
    return any(phrase in lowered for phrase in RESERVE_TRANSFER_PHRASES)


def totals_for_validation(stated_totals: list[Any]) -> list[dict[str, Any]]:
    """Map printed total rows (``StatedTotal`` models or dicts) onto
    ``ExtractedFinancialStatement.totals`` for the subtotal check.

    Sub-totals partition a section ("Total Utilities" inside operating), so a
    section's grand total is its largest total — the same rule as
    ``budget_history_service._canonical_statement_from_line_items``.
    """
    best: dict[str, dict[str, Any]] = {}
    for total in stated_totals:
        get = total.get if isinstance(total, dict) else lambda key, _t=total: getattr(_t, key, None)
        section_kind, amount = get("section_kind"), get("annual_budget")
        if not section_kind or amount is None:
            continue
        current = best.get(section_kind)
        if current is None or abs(amount) > abs(current["amount"]):
            best[section_kind] = {
                "section_kind": section_kind,
                "label": get("label"),
                "amount": float(amount),
                "source": "document",
            }
    return list(best.values())
