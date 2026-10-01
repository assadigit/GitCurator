"""ProcessingWorker shell + TestWorker — the batch orchestration.

v0.25.0 hygiene pass: the ProcessingWorker's domain methods moved
verbatim into gitcurator/gui/worker/ mixins (llm, github_meta, notes,
reports, inputs, website_phase). This module keeps the QThread shell
— the signals, __init__, run, the _run_impl batch orchestrator, stop
— plus TestWorker, and remains the public import path
(gitcurator.gui.processing_worker).
"""


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

try:
    import github
    from github import Github, GithubException, Auth
except ImportError:
    print("PyGithub is not installed. Please run: pip install PyGithub")
    sys.exit(1)

try:
    import ollama
except ImportError:
    print("Ollama Python client is not installed. Please run: pip install ollama")
    sys.exit(1)

_APP_DIR = APP_DIR

from gitcurator.gui.cache_db import CacheDB

from gitcurator.gui.dead_links import DEAD_LINK_THRESHOLD, dead_link_threshold

from gitcurator.gui.link_helpers import clean_url, normalize_url

from gitcurator.gui.link_tracker import LinkTracker

from gitcurator.gui.platform_intake import (
    PLATFORM_INFO,
    _inbox_table_vault,
    classify_platform,
    write_inbox_links_by_platform,
)

from gitcurator.gui.vault_index import VaultIndex, _safe_moc_name

from gitcurator.gui.worker_jobs import _run_telegram_worker

from gitcurator.gui.worker.llm import WorkerLlmMixin
from gitcurator.gui.worker.github_meta import WorkerGithubMetaMixin
from gitcurator.gui.worker.notes import WorkerNotesMixin
from gitcurator.gui.worker.reports import WorkerReportsMixin
from gitcurator.gui.worker.inputs import WorkerInputsMixin
from gitcurator.gui.worker.website_phase import WorkerWebsitePhaseMixin


