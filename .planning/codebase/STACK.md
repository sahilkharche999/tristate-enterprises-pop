# Technology Stack

**Analysis Date:** 2026-03-18

## Languages

**Primary:**
- Python 3.11 - Backend API, Excel processing, authentication, and AI pipeline code under `backend/app/`
- TypeScript - Frontend SPA code under `frontend/src/app/`

**Secondary:**
- CSS and HTML - Styling and static shell files under `frontend/src/styles/`, `frontend/index.html`, and `frontend/nginx.conf`
- SQL - SQLite schema in `backend/app/ai_implementation/schema.sql`
- JSON, JSONL, and XLSX - Seed data and workbook inputs under `backend/app/ai_implementation/seed/data/`
- TOML and Docker Compose YAML - Deployment/runtime config in `backend/railway.toml`, `frontend/railway.toml`, and `docker-compose.yml`

## Runtime

**Environment:**
- Python 3.11+ for local backend development; `backend/Dockerfile` uses `python:3.11-slim`
- Node.js 20 for Docker builds; local docs mention Node 18+ as the minimum frontend runtime
- Browser runtime for the React SPA
- Nginx for serving the production frontend container

**Package Manager:**
- `pip` for backend dependencies from `backend/requirements.txt`
- `pnpm` for frontend installs in Docker; `frontend/pnpm-lock.yaml` is present
- Local README instructions still mention `npm`, so local tooling guidance is mixed even though the checked-in lockfile is `pnpm`

## Frameworks

**Core:**
- FastAPI - HTTP API, route registration, and dependency injection in `backend/app/main.py`
- SQLAlchemy 2.x - ORM and session management for the AI pipeline SQLite database
- Pydantic / pydantic-settings - request-response schemas and environment-backed settings
- React 18 - Frontend UI runtime
- React Router 7 - Browser routing for login, workspace, HOA, and settings flows
- Tailwind CSS 4 + Radix UI - Styling tokens and low-level UI primitives in `frontend/src/styles/` and `frontend/src/app/components/ui/`

**Testing:**
- No backend or frontend test framework is configured in tracked app code

**Build/Dev:**
- Uvicorn - Backend ASGI server
- Vite 6 - Frontend bundler and dev server
- `@vitejs/plugin-react` - React transform support
- Docker and Docker Compose - Local multi-service runtime
- Railway Dockerfile deploys - Hosted deployment path for both backend and frontend

## Key Dependencies

**Critical:**
- `openpyxl` - Reads and mutates uploaded Excel workbooks in `backend/app/services/macros_service.py`
- `groq` - LLM API client used by the AI suggestion pipeline in `backend/app/ai_implementation/pipeline/groq_client.py`
- `sqlalchemy` - SQLite persistence for users, suggestion runs, feedback cases, and SOP rules
- `python-jose`, `passlib`, and `bcrypt` - Custom JWT auth and password hashing in `backend/app/auth/`
- `catboost`, `scikit-learn`, `numpy`, and `pandas` - Optional ML training and inference support for the AI subsystem

**Infrastructure:**
- `python-multipart` - Multipart upload handling for workbook endpoints
- `xlsx-js-style` - Frontend workbook export and spreadsheet formatting support
- `sonner` and `lucide-react` - User notifications and iconography in the SPA

## Configuration

**Environment:**
- Root `.env` file for backend and local compose settings
- Example values in `.env.example`
- Backend settings centralized in `backend/app/config.py`
- Frontend API base URL resolved from `VITE_API_URL` in `frontend/src/app/api/config.ts`

**Build:**
- `frontend/vite.config.ts` - Frontend aliasing and asset handling
- `backend/Dockerfile` and `frontend/Dockerfile` - Production container build definitions
- `docker-compose.yml` - Local service wiring and shared environment values
- `backend/railway.toml` and `frontend/railway.toml` - Railway deploy metadata

**Critical Variables:**
- `GROQ_API_KEY` - Enables Groq-backed AI reasoning
- `JWT_SECRET_KEY` - Signs access and refresh tokens
- `ALLOW_ORIGINS` - Controls CORS for browser clients
- `COOKIE_SECURE` - Controls refresh cookie security
- `DB_PATH`, `REPO_ROOT`, and `DEFAULT_TEMPLATE_PATH` - Optional backend overrides
- `VITE_API_URL` - Frontend build-time backend URL

## Platform Requirements

**Development:**
- Python 3.11+ and Node.js 18+/20
- Docker optional but recommended for integrated local runs
- Writable local filesystem for temp uploads and SQLite files

**Production:**
- Dockerized backend and frontend services
- Backend currently assumes a single writable filesystem for SQLite, temp files, and optional CatBoost artifacts
- Frontend is built to static files and served behind Nginx
- Railway is the intended hosted deployment target based on checked-in config

---
*Stack analysis: 2026-03-18*
*Update after major dependency or deployment changes*
