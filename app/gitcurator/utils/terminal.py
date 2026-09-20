#!/usr/bin/env python3
"""gitcurator.utils.terminal — ANSI color support (guarded colorama import).

Moved verbatim from ``gitcurator/gui/app.py`` (v32.3 modularization).
Exposes ``Fore`` / ``Style``; when colorama is missing a no-op fallback
keeps the app importable, and colorama is initialized with ``autoreset``
— exactly the import-time behavior the monolith had.
"""

__all__ = ["colorama", "Fore", "Style"]

try:
    import colorama
    from colorama import Fore, Style
    colorama.init(autoreset=True)
except ImportError:
    # Fallback if colorama missing
    class Fore:
        GREEN = ''; YELLOW = ''; RED = ''; CYAN = ''; WHITE = ''; RESET = ''
    Style = Fore
    print("colorama not installed; colored output disabled.")
