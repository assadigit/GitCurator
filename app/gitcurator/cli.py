#!/usr/bin/env python3
"""gitcurator.cli — the command-line entry points (GUI launcher + headless).

Extracted verbatim from ``gitcurator/gui/app.py`` (v32.3 modularization):

* :func:`run_headless`  — the headless pipeline CLI (``--import-file`` /
  Telegram range / offset / single-id modes; VaultSeal + GoodRepos
  post-run, non-blocking worker defaults).
* :func:`_is_process_running` — Windows PID-recycling-aware liveness
  check (ctypes, python-exe verification) with a Unix fallback.
* :func:`main`          — argv dispatch (``--headless`` vs GUI), the
  single-instance ``app.lock`` dance (stale-lock recovery) and the
  QApplication bootstrap (high-DPI rounding policy, Fusion style).

v32.3 CLI hardening: ``--config`` / ``--import-file`` / ``app.lock`` /
``cache.db`` / ``system_prompt.txt`` / ``session.session`` / ``logs/`` are
all APP_DIR-anchored now (cwd-first for user-supplied relative paths), so
the CLI works from any launch directory. Headless runs exit with their
real status code. ``--help`` / ``-h`` route to the headless argument
parser BEFORE any PyQt6/PyGithub/ollama import (the README always
promised "python main.py --help :: headless mode options" — the old
code launched the GUI instead). Redirected stdout/stderr are
reconfigured to UTF-8 so the emoji-rich log lines cannot raise
UnicodeEncodeError on Windows cp1252 pipes.
"""

import argparse
import json
import os
import sys
from datetime import datetime

from gitcurator.constants import APP_DIR, CONFIG_FILE, resolve_app_path

__all__ = ["main", "run_headless", "build_arg_parser"]


