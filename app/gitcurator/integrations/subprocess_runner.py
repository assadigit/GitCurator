#!/usr/bin/env python3
"""
gitcurator.integrations.subprocess_runner — run telegram_fetch_worker.py
safely in a subprocess, WITH A TIMEOUT THAT ACTUALLY WORKS.

THE BUG THIS MODULE FIXES (v0.05, "Another Telegram operation is already
running" forever)
=========================================================================
The old inline ``_run_telegram_worker`` in gui/app.py read the child's
stderr with ``for line in proc.stderr:`` — an UNBOUNDED blocking loop. Its
only timeout, ``proc.wait(timeout=300)``, ran *after* that loop, i.e. only
after the child closed stderr or died. A stalled telethon child (dead
SOCKS proxy, hung connect, session-file SQLite contention with an orphaned
previous run) therefore blocked the reader thread forever:

    reader thread hangs -> finished_signal never emitted
        -> _keep_worker cleanup never runs
            -> _telegram_busy stuck True
                -> every Telegram button logs
                   "⏳ Another Telegram operation is already running"

THE FIX
=======
1. **Idle-based timeout** — stderr is drained by a feeder thread into a
   queue; the consumer treats "no output for ``idle_timeout`` seconds" as
   a hang and kills the child. Progress lines (``[worker] Searched 500
   messages…``) naturally keep a long-but-healthy fetch alive, while a
   truly stuck child dies.
2. **Interactive-auth grace** — while the GUI is collecting a login code /
   2FA password (``__NEED_CODE__`` / ``__NEED_PASSWORD__``), the idle
   deadline is extended to ``auth_grace`` (default 6 min ≥ the GUI's 5-min
   input timeout) so a slow human never gets killed.
3. **Hard cap** — no run may exceed ``hard_cap`` (default 30 min)
   regardless of activity.
4. **Process registry** — every live child is tracked in a module-level
   registry so the GUI's closeEvent can kill them all; an orphaned child
   holding the SQLite session file is exactly what makes the NEXT app
   launch's auto bot-check stall and reproduce the forever-bug.

Pure stdlib. Importable from the GUI and from headless/CI contexts.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from typing import Callable, Dict, List, Optional

__VERSION__ = "1.0"

# ---------------------------------------------------------------------------
# Process registry — so shutdown can kill every live telegram child.
# ---------------------------------------------------------------------------

_registry_lock = threading.Lock()
_live_processes: "List[subprocess.Popen]" = []


def _register(proc: "subprocess.Popen") -> None:
    with _registry_lock:
        _live_processes.append(proc)


def _unregister(proc: "subprocess.Popen") -> None:
    with _registry_lock:
        try:
            _live_processes.remove(proc)
        except ValueError:
            pass


def kill_all_workers() -> int:
    """Terminate every live worker subprocess (app shutdown). Returns how
    many were killed. Safe to call from any thread; never raises."""
    with _registry_lock:
        procs = list(_live_processes)
    killed = 0
    for proc in procs:
        try:
            if proc.poll() is None:  # still alive
                proc.kill()
                killed += 1
            _unregister(proc)
        except Exception:
            pass
    return killed


def live_worker_count() -> int:
    """How many worker subprocesses are currently alive (diagnostics)."""
    with _registry_lock:
        return sum(1 for p in _live_processes if p.poll() is None)


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------

def run_telegram_worker(
    config: dict,
    log_signal,
    code_callback: Optional[Callable[[str], Optional[str]]] = None,
    timeout: int = 300,
    idle_timeout: int = 180,
    hard_cap: int = 1800,
    auth_grace: int = 360,
    worker_script: Optional[str] = None,
) -> dict:
    """Run telegram_fetch_worker.py in a separate process.

    Signature-compatible with the old gui/app.py ``_run_telegram_worker``
    (same first three params, same return shape) — callers migrate by
    importing this instead.

    Parameters
    ----------
    config:         JSON-serializable job config, sent to the child's stdin.
    log_signal:     Qt signal or callable ``(msg, level)``; child stderr is
                    streamed through it.
    code_callback:  ``None`` or ``fn("CODE"|"PASSWORD") -> str`` for
                    interactive auth. May block (the GUI shows a dialog).
    timeout:        LEGACY parameter, accepted for call-site compatibility
                    and IGNORED (the old wall-clock semantic was the bug).
    idle_timeout:   Kill the child after this many seconds with NO stderr
                    output (progress counts as activity). Floor of 5s.
    hard_cap:       Absolute maximum runtime in seconds, activity or not.
                    Whichever deadline (idle / hard) trips first wins.
    auth_grace:     Idle budget while waiting for the user to type a login
                    code / 2FA password (>= the GUI's 5-min input timeout).

    Returns the JSON dict the child wrote to stdout, or
    ``{"success": False, "error": …}``.
    """
    def _emit(msg: str, level: str = "info") -> None:
        try:
            log_signal.emit(msg, level)  # Qt signal
        except AttributeError:
            try:
                log_signal(msg, level)   # plain callable
            except Exception:
                pass
        except Exception:
            pass

    if worker_script is None:
        worker_script = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "gitcurator", "integrations", "telegram_fetch_worker.py",
        )
    if not os.path.isfile(worker_script):
        return {"success": False,
                "error": f"Worker script not found: {worker_script}"}

    # Effective budgets: small floor so a typo can't kill healthy children;
    # the legacy ``timeout`` arg is deliberately NOT consulted (see docstring).
    # Both deadlines are honored exactly as given — whichever trips first wins.
    idle_budget = max(5, int(idle_timeout))
    hard_budget = max(1, int(hard_cap))

    _emit(f"Starting worker process: {worker_script}", "info")

    try:
        proc = subprocess.Popen(
            [sys.executable, worker_script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(worker_script)))),
            text=True,
            encoding="utf-8",
        )
    except Exception as e:  # pragma: no cover - environment specific
        return {"success": False, "error": f"Failed to start worker: {e}"}

    _register(proc)

    # deadline bookkeeping
    started_at = time.monotonic()
    hard_deadline = started_at + hard_budget
    idle_deadline_var = {"value": started_at + idle_budget}
    idle_lock = threading.Lock()

    def _extend_idle(seconds: float) -> None:
        with idle_lock:
            idle_deadline_var["value"] = max(
                idle_deadline_var["value"], time.monotonic() + seconds
            )

    _ = timeout  # documented as ignored; keeps linters honest

    stderr_lines: List[str] = []
    stdout_chunks: List[str] = []
    kill_reason: Optional[str] = None

    def _set_kill(reason: str) -> None:
        nonlocal kill_reason
        if kill_reason is None:
            kill_reason = reason

    try:
        try:
            assert proc.stdin is not None
            proc.stdin.write(json.dumps(config, default=str))
            proc.stdin.flush()
            proc.stdin.write("\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            # Child died instantly (import error, bad interpreter…).
            _emit(f"Worker stdin closed before config send: {e}", "error")

        # stdout feeder (prevents pipe-buffer deadlock on large results)
        def _read_stdout() -> None:
            try:
                assert proc.stdout is not None
                for chunk in iter(lambda: proc.stdout.read(4096), ""):  # type: ignore[union-attr]
                    stdout_chunks.append(chunk)
            except Exception:
                pass

        stdout_thread = threading.Thread(target=_read_stdout, daemon=True)
        stdout_thread.start()

        # stderr feeder — the ONLY blocking reader; pushes lines to a queue
        # so the consumer below can time out instead of hanging forever.
        line_q: "queue.Queue[Optional[str]]" = queue.Queue()

        def _read_stderr() -> None:
            try:
                assert proc.stderr is not None
                for raw in iter(proc.stderr.readline, ""):  # type: ignore[union-attr]
                    line_q.put(raw.rstrip("\n"))
            except Exception:
                pass
            finally:
                line_q.put(None)  # EOF sentinel

        stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
        stderr_thread.start()

        # Consumer loop — bounded waits, enforced deadlines.
        while True:
            now = time.monotonic()
            if now >= hard_deadline:
                _set_kill(f"hard cap of {int(hard_cap)}s exceeded")
                break
            with idle_lock:
                idle_deadline = idle_deadline_var["value"]
            if now >= idle_deadline:
                _set_kill(
                    f"no worker output for {int(idle_budget)}s "
                    f"(network/proxy stall?)"
                )
                break

            try:
                line = line_q.get(timeout=1.0)
            except queue.Empty:
                continue  # loop re-checks deadlines

            if line is None:  # child closed stderr = finishing normally
                break

            if not line:
                continue
            stderr_lines.append(line)
            # any output = progress = the child is alive
            with idle_lock:
                idle_deadline_var["value"] = time.monotonic() + idle_budget

            if line == "__NEED_CODE__":
                _emit("📲 Login code sent by Telegram. Check your app or SMS, "
                      "then enter it in the dialog...", "info")
                if code_callback is None:
                    _emit("❌ No code callback available. Cannot authenticate.", "error")
                    _set_kill("auth needed but no callback wired")
                    break
                _extend_idle(auth_grace)  # user is typing — don't kill
                code = code_callback("CODE")
                if code:
                    try:
                        proc.stdin.write(code + "\n")  # type: ignore[union-attr]
                        proc.stdin.flush()
                        _emit("✅ Code sent to worker.", "info")
                    except (BrokenPipeError, OSError) as e:
                        _emit(f"❌ Worker died while sending code: {e}", "error")
                        _set_kill("worker died during auth")
                        break
                else:
                    _emit("❌ Code input cancelled.", "error")
                    _set_kill("auth cancelled by user")
                    break

            elif line == "__NEED_PASSWORD__":
                _emit("🔒 Telegram 2FA password required...", "info")
                if code_callback is None:
                    _emit("❌ No password callback available.", "error")
                    _set_kill("2FA needed but no callback wired")
                    break
                _extend_idle(auth_grace)
                password = code_callback("PASSWORD")
                if password:
                    try:
                        proc.stdin.write(password + "\n")  # type: ignore[union-attr]
                        proc.stdin.flush()
                        _emit("✅ Password sent to worker.", "info")
                    except (BrokenPipeError, OSError) as e:
                        _emit(f"❌ Worker died while sending password: {e}", "error")
                        _set_kill("worker died during 2FA")
                        break
                else:
                    _emit("❌ Password input cancelled.", "error")
                    _set_kill("2FA cancelled by user")
                    break
            else:
                # The child prefixes its own lines with "[worker] " — strip
                # it to avoid a doubled prefix in the GUI log.
                display = line.removeprefix("[worker] ") if line.startswith("[worker] ") else line
                _emit(f"[worker] {display}", "info")

        # Kill if a deadline tripped (or auth was cancelled).
        if kill_reason is not None:
            _emit(f"⚠️ Killing telegram worker: {kill_reason}", "warning")
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                pass
            return {
                "success": False,
                "error": f"Telegram worker aborted: {kill_reason}.",
                "stderr_tail": "\n".join(stderr_lines[-10:]),
            }

        # Normal completion path (stderr EOF reached).
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            _set_kill("stderr closed but process did not exit")
            proc.kill()
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
            return {"success": False,
                    "error": "Telegram worker finished its output but refused to exit; killed."}

        stdout_thread.join(timeout=5)
        stdout_data = "".join(stdout_chunks)

        try:
            result = json.loads(stdout_data)
            if isinstance(result, dict):
                return result
            return {"success": False,
                    "error": f"Worker returned non-object JSON: {str(result)[:300]}"}
        except Exception:
            err_tail = "\n".join(stderr_lines[-10:]) if stderr_lines else "(no stderr)"
            return {
                "success": False,
                "error": f"Worker exited with code {proc.returncode}.\n"
                         f"stderr:\n{err_tail}\n"
                         f"stdout: {stdout_data[:500]}",
            }

    except Exception as e:  # belt & braces — the caller thread must survive
        _set_kill(f"runner crash: {type(e).__name__}: {e}")
        try:
            proc.kill()
        except Exception:
            pass
        return {"success": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        _unregister(proc)
        # v0.06 — hygiene: close every pipe explicitly. Relying on garbage
        # collection leaked file descriptors (ResourceWarning) whenever a
        # killed child left streams half-open.
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Self-test (python -m gitcurator.integrations.subprocess_runner)
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import tempfile

    class _Printer:
        @staticmethod
        def emit(msg, level):
            print(f"[{level}] {msg}")

    with tempfile.TemporaryDirectory() as td:
        fake = os.path.join(td, "fake_worker.py")
        # 1) healthy child that streams progress then prints JSON
        with open(fake, "w", encoding="utf-8") as f:
            f.write(
                "import sys, time\n"
                "for i in range(3):\n"
                "    print(f'[worker] step {i}', file=sys.stderr, flush=True)\n"
                "    time.sleep(0.2)\n"
                "print('{\"success\": true, \"urls\": []}')\n"
            )
        res = run_telegram_worker({}, _Printer(), worker_script=fake,
                                  idle_timeout=10, hard_cap=30)
        assert res.get("success") is True, res
        print("PASS: healthy worker ->", res)

        # 2) hung child -> idle timeout kills it
        with open(fake, "w", encoding="utf-8") as f:
            f.write("import sys, time\nprint('[worker] starting', file=sys.stderr, flush=True)\ntime.sleep(600)\n")
        res = run_telegram_worker({}, _Printer(), worker_script=fake,
                                  idle_timeout=2, hard_cap=60)
        assert res.get("success") is False and "aborted" in res.get("error", ""), res
        print("PASS: hung worker killed ->", res["error"])

        # 3) interactive auth grace
        with open(fake, "w", encoding="utf-8") as f:
            f.write(
                "import sys, time\n"
                "print('__NEED_CODE__', file=sys.stderr, flush=True)\n"
                "line = sys.stdin.readline().strip()\n"
                "print(f'[worker] got code {line}', file=sys.stderr, flush=True)\n"
                "print('{\"success\": true, \"auth\": true}')\n"
            )
        res = run_telegram_worker({}, _Printer(),
                                  code_callback=lambda kind: "12345",
                                  worker_script=fake,
                                  idle_timeout=2, auth_grace=20, hard_cap=60)
        assert res.get("success") is True, res
        print("PASS: auth flow ->", res)

        # 4) hard cap
        with open(fake, "w", encoding="utf-8") as f:
            f.write("import sys, time\nwhile True: print('[worker] busy', file=sys.stderr, flush=True); time.sleep(0.5)\n")
        # busy child: prints constantly (idle never trips) but exceeds the
        # absolute hard cap (3s) -> wall-clock deadline wins.
        res = run_telegram_worker({}, _Printer(), worker_script=fake,
                                  idle_timeout=5, hard_cap=3)
        assert res.get("success") is False, res
        assert "hard cap" in res.get("error", ""), res
        print("PASS: hard cap ->", res["error"])

    print(f"\nAll subprocess_runner self-tests passed. live children: {live_worker_count()}")
