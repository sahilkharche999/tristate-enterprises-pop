"""FastAPI sub-router for AI Budget Pipeline endpoints."""
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from .config import settings
from .db import get_session, FeedbackCase, Property, SOPRule, SuggestionRun, User, DECIDED_STATUSES
from .models.schemas import (
    FeedbackRequest,
    FeedbackResponse,
    FlaggedItem,
    StatsResponse,
    SuggestRequest,
    SuggestResponse,
    SuggestionItem,
)
from .pipeline.cbr_engine import retain_feedback
from .pipeline.ml_model import should_activate
from .pipeline.orchestrator import run_pipeline

logger = logging.getLogger(__name__)

router = APIRouter()

_CATBOOST_META_PATH = Path(__file__).parent / "data" / "catboost_model" / "meta.json"


def _count_decided_cases(session: Session) -> int:
    """Count accepted/modified feedback cases. Shared by feedback + stats endpoints."""
    return session.scalar(
        select(func.count()).select_from(FeedbackCase)
        .where(FeedbackCase.user_decision.in_(DECIDED_STATUSES))
    ) or 0


@router.post("/suggest", response_model=SuggestResponse)
async def suggest(request: SuggestRequest, session: Session = Depends(get_session)) -> SuggestResponse:
    """Run the full AI budget pipeline and return suggestions."""
    logger.info(
        f"POST /ai/suggest: {len(request.line_items)} items, "
        f"property={request.property_name}, month={request.statement_month}"
    )
    try:
        response = await run_pipeline(request, session)
        logger.info(f"POST /ai/suggest complete: run_id={response.run_id}")
        return response
    except Exception as e:
        logger.exception("Pipeline error")
        raise HTTPException(status_code=500, detail="AI suggestion pipeline failed. Check server logs for details.")


@router.get("/suggest/{property_id}/latest", response_model=SuggestResponse)
def get_latest_suggestions(
    property_id: int,
    session: Session = Depends(get_session),
) -> SuggestResponse:
    """Return the most recent suggestion run for a property (no pipeline re-execution)."""
    run = session.execute(
        select(SuggestionRun)
        .where(SuggestionRun.property_id == property_id)
        .order_by(SuggestionRun.id.desc())
    ).scalars().first()
    if run is None:
        raise HTTPException(status_code=404, detail="No suggestion run found for this property")

    cases = session.execute(
        select(FeedbackCase)
        .where(FeedbackCase.run_id == run.id)
        .order_by(FeedbackCase.account_code)
    ).scalars().all()

    suggestions = [
        SuggestionItem(
            id=c.id,
            account_code=c.account_code,
            account_name=c.account_name,
            suggested_pct_change=c.ai_suggested_pct_change or 0.0,
            reason=c.ai_reason or "",
            confidence=c.ai_confidence or 0.0,
            revised_by_pass2=bool(c.revised_by_pass2),
            cbr_match=c.cbr_similarity,
            ml_baseline=c.ml_baseline_pct,
        )
        for c in cases
    ]

    flagged = json.loads(run.flagged_items_json or "[]")

    return SuggestResponse(
        run_id=run.id,
        suggestions=suggestions,
        executive_summary=run.executive_summary or "",
        coherence_score=run.coherence_score or "medium",
        total_budget_impact=run.total_budget_impact or "",
        flagged_items=[FlaggedItem(**f) for f in flagged],
        projected_deficit=run.projected_deficit or 0.0,
        recommended_assessment_increase_pct=run.recommended_assessment_increase_pct or 0.0,
        assessment_recommendation_note=run.assessment_recommendation_note or "",
    )


@router.post("/feedback", response_model=FeedbackResponse)
async def feedback(request: FeedbackRequest, session: Session = Depends(get_session)) -> FeedbackResponse:
    """Record user accept/modify/reject decisions for a suggestion run."""
    logger.info(f"POST /ai/feedback: run_id={request.run_id}, {len(request.decisions)} decisions")
    try:
        # Process all decisions, then commit once
        for decision in request.decisions:
            retain_feedback(
                decision.feedback_case_id,
                decision.decision,
                decision.final_pct_change,
                decision.note or "",
                session,
            )
        session.commit()

        total = _count_decided_cases(session)
        catboost_active = total >= settings.CATBOOST_MIN_CASES

        return FeedbackResponse(
            updated=len(request.decisions),
            total_cases=total,
            catboost_active=catboost_active,
        )
    except Exception as e:
        session.rollback()
        logger.exception("Feedback error")
        raise HTTPException(status_code=500, detail="Feedback submission failed. Check server logs for details.")


@router.get("/stats", response_model=StatsResponse)
async def stats(session: Session = Depends(get_session)) -> StatsResponse:
    """Return pipeline health metrics."""
    total = _count_decided_cases(session)
    catboost_active = total >= settings.CATBOOST_MIN_CASES

    last_training = None
    if _CATBOOST_META_PATH.exists():
        try:
            meta = json.loads(_CATBOOST_META_PATH.read_text())
            last_training = meta.get("timestamp")
        except Exception:
            pass

    props = list(session.scalars(select(Property.name)))
    sop_count = session.scalar(
        select(func.count()).select_from(SOPRule).where(SOPRule.active == 1)
    ) or 0

    return StatsResponse(
        total_cases=total,
        catboost_active=catboost_active,
        last_training=last_training,
        properties=props,
        sop_rules=sop_count,
    )


@router.get("/export")
async def export_data(session: Session = Depends(get_session)):
    """Export all database tables as JSON for backup/analysis."""
    users = [
        {"id": u.id, "email": u.email, "name": u.name, "created_at": u.created_at}
        for u in session.execute(select(User)).scalars().all()
    ]

    properties = [
        {"id": p.id, "name": p.name, "units": p.units, "created_at": p.created_at}
        for p in session.execute(select(Property)).scalars().all()
    ]

    runs = [
        {
            "id": r.id, "property_id": r.property_id, "source": r.source,
            "statement_month": r.statement_month, "fiscal_year": r.fiscal_year,
            "executive_summary": r.executive_summary, "coherence_score": r.coherence_score,
            "total_budget_impact": r.total_budget_impact, "latency_ms": r.latency_ms,
            "created_at": r.created_at,
        }
        for r in session.execute(select(SuggestionRun)).scalars().all()
    ]

    cases = [
        {
            "id": c.id, "run_id": c.run_id, "account_code": c.account_code,
            "account_name": c.account_name, "label": c.label,
            "annual_budget": c.annual_budget, "ytd_actual": c.ytd_actual,
            "ai_suggested_pct_change": c.ai_suggested_pct_change,
            "ai_reason": c.ai_reason, "ai_confidence": c.ai_confidence,
            "user_decision": c.user_decision,
            "user_final_pct_change": c.user_final_pct_change,
            "user_note": c.user_note, "created_at": c.created_at,
        }
        for c in session.execute(select(FeedbackCase)).scalars().all()
    ]

    sop_rules = [
        {"id": s.id, "rule_text": s.rule_text, "active": s.active}
        for s in session.execute(select(SOPRule)).scalars().all()
    ]

    return {
        "users": users,
        "properties": properties,
        "suggestion_runs": runs,
        "feedback_cases": cases,
        "sop_rules": sop_rules,
    }
