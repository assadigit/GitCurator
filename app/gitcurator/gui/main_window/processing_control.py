"""MainWindow ProcessingControlMixin — methods moved verbatim from the MainWindow in gitcurator/gui/app.py (branch refactor/gui-app-split; see docs/history/REFACTOR_PLAN.md)."""

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

from gitcurator.gui.link_helpers import normalize_url

from gitcurator.gui.processing_worker import ProcessingWorker

class ProcessingControlMixin:
    """ProcessingControlMixin"""

    def start_processing(self):
        """v0.23.0 — the simplified PROCESS dispatcher. Two input paths
        remain: the fetched bot queue (SYNC) and the Import txt file
        (Settings → 📥 Input). The ID Range / Markers / Single Msg
        branches were removed with their modes."""
        self.save_config()
        vault = self.vault_combo.currentText()
        if not vault or not os.path.isdir(vault):
            self._show_custom_message_box("Error", "Please select a valid Obsidian vault path.", success=False)
            return

        # Fetched bot queue first (the SYNC flow's fetched items).
        # v0.24.1 — Fix (websites never sync): the gate covers pending
        # WEBSITES too — a websites-only batch starts here when the GitHub
        # side is already caught up. Before, "0 pending repos" blocked the
        # batch and the Websites pipeline never ran from the queue.
        bot_urls = getattr(self, '_bot_queue_urls', [])
        web_pending = getattr(self, '_bot_queue_pending_websites', []) or []
        if bot_urls or web_pending:
            # v31.1 safety gate: confirm before large batches (>10 items).
            if not self._confirm_batch(len(bot_urls) + len(web_pending), "the bot queue"):
                self.log_message("⏹️ Batch cancelled — nothing was processed.", "warning")
                return
            if bot_urls and web_pending:
                self.log_message(
                    f"🚀 Processing {len(bot_urls)} repo(s) + {len(web_pending)} "
                    "website link(s) from bot queue...", "info")
            elif bot_urls:
                self.log_message(f"🚀 Processing {len(bot_urls)} repos from bot queue...", "info")
            else:
                self.log_message(
                    f"🚀 Processing {len(web_pending)} website link(s) from bot "
                    "queue (Websites pipeline)...", "info")
            # v23 — pass bot_source=True so the manifest records the source
            # as 'bot' and Phase 5 auto-mark-read can fire on success. Also
            # pass the non-GitHub links so they are tracked in the manifest
            # (they were already added to the inbox table by check_bot_queue).
            self._start_worker_with_urls(
                bot_urls,
                bot_source=True,
                non_github_urls=getattr(self, '_bot_queue_non_github', [])
            )
            return

        # Import txt file (the ONE input mode): .txt or .md, one URL per
        # line — GitHub repos to the GitHub pipeline, everything else to
        # the Websites pipeline.
        import_file = self.import_file.text().strip()
        if import_file and os.path.exists(import_file):
            self._start_worker('import', None, None, None, None, import_file, None)
            return

        # Nothing fetched and no file picked — the helpful message.
        self._show_custom_message_box(
            "Nothing to Process",
            "No undone items fetched and no import file picked.\n\n"
            "Click SYNC first — it fetches every undone item from the "
            "Telegram bot, then becomes PROCESS.\n"
            "Or pick a .txt / .md file in Settings → Input.",
            success=False
        )

    def _start_worker_with_urls(self, urls, bot_source=False, non_github_urls=None,
                                intake_duplicates=0, raw_url_count=0):
        """Start a ProcessingWorker in 'direct' mode with a pre-fetched list
        of URLs. Used by the bot-queue, sources, and retry flows.

        v23 — No Link Left Behind:
          * ``bot_source=True``  labels the manifest source as 'bot' (so the
            Phase 5 auto-mark-read flow can fire on success).
          * ``non_github_urls`` is the list of non-GitHub links discovered
            alongside the GitHub URLs (typically from the bot queue). They
            are recorded in the manifest and the inbox table so they are
            never silently dropped.

        v25 pre-flight:
          * ``intake_duplicates`` / ``raw_url_count`` are surfaced in the
            final report so the user can see how many duplicate URLs were
            deduped during intake ("🔄 15 duplicates removed (200 unique
            from 215 total)").
        """
        self._start_worker(
            'direct', None, None, None, None, None, urls,
            bot_source=bot_source,
            non_github_urls=non_github_urls,
            intake_duplicates=intake_duplicates,
            raw_url_count=raw_url_count,
        )

    # -- v0.42.0: the _review backlog notice + retry ------------------------

    def _scan_backlog_alive(self, vault: str):
        """v0.47.0 — the backlog scan minus the RETIRED links: a URL the
        fetcher's own auto-verdict retired (dead / paywalled / refused)
        is not a waiting wall — its placeholder is the record and the
        MASTER TABLE is its ledger. Opens the state DB with the same
        dry-run-shadow law the decommission enforcement uses; on any
        failure falls back to the pure file read (a broken probe never
        hides a waiting link from the owner)."""
        try:
            if _dryrun.is_enabled():
                state = _website_pipeline.WebsiteStateDB(
                    db_path=_dryrun.shadow_cache_path(
                        os.path.join(APP_DIR, 'cache.db')))
            else:
                state = _website_pipeline.WebsiteStateDB()
            try:
                return _website_pipeline.scan_review_backlog(
                    vault, is_dismissed=state.is_dismissed)
            finally:
                state.close()
        except Exception:
            return _website_pipeline.scan_review_backlog(vault)

    def _startup_review_backlog_check(self):
        """v0.42.0 — the startup notice (the owner's ask): scan the Websites
        vault's _review folder; app-owned fetch-failed placeholders are the
        links the old honest-bot User-Agent got walled on (403/405). Offer
        the retry once per launch. The notice is self-extinguishing — retry
        them, they pass under the v0.41 browser-grade fetcher, the folder
        empties, the notice never fires again. Never interrupts a running
        batch; pure frontmatter reads (no state DB, no network).

        v0.44.0 — the third door: some of the pile is GENUINELY dead (real
        404s, lost pages, abandoned domains), so the notice also offers
        "🪦 Decommission dead…" (the graveyard table procedure). URLs
        already marked dead never reach this notice — the scan filters
        them out.

        v0.47.0 — links RETIRED by the fetcher's own auto-verdict (dead /
        paywalled / refused) leave the notice too (their placeholder is
        the record, not a waiting wall — the MASTER TABLE is their
        ledger), and the notice now names the table itself:
        ``<vault>/_review/DECOMMISSIONED.md`` — the master note with
        tables the owner asked for, refreshed after every batch."""
        try:
            if getattr(self, '_batch_running', False):
                return
            cfg = self.config or {}
            if not (cfg.get('pipelines') or {}).get('websites', False):
                return  # websites pipeline off — the backlog is not ours
            vault = (cfg.get('website_vault_path') or '').strip()
            if not vault or not os.path.isdir(vault):
                return
            items = self._scan_backlog_alive(vault)
            if not items:
                return
            urls = sorted({i['url'] for i in items})
            table = _website_pipeline.decommission_table_path(vault)
            self.log_message(
                f"📥 {len(items)} _review placeholder(s) waiting "
                f"({len(urls)} link(s) — fetch failures; the master table "
                f"at _review/{_website_pipeline.DECOMMISSION_TABLE} lists "
                f"every waiting and retired link)", "info")
            choice = self._show_backlog_notice(
                "Links waiting in _review",
                f"{len(urls)} link(s) lie in _review — their fetches were "
                f"refused (mostly 403/405 bot-defense walls).\n\n"
                f"Retry them now? Each link is fetched, analyzed and "
                f"stored properly; anything still walled keeps waiting.\n\n"
                f"The MASTER TABLE lists every one of them:\n"
                f"{table}\n"
                f"Set a row's Status to ✅ reviewed or 🪦 dead there and "
                f"it is never fetched again (♻️ revived brings it "
                f"back).\n\n"
                f"Some genuinely dead (404 / lost / abandoned)? Choose "
                f"🪦 Decommission dead — the picker buries them now."
            )
            if choice == 'retry':
                self._start_review_retry()
            elif choice == 'decommission':
                self.decommission_dead_links_now()
            else:
                self.log_message(
                    "⏭️ _review backlog deferred — More ▸ '🔁 Retry "
                    "_review backlog' or '🪦 Decommission dead links' "
                    "anytime.", "info")
        except Exception as e:
            try:
                self.log_message(
                    f"⚠️ _review backlog check skipped: {e}", "warning")
            except Exception:
                pass  # best-effort — never crash the startup

    def _show_backlog_notice(self, title: str, message: str) -> str:
        """v0.44.0 — the backlog notice's three doors: 'retry' /
        'decommission' / 'later'. Same theme idiom as
        _show_custom_question (roles, _style_btn, the shutdown guard);
        the shutdown default is 'later' (the safe answer)."""
        if getattr(self, '_closing', False) or not self.isVisible():
            try:
                self.log_message(f"(auto-Later during shutdown) {title}",
                                 "info")
            except Exception:
                pass
            return 'later'
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setModal(True)
        dialog.setMinimumWidth(460)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        header = QHBoxLayout()
        icon_label = QLabel("📥")
        icon_label.setObjectName("msg_glyph")
        header.addWidget(icon_label)
        title_label = QLabel(title)
        title_label.setObjectName("msg_heading")
        title_label.setProperty("tone", "warning")
        header.addWidget(title_label)
        header.addStretch()
        layout.addLayout(header)

        msg_label = QLabel(message)
        msg_label.setWordWrap(True)
        layout.addWidget(msg_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        later_btn = QPushButton("Later")
        self._style_btn(later_btn, 'secondary')
        later_btn.clicked.connect(lambda: dialog.done(0))  # 0 = later
        btn_row.addWidget(later_btn)
        dead_btn = QPushButton("🪦 Decommission dead…")
        self._style_btn(dead_btn, 'secondary')
        dead_btn.clicked.connect(lambda: dialog.done(2))  # 2 = decommission
        btn_row.addWidget(dead_btn)
        retry_btn = QPushButton("🔁 Retry now")
        self._style_btn(retry_btn, 'primary')
        retry_btn.clicked.connect(lambda: dialog.done(1))  # 1 = retry
        btn_row.addWidget(retry_btn)
        layout.addLayout(btn_row)

        self._animate_dialog(dialog)
        rc = dialog.exec()
        return {1: 'retry', 2: 'decommission'}.get(rc, 'later')

    def retry_review_backlog_now(self):
        """More ▸ 🔁 Retry _review backlog — the manual trigger (v0.42.0).
        Same flow as the startup notice, with the gates surfaced as
        dialogs instead of silence (the user ASKED for this one).
        v0.44.0: URLs marked 🪦 dead in the graveyard table are never
        retried — More ▸ 🪦 Decommission dead links manages them."""
        cfg = self.config or {}
        if not (cfg.get('pipelines') or {}).get('websites', False):
            self._show_custom_message_box(
                "Websites pipeline is off",
                "Turn the Websites pipeline on first (Settings → 📁 Vault).",
                success=False)
            return
        vault = (cfg.get('website_vault_path') or '').strip()
        if not vault or not os.path.isdir(vault):
            self._show_custom_message_box(
                "No Websites vault",
                "Set the Websites vault first (Settings → 📁 Vault).",
                success=False)
            return
        items = self._scan_backlog_alive(vault)
        if not items:
            self._show_custom_message_box(
                "_review backlog is empty",
                "No app-owned fetch-failed placeholders are waiting.\n\n"
                "Anything else in _review needs your eyes, not a retry.\n\n"
                "(Links marked 🪦 dead in _review/DECOMMISSIONED.md are "
                "buried — never retried. More ▸ 🪦 Decommission dead "
                "links manages them.)",
                success=True)
            return
        urls = sorted({i['url'] for i in items})
        if not self._confirm_batch(len(urls), "the _review backlog"):
            self.log_message("⏹️ _review backlog retry cancelled.", "warning")
            return
        self._start_review_retry()

    # -- v0.44.0: the graveyard — decommissioning dead links ---------------

    def decommission_dead_links_now(self):
        """More ▸ 🪦 Decommission dead links — the owner's procedure for
        links that are GENUINELY gone (a real 404, a lost page, an
        abandoned domain): the graveyard table at
        ``<vault>/_review/DECOMMISSIONED.md`` gets one row per failed
        link and the owner sets the Status emoji (🪦 dead — the same
        gesture as the _inbox tables' ✅ reviewed). This action also
        offers an in-app picker that writes the SAME Status cells, then
        enforces the burials immediately (dismissed + retry-queue rows
        dropped + placeholders swept). ♻️ revived on a row brings a link
        back to life."""
        cfg = self.config or {}
        if not (cfg.get('pipelines') or {}).get('websites', False):
            self._show_custom_message_box(
                "Websites pipeline is off",
                "Turn the Websites pipeline on first (Settings → 📁 Vault).",
                success=False)
            return
        vault = (cfg.get('website_vault_path') or '').strip()
        if not vault or not os.path.isdir(vault):
            self._show_custom_message_box(
                "No Websites vault",
                "Set the Websites vault first (Settings → 📁 Vault).",
                success=False)
            return
        items = self._scan_backlog_alive(vault)
        table = _website_pipeline.decommission_table_path(vault)
        if not items:
            self._show_custom_message_box(
                "No failed links waiting",
                "No app-owned fetch-failed placeholders are waiting in "
                "_review — nothing new to decommission.\n\n"
                "The graveyard table stays available for hand-rows:\n"
                f"{table}\n\n"
                "(Set a row's Status to 🪦 dead anytime — even a link "
                "that never got a placeholder. ♻️ revived brings one "
                "back.)",
                success=True)
            return
        urls = sorted({i['url'] for i in items})
        try:
            _website_pipeline.write_decommission_candidates(
                vault, urls, source='retry backlog',
                log=self.log_message)
        except Exception as e:
            self.log_message(f"⚠️ Graveyard candidates write skipped: {e}",
                             "warning")
        picked = self._pick_dead_links_dialog(urls, table)
        if not picked:
            self.log_message(
                "⏭️ Decommission cancelled — nothing was buried. The "
                "candidate rows wait in the table (set Status to 🪦 "
                "dead by hand in Obsidian; the next batch enforces it).",
                "info")
            return
        try:
            _website_pipeline.mark_urls_dead_in_table(
                vault, picked, log=self.log_message)
        except Exception as e:
            self.log_message(f"⚠️ Graveyard marking skipped: {e}",
                             "warning")
        # Enforce now (the same state DB a batch uses; the dry-run law
        # does not apply — this is the OWNER's explicit hand, and the
        # next batch would enforce it anyway).
        report = None
        try:
            if _dryrun.is_enabled():
                state = _website_pipeline.WebsiteStateDB(
                    db_path=_dryrun.shadow_cache_path(
                        os.path.join(APP_DIR, 'cache.db')))
            else:
                state = _website_pipeline.WebsiteStateDB()
            try:
                report = _website_pipeline.consume_decommission_table(
                    state, vault, log=self.log_message)
            finally:
                state.close()
        except Exception as e:
            self.log_message(
                f"⚠️ Graveyard enforcement deferred to the next batch: "
                f"{e}", "warning")
        buried = (report or {}).get('dead', 0)
        swept = (report or {}).get('placeholders_swept', 0)
        remaining = len(self._scan_backlog_alive(vault))
        self.log_message(
            f"🪦 Graveyard: {buried} link(s) decommissioned"
            + (f", {swept} placeholder(s) swept" if swept else "")
            + (f" — {remaining} still waiting in _review"
               if remaining else " — the _review backlog is clear"),
            "success")
        self._show_custom_message_box(
            "Decommissioned",
            f"{len(picked)} link(s) marked dead"
            + (f" — {buried} enforced now, {swept} placeholder(s) swept."
               if report is not None else
               " — the next batch enforces the burials.")
            + (f"\n\n{remaining} link(s) still wait in _review."
               if remaining else
               "\n\nThe _review backlog is clear — no more notices.")
            + "\n\nThe ledger: " + table
            + "\n(♻️ revived on a row brings a link back to life.)",
            success=True)

    def _pick_dead_links_dialog(self, urls, table_path):
        """v0.44.0 — the in-app burial picker: a checkable list of the
        failed links; the chosen ones get their graveyard-table Status
        cells written (the table stays the ONE ledger). 'Open the table'
        reveals the file in the OS file manager for hand-editing.
        Returns the chosen URLs (empty = cancelled)."""
        if getattr(self, '_closing', False) or not self.isVisible():
            return []
        dialog = QDialog(self)
        dialog.setWindowTitle("Decommission dead links")
        dialog.setModal(True)
        dialog.setMinimumWidth(620)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        header = QHBoxLayout()
        icon_label = QLabel("🪦")
        icon_label.setObjectName("msg_glyph")
        header.addWidget(icon_label)
        title_label = QLabel("Decommission dead links")
        title_label.setObjectName("msg_heading")
        title_label.setProperty("tone", "warning")
        header.addWidget(title_label)
        header.addStretch()
        layout.addLayout(header)

        msg_label = QLabel(
            "Tick the links that are GENUINELY dead — a real 404, a "
            "lost page, an abandoned domain. A buried link is never "
            "fetched, never retried, never noticed again; its _review "
            "placeholder is swept. (Unticked links keep waiting — ♻️ "
            "revived in the table brings a buried link back.)")
        msg_label.setWordWrap(True)
        layout.addWidget(msg_label)

        list_widget = QListWidget()
        list_widget.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        for u in urls:
            item = QListWidgetItem(u)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            list_widget.addItem(item)
        list_widget.setMinimumHeight(min(320, 24 * len(urls) + 24))
        layout.addWidget(list_widget)

        table_label = QLabel(f"The ledger: {table_path}")
        table_label.setWordWrap(True)
        layout.addWidget(table_label)

        btn_row = QHBoxLayout()
        open_btn = QPushButton("Open the table")
        self._style_btn(open_btn, 'secondary')
        open_btn.setToolTip(
            "Reveal the graveyard table in your file manager — hand-edit "
            "the Status column in Obsidian (🪦 dead / ♻️ revived).")

        def _open_table():
            try:
                from PyQt6.QtGui import QDesktopServices
                from PyQt6.QtCore import QUrl
                QDesktopServices.openUrl(QUrl.fromLocalFile(table_path))
            except Exception as e:
                self.log_message(f"⚠️ Could not open the table: {e}",
                                 "warning")

        open_btn.clicked.connect(_open_table)
        btn_row.addWidget(open_btn)
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        self._style_btn(cancel_btn, 'secondary')
        cancel_btn.clicked.connect(dialog.reject)
        btn_row.addWidget(cancel_btn)
        bury_btn = QPushButton("🪦 Decommission selected")
        self._style_btn(bury_btn, 'danger')
        bury_btn.clicked.connect(dialog.accept)
        btn_row.addWidget(bury_btn)
        layout.addLayout(btn_row)

        self._animate_dialog(dialog)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return []
        picked = []
        for i in range(list_widget.count()):
            item = list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                picked.append(item.text())
        return picked

    def _start_review_retry(self):
        """Launch the _review backlog retry batch (worker mode
        'review_retry' — the worker scans, the websites phase drives the
        retry pipeline). Used by the startup notice and the More menu."""
        if getattr(self, '_batch_running', False):
            self._show_custom_message_box(
                "Batch already running",
                "A batch is already running — finish or stop it first.",
                success=False)
            return
        self.save_config()
        self._start_worker('review_retry', None, None, None, None, None, None)

    def _start_worker(self, mode, range_from, range_to, offset_start, offset_count, import_file, urls,
                      bot_source=False, non_github_urls=None, intake_duplicates=0, raw_url_count=0):
        # Acquire Telegram lock for modes that access the session file.
        # 'direct' and 'import' modes don't use Telegram, so no lock needed.
        # v0.06 — owner "batch": processing_finished releases exactly this
        # owner, so a finishing direct/import batch can no longer free a
        # lock held by an unrelated Telegram operation (v0.05 over-release).
        if mode in ('telegram_ids', 'telegram_offset'):
            if not self._acquire_telegram_lock("batch"):
                return

        # v0.32 (five-change pass): the ONE hero button becomes Stop while
        # the batch runs (no separate STOP button). _batch_running is the
        # GUI mirror's signal; the rendering is owned by _set_hero_state
        # ('running' → danger fill + square glyph; processing_finished
        # restores SYNC). A cooperative between-items stop leaves no
        # partial state, so Stop needs no confirmation dialog.
        self._batch_running = True
        self._set_hero_state('running')
        self.progress_bar.setValue(0)
        # v0.34 (follow-up review): the bar is value-driven again for this
        # run — drop any resting RESULT segments left by the previous
        # batch (guarded: headless test stubs replace the bar with a
        # SimpleNamespace).
        _seg_clear = getattr(self.progress_bar, 'clearResultSegments', None)
        if callable(_seg_clear):
            _seg_clear()
        # v31.1 spec: the determinate progress bar is visible ONLY while a
        # batch job runs — show it now, label it with the current item as
        # the batch progresses (update_progress / update_status).
        self.progress_bar.setVisible(True)
        self.progress_bar.setFormat("Processing…")
        # v0.31.0 (balance pass): a fresh batch starts a FRESH log — clear
        # the entry CACHE and the view together (the old view-only clear
        # resurrected pre-batch rows on the next filter change) and flip
        # the end-of-row state indicator to Syncing.
        self._clear_log()
        self._set_pipeline_state('syncing')
        # Track processing start time for elapsed display
        self._processing_start_time = datetime.now()

        self.worker = ProcessingWorker(
            config=self.config,
            mode=mode,
            range_from=range_from,
            range_to=range_to,
            offset_start=offset_start,
            offset_count=offset_count,
            import_file=import_file,
            urls=urls
        )
        # v23 — populate link-tracker flags BEFORE the worker thread starts
        # so run() can read them during Phase 1 intake. non_github_urls is
        # only relevant for 'direct' mode (bot queue / sources / retry).
        self.worker._bot_source = bool(bot_source)
        if non_github_urls:
            self.worker._non_github_urls = list(non_github_urls)
        # v25 pre-flight: surface duplicate-removal stats in the final
        # report. Set from the bot-queue worker's dedup pass.
        self.worker._intake_duplicates = int(intake_duplicates or 0)
        self.worker._raw_url_count = int(raw_url_count or 0)
        self.worker.progress_updated.connect(self.update_progress)
        self.worker.status_updated.connect(self.update_status)
        self.worker.log_message.connect(self.log_message)
        self.worker.code_requested.connect(
            lambda pt, w=self.worker: self._on_telegram_code_requested(pt, w)
        )
        self.worker.finished_signal.connect(self.processing_finished)
        self.worker.disk_full_signal.connect(self._on_disk_full)
        # Pass the worker ref to _on_llm_failed so it can deliver the user's
        # decision back to the worker via resolve_llm_failure(). The signal
        # is emitted from the worker thread but Qt delivers it on the GUI
        # thread via a queued connection, so showing a modal dialog is safe.
        self.worker.llm_failed_signal.connect(
            lambda repo_name, w=self.worker: self._on_llm_failed(repo_name, w)
        )
        # v30 — Fix (model persistence): keep Settings + config.json in sync
        # when the worker's auto-switch or dialog-driven model change fires.
        self.worker.model_changed.connect(self._on_model_changed)
        self.worker.start()

    def stop_processing(self):
        if self.worker:
            self.worker.stop()
            self.log_message("⏹️ Stopping...", "warning")
            # v0.32: the single hero button waits DISABLED ("Stopping…")
            # until processing_finished restores SYNC — no double-click.
            # _batch_running stays True: the batch is still winding down.
            self.start_btn.setEnabled(False)
            self.start_btn.setText("Stopping…")

    def update_progress(self, current, total):
        """Update the progress bar value and show `Processing X of Y` in its
        text (v31.1 spec: 'Processing 7 of 30 — repo-name').

        The format string is intentionally short so it fits on the narrow
        progress bar; the human-readable status (current repo name) is set
        separately by `update_status` which overwrites this format with a
        longer `Processing X of Y — owner/repo` string while a repo is being
        processed.
        """
        if total > 0:
            self.progress_bar.setMaximum(total)
        # v0.34: a live batch owns the bar — no resting result segments.
        _seg_clear = getattr(self.progress_bar, 'clearResultSegments', None)
        if callable(_seg_clear):
            _seg_clear()
        self.progress_bar.setValue(current)
        # v33: standalone X / Y counter beside the bar (wireframe: "10/20").
        # v0.07: never shows "– / –" — a determinate bar always has numbers.
        if hasattr(self, 'progress_count'):
            self.progress_count.setText(f"{current} / {total}" if total > 0 else "0 / 0")
            self.progress_count.setToolTip(
                f"Processing {current} of {total}" if total > 0
                else "No batch running")
        # Show "Processing X of Y" format in the progress bar
        if total > 0:
            self.progress_bar.setFormat(f"Processing {current} of {total}")
        else:
            self.progress_bar.setFormat("Ready")

    def update_status(self, text):
        """Show the current status text in the progress bar's format string.

        Format: `Processing X of Y — owner/repo (elapsed)`
        Elapsed time is calculated from `self._processing_start_time`.
        """
        # Status shown in progress bar format (status label removed)
        if text and text != "Ready":
            # Calculate elapsed time
            elapsed_str = ""
            if hasattr(self, '_processing_start_time'):
                elapsed = datetime.now() - self._processing_start_time
                total_secs = int(elapsed.total_seconds())
                if total_secs >= 60:
                    elapsed_str = f" ({total_secs // 60}m {total_secs % 60}s)"
                else:
                    elapsed_str = f" ({total_secs}s)"

            # Extract repo name from URL if it's a GitHub URL
            if 'github.com/' in text:
                parts = text.replace('https://github.com/', '').split('/')
                if len(parts) >= 2:
                    repo_name = f"{parts[0]}/{parts[1]}"
                    current = self.progress_bar.value()
                    total = self.progress_bar.maximum()
                    self.progress_bar.setFormat(f"Processing {current} of {total} — {repo_name[:40]}{elapsed_str}")
                    return
            self.progress_bar.setFormat(f"Processing: {text[:60]}{elapsed_str}")
        else:
            self.progress_bar.setFormat("Ready")

    def _schedule_progress_hide(self, delay_ms: int = 2500):
        """v31.1 spec: the progress bar is visible ONLY while a batch job
        runs — after a finish/failure we flash the result briefly, then hide.
        Guarded so a freshly-started batch is never hidden by a stale timer."""
        QTimer.singleShot(delay_ms, self._hide_progress_bar)

    def _hide_progress_bar(self):
        # v0.32: the running signal is the _batch_running flag (the hero
        # button stays enabled while a batch runs — it is the Stop
        # control); a disabled button still means a FETCH is in flight.
        if getattr(self, '_batch_running', False):
            return  # a NEW batch is already running — keep the bar live
        if hasattr(self, 'start_btn') and not self.start_btn.isEnabled():
            return  # a fetch is in flight
        # v33: the progress row is a permanent fixture of the main view
        # (wireframe) — reset to Ready instead of hiding.
        self.progress_bar.setFormat("Ready")
        self.progress_bar.setValue(0)
        # v0.07: fall back to the manifest's REAL totals instead of "– / –".
        self._refresh_pipeline_counter()
        # v0.31.0 (balance pass): the Done/Error flash settles back to
        # Idle once the readout resets (a fresh batch keeps its Syncing).
        if getattr(self, '_pipeline_state', 'idle') != 'idle':
            self._set_pipeline_state('idle')

    def processing_finished(self, success, message):
        # v0.32 (five-change pass): the single hero button returns to SYNC
        # (direct enable for the stubbed/headless paths + the full render
        # via _set_hero_state), then the retry banner re-reads the fresh
        # manifest — unfinished links surface as the banner's live count.
        self.start_btn.setEnabled(True)
        self._batch_running = False
        self._set_hero_state('sync')
        if hasattr(self, '_refresh_retry_banner'):
            self._refresh_retry_banner()
        # v0.34 (follow-up review): remember this batch for the next
        # launch — the CTA card's quiet "Last sync" line (guarded for
        # the headless hero-flow stubs).
        if hasattr(self, '_record_last_sync'):
            self._record_last_sync()
        # v0.07: the PROCESSED counter falls back to the manifest's real
        # totals the moment a batch ends (never back to a blank "– / –").
        self._refresh_pipeline_counter()
        # v0.06 — owner-scoped release: only frees the lock when THIS batch
        # actually holds it (telegram modes). A direct/import batch finishing
        # while an unrelated Telegram operation runs must NOT steal its lock
        # — that race used to enable two telethon children on one session.
        self._release_telegram_lock("batch")

        # v29.4 — Auto-backup after batch (if enabled)
        if success and self.config.get('backup_enabled', False) and self.config.get('backup_folder', ''):
            if hasattr(self, '_backup_now'):
                try:
                    self._backup_now()
                except Exception as e:
                    self.log_message(f"⚠️ Auto-backup failed: {e}", "warning")

        # v31 — VaultSeal: seal the vault into its private GitHub mirror.
        # Deliberately runs for FAILED batches too: a mid-run failure may
        # still have written notes, and those are exactly what we want
        # backed up. An unchanged vault is a no-op ("vault unchanged since
        # the last seal"). Never blocks the GUI — runs in a QThread.
        try:
            vs_cfg = self.config.get('vaultseal') or {}
            if vs_cfg.get('enabled', True) and self.config.get('vault_path'):
                self._start_vault_seal()
        except Exception as seal_err:
            self.log_message(f"⚠️ VaultSeal could not start: {seal_err}", "warning")

        # v32 — GoodRepos: publish the PUBLIC curated directory (emoji-rich
        # README + mirrored notes) right after the private seal. Unchanged
        # content is a no-op; never blocks the GUI — its own QThread.
        try:
            gr_cfg = self.config.get('goodrepos') or {}
            if gr_cfg.get('enabled', True) and self.config.get('vault_path'):
                self._start_goodrepos_publish()
        except Exception as good_err:
            self.log_message(f"⚠️ Good Repos could not start: {good_err}", "warning")

        # v0.10.0 — Phase 1: the WEBSITES vault gets its own private mirror
        # (a second, independent VaultSeal). A silent no-op while the
        # websites pipeline is OFF (the default) or no websites vault is
        # configured — exactly the Phase 1 acceptance behavior.
        try:
            _pipes_cfg = self.config.get('pipelines') or {}
            if (_pipes_cfg.get('websites', False)
                    and (self.config.get('website_vault_path') or '').strip()):
                self._start_websites_seal()
        except Exception as ws_err:
            self.log_message(f"⚠️ Websites vault seal could not start: {ws_err}", "warning")

        # Calculate total elapsed time
        elapsed_str = ""
        if hasattr(self, '_processing_start_time'):
            elapsed = datetime.now() - self._processing_start_time
            total_secs = int(elapsed.total_seconds())
            if total_secs >= 60:
                elapsed_str = f" in {total_secs // 60}m {total_secs % 60}s"
            else:
                elapsed_str = f" in {total_secs}s"
            del self._processing_start_time

        if success:
            self.log_message(f"✅ {message}{elapsed_str}", "success")
            # Hint: new messages may have arrived during processing
            self.log_message("💡 Tip: New messages may have arrived during processing — click Check Queue again.", "info")
            self.progress_bar.setFormat(f"✅ Done{elapsed_str}")
            self._set_pipeline_state('done')
            self._schedule_progress_hide()

            # v23 — Phase 5: CLEAR — only mark bot messages as read if ALL
            # links verified (no failures, no pending/processing leftovers).
            # This supersedes the v22 vault-index check, which only verified
            # GitHub URLs and ignored non-GitHub links. The LinkTracker's
            # manifest is now the single source of truth.
            #
            # v0.24.1 — Fix (websites never sync): the gate now also fires for
            # a WEBSITES-ONLY batch (bot messages full of site links). The
            # batch's own manifest (worker.link_tracker) + its _bot_source
            # flag decide — NOT the stale GUI _bot_queue_urls list, so an
            # unrelated import batch can never consume the bot queue.
            #
            # Fallback: if there is no link_tracker (e.g. vault_path was
            # empty when the worker started), fall back to the v22 vault
            # index check so we don't regress.
            bot_urls = getattr(self, '_bot_queue_urls', [])
            worker_lt = getattr(self.worker, 'link_tracker', None) if self.worker else None
            _batch_from_bot = bool(
                getattr(self.worker, '_bot_source', False)) if self.worker else False
            all_clear = False
            # v0.24.1 — the batch's own _bot_source flag decides (every
            # worker gets it in _start_worker since v23): a bot-queue batch
            # (repos, websites, or both) verifies + consumes the queue; an
            # import/retry/sources batch never does — even when a stale
            # _bot_queue_urls list from an earlier check is still around.
            if _batch_from_bot and (bot_urls or worker_lt is not None):
                if worker_lt:
                    if worker_lt.get_all_clear():
                        all_clear = True
                        self.log_message("✅ All links verified — marking bot messages as read...", "info")
                        self._mark_bot_messages_read()
                    else:
                        # Show which links are causing the failure.
                        # v0.38.0 — the listing uses the SAME ground truth
                        # as get_all_clear (vault-present rows are done,
                        # not unfinished), so the count in this log line
                        # can never contradict the verdict above it.
                        try:
                            _has = worker_lt.vault_has
                        except Exception:
                            _has = None
                        pending_links = [
                            l for l in worker_lt.manifest["links"]
                            if l["status"] in ("failed", "processing", "pending")
                            and not (_has and _has(l["url"]))
                        ]
                        self.log_message(
                            f"⚠️ {len(pending_links)} link(s) not verified — bot messages NOT marked as read",
                            "warning"
                        )
                        for pl in pending_links[:5]:
                            self.log_message(
                                f"   • [{pl['status']}] {pl['url']}" + (f" — {pl.get('error','')}" if pl.get('error') else ""),
                                "info"
                            )
                        if len(pending_links) > 5:
                            self.log_message(f"   ... and {len(pending_links) - 5} more", "info")
                else:
                    # No link tracker — fall back to the v22 vault-index check
                    all_in_vault = True
                    worker_vi = getattr(self.worker, '_vault_index', None) if self.worker else None
                    if worker_vi:
                        for url in bot_urls:
                            try:
                                norm = normalize_url(url)
                                if not worker_vi.has_url(norm):
                                    all_in_vault = False
                                    break
                            except Exception:
                                all_in_vault = False
                                break
                    if all_in_vault:
                        all_clear = True
                        self.log_message("✅ All repos processed — marking bot messages as read...", "info")
                        self._mark_bot_messages_read()
                    else:
                        self.log_message("⚠️ Some repos failed — bot messages NOT marked as read (retry next time)", "warning")

            # v25 pre-flight: advance last_processed_msg_id ONLY if the
            # batch came from "📬 Process New" AND Phase 5 CLEAR passed.
            # If verification failed, we keep the old ID so the next
            # "Process New" run re-fetches the failed messages and retries
            # them — no link is ever lost.
            pending_update = getattr(self, '_pending_last_processed_update', 0)
            if pending_update and all_clear:
                old_id = int(self.config.get('last_processed_msg_id', 0) or 0)
                if pending_update > old_id:
                    self.config['last_processed_msg_id'] = int(pending_update)
                    try:
                        self.save_config()
                        self.log_message(
                            f"📌 last_processed_msg_id advanced: {old_id} → {pending_update} "
                            f"(next 'Process New' will skip up to ID {pending_update})",
                            "success"
                        )
                    except Exception as save_err:
                        self.log_message(
                            f"⚠️ Failed to persist last_processed_msg_id ({save_err}) — "
                            f"this run is verified but the next 'Process New' will re-fetch these messages.",
                            "warning"
                        )
            elif pending_update and not all_clear:
                self.log_message(
                    f"⏸️ last_processed_msg_id NOT advanced (some links failed verification) — "
                    f"next 'Process New' will re-fetch messages newer than ID "
                    f"{int(self.config.get('last_processed_msg_id', 0) or 0)} and retry failed links.",
                    "warning"
                )
            # Clear the pending flag regardless — it only applies to this batch.
            self._pending_last_processed_update = 0

            # v0.39.0 — the batch-finish fanfare (the owner's ask): a
            # success chime + a scorecard modal, ONLY when the batch ran
            # to natural completion. batch_summary is set by the worker
            # exactly at its end-of-batch stage and carries 'stopped', so
            # user-stopped batches and the no-work early returns ("No
            # URLs found", "both pipelines off") keep the plain box —
            # no fanfare for an empty batch or a deliberate stop.
            _summary = getattr(self.worker, 'batch_summary', None) \
                if self.worker else None
            if _summary and not _summary.get('stopped'):
                self._celebrate_batch(_summary, elapsed_str)
            else:
                self._show_custom_message_box(
                    "Processing Complete", f"{message}{elapsed_str}",
                    success=True)
        else:
            self.log_message(f"❌ {message}", "error")
            self.progress_bar.setFormat("❌ Failed")
            self._set_pipeline_state('error')
            self._schedule_progress_hide()
            self._show_custom_message_box("Processing Error", message, success=False)

    # ------------------------------------------------------------------
    # v0.39.0 — the batch-finish fanfare: success chime + scorecard modal
    # ------------------------------------------------------------------

    def _celebrate_batch(self, summary, elapsed_str=""):
        """v0.39.0 — a completed batch gets both halves of the fanfare:
        the chime FIRST (it plays while the modal's event loop runs, so
        the audio starts before the user even reaches for the mouse),
        then the Batch Complete scorecard modal.

        v0.40.0 — failures no longer celebrate: a batch that finished
        WITH failed links gets the softer descending needs-retry tone
        (retry.wav) instead of the success arpeggio — the chime says
        what the scorecard's amber row says. The modal opens either
        way (the routing keys on natural completion, not cleanliness)."""
        _failed = 0
        if summary:
            try:
                _failed = int(summary.get('failed_links', 0) or 0)
            except (TypeError, ValueError):
                _failed = 0
        self._play_batch_sound(needs_retry=_failed > 0)
        self._show_batch_success_modal(summary, elapsed_str)

    def _play_batch_sound(self, needs_retry: bool = False):
        """v0.39.0 — play assets/sounds/success.wav. Config keys
        (Settings → Sound since v0.40.0): sound_enabled, sound_volume
        (0.0..1.0, clamped into Qt's [0, 1]). The BatchSound wrapper is
        failure-proof: a missing module/WAV or an audio-less machine is
        a silent no-op with at most ONE warning line in the log.

        v0.40.0 — ``needs_retry=True`` (a batch that finished with
        failed links) plays the softer descending retry.wav instead —
        same gate, same volume, same failure contract."""
        from gitcurator.gui.sound import BatchSound
        if not self.config.get('sound_enabled', True):
            return False
        if getattr(self, '_batch_sound', None) is None:
            self._batch_sound = BatchSound()
        try:
            _vol = float(self.config.get('sound_volume', 0.8))
        except (TypeError, ValueError):
            _vol = 0.8
        if needs_retry:
            return self._batch_sound.play_retry(volume=_vol,
                                                log=self.log_message)
        return self._batch_sound.play_success(volume=_vol,
                                              log=self.log_message)

    def _preview_batch_sound(self, needs_retry: bool = False):
        """v0.40.0 — Settings → Sound's Preview buttons: hear EXACTLY
        what a batch would play, through the exact batch gate (the
        switch, the volume, BatchSound's one-notice failure handling).
        A preview is honest by construction — it shares every line of
        the batch path except the modal."""
        played = self._play_batch_sound(needs_retry=needs_retry)
        if not played and not self.config.get('sound_enabled', True):
            self.log_message(
                "🔇 Preview silent — the chime is switched off "
                "(the switch above).", "info")
        elif not played and getattr(self, '_batch_sound', None) is not None \
                and self._batch_sound.unavailable_reason:
            # BatchSound already said it once (the 🔕 line) — do not
            # repeat it here; this branch exists so the log doesn't
            # blame the switch when the machine is audio-less.
            pass
        return played

    def _show_batch_success_modal(self, summary, elapsed_str=""):
        """v0.39.0 — the Batch Complete scorecard: what the batch did,
        at a glance (repos curated, websites, retries, elapsed, report),
        replacing the old single-line "Processing Complete" box for
        naturally-finished batches. Building lives in
        _build_batch_success_dialog (the split keeps the dialog
        inspectable by tests without a nested event loop)."""
        if getattr(self, '_closing', False) or not self.isVisible():
            # Same shutdown guard as _show_custom_message_box: a modal
            # no one can dismiss must never open (v0.06 zombie fix).
            try:
                self.log_message(
                    "✅ Batch finished — window not visible, modal skipped.",
                    "info")
            except Exception:
                pass
            return
        dialog = self._build_batch_success_dialog(summary, elapsed_str)
        if dialog is None:
            return
        self._animate_dialog(dialog)
        dialog.exec()

    def _build_batch_success_dialog(self, summary, elapsed_str=""):
        """v0.39.0 — BUILD (never exec) the Batch Complete scorecard.

        Roles only (msg_glyph / msg_heading / sync_card / cc_item /
        cc_row_name / info_note) — the app QSS paints it in both themes;
        the two cc_row_name tone variants live in gui.theme (no new
        color values — they reuse the message-box tone tokens)."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Batch Complete")
        dialog.setModal(True)
        dialog.setMinimumWidth(480)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        # Header: 🎉 + "Batch Complete!" (msg roles, success tone).
        # v0.40.0 — a finish WITH failures is honest: a neutral 🏁 flag
        # (no party), the heading rides the warning tone — the amber
        # "Needs retry" row and the softer retry.wav chime complete the
        # "done, but look at me" triad.
        _failed = 0
        try:
            _failed = int(summary.get('failed_links', 0) or 0)
        except (TypeError, ValueError):
            _failed = 0
        header = QHBoxLayout()
        glyph = QLabel("🏁" if _failed else "🎉")
        glyph.setObjectName("msg_glyph")
        header.addWidget(glyph)
        title = QLabel(" Batch Complete!" if not _failed
                       else " Batch Complete")
        title.setObjectName("msg_heading")
        title.setProperty("tone", "warning" if _failed else "success")
        header.addWidget(title)
        header.addStretch()
        layout.addLayout(header)

        # Stats card — one muted name (cc_item) + bold value (cc_row_name)
        # per row, exactly the v0.37 Test-Connection hierarchy vocabulary.
        card = QWidget()
        card.setObjectName("sync_card")
        form = QFormLayout(card)
        form.setContentsMargins(14, 10, 14, 10)
        form.setSpacing(7)
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        def _row(name, value, tone=None):
            n = QLabel(name)
            n.setObjectName("cc_item")
            v = QLabel(str(value))
            v.setObjectName("cc_row_name")
            if tone:
                v.setProperty("tone", tone)
            form.addRow(n, v)
            return v

        _gh_total = int(summary.get('github_total', 0) or 0)
        if _gh_total:
            _row("Repos curated",
                 f"{summary.get('github_processed', 0)} of {_gh_total}")
        _ws = summary.get('websites')
        if _ws:
            _ws_bits = [f"{_ws.get('processed', 0)} saved",
                        f"{_ws.get('review', 0)} to review",
                        f"{_ws.get('skipped', 0)} skipped"]
            if _ws.get('failed', 0):
                _ws_bits.append(f"{_ws['failed']} failed")
            _row("Websites", " · ".join(_ws_bits))
        _failed = int(summary.get('failed_links', 0) or 0)
        if _failed:
            _row("Needs retry", f"{_failed} link(s) — next run",
                 tone="warning")
        else:
            _row("Status", "✓ All links processed cleanly", tone="success")
        _elapsed = str(elapsed_str or "").replace(" in ", "").strip()
        if _elapsed:
            _row("Elapsed", _elapsed)
        _report = str(summary.get('report_path', "") or "")
        if _report:
            _rep = _row("Final report", os.path.basename(_report))
            _rep.setToolTip(_report)  # full path on hover — paths stay
            # out of the visible line (the v0.37 one-line rule)

        layout.addWidget(card)

        # Footnote: the undo affordance whenever the batch wrote notes
        _notes = int(summary.get('new_notes', 0) or 0)
        if _notes:
            note = QLabel(
                f"💡 {_notes} new note(s) added to the vault — undo this "
                "batch anytime from Dashboard → Undo Last Batch.")
            note.setWordWrap(True)
            note.setObjectName("info_note")
            layout.addWidget(note)

        # OK button (primary, right-aligned — the house button row)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("OK")
        self._style_btn(ok_btn, 'primary')
        ok_btn.clicked.connect(dialog.accept)
        ok_btn.setDefault(True)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

        return dialog

    def _on_disk_full(self, path):
        """Handle disk full — show dialog, wait for user to free space, then resume."""
        msg = f"Disk is full!\n\nCannot write:\n{path}\n\nFree up disk space, then click OK to resume processing."
        self.log_message("💾 DISK FULL — processing paused. Free space and click OK.", "error")
        # Use theme-aware question dialog
        if self._show_custom_question("💾 Disk Full", msg):
            self.log_message("▶️ Resuming processing after disk full...", "info")
            if self.worker:
                self.worker._disk_full_paused = False
        else:
            self.log_message("⏹️ Aborting due to disk full.", "error")
            if self.worker:
                self.worker.stop()
                self.worker._disk_full_paused = False

    def _on_llm_failed(self, repo_name, worker):
        """LLM failed for a repo — show a custom blocking dialog with buttons
        + a model dropdown. The worker thread is BLOCKED on
        `_llm_retry_event` until we call `worker.resolve_llm_failure(response)`.

        Response values sent back to the worker:
          - 'skip'   — skip this repo, continue batch
          - 'retry'  — retry with the same model
          - '<name>' — retry with this model name (from the dropdown)
          - 'stop'   — stop the batch

        v30 — Fix (model persistence): when a DIFFERENT model is chosen (and
        "Remember" is checked, default ON), the worker persists it via
        _apply_model_choice(): it applies to the rest of the batch, the
        Settings combo, and config.json. This dialog used to reappear for
        EVERY link because the choice was only used for a single retry.
        """
        # v0.15.0 — llama.cpp engine detection: the failed-model name and
        # the pick-a-model list are provider-aware (llamacpp reads the
        # llamacpp_* keys and lists the llama-server's own /v1/models).
        _provider = self.config.get('llm_provider', 'ollama')
        if _provider == 'ollama':
            failed_model = (self.config.get('ollama', {}) or {}).get('model', '')
        elif _provider == 'llamacpp':
            failed_model = str(self.config.get('llamacpp_model', '') or '')
        else:
            failed_model = self.config.get('cloud_model', '')
        self.log_message(
            f"❌ LLM failed for '{repo_name}' after 3 attempts (model '{failed_model}').",
            "error"
        )

        dialog = QDialog(self)
        dialog.setWindowTitle("LLM Analysis Failed")
        dialog.setModal(True)
        dialog.setMinimumWidth(460)

        # v0.30.0 (audit): DE-STYLED — the themed app QSS paints this dialog
        # (QDialog surface + QWidget text + combo rules); the glyph/heading
        # wear message-box roles, the buttons ride the design-system
        # variants. No per-dialog stylesheet, no hand-picked colors.

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        # Header
        header = QHBoxLayout()
        icon_label = QLabel("🤖")
        icon_label.setObjectName("msg_glyph")
        header.addWidget(icon_label)
        title_label = QLabel(f" LLM Failed: {repo_name}")
        title_label.setObjectName("msg_heading")
        title_label.setProperty("tone", "error")
        header.addWidget(title_label)
        header.addStretch()
        layout.addLayout(header)

        # Description
        desc = QLabel(
            f"The LLM failed to analyze this repo after 3 attempts.\n"
            f"Failed model: <b>{failed_model or 'unknown'}</b>\n"
            "Choose how to proceed:"
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # Fetch available models (best-effort, may be empty/slow).
        # v0.15.0 — provider-aware: Ollama lists its own models; llama.cpp
        # lists the llama-server's /v1/models; the cloud endpoint keeps the
        # legacy behavior (Ollama list — switching models there is rare).
        server_down = False
        if _provider == 'llamacpp':
            _probe = {}
            try:
                _probe = _llm_client.probe_llamacpp(
                    self.llamacpp_api_url.text().strip()
                    if hasattr(self, 'llamacpp_api_url')
                    else self.config.get('llamacpp_api_url', ''),
                    self.llamacpp_api_key.text().strip()
                    if hasattr(self, 'llamacpp_api_key')
                    else self.config.get('llamacpp_api_key', ''))
                model_names = list(_probe.get('models') or [])
                server_down = not _probe.get('found')
            except Exception:
                model_names = []
                server_down = True
            if _probe.get('props_model') and _probe['props_model'] not in model_names:
                model_names.append(_probe['props_model'])
        else:
            try:
                model_names, _err = self._get_ollama_model_names(self.ollama_url.text())
                server_down = _err is not None
            except Exception:
                model_names = []
                server_down = True
        if not model_names:
            model_names = []

        # v0.05 — Fix (owner report: model-switching cannot fix a dead
        # server): when the server is unreachable, the model dropdown is
        # useless — say WHY the analysis failed and what actually helps,
        # right inside the dialog.
        if server_down:
            server_hint = QLabel(
                "⚠️ The LLM server is not reachable right now — a different "
                "model will NOT fix this.\n"
                "Start it first: Settings → LLM → 🚀 Start Server (or run "
                "'ollama serve' in a terminal), then choose Retry."
            )
            server_hint.setWordWrap(True)
            server_hint.setObjectName("warn_box")
            layout.addWidget(server_hint)

        # Model dropdown
        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Retry with:"))
        model_combo = QComboBox()
        model_combo.addItem("— Same model —", "retry")
        for name in model_names:
            model_combo.addItem(name, name)
        model_row.addWidget(model_combo, 1)
        layout.addLayout(model_row)

        # v30 — Fix (model persistence): default the dropdown to a DIFFERENT
        # model when one exists — the same model just failed 3 times.
        for idx in range(model_combo.count()):
            data = model_combo.itemData(idx)
            if data and data != "retry" and data != failed_model:
                model_combo.setCurrentIndex(idx)
                break

        # v30 — "Remember" checkbox: save the chosen model as the default
        # (Settings + config.json + rest of batch). ON by default — that is
        # what users expect: pick once, keep going.
        remember_check = QCheckBox("Remember this model (apply to the rest of the batch and save to Settings)")
        remember_check.setChecked(True)
        layout.addWidget(remember_check)

        # Buttons (v0.30.0: design-system variants — retry is the one
        # primary, stop is the destructive danger, the rest secondary)
        btn_row = QHBoxLayout()
        skip_btn = QPushButton("Skip")
        self._style_btn(skip_btn, 'secondary')
        retry_same_btn = QPushButton("Retry Same")
        self._style_btn(retry_same_btn, 'primary')
        retry_with_btn = QPushButton("Retry With ▾")
        self._style_btn(retry_with_btn, 'secondary')
        stop_btn = QPushButton("Stop Batch")
        self._style_btn(stop_btn, 'danger')
        btn_row.addWidget(skip_btn)
        btn_row.addWidget(retry_same_btn)
        btn_row.addWidget(retry_with_btn)
        btn_row.addWidget(stop_btn)
        layout.addLayout(btn_row)

        # Capture the decision in a closure so we can deliver it to the worker
        response_holder = {"value": None, "remember": True}

        def _respond(value: str):
            response_holder["value"] = value
            response_holder["remember"] = remember_check.isChecked()
            dialog.accept()

        skip_btn.clicked.connect(lambda: _respond("skip"))
        retry_same_btn.clicked.connect(lambda: _respond("retry"))
        retry_with_btn.clicked.connect(lambda: _respond(model_combo.currentData() or "retry"))
        stop_btn.clicked.connect(lambda: _respond("stop"))

        self._animate_dialog(dialog)
        dialog.exec()

        # If user closed the dialog with the X button (no decision), default to skip
        decision = response_holder["value"] or "skip"
        remember = response_holder["remember"]
        self.log_message(f"👉 User chose: '{decision}'" + (" (remember)" if remember else ""), "info")

        # v30 — Fix (model persistence): persist the model choice immediately
        # (config + Settings combo + config.json). The worker ALSO applies it
        # to self.config, but doing it here guarantees the GUI + disk state
        # even if the worker finishes first.
        if remember and decision and decision not in ("skip", "retry", "stop"):
            self._on_model_changed(
                self.config.get('llm_provider', 'ollama'), decision
            )
        # Unblock the worker thread
        worker.resolve_llm_failure(decision)

    def _on_model_changed(self, provider: str, model_name: str):
        """v30 — Fix (model persistence): a new LLM model was selected
        (from the failure dialog, the auto-switch, or the worker). Sync the
        Settings combo + self.config (in place) + config.json so the choice
        survives the batch AND the next app launch."""
        if not model_name:
            return
        try:
            if provider == 'cloud':
                self.config['cloud_model'] = model_name
                if hasattr(self, 'cloud_model'):
                    self.cloud_model.setText(model_name)
            elif provider == 'llamacpp':
                # v0.15.0 — llama.cpp engine detection: keep the llamacpp
                # key + the Settings combo in sync with the auto-detected /
                # re-picked model.
                self.config['llamacpp_model'] = model_name
                if hasattr(self, 'llamacpp_model'):
                    combo = self.llamacpp_model
                    idx = combo.findText(model_name)
                    if idx < 0:
                        combo.insertItem(0, model_name)
                        idx = 0
                    combo.setCurrentIndex(idx)
            else:
                ollama_cfg = self.config.get('ollama')
                if not isinstance(ollama_cfg, dict):
                    ollama_cfg = {}
                    self.config['ollama'] = ollama_cfg
                ollama_cfg['model'] = model_name
                # Sync the Settings dropdown so the user SEES the change.
                if hasattr(self, 'ollama_model'):
                    combo = self.ollama_model
                    idx = combo.findText(model_name)
                    if idx < 0:
                        combo.insertItem(0, model_name)
                        idx = 0
                    combo.setCurrentIndex(idx)
            # Persist to disk (save_config now MERGES — this can no longer
            # wipe cloudflare_*/gdrive_* keys).
            self.save_config()
            self.log_message(
                f"✅ LLM model set to '{model_name}' for the rest of the batch "
                f"and saved to Settings.",
                "success"
            )
        except Exception as e:
            self.log_message(f"⚠️ Could not save model choice: {e}", "warning")

