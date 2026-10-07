"""
scaffold.py - SecureRack Guardian project scaffolder (Phase 0, Task 1).

Creates the directory layout, Python package markers, .gitignore,
README.md and requirements.txt. Safe to re-run: existing files are
never overwritten.

Usage:
    python scaffold.py
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Directories that are Python packages (get an __init__.py).
PACKAGE_DIRS = [
    "backend",
    "backend/api",
    "backend/services",
    "backend/database",
    "backend/models",
    "backend/schemas",
    "backend/security",
    "tests",
]

# Directories that are not packages; tracked in Git via a .gitkeep file.
PLAIN_DIRS = [
    "backend/logs",
    "frontend",
    "firmware/master_esp32",
    "firmware/water_node",
    "firmware/power_node",
    "docs",
]

GITIGNORE = """\
# Python
__pycache__/
*.py[cod]
.venv/
venv/
.pytest_cache/
.mypy_cache/
*.egg-info/

# Environment / secrets
.env

# Logs and runtime data
backend/logs/*
!backend/logs/.gitkeep
*.log
*.db
*.sqlite3

# Evidence and generated reports (created in later phases)
evidence/
reports_output/

# Frontend
frontend/node_modules/
frontend/dist/

# Firmware build artefacts
firmware/**/.pio/
firmware/**/build/

# OS / editor
.DS_Store
Thumbs.db
.vscode/
.idea/
"""

README = """\
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
# Windows:     .venv\\Scripts\\activate
pip install -r requirements.txt
```
"""

REQUIREMENTS = """\
# SecureRack Guardian - Phase 0 dependencies
fastapi==0.115.6
uvicorn[standard]==0.34.0
pydantic-settings==2.7.0
"""


def write_if_missing(path: Path, content: str = "") -> None:
    """Create a file with the given content unless it already exists."""
    if path.exists():
        print(f"  skip   {path.relative_to(ROOT)}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"  create {path.relative_to(ROOT)}")


def main() -> None:
    print("Creating package directories...")
    for rel in PACKAGE_DIRS:
        write_if_missing(ROOT / rel / "__init__.py")

    print("Creating plain directories...")
    for rel in PLAIN_DIRS:
        write_if_missing(ROOT / rel / ".gitkeep")

    print("Creating root files...")
    write_if_missing(ROOT / ".gitignore", GITIGNORE)
    write_if_missing(ROOT / "README.md", README)
    write_if_missing(ROOT / "requirements.txt", REQUIREMENTS)

    print("\nScaffold complete.")


if __name__ == "__main__":
    main()