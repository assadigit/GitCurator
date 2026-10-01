"""cli_settings — the CLI's settings plumbing.

config.json load/save, secret masking, and the interactive model
picker — moved verbatim from gitcurator/cli.py at v0.25.0; cli.py
re-exports every name so existing imports keep working unchanged.
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

from gitcurator.cli_terminal import (  # noqa: F401 — shared paint box
    C, paint, _gui_symbol, cli_print, _PRINT_LOCK,
)


# ----------------------------------------------------------------------------
# Config helpers (same config.json the GUI uses — one source of truth)
# ----------------------------------------------------------------------------

def _config_path(cli_arg: str | None) -> str:
    if cli_arg:
        return os.path.abspath(cli_arg)
    from gitcurator.constants import APP_DIR
    return os.path.join(APP_DIR, "config.json")


def load_config(path: str) -> dict | None:
    if not os.path.isfile(path):
        return None
    try:
        import json
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        cli_print(f"Could not read config ({exc})", "error")
        return None


def save_config(path: str, config: dict) -> bool:
    from gitcurator.core.storage import write_config_file
    try:
        write_config_file(path, config)
        return True
    except Exception as exc:
        cli_print(f"Could not save config ({exc})", "error")
        return False


def _ask(prompt: str, default: str = "", password: bool = False) -> str:
    """Interactive prompt with visible default; Enter keeps the default."""
    suffix = paint(f" [{default}]", C.DIM) if default else ""
    try:
        val = input(paint("? ", C.CYAN) + prompt + suffix + paint(" → ", C.CYAN)).strip()
    except EOFError:
        val = ""
    return val if val else default


def _mask_token(token: str, keep_head: int = 4, keep_tail: int = 4) -> str:
    """Token-style mask: ghp_••••••••DVb2 (head + tail kept)."""
    if not token:
        return C.DIM + "(not set)" + C.RESET
    if len(token) <= keep_head + keep_tail + 2:
        return "•" * len(token)
    return token[:keep_head] + "•" * 8 + token[-keep_tail:]


def _clean_config_for_save(cfg: dict) -> dict:
    """Strip CLI-private keys (``__config_path__``…) before writing
    config.json — the old code leaked them into the file on every
    model-switch save (v0.07.2)."""
    return {k: v for k, v in cfg.items()
            if not (k.startswith("__") and k.endswith("__"))}


def smart_model_pick(configured: str, available: list) -> str:
    """Best stand-in for a missing model — delegates to the SAME heuristic
    the pipeline uses (ProcessingWorker._pick_best_model: drop embedders,
    same family, same size, biggest wins) so the menu's recommendation and
    the headless auto-pick always agree."""
    try:
        ProcessingWorker = _gui_symbol("ProcessingWorker")
        return ProcessingWorker._pick_best_model(configured, available)
    except Exception:
        # Import-time safety net (mirrors the classmethod's simple ranking)
        non_embed = [m for m in available if "embed" not in m.lower()] or list(available or [])
        return non_embed[0] if non_embed else ""


def prompt_model_menu(configured: str, available: list, *,
                      status=None, allow_decline: bool = True) -> str | None:
    """v0.07.2 — THE model picker the CLI was missing (owner report: "it
    didn't let me choose a new model ... Model wasn't found = failed cli").

    Shows a numbered menu of the models Ollama actually has, with the smart
    recommendation as the default. Returns the chosen model name, or None
    when the user declines ('q') / no console is attached (in which case the
    RECOMMENDED model is returned instead — non-interactive runs must not
    fail batch-wide over a missing model)."""
    if not available:
        return None
    rec = smart_model_pick(configured, available)
    ordered = ([rec] if rec in available else []) + [m for m in available if m != rec]

    if not hasattr(sys.stdin, "isatty") or not sys.stdin.isatty():
        return rec  # piped / scheduled run: auto-pick, caller logs it

    with _PRINT_LOCK:
        if status is not None and getattr(status, "_active", False):
            status._erase()
        print()
        cli_print(f"Configured model '{configured}' is not installed.", "warning")
        print(paint("  Models available in Ollama:", C.BOLD))
        for i, m in enumerate(ordered, 1):
            tag = ""
            if m == rec:
                tag = paint("   ← recommended", C.GREEN)
            try:
                _PW = _gui_symbol("ProcessingWorker")
                if _PW._is_embed_model(m):
                    tag = paint("   (embedding — cannot analyze)", C.DIM)
            except Exception:
                pass  # deps unavailable: the plain list is still useful
            print(f"    {paint(str(i) + ')', C.CYAN)} {m}{tag}")
        if allow_decline:
            hint = paint("  (q = abort the run; tip: `ollama pull <model>` installs the original)", C.DIM)
        else:
            hint = paint(f"  (tip: `ollama pull {configured}` installs the original)", C.DIM)
        print(hint)
        for _ in range(5):
            try:
                ans = input(paint("? ", C.CYAN)
                            + "Select a model"
                            + paint(f" [1]", C.DIM)
                            + paint(" → ", C.CYAN)).strip()
            except EOFError:
                return None
            if not ans:
                return rec
            if allow_decline and ans.lower() in ("q", "quit", "n"):
                return None
            if ans.isdigit() and 1 <= int(ans) <= len(ordered):
                return ordered[int(ans) - 1]
            hits = [m for m in ordered if ans.lower() in m.lower()]
            if len(hits) == 1:
                return hits[0]
            print(paint("  ✖ Not a number or unique name — try again (1–%d or q)." % len(ordered), C.RED))
        return None


def apply_model_choice(cfg: dict, model: str, config_path: str | None) -> None:
    """Persist a model switch to the in-memory config AND config.json
    (private keys filtered — one source of truth for GUI + CLI).
    v0.15.0 — provider-aware: 'llamacpp' writes llamacpp_model."""
    provider = cfg.get("llm_provider", "ollama")
    if provider == "cloud":
        cfg["cloud_model"] = model
    elif provider == "llamacpp":
        cfg["llamacpp_model"] = model
    else:
        oll = cfg.get("ollama")
        if not isinstance(oll, dict):
            oll = {}
            cfg["ollama"] = oll
        oll["model"] = model
    if config_path:
        save_config(config_path, _clean_config_for_save(cfg))
