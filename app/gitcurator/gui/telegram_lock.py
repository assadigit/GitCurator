#!/usr/bin/env python3
"""
gitcurator.gui.telegram_lock — the Telegram single-operation lock, done right.

WHY THIS MODULE EXISTS
======================
v0.05 shipped a boolean flag (``MainWindow._telegram_busy``) guarding the
Telethon session file (SQLite) against concurrent access. Three defect
classes made the flag stick at ``True`` forever — the "⏳ Another Telegram
operation is already running" forever-bug the owner reported:

1. **Acquire-without-release paths** — a dozen slots did
   ``_acquire_telegram_lock()`` and then hit an early ``return`` (missing
   credentials, empty field, invalid ID) without releasing. One such click
   froze every Telegram operation for the whole session.

2. **Signal-ordering self-deadlock** — worker ``finished_signal`` handlers
   connected BEFORE ``_keep_worker``'s cleanup slot (connected last) still
   ran while the lock was held; when those handlers started a *new*
   Telegram operation it was always denied — by the very worker finishing.

3. **Hung workers never finish** — the subprocess runner could block on a
   stalled telethon child forever (no working timeout), so the cleanup slot
   that releases the lock never fired.

This module fixes classes 1 and 2 structurally:

* **Owner tracking** — every acquire names its owner (``"bot_check"``,
  ``"batch"``, …). Release is owner-scoped: a finished batch can never
  release a lock held by a different worker (the v0.05 over-release bug
  where a finishing *direct* batch freed an unrelated TestWorker's lock).

* **Age + description** — the busy message now says WHAT holds the lock and
  for HOW LONG, turning "please wait" into an actionable diagnostic.

* **Context manager** — ``with lock.held("marker_search")`` makes the
  acquire/early-return/release pattern leak-proof by construction.

* **Watchdog support** — ``is_stuck(max_age)`` lets the GUI detect and
  force-release a lock whose worker died without cleanup (safety net for
  class 3, which is fixed at the subprocess-runner level as well).

Pure stdlib + thread-safe. No PyQt import — unit-testable headlessly
(the lock lives on the GUI thread in practice, but the worker threads
touch release paths, so a real lock is used anyway).
"""

from __future__ import annotations

import threading
import time
from typing import Optional


class TelegramLockManager:
    """Thread-safe single-slot lock with owner tracking and age reporting.

    All methods are idempotent and never raise — a lock helper that throws
    would itself be a reliability bug.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owner: Optional[str] = None
        self._acquired_at: Optional[float] = None

    # -- core API -----------------------------------------------------------

    def acquire(self, owner: str = "?") -> bool:
        """Try to acquire. Returns True on success, False if already held.

        A re-acquire by the CURRENT owner is refused too (the old code let
        a nested acquire by the same logical flow silently succeed and then
        double-release) — callers must structure around one acquire.
        """
        with self._lock:
            if self._owner is not None:
                return False
            self._owner = owner or "?"
            self._acquired_at = time.monotonic()
            return True

    def release(self, owner: Optional[str] = None) -> bool:
        """Release the lock.

        ``owner=None`` releases unconditionally (legacy behavior, e.g. the
        shutdown path). With an owner string the release only happens when
        it matches the current holder — a finished worker can never free a
        lock that a *different* worker now holds. Returns True if the lock
        was actually released by this call.
        """
        with self._lock:
            if self._owner is None:
                return False
            if owner is not None and self._owner != owner:
                return False
            self._owner = None
            self._acquired_at = None
            return True

    def force_release(self, reason: str = "manual") -> Optional[str]:
        """Unconditionally clear the lock; returns the evicted owner (or None
        if it was already free). The watchdog uses this after detecting a
        stuck holder."""
        with self._lock:
            evicted = self._owner
            self._owner = None
            self._acquired_at = None
        _ = reason  # kept for logging symmetry at call sites
        return evicted

    # -- inspection ---------------------------------------------------------

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._owner is not None

    @property
    def owner(self) -> Optional[str]:
        with self._lock:
            return self._owner

    @property
    def age_seconds(self) -> float:
        """How long the current holder has held the lock (0.0 if free)."""
        with self._lock:
            if self._owner is None or self._acquired_at is None:
                return 0.0
            return max(0.0, time.monotonic() - self._acquired_at)

    def describe(self) -> str:
        """Human-readable state for log lines: ``'bot_check (running 84s)'``
        or ``'free'``."""
        with self._lock:
            if self._owner is None:
                return "free"
            age = int(time.monotonic() - (self._acquired_at or time.monotonic()))
            return f"{self._owner} (running {age}s)"

    def is_stuck(self, max_age: float) -> bool:
        """True when the lock has been held longer than ``max_age`` seconds.
        The GUI watchdog uses this to offer/perform a force-release."""
        return self.busy and self.age_seconds > max_age

    # -- context manager ------------------------------------------------------

    class _Held:
        """Context manager returned by :meth:`held`.

        Usage::

            with self._tg_lock.held("marker_search") as ok:
                if not ok:
                    return          # lock busy — message already logged by caller
                ...                 # early returns are safe: finally releases
        """

        def __init__(self, manager: "TelegramLockManager", owner: str) -> None:
            self._manager = manager
            self._owner = owner
            self.acquired = False

        def __bool__(self) -> bool:
            return self.acquired

        def __enter__(self) -> "TelegramLockManager._Held":
            self.acquired = self._manager.acquire(self._owner)
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            if self.acquired:
                self._manager.release(self._owner)
            return False  # never swallow exceptions

    def held(self, owner: str = "?") -> "TelegramLockManager._Held":
        """Context-manager acquire; see :class:`_Held`."""
        return TelegramLockManager._Held(self, owner)


__all__ = ["TelegramLockManager"]
