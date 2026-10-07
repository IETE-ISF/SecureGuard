"""
session.py - Database engine, session factory and connection manager
(Phase 1, Task 3).

Public API:
    init_engine()      create the engine and session factory (idempotent)
    get_engine()       return the engine, initialising it if needed
    get_session_factory()
    session_scope()    context manager: commit on success, rollback on error
    get_db()           FastAPI dependency yielding a session
    check_connection() run SELECT 1 and report success as a bool
    dispose_engine()   close all pooled connections (used at shutdown)

Usage (background service):
    from backend.database.session import session_scope

    with session_scope() as session:
        session.add(record)

Usage (FastAPI route):
    from fastapi import Depends
    from sqlalchemy.orm import Session
    from backend.database.session import get_db

    @router.get("/things")
    def list_things(db: Session = Depends(get_db)): ...
"""

import logging
import sqlite3
import threading
from collections.abc import Generator, Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.config import Settings, get_settings

logger = logging.getLogger(__name__)

SQLITE_BUSY_TIMEOUT_MS = 5000

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_lock = threading.Lock()


def _apply_sqlite_pragmas(dbapi_connection: sqlite3.Connection, _record: object) -> None:
    """Configure every new SQLite connection (foreign keys, WAL, timeouts)."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    finally:
        cursor.close()


def create_db_engine(settings: Settings | None = None) -> Engine:
    """Build a new SQLite engine for the configured database file."""
    settings = settings or get_settings()
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        settings.database_url,
        echo=settings.database_echo,
        connect_args={"check_same_thread": False},
    )
    event.listen(engine, "connect", _apply_sqlite_pragmas)
    return engine


def init_engine(settings: Settings | None = None) -> Engine:
    """
    Create the engine and session factory once per process.

    Safe to call repeatedly and from multiple threads. If an engine already
    exists it is returned unchanged and the settings argument is ignored.
    """
    global _engine, _session_factory

    with _lock:
        if _engine is None:
            settings = settings or get_settings()
            _engine = create_db_engine(settings)
            _session_factory = sessionmaker(
                bind=_engine,
                autoflush=True,
                expire_on_commit=False,
            )
            logger.info("Database engine initialised (%s)", settings.database_path)
        return _engine


def get_engine() -> Engine:
    """Return the shared engine, initialising it on first use."""
    return init_engine()


def get_session_factory() -> sessionmaker[Session]:
    """Return the shared session factory, initialising the engine if needed."""
    init_engine()
    assert _session_factory is not None  # set by init_engine
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """
    Provide a transactional session for background services.

    Commits when the block finishes cleanly, rolls back on any exception,
    and always closes the session.
    """
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency that yields a session for one request.

    It does not commit: services call session.commit() explicitly. On an
    unhandled error the session is rolled back, and it is always closed.
    """
    session = get_session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_connection() -> bool:
    """Run SELECT 1 against the database. Returns True if it succeeds."""
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError:
        logger.exception("Database connectivity check failed")
        return False


def dispose_engine() -> None:
    """Close all pooled connections and reset the module state."""
    global _engine, _session_factory

    with _lock:
        if _engine is not None:
            _engine.dispose()
            logger.info("Database engine disposed")
        _engine = None
        _session_factory = None