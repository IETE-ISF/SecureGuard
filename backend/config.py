"""
config.py - Central application configuration (Phase 0 Task 2, extended in Phase 1 Task 1).

All settings are loaded from environment variables prefixed with SRG_,
or from a .env file in the project root. Values are validated at startup,
so a bad configuration fails immediately with a clear error instead of
causing a confusing failure later.

Usage:
    from backend.config import get_settings

    settings = get_settings()
    print(settings.database_url)
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
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