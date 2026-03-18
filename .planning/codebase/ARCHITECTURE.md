# Architecture

**Analysis Date:** 2026-03-18

## Pattern Overview

**Overall:** Full-stack monorepo with a monolithic FastAPI backend, a client-heavy React SPA, and an embedded AI suggestion subsystem.

**Key Characteristics:**
- Backend request handling is stateless per HTTP call, but the app persists AI/auth state in a local SQLite database
- Excel workbooks are uploaded, processed on disk, and transformed into JSON or regenerated workbook previews
- AI suggestions use a staged pipeline: feature engineering, case-based retrieval, optional ML, and Groq LLM reasoning
- Frontend feature state is mostly held in React component state rather than persisted server-side
- No shared schema package exists between frontend and backend; the frontend mirrors backend payload shapes manually

## Layers

**Frontend Shell Layer:**
- Purpose: Router, auth bootstrap, guarded navigation, and screen composition
- Contains: `frontend/src/main.tsx`, `frontend/src/app/App.tsx`, `frontend/src/app/routes.tsx`, `frontend/src/app/context/AuthContext.tsx`
- Depends on: Frontend API client layer and screen components
- Used by: Browser entry point

**Frontend Feature Layer:**
- Purpose: HOA workspace flows, upload/generate flows, AI suggestion UI, settings screens, and local view state
- Contains: `frontend/src/app/components/*.tsx`, `frontend/src/app/data/mockData.ts`, `frontend/src/app/lib/*.ts`
- Depends on: API wrappers, auth context, and UI primitives
- Used by: Router-defined pages

**Backend API Boundary Layer:**
- Purpose: App startup, CORS, auth boundaries, FastAPI routes, and request-response validation
- Contains: `backend/app/main.py`, `backend/app/auth/`, `backend/app/routers/macros.py`, `backend/app/ai_implementation/router.py`, `backend/app/models/`, `backend/app/auth/models.py`
- Depends on: Backend service and persistence layers
- Used by: Browser clients and any external HTTP caller

**Workbook Service Layer:**
- Purpose: Deterministic workbook parsing, macro replacements, budget generation orchestration, and temp file handling
- Contains: `backend/app/services/macros_service.py`, `backend/app/generate_budget.py`, `backend/app/generate_budget_pipeline.py`
- Depends on: `openpyxl`, filesystem temp storage, and backend settings
- Used by: Macro and budget endpoints

**AI Pipeline Layer:**
- Purpose: Feature engineering, CBR retrieval, optional CatBoost inference, Groq prompting, and feedback retention
- Contains: `backend/app/ai_implementation/pipeline/`, `backend/app/ai_implementation/models/`, `backend/app/ai_implementation/seed/`
- Depends on: SQLite session layer, Groq API, seed data, and backend settings
- Used by: `/ai/suggest`, `/ai/feedback`, `/ai/stats`, and startup seeding

**Persistence Layer:**
- Purpose: SQLite schema management, ORM models, sessions, and data bootstrapping
- Contains: `backend/app/ai_implementation/database.py`, `backend/app/ai_implementation/db/session.py`, `backend/app/ai_implementation/db/models.py`, `backend/app/ai_implementation/schema.sql`
- Depends on: Local filesystem and SQLAlchemy
- Used by: Auth flows and AI pipeline flows

## Data Flow

**Authentication Flow:**
1. Frontend posts credentials to `/auth/login` using `frontend/src/app/api/auth.ts`
2. `backend/app/auth/router.py` validates the user against SQLite and returns an access token
3. Backend also sets an httpOnly refresh cookie scoped to `/auth`
4. `AuthContext` stores the access token in memory and injects it into `frontend/src/app/api/macros.ts`
5. Protected backend routes require `get_current_user` from `backend/app/auth/dependencies.py`

**Budget Upload and Generation Flow:**
1. User uploads an Excel workbook from `BudgetScreen`
2. Frontend sends multipart form data to `/macros/generate-budget`
3. Backend saves the file to a temp path, optionally writes percent changes back into column `AM`, and runs `BudgetPipeline`
4. Backend reads enriched workbook data and an optional budget preview into JSON tables
5. Frontend converts the enriched sheet into `LineItem` objects via `parseEnrichedResponse`
6. User edits line items locally, then resubmits the original uploaded file plus new percent changes to regenerate the budget

