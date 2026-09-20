#!/usr/bin/env python3
"""gitcurator.gui.log_handler — Python logging -> Qt signal bridge.

Moved verbatim from ``gitcurator/gui/app.py`` (v32.3 modularization).

* :class:`_GuiLogHandler` — forwards formatted records to a Qt
  ``log_signal`` so fetcher internals (session path, proxy tuple,
  attempt details) appear in the GUI log, not just the terminal.
* :func:`_install_gui_log_handler` — attaches the handler AND raises the
  root logger level to INFO (without that, info records are dropped
  before reaching any handler — the historic GUI-log blind spot).
* :func:`_remove_gui_log_handler` — best-effort detach.

No PyQt import: the handler only needs an object with ``.emit(msg,
level)`` (a Qt signal or a test double).
"""

import logging

__all__ = ["_GuiLogHandler", "_install_gui_log_handler", "_remove_gui_log_handler"]


class _GuiLogHandler(logging.Handler):
    """Bridges Python logging -> Qt signal so the fetcher's internal log
    lines (session path, proxy tuple, attempt details) appear in the GUI log,
    not just the terminal."""
    def __init__(self, log_signal):
        super().__init__()
        self._log_signal = log_signal

    def emit(self, record):
        try:
            msg = self.format(record)
            level = record.levelname.lower()
            if level == 'warning':
                level = 'warning'
            elif level in ('error', 'critical'):
                level = 'error'
            else:
                level = 'info'
            self._log_signal.emit(msg, level)
        except Exception:
            pass


def _install_gui_log_handler(log_signal):
    """Attach a _GuiLogHandler to the root logger for the duration of a job.
    Returns the handler so it can be removed afterwards.

    CRITICAL: also set the root logger's LEVEL to INFO. By default the root
    logger level is WARNING, which means logger.info() calls are silently
    dropped BEFORE they ever reach the handler. This was why the Session/Proxy/
    Attempt diagnostics weren't appearing in the GUI log."""
    handler = _GuiLogHandler(log_signal)
    handler.setLevel(logging.INFO)
    root = logging.getLogger()
    root.setLevel(logging.INFO)   # <-- THIS WAS MISSING
    root.addHandler(handler)
    return handler


def _remove_gui_log_handler(handler):
    try:
        logging.getLogger().removeHandler(handler)
    except Exception:
        pass