class ProcessingWorker(WorkerLlmMixin, WorkerGithubMetaMixin, WorkerNotesMixin, WorkerReportsMixin, WorkerInputsMixin, WorkerWebsitePhaseMixin, QThread):
    progress_updated = pyqtSignal(int, int)
    status_updated = pyqtSignal(str)
    log_message = pyqtSignal(str, str)
    finished_signal = pyqtSignal(bool, str)
    code_requested = pyqtSignal(str)          # "CODE" or "PASSWORD"
    disk_full_signal = pyqtSignal(str)        # path that failed
    llm_failed_signal = pyqtSignal(str)       # repo name
    # v30 — Fix (model persistence): emitted when the user picks a new model
    # from the LLM-failure dialog (or the single-model auto-switch below).
    # Args: (provider, model_name). MainWindow syncs the Settings combo and
    # saves config so the choice survives the batch AND the next launch.
    model_changed = pyqtSignal(str, str)

    def __init__(self, config, mode, range_from=None, range_to=None, offset_start=None, offset_count=None, import_file=None, urls=None, headless=False, dry_run=False):
        super().__init__()
        self.config = config
        self.mode = mode
        self.range_from = range_from
        self.range_to = range_to
        self.offset_start = offset_start
        self.offset_count = offset_count
        self.import_file = import_file
        self.urls = urls
        # v30 — Fix (headless hang-bombs): headless mode has NO GUI to answer
        # code_requested / llm_failed_signal / disk_full_signal. Every wait
        # below checks this flag and takes a NON-BLOCKING default instead of
        # stalling the batch (previously: 5-min, 10-min and INFINITE hangs).
        self._headless = bool(headless)
        # v0.09.5 — Phase 0 (dry-run): when True, run() flips the global
        # dry-run switch so every vault write in the batch is logged
        # instead of performed (gitcurator/core/dryrun.py). The GUI never
        # passes this; only the CLI's --dry-run flag does.
        self._dry_run = bool(dry_run)
        self.is_running = True
        self.processed = 0
        self._current_position = 0  # tracks ALL URLs (including skips) for progress bar
        self.total = 0
        self._code_event = threading.Event()
        self._code_response = ""
        # LLM failure retry state — blocks worker thread until GUI delivers a decision.
        # Response values: "skip", "retry", "stop", or a model name to retry with.
        self._llm_retry_event = threading.Event()
        self._llm_retry_response = ""
        # v0.07.2 (merged v0.09) — Fix (CLI model picker, owner report: "it
        # didn't let me choose a new model... model wasn't found = failed
        # cli"): the CLI runs this worker with headless=True, so the per-repo
        # LLM-failure DIALOG never appears and every repo fell back to
        # placeholder values when the configured model wasn't pulled. The CLI
        # now sets this callback; both the warmup path and
        # _wait_for_llm_decision call it (on the worker thread) to show an
        # interactive console menu. Signature: configured_model,
        # available_models -> model name or None (declined / no console).
        # GUI mode leaves it unset.
        self.model_prompt_callback = None
        # v0.07.2 — once the console user declines the model menu we stop
        # asking for the REST of the batch (auto-pick / skip takes over).
        self._llm_headless_declined = False
        # Disk-full pause flag — when True, the worker spins waiting for the
        # GUI to clear it after the user frees up disk space.
        self._disk_full_paused = False
        # Vault index for URL-based dedup (ground truth — beats SQLite cache)
        self._vault_index = None
        # Batch undo snapshot path (v22 — Feature 6: Batch Undo)
        self._batch_snapshot_path = ""
        # v23 — No Link Left Behind: tracks every URL through a 5-phase
        # pipeline. The manifest file is the source of truth.
        self.link_tracker = None
        # Flag set by MainWindow when the URLs come from the bot queue (so
        # the link tracker can record the correct source).
        self._bot_source = False
        # v25 pre-flight: banner download throttle counter — incremented on
        # every _download_banner() call so we can pause periodically and
        # avoid opengraph.githubassets.com 429s during large batches.
        self._banner_count = 0
        # v25 pre-flight: total duplicate URLs removed during intake (raw vs
        # unique). Surfaced in the final report.
        self._intake_duplicates = 0
        # v25 pre-flight: total raw URLs seen during intake (before dedup).
        # Paired with _intake_duplicates for the "N unique from M total"
        # display in the final report.
        self._raw_url_count = 0

    def run(self):
        # v0.06 — Fix (stuck Telegram lock, part 2): exception-proof wrapper.
        # The old run() body had NO top-level try/except, so an uncaught
        # exception anywhere in the ~850-line pipeline (CacheDB init,
        # VaultIndex rebuild, cache.close, …) killed the QThread silently:
        # finished_signal was never emitted -> processing_finished never ran
        # -> the Telegram lock stayed held forever for telegram-mode batches.
        # The body now lives in _run_impl(); this wrapper guarantees the
        # signal is ALWAYS emitted exactly once.
        # v0.09.5 — Phase 0 (dry-run): flip the global switch for the
        # duration of the run — and ONLY the duration; the finally below
        # guarantees it goes off again even on a crash.
        if self._dry_run:
            _dryrun.enable()
            try:
                self.log_message.emit(
                    "🧪 DRY-RUN: every vault write this batch would perform is "
                    "logged and skipped. Nothing will be written, sealed or "
                    "marked read.", "warning")
            except Exception:
                pass
        try:
            self._run_impl()
        except BaseException as e:  # noqa: BLE001 — must always signal
            try:
                import traceback as _tb
                self.log_message.emit(
                    f"💥 Batch crashed: {type(e).__name__}: {e}\n"
                    f"{_tb.format_exc()[-800:]}", "error"
                )
            except Exception:
                pass
            try:
                self.finished_signal.emit(False, f"Batch crashed: {type(e).__name__}: {e}")
            except Exception:
                pass
        finally:
            if self._dry_run:
                _dryrun.disable()

    def _run_impl(self):
        logger = logging.getLogger()

        # v0.10.0 — Phase 1 (pipelines): the GitHub pipeline switch, checked
        # BEFORE anything is fetched so an OFF switch consumes nothing (no
        # queue reads, no marks, no cache writes). Default ON — existing
        # users see no change (SPEC non-negotiable #4).
        # v0.11.0 — Phase 2: the Websites pipeline has the same contract. The
        # early return now fires only when BOTH are off; with websites ON the
        # fetch still happens (the websites pipeline needs those links) and
        # GitHub links are simply skipped below.
        _pipelines_cfg = (self.config or {}).get('pipelines') or {}
        _github_pipeline_on = bool(_pipelines_cfg.get('github', True))
        _websites_pipeline_on = bool(_pipelines_cfg.get('websites', False))
        if not _github_pipeline_on and not _websites_pipeline_on:
            self.log_message.emit(
                "⛔ Both pipelines are switched OFF (Settings → 📁 Vault). "
                "Nothing was fetched or processed.", "warning")
            self.finished_signal.emit(
                True, "Both pipelines are off — nothing to do.")
            return

        if self.mode == 'direct':
            urls = self.urls if self.urls else []
        elif self.mode == 'import':
            urls = self._fetch_from_import()
        else:
            urls = self._fetch_from_telegram()

        # v0.11.0 — Phase 2: with the GitHub pipeline off, GitHub links are
        # logged and skipped — never processed (the websites phase below
        # still runs on the non-Github links).
        if not _github_pipeline_on and urls:
            self.log_message.emit(
                f"⛔ GitHub pipeline is OFF — {len(urls)} GitHub link(s) "
                "skipped this run.", "warning")
            urls = []

        _website_links = list(getattr(self, '_non_github_urls', []) or [])
        if not urls and not (_websites_pipeline_on and _website_links):
            self.log_message.emit("No URLs found to process.", "warning")
            self.finished_signal.emit(True, "No URLs found.")
            return

        self.total = len(urls)
        self.processed = 0

        # v23 — Phase 1: INTAKE — record ALL links BEFORE any processing so
        # the manifest is the source of truth and survives app crashes. The
        # manifest is written atomically (temp file + rename) inside _save().
        vault_path = self.config.get('vault_path', '')
        if vault_path:
            try:
                self.link_tracker = LinkTracker(vault_path)
                # Determine source label for the manifest
                if self.mode == 'direct':
                    source = 'bot' if getattr(self, '_bot_source', False) else 'import'
                elif self.mode == 'import':
                    source = 'import'
                else:
                    source = 'telegram'
                self.link_tracker.set_source(source)

                # Get non-GitHub URLs (stored by _fetch_from_telegram or
                # _fetch_from_import, or passed in by the bot-queue flow).
                non_github = getattr(self, '_non_github_urls', []) or []
                self.link_tracker.intake(urls, non_github)

                # Non-GitHub links: with the Websites pipeline ON (v0.11.0
                # — Phase 2) they are NOT recorded in the _inbox dead end;
                # the website phase at the end of this run processes them
                # and marks the manifest itself (SPEC §6 Phase 2 routing).
                # The old behavior (inbox table + mark_recorded) stays when
                # the websites pipeline is OFF.
                if non_github and not _websites_pipeline_on:
                    for ng_url in non_github:
                        self.link_tracker.mark_recorded(ng_url)

                    # Write non-GitHub links to the inbox table
                    self._create_inbox_notes(non_github, source="Bot" if getattr(self, '_bot_source', False) else "Import")
                elif non_github:
                    self.log_message.emit(
                        f"🌐 {len(non_github)} non-GitHub link(s) queued for "
                        "the Websites pipeline", "info"
                    )

                self.log_message.emit(
                    f"📋 Manifest created: {len(urls)} GitHub + {len(non_github)} non-GitHub links recorded",
                    "info"
                )
            except Exception as lt_err:
                # Manifest is best-effort — never block the batch if it fails.
                self.log_message.emit(
                    f"⚠️ Link tracker init failed (continuing without manifest): {lt_err}",
                    "warning"
                )
                self.link_tracker = None

        github_token = self.config.get('github_token', None)
        if github_token:
            auth = Auth.Token(github_token)
            g = Github(auth=auth)
        else:
            g = Github()

        ollama_base = self.config.get('ollama', {}).get('base_url', 'http://localhost:11434')
        ollama_model = self.config.get('ollama', {}).get('model', 'qwythos-9b')
        # v26 — Fix 4: pick the LLM provider from config. 'cloud' skips the
        # local-Ollama connection check + warmup and routes _llm_analyze
        # through _call_cloud_llm (OpenAI-compatible HTTP API). Default is
        # 'ollama' so existing users see no behavior change.
        llm_provider = self.config.get('llm_provider', 'ollama')
        ollama_client = None
        if llm_provider == 'ollama':
            ollama_client = ollama.Client(host=ollama_base)

            # v30 — Fix (timeouts on every external call): list() with a
            # 15s wall-clock timeout. A hung/zombie Ollama server used to
            # block this QThread forever (batch frozen at "starting...").
            # v0.05 — Fix (owner report: "fails to digest and process the
            # new link... tried with different LLMs"): when the server is
            # down the old code just aborted with one cryptic log line —
            # switching models can NEVER fix a dead server, yet the natural
            # user reaction is to try other models. Now we AUTO-START
            # 'ollama serve' detached, poll until it answers, and only
            # abort (with a clear, actionable message) if it never comes up.
            try:
                _llm_client.call_with_timeout(ollama_client.list, 15)
            except Exception as e:
                self.log_message.emit(f"❌ Ollama is not running: {e}", "error")
                if not self._autostart_ollama(ollama_base, ollama_client):
                    self.finished_signal.emit(False, "Ollama not available")
                    return

            # v22 Feature 8: Ollama Model Warmup — send a tiny prompt to pre-load
            # the model into memory. This avoids the long latency spike on the
            # first real analysis call (Ollama lazily loads models on first use).
            # Best-effort — if warmup fails (e.g. model not yet pulled), the
            # batch continues anyway and the LLM will fail per-repo later.
            try:
                self.log_message.emit(f"🔥 Warming up Ollama model '{ollama_model}'...", "info")
                _llm_client.call_with_timeout(
                    ollama_client.chat, 120,
                    model=ollama_model,
                    messages=[{"role": "user", "content": "Hi"}],
                    options={"num_predict": 1}
                )
                self.log_message.emit("✅ Model warmed up", "success")
            except Exception as warmup_err:
                # v30 — Fix (model adaptation): the configured model is gone or
                # broken (user pulled a NEW model and retired the old one).
                # Instead of failing on EVERY repo with a modal, look at what
                # Ollama actually has:
                #   - exactly one model available  -> auto-switch to it, persist
                #   - several models, GUI          -> first per-repo dialog
                #                                     choice persists batch-wide
                #   - several models, HEADLESS/CLI -> v0.07.2: ask the console
                #     host (model_prompt_callback → interactive menu) or
                #     auto-pick the best stand-in. The OLD code just logged
                #     "the first LLM-failure dialog lets you pick one" — a
                #     promise no headless run could keep: every repo then
                #     silently degraded to fallback notes (owner report:
                #     "Model wasn't found = failed cli").
                try:
                    self.log_message.emit(
                        f"⚠️ Model warmup failed: {warmup_err}", "warning"
                    )
                    try:
                        available = _llm_client.list_models_with_timeout(ollama_client, 15)
                    except Exception:
                        available = []
                    if ollama_model not in available and len(available) == 1:
                        new_model = available[0]
                        self.log_message.emit(
                            f"🔄 Configured model '{ollama_model}' not found — Ollama has only "
                            f"'{new_model}'. Auto-switching to it (saved to Settings).",
                            "success"
                        )
                        ollama_model = new_model
                        self._apply_model_choice(new_model)
                    elif available and ollama_model not in available:
                        # Resolve a stand-in BEFORE the batch starts so no
                        # repo is ever processed with a known-missing model.
                        orig_model = ollama_model
                        choice = None
                        _cb = getattr(self, "model_prompt_callback", None)
                        if self._headless and callable(_cb):
                            try:
                                choice = _cb(ollama_model, list(available)) or None
                            except Exception:
                                choice = None
                        if choice and choice in available:
                            ollama_model = choice
                            self._apply_model_choice(choice)
                            self.log_message.emit(
                                f"🔄 Switched to '{choice}' (your choice — applied to the "
                                f"rest of the batch and saved).",
                                "success"
                            )
                            # Verify the stand-in actually warms up; if not,
                            # fall through to auto-pick below.
                            try:
                                _llm_client.call_with_timeout(
                                    ollama_client.chat, 120,
                                    model=ollama_model,
                                    messages=[{"role": "user", "content": "Hi"}],
                                    options={"num_predict": 1}
                                )
                                self.log_message.emit(
                                    f"✅ Replacement model '{ollama_model}' warmed up",
                                    "success"
                                )
                            except Exception:
                                choice = None
                                self.log_message.emit(
                                    f"⚠️ '{ollama_model}' also failed to warm up — "
                                    f"trying the best available stand-in…",
                                    "warning"
                                )
                        if not choice and self._headless:
                            # No console / user declined / stand-in failed:
                            # auto-pick the best available model rather than
                            # letting the whole batch degrade to fallbacks.
                            best = self._pick_best_model(orig_model, available)
                            if best and best != ollama_model:
                                ollama_model = best
                                self._apply_model_choice(best)
                                self.log_message.emit(
                                    f"🔄 Auto-selected '{best}' as the closest installed "
                                    f"stand-in for '{orig_model}' "
                                    f"(applied to the rest of the batch and saved). "
                                    f"Pull the original with: ollama pull <model>",
                                    "warning"
                                )
                        elif not self._headless:
                            self.log_message.emit(
                                f"⚠️ Configured model '{ollama_model}' not in Ollama's list. "
                                f"Available: {', '.join(available)}. The first LLM-failure "
                                f"dialog lets you pick one — your choice now applies to the "
                                f"rest of the batch and is saved.",
                                "warning"
                            )
                except Exception:
                    pass
        elif llm_provider == 'llamacpp':
            # v0.15.0 — llama.cpp engine detection: probe the configured
            # URL; when nothing answers there, SCAN the common llama-server
            # ports once (this is the worker thread — blocking is fine) and
            # switch to whatever is positively identified, exactly like the
            # Ollama single-model auto-switch. A server that is definitively
            # absent aborts the batch with a clear, actionable message
            # (same policy as "Ollama not available") — 100 per-link
            # failures against a known-dead endpoint help nobody.
            llama_url = (self.config.get('llamacpp_api_url', '')
                         or _llm_client.LLAMACPP_DEFAULT_BASE)
            llama_key = self.config.get('llamacpp_api_key', '')
            probe = _llm_client.probe_llamacpp(llama_url, llama_key)
            if not probe.get('found'):
                scan = _llm_client.detect_llamacpp(llama_key)
                if scan and scan.get('base_url'):
                    new_url = scan['base_url'] + '/v1'
                    self.log_message.emit(
                        f"🔄 llama.cpp not at {llama_url} — detected at "
                        f"{scan['base_url']} (switching for this batch "
                        "and saving to Settings).", "warning")
                    llama_url = new_url
                    self.config['llamacpp_api_url'] = new_url
                    probe = scan
            if not probe.get('found'):
                self.log_message.emit(
                    "❌ No llama.cpp server detected (no llama-server "
                    "process, nothing on the common ports). Start it with: "
                    "llama-server -m <model>.gguf --port 8080\n"
                    "   (the app auto-detects llama.cpp at launch and here "
                    "in Settings → 🧠 LLM → 🔍 Detect, or switch "
                    "providers).", "error")
                self.finished_signal.emit(
                    False, "llama.cpp server not detected")
                return
            # "its model detected automatically": an empty llamacpp_model
            # is replaced by what the server actually serves and the choice
            # is persisted — the Ollama warmup-fallback behavior, ported.
            llama_model = _llm_client.resolve_llamacpp_model(
                self.config, probe.get('models'),
                probe.get('props_model'))
            if not llama_model:
                self.log_message.emit(
                    "❌ llama.cpp server detected but no model is loaded "
                    "(start llama-server with -m <model>.gguf).", "error")
                self.finished_signal.emit(
                    False, "llama.cpp has no model loaded")
                return
            configured = str(
                self.config.get('llamacpp_model', '') or '').strip()
            if llama_model != configured:
                self.log_message.emit(
                    f"🔄 llama.cpp model auto-detected: '{llama_model}'"
                    + (f" (was '{configured}')" if configured else "")
                    + " — saved to Settings.", "success")
                self._apply_model_choice(llama_model)
            elif probe.get('models') and llama_model.lower() not in {
                    n.lower() for n in probe['models']}:
                self.log_message.emit(
                    f"⚠️ '{llama_model}' is not in the server's /v1/models "
                    f"list ({', '.join(probe['models'][:5])}) — llama-server "
                    "serves the loaded model; trying anyway.", "warning")
            if probe.get('ready') is False:
                self.log_message.emit(
                    "⏳ llama.cpp is still loading the model — first calls "
                    "may wait or fail; the batch proceeds.", "warning")
            self.log_message.emit(
                f"🦙 Using llama.cpp server {probe['base_url']} · model "
                f"'{llama_model}'", "info")
            # ollama_model carries the model name for the retry/fallback
            # messages (the cloud branch does the same with cloud_model).
            ollama_model = llama_model
        else:
            # Cloud API (v0.23.0 — TWO wire formats: the URL decides —
            # api.anthropic.com speaks the Claude Messages API, everything
            # else is OpenAI-compatible: llama.cpp server, vLLM, LM Studio
            # or a cloud API). No warmup, but log the selection so the user
            # sees which backend is being used, then run the /v1/models
            # pre-flight check: warn (never block) when the endpoint is
            # unreachable or the configured model is not listed —
            # single-model llama.cpp builds and some proxies legitimately
            # hide /models.
            cloud_model = self.config.get('cloud_model', 'gpt-4o-mini')
            cloud_url = self.config.get('cloud_api_url', 'https://api.openai.com/v1')
            cloud_key = self.config.get('cloud_api_key', '')
            _cloud_flavor = ("Claude" if _llm_client.is_anthropic_url(cloud_url)
                             else "OpenAI-compatible")
            self.log_message.emit(
                f"☁️ Using Cloud API ({_cloud_flavor}): {cloud_url} / "
                f"model '{cloud_model}'",
                "info"
            )
            _models_cfg = (self.config.get('models') or {})
            _checks = [('model', str(cloud_model or '').strip())]
            for _t in ('classify', 'analyze'):
                _m = str(_models_cfg.get(_t) or '').strip()
                if _m:
                    _checks.append((f"models.{_t}", _m))
            try:
                try:
                    # v0.23.0 — the pre-flight speaks both wire formats too.
                    if _llm_client.is_anthropic_url(cloud_url):
                        _names = _llm_client.anthropic_list_models(
                            cloud_url, cloud_key, 15)
                    else:
                        _names = _llm_client.openai_list_models(
                            cloud_url, cloud_key, 15)
                except _llm_client.CloudLLMError as _pf_err:
                    self.log_message.emit(
                        f"ℹ️ /models pre-flight unavailable ({_pf_err}) — "
                        "skipping the model check; the batch proceeds.",
                        "info")
                    _names = None
                if _names is not None:
                    _lowered = {n.lower() for n in _names}
                    _missing = [(label, m) for label, m in _checks
                                if m and m.lower() not in _lowered]
                    if _missing:
                        for _label, _m in _missing:
                            self.log_message.emit(
                                f"⚠️ Endpoint pre-flight: '{_m}' "
                                f"({_label}) is NOT in the /models list "
                                f"({len(_names)} listed). Servers that "
                                "load models on demand may still work — "
                                "the run continues; per-link failures "
                                "will name the model if it is wrong.",
                                "warning")
                    else:
                        self.log_message.emit(
                            f"✅ Endpoint pre-flight: {len(_names)} "
                            "model(s) listed; every configured model is "
                            "available.", "success")
            except Exception as _pf_err:
                self.log_message.emit(
                    f"⚠️ Endpoint pre-flight failed: {_pf_err} — the "
                    "batch proceeds; per-link errors will surface any "
                    "real problem.", "warning")
            # Set ollama_model to the cloud model so _llm_analyze's retry
            # fallback messages reference the right model name.
            ollama_model = cloud_model

        # v30 — Fix (CacheDB leak): created AFTER the Ollama early-return so
        # the "Ollama not available" exit can no longer leak the sqlite handle.
        # v0.09.5 — Phase 0 (dry-run): a dry-run batch reads the REAL cache
        # (through a throwaway copy) so its numbers are truthful, but every
        # write lands in the copy — no processed marks, 404 strikes or
        # retry-queue changes can leak out of a dry-run and make the next
        # REAL run skip links.
        if _dryrun.is_enabled():
            cache = CacheDB(db_path=_dryrun.shadow_cache_path(
                os.path.join(APP_DIR, 'cache.db')))
        else:
            cache = CacheDB()

        # v0.10.0 — Phase 1 (note state): one connection for the whole batch.
        # A dry-run NEVER touches the real cache.db, so it gets none — a
        # rehearsal must not record bookkeeping either.
        note_state_db = None
        if not _dryrun.is_enabled():
            try:
                note_state_db = _note_state.NoteStateDB()
            except Exception:
                note_state_db = None  # bookkeeping must never break a run

        # v0.08 — 404 QUARANTINE: load the CONFIRMED-dead set ONCE (fail_count
        # >= threshold in a PREVIOUS session). Every dead link in this batch
        # is skipped below BEFORE any GitHub API call — this is the fix for
        # the owner's report that deleted repos kept being re-found, re-404'd
        # and re-logged on every run (the old code only filtered the
        # bot-queue path; Telethon channel fetch and import files funneled
        # straight into get_repo → 404 → log spam every single session).
        # v0.09 (merge): the threshold is the CONFIGURED one
        # (notfound_strike_threshold — Settings → Dashboard spinbox, CLI
        # --strikes N, config.json; default 3, min 2).
        _dead_threshold = dead_link_threshold(self.config)
        dead_urls = cache.get_dead_url_set(_dead_threshold)
        dead_skipped = 0

        # Build vault index for URL-based dedup (ground truth)
        vault_path = self.config.get('vault_path', '')
        if vault_path:
            self._vault_index = VaultIndex(vault_path)
            # Fix: rebuild() calls log_signal.emit(), so pass the signal directly
            self._vault_index.rebuild(log_signal=self.log_message)

        # v0.20.0 — missing-repo backfill: repos that 404'd in earlier
        # versions (strikes below the threshold) get their _missing note
        # NOW, so they stop counting as pending in every queue view and
        # never burn another GitHub API call (the owner's "8-9 github
        # addresses that are 404 always count as remaining").
        if ((self.config or {}).get('pipelines') or {}).get('github', True) \
                and vault_path:
            try:
                self._backfill_missing_notes(cache, vault_path,
                                             _dead_threshold)
            except Exception as e:
                self.log_message.emit(
                    f"⚠️ Missing-repo backfill skipped: {e}", "warning")

        # v0.10.0 — Phase 1 (note state): ONE-TIME silent baseline of the
        # GitHub vault (SPEC §4.4: "the first run after this feature ships
        # records a baseline silently").
        # v0.12.0 — Phase 3: this is now the FULL start-of-run §4.4 pass for
        # BOTH vaults — baseline-if-empty, then detect + act: the owner's
        # moves become corrections (front-matter updated, category locked,
        # correction logged), deleted notes are dismissed (never re-added),
        # edits/duplicates/unmapped are report-only. A dry-run detects and
        # logs, never records (run_start_check enforces that itself).
        self._note_state_run = {}
        _pipes_cfg = self.config.get('pipelines') or {}
        _github_on = _pipes_cfg.get('github', True)
        _websites_on = _pipes_cfg.get('websites', False)
        if _github_on and vault_path:
            try:
                self._note_state_run['github'] = _note_state.run_start_check(
                    _note_state.VAULT_GITHUB, vault_path, db=note_state_db,
                    log=self.log_message.emit)
            except Exception:
                self._note_state_run['github'] = None  # never break a run
        if _websites_on:
            _web_vault = (self.config.get('website_vault_path') or '').strip()
            if _web_vault:
                _web_taxonomy = None
                try:
                    from gitcurator.core.taxonomy import \
                        load_taxonomy_from_config
                    _web_taxonomy = load_taxonomy_from_config(self.config)
                except Exception as exc:
                    self.log_message.emit(
                        f"⚠️ Note state: websites taxonomy unavailable "
                        f"({exc}) — websites vault check skipped", "warning")
                if _web_taxonomy is not None:
                    try:
                        self._note_state_run['websites'] = \
                            _note_state.run_start_check(
                                _note_state.VAULT_WEBSITES, _web_vault,
                                db=note_state_db, taxonomy=_web_taxonomy,
                                log=self.log_message.emit)
                    except Exception:
                        self._note_state_run['websites'] = None

        # v0.12.0 — Phase 3: the dismissed list for the GitHub loop (notes
        # the owner deleted — §4.4 "never re-add"). Checked per-URL before
        # any processing work below.
        _dismissed_github = set()
        if note_state_db is not None:
            try:
                _dismissed_github = note_state_db.dismissed_set(
                    _note_state.VAULT_GITHUB)
            except Exception:
                _dismissed_github = set()
        dismissed_skipped = 0
        self._dismissed_skipped = 0

        # v22 Feature 6: Batch Undo — snapshot the vault BEFORE processing so
        # we can compute the list of NEW files written by this batch and let
        # the user undo the batch (delete the new files) from the Dashboard.
        # The snapshot is a list of .md file paths that already exist.
        batch_files = set()
        if vault_path and os.path.isdir(vault_path):
            try:
                for root, dirs, files in os.walk(vault_path):
                    # Skip the same non-note folders the vault index skips
                    if any(skip in root for skip in ['.obsidian', 'attachments']):
                        continue
                    for f in files:
                        if f.endswith('.md'):
                            batch_files.add(os.path.join(root, f))
            except Exception:
                pass  # best-effort — undo just won't work this run

        for url in urls:
            if not self.is_running:
                break
            self._current_position += 1  # increment for EVERY URL (including skips)
            self.status_updated.emit(url)
            self.progress_updated.emit(self._current_position, self.total)

            # v23 — Phase 2: mark as processing (manifest is source of truth)
            if self.link_tracker:
                try:
                    self.link_tracker.mark_processing(url)
                except Exception:
                    pass  # best-effort — never break the batch

            # Check GitHub rate limit (v25 pre-flight: threshold raised from
            # 10 to 50 so we always pause with a safe buffer before hitting
            # the hard limit. The wait uses the *exact* reset time from the
            # GitHub response so we resume the moment the limit clears.)
            try:
                rate_limit = g.get_rate_limit()
                remaining = rate_limit.core.remaining
                if remaining < 50:
                    reset_time = rate_limit.core.reset
                    from datetime import timezone
                    reset_local = reset_time.replace(tzinfo=timezone.utc).astimezone()
                    wait_seconds = (reset_time - datetime.now(timezone.utc)).total_seconds()
                    if wait_seconds > 0 and wait_seconds < 3700:  # less than ~1 hour
                        self.log_message.emit(
                            f"⏳ GitHub rate limit reached. Pausing until {reset_local.strftime('%H:%M')} "
                            f"({wait_seconds/60:.1f} minutes, {remaining} remaining)...",
                            "warning"
                        )
                        # Sleep in small chunks so Stop is responsive
                        end_time = time.time() + wait_seconds + 5
                        while time.time() < end_time and self.is_running:
                            time.sleep(min(5, end_time - time.time()))
                        if not self.is_running:
                            break
                        self.log_message.emit("✅ Rate limit wait complete — resuming.", "success")
                    else:
                        self.log_message.emit(
                            f"⚠️ GitHub rate limit low ({remaining} remaining). Reset at {reset_local.strftime('%H:%M')}. Continuing anyway...",
                            "warning"
                        )
                elif remaining % 50 == 0:
                    self.log_message.emit(f"📊 GitHub API: {remaining} requests remaining", "info")
            except Exception:
                pass  # rate limit check is best-effort

            try:
                url = clean_url(url)
                if not url.startswith("https://github.com/"):
                    self.log_message.emit(f"Skipping non-GitHub URL: {url}", "warning")
                    # v23 — Phase 2: mark as skipped (defensive — should not
                    # happen after _fetch_from_import filtering, but be safe)
                    if self.link_tracker:
                        try:
                            self.link_tracker.mark_skipped(url, "non-GitHub URL")
                        except Exception:
                            pass
                    # v26 — Fix 1: emit progress on skip so the bar repaints
                    # (otherwise it appears frozen on the previous URL's status).
                    self.progress_updated.emit(self._current_position, self.total)
                    continue

                # v0.08 — 404 QUARANTINE skip: confirmed-dead links (3+
                # consecutive 404s across sessions) never reach the GitHub
                # API. Counted silently; ONE aggregate line is logged in the
                # batch summary (per-URL logging here is exactly the spam the
                # owner asked to remove).
                if normalize_url(url) in dead_urls:
                    dead_skipped += 1
                    if self.link_tracker:
                        try:
                            self.link_tracker.mark_skipped(url, "404 quarantine (confirmed dead)")
                        except Exception:
                            pass
                    self.progress_updated.emit(self._current_position, self.total)
                    continue

                # v0.12.0 — Phase 3 (§4.4 "deleted"): a URL whose note the
                # owner deleted is NEVER re-added. Checked here, BEFORE any
                # GitHub API call — the note is gone, so the vault-index
                # dedupe below cannot know about it.
                if _dismissed_github and \
                        _note_state.normalize_url(url) in _dismissed_github:
                    self.log_message.emit(
                        f"🚫 Dismissed (you deleted its note): {url}", "info")
                    dismissed_skipped += 1
                    if self.link_tracker:
                        try:
                            self.link_tracker.mark_skipped(
                                url, "dismissed (note deleted)")
                        except Exception:
                            pass
                    self.progress_updated.emit(self._current_position,
                                               self.total)
                    continue
                self._dismissed_skipped = dismissed_skipped

                parts = url.replace("https://github.com/", "").split("/")
                if len(parts) < 2:
                    self.log_message.emit(f"Invalid GitHub URL: {url}", "warning")
                    if self.link_tracker:
                        try:
                            self.link_tracker.mark_skipped(url, "invalid GitHub URL")
                        except Exception:
                            pass
                    # v26 — Fix 1: emit progress on skip.
                    self.progress_updated.emit(self._current_position, self.total)
                    continue
                owner, repo_name = parts[0], parts[1]

                try:
                    repo = g.get_repo(f"{owner}/{repo_name}")
                    repo_id = repo.id
                except GithubException as e:
                    # v25 pre-flight: GitHub returns 403 when rate-limited.
                    # We catch it specifically, wait the full hour, then
                    # retry THIS repo (no link lost). Other 403s (e.g.
                    # repo blocked by abuse detection) fall through to the
                    # generic error path so the link is marked failed and
                    # retried later.
                    if e.status == 403 and 'rate limit' in str(e).lower():
                        self.log_message.emit(
                            "⏳ GitHub rate limit reached. Waiting 3600s (1 hour) for reset...",
                            "warning"
                        )
                        # Sleep in small chunks so Stop stays responsive
                        _rl_end = time.time() + 3605
                        while time.time() < _rl_end and self.is_running:
                            time.sleep(min(5, _rl_end - time.time()))
                        if not self.is_running:
                            break
                        try:
                            # Retry this repo after the wait
                            repo = g.get_repo(f"{owner}/{repo_name}")
                            repo_id = repo.id
                            self.log_message.emit(
                                "✅ Rate limit wait complete — resuming with this repo.",
                                "success"
                            )
                        except GithubException as e2:
                            if e2.status == 404:
                                _n404 = cache.record_404(url, "404 Not Found")
                                # v0.20.0 — a GitHub 404 is definitive
                                # (deleted/private): the _missing note +
                                # immediate confirmation stop the link
                                # from ever counting as pending again.
                                self._record_missing_repo(
                                    url, owner, repo_name, _n404, cache,
                                    _dead_threshold)
                                self.log_message.emit(
                                    f"🗑️ Repo not found (404) — missing-repo "
                                    f"note written, never counted again: {url}", "warning")
                                if self.link_tracker:
                                    try:
                                        self.link_tracker.mark_skipped(url, "404 Not Found — missing-repo note")
                                    except Exception:
                                        pass
                            else:
                                self.log_message.emit(f"GitHub API error after rate-limit wait: {e2}", "error")
                                if self.link_tracker:
                                    try:
                                        self.link_tracker.mark_failed(url, f"GitHub API: {e2}")
                                    except Exception:
                                        pass
                            self.progress_updated.emit(self._current_position, self.total)
                            continue
                    elif e.status == 401 and github_token:
                        # v32.1 — Fix (bad-credentials spam): the saved GitHub
                        # token was rejected (expired / revoked / rotated).
                        # Instead of failing EVERY repo with a raw 401 JSON
                        # blob, drop the token for the rest of the batch
                        # (anonymous access, 60 req/h), retry THIS repo, and
                        # tell the user exactly how to fix it. Logged once.
                        if not getattr(self, '_gh_token_dropped', False):
                            self._gh_token_dropped = True
                            self.log_message.emit(
                                "🔑 GitHub token rejected (401 Bad credentials) — it is invalid, expired, or was rotated.",
                                "error",
                            )
                            self.log_message.emit(
                                "   Continuing this batch anonymously (60 requests/hour). "
                                "Fix: Settings → Credentials → paste a fresh token "
                                "(github.com/settings/tokens) → 'Test GitHub Token'.",
                                "warning",
                            )
                            github_token = None  # never re-enter this branch
                        try:
                            g = Github()  # anonymous client from here on
                            repo = g.get_repo(f"{owner}/{repo_name}")
                            repo_id = repo.id
                        except GithubException as e2:
                            self.log_message.emit(f"GitHub API error: {e2}", "error")
                            if self.link_tracker:
                                try:
                                    self.link_tracker.mark_failed(url, f"GitHub API: {e2}")
                                except Exception:
                                    pass
                            self.progress_updated.emit(self._current_position, self.total)
                            continue
                    elif e.status == 404:
                        # v0.08 — attempt-counted decommission: the counter
                        # lives in cache.db so it accumulates ACROSS SESSIONS.
                        # v0.20.0 — the confirmation is now IMMEDIATE: a 404
                        # from /repos/{owner}/{repo} means the repo is deleted
                        # or private, so a _missing placeholder note is
                        # written (its ``source:`` line is the VaultIndex
                        # dedupe key — the link stops counting as pending in
                        # every queue view) and the quarantine row is
                        # confirmed on the spot. The owner's re-check path:
                        # delete the note + reset in the quarantine manager.
                        # The permanent record in _inbox/notfound-links/ is
                        # appended once (the dead-set pre-filter + the note
                        # keep this branch from ever firing twice for the
                        # same URL).
                        _n404 = cache.record_404(url, "404 Not Found")
                        _note_path = self._record_missing_repo(
                            url, owner, repo_name, _n404, cache,
                            _dead_threshold)
                        self.log_message.emit(
                            f"🗑️ Repo not found (404) — missing-repo note "
                            f"written, never counted again: {url}"
                            + (f" → {os.path.basename(_note_path)}"
                               if _note_path else ""), "warning")
                        # Write to _inbox/notfound-links/ ONCE
                        try:
                            vault_path = self.config.get('vault_path', '')
                            if vault_path:
                                nf_folder = os.path.join(vault_path, "_inbox", "notfound-links")
                                # v0.09.5 — Phase 0 (dry-run): recorded, not performed.
                                _dryrun.makedirs(nf_folder, exist_ok=True)
                                nf_path = os.path.join(nf_folder, "notfound_links.md")
                                # v0.09.5 — Phase 0 (dry-run): recorded, not performed.
                                _dryrun.append_text(nf_path, f"| {datetime.now().strftime('%Y-%m-%d')} | {url} | 404 Not Found (missing-repo note) |\n")
                        except Exception:
                            pass
                        # Mark as skipped (not failed — it's deliberately excluded)
                        if self.link_tracker:
                            try:
                                self.link_tracker.mark_skipped(url, "404 Not Found — missing-repo note")
                            except Exception:
                                pass
                        self.progress_updated.emit(self._current_position, self.total)
                        continue
                    else:
                        self.log_message.emit(f"GitHub API error: {e}", "error")
                        # v23 — Phase 2: GitHub-level failure (repo gone / API error)
                        if self.link_tracker:
                            try:
                                self.link_tracker.mark_failed(url, f"GitHub API: {e}")
                            except Exception:
                                pass
                        # v26 — Fix 1: emit progress on skip.
                        self.progress_updated.emit(self._current_position, self.total)
                        continue

                # v0.09 (merge fix) — 404-quarantine RESET on success: the
                # repo EXISTS again (restored, renamed back, or the earlier
                # 404s were transient noise), so its attempt counter starts
                # fresh. This restores the CONSECUTIVE-miss semantics from
                # the v0.07 strike design: the v0.08 quarantine counted
                # attempts without ever resetting on success, so a repo with
                # two stale strikes from months ago could be quarantined by
                # one more transient miss. All get_repo success paths
                # (direct, post-rate-limit retry, anonymous post-401 retry)
                # converge here.
                try:
                    cache.reset_dead_links(url)
                except Exception:
                    pass

                # v0.12.0 — Phase 3 (§4.4 "deleted") — the dismissed check
                # runs earlier, with the dead-link filter above (before any
                # GitHub API call).

                # === DEDUP CHECK (vault index is ground truth) ===
                # 1. Check the vault index FIRST — if the note exists in the
                #    vault, skip (deterministic, no AI).
                # 2. If not in vault but in SQLite cache, the note was deleted
                #    → re-process (self-healing).
                # 3. If not in vault and not in cache → process as new.
                if self._vault_index and self._vault_index.has_url(url):
                    note_path = self._vault_index.get_path(url)
                    self.log_message.emit(
                        f"⏭️ Already in vault: {owner}/{repo_name} → {os.path.basename(note_path)}",
                        "info"
                    )
                    # v23 — Phase 2: mark as skipped (dedup) — note already
                    # exists in the vault from a previous batch.
                    if self.link_tracker:
                        try:
                            self.link_tracker.mark_skipped(url, "already in vault")
                        except Exception:
                            pass
                    # v26 — Fix 1: emit progress on skip so the bar repaints.
                    self.progress_updated.emit(self._current_position, self.total)
                    continue

                if cache.is_duplicate(repo_id):
                    # Check if the note file still exists in the vault.
                    # If it was deleted, remove the stale cache entry and
                    # re-process the repo.
                    if cache.is_note_valid(repo_id):
                        note_path = cache.get_note_path(repo_id)
                        self.log_message.emit(
                            f"⏭️ Already processed (note exists): {url} -> {note_path}",
                            "info"
                        )
                        # Also add to vault index for future runs
                        if self._vault_index:
                            self._vault_index.add_url(url, note_path)
                        # v23 — Phase 2: mark as skipped (dedup) — note
                        # exists from a previous batch.
                        if self.link_tracker:
                            try:
                                self.link_tracker.mark_skipped(url, "already in cache")
                            except Exception:
                                pass
                        # v26 — Fix 1: emit progress on skip.
                        self.progress_updated.emit(self._current_position, self.total)
                        continue
                    else:
                        self.log_message.emit(
                            f"♻️ Note file was deleted, re-processing: {url}",
                            "info"
                        )
                        cache.remove_entry(repo_id)
                        # Fall through to re-process

                # Check if this is a fork — analyze the parent instead
                is_fork = False
                parent_info = ""
                try:
                    if repo.fork:
                        is_fork = True
                        parent = repo.parent
                        parent_info = f"This is a fork of {parent.full_name}. "
                        self.log_message.emit(f"🍴 Fork detected — analyzing parent: {parent.full_name}", "info")
                        # Use the parent repo for analysis
                        repo = parent
                        repo_id = repo.id
                        owner_login = repo.owner.login
                        repo_name = repo.name
                except Exception:
                    pass

                stars = repo.stargazers_count
                forks = repo.forks_count
                description = (repo.description or "") + f"\n\n{parent_info}" if parent_info else (repo.description or "")
                topics = repo.get_topics() if hasattr(repo, 'get_topics') else []
                owner_login = repo.owner.login
                org_name = repo.organization.login if repo.organization else owner_login
                org_rep = self._get_org_reputation(org_name)

                try:
                    commits = repo.get_commits(since=datetime.now() - timedelta(days=90))
                    # Use totalCount instead of iterating (avoids fetching all objects)
                    commit_count = commits.totalCount if hasattr(commits, 'totalCount') else sum(1 for _ in commits)
                except Exception as e:
                    self.log_message.emit(f"   ⚠️ Could not fetch commits: {e}", "warning")
                    commit_count = 0

                # Fetch README content (first 2000 chars) for better LLM context
                readme_content = ""
                try:
                    readme = repo.get_readme()
                    import base64
                    readme_raw = base64.b64decode(readme.content).decode('utf-8', errors='ignore')
                    readme_content = readme_raw[:1500]  # cap at 1500 for faster LLM processing
                    self.log_message.emit(f"   📄 README fetched ({len(readme_raw)} chars)", "info")
                except Exception:
                    self.log_message.emit(f"   ⚠️ No README found", "warning")

                # Start banner download in parallel (thread) while LLM analyzes
                banner_result = [None]
                def _download_banner_thread():
                    vault_path_tmp = self.config.get('vault_path', '')
                    if vault_path_tmp:
                        folder = os.path.join(vault_path_tmp, CATEGORY_FOLDERS.get("Uncategorized", "Uncategorized"))
                        # v0.09.5 — Phase 0 (dry-run): recorded, not performed.
                        _dryrun.makedirs(folder, exist_ok=True)
                        banner_result[0] = self._download_banner(owner_login, repo_name, folder)
                banner_thread = threading.Thread(target=_download_banner_thread, daemon=True)
                banner_thread.start()

                # v30 — Fix (model persistence): re-read the model from
                # self.config on EVERY url. When the user picks a new model
                # in the LLM-failure dialog (or the single-model auto-switch
                # fires), _apply_model_choice mutates this same dict — so the
                # rest of the batch uses the new model instead of re-failing
                # and re-prompting on every single link.
                if llm_provider == 'ollama':
                    ollama_model = self.config.get('ollama', {}).get('model', ollama_model)
                elif llm_provider == 'llamacpp':
                    ollama_model = self.config.get('llamacpp_model', ollama_model)
                else:
                    ollama_model = self.config.get('cloud_model', ollama_model)

                # LLM analysis with README + about_me context
                llm_result = self._llm_analyze(
                    ollama_client, ollama_model,
                    repo_name, description, topics, owner_login, stars, forks,
                    readme_content=readme_content
                )
                summary = llm_result.get("summary", "No summary available.")
                how_it_works = llm_result.get("how_it_works", "No explanation provided.")
                core_value = llm_result.get("core_value", "No core value provided.")
                features = llm_result.get("features", ["Feature 1", "Feature 2"])
                difference = llm_result.get("difference", "No comparison provided.")
                category_guess = llm_result.get("category", "Uncategorized")
                confidence = float(llm_result.get("confidence", 50))
                tags = llm_result.get("tags", [])

                # Lower threshold to 50% — always pick a category, just flag low-confidence
                if confidence < 50:
                    category_key = "Uncategorized"
                else:
                    category_key = category_guess if category_guess in CATEGORY_KEYS else "Uncategorized"

                # Merge GitHub topics with LLM tags (topics are authoritative)
                all_tags = list(tags)
                for t in topics:
                    if t and t not in all_tags:
                        all_tags.append(t)

                # Fetch primary language + all languages from GitHub API
                primary_language = ""
                languages_list = []
                try:
                    primary_language = repo.language or ""
                    if primary_language and primary_language not in all_tags:
                        all_tags.insert(0, primary_language.lower())
                    # Get all languages used
                    langs = repo.get_languages()
                    for lang_name in list(langs.keys())[:5]:
                        if lang_name not in languages_list:
                            languages_list.append(lang_name)
                        # Add language as tag (lowercase)
                        lang_lower = lang_name.lower()
                        if lang_lower not in all_tags:
                            all_tags.append(lang_lower)
                except Exception:
                    pass

                tags = all_tags[:12]  # cap at 12

                # Fetch latest release date
                latest_release_date = ""
                try:
                    releases = repo.get_releases()
                    latest = releases[0] if releases.totalCount > 0 else None
                    if latest:
                        latest_release_date = latest.published_at.strftime("%Y-%m-%d")
                        self.log_message.emit(f"   📦 Latest release: {latest.tag_name} ({latest_release_date})", "info")
                except Exception:
                    pass

                cred_score = self._calculate_credibility(org_rep, stars, commit_count, repo)

                vault_path = self.config.get('vault_path', '')
                if not vault_path:
                    self.log_message.emit("Vault path not configured", "error")
                    # v30 — Fix (CacheDB leak): close the handle before the
                    # early return (this path previously leaked it).
                    cache.close()
                    self.finished_signal.emit(False, "Vault path missing")
                    return

                folder_path = os.path.join(vault_path, CATEGORY_FOLDERS.get(category_key, "Uncategorized"))
                # v0.09.5 — Phase 0 (dry-run): recorded, not performed.
                _dryrun.makedirs(folder_path, exist_ok=True)

                # Review queue: low-confidence notes go to _review/
                review_mode = confidence < 60
                if review_mode:
                    review_folder = os.path.join(vault_path, "_review")
                    # v0.09.5 — Phase 0 (dry-run): recorded, not performed.
                    _dryrun.makedirs(review_folder, exist_ok=True)
                    folder_path = review_folder
                    self.log_message.emit(f"⚠️ Low confidence ({confidence}%) — note sent to _review/ folder", "warning")

                # v22 Feature 5: Note Quality Score — flag low-quality notes for
                # review. If the note is low-quality AND not already in the
                # review folder (low-confidence), move it to _review/.
                quality_issues = []
                if len(summary) < 50:
                    quality_issues.append("summary too short")
                if isinstance(features, list) and len(features) < 3:
                    quality_issues.append("fewer than 3 features")
                if confidence < 30:
                    quality_issues.append("low confidence")
                if category_key == "Uncategorized":
                    quality_issues.append("uncategorized")
                is_low_quality = len(quality_issues) > 0
                if is_low_quality and not review_mode:
                    review_folder = os.path.join(vault_path, "_review")
                    # v0.09.5 — Phase 0 (dry-run): recorded, not performed.
                    _dryrun.makedirs(review_folder, exist_ok=True)
                    folder_path = review_folder
                    self.log_message.emit(
                        f"⚠️ Low quality ({', '.join(quality_issues)}) — note sent to _review/ folder",
                        "warning"
                    )

                # Wait for banner download to finish (started in parallel above)
                banner_thread.join(timeout=15)
                banner_path = banner_result[0]

                # If banner was downloaded to Uncategorized but category differs, move it
                if banner_path and category_key != "Uncategorized":
                    uncategorized_folder = os.path.join(vault_path, CATEGORY_FOLDERS.get("Uncategorized", "Uncategorized"))
                    if uncategorized_folder in banner_path:
                        import shutil
                        new_banner = os.path.join(folder_path, os.path.basename(banner_path))
                        try:
                            # v0.09.5 — Phase 0 (dry-run): recorded, not performed.
                            _dryrun.move(banner_path, new_banner)
                            banner_path = new_banner
                        except Exception:
                            pass

                if banner_path:
                    self.log_message.emit(f"   ✅ Banner: {os.path.basename(banner_path)}", "success")
                else:
                    # v26 — Fix 5: a missing banner is NOT a problem — the
                    # note is still written and the banner can be retried
                    # later. Log as 'info' (not 'warning') so the user
                    # doesn't think something went wrong.
                    self.log_message.emit(
                        f"   ⚠️ No banner (will retry later) — note still written",
                        "info"
                    )

                short_summary = llm_result.get("short_summary", "")

                note_content = self._build_note(
                    url=url, repo_name=repo_name, owner=owner_login,
                    org_name=org_name, stars=stars, forks=forks,
                    commit_count=commit_count, cred_score=cred_score,
                    org_rep=org_rep, summary=summary, tags=tags,
                    category_key=category_key, confidence=confidence,
                    how_it_works=how_it_works, core_value=core_value,
                    features=features, difference=difference,
                    banner_path=banner_path,
                    primary_language=primary_language,
                    languages=languages_list,
                    short_summary=short_summary,
                    latest_release_date=latest_release_date,
                    quality_issues=quality_issues,
                    is_low_quality=is_low_quality
                )

                # v30 — Fix (atomic writes + collision handling): filename via
                # storage helpers; the note is written to a temp file in the
                # same folder then os.replace()d — a crash/disk-full mid-write
                # can never leave a truncated note behind that the cache
                # would then record as processed.
                filename = _storage.build_note_filename(repo_name, category_key, tags)
                full_path = _storage.unique_path(os.path.join(folder_path, filename))

                try:
                    _storage.atomic_write_text(full_path, note_content)
                except OSError as write_err:
                    if "No space" in str(write_err) or "disk" in str(write_err).lower():
                        self.log_message.emit(f"💾 DISK FULL! Cannot write: {full_path}", "error")
                        # v30 — Fix (headless hang-bomb): the GUI-less run has
                        # no dialog to clear _disk_full_paused — the old code
                        # spun on sleep(1) FOREVER. Skip the repo, record it in
                        # the retry queue, keep the batch moving.
                        if self._headless:
                            self.log_message.emit(
                                "⏭️ Headless mode: skipping this repo (disk full). "
                                "Free space, then use 'Retry Failed' in the GUI.",
                                "error"
                            )
                            try:
                                cache.add_failed(url, f"disk full: {write_err}")
                            except Exception:
                                pass
                            if self.link_tracker:
                                try:
                                    self.link_tracker.mark_failed(url, f"disk full: {write_err}")
                                except Exception:
                                    pass
                            self.progress_updated.emit(self._current_position, self.total)
                            continue
                        self.disk_full_signal.emit(full_path)
                        # Wait for resume (is_running stays True, but we set a flag)
                        self._disk_full_paused = True
                        while self._disk_full_paused and self.is_running:
                            time.sleep(1)
                        if not self.is_running:
                            break
                        # Retry the write (wrapped in try/except — disk may
                        # still be full)
                        try:
                            _storage.atomic_write_text(full_path, note_content)
                        except OSError:
                            self.log_message.emit(f"❌ Disk still full — skipping {url}", "error")
                            # v26 — Fix 1: emit progress on skip.
                            self.progress_updated.emit(self._current_position, self.total)
                            continue
                    else:
                        raise

                cache.add_processed(repo_id, url, owner, repo_name, full_path, category_key)
                # v0.10.0 — Phase 1 (note state): record the note the app
                # just WROTE (identity = source URL) so the baseline stays
                # fresh and Phase 3's comparison treats it as known-good.
                # The fingerprint comes from the in-memory content — no
                # re-read, and dry-runs never reach here (no connection).
                if note_state_db is not None:
                    try:
                        note_state_db.record_note(
                            _note_state.VAULT_GITHUB, url, full_path,
                            content=note_content, category=category_key)
                    except Exception:
                        pass  # best-effort by design
                # v22 Feature 4: Mark any previous failure for this URL as resolved
                # so it no longer shows up in the "Retry Failed" queue.
                cache.mark_failed_resolved(url)
                # Update the vault index incrementally so the next URL in the
                # batch can dedup against this note.
                if self._vault_index:
                    self._vault_index.add_url(url, full_path)
                self.log_message.emit(f"✅ Processed: {owner_login}/{repo_name} -> {full_path}", "success")
                self.processed += 1

                # v23 — Phase 2: mark as processed (note written + cache updated)
                if self.link_tracker:
                    try:
                        self.link_tracker.mark_processed(url, full_path)
                    except Exception:
                        pass

                # Track for the summary log
                if not hasattr(self, '_processed_log'):
                    self._processed_log = []
                self._processed_log.append({
                    'url': url,
                    'repo': f"{owner_login}/{repo_name}",
                    'category': category_key,
                    'note_path': full_path,
                    'banner': bool(banner_path),
                    'credibility': cred_score,
                })

            except Exception as e:
                self.log_message.emit(f"❌ Error processing {url}: {e}", "error")
                # v22 Feature 4: Record the failure so the user can retry later
                # via the "🔄 Retry Failed" button. Best-effort.
                try:
                    cache.add_failed(url, str(e))
                except Exception:
                    pass
                # v23 — Phase 2: mark as failed in the manifest so Phase 5
                # verification blocks the bot-queue mark-read and Phase 4
                # reconciliation can surface it on next launch.
                if self.link_tracker:
                    try:
                        self.link_tracker.mark_failed(url, str(e))
                    except Exception:
                        pass
                # v26 — Fix 1: emit progress on skip.
                self.progress_updated.emit(self._current_position, self.total)
                continue

            time.sleep(self.config.get('delay_between_api_calls', 0.5))
            # v25 pre-flight: For large batches (>50 links), add an extra
            # 1.5s delay between repos so we never hit the GitHub rate limit
            # mid-batch. 5000 requests/hour ÷ 1.5s/repo = ~333 repos/hour, so
            # even a 300-link batch finishes well under the limit.
            if self.total > 50:
                time.sleep(self.config.get('large_batch_extra_delay', 1.5))

        # v0.11.0 — Phase 2: the Websites pipeline runs AFTER the GitHub
        # loop, on this batch's non-GitHub links plus any fetch-retries
        # whose backoff elapsed. All logic lives in core/website_pipeline;
        # this is the thin wiring hook (non-negotiable #8). Runs even when
        # the GitHub loop above was skipped (pipelines.github off).
        _website_summary = self._run_website_phase(
            cache, note_state_db, ollama_client, ollama_model)

        cache.close()
        # v0.10.0 — Phase 1 (note state): close the batch's record connection
        # alongside the cache (best-effort; None in a dry-run).
        if note_state_db is not None:
            try:
                note_state_db.close()
            except Exception:
                pass

        # v22 Feature 6: Batch Undo — compute the list of NEW .md files
        # written by this batch (anything in the vault now that wasn't in
        # the pre-batch snapshot). Save to `_undo_last_batch.txt` in the
        # vault root so the user can undo via the Dashboard button.
        # Best-effort: any error is logged but doesn't break the batch.
        try:
            new_files = []
            if vault_path and os.path.isdir(vault_path) and batch_files:
                for root, dirs, files in os.walk(vault_path):
                    if any(skip in root for skip in ['.obsidian', 'attachments']):
                        continue
                    for f in files:
                        if f.endswith('.md'):
                            fpath = os.path.join(root, f)
                            if fpath not in batch_files:
                                new_files.append(fpath)
            if vault_path and os.path.isdir(vault_path):
                undo_path = os.path.join(vault_path, '_undo_last_batch.txt')
                # v0.09.5 — Phase 0 (dry-run): recorded, not performed.
                _dryrun.write_text(undo_path, ''.join(f + '\n' for f in new_files))
                if new_files:
                    self.log_message.emit(
                        f"↩️ Batch undo saved: {len(new_files)} new files can be undone via Dashboard → 'Undo Last Batch'",
                        "info"
                    )
        except Exception as undo_err:
            try:
                self.log_message.emit(f"⚠️ Failed to save batch undo list: {undo_err}", "warning")
            except Exception:
                pass

        # Final progress update to 100%
        self.progress_updated.emit(self.total, self.total)

        # v23 — Phase 3: VERIFICATION — check that every "processed" link has
        # a real note file (>100 bytes) on disk and every "recorded" non-GitHub
        # link is actually in the inbox table. Any link that fails verification
        # is marked "failed" in the manifest so Phase 5 (in MainWindow) blocks
        # the bot-queue mark-read and Phase 4 (on next launch) can surface it.
        report = None  # v25: capture for the final report
        if self.link_tracker:
            try:
                # v0.21.0 — the _inbox tables live in the WEBSITES vault
                # when one is set (v0.20.0 vault separation); verify looks
                # for blocked/recorded rows there too.
                _extra = []
                _wv = (self.config.get('website_vault_path') or '').strip()
                if _wv:
                    _extra.append(os.path.join(_wv, "_inbox"))
                report = self.link_tracker.verify(
                    log_signal=self.log_message, extra_inbox_dirs=_extra)
                if not report["verification_passed"]:
                    self.log_message.emit(
                        f"⚠️ {len(report['failed_links'])} links failed verification — will retry on next run",
                        "warning"
                    )
            except Exception as verify_err:
                self.log_message.emit(
                    f"⚠️ Verification failed (continuing): {verify_err}",
                    "warning"
                )

        # Generate summary txt log
        summary_path = self._generate_summary_log()

        # Generate master index + MOCs (incremental, with timestamps)
        self._generate_master_index()

        # v25 pre-flight: comprehensive final report — saved in the vault
        # root as _processing_report_YYYYMMDD_HHMMSS.md. Always generated
        # (even if some links failed) so the user has a complete audit
        # trail of what was processed, what was skipped, and what needs
        # retry. Includes the LinkTracker verification report when present.
        try:
            report_path = self._generate_final_report(report)
            if report_path:
                self.log_message.emit(f"📊 Final report saved: {report_path}", "success")
        except Exception as final_report_err:
            try:
                self.log_message.emit(f"⚠️ Failed to generate final report: {final_report_err}", "warning")
            except Exception:
                pass

        msg = f"Processed {self.processed} out of {self.total} repos."
        # v0.11.0 — Phase 2: append the websites tally when the phase ran.
        _ws = getattr(self, '_website_summary', None)
        if _ws:
            _c = _ws.get('counters', {})
            msg += (f" Websites: {_c.get('processed', 0)} processed, "
                    f"{_c.get('review', 0)} to review, "
                    f"{_c.get('skipped', 0)} skipped.")
        if summary_path:
            msg += f" Summary log: {summary_path}"
        self.finished_signal.emit(True, msg)
        self.log_message.emit(f"🏁 Done. Processed {self.processed} repos.", "info")
        # v0.08 — ONE aggregate line for quarantined links (per-URL lines were
        # the log spam the owner reported).
        if dead_skipped:
            self.log_message.emit(
                f"🚫 {dead_skipped} dead link(s) skipped — 404 quarantine "
                f"(confirmed after {DEAD_LINK_THRESHOLD} attempts in earlier runs; "
                f"More ▸ View 404 Quarantine to manage).", "info")
        # v0.12.0 — Phase 3: one aggregate line for dismissed URLs (the
        # GitHub notes the owner deleted — never re-added, §4.4).
        if dismissed_skipped:
            self.log_message.emit(
                f"🗑️ {dismissed_skipped} dismissed link(s) skipped — you "
                "deleted their notes (run report lists them).", "info")
        if summary_path:
            self.log_message.emit(f"📝 Summary log saved: {summary_path}", "success")

    def stop(self):
        self.is_running = False


