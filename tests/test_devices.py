"""
test_devices.py - Tests for the device registry (Phase 2, Task 5).

Service tests use an in-memory database; API tests run the real app
against a temporary database file. The real data/securerack.db is never touched.
"""

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.database.base import utcnow
from backend.database.init_db import create_tables
from backend.database.session import dispose_engine
from backend.main import app
from backend.models import DeviceStatus
from backend.schemas.device import DeviceCreate, DeviceUpdate
from backend.services.device_service import (
    DeviceNotFoundError,
    get_device,
    mark_stale_devices_offline,
    record_heartbeat,
    register_device,
    update_device,
)

WATER = {"node_id": "WATER_01", "node_name": "Water Node", "node_type": "water"}
POWER = {"node_id": "POWER_01", "node_name": "Power Node", "node_type": "power"}


@pytest.fixture
def db() -> Iterator[Session]:
    """A session on a fresh in-memory database with all tables created."""
    engine = create_engine("sqlite://")
    create_tables(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The real app, started against a temporary database file."""
    monkeypatch.setenv("SRG_DATABASE_PATH", str(tmp_path / "devices_test.db"))
    get_settings.cache_clear()
    dispose_engine()
    with TestClient(app) as test_client:
        yield test_client
    dispose_engine()
    get_settings.cache_clear()


def _register(db: Session, payload: dict[str, str] = WATER) -> None:
    register_device(db, DeviceCreate(**payload))


def _ids(client: TestClient, **params: object) -> list[str]:
    return [d["node_id"] for d in client.get("/devices", params=params).json()]


# --- service: status management ----------------------------------------------


def test_heartbeat_brings_offline_device_online(db: Session) -> None:
    _register(db)
    device = record_heartbeat(db, "water_01")
    assert device.status == DeviceStatus.ONLINE
    assert device.last_seen is not None


def test_heartbeat_keeps_maintenance_status(db: Session) -> None:
    _register(db)
    update_device(db, "WATER_01", DeviceUpdate(status="maintenance"))
    device = record_heartbeat(db, "WATER_01")
    assert device.status == DeviceStatus.MAINTENANCE
    assert device.last_seen is not None


def test_heartbeat_unknown_device_raises(db: Session) -> None:
    with pytest.raises(DeviceNotFoundError):
        record_heartbeat(db, "NOPE_99")


def test_sweep_marks_silent_device_offline(db: Session) -> None:
    _register(db)
    seen = utcnow()
    record_heartbeat(db, "WATER_01", seen_at=seen)
    result = mark_stale_devices_offline(
        db, timeout_seconds=30, now=seen + timedelta(seconds=60)
    )
    assert result == ["WATER_01"]
    assert get_device(db, "WATER_01").status == DeviceStatus.OFFLINE


def test_sweep_keeps_recently_seen_device_online(db: Session) -> None:
    _register(db)
    seen = utcnow()
    record_heartbeat(db, "WATER_01", seen_at=seen)
    result = mark_stale_devices_offline(
        db, timeout_seconds=30, now=seen + timedelta(seconds=10)
    )
    assert result == []
    assert get_device(db, "WATER_01").status == DeviceStatus.ONLINE


def test_sweep_ignores_offline_and_maintenance_devices(db: Session) -> None:
    _register(db)
    _register(db, POWER)
    update_device(db, "POWER_01", DeviceUpdate(status="maintenance"))
    result = mark_stale_devices_offline(
        db, timeout_seconds=30, now=utcnow() + timedelta(hours=1)
    )
    assert result == []
    assert get_device(db, "POWER_01").status == DeviceStatus.MAINTENANCE
    assert get_device(db, "WATER_01").status == DeviceStatus.OFFLINE


def test_sweep_marks_online_device_without_last_seen_offline(db: Session) -> None:
    _register(db)
    update_device(db, "WATER_01", DeviceUpdate(status="online"))
    assert mark_stale_devices_offline(db, timeout_seconds=30) == ["WATER_01"]


# --- API ---------------------------------------------------------------------


def test_register_returns_201_and_starts_offline(client: TestClient) -> None:
    response = client.post("/devices", json=WATER)
    assert response.status_code == 201
    body = response.json()
    assert body["node_id"] == "WATER_01"
    assert body["status"] == "offline"
    assert body["last_seen"] is None


def test_register_normalises_node_id(client: TestClient) -> None:
    response = client.post("/devices", json={**WATER, "node_id": " water_01 "})
    assert response.json()["node_id"] == "WATER_01"


def test_duplicate_registration_returns_409(client: TestClient) -> None:
    client.post("/devices", json=WATER)
    response = client.post("/devices", json={**WATER, "node_id": "water_01"})
    assert response.status_code == 409


def test_invalid_payloads_return_422(client: TestClient) -> None:
    assert client.post("/devices", json={**WATER, "node_id": "bad id!"}).status_code == 422
    assert client.post("/devices", json={**WATER, "node_type": "toaster"}).status_code == 422


def test_get_is_case_insensitive_and_404_when_missing(client: TestClient) -> None:
    client.post("/devices", json=WATER)
    assert client.get("/devices/water_01").json()["node_name"] == "Water Node"
    assert client.get("/devices/NOPE_99").status_code == 404


def test_list_filters_and_pagination(client: TestClient) -> None:
    client.post("/devices", json=WATER)
    client.post("/devices", json=POWER)
    assert _ids(client) == ["WATER_01", "POWER_01"]
    assert _ids(client, node_type="power") == ["POWER_01"]
    assert _ids(client, limit=1, offset=1) == ["POWER_01"]
    assert _ids(client, status="online") == []
    assert client.get("/devices", params={"limit": 0}).status_code == 422


def test_patch_updates_name_and_status_but_not_identity(client: TestClient) -> None:
    client.post("/devices", json=WATER)
    response = client.patch(
        "/devices/WATER_01", json={"node_name": "Renamed", "status": "maintenance"}
    )
    assert response.status_code == 200
    assert response.json()["node_name"] == "Renamed"
    assert response.json()["status"] == "maintenance"
    assert client.patch("/devices/WATER_01", json={"node_id": "HACK_01"}).status_code == 422
    assert client.patch("/devices/WATER_01", json={}).status_code == 422
    assert client.patch("/devices/NOPE_99", json={"status": "online"}).status_code == 404


def test_delete_returns_204_then_404(client: TestClient) -> None:
    client.post("/devices", json=WATER)
    assert client.delete("/devices/WATER_01").status_code == 204
    assert client.delete("/devices/WATER_01").status_code == 404
    assert _ids(client) == []


def test_heartbeat_endpoint_marks_device_online(client: TestClient) -> None:
    client.post("/devices", json=WATER)
    response = client.post("/devices/water_01/heartbeat")
    assert response.status_code == 200
    assert response.json()["status"] == "online"
    assert response.json()["last_seen"] is not None
    assert client.post("/devices/NOPE_99/heartbeat").status_code == 404