def _force_utf8_stdio() -> None:
    """Windows CLI hardening: redirected stdout/stderr default to the ANSI
    code page (e.g. cp1252), where the emoji-rich log lines (📬 ✅ ❌ …)
    raise UnicodeEncodeError. Reconfigure both streams to UTF-8 with
    ``errors='replace'`` — a no-op on UTF-8 terminals and on the Windows
    console (already UTF-8 via PEP 528); only piped/redirected output
    changes, from crash to readable."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass  # exotic streams (captured, closed) — leave untouched


def build_arg_parser() -> argparse.ArgumentParser:
    """The headless-mode argument parser.

    v32.3: hoisted to module level (it used to live inside run_headless)
    so ``--help`` can be answered BEFORE PyQt6 / PyGithub / ollama are
    imported — ``python main.py --help`` works on a bare Python install.
    """
    parser = argparse.ArgumentParser(description="GitHub Project Curator (headless mode)")
    parser.add_argument('--headless', action='store_true', help='Run without GUI')
    parser.add_argument('--from-id', type=int, help='Start message ID (Telegram range mode)')
    parser.add_argument('--to-id', type=int, help='End message ID (Telegram range mode)')
    parser.add_argument('--offset-start', type=int, help='Offset start ID (Telegram offset mode)')
    parser.add_argument('--count', type=int, help='Number of messages (offset mode)')
    parser.add_argument('--import-file', type=str, help='Path to .txt file with URLs')
    parser.add_argument('--vault', type=str, required=True, help='Obsidian vault path')
    parser.add_argument('--config', type=str, default=CONFIG_FILE, help='Config file path (default: the app\'s own config.json, resolved against APP_DIR)')
    parser.add_argument('--single-id', type=int, help='Single Telegram message ID')
    return parser


# ============================================================================
# Main Entry Point
# ============================================================================

def run_headless(args):
    """Run the app in headless mode (no GUI) for CLI/scripting use.

    Usage:
        python main.py --headless --from-id 123 --to-id 456 --vault "/path/to/vault" --config "config.json"
        python main.py --headless --import-file "urls.txt" --vault "/path/to/vault" --config "config.json"
        python main.py --headless --single-id 12345 --vault "/path/to/vault"
    """
    _force_utf8_stdio()

    # v32.3: parse FIRST — --help / usage errors exit here, before any
    # PyQt6 / PyGithub / ollama import (works on a bare Python install).
    parsed = build_arg_parser().parse_args(args)

    # Heavy imports AFTER argparse. Importing the gui.app facade preserves
    # the historical '[main] LOADED version …' stderr stamp and provides
    # the telethon-guarded fetch_github_urls_sync (the facade runs its own
    # import guards; the modules below are already loaded by then).
    import gitcurator.gui.app as _gui_app  # noqa: F401 — version stamp + deps
    from gitcurator.gui._qt import QCoreApplication
    from gitcurator.gui.workers import ProcessingWorker
    from gitcurator.core import storage as _storage
    from gitcurator.integrations import vaultseal as _vaultseal
    from gitcurator.integrations import goodrepos as _goodrepos
    fetch_github_urls_sync = _gui_app.fetch_github_urls_sync

    # Load config
    # v32.3 fix (root cause): the default was the CWD-relative 'config.json'.
    # The v32 modularization anchored CONFIG_FILE to APP_DIR for the GUI
    # but never updated this CLI default — running `python app/main.py
    # --headless ...` from any directory other than app/ died with
    # "Config file not found: config.json". The default is now the
    # APP_DIR-anchored CONFIG_FILE; a user-supplied RELATIVE path still
    # resolves cwd-first (as typed), then falls back to APP_DIR.
    config_path = resolve_app_path(parsed.config)
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            config = json.load(f)
    else:
        print(f"Config file not found: {config_path}")
        return 1

    # Override vault path
    config['vault_path'] = parsed.vault

    print(f"[headless] Config loaded from {config_path}")
    print(f"[headless] Vault: {parsed.vault}")

    # Determine mode
    if parsed.import_file:
        mode = 'import'
        # v32.3 fix: same cwd-first / APP_DIR-fallback resolution as
        # --config, so `--import-file urls.txt` works from any cwd.
        import_file = resolve_app_path(parsed.import_file)
        range_from = range_to = offset_start = offset_count = None
        urls = None
        print(f"[headless] Mode: import from file: {import_file}")
    elif parsed.single_id:
        print(f"[headless] Mode: single message ID {parsed.single_id}")
        proxy = config.get('proxy', {})
        api_id = config.get('telegram_api_id', 0)
        api_hash = config.get('telegram_api_hash', '')
        phone = config.get('telegram_phone', '')
        result = fetch_github_urls_sync(
            api_id=api_id, api_hash=api_hash, phone=phone,
            proxy=proxy, from_id=parsed.single_id, to_id=parsed.single_id,
            preview_only=False
        )
        if not result.get('success'):
            print(f"[headless] Failed to fetch: {result.get('error')}")
            return 1
        urls = result.get('urls', [])
        mode = 'direct'
        import_file = None
        range_from = range_to = offset_start = offset_count = None
        print(f"[headless] Fetched {len(urls)} URLs from message {parsed.single_id}")
    elif parsed.from_id is not None and parsed.to_id is not None:
        mode = 'telegram_ids'
        range_from = parsed.from_id
        range_to = parsed.to_id
        offset_start = offset_count = None
        import_file = None
        urls = None
        print(f"[headless] Mode: Telegram range {range_from} to {range_to}")
    elif parsed.offset_start is not None and parsed.count is not None:
        mode = 'telegram_offset'
        offset_start = parsed.offset_start
        offset_count = parsed.count
        range_from = range_to = None
        import_file = None
        urls = None
        print(f"[headless] Mode: Telegram offset {offset_start} count {offset_count}")
    else:
        print("[headless] Error: must specify --from-id + --to-id, --offset-start + --count, --import-file, or --single-id")
        return 1

    # Create a QCoreApplication so QThread works without a GUI
    app = QCoreApplication(sys.argv)

    worker = ProcessingWorker(
        config=config,
        mode=mode,
        range_from=range_from,
        range_to=range_to,
        offset_start=offset_start,
        offset_count=offset_count,
        import_file=import_file,
        urls=urls,
        headless=True,  # v30 — Fix (headless hang-bombs): non-blocking defaults
    )

    def _log(msg, level):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] [{level}] {msg}")

    def _on_finished(success, message):
        # v31 — VaultSeal: post-run vault backup (best-effort, never raises,
        # runs before the exit print so it can never be cut off). Runs for
        # failed batches too — notes written before a mid-run failure are
        # exactly what we want backed up.
        # v32.3 fix: vs_summary is built BEFORE the try — if seal_from_config
        # ever raises, the GoodRepos call below used to die with
        # NameError: name 'vs_summary' is not defined instead of the real
        # error.
        vs_summary = {
            "processed": int(getattr(worker, 'processed', 0) or 0),
            "total": int(getattr(worker, 'total', 0) or 0),
        }
        try:
            vs_result = _vaultseal.seal_from_config(config, run_summary=vs_summary)
            print(f"[headless] VaultSeal: {vs_result.describe()}")
        except Exception as seal_err:
            print(f"[headless] VaultSeal error: {seal_err}")
        # v32 — GoodRepos: publish the PUBLIC curated directory. Best-effort,
        # never raises — whatever notes exist deserve publication.
        try:
            gr_result = _goodrepos.publish_from_config(config, run_summary=vs_summary)
            print(f"[headless] Good Repos: {gr_result.describe()}")
        except Exception as good_err:
            print(f"[headless] Good Repos error: {good_err}")
        if success:
            print(f"[headless] DONE: {message}")
        else:
            print(f"[headless] ERROR: {message}")
        # v32.3 fix: app.quit() made exec() — and therefore the process —
        # exit 0 even when the batch FAILED. Schedulers / CI could not
        # detect a failed headless run. app.exit(code) propagates the real
        # status through exec()'s return value.
        app.exit(0 if success else 1)

    # v30 — Fix (headless hang-bombs): belt-and-suspenders receivers for the
    # three signals that previously had NO receiver in headless mode. The
    # worker short-circuits before emitting them (self._headless checks), but
    # these guarantee no emission is ever silently dropped.
    worker.code_requested.connect(
        lambda pt: print(f"[headless] Telegram {pt} requested — no GUI available; the worker fails fast.")
    )
    worker.disk_full_signal.connect(
        lambda p: print(f"[headless] Disk full at {p} — repo skipped and recorded in retry queue.")
    )
    worker.llm_failed_signal.connect(
        lambda repo: print(f"[headless] LLM failed for {repo} — fallback note written, batch continues.")
    )

    def _on_model_changed_headless(provider, model):
        # v30 — persist model auto-switches in headless mode too: the worker
        # already mutated the shared `config` dict in place; write it back.
        print(f"[headless] LLM model switched to '{model}' ({provider}) — saving config.")
        try:
            _storage.write_config_file(config_path, config)
        except Exception as e:
            print(f"[headless] Could not save config: {e}")

    worker.model_changed.connect(_on_model_changed_headless)

    worker.log_message.connect(_log)
    worker.finished_signal.connect(_on_finished)
    worker.start()

    return app.exec()


def _is_process_running(pid):
    """Check if a process with the given PID is running AND is a Python process.
    Windows recycles PIDs aggressively, so we verify the process name too."""
    try:
        if sys.platform == 'win32':
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.windll.kernel32

            # Open the process with QUERY_INFORMATION + SYNCHRONIZE
            PROCESS_QUERY_INFORMATION = 0x0400
            SYNCHRONIZE = 0x00100000
            handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | SYNCHRONIZE, False, pid)
            if not handle:
                return False  # Can't open = process doesn't exist or no access

            try:
                # Get the executable name
                max_path = 260
                buf = ctypes.create_unicode_buffer(max_path)
                # Try QueryFullProcessImageName (more reliable)
                if hasattr(kernel32, 'QueryFullProcessImageNameW'):
                    kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(ctypes.c_uint(max_path)))
                    exe_path = buf.value.lower()
                    # Only treat as "our" process if it's a Python executable
                    if 'python' in exe_path or 'pythonw' in exe_path:
                        return True
                    return False  # PID exists but it's not Python — recycled PID
                return True  # Can't check name — assume running
            finally:
                kernel32.CloseHandle(handle)
        else:
            # Unix: os.kill(pid, 0) works reliably
            os.kill(pid, 0)
            return True
    except (OSError, ProcessLookupError, ValueError, Exception):
        return False

def main():
    _force_utf8_stdio()
    argv = sys.argv[1:]

    # v32.3 fix: route --headless AND --help/-h to the headless parser
    # BEFORE any Qt import. The README always promised "python main.py
    # --help :: headless mode options" — the old code only checked for
    # '--headless', so --help launched the GUI instead (and on a
    # display-less machine died with a Qt platform-plugin error).
    if argv and ('--headless' in argv or '--help' in argv or '-h' in argv):
        sys.exit(run_headless(argv))

    # GUI path — the heavy imports happen here, after the arg routing.
    # The facade import preserves the historical '[main] LOADED version …'
    # stderr stamp and pulls in MainWindow + every dependency.
    import gitcurator.gui.app  # noqa: F401 — version stamp + heavy deps
    from gitcurator.gui._qt import QApplication, Qt
    from gitcurator.gui.main_window import MainWindow

    # Single instance check — detects stale locks from crashed sessions.
    # v32.3 fix: anchor app.lock to APP_DIR (was CWD-relative — two
    # instances launched from different directories never saw each
    # other's lock, and stray app.lock files scattered wherever the
    # process happened to start).
    lock_file = os.path.join(APP_DIR, "app.lock")
    lock_fd = None
    try:
        lock_fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(lock_fd, str(os.getpid()).encode())
    except FileExistsError:
        # Lock file exists — check if the PID is still running
        old_pid = None
        try:
            with open(lock_file, 'r') as f:
                old_pid = int(f.read().strip())
        except (ValueError, IOError):
            pass

        if old_pid and _is_process_running(old_pid):
            print(f"Another instance is already running (PID {old_pid}). Exiting.")
            sys.exit(1)

        # Process is dead — stale lock, force remove and retry
        print(f"Removing stale lock file (PID {old_pid or 'unknown'} is no longer running)...")
        try:
            os.remove(lock_file)
        except PermissionError:
            # Windows: file may be held by a zombie handle — retry with small delay
            import time
            time.sleep(0.5)
            try:
                os.remove(lock_file)
            except PermissionError:
                # Last resort: overwrite without O_EXCL
                pass
        try:
            lock_fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(lock_fd, str(os.getpid()).encode())
        except FileExistsError:
            # O_EXCL still failing — just overwrite the file content
            try:
                if lock_fd:
                    os.close(lock_fd)
            except:
                pass
            with open(lock_file, 'w') as f:
                f.write(str(os.getpid()))
            lock_fd = None  # We don't have an exclusive lock, but that's OK
    except OSError as e:
        print(f"Cannot create lock file ({lock_file}): {e}")
        # Don't exit — just continue without lock
        lock_fd = None

    try:
        # Enable high-DPI scaling for sharp rendering on 4K displays
        try:
            QApplication.setHighDpiScaleFactorRoundingPolicy(
                Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
            )
        except AttributeError:
            pass  # Older PyQt6 versions
        app = QApplication(sys.argv)
        app.setStyle('Fusion')
        window = MainWindow()
        window.show()
        exit_code = app.exec()
    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        exit_code = 1
    finally:
        if lock_fd is not None:
            try:
                os.close(lock_fd)
            except OSError:
                pass
        try:
            os.remove(lock_file)
        except OSError:
            pass
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
