"""DRE-extraction Gemini prompts.

Each prompt's text lives in a sibling .txt file, loaded at import time
and exposed as a module-level constant alongside its SHA-256 digest.
The text files are the source of truth — diffing them against
``openspec/changes/.../prompts.md`` is the audit trail.

The SHA-256 is computed once at module load; ``DREExtractionRun`` rows
record it so prompt drift is observable.
"""

from .dre_setup_extractor import (
    PROMPT_TEXT as DRE_SETUP_EXTRACTOR_PROMPT,
    PROMPT_SHA256 as DRE_SETUP_EXTRACTOR_PROMPT_SHA256,
    PROMPT_VERSION as DRE_SETUP_EXTRACTOR_PROMPT_VERSION,
)
from .budget_pool_mapper import (
    PROMPT_TEXT as BUDGET_POOL_MAPPER_PROMPT,
    PROMPT_SHA256 as BUDGET_POOL_MAPPER_PROMPT_SHA256,
    PROMPT_VERSION as BUDGET_POOL_MAPPER_PROMPT_VERSION,
)
from .assessment_mapping_review_assistant import (
    PROMPT_TEXT as ASSESSMENT_MAPPING_REVIEW_ASSISTANT_PROMPT,
    PROMPT_SHA256 as ASSESSMENT_MAPPING_REVIEW_ASSISTANT_PROMPT_SHA256,
    PROMPT_VERSION as ASSESSMENT_MAPPING_REVIEW_ASSISTANT_PROMPT_VERSION,
)

__all__ = [
    "DRE_SETUP_EXTRACTOR_PROMPT",
    "DRE_SETUP_EXTRACTOR_PROMPT_SHA256",
    "DRE_SETUP_EXTRACTOR_PROMPT_VERSION",
    "BUDGET_POOL_MAPPER_PROMPT",
    "BUDGET_POOL_MAPPER_PROMPT_SHA256",
    "BUDGET_POOL_MAPPER_PROMPT_VERSION",
    "ASSESSMENT_MAPPING_REVIEW_ASSISTANT_PROMPT",
    "ASSESSMENT_MAPPING_REVIEW_ASSISTANT_PROMPT_SHA256",
    "ASSESSMENT_MAPPING_REVIEW_ASSISTANT_PROMPT_VERSION",
]
