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
        """Mirror the pipeline state onto the SINGLE hero button (v0.03
        two-stage flow; v0.32 five-change pass — the button is also the
        Stop control): SYNC when idle → PROCESS once undone items have
        been fetched → STOP while a batch runs (the same button, danger
        fill + square glyph) → back to SYNC when it finishes or is
        cancelled. Reads ONLY the _batch_running flag (owned by
        _start_worker / processing_finished) plus the GUI-only
        _hero_state, and writes ONLY label text/state — zero pipeline
        coupling. No separate STOP button exists (one primary action).

        v0.24.1 — the pending count covers BOTH pipelines: pending repos
        AND pending website links (fix for "websites never sync" — with a
        GitHub-only count the button said PROCESS/0 while website links
        waited in the queue)."""
        try:
            state = getattr(self, '_hero_state', 'sync')
            running = getattr(self, '_batch_running', False)
            if running:
                # A batch runs: the button IS Stop (rendered by
                # _set_hero_state('running') from _start_worker; this
                # mirror only catches a missed transition).
                if state != 'running':
                    self._hero_state = 'running'
                    self._set_hero_state('running')
                return
            if state == 'running':
                # The batch just ended (flag cleared) → SYNC.
                self._hero_state = 'sync'
                self._set_hero_state('sync')
                state = 'sync'
            if state == 'fetching':
                # Fetch in flight: keep the (disabled) FETCHING rendering.
                return
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
        fetched). Stop (v0.32: the SAME button while a batch runs) →
        cancel the running batch — a cooperative, between-items stop
        that leaves no partial state (unfinished links are surfaced by
        the retry banner / next reconciliation), so no confirmation
        dialog is needed."""
        state = getattr(self, '_hero_state', 'sync')
        if state == 'running':
            # v0.32 (five-change pass): the one button IS the Stop
            # control — no separate STOP button exists.
            self.stop_processing()
            return
        if state == 'process':
            self._begin_hero_processing()
            return
        if state != 'sync':
            return  # fetching (button disabled)

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
        # v0.31.0 (balance pass): the fetch IS the syncing stage — flip the
        # end-of-row state indicator (guarded: the headless hero-flow test
        # stubs have no chrome).
        if hasattr(self, '_set_pipeline_state'):
            self._set_pipeline_state('syncing')
        started = self.check_bot_queue(on_done=self._after_sync_fetch)
        if not started:
            # Early bail (busy Telegram lock / missing fields) — the reason
            # was already logged; restore the SYNC button.
            self._set_hero_state('sync')
            self.progress_bar.setFormat("Ready")
            if hasattr(self, '_set_pipeline_state'):
                self._set_pipeline_state('idle')

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
        # v0.31.0 (balance pass): the fetch just completed — flash the
        # end-of-row state to Done, then settle back to Idle (the shared
        # hide timer owns the reset). Guarded for the headless stubs.
        _state = getattr(self, '_set_pipeline_state', None)
        if callable(_state):
            _state('done')
            _settle = getattr(self, '_schedule_progress_hide', None)
            if callable(_settle):
                _settle()
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
            # v0.51.0 — the caught-up check reads the table FIRST: the
            # owner's law ("before declaring everything is uptodate it
            # must check 'decomissioned' note and find those that should
            # be retried") — a " - " row with no verdict emoji is a valid
            # link whose fetch failed, so it is fetched AGAIN instead of
            # the empty-state claim. Only a table with no waiting fetch
            # row may say "everything is up to date". (Guarded: the bare
            # hero-flow stubs carry no processing-control collaborator.)
            _scan_master = getattr(self, '_scan_master_waiting', None)
            _waiting = _scan_master() if callable(_scan_master) else []
            _work_started = False
            if _waiting:
                _eyes = getattr(self, '_master_waiting_eyes', 0)
                self.log_message(
                    f"📋 Bot queue caught up, but {len(_waiting)} ' - ' "
                    f"row(s) in the master table "
                    f"(_review/DECOMMISSIONED.md) are still waiting — "
                    f"valid links whose fetches failed. Fetching them "
                    f"again now"
                    + (f" ({_eyes} row(s) wait for your eyes: set ✅ or "
                       f"🪦 in the table)" if _eyes else "")
                    + ".", "info")
                self._start_master_retry()
                _work_started = True
            # v0.52.0 — the 🖐 hand rows get their scrape before the
            # claim too (the owner's report: "I set hand emoji, but
            # those links didn't refetched using scrapping manually on
            # chrome"): a hand row is a request for the app's own
            # Chrome pass, not a note to self — the caught-up sync
            # opens the tabs itself, the pages land as real fetches.
            # (Guarded: the bare hero-flow stubs carry no
            # processing-control collaborator.)
            _scan_hand = getattr(self, '_scan_master_hand', None)
            _hand_rows = _scan_hand() if callable(_scan_hand) else []
            if _hand_rows:
                _deliver = getattr(self, '_start_chrome_tab_retry', None)
                try:
                    for _l in _hand_rows:
                        if isinstance(_l, dict):
                            _l.setdefault(
                                'error', _l.get('wall')
                                or 'the 🖐 hand gesture in the master table')
                except Exception:
                    pass    # a stub shape never breaks the flow
                self.log_message(
                    f"🖐 Bot queue caught up, but {len(_hand_rows)} 🖐 "
                    f"hand row(s) are waiting for your real Chrome — "
                    f"scraping them now (one tab per link, the live "
                    f"pages become real fetches)", "info")
                if callable(_deliver):
                    try:
                        _deliver(_hand_rows)
                        _work_started = True
                    except Exception as _e:
                        self.log_message(
                            f"⚠️ The hand-row Chrome pass could not "
                            f"start ({_e}) — More ▸ 🖐 Scrape hand rows "
                            f"via Chrome runs it anytime", "warning")
            if _work_started:
                return
            self._set_hero_state('sync')
            self.progress_bar.setFormat("Ready")
            self.log_message("✅ All caught up — nothing undone in the bot queue.", "success")
            # v0.31.0 (balance pass): a sync that finds nothing new ends
            # with a clean, meaningful log card — the fetch chatter is
            # cleared and the EMPTY STATE carries the summary ("Everything
            # is up to date — last sync …").
            self._log_caught_up_state()
        else:
            # The fetch error was already logged by check_bot_queue.
            self._set_hero_state('sync')
            self.progress_bar.setFormat("Ready")
            _fail_state = getattr(self, '_set_pipeline_state', None)
            if callable(_fail_state):
                _fail_state('error')
                _settle = getattr(self, '_schedule_progress_hide', None)
                if callable(_settle):
                    _settle()

    def _log_caught_up_state(self):
        """v0.31.0 (balance pass): record the caught-up sync time and clear
        the log card so its EMPTY STATE can speak ("Everything is up to
        date · Last sync: … · No new messages found."). Guarded throughout:
        the headless hero-flow test stubs run this method with NO widgets
        and must keep working unchanged."""
        self._log_last_uptodate = datetime.now().strftime("%H:%M:%S")
        entries = getattr(self, '_all_log_entries', None)
        if entries is not None:
            entries.clear()
        view = getattr(self, 'log_text', None)
        if view is not None:
            view.clear()
            reset_map = getattr(view, 'reset_full_texts', None)
            if callable(reset_map):
                reset_map()
        updater = getattr(self, '_update_log_empty_state', None)
        if callable(updater):
            updater()

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
        flags — the running flag stays with _start_worker /
        processing_finished. v0.07: each stage pairs its label with a unified
        SVG glyph (refresh / loader / play / stop) tinted for the fill.
        v0.32 (five-change pass): 'running' renders the SAME button as
        Stop (danger fill, square glyph) — the separate STOP button is
        gone; every other state restores the hero_primary fill."""
        self._hero_state = state
        try:
            if state == 'running':
                # The batch runs: the one button becomes Stop.
                self.start_btn.setText("Stop")
                _icons.set_btn_icon(self.start_btn, 'stop', '#FFFFFF', 18)
                self.start_btn.setEnabled(True)
                self._style_btn(self.start_btn, 'hero_danger')
                self.start_btn.setToolTip(
                    "Cancel the running batch (SYNC returns when it stops).\n"
                    "The stop is clean — already-processed notes are kept;\n"
                    "unfinished links are surfaced for retry when it ends."
                )
            elif state == 'fetching':
                self.start_btn.setText("FETCHING…")
                _icons.set_btn_icon(self.start_btn, 'loader', '#6C6480', 18)
                self.start_btn.setEnabled(False)
                self._style_btn(self.start_btn, 'hero_primary')
                self.start_btn.setToolTip("Fetching undone items from the Telegram bot…")
            elif state == 'process':
                # v0.24.1 — the count includes pending WEBSITES links too.
                n = len(getattr(self, '_bot_queue_urls', None) or []) \
                    + len(getattr(self, '_bot_queue_pending_websites', None) or [])
                self.start_btn.setText(f"PROCESS ({n})" if n else "PROCESS")
                _icons.set_btn_icon(self.start_btn, 'play', self._hero_text_color(), 18)
                self.start_btn.setEnabled(True)
                self._style_btn(self.start_btn, 'hero_primary')
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
                _icons.set_btn_icon(self.start_btn, 'refresh', self._hero_text_color(), 18)
                self.start_btn.setEnabled(True)
                self._style_btn(self.start_btn, 'hero_primary')
                self.start_btn.setToolTip(
                    "Stage 1 — fetch every UNDONE item from the Telegram bot\n"
                    "(repos already in the vault and decommissioned ones are skipped;\n"
                    "website links already in the Websites vault are skipped too).\n"
                    "The button then becomes PROCESS — click it to start the batch.\n"
                    "While a batch runs this button becomes Stop — click to cancel."
                )
        except RuntimeError:
            pass  # widgets already destroyed during shutdown

