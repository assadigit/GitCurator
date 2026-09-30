"""MainWindow TelegramUiMixin — methods moved verbatim from the MainWindow in gitcurator/gui/app.py (branch refactor/gui-app-split; see REFACTOR_PLAN.md at the repo root)."""

import sys as _sys
import sys
import os
import re
import json
import time
import urllib.error
import sqlite3
import subprocess
import html as _html_module
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any
import threading
import logging
from logging.handlers import RotatingFileHandler

from gitcurator.constants import (
    APP_DIR, COLORS, CONFIG_FILE, CONFIG_EXAMPLE, CATEGORY_FOLDERS,
    CATEGORY_KEYS, DEFAULT_SYSTEM_PROMPT,
)
from gitcurator.core import links as _links
from gitcurator.core import storage as _storage
from gitcurator.core import note_builder as _note_builder
from gitcurator.core import llm_client as _llm_client
from gitcurator.core import dryrun as _dryrun
from gitcurator.core import note_state as _note_state
from gitcurator.core import website_pipeline as _website_pipeline
from gitcurator.core import connection_check as _connection_check
from gitcurator.integrations import vaultseal as _vaultseal
from gitcurator.integrations import goodrepos as _goodrepos
from gitcurator.gui.telegram_lock import TelegramLockManager
from gitcurator.gui import icons as _icons
from gitcurator.integrations.subprocess_runner import (
    run_telegram_worker as _run_worker_subprocess,
    kill_all_workers as _kill_all_telegram_workers,
    live_worker_count as _live_telegram_worker_count,
)

try:
    from PyQt6.QtWidgets import *
    from PyQt6.QtCore import *
    from PyQt6.QtGui import *
except ImportError:
    print("PyQt6 is not installed. Please run: pip install PyQt6")
    sys.exit(1)

_APP_DIR = APP_DIR

from gitcurator.gui.processing_worker import TestWorker

