"""WorkerWebsitePhaseMixin — the Websites-pipeline leg of a batch.

Moved verbatim from gitcurator/gui/processing_worker.py at
v0.25.0 (hygiene pass); processing_worker.py composes this mixin
onto the ProcessingWorker shell. Bodies are byte-identical.
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
from gitcurator.core import netctx as _netctx
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


class WorkerWebsitePhaseMixin:
    # ---- moved verbatim; see module docstring ----
    def _run_website_phase(self, cache, note_state_db, ollama_client=None,
                           ollama_model=""):
        """v0.11.0 — Phase 2 thin hook: run the Websites pipeline
        (core/website_pipeline.py) for this batch's non-GitHub links plus
        due fetch-retries. All per-link logic lives in the core module —
        this wires the worker's LLM router, dedupe probe, state DB and
        logging into it. Returns a summary dict (or None when the pipeline
        is off / not configured / nothing to do)."""
        try:
            _pipes = (self.config or {}).get('pipelines') or {}
            if not _pipes.get('websites', False):
                return None
            website_vault = (self.config.get('website_vault_path') or '').strip()
            links = list(getattr(self, '_non_github_urls', []) or [])
            # v0.20.0 — blocked domains at INTAKE (the X fix): these links
            # are already addressed as rows in the _inbox platform tables
            # and never reach the fetcher. The pipeline's own guard is the
            # second layer (due-retries, any other entry path).
            # v0.21.0 — SELF domains (the app's own bot) get the same
            # intake treatment: its auth links (…/auth/?token=…) are never
            # fetched and never noted; the _inbox row (token scrubbed) is
            # the record. Both marked 'blocked' in the manifest so the
            # verification report accounts for them explicitly.
            _blocked = _links.blocked_domains_from_config(self.config)
            _self = _links.self_domains_from_config(self.config)
            if (_blocked or _self) and links:
                _kept, _drop_blocked, _drop_self = [], [], []
                for _u in links:
                    if _blocked and _links.domain_is_blocked(_u, _blocked):
                        _drop_blocked.append(_u)
                    elif _self and _links.domain_is_self(_u, _self):
                        _drop_self.append(_u)
                    else:
                        _kept.append(_u)
                if _drop_blocked:
                    self.log_message.emit(
                        f"🚫 {len(_drop_blocked)} link(s) on blocked domains "
                        f"({', '.join(_blocked)}) — never fetched; the "
                        f"_inbox table keeps the record", "info")
                    if self.link_tracker:
                        for _u in _drop_blocked:
                            try:
                                self.link_tracker.mark_blocked(
                                    _u, "blocked domain")
                            except Exception:
                                pass
                if _drop_self:
                    self.log_message.emit(
                        f"🔒 {len(_drop_self)} link(s) on self domains "
                        f"({', '.join(_self)} — the app's own bot) — never "
                        f"fetched; the _inbox row keeps the record (tokens "
                        f"scrubbed)", "info")
                    if self.link_tracker:
                        for _u in _drop_self:
                            try:
                                self.link_tracker.mark_blocked(
                                    _u, "self domain (the app's own bot)")
                            except Exception:
                                pass
                links = _kept
            if not website_vault:
                if links:
                    self.log_message.emit(
                        "⚠️ Websites pipeline is ON but no Websites vault is "
                        "set (Settings → 📁 Vault) — the non-GitHub links of "
                        "this batch are kept in the manifest only.", "warning")
                return None

            # State DB: the shadow cache during a dry-run (same rule as
            # CacheDB / note_state) so a rehearsal records nothing.
            if _dryrun.is_enabled():
                state = _website_pipeline.WebsiteStateDB(
                    db_path=_dryrun.shadow_cache_path(
                        os.path.join(APP_DIR, 'cache.db')))
            else:
                state = _website_pipeline.WebsiteStateDB()

            # The websites vault gets its OWN VaultIndex keyed with the
            # website normalizer (query params are part of the identity).
            index = VaultIndex(
                website_vault,
                normalizer=_links.normalize_website_url)
            index.rebuild(log_signal=self.log_message)

            # LLM router — v0.13.0 Phase 4: both providers go through the
            # shared llm_client helpers (the SAME wall-clock timeout
            # wrapper, JSON mode with clean fallback on
            # response_format-rejecting servers, the explicit context
            # window with its over-budget warning) and honor the per-task
            # model overrides: models.classify for w01/w02,
            # models.analyze for w03. The pipeline tags every call.
            llm_provider = self.config.get('llm_provider', 'ollama')
            _num_ctx = int(self.config.get(
                'llm_num_ctx', _llm_client.DEFAULT_NUM_CTX) or 0) or None
            _warn = lambda m: self.log_message.emit(m, "warning")

            def _llm_call(messages, task=None):
                timeout_s = float(
                    self.config.get('llm_timeout_s', 300) or 300)
                if llm_provider == 'cloud':
                    model = _llm_client.resolve_task_model(
                        self.config, task,
                        self.config.get('cloud_model', ''))
                    return self._call_cloud_llm(
                        self.config.get('cloud_api_url', ''),
                        self.config.get('cloud_api_key', ''),
                        model, messages,
                        json_mode=True, timeout_s=timeout_s,
                        num_ctx=_num_ctx, on_warn=_warn,
                        verify_tls=_netctx.verify_ssl_enabled(
                            self.config))
                if llm_provider == 'llamacpp':
                    # v0.15.0 — llama.cpp engine detection: same shared
                    # OpenAI-compatible path with the llamacpp_* keys.
                    model = _llm_client.resolve_task_model(
                        self.config, task,
                        str(self.config.get('llamacpp_model', '') or ''))
                    return self._call_cloud_llm(
                        _llm_client.normalize_llamacpp_api_url(
                            self.config.get('llamacpp_api_url', '')),
                        self.config.get('llamacpp_api_key', ''),
                        model, messages,
                        json_mode=True, timeout_s=timeout_s,
                        num_ctx=_num_ctx, on_warn=_warn,
                        verify_tls=_netctx.verify_ssl_enabled(
                            self.config))
                model = _llm_client.resolve_task_model(
                    self.config, task, ollama_model)
                return _llm_client.ollama_chat(
                    ollama_client, model, messages, timeout_s,
                    json_mode=True, num_ctx=_num_ctx, on_warn=_warn)

            try:
                pipeline = _website_pipeline.WebsitePipeline(
                    config=self.config,
                    llm_call=_llm_call,
                    vault_index_has=index.has_url,
                    state=state,
                    log=self.log_message.emit,
                    note_state_db=note_state_db)

                due = state.due_retries()
                self.log_message.emit(
                    f"🌐 Websites pipeline: {len(links)} link(s)"
                    + (f" + {len(due)} due retry(ies)" if due else ""),
                    "info")

                if due:
                    pipeline.run_due_retries(
                        should_continue=lambda: self.is_running)
                results = pipeline.run(
                    links, should_continue=lambda: self.is_running)

                # Manifest marking (the intake left website links pending):
                # processed/review -> processed (a _review note IS a note),
                # skipped -> skipped, hard failures -> failed.
                if self.link_tracker:
                    for r in results:
                        try:
                            if r.get('outcome') in ('processed', 'review'):
                                self.link_tracker.mark_processed(
                                    r['url'], r.get('note_path') or '')
                            elif r.get('outcome') == 'skipped':
                                self.link_tracker.mark_skipped(
                                    r['url'], r.get('error') or 'skipped')
                            elif r.get('outcome') == 'failed':
                                self.link_tracker.mark_failed(
                                    r['url'], r.get('error') or 'failed')
                        except Exception:
                            pass  # manifest is best-effort bookkeeping

                summary = {'counters': dict(pipeline.counters),
                           'results': list(pipeline.last_results),
                           'vault': website_vault}
                self._website_summary = summary
                c = pipeline.counters
                self.log_message.emit(
                    f"🌐 Websites done: {c['processed']} processed, "
                    f"{c['review']} to review, {c['skipped']} skipped, "
                    f"{c['retried']} retried, {c['upgraded']} upgraded, "
                    f"{c['failed']} failed.", "success")
                return summary
            finally:
                state.close()
        except Exception as e:
            # One failing vault/pipeline never stops the rest of the batch
            # (non-negotiable #7) — but it IS reported loudly.
            try:
                self.log_message.emit(
                    f"⚠️ Websites pipeline failed (GitHub results above are "
                    f"unaffected): {e}", "error")
            except Exception:
                pass
            return None
