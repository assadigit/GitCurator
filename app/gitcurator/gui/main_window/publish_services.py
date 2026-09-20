#!/usr/bin/env python3
"""gitcurator.gui.main_window.publish_services — the publish & seal services mixin.

VaultSeal seal now/status, GoodRepos publish now/status, and the
dashboard launchers (verbatim methods of the original MainWindow).
"""

from gitcurator.gui._qt import *  # noqa: F401,F403 — Qt widget names
from gitcurator.gui.main_window._deps import *  # noqa: F401,F403

__all__ = ["PublishServicesMixin"]


class PublishServicesMixin:
    """PublishServicesMixin — see module docstring (methods are verbatim moves)."""


    # ========================================================================
    # v31 — VaultSeal (post-run vault backup to a private GitHub repo)
    # ========================================================================

    def _start_vault_seal(self):
        """Seal the vault in a background thread: commit + best-effort push.

        Mirrors the v29.4 BackupWorker pattern — the GUI never blocks on git
        or the network; the result lands in the log panel and refreshes the
        Backup tab status label.
        """
        run_summary = {}
        try:
            run_summary = {
                "processed": int(getattr(self.worker, 'processed', 0) or 0),
                "total": int(getattr(self.worker, 'total', 0) or 0),
            }
        except Exception:
            run_summary = {}

        class VaultSealWorker(QThread):
            done = pyqtSignal(bool, str)

            def __init__(self, config, summary):
                super().__init__()
                self.config = config
                self.summary = summary

            def run(self):
                try:
                    result = _vaultseal.seal_from_config(
                        self.config, run_summary=self.summary)
                    self.done.emit(result.ok, result.describe())
                except Exception as e:  # belt & suspenders — seal() never raises
                    self.done.emit(False, str(e))

        self.log_message("🛡️ VaultSeal: sealing vault → private GitHub mirror…", "info")
        self._vaultseal_worker = VaultSealWorker(self.config, run_summary)
        self._vaultseal_worker.done.connect(self._vault_seal_result)
        self._vaultseal_worker.start()

    def _vault_seal_result(self, ok, message):
        if hasattr(self, 'vaultseal_now_btn'):
            self.vaultseal_now_btn.setEnabled(True)
            self.vaultseal_now_btn.setText("🛡️ Seal Now")
        icon = "✅" if ok else "⚠️"
        level = "success" if ok else "warning"
        self.log_message(f"{icon} VaultSeal: {message}", level)
        self._vaultseal_refresh_status()

    def _vaultseal_now(self):
        """Manual seal — the exact code path the post-run hook uses."""
        self._backup_save_config()
        if not self.config.get('vault_path'):
            self._show_custom_message_box("No Vault", "Set a vault path first (📁 Vault tab).", success=False)
            return
        if hasattr(self, 'vaultseal_now_btn'):
            self.vaultseal_now_btn.setEnabled(False)
            self.vaultseal_now_btn.setText("Sealing…")
        self._start_vault_seal()

    def _vaultseal_refresh_status(self):
        """Cheap status line — config only, no git subprocesses."""
        if not hasattr(self, 'vaultseal_status_label'):
            return
        if hasattr(self, 'vaultseal_enabled_check'):
            enabled = self.vaultseal_enabled_check.isChecked()
        else:
            enabled = (self.config.get('vaultseal') or {}).get('enabled', True)
        has_vault = bool(self.config.get('vault_path'))
        has_token = bool((self.config.get('github_token') or '').strip())
        if not enabled:
            text, color = "● Disabled", self._status_colors()['error']
        elif not has_vault:
            text, color = "● No vault selected", self._status_colors()['warning']
        elif not has_token:
            text, color = "● Local-only (no GitHub token — commits, no push)", self._status_colors()['warning']
        else:
            text, color = "● Ready — auto-seal after every run", self._status_colors()['success']
        self.vaultseal_status_label.setText(text)
        self.vaultseal_status_label.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {color};")

    # ========================================================================
    # v32 — GoodRepos (post-run publish of the PUBLIC curated directory)
    # ========================================================================

    def _start_goodrepos_publish(self):
        """Publish the public directory in a background thread (never blocks).

        Mirrors the VaultSealWorker pattern exactly — the GUI never blocks
        on git or the network; the result lands in the log panel and
        refreshes the Backup tab status label.
        """
        run_summary = {}
        try:
            run_summary = {
                "processed": int(getattr(self.worker, 'processed', 0) or 0),
                "total": int(getattr(self.worker, 'total', 0) or 0),
            }
        except Exception:
            run_summary = {}

        class GoodReposWorker(QThread):
            done = pyqtSignal(bool, str)

            def __init__(self, config, summary):
                super().__init__()
                self.config = config
                self.summary = summary

            def run(self):
                try:
                    result = _goodrepos.publish_from_config(
                        self.config, run_summary=self.summary)
                    self.done.emit(result.ok, result.describe())
                except Exception as e:  # publish() never raises — belt & suspenders
                    self.done.emit(False, str(e))

        self.log_message("🌟 Good Repos: publishing the curated directory → public repo…", "info")
        self._goodrepos_worker = GoodReposWorker(self.config, run_summary)
        self._goodrepos_worker.done.connect(self._goodrepos_result)
        self._goodrepos_worker.start()

    def _goodrepos_result(self, ok, message):
        if hasattr(self, 'goodrepos_now_btn'):
            self.goodrepos_now_btn.setEnabled(True)
            self.goodrepos_now_btn.setText("🌟 Publish Now")
        icon = "✅" if ok else "⚠️"
        level = "success" if ok else "warning"
        self.log_message(f"{icon} Good Repos: {message}", level)
        self._goodrepos_refresh_status()

    def _goodrepos_now(self):
        """Manual publish — the exact code path the post-run hook uses."""
        self._backup_save_config()
        if not self.config.get('vault_path'):
            self._show_custom_message_box("No Vault", "Set a vault path first (📁 Vault tab).", success=False)
            return
        if hasattr(self, 'goodrepos_now_btn'):
            self.goodrepos_now_btn.setEnabled(False)
            self.goodrepos_now_btn.setText("Publishing…")
        self._start_goodrepos_publish()

    def _goodrepos_refresh_status(self):
        """Cheap status line — config only, no git subprocesses."""
        if not hasattr(self, 'goodrepos_status_label'):
            return
        if hasattr(self, 'goodrepos_enabled_check'):
            enabled = self.goodrepos_enabled_check.isChecked()
        else:
            enabled = (self.config.get('goodrepos') or {}).get('enabled', True)
        has_vault = bool(self.config.get('vault_path'))
        has_token = bool((self.config.get('github_token') or '').strip())
        if not enabled:
            text, color = "● Disabled", self._status_colors()['error']
        elif not has_vault:
            text, color = "● No vault selected", self._status_colors()['warning']
        elif not has_token:
            text, color = "● Local-only (no GitHub token — README built, no push)", self._status_colors()['warning']
        else:
            text, color = "● Ready — auto-publish after every run", self._status_colors()['success']
        self.goodrepos_status_label.setText(text)
        self.goodrepos_status_label.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {color};")

    def _open_dashboard_from_backup_tab(self):
        """Open the web dashboard from the Backup tab."""
        import webbrowser
        worker_url = self.dash_worker_url_input.text().strip().rstrip('/')
        if not worker_url:
            worker_url = self.config.get('cloudflare_worker_url', '').rstrip('/')
        if not worker_url:
            self._show_custom_message_box(
                "No Worker URL",
                "Enter your Worker URL first.\n\n"
                "Example: https://github-to-obsidian-bot.your-subdomain.workers.dev",
                success=False
            )
            return
        self._backup_save_config()
        webbrowser.open(f"{worker_url}/dashboard")

    def _open_dashboard_browser(self):
        """Open dashboard from toolbar button."""
        import webbrowser
        worker_url = ''
        if hasattr(self, 'dash_worker_url_input'):
            worker_url = self.dash_worker_url_input.text().strip().rstrip('/')
        if not worker_url:
            worker_url = self.config.get('cloudflare_worker_url', '').rstrip('/')
        if not worker_url:
            self._show_custom_message_box(
                "Dashboard Not Configured",
                "Enter your Worker URL in the 💾 Backup tab first.\n\n"
                "Example: https://github-to-obsidian-bot.your-subdomain.workers.dev",
                success=False
            )
            return
        webbrowser.open(f"{worker_url}/dashboard")
