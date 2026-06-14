"""Gemini-assisted triage for annual assessment mapping review.

This layer never replaces deterministic routing. It only summarizes the
current HOA-specific review state into grouped operator decisions.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from ..ai_implementation.pipeline import llm_client
from ..config import settings
from ..dre_extraction.prompts.assessment_mapping_review_assistant import (
    PROMPT_SHA256,
    PROMPT_TEXT,
    PROMPT_VERSION,
)
from ..dre_extraction.schemas import DRESetupExtraction
from .assessment_budget_mapping_rule_service import (
    build_assessment_mapping_review_rows,
    build_line_review_items,
    canonicalize_budget_lines_for_mapping,
    classify_budget_lines_for_mapping,
    normalize_budget_label,
)


logger = logging.getLogger(__name__)


class EvidenceRef(BaseModel):
    source_type: str = ""
    rule_id: Optional[int] = None
    alias_id: Optional[int] = None
    pool_key: Optional[str] = None
    page_numbers: list[int] = Field(default_factory=list)


class SafeToStageDecision(BaseModel):
    line_label: str = ""
    normalized_label: str = ""
    section: str = ""
    category: str = ""
    fund_type: str = ""
    account_code: Optional[str] = None
    suggested_pool_key: str = ""
    action_kind: str = ""
    confidence: float = 0.0
    explanation: str = ""
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class DecisionOption(BaseModel):
    pool_key: str = ""
    label: str = ""


class NeedsDecisionItem(BaseModel):
    subject_type: str = ""
    line_label: str = ""
    normalized_label: str = ""
    section: str = ""
    category: str = ""
    fund_type: str = ""
    account_code: Optional[str] = None
    pool_key: Optional[str] = None
    options: list[DecisionOption] = Field(default_factory=list)
    recommended_pool_key: Optional[str] = None
    explanation: str = ""
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    blocker_kind: str = ""

    @field_validator("options", mode="before")
    @classmethod
    def _coerce_options(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        coerced: list[Any] = []
        for item in value:
            if isinstance(item, str):
                coerced.append({"pool_key": item, "label": item})
            else:
                coerced.append(item)
        return coerced


class ExcludeFromMappingItem(BaseModel):
    line_label: str = ""
    normalized_label: str = ""
    section: str = ""
    category: str = ""
    fund_type: str = ""
    account_code: Optional[str] = None
    exclusion_kind: str = ""
    explanation: str = ""
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class ResidualPreviewLine(BaseModel):
    line_label: str = ""
    normalized_label: str = ""
    section: str = ""
    category: str = ""
    fund_type: str = ""
    account_code: Optional[str] = None
    amount: Optional[float] = None
    reason: str = ""


class ResidualEqualPreview(BaseModel):
    residual_pool_key: Optional[str] = None
    candidate_lines: list[ResidualPreviewLine] = Field(default_factory=list)
    blocked_lines: list[ResidualPreviewLine] = Field(default_factory=list)
    explanation: str = ""

    @field_validator("candidate_lines", "blocked_lines", mode="before")
    @classmethod
    def _coerce_preview_lines(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        coerced: list[Any] = []
        for item in value:
            if isinstance(item, str):
                coerced.append({"line_label": item})
            else:
                coerced.append(item)
        return coerced


class MappingAnalysisAudit(BaseModel):
    model_name: str = ""
    prompt_version: str = ""
    prompt_sha256: str = ""


class MappingAnalysisResponse(BaseModel):
    available: bool = False
    reasons: list[str] = Field(default_factory=list)
    safe_to_stage: list[SafeToStageDecision] = Field(default_factory=list)
    needs_decision: list[NeedsDecisionItem] = Field(default_factory=list)
    exclude_from_mapping: list[ExcludeFromMappingItem] = Field(default_factory=list)
    residual_equal_preview: ResidualEqualPreview = Field(default_factory=ResidualEqualPreview)
    audit: MappingAnalysisAudit = Field(default_factory=MappingAnalysisAudit)


def _json_loads(value: Optional[str], default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _json_list(value: object) -> list[int]:
    if not value:
        return []
    decoded = _json_loads(str(value), [])
    if not isinstance(decoded, list):
        return []
    out: list[int] = []
    for item in decoded:
        try:
            out.append(int(item))
        except Exception:
            continue
    return out


def _line_key(line: dict[str, Any]) -> tuple[str, str, str, str, Optional[str]]:
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


def _pool_rows(
    *,
    assessment_setup_id: int,
    connection: sqlite3.Connection,
) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT pool_key, pool_name, allocation_method, recipient_scope,
               budget_line_derivation
          FROM allocation_pools
         WHERE assessment_setup_id = ?
         ORDER BY display_order, id
        """,
        (assessment_setup_id,),
    ).fetchall()


