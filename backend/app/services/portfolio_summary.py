"""Cheap portfolio readiness summary for HOA list cards.

Reuses ``run_preflight_detailed`` step definitions so portfolio status matches
Disclosure Package readiness (same truth, no second engine). Intended for small
portfolios; each HOA runs DB-side preflight only (no PDF/Gemini).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..disclosure_package import service as dp_service

logger = logging.getLogger(__name__)

STATUS_NOT_STARTED = "Not Started"
STATUS_IN_PROGRESS = "In Progress"
STATUS_READY = "Ready for package"


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        # Support SQLite-ish and ISO forms
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _max_iso(timestamps: list[Optional[str]]) -> Optional[str]:
    best: Optional[datetime] = None
    best_raw: Optional[str] = None
    for raw in timestamps:
        if not raw:
            continue
        parsed = _parse_ts(raw)
        if parsed is None:
            continue
        if best is None or parsed > best:
            best = parsed
            best_raw = str(raw)
    return best_raw


def _activity_timestamps(conn, hoa_id: int) -> list[Optional[str]]:
    """Collect last-worked candidates from known activity tables."""
    out: list[Optional[str]] = []
    queries = [
        "SELECT MAX(updated_at) FROM budget_drafts WHERE property_id = ?",
        "SELECT MAX(created_at) FROM budget_versions WHERE property_id = ?",
        "SELECT MAX(created_at) FROM disclosure_package_jobs WHERE property_id = ?",
        "SELECT MAX(created_at) FROM dre_extraction_runs WHERE property_id = ?",
        "SELECT MAX(updated_at) FROM hoa_settings WHERE property_id = ?",
        "SELECT created_at FROM properties WHERE id = ?",
    ]
    for sql in queries:
        try:
            row = conn.execute(sql, (hoa_id,)).fetchone()
            if row and row[0] is not None:
                out.append(str(row[0]))
        except Exception:
            # Table may not exist in older DBs — skip.
            logger.debug("portfolio activity query failed: %s", sql, exc_info=True)
    return out


def _draft_and_version_flags(conn, hoa_id: int) -> tuple[bool, Optional[int]]:
    has_active = False
    latest_version_id: Optional[int] = None
    try:
        row = conn.execute(
            "SELECT id FROM budget_drafts WHERE property_id = ? AND status = 'active' "
            "ORDER BY id DESC LIMIT 1",
            (hoa_id,),
        ).fetchone()
        has_active = bool(row)
    except Exception:
        logger.debug("active draft lookup failed for %s", hoa_id, exc_info=True)
    try:
        row = conn.execute(
            "SELECT id FROM budget_versions WHERE property_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (hoa_id,),
        ).fetchone()
        if row:
            latest_version_id = int(row[0])
    except Exception:
        logger.debug("latest version lookup failed for %s", hoa_id, exc_info=True)
    return has_active, latest_version_id


def _portfolio_status_from_steps(
    steps: list[dict],
    *,
    has_active_draft: bool,
    latest_version_id: Optional[int],
) -> str:
    actionable = [s for s in steps if s.get("status") != "not_required"]
    if not actionable:
        if not has_active_draft and latest_version_id is None:
            return STATUS_NOT_STARTED
        return STATUS_IN_PROGRESS

    needs = any(s.get("status") in {"needs_action", "warning"} for s in actionable)
    # Treat warning as incomplete for portfolio "ready" band so green means
    # truly ready (blocking-free). Warnings keep readiness_pct high but status
    # stays In Progress unless only "done".
    blocking_needs = any(s.get("status") == "needs_action" for s in actionable)
    all_done = all(s.get("status") == "done" for s in actionable)

    if all_done and not needs:
        return STATUS_READY
    if blocking_needs or needs:
        # Zero progress with no artifacts → Not Started
        done_count = sum(1 for s in actionable if s.get("status") == "done")
        if done_count == 0 and not has_active_draft and latest_version_id is None:
            return STATUS_NOT_STARTED
        return STATUS_IN_PROGRESS
    return STATUS_READY


def _next_action_from_steps(steps: list[dict], hoa_id: int) -> Optional[dict[str, str]]:
    priority = (
        "needs_action",
        "warning",
    )
    for status in priority:
        for step in steps:
            if step.get("status") == status:
                label = step.get("fix_label") or step.get("label") or "Continue setup"
                # Prefer operator-facing next action phrasing
                step_label = step.get("label") or "Next step"
                if status == "needs_action":
                    text = f"Next: {step_label}"
                else:
                    text = f"Review: {step_label}"
                href = step.get("fix_path") or f"/hoa/{hoa_id}"
                return {
                    "label": text,
                    "href": str(href),
                    "code": str(step.get("id") or ""),
                }
    return {
        "label": "Generate disclosure package",
        "href": f"/hoa/{hoa_id}/disclosure",
        "code": "ready",
    }


def build_hoa_portfolio_summary(session: Session, hoa_id: int, portfolio_year: Optional[int]) -> dict:
    """Return portfolio enrichment fields for one HOA."""
    conn = session.connection().connection
    has_active_draft, latest_version_id = _draft_and_version_flags(conn, hoa_id)
    last_worked_at = _max_iso(_activity_timestamps(conn, hoa_id))

    fiscal_year = (
        int(portfolio_year)
        if portfolio_year is not None
        else dp_service._latest_fiscal_year(session, hoa_id)
    )

    steps: list[dict] = []
    try:
        detailed = dp_service.run_preflight_detailed(session, hoa_id, fiscal_year)
        steps = list(detailed.get("steps") or [])
    except Exception:
        logger.exception("portfolio preflight failed for HOA %s", hoa_id)
        steps = []

    actionable = [s for s in steps if s.get("status") != "not_required"]
    done = sum(1 for s in actionable if s.get("status") == "done")
    total = len(actionable) if actionable else 0
    readiness_pct = int(round(100.0 * done / total)) if total else 0

    portfolio_status = _portfolio_status_from_steps(
        steps,
        has_active_draft=has_active_draft,
        latest_version_id=latest_version_id,
    )
    # Never surface legacy "Completed" from this path.
    if portfolio_status == "Completed":
        portfolio_status = STATUS_READY

    next_action = _next_action_from_steps(steps, hoa_id)

    return {
        "portfolio_status": portfolio_status,
        "readiness_pct": readiness_pct,
        "readiness_done": done,
        "readiness_total": total,
        "next_action": next_action,
        "last_worked_at": last_worked_at,
        "has_active_draft": has_active_draft,
        "latest_budget_version_id": latest_version_id,
    }


def enrich_hoa_payload(session: Session, payload: dict) -> dict:
    """Mutate/copy HOA dict with portfolio summary fields."""
    hoa_id = int(payload["id"])
    summary = build_hoa_portfolio_summary(
        session,
        hoa_id,
        payload.get("portfolio_year"),
    )
    return {**payload, **summary}
