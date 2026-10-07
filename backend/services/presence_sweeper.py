"""
presence_sweeper.py - Marks silent devices offline (Phase 3, Task 4).

Runs on a daemon thread. Every `interval_seconds` it sets ONLINE devices
whose last heartbeat is older than `timeout_seconds` to OFFLINE.
"""

import logging
import threading

from backend.config import Settings, get_settings
from backend.database.session import session_scope
from backend.services.device_service import mark_stale_devices_offline

logger = logging.getLogger(__name__)

STOP_TIMEOUT_SECONDS = 5.0


class PresenceSweeper:
    """Periodically marks devices offline when they stop reporting."""

    def __init__(self, timeout_seconds: int, interval_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "PresenceSweeper":
        """Build a sweeper from the SRG_DEVICE_* settings."""
        settings = settings or get_settings()
        return cls(
            settings.device_offline_timeout_seconds,
            settings.device_sweep_interval_seconds,
        )

    @property
    def is_running(self) -> bool:
        """True while the sweeper thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def sweep_once(self) -> list[str]:
        """Run one sweep now and return the node_ids that were marked offline."""
        with session_scope() as db:
            return mark_stale_devices_offline(db, self.timeout_seconds)

    def start(self) -> None:
        """Start the sweeper thread. Does nothing if it is already running."""
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="presence-sweeper", daemon=True
        )
        self._thread.start()
        logger.info(
            "Presence sweeper started (timeout=%ds, interval=%gs)",
            self.timeout_seconds,
            self.interval_seconds,
        )

    def stop(self, timeout: float = STOP_TIMEOUT_SECONDS) -> None:
        """Stop the thread and wait for it. Safe to call more than once."""
        thread = self._thread
        if thread is None:
            return
        self._stop_event.set()
        thread.join(timeout)
        if thread.is_alive():
            logger.warning("Presence sweeper did not stop within %.1fs", timeout)
            return
        self._thread = None
        logger.info("Presence sweeper stopped")

    def _run(self) -> None:
        # Wait one full interval before each sweep; wait() returns True once stopped.
        while not self._stop_event.wait(self.interval_seconds):
            try:
                self.sweep_once()
            except Exception:
                logger.exception("Presence sweep failed")