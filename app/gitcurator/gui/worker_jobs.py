"""_run_telegram_worker, _telegram_test_job, _bot_queue_job, _connection_battery_job, _quick_detect_job — moved verbatim from gitcurator/gui/app.py (branch refactor/gui-app-split; see docs/history/REFACTOR_PLAN.md)."""

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

from gitcurator.gui.cache_db import CacheDB

from gitcurator.gui.link_helpers import normalize_url

from gitcurator.gui.vault_index import VaultIndex

def _run_telegram_worker(config: dict, log_signal, code_callback=None, timeout: int = 300) -> dict:
    """Run telegram_fetch_worker.py in a separate process. (v0.06 — delegated)

    Streams the worker's stderr to the GUI log, supports interactive auth,
    returns the JSON result parsed from stdout. The implementation moved to
    ``gitcurator.integrations.subprocess_runner.run_telegram_worker`` which
    fixes the forever-hang this inline version had:

      * the old ``for line in proc.stderr:`` loop blocked INDEFINITELY on a
        stalled child (dead proxy / session contention) — its
        ``proc.wait(timeout=…)`` only ran AFTER stderr closed, so it never
        fired. A hung startup bot-check therefore never emitted
        finished_signal, never released the Telegram lock, and every button
        logged "⏳ Another Telegram operation is already running" forever.
      * the new runner kills the child after ``idle_timeout`` seconds of NO
        output (progress lines keep it alive) or an absolute ``hard_cap``
      * interactive auth (login code / 2FA) gets a generous grace budget
        so a slow human is never killed
      * every live child is registered so closeEvent can kill orphans (an
        orphan holding session.session made the NEXT launch hang too)

    The legacy ``timeout`` argument is accepted and ignored.
    """
    return _run_worker_subprocess(config, log_signal, code_callback)

def _telegram_test_job(api_id, api_hash, phone, proxy, log_signal, code_callback=None):
    """Quick Telegram connection test (fetch latest message via subprocess)."""
    log_signal.emit("Testing Telegram via subprocess (identical to test.py)...", "info")
    config = {
        'api_id': int(api_id),
        'api_hash': api_hash,
        'phone': phone,
        'proxy': proxy,
        'session_file': 'session',
        'offset_start': 0,
        'count': 1,
        'preview_only': True,
        'from_id': 0,
        'to_id': 0,
        'preview_count': 1,
    }
    return _run_telegram_worker(config, log_signal, code_callback=code_callback)

