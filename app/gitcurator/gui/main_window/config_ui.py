#!/usr/bin/env python3
"""gitcurator.gui.main_window.config_ui — the config & vault-selection mixin.

Vault combo populate/change/browse/remove, config load/save, import-file
picker, mode update, input help, and the proxy dict/health/status
helpers (verbatim methods of the original MainWindow).
"""

import json, os
from gitcurator.gui._qt import *  # noqa: F401,F403 — Qt widget names
from gitcurator.gui.main_window._deps import *  # noqa: F401,F403

__all__ = ["ConfigUiMixin"]


class ConfigUiMixin:
    """ConfigUiMixin — see module docstring (methods are verbatim moves)."""


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

    def browse_vault(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Obsidian Vault")
        if folder:
            if self.vault_combo.findText(folder) == -1:
                self.vault_combo.insertItem(0, folder)
            self.vault_combo.setCurrentText(folder)
            self.on_vault_changed(folder)

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
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                return CONFIG_EXAMPLE.copy()
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
            "telegram_api_hash": self.api_hash.text(),
            "telegram_phone": self.phone.text(),
            "proxy": {
                "enabled": self.proxy_enabled.isChecked(),
                "type": self.proxy_type.currentText(),
                "host": self.proxy_host.text(),
                "port": int(self.proxy_port.text()) if self.proxy_port.text().isdigit() else 10808
            },
            "ollama": {
                "base_url": self.ollama_url.text(),
                "model": self.ollama_model.currentText()
            },
            # v26 — Fix 4: persist the cloud LLM provider selection + creds
            # so ProcessingWorker can pick the right backend on next launch.
            # Defaults to 'ollama' for backward compatibility — existing users
            # won't notice anything changed unless they explicitly switch.
            "llm_provider": "cloud" if getattr(self, 'llm_provider_cloud', None) and self.llm_provider_cloud.isChecked() else "ollama",
            "cloud_api_url": getattr(self, 'cloud_api_url', QLineEdit()).text() if hasattr(self, 'cloud_api_url') else self.config.get('cloud_api_url', 'https://api.openai.com/v1'),
            "cloud_api_key": getattr(self, 'cloud_api_key', QLineEdit()).text() if hasattr(self, 'cloud_api_key') else self.config.get('cloud_api_key', ''),
            "cloud_model": getattr(self, 'cloud_model', QLineEdit()).text().strip() if hasattr(self, 'cloud_model') else self.config.get('cloud_model', 'gpt-4o-mini'),
            "github_token": self.github_token.text(),
            "bot_token": getattr(self, 'bot_token', QLineEdit()).text() if hasattr(self, 'bot_token') else "",
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

    def select_import_file(self):
        file, _ = QFileDialog.getOpenFileName(self, "Select .txt file", "", "Text Files (*.txt)")
        if file:
            self.import_file.setText(file)

    def update_mode(self):
        is_telegram = self.mode_telegram.isChecked()
        is_keyword = self.mode_keyword.isChecked()
        is_import = self.mode_import.isChecked()
        is_single = self.mode_single.isChecked()
        # Show ONLY the group box relevant to the selected mode; hide the rest.
        # This declutters the Input tab so the user sees just one section at a
        # time instead of all four stacked together.
        self.range_group.setVisible(is_telegram)
        self.marker_group.setVisible(is_keyword)
        self.import_group.setVisible(is_import)
        self.single_group.setVisible(is_single)
        # Keep the enable/disable calls for backward compatibility (fields are
        # also visually hidden when their parent group is hidden, but this
        # makes the enabled state explicit and consistent with prior behavior).
        self.range_from.setEnabled(is_telegram)
        self.range_to.setEnabled(is_telegram)
        self.offset_start.setEnabled(is_telegram)
        self.offset_count.setEnabled(is_telegram)
        self.import_file.setEnabled(is_import)
        self.single_id.setEnabled(is_single)
        self.keyword_start.setEnabled(is_keyword)
        self.keyword_end.setEnabled(is_keyword)

    def show_input_help(self):
        """Popup with concise usage instructions for the Input tab.

        Replaces the old verbose 'HOW TO USE' instruction box that took up
        a lot of vertical space. Triggered by the small '?' button next to
        the mode selector.
        """
        self._show_custom_message_box(
            "Input — How to Use",
            "ID Range: Enter From ID and To ID, then click Start Processing.\n\n"
            "Advanced (offset): Check 'use offset instead of ID range', "
            "enter Offset from + Count, then click Start.\n\n"
            "Markers: Click Generate, copy the code, paste it into the "
            "FIRST and LAST messages of your desired range in Saved Messages, then "
            "click Find by Marker. The From/To IDs are filled in automatically "
            "and the mode switches to ID Range.\n\n"
            "Custom keywords (advanced): Check the box, enter start/end keywords, "
            "click Find by Keywords.\n\n"
            "Import .txt: Select a .txt file with one GitHub URL per line.\n\n"
            "Single Msg: Enter one message ID to fetch just that message.\n\n"
            "Preview: In ID Range mode, click Preview to fetch the first/last "
            "messages of the range without processing.",
            success=True
        )

    def _get_proxy_dict(self):
        return {
            "enabled": self.proxy_enabled.isChecked(),
            "type": self.proxy_type.currentText(),
            "host": self.proxy_host.text(),
            "port": int(self.proxy_port.text()) if self.proxy_port.text().isdigit() else 10808
        }

    def _check_proxy_health(self):
        """v22 Feature 7: Poll the configured proxy port and update the
        status-label dot in the action button row. Non-blocking — uses a
        2-second socket connect_ex timeout. Runs on the GUI thread (the
        socket call returns well within 2s whether the host is up or down)."""
        # Guard: widget might not exist yet (early init) or might've been
        # destroyed during shutdown.
        if not hasattr(self, 'proxy_status_label'):
            return
        try:
            proxy = self._get_proxy_dict()
        except Exception:
            return
        try:
            if not proxy.get('enabled'):
                self.proxy_status_label.setText("⚪")
                self.proxy_status_label.setToolTip("Proxy disabled")
                self._set_proxy_status_text("Idle", "Proxy disabled")
                return
            host = proxy.get('host', '127.0.0.1') or '127.0.0.1'
            try:
                port = int(proxy.get('port', 10808))
            except (TypeError, ValueError):
                port = 10808
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            try:
                result = sock.connect_ex((host, port))
            finally:
                try:
                    sock.close()
                except Exception:
                    pass
            if result == 0:
                self.proxy_status_label.setText("🟢")
                self.proxy_status_label.setToolTip(f"Proxy OK ({host}:{port})")
                self._set_proxy_status_text("Connected", f"Proxy OK ({host}:{port})")
            else:
                self.proxy_status_label.setText("🔴")
                self.proxy_status_label.setToolTip(f"Proxy unreachable ({host}:{port})")
                self._set_proxy_status_text("Error", f"Proxy unreachable ({host}:{port})")
        except Exception:
            try:
                self.proxy_status_label.setText("🔴")
                self.proxy_status_label.setToolTip("Proxy check failed")
                self._set_proxy_status_text("Error", "Proxy check failed")
            except Exception:
                pass

    def _set_proxy_status_text(self, state: str, tooltip: str):
        """v31.1 (WCAG 1.4.1): the status DOT is always accompanied by a TEXT
        label — color alone never conveys state ("Connected/Idle/Error")."""
        if not hasattr(self, 'proxy_status_text'):
            return
        self.proxy_status_text.setText(state)
        self.proxy_status_text.setToolTip(tooltip)
