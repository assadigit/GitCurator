"""MainWindow TestConnectionModalMixin — methods moved verbatim from the MainWindow in gitcurator/gui/app.py (branch refactor/gui-app-split; see docs/history/REFACTOR_PLAN.md)."""

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

_APP_DIR = APP_DIR

from gitcurator.gui.dialogs import ConnectionTestDialog

from gitcurator.gui.processing_worker import TestWorker

from gitcurator.gui.worker_jobs import _bot_queue_job, _connection_battery_job, _telegram_test_job

class TestConnectionModalMixin:
    """TestConnectionModalMixin"""

    def test_all(self):
        """🔌 Test Connection — the owner's four-subsystem readiness check,
        v0.23.0: a MODAL with live per-subsystem status (owner spec: "a
        modal must open with a loading, then everything that is connected
        gets an emoji check; a close button and a Start Syncing button
        that turns green after everything is connected"):

          [1/4] 📁 Vaults    — found + writable (ready to receive notes)
          [2/4] 🧠 LLM       — the ACTIVE provider: cloud (OpenAI-compatible
                               or Claude) / Ollama / llama.cpp
          [3/4] 🐙 GitHub    — token valid + the backup repos ready
          [4/4] ✈️ Telegram  — credentials/session/bot/proxy + a LIVE
                               connection test through the same subprocess
                               a batch uses

        The battery (1-4 local) runs in one background TestWorker whose
        section/result signals drive the modal's rows (and still stream
        the log line by line); the LIVE Telegram leg starts only after it
        (session.session is single-user — serialized like every other
        Telegram button) and finalizes the Telegram row. The verdict
        enables 🚀 Start Syncing (→ the hero SYNC flow). The interactive
        login dialog still works: the live leg wires code_requested, so a
        first-run user can complete the account login during the test.
        """
        if self.worker is not None and not self.worker.isFinished():
            self.log_message(
                "⏳ A batch is running — Test Connection is available when "
                "it finishes.", "warning")
            return
        if not self._acquire_telegram_lock("connection_check"):
            self.log_message(
                "⏳ Another Telegram operation is already running — try Test "
                "Connection again in a moment.", "warning")
            return
        self.log_message(
            "🔍 Test Connection — checking vaults, LLM, GitHub and "
            "Telegram…", "info")

        # v0.23.0 — the modal (kept on self so every leg can route into it;
        # guarded everywhere with isVisible() — a closed dialog never
        # crashes a late result).
        self._cc_dialog = ConnectionTestDialog(self)

        # Snapshot: saved config + the live credential widgets (the same
        # values the per-test buttons read — unsaved edits get tested too).
        snapshot = dict(self.config)
        try:
            tok = self.github_token.text().strip()
            if tok:
                snapshot['github_token'] = tok
            aid = self.api_id.text().strip()
            if aid:
                snapshot['telegram_api_id'] = aid
            ahash = self.api_hash.text().strip()
            if ahash:
                snapshot['telegram_api_hash'] = ahash
            ph = self.phone.text().strip()
            if ph:
                snapshot['telegram_phone'] = ph
            bot = self.bot_username.text().strip().lstrip('@')
            if bot:
                snapshot['bot_username'] = bot
            snapshot['proxy'] = self._get_proxy_dict()
        except Exception:
            pass  # widget access must never break the check
        self._cc_sections = None

        worker = TestWorker(_connection_battery_job, "connection_battery",
                            snapshot)

        def _job(cfg):
            return _connection_battery_job(
                cfg, worker.log_message,
                on_section=worker.section_signal.emit,
                on_result=worker.result_signal.emit)
        worker._fn = _job
        worker.log_message.connect(self.log_message)

        # v0.23.0 — structured progress into the modal's rows.
        def _on_section(idx, _title, _total):
            dlg = getattr(self, '_cc_dialog', None)
            if dlg is not None and dlg.isVisible():
                dlg.set_row_checking(idx)
        worker.section_signal.connect(_on_section)

        def _on_result(idx, r):
            dlg = getattr(self, '_cc_dialog', None)
            if dlg is not None and dlg.isVisible():
                # v0.37.0 — the hierarchy pass: the dialog row gets the
                # compact ONE-line summary; the full text rides the item
                # tooltip. The log still streams the full render_line.
                dlg.add_row_detail(
                    idx, _connection_check.short_line(r),
                    full=_connection_check.render_line(r))
        worker.result_signal.connect(_on_result)

        def _on_battery_done(_name, result):
            if getattr(self, '_closing', False):
                self._release_telegram_lock("connection_check")
                return
            sections = (result or {}).get("sections")
            if not sections:
                # The battery itself crashed — NEVER report 'ALL SYSTEMS
                # READY' from an empty result; surface the crash instead.
                err = (result or {}).get("error") or "the check crashed"
                sections = [["Check", [{"name": "Test battery",
                                        "level": "error",
                                        "detail": str(err)[:300]}]]]
            self._cc_sections = sections
            self._cc_dialog_sync_rows(sections)
            self._cc_telegram_leg(snapshot)

        worker.finished_signal.connect(_on_battery_done)
        self._active_test_workers.append(worker)
        worker.start()
        self._animate_dialog(self._cc_dialog)
        self._cc_dialog.exec()

    def _cc_dialog_sync_rows(self, sections):
        """v0.23.0 — settle the modal's rows from the battery's sections
        (rows 1-3 final; the Telegram row stays spinning through the live
        leg — unless the section list is degenerate, in which case every
        mapped row settles with its own verdict)."""
        dlg = getattr(self, '_cc_dialog', None)
        if dlg is None or not dlg.isVisible():
            return
        for i, (_title, results) in enumerate(sections or [], start=1):
            levels = [r.get("level") for r in (results or [])] or ["info"]
            verdict = ("error" if "error" in levels
                       else "warn" if "warning" in levels else "ok")
            dlg.finalize_row(i, verdict)

    def _cc_telegram_leg(self, snapshot):
        """The LIVE Telegram test — runs after the battery (never two
        Telethon children at once). Bot queue when a bot is configured
        (proves account login AND the bot chat through the same session),
        else the Saved-Messages preview (account login only)."""
        api_id = str(snapshot.get('telegram_api_id', '') or '').strip()
        api_hash = str(snapshot.get('telegram_api_hash', '') or '').strip()
        phone = str(snapshot.get('telegram_phone', '') or '').strip()
        bot = str(snapshot.get('bot_username', '') or '').strip().lstrip('@')
        if not (api_id and api_hash and phone):
            self.log_message(
                "⏭️ Live Telegram test skipped — credentials incomplete "
                "(see the Telegram lines above).", "info")
            self._release_telegram_lock("connection_check")
            self._cc_finish()
            return
        proxy = snapshot.get('proxy') or {}
        if bot:
            self.log_message(
                f"📡 Live Telegram test — reading the @{bot} queue through "
                f"your session…", "info")
            worker = TestWorker(_bot_queue_job, "connection_telegram",
                                api_id, api_hash, phone, proxy, bot,
                                None, None)

            def _job(aid, ahash, ph, px, _b, _ignored_log, _ignored_code):
                return _bot_queue_job(aid, ahash, ph, px, bot,
                                      worker.log_message, worker.request_code,
                                      mark_read=False, min_id=0,
                                      vault_path=None)
            mode = "bot"
        else:
            self.log_message(
                "📡 Live Telegram test — no bot configured; testing the "
                "account login (Saved Messages)…", "info")
            worker = TestWorker(_telegram_test_job, "connection_telegram",
                                api_id, api_hash, phone, proxy, None, None)

            def _job(aid, ahash, ph, px, _ignored_log, _ignored_code):
                return _telegram_test_job(aid, ahash, ph, px,
                                          worker.log_message,
                                          worker.request_code)
            mode = "account"
        worker._fn = _job
        worker.log_message.connect(self.log_message)
        worker.code_requested.connect(
            lambda pt, w=worker: self._on_telegram_code_requested(pt, w))

        def _on_done(_name, result):
            line = _connection_check.telegram_live_result(result, mode)
            self.log_message("   " + _connection_check.render_line(line),
                             _connection_check.GUI_LEVELS.get(line["level"],
                                                              "info"))
            # v0.37.0 — the live leg's verdict now ALSO lands in the dialog
            # as a one-line item under the Telegram heading (it used to be
            # log-only, so the modal's Telegram section stayed incomplete).
            _dlg = getattr(self, '_cc_dialog', None)
            if _dlg is not None and _dlg.isVisible():
                _dlg.add_row_detail(
                    4, _connection_check.short_line(line),
                    full=_connection_check.render_line(line))
            if isinstance(self._cc_sections, list) and self._cc_sections:
                self._cc_sections[-1][1].append(line)
            self._cc_finish()

        worker.finished_signal.connect(_on_done)
        # _keep_worker releases the connection_check lock when this finishes
        self._keep_worker(worker, owner="connection_check")
        worker.start()

    def _cc_finish(self):
        """The one-line verdict — the 'user is ensured everything is up'.
        v0.23.0 — also settles the modal: every section's verdict lands on
        its row and Start Syncing unlocks (green) only when ALL are ok."""
        s = _connection_check.summarize(self._cc_sections or [])
        level = _connection_check.GUI_LEVELS.get(s["level"], "info")
        self.log_message(f"🏁 Test Connection — {s['headline']}", level)
        dlg = getattr(self, '_cc_dialog', None)
        if dlg is not None and dlg.isVisible():
            self._cc_dialog_sync_rows(self._cc_sections or [])
            dlg.finish_all()
        self._cc_sections = None

