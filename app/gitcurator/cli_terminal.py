"""cli_terminal — the CLI's paint box.

The ANSI color system, level styles, banners, rules, progress bars,
the braille spinner and the StatusLine — moved verbatim from
gitcurator/cli.py at v0.25.0; cli.py re-exports every name so existing
imports (cli.C, cli.banner, …) keep working unchanged.
"""
from __future__ import annotations

import os
import re
import socket
import sys
import shutil
import signal
import threading
import time
from collections import deque
from datetime import datetime

# Keep the app/ root on sys.path so `import gitcurator` resolves when this
# file is launched directly (mirrors the main.py shim). cli.py lives at
# app/gitcurator/cli.py, so TWO dirnames give app/. (v0.07.2: the old code
# used three — the repo root — so `python gitcurator/cli.py --status`
# crashed with ModuleNotFoundError unless launched through main.py.)
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)

# ----------------------------------------------------------------------------
# ANSI color system (respects NO_COLOR, non-TTY output and --no-color)
# ----------------------------------------------------------------------------


class C:
    """ANSI SGR codes. Empty strings when colors are disabled."""
    RESET = BOLD = DIM = ITALIC = ""
    RED = GREEN = YELLOW = CYAN = MAGENTA = BLUE = WHITE = ""
    BG_RED = BG_GREEN = ""
    _ENABLED = False

    @classmethod
    def enable(cls, force: bool = False):
        """Turn on colors: honor NO_COLOR, TTY detection, colorama (Windows)."""
        if force:
            cls._ENABLED = True
        elif os.environ.get("NO_COLOR"):
            cls._ENABLED = False
        elif not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
            cls._ENABLED = False  # redirected/pipe output stays clean
        else:
            cls._ENABLED = True
        if cls._ENABLED:
            # Windows 10+ consoles need VT processing enabled. colorama (in
            # requirements.txt) does it cleanly when present; the empty-string
            # os.system trick is the documented fallback.
            try:
                import colorama  # type: ignore
                colorama.just_fix_windows_console()
            except Exception:
                if os.name == "nt":
                    os.system("")
            cls.RESET = "\033[0m"
            cls.BOLD = "\033[1m"
            cls.DIM = "\033[2m"
            cls.ITALIC = "\033[3m"
            cls.RED = "\033[31m"
            cls.GREEN = "\033[32m"
            cls.YELLOW = "\033[33m"
            cls.CYAN = "\033[36m"
            cls.MAGENTA = "\033[35m"
            cls.BLUE = "\033[34m"
            cls.WHITE = "\033[37m"
            cls.BG_RED = "\033[41m"
            cls.BG_GREEN = "\033[42m"


def paint(text: str, *codes: str) -> str:
    """Wrap text in the given ANSI codes (no-op when disabled)."""
    if not any(codes) or not C._ENABLED:
        return text
    return "".join(codes) + text + C.RESET


# ----------------------------------------------------------------------------
# v0.09.1 — CLI reliability fixes (owner report: "the CLI is not running")
# ----------------------------------------------------------------------------

