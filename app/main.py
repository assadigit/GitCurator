#!/usr/bin/env python3
"""
GitCurator — launcher shim (v32.3 modular layout).

The application lives in the ``gitcurator`` package:

    gitcurator/cli.py               run_headless, _is_process_running, main
    gitcurator/gui/                 main_window (MainWindow) · workers · _qt · log_handler
    gitcurator/gui/app.py           back-compat facade re-exporting everything
    gitcurator/core/                links · storage · note_builder · llm_client
                                    vault · cache_db · link_tracker · inbox
    gitcurator/integrations/        telegram_jobs · vaultseal · goodrepos · …
    gitcurator/cloud/               cloudflare_* · gdrive_* (dormant)
    gitcurator/tools/               developer utilities
    gitcurator/constants.py         shared design tokens + config defaults +
                                    APP_DIR path anchoring (resolve_app_path)

This shim keeps every historical entry point working unchanged:

    python main.py                  (GUI)
    python main.py --headless …     (headless CLI — see "headless mode command.txt")
    python main.py --help           (headless CLI options — v32.3: no GUI launch)
    python -m unittest tests.test_core tests.test_e2e   (run from this directory)

v32.3: main.py imports :func:`gitcurator.cli.main` DIRECTLY (not via the
gui.app facade) so ``--help`` is answered by argparse before any
PyQt6/PyGithub/ollama import. The facade still re-exports ``main`` for
backward compatibility (``gitcurator.gui.app.main`` keeps working).
"""
import os
import sys

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from gitcurator.cli import main  # noqa: E402  (needs the sys.path above)

if __name__ == "__main__":
    main()
