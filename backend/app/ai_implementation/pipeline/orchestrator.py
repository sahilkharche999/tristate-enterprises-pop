"""5-stage pipeline orchestrator for AI budget suggestions."""
import asyncio
import json
import logging
import time
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db.models import Property, SuggestionRun, FeedbackCase
from ..models.schemas import (
    EnrichedLineItem,
    FlaggedItem,
    LLMPass1Result,
    SuggestRequest,
    SuggestResponse,
    SuggestionItem,
)
from ..pipeline.cbr_engine import find_anchor, retrieve_cases
from ..pipeline.confidence import calculate_confidence
from ..pipeline.feature_engineering import enrich_all_items
from ..pipeline.llm_pass1 import run_pass1
from ..pipeline.llm_pass2 import apply_pass2_revisions, compute_aggregate, run_pass2
from ..pipeline.ml_model import load_model, predict, should_activate, train_async

logger = logging.getLogger(__name__)


async def run_pipeline(request: SuggestRequest, session: Session) -> SuggestResponse:
    """Run the full 5-stage AI budget pipeline."""
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

    # ── Stage 2: CBR Retrieval (load cases once, match per item) ─────────────
    t0 = time.time()
    case_matrix, case_rows = await asyncio.to_thread(retrieve_cases, session)
    cbr_results: dict[int, tuple[Optional[float], Optional[float]]] = {}
    for item in active_items:
        anchor_pct, sim = find_anchor(item, case_matrix, case_rows)
        cbr_results[item.account_code] = (anchor_pct, sim)
    timings["cbr_ms"] = round((time.time() - t0) * 1000)
    cbr_hits = sum(1 for (a, _) in cbr_results.values() if a is not None)
    logger.info(f"Stage 2 done: {cbr_hits}/{len(active_items)} CBR anchors in {timings['cbr_ms']}ms")

    # ── Stage 3: CatBoost Inference ─────────────────────────────────────────────
    t0 = time.time()
    ml_results: dict[int, float] = {}
    catboost_active = should_activate(session)
    if catboost_active:
        model = await asyncio.to_thread(load_model)
        if model is not None:
            ml_results = await asyncio.to_thread(predict, model, active_items)
            logger.info(f"Stage 3 done: {len(ml_results)} ML predictions in {round((time.time() - t0) * 1000)}ms")
        else:
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
    pass1_results = await run_pass1(
        active_items, cbr_results, ml_results, macro_context, session,
        pct_year_elapsed=request.pct_year_elapsed,
    )
    timings["llm_pass1_ms"] = round((time.time() - t0) * 1000)
    logger.info(f"Stage 4 done: {len(pass1_results)} suggestions in {timings['llm_pass1_ms']}ms")

    # ── Stage 5: LLM Pass 2 ─────────────────────────────────────────────────────
    t0 = time.time()
    budget_map = {item.account_code: item.annual_budget for item in active_items}
    aggregate = compute_aggregate(pass1_results, request.total_annual_budget, budget_map)
    pass2_result = await run_pass2(pass1_results, aggregate, macro_context)
    enriched_item_map = {item.account_code: item for item in active_items}
    final_results, flagged_serialized = apply_pass2_revisions(
        pass1_results, pass2_result,
        enriched_item_map=enriched_item_map,
        pct_year_elapsed=request.pct_year_elapsed,
    )
    timings["llm_pass2_ms"] = round((time.time() - t0) * 1000)
    logger.info(f"Stage 5 done in {timings['llm_pass2_ms']}ms")

    # ── Post-processing ──────────────────────────────────────────────────────────
    flagged_account_codes = {f["account_code"] for f in flagged_serialized}
    revised_by_pass2_map: dict[int, bool] = {ac: True for ac in flagged_account_codes}

    total_latency_ms = round((time.time() - pipeline_start) * 1000)

    final_aggregate = compute_aggregate(final_results, request.total_annual_budget, budget_map)
    total_change = final_aggregate.get("total_proposed_change", 0)
    total_budget = request.total_annual_budget
    impact_pct = (total_change / total_budget * 100) if total_budget else 0
    impact_str = f"{'+' if total_change >= 0 else ''}{total_change:,.0f} ({impact_pct:+.1f}%)"

    pass1_map = {r.account_code: r for r in final_results}

    # ── Persist to SQLite ───────────────────────────────────────────────────────
    run_id, case_ids = await asyncio.to_thread(
        _persist_run, request, enriched_items, pass1_map,
        cbr_results, ml_results, revised_by_pass2_map,
        pass2_result.executive_summary, pass2_result.coherence_score,
        impact_str, flagged_serialized, total_latency_ms, session,
    )

    # ── Build Response ──────────────────────────────────────────────────────────
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

    # ── Deficit-driven assessment recommendation ──────────────────────────────────
    projected_deficit, recommended_assessment_pct, assessment_note = _compute_assessment_recommendation(
        enriched_items, pass1_map
    )

    logger.info(f"Pipeline complete in {total_latency_ms}ms | timings: {timings}")

    return SuggestResponse(
        run_id=run_id,
        suggestions=suggestions,
        executive_summary=pass2_result.executive_summary,
        coherence_score=pass2_result.coherence_score,
        total_budget_impact=impact_str,
        flagged_items=flagged_items,
        projected_deficit=projected_deficit,
        recommended_assessment_increase_pct=recommended_assessment_pct,
        assessment_recommendation_note=assessment_note,
    )


