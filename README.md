# Tristate Enterprises POP — Local Run Guide

This README explains how to run the project locally either with Docker (recommended) or directly on your machine (development mode). It covers backend (FastAPI) and frontend (Vite/React) workspaces.

Prerequisites
-------------
- Docker & Docker Compose (v2) or docker-compose
- For local (no Docker): Python 3.11+, Node 18+ and npm
- Recommended: Git, and a terminal with bash/zsh

Environment
-----------
Create a `.env` file in the repository root to provide environment variables used by the backend or compose. Example minimal `.env`:

```env
# Allow CORS origins (comma-separated) or *
ALLOW_ORIGINS=*

# Optionally provide REPO_ROOT to locate generate_budget modules (if using generate-budget endpoint)
# REPO_ROOT=/absolute/path/to/repo
```

Running with Docker (recommended)
--------------------------------
A convenience script `build_and_up.sh` is included in the repo root to build images and bring up services with compose.

Make the script executable and run it (this will build both images and start compose using your `.env` file):

```bash
chmod +x build_and_up.sh
./build_and_up.sh
```

Or run the commands manually:

```bash
# From repo root
# Build images (optional: compose can also build with `--build`)
docker build -t pop-backend:latest ./backend
docker build -t pop-frontend:latest ./frontend

# Start services with docker compose and load .env
docker compose up -d --env-file .env
# or (fallback) docker-compose
# docker-compose --env-file .env up -d
```

Check logs and status:

```bash
docker compose ps
docker compose logs -f backend
# or for a single container
docker logs -f <container-id>
```

Stop and remove containers:

```bash
docker compose down
```

Notes:
- The compose file maps backend to port `8000` and frontend to `80` by default.
- Compose includes a healthcheck for the backend hitting `/openapi.json`. You can check health with `docker inspect` or `docker compose ps`.

Running locally without Docker
-----------------------------
Backend (FastAPI)

```bash
# from repo root
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r backend/requirements.txt

# Run with Uvicorn (development)
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The backend will be available at `http://localhost:8000` and the OpenAPI docs at `http://localhost:8000/docs`.

Frontend (Vite + React)

```bash
cd frontend
npm ci
npm run dev
```

By default Vite dev server will run on port 5173 (or another port if 5173 is in use). For a production build and local static preview:

```bash
cd frontend
npm ci
npm run build
# Preview using a static server (optional):
# npm install -g serve
# serve -s dist -l 5000
```

Development tips
----------------
- If you run backend locally and want the frontend dev server to call the backend, set the appropriate CORS origins in `.env` and set the frontend API base URL in the UI (or proxy via your dev server).
- The generate-budget endpoint attempts to locate `generate_budget_pipeline.py` / `generate_budget.py` by walking the repo if `REPO_ROOT` is not set. If you packaged or relocated the pipeline code, set `REPO_ROOT` in `.env` to point to the repo root (absolute path).

Troubleshooting
---------------
- "docker: command not found": install Docker and ensure it is in your PATH.
- Ports conflicts: stop any service using ports `80` or `8000` or change the compose port mappings.
- Missing `.env`: the `build_and_up.sh` script warns but will still run the compose command with `--env-file .env` (compose may error if the file is strictly required). Create a `.env` as shown above.
- Backend import errors for the budget pipeline: if the pipeline modules are not in the repo root or are not importable, set `REPO_ROOT` to the directory that contains `generate_budget_pipeline.py` and `generate_budget.py`.

Useful commands
---------------
- View backend logs: `docker compose logs -f backend`
- Rebuild images and restart: `docker compose up -d --build --env-file .env`
- Run only backend locally (no docker): see the Backend steps above

Next steps and CI
-----------------
- Add unit and integration tests for backend functions and endpoints.
- Add a metrics endpoint (Prometheus) or push metrics to your monitoring system for production observability.

If you want, I can also:
- Add a small health-check script that waits for the backend to be healthy after `docker compose up` completes.
- Add a `Makefile` or more advanced deployment scripts.

Enjoy — tell me if you want the README tweaked or a health-wait helper added.