class TestWorker(QThread):
    log_message = pyqtSignal(str, str)        # (msg, level)
    finished_signal = pyqtSignal(str, dict)   # (test_name, result_dict)
    code_requested = pyqtSignal(str)          # "CODE" or "PASSWORD"
    # v0.23.0 — Test Connection modal progress: which subsystem section is
    # being checked / each result as it lands. (section_index is 1-based,
    # mirroring run_local_checks' on_section.)
    section_signal = pyqtSignal(int, str, int)  # (index, title, total)
    result_signal = pyqtSignal(int, dict)       # (section_index, result)

    def __init__(self, fn, test_name: str, *args, **kwargs):
        super().__init__()
        self._fn = fn
        self._test_name = test_name
        self._args = args
        self._kwargs = kwargs
        self._code_event = threading.Event()
        self._code_response = ""

    def provide_code(self, code: str):
        """Called from the GUI thread to deliver the login code/password."""
        self._code_response = code
        self._code_event.set()

    def request_code(self, prompt_type: str = "CODE") -> str:
        """Called from the worker thread. Emits code_requested, then blocks
        until the GUI thread calls provide_code(). Returns the code, or
        empty string if the user cancelled or timed out (5 minutes)."""
        self._code_event.clear()
        self._code_response = ""
        self.code_requested.emit(prompt_type)
        timed_out = not self._code_event.wait(timeout=300)  # 5 minute timeout
        if timed_out:
            # Log the timeout so the worker can handle it
            self.log_message.emit("⏰ Auth code input timed out (5 minutes)", "warning")
        return self._code_response

    def run(self):
        # v0.06 — Fix: catch BaseException, not just Exception. A SystemExit
        # or KeyboardInterrupt raised inside a job used to kill this thread
        # WITHOUT emitting finished_signal, which meant _keep_worker's
        # cleanup never ran and the Telegram lock stayed held forever.
        try:
            result = self._fn(*self._args, **self._kwargs)
            self.finished_signal.emit(self._test_name, result or {})
        except BaseException as e:  # noqa: BLE001 — worker must always signal
            try:
                import traceback as _tb
                self.log_message.emit(
                    f"💥 Worker '{self._test_name}' crashed: "
                    f"{type(e).__name__}: {e}\n{_tb.format_exc()[-600:]}", "error"
                )
            except Exception:
                pass
            self.finished_signal.emit(
                self._test_name,
                {"success": False, "error": f"{type(e).__name__}: {e}"}
            )
