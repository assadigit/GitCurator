"""VaultScanUiMixin — the [Scan] CTA (v0.61.0 THE VAULT SCAN).

The owner's ask (session, verbatim): "New CTA row: [Fetch] [Scan]
[Test Connection]." The button sits between the hero SYNC and Test
Connection; its job runs in a TestWorker thread (the same
lightweight pattern Test Connection uses — never the GUI thread):
the vault walk → the plan (the v0.60.1 auto-delete grammar + the
LLM's filing proposal) → the confirm door → on the owner's ✅:
folders + byte-identical moves + the banishment machinery. The log
tells the whole story; the button re-enables when the story ends.

v0.62.0 — THE GUI CONFIRM DOOR (the owner's report, verbatim: "the
scan now suggest new folders to be made, but there is no modal or
accept or confirm button to actually LLM do them"): the desktop's
own launches inject the worker's ask-gate
(``request_scan_confirm``) into the job, so the plan opens in
ScanPlanDialog ON SCREEN — 🗂️ Apply plan / ✋ Keep everything —
instead of dying in the log (config ``scan_confirm_door`` can pin
'telegram' to keep the old round-trip). The ask-gate is the
login-code pattern's twin: the plan rides ``scan_confirm_requested``
up to the GUI thread, the modal runs a nested event loop, and the
verdict rides ``provide_scan_verdict`` back down before the worker
proceeds — never the GUI thread for the scan itself, exactly the
TestWorker law.
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
            "appears here for your confirmation BEFORE anything is "
            "deleted or moved (no answer in 300s = nothing done).",
            "info")
        try:
            self.scan_btn.setEnabled(False)
            self.scan_btn.setText("Scanning…")
        except Exception:
            pass
        # v0.61.1 — Fix: the job takes (config, log_signal); the raw call
        # crashed with TypeError on EVERY Scan click (the owner's run,
        # 2026-10-10: "_vault_scan_job() missing 1 required positional
        # argument: 'log_signal'"). Re-bind to a closure that feeds the
        # worker's own log_message signal in — the exact pattern Test
        # Connection's battery uses (worker._fn = _job).
        worker = TestWorker(_vault_scan_job, 'vault_scan', snapshot)

        def _job(cfg):
            # v0.62.0 — the GUI confirm door: the worker's own ask-gate
            # rides in as confirm_gui, so the plan opens in ScanPlanDialog
            # (the login-code pattern — the worker thread blocks on the
            # Event while the modal stands open on the GUI thread).
            return _vault_scan_job(cfg, worker.log_message,
                                   confirm_gui=worker.request_scan_confirm)
        worker._fn = _job

        self._scan_worker = worker
        self._scan_worker.log_message.connect(
            lambda m, l: self.log_message(m, l))
        # v0.62.0 — the plan's ride up to the GUI thread: the modal opens
        # here (never in the worker), and the verdict rides back down.
        self._scan_worker.scan_confirm_requested.connect(
            lambda plan, w=worker: self._on_scan_confirm_requested(plan, w))
        self._scan_worker.finished_signal.connect(
            self._vault_scan_finished)
        # keep the worker alive until its story ends (the TestWorker
        # contract — _keep_worker's law):
        self._scan_worker_ref = self._scan_worker
        self._scan_worker.start()

    def _on_scan_confirm_requested(self, plan: dict, worker):
        """v0.62.0 — THE GUI CONFIRM DOOR: the scan's ask reached the
        owner's screen. Runs on the GUI thread (the signal's own hop);
        the modal's nested event loop stands open while the worker
        blocks on its Event — the answer rides provide_scan_verdict
        back down. The vault is never touched here: the verdict is the
        job's to enforce, exactly like a Telegram confirm."""
        try:
            timeout_s = 300.0
            try:
                timeout_s = float(
                    (self.config or {}).get(
                        'scan_confirm_timeout_s', 300.0) or 300.0)
            except (TypeError, ValueError):
                timeout_s = 300.0
            from gitcurator.gui.scan_plan_dialog import ask_scan_plan
            verdict = ask_scan_plan(self, plan, timeout_s=timeout_s)
        except Exception as e:
            self.log_message(
                f"⚠️ The scan plan modal could not open ({e}) — the safe "
                f"defer applies (nothing moved, nothing deleted)",
                "warning")
            verdict = 'declined'
        try:
            worker.provide_scan_verdict(verdict)
        except Exception:
            pass    # a dead worker never crashes the GUI thread

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
