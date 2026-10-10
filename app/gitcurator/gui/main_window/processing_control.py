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
from gitcurator.core import hand_delivery as _hand_delivery
from gitcurator.core import chrome_tabs as _chrome_tabs
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

    # -- v0.51.0: the caught-up check's own retry pass ---------------------

    def _scan_master_waiting(self):
        """v0.51.0 — the master table's " - " rows that are the machine's
        to retry (the owner's law: before declaring everything up to
        date the app must check the decommissioned note). Opens the
        state ledger with the same dry-run-shadow law the backlog scan
        uses, probes retired/stored links out of the set, and stashes
        the eyes-count for the caught-up message. Best-effort and
        guarded throughout (a broken probe never blocks the caught-up
        path — it falls back to the pure file read, then to [])."""
        try:
            self._master_waiting_eyes = 0
            cfg = self.config or {}
            vault = (cfg.get('website_vault_path') or '').strip()
            if not vault or not os.path.isdir(vault):
                return []
            if not (cfg.get('pipelines') or {}).get('websites', False):
                return []
            if getattr(self, '_batch_running', False):
                return []      # a running batch owns the table's truth
            try:
                if _dryrun.is_enabled():
                    state = _website_pipeline.WebsiteStateDB(
                        db_path=_dryrun.shadow_cache_path(
                            os.path.join(APP_DIR, 'cache.db')))
                else:
                    state = _website_pipeline.WebsiteStateDB()
                try:
                    scanned = _website_pipeline.scan_master_waiting_rows(
                        vault, state=state)
                finally:
                    state.close()
            except Exception:
                scanned = _website_pipeline.scan_master_waiting_rows(vault)
            waiting = [i for i in scanned if i.get('kind') == 'fetch']
            self._master_waiting_eyes = len(
                [i for i in scanned if i.get('kind') == 'eyes'])
            return waiting
        except Exception:
            return []

    def _start_master_retry(self):
        """v0.51.0 — launch the caught-up check's retry batch (worker mode
        'master_retry'): the " - " rows of _review/DECOMMISSIONED.md are
        fetched again through the FULL pipeline, burned-out retry
        counters reborn one row at a time, the honest verdict said at
        the end. Called by the caught-up sync (automatic — the owner's
        "try to fetch again") and More ▸ 🔁 Retry the table's ' - ' rows."""
        if getattr(self, '_batch_running', False):
            return      # silent — the caught-up flow only calls when free
        self.save_config()
        self._start_worker('master_retry', None, None, None, None, None, None)

    def retry_master_waiting_now(self):
        """More ▸ 🔁 Retry the table's ' - ' rows — the manual trigger
        (v0.51.0). Same pass the caught-up sync runs automatically, with
        the gates surfaced as dialogs instead of silence (the user ASKED
        for this one). Rows with a verdict (🪦 ❌ ☠️ 💀 / ✅ / ♻️ / 🖐) are
        never touched — their Status cell already decided."""
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
        if getattr(self, '_batch_running', False):
            self._show_custom_message_box(
                "Batch already running",
                "A batch is already running — finish or stop it first.",
                success=False)
            return
        waiting = self._scan_master_waiting()
        if not waiting:
            eyes = getattr(self, '_master_waiting_eyes', 0) or 0
            self._show_custom_message_box(
                "No ' - ' rows waiting",
                "The master table (_review/DECOMMISSIONED.md) has no row "
                "waiting for a retry — every link is stored, retired or "
                "verdicted.\n\n"
                + (f"{eyes} row(s) wait for your eyes: set ✅ reviewed or "
                   f"🪦 dead in the table.\n\n" if eyes else "")
                + "\"Everything is up to date\" is true.",
                success=True)
            return
        if not self._confirm_batch(len(waiting), "the master table's ' - ' rows"):
            self.log_message(
                "⏹️ Master-table waiting retry cancelled — the ' - ' rows "
                "keep waiting (their automatic retries continue).",
                "warning")
            return
        self._start_master_retry()

    # -- v0.52.0: the hand rows — the fifth door's own to-do list ----------

    def _scan_master_hand(self):
        """v0.52.0 — the master table's 🖐 hand rows that still owe a
        Chrome scrape (the owner's report: "I set hand emoji, but those
        links didn't refetched using scrapping manually on chrome").
        A pure file read (the table's gestures + the fourth door's
        queue), guarded exactly like ``_scan_master_waiting``: no
        vault, no pipeline, or a running batch means no scan. Returns
        ``[{'url', 'error'}]`` — the shape the Chrome delivery worker
        takes; never raises."""
        try:
            cfg = self.config or {}
            vault = (cfg.get('website_vault_path') or '').strip()
            if not vault or not os.path.isdir(vault):
                return []
            if not (cfg.get('pipelines') or {}).get('websites', False):
                return []
            if getattr(self, '_batch_running', False):
                return []      # a running batch owns the table's truth
            return _website_pipeline.scan_master_hand_rows(vault) or []
        except Exception:
            return []

    def _maybe_deliver_hand_queue(self):
        """v0.52.0 — the end-of-run hand pass: every 🖐 hand row whose
        page has not landed yet is scraped by the app ITSELF, in the
        owner's real Chrome, automatically — no dialog (the gesture
        WAS the owner's answer), no manual saving. The delivered pages
        are re-processed as real fetches by the delivery worker's own
        finish path. Each hand link is tried ONCE per app session
        (``_hand_delivery_tried`` — a link whose Chrome scrape failed
        keeps waiting for its doors instead of reopening Chrome every
        batch); opted-out via the ``web_browser_retry`` knob. Quiet
        no-op whenever the pass cannot run."""
        cfg = self.config or {}
        if cfg.get('web_browser_retry') is False:
            return  # the fifth door's knob (default ON) opts out
        if not (cfg.get('pipelines') or {}).get('websites', False):
            return
        vault = (cfg.get('website_vault_path') or '').strip()
        if not vault or not os.path.isdir(vault):
            return
        if getattr(self, '_closing', False) or not self.isVisible():
            return
        links = self._scan_master_hand()
        if not links:
            return
        tried = getattr(self, '_hand_delivery_tried', None)
        if tried is None:
            tried = self._hand_delivery_tried = set()
        links = [l for l in links if l.get('url') not in tried]
        if not links:
            return
        tried.update(l.get('url') for l in links)
        for l in links:
            l.setdefault('error', l.get('wall')
                         or 'the 🖐 hand gesture in the master table')
        self.log_message(
            f"🖐 {len(links)} hand row(s) — your real Chrome opens for "
            f"them now (one tab per link; GitCurator takes each live "
            f"page and processes it as a real fetch — no manual "
            f"saving)", "info")
        try:
            self._start_chrome_tab_retry(links)
        except Exception as e:
            self.log_message(
                f"⚠️ The hand-row Chrome pass could not start ({e}) — "
                f"More ▸ 🤖 Chrome tab-retry, or save the pages into "
                f"the hand-delivered folder yourself", "warning")

    # -- v0.57.0: THE NOTE IS THE SUCCESS — the redo pass ------------------

    def _scan_master_redo(self):
        """v0.57.0 — the 🖐 hand rows whose delivery was a FALSE SUCCESS:
        the page was delivered (a consumed queue row or the page file
        in the hand-delivered folder) but the note is NOT properly
        stored (:func:`website_pipeline.note_is_properly_stored` fails
        — a half-fetched ``_review`` item, a missing file, a stranger
        note). The owner's words: "it's fetched and became ✅ in the
        table, but actually it's note is not properly saved and only
        saved under _review folder, so it's false success and must be
        redo." Returns ``[{'url', 'reason'}]``; best-effort and
        guarded exactly like ``_scan_master_waiting`` (no vault, no
        pipeline, a running batch or a broken state ledger means no
        scan — the hermetic law)."""
        try:
            cfg = self.config or {}
            vault = (cfg.get('website_vault_path') or '').strip()
            if not vault or not os.path.isdir(vault):
                return []
            if not (cfg.get('pipelines') or {}).get('websites', False):
                return []
            if getattr(self, '_batch_running', False):
                return []      # a running batch owns the table's truth
            if _dryrun.is_enabled():
                state = _website_pipeline.WebsiteStateDB(
                    db_path=_dryrun.shadow_cache_path(
                        os.path.join(APP_DIR, 'cache.db')))
            else:
                state = _website_pipeline.WebsiteStateDB()
            try:
                return _website_pipeline.scan_master_redo_rows(
                    vault, state=state) or []
            finally:
                state.close()
        except Exception:
            return []

    def _maybe_redo_hand_notes(self):
        """v0.57.0 — the owner's law, automatic: "the app must refetch
        and generate notes, if they notes aren't properly stored." A
        hand row whose delivery landed only a half-fetched note is
        REDONE right here — no new Chrome tab (the delivered page is
        the record; the pipeline re-reads it in place), the LLM asked
        again, the proper, categorized note written. Bounded honestly:
        each link is redone ONCE per app session (``_hand_redo_tried``
        — the LLM's low-confidence answer will not change on an
        immediate second ask; the next session asks again with fresh
        context); skipped while a Chrome delivery is gathering pages
        (those links get their own re-run through the Websites
        pipeline). Quiet no-op whenever the pass cannot run."""
        cfg = self.config or {}
        if not (cfg.get('pipelines') or {}).get('websites', False):
            return
        vault = (cfg.get('website_vault_path') or '').strip()
        if not vault or not os.path.isdir(vault):
            return
        if getattr(self, '_closing', False) or not self.isVisible():
            return
        if int(getattr(self, '_chrome_delivery_pending', 0) or 0) > 0 \
                or getattr(self, '_chrome_delivery_busy', False):
            return  # the delivery's own re-run owns those links now
        if getattr(self, '_batch_running', False):
            return  # the running batch's end-of-run pass will fire it
        rows = self._scan_master_redo()
        if not rows:
            return
        tried = getattr(self, '_hand_redo_tried', None)
        if tried is None:
            tried = self._hand_redo_tried = set()
        rows = [r for r in rows if r.get('url') not in tried]
        if not rows:
            return
        tried.update(r.get('url') for r in rows)
        urls = [r['url'] for r in rows]
        first_reason = str(rows[0].get('reason') or '').strip()
        self.log_message(
            f"🔁 {len(urls)} hand note(s) NOT properly stored — "
            f"refetching and re-generating them now (the delivered "
            f"page is re-read, the LLM writes the proper, categorized "
            f"note; a ✅ is only earned by the note itself"
            + (f"; first reason: {first_reason}" if first_reason else "")
            + ")", "info")
        try:
            self._delivery_rerun_pending = True
            self._start_worker_with_urls([], non_github_urls=urls)
        except Exception as e:
            self._delivery_rerun_pending = False
            self.log_message(
                f"⚠️ The hand-note redo could not start ({e}) — the next "
                f"SYNC's caught-up check picks them up automatically",
                "warning")

    def hand_rows_deliver_now(self):
        """More ▸ 🖐 Scrape hand rows via Chrome — v0.52.0, the manual
        trigger: the 🖐 hand rows of the master table (the # cell or
        the Status cell — wherever the owner set the emoji) are
        scraped in the owner's real Chrome NOW, the delivered pages
        processed as real fetches. The same delivery the end-of-run
        pass runs automatically; the dialogs surface the gates."""
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
        if getattr(self, '_chrome_delivery_busy', False):
            self._show_custom_message_box(
                "A Chrome delivery is running",
                "A delivery is already open in your Chrome — the hand "
                "rows queued behind it open when the current tabs "
                "finish.", success=True)
            return
        links = self._scan_master_hand()
        if not links:
            self._show_custom_message_box(
                "No hand rows waiting",
                "The master table (_review/DECOMMISSIONED.md) has no 🖐 "
                "hand row waiting for a Chrome scrape (a delivered row "
                "is consumed — set 🖐 anywhere in a row: the # cell or "
                "the Status cell).", success=True)
            return
        if not self._confirm_batch(len(links), "the table's 🖐 hand rows"):
            self.log_message(
                "⏹️ Hand-row Chrome scrape cancelled — the rows keep "
                "waiting (the end-of-run pass offers them again).",
                "warning")
            return
        for l in links:
            l.setdefault('error', l.get('wall')
                         or 'the 🖐 hand gesture in the master table')
        self.log_message(
            f"🖐 {len(links)} hand row(s) — opening your real Chrome "
            f"(one tab per link; the live pages become real fetches)",
            "info")
        self._start_chrome_tab_retry(links)

    # -- v0.55.0: the owner's own Chrome — the attach setup ----------------

    def attach_my_chrome_now(self):
        """More ▸ 🪄 Attach to my Chrome — v0.55.0, the owner's ask
        ("the system must be able to open tabs in my real chrome
        instance instead"): restart the owner's REAL Chrome with its
        DevTools port open so the fifth door drives HIS instance —
        his profile, his cookies, his logins, his clearances, his
        network path; the tabs land in HIS window and GitCurator
        never closes his browser (only its own tabs). Writes
        ``chrome-attach.bat`` (kill Chrome → relaunch with
        ``--remote-debugging-port=9222`` through the attach link;
        ``--restore-last-session`` brings his tabs back) and runs it
        on his explicit OK. Afterwards every delivery (and More ▸ 🖐
        Scrape hand rows) attaches automatically."""
        try:
            if getattr(self, '_closing', False):
                return
            from gitcurator.core import hand_delivery as _hd
            from gitcurator.core import chrome_tabs as _chrome_tabs
            from gitcurator.constants import APP_DIR
            chrome = _hd.find_chrome()
            if not chrome:
                self._show_custom_message_box(
                    "No Chrome found",
                    "GitCurator could not find Google Chrome on this "
                    "machine — the fifth door needs the real browser.",
                    success=False)
                return
            data_dir = _chrome_tabs.real_user_data_dir()
            if not data_dir:
                self._show_custom_message_box(
                    "No Chrome profile found",
                    "GitCurator could not find your Chrome profile "
                    "(the usual location is empty) — nothing to "
                    "attach to yet.", success=False)
                return
            cfg = self.config or {}
            try:
                port = int(cfg.get('web_browser_attach_port', 9222)
                           or 9222)
            except (TypeError, ValueError):
                port = 9222
            bat_path = os.path.join(APP_DIR, 'chrome-attach.bat')
            written = _chrome_tabs.write_chrome_attach_bat(
                bat_path, chrome_exe=chrome, port=port,
                data_dir=data_dir, link_dir=APP_DIR)
            if not written:
                self._show_custom_message_box(
                    "The attach script could not be written",
                    f"GitCurator could not write {bat_path} — check "
                    f"the folder's permissions and try again.",
                    success=False)
                return
            if os.name != 'nt':
                self.log_message(
                    f"🪄 The attach script is ready: {bat_path} — a "
                    f"Windows .bat (on this machine, start your Chrome "
                    f"yourself with --remote-debugging-port={port} and "
                    f"the door attaches automatically)", "info")
                self._show_custom_message_box(
                    "The attach script is ready",
                    f"{bat_path}\n\nA Windows .bat (this machine is not "
                    f"Windows) — start your Chrome with "
                    f"--remote-debugging-port={port} and every "
                    f"delivery attaches to it automatically.",
                    success=True)
                return
            answer = self._confirm_attach_restart(bat_path)
            if not answer:
                self.log_message(
                    "⏹️ Chrome attach setup cancelled — the door keeps "
                    "its own identity (your Chrome is never touched "
                    "without your OK)", "warning")
                return
            self.log_message(
                f"🪄 Restarting your Chrome with the door attached "
                f"(DevTools on 127.0.0.1:{port}) — your tabs come back; "
                f"the next delivery opens ITS tabs in YOUR window",
                "info")
            try:
                subprocess.Popen(
                    ['cmd', '/c', bat_path],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL, close_fds=True,
                    cwd=os.path.dirname(bat_path) or None)
            except Exception as e:
                self.log_message(
                    f"⚠️ The attach script could not be run ({e}) — "
                    f"run it by hand: {bat_path}", "warning")
                self._show_custom_message_box(
                    "Run it by hand",
                    f"The script is ready but could not be launched "
                    f"({e}).\n\nRun it by hand:\n{bat_path}",
                    success=False)
                return
            self._show_custom_message_box(
                "Your Chrome is being restarted with the door attached",
                "Chrome is closing and reopening with its DevTools "
                "port open (--restore-last-session brings your tabs "
                "back).\n\nFrom now on every Chrome delivery (and More "
                "▸ 🖐 Scrape hand rows via Chrome) opens its tabs in "
                "YOUR window — your profile, your cookies, your "
                "logins — and GitCurator never closes your browser "
                "(only its own tabs).\n\nTo undo: just relaunch Chrome "
                "normally.",
                success=True)
        except Exception as e:
            try:
                self.log_message(
                    f"⚠️ The Chrome attach setup failed ({e})", "warning")
            except Exception:
                pass

    def _confirm_attach_restart(self, bat_path: str) -> bool:
        """The one explicit OK the restart needs: the owner's Chrome is
        about to be closed and reopened (his tabs come back). Never
        raises; a dialog that cannot be shown answers False (his
        browser is never touched on a doubt)."""
        try:
            from PyQt6.QtWidgets import QMessageBox
            box = QMessageBox(self)
            box.setWindowTitle("Attach the fifth door to MY Chrome")
            box.setIcon(QMessageBox.Icon.Question)
            box.setText(
                "GitCurator will now close and restart your Chrome with "
                "its DevTools port open — your profile, your cookies, "
                "your logins; your tabs come back (--restore-last-"
                "session).\n\nThe fifth door then opens its tabs in YOUR "
                "window (it never closes your browser — only its own "
                "tabs).\n\nTo undo: relaunch Chrome normally.")
            box.setStandardButtons(QMessageBox.StandardButton.Yes
                                   | QMessageBox.StandardButton.No)
            box.setDefaultButton(QMessageBox.StandardButton.No)
            return box.exec() == QMessageBox.StandardButton.Yes
        except Exception:
            return False

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

    def hand_deliver_walled_links_now(self):
        """More ▸ 🖐 Hand-deliver walled links… — v0.48.0, the fourth
        door. The links every machine door failed to open (403 / bot
        defense / TLS fingerprint walls, sitting in the retry queue)
        are offered to the owner's OWN Chrome: the picker queues the
        chosen ones (queue.json + README with suggested filenames in
        <vault>/_review/hand-delivered/), opens each in the real
        Chrome, and the next batch consumes a saved page as a REAL
        fetch. The master table's 🖐 hand Status is the same gesture
        by hand (never a retirement — the link keeps waiting)."""
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
        folder = _hand_delivery.hand_delivery_dir(vault)
        candidates = []
        try:
            if _dryrun.is_enabled():
                state = _website_pipeline.WebsiteStateDB(
                    db_path=_dryrun.shadow_cache_path(
                        os.path.join(APP_DIR, 'cache.db')))
            else:
                state = _website_pipeline.WebsiteStateDB()
            try:
                candidates = _hand_delivery.walled_retry_rows(state)
            finally:
                state.close()
        except Exception as e:
            self.log_message(
                f"⚠️ Walled-links scan skipped: {e}", "warning")
        # links already queued for hand-delivery are shown too (a
        # re-open is harmless — the queue merge is a no-op) but kept
        # out of the "new" count.
        try:
            queued = set((_hand_delivery._read_queue(vault)
                          .get('links') or {}).keys())
        except Exception:
            queued = set()
        if not candidates:
            self._show_custom_message_box(
                "No walled links waiting",
                "No retry-queued link currently ends in a wall the "
                "machine doors could not open (403 / bot defense / TLS "
                "fingerprint).\n\n"
                + (f"{len(queued)} link(s) are already queued for hand "
                   f"delivery — save their pages into:\n{folder}"
                   if queued else
                   "When one appears, its _review note and master-table "
                   "row will say so (the fourth door hint)."),
                success=True)
            return
        picked = self._pick_hand_links_dialog(
            [{'url': c['url'], 'wall': c['wall'],
              'attempts': c.get('attempts', 0)} for c in candidates],
            folder)
        if not picked:
            self.log_message(
                "⏭️ Hand-delivery cancelled — nothing was queued. More ▸ "
                "🖐 Hand-deliver walled links anytime (the master table's "
                "🖐 hand Status queues a single link too).", "info")
            return
        walls = {c['url']: c['wall'] for c in candidates}
        try:
            report = _hand_delivery.enqueue_hand_delivery(
                vault, picked, walls=walls, log=self.log_message,
                open_chrome=True)
        except Exception as e:
            self.log_message(
                f"⚠️ Hand-delivery queue write skipped: {e}", "warning")
            report = {'added': 0, 'opened': 0}
        opened = report.get('opened', 0)
        self._show_custom_message_box(
            "Queued for hand-delivery",
            f"{report.get('added', 0)} link(s) queued"
            + (f", {opened} opened in your Chrome."
               if opened else " — open them from the README (no browser "
               "could be launched here).")
            + "\n\n"
            "For each link: in Chrome, Ctrl+S → format 'Webpage, HTML "
            "Only' → filename = the suggested name (the README lists "
            "them) → folder:\n" + folder
            + "\n\nThe next SYNC batch consumes every delivered page "
            "as a REAL fetch — the note is written, the retry clears.",
            success=True)

    def _pick_hand_links_dialog(self, candidates, folder):
        """v0.48.0 — the fourth door's picker: a checkable list of the
        walled links (URL + the wall that stopped the machine doors);
        the chosen ones are queued and opened in the real Chrome.
        'Open the folder' reveals the hand-delivered folder in the OS
        file manager. Returns the chosen URLs (empty = cancelled)."""
        if getattr(self, '_closing', False) or not self.isVisible():
            return []
        dialog = QDialog(self)
        dialog.setWindowTitle("Hand-deliver walled links")
        dialog.setModal(True)
        dialog.setMinimumWidth(680)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        header = QHBoxLayout()
        icon_label = QLabel("🖐")
        icon_label.setObjectName("msg_glyph")
        header.addWidget(icon_label)
        title_label = QLabel("Hand-deliver walled links")
        title_label.setObjectName("msg_heading")
        title_label.setProperty("tone", "info")
        header.addWidget(title_label)
        header.addStretch()
        layout.addLayout(header)

        msg_label = QLabel(
            "These links answered every machine door with a wall (403 / "
            "bot defense / TLS fingerprint — the wall rides under each "
            "URL). Tick the ones worth saving: each opens in your real "
            "Chrome, is queued with a suggested filename, and the page "
            "you save (Ctrl+S, 'Webpage, HTML Only') becomes a REAL "
            "fetch on the next batch. Nothing is retired — the links "
            "keep waiting until their page is delivered.")
        msg_label.setWordWrap(True)
        layout.addWidget(msg_label)

        list_widget = QListWidget()
        list_widget.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        for c in candidates:
            wall = (c.get('wall') or '')[:110]
            text = c['url'] + (f"   —   {wall}" if wall else '')
            item = QListWidgetItem(text)
            item.setToolTip(text)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            list_widget.addItem(item)
        list_widget.setMinimumHeight(min(340, 22 * len(candidates) + 24))
        layout.addWidget(list_widget)

        folder_label = QLabel(
            f"Deliver into: {folder} (the README regenerates there with "
            f"every link's suggested filename)")
        folder_label.setWordWrap(True)
        layout.addWidget(folder_label)

        btn_row = QHBoxLayout()
        open_btn = QPushButton("Open the folder")
        self._style_btn(open_btn, 'secondary')
        open_btn.setToolTip(
            "Reveal the hand-delivered folder in your file manager — "
            "saved pages land there.")

        def _open_folder():
            try:
                from PyQt6.QtGui import QDesktopServices
                from PyQt6.QtCore import QUrl
                os.makedirs(folder, exist_ok=True)
                QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
            except Exception as e:
                self.log_message(f"⚠️ Could not open the folder: {e}",
                                 "warning")

        open_btn.clicked.connect(_open_folder)
        btn_row.addWidget(open_btn)
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        self._style_btn(cancel_btn, 'secondary')
        cancel_btn.clicked.connect(dialog.reject)
        btn_row.addWidget(cancel_btn)
        queue_btn = QPushButton("🖐 Queue & open in Chrome")
        self._style_btn(queue_btn, 'primary')
        queue_btn.clicked.connect(dialog.accept)
        btn_row.addWidget(queue_btn)
        layout.addLayout(btn_row)

        self._animate_dialog(dialog)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return []
        picked = []
        for i in range(list_widget.count()):
            item = list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                picked.append(candidates[i]['url'])
        return picked

    # ------------------------------------------------------------------
    # v0.50.0 — the fifth door: Chrome fetches the pages itself
    # ------------------------------------------------------------------

    def _maybe_offer_chrome_tab_retry(self):
        """v0.50.0 — the end-of-run offer (the owner's ask, verbatim:
        \"before finishing the run and fetch, app must show a modal —
        xx links didn't generate content… want to retry them in real
        browser?\"). The fetch-failed links of THIS run (each with its
        last error — the master table's \" - \" rows) are offered once:
        one yes opens the owner's own Google Chrome in a fresh session,
        starts one tab per link, and the app takes the content from
        the live pages. Declined (or opted-out) links keep waiting —
        their automatic retries continue, the master table's Status
        cell still decides their fate. Quiet no-op whenever the offer
        cannot be made (pipeline off, no vault, no failures, nothing
        new, shutting down)."""
        cfg = self.config or {}
        if cfg.get('web_browser_retry') is False:
            return  # opted out (the config knob, default ON)
        if not (cfg.get('pipelines') or {}).get('websites', False):
            return
        vault = (cfg.get('website_vault_path') or '').strip()
        if not vault or not os.path.isdir(vault):
            return
        if getattr(self, '_closing', False) or not self.isVisible():
            return
        try:
            summary = getattr(self.worker, '_website_summary', None)
        except Exception:
            summary = None
        if not summary:
            return
        try:
            results = summary.get('results') or []
        except Exception:
            results = []
        links = _chrome_tabs.collect_failed_fetch_links(results)
        if not links:
            return
        # drop the links the fetcher itself retired (auto-verdict:
        # dead / paywalled / refused — the ladder's own final answers;
        # ♻️ revived in the master table is their door back)
        try:
            if _dryrun.is_enabled():
                state = _website_pipeline.WebsiteStateDB(
                    db_path=_dryrun.shadow_cache_path(
                        os.path.join(APP_DIR, 'cache.db')))
            else:
                state = _website_pipeline.WebsiteStateDB()
            try:
                links = [l for l in links
                         if not state.is_dismissed(l['url'])]
            finally:
                state.close()
        except Exception:
            pass  # a state question that cannot be asked: offer them
        offered = getattr(self, '_chrome_tab_offered', None)
        if offered is None:
            offered = self._chrome_tab_offered = set()
        links = [l for l in links if l['url'] not in offered]
        if not links:
            return
        picked = self._chrome_tab_retry_dialog(links)
        if not picked:
            self.log_message(
                "⏭️ Chrome tab retry declined — the failed links keep "
                "waiting (their automatic retries continue; the master "
                "table's Status cell decides their fate).", "info")
            return
        offered.update(p['url'] for p in picked)
        self._start_chrome_tab_retry(picked)

    def _chrome_tab_retry_dialog(self, links):
        """v0.50.0 — the offer modal: the fetch-failed links with their
        last error under each URL (the owner's \"got error xxx\", mind
        the wording), a plain question, and the two answers — retry in
        the real Chrome now, or not now. Checkable list (the picker's
        law — the owner may untick), all links ticked by default.
        Returns the chosen ``[{'url', 'error'}]`` (empty = declined)."""
        if getattr(self, '_closing', False) or not self.isVisible():
            return []
        dialog = QDialog(self)
        dialog.setWindowTitle("Retry in your real Chrome?")
        dialog.setModal(True)
        dialog.setMinimumWidth(720)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        header = QHBoxLayout()
        icon_label = QLabel("🤖")
        icon_label.setObjectName("msg_glyph")
        header.addWidget(icon_label)
        title_label = QLabel(
            f"{len(links)} link(s) couldn't generate content")
        title_label.setObjectName("msg_heading")
        title_label.setProperty("tone", "info")
        header.addWidget(title_label)
        header.addStretch()
        layout.addLayout(header)

        msg_label = QLabel(
            f"The machine fetch failed for {len(links)} link(s) this run — "
            "no content was generated, each one's last error rides under "
            "its URL (they are the \" - \" rows in the master table).\n\n"
            "Want to retry them in your real Google Chrome? GitCurator "
            "opens a fresh Chrome window (your own Chrome, a new session "
            "— your running window is never touched), starts one tab per "
            "link, waits for every page to load, and takes the content "
            "from the live pages itself — no manual saving. Links that "
            "still fail keep waiting; their automatic retries continue.")
        msg_label.setWordWrap(True)
        layout.addWidget(msg_label)

        list_widget = QListWidget()
        list_widget.setSelectionMode(
            QListWidget.SelectionMode.NoSelection)
        for l in links:
            err = (l.get('error') or '')[:110]
            text = l['url'] + (f"   —   {err}" if err else '')
            item = QListWidgetItem(text)
            item.setToolTip(text)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            list_widget.addItem(item)
        list_widget.setMinimumHeight(
            min(320, 22 * min(len(links), 12) + 24))
        layout.addWidget(list_widget)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        no_btn = QPushButton("Not now")
        self._style_btn(no_btn, 'secondary')
        no_btn.setToolTip("The failed links keep waiting — their "
                          "automatic retries continue, and the master "
                          "table's Status cell decides their fate.")
        no_btn.clicked.connect(dialog.reject)
        btn_row.addWidget(no_btn)
        retry_btn = QPushButton("🤖 Retry in Chrome now")
        self._style_btn(retry_btn, 'primary')
        retry_btn.setToolTip("Opens your own Google Chrome (a fresh "
                             "session), one tab per link, and takes the "
                             "content from the live pages.")
        retry_btn.clicked.connect(dialog.accept)
        btn_row.addWidget(retry_btn)
        layout.addLayout(btn_row)

        self._animate_dialog(dialog)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return []
        picked = []
        for i in range(list_widget.count()):
            if list_widget.item(i).checkState() == Qt.CheckState.Checked:
                picked.append(links[i])
        return picked

    def _start_chrome_tab_retry(self, links):
        """v0.50.0 — run the fifth door in the background (the GUI never
        blocks): the pages are delivered from the owner's real Chrome
        into the hand-delivered folder, then the delivered links are
        re-processed as REAL fetches (a fresh mini-batch — the pipeline
        takes each delivered page before any machine door is asked).
        Same QThread pattern as the vault seals: a kept reference, log
        lines into the main log, and a queued signal back.

        v0.52.0 — ONE delivery at a time: a second request while a
        delivery is running (the end-of-run auto hand pass racing the
        modal's pick, the caught-up check racing a batch) is MERGED
        into a backlog the finish handler drains — two Chrome
        deliveries at once would fight over the worker reference (Qt
        GC on a running QThread is the crash the kept-reference law
        exists to prevent) and over the owner's screen.

        v0.53.0 — the delivery's verdict is TOLD: the door waits for
        each page (the load watch — complete at an http(s) URL, still
        for the settle, real by the verdict), and a page that could
        not be gathered fires ``delivery_failed`` — the honest modal
        the owner asked for ("xx links didn't generate content or
        wasn't successful or got error xxx"), never a silent false
        positive of success after a Chrome that crashed and closed.

        v0.54.0 — OPENING IS NOT THE SUCCESS: the pending count
        (``_chrome_delivery_pending``) is what holds the batch
        scorecard back (see processing_finished) — a tab that opened
        is a tab still being waited on, and the scorecard may only
        celebrate notes that landed in the vault. The worker keeps
        its full report so the finish handlers can tell the whole
        story, and the failure detail is emitted BEFORE the delivered
        list (the failure modal shows first; the delivered handler
        then decides what the scorecard waits for)."""
        cfg = self.config or {}
        vault = (cfg.get('website_vault_path') or '').strip()
        if not vault or not links:
            return
        if getattr(self, '_chrome_delivery_busy', False):
            backlog = getattr(self, '_chrome_delivery_backlog', None)
            if backlog is None:
                backlog = self._chrome_delivery_backlog = []
            backlog.extend(links)
            self._chrome_delivery_pending = \
                getattr(self, '_chrome_delivery_pending', 0) + len(links)
            self.log_message(
                f"🖐 A Chrome delivery is already running — "
                f"{len(links)} link(s) queued behind it (they open "
                f"when the current tabs finish)", "info")
            return

        class ChromeTabRetryWorker(QThread):
            log_message = pyqtSignal(str, str)
            delivered = pyqtSignal(list)
            delivery_failed = pyqtSignal(list)

            def __init__(self, vault, links, config):
                super().__init__()
                self._vault = vault
                self._links = links
                self._config = config
                self._report = {}          # v0.54 — the finish handlers'
                                            # whole story (kept reference)

            def run(self):
                try:
                    report = _chrome_tabs.deliver_pages_via_chrome(
                        self._vault, self._links,
                        log=self.log_message.emit, config=self._config)
                except Exception as e:  # the door never breaks anything
                    self.log_message.emit(
                        f"⚠️ Chrome tab retry failed: {e}", "warning")
                    self._report = {'delivered': 0, 'failed': 1,
                                    'urls': [],
                                    'failed_links': [
                                        {'url': '',
                                         'error': f'Chrome tab retry '
                                                  f'failed: {e}'}]}
                    self.delivery_failed.emit(list(
                        self._report['failed_links']))
                    self.delivered.emit([])
                    return
                self._report = report
                # v0.54 — the failure detail FIRST (its modal shows,
                # the owner reads it, then the delivered handler
                # decides what the scorecard still waits for)
                failed_links = list(report.get('failed_links') or [])
                if failed_links:
                    self.delivery_failed.emit(failed_links)
                self.delivered.emit(list(report.get('urls') or []))

        worker = ChromeTabRetryWorker(vault, links, dict(cfg))
        self._chrome_retry_worker = worker  # a kept reference (Qt GC law)
        self._chrome_delivery_busy = True
        self._chrome_delivery_pending = \
            getattr(self, '_chrome_delivery_pending', 0) + len(links)
        worker.log_message.connect(self.log_message)

        def _on_delivered(urls):
            self._chrome_delivery_busy = False
            self._chrome_delivery_pending = 0
            if urls:
                if getattr(self, '_batch_running', False):
                    self.log_message(
                        f"🤖 {len(urls)} page(s) delivered from your "
                        f"Chrome — a batch is running, so they join the "
                        f"NEXT one automatically (delivered pages are "
                        f"never left waiting)", "info")
                else:
                    self.log_message(
                        f"🤖 {len(urls)} page(s) delivered from your "
                        f"Chrome — re-processing them now through the "
                        f"Websites pipeline as real fetches (classify → "
                        f"analyze → the proper, categorized note)…",
                        "success")
                    # v0.54 — the re-run is part of THIS delivery's
                    # story: its finish merges into the stashed
                    # scorecard (the batch that waited) instead of
                    # celebrating a second, partial story on its own
                    # v0.57.0 — THE NOTE IS THE SUCCESS, the routing
                    # fix: the delivered links ride as WEBSITE links
                    # (``non_github_urls``), never as GitHub urls —
                    # the owner's report ("in logs it shows this ✅
                    # but actually the llm model does not actually
                    # work on that website and create a proper note")
                    # was exactly this bug: 'direct' mode fed the
                    # website URLs to the GITHUB loop, which marked
                    # each one "non-GitHub URL — skipped", and the
                    # Websites pipeline never saw them. The LLM never
                    # ran; the pages sat in the folder while the log
                    # told its ✅ story. Now they land in the Websites
                    # phase, the delivered page answers the fetch, and
                    # the note is the success.
                    self._delivery_rerun_pending = True
                    try:
                        self._start_worker_with_urls(
                            [], non_github_urls=list(urls))
                    except Exception as e:
                        self._delivery_rerun_pending = False
                        self.log_message(
                            f"⚠️ The delivered-page re-run could not "
                            f"start ({e}) — the next SYNC batch takes "
                            f"them automatically", "warning")
                        self._flush_stashed_batch_summary()
            else:
                # nothing delivered (all failed, or nothing to fetch):
                # the failure modal (if any) already told its story —
                # the stashed scorecard follows it with the full truth
                self._flush_stashed_batch_summary()
            # drain the backlog — the deliveries that arrived while
            # this one ran get their own Chrome pass now
            backlog = getattr(self, '_chrome_delivery_backlog', None) \
                or []
            self._chrome_delivery_backlog = []
            if backlog:
                self._start_chrome_tab_retry(backlog)

        worker.delivered.connect(_on_delivered)
        worker.delivery_failed.connect(self._on_delivery_failed)
        worker.start()
        self.log_message(
            f"🤖 Chrome tab retry: opening {len(links)} link(s) in your "
            f"real Chrome (your own window when its debug port "
            f"answers — More ▸ 🪄 Attach to my Chrome; otherwise the "
            f"door's identity, direct line first, then your proxy — "
            f"one tab per link; watch the tabs load; GitCurator takes "
            f"each page when it is ready and processes it into the "
            f"vault — the scorecard waits for the notes to land)",
            "info")

    def _on_delivery_failed(self, failed):
        """v0.53.0 — the end-of-delivery honesty: the owner's report
        ("app shown false positive of success") ends here. A Chrome
        delivery that could not gather pages is TOLD, in the owner's
        own modal grammar ("xx number of links didn't generate content
        or wasn't successful or got error xxx"), with the first errors
        named — the links keep waiting in the retry queue, the next
        run offers them again. Tolerated everywhere (a modal that
        cannot be shown never breaks the finish path); the log already
        carries the per-link warnings.

        v0.54.0 — this modal is emitted BEFORE the delivered list, so
        it is the FIRST thing the owner reads at a delivery's end;
        the stashed batch scorecard (if the batch is waiting on these
        very pages) follows it — flushed by the delivered handler with
        the delivery's numbers riding in (see
        _flush_stashed_batch_summary)."""
        try:
            failed = [f for f in (failed or []) if isinstance(f, dict)]
            if not failed:
                return
            names = []
            for f in failed[:5]:
                url = (f.get('url') or '').strip()
                err = str(f.get('error') or '').strip()
                names.append(f"• {url or '(a link)'}"
                             + (f" — {err}" if err else ''))
            if len(failed) > 5:
                names.append(f"… and {len(failed) - 5} more")
            self.log_message(
                f"⚠️ Chrome tab retry: {len(failed)} link(s) couldn't be "
                f"gathered from your Chrome — they keep waiting in the "
                f"retry queue", "warning")
            if getattr(self, '_closing', False) or not self.isVisible():
                return
            self._show_custom_message_box(
                "Chrome tab retry — pages not taken",
                f"{len(failed)} link(s) didn't generate content from "
                f"your Chrome (the tabs crashed, showed an error page, "
                f"or never finished loading).\n\nThey keep waiting in "
                f"the retry queue — the next run offers them again "
                f"(More ▸ 🤖 Chrome tab-retry anytime).\n\n"
                + "\n".join(names),
                success=False)
        except Exception:
            pass  # the log already told the honest story

    def _flush_stashed_batch_summary(self):
        """v0.54.0 — the scorecard that WAITED gets its turn: the
        batch summary stashed while Chrome was gathering pages is
        brought out with the delivery's own numbers riding in
        (``chrome_delivered`` / ``chrome_failed`` from the delivery
        worker's kept report) so the score the owner finally reads is
        the WHOLE story — what the batch did + what Chrome took. The
        stash is cleared first (idempotent; a second call is a no-op);
        a missing or empty stash is a quiet no-op (a delivery that
        ended without a waiting batch — the manual More ▸ paths)."""
        try:
            stashed = getattr(self, '_stashed_batch_summary', None)
            self._stashed_batch_summary = None
            if not stashed:
                return
            summary, elapsed_str = stashed
            try:
                rep = getattr(self._chrome_retry_worker, '_report',
                              None) or {}
                summary['chrome_delivered'] = int(rep.get('delivered') or 0)
                summary['chrome_failed'] = int(rep.get('failed') or 0)
            except Exception:
                pass  # the report is an optional grace, never a gate
            self._celebrate_batch(summary, elapsed_str)
        except Exception as e:
            try:
                self.log_message(
                    f"⚠️ The held-back scorecard could not be shown "
                    f"({e}) — the batch's story is in the log and the "
                    f"final report", "warning")
            except Exception:
                pass

    def chrome_tab_retry_now(self):
        """More ▸ 🤖 Chrome tab-retry failed links… — v0.50.0, the
        fifth door on demand: every link waiting in the websites retry
        queue (the fetch-failed pile — each with its last error, the
        master table's \" - \" rows) is offered for the automatic
        real-Chrome tab retry. Same dialog, same background worker,
        same delivered-page re-run as the end-of-batch offer."""
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
        if getattr(self, '_batch_running', False):
            self._show_custom_message_box(
                "Batch already running",
                "A batch is already running — finish or stop it first.",
                success=False)
            return
        candidates = []
        try:
            if _dryrun.is_enabled():
                state = _website_pipeline.WebsiteStateDB(
                    db_path=_dryrun.shadow_cache_path(
                        os.path.join(APP_DIR, 'cache.db')))
            else:
                state = _website_pipeline.WebsiteStateDB()
            try:
                for row in state.all_retry_rows():
                    url = (row.get('url') or '').strip()
                    if not url.lower().startswith(('http://', 'https://')) \
                            or _chrome_tabs.is_loopback_url(url):
                        continue
                    if state.is_dismissed(url):
                        continue  # the ladder's own verdict answered it
                    candidates.append(
                        {'url': url,
                         'error': str(row.get('last_error') or '')})
            finally:
                state.close()
        except Exception as e:
            self.log_message(f"⚠️ Retry-queue scan skipped: {e}", "warning")
        offered = getattr(self, '_chrome_tab_offered', None)
        if offered is None:
            offered = self._chrome_tab_offered = set()
        candidates = [c for c in candidates
                      if c['url'] not in offered]
        if not candidates:
            self._show_custom_message_box(
                "No failed links waiting",
                "The websites retry queue has no failed link waiting "
                "for the fifth door (loopback and auto-retired links "
                "never ask). When a fetch fails, the end-of-run modal "
                "offers the retry automatically.", success=True)
            return
        picked = self._chrome_tab_retry_dialog(candidates)
        if not picked:
            self.log_message(
                "⏭️ Chrome tab retry cancelled — the failed links keep "
                "waiting (their automatic retries continue).", "info")
            return
        offered.update(p['url'] for p in picked)
        self._start_chrome_tab_retry(picked)

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
                        # v0.63.1 — and the SAME population too: get_all_clear
                        # gates on GITHUB rows only (a website row's
                        # ledger lives in the Websites pipeline's own
                        # retry queue — five doors of its own), so the
                        # listing counts github rows only. The waiting
                        # websites get their own quiet line instead of a
                        # false "not verified" cry.
                        try:
                            _has = worker_lt.vault_has
                        except Exception:
                            _has = None
                        pending_links = [
                            l for l in worker_lt.manifest["links"]
                            if l.get("type") == "github"
                            and l["status"] in ("failed", "processing", "pending")
                            and not (_has and _has(l["url"]))
                        ]
                        _ws_waiting = sum(
                            1 for l in worker_lt.manifest["links"]
                            if l.get("type") != "github"
                            and l["status"] in ("failed", "processing", "pending"))
                        self.log_message(
                            f"⚠️ {len(pending_links)} repo link(s) not verified — bot messages NOT marked as read",
                            "warning"
                        )
                        for pl in pending_links[:5]:
                            self.log_message(
                                f"   • [{pl['status']}] {pl['url']}" + (f" — {pl.get('error','')}" if pl.get('error') else ""),
                                "info"
                            )
                        if len(pending_links) > 5:
                            self.log_message(f"   ... and {len(pending_links) - 5} more", "info")
                        if _ws_waiting:
                            self.log_message(
                                f"🌐 {_ws_waiting} website link"
                                f"{'s' if _ws_waiting != 1 else ''} keep "
                                f"waiting in the Websites pipeline's own "
                                f"retry queue — accounted for, not a "
                                f"verification failure.", "info")
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
                # v0.52.0 — the hand rows go FIRST: the owner's 🖐
                # gestures are his answer, so the app scrapes them in
                # his real Chrome without asking again (the gesture
                # was the ask). Tolerated everywhere — a pass that
                # cannot run never costs the owner his scorecard.
                try:
                    self._maybe_deliver_hand_queue()
                except Exception as _hand_err:
                    self.log_message(
                        f"⚠️ Hand-row Chrome pass skipped: {_hand_err}",
                        "warning")
                # v0.57.0 — THE NOTE IS THE SUCCESS, the redo pass: the
                # hand links whose notes are still not properly stored
                # (half-fetched _review items, the false successes) are
                # re-generated now — the delivered page re-read, the
                # LLM asked again. Skipped while a Chrome delivery is
                # gathering (those links get their own re-run); each
                # link redone once per session. Tolerated everywhere.
                try:
                    self._maybe_redo_hand_notes()
                except Exception as _redo_err:
                    self.log_message(
                        f"⚠️ Hand-note redo pass skipped: {_redo_err}",
                        "warning")
                # v0.50.0 — the fifth door's offer rides BEFORE the
                # scorecard: the run's fetch failures are the run's last
                # question ("want to retry them in your real Chrome?"),
                # and only then does the fanfare celebrate what landed.
                # Tolerated everywhere — an offer that cannot be made
                # never costs the owner his scorecard.
                try:
                    self._maybe_offer_chrome_tab_retry()
                except Exception as _chrome_err:
                    self.log_message(
                        f"⚠️ Chrome tab-retry offer skipped: "
                        f"{_chrome_err}", "warning")
                # v0.54.0 — OPENING IS NOT THE SUCCESS: while a Chrome
                # delivery is gathering pages (the hand pass above, or
                # the offer the owner just accepted), the scorecard
                # WAITS — a tab that opened is a tab still being
                # waited on, and "✓ All links processed cleanly" may
                # only be said after the notes land in the vault. The
                # stash is flushed when the delivery (and, for the
                # delivered pages, their re-run) finishes — with the
                # delivery's own numbers riding in.
                _pending_chrome = int(
                    getattr(self, '_chrome_delivery_pending', 0) or 0)
                if _pending_chrome > 0:
                    self._stashed_batch_summary = (_summary, elapsed_str)
                    self.log_message(
                        f"⏳ The batch's scorecard waits — "
                        f"{_pending_chrome} link(s) are being gathered "
                        f"in your Chrome right now (their notes land "
                        f"in the vault when the pages are taken and "
                        f"processed; the scorecard follows)",
                        "info")
                elif getattr(self, '_delivery_rerun_pending', False):
                    # v0.54.0 — this IS the delivered pages' re-run: its
                    # numbers MERGE into the stashed scorecard (the
                    # batch that waited) and ONE scorecard celebrates
                    # the whole story — batch + Chrome pages + notes.
                    self._delivery_rerun_pending = False
                    _stashed = getattr(self, '_stashed_batch_summary',
                                       None)
                    if _stashed:
                        _base, _base_elapsed = _stashed
                        self._stashed_batch_summary = None
                        try:
                            _rep = getattr(self._chrome_retry_worker,
                                           '_report', None) or {}
                            _base['chrome_delivered'] = int(
                                _rep.get('delivered') or 0)
                            _base['chrome_failed'] = int(
                                _rep.get('failed') or 0)
                        except Exception:
                            pass
                        try:
                            _ws = dict(_base.get('websites') or {})
                            _rs = _summary.get('websites') or {}
                            if _rs:
                                for _k in ('processed', 'review',
                                           'skipped', 'failed'):
                                    _ws[_k] = int(_ws.get(_k, 0) or 0) \
                                        + int(_rs.get(_k, 0) or 0)
                                _base['websites'] = _ws
                            _base['failed_links'] = int(
                                _base.get('failed_links', 0) or 0) \
                                + int(_summary.get('failed_links', 0) or 0)
                            _base['new_notes'] = int(
                                _base.get('new_notes', 0) or 0) \
                                + int(_summary.get('new_notes', 0) or 0)
                        except Exception:
                            pass  # the merge is a grace — never a gate
                        self._celebrate_batch(
                            _base, _base_elapsed or elapsed_str)
                    else:
                        # no stash survived (the waiting batch's story
                        # was already told) — this re-run celebrates on
                        # its own, the honest fallback
                        self._celebrate_batch(_summary, elapsed_str)
                else:
                    # nothing is being gathered and this is not a
                    # re-run — a stale stash (its delivery chain died
                    # without flushing) is dropped with an honest line
                    # rather than shown stale on top of the new batch
                    if getattr(self, '_stashed_batch_summary', None):
                        self._stashed_batch_summary = None
                        self.log_message(
                            "ℹ️ The previous batch's held-back scorecard "
                            "was released without showing (its Chrome "
                            "delivery's story was told in the log and "
                            "its failure modal, if any)",
                            "info")
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
        way (the routing keys on natural completion, not cleanliness).

        v0.54.0 — a Chrome page that was not taken is the same
        non-celebration (the scorecard that waited says so; the chime
        must agree with it)."""
        _failed = 0
        _chrome_failed = 0
        if summary:
            try:
                _failed = int(summary.get('failed_links', 0) or 0)
            except (TypeError, ValueError):
                _failed = 0
            try:
                _chrome_failed = int(summary.get('chrome_failed', 0) or 0)
            except (TypeError, ValueError):
                _chrome_failed = 0
        self._play_batch_sound(needs_retry=bool(_failed or _chrome_failed))
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
        # v0.54.0 — a Chrome page that was not taken is the SAME
        # honesty (the scorecard that waited may not party while its
        # links keep waiting): the party needs the failed links AND
        # the Chrome failures to be zero.
        _failed = 0
        try:
            _failed = int(summary.get('failed_links', 0) or 0)
        except (TypeError, ValueError):
            _failed = 0
        _chrome_failed = 0
        try:
            _chrome_failed = int(summary.get('chrome_failed', 0) or 0)
        except (TypeError, ValueError):
            _chrome_failed = 0
        _gathering_now = 0
        try:
            _gathering_now = int(getattr(self, '_chrome_delivery_pending',
                                         0) or 0)
        except (TypeError, ValueError):
            _gathering_now = 0
        _not_clean = bool(_failed or _chrome_failed or _gathering_now)
        header = QHBoxLayout()
        glyph = QLabel("⏳" if _gathering_now else ("🏁" if _not_clean
                                                    else "🎉"))
        glyph.setObjectName("msg_glyph")
        header.addWidget(glyph)
        title = QLabel(" Batch Complete!" if not _not_clean
                       else (" Batch Complete — still gathering"
                             if _gathering_now else " Batch Complete"))
        title.setObjectName("msg_heading")
        title.setProperty("tone",
                          "warning" if _not_clean else "success")
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
        # v0.54.0 — the Chrome pages row: the delivery's own numbers
        # (set at the flush/merge) tell the fifth door's part of the
        # story — never a silent "0 saved" while the tabs load.
        _cd = 0
        _cf = 0
        try:
            _cd = int(summary.get('chrome_delivered', 0) or 0)
            _cf = int(summary.get('chrome_failed', 0) or 0)
        except (TypeError, ValueError):
            _cd, _cf = 0, 0
        if _cd or _cf:
            _row("Chrome pages",
                 f"{_cd} taken from your Chrome · "
                 + (f"{_cf} not taken (they keep waiting)"
                    if _cf else "all taken"),
                 tone=("warning" if _cf else "success"))
        _failed = int(summary.get('failed_links', 0) or 0)
        _gathering = 0
        try:
            _gathering = int(getattr(self, '_chrome_delivery_pending', 0)
                             or 0)
        except (TypeError, ValueError):
            _gathering = 0
        if _failed:
            _row("Needs retry", f"{_failed} link(s) — next run",
                 tone="warning")
        elif _gathering > 0:
            # v0.54.0 — pages are being gathered RIGHT NOW: opening a
            # tab is not the success, so the clean verdict stays unsaid
            # until the notes land (the stash/flush law)
            _row("Status",
                 f"⏳ {_gathering} link(s) gathering via your Chrome — "
                 f"stored when their pages are taken",
                 tone="warning")
        elif _cf:
            _row("Status",
                 f"⚠️ {_cf} Chrome link(s) didn't generate content — "
                 f"they keep waiting",
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

