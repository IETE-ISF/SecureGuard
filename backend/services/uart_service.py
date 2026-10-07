"""
uart_service.py - Background UART reader (Phase 3, Task 2).

Reads newline-terminated lines from the serial port on a daemon thread and
passes each decoded line to a callback. Packet parsing and database work
are done elsewhere: this module only moves lines from the wire to Python.

    service = UartService.from_settings(on_line=handle_line)
    service.start()
    ...
    service.stop()

The thread reconnects automatically if the port cannot be opened or the
connection drops. An exception raised by the callback is logged and does
not stop the reader.
"""

import logging
import threading
from collections.abc import Callable
from datetime import datetime
from typing import Any

import serial

from backend.config import Settings, get_settings
from backend.database.base import utcnow

logger = logging.getLogger(__name__)

DEFAULT_RECONNECT_DELAY = 5.0
DEFAULT_MAX_LINE_BYTES = 1024
STOP_TIMEOUT_SECONDS = 5.0

LineCallback = Callable[[str], None]
SerialFactory = Callable[..., Any]


class LineBuffer:
    """
    Turns a stream of bytes into complete, decoded text lines.

    * Handles lines split across several reads.
    * Strips whitespace and drops empty lines (so \\r\\n works).
    * Decodes UTF-8 with replacement characters instead of raising.
    * Drops any line longer than max_line_bytes, including a runaway
      line with no newline, and counts it in `overflows`.
    """

    def __init__(self, max_line_bytes: int = DEFAULT_MAX_LINE_BYTES) -> None:
        self._max_line_bytes = max_line_bytes
        self._buffer = bytearray()
        self._discarding = False
        self.overflows = 0

    def feed(self, data: bytes) -> list[str]:
        """Add bytes and return every complete line they finished."""
        self._buffer.extend(data)
        lines: list[str] = []

        while (index := self._buffer.find(b"\n")) >= 0:
            raw = bytes(self._buffer[:index])
            del self._buffer[: index + 1]

            if self._discarding:  # tail of a line that already overflowed
                self._discarding = False
                continue
            if len(raw) > self._max_line_bytes:
                self.overflows += 1
                continue

            text = raw.decode("utf-8", errors="replace").strip()
            if text:
                lines.append(text)

        if len(self._buffer) > self._max_line_bytes:
            self._buffer.clear()
            self._discarding = True
            self.overflows += 1

        return lines


class UartService:
    """Reads lines from a serial port on a background thread."""

    def __init__(
        self,
        port: str,
        baudrate: int,
        read_timeout: float,
        on_line: LineCallback,
        *,
        reconnect_delay: float = DEFAULT_RECONNECT_DELAY,
        max_line_bytes: int = DEFAULT_MAX_LINE_BYTES,
        serial_factory: SerialFactory = serial.Serial,
    ) -> None:
        self._port = port
        self._baudrate = baudrate
        self._read_timeout = read_timeout
        self._on_line = on_line
        self._reconnect_delay = reconnect_delay
        self._max_line_bytes = max_line_bytes
        self._serial_factory = serial_factory

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        self._lock = threading.Lock()
        self._connected = False
        self._last_line_at: datetime | None = None
        self._stats: dict[str, int] = {
            "connections": 0,
            "open_failures": 0,
            "lines_received": 0,
            "bytes_received": 0,
            "callback_errors": 0,
            "dropped_lines": 0,
        }

    @classmethod
    def from_settings(
        cls,
        on_line: LineCallback,
        settings: Settings | None = None,
        **kwargs: Any,
    ) -> "UartService":
        """Build a service from SRG_UART_* settings."""
        settings = settings or get_settings()
        if not settings.uart_port:
            raise ValueError("SRG_UART_PORT is not set")
        return cls(
            settings.uart_port,
            settings.uart_baudrate,
            settings.uart_read_timeout,
            on_line,
            **kwargs,
        )

    # --- public API --------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """True while the reader thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    @property
    def is_connected(self) -> bool:
        """True while the serial port is open."""
        with self._lock:
            return self._connected

    def stats(self) -> dict[str, Any]:
        """Return a snapshot of the counters, connection state and last line time."""
        with self._lock:
            snapshot: dict[str, Any] = dict(self._stats)
            snapshot["connected"] = self._connected
            snapshot["last_line_at"] = self._last_line_at
        return snapshot

    def start(self) -> None:
        """Start the reader thread. Does nothing if it is already running."""
        if self.is_running:
            logger.warning("UART service is already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="uart-reader", daemon=True
        )
        self._thread.start()
        logger.info("UART service started (port=%s, baudrate=%d)", self._port, self._baudrate)

    def stop(self, timeout: float = STOP_TIMEOUT_SECONDS) -> None:
        """Stop the reader thread and wait for it. Safe to call more than once."""
        thread = self._thread
        if thread is None:
            return
        self._stop_event.set()
        thread.join(timeout)
        if thread.is_alive():
            logger.warning("UART reader thread did not stop within %.1fs", timeout)
            return
        self._thread = None
        logger.info("UART service stopped")

    # --- reader thread -----------------------------------------------------

    def _run(self) -> None:
        """Open the port, read until it fails, wait, repeat until stopped."""
        last_open_error: str | None = None

        while not self._stop_event.is_set():
            try:
                connection = self._serial_factory(
                    self._port, self._baudrate, timeout=self._read_timeout
                )
            except (serial.SerialException, OSError) as exc:
                self._count("open_failures")
                message = str(exc)
                if message != last_open_error:  # log a repeating failure once
                    logger.warning(
                        "Cannot open %s: %s (retrying every %.1fs)",
                        self._port,
                        message,
                        self._reconnect_delay,
                    )
                    last_open_error = message
                self._stop_event.wait(self._reconnect_delay)
                continue

            last_open_error = None
            self._set_connected(True)
            logger.info("Serial port %s opened", self._port)
            try:
                self._read_loop(connection)
            except (serial.SerialException, OSError) as exc:
                logger.error("Serial connection lost on %s: %s", self._port, exc)
            except Exception:
                logger.exception("Unexpected error in UART reader")
            finally:
                self._set_connected(False)
                self._close(connection)

            self._stop_event.wait(self._reconnect_delay)

    def _read_loop(self, connection: Any) -> None:
        """Read chunks, split them into lines and dispatch each line."""
        buffer = LineBuffer(self._max_line_bytes)
        seen_overflows = 0

        while not self._stop_event.is_set():
            chunk = connection.read(connection.in_waiting or 1)
            if not chunk:  # read timeout, no data
                continue

            self._count("bytes_received", len(chunk))
            for line in buffer.feed(chunk):
                self._dispatch(line)

            if buffer.overflows > seen_overflows:
                dropped = buffer.overflows - seen_overflows
                seen_overflows = buffer.overflows
                self._count("dropped_lines", dropped)
                logger.warning("Dropped %d overlong line(s) from %s", dropped, self._port)

    def _dispatch(self, line: str) -> None:
        """Pass one line to the callback; a failing callback never stops the reader."""
        with self._lock:
            self._stats["lines_received"] += 1
            self._last_line_at = utcnow()
        try:
            self._on_line(line)
        except Exception:
            self._count("callback_errors")
            logger.exception("UART line callback failed for line: %.100s", line)

    # --- helpers -----------------------------------------------------------

    def _count(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._stats[name] += amount

    def _set_connected(self, connected: bool) -> None:
        with self._lock:
            self._connected = connected
            if connected:
                self._stats["connections"] += 1

    @staticmethod
    def _close(connection: Any) -> None:
        try:
            connection.close()
        except Exception:
            logger.debug("Error while closing serial port", exc_info=True)