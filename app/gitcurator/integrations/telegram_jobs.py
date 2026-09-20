#!/usr/bin/env python3
"""gitcurator.integrations.telegram_jobs — subprocess-based Telegram jobs.

Moved verbatim from ``gitcurator/gui/app.py`` (v32.3 modularization).

Background jobs (run inside TestWorker). Each accepts a log_signal so it
can stream progress from the worker thread to the GUI via a queued
signal.

Runs the Telegram fetch in a SEPARATE Python process
(``telegram_fetch_worker.py``), identical to running test.py — this
avoids ALL asyncio/threading issues (ProactorEventLoop IOCP deadlock in
QThread / WinError 121; python-socks proxy silently ignored with
SelectorEventLoop / Errno 10060). The worker process runs in the main
thread with the default event loop, exactly like test.py.

* :func:`_run_telegram_worker`     — stdin JSON config -> stdout JSON
                                      result; stderr streamed to
                                      log_signal; interactive
                                      ``__NEED_CODE__`` / ``__NEED_PASSWORD__
                                      auth via code_callback.
* :func:`_telegram_test_job`       — quick connection test.
* :func:`_telegram_preview_job`    — first/last message preview for a range.
* :func:`_telegram_single_job`     — single message fetch + URL extraction.
* :func:`_telegram_keyword_job`    — Saved Messages keyword search (IDs).
* :func:`_bot_queue_job`           — bot-chat queue fetch (optionally
                                      mark-as-read / min_id filtering).

No Qt import — jobs accept any object with ``.emit(msg, level)``.
"""

import json
import os
import subprocess as _subprocess
import sys
import threading

from gitcurator.constants import APP_DIR

_APP_DIR = APP_DIR  # worker script path + cwd anchor (unchanged behavior)

__all__ = [
    "_run_telegram_worker", "_telegram_test_job", "_telegram_preview_job",
    "_telegram_single_job", "_telegram_keyword_job", "_bot_queue_job",
]


# ============================================================================
# Background jobs (run inside TestWorker). Each accepts a log_signal so it can
# stream progress from the worker thread to the GUI via a queued signal.
# ============================================================================


# ============================================================================
# SUBPROCESS-BASED TELEGRAM FETCH
# ----------------------------------------------------------------------------
# Runs the Telegram fetch in a SEPARATE Python process (telegram_fetch_worker.py),
# identical to running test.py. This avoids ALL asyncio/threading issues:
#   - ProactorEventLoop IOCP deadlock in QThread (WinError 121)
#   - python-socks proxy silently ignored with SelectorEventLoop (Errno 10060)
# The worker process runs in the main thread with the default event loop,
# exactly like test.py -> GUARANTEED to work.
# ============================================================================



def _run_telegram_worker(config: dict, log_signal, code_callback=None, timeout: int = 300) -> dict:
    """Run telegram_fetch_worker.py in a separate process.

    Streams the worker's stderr to the GUI log so you can see progress.
    Supports interactive auth: when the worker prints __NEED_CODE__ or
    __NEED_PASSWORD__ to stderr, code_callback is called (which blocks until
    the GUI provides the code), and the result is sent to the worker's stdin.
    Returns the JSON result parsed from stdout.
    """
    worker_script = os.path.join(
        _APP_DIR, 'gitcurator', 'integrations', 'telegram_fetch_worker.py'
    )
    if not os.path.isfile(worker_script):
        return {
            "success": False,
            "error": f"Worker script not found: {worker_script}"
        }

    log_signal.emit(f"Starting worker process: {worker_script}", "info")

    try:
        proc = _subprocess.Popen(
            [sys.executable, worker_script],
            stdin=_subprocess.PIPE,
            stdout=_subprocess.PIPE,
            stderr=_subprocess.PIPE,
            cwd=_APP_DIR,
            text=True,
            encoding='utf-8',
        )
    except Exception as e:
        return {"success": False, "error": f"Failed to start worker: {e}"}

    try:
        # Send config to worker's stdin (but DON'T close stdin — we may need
        # it later to send the login code/password).
        proc.stdin.write(json.dumps(config, default=str))
        proc.stdin.flush()
        proc.stdin.write("\n")  # newline so readline() in worker unblocks
        proc.stdin.flush()

        # Read stdout in a separate thread to prevent pipe buffer deadlock.
        # (If the worker writes a lot to stdout while we're reading stderr,
        # the pipe fills and the worker blocks.)
        stdout_chunks = []
        def _read_stdout():
            try:
                for chunk in iter(lambda: proc.stdout.read(4096), ''):
                    stdout_chunks.append(chunk)
            except Exception:
                pass
        stdout_thread = threading.Thread(target=_read_stdout, daemon=True)
        stdout_thread.start()

        # Read stderr line by line, stream to GUI log, handle auth requests.
        stderr_lines = []
        for line in proc.stderr:
            line = line.rstrip('\n')
            if not line:
                continue
            stderr_lines.append(line)

            if line == "__NEED_CODE__":
                # The worker has already called send_code_request() by this
                # point — Telegram has sent the code to the user's app.
                log_signal.emit("📲 Login code sent by Telegram. Check your Saved Messages or SMS, then enter it in the dialog...", "info")
                if code_callback:
                    code = code_callback("CODE")
                    if code:
                        proc.stdin.write(code + "\n")
                        proc.stdin.flush()
                        log_signal.emit("✅ Code sent to worker.", "info")
                    else:
                        log_signal.emit("❌ Code input cancelled.", "error")
                        proc.kill()
                        break
                else:
                    log_signal.emit("❌ No code callback available. Cannot authenticate.", "error")
                    proc.kill()
                    break
            elif line == "__NEED_PASSWORD__":
                log_signal.emit("🔒 Telegram 2FA password required...", "info")
                if code_callback:
                    password = code_callback("PASSWORD")
                    if password:
                        proc.stdin.write(password + "\n")
                        proc.stdin.flush()
                        log_signal.emit("✅ Password sent to worker.", "info")
                    else:
                        log_signal.emit("❌ Password input cancelled.", "error")
                        proc.kill()
                        break
                else:
                    log_signal.emit("❌ No password callback available.", "error")
                    proc.kill()
                    break
            else:
                log_signal.emit(f"[worker] {line}", "info")

        # Wait for process to finish
        try:
            proc.wait(timeout=timeout)
        except _subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            return {"success": False, "error": f"Worker timed out after {timeout}s"}

        stdout_thread.join(timeout=5)
        stdout_data = ''.join(stdout_chunks)

        # Try to parse JSON from stdout (works for both success and error cases,
        # since the worker writes JSON to stdout in both cases).
        try:
            return json.loads(stdout_data)
        except Exception:
            # If JSON parsing fails, include the full stderr + stdout in the error
            err_tail = '\n'.join(stderr_lines[-10:]) if stderr_lines else "(no stderr)"
            return {
                "success": False,
                "error": f"Worker exited with code {proc.returncode}.\n"
                         f"stderr:\n{err_tail}\n"
                         f"stdout: {stdout_data[:500]}"
            }

    except Exception as e:
        return {"success": False, "error": f"{type(e).__name__}: {e}"}


