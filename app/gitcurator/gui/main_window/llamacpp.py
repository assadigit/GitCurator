"""MainWindow LlamaCppMixin — methods moved verbatim from the MainWindow in gitcurator/gui/app.py (branch refactor/gui-app-split; see docs/history/REFACTOR_PLAN.md)."""

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

_APP_DIR = APP_DIR

from gitcurator.gui.processing_worker import ProcessingWorker, TestWorker

from gitcurator.gui.worker_jobs import _quick_detect_job

class LlamaCppMixin:
    """LlamaCppMixin"""

    def _llamacpp_fields(self):
        """(url, key) from the Settings widgets when they exist, else from
        config — so headless/CLI paths and tests can call the helpers."""
        url = (self.llamacpp_api_url.text().strip()
               if hasattr(self, 'llamacpp_api_url')
               else (self.config.get('llamacpp_api_url', '') or ''))
        key = (self.llamacpp_api_key.text().strip()
               if hasattr(self, 'llamacpp_api_key')
               else (self.config.get('llamacpp_api_key', '') or ''))
        return url, key

    def _fill_llamacpp_models(self, models, props_model=None, pick=None):
        """Populate the llama.cpp model combo, auto-selecting ``pick`` (or
        the first listed model) — 'its model detected automatically'.
        Also syncs self.config in place so the next batch sees it."""
        current = ''
        if hasattr(self, 'llamacpp_model'):
            try:
                current = self.llamacpp_model.currentText().strip()
            except Exception:
                current = ''
        names = [n for n in (models or []) if n]
        if props_model and props_model not in names:
            names.append(props_model)
        choice = pick or current or (names[0] if names else '')
        if hasattr(self, 'llamacpp_model'):
            combo = self.llamacpp_model
            combo.clear()
            for n in names:
                combo.addItem(n)
            combo.setCurrentText(choice)
        # Keep self.config in sync IN PLACE (the v30 rule — never rebind).
        self.config['llamacpp_model'] = choice
        return choice

    def _startup_llamacpp_autodetect(self):
        """v0.15.1 — fires ~1.5s after launch (once per session): find
        llama-server in a daemon thread — the configured URL first, then
        the RUNNING PROCESS's listening ports (Task-Manager guarantee:
        any --port), then the common-port scan — and hand the result to
        the GUI thread through the queued signal. The UI never blocks;
        dead local ports refuse instantly, so the whole probe is ~instant
        when nothing runs."""
        if getattr(self, '_llamacpp_autodetect_done', False) \
                or getattr(self, '_closing', False):
            return
        self._llamacpp_autodetect_done = True

        def _detect():
            payload = {'probe': None, 'decision': None,
                       'ollama_up': None}
            try:
                url = (self.config.get('llamacpp_api_url', '')
                       or _llm_client.LLAMACPP_DEFAULT_BASE)
                key = self.config.get('llamacpp_api_key', '')
                probe = _llm_client.probe_llamacpp(url, key)
                if not probe.get('found'):
                    probe = _llm_client.detect_llamacpp(key) or probe
                payload['probe'] = probe
                if probe.get('found'):
                    # The switch policy needs to know whether the CURRENT
                    # (default) provider actually works — only a dead
                    # Ollama / keyless cloud justifies taking over.
                    if str(self.config.get('llm_provider',
                                           'ollama')).lower() == 'ollama':
                        oll = self.config.get('ollama') or {}
                        base = (oll.get('base_url',
                                        'http://127.0.0.1:11434')
                                if isinstance(oll, dict)
                                else 'http://127.0.0.1:11434')
                        payload['ollama_up'] = _llm_client.ollama_reachable(
                            base)
                    payload['decision'] = _llm_client \
                        .llamacpp_autodetect_decision(
                            self.config, probe, payload['ollama_up'])
            except Exception as e:
                payload['error'] = str(e)
            try:
                self._llamacpp_autodetect_signal.emit(payload)
            except Exception:
                pass  # window already gone — nothing to update

        threading.Thread(target=_detect, daemon=True,
                         name='llamacpp-autodetect').start()

    def _apply_llamacpp_autodetect(self, payload):
        """v0.15.1 — GUI thread: apply the startup auto-detect result.
        The policy itself lives in llm_client.llamacpp_autodetect_decision
        (pure, unit-tested): switch ONLY when the current provider is
        unusable (dead Ollama / keyless cloud), otherwise a one-line hint.
        Every write is in-place + MERGE-saved — never rebinds self.config."""
        try:
            if getattr(self, '_closing', False):
                return
            payload = payload or {}
            probe = payload.get('probe') or {}
            decision = payload.get('decision') or {}
            if not probe.get('found'):
                # Quiet unless llama.cpp IS the configured provider — a
                # warning at every launch for a server the user never
                # asked about would be noise, but a llama.cpp user whose
                # server died wants to know immediately.
                if str(self.config.get('llm_provider', '')).lower() \
                        == 'llamacpp':
                    self.log_message(
                        "🦙 llama.cpp is the selected provider but no "
                        "llama-server was detected. Start it with: "
                        "llama-server -m <model>.gguf --port 8080",
                        "warning")
                return
            if decision.get('message'):
                self.log_message(decision['message'],
                                 decision.get('level', 'info'))
            changed = False
            if decision.get('switch'):
                self.config['llm_provider'] = 'llamacpp'
                # v0.23.0 — the two-level radios: switching to a local
                # engine must also leave the cloud host selection.
                if hasattr(self, 'llm_host_local'):
                    self.llm_host_local.setChecked(True)
                if hasattr(self, 'llm_provider_llamacpp'):
                    self.llm_provider_llamacpp.setChecked(True)
                changed = True
            new_url = decision.get('url')
            if new_url:
                self.config['llamacpp_api_url'] = new_url
                if hasattr(self, 'llamacpp_api_url'):
                    self.llamacpp_api_url.setText(new_url)
                changed = True
            if decision.get('switch') or decision.get('url') \
                    or decision.get('model'):
                # A switch or a refresh — fill the combo. The pure-hint
                # case (switch/url/model all None) touches NOTHING: no
                # config write, no save, just the log line above.
                if probe.get('models') or probe.get('props_model'):
                    before = str(self.config.get('llamacpp_model', '')
                                 or '')
                    model = self._fill_llamacpp_models(
                        probe.get('models'), probe.get('props_model'),
                        pick=decision.get('model') or probe.get('model'))
                    if model and model != before:
                        changed = True
            if changed:
                # save_config MERGES — nothing else in config.json is
                # touched by persisting the auto-detected values.
                self.save_config()
                if decision.get('switch'):
                    self.log_message(
                        "✅ llama.cpp set as the LLM provider — saved to "
                        "Settings.", "success")
                else:
                    self.log_message(
                        "✅ llama.cpp settings refreshed — saved to "
                        "Settings.", "success")
        except Exception as e:
            try:
                self.log_message(
                    f"⚠️ llama.cpp auto-detect failed: {e}", "warning")
            except Exception:
                pass

    def detect_llamacpp_service(self):
        """🔍 Detect: find the running llama-server (its PROCESS's listening
        ports first — any --port — then the common ports), positively
        identify llama.cpp, fill the URL + model list and auto-select the
        model. Never raises — every failure is a clear log line with the
        exact command that starts the server."""
        _key = self._llamacpp_fields()[1]
        self.log_message(
            "🦙 Detecting llama.cpp server (running llama-server processes "
            "+ ports "
            + ", ".join(str(p) for p in _llm_client.LLAMACPP_SCAN_PORTS)
            + ")…", "info")
        probe = _llm_client.detect_llamacpp(_key)
        if not probe:
            self.log_message(
                "❌ No llama.cpp server found (no llama-server process, "
                "nothing on the common ports). Start it with:\n   "
                "llama-server -m <model>.gguf --port 8080\n"
                "   (llama-server ships with llama.cpp — 'winget install "
                "ggml.llamacpp' or build from source), then click Detect "
                "again — or type a custom URL in the Server URL field.",
                "error")
            return
        url = probe['base_url'] + '/v1'
        if hasattr(self, 'llamacpp_api_url'):
            self.llamacpp_api_url.setText(url)
        # IN-PLACE sync so a batch started right after sees the new URL.
        self.config['llamacpp_api_url'] = url
        model = self._fill_llamacpp_models(probe.get('models'),
                                           probe.get('props_model'),
                                           pick=probe.get('model'))
        self.log_message(f"✅ {probe['detail']}", "success")
        if not model:
            self.log_message(
                "⚠️ Server detected but no model is loaded — start it with "
                "-m <model>.gguf.", "warning")
        elif probe.get('ready') is False:
            self.log_message(
                "⏳ The model is still loading — first real calls may wait "
                "until it is ready.", "warning")

    def refresh_llamacpp_models(self):
        """🔄 Refresh: pull the model list from the llama.cpp server at the
        URL currently in the field (no port scan)."""
        url, key = self._llamacpp_fields()
        if not url:
            self.log_message("❌ llama.cpp Server URL is empty.", "error")
            return
        self.log_message(f"🦙 Probing {url}…", "info")
        probe = _llm_client.probe_llamacpp(url, key)
        if not probe.get('found'):
            self.log_message(
                f"❌ No llama.cpp server at {url} ({probe.get('detail')}). "
                "Start llama-server, click '🔍 Detect' to scan the common "
                "ports, or fix the URL.", "error")
            return
        model = self._fill_llamacpp_models(probe.get('models'),
                                           probe.get('props_model'),
                                           pick=probe.get('model'))
        self.log_message(
            f"✅ {probe['detail']}", "success")

    def test_llamacpp(self):
        """Test llama.cpp: /props identification → /health → /v1/models →
        a real one-token chat ping through the SAME OpenAI-compatible path
        the batch uses. Never crashes the app — every failure is logged."""
        self.log_message("🦙 Testing llama.cpp…", "info")
        url, key = self._llamacpp_fields()
        if not url:
            self.log_message("❌ llama.cpp test failed: Server URL is empty.",
                             "error")
            return
        probe = _llm_client.probe_llamacpp(url, key)
        if not probe.get('found'):
            # Fall back to the port scan before declaring failure — the
            # configured URL may be stale.
            probe = _llm_client.detect_llamacpp(key) or probe
        if not probe.get('found'):
            self.log_message(
                f"❌ llama.cpp test failed: {probe.get('detail')}. "
                "Start it with: llama-server -m <model>.gguf --port 8080",
                "error")
            self._show_custom_message_box(
                "llama.cpp — Not Detected",
                "No llama.cpp server was found.\n\n"
                "Start it with:\n"
                "  llama-server -m <model>.gguf --port 8080\n\n"
                "then click '🔍 Detect' in Settings → 🧠 LLM.",
                success=False)
            return
        self.log_message(f"✅ {probe['detail']}", "success")
        model = self._fill_llamacpp_models(probe.get('models'),
                                           probe.get('props_model'),
                                           pick=probe.get('model'))
        if not model:
            self.log_message(
                "❌ Server detected but no model is loaded — start it with "
                "-m <model>.gguf.", "error")
            return
        try:
            self.log_message(
                f"💬 Sending test prompt to '{model}' at "
                f"{probe['base_url']}…", "info")
            response = ProcessingWorker._call_cloud_llm(
                probe['base_url'] + '/v1', key, model,
                [{"role": "user", "content": "Say hello"}],
                verify_tls=_netctx.verify_ssl_enabled(self.config))
            reply = (response or "").strip()
            if reply:
                self.log_message(
                    f"✅ llama.cpp responded: \"{reply[:100]}\"", "success")
                self._show_custom_message_box(
                    "llama.cpp Connected",
                    f"llama.cpp server at {probe['base_url']} answered with "
                    f"model '{model}'.\n\nReply: {reply[:200]}",
                    success=True)
            else:
                self.log_message(
                    "⚠️ llama.cpp returned an empty response (the model "
                    "may still be loading).", "warning")
        except Exception as e:
            self.log_message(f"❌ llama.cpp test failed: {e}", "error")
            self._show_custom_message_box(
                "llama.cpp — Chat Failed",
                f"The server was detected but the chat ping failed.\n\n"
                f"Error: {e}\n\n"
                "Check that the model is fully loaded (⏳ loading state) "
                "and that the context window (-c) is large enough.",
                success=False)

    # ------------------------------------------------------------------
    # v0.18.0 — Detect & Set: the fast lane between the two local engines
    # (the owner runs BOTH llama.cpp and Ollama and switches between
    # them). One click: probe → model menu when several are installed →
    # provider + model + URL set and SAVED.
    # ------------------------------------------------------------------
    def quick_detect_set_ollama(self):
        """🧠 Detect & Set Ollama — probe the Ollama server, list its
        models, let the user pick when several are installed, then switch
        the LLM provider to Ollama + set the model + URL and save. One
        click — no Settings digging."""
        self._run_quick_detect('ollama')

    def quick_detect_set_llamacpp(self):
        """🦙 Detect & Set llama.cpp — find the running llama-server (its
        PROCESS's listening ports first — any --port — then the configured
        URL, then the common ports), let the user pick the model when
        several are advertised, then switch the LLM provider to llama.cpp
        + set the URL + model and save. The dedicated quick path."""
        self._run_quick_detect('llamacpp')

    def _run_quick_detect(self, provider):
        """Shared fast-lane runner: guards (no batch running, no double
        click), live-widget snapshot (unsaved edits count), background
        probe in a TestWorker (the GUI never blocks), then
        _apply_quick_detect on the GUI thread."""
        if self.worker is not None and not self.worker.isFinished():
            self.log_message(
                "⏳ A batch is running — wait for it to finish before "
                "switching the LLM engine.", "warning")
            return
        if getattr(self, '_quick_detect_running', False):
            self.log_message(
                "⏳ A Detect & Set is already running — one moment…",
                "warning")
            return
        self._quick_detect_running = True
        # Snapshot: saved config + the LIVE LLM widgets — a URL the user
        # just typed (but did not Save) is probed, not the stale one.
        snapshot = dict(self.config or {})
        try:
            if hasattr(self, 'ollama_url'):
                u = self.ollama_url.text().strip()
                if u:
                    oll = dict(snapshot.get('ollama') or {})
                    oll['base_url'] = u
                    snapshot['ollama'] = oll
            if hasattr(self, 'llamacpp_api_url'):
                u = self.llamacpp_api_url.text().strip()
                if u:
                    snapshot['llamacpp_api_url'] = u
            if hasattr(self, 'llamacpp_api_key'):
                snapshot['llamacpp_api_key'] = \
                    self.llamacpp_api_key.text().strip()
        except Exception:
            pass  # widget access must never break the fast lane

        worker = TestWorker(_quick_detect_job, 'quick_detect_' + provider,
                            provider, snapshot)

        def _job(prov, cfg):
            return _quick_detect_job(prov, cfg, worker.log_message)
        worker._fn = _job
        worker.log_message.connect(self.log_message)

        def _done(_name, result):
            self._quick_detect_running = False
            if getattr(self, '_closing', False):
                return
            try:
                self._apply_quick_detect(provider, result or {})
            except Exception as e:  # noqa: BLE001 — apply must never raise
                self.log_message(
                    f"❌ Detect & Set failed: {type(e).__name__}: {e}",
                    "error")

        worker.finished_signal.connect(_done)
        self._active_test_workers.append(worker)
        worker.start()

    def _quick_model_dialog(self, provider, base_url, models, current=''):
        """The model menu when an engine advertises SEVERAL models ("a menu
        like the current one" — the owner's words): a compact themed dialog
        with a dropdown, pre-set to the configured model when it is still
        installed, else the first. Returns the chosen name, or '' when the
        user cancelled (nothing changes)."""
        label = 'Ollama' if provider == 'ollama' else 'llama.cpp'
        icon = '🧠' if provider == 'ollama' else '🦙'
        dlg = QDialog(self)
        dlg.setWindowTitle(f"{icon} Select the {label} model")
        dlg.setModal(True)
        dlg.setMinimumWidth(440)
        lay = QVBoxLayout(dlg)
        lay.setSpacing(10)
        prompt = QLabel(
            f"{len(models)} models detected at <b>{base_url}</b> — "
            f"pick the one to use:")
        prompt.setWordWrap(True)
        lay.addWidget(prompt)
        combo = QComboBox()
        combo.addItems(models)
        # v33.1 rule: long model tags must not size the dialog to the sky.
        combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(24)
        cur = str(current or '').strip()
        if cur in models:
            combo.setCurrentText(cur)
        lay.addWidget(combo)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(
            QDialogButtonBox.StandardButton.Ok).setText("Use this model")
        buttons.button(
            QDialogButtonBox.StandardButton.Cancel).setText("Cancel")
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)
        combo.setFocus()
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return combo.currentText().strip()
        return ''

    def _apply_quick_detect(self, provider, result):
        """GUI thread: turn a _quick_detect_job result into the SET half
        of the fast lane — pick the model (a menu when several), switch
        provider + URL + model, update every live Settings widget, then
        MERGE-save so the next batch uses it immediately."""
        provider = str(provider or 'ollama').lower()
        is_ollama = provider == 'ollama'
        label = 'Ollama' if is_ollama else 'llama.cpp'
        icon = '🧠' if is_ollama else '🦙'
        if not result.get('success'):
            self.log_message(
                f"❌ {icon} Detect & Set {label}: the probe crashed — "
                f"{result.get('detail', '?')}", "error")
            return
        if not result.get('found'):
            if is_ollama:
                self.log_message(
                    f"❌ {icon} Ollama is not running at "
                    f"{result.get('base_url', '')} "
                    f"({result.get('detail', '')}). Start the Ollama app, "
                    "or Settings → 🧠 LLM → 🚀 Start Server, then click "
                    "Detect & Set Ollama again.", "error")
            else:
                self.log_message(
                    f"❌ {icon} No llama.cpp server found "
                    f"({result.get('detail', '')}). Start it with:\n   "
                    "llama-server -m <model>.gguf --port 8080\n"
                    "   then click Detect & Set llama.cpp again.", "error")
            return
        base = str(result.get('base_url', '') or '')
        models = [m for m in (result.get('models') or []) if m]
        where = f"at {base}"
        if not is_ollama and result.get('via') == 'process':
            where += " (found via the running llama-server process)"
        loading = " · model still LOADING…" \
            if (not is_ollama and result.get('ready') is False) else ''
        if not models:
            if is_ollama:
                self.log_message(
                    f"⚠️ {icon} Ollama is up {where} but NO models are "
                    "installed. Pull one with: ollama pull <model>, then "
                    "click Detect & Set Ollama again.", "warning")
            else:
                self.log_message(
                    f"⚠️ {icon} llama.cpp is up {where} but no model could "
                    f"be read{loading}. Start llama-server with "
                    "-m <model>.gguf (or wait for it to finish loading), "
                    "then click Detect & Set llama.cpp again.", "warning")
            return
        # Pick the model: the ONLY one directly; a menu when several —
        # "a menu like the current one should help user to select their
        # desired model".
        if is_ollama:
            oll = self.config.get('ollama') or {}
            current = str(oll.get('model', '') or '').strip() \
                if isinstance(oll, dict) else ''
        else:
            current = str(self.config.get('llamacpp_model', '')
                          or '').strip()
        if len(models) == 1:
            choice = models[0]
            self.log_message(
                f"✅ {icon} {label} detected {where} · model '{choice}'"
                + loading, "success")
        else:
            self.log_message(
                f"✅ {icon} {label} detected {where} · {len(models)} "
                "models — pick one:", "success")
            choice = self._quick_model_dialog(provider, base, models,
                                              current)
            if not choice:
                self.log_message(
                    "⏭️ Cancelled — the LLM provider was NOT changed "
                    f"(still "
                    f"{self.config.get('llm_provider', 'ollama')}).",
                    "info")
                return
        # SET — provider + URL + model, every live widget, MERGE-save.
        # v0.23.0 — the two-level radios: a Detect & Set always lands on
        # the LOCAL host + the detected engine (the buttons only exist in
        # the local group now).
        if hasattr(self, 'llm_host_local'):
            self.llm_host_local.setChecked(True)
        if is_ollama:
            self.config['llm_provider'] = 'ollama'
            if hasattr(self, 'llm_provider_ollama'):
                self.llm_provider_ollama.setChecked(True)
            oll = self.config.get('ollama')
            if not isinstance(oll, dict):
                oll = {}
                self.config['ollama'] = oll
            oll['base_url'] = base
            oll['model'] = choice
            if hasattr(self, 'ollama_url'):
                self.ollama_url.setText(base)
            if hasattr(self, 'ollama_model'):
                self.ollama_model.clear()
                for n in models:
                    self.ollama_model.addItem(n)
                self.ollama_model.setCurrentText(choice)
        else:
            self.config['llm_provider'] = 'llamacpp'
            if hasattr(self, 'llm_provider_llamacpp'):
                self.llm_provider_llamacpp.setChecked(True)
            url = base.rstrip('/') + '/v1'
            self.config['llamacpp_api_url'] = url
            self._fill_llamacpp_models(models,
                                       result.get('props_model'),
                                       pick=choice)
            if hasattr(self, 'llamacpp_api_url'):
                self.llamacpp_api_url.setText(url)
        self.save_config()
        shown_url = base if is_ollama \
            else self.config.get('llamacpp_api_url', url)
        self.log_message(
            f"✅ {icon} LLM provider SET to {label} — {shown_url} · model "
            f"'{choice}' — saved. The next batch uses it immediately.",
            "success")

