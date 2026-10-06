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
    is open. It inherits the main window's theme QSS (the app-wide rules
    in gui.theme re-style it on toggle).

    v0.30.0 (Settings-UI audit): the sidebar rows carry real Lucide icons
    (two-mode: white/plum on the selected fill) and one-line descriptions;
    the header hint follows the selected section, and every nav item has a
    tooltip. Emoji were purged from the nav labels — the icons carry the
    meaning now.
    """

    # One icon + one-line description per section (the audit's TAB_INFO).
    TAB_INFO = {
        'Credentials': ('key-round',
                        "API keys and tokens — Telegram, GitHub, the worker URL"),
        'Proxy':       ('globe',
                        "The SOCKS/HTTP proxy for Telegram and web fetches"),
        'Vault':       ('folder',
                        "Vault folders, backup repos, the law and pipeline switches"),
        'LLM':         ('brain',
                        "Local engines or any cloud API — provider, limits, detection"),
        'Input':       ('inbox',
                        "Import a .txt or .md file of links, one address per line"),
        'Dashboard':   ('bar-chart-3',
                        "Vault statistics, the 404 quarantine and batch undo"),
        'Bot':         ('bot',
                        "The Telegram bot queue — check, process, verify"),
        'Sources':     ('satellite-dish',
                        "GitHub links from RSS feeds and Reddit, no API key needed"),
        'Backup':      ('archive',
                        "Folder backups, VaultSeal and Good Repos publishing"),
        # v0.40.0 — the batch-finish chime's controls (Settings → Sound).
        'Sound':       ('volume-2',
                        "The batch-finish chime — on/off, volume, both previews"),
    }
    _DEFAULT_HINT = "Pick a section — every former tab lives here"

    def __init__(self, main_window: 'MainWindow'):
        super().__init__(main_window)
        self.main = main_window
        # sidebar labels (filled when the nav is built below; the header
        # hint reads them through _hint_for, so they must exist first)
        self._nav_labels: List[str] = []
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
        self.hint = QLabel(self._hint_for(0))
        self.hint.setObjectName("settings_hint")
        title_col.addWidget(title)
        title_col.addWidget(self.hint)
        header_lay.addLayout(title_col)
        header_lay.addStretch()

        # The 'More ⋯' overflow menu lives here now (same QToolButton + QMenu,
        # same actions — tests, verification, export, retry, theme).
        header_lay.addWidget(main_window.more_btn)

        close_btn = QPushButton("Close")
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
            self._nav_labels.append(label)
            item = QListWidgetItem(label)
            icon_name, desc = self.TAB_INFO.get(label, (None, ''))
            if icon_name:
                item.setIcon(self._nav_icon(icon_name))
            if desc:
                item.setToolTip(desc)
            self.nav.addItem(item)

        self.stack = QStackedWidget()
        for page, _label in main_window._settings_pages:
            self.stack.addWidget(page)  # reparents the scroll area here

        self.nav.currentRowChanged.connect(self._on_nav_changed)
        self.nav.setCurrentRow(0)

        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)
        root.addLayout(body, 1)

    # -- sidebar helpers (two-mode icons + the following hint) ------------

    def _nav_icon(self, icon_name: str):
        """One sidebar icon in the active mode's two-mode form: muted glyph
        on the plain row, white (light) / plum (dark) on the selected fill."""
        dark = bool(getattr(self.main, '_dark_mode', False))
        base = '#B7AFC9' if dark else '#6C6480'
        selected = '#221E2E' if dark else '#FFFFFF'
        return _icons.nav_icon(icon_name, base, selected, 16)

    def refresh_nav_icons(self):
        """Re-tint every sidebar icon after a theme flip (called by
        MainWindow.toggle_theme — duck-typed, so a closed dialog is a
        no-op)."""
        for row in range(self.nav.count()):
            item = self.nav.item(row)
            icon_name, _desc = self.TAB_INFO.get(
                self._nav_labels[row] if row < len(self._nav_labels) else '',
                (None, ''))
            if icon_name:
                item.setIcon(self._nav_icon(icon_name))

    def _hint_for(self, row: int) -> str:
        """The header hint names the SELECTED section's purpose."""
        if 0 <= row < len(self._nav_labels):
            _icon_name, desc = self.TAB_INFO.get(self._nav_labels[row],
                                                 (None, ''))
            if desc:
                return desc
        return self._DEFAULT_HINT

    def _on_nav_changed(self, row: int):
        """Master-detail: switch the page AND follow with the hint."""
        self.stack.setCurrentIndex(row)
        self.hint.setText(self._hint_for(row))

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

    v0.37.0 — the hierarchy pass (the owner's spec): every check is ONE
    line, smaller + paler, indented under its BOLD section heading —
    the wrapped multi-line detail blob is gone. Each item shows the
    compact summary (connection_check.short_line); its tooltip carries
    the FULL render_line text. The dialog never talks to the network —
    MainWindow.test_all routes the battery worker's section/result
    signals into set_row_* calls.
    """

    _SPIN_FRAMES = ('⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏')
    _MARK = {'ok': '✅', 'warn': '⚠️', 'error': '❌'}

    # v0.37.0 — the item column's pixel width: 560 dialog - 2*20 root
    # margins - 2*12 card margins - 18 item indent. add_row_detail
    # elides every item to this width with the label's OWN font metrics
    # (ElideMiddle) so no line can ever clip at the edge, whatever the
    # font's real advance widths are.
    _ITEM_COL_PX = 560 - 2 * 20 - 2 * 12 - 18

    def __init__(self, main_window: 'MainWindow'):
        super().__init__(main_window)
        self.main = main_window
        self.setObjectName("connection_test_dialog")
        self.setWindowTitle("Test Connection")
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
            row = QWidget()
            row_lay = QVBoxLayout(row)
            row_lay.setContentsMargins(0, 4, 0, 4)
            row_lay.setSpacing(1)
            head = QHBoxLayout()
            head.addWidget(name, 0)
            head.addStretch(1)
            head.addWidget(status, 0)
            row_lay.addLayout(head)
            # v0.37.0 — one compact QLabel per check line (never a
            # wrapped blob): smaller + paler via QSS (#cc_item), indented
            # under the bold heading, full detail on the tooltip.
            items_lay = QVBoxLayout()
            items_lay.setContentsMargins(18, 0, 0, 1)
            items_lay.setSpacing(0)
            row_lay.addLayout(items_lay)
            rows_lay.addWidget(row)
            self._rows[idx] = {
                'title': title, 'status': status, 'items_lay': items_lay,
                'items': [], 'spinning': False, 'done': False,
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
        self.close_btn = QPushButton("Close")
        self.close_btn.setToolTip("Close this dialog (the checks keep running in the log)")
        self.close_btn.clicked.connect(self.reject)
        main_window._style_btn(self.close_btn, 'secondary')
        btn_row.addWidget(self.close_btn)

        self.start_sync_btn = QPushButton("Start Syncing")
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
        for item in row['items']:
            item.setVisible(False)

    def add_row_detail(self, idx: int, text: str, full: str = ""):
        """v0.37.0 — one compact item line under the section heading.
        ``text`` is the short one-line summary (connection_check
        .short_line); ``full`` (optional) is the complete render_line
        text, shown as the item's tooltip. The visible text is elided to
        the item column's width with the label's own font metrics —
        pixel-perfect, never clipped, always ONE line."""
        row = self._rows.get(int(idx))
        if row is None:
            return
        raw = str(text or "")
        item = QLabel()
        item.setObjectName("cc_item")
        item.setWordWrap(False)   # ONE line, always
        try:
            item.ensurePolished()   # the QSS 11px font applies to metrics
            shown = item.fontMetrics().elidedText(
                raw, Qt.TextElideMode.ElideMiddle, self._ITEM_COL_PX)
        except Exception:
            shown = raw
        item.setText(shown)
        item.setToolTip(str(full) if full else raw)
        row['items_lay'].addWidget(item)
        row['items'].append(item)

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
