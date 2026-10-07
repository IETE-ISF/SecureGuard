"""
test_uart_integration.py - End-to-end tests for the UART link (Phase 3, Task 5).

Fake ESP32 bytes go in one end and device-registry changes come out the
other, through the real UartService, parser, MessageHandler and database.
No hardware is needed.
"""

import json
import queue
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.database.init_db import create_tables
from backend.database.session import dispose_engine, init_engine, session_scope
from backend.main import app
from backend.models import DeviceStatus
from backend.schemas.device import DeviceCreate
from backend.services.device_service import get_device, register_device
from backend.services.message_handler import MessageHandler
from backend.services.presence_sweeper import PresenceSweeper
from backend.services.uart_monitor import describe_line
from backend.services.uart_service import UartService

WATER = {"node_id": "WATER_01", "node_name": "Water Node", "node_type": "water"}


class QueueSerial:
    """A fake serial port that the test feeds bytes into, like an ESP32 transmitting."""

    in_waiting = 0

    def __init__(self) -> None:
        self._chunks: queue.Queue[bytes] = queue.Queue()
        self.closed = False

    def feed(self, data: bytes) -> None:
        self._chunks.put(data)

    def read(self, size: int = 1) -> bytes:
        try:
            return self._chunks.get(timeout=0.02)
        except queue.Empty:
            return b""

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def temp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Give every test a fresh database with all tables created."""
    monkeypatch.setenv("SRG_DATABASE_PATH", str(tmp_path / "uart_e2e.db"))
    get_settings.cache_clear()
    dispose_engine()
    init_engine()
    create_tables()
    yield
    dispose_engine()
    get_settings.cache_clear()


def pkt(src: str = "WATER_01", type_: str = "data", seq: int = 1, **d: Any) -> bytes:
    """One wire-format packet, newline-terminated."""
    body: dict[str, Any] = {"v": 1, "src": src, "type": type_, "seq": seq}
    if d:
        body["d"] = d
    return (json.dumps(body) + "\n").encode()


def register(node_id: str, node_type: str = "water") -> None:
    with session_scope() as db:
        register_device(
            db, DeviceCreate(node_id=node_id, node_name=node_id, node_type=node_type)
        )


def status_of(node_id: str) -> DeviceStatus:
    with session_scope() as db:
        return get_device(db, node_id).status


def wait_until(predicate: Callable[[], bool], timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def make_service(port: QueueSerial, handler: MessageHandler) -> UartService:
    return UartService(
        "FAKE",
        115200,
        0.05,
        handler.handle_line,
        reconnect_delay=0.05,
        serial_factory=lambda *args, **kwargs: port,
    )


# --- wire -> database ----------------------------------------------------------


def test_packets_flow_from_the_wire_to_the_database() -> None:
    register("WATER_01")
    register("POWER_01", "power")
    port = QueueSerial()
    handler = MessageHandler()
    service = make_service(port, handler)

    service.start()
    try:
        water = pkt("WATER_01", flow_in=15.4, flow_out=11.2)
        port.feed(water[:20])  # a packet split across two reads
        port.feed(water[20:])
        port.feed(b"boot: ESP32-C3 ready\n")  # firmware debug text on the same wire
        port.feed(pkt("GHOST_99", seq=2, x=1))  # valid packet, unregistered device
        port.feed(pkt("POWER_01", "event", 3, door="open"))

        assert wait_until(lambda: service.stats()["lines_received"] == 4)
        assert wait_until(lambda: handler.stats().get("packets_accepted") == 2)
    finally:
        service.stop()

    assert status_of("WATER_01") == DeviceStatus.ONLINE
    assert status_of("POWER_01") == DeviceStatus.ONLINE
    stats = handler.stats()
    assert stats["packets_data"] == 1
    assert stats["packets_event"] == 1
    assert stats["rejected_invalid_json"] == 1
    assert stats["unknown_device"] == 1
    assert port.closed


def test_silent_device_goes_offline_after_traffic_stops() -> None:
    register("WATER_01")
    port = QueueSerial()
    handler = MessageHandler()
    service = make_service(port, handler)

    service.start()
    try:
        port.feed(pkt("WATER_01", "heartbeat"))
        assert wait_until(lambda: status_of("WATER_01") == DeviceStatus.ONLINE)
    finally:
        service.stop()

    sweeper = PresenceSweeper(timeout_seconds=0, interval_seconds=0.05)
    sweeper.start()
    try:
        assert wait_until(lambda: status_of("WATER_01") == DeviceStatus.OFFLINE)
    finally:
        sweeper.stop()


# --- the whole application -----------------------------------------------------


def test_app_receives_packets_over_uart_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = QueueSerial()
    monkeypatch.setenv("SRG_UART_ENABLED", "true")
    monkeypatch.setenv("SRG_UART_PORT", "FAKE")
    get_settings.cache_clear()

    original = UartService.from_settings.__func__  # type: ignore[attr-defined]

    def with_fake_port(cls: Any, on_line: Any, settings: Any = None, **kwargs: Any) -> Any:
        return original(
            cls, on_line, settings, serial_factory=lambda *a, **k: port, **kwargs
        )

    monkeypatch.setattr(UartService, "from_settings", classmethod(with_fake_port))

    with TestClient(app) as client:
        assert app.state.uart is not None
        assert app.state.uart.is_running

        assert client.post("/devices", json=WATER).status_code == 201
        assert client.get("/devices/WATER_01").json()["status"] == "offline"

        port.feed(pkt("WATER_01", flow_in=15.4))
        assert wait_until(
            lambda: client.get("/devices/WATER_01").json()["status"] == "online"
        )
        assert client.get("/devices/WATER_01").json()["last_seen"] is not None
        assert app.state.message_handler.stats()["packets_accepted"] == 1

    assert not app.state.uart.is_running
    assert port.closed


# --- serial monitor ------------------------------------------------------------


def test_monitor_describes_valid_packets() -> None:
    line = describe_line(pkt("water_01", seq=7, flow_in=15.4).decode())
    assert line == "OK   WATER_01 data seq=7 flow_in=15.4"


def test_monitor_describes_invalid_lines() -> None:
    assert describe_line("boot: ESP32-C3 ready").startswith("BAD  [invalid_json]")
    assert describe_line('{"v":2,"src":"WATER_01","type":"heartbeat","seq":1}').startswith(
        "BAD  [invalid_packet]"
    )