"""
test_message_handler.py - Tests for the message handler, presence sweeper
and startup wiring (Phase 3, Task 4).

Every test runs against its own temporary database file.
"""

import json
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings, get_settings
from backend.database.init_db import create_tables
from backend.database.session import dispose_engine, init_engine, session_scope
from backend.main import app
from backend.models import DeviceStatus
from backend.schemas.device import DeviceCreate, DeviceUpdate
from backend.services.device_service import (
    get_device,
    record_heartbeat,
    register_device,
    update_device,
)
from backend.services.message_handler import MessageHandler, should_log
from backend.services.packet_parser import Packet
from backend.services.presence_sweeper import PresenceSweeper


@pytest.fixture(autouse=True)
def temp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Give every test a fresh database with all tables created."""
    monkeypatch.setenv("SRG_DATABASE_PATH", str(tmp_path / "handler_test.db"))
    get_settings.cache_clear()
    dispose_engine()
    init_engine()
    create_tables()
    yield
    dispose_engine()
    get_settings.cache_clear()


def register(node_id: str, node_type: str = "water") -> None:
    with session_scope() as db:
        register_device(
            db, DeviceCreate(node_id=node_id, node_name=node_id, node_type=node_type)
        )


def snapshot(node_id: str) -> tuple[DeviceStatus, bool]:
    """Return (status, has_last_seen) for a device."""
    with session_scope() as db:
        device = get_device(db, node_id)
        return device.status, device.last_seen is not None


def packet_line(src: str = "WATER_01", type_: str = "heartbeat", seq: int = 1, **d: Any) -> str:
    packet: dict[str, Any] = {"v": 1, "src": src, "type": type_, "seq": seq}
    if d:
        packet["d"] = d
    return json.dumps(packet)


def wait_until(predicate: Callable[[], bool], timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# --- MessageHandler ----------------------------------------------------------


def test_heartbeat_marks_registered_device_online() -> None:
    register("WATER_01")
    assert snapshot("WATER_01") == (DeviceStatus.OFFLINE, False)

    MessageHandler().handle_line(packet_line())

    assert snapshot("WATER_01") == (DeviceStatus.ONLINE, True)


def test_every_packet_type_counts_as_liveness() -> None:
    register("WATER_01")
    register("POWER_01", "power")
    register("MASTER_01", "master")
    handler = MessageHandler()

    handler.handle_line(packet_line("MASTER_01"))
    handler.handle_line(packet_line("WATER_01", "data", 2, flow_in=15.4))
    handler.handle_line(packet_line("POWER_01", "event", 3, door="open"))

    for node_id in ("MASTER_01", "WATER_01", "POWER_01"):
        assert snapshot(node_id) == (DeviceStatus.ONLINE, True)
    stats = handler.stats()
    assert stats["packets_accepted"] == 3
    assert stats["packets_heartbeat"] == 1
    assert stats["packets_data"] == 1
    assert stats["packets_event"] == 1


def test_unknown_device_is_ignored_and_not_registered() -> None:
    handler = MessageHandler()
    handler.handle_line(packet_line("GHOST_99"))

    stats = handler.stats()
    assert stats["unknown_device"] == 1
    assert stats.get("packets_accepted", 0) == 0
    with session_scope() as db:
        from backend.services.device_service import find_device

        assert find_device(db, "GHOST_99") is None


def test_invalid_lines_are_counted_by_reason() -> None:
    register("WATER_01")
    handler = MessageHandler()

    handler.handle_line("")
    handler.handle_line("{bad")
    handler.handle_line("[1,2]")
    handler.handle_line(json.dumps({"v": 2, "src": "WATER_01", "type": "heartbeat", "seq": 1}))

    stats = handler.stats()
    assert stats["rejected_empty"] == 1
    assert stats["rejected_invalid_json"] == 1
    assert stats["rejected_not_object"] == 1
    assert stats["rejected_invalid_packet"] == 1
    assert stats.get("packets_accepted", 0) == 0
    assert snapshot("WATER_01") == (DeviceStatus.OFFLINE, False)


def test_listener_receives_accepted_packets_only() -> None:
    register("WATER_01")
    seen: list[Packet] = []
    handler = MessageHandler()
    handler.add_listener(seen.append)

    handler.handle_line("{bad")
    handler.handle_line(packet_line("GHOST_99"))
    handler.handle_line(packet_line("WATER_01", "data", 5, flow_in=1.5))

    assert len(seen) == 1
    assert seen[0].source == "WATER_01"
    assert seen[0].payload == {"flow_in": 1.5}


def test_failing_listener_is_isolated() -> None:
    register("WATER_01")
    seen: list[Packet] = []

    def broken(packet: Packet) -> None:
        raise RuntimeError("boom")

    handler = MessageHandler()
    handler.add_listener(broken)
    handler.add_listener(seen.append)
    handler.handle_line(packet_line())

    assert len(seen) == 1
    assert handler.stats()["listener_errors"] == 1
    assert snapshot("WATER_01") == (DeviceStatus.ONLINE, True)


def test_should_log_rate_limits_repeated_events() -> None:
    assert all(should_log(n) for n in range(1, 6))
    assert not any(should_log(n) for n in (6, 7, 50, 99, 101))
    assert should_log(100)
    assert should_log(200)


# --- PresenceSweeper ---------------------------------------------------------


def test_sweep_once_marks_only_stale_online_devices() -> None:
    register("WATER_01")
    register("POWER_01", "power")
    with session_scope() as db:
        update_device(db, "WATER_01", DeviceUpdate(status="online"))  # never heard from
        record_heartbeat(db, "POWER_01")  # heard just now

    sweeper = PresenceSweeper(timeout_seconds=30, interval_seconds=10)
    assert sweeper.sweep_once() == ["WATER_01"]
    assert snapshot("WATER_01")[0] == DeviceStatus.OFFLINE
    assert snapshot("POWER_01")[0] == DeviceStatus.ONLINE


def test_sweeper_thread_marks_device_offline_and_stops() -> None:
    register("WATER_01")
    with session_scope() as db:
        update_device(db, "WATER_01", DeviceUpdate(status="online"))

    sweeper = PresenceSweeper(timeout_seconds=30, interval_seconds=0.05)
    sweeper.start()
    try:
        assert wait_until(lambda: snapshot("WATER_01")[0] == DeviceStatus.OFFLINE)
        assert sweeper.is_running
    finally:
        sweeper.stop()
    assert not sweeper.is_running
    sweeper.stop()  # safe to call again


def test_sweeper_from_settings() -> None:
    settings = Settings(
        device_offline_timeout_seconds=7, device_sweep_interval_seconds=2.0
    )
    sweeper = PresenceSweeper.from_settings(settings)
    assert sweeper.timeout_seconds == 7
    assert sweeper.interval_seconds == 2.0


# --- application wiring ------------------------------------------------------


def test_app_starts_sweeper_and_leaves_uart_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SRG_UART_ENABLED", "false")
    get_settings.cache_clear()

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert app.state.uart is None
        assert app.state.sweeper.is_running

    assert not app.state.sweeper.is_running