class TelegramUiMixin:
    """TelegramUiMixin"""

    def _acquire_telegram_lock(self, owner: str = "telegram") -> bool:
        """Try to acquire the Telegram busy lock. Returns True if acquired,
        False if another Telegram operation is already running.

        v0.06 — the lock is a TelegramLockManager with owner tracking; the
        busy message now names the holder and its age so "please wait"
        becomes an actionable diagnostic instead of a dead end."""
        if not self._tg_lock.acquire(owner):
            held = self._tg_lock.describe()
            age = int(self._tg_lock.age_seconds)
            stuck_hint = (
                " It looks stuck — it will be force-released automatically "
                "if it doesn't finish, or restart the app."
                if age > 300 else
                " Please wait for it to finish."
            )
            self.log_message(
                f"⏳ Another Telegram operation is already running: {held}.{stuck_hint}",
                "warning"
            )
            return False
        return True

    def _release_telegram_lock(self, owner: Optional[str] = None):
        """Release the Telegram busy lock.

        v0.06 — owner-scoped: when ``owner`` is given the release only takes
        effect if that owner still holds the lock (a finished worker can no
        longer free a lock a DIFFERENT worker now holds — the v0.05
        over-release bug that enabled two telethon children on one session
        file). ``owner=None`` releases unconditionally (shutdown paths)."""
        self._tg_lock.release(owner)

    def _tg_lock_watchdog(self):
        """v0.06 — auto-release a Telegram lock held implausibly long.

        Every real operation is bounded by the subprocess runner's hard cap
        (30 min), so a lock still held after _TG_STUCK_SECONDS means the
        worker-finished cleanup chain itself died. Force-release so the app
        stays usable — exactly the scenario that used to show "another
        operation is running" forever."""
        try:
            if self._tg_lock.is_stuck(self._TG_STUCK_SECONDS):
                held_for = int(self._tg_lock.age_seconds)
                evicted = self._tg_lock.force_release("watchdog")
                self.log_message(
                    f"🔓 Watchdog: Telegram lock held by '{evicted}' for "
                    f"{held_for}s looks stuck — force-released. "
                    f"If Telegram misbehaves, restart the app.",
                    "warning"
                )
        except Exception:
            pass  # best-effort — never crash the timer

    def _keep_worker(self, worker: TestWorker, owner: Optional[str] = None):
        """Hold a reference so the QThread isn't garbage-collected mid-run.
        Also releases the Telegram busy lock when the worker finishes.

        v0.06 — owner-scoped release: the cleanup releases the lock only if
        THIS worker's owner still holds it. Previously any finishing worker
        unconditionally cleared the flag, which could free a lock that a
        newer operation had legitimately acquired."""
        self._active_test_workers.append(worker)
        def _cleanup(*_a):
            try:
                self._active_test_workers.remove(worker)
            except ValueError:
                pass
            self._tg_lock.release(owner)
        worker.finished_signal.connect(_cleanup)

    def _on_telegram_code_requested(self, prompt_type: str, worker: TestWorker):
        """Called when the Telegram worker needs a login code or 2FA password.
        Shows a theme-aware modal dialog with clear instructions."""
        dialog = QDialog(self)
        dialog.setModal(True)
        dialog.raise_()
        dialog.activateWindow()

        # Theme-aware colors
        is_dark = getattr(self, '_dark_mode', False)
        if is_dark:
            bg_color = "#2B2639"
            text_color = "#F2EEE7"
            help_bg = "#2E2A3C"
            border_color = "#4A4263"
            focus_color = "#C4BCF5"
        else:
            bg_color = "#FFFFFF"
            text_color = "#423A52"
            help_bg = "#F2EDE3"
            border_color = "#D8D0BE"
            focus_color = "#5F54B4"  # v32 pastel: violet accent

        dialog.setStyleSheet(f"""
            QDialog {{ background-color: {bg_color}; }}
            QLabel {{ color: {text_color}; background: transparent; }}
        """)

        if prompt_type == "PASSWORD":
            dialog.setWindowTitle("🔒 Telegram 2FA Password")
            label_text = "Enter your Telegram cloud password (2FA):"
            echo_mode = QLineEdit.EchoMode.Password
            placeholder = ""
            help_text = ""
        else:
            dialog.setWindowTitle("📲 Telegram Login Code")
            label_text = "Telegram has sent a login code to your account."
            echo_mode = QLineEdit.EchoMode.Normal
            placeholder = "e.g. 12345"
            help_text = (
                "WHERE TO FIND THE CODE:\n"
                "• Telegram app: Look for a 'Login Code' notification at the top\n"
                "  of your chat list (it's NOT in Saved Messages)\n"
                "• SMS: Check text messages on your phone\n"
                "• Other devices: Check any other device logged into Telegram\n\n"
                "The code expires in a few minutes. If you don't receive it,\n"
                "cancel and try again."
            )

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        # Title label
        title_label = QLabel(label_text)
        title_label.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {text_color}; background: transparent;")
        title_label.setWordWrap(True)
        layout.addWidget(title_label)

        # Input field
        input_field = QLineEdit()
        input_field.setEchoMode(echo_mode)
        input_field.setPlaceholderText(placeholder)
        input_field.setMinimumWidth(360)
        input_field.setStyleSheet(
            f"QLineEdit {{ padding: 8px; border: 2px solid {border_color}; border-radius: 6px; font-size: 14px; background: {bg_color}; color: {text_color}; }}"
            f"QLineEdit:focus {{ border-color: {focus_color}; }}"
        )
        layout.addWidget(input_field)

        # Help text for code input
        if help_text:
            help_label = QLabel(help_text)
            help_label.setStyleSheet(
                f"font-size: 11px; color: {text_color}; background-color: {help_bg}; "
                f"padding: 10px; border-radius: 4px; font-family: Consolas, monospace;"
            )
            help_label.setWordWrap(True)
            layout.addWidget(help_label)

        # Buttons
        button_row = QHBoxLayout()
        button_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(
            "padding: 8px 20px; font-size: 13px; border: 1px solid #ccc; border-radius: 4px; background: #f5f5f5;"
        )

        ok_btn = QPushButton("✓ Submit Code")
        ok_btn.setDefault(True)
        ok_btn.setStyleSheet(self._btn_style(COLORS['primary'], COLORS['primary_hover']))

        button_row.addWidget(cancel_btn)
        button_row.addWidget(ok_btn)
        layout.addLayout(button_row)

        def _on_accept():
            worker.provide_code(input_field.text().strip())
            dialog.accept()

        def _on_reject():
            worker.provide_code("")
            dialog.reject()

        ok_btn.clicked.connect(_on_accept)
        cancel_btn.clicked.connect(_on_reject)
        input_field.returnPressed.connect(_on_accept)

        # Focus the input field and auto-resize
        input_field.setFocus()
        dialog.adjustSize()
        self._animate_dialog(dialog)
        dialog.exec()