def _telegram_test_job(api_id, api_hash, phone, proxy, log_signal, code_callback=None):
    """Quick Telegram connection test (fetch latest message via subprocess)."""
    log_signal.emit("Testing Telegram via subprocess (identical to test.py)...", "info")
    config = {
        'api_id': int(api_id),
        'api_hash': api_hash,
        'phone': phone,
        'proxy': proxy,
        'session_file': 'session',
        'offset_start': 0,
        'count': 1,
        'preview_only': True,
        'from_id': 0,
        'to_id': 0,
        'preview_count': 1,
    }
    return _run_telegram_worker(config, log_signal, code_callback=code_callback)


def _telegram_preview_job(api_id, api_hash, phone, proxy, from_id, to_id, log_signal, code_callback=None):
    """Fetch first/last message preview for a range (via subprocess)."""
    log_signal.emit(f"Fetching preview for IDs {from_id}..{to_id} via subprocess...", "info")
    config = {
        'api_id': int(api_id),
        'api_hash': api_hash,
        'phone': phone,
        'proxy': proxy,
        'session_file': 'session',
        'preview_only': True,
        'from_id': int(from_id),
        'to_id': int(to_id),
        'preview_count': 2,
    }
    return _run_telegram_worker(config, log_signal, code_callback=code_callback)


def _telegram_single_job(api_id, api_hash, phone, proxy, single_id, log_signal, code_callback=None):
    """Fetch a single message and extract its GitHub URLs (via subprocess)."""
    log_signal.emit(f"Fetching single message ID {single_id} via subprocess...", "info")
    config = {
        'api_id': int(api_id),
        'api_hash': api_hash,
        'phone': phone,
        'proxy': proxy,
        'session_file': 'session',
        'preview_only': False,
        'from_id': int(single_id),
        'to_id': int(single_id),
    }
    return _run_telegram_worker(config, log_signal, code_callback=code_callback)


def _telegram_keyword_job(api_id, api_hash, phone, proxy, keyword_start, keyword_end, log_signal, code_callback=None):
    """Search Saved Messages for start/end keywords and return the message IDs."""
    log_signal.emit(f"Searching for keywords: '{keyword_start}' ... '{keyword_end}'", "info")
    config = {
        'api_id': int(api_id),
        'api_hash': api_hash,
        'phone': phone,
        'proxy': proxy,
        'session_file': 'session',
        'keyword_search': True,
        'keyword_start': keyword_start,
        'keyword_end': keyword_end,
    }
    return _run_telegram_worker(config, log_signal, code_callback=code_callback)


def _bot_queue_job(api_id, api_hash, phone, proxy, bot_username, log_signal, code_callback=None, mark_read=False, min_id=0):
    """Fetch unread GitHub URLs from the user's dedicated bot chat.
    Uses the user's Telethon session (through proxy) to read messages sent
    TO the bot. Resolves the bot by username (no Bot API call needed —
    api.telegram.org is blocked in Iran).
    If mark_read=True, marks all bot messages as read (clears the queue).

    v25 pre-flight: ``min_id`` (when > 0) makes the worker fetch only
    messages with id > min_id. Used by the "📬 Process New" button to
    skip messages already processed in a previous run."""
    config = {
        'api_id': int(api_id),
        'api_hash': api_hash,
        'phone': phone,
        'proxy': proxy,
        'session_file': 'session',
        'bot_queue': True,
        'bot_username': bot_username,
        'mark_read': mark_read,
        'min_id': int(min_id or 0),
    }
    return _run_telegram_worker(config, log_signal, code_callback=code_callback)
