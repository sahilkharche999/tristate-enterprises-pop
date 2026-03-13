"""FastAPI application for converted VBA macros.

This app exposes endpoints that accept an uploaded Excel file and parameters, run
pipeline-style computations and return JSON results. Configuration values are
externalized via `app.config.settings` (pydantic.BaseSettings).
"""
from .config import settings
from .routers import macros
from contextlib import asynccontextmanager
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
    import os

    # Add project root to path for ai_implementation imports
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup
        try:
            from ai_implementation.database import init_db
            init_db()
            logger.info("AI pipeline database initialized")
        except Exception as e:
            logger.warning(f"AI pipeline DB init failed: {e}")
        yield
        # Shutdown (nothing to clean up)

    app = FastAPI(title="VBA -> Python Macro Pipeline", lifespan=lifespan)

    allow_origins = [o.strip() for o in settings.ALLOW_ORIGINS.split(',')] if settings.ALLOW_ORIGINS else ["*"]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(macros.router, prefix="")

    # Mount AI Budget Pipeline router
    try:
        from ai_implementation.router import router as ai_router
        app.include_router(ai_router, prefix="/ai", tags=["AI Pipeline"])
        logger.info("AI Budget Pipeline router mounted at /ai")
    except ImportError as e:
        logger.warning(f"AI pipeline not available (missing deps?): {e}")

    return app


app = create_app()
