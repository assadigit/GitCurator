#!/usr/bin/env python3
"""gitcurator.gui._qt — the single site of the guarded PyQt6 import.

Extracted from ``gitcurator/gui/app.py`` (v32.3 modularization): every
GUI-side module (the facade, workers, main_window, cli) star-imports Qt
names from here, so a missing PyQt6 prints the same message and exits
with code 1 at exactly ONE place — the behavior the monolith had.
"""

import sys

try:
    from PyQt6.QtWidgets import *
    from PyQt6.QtCore import *
    from PyQt6.QtGui import *
except ImportError:
    print("PyQt6 is not installed. Please run: pip install PyQt6")
    sys.exit(1)
