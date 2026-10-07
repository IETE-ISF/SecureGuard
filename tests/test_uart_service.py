"""
test_uart_service.py - Tests for the UART reader (Phase 3, Task 2).

No hardware needed: a scripted FakeSerial stands in for serial.Serial.
"""

import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from serial import SerialException

from backend.config import Settings
from backend.services.uart_service import LineBuffer, UartService


class FakeSerial:
    """Scripted serial port: each read() returns the next item, or raises it if it is an exception."""

    in_waiting = 0

    def __init__(self, script: list[bytes | Exception]) -> None:
        self._script: deque[bytes | Exception] = deque(script)
        self.closed = False

    def read(self, size: int = 1) -> bytes:
        if self._script:
            item = self._script.popleft()
            if isinstance(item, Exception):
                raise item
            return item
        time.sleep(0.01)  # behave like an idle port hitting its read timeout
        return b""

    def close(self) -> None:
        self.closed = True


def scripted_factory(*attempts: FakeSerial | Exception) -> Callable[..., Any]:
    """Factory returning (or raising) each attempt in turn, then idle fake ports."""
    queue: deque[FakeSerial | Exception] = deque(attempts)
    calls: list[tuple[str, int, float]] = []

    def factory(port: str, baudrate: int, timeout: float) -> FakeSerial:
        calls.append((port, baudrate, timeout))
        attempt = queue.popleft() if queue else FakeSerial([])
        if isinstance(attempt, Exception):
            raise attempt
        return attempt

    factory.calls = calls  # type: ignore[attr-defined]
    return factory


def make_service(
    factory: Callable[..., Any], on_line: Callable[[str], None]
) -> UartService:
    return UartService(
        "FAKE",
        115200,
        0.05,
        on_line,
        reconnect_delay=0.05,
        serial_factory=factory,
    )


def wait_until(predicate: Callable[[], bool], timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# --- LineBuffer --------------------------------------------------------------


def test_line_buffer_joins_partial_chunks_and_skips_blank_lines() -> None:
    buffer = LineBuffer()
    assert buffer.feed(b'{"a":1}\r\n{"b"') == ['{"a":1}']
    assert buffer.feed(b':2}\n\n  \n') == ['{"b":2}']


def test_line_buffer_replaces_invalid_utf8() -> None:
    assert LineBuffer().feed(b"ab\xffcd\n") == ["ab\ufffdcd"]


def test_line_buffer_drops_overlong_lines() -> None:
    buffer = LineBuffer(max_line_bytes=10)
    assert buffer.feed(b"x" * 20) == []  # runaway line with no newline
    assert buffer.overflows == 1
    assert buffer.feed(b"yyy\nok\n") == ["ok"]  # tail of the bad line is dropped too
    assert buffer.overflows == 1
    assert buffer.feed(b"z" * 20 + b"\nok2\n") == ["ok2"]  # complete but too long
    assert buffer.overflows == 2


# --- UartService -------------------------------------------------------------


def test_service_delivers_lines_in_order() -> None:
    received: list[str] = []
    factory = scripted_factory(FakeSerial([b'{"n":1}\n{"n":', b"2}\n"]))
    service = make_service(factory, received.append)
    service.start()
    try:
        assert wait_until(lambda: len(received) == 2)
    finally:
        service.stop()

    assert received == ['{"n":1}', '{"n":2}']
    assert factory.calls[0] == ("FAKE", 115200, 0.05)  # type: ignore[attr-defined]
    stats = service.stats()
    assert stats["lines_received"] == 2
    assert stats["bytes_received"] == len(b'{"n":1}\n{"n":') + len(b"2}\n")
    assert stats["last_line_at"] is not None
    assert not service.is_running


def test_service_retries_when_port_cannot_be_opened() -> None:
    received: list[str] = []
    factory = scripted_factory(
        SerialException("no such port"),
        SerialException("no such port"),
        FakeSerial([b"hello\n"]),
    )
    service = make_service(factory, received.append)
    service.start()
    try:
        assert wait_until(lambda: received == ["hello"])
        assert service.is_connected
    finally:
        service.stop()

    stats = service.stats()
    assert stats["open_failures"] == 2
    assert stats["connections"] == 1


def test_service_reconnects_after_connection_loss() -> None:
    received: list[str] = []
    first = FakeSerial([b"one\n", SerialException("device unplugged")])
    second = FakeSerial([b"two\n"])
    service = make_service(scripted_factory(first, second), received.append)
    service.start()
    try:
        assert wait_until(lambda: received == ["one", "two"])
    finally:
        service.stop()

    assert first.closed
    assert service.stats()["connections"] == 2


def test_failing_callback_does_not_stop_the_reader() -> None:
    received: list[str] = []

    def on_line(line: str) -> None:
        if line == "bad":
            raise RuntimeError("boom")
        received.append(line)

    service = make_service(scripted_factory(FakeSerial([b"bad\ngood\n"])), on_line)
    service.start()
    try:
        assert wait_until(lambda: received == ["good"])
    finally:
        service.stop()

    assert service.stats()["callback_errors"] == 1


def test_stop_is_safe_to_call_repeatedly() -> None:
    service = make_service(scripted_factory(FakeSerial([])), lambda line: None)
    service.stop()  # never started
    service.start()
    assert wait_until(lambda: service.is_connected)
    service.stop()
    service.stop()
    assert not service.is_running
    assert not service.is_connected


def test_start_twice_runs_a_single_thread() -> None:
    service = make_service(scripted_factory(FakeSerial([])), lambda line: None)
    service.start()
    service.start()
    try:
        threads = [t for t in threading.enumerate() if t.name == "uart-reader"]
        assert len(threads) == 1
    finally:
        service.stop()


def test_from_settings_uses_configured_port_and_baudrate() -> None:
    settings = Settings(
        uart_enabled=True, uart_port="TTY1", uart_baudrate=9600, uart_read_timeout=2.0
    )
    factory = scripted_factory(FakeSerial([]))
    service = UartService.from_settings(
        lambda line: None, settings, serial_factory=factory
    )
    service.start()
    try:
        assert wait_until(lambda: service.is_connected)
    finally:
        service.stop()

    assert factory.calls[0] == ("TTY1", 9600, 2.0)  # type: ignore[attr-defined]