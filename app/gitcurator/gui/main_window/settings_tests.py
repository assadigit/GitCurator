#!/usr/bin/env python3
"""gitcurator.gui.main_window.settings_tests — the per-tab connection-test mixin.

The About-me wizard and every 'Test' button: Telegram+GitHub, GitHub
token, proxy, vault, Ollama server/model refresh, cloud LLM, test_all
sequential chain (verbatim methods of the original MainWindow).
"""

import os, subprocess, sys
from gitcurator.gui._qt import *  # noqa: F401,F403 — Qt widget names
from gitcurator.gui.main_window._deps import *  # noqa: F401,F403

__all__ = ["SettingsTestsMixin"]


class SettingsTestsMixin:
    """SettingsTestsMixin — see module docstring (methods are verbatim moves)."""


    # ------------------------------------------------------------------
    # About Me Wizard — generates about_me.md to give the LLM context
    # ------------------------------------------------------------------
    def show_about_me_wizard(self):
        """Show a multi-step interview wizard to generate about_me.md."""
        dialog = QDialog(self)
        dialog.setWindowTitle("📝 About Me Wizard")
        
        dialog.setModal(True)
        dialog.setMinimumWidth(600)
        dialog.setMinimumHeight(500)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Intro
        intro = QLabel(
            "📝 About Me Wizard\n\n"
            "This generates an 'about_me.md' file that gives the LLM context about you.\n"
            "The LLM uses this to personalize its analysis and explain how each project\n"
            "might specifically help YOU.\n\n"
            "Fill in the fields below (leave blank if you prefer not to answer):"
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("font-size: 13px; color: #423A52; padding: 8px;")
        layout.addWidget(intro)

        # Form fields
        from PyQt6.QtWidgets import QFormLayout as _QFormLayout
        form = _QFormLayout()
        form.setSpacing(10)

        name_input = QLineEdit()
        name_input.setPlaceholderText("e.g. Software Engineer, Researcher, Student")
        role_input = QLineEdit()
        role_input.setPlaceholderText("e.g. AI/ML Engineer, Full-stack Developer")
        interests_input = QTextEdit()
        interests_input.setPlaceholderText("e.g. AI agents, developer tools, self-hosted software, automation...")
        interests_input.setMaximumHeight(80)
        objectives_input = QTextEdit()
        objectives_input.setPlaceholderText("e.g. Find tools for my workflow, learn new frameworks, build a knowledge base...")
        objectives_input.setMaximumHeight(80)
        tech_input = QLineEdit()
        tech_input.setPlaceholderText("e.g. Python, Rust, TypeScript, Docker, Kubernetes")
        vault_input = QTextEdit()
        vault_input.setPlaceholderText("e.g. I organize projects by domain (AI, Tools, Infrastructure) and use notes for quick recall.")
        vault_input.setMaximumHeight(60)

        form.addRow("Who are you:", name_input)
        form.addRow("Your role:", role_input)
        form.addRow("Your interests:", interests_input)
        form.addRow("Your objectives:", objectives_input)
        form.addRow("Technologies you use:", tech_input)
        form.addRow("How you use this vault:", vault_input)
        layout.addLayout(form)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet("padding: 8px 20px; border: 1px solid #ccc; border-radius: 5px;")
        generate_btn = QPushButton("✓ Generate about_me.md")
        generate_btn.setStyleSheet(self._btn_style(COLORS['primary'], COLORS['primary_hover']))
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(generate_btn)
        layout.addLayout(btn_row)

        def _generate():
            name = name_input.text().strip()
            role = role_input.text().strip()
            interests = interests_input.toPlainText().strip()
            objectives = objectives_input.toPlainText().strip()
            tech = tech_input.text().strip()
            vault = vault_input.toPlainText().strip()

            content = "# About Me\n\n"
            if name:
                content += f"## Who I am\nI am a {name}.\n\n"
            if role:
                content += f"## My role\n{role}\n\n"
            if interests:
                content += f"## My interests\n{interests}\n\n"
            if objectives:
                content += f"## My objectives\n{objectives}\n\n"
            if tech:
                content += f"## Technologies I use\n{tech}\n\n"
            if vault:
                content += f"## How I use this knowledge base\n{vault}\n\n"

            # Add default if nothing was filled
            if not any([name, role, interests, objectives, tech, vault]):
                content += "## Who I am\nI am a software engineer who curates GitHub projects.\n\n"
                content += "## My objectives\nDiscover useful tools and frameworks for my development workflow.\n\n"

            try:
                with open("about_me.md", 'w', encoding='utf-8') as f:
                    f.write(content)
                self.log_message(f"📝 Generated about_me.md ({len(content)} chars)", "success")
                self.log_message("The LLM will now personalize analyses based on your profile.", "info")
                self._show_custom_message_box("Success", "about_me.md generated successfully!", success=True)
                dialog.accept()
            except Exception as e:
                self._show_custom_message_box("Error", f"Failed to write about_me.md: {e}", success=False)

        generate_btn.clicked.connect(_generate)
        cancel_btn.clicked.connect(dialog.reject)

        self._animate_dialog(dialog)
        dialog.exec()

    # ------------------------------------------------------------------
    # Test Functions (all run on TestWorker -> GUI never blocks -> log
    # updates in real time via queued signal connections)
    # ------------------------------------------------------------------
    def test_telegram_github(self):
        """Test Telegram (background) + GitHub (inline, fast)."""
        if not self._acquire_telegram_lock():
            return
        self.log_message("🔍 Testing Telegram & GitHub...", "info")
        api_id = self.api_id.text()
        api_hash = self.api_hash.text()
        phone = self.phone.text()
        if not api_id or not api_hash or not phone:
            self.log_message("❌ Telegram credentials incomplete.", "error")
            return

        # GitHub test is fast; keep it inline.
        token = self.github_token.text()
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
        self._keep_worker(worker)
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
        if not self._acquire_telegram_lock():
            return
        self.log_message("🌐 Testing proxy connection...", "info")
        api_id = self.api_id.text()
        api_hash = self.api_hash.text()
        phone = self.phone.text()
        if not api_id or not api_hash or not phone:
            self.log_message("❌ Telegram credentials missing. Please fill them in first.", "error")
            return
        proxy = self._get_proxy_dict()
        if not proxy.get('enabled'):
            self.log_message("⚠️ Proxy is not enabled. Enable it in the Proxy tab.", "warning")
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
        self._keep_worker(worker)
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

    def test_cloud_llm(self):
        """v26 — Fix 4: Test the Cloud LLM connection by sending a tiny prompt
        and verifying the response is non-empty.

        Sends ``"Say hello"`` and shows the actual response text in the log
        so the user can confirm the model is generating sensible output
        (not just returning 200 OK). All failures are caught and logged —
        the test never crashes the app."""
        self.log_message("🔌 Testing Cloud LLM connection...", "info")
        api_url = self.cloud_api_url.text().strip()
        api_key = self.cloud_api_key.text().strip()
        model = self.cloud_model.text().strip()
        if not api_url:
            self.log_message("❌ Cloud LLM test failed: API URL is required.", "error")
            return
        if not model:
            self.log_message("❌ Cloud LLM test failed: Model name is required.", "error")
            return
        if not api_key:
            # Allow empty key for self-hosted servers, but warn loudly —
            # most public providers (OpenAI, OpenRouter, etc.) require a key.
            self.log_message(
                "⚠️ API Key is empty — proceeding anyway (only works for self-hosted servers without auth).",
                "warning",
            )
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

    def test_all(self):
        """Run all tests SEQUENTIALLY. Telegram and proxy tests both use the
        same session.session SQLite file, so running them in parallel causes
        'database is locked' errors. We chain them via finished_signal so
        each starts only after the previous completes."""
        self.log_message("🔍 Running full test suite (sequential)...", "info")
        self.test_ollama()
        self.test_vault()

        # Chain telegram test -> proxy test sequentially
        # We need to wait for the telegram test to finish before starting
        # the proxy test, because both use the same session file.
        self._test_all_chain_step = "telegram"
        self._run_sequential_test()

    def _run_sequential_test(self):
        """Run telegram and proxy tests one after another to avoid
        'database is locked' errors from concurrent session access."""
        if self._test_all_chain_step == "telegram":
            self.log_message("📋 [1/2] Testing Telegram...", "info")
            self.test_telegram_github_sequential(self._on_telegram_test_done_for_chain)
        elif self._test_all_chain_step == "proxy":
            self.log_message("📋 [2/2] Testing Proxy...", "info")
            self.test_proxy_sequential(self._on_proxy_test_done_for_chain)

    def _on_telegram_test_done_for_chain(self, success, result):
        """Called when the telegram test finishes during test_all."""
        if self._test_all_chain_step == "telegram":
            self._test_all_chain_step = "proxy"
            # Small delay to ensure session file is released
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(1000, self._run_sequential_test)

    def _on_proxy_test_done_for_chain(self, success, result):
        """Called when the proxy test finishes during test_all."""
        self.log_message("🏁 Test suite completed.", "info")
        self._test_all_chain_step = None

    def test_telegram_github_sequential(self, callback=None):
        """Like test_telegram_github but with a callback when done."""
        self._sequential_callback = callback
        self.test_telegram_github()
        # We need to hook into the worker's finished_signal - find the last worker
        if self._active_test_workers:
            last_worker = self._active_test_workers[-1]
            if callback:
                def _cb(name, result):
                    callback(True, result)
                last_worker.finished_signal.connect(_cb)

    def test_proxy_sequential(self, callback=None):
        """Like test_proxy but with a callback when done."""
        self.test_proxy()
        if self._active_test_workers:
            last_worker = self._active_test_workers[-1]
            if callback:
                def _cb(name, result):
                    callback(True, result)
                last_worker.finished_signal.connect(_cb)
