# Roadmap: Tri-State HOA Budget Management Platform

## Overview

The product already has a working budget-generation and AI-processing core, but it is not yet a complete operational system. This roadmap turns the current brownfield app into a usable HOA budget platform by first replacing mock HOA/settings data with real backend records, then hardening the budget and AI workflows, then adding audit/history, knowledge-base, and admin controls around that core.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Real HOA Data & Settings** - Replace mock HOA data with persistent records and make settings immediately meaningful
- [ ] **Phase 2: Budget Workflow Hardening** - Make upload-to-generated-budget flow robust and HOA-aware
- [ ] **Phase 3: AI Review Workflow** - Turn AI suggestions into a dependable review step
- [ ] **Phase 4: Sync History & Version Audit** - Replace mock history with real run and snapshot tracking
- [ ] **Phase 04.1: Draft vs Last Final Compare and Reserve Inflation Consideration (INSERTED)** - Add in-progress-vs-approved budget comparison and decide how reserve component inflation should affect budgeting
- [ ] **Phase 5: Knowledge Base** - Replace mock document browsing with real per-HOA knowledge-base access
- [ ] **Phase 6: Admin Security & Operations** - Restrict sensitive actions and harden the app for operational use
- [ ] **Phase 7: Section-Based Line Item Classification & Dynamic Parsing** - Replace keyword-based classification with section-aware parsing and flexible multi-format statement parsing
- [ ] **Phase 8: VLM-First PDF Financial Extraction and Validation Pipeline** - Keep known clean Excel on the deterministic path while routing PDFs and unknown/scanned financial documents through VLM extraction with schema and math validation
- [ ] **Phase 9: Groq to Gemini Migration** - Replace all Groq LLM calls (budget suggestions + PDF extraction) with Google Gemini API, use native structured output, single-call document processing

## Phase Details

### Phase 1: Real HOA Data & Settings
**Goal**: Replace mock HOA list and settings data with persistent backend-backed HOA records, and make saved fiscal-year changes affect the next budget run immediately.
**Depends on**: Nothing (first phase)
**Requirements**: [PORT-01, PORT-02, PORT-03, SETT-01, SETT-02, SETT-03]
**Canonical refs**: `docs/decisions/2026-03-12-frontend-backend-integration.md` - prior frontend/backend integration choices; `frontend/src/app/components/SettingsScreen.tsx` - current mock settings UI; `frontend/src/app/lib/fiscalYear.ts` - current fiscal-year timing logic
**Success Criteria** (what must be TRUE):
  1. User sees real HOA records in the workspace and settings selector instead of mock `hoaList` entries
  2. User can edit and save core HOA settings and later return to the same saved values
  3. Changing fiscal-year settings affects the next enrichment, AI timing, and final budget generation immediately
  4. Opening an HOA workspace/settings route loads data for that persistent HOA record
**Plans**: 3 plans

Plans:
- [ ] 01-01: Add HOA persistence model and backend read/write API
- [ ] 01-02: Replace mock HOA selectors, workspace loading, and settings load/save flows
- [ ] 01-03: Wire saved fiscal-year settings into enrichment and final budget generation

### Phase 2: Budget Workflow Hardening
**Goal**: Make the budget upload, enrichment, generation, and output review flow reliable for each HOA using saved settings and durable run metadata.
**Depends on**: Phase 1
**Requirements**: [BUDG-01, BUDG-02, BUDG-03]
**Canonical refs**: `docs/decisions/2026-03-12-frontend-backend-integration.md` - budget-edit flow decisions; `README.md` - current local run contract
**Success Criteria** (what must be TRUE):
  1. User can upload an income statement for a selected HOA and receive enriched editable line items
  2. User can generate a budget using current line-item edits plus saved HOA settings
  3. Generated budget output shows version/run metadata for the selected HOA
  4. Workflow errors are surfaced clearly without forcing the user into broken mock states
**Plans**: 3 plans

Plans:
- [ ] 02-01: Persist budget run metadata and associate runs to HOAs
- [ ] 02-02: Finalize generated budget output review and export behavior
- [ ] 02-03: Harden validation and failure handling for upload/enrich/generate flow

### Phase 3: AI Review Workflow
**Goal**: Make AI suggestions, application, feedback retention, and operator visibility a dependable part of the budget review flow.
**Depends on**: Phase 2
**Requirements**: [AI-01, AI-02, AI-03, ADMN-03]
**Canonical refs**: `docs/client-review-seasonality-and-reserve-logic.md` - current AI domain assumptions; `backend/app/ai_implementation/router.py` - existing AI backend contract
**Success Criteria** (what must be TRUE):
  1. User can request AI suggestions from the current HOA budget line items
  2. User can apply selected AI suggestions to editable line items before budget generation
  3. User can submit accept/modify/reject feedback and the backend retains it successfully
  4. Authorized user can inspect AI stats needed to operate the workflow safely
**Plans**: 3 plans

Plans:
- [ ] 03-01: Finalize AI suggestion request/response UI and state handling
- [ ] 03-02: Implement retained feedback submission and review UX
- [ ] 03-03: Expose AI stats and operational visibility for authorized users

