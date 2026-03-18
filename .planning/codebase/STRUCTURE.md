# Codebase Structure

**Analysis Date:** 2026-03-18

## Directory Layout

```text
Tri-State Product Delivery/
|-- .agents/                         # Local agent assets and project-specific automation metadata
|-- .claude/                         # Legacy Claude-oriented project tooling
|-- .planning/codebase/              # Generated GSD codebase map documents
|-- backend/                         # FastAPI backend, workbook logic, AI pipeline, deploy config
|   |-- app/
|   |   |-- ai_implementation/       # AI pipeline, SQLite models, seed data, schema
|   |   |-- auth/                    # JWT auth routes, models, and helpers
|   |   |-- models/                  # Macro response schemas
|   |   |-- routers/                 # Non-AI FastAPI routers
|   |   `-- services/                # Workbook macro service functions
|   |-- Dockerfile                   # Backend container build
|   |-- README.md                    # Backend run guide
|   `-- requirements.txt             # Backend Python dependencies
|-- docs/                            # Design notes, decision records, and architecture writeups
|-- frontend/                        # React/Vite frontend and deploy config
|   |-- src/
|   |   |-- app/
|   |   |   |-- api/                # Browser API wrappers
|   |   |   |-- components/         # Screens and UI components
|   |   |   |-- context/            # React auth context
|   |   |   |-- data/               # Mock domain data and shared interfaces
|   |   |   `-- lib/                # Frontend utilities
|   |   `-- styles/                 # Global styles, theme tokens, fonts
|   |-- Dockerfile                   # Frontend multi-stage build
|   |-- package.json                 # Frontend manifest
|   `-- vite.config.ts               # Frontend build config
|-- guidelines/                      # Project guidance documents
|-- mockdata/                        # Legacy or auxiliary sample data
|-- openspec/                        # OpenSpec change artifacts
|-- venv/                            # Local root virtual environment
|-- README.md                        # Root local run guide
`-- docker-compose.yml               # Local service orchestration
```

## Directory Purposes

