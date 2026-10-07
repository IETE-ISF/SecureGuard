# SecureRack Guardian

Data Center Infrastructure Monitoring and Cybersecurity Platform.

## Architecture

Raspberry Pi 4 (FastAPI + SQLite) <-UART-> Master ESP32-C3 <-ESP-NOW-> Water Node / Power+Security Node

## Layout

| Path        | Purpose                              |
|-------------|--------------------------------------|
| `backend/`  | FastAPI backend running on the Pi    |
| `frontend/` | React + Vite + Tailwind dashboard    |
| `firmware/` | ESP32-C3 firmware (master, nodes)    |
| `docs/`     | Design notes and documentation       |
| `tests/`    | Automated tests                      |

## Setup

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows:     .venv\Scripts\activate
pip install -r requirements.txt
```
