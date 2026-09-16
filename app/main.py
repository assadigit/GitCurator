#!/usr/bin/env python3
"""
GitCurator — launcher shim (v32 modular layout).

The application now lives in the ``gitcurator`` package:

    gitcurator/core         links · storage · note_builder · llm_client
    gitcurator/integrations telegram workers · vaultseal · goodrepos · error_reporter
    gitcurator/cloud         cloudflare_* · gdrive_*
    gitcurator/gui           app.py (MainWindow + pipeline + headless CLI)
    gitcurator/tools         developer utilities
    gitcurator/constants.py  shared design tokens + config defaults

This shim keeps every historical entry point working unchanged:

    python main.py                  (GUI)
    python main.py --headless …     (headless CLI — see "headless mode command.txt")
    python -m unittest tests.test_core tests.test_e2e   (run from this directory)

The real main() is :func:`gitcurator.gui.app.main`.
"""
import os
import sys

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from gitcurator.gui.app import main  # noqa: E402  (needs the sys.path above)

if __name__ == "__main__":
    main()