def _harden_stdio() -> None:
    """Make every CLI print safe on Windows legacy consoles.

    ROOT CAUSE of the reported failure: a default Windows console runs a
    legacy codepage (cp437/cp850/cp1252 — anything but 65001).  The CLI's
    very first output — the banner's block-drawing glyphs — then raised

        UnicodeEncodeError: 'charmap' codec can't encode characters

    and killed the process before ANY command could run, so `main.py --cli
    …` (and the double-click .bat launchers) appeared simply "not to run".

    Fix: reconfigure stdout/stderr to UTF-8 with ``errors="replace"`` (a
    no-op on already-UTF-8 streams, e.g. Windows Terminal / modern Linux
    / piped output) and switch the Windows console codepage to 65001 so
    the glyphs RENDER, not just encode.  Runs at import time — before
    argparse prints --help — so every output path is covered.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass  # exotic stream objects without reconfigure(): keep as-is
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleOutputCP(65001)
            kernel32.SetConsoleCP(65001)
        except Exception:
            pass  # best effort; errors="replace" already prevents crashes


_harden_stdio()  # at import: before argparse's --help can print anything


def _gui_symbol(name: str):
    """Import ONE symbol from ``gitcurator.gui.app`` without letting the
    module's dependency guards kill the CLI process.

    ``gitcurator/gui/app.py`` guards its third-party imports with
    ``sys.exit(1)`` when PyQt6 / PyGithub / ollama / Telethon are missing.
    ``sys.exit()`` raises SystemExit — a BaseException, invisible to plain
    ``except Exception`` — so the CLI's lazy imports used to TERMINATE the
    whole process mid-command.  This helper converts the guard's exit into
    a clean ImportError with an actionable message, so commands can report
    "install requirements.txt" and exit with a proper CLI error code.
    """
    try:
        import gitcurator.gui.app as _gui
    except SystemExit as exc:  # the dependency guards in gui/app.py
        raise ImportError(
            "the pipeline engine (gitcurator.gui.app) needs the GUI "
            "requirements — run:  pip install -r requirements.txt"
        ) from exc
    except ImportError as exc:  # unguarded Qt imports (e.g. gui/icons.py)
        raise ImportError(
            f"{exc} — the pipeline engine needs the GUI requirements; "
            "run:  pip install -r requirements.txt"
        ) from exc
    return getattr(_gui, name)


_LEVEL_STYLE = {
    "info":    (C.CYAN,  "•"),
    "success": (C.GREEN, "✔"),
    "warning": (C.YELLOW, "▲"),
    "error":   (C.RED,   "✖"),
    "dim":     (C.DIM,   "·"),
}


def style_level(level: str):
    return _LEVEL_STYLE.get(level, _LEVEL_STYLE["info"])


# ----------------------------------------------------------------------------
# Terminal helpers
# ----------------------------------------------------------------------------

_PRINT_LOCK = threading.RLock()


def term_width() -> int:
    try:
        return max(48, shutil.get_terminal_size((80, 24)).columns)
    except Exception:
        return 80


def mask(secret: str, keep: int = 4) -> str:
    """Mask a credential for display: keeps the first `keep` chars only."""
    if not secret:
        return C.DIM + "(not set)" + C.RESET
    if len(secret) <= keep:
        return "•" * len(secret)
    return secret[:keep] + "•" * min(24, len(secret) - keep)


def rule(char: str = "─") -> str:
    return C.DIM + char * min(term_width(), 64) + C.RESET


def cli_print(text: str = "", level: str = "info", end: str = "\n"):
    """Level-colored, timestamped line printer (thread-safe)."""
    color, icon = style_level(level)
    ts = C.DIM + datetime.now().strftime("%H:%M:%S") + C.RESET if C._ENABLED else datetime.now().strftime("%H:%M:%S")
    with _PRINT_LOCK:
        print(f"{ts} {paint(icon, color)} {paint(text, color if level != 'info' else '')}", end=end, flush=True)


def banner() -> None:
    """The GitCurator CLI welcome banner (v0.09)."""
    art = r"""
   ▄███████▄  ▄████████    ▄████████    ▄████████  ███▄▄▄▄      ▄████████
  ███    ███ ███    ███   ███    ███   ███    ███ ███▀▀▀██▄   ███    ███
  ███    ███ ███    ███   ███    ███   ███    █▀▀ ███    ███  ███    ███
 ▄███▄▄▄▄███▄███▄▄▄▄▄███▄ ███    ███  ▄███▄▄▄     ███    ███ ▄███▄▄▄▄███▄
