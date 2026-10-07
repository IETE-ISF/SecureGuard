"""
config.py - Central application configuration
(Phase 0 Task 2, extended in Phase 1 Task 1 and Phase 3 Task 1).

All settings are loaded from environment variables prefixed with SRG_,
or from a .env file in the project root. Values are validated at startup,
so a bad configuration fails immediately with a clear error instead of
causing a confusing failure later.

Usage:
    from backend.config import get_settings

    settings = get_settings()
    print(settings.uart_baudrate)
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Absolute paths, independent of the directory the app is launched from.
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
BACKEND_DIR: Path = PROJECT_ROOT / "backend"
DATA_DIR: Path = PROJECT_ROOT / "data"

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
Environment = Literal["development", "testing", "production"]


class Settings(BaseSettings):
    """Typed application settings. Later phases add their own fields here."""

    model_config = SettingsConfigDict(
        env_prefix="SRG_",
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application ---
    app_name: str = "SecureRack Guardian"
    app_version: str = "0.1.0"
    environment: Environment = "development"
    debug: bool = False

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)

    # --- Logging ---
    log_level: LogLevel = "INFO"
    log_dir: Path = BACKEND_DIR / "logs"

    # --- Database ---
    database_path: Path = DATA_DIR / "securerack.db"
    database_echo: bool = False

    # --- UART (link to the master ESP32-C3) ---
    uart_enabled: bool = False
    uart_port: str = ""  # e.g. COM5 on Windows, /dev/serial0 on the Raspberry Pi
    uart_baudrate: int = Field(default=115200, ge=1200, le=3_000_000)
    uart_read_timeout: float = Field(default=1.0, gt=0, le=60)

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalise_log_level(cls, value: object) -> object:
        """Accept 'info', 'Info', 'INFO' alike."""
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("log_dir", "database_path", mode="after")
    @classmethod
    def _resolve_paths(cls, value: Path) -> Path:
        """Resolve relative paths against the project root."""
        if not value.is_absolute():
            value = PROJECT_ROOT / value
        return value.resolve()

    @field_validator("uart_port", mode="after")
    @classmethod
    def _strip_uart_port(cls, value: str) -> str:
        """Ignore stray whitespace around the port name."""
        return value.strip()

    @model_validator(mode="after")
    def _require_uart_port_when_enabled(self) -> Self:
        """UART cannot be enabled without saying which port to open."""
        if self.uart_enabled and not self.uart_port:
            raise ValueError("SRG_UART_PORT must be set when SRG_UART_ENABLED is true")
        return self

    @property
    def database_url(self) -> str:
        """SQLAlchemy URL for the SQLite database file."""
        return f"sqlite:///{self.database_path.as_posix()}"

    @property
    def is_production(self) -> bool:
        """True when running in the production environment."""
        return self.environment == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached Settings instance (parsed once per process)."""
    return Settings()