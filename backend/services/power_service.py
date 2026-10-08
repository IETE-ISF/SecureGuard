"""
power_service.py - Stores power readings (Phase 5, Task 2).

PowerIngestor is registered as a listener on the MessageHandler. For every
"data" packet from a device of type "power" it validates the payload with
PowerPayload and stores a PowerReading. Invalid payloads are counted and
logged, never stored. Packets of any other kind are ignored here.

    handler.add_listener(PowerIngestor().handle_packet)
"""

import logging
import threading
from datetime import datetime

from pydantic import ValidationError
from sqlalchemy.orm import Session

from backend.database.base import utcnow
from backend.database.session import session_scope
from backend.models.device import Device, DeviceType
from backend.models.power import PowerReading
from backend.schemas.power import PowerPayload
from backend.services.device_service import find_device
from backend.services.message_handler import should_log
from backend.services.packet_parser import Packet, PacketType

logger = logging.getLogger(__name__)


def store_reading(
    db: Session,
    device: Device,
    payload: PowerPayload,
    recorded_at: datetime | None = None,
) -> PowerReading:
    """Store one validated reading for a device. recorded_at defaults to now (UTC)."""
    reading = PowerReading(
        device_id=device.id,
        recorded_at=recorded_at or utcnow(),
        **payload.model_dump(),
    )
    db.add(reading)
    db.commit()
    db.refresh(reading)
    return reading


def _describe(error: ValidationError, limit: int = 3) -> str:
    """Condense a validation error to the first few 'field: message' pairs."""
    items = error.errors()
    parts = [
        f"{'.'.join(str(part) for part in item['loc']) or 'payload'}: {item['msg']}"
        for item in items[:limit]
    ]
    if len(items) > limit:
        parts.append(f"(+{len(items) - limit} more)")
    return "; ".join(parts)


class PowerIngestor:
    """Turns power-node data packets into stored readings."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stats: dict[str, int] = {}

    def stats(self) -> dict[str, int]:
        """Return a snapshot of the counters (readings_stored, invalid_payload)."""
        with self._lock:
            return dict(self._stats)

    def handle_packet(self, packet: Packet) -> None:
        """Listener for MessageHandler: store the packet if it is a power reading."""
        if packet.packet_type != PacketType.DATA:
            return

        with session_scope() as db:
            device = find_device(db, packet.source)
            if device is None or device.node_type != DeviceType.POWER:
                return  # not ours: another service handles other node types

            try:
                payload = PowerPayload.model_validate(packet.payload)
            except ValidationError as exc:
                count = self._count("invalid_payload")
                if should_log(count):
                    logger.warning(
                        "Invalid power payload from %s (#%d): %s",
                        packet.source,
                        count,
                        _describe(exc),
                    )
                return

            store_reading(db, device, payload)

        self._count("readings_stored")

    def _count(self, name: str) -> int:
        with self._lock:
            self._stats[name] = self._stats.get(name, 0) + 1
            return self._stats[name]