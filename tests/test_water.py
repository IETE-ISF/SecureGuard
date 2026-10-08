"""
test_water.py - Tests for water monitoring (Phase 4, Task 5).

Covers the whole path: UART packet -> MessageHandler -> WaterIngestor ->
database -> water API. Every test runs against its own temporary database file.
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
from backend.models import DeviceStatus, WaterReading
from backend.schemas.device import DeviceCreate
from backend.schemas.water import WaterPayload
from backend.services.device_service import delete_device, get_device, register_device
from backend.services.message_handler import MessageHandler
from backend.services.uart_service import UartService
from backend.services.water_service import WaterIngestor, store_reading

GOOD: dict[str, Any] = {
    "flow_in": 15.4,
    "flow_out": 11.2,
    "temp_in": 24.3,
    "temp_out": 28.9,
    "humidity": 58,
    "pressure": 1013.2,
}
MINIMAL: dict[str, Any] = {"flow_in": 1, "flow_out": 0.5, "temp_in": 20, "temp_out": 21}
W1 = {"node_id": "WATER_01"}


@pytest.fixture(autouse=True)
def temp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Give every test a fresh database with all tables created."""
    monkeypatch.setenv("SRG_DATABASE_PATH", str(tmp_path / "water_test.db"))
    get_settings.cache_clear()
    dispose_engine()
    init_engine()
    create_tables()
    yield
    dispose_engine()
    get_settings.cache_clear()


# --- helpers -----------------------------------------------------------------


def register(node_id: str, kind: str = "water") -> None:
    with session_scope() as db:
        register_device(
            db, DeviceCreate(node_id=node_id, node_name=node_id, node_type=kind)
        )


def line(src: str = "WATER_01", type_: str = "data", seq: int = 1, **d: Any) -> str:
    """One wire-format packet line (no trailing newline)."""
    body: dict[str, Any] = {"v": 1, "src": src, "type": type_, "seq": seq}
    if d:
        body["d"] = d
    return json.dumps(body)


def packet_bytes(**kwargs: Any) -> bytes:
    return (line(**kwargs) + "\n").encode()


def rows() -> list[WaterReading]:
    with session_scope() as db:
        return list(db.scalars(select(WaterReading).order_by(WaterReading.id)))


def make_pipeline() -> tuple[MessageHandler, WaterIngestor]:
    handler = MessageHandler()
    ingestor = WaterIngestor()
    handler.add_listener(ingestor.handle_packet)
    return handler, ingestor


def iso(moment: datetime) -> str:
    """A UTC timestamp in the form the API expects, e.g. 2026-10-08T10:00:00Z."""
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def flows(response: Any) -> list[float]:
    assert response.status_code == 200, response.text
    return [item["flow_in"] for item in response.json()]


def wait_until(predicate: Callable[[], bool], timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# --- ingestion: packet -> database -------------------------------------------


def test_valid_data_packet_is_stored() -> None:
    register("WATER_01")
    handler, ingestor = make_pipeline()

    handler.handle_line(line(**GOOD))

    stored = rows()
    assert len(stored) == 1
    reading = stored[0]
    assert (reading.flow_in, reading.flow_out) == (15.4, 11.2)
    assert (reading.temp_in, reading.temp_out) == (24.3, 28.9)
    assert (reading.humidity, reading.pressure) == (58, 1013.2)
    assert reading.recorded_at.tzinfo == timezone.utc
    assert abs(utcnow() - reading.recorded_at) < timedelta(seconds=10)
    assert ingestor.stats() == {"readings_stored": 1}
    with session_scope() as db:
        assert get_device(db, "WATER_01").status == DeviceStatus.ONLINE


def test_optional_fields_may_be_missing() -> None:
    register("WATER_01")
    handler, _ = make_pipeline()

    handler.handle_line(line(**MINIMAL))

    reading = rows()[0]
    assert reading.humidity is None
    assert reading.pressure is None


INVALID_PAYLOADS: dict[str, dict[str, Any]] = {
    "negative flow": {**GOOD, "flow_in": -1},
    "missing field": {k: v for k, v in GOOD.items() if k != "flow_out"},
    "temp too high": {**GOOD, "temp_out": 200},
    "humidity too high": {**GOOD, "humidity": 101},
    "unknown key": {**GOOD, "rssi": -60},
    "numeric string": {**GOOD, "flow_in": "15.4"},
    "boolean": {**GOOD, "flow_in": True},
}


@pytest.mark.parametrize(
    "payload", INVALID_PAYLOADS.values(), ids=INVALID_PAYLOADS.keys()
)
def test_invalid_payloads_are_not_stored(payload: dict[str, Any]) -> None:
    register("WATER_01")
    handler, ingestor = make_pipeline()

    handler.handle_line(line(**payload))

    assert rows() == []
    assert ingestor.stats() == {"invalid_payload": 1}
    # A node that sends garbage is still alive.
    assert handler.stats()["packets_accepted"] == 1
    with session_scope() as db:
        assert get_device(db, "WATER_01").status == DeviceStatus.ONLINE


def test_heartbeat_and_event_packets_are_not_stored() -> None:
    register("WATER_01")
    handler, ingestor = make_pipeline()

    handler.handle_line(line(type_="heartbeat"))
    handler.handle_line(line(type_="event", seq=2, door="open"))

    assert rows() == []
    assert ingestor.stats() == {}


def test_data_from_other_node_types_is_ignored() -> None:
    register("POWER_01", "power")
    handler, ingestor = make_pipeline()

    handler.handle_line(line("POWER_01", voltage=229, current=7.2))

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
    register("WATER_01")
    handler, _ = make_pipeline()

    for seq in (1, 2, 3):
        handler.handle_line(line(seq=seq, **{**MINIMAL, "flow_in": float(seq)}))

    stored = rows()
    assert [r.flow_in for r in stored] == [1.0, 2.0, 3.0]
    times = [r.recorded_at for r in stored]
    assert times == sorted(times)


def test_store_reading_uses_the_given_timestamp() -> None:
    register("WATER_01")
    moment = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)

    with session_scope() as db:
        store_reading(db, get_device(db, "WATER_01"), WaterPayload(**MINIMAL), moment)

    assert rows()[0].recorded_at == moment


