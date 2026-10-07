"""
main.py - FastAPI application entry point (Phase 0 Task 4, extended in Phase 1 Task 4).

Run with either:
    python -m backend.main
    uvicorn backend.main:app --reload
"""

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from backend.api import health
from backend.config import get_settings
from backend.database.session import check_connection, dispose_engine, init_engine
from backend.logging_config import setup_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Run startup and shutdown logic for the application.

    Startup: configure logging, open the database and verify it responds.
    If the database check fails, startup is aborted.
    Shutdown: close all pooled database connections.
    """
    settings = get_settings()
    setup_logging(settings)

    app.state.started_at = time.monotonic()
    logger.info(
        "%s v%s starting (environment=%s)",
        settings.app_name,
        settings.app_version,
        settings.environment,
    )

    init_engine(settings)
    if not check_connection():
        dispose_engine()
        raise RuntimeError(
            f"Database connectivity check failed ({settings.database_path})"
        )
    logger.info("Database connection OK (%s)", settings.database_path)

    try:
        yield
    finally:
        dispose_engine()
        logger.info("%s shutting down", settings.app_name)


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        lifespan=lifespan,
    )
    app.include_router(health.router)
    return app


app = create_app()


if __name__ == "__main__":
    _settings = get_settings()
    uvicorn.run(
        "backend.main:app",
        host=_settings.host,
        port=_settings.port,
        reload=_settings.debug,
    )