### Phase 4: Sync History & Version Audit
**Goal**: Replace mock sync history and snapshot data with real upload, generation, and version history for each HOA.
**Depends on**: Phase 2
**Requirements**: [HIST-01, HIST-02, HIST-03]
**Canonical refs**: `frontend/src/app/components/SyncHistoryScreen.tsx` - current mock history UI
**Success Criteria** (what must be TRUE):
  1. User can see real upload and sync history for a selected HOA
  2. User can inspect prior generated budget versions and their metadata
  3. User can compare or reopen a prior version without losing audit visibility
**Plans**: 3 plans

Plans:
- [x] 04-01-PLAN.md — Model sync/history records and budget snapshot metadata
- [x] 04-02-PLAN.md — Replace mock Sync History screen with real data
- [x] 04-03-PLAN.md — Add compare/reopen behavior with audit-safe rules

### Phase 04.1: Draft vs Last Final Compare and Reserve Inflation Consideration (INSERTED)

**Goal:** Add an active-draft-vs-baseline compare flow in the budget workspace and persist reserve inflation as an audit-visible review overlay without changing the core budget-generation math.
**Requirements**: [REQ-04.1-CMP-01, REQ-04.1-CMP-02, REQ-04.1-RES-01, REQ-04.1-RES-02, REQ-04.1-AUD-01, REQ-04.1-RES-BASE]
**Depends on:** Phase 4
**Canonical refs**: `frontend/src/app/components/BudgetScreen.tsx` - active draft workspace entry point; `frontend/src/app/api/budgetHistory.ts` - typed draft/version transport; `backend/app/services/budget_history_service.py` - persisted draft/version history rules; `docs/client-review-seasonality-and-reserve-logic.md` - funded reserve and flat-budget assumptions
**Success Criteria** (what must be TRUE):
  1. User can launch compare from the active draft workspace, manually choose a baseline, and see the latest `Final` recommended first
  2. Compare shows changed-only line-item details with baseline/current/delta/percent/note data and reserve rows clearly labeled
  3. Reserve inflation works only as a review overlay for funded reserve component rows, requires a note when non-default, and does not mutate core generation math
  4. Generated versions and sync history visibly retain the reserve inflation assumption while Phase 4 immutable compare/reopen rules remain intact
**Plans:** 3 plans

Plans:
- [x] 04.1-01-PLAN.md — Add Wave 0 tests, compare contracts, and reserve inflation storage fields
- [ ] 04.1-02-PLAN.md — Implement backend compare options, reserve overlay logic, and audit-safe persistence
- [ ] 04.1-03-PLAN.md — Build the active-draft compare UI and verify reserve inflation visibility

### Phase 5: Knowledge Base
**Goal**: Replace mock knowledge-base folders and files with real per-HOA document metadata and access flows.
**Depends on**: Phase 1
**Requirements**: [KB-01, KB-02]
**Canonical refs**: `frontend/src/app/components/SettingsScreen.tsx` - current knowledge-base tab behavior
**Success Criteria** (what must be TRUE):
  1. User can browse real folder and file metadata for a selected HOA
  2. User can view or download linked knowledge-base files
  3. Empty and error states behave clearly when no documents exist
**Plans**: 3 plans

Plans:
- [ ] 05-01: Define document metadata model and storage abstraction
- [ ] 05-02: Replace knowledge-base mock data with backend-driven folder/file views
- [ ] 05-03: Implement view/download permissions and file access behavior

### Phase 6: Admin Security & Operations
**Goal**: Lock down sensitive admin actions and add the operational safeguards needed for reliable real-world use.
**Depends on**: Phase 3
**Requirements**: [ADMN-01, ADMN-02]
**Canonical refs**: `backend/app/ai_implementation/router.py` - current export endpoint and AI admin surface
**Success Criteria** (what must be TRUE):
  1. Sensitive export and admin actions are only available to authorized users
  2. Authorized export flow still works after access control is added
  3. Core settings, budget, and AI flows have repeatable verification coverage or smoke checks
  4. Operators have enough health and usage visibility to support the system safely
**Plans**: 3 plans

Plans:
- [ ] 06-01: Add role/permission model for export and admin actions
- [ ] 06-02: Harden export endpoint behavior and audit access
- [ ] 06-03: Add smoke verification and operational checks for core flows

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 04.1 → 5 → 6 → 7 → 8 → 9

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Real HOA Data & Settings | 0/3 | Not started | - |
| 2. Budget Workflow Hardening | 0/3 | Not started | - |
| 3. AI Review Workflow | 0/3 | Not started | - |
| 4. Sync History & Version Audit | 3/3 | Complete | 2026-03-19 |
| 04.1 Draft vs Last Final Compare and Reserve Inflation Consideration | 1/3 | In progress | - |
| 5. Knowledge Base | 0/3 | Not started | - |
| 6. Admin Security & Operations | 0/3 | Not started | - |
| 7. Section-Based Line Item Classification & Dynamic Parsing | 2/3 | In Progress|  |
| 8. VLM-First PDF Financial Extraction and Validation Pipeline | 0/3 | Not started | - |
| 9. Groq to Gemini Migration | 0/3 | Not started | - |

