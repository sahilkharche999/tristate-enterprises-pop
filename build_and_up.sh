#!/usr/bin/env bash
# Build backend and frontend images and bring up services with docker compose.
# Usage: ./build_and_up.sh
# Requires: docker and docker compose available in PATH, and .env in repo root.

set -euo pipefail
IFS=$'\n\t'

# Resolve script directory (repo root)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "[build_and_up] Starting build and deploy (timestamp: $(date -u +%Y-%m-%dT%H:%M:%SZ))"

# Check for .env file
if [[ ! -f .env ]]; then
  echo "[build_and_up] Warning: .env file not found in $SCRIPT_DIR. Continuing without it." >&2
fi

# Check docker available
if ! command -v docker >/dev/null 2>&1; then
  echo "[build_and_up] ERROR: docker is not installed or not in PATH." >&2
  exit 2
fi

# Build backend image
if [[ -d backend ]]; then
  echo "[build_and_up] Building backend image (pop-backend:latest) from ./backend"
  docker build --pull -t pop-backend:latest ./backend
else
  echo "[build_and_up] ERROR: ./backend directory not found" >&2
  exit 3
fi

# Build frontend image
if [[ -d frontend ]]; then
  echo "[build_and_up] Building frontend image (pop-frontend:latest) from ./frontend"
  docker build --pull -t pop-frontend:latest ./frontend
else
  echo "[build_and_up] ERROR: ./frontend directory not found" >&2
  exit 4
fi

# Run docker compose
echo "[build_and_up] Starting services with docker compose (detached) using .env"
# Use `docker compose` (v2) if available, else fall back to docker-compose if present
if docker compose version >/dev/null 2>&1; then
  docker compose up -d --env-file .env
elif command -v docker-compose >/dev/null 2>&1; then
  docker-compose --env-file .env up -d
else
  echo "[build_and_up] ERROR: docker compose (v2) or docker-compose not available." >&2
  exit 5
fi

echo "[build_and_up] Done. Services should be up. Use 'docker compose ps' to check status."
