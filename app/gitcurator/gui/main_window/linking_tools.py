"""MainWindow LinkingToolsMixin — the recall-hooks / link-suggestions tool
launchers. (Was Phase6Mixin in gitcurator/gui/app.py, then
main_window/phase6.py; renamed at v0.25.0 to say what it does. Bodies
moved verbatim — see REFACTOR_PLAN.md at the repo root.)"""

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

_APP_DIR = APP_DIR

from gitcurator.gui.processing_worker import TestWorker

class LinkingToolsMixin:
    """LinkingToolsMixin — launch the Phase-6 linking tools safely from the GUI."""

    # ------------------------------------------------------------------
    # v0.16.0 — Phase 6 (Linking): the recall-hook and link-suggestion
    # tools, run in-process through TestWorker with stdout captured and
    # streamed to the GUI log on completion. The SAFE defaults only —
    # the bulk/apply/collect steps stay on the command line where the
    # SPEC's owner-approval flow lives.
    # ------------------------------------------------------------------
    def _run_phase6_tool(self, tool_main, argv, label):
        """Run one Phase-6 tool's main() in a TestWorker, stdout captured;
        the tail lands in the GUI log when it finishes."""
        if self.worker is not None and not self.worker.isFinished():
            self.log_message(
                "⏳ A batch is running — wait for it to finish before "
                "running the linking tools.", "warning")
            return
        import contextlib
        import io as _io

        def _job():
            buf = _io.StringIO()
            try:
                with contextlib.redirect_stdout(buf), \
                        contextlib.redirect_stderr(buf):
                    rc = tool_main(argv)
            except SystemExit as exc:            # argparse / provider aborts
                rc = int(exc.code or 0)
            except Exception as exc:              # noqa: BLE001
                buf.write(f"\n💥 {type(exc).__name__}: {exc}\n")
                rc = 2
            return {'success': rc == 0, 'rc': rc,
                    'output': buf.getvalue()}

        worker = TestWorker(_job, label)
        worker.log_message.connect(self.log_message)

        def _on_finished(_name, result):
            lines = (result.get('output') or '').splitlines()
            tail = lines[-40:]
            for line in tail:
                self.log_message(line, "info")
            if result.get('success'):
                self.log_message(f"✅ {label} finished.", "success")
            else:
                self.log_message(
                    f"❌ {label} finished with exit code "
                    f"{result.get('rc')} (see the lines above — the "
                    "command-line tools print the exact next step).",
                    "error")

        worker.finished_signal.connect(_on_finished)
        self._active_test_workers.append(worker)
        self.log_message(f"⏳ {label} running… (the GUI stays usable)",
                         "info")
        worker.start()

    def run_recall_hooks_dryrun(self):
        """🪝 Recall hooks (dry-run): the Phase-6 step-1 tool in its
        default dry-run mode — nothing is written, the diff lands in the
        log + app/reports/recall/. Approve samples and apply from the
        command line (the SPEC flow)."""
        from gitcurator.tools import add_recall_hooks
        self._run_phase6_tool(add_recall_hooks.main, [],
                              "Recall hooks (dry-run)")

    def run_link_suggestions(self):
        """🔗 Build link suggestions: embeddings → neighbors → LLM
        confirm → the Suggestions note under the manual vault's Library/
        (its only write). Tick boxes there, then run the tool with
        --collect on the command line to apply."""
        from gitcurator.tools import build_links
        self._run_phase6_tool(build_links.main, [],
                              "Link suggestions")