### Phase 7: Section-Based Line Item Classification & Dynamic Parsing

**Goal:** Replace keyword-based reserve/operating classification with a section-aware state machine that reads the actual structure of uploaded income statements, add header-scanning for flexible column detection, .xls format support, and text-based PDF parsing.
**Requirements**: [PARSE-01, PARSE-02, PARSE-03, PARSE-04, PARSE-05, PARSE-06, PARSE-07, PARSE-08, PARSE-09, PARSE-10, PARSE-11]
**Depends on:** Phase 6
**Canonical refs**: `backend/app/services/budget_history_service.py` - current parsing and classification code; `backend/app/generate_budget_pipeline.py` - enrichment pipeline with hardcoded columns; `backend/app/ai_implementation/pipeline/feature_engineering.py` - read_only logic
**Success Criteria** (what must be TRUE):
  1. Items are classified by which section they appear in (income/operating/reserve), not by label text
  2. "90000 - Reserve - Allocation/Transfer" under Operating Expense is classified as operating and editable
  3. Column positions are auto-detected from headers, not hardcoded
  4. .xls (Excel 97-2003) and text-based PDF income statements parse correctly
  5. Scanned PDFs return a clear error message
**Plans:** 2/3 plans executed

Plans:
- [x] 07-01-PLAN.md — Core parser module with section state machine, column detection, multi-format readers, and TDD tests
- [x] 07-02-PLAN.md — Wire parser into budget_history_service, generate_budget_pipeline, feature_engineering, and upload flow
- [ ] 07-03-PLAN.md — Integration tests and human verification of classification correctness

### Phase 8: VLM-First PDF Financial Extraction and Validation Pipeline

**Goal:** Add a second financial-document ingestion path that keeps known clean Excel files on the existing deterministic parser, adds shared schema/classification/confidence hardening for Excel-family inputs, and routes PDFs, scanned statements, and unknown visual layouts through a VLM-first extraction pipeline with strict schema enforcement and post-extraction financial validation.
**Requirements**: TBD
**Depends on:** Phase 7
**Canonical refs**: `backend/app/services/income_statement_parser.py` - current deterministic parser and routing entry point; `backend/app/ai_implementation/pipeline/groq_client.py` - existing Groq client integration; `backend/app/config.py` - model/provider configuration; `backend/app/services/budget_history_service.py` - upload parsing integration; `Example Income Statements/` - real PDF corpus that exposed current parsing failures; `.planning/research.md` - financial document extraction research and architecture notes
**Success Criteria** (what must be TRUE):
  1. Known clean Excel uploads continue to use the deterministic parser path without regressing current behavior
  2. Non-clean Excel layouts go through shared deterministic hardening: family-aware parsing, canonical normalization, and fail-closed confidence checks
  3. PDFs, scanned statements, and unknown financial layouts are routed to a VLM-first extraction flow that produces a canonical structured schema
  4. VLM extraction output is enforced through schema validation and retries or fails closed when the structure is invalid
  5. Extracted financial statements pass subtotal and accounting-style math checks or return a clear review-needed error instead of silently bad line items
  6. The upload workflow surfaces actionable error/review states for low-confidence or invalid Excel/PDF extraction results
**Plans:** 3 plans

Plans:
- [ ] 08-01: Define canonical multi-format extraction schema, routing, provider interface, and deterministic Excel hardening boundaries
- [ ] 08-02: Implement VLM-first PDF extraction with page rendering, schema enforcement, and shared validation/confidence logic
- [ ] 08-03: Integrate upload/review behavior, deterministic Excel hardening hooks, and regression verification across PDF and Excel variants

### Phase 9: Groq to Gemini Migration

**Goal:** Replace all Groq API calls (budget suggestion generation and PDF financial document extraction) with Google Gemini API. Use Gemini's native structured output (Pydantic schema enforcement at decode level) to eliminate validation errors. Send full document text in a single call instead of per-page splitting. Keep pdfplumber text extraction, all Pydantic models, validation logic, and existing API contracts unchanged — only swap the LLM provider layer.
**Requirements**: [D-01, D-02, D-03, D-04, D-05, D-06, D-07, D-08, D-09, D-10, D-11, D-12, D-13]
**Depends on:** Phase 8
**Success Criteria** (what must be TRUE):
  1. All LLM calls use Gemini API via google-genai SDK, not Groq
  2. Controlled generation (response_schema) enforces Pydantic schema at decode level
  3. PDF extraction sends full document (text + images) in a single API call
  4. No references to Groq SDK remain in any Python file or requirements.txt
  5. Full test suite passes at baseline (109/110)
**Plans:** 3 plans

Plans:
- [x] 09-01-PLAN.md — Config, requirements, and Gemini llm_client.py wrapper
- [ ] 09-02-PLAN.md — Update consumer files and rewrite PDF extraction for single-call hybrid ingestion
- [ ] 09-03-PLAN.md — Rewrite tests for Gemini mocks and verify full suite