def test_deleting_a_device_deletes_its_readings() -> None:
    register("WATER_01")
    register("WATER_02")
    handler, _ = make_pipeline()
    handler.handle_line(line("WATER_01", **{**MINIMAL, "flow_in": 1.0}))
    handler.handle_line(line("WATER_02", **{**MINIMAL, "flow_in": 2.0}))

    with session_scope() as db:
        delete_device(db, "WATER_01")

    assert [r.flow_in for r in rows()] == [2.0]


# --- API: reading the data back ----------------------------------------------


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def seeded(client: TestClient) -> datetime:
    """Four water nodes' worth of readings; returns the reference 'now'."""
    now = utcnow()
    for node_id, kind in [
        ("WATER_01", "water"),
        ("WATER_02", "water"),
        ("WATER_03", "water"),
        ("POWER_01", "power"),
    ]:
        register(node_id, kind)

    def add(node_id: str, ago: timedelta, **values: Any) -> None:
        with session_scope() as db:
            store_reading(db, get_device(db, node_id), WaterPayload(**values), now - ago)

    add("WATER_01", timedelta(hours=48), flow_in=20, flow_out=0, temp_in=20, temp_out=30)
    add("WATER_01", timedelta(hours=3), flow_in=10, flow_out=8, temp_in=20, temp_out=24,
        humidity=50, pressure=1000)
    add("WATER_01", timedelta(hours=2), flow_in=14, flow_out=10, temp_in=22, temp_out=28,
        humidity=60, pressure=1010)
    add("WATER_01", timedelta(hours=1), flow_in=12, flow_out=11, temp_in=21, temp_out=22)
    add("WATER_02", timedelta(minutes=30), flow_in=5, flow_out=5, temp_in=19, temp_out=19.5)
    return now


def test_latest_returns_newest_reading(client: TestClient, seeded: datetime) -> None:
    assert client.get("/water/latest").json()["flow_in"] == 5.0

    body = client.get("/water/latest", params={"node_id": "water_01"}).json()
    assert body["flow_in"] == 12.0
    assert body["consumption"] == 1.0
    assert body["delta_temp"] == 1.0
    assert body["humidity"] is None


def test_latest_returns_404_when_nothing_is_stored(client: TestClient) -> None:
    response = client.get("/water/latest")
    assert response.status_code == 404
    assert response.json()["detail"] == "No water readings stored yet"


def test_latest_error_responses(client: TestClient, seeded: datetime) -> None:
    assert client.get("/water/latest", params={"node_id": "WATER_03"}).status_code == 404
    assert client.get("/water/latest", params={"node_id": "NOPE_99"}).status_code == 404
    assert client.get("/water/latest", params={"node_id": "POWER_01"}).status_code == 422


def test_readings_order_and_pagination(client: TestClient, seeded: datetime) -> None:
    assert flows(client.get("/water/readings", params=W1)) == [12.0, 14.0, 10.0, 20.0]
    assert flows(client.get("/water/readings", params={**W1, "order": "asc"})) == [
        20.0, 10.0, 14.0, 12.0,
    ]
    assert flows(client.get("/water/readings", params={**W1, "limit": 2, "offset": 1})) == [
        14.0, 10.0,
    ]
    assert len(client.get("/water/readings").json()) == 5  # every water node