def _bot_queue_job(api_id, api_hash, phone, proxy, bot_username, log_signal, code_callback=None, mark_read=False, min_id=0, vault_path=None, blocked_domains=None, self_domains=None, website_vault_path=None, websites_pipeline_on=False, website_blocked_domains=None):
    """Fetch unread GitHub URLs from the user's dedicated bot chat.
    Uses the user's Telethon session (through proxy) to read messages sent
    TO the bot. Resolves the bot by username (no Bot API call needed —
    api.telegram.org is blocked in Iran).
    If mark_read=True, marks all bot messages as read (clears the queue).

    v25 pre-flight: ``min_id`` (when > 0) makes the worker fetch only
    messages with id > min_id. Used by the "📬 Process New" button to
    skip messages already processed in a previous run.

    v0.06 — Perf: when ``vault_path`` is given (and the fetch succeeded,
    and we're not just marking read), the vault filtering — VaultIndex
    rebuild (a full os.walk + read of every note) + the decommissioned-set
    load + the pending/in-vault/decommissioned classification — runs HERE,
    in the worker thread, instead of freezing the GUI inside the
    finished-signal handler. The result dict gains:
      pending_urls, in_vault_count, decommissioned_count, vault_index_count

    v0.20.0 — ``blocked_domains`` (from the app config) classifies those
    links into their OWN bucket (``blocked_count``): they are already
    addressed as rows in the _inbox platform tables and must never show
    as pending nor reach the Websites pipeline.

    v0.21.0 — ``self_domains`` does the same for the app's OWN hosts
    (the bot's auth links): their own bucket (``self_count``), never
    pending, never fetched.

    v0.28.0 — THE LAW: ``blocked_domains`` is now the PLATFORM half of
    the law (x/twitter, HuggingFace, Instagram, Facebook, LinkedIn —
    the GitHub group EXCLUDED, because this filter also sees the repo
    links that must keep flowing to the GitHub pipeline).
    ``website_blocked_domains`` (default: same as ``blocked_domains``)
    is the FULL law used for the non-GitHub website links below — gists
    and bare *.github.io sites are banned from the Websites vault too.
    Both come from links.platform_domains_from_config / .blocked_
    domains_from_config.

    v0.24.1 — Fix (websites never sync): ``website_vault_path`` +
    ``websites_pipeline_on`` classify the NON-GitHub links against the
    WEBSITES vault (its own VaultIndex, keyed by
    links.normalize_website_url) + the WebsiteStateDB dedupe tables, so
    the queue knows how many website links are still UNDONE. Before this,
    "pending" was GitHub-only: when every repo was already in the vault
    the app said "All caught up" even with hundreds of unprocessed
    website links waiting, and PROCESS never ran the Websites pipeline.
    The result dict gains: pending_website_urls,
    websites_in_vault_count, websites_processed_count,
    websites_dismissed_count, websites_blocked_count,
    websites_self_count, websites_vault_index_count (plus
    websites_pipeline_off / websites_no_vault hints when not configured).
    """
    config = {
        'api_id': int(api_id),
        'api_hash': api_hash,
        'phone': phone,
        'proxy': proxy,
        'session_file': 'session',
        'bot_queue': True,
        'bot_username': bot_username,
        'mark_read': mark_read,
        'min_id': int(min_id or 0),
    }
    result = _run_telegram_worker(config, log_signal, code_callback=code_callback)

    if vault_path and result.get('success') and not mark_read:
        try:
            vi = VaultIndex(vault_path)
            vi.rebuild(log_signal=None)
            result['vault_index_count'] = vi.count
            # v0.08 — only CONFIRMED-dead links (fail_count >= threshold)
            # are filtered out; unconfirmed entries (1-2 attempts) stay
            # pending so they receive their remaining attempts.
            try:
                cache = CacheDB()
                decommissioned_urls = cache.get_dead_url_set()
                cache.close()
            except Exception:
                decommissioned_urls = set()
            # v0.28.0 — the repo filter uses the PLATFORM list only
            # (links.platform_domains_from_config): a github.com repo
            # link must NEVER be blanket-banned here, whatever the law
            # says about the Websites vault.
            pending, in_vault, decomm, blocked, selfc = [], 0, 0, 0, 0
            for url in result.get('urls', []):
                norm = normalize_url(url)
                if blocked_domains and _links.domain_is_blocked(
                        url, blocked_domains):
                    blocked += 1
                elif self_domains and _links.domain_is_self(
                        url, self_domains):
                    selfc += 1
                elif vi.has_url(url):
                    in_vault += 1
                elif norm in decommissioned_urls:
                    decomm += 1
                else:
                    pending.append(url)
            result['pending_urls'] = pending
            result['in_vault_count'] = in_vault
            result['decommissioned_count'] = decomm
            result['blocked_count'] = blocked
            result['self_count'] = selfc
        except Exception as vi_err:
            log_signal.emit(f"⚠️ Vault filter failed in worker ({vi_err}); showing all URLs.", "warning")
            result['pending_urls'] = list(result.get('urls', []))
            result['in_vault_count'] = 0
            result['decommissioned_count'] = 0

    # v0.24.1 — Fix (websites never sync): classify the NON-GitHub links
    # against the Websites vault so "pending" covers BOTH pipelines. Runs
    # on this worker thread (same anti-GUI-freeze rule as the GitHub
    # classification above). The WebsitePipeline's own dedupe layers are
    # replicated exactly (core/website_pipeline._process_link_inner):
    #   blocked/self domain   -> never fetched, never collected (v0.35.0)
    #   dismissed             -> owner deleted the note; never re-add
    #   in vault (real note)  -> done
    #   in vault, failed _review placeholder with retries left -> PENDING
    #   (the upgrade/retry path — the pipeline re-processes it)
    #   websites_processed    -> done (any fetch_status but a failed one
    #   whose note is a _review placeholder is still retried via due list)
    #   anything else         -> PENDING (never processed)
    if result.get('success') and not mark_read:
        non_github = list(result.get('non_github_urls', []) or [])
        if not websites_pipeline_on:
            # The switch is OFF: websites are _inbox rows by design — but
            # SAY so (the owner's rule: never silently look "caught up"
            # while non-GitHub links are sitting unprocessed).
            result['pending_website_urls'] = []
            if non_github:
                result['websites_pipeline_off'] = True
        elif not (website_vault_path or '').strip():
            result['pending_website_urls'] = []
            if non_github:
                result['websites_no_vault'] = True
        elif not os.path.isdir(website_vault_path):
            # v0.24.1 — a configured-but-missing Websites vault path is a
            # config error: say so, and treat every non-GitHub link as
            # pending (never silently "all caught up"; VaultIndex.rebuild
            # on a missing dir would otherwise index 0 notes quietly).
            log_signal.emit(
                f"⚠️ Websites vault not found at {website_vault_path} — "
                "treating every non-GitHub link as pending. Fix the path "
                "in Settings → 📁 Vault.", "warning")
            result['pending_website_urls'] = list(non_github)
        elif non_github:
            try:
                # v0.28.0 — the websites loop uses the FULL law (gists,
                # bare *.github.io pages and raw.githubusercontent hosts
                # are banned from the Websites vault like every other
                # law domain — they count as blocked here, and the
                # pipeline gate skips them again on arrival).
                web_block = website_blocked_domains \
                    if website_blocked_domains is not None else blocked_domains
                wvi = VaultIndex(
                    website_vault_path,
                    normalizer=_links.normalize_website_url)
                wvi.rebuild(log_signal=None)
                result['websites_vault_index_count'] = wvi.count
                state = None
                try:
                    state = _website_pipeline.WebsiteStateDB()
                except Exception as state_err:
                    log_signal.emit(
                        f"⚠️ Websites state DB unavailable ({state_err}) — "
                        "classifying by vault index only.", "warning")
                pending_web, web_in_vault, web_done, web_dismissed = [], 0, 0, 0
                web_blocked, web_self = 0, 0
                for url in non_github:
                    canonical = _links.normalize_website_url(url)
                    if web_block and _links.domain_is_blocked(
                            url, web_block):
                        web_blocked += 1
                    elif self_domains and _links.domain_is_self(
                            url, self_domains):
                        web_self += 1
                    elif state is not None and state.is_dismissed(canonical):
                        # v0.60.1 — the verdict outranks the vault: a
                        # dismissed link whose note still sits in the
                        # index (an un-swept placeholder, an
                        # auto-retired note) is NOT pending and NOT
                        # "in vault" — the owner's report: "it still
                        # counts decommissioned links as unprocessed".
                        web_dismissed += 1
                    elif wvi.has_url(url):
                        # In the vault. A failed-fetch _review placeholder
                        # with retries remaining is PENDING (upgrade path);
                        # everything else is a real note — done.
                        prior = None
                        if state is not None:
                            try:
                                prior = state.processed_row(canonical)
                            except Exception:
                                prior = None
                        if (prior and prior.get('fetch_status') == 'failed'
                                and state is not None):
                            pending_web.append(url)
                        else:
                            web_in_vault += 1
                    elif state is not None and state.is_processed(canonical):
                        web_done += 1
                    else:
                        pending_web.append(url)
                if state is not None:
                    try:
                        state.close()
                    except Exception:
                        pass
                result['pending_website_urls'] = pending_web
                result['websites_in_vault_count'] = web_in_vault
                result['websites_processed_count'] = web_done
                result['websites_dismissed_count'] = web_dismissed
                result['websites_blocked_count'] = web_blocked
                result['websites_self_count'] = web_self
            except Exception as web_err:
                log_signal.emit(
                    f"⚠️ Websites vault filter failed in worker ({web_err}) — "
                    "treating every non-GitHub link as a pending website.",
                    "warning")
                result['pending_website_urls'] = list(non_github)
        else:
            result['pending_website_urls'] = []
    return result

