"""MainWindow LifecycleMixin — methods moved verbatim from the MainWindow in gitcurator/gui/app.py (branch refactor/gui-app-split; see REFACTOR_PLAN.md at the repo root)."""

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

class LifecycleMixin:
    """LifecycleMixin"""

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
        # v0.23.0 — no hardcoded text color: the themed QWidget rule colors
        # it (the old #423A52 was unreadable on the dark plum dialog).
        intro.setStyleSheet("font-size: 13px; padding: 8px;")
        intro.setObjectName("info_header")
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

        # v0.23.0 — theme-aware design-system buttons (the hardcoded light
        # stylesheet painted a light-bordered button on the dark theme), and
        # _style_btn tracks them so a theme flip restyles them too.
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dialog.reject)
        self._style_btn(cancel_btn, 'secondary')
        generate_btn = QPushButton("✓ Generate about_me.md")
        self._style_btn(generate_btn, 'primary')
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

        self._animate_dialog(dialog)
        dialog.exec()

    def closeEvent(self, event):
        # If processing is active, ask for confirmation BEFORE anything else
        # (the question helper must still be allowed to show its modal, so
        # the _closing flag is only set after the user confirms).
        if self.worker and self.worker.is_running:
            processed = self.worker.processed
            total = self.worker.total
            msg = (
                f"Processing in progress: {processed}/{total} repos extracted.\n\n"
                f"Do you really want to terminate?"
            )
            reply = QMessageBox.StandardButton.Yes if self._show_custom_question("⚠️ Terminate Processing?", msg) else QMessageBox.StandardButton.No
            if reply == QMessageBox.StandardButton.No:
                event.ignore()
                return

        # v0.06 — Fix (zombie process): the user confirmed the close (or
        # nothing was running) — shutdown begins. Mark closing so every
        # timer-driven callback (startup auto-check, proxy monitor) and
        # modal helper knows never to open a dialog from here on, and stop
        # the recurring timers so nothing fires after this point.
        self._closing = True
        for timer_name in ('_proxy_timer', '_tg_watchdog_timer'):
            timer = getattr(self, timer_name, None)
            if timer is not None:
                try:
                    timer.stop()
                except Exception:
                    pass

        self.save_config()
        if self.worker:
            # v30 — Fix (graceful close, W10): before waiting, UNBLOCK every
            # wait the worker could be parked on. Previously, if the worker
            # was sitting in _llm_retry_event.wait(600) (LLM failure dialog
            # pending) or the disk-full spin loop, worker.wait(5000) expired
            # and the app quit with the thread still running — random crashes
            # / half-written state on exit.
            self._unblock_worker_for_shutdown(self.worker)
            self.worker.stop()
            self.worker.wait(5000)  # wait up to 5 seconds for graceful shutdown
        # Wait for any in-flight test workers too.
        for w in list(self._active_test_workers):
            try:
                self._unblock_worker_for_shutdown(w)
                w.wait(2000)
            except Exception:
                pass
        # v0.06 — Fix (orphaned telethon children): kill every live
        # telegram worker subprocess. An orphan kept the session.session
        # SQLite file locked, which made the NEXT launch's auto bot-check
        # stall — and with the old unbounded stderr reader that stall was
        # permanent, reproducing the forever-bug on every restart.
        try:
            killed = _kill_all_telegram_workers()
            if killed:
                print(f"[closeEvent] killed {killed} telegram worker subprocess(es)",
                      file=sys.stderr, flush=True)
        except Exception:
            pass
        # Release the lock so a crashed shutdown never leaves bookkeeping
        # in a busy state (harmless at exit, correct if the app is reused).
        try:
            self._tg_lock.force_release("shutdown")
        except Exception:
            pass
        event.accept()
        # v0.06 — Fix (zombie process): force the application loop to exit.
        # Hidden parented dialogs (SettingsDialog and friends) survive the
        # main window's close; with them technically "open" Qt did NOT emit
        # lastWindowClosed, app.exec() never returned, the finally-block in
        # main() never deleted app.lock, and the process lingered as a
        # zombie — which then made every subsequent launch print "Another
        # instance is already running" and exit. An explicit quit()
        # guarantees exec() returns, the lock file is removed, and the
        # process actually dies.
        try:
            QApplication.instance().quit()
        except Exception:
            pass

    @staticmethod
    def _unblock_worker_for_shutdown(worker) -> None:
        """v30 — release every blocking wait a worker may be parked on so a
        pending dialog/timeout can't hold the thread past app exit:
          - resolve_llm_failure('stop')  -> unblocks _llm_retry_event
          - provide_code('')             -> unblocks _code_event
          - _disk_full_paused = False    -> exits the disk-full spin loop
        All calls are hasattr-guarded so it also works for TestWorker."""
        if worker is None:
            return
        try:
            if hasattr(worker, 'resolve_llm_failure'):
                worker.resolve_llm_failure("stop")
            if hasattr(worker, 'provide_code'):
                worker.provide_code("")
            if hasattr(worker, '_disk_full_paused'):
                worker._disk_full_paused = False
        except Exception:
            pass