def test_readings_time_range(client: TestClient, seeded: datetime) -> None:
    four_hours_ago = iso(seeded - timedelta(hours=4))
    ninety_minutes_ago = iso(seeded - timedelta(minutes=90))

    assert flows(client.get("/water/readings", params={**W1, "start": four_hours_ago})) == [
        12.0, 14.0, 10.0,
    ]
    assert flows(client.get("/water/readings", params={**W1, "end": ninety_minutes_ago})) == [
        14.0, 10.0, 20.0,
    ]
    both = {**W1, "start": four_hours_ago, "end": ninety_minutes_ago}
    assert flows(client.get("/water/readings", params=both)) == [14.0, 10.0]


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
        pytest.param({"node_id": "POWER_01"}, id="not-a-water-node"),
    ],
)
def test_readings_reject_bad_requests(
    client: TestClient, seeded: datetime, params: dict[str, Any]
) -> None:
    assert client.get("/water/readings", params=params).status_code == 422


def test_readings_unknown_node_returns_404(client: TestClient, seeded: datetime) -> None:
    assert client.get("/water/readings", params={"node_id": "NOPE_99"}).status_code == 404


def test_summary_defaults_to_last_24_hours(client: TestClient, seeded: datetime) -> None:
    summary = client.get("/water/summary", params={"node_id": "water_01"}).json()

    assert summary["node_id"] == "WATER_01"
    assert summary["count"] == 3  # the 48-hour-old reading is outside the window
    assert summary["flow_in"] == {"avg": 12.0, "min": 10.0, "max": 14.0}
    assert summary["consumption"] == {"avg": 2.333, "min": 1.0, "max": 4.0}
    assert summary["delta_temp"] == {"avg": 3.667, "min": 1.0, "max": 6.0}
    assert summary["humidity"] == {"avg": 55.0, "min": 50.0, "max": 60.0}
    assert summary["pressure"] == {"avg": 1005.0, "min": 1000.0, "max": 1010.0}
    assert summary["first_recorded_at"] < summary["last_recorded_at"]


def test_summary_over_a_wider_window(client: TestClient, seeded: datetime) -> None:
    params = {**W1, "start": iso(seeded - timedelta(hours=72))}
    summary = client.get("/water/summary", params=params).json()
    assert summary["count"] == 4
    assert summary["flow_in"]["avg"] == 14.0
    assert summary["flow_in"]["max"] == 20.0


def test_summary_across_all_nodes(client: TestClient, seeded: datetime) -> None:
    summary = client.get("/water/summary").json()
    assert summary["node_id"] is None
    assert summary["count"] == 4


def test_summary_with_no_readings(client: TestClient, seeded: datetime) -> None:
    summary = client.get("/water/summary", params={"node_id": "WATER_03"}).json()
    assert summary["count"] == 0
    assert summary["flow_in"] == {"avg": None, "min": None, "max": None}
    assert summary["first_recorded_at"] is None


def test_summary_leaves_missing_optional_metrics_empty(
    client: TestClient, seeded: datetime
) -> None:
    summary = client.get("/water/summary", params={"node_id": "WATER_02"}).json()
    assert summary["count"] == 1
    assert summary["flow_in"]["avg"] == 5.0
    assert summary["humidity"] == {"avg": None, "min": None, "max": None}


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        pytest.param({"node_id": "POWER_01"}, 422, id="not-a-water-node"),
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
    assert client.get("/water/summary", params=params).status_code == expected


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


def test_packet_sent_over_uart_is_served_by_the_water_api(
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
            json={"node_id": "WATER_01", "node_name": "Water Node", "node_type": "water"},
        )
        assert created.status_code == 201
        assert http.get("/water/latest").status_code == 404

        port.feed(packet_bytes(**GOOD))
        assert wait_until(lambda: http.get("/water/latest").status_code == 200)

        latest = http.get("/water/latest").json()
        assert (latest["flow_in"], latest["humidity"], latest["pressure"]) == (15.4, 58.0, 1013.2)
        assert latest["consumption"] == 4.2
        assert latest["delta_temp"] == 4.6
        assert http.get("/devices/WATER_01").json()["status"] == "online"

        port.feed(packet_bytes(seq=2, **{**GOOD, "flow_in": -5}))
        ingestor = app.state.water_ingestor
        assert wait_until(lambda: ingestor.stats().get("invalid_payload") == 1)
        assert ingestor.stats()["readings_stored"] == 1
        assert len(http.get("/water/readings").json()) == 1

    assert port.closed