def _connection_battery_job(config, log_signal, on_section=None, on_result=None):
    """v0.17.0 — Test Connection background battery: vaults + LLM + GitHub
    + the Telegram LOCAL checks, in that order, one log line per result.

    Everything here never raises (each group is individually guarded in
    connection_check.run_local_checks) and touches NO session file — the
    LIVE Telegram leg runs only after this worker finishes, because
    session.session is single-user (two Telethon children at once =
    'database is locked', the rule every other Telegram button follows).

    v0.23.0 — optional ``on_section`` / ``on_result`` callbacks stream the
    same progress STRUCTURED (for the Test Connection modal's per-subsystem
    rows) alongside the log lines. ``on_section(title, idx, total)`` fires
    before the section's results; ``on_result(section_idx, result)`` fires
    per result with the 1-based section index.

    Returns ``{'success': True, 'sections': [[title, [result, …]], …]}``
    for the final verdict line."""
    _sec_box = {'idx': 0}

    def _sec(title, idx, total):
        log_signal.emit(f"📋 [{idx}/{total}] {title}", "info")
        _sec_box['idx'] = idx
        if on_section is not None:
            try:
                on_section(title, idx, total)
            except Exception:
                pass

    def _res(r):
        log_signal.emit(
            "   " + _connection_check.render_line(r),
            _connection_check.GUI_LEVELS.get(r.get("level"), "info"))
        if on_result is not None:
            try:
                on_result(_sec_box['idx'], r)
            except Exception:
                pass

    sections = _connection_check.run_local_checks(
        config, on_section=_sec, on_result=_res)
    return {"success": True, "sections": sections}

