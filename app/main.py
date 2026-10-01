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
    python main.py --cli …          (VISUAL CLI — colors + spinner + progress bar;
                                    --auto is the one-command fully-automatic run;
                                    see GitCurator-CLI.bat for the double-click
                                    launcher and GitCurator-CLI-Setup.bat for the
                                    first-run wizard)
    python main.py --headless …     (legacy plain headless — same flags as --cli)
    python -m unittest tests.test_core tests.test_e2e   (run from this directory)

The real main() is :func:`gitcurator.gui.app.main`.
"""
import os
import sys

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

# v0.07 — the visual CLI has its own module (gitcurator/cli.py) and is
# dispatched BEFORE the GUI import so `--cli --status` never pays for the
# PyQt6 widget stack. It shares the same config.json and pipeline.
# (--cli itself is stripped; every following flag is forwarded.)
if "--cli" in sys.argv:
    from gitcurator.cli import cli_main
    sys.exit(cli_main([a for a in sys.argv[1:] if a != "--cli"]))

from gitcurator.gui.app import main  # noqa: E402  (needs the sys.path above)

if __name__ == "__main__":
    main()
