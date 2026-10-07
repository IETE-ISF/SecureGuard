"""
uart_monitor.py - Live serial monitor for the master ESP32 link (Phase 3, Task 5).

Prints every line received on a serial port and whether it is a valid
packet. No database is touched, so it is safe to run against real hardware
before enabling UART in the app.

    python -m backend.services.uart_monitor --port COM7
    python -m backend.services.uart_monitor --port /dev/serial0

Press Ctrl+C to stop.
"""

import argparse
import time
from collections.abc import Sequence

from backend.config import get_settings
from backend.services.packet_parser import PacketError, parse_packet
from backend.services.uart_service import UartService


def describe_line(line: str) -> str:
    """Return a one-line summary: 'OK ...' for a valid packet, 'BAD ...' otherwise."""
    try:
        packet = parse_packet(line)
    except PacketError as exc:
        return f"BAD  [{exc.reason}] {exc.detail} | {line[:80]!r}"
    payload = ", ".join(f"{key}={value}" for key, value in packet.payload.items()) or "-"
    return (
        f"OK   {packet.source} {packet.packet_type.value} "
        f"seq={packet.sequence} {payload}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the monitor until Ctrl+C."""
    settings = get_settings()

    parser = argparse.ArgumentParser(
        description="Show every line arriving on the serial port and whether it is a valid packet."
    )
    parser.add_argument(
        "--port",
        default=settings.uart_port or None,
        help="serial port (default: SRG_UART_PORT), e.g. COM7 or /dev/serial0",
    )
    parser.add_argument(
        "--baudrate",
        type=int,
        default=settings.uart_baudrate,
        help="baud rate (default: SRG_UART_BAUDRATE, 115200)",
    )
    args = parser.parse_args(argv)
    if not args.port:
        parser.error("no port given: use --port or set SRG_UART_PORT")

    service = UartService(
        args.port,
        args.baudrate,
        settings.uart_read_timeout,
        lambda line: print(describe_line(line), flush=True),
    )
    print(f"Listening on {args.port} at {args.baudrate} baud. Press Ctrl+C to stop.")
    service.start()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        service.stop()
        stats = service.stats()
        print(f"Received {stats['lines_received']} line(s), {stats['bytes_received']} byte(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())