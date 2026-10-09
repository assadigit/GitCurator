"""VaultScanUiMixin — the [Scan] CTA (v0.61.0 THE VAULT SCAN).

The owner's ask (session, verbatim): "New CTA row: [Fetch] [Scan]
[Test Connection]." The button sits between the hero SYNC and Test
Connection; its job runs in a TestWorker thread (the same
lightweight pattern Test Connection uses — never the GUI thread):
the vault walk → the plan (the v0.60.1 auto-delete grammar + the
LLM's filing proposal) → the Telegram ask (the banish-gate round-trip
template; 300s without an answer = safe defer) → on the owner's ✅:
folders + byte-identical moves + the banishment machinery. The log
tells the whole story; the button re-enables when the story ends.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QPushButton, QSizePolicy

from gitcurator.gui.processing_worker import TestWorker
from gitcurator.gui.worker_jobs import _vault_scan_job


class VaultScanUiMixin:
    """VaultScanUiMixin — the Scan button + its background job."""

    def _start_vault_scan(self):
        """🔍 Scan — the librarian's pass over the whole vault."""
        if self.worker is not None and not self.worker.isFinished():
            self.log_message(
                "⏳ A batch is running — Scan is available when it "
                "finishes.", "warning")
            return
        if getattr(self, '_scan_worker', None) is not None \
                and not self._scan_worker.isFinished():
            self.log_message(
                "⏳ A vault scan is already running — its Telegram ask "
                "is waiting (or its plan is being applied).", "warning")
            return
        snapshot = dict(self.config)
        try:
            wv = self.website_vault_input.text().strip()
            if wv:
                snapshot['website_vault_path'] = wv
        except Exception:
            pass    # widget access must never break the scan
        self.log_message(
            "🔍 Vault scan started — reading the vault, then the plan "
            "goes to your Telegram for confirmation BEFORE anything is "
            "deleted or moved (no answer in 300s = nothing done).",
            "info")
        try:
            self.scan_btn.setEnabled(False)
            self.scan_btn.setText("Scanning…")
        except Exception:
            pass
        self._scan_worker = TestWorker(
            _vault_scan_job, 'vault_scan', snapshot)
        self._scan_worker.log_message.connect(
            lambda m, l: self.log_message(m, l))
        self._scan_worker.finished_signal.connect(
            self._vault_scan_finished)
        # keep the worker alive until its story ends (the TestWorker
        # contract — _keep_worker's law):
        self._scan_worker_ref = self._scan_worker
        self._scan_worker.start()

    def _vault_scan_finished(self, name: str, result: dict):
        """The scan's story ended — speak the verdict, re-arm the
        button. (Runs on the GUI thread via the signal.)"""
        try:
            self.scan_btn.setEnabled(True)
            self.scan_btn.setText("Scan")
        except Exception:
            pass
        result = result or {}
        verdict = str(result.get('verdict') or 'defer')
        if verdict == 'clean':
            self.log_message(
                "✅ Vault scan: the library is clean — nothing to ask, "
                "nothing to do.", "success")
        elif verdict == 'confirmed':
            self.log_message(
                f"✅ Vault scan complete — {result.get('applied', 0)} "
                f"note(s) removed, {result.get('moved', 0)} re-filed, "
                f"{result.get('folders_created', 0)} new folder(s).",
                "success")
        elif verdict == 'error':
            self.log_message(
                f"💥 Vault scan failed: {result.get('error', '?')}",
                "error")
        # declined / timeout / defer already spoke their own lines in
        # the job — the button is the only state left to restore.
