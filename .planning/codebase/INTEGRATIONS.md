# External Integrations

**Analysis Date:** 2026-03-18

## APIs & External Services

**LLM Provider:**
- Groq API - AI reasoning for budget suggestions
  - SDK/Client: `groq` Python package via `AsyncGroq` in `backend/app/ai_implementation/pipeline/groq_client.py`
  - Auth: `GROQ_API_KEY` environment variable
  - Endpoints used: Chat completions for structured JSON responses
  - Notes: Model default is `llama-3.3-70b-versatile` from `backend/app/config.py`

**Browser-to-Backend API:**
- Internal HTTP API - SPA calls the FastAPI backend from the browser
  - Integration method: `fetch()` wrappers in `frontend/src/app/api/auth.ts` and `frontend/src/app/api/macros.ts`
  - Auth: Bearer access token for protected routes; refresh cookie for `/auth/refresh`
  - Base URL: `VITE_API_URL` or `http://localhost:8000`

**Other External APIs:**
- No payment, email, SMS, analytics, or webhook providers were found in tracked app code

## Data Storage

**Databases:**
- SQLite - Primary app data store for users, properties, suggestion runs, feedback cases, and SOP rules
  - Connection: file path from `DB_PATH` or default `backend/app/ai_implementation/data/budget_ai.db`
  - Client: SQLAlchemy session/ORM in `backend/app/ai_implementation/db/`
  - Schema: `backend/app/ai_implementation/schema.sql`

**File Storage:**
- Local filesystem temp storage - Uploaded Excel workbooks and intermediate pipeline outputs
  - Code paths: `settings.TEMP_DIR`, `tempfile`, `shutil`
  - Cleanup: FastAPI background tasks and `finally` blocks
- Committed seed storage - JSON, JSONL, and XLSX seed assets in `backend/app/ai_implementation/seed/data/`
  - Purpose: bootstrap AI history and manual validation scenarios

**Caching:**
- None found

## Authentication & Identity

**Auth Provider:**
- Custom JWT auth backed by SQLite users
  - Implementation: `backend/app/auth/` plus `get_current_user` dependency
  - Token storage: frontend keeps the access token in memory; backend stores refresh token in an httpOnly cookie on `/auth`
  - Session management: access token renewal through `/auth/refresh`

**OAuth Integrations:**
- None found

## Monitoring & Observability

**Error Tracking:**
- None found

**Analytics:**
- None found

**Logs:**
- Stdout logging via Python `logging`
  - Integration: configured in `backend/app/main.py`
  - Scope: workbook operations, auth events, AI pipeline timings, and startup activity

**Health Checks:**
- Docker Compose backend healthcheck uses `/openapi.json`

## CI/CD & Deployment

**Hosting:**
- Railway - Intended deploy target for both services
  - Deployment: Dockerfile-based builds defined in `backend/railway.toml` and `frontend/railway.toml`
  - Environment vars: expected to be injected by Railway
- Docker Compose - Local development/deployment path
  - Services: `backend`, `frontend`, and a named volume for backend data

**Frontend Serving:**
- Nginx - Static SPA hosting with client-side route fallback
  - Config: `frontend/nginx.conf`

**CI Pipeline:**
- No GitHub Actions or other CI config was found in tracked repo files

## Environment Configuration

**Development:**
- Root `.env` file is the main local secret/config surface
- Required env vars: `GROQ_API_KEY`, `JWT_SECRET_KEY`
- Common local overrides: `ALLOW_ORIGINS`, `COOKIE_SECURE=false`, `VITE_API_URL`
- Optional backend overrides: `DB_PATH`, `REPO_ROOT`, `DEFAULT_TEMPLATE_PATH`

**Staging:**
- No separate staging config or environment-specific folder was found

**Production:**
- Secrets are expected to come from the deployment platform
- Backend and frontend each build from their own Dockerfile
- Backend currently depends on a writable local filesystem for SQLite and temp artifacts

## Webhooks & Callbacks

**Incoming:**
- None found

**Outgoing:**
- None found

---
*Integration audit: 2026-03-18*
*Update when adding or removing external services*
