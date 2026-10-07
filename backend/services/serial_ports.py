"""
serial_ports.py - Serial port discovery (Phase 3, Task 1).

Lists the serial ports available on this machine, so you can find the
right port name for SRG_UART_PORT.

Run from the project root:
    python -m backend.services.serial_ports
"""

from dataclasses import dataclass

from serial.tools import list_ports


@dataclass(frozen=True)
class SerialPortInfo:
    """One serial port as reported by the operating system."""

    device: str
    description: str
    hwid: str
    vid: int | None
    pid: int | None


def list_serial_ports() -> list[SerialPortInfo]:
    """Return every serial port currently visible, sorted by name."""
    ports = sorted(list_ports.comports(), key=lambda port: port.device)
    return [
        SerialPortInfo(
            device=port.device,
            description=port.description or "n/a",
            hwid=port.hwid or "n/a",
            vid=port.vid,
            pid=port.pid,
        )
        for port in ports
    ]


def main() -> None:
    """Print the available serial ports."""
    ports = list_serial_ports()
    if not ports:
        print("No serial ports found.")
        return
    for port in ports:
        print(f"{port.device:<14} {port.description}  [{port.hwid}]")


if __name__ == "__main__":
    main()