"""Engine-level exception types.

Pool-math errors live with their allocators (``pools.MissingSpecifiedValue``,
``pools.DenominatorMismatchWarning`` etc.). The ones here are raised by
the orchestrator (``engine.run``) when input shape or routing is wrong.
"""

from __future__ import annotations

from typing import Any


class EngineSetupError(Exception):
    """Base class for "the engine cannot proceed with this setup" errors.

    These signal a bad/incomplete AssessmentSetup (unsupported allocation
    method, missing denominator, missing specified value) — as opposed to a
    software fault. Callers catch this base at the matrix/render boundary so
    ANY setup error degrades to the operator-review fallback matrix with a
    named reason, instead of failing the render job with an unhandled
    exception. New engine setup errors should subclass this so call sites
    don't need to enumerate concrete types.
    """


class NeedsHumanReview(Exception):
    """Raised when a budget line has no active BudgetLinePoolMapping.

    The caller surfaces this in the Review Workbench; the engine cannot
    proceed without a human-approved routing decision.
    """

    def __init__(self, budget_line: Any) -> None:
        self.budget_line = budget_line
        label = getattr(budget_line, "normalized_label", "?")
        section = getattr(budget_line, "section", "?")
        super().__init__(
            f"Budget line '{label}' (section={section}) has no active "
            "BudgetLinePoolMapping; needs human review before engine can route it"
        )


class IncompleteSetupError(EngineSetupError):
    """Raised when an AssessmentSetup references a pool method or
    denominator the engine can't fulfil — typically a draft setup that
    slipped past approval. Preflight should have caught it.
    """


class UnsupportedAllocationMethod(EngineSetupError):
    """Raised when ``AllocationPool.allocation_method`` is a value the
    engine doesn't know (e.g. an unmapped prompt-vocab value that wasn't
    collapsed by the adapter).
    """

    def __init__(self, method: str, pool_key: str) -> None:
        self.method = method
        self.pool_key = pool_key
        super().__init__(
            f"Pool '{pool_key}' uses unsupported allocation_method "
            f"'{method}'; expected equal | square_footage | "
            "ownership_percentage | specified_value"
        )