def _quick_detect_job(provider, config, log_signal):
    """v0.18.0 — the background half of "Detect & Set": probe ONE local
    engine (Ollama or llama.cpp) and return where it runs + its models.
    Never raises — the worst outcome is a result dict with found=False.

    * ollama    — ONE /api/tags probe at the configured URL
                  (llm_client.detect_ollama; raw HTTP, no ollama SDK →
                  thread-safe + testable against a stdlib http.server).
    * llamacpp  — the FULL catch (the startup auto-detect order): the
                  configured URL first, then the RUNNING llama-server
                  process's listening ports (any --port — the Task-Manager
                  guarantee), then the common-port scan.

    Returns ``{'success': bool, 'provider': str, 'found': bool,
    'base_url': str, 'models': [str], 'detail': str, 'props_model': str,
    'ready': bool, 'via': str}`` — success only means the probe RAN;
    found says whether the engine answered."""
    provider = str(provider or 'ollama').lower()
    out = {'success': False, 'provider': provider, 'found': False,
           'base_url': '', 'models': [], 'detail': 'not run',
           'props_model': None, 'ready': None, 'via': None}
    cfg = config if isinstance(config, dict) else {}
    try:
        if provider == 'ollama':
            oll = cfg.get('ollama') or {}
            base = (str(oll.get('base_url',
                                'http://127.0.0.1:11434') or '').strip()
                    if isinstance(oll, dict)
                    else 'http://127.0.0.1:11434') \
                or 'http://127.0.0.1:11434'
            log_signal.emit(f"🧠 Detecting Ollama at {base}…", "info")
            r = _llm_client.detect_ollama(base)
            out.update({'success': True, 'found': bool(r.get('found')),
                        'base_url': r.get('base_url', base),
                        'models': list(r.get('models') or []),
                        'detail': r.get('detail', '')})
        else:
            key = str(cfg.get('llamacpp_api_key', '') or '')
            url = str(cfg.get('llamacpp_api_url', '') or '').strip() \
                or _llm_client.LLAMACPP_DEFAULT_BASE
            log_signal.emit(
                f"🦙 Detecting llama.cpp — {url} first, then the running "
                "llama-server process + common ports…", "info")
            probe = _llm_client.probe_llamacpp(url, key)
            via = 'configured'
            if not probe.get('found'):
                probe = _llm_client.detect_llamacpp(key) or probe
                via = probe.get('via')  # 'process' | 'scan' | None
            models = [m for m in (probe.get('models') or []) if m]
            props_model = probe.get('props_model')
            if props_model and props_model not in models:
                models.append(props_model)
            out.update({'success': True,
                        'found': bool(probe.get('found')),
                        'base_url': probe.get('base_url') or url,
                        'models': models,
                        'detail': probe.get('detail', ''),
                        'props_model': props_model,
                        'ready': probe.get('ready'), 'via': via})
    except Exception as e:  # noqa: BLE001 — the probe must never raise
        out['detail'] = f'{type(e).__name__}: {e}'
    return out


