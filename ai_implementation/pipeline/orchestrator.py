"""5-stage pipeline orchestrator for AI budget suggestions."""
import asyncio
import json
import logging
import sqlite3
import time
from typing import Any, Optional

from ..config import settings
from ..database import get_db, write_lock
from ..models.schemas import (
    EnrichedLineItem,
    FlaggedItem,
    LLMPass1Result,
    SuggestRequest,
    SuggestResponse,
    SuggestionItem,
)
from ..pipeline.cbr_engine import find_anchor
from ..pipeline.confidence import calculate_confidence
from ..pipeline.feature_engineering import enrich_all_items
from ..pipeline.llm_pass1 import run_pass1
from ..pipeline.llm_pass2 import apply_pass2_revisions, compute_aggregate, run_pass2
from ..pipeline.ml_model import load_model, predict, should_activate, train_async

logger = logging.getLogger(__name__)


async def run_pipeline(request: SuggestRequest, db: sqlite3.Connection) -> SuggestResponse:
    """Run the full 5-stage AI budget pipeline.

    Stages:
        1. Feature engineering
        2. CBR retrieval (for all non-readOnly items)
        3. CatBoost inference (if active)
        4. LLM Pass 1 (3 async batches)
        5. LLM Pass 2 (holistic review)
    """
    pipeline_start = time.time()
    timings: dict[str, float] = {}

    # ── Stage 1: Feature Engineering ───────────────────────────────────────────
    t0 = time.time()
    enriched_items = await asyncio.to_thread(
        enrich_all_items, request.line_items, request.statement_month
    )
    active_items = [item for item in enriched_items if not item.read_only]
    timings["feature_engineering_ms"] = round((time.time() - t0) * 1000)
    logger.info(f"Stage 1 done: {len(active_items)} active items in {timings['feature_engineering_ms']}ms")

    # ── Stage 2: CBR Retrieval ──────────────────────────────────────────────────
    t0 = time.time()
    cbr_results: dict[int, tuple[Optional[float], Optional[float]]] = {}
    for item in active_items:
        anchor_pct, sim = await asyncio.to_thread(find_anchor, item, db)
        cbr_results[item.account_code] = (anchor_pct, sim)
    timings["cbr_ms"] = round((time.time() - t0) * 1000)
    cbr_hits = sum(1 for (a, _) in cbr_results.values() if a is not None)
    logger.info(f"Stage 2 done: {cbr_hits}/{len(active_items)} CBR anchors in {timings['cbr_ms']}ms")

    # ── Stage 3: CatBoost Inference ─────────────────────────────────────────────
    t0 = time.time()
    ml_results: dict[int, float] = {}
    catboost_active = await asyncio.to_thread(should_activate, db)
    if catboost_active:
        model = await asyncio.to_thread(load_model)
        if model is not None:
            ml_results = await asyncio.to_thread(predict, model, active_items)
            logger.info(f"Stage 3 done: {len(ml_results)} ML predictions in {round((time.time() - t0) * 1000)}ms")
        else:
            # Trigger background training if no model saved yet
            asyncio.create_task(train_async())
            logger.info("Stage 3: CatBoost active but no model yet, triggering training")
    else:
        logger.info("Stage 3: CatBoost dormant (insufficient cases)")
    timings["catboost_ms"] = round((time.time() - t0) * 1000)

    # ── Macro context for LLM ────────────────────────────────────────────────────
    ytd_pct = request.pct_year_elapsed * 100
    budget_vs_target = ((request.total_ytd_actuals / (request.total_annual_budget * request.pct_year_elapsed)) - 1) * 100 if request.pct_year_elapsed > 0 and request.total_annual_budget > 0 else 0
    status_str = f"{abs(budget_vs_target):.1f}% {'over' if budget_vs_target > 0 else 'under'} target at {ytd_pct:.0f}% through year"

    macro_context = {
        "property_name": request.property_name,
        "total_annual_budget": request.total_annual_budget,
        "total_ytd_actuals": request.total_ytd_actuals,
        "pct_year_elapsed": request.pct_year_elapsed,
        "overall_budget_status": status_str,
    }

    # ── Stage 4: LLM Pass 1 ─────────────────────────────────────────────────────
    t0 = time.time()
    pass1_results = await run_pass1(active_items, cbr_results, ml_results, macro_context, db)
    timings["llm_pass1_ms"] = round((time.time() - t0) * 1000)
    logger.info(f"Stage 4 done: {len(pass1_results)} suggestions in {timings['llm_pass1_ms']}ms")

    # ── Stage 5: LLM Pass 2 ─────────────────────────────────────────────────────
    t0 = time.time()
    budget_map = {item.account_code: item.annual_budget for item in active_items}
    aggregate = compute_aggregate(pass1_results, request.total_annual_budget, budget_map)
    pass2_result = await run_pass2(pass1_results, aggregate, macro_context)
    final_results, flagged_serialized = apply_pass2_revisions(pass1_results, pass2_result)
    timings["llm_pass2_ms"] = round((time.time() - t0) * 1000)
    logger.info(f"Stage 5 done in {timings['llm_pass2_ms']}ms")

    # ── Confidence Calculation ──────────────────────────────────────────────────
    flagged_account_codes = {f["account_code"] for f in flagged_serialized}
    revised_by_pass2_map: dict[int, bool] = {ac: True for ac in flagged_account_codes}

    # ── Persist to SQLite ───────────────────────────────────────────────────────
    total_latency_ms = round((time.time() - pipeline_start) * 1000)

    # Recompute aggregate from final_results so Pass 2 revisions are reflected
    final_aggregate = compute_aggregate(final_results, request.total_annual_budget, budget_map)
    total_change = final_aggregate.get("total_proposed_change", 0)
    total_budget = request.total_annual_budget
    impact_pct = (total_change / total_budget * 100) if total_budget else 0
    impact_str = f"{'+' if total_change >= 0 else ''}{total_change:,.0f} ({impact_pct:+.1f}%)"

    run_id, case_ids = await asyncio.to_thread(
        _persist_run,
        request,
        enriched_items,
        final_results,
        cbr_results,
        ml_results,
        revised_by_pass2_map,
        pass2_result.executive_summary,
        pass2_result.coherence_score,
        impact_str,
        flagged_serialized,
        total_latency_ms,
        db,
    )

    # ── Build Response ──────────────────────────────────────────────────────────
    pass1_map = {r.account_code: r for r in final_results}
    suggestions = []
    for case_id, item in zip(case_ids, active_items):
        p1 = pass1_map.get(item.account_code)
        if p1 is None:
            continue
        cbr_anchor, cbr_sim = cbr_results.get(item.account_code, (None, None))
        ml_baseline = ml_results.get(item.account_code)
        confidence = calculate_confidence(
            cbr_sim=cbr_sim,
            ml_baseline=ml_baseline,
            llm_suggestion=p1.suggested_pct_change,
            llm_confidence=p1.confidence,
        )
        suggestions.append(SuggestionItem(
            id=case_id,
            account_code=item.account_code,
            account_name=item.account_name,
            suggested_pct_change=p1.suggested_pct_change,
            reason=p1.reason,
            confidence=confidence,
            revised_by_pass2=revised_by_pass2_map.get(item.account_code, False),
            cbr_match=cbr_sim,
            ml_baseline=ml_baseline,
        ))

    flagged_items = [
        FlaggedItem(
            account_code=f["account_code"],
            issue=f["issue"],
            revised_pct_change=f["revised_pct_change"],
            revised_reason=f["revised_reason"],
        )
        for f in flagged_serialized
    ]

    logger.info(f"Pipeline complete in {total_latency_ms}ms | timings: {timings}")

    return SuggestResponse(
        run_id=run_id,
        suggestions=suggestions,
        executive_summary=pass2_result.executive_summary,
        coherence_score=pass2_result.coherence_score,
        total_budget_impact=impact_str,
        flagged_items=flagged_items,
    )


