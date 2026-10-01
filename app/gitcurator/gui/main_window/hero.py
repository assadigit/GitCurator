"""MainWindow HeroMixin — methods moved verbatim from the MainWindow in gitcurator/gui/app.py (branch refactor/gui-app-split; see docs/history/REFACTOR_PLAN.md)."""

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

class HeroMixin:
    """HeroMixin"""

    def _confirm_batch(self, count: int, source: str) -> bool:
        """v31.1 safety gate: confirm before starting any batch operation on
        MORE THAN 10 items, stating the exact item count."""
        if count <= 10:
            return True
        return self._show_custom_question(
            "Confirm Large Batch",
            f"This will process {count} items ({source}).\n\nContinue?"
        )

    def _sync_run_button(self):
        """Mirror the pipeline state onto the single hero button (v0.03
        two-stage flow): SYNC when idle → PROCESS once undone items have
        been fetched → STOP while a batch runs → back to SYNC when it
        finishes. Reads ONLY the existing enabled-state (owned by
        _start_worker / processing_finished) plus the GUI-only _hero_state
        and writes ONLY widget visibility/text — zero pipeline coupling.

        v0.24.1 — the pending count covers BOTH pipelines: pending repos
        AND pending website links (fix for "websites never sync" — with a
        GitHub-only count the button said PROCESS/0 while website links
        waited in the queue)."""
        try:
            state = getattr(self, '_hero_state', 'sync')
            if state == 'fetching':
                # Fetch in flight: keep the (disabled) FETCHING button visible.
                self.start_btn.setVisible(True)
                self.stop_btn.setVisible(False)
                return
            running = not self.start_btn.isEnabled()
            if running:
                if state != 'running':
                    self._hero_state = 'running'
                self.start_btn.setVisible(False)
                self.stop_btn.setVisible(True)
            else:
                if state == 'running':
                    # The batch just finished (start_btn re-enabled) → SYNC.
                    self._hero_state = 'sync'
                    self._set_hero_state('sync')
                    state = 'sync'
                self.start_btn.setVisible(True)
                self.stop_btn.setVisible(False)
                if state == 'process':
                    # Keep the pending count fresh — a Settings → Bot
                    # re-check updates self._bot_queue_urls in place.
                    n = len(getattr(self, '_bot_queue_urls', None) or []) \
                        + len(getattr(self, '_bot_queue_pending_websites', None) or [])
                    txt = f"PROCESS ({n})" if n else "PROCESS"
                    if self.start_btn.text() != txt:
                        self.start_btn.setText(txt)
        except RuntimeError:
            pass  # widgets already destroyed during shutdown

    # ------------------------------------------------------------------
    # v0.03 — two-stage SYNC flow (GUI-only orchestration; the workers,
    # check_bot_queue's fetch logic and the processing pipeline are
    # unchanged — these methods only sequence and render them).
    # ------------------------------------------------------------------
    def _on_hero_clicked(self):
        """Hero button click. SYNC → fetch every UNDONE item from the
        Telegram bot (bot-queue check). PROCESS → start the batch (the
        fetched queue, or the selected Input mode when nothing was
        fetched)."""
        state = getattr(self, '_hero_state', 'sync')
        if state == 'process':
            self._begin_hero_processing()
            return
        if state != 'sync':
            return  # fetching (button disabled) or running (STOP overlay)

        # --- Stage 1: SYNC → fetch undone items --------------------------
        vault = self.vault_combo.currentText()
        if not vault or not os.path.isdir(vault):
            self._show_custom_message_box("Error", "Please select a valid Obsidian vault path.", success=False)
            return
        bot_username = self.bot_username.text().strip().lstrip('@')
        has_creds = bool(self.api_id.text() and self.api_hash.text() and self.phone.text())
        if not bot_username or not has_creds:
            # No bot configured → keep the Import txt file path usable from
            # the main button (v0.23.0: the ONE remaining input mode).
            if self.import_file.text().strip():
                self.start_processing()
                return
            self._show_custom_message_box(
                "Nothing to sync",
                "SYNC fetches undone items from your Telegram bot.\n\n"
                "1) Settings → Bot — enter the bot username, and\n"
                "2) Settings → Credentials — fill the Telegram API credentials.\n\n"
                "To import from a file instead, pick it in Settings → Input.",
                success=False
            )
            return

        self._set_hero_state('fetching')
        self.progress_bar.setFormat("Fetching undone items…")
        started = self.check_bot_queue(on_done=self._after_sync_fetch)
        if not started:
            # Early bail (busy Telegram lock / missing fields) — the reason
            # was already logged; restore the SYNC button.
            self._set_hero_state('sync')
            self.progress_bar.setFormat("Ready")

    def _after_sync_fetch(self, _name, result):
        """Fires when check_bot_queue's worker finishes (connected AFTER its
        own _on_finished handler, so self._bot_queue_urls is already updated
        when this runs). Flips the hero button SYNC → PROCESS.

        v0.24.1 — the flip now fires when EITHER pipeline has pending work:
        pending GitHub repos OR pending website links. Before the fix, a
        caught-up GitHub vault masked unprocessed websites and the button
        fell back to SYNC with "All caught up" — the Websites pipeline
        then NEVER ran from the queue."""
        pending = getattr(self, '_bot_queue_urls', None) or []
        pending_web = getattr(self, '_bot_queue_pending_websites', None) or []
        n_total = len(pending) + len(pending_web)
        # v0.23.0 — the only input mode left is the Import txt file
        # (Settings → 📥 Input): PROCESS runs it when the queue is caught
        # up AND a file is picked.
        import_ready = bool(self.import_file.text().strip())
        if result.get('success') and n_total:
            self._set_hero_state('process')
            self.progress_bar.setFormat(f"{n_total} ready to process")
            # v0.07: the counter reflects the fetched queue immediately.
            self.progress_count.setText(f"0 / {n_total}")
            self.progress_count.setToolTip(
                f"{n_total} fetched item(s) ready to process")
            _parts = []
            if pending:
                _parts.append(f"{len(pending)} repo(s)")
            if pending_web:
                _parts.append(f"{len(pending_web)} website link(s)")
            self.log_message(
                "🟢 Fetched " + " + ".join(_parts) + " — click PROCESS to start.",
                "success"
            )
        elif result.get('success') and import_ready:
            # Queue all caught up → PROCESS will run the import file.
            self._set_hero_state('process')
            self.progress_bar.setFormat("Import file ready")
            self.log_message(
                "✅ Bot queue is all caught up — PROCESS will import the file "
                "picked in Settings → Input instead.",
                "info"
            )
        elif result.get('success'):
            self._set_hero_state('sync')
            self.progress_bar.setFormat("Ready")
            self.log_message("✅ All caught up — nothing undone in the bot queue.", "success")
        else:
            # The fetch error was already logged by check_bot_queue.
            self._set_hero_state('sync')
            self.progress_bar.setFormat("Ready")

    def _begin_hero_processing(self):
        """Stage 2: PROCESS click → run the batch. The fetched undone items
        ALWAYS win (user spec: click PROCESS → it starts); the legacy
        input-mode path only runs when the fetch found nothing (note: the
        Markers radio is checked by default, so that's the marker
        workflow's launcher).

        v0.24.1 — "fetched undone items" now includes pending WEBSITES:
        process_bot_queue starts a websites-only batch when the GitHub
        side is already caught up."""
        pending = getattr(self, '_bot_queue_urls', None) or []
        pending_web = getattr(self, '_bot_queue_pending_websites', None) or []
        if pending or pending_web:
            self.process_bot_queue()   # existing: confirm gate + worker start
            return
        self.start_processing()        # legacy input-mode path (single /

    def _set_hero_state(self, state):
        """Render the GUI-only hero-button state. Never touches pipeline
        flags — enabled-state ownership stays with _start_worker /
        processing_finished. v0.07: each stage pairs its label with a unified
        SVG glyph (refresh / loader / play / stop) tinted for the fill."""
        self._hero_state = state
        try:
            if state == 'fetching':
                self.start_btn.setText("FETCHING…")
                _icons.set_btn_icon(self.start_btn, 'loader', '#6C6480', 18)
                self.start_btn.setEnabled(False)
                self.start_btn.setToolTip("Fetching undone items from the Telegram bot…")
            elif state == 'process':
                # v0.24.1 — the count includes pending WEBSITES links too.
                n = len(getattr(self, '_bot_queue_urls', None) or []) \
                    + len(getattr(self, '_bot_queue_pending_websites', None) or [])
                self.start_btn.setText(f"PROCESS ({n})" if n else "PROCESS")
                _icons.set_btn_icon(self.start_btn, 'play', COLORS['hero_text'], 18)
                self.start_btn.setEnabled(True)
                if n:
                    self.start_btn.setToolTip(
                        f"Stage 2 — start curating the {n} fetched undone item(s) "
                        "into the Obsidian vault(s).\nA confirmation appears for large "
                        "batches (more than 10 items)."
                    )
                else:
                    self.start_btn.setToolTip(
                        "Nothing was fetched — clicking runs the selected Input "
                        "mode\n(Settings → Input) instead."
                    )
            else:   # 'sync' — also restores after running/fetching
                self.start_btn.setText("SYNC")
                _icons.set_btn_icon(self.start_btn, 'refresh', COLORS['hero_text'], 18)
                self.start_btn.setEnabled(True)
                self.start_btn.setToolTip(
                    "Stage 1 — fetch every UNDONE item from the Telegram bot\n"
                    "(repos already in the vault and decommissioned ones are skipped;\n"
                    "website links already in the Websites vault are skipped too).\n"
                    "The button then becomes PROCESS — click it to start the batch.\n"
                    "While a batch runs this button becomes STOP — click to cancel."
                )
            # The STOP overlay always carries the white square glyph.
            _icons.set_btn_icon(self.stop_btn, 'stop', '#FFFFFF', 18)
        except RuntimeError:
            pass  # widgets already destroyed during shutdown

