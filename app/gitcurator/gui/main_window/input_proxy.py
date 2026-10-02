"""MainWindow InputProxyMixin — methods moved verbatim from the MainWindow in gitcurator/gui/app.py (branch refactor/gui-app-split; see docs/history/REFACTOR_PLAN.md)."""

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

class InputProxyMixin:
    """InputProxyMixin"""

    def select_import_file(self):
        """v0.23.0 — the Import txt file picker: .txt AND .md (one URL per
        line; # comments and blank lines skipped by the worker's importer)."""
        file, _ = QFileDialog.getOpenFileName(
            self, "Import txt file", "",
            "Text & Markdown (*.txt *.md);;Text Files (*.txt);;Markdown (*.md);;All files (*)")
        if file:
            self.import_file.setText(file)
            self.log_message(
                f"📄 Import file selected: {file}", "info")

    def show_input_help(self):
        """Popup with concise usage instructions for the Input tab (the
        single Import txt file mode)."""
        self._show_custom_message_box(
            "Input — How to Use",
            "Import txt file: pick a .txt or .md file with one URL per line "
            "(lines starting with # are comments; blank lines are skipped), "
            "then click PROCESS on the main view.\n\n"
            "GitHub repos are noted into the GitHub vault; every other "
            "website into the Websites vault — exactly like a fetched "
            "batch.\n\n"
            "To fetch from Telegram instead, click SYNC — it pulls every "
            "undone item from the bot queue.",
            success=True
        )

    def _get_proxy_dict(self):
        return {
            "enabled": self.proxy_enabled.isChecked(),
            "type": self.proxy_type.currentText(),
            "host": self.proxy_host.text(),
            "port": int(self.proxy_port.text()) if self.proxy_port.text().isdigit() else 10808,
            # v0.19.0 — carried along so live snapshots (Test Connection,
            # batch runs) see the same setting the Websites pipeline uses.
            "use_for_web": self.proxy_use_for_web.isChecked()
        }

    def _check_proxy_health(self):
        """v22 Feature 7: Poll the configured proxy port and update the
        status-label dot in the action button row. Non-blocking — uses a
        2-second socket connect_ex timeout. Runs on the GUI thread (the
        socket call returns well within 2s whether the host is up or down)."""
        # Guard: widget might not exist yet (early init) or might've been
        # destroyed during shutdown.
        if not hasattr(self, 'proxy_status_label'):
            return
        try:
            proxy = self._get_proxy_dict()
        except Exception:
            return
        try:
            if not proxy.get('enabled'):
                self.proxy_status_label.setPixmap(
                    _icons.pixmap('dot', '#8E8A90', 12))
                self.proxy_status_label.setToolTip("Proxy disabled")
                self._set_proxy_status_text("Idle", "Proxy disabled")
                return
            host = proxy.get('host', '127.0.0.1') or '127.0.0.1'
            try:
                port = int(proxy.get('port', 10808))
            except (TypeError, ValueError):
                port = 10808
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            try:
                result = sock.connect_ex((host, port))
            finally:
                try:
                    sock.close()
                except Exception:
                    pass
            if result == 0:
                self.proxy_status_label.setPixmap(
                    _icons.pixmap('dot', '#42B36B', 12))
                self.proxy_status_label.setToolTip(f"Proxy OK ({host}:{port})")
                self._set_proxy_status_text("Connected", f"Proxy OK ({host}:{port})")
            else:
                self.proxy_status_label.setPixmap(
                    _icons.pixmap('dot', '#E85D75', 12))
                self.proxy_status_label.setToolTip(f"Proxy unreachable ({host}:{port})")
                self._set_proxy_status_text("Error", f"Proxy unreachable ({host}:{port})")
        except Exception:
            try:
                self.proxy_status_label.setPixmap(
                    _icons.pixmap('dot', '#E85D75', 12))
                self.proxy_status_label.setToolTip("Proxy check failed")
                self._set_proxy_status_text("Error", "Proxy check failed")
            except Exception:
                pass

    def _set_proxy_status_text(self, state: str, tooltip: str):
        """v31.1 (WCAG 1.4.1): the status DOT is always accompanied by a TEXT
        label — color alone never conveys state ("Connected/Idle/Error").
        v0.07: the label also carries the matching SEMANTIC text color from
        the design system (success/error/muted), so the state is legible at
        a glance without reading the word."""
        if not hasattr(self, 'proxy_status_text'):
            return
        # v0.31.0 (balance pass): the monitor lives in the HEADER next
        # to the pipeline state word, so the label names its subject —
        # "Proxy: connected" — instead of a bare state word that could
        # be mistaken for the app state.
        # v0.34 (follow-up review): every state SPELLS a real word —
        # "off / connected / unreachable / checking…". The v0.33 dash
        # ("Proxy —") read as nothing and is retired; the dedup intent
        # survives, because none of these words is the pipeline row's
        # "Idle".
        word = {
            'Idle': 'off',
            'Connected': 'connected',
            'Error': 'unreachable',
            'Checking…': 'checking…',
        }.get(state, state.lower())
        self.proxy_status_text.setText(f"Proxy: {word}")
        self.proxy_status_text.setToolTip(tooltip)
        # WCAG 2.5.3 (Label in Name): the accessible name matches the
        # visible label exactly.
        self.proxy_status_text.setAccessibleName(f"Proxy: {word}")
        semantic = {
            'Connected': 'success',
            'Error':     'error',
            'Idle':      'muted',
            'Checking…': 'muted',
        }.get(state, 'muted')
        self._set_status(self.proxy_status_text, semantic)