def _scan_llm_call(config: dict, log) -> Optional[Any]:
    """v0.61.0 — the scan's LLM router (the website phase's router,
    minimal): ollama / cloud / llama.cpp over the shared llm_client
    helpers, JSON mode, the wall-clock timeout. Returns None when no
    provider is configured (the scan still runs — the deletions and
    the inventory need no LLM; the filing pass simply proposes
    nothing)."""
    try:
        provider = str(config.get('llm_provider', 'ollama') or 'ollama')
        timeout_s = float(config.get('llm_timeout_s', 300) or 300)
        _num_ctx = int(config.get(
            'llm_num_ctx', _llm_client.DEFAULT_NUM_CTX) or 0) or None

        def _warn(m):
            log(f"⚠️ {m}", "warning")

        if provider == 'cloud':
            model = _llm_client.resolve_task_model(
                config, 'vaultscan', config.get('cloud_model', ''))

            def _call(messages, task=None):
                return _llm_client.cloud_chat(
                    config.get('cloud_api_url', ''),
                    config.get('cloud_api_key', ''),
                    model, messages, timeout_s,
                    json_mode=True, num_ctx=_num_ctx, on_warn=_warn)
            return _call
        if provider == 'llamacpp':
            model = _llm_client.resolve_task_model(
                config, 'vaultscan',
                str(config.get('llamacpp_model', '') or ''))

            def _call(messages, task=None):
                return _llm_client.cloud_chat(
                    _llm_client.normalize_llamacpp_api_url(
                        config.get('llamacpp_api_url', '')),
                    config.get('llamacpp_api_key', ''),
                    model, messages, timeout_s,
                    json_mode=True, num_ctx=_num_ctx, on_warn=_warn)
            return _call
        # ollama (the default)
        base = str(config.get('ollama', {}).get('base_url')
                   or 'http://localhost:11434').rstrip('/')
        model = _llm_client.resolve_task_model(
            config, 'vaultscan',
            str((config.get('ollama', {}) or {}).get('model')
                or ''))

        def _call(messages, task=None):
            import ollama
            client = ollama.Client(host=base)
            return _llm_client.ollama_chat(
                client, model, messages, timeout_s,
                json_mode=True, num_ctx=_num_ctx, on_warn=_warn)
        return _call
    except Exception as e:  # noqa: BLE001 — a broken router never kills the scan
        log(f"⚠️ The scan's LLM router could not be built: {e} — the "
            f"filing pass is skipped this run (the deletions still "
            f"run)", "warning")
        return None


