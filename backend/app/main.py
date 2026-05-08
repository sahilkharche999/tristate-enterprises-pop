"""FastAPI application for converted VBA macros.

This app exposes endpoints that accept an uploaded Excel file and parameters, run
pipeline-style computations and return JSON results. Configuration values are
externalized via `app.config.settings` (pydantic.BaseSettings).
"""
import asyncio
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .routers import macros
from .auth.router import router as auth_router
from .auth.dependencies import get_current_user
from .ai_implementation.database import init_db
from .ai_implementation.seed.seed_database import run_seed
from .ai_implementation.router import router as ai_router
from .routers.budget_history import router as budget_history_router
from .routers.hoa import router as hoa_router
from .routers.app_settings import router as app_settings_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            init_db()
            logger.info("AI pipeline database initialized")
            await asyncio.to_thread(run_seed)
        except Exception as e:
            logger.warning(f"AI pipeline startup failed: {e}")
        yield

    app = FastAPI(title="VBA -> Python Macro Pipeline", lifespan=lifespan)

    allow_origins = [o.strip() for o in settings.ALLOW_ORIGINS.split(',')] if settings.ALLOW_ORIGINS else ["*"]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Auth routes (unprotected)
    app.include_router(auth_router)

    # Protected macro routes
    app.include_router(macros.router, prefix="", dependencies=[Depends(get_current_user)])

    # Protected HOA routes
    app.include_router(hoa_router, prefix="", dependencies=[Depends(get_current_user)])
    app.include_router(app_settings_router, prefix="", dependencies=[Depends(get_current_user)])
    app.include_router(budget_history_router, prefix="", dependencies=[Depends(get_current_user)])

    # Protected AI routes
    app.include_router(
        ai_router, prefix="/ai", tags=["AI Pipeline"],
        dependencies=[Depends(get_current_user)],
    )
    logger.info("AI Budget Pipeline router mounted at /ai")

    return app


app = create_app()
