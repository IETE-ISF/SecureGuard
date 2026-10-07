"""
message_handler.py - Handles packets arriving over UART (Phase 3, Task 4).

handle_line() is the callback given to UartService. For every line it:
  1. parses and validates it (rejects are counted by reason, never raised)
  2. records a heartbeat for the sending device (last_seen, offline -> online)
  3. passes the packet to any registered listeners

Unknown devices are dropped, not auto-registered: devices enter the
registry only through the API.

Later phases subscribe to packets with add_listener(), for example the
water storage service in Phase 4.
"""

import logging
import threading
from collections.abc import Callable

from backend.database.session import session_scope
from backend.services.device_service import DeviceNotFoundError, record_heartbeat
from backend.services.packet_parser import Packet, PacketError, parse_packet

logger = logging.getLogger(__name__)

PacketListener = Callable[[Packet], None]

LOG_FIRST_N = 5
LOG_EVERY_NTH = 100


def should_log(count: int) -> bool:
    """Log the first few occurrences, then every 100th, so bad input cannot flood the log."""
    return count <= LOG_FIRST_N or count % LOG_EVERY_NTH == 0


class MessageHandler:
    """Turns raw UART lines into device-registry updates."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stats: dict[str, int] = {}
        self._listeners: list[PacketListener] = []

    def add_listener(self, listener: PacketListener) -> None:
        """Register a function called with every accepted packet."""
        self._listeners.append(listener)

    def stats(self) -> dict[str, int]:
        """Return a snapshot of the counters (accepted, rejected by reason, and so on)."""
        with self._lock:
            return dict(self._stats)

    def handle_line(self, line: str) -> None:
        """Process one line from the serial port."""
        try:
            packet = parse_packet(line)
        except PacketError as exc:
            count = self._count(f"rejected_{exc.reason}")
            if should_log(count):
                logger.warning(
                    "Rejected line (%s, #%d): %s | %r",
                    exc.reason,
                    count,
                    exc.detail,
                    line[:80],
                )
            return

        try:
            with session_scope() as db:
                record_heartbeat(db, packet.source)
        except DeviceNotFoundError:
            count = self._count("unknown_device")
            if should_log(count):
                logger.warning(
                    "Packet from unregistered device %s ignored (#%d)",
                    packet.source,
                    count,
                )
            return

        self._count("packets_accepted")
        self._count(f"packets_{packet.packet_type.value}")
        self._notify(packet)

    def _notify(self, packet: Packet) -> None:
        for listener in list(self._listeners):
            try:
                listener(packet)
            except Exception:
                self._count("listener_errors")
                logger.exception("Packet listener failed for %s", packet.source)

    def _count(self, name: str) -> int:
        with self._lock:
            self._stats[name] = self._stats.get(name, 0) + 1
            return self._stats[name]