▀▀▀▀▀▀▀▀▀▀▀ ███▀▀▀▀▀▀▀▀▀  ███    ███ ▀▀▀▀▀▀▀    ▀▀     ███▀ ▀▀▀▀▀   ███▀
▀███████████ ███    ███   ███    ███ ▀███████████ ▄██    ███ ▄██████████▄
  ███    ███ ███    ███   ███    ███          ███ ███    ███ ███    █████
  ███    ███ ███    ███ ████    ████ ▄███████████  ▀█████▀  ███    █████
"""
    print(paint(art, C.CYAN, C.BOLD), end="")
    print(paint("  GitHub Project Curator — CLI ", C.BOLD) + paint("v0.09.5", C.MAGENTA)
          + paint("  ·  colored · animated · fully automatic", C.DIM))
    print()


# ----------------------------------------------------------------------------
# Live status line: spinner + progress bar (single owner of the bottom line)
# ----------------------------------------------------------------------------

SPIN_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def render_bar(current: int, total: int, width: int = 20) -> str:
    """Determinate block progress bar: ████████░░░░░░░░ 12/40 (30%)."""
    total = max(1, total or 1)
    frac = max(0.0, min(1.0, current / total))
    filled = int(round(width * frac))
    bar_txt = "█" * filled + "░" * (width - filled)
    pct = int(frac * 100)
    color = C.GREEN if frac >= 1.0 else C.CYAN
    return paint(bar_txt, color) + paint(f" {current}/{total} ({pct}%)", C.BOLD)


class StatusLine:
    """Owns the terminal's bottom line while a phase runs.

    ``update()`` repaints the spinner/progress line with \\r; ``log()``
    clears it, prints a full log line, then repaints underneath. Driven by
    a QTimer tick inside the Qt loop (batch phase) or a plain sleep loop
    (fetch phase) — both call :meth:`tick`."""

    def __init__(self):
        self._frame = 0
        self._active = False
        self._label = ""
        self._current = 0
        self._total = 0
        self._repo = ""

    # -- lifecycle ----------------------------------------------------------
    def start(self, label: str, total: int = 0):
        self._label = label
        self._total = total
        self._current = 0
        self._repo = ""
        self._active = True
        self.tick()

    def stop(self, final: str = ""):
        if self._active:
            self._erase()
        self._active = False
        if final:
            print(final, flush=True)

    # -- state --------------------------------------------------------------
    def set_progress(self, current: int, total: int):
        self._current, self._total = current, total

    def set_repo(self, repo: str):
        self._repo = repo

    # -- rendering ----------------------------------------------------------
    def _erase(self):
        sys.stdout.write("\r" + " " * (term_width() - 1) + "\r")

    def tick(self):
        if not self._active:
            return
        frame = SPIN_FRAMES[self._frame % len(SPIN_FRAMES)]
        self._frame += 1
        spin = paint(frame, C.MAGENTA, C.BOLD)
        line = f"{spin} {self._label}"
        if self._total:
            line += "  " + render_bar(self._current, self._total)
        if self._repo:
            line += "  " + paint(self._repo, C.DIM)
        line = line[: term_width() - 1]
        with _PRINT_LOCK:
            sys.stdout.write("\r" + line.ljust(term_width() - 1))
            sys.stdout.flush()

    def log(self, text: str, level: str = "info"):
        """Print a log line ABOVE the live status line."""
        with _PRINT_LOCK:
            if self._active:
                self._erase()
        cli_print(text, level)
        if self._active:
            self.tick()


# ----------------------------------------------------------------------------
# v0.07.2 — Pre-flight checks + run-configuration card
#
# Owner report that birthed this section: the CLI detected
# "(configured '…' not pulled)" at startup yet showed a GREEN ✓ and ran the
# batch anyway — every repo then failed its LLM call and degraded to
# fallback notes ("Model wasn't found = failed cli"). Checks now gate the
# run BEFORE any fetching happens, and a missing model opens the
# interactive picker (prompt_model_menu) with the choice persisted to
# config.json.
# ----------------------------------------------------------------------------

def _strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s or "")
