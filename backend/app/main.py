"""FastAPI application for converted VBA macros.

This app exposes endpoints that accept an uploaded Excel file and parameters, run
pipeline-style computations and return JSON results. Configuration values are
externalized via `app.config.settings` (pydantic.BaseSettings).
"""
from .config import settings
from .routers import macros
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging
import sys

# Basic logging configuration for the application. Containers and prod deployments
# can override this via environment or a more advanced logging setup.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(title="VBA -> Python Macro Pipeline")

    allow_origins = [o.strip() for o in settings.ALLOW_ORIGINS.split(',')] if settings.ALLOW_ORIGINS else ["*"]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(macros.router, prefix="")

    return app


app = create_app()