**AI Suggestion Flow:**
1. Frontend converts editable line items into `AILineItemInput[]` and posts to `/ai/suggest`
2. `run_pipeline()` in `backend/app/ai_implementation/pipeline/orchestrator.py` enriches items and computes macro context
3. CBR retrieval scans historical feedback cases from SQLite
4. Optional CatBoost predictions run if enough feedback exists and `CATBOOST_ENABLED=true`
5. Groq LLM passes generate and revise suggestions
6. Backend persists `SuggestionRun` and `FeedbackCase` rows, then returns suggestion payloads to the frontend
7. User feedback later posts to `/ai/feedback` and is retained in SQLite

**State Management:**
- Frontend auth state is in React context and access tokens stay in memory only
- Uploaded workbook `File` objects, parsed line items, and AI responses live in component state in `BudgetScreenWrapper`
- Backend durable state lives in the SQLite database and seed/model files under `backend/app/ai_implementation/data/`

## Key Abstractions

**Line Item / Sheet Table:**
- Purpose: Bridge workbook rows to frontend-editable budget data
- Examples: `LineItem` in `frontend/src/app/data/mockData.ts`, `SheetTable` and `GenerateBudgetResponse` in `frontend/src/app/api/macros.ts`
- Pattern: DTO-style typed objects moved across the upload, enrichment, and generation flow

**Workbook Macro Service:**
- Purpose: Encapsulate Excel operations behind deterministic Python helpers
- Examples: `payment_search_format`, `write_percent_changes_by_label`, `remove_protection_return_bytes`
- Pattern: Service module with pure-ish functions over workbook paths

**Suggestion Run / Feedback Case:**
- Purpose: Persist AI output, user decisions, and training signals
- Examples: `SuggestionRun`, `FeedbackCase`, `Property`, `SOPRule`, `User` in `backend/app/ai_implementation/db/models.py`
- Pattern: SQLAlchemy ORM entities backed by SQLite tables

**Auth Context + Token Accessor:**
- Purpose: Connect backend bearer auth to the SPA without storing access tokens in localStorage
- Examples: `AuthProvider` and `setTokenAccessor()`
- Pattern: React context plus module-level token accessor callback

## Entry Points

**Backend Server:**
- Location: `backend/app/main.py`
- Triggers: Uvicorn startup, Docker container launch
- Responsibilities: Initialize DB, run seed logic, mount auth/macros/AI routers, configure CORS

**Frontend Browser App:**
- Location: `frontend/src/main.tsx`
- Triggers: Browser loading `frontend/index.html`
- Responsibilities: Mount React app and global styles

**Router Definition:**
- Location: `frontend/src/app/routes.tsx`
- Triggers: Client-side navigation
- Responsibilities: Map URLs to login, signup, workspace, HOA detail, settings, and sync history screens

**Seed Script:**
- Location: `backend/app/ai_implementation/seed/seed_database.py`
- Triggers: Manual execution and startup helper calls
- Responsibilities: Build initial property/case/SOP data from committed seed files

## Error Handling

**Strategy:** Catch exceptions at HTTP boundaries, return `HTTPException` responses, and keep heavy processing inside service functions and background-thread helpers.

**Patterns:**
- Workbook endpoints translate `ValueError` into `400` and most unexpected failures into sanitized `500` responses
- The AI endpoints log exceptions and surface the underlying message in some cases, especially in `/ai/suggest`
- Frontend API wrappers throw `{ status, message }` objects rather than custom `Error` instances
- `frontend/src/app/api/macros.ts` performs a hard redirect to `/` on `401` responses
- Temp directories are cleaned in `finally` blocks or FastAPI background tasks

## Cross-Cutting Concerns

**Logging:**
- Backend uses Python `logging` configured in `backend/app/main.py`
- Service functions log start/end markers and durations for workbook and AI steps

**Validation:**
- Pydantic models validate auth and macro payloads on the backend
- Frontend does lightweight field validation in forms and manual parsing in `parseEnrichedResponse`

**Authentication:**
- Access token: bearer header on protected routes
- Refresh token: httpOnly cookie on `/auth`
- Frontend route protection: `ProtectedRoute`

**Filesystem Use:**
- Uploaded workbooks are saved under `settings.TEMP_DIR`
- SQLite, seed files, seasonality data, and optional CatBoost artifacts live on local disk

**Startup Behavior:**
- DB schema init and seeding happen during FastAPI lifespan startup
- Startup failures are logged as warnings and do not abort app boot

---
*Architecture analysis: 2026-03-18*
*Update when major patterns or flows change*
