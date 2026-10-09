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
from gitcurator.integrations import banish_confirm as _banish_confirm
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
    prune_inbox_tables,
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
            # v0.20.0 — blocked domains at INTAKE (the X fix): these
            # links are never fetched and never noted. The pipeline's own
            # guard is the second layer (due-retries, any other entry
            # path).
            # v0.21.0 — SELF domains (the app's own bot) get the same
            # intake treatment: its auth links (…/auth/?token=…) are
            # never fetched and never noted; the _inbox row (token
            # scrubbed) is the record. Both marked 'blocked' in the
            # manifest so the verification report accounts for them
            # explicitly.
            # v0.35.0 — THE LAW grew (YouTube, Google share/drive/docs,
            # the social-media majors): banned links are omitted ENTIRELY
            # — never fetched, never noted, never collected in _inbox
            # (the manifest's blocked bucket is the count).
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
                        f"🚫 {len(_drop_blocked)} link(s) on banned domains "
                        f"({', '.join(_blocked)}) — omitted entirely: "
                        f"never fetched, never noted, never collected", "info")
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
                    # v0.37.0 — these links will never advance the bar;
                    # give the denominator back so the bar's math stays
                    # truthful (the final emit lands on 100% either way).
                    try:
                        self.total = max(0, self.total - len(links))
                    except Exception:
                        pass
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
                # v0.60.0 — THE CONFIRMATION GATE's channel: the Telegram
                # round-trip (propose -> the bot's 🗑️ Delete / ✋ Keep
                # buttons -> poll -> verdict) the pipeline asks over.
                # None when the worker isn't paired/enabled — the
                # pipeline then defers (marks stay, nothing deleted,
                # the log says so) — the SAFE default.
                _banish_fn = None
                try:
                    _banish_fn = _banish_confirm.make_telegram_confirm(
                        self.config, log=self.log_message.emit)
                except Exception as _gate_err:
                    self.log_message.emit(
                        f"⚠️ Banish gate channel unavailable: "
                        f"{_gate_err}", "warning")
                pipeline = _website_pipeline.WebsitePipeline(
                    config=self.config,
                    llm_call=_llm_call,
                    vault_index_has=index.has_url,
                    state=state,
                    log=self.log_message.emit,
                    note_state_db=note_state_db,
                    banish_confirm=_banish_fn)

                due = state.due_retries()
                # v0.37.0 — the phase owns its slice of the bar: re-base
                # the denominator on what will ACTUALLY run (the law-banned
                # links above never advance; the due retries do) and then
                # advance the position + emit progress per link, exactly
                # like the GitHub loop does. A websites batch finally moves
                # the bar (the owner's frozen-"Syncing" report).
                # NB: getattr-with-default is NOT enough on a QObject that
                # skipped __init__ (the headless test harness shape) —
                # PyQt's fallback __getattr__ raises RuntimeError, so the
                # read is guarded explicitly.
                try:
                    _github_done = int(self._current_position or 0)
                except Exception:
                    # the bare-worker shape (headless tests build one via
                    # __new__): the counter does not exist yet — start it
                    # at 0 so _wp_advance below can increment it.
                    _github_done = 0
                    self._current_position = 0
                self.total = _github_done + len(links) + len(due)

                def _wp_advance(u):
                    self._current_position += 1
                    self.status_updated.emit(u)
                    self.progress_updated.emit(self._current_position,
                                               self.total)

                # v0.42.0 — the _review backlog retry (mode 'review_retry'):
                # the phase's normal passes are REPLACED by the backlog
                # driver this run. The driver re-arms the retry queue (the
                # wall pile's 3 attempts burned out under the old fetcher),
                # re-registers lost state rows, runs every scanned URL
                # through the FULL pipeline (v0.41 browser-grade fetch →
                # classify → analyze → store) and cleans stale duplicate
                # placeholders. Manifest marking, the directory rebuild and
                # the _inbox prune below work on its results unchanged.
                # NB: the read is guarded explicitly — a QObject that
                # skipped __init__ (the headless test harness shape) makes
                # getattr-with-default RAISE via PyQt's __getattr__
                # fallback, so the default alone is not enough (same law
                # as the _current_position read above).
                try:
                    _backlog_items = \
                        getattr(self, '_review_backlog_items', None) or []
                except Exception:
                    _backlog_items = []
                if _backlog_items:
                    _unique = len({i.get('url') for i in _backlog_items})
                    self.total = _github_done + _unique
                    # v0.51.0 — mode 'master_retry': the items came from
                    # the DECOMMISSIONED.md " - " scan, not the placeholder
                    # scan — the phase drives them through the caught-up
                    # check's own driver (burned-out counters reborn per
                    # link, the FULL pipeline per link, the honest verdict
                    # at the end). The same progress/stop contract.
                    try:
                        _master_mode = bool(
                            getattr(self, '_master_retry_mode', False))
                    except Exception:
                        _master_mode = False
                    if _master_mode:
                        self.log_message.emit(
                            f"🔁 Master-table waiting pass: {_unique} "
                            f"' - ' link(s) — valid links whose fetches "
                            f"failed and no verdict is set; each is "
                            f"fetched again before 'everything is up to "
                            f"date' may be said", "info")
                        results = pipeline.retry_master_waiting(
                            should_continue=lambda: self.is_running,
                            on_progress=_wp_advance)
                    else:
                        self.log_message.emit(
                            f"🔁 Retrying the _review backlog: "
                            f"{len(_backlog_items)} "
                            f"app-owned placeholder(s) ({_unique} link(s)) — "
                            f"v0.41.0 fetches as a browser now; the "
                            f"bot-defense wall may be down for them", "info")
                        results = pipeline.retry_review_backlog(
                            _backlog_items,
                            should_continue=lambda: self.is_running,
                            on_progress=_wp_advance)
                else:
                    self.log_message.emit(
                        f"🌐 Websites pipeline: {len(links)} link(s)"
                        + (f" + {len(due)} due retry(ies)" if due else ""),
                        "info")
                    if due:
                        pipeline.run_due_retries(
                            should_continue=lambda: self.is_running,
                            on_progress=_wp_advance)
                    results = pipeline.run(
                        links, should_continue=lambda: self.is_running,
                        on_progress=_wp_advance)

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

                # v0.28.0 — the Website Directory: regenerate the
                # consolidated categorized index of the whole vault after
                # every websites batch (app-owned file, atomic write; a
                # rebuild with zero new links is a cheap no-op rewrite).
                # Never fails the batch — the index is a convenience.
                try:
                    from gitcurator.core import \
                        website_directory as _webdir
                    _dir = _webdir.build_website_directory(
                        website_vault, taxonomy=pipeline.taxonomy,
                        config=self.config, log=self.log_message.emit)
                    if _dir:
                        self.log_message.emit(
                            f"📚 Website Directory rebuilt: "
                            f"{_dir['sites']} site(s) in "
                            f"{_dir['categories']} categor(ies)"
                            + (f" (+{_dir['review']} in review)"
                               if _dir['review'] else "")
                            + f" → {os.path.basename(_dir['path'])}",
                            "success")
                except Exception as _dir_err:
                    self.log_message.emit(
                        f"⚠️ Website Directory rebuild skipped: "
                        f"{_dir_err}", "warning")

                # v0.35.0 — prune the _inbox tables now that the batch is
                # done: every link this run stored as a note (processed or
                # _review) leaves the review queue ("already addressed and
                # stored in correct notes"), and any lingering banned-
                # domain row goes too. The phase's VaultIndex (pre-run) +
                # the batch's results together see the whole truth.
                try:
                    _tbl_vault = _inbox_table_vault(self.config)
                    if _tbl_vault:
                        _stored = {
                            _links.normalize_website_url(r['url'])
                            for r in results
                            if r.get('outcome') in ('processed', 'review')
                        } | {
                            r.get('canonical') for r in results
                            if r.get('outcome') in ('processed', 'review')
                        }
                        _stored.discard(None)
                        _stored.discard('')
                        prune_inbox_tables(
                            _tbl_vault,
                            blocked_domains=_blocked,
                            vault_index_has=index.has_url,
                            log_callback=self.log_message.emit,
                            stored_urls=_stored)
                except Exception as _prune_err:
                    self.log_message.emit(
                        f"⚠️ _inbox table prune skipped: {_prune_err}",
                        "warning")

                # v0.59.0 — the banished tally rides the run's summary
                # (the pipeline's own 🗑️ Run tally line spoke at start;
                # the done line repeats the number so the end of the run
                # answers the owner's "how many were wiped" too).
                # v0.60.0 — the gate's verdict and the pending count ride
                # along (a run that KEPT N marks says so at the end too).
                _banished = list(
                    getattr(pipeline, 'banished_urls', None) or [])
                _gate = dict(getattr(pipeline, 'banish_gate', None) or {})
                summary = {'counters': dict(pipeline.counters),
                           'results': list(pipeline.last_results),
                           'banished': len(_banished),
                           'banished_urls': _banished,
                           'banish_gate': _gate,
                           'vault': website_vault}
                self._website_summary = summary
                c = pipeline.counters
                _tally = (f" 🗑️ {len(_banished)} banished — removed "
                          f"and never fetched again (you confirmed)."
                          if _banished else "")
                _kept = (_gate.get('pending') or 0)
                _verdict = str(_gate.get('verdict') or 'auto')
                if not _banished and _kept and _verdict != 'auto':
                    _tally = (f" 🗑️ {_kept} marked note(s) KEPT — "
                              f"waiting on your confirmation "
                              f"(I'll ask again next run).")
                self.log_message.emit(
                    f"🌐 Websites done: {c['processed']} processed, "
                    f"{c['review']} to review, {c['skipped']} skipped, "
                    f"{c['retried']} retried, {c['upgraded']} upgraded, "
                    f"{c['failed']} failed.{_tally}", "success")
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