def _rule_rows(
    *,
    property_id: int,
    assessment_setup_id: int,
    connection: sqlite3.Connection,
) -> list[sqlite3.Row]:
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
         ORDER BY pool_key, id
        """,
        (property_id, assessment_setup_id),
    ).fetchall()


def _alias_rows(
    *,
    property_id: int,
    assessment_setup_id: int,
    connection: sqlite3.Connection,
) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT id, pool_key, dre_label, budget_label, account_code,
               approval_status, note
          FROM assessment_mapping_aliases
         WHERE property_id = ?
           AND assessment_setup_id = ?
           AND active = 1
         ORDER BY id
        """,
        (property_id, assessment_setup_id),
    ).fetchall()


def _existing_mapping_rows(
    *,
    property_id: int,
    assessment_setup_id: int,
    connection: sqlite3.Connection,
) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT budget_line_normalized_label, section, category, fund_type,
               account_code, pool_key, mapping_source, review_state,
               budget_line_amount
          FROM budget_line_pool_mappings
         WHERE property_id = ?
           AND assessment_setup_id = ?
           AND active = 1
         ORDER BY budget_line_normalized_label
        """,
        (property_id, assessment_setup_id),
    ).fetchall()


def _exemption_rows(
    *,
    property_id: int,
    assessment_setup_id: int,
    connection: sqlite3.Connection,
) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT pool_key, exemption_state, budget_year, budget_draft_id, notes
          FROM assessment_exemption_decisions
         WHERE property_id = ?
           AND assessment_setup_id = ?
         ORDER BY pool_key
        """,
        (property_id, assessment_setup_id),
    ).fetchall()


def _load_approved_extraction(
    *,
    assessment_setup_id: int,
    connection: sqlite3.Connection,
) -> Optional[DRESetupExtraction]:
    row = connection.execute(
        """
        SELECT parsed_json
          FROM dre_extraction_runs
         WHERE promoted_setup_id = ?
           AND review_status = 'promoted'
           AND parsed_json IS NOT NULL
         ORDER BY id DESC
         LIMIT 1
        """,
        (assessment_setup_id,),
    ).fetchone()
    if row is None or not row[0]:
        return None
    try:
        return DRESetupExtraction.model_validate_json(row[0])
    except Exception:
        return None


def _pool_option_payloads(pool_rows: list[sqlite3.Row]) -> list[dict[str, str]]:
    return [
        {
            "pool_key": str(row[0]),
            "label": str(row[1] or row[0]),
            "allocation_method": str(row[2] or ""),
            "recipient_scope": str(row[3] or ""),
            "budget_line_derivation": str(row[4] or ""),
        }
        for row in pool_rows
    ]


