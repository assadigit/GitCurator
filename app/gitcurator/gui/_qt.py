#!/usr/bin/env python3
"""gitcurator.gui._qt — the single site of the guarded PyQt6 import.

Extracted from ``gitcurator/gui/app.py`` (v32.3 modularization): every
GUI-side module (the facade, workers, main_window, cli) star-imports Qt
names from here, so a PyQt6 problem prints ONE message and exits with
code 1 at exactly ONE place — the behavior the monolith had.

v32.4 diagnostic fix: the old blanket message claimed "PyQt6 is not
installed" even when PyQt6 WAS installed but its native Qt libraries
failed to load (e.g. a missing libEGL.so.1 / libGL on a headless box —
exactly what a GitCurator dev hit running the offscreen test suite).
The exit code and the not-installed message are unchanged; the
installed-but-broken branch now reports the real ImportError and points
at the system-library cause instead of sending the user to reinstall a
package they already have.
"""

import importlib.util
import sys

try:
    from PyQt6.QtWidgets import *
    from PyQt6.QtCore import *
    from PyQt6.QtGui import *
except ImportError as exc:
    if importlib.util.find_spec("PyQt6") is None:
        print("PyQt6 is not installed. Please run: pip install PyQt6")
    else:
        print(f"PyQt6 is installed but could not be imported: {exc}")
        print(
            "A native Qt library failed to load — install the Qt system "
            "runtime for your platform (on Debian/Ubuntu: "
            "libegl1 libgl1 libxkbcommon0 libglib2.0-0; on Windows check "
            "the PyQt6 wheels match your Python build)."
        )
    sys.exit(1)