**backend/**
- Purpose: All server-side code and deployment config
- Contains: FastAPI app code, workbook processing, AI pipeline code, auth, Docker config
- Key files: `backend/app/main.py`, `backend/app/routers/macros.py`, `backend/app/services/macros_service.py`, `backend/app/ai_implementation/router.py`
- Subdirectories: `app/auth/`, `app/services/`, `app/routers/`, `app/ai_implementation/`

**backend/app/ai_implementation/**
- Purpose: AI suggestion subsystem and persistence layer
- Contains: Pipeline stages, ORM models, schema, DB/session utilities, seed/import scripts, runtime data
- Key files: `backend/app/ai_implementation/pipeline/orchestrator.py`, `backend/app/ai_implementation/db/models.py`, `backend/app/ai_implementation/schema.sql`
- Subdirectories: `db/`, `models/`, `pipeline/`, `seed/`, `data/`

**frontend/src/app/**
- Purpose: All typed browser application code
- Contains: Route definitions, page components, API wrappers, auth context, utilities, mock data
- Key files: `frontend/src/app/routes.tsx`, `frontend/src/app/context/AuthContext.tsx`, `frontend/src/app/api/macros.ts`
- Subdirectories: `api/`, `components/`, `context/`, `data/`, `lib/`

**frontend/src/styles/**
- Purpose: Global theme, Tailwind, and font setup
- Contains: CSS entrypoint, theme variables, Tailwind import file, font declarations
- Key files: `frontend/src/styles/index.css`, `frontend/src/styles/theme.css`, `frontend/src/styles/tailwind.css`
- Subdirectories: None

**docs/**
- Purpose: Human-written architecture notes and change decisions
- Contains: AI design notes, client review docs, dated decision records
- Key files: `docs/AI_PIPELINE_ARCHITECTURE.md`, `docs/client-review-seasonality-and-reserve-logic.md`, `docs/decisions/2026-03-12-frontend-backend-integration.md`
- Subdirectories: `decisions/`, `superpowers/`

**guidelines/**
- Purpose: Project-specific written guidance
- Contains: Markdown docs rather than runtime code
- Key files: `guidelines/Guidelines.md`
- Subdirectories: None

## Key File Locations

**Entry Points:**
- `backend/app/main.py` - FastAPI startup, router registration, lifespan init
- `frontend/src/main.tsx` - Browser React mount
- `frontend/src/app/routes.tsx` - Client-side route map
- `docker-compose.yml` - Local backend/frontend service orchestration

**Configuration:**
- `backend/app/config.py` - Backend environment-backed settings
- `.env.example` - Required environment variables and deploy notes
- `frontend/src/app/api/config.ts` - Frontend API base URL resolution
- `frontend/vite.config.ts` - Vite aliasing and asset config
- `backend/railway.toml` and `frontend/railway.toml` - Railway deployment metadata

**Core Logic:**
- `backend/app/routers/macros.py` - Workbook and budget endpoints
- `backend/app/services/macros_service.py` - Workbook parsing and mutation functions
- `backend/app/generate_budget.py` and `backend/app/generate_budget_pipeline.py` - Budget generation pipeline
- `backend/app/ai_implementation/pipeline/` - AI pipeline stages
- `frontend/src/app/components/BudgetScreen.tsx` - Upload, edit, and AI workflow UI
- `frontend/src/app/components/BudgetScreenWrapper.tsx` - Local orchestration and generated-budget state

**Authentication:**
- `backend/app/auth/router.py` - Signup, login, refresh, logout, current-user endpoints
- `backend/app/auth/dependencies.py` - Bearer token enforcement
- `frontend/src/app/context/AuthContext.tsx` - Frontend session bootstrap and refresh behavior

**Testing:**
- No first-party backend test files found under tracked app code
- No first-party frontend `*.test.*` or `*.spec.*` files found under tracked app code

**Documentation:**
- `README.md` - Root run guide
- `backend/README.md` and `backend/README-ARCHITECTURE.md` - Backend-specific documentation
- `docs/` - Deeper design and decision documents

## Naming Conventions

**Files:**
- Python modules use `snake_case.py` such as `macros_service.py`, `seed_database.py`, and `groq_client.py`
- React screen and feature components use `PascalCase.tsx` such as `BudgetScreen.tsx`, `LoginScreen.tsx`, and `HOAWorkspace.tsx`
- Shared frontend utility and API files use `camelCase.ts` or descriptive lowercase names such as `mockData.ts`, `statusColors.ts`, `config.ts`
- Low-level UI primitives under `frontend/src/app/components/ui/` use lowercase or kebab-case filenames such as `alert-dialog.tsx`, `button.tsx`, and `tabs.tsx`

**Directories:**
- App directories are lowercase and feature-oriented: `auth`, `routers`, `services`, `api`, `components`, `context`, `lib`
- AI pipeline subdirectories are role-oriented: `db`, `models`, `pipeline`, `seed`, `data`

**Special Patterns:**
- Python package markers use `__init__.py`
- Backend modules often follow `router.py`, `models.py`, `config.py`, `database.py`, or `session.py`
- Frontend wrappers and container components use suffixes like `*Wrapper.tsx`, `*Screen.tsx`, and `*Context.tsx`

## Where to Add New Code

**New Backend API Feature:**
- Route definitions: `backend/app/routers/` for standard workbook/auth-adjacent features
- AI-specific endpoints: `backend/app/ai_implementation/router.py`
- Business logic: `backend/app/services/` or `backend/app/ai_implementation/pipeline/`

**New Frontend Screen or Workflow:**
- Screen component: `frontend/src/app/components/`
- Shared API wrapper: `frontend/src/app/api/`
- Shared utility: `frontend/src/app/lib/`
- Shared domain types or temporary mock data: `frontend/src/app/data/`

**New Persistence or Schema Work:**
- ORM model updates: `backend/app/ai_implementation/db/models.py`
- Session/engine changes: `backend/app/ai_implementation/db/session.py`
- Schema changes: `backend/app/ai_implementation/schema.sql`

**New Tests:**
- No established home exists today
- If tests are introduced, choose a consistent scheme deliberately rather than mimicking ad hoc files

## Special Directories

**`backend/app/ai_implementation/data/`:**
- Purpose: Runtime SQLite database, seasonality JSON, and optional CatBoost artifacts
- Source: App startup, AI training, and committed data files
- Committed: Partially; database files are ignored, some seed assets are committed

**`backend/app/ai_implementation/seed/data/`:**
- Purpose: Committed sample workbooks, JSON casebases, JSONL feedback, and SOP notes for seeding
- Source: Manual client/seed imports
- Committed: Yes

**`frontend/dist/`:**
- Purpose: Built frontend assets
- Source: `pnpm run build`
- Committed: Present locally but ignored in `.gitignore`

**`backend/.venv/`, `frontend/node_modules/`, `venv/`:**
- Purpose: Local dependency/install artifacts
- Source: Developer environment setup
- Committed: No; ignored

**`.planning/codebase/`:**
- Purpose: Generated codebase documentation for GSD planning workflows
- Source: `$gsd-map-codebase`
- Committed: Intended to be committed when `commit_docs` is enabled

---
*Structure analysis: 2026-03-18*
*Update when directory layout or placement rules change*