def _compute_assessment_recommendation(
    enriched_items: list[EnrichedLineItem],
    pass1_map: dict[int, LLMPass1Result],
) -> tuple[float, float, str]:
    """Compute deficit-driven assessment (dues) increase recommendation.

    Projects next-year expenses using the suggested % changes, holds income flat,
    then calculates what dues increase would close the resulting deficit.
    Reserve funding (non-income items) is included in the expense projection per plan.

    Returns (projected_deficit, recommended_pct, note_string).
    """
    projected_expenses = 0.0
    projected_income = 0.0

    for item in enriched_items:
        if item.read_only or item.annual_budget is None or item.annual_budget <= 0:
            continue
        if item.is_income:
            projected_income += item.annual_budget
        else:
            p1 = pass1_map.get(item.account_code)
            suggested = p1.suggested_pct_change if p1 else 0.0
            projected_expenses += item.annual_budget * (1.0 + suggested)

    projected_deficit = projected_expenses - projected_income

    if projected_income <= 0 or projected_deficit <= 0:
        surplus = -projected_deficit
        note = (
            f"Projected expenses ${projected_expenses:,.0f} vs. income ${projected_income:,.0f} "
            f"— surplus of ${surplus:,.0f}. No assessment increase needed."
            if projected_deficit <= 0
            else "No income data available to compute assessment recommendation."
        )
        return projected_deficit, 0.0, note

    recommended_pct = projected_deficit / projected_income
    note = (
        f"Projected expenses ${projected_expenses:,.0f} exceed income ${projected_income:,.0f} "
        f"by ${projected_deficit:,.0f} (incl. reserve funding). "
        f"Raise assessments ~{recommended_pct*100:.1f}% to break even."
    )
    return projected_deficit, recommended_pct, note


def _persist_run(
    request: SuggestRequest,
    enriched_items: list[EnrichedLineItem],
    pass1_map: dict[int, LLMPass1Result],
    cbr_results: dict,
    ml_results: dict,
    revised_by_pass2_map: dict,
    executive_summary: str,
    coherence_score: str,
    impact_str: str,
    flagged_serialized: list,
    latency_ms: int,
    session: Session,
) -> tuple[int, list[int]]:
    """Persist suggestion run and feedback_cases. Returns (run_id, [case_ids])."""
    # Get or create property
    prop = session.execute(
        select(Property).where(Property.name == request.property_name)
    ).scalar_one_or_none()
    if not prop:
        prop = Property(name=request.property_name)
        session.add(prop)
        session.flush()

    # Insert suggestion run
    run = SuggestionRun(
        property_id=prop.id,
        source="live",
        total_annual_budget=request.total_annual_budget,
        total_ytd_actuals=request.total_ytd_actuals,
        pct_year_elapsed=request.pct_year_elapsed,
        statement_month=request.statement_month,
        fiscal_year=request.fiscal_year,
        growth_factor=request.growth_factor,
        executive_summary=executive_summary,
        coherence_score=coherence_score,
        total_budget_impact=impact_str,
        flagged_items_json=json.dumps(flagged_serialized),
        latency_ms=latency_ms,
    )
    session.add(run)
    session.flush()

    # Insert feedback cases in batch
    cases = []
    for item in enriched_items:
        if item.read_only:
            continue
        p1 = pass1_map.get(item.account_code)
        cbr_anchor, cbr_sim = cbr_results.get(item.account_code, (None, None))
        ml_baseline = ml_results.get(item.account_code)

        cases.append(FeedbackCase(
            run_id=run.id,
            property_id=prop.id,
            account_code=item.account_code,
            account_name=item.account_name,
            label=item.label,
            category=item.category,
            account_level_1=item.account_level_1,
            account_level_2=item.account_level_2,
            account_level_3=item.account_level_3,
            is_income=int(item.is_income),
            is_reserve=int(item.is_reserve),
            is_admin=int(item.is_admin),
            annual_budget=item.annual_budget,
            ytd_actual=item.ytd_actual,
            projection=item.adjusted_projection,
            pct_diff=item.pct_diff,
            coverage_ratio=item.coverage_ratio,
            adjusted_pct_diff=item.adjusted_pct_diff,
            adjusted_coverage_ratio=item.adjusted_coverage_ratio,
            seasonality_index=item.seasonality_index,
            normalized_annual_budget=item.normalized_annual_budget,
            cbr_anchor_pct=cbr_anchor,
            cbr_similarity=cbr_sim,
            ml_baseline_pct=ml_baseline,
            ai_suggested_pct_change=p1.suggested_pct_change if p1 else None,
            ai_reason=p1.reason if p1 else None,
            ai_confidence=p1.confidence if p1 else None,
            revised_by_pass2=int(revised_by_pass2_map.get(item.account_code, False)),
            user_decision="pending",
        ))

    session.add_all(cases)
    session.flush()
    case_ids = [c.id for c in cases]

    session.commit()
    return run.id, case_ids
