"""SettingsDialog, ConnectionTestDialog — moved verbatim from gitcurator/gui/app.py (branch refactor/gui-app-split; see docs/history/REFACTOR_PLAN.md)."""

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

class SettingsDialog(QDialog):
    """v33 wireframe redesign: EVERY former main-window tab moved here.

    Layout (wireframe 'Settings' page): a 'Settings' header, a left sidebar
    with one entry per former tab, and a master-detail content area that
    shows the selected page. The pages are the exact same scroll-wrapped
    widgets initUI() always built — only their container changed, so every
    widget reference, signal connection and pipeline hook keeps working.

    The dialog is NON-MODAL (show/raise from the main window's ⚙️ button):
    batch runs, the proxy monitor and worker dialogs keep working while it
    is open. It inherits the main window's theme QSS (objectName-scoped
    rules in apply_light_theme/apply_dark_theme re-style it on toggle).
    """

    def __init__(self, main_window: 'MainWindow'):
        super().__init__(main_window)
        self.main = main_window
        self.setObjectName("settings_dialog")
        self.setWindowTitle("Settings — GitCurator")
        self.setModal(False)
        self.setFixedSize(880, 640)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- Header: title + hint · More menu · Close (wireframe) ----
        header = QWidget()
        header.setObjectName("settings_header")
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(20, 12, 14, 12)
        header_lay.setSpacing(10)

        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        title = QLabel("Settings")
        title.setObjectName("settings_title")
        hint = QLabel("Credentials · proxy · vault · LLM · input · bot · sources · dashboard · backup")
        hint.setObjectName("settings_hint")
        title_col.addWidget(title)
        title_col.addWidget(hint)
        header_lay.addLayout(title_col)
        header_lay.addStretch()

        # The 'More ⋯' overflow menu lives here now (same QToolButton + QMenu,
        # same actions — tests, verification, export, retry, theme).
        header_lay.addWidget(main_window.more_btn)

        close_btn = QPushButton("✕  Close")
        close_btn.setToolTip("Close Settings and return to the main view")
        close_btn.clicked.connect(self.close)
        main_window._style_btn(close_btn, 'secondary')
        header_lay.addWidget(close_btn)
        root.addWidget(header)

        # ---- Body: sidebar navigation + master-detail content ----
        body = QHBoxLayout()
        body.setContentsMargins(14, 14, 14, 14)
        body.setSpacing(14)

        self.nav = QListWidget()
        self.nav.setObjectName("settings_nav")
        self.nav.setFixedWidth(190)
        for _page, label in main_window._settings_pages:
            self.nav.addItem(label)

        self.stack = QStackedWidget()
        for page, _label in main_window._settings_pages:
            self.stack.addWidget(page)  # reparents the scroll area here

        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)
        root.addLayout(body, 1)

