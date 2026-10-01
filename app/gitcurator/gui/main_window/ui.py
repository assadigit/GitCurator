"""MainWindow UiMixin — methods moved verbatim from the MainWindow in gitcurator/gui/app.py (branch refactor/gui-app-split; see docs/history/REFACTOR_PLAN.md)."""

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

try:
    import colorama
    from colorama import Fore, Style
    # (colorama.init stays in app.py — once per process, as at baseline)
except ImportError:
    # Fallback if colorama missing
    class Fore:
        GREEN = ''; YELLOW = ''; RED = ''; CYAN = ''; WHITE = ''; RESET = ''
    Style = Fore
    print("colorama not installed; colored output disabled.")

_APP_DIR = APP_DIR

from gitcurator.gui.dead_links import DEAD_LINK_THRESHOLD

from gitcurator.gui.dialogs import SettingsDialog

class UiMixin:
    """UiMixin"""

    def initUI(self):
        # Load bundled Inter font (if present) before any widgets are created so
        # the global stylesheet's `font-family: 'Inter'` resolves correctly.
        self._load_fonts()
        self.setWindowTitle("GitCurator 🚀")
        # v0.08 — Fix (owner report: "the app is unnecessarily long — too
        # much width, low height; I prefer a ratio like 6×4"): the v33
        # 1000×375 window was a 2.67:1 ultra-wide strip. Now 900×600 — an
        # exact 6:4 (3:2) ratio: 100px narrower, 225px taller. The extra
        # height goes to the log panel (the main view's ONE growable
        # region, stretch 1), so long batches show far more history
        # without scrolling.
        self.setGeometry(100, 100, 900, 600)
        self.setFixedSize(900, 600)

        central = QWidget()
        self.setCentralWidget(central)
        # v33 wireframe redesign: the main view is ONE focused screen —
        # logo lockup, two hero CTAs (SYNC ⇄ STOP, Test Connectivity), a
        # labeled progress row and the always-visible Progress Logs panel.
        # EVERY former tab moved to the Settings window (SettingsDialog,
        # opened from the ⚙️ button top-right).
        main_layout = QVBoxLayout(central)
        # v0.07 rhythm: one spacing scale (10px between the three bands —
        # top bar / CTA card / pipeline strip / log) instead of the old
        # uneven 8px gaps; breathing room comes from the margins, not from
        # dead space inside an empty log box.
        main_layout.setContentsMargins(20, 12, 20, 12)
        main_layout.setSpacing(10)

        # Every former tab page is collected here and handed to the Settings
        # window at the end of initUI. The page-creation code below is
        # UNCHANGED — only the container the pages land in changed.
        self._settings_pages: List[Tuple[QWidget, str]] = []

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
        self.test_github_btn = QPushButton("Test GitHub Token")
        self.test_github_btn.setToolTip("Validate the token and show the GitHub account it belongs to")
        self.test_github_btn.clicked.connect(self.test_github_token)
        self._style_btn(self.test_github_btn, 'secondary')
        creds_layout.addRow("", self.test_github_btn)

        # v31.1: '🔗 Test Telegram & GitHub' moved to the global 'More'
        # overflow menu (infrequent actions: export / verify / retry /
        # recategorize / test).

        # About Me Wizard button — generates about_me.md to give the LLM context
        about_me_btn = QPushButton("About Me Wizard")
        about_me_btn.clicked.connect(self.show_about_me_wizard)
        about_me_btn.setToolTip("Generate about_me.md to give the LLM context about who you are")
        self._style_btn(about_me_btn, 'secondary')
        creds_layout.addRow("", about_me_btn)

        self._settings_pages.append((self._wrap_scroll(creds_tab), "Credentials"))

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

        # v0.19.0 — the blocked-web fix rides the SAME proxy: x.com / t.co /
        # youtu.be connections are refused on the owner's direct line
        # (poisoned DNS), so the Websites pipeline fetches through the
        # proxy too. Default ON (a configured proxy is there to be used);
        # untick to fetch direct.
        self.proxy_use_for_web = QCheckBox(
            "Use this proxy for web fetches too (Websites pipeline — "
            "x.com / YouTube need it)")
        self.proxy_use_for_web.setChecked(
            bool(self.config.get('proxy', {}).get('use_for_web', True)))
        self.proxy_use_for_web.setToolTip(
            "When ON, every website fetch goes through this proxy and DNS "
            "is resolved at the proxy exit. Fixes the connection-refused "
            "failures on blocked sites (x.com, t.co, youtu.be). Loopback "
            "(Ollama / llama.cpp) is NEVER proxied.")

        proxy_layout.addRow(self.proxy_enabled)
        proxy_layout.addRow("Type:", self.proxy_type)
        proxy_layout.addRow("Host:", self.proxy_host)
        proxy_layout.addRow("Port:", self.proxy_port)
        proxy_layout.addRow(self.proxy_use_for_web)

        # v31.1: '🌐 Test Proxy Connection' moved to the global 'More' menu.

        self._settings_pages.append((self._wrap_scroll(proxy_tab), "Proxy"))

        # ---- Tab 3: Vault ----
        vault_tab = QWidget()
        vault_layout = QVBoxLayout(vault_tab)
        self.vault_combo = QComboBox()
        self.vault_combo.setEditable(True)
        self.vault_combo.setInsertPolicy(QComboBox.InsertPolicy.InsertAtTop)
        # v33.1: cap the combo's minimum width — without this, one long vault
        # path (e.g. "G:/Docs/…/Github Projects(Automated)") sizes the combo's
        # min-size hint to the full string, pushing the page wider than the
        # Settings viewport. The popup still shows full paths.
        self.vault_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.vault_combo.setMinimumContentsLength(28)
        self.populate_vaults()

        vault_buttons = QHBoxLayout()
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self.browse_vault)
        self._style_btn(browse_btn, 'secondary')
        remove_btn = QPushButton("Remove")
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

        # v0.10.0 — Phase 1 (vault settings, SPEC §6 Phase 1): the two NEW
        # vault paths, the websites backup repo, and the pipeline switches.
        # Every picker gets a LIVE status — "not set" / "will be created" /
        # "found" — and blank paths degrade gracefully: nothing is written
        # anywhere until the pipeline owning that vault is switched ON.

        # --- Websites vault (the Phase 2 pipeline; folder may not exist yet) ---
        web_group = QGroupBox("Websites vault (new — the Phase 2 pipeline)")
        web_layout = QVBoxLayout(web_group)
        web_layout.setSpacing(6)
        web_row = QHBoxLayout()
        self.website_vault_input = QLineEdit(
            (self.config.get('website_vault_path') or '').strip())
        self.website_vault_input.setPlaceholderText(
            "path to the Websites vault — the folder does not need to exist yet")
        web_row.addWidget(self.website_vault_input, 1)
        web_browse = QPushButton("Browse...")
        self._style_btn(web_browse, 'secondary')
        web_browse.clicked.connect(self.browse_website_vault)
        web_row.addWidget(web_browse)
        web_layout.addLayout(web_row)
        self.website_vault_status = QLabel("● —")
        self._set_status(self.website_vault_status, 'muted', strong=True)
        web_layout.addWidget(self.website_vault_status)
        web_repo_row = QHBoxLayout()
        web_repo_row.addWidget(QLabel("Backup repo:"))
        self.website_repo_input = QLineEdit(
            (self.config.get('website_repo_name') or '').strip())
        self.website_repo_input.setPlaceholderText(
            "private GitHub repo for the Websites vault backup")
        web_repo_row.addWidget(self.website_repo_input, 1)
        web_layout.addLayout(web_repo_row)
        # v0.28.0 — THE LAW: these domains are ALWAYS banned from the
        # Websites vault and cannot be removed — x/twitter, the GitHub
        # group, HuggingFace, Instagram, Facebook, LinkedIn (the owner's
        # law, 2026-10-01). The field below only ADDS more; the app also
        # sweeps any legacy banned-domain notes out of the vault.
        web_law_label = QLabel(
            "🔒 Always banned (the law — cannot be removed): "
            + ', '.join(_links.LAW_BLOCKED_DOMAINS))
        web_law_label.setWordWrap(True)
        web_law_label.setObjectName("law_note")
        web_law_label.setToolTip(
            "Links on these domains never enter the Websites vault: never "
            "fetched, never noted, never retried. Existing notes for them "
            "are swept to the vault's .trash on the next run. The record "
            "lives on: the _inbox platform tables and the bot's ledger "
            "keep every link.")
        web_layout.addWidget(web_law_label)
        web_blocked_row = QHBoxLayout()
        web_blocked_row.addWidget(QLabel("Blocked domains:"))
        self.web_blocked_input = QLineEdit(
            ', '.join(_links.blocked_domains_from_config(self.config)))
        self.web_blocked_input.setPlaceholderText(
            "EXTRA domains to ban beyond the law (e.g. reddit.com) — "
            "the law list above always applies")
        self.web_blocked_input.setToolTip(
            "EXTRA domains the Websites pipeline refuses, on top of the "
            "always-banned law list above. Links on them are recorded in "
            "the _inbox platform tables only — never fetched, never "
            "turned into notes, never retried. Subdomains count "
            "(www.x.com matches x.com). The law entries above cannot be "
            "removed; the field can only add more.")
        web_blocked_row.addWidget(self.web_blocked_input, 1)
        web_layout.addLayout(web_blocked_row)
        # v0.21.0 — self domains: hosts that belong to THIS deployment
        # (the Telegram bot's own worker). Its auth links (…/auth/?token=…)
        # land in the same chat the curator reads; they are never fetched,
        # never noted — the _inbox row (token scrubbed) is the record.
        web_self_row = QHBoxLayout()
        web_self_row.addWidget(QLabel("Self domains:"))
        self.web_self_input = QLineEdit(
            ', '.join(_links.self_domains_from_config(self.config)))
        self.web_self_input.setPlaceholderText(
            "the app's OWN hosts — never fetched (default: the bot's "
            "workers.dev URL) — empty = none")
        self.web_self_input.setToolTip(
            "Links on these domains belong to this deployment (the bot's "
            "auth/OAuth handoff links) — never fetched, never turned into "
            "notes; the _inbox row keeps the record with secret query "
            "values scrubbed. Subdomains count. Empty field = no self "
            "domains.")
        web_self_row.addWidget(self.web_self_input, 1)
        web_layout.addLayout(web_self_row)
        vault_layout.addWidget(web_group)

        # --- Manual Notes vault (owner-owned; the app writes only the
        #     read-only Library/ mirror there — v0.14.0 Phase 5) ---
        manual_group = QGroupBox("Manual Notes vault (yours — the app writes only its Library/ mirror)")
        manual_layout = QVBoxLayout(manual_group)
        manual_layout.setSpacing(6)
        manual_row = QHBoxLayout()
        self.manual_vault_input = QLineEdit(
            (self.config.get('manual_vault_path') or '').strip())
        self.manual_vault_input.setPlaceholderText(
            "path to your Manual Notes vault (receives the read-only Library/ mirror)")
        manual_row.addWidget(self.manual_vault_input, 1)
        manual_browse = QPushButton("Browse...")
        self._style_btn(manual_browse, 'secondary')
        manual_browse.clicked.connect(self.browse_manual_vault)
        manual_row.addWidget(manual_browse)
        manual_layout.addLayout(manual_row)
        self.manual_vault_status = QLabel("● —")
        self._set_status(self.manual_vault_status, 'muted', strong=True)
        manual_layout.addWidget(self.manual_vault_status)
        self.manual_mirror_hint = QLabel(
            "Library mirror: run tools/mirror_manual.py — dry-run first, "
            "then --apply. Only Library/ is ever touched.")
        self.manual_mirror_hint.setWordWrap(True)
        self.manual_mirror_hint.setObjectName("muted_note")
        manual_layout.addWidget(self.manual_mirror_hint)
        vault_layout.addWidget(manual_group)

        # --- Pipeline switches ---
        pipes_group = QGroupBox("Pipelines")
        pipes_layout = QVBoxLayout(pipes_group)
        pipes_layout.setSpacing(6)
        _pipes_cfg = self.config.get('pipelines') or {}
        self.pipeline_github_check = QCheckBox(
            "GitHub projects — the existing pipeline")
        self.pipeline_github_check.setChecked(_pipes_cfg.get('github', True))
        self.pipeline_websites_check = QCheckBox(
            "Websites — the new pipeline (v0.11.0: non-GitHub links become notes)")
        self.pipeline_websites_check.setChecked(_pipes_cfg.get('websites', False))
        pipes_layout.addWidget(self.pipeline_github_check)
        pipes_layout.addWidget(self.pipeline_websites_check)
        pipes_info = QLabel(
            "💡 The GitHub switch is ON by default and keeps today's behavior "
            "exactly. The Websites switch runs the Phase 2 pipeline: fetched, "
            "classified notes in the Websites vault (OFF = non-GitHub links "
            "keep going to the _inbox tables, like before). Both OFF = a SYNC "
            "processes nothing.")
        pipes_info.setWordWrap(True)
        pipes_info.setObjectName("info_note")
        pipes_layout.addWidget(pipes_info)
        vault_layout.addWidget(pipes_group)

        # Live status while typing; save once on commit (Enter / focus-out /
        # Browse / toggle) — the established save-on-change pattern.
        self.website_vault_input.textEdited.connect(self._refresh_vault_page_status)
        self.website_vault_input.editingFinished.connect(self._save_vault_page)
        self.website_repo_input.editingFinished.connect(self._save_vault_page)
        self.web_blocked_input.editingFinished.connect(self._save_vault_page)
        self.web_self_input.editingFinished.connect(self._save_vault_page)
        self.manual_vault_input.textEdited.connect(self._refresh_vault_page_status)
        self.manual_vault_input.editingFinished.connect(self._save_vault_page)
        self.pipeline_github_check.toggled.connect(self._save_vault_page)
        self.pipeline_websites_check.toggled.connect(self._save_vault_page)
        self._refresh_vault_page_status()

        self._settings_pages.append((self._wrap_scroll(vault_tab), "Vault"))

        # ---- Tab 4: LLM — three flat cards: provider · limits · engine ----
        # v0.30.0 (external Settings-UI audit, presentation-only). The
        # v0.23.0 semantics are UNCHANGED — still the two-level choice
        # (host: locally-hosted vs cloud; engine inside local: Ollama vs
        # llama.cpp) and the same three-value config['llm_provider'] —
        # but the stacked QGroupBox zoo is now three flat cards:
        #   1. LLM provider — the host radios + one muted caption
        #   2. Limits — the token budget as a form with spanning captions
        #   3. Local engine — heading + engine radios on ONE row, the
        #      quick-switch fast lane, a 1px divider, then the active
        #      engine's fields (or the Cloud API card when cloud is picked)
        # Every widget attribute keeps its name (local_llm_group /
        # ollama_group / llamacpp_group / cloud_group …) so the toggle
        # logic, save path and tests keep working untouched.
        ollama_tab = QWidget()
        ollama_layout = QVBoxLayout(ollama_tab)
        ollama_layout.setSpacing(10)

        # -- card helpers (the audit's flat-card vocabulary) --
        def _card():
            card = QWidget()
            card.setObjectName("card")
            lay = QVBoxLayout(card)
            lay.setContentsMargins(14, 12, 14, 12)
            lay.setSpacing(8)
            return card, lay

        def _heading(text):
            lbl = QLabel(text)
            lbl.setObjectName("card_heading")
            return lbl

        def _subhead(text):
            # a section heading INSIDE a card's form — spans both columns
            lbl = QLabel(text)
            lbl.setObjectName("card_subhead")
            return lbl

        def _form_label(text):
            # the audit's fixed 96px label column — every form in the app
            # aligns its labels at the same x, card after card
            lbl = QLabel(text)
            lbl.setProperty("role", "form_label")
            lbl.setMinimumWidth(96)
            return lbl

        def _muted(text):
            lbl = QLabel(text)
            lbl.setProperty("role", "muted")
            lbl.setWordWrap(True)
            return lbl

        def _divider():
            line = QFrame()
            line.setObjectName("divider")
            line.setFixedHeight(1)
            return line

        # === Card 1 — LLM provider (the host choice) ===
        provider_card, provider_lay = _card()
        provider_row = QHBoxLayout()
        provider_row.setSpacing(14)
        provider_row.addWidget(_heading("LLM provider"))
        provider_row.addStretch(1)
        self.llm_host_local = QRadioButton("Locally hosted LLM model")
        self.llm_host_local.setToolTip(
            "Run the model on YOUR machine — no data leaves it.\n"
            "Two engines: Ollama (http://localhost:11434) or a llama.cpp\n"
            "llama-server (http://127.0.0.1:8080). Both are DETECTED —\n"
            "the Detect & Set buttons find the running server, list its\n"
            "models and configure everything in one click."
        )
        self.llm_host_cloud = QRadioButton("Cloud API model")
        self.llm_host_cloud.setToolTip(
            f"Any {_llm_client.CLOUD_PROVIDER_LABEL}: OpenAI, OpenRouter,\n"
            "Together, vLLM, LM Studio, Cloudflare Workers AI…\n"
            "AND Anthropic Claude — api.anthropic.com URLs automatically\n"
            "use the Claude Messages API (x-api-key + anthropic-version).\n"
            "The URL decides the wire format; the same Model field takes\n"
            "'gpt-4o-mini' or 'claude-sonnet-4-5' alike."
        )
        saved_provider = self.config.get('llm_provider', 'ollama')
        if saved_provider == 'cloud':
            self.llm_host_cloud.setChecked(True)
        else:
            self.llm_host_local.setChecked(True)
        provider_row.addWidget(self.llm_host_local)
        provider_row.addWidget(self.llm_host_cloud)
        provider_lay.addLayout(provider_row)
        provider_lay.addWidget(_muted(
            "Where the model runs. Local engines keep every byte on your "
            "machine; the cloud API reaches any OpenAI-compatible endpoint "
            "or Anthropic Claude — the URL decides the wire format."))
        ollama_layout.addWidget(provider_card)

        # === Card 2 — Limits (the v0.23.0 token budget, code-accurate) ===
        # The total window is split: what the model can READ (max context)
        # and what it can WRITE (output tokens). The captions spell the
        # exact 0 semantics read from llm_client (verified against the
        # wire calls, not the old tooltips):
        #   ctx 0  → num_ctx NOT sent → Ollama's own default window;
        #            cloud: the over-budget warning is simply skipped.
        #   out 0  → max_tokens NOT sent → the server's default cap;
        #            Claude always sends SOMETHING (4096 fallback).
        limits_card, limits_lay = _card()
        limits_lay.addWidget(_heading("Limits"))
        limits_form = QFormLayout()
        limits_form.setVerticalSpacing(6)
        limits_form.setHorizontalSpacing(8)
        self.llm_num_ctx = QLineEdit(
            str(self.config.get('llm_num_ctx',
                                _llm_client.DEFAULT_NUM_CTX)))
        self.llm_num_ctx.setPlaceholderText(
            str(_llm_client.DEFAULT_NUM_CTX))
        self.llm_num_ctx.setFixedWidth(160)
        limits_form.addRow(
            _form_label("Model max context window (tokens):"),
            self.llm_num_ctx)
        limits_form.addRow(_muted(
            "What the model can READ — the total context window. Ollama: "
            "sent as num_ctx with every call (its defaults are small and "
            "truncate silently). OpenAI-compatible servers: the window is "
            "fixed at server launch (llama.cpp -c / vLLM --max-model-len) — "
            "this value only powers the over-budget warning; Claude: the "
            "same warning. 0 = not sent — the server's own window is used."))
        self.llm_max_output_tokens = QLineEdit(
            str(self.config.get('llm_max_output_tokens',
                                _llm_client.DEFAULT_MAX_OUTPUT_TOKENS)))
        self.llm_max_output_tokens.setPlaceholderText(
            str(_llm_client.DEFAULT_MAX_OUTPUT_TOKENS))
        self.llm_max_output_tokens.setFixedWidth(160)
        limits_form.addRow(
            _form_label("Output max tokens:"),
            self.llm_max_output_tokens)
        limits_form.addRow(_muted(
            "What the model can WRITE — the cap on the model's answer. "
            "Ollama: sent as options.num_predict. OpenAI-compatible: sent "
            "as max_tokens. Claude: max_tokens is REQUIRED — the configured "
            f"value is sent, with a {_llm_client.ANTHROPIC_FALLBACK_MAX_TOKENS}-token "
            "fallback when unset. 0 = not sent — the server's default cap "
            "is used."))
        limits_lay.addLayout(limits_form)
        ollama_layout.addWidget(limits_card)

        # === Card 3 — Local engine (self.local_llm_group) ===
        # The heading and the engine radios share ONE row (the audit's
        # compact heading pattern); below them the quick-switch fast lane,
        # a 1px divider, then the active engine's fields.
        self.local_llm_group = QWidget()
        self.local_llm_group.setObjectName("card")
        local_layout = QVBoxLayout(self.local_llm_group)
        local_layout.setContentsMargins(14, 12, 14, 12)
        local_layout.setSpacing(8)

        # Engine radios (the local sub-choice; same button group — the
        # stored llm_provider value 'ollama' / 'llamacpp').
        engine_row = QHBoxLayout()
        engine_row.setSpacing(14)
        engine_row.addWidget(_heading("Local engine"))
        engine_row.addStretch(1)
        self.llm_provider_ollama = QRadioButton("Ollama")
        self.llm_provider_ollama.setToolTip(
            "Use a local Ollama server (http://localhost:11434 by default).\n"
            "No API key required — runs entirely on your machine."
        )
        # v0.15.0 — llama.cpp engine detection: llama-server as its own
        # DETECTED provider (like Ollama), not a hand-configured URL.
        self.llm_provider_llamacpp = QRadioButton("llama.cpp")
        self.llm_provider_llamacpp.setToolTip(
            "A local llama.cpp server (llama-server, http://127.0.0.1:8080\n"
            "by default). Detected like Ollama: 'Detect' finds the server\n"
            "and its loaded model automatically (llama.cpp /props + /v1/models).\n"
            "No API key unless the server was started with --api-key."
        )
        if saved_provider == 'llamacpp':
            self.llm_provider_llamacpp.setChecked(True)
        else:
            self.llm_provider_ollama.setChecked(True)
        engine_row.addWidget(self.llm_provider_ollama)
        engine_row.addWidget(self.llm_provider_llamacpp)
        local_layout.addLayout(engine_row)

        # Quick switch — v0.23.0: the two fast-lane buttons live HERE
        # (exclusively — the main view's copy was removed at the owner's
        # request). One click per engine: probe → model menu when several →
        # provider + engine + model + URL set AND saved. ('&&' renders as
        # a literal '&' — a single & would become a mnemonic underscore.)
        local_layout.addWidget(_muted(
            "Quick switch — one click per engine: probe the running server, "
            "pick the model when several are installed, set the provider + "
            "model + URL and save."))
        quick_row = QHBoxLayout()
        quick_row.setSpacing(6)
        quick_ollama_btn = QPushButton("Detect && Set Ollama")
        quick_ollama_btn.setToolTip(
            "Probe the Ollama server, pick the model when several are "
            "installed, set the provider + model + URL and save")
        quick_ollama_btn.clicked.connect(self.quick_detect_set_ollama)
        self._style_btn(quick_ollama_btn, 'secondary')
        quick_row.addWidget(quick_ollama_btn)
        quick_llamacpp_btn = QPushButton("Detect && Set llama.cpp")
        quick_llamacpp_btn.setToolTip(
            "Find the running llama-server (process ports + common ports), "
            "pick the model when several are advertised, set the provider "
            "+ model + URL and save")
        quick_llamacpp_btn.clicked.connect(self.quick_detect_set_llamacpp)
        self._style_btn(quick_llamacpp_btn, 'secondary')
        quick_row.addWidget(quick_llamacpp_btn)
        quick_row.addStretch()
        local_layout.addLayout(quick_row)
        local_layout.addWidget(_divider())

        # --- Ollama section (existing fields, inside the local card) ---
        self.ollama_group = QWidget()
        ollama_form = QFormLayout(self.ollama_group)
        ollama_form.setContentsMargins(0, 0, 0, 0)
        ollama_form.setVerticalSpacing(6)
        ollama_form.setHorizontalSpacing(8)
        ollama_form.addRow(_subhead("Ollama"))
        self.ollama_url = QLineEdit(self.config.get('ollama', {}).get('base_url', 'http://localhost:11434'))
        ollama_form.addRow(_form_label("Ollama URL:"), self.ollama_url)

        # Model dropdown (editable combo so user can type a custom model name
        # OR pick from the list of available models pulled from the server).
        model_row = QHBoxLayout()
        model_row.setSpacing(6)
        self.ollama_model = QComboBox()
        self.ollama_model.setEditable(True)
        self.ollama_model.setInsertPolicy(QComboBox.InsertPolicy.InsertAtTop)
        # v33.1: cap the combo's minimum width — long model tags sized this
        # row to a 833px minimum and the Settings viewport clipped the
        # Refresh button at its right edge. The popup still shows full tags.
        self.ollama_model.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.ollama_model.setMinimumContentsLength(18)
        # Pre-fill with saved model + a common default
        saved_model = self.config.get('ollama', {}).get('model', 'qwythos-9b')
        self.ollama_model.addItem(saved_model)
        self.ollama_model.setCurrentText(saved_model)
        model_row.addWidget(self.ollama_model, 1)

        refresh_models_btn = QPushButton("Refresh")
        refresh_models_btn.setToolTip("Reload the model list from the Ollama server")
        refresh_models_btn.clicked.connect(self.refresh_ollama_models)
        self._style_btn(refresh_models_btn, 'secondary')
        model_row.addWidget(refresh_models_btn)

        # v33.1 compact: Start Server shares the Model row (it was a row of
        # its own — same signal, same behavior, one row less).
        start_ollama_btn = QPushButton("Start Server")
        start_ollama_btn.setToolTip("Start the local Ollama server (ollama serve)")
        start_ollama_btn.clicked.connect(self.start_ollama_server)
        self._style_btn(start_ollama_btn, 'secondary')
        model_row.addWidget(start_ollama_btn)
        ollama_form.addRow(_form_label("Model:"), model_row)
        local_layout.addWidget(self.ollama_group)

        # --- llama.cpp section (v0.15.0 — engine detection) ---
        # The DETECTED local provider: the URL defaults to llama-server's
        # own default and 'Detect' scans the common ports (/props
        # positively identifies llama.cpp), fills the URL and auto-selects
        # the model. Chat rides the OpenAI-compatible path underneath.
        self.llamacpp_group = QWidget()
        llamacpp_form = QFormLayout(self.llamacpp_group)
        llamacpp_form.setContentsMargins(0, 0, 0, 0)
        llamacpp_form.setVerticalSpacing(6)
        llamacpp_form.setHorizontalSpacing(8)
        llamacpp_form.addRow(_subhead(_llm_client.LLAMACPP_PROVIDER_LABEL))
        self.llamacpp_api_url = QLineEdit(self.config.get(
            'llamacpp_api_url',
            _llm_client.LLAMACPP_DEFAULT_BASE + '/v1'))
        self.llamacpp_api_url.setPlaceholderText(
            _llm_client.LLAMACPP_DEFAULT_BASE + '/v1')
        llamacpp_form.addRow(_form_label("Server URL:"), self.llamacpp_api_url)

        self.llamacpp_api_key = QLineEdit(self.config.get(
            'llamacpp_api_key', ''))
        self.llamacpp_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.llamacpp_api_key.setPlaceholderText(
            "(empty — only needed when llama-server was started with --api-key)")
        llamacpp_form.addRow(_form_label("API key:"), self.llamacpp_api_key)

        llamacpp_model_row = QHBoxLayout()
        llamacpp_model_row.setSpacing(6)
        self.llamacpp_model = QComboBox()
        self.llamacpp_model.setEditable(True)
        self.llamacpp_model.setInsertPolicy(
            QComboBox.InsertPolicy.InsertAtTop)
        self.llamacpp_model.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.llamacpp_model.setMinimumContentsLength(18)
        saved_llamacpp_model = str(self.config.get('llamacpp_model', '')
                                   or '').strip()
        if saved_llamacpp_model:
            self.llamacpp_model.addItem(saved_llamacpp_model)
            self.llamacpp_model.setCurrentText(saved_llamacpp_model)
        else:
            self.llamacpp_model.setCurrentText("")
            self.llamacpp_model.setPlaceholderText(  # shown when editable+empty
                "(auto-detected from the server)")
        llamacpp_model_row.addWidget(self.llamacpp_model, 1)

        llamacpp_detect_btn = QPushButton("Detect")
        llamacpp_detect_btn.setToolTip(
            "Find the running llama-server automatically: its PROCESS's\n"
            "listening ports first (any --port), then the common ports\n"
            "(8080 first). Positively identifies llama.cpp, then fills\n"
            "this URL and the model automatically.")
        llamacpp_detect_btn.clicked.connect(self.detect_llamacpp_service)
        # v0.30.0 (audit): Detect is the LLM tab's ONE primary button —
        # the single most characteristic action on the page.
        self._style_btn(llamacpp_detect_btn, 'primary')
        llamacpp_model_row.addWidget(llamacpp_detect_btn)

        llamacpp_refresh_btn = QPushButton("Refresh")
        llamacpp_refresh_btn.setToolTip(
            "Reload the model list from the llama.cpp server at the URL above")
        llamacpp_refresh_btn.clicked.connect(self.refresh_llamacpp_models)
        self._style_btn(llamacpp_refresh_btn, 'secondary')
        llamacpp_model_row.addWidget(llamacpp_refresh_btn)
        llamacpp_form.addRow(_form_label("Model:"), llamacpp_model_row)
        local_layout.addWidget(self.llamacpp_group)
        # The assembled local engine card joins the page AFTER its children.
        ollama_layout.addWidget(self.local_llm_group)

        # === The Cloud API card (shown when the cloud host radio is picked;
        # v26 — Fix 4; v0.23.0 — the owner's second top-level option, now
        # covering BOTH cloud wire formats) ===
        self.cloud_group = QWidget()
        self.cloud_group.setObjectName("card")
        cloud_lay = QVBoxLayout(self.cloud_group)
        cloud_lay.setContentsMargins(14, 12, 14, 12)
        cloud_lay.setSpacing(8)
        cloud_head = QHBoxLayout()
        cloud_head.addWidget(_heading(
            f"Cloud API — {_llm_client.CLOUD_PROVIDER_LABEL}"))
        cloud_head.addStretch(1)
        cloud_lay.addLayout(cloud_head)
        cloud_form = QFormLayout()
        cloud_form.setVerticalSpacing(6)
        cloud_form.setHorizontalSpacing(8)
        self.cloud_api_url = QLineEdit(self.config.get('cloud_api_url', 'https://api.openai.com/v1'))
        self.cloud_api_url.setPlaceholderText("https://api.openai.com/v1 — or https://api.anthropic.com/v1 for Claude")
        self.cloud_api_url.setToolTip(
            "OpenAI-compatible: https://api.openai.com/v1, OpenRouter,\n"
            "Together, vLLM, LM Studio, a local server…\n"
            "Claude: https://api.anthropic.com/v1 — the URL decides the\n"
            "wire format automatically (Messages API, x-api-key header).")
        cloud_form.addRow(_form_label("API URL:"), self.cloud_api_url)

        self.cloud_api_key = QLineEdit(self.config.get('cloud_api_key', ''))
        self.cloud_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.cloud_api_key.setPlaceholderText("sk-… / sk-ant-… (kept locally in config.json)")
        cloud_form.addRow(_form_label("API Key:"), self.cloud_api_key)

        self.cloud_model = QLineEdit(self.config.get('cloud_model', 'gpt-4o-mini'))
        self.cloud_model.setPlaceholderText("gpt-4o-mini / claude-sonnet-4-5 / …")
        cloud_form.addRow(_form_label("Model:"), self.cloud_model)
        cloud_lay.addLayout(cloud_form)
        cloud_lay.addWidget(_muted(
            "Any OpenAI-compatible endpoint (OpenAI, OpenRouter, Together, "
            "vLLM, LM Studio, Cloudflare Workers AI…) or Anthropic Claude — "
            "the URL decides the wire format."))
        ollama_layout.addWidget(self.cloud_group)

        # --- Toggle visibility based on the host + engine radios ---
        def _toggle_llm_provider(*_args):
            # v0.23.0 — two-level: the host radio picks local vs cloud;
            # inside local, the engine radio picks Ollama vs llama.cpp.
            is_local = self.llm_host_local.isChecked()
            is_cloud = self.llm_host_cloud.isChecked()
            self.local_llm_group.setVisible(is_local)
            self.cloud_group.setVisible(is_cloud)
            is_ollama = self.llm_provider_ollama.isChecked()
            self.ollama_group.setVisible(is_ollama)
            self.llamacpp_group.setVisible(not is_ollama)
        self.llm_host_local.toggled.connect(_toggle_llm_provider)
        self.llm_host_cloud.toggled.connect(_toggle_llm_provider)
        self.llm_provider_ollama.toggled.connect(_toggle_llm_provider)
        self.llm_provider_llamacpp.toggled.connect(_toggle_llm_provider)
        # Apply initial state (must be after all groups are constructed).
        _toggle_llm_provider()

        # v31.1: no filler stretch — content keeps its natural height at the
        # top of the scrollable tab; the window never resizes.
        self._settings_pages.append((self._wrap_scroll(ollama_tab), "LLM"))

        # ---- Tab: Input (Import txt file — the sole input mode) ----
        # v0.23.0 — owner-spec redesign: the ID Range / Markers / Single
        # Msg modes are GONE (with their GUI handlers — the marker hash,
        # find-by-keywords, single-message fetch and range-preview paths).
        # The bot-queue SYNC on the main view fetches from Telegram; this
        # tab is the file alternative: "Import txt file" — a .txt OR .md
        # file with one URL per line (GitHub repos AND websites; both
        # pipelines run exactly like a fetched batch).
        input_tab = QWidget()
        input_layout = QVBoxLayout(input_tab)
        input_layout.setSpacing(8)

        # --- Import txt file group (the ONE input mode) ---
        self.import_group = QGroupBox("Import txt file")
        import_layout = QVBoxLayout()
        import_layout.setSpacing(8)

        # File row: [path field] [Select…]
        file_row = QHBoxLayout()
        self.import_file = QLineEdit()
        self.import_file.setPlaceholderText(
            "Path to a .txt or .md file — one URL per line")
        self.import_file.setToolTip(
            "A plain-text or Markdown file with one address per line.\n"
            "Markdown links, bullets and trailing notes are fine — the\n"
            "address is read out of them. Lines starting with # are\n"
            "comments; blank lines are skipped; duplicates are ignored\n"
            "(reported); lines with no address are listed in the log.\n"
            "GitHub repos go to the GitHub pipeline, every other website\n"
            "to the Websites pipeline — exactly like a fetched batch.")
        import_btn = QPushButton("Select…")
        import_btn.setToolTip("Pick the .txt / .md file to import")
        import_btn.clicked.connect(self.select_import_file)
        self._style_btn(import_btn, 'secondary')
        file_row.addWidget(self.import_file, 1)
        file_row.addWidget(import_btn)
        import_layout.addLayout(file_row)

        # Hint — what happens on PROCESS (the main view's button).
        import_hint = QLabel(
            "PROCESS (main view) imports the file: GitHub repos are noted "
            "into the GitHub vault, every other website into the Websites "
            "vault. Use SYNC instead to fetch the Telegram bot queue.")
        import_hint.setWordWrap(True)
        import_hint.setObjectName("info_note")
        import_layout.addWidget(import_hint)

        self.import_group.setLayout(import_layout)
        input_layout.addWidget(self.import_group)

        # v31.1: every tab scrolls independently inside the fixed window.
        input_scroll = self._wrap_scroll(input_tab)
        self._settings_pages.append((input_scroll, "Input"))

        # ---- Tab: Dashboard (added last; remains the last tab after Input is moved to 0) ----
        dash_tab = QWidget()
        dash_layout = QVBoxLayout(dash_tab)

        dash_btn_row = QHBoxLayout()
        self.refresh_dash_btn = QPushButton("Refresh Dashboard")
        # v31.1: the Dashboard tab's ONE filled primary button.
        self._style_btn(self.refresh_dash_btn, 'primary')
        self.refresh_dash_btn.clicked.connect(self.update_dashboard)
        dash_btn_row.addWidget(self.refresh_dash_btn)

        # v22 Feature 6: Batch Undo — deletes the .md files written by the
        # most recent batch (listed in `<vault>/_undo_last_batch.txt`).
        self.undo_batch_btn = QPushButton("Undo Last Batch")
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

        # v0.09 (lineage merge) — 404 quarantine manager: the v0.07 lineage
        # had a strike-counter manager here; the v0.08 lineage had a
        # hardcoded threshold + a More-menu viewer. The merged design keeps
        # BOTH UIs on ONE system (decommissioned_repos.fail_count): this
        # group makes the threshold configurable (spinbox →
        # notfound_strike_threshold, shared with CLI --strikes N) and shows
        # every URL carrying attempts (in-progress AND confirmed ⛔), the
        # More ▸ View 404 Quarantine dialog stays for confirmed-only + reset.
        self.quarantine_group = QGroupBox("Deleted Repos — 404 Quarantine")
        quarantine_layout = QVBoxLayout()

        quarantine_ctrl_row = QHBoxLayout()
        quarantine_ctrl_row.addWidget(QLabel("Confirm dead after"))
        self.quarantine_threshold_spin = QSpinBox()
        self.quarantine_threshold_spin.setRange(2, 10)
        # blockSignals: the initial setValue must NOT fire valueChanged —
        # that would run save_config() in the middle of initUI on every
        # launch (harmless but wasteful; the value is already on disk).
        self.quarantine_threshold_spin.blockSignals(True)
        self.quarantine_threshold_spin.setValue(
            max(2, int(self.config.get('notfound_strike_threshold',
                                       DEAD_LINK_THRESHOLD)
                       or DEAD_LINK_THRESHOLD)))
        self.quarantine_threshold_spin.blockSignals(False)
        self.quarantine_threshold_spin.setToolTip(
            "How many consecutive 404s (counted across sessions) before a "
            "repo is quarantined (auto-ignored). A successful fetch resets "
            "its counter. Minimum 2, default 3.")
        quarantine_ctrl_row.addWidget(self.quarantine_threshold_spin)
        quarantine_ctrl_row.addWidget(QLabel("consecutive 404s"))
        quarantine_ctrl_row.addStretch()

        refresh_quarantine_btn = QPushButton("Refresh")
        refresh_quarantine_btn.setToolTip("Reload the 404 quarantine table from cache.db")
        refresh_quarantine_btn.clicked.connect(self.refresh_quarantine_view)
        self._style_btn(refresh_quarantine_btn, 'secondary')
        quarantine_ctrl_row.addWidget(refresh_quarantine_btn)

        clear_quarantine_btn = QPushButton("Reset Quarantine")
        clear_quarantine_btn.setToolTip(
            "Reset ALL 404 attempt counters — quarantined repos are "
            "re-checked on the next run instead of being auto-ignored.")
        clear_quarantine_btn.clicked.connect(self.clear_all_quarantine)
        self._style_btn(clear_quarantine_btn, 'secondary')
        quarantine_ctrl_row.addWidget(clear_quarantine_btn)
        quarantine_layout.addLayout(quarantine_ctrl_row)

        self.quarantine_text = QTextEdit()
        self.quarantine_text.setReadOnly(True)
        self.quarantine_text.setPlaceholderText(
            "No 404 attempts recorded yet — deleted repos will appear "
            "here with their attempt counts after a run.")
        self.quarantine_text.setFixedHeight(110)
        quarantine_layout.addWidget(self.quarantine_text)

        self.quarantine_group.setLayout(quarantine_layout)
        dash_layout.addWidget(self.quarantine_group)

        # Persist threshold changes immediately (save_config MERGES, so no
        # other key is touched; the worker reads the value per batch).
        self.quarantine_threshold_spin.valueChanged.connect(
            self._save_quarantine_threshold)

        self._settings_pages.append((self._wrap_scroll(dash_tab), "Dashboard"))

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
        save_token_btn = QPushButton("Save")
        save_token_btn.clicked.connect(self.save_config)
        self._style_btn(save_token_btn, 'secondary')  # v33: joins the design system
        token_row2.addWidget(save_token_btn)
        bot_layout.addLayout(token_row2)

        # Queue controls — v31.1 three-variant hierarchy: ONE filled primary
        # (Process All) + outlined secondary actions. Infrequent actions
        # (export / verify / retry) moved to the global 'More' menu.
        queue_btn_row = QHBoxLayout()
        self.check_queue_btn = QPushButton("Check Queue")
        self._style_btn(self.check_queue_btn, 'secondary')
        self.check_queue_btn.clicked.connect(self.check_bot_queue)
        queue_btn_row.addWidget(self.check_queue_btn)

        # Pending badge (appears after Check Queue, shows repos not yet in
        # the vault). v31.1: zinc — a pending COUNT is not an error; red is
        # reserved for actual failures (WCAG-safe neutral).
        self.pending_badge = QLabel("")
        self.pending_badge.setObjectName("pending_badge")
        self._set_badge_state(self.pending_badge, 'pending')
        self.pending_badge.setVisible(False)
        queue_btn_row.addWidget(self.pending_badge)

        process_queue_btn = QPushButton("Process All")
        # v31.1: the Bot tab's ONE filled primary button.
        self._style_btn(process_queue_btn, 'primary')
        process_queue_btn.clicked.connect(self.process_bot_queue)
        queue_btn_row.addWidget(process_queue_btn)

        # v25 pre-flight: "Process New" — fetches only messages newer than
        # the last successfully-processed message ID (saved to config.json
        # after each verified-clean batch). Lets the user run incremental
        # batches without re-processing already-handled repos.
        process_new_btn = QPushButton("Process New")
        self._style_btn(process_new_btn, 'secondary')
        process_new_btn.setToolTip(
            "Fetch only messages newer than the last successfully-processed batch.\n"
            "Use this for daily incremental runs — skips already-processed repos."
        )
        process_new_btn.clicked.connect(self.process_new_bot_queue)
        queue_btn_row.addWidget(process_new_btn)

        # Mark All Read — hidden by default, appears only after verify passes
        self.mark_all_read_btn = QPushButton("Mark All as Read")
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

        self._settings_pages.append((self._wrap_scroll(bot_tab), "Bot"))

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

        fetch_sources_btn = QPushButton("Fetch URLs")
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
        process_sources_btn = QPushButton("Process Fetched URLs")
        self._style_btn(process_sources_btn, 'primary')
        process_sources_btn.clicked.connect(self.process_sources_urls)
        sources_layout.addWidget(process_sources_btn)

        # v31.1: 📡 (feeds) — Proxy keeps 🌐. Two different destinations no
        # longer share one icon.
        self._settings_pages.append((self._wrap_scroll(sources_tab), "Sources"))

        # ---- Tab: Backup (local folder + timestamped zip) ----
        # v32.2: wrap in the scroll area like every other tab — the four
        # sections' natural height exceeds the fixed tab pane, which
        # previously clipped each section's lower rows (buttons, toggles,
        # the dashboard link).
        backup_tab = self._create_backup_tab()
        self._settings_pages.append((self._wrap_scroll(backup_tab), "Backup"))

        # v33: the Bot-tab reorder block below was dead code (findChild never
        # matched) and is removed with the tab strip itself — every page now
        # lives in the Settings window's sidebar navigation.

        # ---- Top bar: logo lockup (left) · settings + theme buttons (right)
        top_bar = QHBoxLayout()
        top_bar.setSpacing(10)
        self._build_logo_lockup(top_bar)
        top_bar.addStretch()

        self.settings_btn = QPushButton()
        self.settings_btn.setFixedSize(34, 30)
        self.settings_btn.setToolTip(
            "Settings — credentials, proxy, vault, LLM, input modes,\n"
            "bot queue, sources, dashboard and backup (all former tabs)."
        )
        self.settings_btn.setAccessibleName("Settings")
        self.settings_btn.clicked.connect(self._open_settings)
        self._style_btn(self.settings_btn, 'icon')
        top_bar.addWidget(self.settings_btn)

        # v32.1 — ALWAYS-VISIBLE light/dark toggle (unchanged widget & wiring).
        # One compact icon button shows the mode you'll switch TO (🌙 in light
        # mode, ☀️ in dark mode); the tooltip spells it out. Synced by
        # _sync_theme_toggle_btn() on init + every flip.
        self.theme_toggle_btn = QPushButton()
        self.theme_toggle_btn.setFixedSize(34, 30)
        self.theme_toggle_btn.setToolTip("Switch to dark mode (current: Light)")
        self.theme_toggle_btn.setAccessibleName("Toggle dark or light theme")
        self.theme_toggle_btn.clicked.connect(self.toggle_theme)
        self._style_btn(self.theme_toggle_btn, 'icon')
        top_bar.addWidget(self.theme_toggle_btn)
        main_layout.addLayout(top_bar)

        # ---- Hero CTA card: SYNC (⇄ STOP) + Test Connectivity, side by side.
        # v0.07 (design review "balance/hierarchy"): the two CTAs share ONE
        # row — SYNC grows, Test Connectivity keeps its natural width — so
        # the vertical space the stacked layout wasted now belongs to the
        # log panel (the main view's growable region).
        cta_card = QWidget()
        cta_card.setObjectName("sync_card")
        cta_layout = QVBoxLayout(cta_card)
        cta_layout.setContentsMargins(16, 10, 16, 10)
        cta_layout.setSpacing(0)

        # v0.03 two-stage hero flow (user spec): SYNC fetches all UNDONE
        # items from the Telegram bot → the button becomes PROCESS → clicking
        # it starts the batch. start_btn/stop_btn keep their EXACT
        # enabled-state ownership (_start_worker disables start / enables
        # stop; processing_finished restores it); the 200ms GUI-state mirror
        # (see _sync_run_button) renders the stages: SYNC → PROCESS → STOP
        # (while a batch runs) → back to SYNC.
        run_slot = QGridLayout()
        run_slot.setContentsMargins(0, 0, 0, 0)
        run_slot.setSpacing(0)
        self._hero_state = 'sync'   # sync | fetching | process | running
        self.start_btn = QPushButton("SYNC")
        self.start_btn.setMinimumHeight(40)
        self.start_btn.setToolTip(
            "Stage 1 — fetch every UNDONE item from the Telegram bot\n"
            "(repos already in the vault and decommissioned ones are skipped).\n"
            "The button then becomes PROCESS — click it to start the batch.\n"
            "While a batch runs this button becomes STOP — click to cancel."
        )
        self._style_btn(self.start_btn, 'hero_primary')
        self.start_btn.clicked.connect(self._on_hero_clicked)  # v0.03 two-stage flow

        self.stop_btn = QPushButton("STOP")
        self.stop_btn.setMinimumHeight(40)
        self.stop_btn.setToolTip("Cancel the running batch (SYNC returns when it stops).")
        self._style_btn(self.stop_btn, 'hero_danger')
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_processing)  # unchanged wiring
        run_slot.addWidget(self.start_btn, 0, 0)
        run_slot.addWidget(self.stop_btn, 0, 0)
        self.stop_btn.setVisible(False)

        cta_row = QHBoxLayout()
        cta_row.setSpacing(10)
        cta_row.addLayout(run_slot, 1)   # hero button grows

        self.test_btn = QPushButton("Test Connection")
        self.test_btn.setMinimumHeight(40)
        self.test_btn.setToolTip(
            "Check that everything is up and ready, and show it in the log:\n"
            "① Vaults — found + writable (ready to receive notes)\n"
            "② LLM — the active provider: API, Ollama or llama.cpp\n"
            "③ GitHub — token valid + the backup repos ready\n"
            "④ Telegram — bot + account login (live connection test)"
        )
        self._style_btn(self.test_btn, 'hero_secondary')
        self.test_btn.clicked.connect(self.test_all)
        cta_row.addWidget(self.test_btn)
        cta_layout.addLayout(cta_row)
        # v0.23.0 — the LLM quick-switch row (Detect & Set Ollama / llama.cpp)
        # is GONE from the main view (owner request: "remove from the main
        # view — the settings is enough"). Both buttons live on in Settings →
        # 🧠 LLM (they were already there as the Quick switch row), and the
        # quick_detect_set_* handlers stay for that row + the CLI twin.
        main_layout.addWidget(cta_card)

        # ---- Pipeline strip: PROCESSED x / y counter · determinate bar ·
        # proxy health — ONE connected story (design review: the counter and
        # the "Connected" status are two halves of the same pipeline-health
        # readout, so they share one row with the bar bridging them). ----
        prog_row = QHBoxLayout()
        prog_row.setSpacing(10)

        # v0.07: the counter gets a LABEL (proximity) — "- / -" with no
        # label told the user nothing. _refresh_pipeline_counter() keeps the
        # numbers real (manifest totals while idle, live counts in a batch).
        pipeline_caption = QLabel("PROCESSED")
        pipeline_caption.setObjectName("pipeline_caption")
        _cap_font = pipeline_caption.font()
        _cap_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.2)
        pipeline_caption.setFont(_cap_font)
        pipeline_caption.setToolTip("Links processed out of the current batch")
        prog_row.addWidget(pipeline_caption)

        self.progress_count = QLabel("0 / 0")
        self.progress_count.setObjectName("progress_count")
        self.progress_count.setMinimumWidth(64)
        self.progress_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.progress_count.setToolTip("No batch manifest yet — SYNC to fetch items")
        prog_row.addWidget(self.progress_count)

        # Progress bar — determinate, NOW a permanent fixture of the main
        # view (v33 wireframe); labeled "Processing X of Y — repo-name" via
        # update_progress()/update_status() while a batch runs.
        self.progress_bar = QProgressBar()
        self.progress_bar.setFormat("Ready")
        self.progress_bar.setFixedHeight(16)
        self.progress_bar.setTextVisible(True)
        # v0.03 fix: a fresh QProgressBar holds value = -1 (unset), which is
        # OUT OF RANGE — a QSS-styled bar then renders NO text at all, so the
        # idle "Ready" label (and the v0.03 "N ready to process" state) was
        # invisible until the first batch ran. Pin the value to 0 up front.
        self.progress_bar.setValue(0)
        prog_row.addWidget(self.progress_bar, 1)

        # v22 Feature 7: Proxy Health Monitor — small colored dot + TEXT label
        # (v31.1: color alone never conveys state — WCAG 1.4.1) that reflect
        # whether the configured proxy is reachable. Updated every 60 seconds
        # by a QTimer (see __init__ end). Non-blocking: the check uses a 2s
        # socket timeout and runs on the GUI thread.
        # v0.07: the dot is the unified 'dot' SVG glyph (was a full-color
        # emoji circle) and the label carries a semantic text color.
        self.proxy_status_label = QLabel()
        self.proxy_status_label.setFixedSize(16, 16)
        self.proxy_status_label.setToolTip("Proxy status — checking...")
        self.proxy_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.proxy_status_label.setAccessibleName("Proxy status")
        self.proxy_status_label.setPixmap(_icons.pixmap('dot', '#8E8A90', 12))
        prog_row.addWidget(self.proxy_status_label)
        self.proxy_status_text = QLabel("Checking…")
        self.proxy_status_text.setToolTip("Proxy status — checking...")
        prog_row.addWidget(self.proxy_status_text)
        main_layout.addLayout(prog_row)

        # ---- 'More' overflow menu (v31.1: one menu for infrequent actions).
        # v33: the SAME menu, now hosted in the Settings window's header so
        # the main view keeps only the wireframe elements. ----
        self.more_btn = QToolButton()
        self.more_btn.setText("More ▾")
        self.more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_btn.setToolTip("Tests, verification, export, retry and settings")
        more_menu = QMenu(self.more_btn)
        more_menu.addAction("🔌 Test Connection (all systems)", self.test_all)
        more_menu.addSeparator()
        more_menu.addAction("🔑 Test Telegram & GitHub", self.test_telegram_github)
        more_menu.addAction("🌐 Test Proxy Connection", self.test_proxy)
        more_menu.addAction("🧠 Test Ollama", self.test_ollama)
        more_menu.addAction("🔌 Test Cloud API", self.test_cloud_llm)
        more_menu.addAction("🦙 Test llama.cpp", self.test_llamacpp)
        more_menu.addSeparator()
        # v0.16.0 — Phase 6 (Linking): the two tool surfaces, runnable
        # from the GUI. Both run the tools' SAFE defaults: recall hooks
        # in DRY-RUN (diff only), link suggestions in suggest mode (the
        # only write is the Suggestions note under the manual vault's
        # Library/).
        more_menu.addAction("🪝 Recall hooks (dry-run)",
                            self.run_recall_hooks_dryrun)
        more_menu.addAction("🔗 Build link suggestions",
                            self.run_link_suggestions)
        more_menu.addSeparator()
        more_menu.addAction("✅ Validate Vault", self.test_vault)
        more_menu.addAction("🔍 Verify Vault", self.verify_vault)
        more_menu.addAction("📁 Recategorize Notes", self.recategorize_notes)
        more_menu.addSeparator()
        more_menu.addAction("✅ Verify All Processed", self.verify_all_bot_links)
        more_menu.addAction("📋 Export All Links", self.export_all_bot_links)
        more_menu.addAction("🔄 Retry Failed", self.retry_failed_repos)
        # v0.08 — 404 quarantine (dead-link) management: view the confirmed
        # list + reset for false positives.
        more_menu.addAction("🚫 View 404 Quarantine", self.view_dead_links)
        self.backup_export_btn = more_menu.addAction("📤 Export Backup ZIP")
        self.backup_export_btn.triggered.connect(self._backup_export_zip)
        more_menu.addSeparator()
        # v0.23.0 — '👁️ Preview Messages' removed with the ID Range mode
        # it served (preview_messages is gone).
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

        # ---- Progress Logs panel — ALWAYS VISIBLE (v33 wireframe), the
        # main view's ONE growable region ----
        log_group = QGroupBox()
        log_group.setObjectName("log_group")  # v33.1: compact QSS override (no title → no top margin)
        log_group_layout = QVBoxLayout()
        log_group.setContentsMargins(4, 4, 4, 4)

        # Log header with filter buttons, search box, and clear button
        log_header = QHBoxLayout()

        # Filter buttons — a segmented control (v0.07: the ACTIVE filter is
        # now legible at a glance; the old buttons had zero checked-state
        # styling, violating Nielsen's visibility of system status).
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
            btn.setObjectName("log_filter")   # themed via QSS (checked = filled)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            log_header.addWidget(btn)

        log_header.addStretch()

        # Search box — unified 'search' glyph as a leading action (no emoji;
        # placeholder text is themed to AA via the QPalette PlaceholderText
        # role set in apply_light_theme/apply_dark_theme).
        self.log_search = QLineEdit()
        self.log_search.setObjectName("log_search")  # v0.09.1: enables the height-harmonizing QSS
        self.log_search.setPlaceholderText("Search log…")
        self.log_search.setAccessibleName("Search log")
        self.log_search.setMaximumWidth(180)
        self.log_search.textChanged.connect(self._filter_log)
        self._log_search_action = QAction(self)
        self._log_search_action.setIcon(_icons.icon('search', '#7A7288'))
        self.log_search.addAction(self._log_search_action, QLineEdit.ActionPosition.LeadingPosition)
        log_header.addWidget(self.log_search)

        # Clear button — unified 'trash' glyph, ghost styling, real
        # accessible name (icon-only buttons must be labeled for AT).
        self._clear_log_btn = QPushButton()
        self._clear_log_btn.setFixedSize(30, 26)
        self._clear_log_btn.setToolTip("Clear log")
        self._clear_log_btn.setAccessibleName("Clear log")
        self._clear_log_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_log_btn.clicked.connect(self._clear_log)
        self._style_btn(self._clear_log_btn, 'ghost')
        _icons.set_btn_icon(self._clear_log_btn, 'trash', '#6C6480', 14)
        log_header.addWidget(self._clear_log_btn)

        log_group_layout.addLayout(log_header)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        # Mono 12px comes from the global QSS (QTextEdit:read-only) — the
        # log panel is the main view's ONE growable region and always scrolls.
        # v0.07: the CTA row went horizontal, so the well reclaimed ~46px of
        # vertical space — the empty state no longer looks like dead void.
        self.log_text.setMinimumHeight(72)
        log_group_layout.addWidget(self.log_text)
        log_group.setLayout(log_group_layout)
        main_layout.addWidget(log_group, 1)

        # ---- Settings window: every former tab, sidebar navigation ----
        # (constructed AFTER more_btn + all pages exist; non-modal)
        self.settings_dialog = SettingsDialog(self)

        # Mirror the pipeline's start/stop button state onto the single hero
        # button every 200ms (pure GUI chrome — reads only the enabled-state
        # owned by _start_worker / processing_finished, writes only visibility).
        self._run_mirror_timer = QTimer(self)
        self._run_mirror_timer.timeout.connect(self._sync_run_button)
        self._run_mirror_timer.start(200)

        # v0.23.0 — no Input-mode radios to wire anymore: the Input tab is
        # the single Import txt file group (update_mode is gone with the
        # ID Range / Markers / Single Msg modes).

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
        # v0.07: bake the initial icon tints (theme-aware glyphs) and put
        # REAL numbers in the PROCESSED counter (manifest totals, not "- / -").
        self._refresh_main_icons()
        self._refresh_pipeline_counter()

        # Auto-check bot queue on startup (after proxy validation)
        # (QTimer comes from the module-level PyQt6 wildcard import — the old
        # local re-import here shadowed the earlier _run_mirror_timer usage.)
        QTimer.singleShot(2000, self._startup_auto_check)

    # ------------------------------------------------------------------
    # v33 wireframe-redesign helpers (pure GUI chrome — no pipeline logic)
    # ------------------------------------------------------------------
    def _build_logo_lockup(self, layout: QHBoxLayout):
        """Brand lockup for the main view: a violet icon tile + the GitCurator
        wordmark + a muted tagline. v0.07: the glyph is the unified 'layers'
        SVG (white on violet works on both themes — no re-render needed)."""
        logo_box = QLabel()
        logo_box.setObjectName("logo_box")
        logo_box.setFixedSize(28, 28)
        logo_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_box.setPixmap(_icons.pixmap('layers', '#FFFFFF', 16))
        logo_box.setAccessibleName("GitCurator logo")
        layout.addWidget(logo_box)

        text_col = QVBoxLayout()
        text_col.setSpacing(0)
        title = QLabel("GitCurator")
        title.setObjectName("logo_title")
        sub = QLabel("Telegram → Ollama → Obsidian")
        sub.setObjectName("logo_sub")
        text_col.addWidget(title)
        text_col.addWidget(sub)
        layout.addLayout(text_col)
        layout.addSpacing(8)

    def log_message(self, msg, level="info"):
        """Append a colored line to the GUI log and auto-scroll to the bottom.

        Uses HTML coloring so different log levels are visually distinct:
          - error   -> red   (#f44336 — visible on both light & dark)
          - warning -> amber (#FF9800 — visible on both light & dark)
          - success -> green (#4CAF50 — visible on both light & dark)
          - info    -> gray  (#9E9E9E — visible on both light & dark)

        Also stores the entry in `self._all_log_entries` so the GUI log can
        be re-rendered when the user changes the active filter or search text
        (see `_set_log_filter` / `_filter_log`).
        """
        # Terminal colors (for console output)
        color_map = {
            "info": Fore.GREEN,
            "warning": Fore.YELLOW,
            "error": Fore.RED,
            "success": Fore.CYAN
        }
        color = color_map.get(level, Fore.WHITE)

        timestamp = datetime.now().strftime("%H:%M:%S")

        # Store entry for re-rendering on filter/search change
        if not hasattr(self, '_all_log_entries'):
            self._all_log_entries = []
        self._all_log_entries.append({'msg': str(msg), 'level': level, 'timestamp': timestamp})
        # Prevent memory leak — cap at 1000 entries (oldest are dropped)
        if len(self._all_log_entries) > 1000:
            self._all_log_entries = self._all_log_entries[-1000:]

        # HTML colors chosen to be readable on the ACTIVE theme background
        # (v31.1: theme-aware — dark shades in light mode, light in dark).
        html_color = self._log_html_colors().get(level, "#6C6480")
        # v0.07: timestamps too — the old fixed #666 sat at ~2.4:1 on the
        # recessed log well. Muted, but still above 4.5:1 on both themes.
        ts_color = '#8F89A3' if getattr(self, '_dark_mode', False) else '#7A7288'

        # Apply current filter — skip rendering if the entry doesn't match.
        if self._log_filter != "all" and level != self._log_filter:
            return
        search = self.log_search.text().lower() if hasattr(self, 'log_search') else ""
        if search and search not in str(msg).lower():
            return

        # Escape HTML special chars in the message

        safe_msg = _html_module.escape(str(msg), quote=False)
        html_line = (
            f'<span style="color:{ts_color}; font-family:Consolas,monospace;">[{timestamp}]</span> '
            f'<span style="color:{html_color}; font-family:Consolas,monospace;">{safe_msg}</span>'
        )
        self.log_text.append(html_line)

        # Auto-scroll to newest line
        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.log_text.setTextCursor(cursor)

    def _toggle_log_panel(self):
        """v33: the Progress Logs panel is a permanent fixture of the main
        view (wireframe redesign) — nothing to toggle. Kept as a safe no-op
        because _startup_auto_check still calls it."""
        pass

    def _set_log_filter(self, filter_type):
        """Set the log filter and re-render the log panel."""
        self._log_filter = filter_type
        # Update button checked states (only the active filter is checked)
        self.log_filter_all.setChecked(filter_type == "all")
        self.log_filter_errors.setChecked(filter_type == "error")
        self.log_filter_warnings.setChecked(filter_type == "warning")
        self.log_filter_success.setChecked(filter_type == "success")
        self._filter_log()

    def _filter_log(self):
        """Re-render the log panel from `self._all_log_entries` applying the
        current level filter + search text."""
        search = self.log_search.text().lower() if hasattr(self, 'log_search') else ""
        if not hasattr(self, '_all_log_entries'):
            self._all_log_entries = []

        # v31.1: theme-aware log colors (see _log_html_colors).
        html_color_map = self._log_html_colors()


        # Suppress auto-scroll flicker while we rebuild the log.
        self.log_text.clear()
        for entry in self._all_log_entries:
            level = entry.get('level', 'info')
            msg = entry.get('msg', '')
            timestamp = entry.get('timestamp', '')

            # Filter by level
            if self._log_filter != "all" and level != self._log_filter:
                continue
            # Filter by search
            if search and search not in msg.lower():
                continue

            color = html_color_map.get(level, "#9E9E9E")
            safe_msg = _html_module.escape(msg, quote=False)
            ts_color = '#8F89A3' if getattr(self, '_dark_mode', False) else '#7A7288'
            html_line = (
                f'<span style="color:{ts_color}; font-family:Consolas,monospace;">[{timestamp}]</span> '
                f'<span style="color:{color}; font-family:Consolas,monospace;">{safe_msg}</span>'
            )
            self.log_text.append(html_line)

        # Jump to the bottom after re-rendering.
        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.log_text.setTextCursor(cursor)

    def _clear_log(self):
        """Clear both the visible log and the stored entry cache."""
        if hasattr(self, '_all_log_entries'):
            self._all_log_entries.clear()
        self.log_text.clear()

    def _refresh_pipeline_counter(self):
        """v0.07 (design review “make the status readout say something”):
        keep the PROCESSED x / y counter truthful at ALL times — live counts
        while a batch runs, fetched-pending counts after SYNC, and the last
        batch manifest's real totals while idle (the numbers used to live
        only in a log line; the dedicated widget said "- / -")."""
        if not hasattr(self, 'progress_count'):
            return
        # While a batch runs, update_progress() owns the counter.
        if hasattr(self, 'start_btn') and not self.start_btn.isEnabled():
            return
        pending = getattr(self, '_bot_queue_urls', None) or []
        try:
            if pending:
                self.progress_count.setText(f"0 / {len(pending)}")
                self.progress_count.setToolTip(
                    f"{len(pending)} fetched item(s) ready to process")
                return
            done = total = 0
            vault = self.vault_combo.currentText() if hasattr(self, 'vault_combo') else ''
            if vault and os.path.isdir(vault):
                manifest_path = os.path.join(vault, 'links_manifest.json')
                with open(manifest_path, 'r', encoding='utf-8') as fh:
                    manifest = json.load(fh)
                entries = manifest.get('links', []) or []
                total = len(entries)
                done = sum(1 for e in entries
                           if e.get('status') in ('processed', 'recorded', 'skipped'))
        except Exception:
            done = total = 0   # no manifest yet (or unreadable) — honest zero
        self.progress_count.setText(f"{done} / {total}")
        if total:
            self.progress_count.setToolTip(
                f"{done} of {total} links processed (last batch manifest)")
        else:
            self.progress_count.setToolTip("No batch manifest yet — SYNC to fetch items")

