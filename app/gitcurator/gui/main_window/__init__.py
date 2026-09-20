#!/usr/bin/env python3
"""gitcurator.gui.main_window — the PyQt6 main window.

Public surface (unchanged since the v32.3 extraction from the
``gui/app.py`` monolith): :class:`MainWindow`, now assembled in
``window.py`` from single-concern mixins whose method bodies are
verbatim moves (md5-verified) — see ``window.py`` for the map.
"""

from gitcurator.gui.main_window.window import MainWindow

__all__ = ["MainWindow"]
