"""HOA-agnostic assessment calculation engine.

Pool-based math that turns an operator-approved annual budget plus a saved
DRE-derived AssessmentSetup into per-recipient (group or unit) monthly dues.

Public surface:

- Domain models (DB-mapped entity shapes): see ``models``
- Calc contracts (engine input/output): see ``schemas``

The engine consumes ``BudgetDraft.line_items.amount`` as annual by invariant
(enforced upstream at parser promotion) and derives monthly inline at the
pool boundary. Rounding happens once at the recipient level, never at the
pool level, to avoid compounding rounding error.
"""

from .models import (
    AllocationMethod,
    AllocationPool,
    AssessmentSetup,
    BudgetLinePoolMapping,
    CalcResult,
    PoolSource,
    RecipientScope,
    SetupType,
)
from .recipients import UnsupportedRecipientScope, resolve_recipients
from .schemas import (
    AppliedOverrideEntry,
    AssessmentOverride,
    BudgetLineInput,
    CalcInput,
    CalcResultSet,
    PoolAllocationResult,
    PoolDefinition,
    RecipientReference,
    RecipientSet,
    RecipientTotalResult,
    SpecialAssessmentInput,
    SpecialAssessmentRendererEvent,
)

__all__ = [
    "AllocationMethod",
    "AllocationPool",
    "AppliedOverrideEntry",
    "AssessmentOverride",
    "AssessmentSetup",
    "BudgetLineInput",
    "BudgetLinePoolMapping",
    "CalcInput",
    "CalcResult",
    "CalcResultSet",
    "PoolAllocationResult",
    "PoolDefinition",
    "PoolSource",
    "RecipientReference",
    "RecipientScope",
    "RecipientSet",
    "RecipientTotalResult",
    "SetupType",
    "SpecialAssessmentInput",
    "SpecialAssessmentRendererEvent",
    "UnsupportedRecipientScope",
    "resolve_recipients",
]