def _blocked_rule_matches(
    *,
    budget_lines: list[dict[str, Any]],
    rules: list[sqlite3.Row],
) -> list[dict[str, Any]]:
    blocked_types = {
        "exemption_credit": "exemption_or_credit",
        "subsidy_credit": "exemption_or_credit",
        "pass_through": "pass_through",
        "reserve_component": "reserve_component",
        "excluded_or_informational": "excluded_or_informational",
    }
    by_key: dict[tuple[str, str, str, str, Optional[str]], dict[str, Any]] = {}
    for line in budget_lines:
        by_key[_line_key(line)] = line

    matches: list[dict[str, Any]] = []
    for rule in rules:
        assessment_type = str(rule[13] or "unknown_needs_review")
        if assessment_type not in blocked_types:
            continue
        normalized = str(rule[3] or "")
        match_label = str(rule[2] or "")
        for line_key, line in by_key.items():
            line_normalized = line_key[0]
            if not normalized:
                continue
            if line_normalized != normalized and normalized not in line_normalized and line_normalized not in normalized:
                continue
            matches.append(
                {
                    "line_label": str(line.get("label") or ""),
                    "normalized_label": line_normalized,
                    "section": line_key[1],
                    "category": line_key[2],
                    "fund_type": line_key[3],
                    "account_code": line_key[4],
                    "assessment_type": assessment_type,
                    "exclusion_kind": blocked_types[assessment_type],
                    "match_label": match_label,
                    "rule_id": int(rule[0]),
                    "pool_key": str(rule[1]),
                    "review_reason": str(rule[15] or ""),
                    "source_evidence_text": str(rule[16] or ""),
                    "page_numbers": _json_list(rule[11]),
                }
            )
    unique: dict[tuple[str, str, str, str, Optional[str]], dict[str, Any]] = {}
    for item in matches:
        unique[(item["normalized_label"], item["section"], item["category"], item["fund_type"], item["account_code"])] = item
    return list(unique.values())


def _residual_pool_key(rules: list[sqlite3.Row]) -> Optional[str]:
    for rule in rules:
        if str(rule[5]) == "remainder" or str(rule[6]) == "system_remainder":
            return str(rule[1])
    return None


def _trigger_reasons(
    *,
    line_review_items: list[dict[str, Any]],
    blocked_matches: list[dict[str, Any]],
    exemptions: list[sqlite3.Row],
    residual_pool_key: Optional[str],
) -> list[str]:
    reasons: list[str] = []
    if any(item.get("status") != "mapped" for item in line_review_items):
        reasons.append("unresolved_or_suggested_lines")
    if blocked_matches:
        reasons.append("blocked_or_excluded_lines")
    if any(str(row[1]) == "pending_review" for row in exemptions):
        reasons.append("pending_exemption_decisions")
    if residual_pool_key:
        reasons.append("residual_pool_requires_operator_review")
    return reasons


