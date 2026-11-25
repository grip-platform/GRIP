"""Session inactivity watcher (Seam E from council verdict 2026-05-03).

Per-session idle timers. Each session_key registers a threshold_ms + a
callback. On any activity, call touch(session_key) to reset the timer.
If no activity within threshold_ms, the callback fires (typically:
checkpoint to disk, evict from router).

Implementation: threading.Timer. Each session has its own Timer object;
touch() cancels the existing timer and starts a fresh one. Thread-safe
via a single Lock guarding the registry.

Default threshold: INACTIVITY_CHECKPOINT_MS = 1800000 (30 minutes).
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

logger = logging.getLogger(__name__)

INACTIVITY_CHECKPOINT_MS = 1800000  # 30 minutes


class SessionInactivityWatcher:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._timers: dict[str, threading.Timer] = {}
        self._callbacks: dict[str, Callable[[], None]] = {}
        self._thresholds: dict[str, int] = {}

    def register(
        self,
        session_key: str,
        threshold_ms: int,
        on_idle: Callable[[], None],
    ) -> None:
        """Register a session with an idle threshold and callback.

        If a timer already exists for this session_key, the existing one
        is cancelled and replaced. Calling register() implicitly starts
        the timer (equivalent to register + immediate touch).
        """
        with self._lock:
            self._cancel_timer_locked(session_key)
            self._callbacks[session_key] = on_idle
            self._thresholds[session_key] = threshold_ms
            self._start_timer_locked(session_key)

    def touch(self, session_key: str) -> bool:
        """Reset the timer for a session.

        Returns True if a timer was reset, False if no such session
        is registered (silent no-op).
        """
        with self._lock:
            if session_key not in self._callbacks:
                return False
            self._cancel_timer_locked(session_key)
            self._start_timer_locked(session_key)
            return True

    def unregister(self, session_key: str) -> bool:
        """Cancel and remove the timer for a session.

        Returns True if a timer was cancelled, False if not registered.
        Caller must call this on session eviction to avoid stale timers.
        """
        with self._lock:
            if session_key not in self._callbacks:
                return False
            self._cancel_timer_locked(session_key)
            self._callbacks.pop(session_key, None)
            self._thresholds.pop(session_key, None)
            return True

    def is_registered(self, session_key: str) -> bool:
        with self._lock:
            return session_key in self._callbacks

    def __del__(self) -> None:
        """Cancel all outstanding timers on GC to prevent leaked threads."""
        for key in list(self._timers):
            self._cancel_timer_locked(key)

    # ------------------------------------------------------------------
    # Private helpers (must be called with self._lock held)
    # ------------------------------------------------------------------

    def _start_timer_locked(self, session_key: str) -> None:
        interval = self._thresholds[session_key] / 1000.0
        timer = threading.Timer(interval, self._on_fire, args=(session_key,))
        timer.daemon = True
        timer.start()
        self._timers[session_key] = timer

    def _cancel_timer_locked(self, session_key: str) -> None:
        timer = self._timers.pop(session_key, None)
        if timer is not None:
            timer.cancel()

    def _on_fire(self, session_key: str) -> None:
        """Timer target — fires on the timer thread (outside the lock)."""
        with self._lock:
            # Remove stale timer reference so the registry is clean.
            self._timers.pop(session_key, None)
            callback = self._callbacks.pop(session_key, None)
            self._thresholds.pop(session_key, None)

        if callback is None:
            return

        try:
            callback()
        except Exception:
            logger.exception("on_idle callback raised for session %s", session_key)
