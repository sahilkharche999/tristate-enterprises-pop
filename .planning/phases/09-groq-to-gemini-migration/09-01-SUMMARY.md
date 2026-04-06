---
phase: 09-groq-to-gemini-migration
plan: 01
subsystem: api
tags: [gemini, google-genai, llm, structured-output, controlled-generation, multimodal, pdf-extraction]

# Dependency graph
requires:
  - phase: 08-vlm-first-pdf-financial-extraction-and-validation-pipeline
    provides: pdf extraction pipeline, groq_client.py wrapper interface, provider routing architecture
provides:
  - Gemini LLM client wrapper (llm_client.py) with call_llm, call_llm_vision, get_llm_client
  - GEMINI_API_KEY and GEMINI_MODEL config settings
  - google-genai>=1.0.0 dependency replacing groq>=0.12.0
affects: [09-02, 09-03, llm_pass1, llm_pass2, income_statement_parser, pdf_vlm_extractor]

# Tech tracking
tech-stack:
  added: [google-genai>=1.0.0]
  patterns:
    - Gemini controlled generation via response_schema=PydanticModel in GenerateContentConfig
    - system_instruction in GenerateContentConfig (not in contents list)
    - Module-level singleton _gemini_client with lazy initialization
    - Rate-limit-only retry with exponential backoff [2,4,8]s on errors.ClientError code 429

key-files:
  created:
    - backend/app/ai_implementation/pipeline/llm_client.py
  modified:
    - backend/app/config.py
    - backend/requirements.txt

key-decisions:
  - "Single GEMINI_MODEL config replaces MODEL_NAME + DOCUMENT_VLM_MODEL (D-02)"
  - "Controlled generation (response_schema) eliminates validation-retry loop and null-label workarounds (D-04, D-05, D-06)"
  - "groq_client.py kept in place — deletion deferred to Plan 02 after consumers updated (D-09)"

patterns-established:
  - "Gemini client: get_llm_client() singleton, call_llm() for text, call_llm_vision() for hybrid text+image"
  - "Multimodal message format: {type: image, data: bytes, mime_type: image/png} in content list"
  - "System prompt in system_instruction field, not as role=system in contents"

requirements-completed: [D-01, D-02, D-04, D-05, D-06, D-07, D-08, D-09]

# Metrics
duration: 18min
completed: 2026-04-05
---

# Phase 9 Plan 01: Gemini LLM Client Wrapper and Config Migration Summary

**Gemini client wrapper (llm_client.py) with call_llm, call_llm_vision, get_llm_client using native controlled generation (response_schema) replacing Groq SDK**

## Performance

- **Duration:** 18 min
- **Started:** 2026-04-05T00:00:00Z
- **Completed:** 2026-04-05T00:18:00Z
- **Tasks:** 2
- **Files modified:** 3 (config.py, requirements.txt, llm_client.py new)

## Accomplishments

- Replaced GROQ_API_KEY + MODEL_NAME with GEMINI_API_KEY + GEMINI_MODEL in config.py, updating the AI Pipeline comment to "AI Pipeline (Gemini)"
- Swapped groq>=0.12.0 for google-genai>=1.0.0 in requirements.txt
- Created llm_client.py with three exported functions: get_llm_client (singleton), call_llm (text-only), call_llm_vision (hybrid text+image multimodal)
- Implemented Gemini controlled generation: response_schema=PydanticModel + response_mime_type="application/json" guarantees schema-valid output without validation-retry loops
- Rate-limit retry with [2,4,8]s exponential backoff on errors.ClientError code 429 only

## Task Commits

1. **Task 1: Update config.py and requirements.txt for Gemini** - `fe5d27f` (chore)
2. **Task 2: Create llm_client.py with Gemini wrapper functions** - `6ad6339` (feat)

## Files Created/Modified

- `backend/app/ai_implementation/pipeline/llm_client.py` - New Gemini client wrapper (get_llm_client, call_llm, call_llm_vision)
- `backend/app/config.py` - GROQ_API_KEY + MODEL_NAME replaced with GEMINI_API_KEY + GEMINI_MODEL
- `backend/requirements.txt` - groq>=0.12.0 removed, google-genai>=1.0.0 added

## Decisions Made

- Kept groq_client.py in place (not deleted) — consumer files still import from it; deletion deferred to Plan 02 once all consumers are updated to use llm_client.py
- The worktree's config.py did not have DOCUMENT_VLM_PROVIDER, DOCUMENT_VLM_MODEL, DOCUMENT_VLM_ENABLED, DOCUMENT_VLM_MAX_PAGES, DOCUMENT_VLM_MAX_RETRIES (those fields exist only in the main project branch at this point), so only GROQ_API_KEY and MODEL_NAME required replacement

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- The worktree config.py is missing the VLM-specific fields (DOCUMENT_VLM_PROVIDER, DOCUMENT_VLM_MODEL, DOCUMENT_VLM_ENABLED, etc.) that the plan mentioned keeping unchanged. These fields do not exist in the worktree's HEAD, so no action was required. The plan's acceptance criteria (no GROQ_API_KEY/MODEL_NAME/DOCUMENT_VLM_PROVIDER/DOCUMENT_VLM_MODEL) is satisfied either way.

## User Setup Required

**GEMINI_API_KEY environment variable must be set before the app can make LLM calls.** Add to `.env` file in backend directory:

```
GEMINI_API_KEY=your-google-gemini-api-key-here
```

Get a key from: https://aistudio.google.com/app/apikey

## Next Phase Readiness

- llm_client.py is importable and exports all three required functions
- Config has GEMINI_API_KEY and GEMINI_MODEL, no Groq references
- Plan 02 can now update consumer files (llm_pass1.py, llm_pass2.py, income_statement_parser.py, pdf_vlm_extractor.py) to import from llm_client instead of groq_client

---
*Phase: 09-groq-to-gemini-migration*
*Completed: 2026-04-05*
