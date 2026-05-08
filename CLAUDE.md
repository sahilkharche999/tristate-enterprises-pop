# CLAUDE.md

Project-specific guidance for Claude Code working in this repository.

## Repo layout

Monorepo with two independently-deployable services:

- `backend/` — FastAPI + Gemini AI pipeline. Own `Dockerfile`, `railway.toml`, `requirements.txt`.
- `frontend/` — Vite + React (served by nginx in production). Own `Dockerfile`, `railway.toml`, `package.json`.
- `docker-compose.yml` at the repo root is for **local dev only**. Railway does NOT use it — each subfolder deploys as its own service.

## Railway deployment

### Project and services

| Thing | Value |
|---|---|
| Project | `tristate-product-delivery` |
| Environment | `production` |
| Backend service name | `tristate-product-delivery` (auto-named from project — unfortunate but locked in) |
| Frontend service name | `frontend` |
| Backend URL | `https://tristate-product-delivery-production.up.railway.app` |
| Frontend URL | `https://frontend-production-4aaf.up.railway.app` |

### The `--path-as-root` gotcha (IMPORTANT)

`railway up` does NOT upload the current working directory. It walks up to the git root and uploads from there. Running `railway up` from inside `backend/` will upload the **entire repo**, Railpack will look at the repo root, see no Dockerfile, and fail with:

```
✖ Railpack could not determine how to build the app.
```

**The fix: deploy from the repo root using the `--path-as-root` flag with the subfolder as the positional PATH argument.** This tells Railway CLI to use that subfolder as the prefix for the uploaded archive, so the service sees only its own files.

### Deploy commands

Always run these from the **repo root** (`/Users/consultadd/Downloads/Tri-State Product Delivery`):

```bash
# Backend
railway up --path-as-root backend \
  --service tristate-product-delivery \
  --environment production \
  --ci

# Frontend
railway up --path-as-root frontend \
  --service frontend \
  --environment production \
  --ci
```

The `--ci` flag streams build logs only, then exits when the build finishes (success or failure). Drop `--ci` only if you want to attach to the long-running deploy log stream interactively.

### Railway MCP equivalent

The Railway MCP `deploy` tool does not expose `--path-as-root` directly. If you need it via MCP you have to run the CLI via Bash. Using `workspacePath` set to the subfolder via the MCP will fail with the same Railpack error above — confirmed.

### Required environment variables

#### Backend service (`tristate-product-delivery`)

| Variable | Source | Notes |
|---|---|---|
| `GEMINI_API_KEY` | Google AI Studio (https://aistudio.google.com/apikey) | Secret. Set via `railway variables --set` with shell expansion so it never appears in tool call arguments. |
| `GEMINI_MODEL` | e.g. `gemini-flash-latest` | `backend/app/config.py` has no fallback; missing value fails fast via `_require_gemini_config`. |
| `JWT_SECRET_KEY` | `openssl rand -hex 32` | Secret. |
| `ALLOW_ORIGINS` | `https://frontend-production-4aaf.up.railway.app` | Must match the frontend public URL exactly — no trailing slash. |
| `COOKIE_SECURE` | `true` | Required for HTTPS. |
| `BUDGET_STORAGE_ROOT` | `/app/app/ai_implementation/data/budget-storage` | Must live under the mounted volume path. |

A persistent volume is mounted at `/app/app/ai_implementation/data` (`tristate-product-delivery-volume`). SQLite database and budget storage both live inside it.

#### Frontend service (`frontend`)

| Variable | Notes |
|---|---|
| `VITE_API_URL` | `https://tristate-product-delivery-production.up.railway.app`. **Build-time** variable — baked into the static bundle during `pnpm run build`. Changing it requires a redeploy of the frontend, not just a restart. |

### Setting secrets without leaking them

The Railway MCP `list-variables` tool does **not** redact secrets — it returns raw values. Prefer the Railway CLI via Bash with shell expansion for setting secrets:

```bash
export GEMINI_API_KEY="$(grep '^GEMINI_API_KEY=' .env | cut -d'=' -f2-)"
railway variables \
  --service tristate-product-delivery \
  --environment production \
  --set "GEMINI_API_KEY=$GEMINI_API_KEY" \
  --skip-deploys
```

`--skip-deploys` prevents Railway from triggering an unrelated redeploy when you only want to update vars.

### Deleting a variable

CLI subcommand is `railway variable delete <KEY>` (note: singular `variable`, not `variables`):

```bash
railway variable delete GROQ_API_KEY \
  --service tristate-product-delivery \
  --environment production
```

### Blue-green behavior

Railway runs the new build in parallel with the old one. The old version keeps serving until the new one passes its healthcheck, so a failed build never takes prod down. Safe to deploy at any time.

## Local development

Use `docker-compose.yml` at the repo root:

```bash
docker compose up -d --build
```

If a frontend change doesn't appear after a rebuild, force-recreate the container (a rebuilt image is not automatically applied to a running container):

```bash
docker compose up -d --force-recreate frontend
```

This was a recurring footgun — the build command alone doesn't restart the running service.
