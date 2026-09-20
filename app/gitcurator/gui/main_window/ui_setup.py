#!/usr/bin/env python3
"""gitcurator.gui.main_window.ui_setup — the UI construction mixin.

``initUI`` builds the whole tabbed layout (Settings / fetch modes /
Sources / Bot / Backup) — one 784-line method kept intact (splitting it
would be a rewrite, not a refactor); moved verbatim from MainWindow.
"""

from gitcurator.gui._qt import *  # noqa: F401,F403 — Qt widget names
from gitcurator.gui.main_window._deps import *  # noqa: F401,F403

__all__ = ["UISetupMixin"]


class UISetupMixin:
    """UISetupMixin — see module docstring (methods are verbatim moves)."""


    def initUI(self):
        # Load bundled Inter font (if present) before any widgets are created so
        # the global stylesheet's `font-family: 'Inter'` resolves correctly.
        self._load_fonts()
        self.setWindowTitle("GitHub Project Curator 🚀")
        # v31.1 UI spec: ONE fixed window size used for every tab — the window
        # never resizes when the user switches tabs (preserves the user's
        # spatial memory of where controls sit).
        self.setGeometry(100, 100, 1000, 750)
        self.setFixedSize(1000, 750)

        central = QWidget()
        self.setCentralWidget(central)
        # Vertical layout: controls on top, log on bottom (3:4 landscape ratio)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Vertical)

        # Top panel: Tab widget + action buttons (sizes to content height)
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        self.tab_widget = QTabWidget()
        self.tab_widget.setTabPosition(QTabWidget.TabPosition.North)

        # ---- Tab 1: Credentials ----
        creds_tab = QWidget()
        creds_layout = QFormLayout(creds_tab)
        self.api_id = QLineEdit(str(self.config.get('telegram_api_id', '')))
        self.api_hash = QLineEdit(self.config.get('telegram_api_hash', ''))
        self.phone = QLineEdit(self.config.get('telegram_phone', ''))
        self.github_token = QLineEdit(self.config.get('github_token', ''))
        self.github_token.setEchoMode(QLineEdit.EchoMode.Password)

        creds_layout.addRow("API ID:", self.api_id)
        creds_layout.addRow("API Hash:", self.api_hash)
        creds_layout.addRow("Phone:", self.phone)
        creds_layout.addRow("GitHub Token (optional):", self.github_token)

        # v32.1: one-click token validation — catches the #1 field error
        # (expired/rotated token) BEFORE a batch burns its repos on 401s.
        # Reads the field as typed (test before Save), reports the account
        # login on success or an actionable message on 401/403.
        self.test_github_btn = QPushButton("🔑 Test GitHub Token")
        self.test_github_btn.setToolTip("Validate the token and show the GitHub account it belongs to")
        self.test_github_btn.clicked.connect(self.test_github_token)
        self._style_btn(self.test_github_btn, 'secondary')
        creds_layout.addRow("", self.test_github_btn)

        # v31.1: '🔗 Test Telegram & GitHub' moved to the global 'More'
        # overflow menu (infrequent actions: export / verify / retry /
        # recategorize / test).

        # About Me Wizard button — generates about_me.md to give the LLM context
        about_me_btn = QPushButton("📝 About Me Wizard")
        about_me_btn.clicked.connect(self.show_about_me_wizard)
        about_me_btn.setToolTip("Generate about_me.md to give the LLM context about who you are")
        self._style_btn(about_me_btn, 'secondary')
        creds_layout.addRow("", about_me_btn)

        self.tab_widget.addTab(self._wrap_scroll(creds_tab), "🔑 Credentials")

        # ---- Tab 2: Proxy ----
        proxy_tab = QWidget()
        proxy_layout = QFormLayout(proxy_tab)
        self.proxy_enabled = QCheckBox("Enable Proxy")
        self.proxy_enabled.setChecked(self.config.get('proxy', {}).get('enabled', False))
        self.proxy_type = QComboBox()
        self.proxy_type.addItems(['socks5', 'socks4', 'http'])
        self.proxy_type.setCurrentText(self.config.get('proxy', {}).get('type', 'socks5'))
        self.proxy_host = QLineEdit(self.config.get('proxy', {}).get('host', '127.0.0.1'))
        self.proxy_port = QLineEdit(str(self.config.get('proxy', {}).get('port', 10808)))

        proxy_layout.addRow(self.proxy_enabled)
        proxy_layout.addRow("Type:", self.proxy_type)
        proxy_layout.addRow("Host:", self.proxy_host)
        proxy_layout.addRow("Port:", self.proxy_port)

        # v31.1: '🌐 Test Proxy Connection' moved to the global 'More' menu.

        self.tab_widget.addTab(self._wrap_scroll(proxy_tab), "🌐 Proxy")

        # ---- Tab 3: Vault ----
        vault_tab = QWidget()
        vault_layout = QVBoxLayout(vault_tab)
        self.vault_combo = QComboBox()
        self.vault_combo.setEditable(True)
        self.vault_combo.setInsertPolicy(QComboBox.InsertPolicy.InsertAtTop)
        self.populate_vaults()

        vault_buttons = QHBoxLayout()
        browse_btn = QPushButton("📂 Browse...")
        browse_btn.clicked.connect(self.browse_vault)
        self._style_btn(browse_btn, 'secondary')
        remove_btn = QPushButton("🗑️ Remove")
        remove_btn.clicked.connect(self.remove_vault)
        self._style_btn(remove_btn, 'danger')
        vault_buttons.addWidget(browse_btn)
        vault_buttons.addWidget(remove_btn)
        vault_buttons.addStretch()

        # v31.1: '✅ Validate Vault' moved to the global 'More' menu.

        vault_layout.setSpacing(8)
        vault_layout.addWidget(QLabel("Select your Obsidian vault:"))
        vault_layout.addWidget(self.vault_combo)
        vault_layout.addLayout(vault_buttons)
        self.tab_widget.addTab(self._wrap_scroll(vault_tab), "📁 Vault")

        # ---- Tab 4: Ollama / Cloud LLM ----
        # v26 — Fix 4: tab now hosts TWO providers. Radio buttons at the top
        # toggle between the local-Ollama group and the cloud-API group.
        # The selected provider is persisted in config['llm_provider'] and
        # read by ProcessingWorker._llm_analyze to decide which backend to
        # call. Default is 'ollama' so existing users see no change.
        ollama_tab = QWidget()
        ollama_layout = QVBoxLayout(ollama_tab)
        ollama_layout.setSpacing(10)

        # --- Provider selector (radio buttons) ---
        provider_row = QHBoxLayout()
        provider_row.addWidget(QLabel("<b>LLM Provider:</b>"))
        self.llm_provider_ollama = QRadioButton("🧠 Local Ollama")
        self.llm_provider_ollama.setToolTip(
            "Use a local Ollama server (http://localhost:11434 by default).\n"
            "No API key required — runs entirely on your machine."
        )
        self.llm_provider_cloud = QRadioButton("☁️ Cloud API (OpenAI compatible)")
        self.llm_provider_cloud.setToolTip(
            "Use an OpenAI-compatible cloud API (OpenAI, OpenRouter, Together, etc.).\n"
            "Requires an API key. Sends repo data over the internet."
        )
        # Default: ollama (backward compat)
        saved_provider = self.config.get('llm_provider', 'ollama')
        if saved_provider == 'cloud':
            self.llm_provider_cloud.setChecked(True)
        else:
            self.llm_provider_ollama.setChecked(True)
        provider_row.addWidget(self.llm_provider_ollama)
        provider_row.addWidget(self.llm_provider_cloud)
        provider_row.addStretch()
        ollama_layout.addLayout(provider_row)

        # --- Local Ollama group (existing fields, now inside a QGroupBox) ---
        self.ollama_group = QGroupBox("🧠 Local Ollama")
        ollama_form = QFormLayout(self.ollama_group)
        self.ollama_url = QLineEdit(self.config.get('ollama', {}).get('base_url', 'http://localhost:11434'))
        ollama_form.addRow("Ollama URL:", self.ollama_url)

        # Model dropdown (editable combo so user can type a custom model name
        # OR pick from the list of available models pulled from the server).
        model_row = QHBoxLayout()
        self.ollama_model = QComboBox()
        self.ollama_model.setEditable(True)
        self.ollama_model.setInsertPolicy(QComboBox.InsertPolicy.InsertAtTop)
        # Pre-fill with saved model + a common default
        saved_model = self.config.get('ollama', {}).get('model', 'qwythos-9b')
        self.ollama_model.addItem(saved_model)
        self.ollama_model.setCurrentText(saved_model)
        model_row.addWidget(self.ollama_model, 1)

        refresh_models_btn = QPushButton("🔄 Refresh Models")
        refresh_models_btn.clicked.connect(self.refresh_ollama_models)
        self._style_btn(refresh_models_btn, 'secondary')
        model_row.addWidget(refresh_models_btn)
        ollama_form.addRow("Model:", model_row)

        # Buttons: start server (the Ollama test action lives in the global
        # 'More' overflow menu — v31.1 button-hierarchy spec).
        ollama_buttons = QHBoxLayout()
        start_ollama_btn = QPushButton("🚀 Start Ollama Server")
        start_ollama_btn.clicked.connect(self.start_ollama_server)
        self._style_btn(start_ollama_btn, 'secondary')
        ollama_buttons.addWidget(start_ollama_btn)
        ollama_buttons.addStretch()
        ollama_form.addRow("", ollama_buttons)
        ollama_layout.addWidget(self.ollama_group)

        # --- Cloud API group (v26 — Fix 4) ---
        self.cloud_group = QGroupBox("☁️ Cloud API (OpenAI compatible)")
        cloud_form = QFormLayout(self.cloud_group)
        self.cloud_api_url = QLineEdit(self.config.get('cloud_api_url', 'https://api.openai.com/v1'))
        self.cloud_api_url.setPlaceholderText("https://api.openai.com/v1")
        cloud_form.addRow("API URL:", self.cloud_api_url)

        self.cloud_api_key = QLineEdit(self.config.get('cloud_api_key', ''))
        self.cloud_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.cloud_api_key.setPlaceholderText("sk-... (kept locally in config.json)")
        cloud_form.addRow("API Key:", self.cloud_api_key)

        self.cloud_model = QLineEdit(self.config.get('cloud_model', 'gpt-4o-mini'))
        self.cloud_model.setPlaceholderText("gpt-4o-mini")
        cloud_form.addRow("Model:", self.cloud_model)

        # v31.1: '🔌 Test Connection' moved to the global 'More' menu.
        ollama_layout.addWidget(self.cloud_group)

        # --- Toggle visibility based on selected provider ---
        def _toggle_llm_provider(*_args):
            is_ollama = self.llm_provider_ollama.isChecked()
            self.ollama_group.setVisible(is_ollama)
            self.cloud_group.setVisible(not is_ollama)
        self.llm_provider_ollama.toggled.connect(_toggle_llm_provider)
        # Apply initial state (must be after both groups are constructed).
        _toggle_llm_provider()

        # v31.1: no filler stretch — content keeps its natural height at the
        # top of the scrollable tab; the window never resizes.
        self.tab_widget.addTab(self._wrap_scroll(ollama_tab), "🧠 LLM")

        # ---- Tab: Input Mode (PRIMARY TAB — shown first on launch) ----
        input_tab = QWidget()
        input_layout = QVBoxLayout(input_tab)
        input_layout.setSpacing(8)

        # --- Mode selector row: compact radio buttons + help button ---
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("<b>Mode:</b>"))
        self.mode_telegram = QRadioButton("ID Range")
        self.mode_telegram.setToolTip("Telegram Messages (by ID range or offset)")
        self.mode_keyword = QRadioButton("Markers")
        self.mode_keyword.setToolTip("Telegram Messages (by Keyword Markers) — paste a unique code into Saved Messages")
        self.mode_import = QRadioButton("Import .txt")
        self.mode_import.setToolTip("Import GitHub URLs from a .txt file")
        self.mode_single = QRadioButton("Single Msg")
        self.mode_single.setToolTip("Fetch a single Telegram message by ID")
        # Default to keyword/marker mode (the hash workflow is the primary use case)
        self.mode_keyword.setChecked(True)
        mode_row.addWidget(self.mode_telegram)
        mode_row.addWidget(self.mode_keyword)
        mode_row.addWidget(self.mode_import)
        mode_row.addWidget(self.mode_single)
        mode_row.addStretch()
        help_btn = QPushButton("?")
        help_btn.setToolTip("Show usage instructions")
        help_btn.setFixedWidth(28)
        help_btn.setCursor(Qt.CursorShape.WhatsThisCursor)
        help_btn.clicked.connect(self.show_input_help)
        mode_row.addWidget(help_btn)
        input_layout.addLayout(mode_row)

        # --- Message Range group (Telegram by ID mode) ---
        # Compact 2-column layout with offset behind an 'Advanced' toggle.
        self.range_group = QGroupBox("Message Range (Telegram)")
        range_layout = QVBoxLayout()
        # ID range row: From ID | To ID (placeholders, no labels)
        self.range_row_widget = QWidget()
        range_row = QHBoxLayout()
        range_row.setContentsMargins(0, 0, 0, 0)
        self.range_from = QLineEdit()
        self.range_from.setPlaceholderText("From ID")
        self.range_to = QLineEdit()
        self.range_to.setPlaceholderText("To ID")
        range_row.addWidget(self.range_from)
        range_row.addWidget(self.range_to)
        self.range_row_widget.setLayout(range_row)
        range_layout.addWidget(self.range_row_widget)
        # Advanced: offset toggle (hides ID range, shows offset fields)
        self.offset_toggle = QCheckBox("Advanced: use offset instead of ID range")
        range_layout.addWidget(self.offset_toggle)
        # Offset row (hidden by default)
        self.offset_row_widget = QWidget()
        offset_row = QHBoxLayout()
        offset_row.setContentsMargins(0, 0, 0, 0)
        self.offset_start = QLineEdit()
        self.offset_start.setPlaceholderText("Offset from")
        self.offset_count = QLineEdit()
        self.offset_count.setPlaceholderText("Count")
        offset_row.addWidget(self.offset_start)
        offset_row.addWidget(self.offset_count)
        self.offset_row_widget.setLayout(offset_row)
        self.offset_row_widget.setVisible(False)
        range_layout.addWidget(self.offset_row_widget)
        def _toggle_offset(checked):
            self.offset_row_widget.setVisible(checked)
            self.range_row_widget.setVisible(not checked)
        self.offset_toggle.toggled.connect(_toggle_offset)
        self.range_group.setLayout(range_layout)
        input_layout.addWidget(self.range_group)

        # --- Marker group (Telegram by Keyword Markers mode) — compact, one row ---
        self.marker_group = QGroupBox("Find Messages by Marker")
        marker_layout = QVBoxLayout()
        # Hash row: [hash field] [Generate] [Copy] [Find by Marker]
        hash_row = QHBoxLayout()
        self.marker_hash = QLineEdit()
        self.marker_hash.setReadOnly(True)
        self.marker_hash.setPlaceholderText("Click 'Generate' to create a marker code")
        hash_row.addWidget(self.marker_hash, 1)
        gen_hash_btn = QPushButton("🎲 Generate")
        gen_hash_btn.clicked.connect(self.generate_marker_hash)
        self._style_btn(gen_hash_btn, 'secondary')
        hash_row.addWidget(gen_hash_btn)
        copy_hash_btn = QPushButton("📋 Copy")
        copy_hash_btn.clicked.connect(self.copy_marker_hash)
        self._style_btn(copy_hash_btn, 'secondary')
        hash_row.addWidget(copy_hash_btn)
        find_by_hash_btn = QPushButton("🔍 Find by Marker")
        find_by_hash_btn.clicked.connect(self.find_by_marker)
        # v31.1: the Input tab's ONE filled primary button.
        self._style_btn(find_by_hash_btn, 'primary')
        hash_row.addWidget(find_by_hash_btn)
        marker_layout.addLayout(hash_row)
        # Advanced: custom keywords toggle (expands to show keyword fields)
        self.kw_toggle = QCheckBox("Advanced: custom keywords")
        marker_layout.addWidget(self.kw_toggle)
        # Custom keywords row (hidden by default)
        self.kw_widget = QWidget()
        kw_row = QHBoxLayout()
        kw_row.setContentsMargins(0, 0, 0, 0)
        self.keyword_start = QLineEdit()
        self.keyword_start.setPlaceholderText("Start keyword")
        self.keyword_end = QLineEdit()
        self.keyword_end.setPlaceholderText("End keyword")
        find_by_kw_btn = QPushButton("🔍 Find by Keywords")
        find_by_kw_btn.clicked.connect(self.find_keyword_ids)
        self._style_btn(find_by_kw_btn, 'secondary')
        kw_row.addWidget(self.keyword_start)
        kw_row.addWidget(self.keyword_end)
        kw_row.addWidget(find_by_kw_btn)
        self.kw_widget.setLayout(kw_row)
        self.kw_widget.setVisible(False)
        marker_layout.addWidget(self.kw_widget)
        self.kw_toggle.toggled.connect(self.kw_widget.setVisible)
        self.marker_group.setLayout(marker_layout)
        input_layout.addWidget(self.marker_group)

        # --- Single Message group (Single Message ID mode) ---
        self.single_group = QGroupBox("Single Message")
        single_layout = QHBoxLayout()
        self.single_id = QLineEdit()
        self.single_id.setPlaceholderText("Enter message ID")
        single_layout.addWidget(self.single_id)
        self.single_group.setLayout(single_layout)
        input_layout.addWidget(self.single_group)

        # --- Import File group (Import mode) ---
        self.import_group = QGroupBox("Import File")
        import_layout = QHBoxLayout()
        self.import_file = QLineEdit()
        self.import_file.setPlaceholderText("Path to .txt file")
        import_btn = QPushButton("📄 Select...")
        import_btn.clicked.connect(self.select_import_file)
        self._style_btn(import_btn, 'secondary')
        import_layout.addWidget(self.import_file, 1)
        import_layout.addWidget(import_btn)
        self.import_group.setLayout(import_layout)
        input_layout.addWidget(self.import_group)

        # v31.1: every tab scrolls independently inside the fixed window.
        input_scroll = self._wrap_scroll(input_tab)
        self.tab_widget.addTab(input_scroll, "📥 Input")

        # ---- Tab: Dashboard (added last; remains the last tab after Input is moved to 0) ----
        dash_tab = QWidget()
        dash_layout = QVBoxLayout(dash_tab)

        dash_btn_row = QHBoxLayout()
        self.refresh_dash_btn = QPushButton("🔄 Refresh Dashboard")
        # v31.1: the Dashboard tab's ONE filled primary button.
        self._style_btn(self.refresh_dash_btn, 'primary')
        self.refresh_dash_btn.clicked.connect(self.update_dashboard)
        dash_btn_row.addWidget(self.refresh_dash_btn)

        # v22 Feature 6: Batch Undo — deletes the .md files written by the
        # most recent batch (listed in `<vault>/_undo_last_batch.txt`).
        self.undo_batch_btn = QPushButton("↩️ Undo Last Batch")
        # v31.1: filled danger — destructive action (deletes the last
        # batch's note files).
        self._style_btn(self.undo_batch_btn, 'danger')
        self.undo_batch_btn.clicked.connect(self.undo_last_batch)
        dash_btn_row.addWidget(self.undo_batch_btn)

        # v31.1: '🔍 Verify Vault' moved to the global 'More' overflow menu.

        dash_btn_row.addStretch()
        dash_layout.addLayout(dash_btn_row)

        # v31.1: the results panel is the tab's ONE growable region — it fills
        # the leftover vertical space and scrolls independently (the global
        # QSS already renders read-only QTextEdit in Consolas 12px mono).
        self.dashboard_text = QTextEdit()
        self.dashboard_text.setReadOnly(True)
        self.dashboard_text.setPlaceholderText("Click 'Refresh Dashboard' to scan the vault and view statistics.")
        self.dashboard_text.setMinimumHeight(120)
        dash_layout.addWidget(self.dashboard_text)
        dash_layout.setStretchFactor(self.dashboard_text, 1)

        self.tab_widget.addTab(self._wrap_scroll(dash_tab), "📊 Dashboard")

        # ---- Tab: Bot Queue ----
        # Dedicated Telegram bot inbox — forward repos to your bot, the app
        # reads them via Telethon (no external backend needed).
        bot_tab = QWidget()
        bot_layout = QVBoxLayout(bot_tab)
        bot_layout.setSpacing(8)

        bot_header = QLabel(
            "🤖 Bot Queue\n"
            "Forward GitHub repo messages to your dedicated bot (@githubfetcherbot).\n"
            "Click 'Check Queue' to fetch pending repos, then 'Process All'."
        )
        bot_header.setWordWrap(True)
        # v31.1: solid theme-aware callout (objectName rule in the theme QSS
        # paints it #F4F4F5 in light / #27272A in dark — rgba fills break
        # dark mode in Qt's QSS compositor).
        bot_header.setObjectName("info_header")
        bot_layout.addWidget(bot_header)

        # Bot username + token inputs
        token_row = QHBoxLayout()
        token_row.addWidget(QLabel("Bot Username:"))
        self.bot_username = QLineEdit(self.config.get('bot_username', 'githubfetcherbot'))
        self.bot_username.setPlaceholderText("e.g. githubfetcherbot")
        token_row.addWidget(self.bot_username, 1)
        bot_layout.addLayout(token_row)

        token_row2 = QHBoxLayout()
        token_row2.addWidget(QLabel("Bot Token:"))
        self.bot_token = QLineEdit(self.config.get('bot_token', ''))
        self.bot_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.bot_token.setPlaceholderText("e.g. 123456789:AAF... (optional)")
        token_row2.addWidget(self.bot_token, 1)
        save_token_btn = QPushButton("💾 Save")
        save_token_btn.clicked.connect(self.save_config)
        token_row2.addWidget(save_token_btn)
        bot_layout.addLayout(token_row2)

        # Queue controls — v31.1 three-variant hierarchy: ONE filled primary
        # (Process All) + outlined secondary actions. Infrequent actions
        # (export / verify / retry) moved to the global 'More' menu.
        queue_btn_row = QHBoxLayout()
        self.check_queue_btn = QPushButton("📬 Check Queue")
        self._style_btn(self.check_queue_btn, 'secondary')
        self.check_queue_btn.clicked.connect(self.check_bot_queue)
        queue_btn_row.addWidget(self.check_queue_btn)

        # Pending badge (appears after Check Queue, shows repos not yet in
        # the vault). v31.1: zinc — a pending COUNT is not an error; red is
        # reserved for actual failures (WCAG-safe neutral).
        self.pending_badge = QLabel("")
        self.pending_badge.setStyleSheet(
            "background-color: #6C6480; color: white; padding: 4px 8px; "
            "border-radius: 10px; font-size: 12px; font-weight: bold;"
        )
        self.pending_badge.setVisible(False)
        queue_btn_row.addWidget(self.pending_badge)

        process_queue_btn = QPushButton("🚀 Process All")
        # v31.1: the Bot tab's ONE filled primary button.
        self._style_btn(process_queue_btn, 'primary')
        process_queue_btn.clicked.connect(self.process_bot_queue)
        queue_btn_row.addWidget(process_queue_btn)

        # v25 pre-flight: "Process New" — fetches only messages newer than
        # the last successfully-processed message ID (saved to config.json
        # after each verified-clean batch). Lets the user run incremental
        # batches without re-processing already-handled repos.
        process_new_btn = QPushButton("📬 Process New")
        self._style_btn(process_new_btn, 'secondary')
        process_new_btn.setToolTip(
            "Fetch only messages newer than the last successfully-processed batch.\n"
            "Use this for daily incremental runs — skips already-processed repos."
        )
        process_new_btn.clicked.connect(self.process_new_bot_queue)
        queue_btn_row.addWidget(process_new_btn)

        # Mark All Read — hidden by default, appears only after verify passes
        self.mark_all_read_btn = QPushButton("✓ Mark All as Read")
        self._style_btn(self.mark_all_read_btn, 'secondary')
        self.mark_all_read_btn.clicked.connect(self.clear_bot_queue)
        self.mark_all_read_btn.setVisible(False)
        self.mark_all_read_btn.setToolTip(
            "Marks ALL bot messages as read.\n"
            "Only available after '✅ Verify All Processed' confirms 0 missing.\n"
            "Use this when you've verified everything is in the vault."
        )
        queue_btn_row.addWidget(self.mark_all_read_btn)

        # v26 — Fix 6: '✅ Verify All Processed', '📋 Export All Links' and
        # '🔄 Retry Failed' moved to the global 'More' overflow menu (v31.1).

        queue_btn_row.addStretch()
        bot_layout.addLayout(queue_btn_row)

        # Queue display — the tab's ONE growable region: fills the leftover
        # vertical space, scrolls independently, no fixed-height cap.
        bot_layout.addWidget(QLabel("Pending Repos:"))
        self.queue_display = QTextEdit()
        self.queue_display.setReadOnly(True)
        self.queue_display.setMinimumHeight(120)
        self.queue_display.setPlaceholderText("Click 'Check Queue' to fetch pending repos from your bot...")
        bot_layout.addWidget(self.queue_display)
        bot_layout.setStretchFactor(self.queue_display, 1)

        self.tab_widget.addTab(self._wrap_scroll(bot_tab), "🤖 Bot")

        # ---- Tab: Sources (RSS/Reddit) ----
        # Lets the user fetch GitHub URLs from RSS feeds or Reddit .json
        # endpoints (free, no API key needed) and process them like any
        # other URL list.
        sources_tab = QWidget()
        sources_layout = QVBoxLayout(sources_tab)

        sources_label = QLabel(
            "📡 Additional Sources\n"
            "Fetch GitHub URLs from RSS feeds or Reddit (no API key needed).\n"
            "Reddit uses the free .json endpoint (e.g. https://reddit.com/r/programming.json)"
        )
        sources_label.setWordWrap(True)
        # v31.1: solid theme-aware callout (see info_header in the theme QSS).
        sources_label.setObjectName("info_header")
        sources_layout.addWidget(sources_label)

        # URL input
        url_row = QHBoxLayout()
        url_row.addWidget(QLabel("URL:"))
        self.sources_url = QLineEdit()
        self.sources_url.setPlaceholderText("https://reddit.com/r/programming.json  OR  https://hnrss.org/frontpage")
        url_row.addWidget(self.sources_url, 1)

        fetch_sources_btn = QPushButton("🔍 Fetch URLs")
        self._style_btn(fetch_sources_btn, 'secondary')
        fetch_sources_btn.clicked.connect(self.fetch_from_sources)
        url_row.addWidget(fetch_sources_btn)
        sources_layout.addLayout(url_row)

        # Results area — the tab's ONE growable region (fills leftover
        # space, scrolls independently, no fixed-height cap).
        sources_layout.addWidget(QLabel("Fetched GitHub URLs:"))
        self.sources_results = QTextEdit()
        self.sources_results.setReadOnly(True)
        self.sources_results.setMinimumHeight(120)
        sources_layout.addWidget(self.sources_results)
        sources_layout.setStretchFactor(self.sources_results, 1)

        # Process button — the Sources tab's ONE filled primary button.
        process_sources_btn = QPushButton("🚀 Process Fetched URLs")
        self._style_btn(process_sources_btn, 'primary')
        process_sources_btn.clicked.connect(self.process_sources_urls)
        sources_layout.addWidget(process_sources_btn)

        # v31.1: 📡 (feeds) — Proxy keeps 🌐. Two different destinations no
        # longer share one icon.
        self.tab_widget.addTab(self._wrap_scroll(sources_tab), "📡 Sources")

        # ---- Tab: Backup (local folder + timestamped zip) ----
        # v32.2: wrap in the scroll area like every other tab — the four
        # sections' natural height exceeds the fixed tab pane, which
        # previously clipped each section's lower rows (buttons, toggles,
        # the dashboard link).
        backup_tab = self._create_backup_tab()
        self.tab_widget.addTab(self._wrap_scroll(backup_tab), "💾 Backup")

        # Make the Bot tab the PRIMARY view (auto-check runs there on startup)
        # Move Bot tab to position 0, Input to position 1
        bot_idx = self.tab_widget.indexOf(self.findChild(QWidget, "bot_tab")) if self.findChild(QWidget, "bot_tab") else -1
        if bot_idx > 0:
            self.tab_widget.tabBar().moveTab(bot_idx, 0)
        # Move Input tab to position 1 (v31.1: tabs wrap in QScrollArea, so
        # look up the scroll container, not the inner content widget)
        input_idx = self.tab_widget.indexOf(input_scroll)
        if input_idx > 1:
            self.tab_widget.tabBar().moveTab(input_idx, 1)
        self.tab_widget.setCurrentIndex(0)  # Bot tab is primary

        left_layout.addWidget(self.tab_widget)

        # Action bar (outside tabs, always visible) — v31.1 hierarchy: ONE
        # filled primary (Start), ONE filled danger (Stop), ONE 'More'
        # overflow menu holding the infrequent actions (export / verify /
        # retry / recategorize / test), plus the labeled proxy status dot.
        action_layout = QHBoxLayout()
        action_layout.setSpacing(8)
        self.start_btn = QPushButton("🚀 Start Processing")
        self._style_btn(self.start_btn, 'primary')
        self.start_btn.clicked.connect(self.start_processing)

        self.stop_btn = QPushButton("🛑 Stop")
        self._style_btn(self.stop_btn, 'danger')
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_processing)

        action_layout.addWidget(self.start_btn)
        action_layout.addWidget(self.stop_btn)
        action_layout.addSpacing(16)  # visual separation: run controls | utilities

        # ---- 'More' overflow menu (v31.1: one menu for infrequent actions) ----
        self.more_btn = QToolButton()
        self.more_btn.setText("More ▾")
        self.more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_btn.setToolTip("Tests, verification, export, retry and settings")
        more_menu = QMenu(self.more_btn)
        more_menu.addAction("🔗 Test All Connections", self.test_all)
        more_menu.addSeparator()
        more_menu.addAction("🔑 Test Telegram & GitHub", self.test_telegram_github)
        more_menu.addAction("🌐 Test Proxy Connection", self.test_proxy)
        more_menu.addAction("🧠 Test Ollama", self.test_ollama)
        more_menu.addAction("🔌 Test Cloud API", self.test_cloud_llm)
        more_menu.addSeparator()
        more_menu.addAction("✅ Validate Vault", self.test_vault)
        more_menu.addAction("🔍 Verify Vault", self.verify_vault)
        more_menu.addAction("📁 Recategorize Notes", self.recategorize_notes)
        more_menu.addSeparator()
        more_menu.addAction("✅ Verify All Processed", self.verify_all_bot_links)
        more_menu.addAction("📋 Export All Links", self.export_all_bot_links)
        more_menu.addAction("🔄 Retry Failed", self.retry_failed_repos)
        self.backup_export_btn = more_menu.addAction("📤 Export Backup ZIP")
        self.backup_export_btn.triggered.connect(self._backup_export_zip)
        more_menu.addSeparator()
        more_menu.addAction("👁️ Preview Messages", self.preview_messages)
        more_menu.addAction("📊 Open Dashboard", self._open_dashboard_browser)
        # Settings submenu — the dark-mode toggle is a display preference,
        # not a batch action, so it lives under Settings (v31.1 spec).
        settings_menu = more_menu.addMenu("⚙️ Settings")
        self.theme_btn = settings_menu.addAction("🌙 Dark Mode")
        self.theme_btn.setCheckable(True)
        self.theme_btn.setChecked(bool(self.config.get('dark_mode', False)))
        self.theme_btn.triggered.connect(self.toggle_theme)
        self.more_btn.setMenu(more_menu)
        self._style_btn(self.more_btn, 'secondary')
        action_layout.addWidget(self.more_btn)
        action_layout.addSpacing(8)

        # v32.1 — ALWAYS-VISIBLE light/dark toggle. v31.1 hid the toggle in
        # the More → Settings submenu and users read the app as "dark only".
        # One compact icon button in the action row shows the mode you'll
        # switch TO (🌙 in light mode, ☀️ in dark mode); the tooltip spells
        # it out. Synced by _sync_theme_toggle_btn() on init + every flip.
        self.theme_toggle_btn = QPushButton("🌙")
        self.theme_toggle_btn.setFixedSize(38, 36)
        self.theme_toggle_btn.setToolTip("Switch to dark mode (current: Light)")
        self.theme_toggle_btn.clicked.connect(self.toggle_theme)
        self._style_btn(self.theme_toggle_btn, 'icon')
        action_layout.addWidget(self.theme_toggle_btn)

        # v22 Feature 7: Proxy Health Monitor — small colored dot + TEXT label
        # (v31.1: color alone never conveys state — WCAG 1.4.1) that reflect
        # whether the configured proxy is reachable. Updated every 60 seconds
        # by a QTimer (see __init__ end). Non-blocking: the check uses a 2s
        # socket timeout and runs on the GUI thread.
        self.proxy_status_label = QLabel("⚪")
        self.proxy_status_label.setFixedSize(16, 16)
        self.proxy_status_label.setToolTip("Proxy status — checking...")
        self.proxy_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        action_layout.addWidget(self.proxy_status_label)
        self.proxy_status_text = QLabel("Checking…")
        self.proxy_status_text.setToolTip("Proxy status — checking...")
        action_layout.addWidget(self.proxy_status_text)

        action_layout.addStretch()
        left_layout.addLayout(action_layout)

        # Progress bar — determinate, visible ONLY while a batch job runs
        # (v31.1 spec); labeled "Processing X of Y — repo-name" via
        # update_progress()/update_status().
        self.progress_bar = QProgressBar()
        self.progress_bar.setFormat("Ready")
        self.progress_bar.setFixedHeight(20)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setVisible(False)
        left_layout.addWidget(self.progress_bar)

        # NO addStretch() here — removes the white space in the middle.
        # The left panel sizes to its content; the log panel takes the rest.

        # Bottom panel: Log (collapsible — hidden by default)
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # Log toggle button (shown in the action row, toggles log visibility)
        self.log_toggle_btn = QPushButton("📋 Show Log")
        self.log_toggle_btn.setFixedHeight(28)
        self._style_btn(self.log_toggle_btn, 'ghost')
        self.log_toggle_btn.setCheckable(True)
        left_layout.addWidget(self.log_toggle_btn)

        log_group = QGroupBox()
        log_group_layout = QVBoxLayout()
        log_group.setContentsMargins(4, 4, 4, 4)

        # Log header with filter buttons, search box, and clear button
        log_header = QHBoxLayout()

        # Filter buttons (no duplicate "Log" label — group box border is enough)
        self.log_filter_all = QPushButton("All")
        self.log_filter_all.setCheckable(True)
        self.log_filter_all.setChecked(True)
        self.log_filter_errors = QPushButton("Errors")
        self.log_filter_errors.setCheckable(True)
        self.log_filter_warnings = QPushButton("Warnings")
        self.log_filter_warnings.setCheckable(True)
        self.log_filter_success = QPushButton("Success")
        self.log_filter_success.setCheckable(True)

        self.log_filter_all.clicked.connect(lambda: self._set_log_filter("all"))
        self.log_filter_errors.clicked.connect(lambda: self._set_log_filter("error"))
        self.log_filter_warnings.clicked.connect(lambda: self._set_log_filter("warning"))
        self.log_filter_success.clicked.connect(lambda: self._set_log_filter("success"))

        for btn in [self.log_filter_all, self.log_filter_errors, self.log_filter_warnings, self.log_filter_success]:
            btn.setStyleSheet("padding: 4px 8px; font-size: 12px;")
            log_header.addWidget(btn)

        log_header.addStretch()

        # Search box
        self.log_search = QLineEdit()
        self.log_search.setPlaceholderText("🔍 Search log...")
        self.log_search.setMaximumWidth(200)
        self.log_search.textChanged.connect(self._filter_log)
        log_header.addWidget(self.log_search)

        # Clear button
        clear_log_btn = QPushButton("🗑️")
        clear_log_btn.setFixedWidth(35)
        clear_log_btn.setToolTip("Clear log")
        clear_log_btn.clicked.connect(self._clear_log)
        clear_log_btn.setStyleSheet("padding: 4px; font-size: 12px;")
        log_header.addWidget(clear_log_btn)

        log_group_layout.addLayout(log_header)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        # Mono 12px comes from the global QSS (QTextEdit:read-only) — the
        # log panel is the window's ONE growable region and always scrolls.
        self.log_text.setMinimumHeight(150)  # ensure log is always visible
        log_group_layout.addWidget(self.log_text)
        log_group.setLayout(log_group_layout)
        right_layout.addWidget(log_group)

        # Log panel hidden by default — toggle button shows/hides it
        right_widget.setVisible(False)
        self.log_toggle_btn.clicked.connect(self._toggle_log_panel)

        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        # When log is hidden, top panel takes full height
        splitter.setSizes([600, 0])
        splitter.setStretchFactor(0, 1)  # top: takes all space
        splitter.setStretchFactor(1, 0)  # bottom: hidden by default

        main_layout.addWidget(splitter)

        # Connect mode changes
        self.mode_telegram.toggled.connect(self.update_mode)
        self.mode_keyword.toggled.connect(self.update_mode)
        self.mode_import.toggled.connect(self.update_mode)
        self.mode_single.toggled.connect(self.update_mode)
        self.update_mode()

        # Vault change
        self.vault_combo.currentTextChanged.connect(self.on_vault_changed)

        # Load config into UI
        self.load_ui_config()

        # Apply light theme palette
        self.apply_light_theme()

        # If the user previously enabled dark mode, re-apply it now (overrides
        # the light theme set above) and sync the Settings-menu check state.
        if self.config.get('dark_mode', False):
            self._dark_mode = True
            self.apply_dark_theme()
            self.theme_btn.setChecked(True)
            self._refresh_button_styles()  # outlined variant is theme-aware
            # v32.2: the status dots were styled with LIGHT colors during
            # _build_ui (dark_mode is only set here) — re-run so a user
            # starting in dark mode gets dark-palette dots immediately.
            self._vaultseal_refresh_status()
            self._goodrepos_refresh_status()
            self._backup_refresh_status()
        self._sync_theme_toggle_btn()  # v32.1: header toggle reflects the restored mode

        # Auto-check bot queue on startup (after proxy validation)
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(2000, self._startup_auto_check)
