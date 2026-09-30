"""MainWindow ConnectionTestsMixin — methods moved verbatim from the MainWindow in gitcurator/gui/app.py (branch refactor/gui-app-split; see REFACTOR_PLAN.md at the repo root)."""

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

from gitcurator.gui.processing_worker import ProcessingWorker, TestWorker

from gitcurator.gui.worker_jobs import _telegram_test_job

class ConnectionTestsMixin:
    """ConnectionTestsMixin"""

    # ------------------------------------------------------------------
    # Test Functions (all run on TestWorker -> GUI never blocks -> log
    # updates in real time via queued signal connections)
    # ------------------------------------------------------------------
    def test_telegram_github(self):
        """Test Telegram (background) + GitHub (inline, fast)."""
        if not self._acquire_telegram_lock("test_telegram"):
            return
        self.log_message("🔍 Testing Telegram & GitHub...", "info")
        api_id = self.api_id.text()
        api_hash = self.api_hash.text()
        phone = self.phone.text()
        if not api_id or not api_hash or not phone:
            self.log_message("❌ Telegram credentials incomplete.", "error")
            # v0.06 — Fix: early returns after acquiring MUST release the
            # lock (this exact pattern stuck it at True forever in v0.05).
            self._release_telegram_lock("test_telegram")
            return

        # GitHub test is fast; keep it inline.
        # v0.07.1 — Fix: .strip() — a token pasted with a trailing newline
        # made PyGithub die with "Invalid ... character(s) in header value:
        # 'token ghp_…\n'" here while the direct token test (which strips)
        # passed seconds earlier. All token reads strip now.
        token = self.github_token.text().strip()
        try:
            if token:
                auth = Auth.Token(token)
                g = Github(auth=auth)
                user = g.get_user().login
                self.log_message(f"✅ GitHub token valid (user: {user})", "success")
            else:
                g = Github()
                g.get_user("octocat")
                self.log_message("✅ GitHub public API is accessible.", "success")
        except Exception as e:
            self.log_message(f"❌ GitHub test failed: {e}", "error")

        # Telegram test -> background thread.
        proxy = self._get_proxy_dict()
        if not proxy.get('enabled'):
            self.log_message("⚠️ Proxy is NOT enabled. Enable it in the Proxy tab, or Telegram will try a direct connection (blocked in Iran).", "warning")

        worker = TestWorker(_telegram_test_job, "telegram",
                            api_id, api_hash, phone, proxy, None, None)
        # Wire the signal in so the job can stream logs + request auth codes.
        def _job(aid, ahash, ph, px, _ignored_log, _ignored_code):
            return _telegram_test_job(aid, ahash, ph, px, worker.log_message, worker.request_code)
        worker._fn = _job

        worker.log_message.connect(self.log_message)
        worker.code_requested.connect(
            lambda pt, w=worker: self._on_telegram_code_requested(pt, w)
        )

        def _on_finished(name, result):
            if result.get('success'):
                preview = result.get('preview', {})
                total = preview.get('total_count', 0)
                self.log_message(
                    f"✅ Telegram connection OK. Found {total} messages (latest fetched).",
                    "success",
                )
                first = preview.get('first', [])
                if first:
                    self.log_message(f"   Latest message ID: {first[0].get('id')}", "info")
            else:
                self.log_message(f"❌ Telegram connection failed: {result.get('error')}", "error")

        worker.finished_signal.connect(_on_finished)
        self._keep_worker(worker, owner="test_telegram")
        worker.start()

    def test_github_token(self):
        """v32.1: validate the GitHub token from the Credentials tab.

        Calls GET /user with the token as typed (before Save). Shows the
        account login on success, or an actionable message on 401/403 —
        the exact failure that used to surface as raw JSON blobs for every
        repo of a batch. Fast + inline, same pattern as the GitHub half of
        test_telegram_github()."""
        token = self.github_token.text().strip()
        if not token:
            self.log_message(
                "🔑 No GitHub token entered — the app will use anonymous access "
                "(60 requests/hour, 5000 with a token).",
                "warning",
            )
            return
        self.log_message("🔑 Testing GitHub token...", "info")
        try:
            auth = Auth.Token(token)
            g = Github(auth=auth, timeout=15)
            user = g.get_user().login
            self.log_message(
                f"✅ GitHub token valid — account: {user} "
                f"(5000 requests/hour enabled)",
                "success",
            )
        except GithubException as e:
            if getattr(e, 'status', None) == 401:
                self.log_message(
                    "❌ GitHub token REJECTED (401 Bad credentials) — it is invalid, "
                    "expired, or was rotated. Create a fresh token at "
                    "github.com/settings/tokens (classic, 'repo' scope) and paste it here.",
                    "error",
                )
            elif getattr(e, 'status', None) == 403:
                self.log_message(
                    f"❌ GitHub token forbidden (403): {e.data if hasattr(e, 'data') else e}",
                    "error",
                )
            else:
                self.log_message(f"❌ GitHub token test failed: {e}", "error")
        except Exception as e:
            self.log_message(f"❌ GitHub token test failed (network): {e}", "error")

    def test_proxy(self):
        """Test proxy by connecting to Telegram through it (background)."""
        if not self._acquire_telegram_lock("test_proxy"):
            return
        self.log_message("🌐 Testing proxy connection...", "info")
        api_id = self.api_id.text()
        api_hash = self.api_hash.text()
        phone = self.phone.text()
        if not api_id or not api_hash or not phone:
            self.log_message("❌ Telegram credentials missing. Please fill them in first.", "error")
            self._release_telegram_lock("test_proxy")  # v0.06 — never leak the lock
            return
        proxy = self._get_proxy_dict()
        if not proxy.get('enabled'):
            self.log_message("⚠️ Proxy is not enabled. Enable it in the Proxy tab.", "warning")
            self._release_telegram_lock("test_proxy")  # v0.06 — never leak the lock
            return

        worker = TestWorker(_telegram_test_job, "proxy",
                            api_id, api_hash, phone, proxy, None, None)
        def _job(aid, ahash, ph, px, _ignored_log, _ignored_code):
            return _telegram_test_job(aid, ahash, ph, px, worker.log_message, worker.request_code)
        worker._fn = _job

        worker.log_message.connect(self.log_message)
        worker.code_requested.connect(
            lambda pt, w=worker: self._on_telegram_code_requested(pt, w)
        )
        def _on_finished(name, result):
            if result.get('success'):
                self.log_message("✅ Proxy is working. Telegram connected successfully.", "success")
            else:
                self.log_message(f"❌ Proxy test failed: {result.get('error')}", "error")
        worker.finished_signal.connect(_on_finished)
        self._keep_worker(worker, owner="test_proxy")
        worker.start()

    def test_vault(self):
        """Validate the selected vault path (fast, inline)."""
        self.log_message("📁 Validating vault path...", "info")
        path = self.vault_combo.currentText()
        if not path:
            self.log_message("❌ No vault selected.", "error")
            return
        if not os.path.isdir(path):
            self.log_message(f"❌ Vault path does not exist: {path}", "error")
            return
        if os.path.isdir(os.path.join(path, ".obsidian")):
            self.log_message(f"✅ Vault is valid (contains .obsidian folder): {path}", "success")
        else:
            self.log_message(f"⚠️ Path exists but does not appear to be an Obsidian vault (no .obsidian folder).", "warning")

    def _get_ollama_model_names(self, url: str):
        """Connect to Ollama and return a list of available model names.

        v30 — the API-shape normalization now lives in llm_client
        (list_models_with_timeout) with a 15s wall-clock timeout so a hung
        Ollama can no longer freeze the GUI thread that calls this.
        Returns (model_names, error_message). On success error_message is None.
        """
        try:
            client = ollama.Client(host=url)
            names = _llm_client.list_models_with_timeout(client, timeout_s=15)
            return names, None
        except Exception as e:
            return [], f"{type(e).__name__}: {e}"

    def refresh_ollama_models(self):
        """Pull the list of available models from the Ollama server and
        populate the model dropdown. Runs inline (fast)."""
        url = self.ollama_url.text().strip()
        if not url:
            self.log_message("❌ Ollama URL is empty.", "error")
            return
        self.log_message(f"🔄 Refreshing models from {url}...", "info")
        names, err = self._get_ollama_model_names(url)
        if err is not None:
            self.log_message(
                f"❌ Could not list models. Is Ollama running? "
                f"Click '🚀 Start Ollama Server' first. Error: {err}",
                "error",
            )
            return
        if not names:
            self.log_message(
                "⚠️ Ollama is running but no models are installed. "
                "Pull one with: ollama pull <model>",
                "warning",
            )
            return
        # Preserve the current text so we don't lose a custom name the user typed.
        current = self.ollama_model.currentText()
        self.ollama_model.clear()
        for n in names:
            self.ollama_model.addItem(n)
        if current in names:
            self.ollama_model.setCurrentText(current)
        else:
            # Insert the user's custom name at the top and select it.
            self.ollama_model.insertItem(0, current)
            self.ollama_model.setCurrentIndex(0)
        # v30 — Fix (model persistence): keep self.config in sync with the
        # combo IN PLACE so the next batch reads what the user sees here,
        # even before save_config() runs.
        ollama_cfg = self.config.get('ollama')
        if not isinstance(ollama_cfg, dict):
            ollama_cfg = {}
            self.config['ollama'] = ollama_cfg
        ollama_cfg['model'] = self.ollama_model.currentText()
        self.log_message(
            f"✅ Found {len(names)} model(s): {', '.join(names)}", "success"
        )

    def start_ollama_server(self):
        """Start `ollama serve` in a detached background process so the user
        doesn't need a separate terminal. Works on Windows and Unix."""
        self.log_message("🚀 Starting Ollama server...", "info")

        # On Windows: use CREATE_NEW_PROCESS_GROUP + DETACHED_PROCESS so it
        # survives the GUI closing. On Unix: use start_new_session=True.
        try:
            kwargs = {}
            if sys.platform == 'win32':
                kwargs['creationflags'] = (
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    | getattr(subprocess, 'DETACHED_PROCESS', 0x00000008)
                )
            else:
                kwargs['start_new_session'] = True

            # Try `ollama serve`; if not on PATH, fall back to common locations.
            cmd = ['ollama', 'serve']
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                **kwargs,
            )
            self._ollama_server_proc = proc
            self.log_message(
                f"✅ Ollama server starting in background (PID {proc.pid}). "
                f"Wait a moment, then click '🔄 Refresh Models'.",
                "success",
            )
            # Give it a moment, then try to refresh the model list automatically.
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(3000, self.refresh_ollama_models)
        except FileNotFoundError:
            self.log_message(
                "❌ 'ollama' not found on PATH. Install it from "
                "https://ollama.com/download and make sure it's in your PATH.",
                "error",
            )
        except Exception as e:
            self.log_message(f"❌ Failed to start Ollama: {e}", "error")

    def test_ollama(self):
        """Test Ollama: connection + model availability + ACTUAL prompt test.
        Sends a simple prompt to verify the model can generate responses."""
        self.log_message("🧠 Testing Ollama...", "info")
        url = self.ollama_url.text()
        model = self.ollama_model.currentText()

        # Step 1: check connection + list models
        names, err = self._get_ollama_model_names(url)
        if err is not None:
            self.log_message(
                f"❌ Ollama test failed: {err}. "
                f"Click '🚀 Start Ollama Server' if it isn't running.",
                "error",
            )
            return
        self.log_message(
            f"✅ Ollama is running at {url}. Available models: {', '.join(names)}",
            "success",
        )
        if model not in names:
            self.log_message(
                f"⚠️ Model '{model}' not found in Ollama. Pull it with: ollama pull {model}",
                "warning",
            )
            return

        # Step 2: send an actual prompt to verify the model works
        self.log_message(f"💬 Sending test prompt to '{model}'...", "info")
        try:
            client = ollama.Client(host=url)
            test_prompt = "What is 2 + 3? Answer with just the number."

            # Try WITHOUT options first — some custom/uncensored models return
            # empty output when temperature=0 is forced.
            # v30 — timeout-wrapped (llm_client.call_with_timeout, 120s).
            response = _llm_client.call_with_timeout(
                client.chat, 120,
                model=model,
                messages=[{"role": "user", "content": test_prompt}],
            )

            # Extract the response text (handle both old and new API)
            if hasattr(response, 'message'):
                reply = response.message.content or ""
            elif isinstance(response, dict):
                reply = response.get('message', {}).get('content', '')
            else:
                reply = str(response)

            reply = reply.strip()

            # If empty, try once more with explicit options (some models need them)
            if not reply:
                self.log_message(f"   (empty response, retrying with options...)", "info")
                try:
                    response = _llm_client.call_with_timeout(
                        client.chat, 120,
                        model=model,
                        messages=[{"role": "user", "content": test_prompt}],
                        options={"temperature": 0.7, "num_predict": 50},
                    )
                    if hasattr(response, 'message'):
                        reply = response.message.content or ""
                    elif isinstance(response, dict):
                        reply = response.get('message', {}).get('content', '')
                    reply = reply.strip()
                except Exception:
                    pass

            if reply:
                self.log_message(
                    f"✅ Model '{model}' responded: \"{reply[:100]}\"",
                    "success",
                )
                self.log_message("✅ Ollama is fully functional (connection + model + inference).", "success")
            else:
                # Log the full response object for debugging
                self.log_message(
                    f"⚠️ Model '{model}' returned an empty response even after retry.",
                    "warning",
                )
                self.log_message(
                    f"   This model may not work with this prompt format. Try selecting "
                    f"a different model from the dropdown (e.g. Qwen3.5-9B-Uncensored).",
                    "warning",
                )
                self.log_message(
                    f"   Raw response type: {type(response).__name__}",
                    "info",
                )
        except Exception as e:
            self.log_message(
                f"❌ Model test failed (model exists but can't generate): {e}",
                "error",
            )

    def _llm_num_ctx_value(self):
        """v0.13.0 — Phase 4: the llm_num_ctx field as an int for
        save_config. Digits ≥ 0 are taken as-is; anything else (empty,
        garbage) keeps the previous config value, defaulting to 8192 —
        a bad field can never break a save."""
        raw = ''
        if hasattr(self, 'llm_num_ctx'):
            try:
                raw = str(self.llm_num_ctx.text()).strip()
            except Exception:
                raw = ''
        if raw.isdigit() and int(raw) >= 0:
            return int(raw)
        return int(self.config.get('llm_num_ctx',
                                   _llm_client.DEFAULT_NUM_CTX)
                   or _llm_client.DEFAULT_NUM_CTX)

    def _llm_max_output_tokens_value(self):
        """v0.23.0 — the llm_max_output_tokens field as an int for
        save_config. Same lenient contract as _llm_num_ctx_value: digits
        ≥ 0 are taken as-is; anything else (empty, garbage) keeps the
        previous config value, defaulting to 0 (= leave the output cap
        to the server)."""
        raw = ''
        if hasattr(self, 'llm_max_output_tokens'):
            try:
                raw = str(self.llm_max_output_tokens.text()).strip()
            except Exception:
                raw = ''
        if raw.isdigit() and int(raw) >= 0:
            return int(raw)
        return int(self.config.get('llm_max_output_tokens',
                                   _llm_client.DEFAULT_MAX_OUTPUT_TOKENS)
                   or _llm_client.DEFAULT_MAX_OUTPUT_TOKENS)

    def test_cloud_llm(self):
        """v26 — Fix 4: Test the Cloud LLM connection by sending a tiny prompt
        and verifying the response is non-empty.

        v0.13.0 — Phase 4: the test starts with the /v1/models pre-flight
        (cheap, instant, no tokens spent): model list + whether the
        configured model is on it. Servers that hide /models are reported
        as such, then the classic "Say hello" chat ping runs anyway —
        all failures are caught and logged; the test never crashes the app.
        v0.23.0 — the cloud is TWO wire formats: api.anthropic.com URLs
        test the Claude Messages API, everything else OpenAI-compatible
        (the URL decides, exactly like the batch path's cloud_chat router).
        """
        api_url = self.cloud_api_url.text().strip()
        flavor = ("Claude" if _llm_client.is_anthropic_url(api_url)
                  else "OpenAI-compatible")
        self.log_message(f"🔌 Testing Cloud API ({flavor})...", "info")
        api_key = self.cloud_api_key.text().strip()
        model = self.cloud_model.text().strip()
        if not api_url:
            self.log_message("❌ Endpoint test failed: API URL is required.", "error")
            return
        if not model:
            self.log_message("❌ Endpoint test failed: Model name is required.", "error")
            return
        if not api_key:
            # Allow empty key for self-hosted servers, but warn loudly —
            # most public providers (OpenAI, OpenRouter, etc.) require a key.
            self.log_message(
                "⚠️ API Key is empty — proceeding anyway (only works for self-hosted servers without auth).",
                "warning",
            )
        # --- /v1/models pre-flight (warn-never-block) ---
        try:
            ok, message, listed = _llm_client.preflight_cloud(
                api_url, api_key, model)
            if ok:
                self.log_message(
                    f"📋 /models check: {message}"
                    + (f" — '{model}' is listed."
                       if listed else
                       f" — '{model}' is NOT listed (check the name or "
                       "load it on the server)."),
                    "success" if listed else "warning")
            else:
                self.log_message(
                    f"📋 /models check unavailable: {message} — "
                    "continuing with the chat ping.", "info")
        except Exception as e:
            self.log_message(
                f"📋 /models check failed: {e} — continuing with the "
                "chat ping.", "warning")
        try:
            self.log_message(f"💬 Sending test prompt to '{model}' at {api_url}...", "info")
            response = ProcessingWorker._call_cloud_llm(
                api_url, api_key, model,
                [{"role": "user", "content": "Say hello"}]
            )
            reply = (response or "").strip()
            if reply:
                self.log_message(
                    f"✅ Cloud LLM responded: \"{reply[:100]}\"",
                    "success",
                )
                self.log_message("✅ Cloud LLM is fully functional.", "success")
                self._show_custom_message_box(
                    "Cloud LLM Connected",
                    f"Model '{model}' responded:\n\n  \"{reply[:200]}\"\n\n"
                    f"Cloud LLM is fully functional.",
                    success=True,
                )
            else:
                self.log_message(
                    "⚠️ Cloud LLM returned an empty response. "
                    "Check the model name and API key.",
                    "warning",
                )
                self._show_custom_message_box(
                    "Cloud LLM — Empty Response",
                    "The API returned 200 OK but the response content was empty.\n\n"
                    "Check that the model name is correct and that your API key "
                    "has access to it.",
                    success=False,
                )
        except Exception as e:
            self.log_message(f"❌ Cloud LLM test failed: {e}", "error")
            self._show_custom_message_box(
                "Cloud LLM — Connection Failed",
                f"Could not connect to the Cloud LLM API.\n\n"
                f"Error: {e}\n\n"
                f"Check:\n"
                f"  • API URL is correct (e.g. https://api.openai.com/v1)\n"
                f"  • API key is valid\n"
                f"  • Model name is spelled correctly\n"
                f"  • Network/proxy allows the connection",
                success=False,
            )

