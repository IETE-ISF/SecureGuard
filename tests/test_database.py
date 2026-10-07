"""
test_database.py - Tests for the database layer (Phase 1, Task 5).

Every test runs against its own temporary SQLite file, so the real
data/securerack.db is never touched.
"""

import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import String, select, text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Mapped, mapped_column

import backend.database.session as session_module
from backend.config import get_settings
from backend.database.base import NAMING_CONVENTION, Base, TimestampMixin, utcnow
from backend.database.session import (
    check_connection,
    dispose_engine,
    get_db,
    get_engine,
    init_engine,
    session_scope,
)
from backend.main import app


class DemoItem(TimestampMixin, Base):
    """Throwaway model used only by these tests."""

    __tablename__ = "test_demo_item"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    seen_at: Mapped[datetime]


@pytest.fixture(scope="module", autouse=True)
def _unregister_demo_table() -> Iterator[None]:
    """Remove the demo table from the shared metadata when this module ends."""
    yield
    Base.metadata.remove(DemoItem.__table__)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the app at a fresh temporary database for every test."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("SRG_DATABASE_PATH", str(db_path))
    get_settings.cache_clear()
    dispose_engine()
    yield db_path
    dispose_engine()
    get_settings.cache_clear()


def _create_tables() -> None:
    Base.metadata.create_all(get_engine())


# --- base.py -----------------------------------------------------------------


def test_naming_convention_is_applied() -> None:
    names = {constraint.name for constraint in DemoItem.__table__.constraints}
    assert {"pk_test_demo_item", "uq_test_demo_item_name"} <= names
    assert Base.metadata.naming_convention == NAMING_CONVENTION


def test_utc_datetime_round_trip_returns_aware_utc() -> None:
    _create_tables()
    ist = timezone(timedelta(hours=5, minutes=30))
    moment = datetime(2026, 10, 8, 10, 0, tzinfo=ist)

    with session_scope() as session:
        session.add(DemoItem(name="a", seen_at=moment))

    with session_scope() as session:
        stored = session.scalars(select(DemoItem)).one()
        assert stored.seen_at.tzinfo == timezone.utc
        assert stored.seen_at == moment
        assert (stored.seen_at.hour, stored.seen_at.minute) == (4, 30)


def test_naive_datetime_is_rejected() -> None:
    _create_tables()
    with pytest.raises(StatementError):
        with session_scope() as session:
            session.add(DemoItem(name="a", seen_at=datetime.now()))


def test_timestamp_mixin_sets_and_refreshes() -> None:
    _create_tables()
    with session_scope() as session:
        session.add(DemoItem(name="a", seen_at=utcnow()))

    with session_scope() as session:
        item = session.scalars(select(DemoItem)).one()
        created, first_update = item.created_at, item.updated_at
        assert created.tzinfo == timezone.utc

    time.sleep(0.05)
    with session_scope() as session:
        session.scalars(select(DemoItem)).one().name = "b"

    with session_scope() as session:
        item = session.scalars(select(DemoItem)).one()
        assert item.created_at == created
        assert item.updated_at > first_update


# --- session.py --------------------------------------------------------------


def test_check_connection_succeeds() -> None:
    assert check_connection() is True


def test_sqlite_pragmas_are_applied() -> None:
    with get_engine().connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert connection.execute(text("PRAGMA busy_timeout")).scalar() == 5000


def test_foreign_keys_are_enforced() -> None:
    engine = get_engine()
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE fk_parent (id INTEGER PRIMARY KEY)"))
        connection.execute(
            text(
                "CREATE TABLE fk_child ("
                "id INTEGER PRIMARY KEY, "
                "parent_id INTEGER REFERENCES fk_parent(id))"
            )
        )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO fk_child (parent_id) VALUES (999)"))


def test_init_engine_is_a_singleton() -> None:
    assert init_engine() is init_engine()
    assert get_engine() is init_engine()


def test_session_scope_commits_on_success() -> None:
    _create_tables()
    with session_scope() as session:
        session.add(DemoItem(name="kept", seen_at=utcnow()))

    with session_scope() as session:
        assert session.scalars(select(DemoItem.name)).all() == ["kept"]


def test_session_scope_rolls_back_on_error() -> None:
    _create_tables()
    with pytest.raises(RuntimeError, match="boom"):
        with session_scope() as session:
            session.add(DemoItem(name="lost", seen_at=utcnow()))
            session.flush()
            raise RuntimeError("boom")

    with session_scope() as session:
        assert session.scalars(select(DemoItem.name)).all() == []


def test_get_db_yields_and_closes_session() -> None:
    generator = get_db()
    session = next(generator)
    session.execute(text("SELECT 1"))
    assert session.in_transaction()
    generator.close()
    assert not session.in_transaction()


def test_dispose_engine_resets_state() -> None:
    init_engine()
    dispose_engine()
    assert session_module._engine is None
    assert session_module._session_factory is None


# --- app lifespan ------------------------------------------------------------


def test_app_startup_creates_database_and_serves_health(isolated_db: Path) -> None:
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert check_connection()

    assert isolated_db.exists()
    assert session_module._engine is None  # disposed at shutdown


def test_app_startup_aborts_when_database_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A directory cannot be opened as a SQLite file.
    monkeypatch.setenv("SRG_DATABASE_PATH", str(tmp_path))
    get_settings.cache_clear()

    with pytest.raises(RuntimeError, match="Database connectivity check failed"):
        with TestClient(app):
            pass