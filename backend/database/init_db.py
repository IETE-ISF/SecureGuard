"""
init_db.py - Create database tables (Phase 2, Task 1).

Called once at application startup. create_all() only creates tables that
are missing, so it is safe to run on every start.
"""

import logging

from sqlalchemy import Engine

import backend.models  # noqa: F401  (registers all models on Base.metadata)
from backend.database.base import Base
from backend.database.session import get_engine

logger = logging.getLogger(__name__)


def create_tables(engine: Engine | None = None) -> list[str]:
    """Create any missing tables and return the sorted table names."""
    engine = engine or get_engine()
    Base.metadata.create_all(engine)
    names = sorted(Base.metadata.tables)
    logger.info("Database tables ready: %s", ", ".join(names))
    return names