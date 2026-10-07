# UART Protocol (ESP32-C3 master to Raspberry Pi 4)

The master ESP32-C3 forwards every packet it receives from the nodes to the
Pi over UART, **one JSON object per line**.

## Link settings

| Setting | Value |
|---|---|
| Baud rate | 115200 |
| Format | 8 data bits, no parity, 1 stop bit (8N1) |
| Encoding | UTF-8 |
| Line ending | `\n` (a `\r\n` is also accepted) |
| Logic level | 3.3 V on both sides, no level shifter needed |

## Wiring (Pi 4 to ESP32-C3)

| Raspberry Pi 4 | ESP32-C3 |
|---|---|
| GPIO14 / TXD (pin 8) | RX pin of the UART you use |
| GPIO15 / RXD (pin 10) | TX pin of the UART you use |
| GND (pin 6) | GND |

Cross TX and RX. On the Pi, enable the serial hardware and disable the
serial console (`sudo raspi-config`, Interface Options, Serial Port), then
set `SRG_UART_PORT=/dev/serial0`.

## Packet format

```json
{"v":1,"src":"WATER_01","type":"data","seq":1042,"d":{"flow_in":15.4,"flow_out":11.2}}
{"v":1,"src":"POWER_01","type":"event","seq":88,"d":{"door":"open"}}
{"v":1,"src":"MASTER_01","type":"heartbeat","seq":3}
```

| Field | Rule |
|---|---|
| `v` | Protocol version, must be `1` |
| `src` | Sender's node ID: `A-Z`, `0-9`, `_`, 3 to 50 characters. It must already be registered on the Pi (`POST /devices`). |
| `type` | `heartbeat`, `data` or `event` |
| `seq` | Integer 0 to 4294967295, increasing per node |
| `d` | Flat object of numbers, booleans and text. At most 32 keys, keys 1 to 40 characters, text values up to 200 characters. Required and non-empty for `data` and `event`. |
| `sig` | Optional text up to 128 characters. Reserved for device authentication. |

## Rules

- A line is at most 1024 characters. Keep key names short, because ESP-NOW
  messages are limited to 250 bytes anyway.
- No nested objects or arrays, no `null`, no `NaN` or `Infinity`.
- No unknown top-level fields.
- Do not send timestamps. The ESP32 has no real clock, so the Pi stamps each
  packet with its arrival time in UTC.
- Any valid packet counts as a heartbeat. A device that sends nothing for
  30 seconds is marked offline. Send a `heartbeat` packet at least every
  10 seconds when there is nothing else to report.
- Debug text on the same UART is harmless: lines that are not valid packets
  are counted and ignored, but a flood of them wastes bandwidth.

## Testing a link

```powershell
python -m backend.services.serial_ports                 # find the port
python -m backend.services.uart_monitor --port COM7     # lines marked OK or BAD
```