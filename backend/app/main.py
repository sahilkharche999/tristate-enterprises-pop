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
from .auth.router import router as auth_router
from .auth.dependencies import get_current_user
from .ai_implementation.database import init_db
from .ai_implementation.seed.seed_database import run_seed
from .ai_implementation.router import router as ai_router
from .routers.budget_history import router as budget_history_router
from .routers.hoa import router as hoa_router
from .routers.app_settings import router as app_settings_router
from .disclosure_package.router import router as disclosure_package_router
from .routers.annual_packages import router as annual_packages_router
from .routers.appendices import router as appendices_router
from .routers.dre import router as dre_router
from .routers.dre_approval import router as dre_approval_router
from .routers.dre_review import router as dre_review_router
from .routers.ccr import router as ccr_router
from .routers.assessment_mapping_review import router as assessment_mapping_review_router
from .routers.allocation_resolution import router as allocation_resolution_router
from .routers.hoa_settings import router as hoa_settings_router
from .routers.manual_setup import router as manual_setup_router

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
        except Exception:
            logger.exception("AI pipeline database initialization failed")
            raise
        try:
            await asyncio.to_thread(run_seed)
        except Exception as e:
            logger.warning("AI pipeline seed failed: %s", e)
        yield

    docs_url = "/docs" if settings.ENABLE_DOCS else None
    openapi_url = "/openapi.json" if settings.ENABLE_DOCS else None
    app = FastAPI(
        title="VBA -> Python Macro Pipeline",
        lifespan=lifespan,
        docs_url=docs_url,
        openapi_url=openapi_url,
    )

    allow_origins = [o.strip() for o in settings.ALLOW_ORIGINS.split(',')] if settings.ALLOW_ORIGINS else ["*"]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # Let browser JS read the download filename on cross-origin responses
        # (e.g. Railway, where frontend and backend are separate domains).
        expose_headers=["Content-Disposition"],
    )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> dict[str, str]:
        return {"status": "ready"}

    # Auth routes (unprotected)
    app.include_router(auth_router)

    # Protected HOA routes
    app.include_router(hoa_router, prefix="", dependencies=[Depends(get_current_user)])
    app.include_router(app_settings_router, prefix="", dependencies=[Depends(get_current_user)])
    app.include_router(budget_history_router, prefix="", dependencies=[Depends(get_current_user)])

    # Phase 11 disclosure-package endpoints. Auth is applied per-endpoint
    # (Depends(get_current_user) on every route in router.py) — we do NOT
    # add a router-level dependency here because the router's per-endpoint
    # auth + ownership check is the canonical site for T-11-01 / T-11-02.
    app.include_router(disclosure_package_router)
    # hoa_settings router applies Depends(get_current_user) per-endpoint, so no
    # router-level dependency needed (mirrors the disclosure_package pattern).
    app.include_router(hoa_settings_router)
    # DRE upload router (Phase 3.1 of dre-driven-assessment-engine). Auth is
    # applied per-route via Depends(get_current_user) on every endpoint.
    app.include_router(dre_router)
    # DRE approval router (Phase 4.2 + 5.9): promotes a reviewed extraction
    # run into a live AssessmentSetup. Concurrent-approve protection via
    # promoted_at IS NOT NULL check.
    app.include_router(dre_approval_router)
    # DRE Review Workbench read-side router (Phase 4.1): lists DRE
    # documents + extraction runs, returns parsed_json + citations
    # + edit history. Mutating endpoints (record edit, approve)
    # live in dre_review and dre_approval routers respectively.
    app.include_router(dre_review_router)
    # CC&R / governing-document upload, extraction, factor entry, and approval.
    # Shares dre_extraction_runs table (discriminated by document_type='ccr').
    app.include_router(ccr_router)
    # Manual assessment setup entry (no DRE/CC&R document at all) — creates
    # a synthetic extraction run promoted through the same DRE/CC&R approve
    # endpoints above.
    app.include_router(manual_setup_router)
    app.include_router(assessment_mapping_review_router)
    app.include_router(allocation_resolution_router)
    # Per-HOA appendix manifest router (Phase 5.4 of
    # dre-driven-assessment-engine). Auth is applied per-route via
    # Depends(get_current_user) on every endpoint.
    app.include_router(appendices_router)
    # AnnualPackage lifecycle router (Phase 4.8 of
    # dre-driven-assessment-engine): draft → approved → finalized
    # transitions; finalization freezes all four snapshot JSONs.
    app.include_router(annual_packages_router)

    # Protected AI routes
    app.include_router(
        ai_router, prefix="/ai", tags=["AI Pipeline"],
        dependencies=[Depends(get_current_user)],
    )
    logger.info("AI Budget Pipeline router mounted at /ai")

    return app


app = create_app()
