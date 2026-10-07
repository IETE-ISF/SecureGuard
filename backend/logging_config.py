"""
logging_config.py - Application logging setup (Phase 0, Task 3).

Configures the "backend" logger namespace with:
  * a console handler (stdout)
  * a rotating file handler (backend/logs/securerack.log)

Level and log directory come from backend.config.Settings.

Usage:
    import logging
    from backend.logging_config import setup_logging

    setup_logging()                       # once, at application startup
    logger = logging.getLogger(__name__)  # in every module
    logger.info("Device %s registered", device_id)
"""

import logging
import sys
from logging.handlers import RotatingFileHandler

from backend.config import Settings, get_settings

PACKAGE_LOGGER_NAME = "backend"
LOG_FILE_NAME = "securerack.log"
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
MAX_LOG_BYTES = 5 * 1024 * 1024  # 5 MB per file
BACKUP_COUNT = 5


def setup_logging(settings: Settings | None = None) -> logging.Logger:
    """
    Configure application logging and return the package logger.

    Safe to call multiple times: existing handlers are removed and closed
    before new ones are attached, so log lines are never duplicated.

    Args:
        settings: Optional Settings instance. Defaults to get_settings().
    """
    settings = settings or get_settings()
    settings.log_dir.mkdir(parents=True, exist_ok=True)

    level = getattr(logging, settings.log_level)
    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

    logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False  # avoid double output via the root logger

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        settings.log_dir / LOG_FILE_NAME,
        maxBytes=MAX_LOG_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
        delay=True,
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    logger.debug("Logging initialised (level=%s, dir=%s)", settings.log_level, settings.log_dir)
    return logger