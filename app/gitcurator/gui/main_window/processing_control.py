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

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress_bar.setValue(0)
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
            self.stop_btn.setEnabled(False)

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
        if not self.start_btn.isEnabled():
            return  # a NEW batch is already running — keep the bar live
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
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
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
                        # Show which links are causing the failure
                        pending_links = [l for l in worker_lt.manifest["links"] if l["status"] in ("failed", "processing", "pending")]
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

            self._show_custom_message_box("Processing Complete", f"{message}{elapsed_str}", success=True)
        else:
            self.log_message(f"❌ {message}", "error")
            self.progress_bar.setFormat("❌ Failed")
            self._set_pipeline_state('error')
            self._schedule_progress_hide()
            self._show_custom_message_box("Processing Error", message, success=False)

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

