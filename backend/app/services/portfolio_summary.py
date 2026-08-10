"""Cheap portfolio readiness summary for HOA list cards.

Reuses ``run_preflight_detailed`` step definitions so portfolio status matches
Disclosure Package readiness (same truth, no second engine). Intended for small
portfolios; each HOA runs DB-side preflight only (no PDF/Gemini).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..disclosure_package import service as dp_service

logger = logging.getLogger(__name__)

STATUS_NOT_STARTED = "Not Started"
STATUS_IN_PROGRESS = "In Progress"
STATUS_READY = "Ready for package"


def _parse_ts(value: Any) -> Optional[datetime]:
    """Parse a timestamp and normalize to UTC-aware for safe comparisons.

    Production SQLite mixes naive ``YYYY-MM-DD HH:MM:SS`` strings with
    ISO offsets / ``Z``. Comparing those without normalization raises
    ``TypeError: can't compare offset-naive and offset-aware datetimes``
    and 500s the entire ``GET /hoa`` list.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            # Support SQLite-ish and ISO forms
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


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


def _budget_draft_incomplete(steps: list[dict]) -> bool:
    """True when the operator still needs to create/upload an active budget draft."""
    budget = next((s for s in steps if s.get("id") == "budget_draft"), None)
    if budget is None:
        return True
    return budget.get("status") != "done"


def _readiness_score(
    steps: list[dict],
    *,
    has_active_draft: bool,
) -> tuple[int, int, int]:
    """Return (done, total, pct) for portfolio cards.

    Free "done" steps (appendices, annual package, leftover settings) must not
    make a green bar when the operator still has to start with income/reserve
    upload. Until the budget-draft step is done (or an active draft exists),
    report 0% complete.
    """
    actionable = [s for s in steps if s.get("status") != "not_required"]
    total = len(actionable) if actionable else 0
    if total == 0:
        return 0, 0, 0

    # No active draft / budget step incomplete → always 0% (start of seasonal flow).
    if _budget_draft_incomplete(steps) and not has_active_draft:
        return 0, total, 0

    done = sum(1 for s in actionable if s.get("status") == "done")
    pct = int(round(100.0 * done / total)) if total else 0
    return done, total, pct


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
    """Return portfolio enrichment fields for one HOA.

    Best-effort: individual steps may fail (schema drift, missing tables) but
    this function must never raise into the HOA list endpoint.
    """
    has_active_draft = False
    latest_version_id: Optional[int] = None
    last_worked_at: Optional[str] = None
    steps: list[dict] = []

    try:
        conn = session.connection().connection
        has_active_draft, latest_version_id = _draft_and_version_flags(conn, hoa_id)
        try:
            last_worked_at = _max_iso(_activity_timestamps(conn, hoa_id))
        except Exception:
            logger.exception("portfolio last_worked_at failed for HOA %s", hoa_id)
            last_worked_at = None

        fiscal_year = (
            int(portfolio_year)
            if portfolio_year is not None
            else dp_service._latest_fiscal_year(session, hoa_id)
        )
        try:
            detailed = dp_service.run_preflight_detailed(session, hoa_id, fiscal_year)
            steps = list(detailed.get("steps") or [])
        except Exception:
            logger.exception("portfolio preflight failed for HOA %s", hoa_id)
            steps = []
    except Exception:
        logger.exception("portfolio summary bootstrap failed for HOA %s", hoa_id)

    done, total, readiness_pct = _readiness_score(
        steps,
        has_active_draft=has_active_draft,
    )

    portfolio_status = _portfolio_status_from_steps(
        steps,
        has_active_draft=has_active_draft,
        latest_version_id=latest_version_id,
    )
    # Never surface legacy "Completed" from this path.
    if portfolio_status == "Completed":
        portfolio_status = STATUS_READY
    # No active budget work → treat as not started on the portfolio card
    # even if optional steps (appendices / annual package) report "done".
    if _budget_draft_incomplete(steps) and not has_active_draft:
        portfolio_status = STATUS_NOT_STARTED

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
    try:
        summary = build_hoa_portfolio_summary(
            session,
            hoa_id,
            payload.get("portfolio_year"),
        )
    except Exception:
        logger.exception("enrich_hoa_payload failed for HOA %s", hoa_id)
        summary = {
            "portfolio_status": STATUS_IN_PROGRESS,
            "readiness_pct": 0,
            "readiness_done": 0,
            "readiness_total": 0,
            "next_action": {
                "label": "Open HOA workspace",
                "href": f"/hoa/{hoa_id}",
                "code": "error",
            },
            "last_worked_at": None,
            "has_active_draft": False,
            "latest_budget_version_id": None,
        }
    return {**payload, **summary}
