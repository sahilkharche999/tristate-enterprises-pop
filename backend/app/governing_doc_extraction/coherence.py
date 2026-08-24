"""Post-extract allocation-policy coherence checks for CC&R runs.

Document-agnostic heuristics on the *domain* ``DRESetupExtraction`` shape
(after wire_to_domain). Detects the common collapse where an exhibit
factor table is mistaken for a per-unit dollar schedule and the extract
emits a single proportional pool under ``individual_unit``.

Does NOT force multi-pool on pure-equal or pure-proportional policies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.dre_extraction.schemas import (
    DRESetupExtraction,
    HumanReviewQuestion,
)

_PROPORTIONAL_METHODS = frozenset({"ownership_percentage", "square_footage"})
_RECURRING_CONTEXTS = frozenset(
    {"regular_operating", "cost_center", "reserve_contribution"}
)


@dataclass(frozen=True)
class CoherenceFinding:
    """One incoherence reason; empty reasons means coherent."""

    reasons: tuple[str, ...] = ()

    @property
    def is_incoherent(self) -> bool:
        return bool(self.reasons)


class IncoherentCcrExtraction(Exception):
    """Raised at promote time when allocation shape is still incoherent.

    Mapped to HTTP 422 by the CCR router (same family as MissingUnitFactors).
    """

    def __init__(self, reasons: list[str], *, collapsed: bool = True) -> None:
        self.reasons = list(reasons)
        prefix = (
            "CC&R extraction allocation policy looks incomplete or collapsed; "
            "correct pools/setup_type in review before promoting. "
            if collapsed
            else ""
        )
        super().__init__(prefix + "; ".join(self.reasons))


def assess_allocation_coherence(
    extraction: Optional[DRESetupExtraction],
) -> CoherenceFinding:
    """Return coherence findings for a domain extraction (or empty if N/A)."""
    if extraction is None:
        return CoherenceFinding()

    reasons: list[str] = []
    setup_type = (extraction.assessment_setup.setup_type or "").strip()
    pools = list(extraction.allocation_pools or [])
    units = list(extraction.unit_structure.units or [])

    # multi_pool_combination must actually carry more than one pool.
    if setup_type == "multi_pool_combination" and len(pools) < 2:
        reasons.append(
            "setup_type is multi_pool_combination but fewer than 2 allocation pools "
            f"were extracted ({len(pools)})"
        )

    # Missouri-class collapse: factor table + single proportional pool
    # mislabeled as individual_unit (not a true per-unit dollar schedule).
    if (
        setup_type == "individual_unit"
        and len(pools) == 1
        and len(units) > 0
    ):
        method = (pools[0].allocation_method or "").strip()
        if method in _PROPORTIONAL_METHODS and not _has_dollar_schedule(
            extraction
        ):
            reasons.append(
                "setup_type is individual_unit with a single "
                f"{method} pool and unit factor rows but no per-unit dollar "
                "schedule — likely a multi-method or residual/exception policy "
                "collapsed onto the exhibit factor table"
            )

    declared_contexts = {
        context.strip()
        for context in extraction.assessment_setup.declared_contexts
        if context and context.strip()
    }
    pool_contexts = {
        pool.allocation_context
        for pool in pools
        if pool.allocation_context
    }
    for context in sorted(declared_contexts - pool_contexts):
        reasons.append(
            f"declared allocation context '{context}' has no corresponding "
            "allocation pool or unresolved-rule review item"
        )

    for pool in pools:
        context = (pool.allocation_context or "").strip()
        if not pool.source_pages:
            reasons.append(
                f"allocation pool '{pool.pool_key}' has no source page citation"
            )
        if context == "special_assessment":
            if pool.billing_cadence != "one_time":
                reasons.append(
                    f"special-assessment pool '{pool.pool_key}' must use "
                    "one_time billing cadence"
                )
            if pool.pool_kind != "separately_billed_special_assessment":
                reasons.append(
                    f"special-assessment pool '{pool.pool_key}' is missing the "
                    "derived separately-billed engine treatment"
                )
        elif (
            context in _RECURRING_CONTEXTS
            and pool.billing_cadence != "recurring"
        ):
            reasons.append(
                f"{context.replace('_', '-')} pool '{pool.pool_key}' must use "
                "recurring billing cadence"
            )
        elif context == "cost_center" and not (
            pool.recipient_scope or ""
        ).strip():
            reasons.append(
                f"cost-center pool '{pool.pool_key}' has no explicit recipient scope"
            )
        if context == "cost_center" and pool.allocation_method == "unknown":
            reasons.append(
                f"cost-center pool '{pool.pool_key}' has an unresolved "
                "allocation basis"
            )

    for residual in pools:
        if residual.budget_line_derivation != "residual_default":
            continue
        if residual.billing_cadence != "recurring":
            continue
        claimed = set(residual.residual_after_pool_keys or [])
        missing = sorted(
            pool.pool_key
            for pool in pools
            if pool.pool_key != residual.pool_key
            and pool.billing_cadence == residual.billing_cadence
            and pool.pool_key not in claimed
        )
        if missing:
            reasons.append(
                f"residual pool '{residual.pool_key}' does not exclude "
                f"exception pools: {', '.join(missing)}"
            )

    return CoherenceFinding(reasons=tuple(reasons))


def _has_dollar_schedule(extraction: DRESetupExtraction) -> bool:
    for pool in extraction.allocation_pools or []:
        if (pool.allocation_method or "").strip() == "specified_value":
            return True
    for unit in extraction.unit_structure.units or []:
        for pf in unit.pool_factors or []:
            if (pf.factor_type or "").strip() == "dollar_amount":
                return True
    return False


def coherence_human_review_question(
    finding: CoherenceFinding,
) -> HumanReviewQuestion:
    """High-severity review question describing coherence failures."""
    detail = "; ".join(finding.reasons) if finding.reasons else "unknown"
    return HumanReviewQuestion(
        question=(
            "Does this CC&R use more than one assessment allocation method "
            "(for example equal share for most costs and a different basis for "
            "exceptions), and do the extracted pools match that policy?"
        ),
        reason=(
            "Automated coherence check flagged a likely under-specified or "
            f"collapsed allocation structure: {detail}. Confirm or correct "
            "allocation_pools and assessment_setup.setup_type before promote."
        ),
        source_pages=[],
        severity="high",
    )


def apply_coherence_to_extraction(
    extraction: DRESetupExtraction,
    finding: CoherenceFinding,
) -> DRESetupExtraction:
    """Append a high-severity HRQ when incoherent; leave extraction otherwise."""
    if not finding.is_incoherent:
        return extraction
    questions = list(extraction.human_review_questions or [])
    marker = "Automated coherence check flagged"
    if any(marker in (q.reason or "") for q in questions):
        return extraction
    questions.append(coherence_human_review_question(finding))
    return extraction.model_copy(update={"human_review_questions": questions})


def assert_ccr_allocation_coherent(
    extraction: Optional[DRESetupExtraction],
) -> None:
    """Raise IncoherentCcrExtraction when promote must be blocked."""
    finding = assess_allocation_coherence(extraction)
    if finding.is_incoherent:
        raise IncoherentCcrExtraction(list(finding.reasons))


__all__ = [
    "CoherenceFinding",
    "IncoherentCcrExtraction",
    "assess_allocation_coherence",
    "coherence_human_review_question",
    "apply_coherence_to_extraction",
    "assert_ccr_allocation_coherent",
]