def _persist_run(
    request: SuggestRequest,
    enriched_items: list[EnrichedLineItem],
    final_results: list[LLMPass1Result],
    cbr_results: dict,
    ml_results: dict,
    revised_by_pass2_map: dict,
    executive_summary: str,
    coherence_score: str,
    impact_str: str,
    flagged_serialized: list,
    latency_ms: int,
    db: sqlite3.Connection,
) -> tuple[int, list[int]]:
    """Persist suggestion run and feedback_cases to SQLite. Returns (run_id, [case_ids])."""
    with write_lock():
        # Get or create property
        prop = db.execute(
            "SELECT id FROM properties WHERE name = ?", (request.property_name,)
        ).fetchone()
        if prop:
            property_id = prop[0]
        else:
            cur = db.execute(
                "INSERT INTO properties (name) VALUES (?)", (request.property_name,)
            )
            property_id = cur.lastrowid

        # Insert suggestion run
        cur = db.execute("""
            INSERT INTO suggestion_runs (
                property_id, source, total_annual_budget, total_ytd_actuals,
                pct_year_elapsed, statement_month, fiscal_year, growth_factor,
                executive_summary, coherence_score, total_budget_impact,
                flagged_items_json, latency_ms
            ) VALUES (?, 'live', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            property_id, request.total_annual_budget, request.total_ytd_actuals,
            request.pct_year_elapsed, request.statement_month, request.fiscal_year,
            request.growth_factor, executive_summary, coherence_score, impact_str,
            json.dumps(flagged_serialized), latency_ms,
        ))
        run_id = cur.lastrowid

        # Build a map of pass1 results
        pass1_map = {r.account_code: r for r in final_results}

        case_ids = []
        for item in enriched_items:
            if item.read_only:
                continue
            p1 = pass1_map.get(item.account_code)
            cbr_anchor, cbr_sim = cbr_results.get(item.account_code, (None, None))
            ml_baseline = ml_results.get(item.account_code)
            ai_pct = p1.suggested_pct_change if p1 else None
            ai_reason = p1.reason if p1 else None
            ai_conf = p1.confidence if p1 else None

            cur = db.execute("""
                INSERT INTO feedback_cases (
                    run_id, property_id, account_code, account_name, label, category,
                    account_level_1, account_level_2, account_level_3,
                    is_income, is_reserve, is_admin,
                    annual_budget, ytd_actual, projection,
                    pct_diff, coverage_ratio, adjusted_pct_diff, adjusted_coverage_ratio,
                    seasonality_index, normalized_annual_budget,
                    cbr_anchor_pct, cbr_similarity, ml_baseline_pct,
                    ai_suggested_pct_change, ai_reason, ai_confidence, revised_by_pass2,
                    user_decision
                ) VALUES (
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?,
                    'pending'
                )
            """, (
                run_id, property_id, item.account_code, item.account_name,
                item.label, item.category,
                item.account_level_1, item.account_level_2, item.account_level_3,
                int(item.is_income), int(item.is_reserve), int(item.is_admin),
                item.annual_budget, item.ytd_actual, item.adjusted_projection,
                item.pct_diff, item.coverage_ratio, item.adjusted_pct_diff,
                item.adjusted_coverage_ratio, item.seasonality_index,
                item.normalized_annual_budget,
                cbr_anchor, cbr_sim, ml_baseline,
                ai_pct, ai_reason, ai_conf,
                int(revised_by_pass2_map.get(item.account_code, False)),
            ))
            case_ids.append(cur.lastrowid)

        db.commit()

    return run_id, case_ids
