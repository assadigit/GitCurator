"""MainWindow VaultConfigMixin — methods moved verbatim from the MainWindow in gitcurator/gui/app.py (branch refactor/gui-app-split; see docs/history/REFACTOR_PLAN.md)."""

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

from gitcurator.gui.vault_index import find_obsidian_vaults

class VaultConfigMixin:
    """VaultConfigMixin"""

    # ------------------------------------------------------------------
    # UI Helpers
    # ------------------------------------------------------------------
    def populate_vaults(self):
        self.vault_combo.clear()
        history = self.config.get('vaults_history', [])
        current = self.config.get('vault_path', '')
        if current and current not in history:
            history.insert(0, current)
        discovered = find_obsidian_vaults()
        all_vaults = list(dict.fromkeys(history + discovered))
        for v in all_vaults:
            self.vault_combo.addItem(v)
        if current:
            index = self.vault_combo.findText(current)
            if index >= 0:
                self.vault_combo.setCurrentIndex(index)
            else:
                self.vault_combo.insertItem(0, current)
                self.vault_combo.setCurrentIndex(0)

    def on_vault_changed(self, text):
        if text:
            self.config['vault_path'] = text
            history = self.config.get('vaults_history', [])
            if text not in history:
                history.insert(0, text)
                self.config['vaults_history'] = history
            self.save_config()
            # v0.07: the PROCESSED counter reads the ACTIVE vault's manifest
            # — keep it truthful when the vault selection changes.
            self._refresh_pipeline_counter()

    def browse_vault(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Obsidian Vault")
        if folder:
            if self.vault_combo.findText(folder) == -1:
                self.vault_combo.insertItem(0, folder)
            self.vault_combo.setCurrentText(folder)
            self.on_vault_changed(folder)

    # v0.10.0 — Phase 1: browse / live-status / save handlers for the new
    # vault pickers on the 📁 Vault settings page.
    def browse_website_vault(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Websites Vault (a folder that does not exist yet is fine)")
        if folder:
            self.website_vault_input.setText(folder)
            self._save_vault_page()

    def browse_manual_vault(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Manual Notes Vault")
        if folder:
            self.manual_vault_input.setText(folder)
            self._save_vault_page()

    def _vault_path_status(self, path, kind):
        """(text, state) for a vault path — the live status line under each
        picker. Websites folders that don't exist yet are 'will be created'
        (its pipeline creates them); the GitHub vault must exist; the
        Manual vault is the owner's to create. The state feeds _set_status
        (the theme kit paints the semantic color)."""
        path = (path or '').strip()
        if not path:
            return "● not set", 'warning'
        if os.path.isdir(path):
            return "● found", 'success'
        if kind == 'websites':
            return "● will be created (folder does not exist yet)", 'warning'
        if kind == 'github':
            return "● missing on disk", 'error'
        return "● not found — you create this vault yourself", 'warning'

    def _refresh_vault_page_status(self, *args):
        """Update the two live status labels (no saving — cheap, per keystroke)."""
        if not hasattr(self, 'website_vault_status'):
            return
        text, state = self._vault_path_status(
            self.website_vault_input.text(), 'websites')
        self.website_vault_status.setText(text)
        self._set_status(self.website_vault_status, state, strong=True)
        text, state = self._vault_path_status(
            self.manual_vault_input.text(), 'manual')
        self.manual_vault_status.setText(text)
        self._set_status(self.manual_vault_status, state, strong=True)

    def _save_vault_page(self, *args):
        """Commit the Vault page (paths + repo + switches) via the ONE
        merge-based save path — same as every other settings page."""
        self._refresh_vault_page_status()
        self.save_config()

    # -- v0.40.0 — the Sound page's save-on-change handlers --------------

    def _sound_volume_value(self) -> float:
        """The volume slider's value as the 0.0–1.0 float save_config
        writes (sound_volume). The slider itself is 0–100; Qt's
        QSoundEffect range is [0, 1] — the value is clamped on read
        (widget init), on write (here) and again inside BatchSound, so
        a hand-edited 7.5 or a NaN can never reach Qt. A missing widget
        keeps the previous config value, defaulting to 0.8."""
        if hasattr(self, 'sound_volume_slider'):
            try:
                return max(0.0, min(1.0,
                                    self.sound_volume_slider.value() / 100.0))
            except Exception:
                pass
        try:
            _v = float(self.config.get('sound_volume', 0.8))
        except (TypeError, ValueError):
            _v = 0.8
        if _v != _v:  # NaN
            _v = 0.8
        return max(0.0, min(1.0, _v))

    def _on_sound_volume_changed(self, value: int):
        """Every drag tick updates the % label ONLY — the write happens
        on slider release (_save_sound_page), so scrubbing the slider
        never becomes a config.json write storm."""
        if getattr(self, 'sound_volume_label', None) is not None:
            self.sound_volume_label.setText(f"{int(value)}%")

    def _save_sound_page(self, *args):
        """Commit the Sound page (switch + volume) via the ONE merge-based
        save path — the Vault page's pattern, one write per commit."""
        self.save_config()

    def remove_vault(self):
        current = self.vault_combo.currentText()
        if not current:
            return
        reply = QMessageBox.StandardButton.Yes if self._show_custom_question("Remove Vault", f"Remove '{current}' from the list? (This will not delete the folder.)") else QMessageBox.StandardButton.No
        if reply == QMessageBox.StandardButton.Yes:
            self.vault_combo.removeItem(self.vault_combo.currentIndex())
            history = self.config.get('vaults_history', [])
            if current in history:
                history.remove(current)
                self.config['vaults_history'] = history
            if self.config.get('vault_path') == current:
                self.config['vault_path'] = ''
            self.save_config()

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    config = json.load(f)
            except (json.JSONDecodeError, OSError):
                return CONFIG_EXAMPLE.copy()
            # v0.07.1 — Fix: heal hand-edited configs. Credentials pasted
            # with surrounding whitespace (typically a trailing newline)
            # broke PyGithub ("Invalid ... character(s) in header value:
            # 'token ghp_…\n'") even though the token itself was valid.
            # Strip the credential-bearing flat keys at load so a saved or
            # hand-edited config can never poison the app again.
            for k in ('telegram_api_id', 'telegram_api_hash', 'telegram_phone',
                      'github_token', 'bot_token', 'cloud_api_key',
                      'cloudflare_worker_url'):
                v = config.get(k)
                if isinstance(v, str):
                    config[k] = v.strip()
            px = config.get('proxy')
            if isinstance(px, dict) and isinstance(px.get('host'), str):
                px['host'] = px['host'].strip()
            return config
        else:
            return CONFIG_EXAMPLE.copy()

    def save_config(self):
        """v30 — Fix (save_config was destroying data): MERGE, never
        whitelist-rebuild.

        What was wrong (all fixed here):
          1. The old body rebuilt config.json from ~25 hardcoded keys — every
             OTHER key was silently DESTROYED on save: cloudflare_install_id,
             cloudflare_shared_secret, gdrive_*, hmac_secret, user-added keys.
             Pairing with the Cloudflare worker / GDrive backup was wiped the
             first time the app closed.
          2. timeout_per_repo / max_retries / delay_between_api_calls were
             hardcoded (60 / 3 / 0.5) — user-tuned values were stomped on every
             save. They are now only DEFAULTED when absent (see
             storage.merge_config).
          3. ``self.config = config`` REBOUND the attribute — the running
             ProcessingWorker kept the OLD dict, so mid-batch config changes
             (like the LLM model fix) were invisible to it, and two divergent
             configs drifted until restart. self.config is now mutated IN
             PLACE so every holder sees the same data.
          4. The file write is now atomic (tempfile + os.replace) — a crash
             mid-save can no longer truncate config.json.
        """
        ui_values = {
            "telegram_api_id": int(self.api_id.text()) if self.api_id.text().isdigit() else 0,
            # v0.07.1 — credential fields strip()d at the source so a
            # pasted trailing newline can never reach the file (or PyGithub
            # headers) again.
            "telegram_api_hash": self.api_hash.text().strip(),
            "telegram_phone": self.phone.text().strip(),
            "proxy": {
                "enabled": self.proxy_enabled.isChecked(),
                "type": self.proxy_type.currentText(),
                "host": self.proxy_host.text().strip(),
                "port": int(self.proxy_port.text()) if self.proxy_port.text().isdigit() else 10808,
                # v0.19.0 — the Websites pipeline rides the same proxy.
                "use_for_web": self.proxy_use_for_web.isChecked()
            },
            "ollama": {
                "base_url": self.ollama_url.text(),
                "model": self.ollama_model.currentText()
            },
            # v26 — Fix 4: persist the cloud LLM provider selection + creds
            # so ProcessingWorker can pick the right backend on next launch.
            # Defaults to 'ollama' for backward compatibility — existing users
            # won't notice anything changed unless they explicitly switch.
            # v0.15.0 — llama.cpp engine detection: the third provider value
            # 'llamacpp' + its llamacpp_* keys (model empty = auto-detected
            # from the running llama-server).
            # v0.23.0 — the two-level radios: the host radio (local/cloud)
            # and, inside local, the engine radio (Ollama/llama.cpp). The
            # stored value keeps the same three strings as always.
            "llm_provider": (
                "cloud" if getattr(self, 'llm_host_cloud', None)
                and self.llm_host_cloud.isChecked()
                else "llamacpp" if getattr(self, 'llm_provider_llamacpp', None)
                and self.llm_provider_llamacpp.isChecked() else "ollama"),
            "cloud_api_url": getattr(self, 'cloud_api_url', QLineEdit()).text() if hasattr(self, 'cloud_api_url') else self.config.get('cloud_api_url', 'https://api.openai.com/v1'),
            "cloud_api_key": getattr(self, 'cloud_api_key', QLineEdit()).text().strip() if hasattr(self, 'cloud_api_key') else self.config.get('cloud_api_key', ''),
            "cloud_model": getattr(self, 'cloud_model', QLineEdit()).text().strip() if hasattr(self, 'cloud_model') else self.config.get('cloud_model', 'gpt-4o-mini'),
            "llamacpp_api_url": (self.llamacpp_api_url.text().strip()
                                  if hasattr(self, 'llamacpp_api_url')
                                  else self.config.get(
                                      'llamacpp_api_url',
                                      _llm_client.LLAMACPP_DEFAULT_BASE + '/v1')),
            "llamacpp_api_key": (self.llamacpp_api_key.text().strip()
                                  if hasattr(self, 'llamacpp_api_key')
                                  else self.config.get('llamacpp_api_key', '')),
            "llamacpp_model": (self.llamacpp_model.currentText().strip()
                                if hasattr(self, 'llamacpp_model')
                                else self.config.get('llamacpp_model', '')),
            # v0.13.0 — Phase 4: the explicit context window (llm_num_ctx).
            # v0.23.0 — plus the OUTPUT half (llm_max_output_tokens):
            # num_ctx/num_predict on Ollama, warning-budget/max_tokens on
            # the cloud paths, max_tokens (required) on Claude.
            "llm_num_ctx": self._llm_num_ctx_value(),
            "llm_max_output_tokens": self._llm_max_output_tokens_value(),
            "github_token": self.github_token.text().strip(),
            "bot_token": getattr(self, 'bot_token', QLineEdit()).text().strip() if hasattr(self, 'bot_token') else "",
            "bot_username": getattr(self, 'bot_username', QLineEdit()).text() if hasattr(self, 'bot_username') else "githubfetcherbot",
            "vault_path": self.vault_combo.currentText() if self.vault_combo.currentText() else "",
            "vaults_history": self.config.get('vaults_history', []),
            "log_level": "INFO",
            "dark_mode": getattr(self, '_dark_mode', False),
            # v25 pre-flight: persist the last-processed bot message ID so
            # "📬 Process New" can skip already-processed messages on the
            # next run. Also persist the configurable delays so the user
            # can tune rate-limit handling from config.json.
            "last_processed_msg_id": int(self.config.get('last_processed_msg_id', 0) or 0),
            "large_batch_extra_delay": float(self.config.get('large_batch_extra_delay', 1.5)),
            "banner_throttle_10": float(self.config.get('banner_throttle_10', 2)),
            "banner_throttle_50": float(self.config.get('banner_throttle_50', 5)),
            # v29.4 — Cloudflare worker URL (for dashboard) + local backup.
            # NOTE: cloudflare_install_id / cloudflare_shared_secret / other
            # cloudflare_* and gdrive_* keys live in self.config and are
            # PRESERVED by the merge — they are no longer wiped on save.
            "cloudflare_worker_url": getattr(self, 'dash_worker_url_input', QLineEdit()).text().strip() if hasattr(self, 'dash_worker_url_input') else self.config.get('cloudflare_worker_url', ''),
            "backup_enabled": getattr(self, 'backup_enabled_check', None) and self.backup_enabled_check.isChecked() if hasattr(self, 'backup_enabled_check') else self.config.get('backup_enabled', False),
            "backup_folder": getattr(self, 'backup_folder_input', QLineEdit()).text().strip() if hasattr(self, 'backup_folder_input') else self.config.get('backup_folder', ''),
            "backup_max": self.config.get('backup_max', 10),
            # v0.09 (merge) — 404 quarantine threshold (Settings → Dashboard
            # spinbox; defensive hasattr pattern like every optional widget).
            "notfound_strike_threshold": (
                self.quarantine_threshold_spin.value()
                if hasattr(self, 'quarantine_threshold_spin')
                else self.config.get('notfound_strike_threshold', 3)),
            # v31 — VaultSeal (GitHub mirror of the vault). Defensive hasattr
            # pattern: the widgets live in the Backup tab and always exist by
            # the time the main window saves — but never bet on widget order.
            "vaultseal": {
                "enabled": (self.vaultseal_enabled_check.isChecked()
                            if hasattr(self, 'vaultseal_enabled_check')
                            else (self.config.get('vaultseal') or {}).get('enabled', True)),
                "auto_push": (self.vaultseal_push_check.isChecked()
                              if hasattr(self, 'vaultseal_push_check')
                              else (self.config.get('vaultseal') or {}).get('auto_push', True)),
                "repo_name": (self.vaultseal_repo_input.text().strip()
                              if hasattr(self, 'vaultseal_repo_input')
                              else (self.config.get('vaultseal') or {}).get('repo_name', '')),
            },
            # v32 — GoodRepos (public curated directory). Same defensive
            # hasattr pattern as VaultSeal above.
            "goodrepos": {
                "enabled": (self.goodrepos_enabled_check.isChecked()
                            if hasattr(self, 'goodrepos_enabled_check')
                            else (self.config.get('goodrepos') or {}).get('enabled', True)),
                "auto_push": (self.goodrepos_push_check.isChecked()
                              if hasattr(self, 'goodrepos_push_check')
                              else (self.config.get('goodrepos') or {}).get('auto_push', True)),
                "repo_name": (self.goodrepos_repo_input.text().strip()
                              if hasattr(self, 'goodrepos_repo_input')
                              else (self.config.get('goodrepos') or {}).get('repo_name', 'good-repos')),
            },
            # v0.10.0 — Phase 1: vault settings + pipeline switches. Same
            # defensive hasattr pattern; a missing widget keeps the config
            # value (merge_config never drops keys).
            "website_vault_path": (self.website_vault_input.text().strip()
                                   if hasattr(self, 'website_vault_input')
                                   else self.config.get('website_vault_path', '')),
            "manual_vault_path": (self.manual_vault_input.text().strip()
                                  if hasattr(self, 'manual_vault_input')
                                  else self.config.get('manual_vault_path', '')),
            "website_repo_name": (self.website_repo_input.text().strip()
                                  if hasattr(self, 'website_repo_input')
                                  else self.config.get('website_repo_name', '')),
            # v0.28.0 — THE LAW: the saved list is the EXTRAS on top of
            # links.LAW_BLOCKED_DOMAINS (which always applies and cannot
            # be removed — see blocked_domains_from_config). Comma-
            # separated text → clean list; an EMPTY field means "just
            # the law" (no extras), NOT allow-all.
            "web_blocked_domains": (
                [d.strip().lower() for d in
                 self.web_blocked_input.text().split(',') if d.strip()]
                if hasattr(self, 'web_blocked_input')
                else self.config.get('web_blocked_domains',
                                     list(_links.DEFAULT_BLOCKED_DOMAINS))),
            # v0.21.0 — self domains (the app's own bot): same text→list
            # contract as the blocked list; empty field = deliberate opt-out.
            "web_self_domains": (
                [d.strip().lower() for d in
                 self.web_self_input.text().split(',') if d.strip()]
                if hasattr(self, 'web_self_input')
                else self.config.get('web_self_domains',
                                     list(_links.DEFAULT_SELF_DOMAINS))),
            "taxonomy_path": self.config.get('taxonomy_path', ''),
            "pipelines": {
                "github": (self.pipeline_github_check.isChecked()
                           if hasattr(self, 'pipeline_github_check')
                           else (self.config.get('pipelines') or {}).get('github', True)),
                "websites": (self.pipeline_websites_check.isChecked()
                             if hasattr(self, 'pipeline_websites_check')
                             else (self.config.get('pipelines') or {}).get('websites', False)),
            },
            # v0.40.0 — the Sound page (Settings → Sound): the chime's
            # master switch + volume. Same defensive hasattr pattern as
            # every optional widget; the slider's 0–100 maps to 0.0–1.0
            # and is clamped into Qt's [0, 1] (QSoundEffect's range).
            "sound_enabled": (
                self.sound_enabled_check.isChecked()
                if hasattr(self, 'sound_enabled_check')
                else bool(self.config.get('sound_enabled', True))),
            "sound_volume": self._sound_volume_value(),
        }

        # Merge UI values into the EXISTING config — unknown keys survive,
        # nested dicts merge key-by-key, tuning keys are only defaulted.
        merged = _storage.merge_config(self.config, ui_values)

        # Atomic write — a crash mid-save can no longer truncate config.json.
        _storage.write_config_file(CONFIG_FILE, merged)

        # v30 — NEVER rebind self.config. Mutate in place so the running
        # worker (which holds this exact dict) sees updates live.
        self.config.clear()
        self.config.update(merged)

    def load_ui_config(self):
        pass