class ConnectionTestDialog(QDialog):
    """v0.23.0 — Test Connection as a MODAL with live per-subsystem status.

    Owner spec: "when clicked test connection, a modal must open with a
    loading, then everything that is connected gets an emoji check; the
    modal has a button to be closed and another button to start syncing
    (which turns green after everything is connected — before that it's
    turned off)."

    Four rows — 📁 Vaults / 🧠 LLM / 🐙 GitHub / ✈️ Telegram — each starts
    as '⏳ Waiting…', spins (braille animation) while its section runs,
    then settles on ✅ / ⚠️ / ❌ with the detail lines under it. The
    Telegram row keeps spinning through the LIVE leg (the battery only
    covers the local checks). Buttons: ✕ Close (always) and 🚀 Start
    Syncing — DISABLED until every row reported ok; then it flips to the
    filled pastel-mint 'go' style (the design system's green) and starts
    the main view's SYNC flow when clicked.

    The dialog never talks to the network — MainWindow.test_all routes
    the battery worker's section/result signals into set_row_* calls.
    """

    _SPIN_FRAMES = ('⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏')
    _MARK = {'ok': '✅', 'warn': '⚠️', 'error': '❌'}

    def __init__(self, main_window: 'MainWindow'):
        super().__init__(main_window)
        self.main = main_window
        self.setObjectName("connection_test_dialog")
        self.setWindowTitle("🔍 Test Connection")
        self.setModal(True)
        self.setMinimumWidth(560)

        self._rows = {}          # 1-based section index -> row dict
        self._spin_pos = 0
        self._verdicts = {}      # section index -> 'ok' | 'warn' | 'error'

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        header = QLabel("Checking every subsystem — vaults, LLM, GitHub and Telegram…")
        header.setWordWrap(True)
        header.setObjectName("info_header")
        root.addWidget(header)

        # ---- The four subsystem rows ----
        rows_card = QWidget()
        rows_card.setObjectName("sync_card")
        rows_lay = QVBoxLayout(rows_card)
        rows_lay.setContentsMargins(12, 10, 12, 10)
        rows_lay.setSpacing(4)
        for idx, (icon, title) in enumerate(
                [("📁", "Vaults"), ("🧠", "LLM"),
                 ("🐙", "GitHub"), ("✈️", "Telegram")], start=1):
            status = QLabel("⏳ Waiting…")
            status.setObjectName("cc_row_status")
            name = QLabel(f"{icon} {title}")
            name.setObjectName("cc_row_name")
            detail = QLabel("")
            detail.setObjectName("cc_row_detail")
            detail.setWordWrap(True)
            detail.setVisible(False)
            row = QWidget()
            row_lay = QVBoxLayout(row)
            row_lay.setContentsMargins(0, 4, 0, 4)
            row_lay.setSpacing(1)
            head = QHBoxLayout()
            head.addWidget(name, 0)
            head.addStretch(1)
            head.addWidget(status, 0)
            row_lay.addLayout(head)
            row_lay.addWidget(detail)
            rows_lay.addWidget(row)
            self._rows[idx] = {
                'title': title, 'status': status,
                'detail': detail, 'spinning': False, 'done': False,
            }
        root.addWidget(rows_card, 1)

        # ---- Spinner driver: one timer animates every spinning row ----
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(100)
        self._spin_timer.timeout.connect(self._tick_spin)
        self._spin_timer.start()

        # ---- Buttons: Close (always) + Start Syncing (gated) ----
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.close_btn = QPushButton("✕  Close")
        self.close_btn.setToolTip("Close this dialog (the checks keep running in the log)")
        self.close_btn.clicked.connect(self.reject)
        main_window._style_btn(self.close_btn, 'secondary')
        btn_row.addWidget(self.close_btn)

        self.start_sync_btn = QPushButton("🚀 Start Syncing")
        self.start_sync_btn.setToolTip(
            "Enabled when every subsystem is connected — starts the main "
            "view's SYNC flow (fetch the bot queue, then PROCESS).")
        self.start_sync_btn.setEnabled(False)
        self.start_sync_btn.clicked.connect(self._on_start_sync)
        # The mint 'go' style is the design system's primary fill — with
        # setEnabled(False) it renders in the muted disabled tones until
        # every row is green.
        main_window._style_btn(self.start_sync_btn, 'primary')
        btn_row.addWidget(self.start_sync_btn)
        root.addLayout(btn_row)

    # -- Row lifecycle (called from MainWindow's signal handlers) --------

    def set_row_checking(self, idx: int):
        row = self._rows.get(int(idx))
        if row is None or row['done']:
            return
        row['spinning'] = True
        row['detail'].setVisible(False)

    def add_row_detail(self, idx: int, text: str):
        row = self._rows.get(int(idx))
        if row is None:
            return
        current = row['detail'].text()
        row['detail'].setText((current + "\n" if current else "") + text)
        row['detail'].setVisible(True)

    def finalize_row(self, idx: int, verdict: str):
        row = self._rows.get(int(idx))
        if row is None:
            return
        row['spinning'] = False
        row['done'] = True
        self._verdicts[int(idx)] = verdict
        mark = self._MARK.get(verdict, '•')
        label = {'ok': 'Connected', 'warn': 'Connected (warnings)',
                 'error': 'Not connected'}[verdict]
        row['status'].setText(f"{mark} {label}")

    def finish_all(self):
        """The verdict is in: enable + 'green' Start Syncing only when
        every subsystem reported ok."""
        all_ok = (bool(self._verdicts)
                  and all(v == 'ok' for v in self._verdicts.values())
                  and len(self._verdicts) == len(self._rows))
        self.start_sync_btn.setEnabled(all_ok)
        if all_ok:
            self.start_sync_btn.setToolTip(
                "Every subsystem is connected — click to start SYNC.")

    # -- internals ---------------------------------------------------------

    def _tick_spin(self):
        self._spin_pos = (self._spin_pos + 1) % len(self._SPIN_FRAMES)
        glyph = self._SPIN_FRAMES[self._spin_pos]
        for row in self._rows.values():
            if row['spinning']:
                row['status'].setText(f"{glyph} Checking…")

    def _on_start_sync(self):
        self.accept()
        # Deferred one tick so this modal is fully closed before the hero
        # flow takes over the UI.
        QTimer.singleShot(0, self.main._on_hero_clicked)
