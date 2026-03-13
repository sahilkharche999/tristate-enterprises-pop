"""FastAPI sub-router for AI Budget Pipeline endpoints."""
import logging
from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from .config import settings
from .database import get_db, write_lock
from .models.schemas import (
    FeedbackRequest,
    FeedbackResponse,
    StatsResponse,
    SuggestRequest,
    SuggestResponse,
)
from .pipeline.cbr_engine import retain_feedback
from .pipeline.ml_model import should_activate
from .pipeline.orchestrator import run_pipeline

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/suggest", response_model=SuggestResponse)
async def suggest(request: SuggestRequest) -> SuggestResponse:
    """Run the full AI budget pipeline and return suggestions."""
    logger.info(
        f"POST /ai/suggest: {len(request.line_items)} items, "
        f"property={request.property_name}, month={request.statement_month}"
    )
    db = await run_in_threadpool(get_db)
    try:
        response = await run_pipeline(request, db)
        logger.info(f"POST /ai/suggest complete: run_id={response.run_id}")
        return response
    except Exception as e:
        logger.exception("Pipeline error")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.post("/feedback", response_model=FeedbackResponse)
async def feedback(request: FeedbackRequest) -> FeedbackResponse:
    """Record user accept/modify/reject decisions for a suggestion run."""
    logger.info(f"POST /ai/feedback: run_id={request.run_id}, {len(request.decisions)} decisions")
    db = await run_in_threadpool(get_db)
    try:
        for decision in request.decisions:
            await run_in_threadpool(
                retain_feedback,
                decision.feedback_case_id,
                decision.decision,
                decision.final_pct_change,
                decision.note or "",
                db,
            )

        total = db.execute(
            "SELECT COUNT(*) FROM feedback_cases WHERE user_decision IN ('accepted','modified')"
        ).fetchone()[0]
        active = await run_in_threadpool(should_activate, db)

        return FeedbackResponse(
            updated=len(request.decisions),
            total_cases=total,
            catboost_active=active,
        )
    except Exception as e:
        logger.exception("Feedback error")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.get("/stats", response_model=StatsResponse)
async def stats() -> StatsResponse:
    """Return pipeline health metrics."""
    db = await run_in_threadpool(get_db)
    try:
        total = db.execute(
            "SELECT COUNT(*) FROM feedback_cases WHERE user_decision IN ('accepted','modified')"
        ).fetchone()[0]
        active = await run_in_threadpool(should_activate, db)

        # Last training timestamp from meta.json
        import json
        from pathlib import Path
        meta_path = Path(__file__).parent / "data" / "catboost_model" / "meta.json"
        last_training = None
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                last_training = meta.get("timestamp")
            except Exception:
                pass

        props = [
            r[0] for r in db.execute("SELECT name FROM properties").fetchall()
        ]
        sop_count = db.execute(
            "SELECT COUNT(*) FROM sop_rules WHERE active = 1"
        ).fetchone()[0]

        return StatsResponse(
            total_cases=total,
            catboost_active=active,
            last_training=last_training,
            properties=props,
            sop_rules=sop_count,
        )
    finally:
        db.close()