def build_analysis_context(
    *,
    property_id: int,
    assessment_setup_id: int,
    budget_lines: list[dict[str, Any]],
    connection: sqlite3.Connection,
) -> tuple[dict[str, Any], list[str]]:
    canonical_budget_lines = canonicalize_budget_lines_for_mapping(budget_lines)
    classification = classify_budget_lines_for_mapping(canonical_budget_lines)
    review_rows = build_assessment_mapping_review_rows(
        property_id=property_id,
        assessment_setup_id=assessment_setup_id,
        budget_lines=budget_lines,
        connection=connection,
    )
    line_review_items = build_line_review_items(
        property_id=property_id,
        assessment_setup_id=assessment_setup_id,
        budget_lines=budget_lines,
        connection=connection,
    )
    pools = _pool_rows(
        assessment_setup_id=assessment_setup_id,
        connection=connection,
    )
    rules = _rule_rows(
        property_id=property_id,
        assessment_setup_id=assessment_setup_id,
        connection=connection,
    )
    aliases = _alias_rows(
        property_id=property_id,
        assessment_setup_id=assessment_setup_id,
        connection=connection,
    )
    exemptions = _exemption_rows(
        property_id=property_id,
        assessment_setup_id=assessment_setup_id,
        connection=connection,
    )
    blocked_matches = _blocked_rule_matches(
        budget_lines=canonical_budget_lines,
        rules=rules,
    )
    residual_pool_key = _residual_pool_key(rules)
    reasons = _trigger_reasons(
        line_review_items=line_review_items,
        blocked_matches=blocked_matches,
        exemptions=exemptions,
        residual_pool_key=residual_pool_key,
    )

    unresolved_regular_rows = [
        row
        for row in review_rows
        if row.get("included_in_regular_basis")
        and row.get("current_status") != "mapped"
    ]
    relevant_rule_ids = {
        int(candidate["rule_id"])
        for row in unresolved_regular_rows
        for candidate in row.get("candidates", [])
        if candidate.get("rule_id") is not None
    }
    relevant_rule_ids.update(
        int(match["rule_id"])
        for match in blocked_matches
        if match.get("rule_id") is not None
    )

    context = {
        "property_id": property_id,
        "assessment_setup_id": assessment_setup_id,
        "pool_options": _pool_option_payloads(pools),
        "review_rows": unresolved_regular_rows,
        "line_review_items": [
            item
            for item in line_review_items
            if item.get("status") != "mapped"
        ],
        "blocked_rule_matches": blocked_matches,
        "rules": [
            {
                "rule_id": int(row[0]),
                "pool_key": str(row[1]),
                "match_label": str(row[2] or ""),
                "normalized_label": str(row[3] or ""),
                "account_code": str(row[4]) if row[4] not in (None, "") else None,
                "match_type": str(row[5] or ""),
                "rule_source": str(row[6] or ""),
                "approval_status": str(row[7] or ""),
                "review_state": str(row[8] or ""),
                "confidence": float(row[9] or 0.0),
                "budget_line_derivation": str(row[10] or ""),
                "page_numbers": _json_list(row[11]),
                "source_parent_category": str(row[12] or ""),
                "assessment_type": str(row[13] or ""),
                "review_required": bool(row[14]),
                "review_reason": str(row[15] or ""),
                "source_evidence_text": str(row[16] or ""),
            }
            for row in rules
            if int(row[0]) in relevant_rule_ids
        ],
        "aliases": [
            {
                "alias_id": int(row[0]),
                "pool_key": str(row[1]),
                "dre_label": str(row[2] or ""),
                "budget_label": str(row[3] or ""),
                "account_code": str(row[4]) if row[4] not in (None, "") else None,
                "approval_status": str(row[5] or ""),
                "note": str(row[6] or ""),
            }
            for row in aliases
        ],
        "exemption_decisions": [
            {
                "pool_key": str(row[0]),
                "exemption_state": str(row[1]),
                "budget_year": row[2],
                "budget_draft_id": row[3],
                "notes": str(row[4] or ""),
            }
            for row in exemptions
        ],
        "residual_pool_key": residual_pool_key,
        "duplicate_conflicts": [
            {
                "normalized_label": conflict.normalized_label,
                "line_labels": conflict.line_labels,
                "amounts": [float(amount) for amount in conflict.amounts],
            }
            for conflict in classification.duplicate_conflicts
        ],
    }
    return context, reasons


def _empty_response(reasons: list[str]) -> MappingAnalysisResponse:
    return MappingAnalysisResponse(
        available=False,
        reasons=reasons,
        residual_equal_preview=ResidualEqualPreview(),
        audit=MappingAnalysisAudit(
            model_name=settings.GEMINI_MODEL or "",
            prompt_version=PROMPT_VERSION,
            prompt_sha256=PROMPT_SHA256,
        ),
    )