def _vault_scan_job(config: dict, log_signal,
                   confirm_gui: Optional[Any] = None) -> Dict:
    """v0.61.0 — THE VAULT SCAN (the [Scan] CTA's background job).

    The owner's ask (verbatim): "The Scan run: LLM scans the vault
    (folder walk + note contents → LLM analysis); Detects notes I
    manually tagged 'auto-delete' — REUSE the v0.60.1 grammar exactly;
    Suggests folder/subfolder creation for orphaned / not-categorized
    / too-broad-category websites, and which notes should move where;
    Confirms with me on Telegram BEFORE deleting or moving anything."

    v0.62.0 — THE GUI CONFIRM DOOR: the owner's report (verbatim):
    "the scan now suggest new folders to be made, but there is no modal
    or accept or confirm button to actually LLM do them." The ask now
    has TWO doors, chosen by config ``scan_confirm_door``: ``'gui'``
    (the DEFAULT whenever a ``confirm_gui`` ask-gate is injected — the
    desktop's own launches; the plan opens in ScanPlanDialog and the
    owner answers on screen) or ``'telegram'`` (the old round-trip —
    still the away-from-desk door, and the only one a headless/CLI
    launch has). Both doors answer the same vocabulary
    ('confirmed'/'declined'/'timeout') and guard the SAME enforcement —
    nothing moves or deletes until the owner says so, whichever screen
    he says it on.

    The whole story, one thread (never the GUI thread): the inventory
    walk → the plan (deletions = scan_pending_banishments — ONE
    grammar; filing = the LLM's validated proposal) → the ask (the
    GUI door: the plan rides scan_confirm_requested and the worker
    blocks while the modal stands open; the Telegram door:
    make_scan_confirm — the banish-gate round-trip template; 300s no
    answer = safe defer) → on CONFIRM: apply_scan_plan (folders +
    byte-identical moves + the banishment machinery) and the closing
    report; on DECLINE/TIMEOUT/DEFER: nothing is touched. Never
    raises (the TestWorker contract); returns the story dict."""
    from gitcurator.core import vault_scan as _vault_scan
    from gitcurator.integrations import scan_confirm as _scan_confirm

    def log(msg, level='info'):
        try:
            log_signal.emit(msg, level)
        except Exception:
            pass

    cfg = dict(config or {})
    vault = str(cfg.get('website_vault_path') or '').strip()
    if not vault or not os.path.isdir(vault):
        log("⚠️ Vault scan: no Websites vault is set (Settings → 📁 "
            "Vault) — nothing to scan.", "warning")
        return {'success': False, 'error': 'no vault'}
    log(f"{_vault_scan.SCAN_PREFIX} reading the vault — the folder "
        f"walk, your auto-delete marks, and the LLM's filing eyes…",
        "info")
    # the state DB rides the dry-run shadow-cache law (a rehearsal
    # records nothing):
    if _dryrun.is_enabled():
        state = _website_pipeline.WebsiteStateDB(
            db_path=_dryrun.shadow_cache_path(
                os.path.join(APP_DIR, 'cache.db')))
    else:
        state = _website_pipeline.WebsiteStateDB()
    out: Dict = {'success': True, 'verdict': 'defer', 'deletions': 0,
                 'moves': 0, 'new_folders': 0, 'applied': 0,
                 'moved': 0, 'folders_created': 0}
    try:
        llm_call = _scan_llm_call(cfg, log)
        plan = _vault_scan.build_scan_plan(
            vault, llm_call=llm_call, log=log, config=cfg) or {}
        out['deletions'] = len(plan.get('deletions') or [])
        out['moves'] = len(plan.get('moves') or [])
        out['new_folders'] = len(plan.get('new_folders') or [])
        inv = plan.get('inventory') or {}
        log(f"{_vault_scan.SCAN_PREFIX} the vault holds "
            f"{inv.get('total_notes', 0)} note(s) in "
            f"{inv.get('folder_count', 0)} folder(s) — "
            f"{inv.get('root_notes', 0)} orphaned, "
            f"{inv.get('uncategorized_notes', 0)} uncategorized",
            "info")
        has_deletions = out['deletions'] > 0
        has_filing = (out['moves'] + out['new_folders']) > 0
        if not has_deletions and not has_filing:
            log(f"{_vault_scan.SCAN_PREFIX} the library is clean — no "
                f"auto-delete marks, no filing to propose. Nothing to "
                f"ask, nothing to do.", "info")
            out['verdict'] = 'clean'
            return out
        if has_deletions:
            log(f"🗑️ {out['deletions']} note(s) carry your delete mark "
                f"(the v0.60.1 grammar — frontmatter + body tags)",
                "info")
        if has_filing:
            for nf in (plan.get('new_folders') or []):
                log(f"🌱 New folder proposed: {nf}", "info")
            for m in (plan.get('moves') or []):
                log(f"🚚 Move proposed: {m.get('note')} — "
                    f"{m.get('from')} → {m.get('to')}"
                    f"{(' (' + m['reason'] + ')') if m.get('reason') else ''}",
                    "info")
        # ---- the gate: nothing moves or deletes without the owner ----
        # v0.62.0 — the door selection: 'gui' (the modal, the default
        # whenever the ask-gate is injected — the desktop's launches)
        # or 'telegram' (the old round-trip). One grammar, two doors.
        try:
            timeout_s = float(
                cfg.get('scan_confirm_timeout_s', 300.0) or 300.0)
        except (TypeError, ValueError):
            timeout_s = 300.0
        door = str(cfg.get('scan_confirm_door') or '').strip().lower()
        use_gui = confirm_gui is not None and door != 'telegram'
        if door == 'gui' and confirm_gui is None:
            log(f"⚠️ The scan_confirm_door is set to 'gui' but this launch "
                f"has no GUI ask-gate — the Telegram door carries the ask "
                f"instead", "warning")
        if use_gui:
            def channel(plan: Dict, ask_log=None) -> Dict:
                ask_log = ask_log or (lambda *a, **k: None)
                ask_log(
                    f"🗂️ Vault scan review — the plan is on your screen "
                    f"now (Apply / Keep buttons; the vault stays "
                    f"untouched until you answer; up to "
                    f"{int(timeout_s)}s)", "info")
                try:
                    verdict = confirm_gui(plan, timeout_s=timeout_s)
                except Exception as e:
                    log(f"⚠️ The GUI confirm door failed: {e} — the safe "
                        f"defer applies (nothing moved, nothing deleted)",
                        "warning")
                    return {'verdict': 'defer', 'report': None}
                verdict = str(verdict or 'timeout').strip().lower()
                if verdict not in ('confirmed', 'declined'):
                    verdict = 'timeout'
                return {'verdict': verdict, 'report': None}
        else:
            channel = None
            try:
                channel = _scan_confirm.make_scan_confirm(cfg, log=log)
            except Exception as _e:
                log(f"⚠️ Scan gate channel unavailable: {_e}", "warning")
        if channel is None:
            # only the Telegram door can land here (the GUI door always
            # yields a channel — a broken gate answers 'defer' itself):
            # pinned 'telegram', a headless launch, or the 'gui'-without-
            # a-gate fallback all tried Telegram and found it unpaired.
            if has_deletions:
                log(f"⚠️ The Telegram worker is not paired/enabled — "
                    f"NOTHING is deleted or moved (the safe defer: the "
                    f"marks and the notes stay; I'll ask again on the "
                    f"next scan)", "warning")
            else:
                log(f"⚠️ The Telegram worker is not paired/enabled — "
                    f"the filing plan is dropped (the safe defer: "
                    f"nothing moves)", "warning")
            out['verdict'] = 'defer'
            return out
        res = channel(plan, ask_log=log) or {}
        verdict = str(res.get('verdict') or 'defer')
        out['verdict'] = verdict
        if verdict != 'confirmed':
            _why = {'declined': "you said keep everything",
                    'timeout': "no answer in time",
                    'defer': "the confirm door could not carry the ask"
                             }.get(verdict, verdict)
            log(f"👌 Vault scan: nothing moved, nothing deleted "
                f"({_why}) — the vault stays exactly as it is", "info")
            return out
        log(f"✅ You confirmed the plan — applying it now (nothing is "
            f"rewritten, only moved)", "info")
        rep = _vault_scan.apply_scan_plan(
            plan, vault, state, log=log) or {}
        out['applied'] = int(rep.get('banished') or 0)
        out['moved'] = int(rep.get('notes_moved') or 0)
        out['folders_created'] = int(rep.get('folders_created') or 0)
        log(f"{_vault_scan.SCAN_PREFIX} done — {out['applied']} "
            f"note(s) removed (resting in .trash/banished, never to be "
            f"fetched again), {out['moved']} note(s) re-filed, "
            f"{out['folders_created']} new folder(s) created",
            "info")
        report = res.get('report')
        if report is not None:
            try:
                report(out['applied'], out['moved'],
                       out['folders_created'])
            except Exception:
                pass    # best-effort — never fails the scan
    except Exception as e:  # noqa: BLE001 — the TestWorker contract
        log(f"💥 Vault scan failed: {type(e).__name__}: {e}", "error")
        out.update({'success': False, 'error': f'{type(e).__name__}: {e}',
                    'verdict': 'error'})
    finally:
        try:
            state.close()
        except Exception:
            pass
    return out
