"""
main.py - FastAPI application entry point
(Phase 0 Task 4, extended in Phases 1 to 4).

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

from backend.api import devices, health, water
from backend.config import get_settings
from backend.database.init_db import create_tables
from backend.database.session import check_connection, dispose_engine, init_engine
from backend.logging_config import setup_logging
from backend.services.message_handler import MessageHandler
from backend.services.presence_sweeper import PresenceSweeper
from backend.services.uart_service import UartService
from backend.services.water_service import WaterIngestor

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Run startup and shutdown logic for the application.

    Startup: configure logging, open the database and verify it responds
    (abort if not), create tables, start the presence sweeper, and, if
    SRG_UART_ENABLED is true, start the UART reader with the water
    ingestor subscribed to incoming packets.
    Shutdown: stop the UART reader, then the sweeper, then close the database.
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

    sweeper = PresenceSweeper.from_settings(settings)
    uart: UartService | None = None
    app.state.sweeper = sweeper
    app.state.uart = None
    app.state.message_handler = None
    app.state.water_ingestor = None

    try:
        create_tables()
        sweeper.start()

        if settings.uart_enabled:
            handler = MessageHandler()
            water = WaterIngestor()
            handler.add_listener(water.handle_packet)
            uart = UartService.from_settings(handler.handle_line, settings)
            uart.start()
            app.state.message_handler = handler
            app.state.water_ingestor = water
            app.state.uart = uart
        else:
            logger.info("UART disabled (set SRG_UART_ENABLED=true and SRG_UART_PORT to enable)")

        yield
    finally:
        if uart is not None:
            uart.stop()
        sweeper.stop()
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
    app.include_router(devices.router)
    app.include_router(water.router)
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