def _normalize_response(
    response: MappingAnalysisResponse,
    *,
    pool_rows: list[sqlite3.Row],
) -> MappingAnalysisResponse:
    allowed_pool_keys = {str(row[0]) for row in pool_rows}
    response.safe_to_stage = [
        item
        for item in response.safe_to_stage
        if item.suggested_pool_key in allowed_pool_keys
    ]
    for item in response.needs_decision:
        item.options = [
            option
            for option in item.options
            if option.pool_key in allowed_pool_keys
        ]
        if item.recommended_pool_key not in allowed_pool_keys:
            item.recommended_pool_key = None
    if response.residual_equal_preview.residual_pool_key not in allowed_pool_keys:
        response.residual_equal_preview.residual_pool_key = None
    response.audit = MappingAnalysisAudit(
        model_name=settings.GEMINI_MODEL or response.audit.model_name,
        prompt_version=PROMPT_VERSION,
        prompt_sha256=PROMPT_SHA256,
    )
    return response


def analyze_assessment_mapping_review(
    *,
    property_id: int,
    assessment_setup_id: int,
    budget_lines: list[dict[str, Any]],
    connection: sqlite3.Connection,
) -> MappingAnalysisResponse:
    context, reasons = build_analysis_context(
        property_id=property_id,
        assessment_setup_id=assessment_setup_id,
        budget_lines=budget_lines,
        connection=connection,
    )
    pool_rows = _pool_rows(
        assessment_setup_id=assessment_setup_id,
        connection=connection,
    )
    if not reasons:
        logger.info(
            "assessment mapping ai analyze skipped property_id=%s setup_id=%s reasons=%s",
            property_id,
            assessment_setup_id,
            ["analysis_not_needed"],
        )
        return _empty_response(["analysis_not_needed"])

    user_payload = {
        "contextual_reasons": reasons,
        "context": context,
    }
    serialized_payload = json.dumps(user_payload, sort_keys=True)
    logger.info(
        "assessment mapping ai analyze start property_id=%s setup_id=%s model=%s "
        "payload_bytes=%s budget_lines=%s review_rows=%s line_review_items=%s "
        "rules=%s aliases=%s blocked_matches=%s reasons=%s",
        property_id,
        assessment_setup_id,
        settings.GEMINI_MODEL or "",
        len(serialized_payload.encode("utf-8")),
        len(budget_lines),
        len(context.get("review_rows") or []),
        len(context.get("line_review_items") or []),
        len(context.get("rules") or []),
        len(context.get("aliases") or []),
        len(context.get("blocked_rule_matches") or []),
        reasons,
    )
    messages = [
        {"role": "system", "content": PROMPT_TEXT},
        {
            "role": "user",
            "content": serialized_payload,
        },
    ]
    try:
        result = asyncio.run(
            llm_client.call_llm(
                messages,
                MappingAnalysisResponse,
                temperature=0.0,
                timeout=30.0,
            )
        )
    except Exception as exc:
        reason = "timeout" if isinstance(exc, TimeoutError) or "timed out" in str(exc).lower() else str(exc)
        logger.warning(
            "assessment mapping ai analyze unavailable property_id=%s setup_id=%s "
            "model=%s reason=%s",
            property_id,
            assessment_setup_id,
            settings.GEMINI_MODEL or "",
            reason,
        )
        return _empty_response([f"analysis_unavailable: {reason}"])
    if result is None:
        logger.warning(
            "assessment mapping ai analyze empty property_id=%s setup_id=%s model=%s",
            property_id,
            assessment_setup_id,
            settings.GEMINI_MODEL or "",
        )
        return _empty_response(["analysis_unavailable: empty structured response"])
    result.available = True
    result.reasons = []
    logger.info(
        "assessment mapping ai analyze complete property_id=%s setup_id=%s available=%s "
        "safe_to_stage=%s needs_decision=%s exclude_from_mapping=%s residual_candidates=%s",
        property_id,
        assessment_setup_id,
        result.available,
        len(result.safe_to_stage),
        len(result.needs_decision),
        len(result.exclude_from_mapping),
        len(result.residual_equal_preview.candidate_lines),
    )
    return _normalize_response(
        result,
        pool_rows=pool_rows,
    )
