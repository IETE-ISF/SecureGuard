"""
test_power.py - Tests for power monitoring (Phase 5, Task 3).

Covers the whole path: UART packet -> MessageHandler -> PowerIngestor ->
database -> power API. Every test runs against its own temporary database file.
"""

import json
import queue
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.config import get_settings
from backend.database.base import utcnow
from backend.database.init_db import create_tables
from backend.database.session import dispose_engine, init_engine, session_scope
from backend.main import app
from backend.models import DeviceStatus, PowerReading
from backend.schemas.device import DeviceCreate
from backend.schemas.power import PowerPayload
from backend.services.device_service import delete_device, get_device, register_device
from backend.services.message_handler import MessageHandler
from backend.services.power_service import PowerIngestor, store_reading
from backend.services.uart_service import UartService

GOOD: dict[str, Any] = {
    "voltage": 229,
    "current": 7.2,
    "power": 1650,
    "energy": 14.2,
    "power_factor": 0.97,
}
MINIMAL: dict[str, Any] = {"voltage": 230, "current": 1, "power": 200, "energy": 1}
P1 = {"node_id": "POWER_01"}


@pytest.fixture(autouse=True)
def temp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Give every test a fresh database with all tables created."""
    monkeypatch.setenv("SRG_DATABASE_PATH", str(tmp_path / "power_test.db"))
    get_settings.cache_clear()
    dispose_engine()
    init_engine()
    create_tables()
    yield
    dispose_engine()
    get_settings.cache_clear()


# --- helpers -----------------------------------------------------------------


def register(node_id: str, kind: str = "power") -> None:
    with session_scope() as db:
        register_device(
            db, DeviceCreate(node_id=node_id, node_name=node_id, node_type=kind)
        )


def line(src: str = "POWER_01", type_: str = "data", seq: int = 1, **d: Any) -> str:
    """One wire-format packet line (no trailing newline)."""
    body: dict[str, Any] = {"v": 1, "src": src, "type": type_, "seq": seq}
    if d:
        body["d"] = d
    return json.dumps(body)


def packet_bytes(**kwargs: Any) -> bytes:
    return (line(**kwargs) + "\n").encode()


def rows() -> list[PowerReading]:
    with session_scope() as db:
        return list(db.scalars(select(PowerReading).order_by(PowerReading.id)))


def make_pipeline() -> tuple[MessageHandler, PowerIngestor]:
    handler = MessageHandler()
    ingestor = PowerIngestor()
    handler.add_listener(ingestor.handle_packet)
    return handler, ingestor


def iso(moment: datetime) -> str:
    """A UTC timestamp in the form the API expects, e.g. 2026-10-08T10:00:00Z."""
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def powers(response: Any) -> list[float]:
    assert response.status_code == 200, response.text
    return [item["power"] for item in response.json()]


def wait_until(predicate: Callable[[], bool], timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# --- ingestion: packet -> database -------------------------------------------


def test_valid_data_packet_is_stored() -> None:
    register("POWER_01")
    handler, ingestor = make_pipeline()

    handler.handle_line(line(**GOOD))

    stored = rows()
    assert len(stored) == 1
    reading = stored[0]
    assert (reading.voltage, reading.current) == (229, 7.2)
    assert (reading.power, reading.energy) == (1650, 14.2)
    assert reading.power_factor == 0.97
    assert reading.recorded_at.tzinfo == timezone.utc
    assert abs(utcnow() - reading.recorded_at) < timedelta(seconds=10)
    assert ingestor.stats() == {"readings_stored": 1}
    with session_scope() as db:
        assert get_device(db, "POWER_01").status == DeviceStatus.ONLINE


def test_power_factor_may_be_missing() -> None:
    register("POWER_01")
    handler, _ = make_pipeline()

    handler.handle_line(line(**MINIMAL))

    assert rows()[0].power_factor is None


INVALID_PAYLOADS: dict[str, dict[str, Any]] = {
    "negative current": {**GOOD, "current": -1},
    "missing field": {k: v for k, v in GOOD.items() if k != "energy"},
    "voltage too high": {**GOOD, "voltage": 301},
    "power factor too high": {**GOOD, "power_factor": 1.5},
    "door key": {**GOOD, "door": "open"},
    "numeric string": {**GOOD, "voltage": "229"},
    "boolean": {**GOOD, "voltage": True},
}


@pytest.mark.parametrize(
    "payload", INVALID_PAYLOADS.values(), ids=INVALID_PAYLOADS.keys()
)
def test_invalid_payloads_are_not_stored(payload: dict[str, Any]) -> None:
    register("POWER_01")
    handler, ingestor = make_pipeline()

    handler.handle_line(line(**payload))

    assert rows() == []
    assert ingestor.stats() == {"invalid_payload": 1}
    # A node that sends garbage is still alive.
    assert handler.stats()["packets_accepted"] == 1
    with session_scope() as db:
        assert get_device(db, "POWER_01").status == DeviceStatus.ONLINE


def test_heartbeat_and_event_packets_are_not_stored() -> None:
    register("POWER_01")
    handler, ingestor = make_pipeline()

    handler.handle_line(line(type_="heartbeat"))
    handler.handle_line(line(type_="event", seq=2, door="open"))

    assert rows() == []
    assert ingestor.stats() == {}


def test_data_from_other_node_types_is_ignored() -> None:
    register("WATER_01", "water")
    handler, ingestor = make_pipeline()

    handler.handle_line(
        line("WATER_01", flow_in=15.4, flow_out=11.2, temp_in=24.3, temp_out=28.9)
    )

    assert rows() == []
    assert ingestor.stats() == {}
    assert handler.stats()["packets_accepted"] == 1


def test_data_from_unregistered_device_is_ignored() -> None:
    handler, ingestor = make_pipeline()

    handler.handle_line(line("GHOST_99", **GOOD))

    assert rows() == []
    assert ingestor.stats() == {}
    assert handler.stats()["unknown_device"] == 1


def test_every_packet_creates_one_row() -> None:
    register("POWER_01")
    handler, _ = make_pipeline()

    for seq in (1, 2, 3):
        handler.handle_line(line(seq=seq, **{**MINIMAL, "power": float(seq * 100)}))

    stored = rows()
    assert [r.power for r in stored] == [100.0, 200.0, 300.0]
    times = [r.recorded_at for r in stored]
    assert times == sorted(times)


def test_store_reading_uses_the_given_timestamp() -> None:
    register("POWER_01")
    moment = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)

    with session_scope() as db:
        store_reading(db, get_device(db, "POWER_01"), PowerPayload(**MINIMAL), moment)

    assert rows()[0].recorded_at == moment


def test_deleting_a_device_deletes_its_readings() -> None:
    register("POWER_01")
    register("POWER_02")
    handler, _ = make_pipeline()
    handler.handle_line(line("POWER_01", **{**MINIMAL, "power": 100.0}))
    handler.handle_line(line("POWER_02", **{**MINIMAL, "power": 200.0}))

    with session_scope() as db:
        delete_device(db, "POWER_01")

    assert [r.power for r in rows()] == [200.0]


# --- API: reading the data back ----------------------------------------------


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def seeded(client: TestClient) -> datetime:
    """Readings for three power nodes and one water node; returns the reference 'now'."""
    now = utcnow()
    for node_id, kind in [
        ("POWER_01", "power"),
        ("POWER_02", "power"),
        ("POWER_03", "power"),
        ("WATER_01", "water"),
    ]:
        register(node_id, kind)

    def add(node_id: str, ago: timedelta, **values: Any) -> None:
        with session_scope() as db:
            store_reading(db, get_device(db, node_id), PowerPayload(**values), now - ago)

    add("POWER_01", timedelta(hours=48), voltage=230, current=5, power=1000, energy=10)
    add("POWER_01", timedelta(hours=3), voltage=228, current=6, power=1300, energy=11,
        power_factor=0.9)
    add("POWER_01", timedelta(hours=2), voltage=230, current=7, power=1500, energy=12.5,
        power_factor=0.95)
    add("POWER_01", timedelta(hours=1), voltage=232, current=8, power=1700, energy=14)
    add("POWER_02", timedelta(minutes=30), voltage=229, current=1, power=200, energy=1)
    return now


def test_latest_returns_newest_reading(client: TestClient, seeded: datetime) -> None:
    assert client.get("/power/latest").json()["power"] == 200.0

    body = client.get("/power/latest", params={"node_id": "power_01"}).json()
    assert body["power"] == 1700.0
    assert body["voltage"] == 232.0
    assert body["energy"] == 14.0
    assert body["power_factor"] is None


def test_latest_returns_404_when_nothing_is_stored(client: TestClient) -> None:
    response = client.get("/power/latest")
    assert response.status_code == 404
    assert response.json()["detail"] == "No power readings stored yet"


def test_latest_error_responses(client: TestClient, seeded: datetime) -> None:
    assert client.get("/power/latest", params={"node_id": "POWER_03"}).status_code == 404
    assert client.get("/power/latest", params={"node_id": "NOPE_99"}).status_code == 404
    assert client.get("/power/latest", params={"node_id": "WATER_01"}).status_code == 422


def test_readings_order_and_pagination(client: TestClient, seeded: datetime) -> None:
    assert powers(client.get("/power/readings", params=P1)) == [1700.0, 1500.0, 1300.0, 1000.0]
    assert powers(client.get("/power/readings", params={**P1, "order": "asc"})) == [
        1000.0, 1300.0, 1500.0, 1700.0,
    ]
    assert powers(client.get("/power/readings", params={**P1, "limit": 2, "offset": 1})) == [
        1500.0, 1300.0,
    ]
    assert len(client.get("/power/readings").json()) == 5  # every power node


def test_readings_time_range(client: TestClient, seeded: datetime) -> None:
    four_hours_ago = iso(seeded - timedelta(hours=4))
    ninety_minutes_ago = iso(seeded - timedelta(minutes=90))

    assert powers(client.get("/power/readings", params={**P1, "start": four_hours_ago})) == [
        1700.0, 1500.0, 1300.0,
    ]
    assert powers(client.get("/power/readings", params={**P1, "end": ninety_minutes_ago})) == [
        1500.0, 1300.0, 1000.0,
    ]
    both = {**P1, "start": four_hours_ago, "end": ninety_minutes_ago}
    assert powers(client.get("/power/readings", params=both)) == [1500.0, 1300.0]


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"start": "2026-10-08T10:00:00"}, id="naive-start"),
        pytest.param({"end": "2026-10-08T10:00:00"}, id="naive-end"),
        pytest.param(
            {"start": "2026-10-08T10:00:00Z", "end": "2026-10-08T09:00:00Z"},
            id="start-after-end",
        ),
        pytest.param({"limit": 0}, id="limit-zero"),
        pytest.param({"limit": 1001}, id="limit-too-big"),
        pytest.param({"offset": -1}, id="negative-offset"),
        pytest.param({"order": "sideways"}, id="bad-order"),
        pytest.param({"start": "yesterday"}, id="unparseable-time"),
        pytest.param({"node_id": "WATER_01"}, id="not-a-power-node"),
    ],
)
def test_readings_reject_bad_requests(
    client: TestClient, seeded: datetime, params: dict[str, Any]
) -> None:
    assert client.get("/power/readings", params=params).status_code == 422


def test_readings_unknown_node_returns_404(client: TestClient, seeded: datetime) -> None:
    assert client.get("/power/readings", params={"node_id": "NOPE_99"}).status_code == 404


def test_summary_defaults_to_last_24_hours(client: TestClient, seeded: datetime) -> None:
    summary = client.get("/power/summary", params={"node_id": "power_01"}).json()

    assert summary["node_id"] == "POWER_01"
    assert summary["count"] == 3  # the 48-hour-old reading is outside the window
    assert summary["power"] == {"avg": 1500.0, "min": 1300.0, "max": 1700.0}
    assert summary["voltage"] == {"avg": 230.0, "min": 228.0, "max": 232.0}
    assert summary["current"] == {"avg": 7.0, "min": 6.0, "max": 8.0}
    assert summary["energy"] == {"avg": 12.5, "min": 11.0, "max": 14.0}
    assert summary["power_factor"] == {"avg": 0.925, "min": 0.9, "max": 0.95}
    assert summary["first_recorded_at"] < summary["last_recorded_at"]


def test_summary_over_a_wider_window(client: TestClient, seeded: datetime) -> None:
    params = {**P1, "start": iso(seeded - timedelta(hours=72))}
    summary = client.get("/power/summary", params=params).json()
    assert summary["count"] == 4
    assert summary["power"]["avg"] == 1375.0
    assert summary["power"]["max"] == 1700.0


def test_summary_across_all_nodes(client: TestClient, seeded: datetime) -> None:
    summary = client.get("/power/summary").json()
    assert summary["node_id"] is None
    assert summary["count"] == 4


def test_summary_with_no_readings(client: TestClient, seeded: datetime) -> None:
    summary = client.get("/power/summary", params={"node_id": "POWER_03"}).json()
    assert summary["count"] == 0
    assert summary["power"] == {"avg": None, "min": None, "max": None}
    assert summary["first_recorded_at"] is None


def test_summary_leaves_missing_power_factor_empty(
    client: TestClient, seeded: datetime
) -> None:
    summary = client.get("/power/summary", params={"node_id": "POWER_02"}).json()
    assert summary["count"] == 1
    assert summary["power"]["avg"] == 200.0
    assert summary["power_factor"] == {"avg": None, "min": None, "max": None}


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        pytest.param({"node_id": "WATER_01"}, 422, id="not-a-power-node"),
        pytest.param({"node_id": "NOPE_99"}, 404, id="unknown-node"),
        pytest.param(
            {"start": "2026-10-08T10:00:00Z", "end": "2026-10-08T09:00:00Z"},
            422,
            id="start-after-end",
        ),
    ],
)
def test_summary_error_responses(
    client: TestClient, seeded: datetime, params: dict[str, Any], expected: int
) -> None:
    assert client.get("/power/summary", params=params).status_code == expected


# --- end to end: a fake ESP32 through the whole application ------------------


class QueueSerial:
    """A fake serial port the test feeds bytes into, like an ESP32 transmitting."""

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


def test_packet_sent_over_uart_is_served_by_the_power_api(
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

    with TestClient(app) as http:
        created = http.post(
            "/devices",
            json={"node_id": "POWER_01", "node_name": "Power Node", "node_type": "power"},
        )
        assert created.status_code == 201
        assert http.get("/power/latest").status_code == 404

        port.feed(packet_bytes(**GOOD))
        assert wait_until(lambda: http.get("/power/latest").status_code == 200)

        latest = http.get("/power/latest").json()
        assert (latest["voltage"], latest["current"]) == (229.0, 7.2)
        assert (latest["power"], latest["energy"]) == (1650.0, 14.2)
        assert latest["power_factor"] == 0.97
        assert http.get("/devices/POWER_01").json()["status"] == "online"

        port.feed(packet_bytes(seq=2, **{**GOOD, "voltage": 999}))
        ingestor = app.state.power_ingestor
        assert wait_until(lambda: ingestor.stats().get("invalid_payload") == 1)
        assert ingestor.stats()["readings_stored"] == 1
        assert len(http.get("/power/readings").json()) == 1

    assert port.closed
