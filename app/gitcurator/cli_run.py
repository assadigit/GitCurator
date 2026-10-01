"""cli_run — the CLI's visualized run engine.

Pre-flight checks (token / proxy / LLM), the bot-queue fetch, the
animated batch run, the dry-run report and the config card — moved
verbatim from gitcurator/cli.py at v0.25.0; cli.py re-exports every
name so existing imports keep working unchanged.
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
    C, StatusLine, _gui_symbol, cli_print, mask, paint, rule, style_level,
    term_width, _PRINT_LOCK,
)
from gitcurator.cli_settings import (  # noqa: F401 — config + model pick
    _ask, _clean_config_for_save, apply_model_choice, prompt_model_menu,
)


# ----------------------------------------------------------------------------
# Fetch phase helpers (threaded _bot_queue_job with a manual spinner)
# ----------------------------------------------------------------------------

class _LogShim:
    """Duck-typed log_signal: queues worker log lines for the main thread."""

    def __init__(self, sink: StatusLine):
        self._sink = sink
        self.queue: deque = deque()

    def emit(self, msg, level="info"):
        # Called on the fetch thread — never print here; the main-thread
        # spinner loop drains the queue so output stays race-free.
        self.queue.append((str(msg), str(level)))


def _code_prompt(kind: str):
    """Interactive Telegram login prompt (runs on the fetch thread)."""
    label = "login code" if kind == "CODE" else "two-step verification PASSWORD"
    with _PRINT_LOCK:
        print()
    try:
        val = input(paint("📩 ", C.CYAN) + f"Enter the Telegram {label}: ").strip()
    except EOFError:
        return ""
    return val


def _links_blocked_domains(cfg: dict) -> list:
    """v0.24.1 — core.links.blocked_domains_from_config, imported lazily
    (CLI keeps its deps local). Never raises."""
    try:
        from gitcurator.core import links as _links
        return _links.blocked_domains_from_config(cfg)
    except Exception:
        return []


def _links_self_domains(cfg: dict) -> list:
    """v0.24.1 — core.links.self_domains_from_config, imported lazily."""
    try:
        from gitcurator.core import links as _links
        return _links.self_domains_from_config(cfg)
    except Exception:
        return []


def fetch_bot_queue(cfg: dict, status: StatusLine, min_id: int = 0) -> dict:
    """Run _bot_queue_job in a worker thread while the main thread animates."""
    try:
        _bot_queue_job = _gui_symbol("_bot_queue_job")
    except ImportError as exc:
        # deps missing: surface the actionable message as a fetch failure —
        # callers already render failure dicts and exit non-zero
        return {"success": False, "error": str(exc)}

    shim = _LogShim(status)
    result_box: dict = {}
    done = threading.Event()

    def _job():
        try:
            result_box["r"] = _bot_queue_job(
                str(cfg.get("telegram_api_id", 0) or 0),
                cfg.get("telegram_api_hash", ""),
                cfg.get("telegram_phone", ""),
                cfg.get("proxy", {}) or {},
                cfg.get("bot_username", ""),
                shim,                                  # log_signal (queued)
                code_callback=_code_prompt,            # interactive login
                mark_read=False,
                min_id=min_id,
                vault_path=cfg.get("vault_path", ""),
                blocked_domains=_links_blocked_domains(cfg),
                self_domains=_links_self_domains(cfg),
                # v0.24.1 — Fix (websites never sync): classify the
                # non-GitHub links against the WEBSITES vault too, so the
                # CLI's "pending" covers both pipelines (same fields the
                # GUI's queue check produces).
                website_vault_path=((cfg.get("website_vault_path") or "").strip() or None),
                websites_pipeline_on=bool(
                    (cfg.get("pipelines") or {}).get("websites", False)),
            )
        except Exception as exc:                       # pragma: no cover
            result_box["r"] = {"success": False, "error": f"{type(exc).__name__}: {exc}"}
        finally:
            done.set()

    threading.Thread(target=_job, name="cli-bot-fetch", daemon=True).start()
    while not done.is_set():
        # Drain queued log lines, then animate one spinner frame.
        while shim.queue:
            msg, lvl = shim.queue.popleft()
            status.log(msg, lvl)
        status.tick()
        time.sleep(0.09)
    while shim.queue:
        msg, lvl = shim.queue.popleft()
        status.log(msg, lvl)
    return result_box.get("r", {"success": False, "error": "fetch thread died"})


def _mark_bot_queue_read(cfg: dict, status: "StatusLine") -> bool:
    """v0.09.4 — Phase 5 CLEAR's final step for the CLI: mark every bot
    message as read (the same call the GUI's auto-mark makes after a
    fully-verified batch). Best-effort — a failure is logged and returns
    False; it never fails the run (the vault index still dedups).

    Runs _bot_queue_job(mark_read=True) in a worker thread while the main
    thread animates the spinner — same pattern as fetch_bot_queue."""
    try:
        _bot_queue_job = _gui_symbol("_bot_queue_job")
    except ImportError as exc:
        status.log(f"Could not mark the bot queue read: {exc}", "warning")
        return False
    shim = _LogShim(status)
    result_box: dict = {}
    done = threading.Event()

    def _job():
        try:
            result_box["r"] = _bot_queue_job(
                str(cfg.get("telegram_api_id", 0) or 0),
                cfg.get("telegram_api_hash", ""),
                cfg.get("telegram_phone", ""),
                cfg.get("proxy", {}) or {},
                cfg.get("bot_username", ""),
                shim, code_callback=_code_prompt, mark_read=True,
            )
        except Exception as exc:                       # pragma: no cover
            result_box["r"] = {"success": False, "error": f"{type(exc).__name__}: {exc}"}
        finally:
            done.set()

    threading.Thread(target=_job, name="cli-bot-mark-read", daemon=True).start()
    while not done.is_set():
        while shim.queue:
            status.log(*shim.queue.popleft())
        status.tick()
        time.sleep(0.09)
    while shim.queue:
        status.log(*shim.queue.popleft())
    r = result_box.get("r", {})
    if not r.get("success"):
        status.log(f"Mark-read failed: {r.get('error', 'unknown')}", "warning")
    return bool(r.get("success"))


# ----------------------------------------------------------------------------
# Batch phase: ProcessingWorker inside a Qt loop, rendered by StatusLine
# ----------------------------------------------------------------------------

def _write_dryrun_report():
    """v0.09.5 — Phase 0: save the dry-run write log as Markdown OUTSIDE the
    vault (app/reports/dry-runs/) so the owner can read exactly what a
    dry-run would have changed. Returns the report path, or None on failure.

    Uses a plain open() on purpose: this runs from the finished_signal
    handler, which can fire a hair BEFORE the worker thread's finally block
    turns the dry-run switch off — a gated write here could end up recorded
    instead of saved. The report itself is never inside a vault, so the
    atomic-write rule for vault files does not apply."""
    try:
        from gitcurator.core import dryrun as _dryrun
        from gitcurator.constants import APP_DIR
        out_dir = os.path.join(APP_DIR, "reports", "dry-runs")
        os.makedirs(out_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(out_dir, f"dry_run_{stamp}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(_dryrun.render_report_markdown("GitCurator dry-run report"))
        return path
    except Exception:
        return None


def run_batch_visual(cfg: dict, mode: str, *, urls=None, import_file=None,
                     range_from=None, range_to=None, offset_start=None,
                     offset_count=None, bot_source=False, non_github=None,
                     intake_duplicates=0, raw_url_count=0,
                     vault_arg: str | None = None,
                     bot_queue_max_id: int = 0,
                     dry_run: bool = False) -> int:
    """Run one ProcessingWorker batch with colors + spinner + progress bar.

    Mirrors run_headless()'s wiring (signals received, VaultSeal + GoodRepos
    best-effort on finish) but every log line is colored and the bottom line
    is a live spinner/progress renderer. Returns a process exit code.

    v0.09.4 — ``bot_queue_max_id`` (set by cmd_auto): after a SUCCESSFUL
    bot-queue batch that the LinkTracker fully verifies, the CLI now runs
    the GUI's Phase 5 CLEAR — the bot messages are marked read and
    last_processed_msg_id is advanced (persisted to config.json) so the
    next --auto fetches only newer messages. A batch with any unverified
    link advances nothing and keeps the queue unread: the failed links are
    re-fetched and retried next run (nothing is lost, nothing repeats).

    v0.09.5 — Phase 0 (``--dry-run``): when ``dry_run`` is True the worker
    flips the global dry-run switch (gitcurator/core/dryrun.py), so every
    vault write is logged instead of performed, the batch reads the real
    cache through a throwaway copy (nothing persists), and this function
    skips VaultSeal, the Good Repos publish, the bot-queue mark-read and
    the last_processed_msg_id advance. A Markdown report of everything
    that was withheld is saved to app/reports/dry-runs/."""
    try:
        from PyQt6.QtCore import QCoreApplication, QTimer
        ProcessingWorker = _gui_symbol("ProcessingWorker")
    except (ImportError, SystemExit) as exc:
        # GUI requirements missing (or their guard exited): report cleanly,
        # exit non-zero — never a mid-command process kill or a Qt traceback
        cli_print(f"Batch engine unavailable: {exc}", "error")
        cli_print("Install the requirements first:  pip install -r requirements.txt", "info")
        return 2
    from gitcurator.integrations import vaultseal as _vaultseal
    from gitcurator.integrations import goodrepos as _goodrepos

    if vault_arg:
        cfg["vault_path"] = vault_arg

    app = QCoreApplication.instance() or QCoreApplication(sys.argv)
    status = StatusLine()

    worker = ProcessingWorker(
        config=cfg, mode=mode,
        range_from=range_from, range_to=range_to,
        offset_start=offset_start, offset_count=offset_count,
        import_file=import_file, urls=urls,
        headless=True,  # no GUI dialogs: non-blocking defaults everywhere
        dry_run=dry_run,  # v0.09.5 — Phase 0: --dry-run batches log, never write
    )
    worker._bot_source = bot_source
    if non_github:
        worker._non_github_urls = list(non_github)
    worker._intake_duplicates = int(intake_duplicates or 0)
    worker._raw_url_count = int(raw_url_count or 0)

    # v0.07.2 — THE model-picker bridge (owner report: "it didn't let me
    # choose a new model ... Model wasn't found = failed cli"). The worker
    # calls this on ITS thread when the configured model is missing at
    # warmup (or mid-batch); we pause the status line, print the numbered
    # menu, read the answer, and persist the choice. prompt_model_menu
    # holds _PRINT_LOCK the whole time so the spinner can't overwrite the
    # menu, and auto-picks when stdin isn't a TTY.
    def _worker_model_prompt(configured, available):
        choice = prompt_model_menu(configured, available, status=status)
        if choice:
            apply_model_choice(cfg, choice, config_path)
        return choice

    worker.model_prompt_callback = _worker_model_prompt

    started = time.time()
    outcome = {"success": False}  # written by _finish; drives the exit code

    # --- signal wiring -------------------------------------------------------
    worker.log_message.connect(lambda m, l: status.log(m, l))
    worker.progress_updated.connect(lambda cur, tot: (status.set_progress(cur, tot), status.tick()))
    worker.status_updated.connect(lambda url: status.set_repo(url.rsplit("/", 1)[-1] or url))
    worker.code_requested.connect(lambda pt: status.log(
        f"Telegram {pt} requested — waiting for the code prompt…", "warning"))
    worker.disk_full_signal.connect(lambda p: status.log(
        f"Disk full at {p} — repo skipped and recorded in retry queue.", "error"))
    worker.llm_failed_signal.connect(lambda repo: status.log(
        f"LLM failed for {repo} — fallback note written, batch continues.", "warning"))

    config_path = cfg.get("__config_path__")

    def _on_model_changed(provider, model):
        status.log(f"LLM model switched to '{model}' ({provider}) — saving config.", "info")
        if config_path:
            from gitcurator.core.storage import write_config_file
            try:
                # v0.07.2: strip CLI-private keys (__config_path__…) — the
                # old code leaked them into config.json on every save.
                write_config_file(config_path, _clean_config_for_save(cfg))
            except Exception as exc:
                status.log(f"Could not save config: {exc}", "warning")

    worker.model_changed.connect(_on_model_changed)

    def _finish(success: bool, message: str):
        outcome["success"] = bool(success)
        status.stop()
        elapsed = max(1, int(time.time() - started))
        print()
        print(rule())
        if success:
            print(paint("  ✔ BATCH DONE ", C.GREEN, C.BOLD)
                  + paint(f"in {elapsed // 60}m {elapsed % 60:02d}s", C.DIM))
            print(paint(f"  {message}", C.GREEN))
        else:
            print(paint("  ✖ BATCH FAILED ", C.RED, C.BOLD)
                  + paint(f"in {elapsed // 60}m {elapsed % 60:02d}s", C.DIM))
            print(paint(f"  {message}", C.RED))
        print(rule())

        # v0.09.5 — Phase 0 (dry-run): a dry-run batch must be side-effect
        # free EVERYWHERE. The vault writes were already withheld by the
        # dry-run switch inside the worker; here the three post-batch
        # side effects are skipped too: no git seal (VaultSeal), no Good
        # Repos publish, no bot-queue mark-read / last_processed_msg_id
        # advance. Then the withheld-writes report is saved and we stop.
        if dry_run:
            from gitcurator.core import dryrun as _dryrun
            n = _dryrun.entry_count()
            status.log(
                f"🧪 DRY-RUN COMPLETE — {n} vault operation(s) were logged, "
                "not performed. VaultSeal, Good Repos publish, bot-queue "
                "mark-read and last_processed_msg_id were all skipped.",
                "warning")
            report_path = _write_dryrun_report()
            if report_path:
                status.log(f"🧪 Dry-run report: {report_path}", "info")
            print()
            app.quit()
            return

        # VaultSeal — best-effort backup of whatever was written (runs for
        # failed batches too; notes saved before a mid-run failure are
        # exactly what we want backed up).
        try:
            summary = {"processed": int(getattr(worker, "processed", 0) or 0),
                       "total": int(getattr(worker, "total", 0) or 0)}
            status.log("Sealing vault (git commit + push if configured)…", "info")
            vs = _vaultseal.seal_from_config(cfg, run_summary=summary)
            status.log(f"VaultSeal: {vs.describe()}", "success" if vs.ok else "warning")
        except Exception as exc:
            status.log(f"VaultSeal error: {exc}", "warning")
        try:
            gr = _goodrepos.publish_from_config(cfg, run_summary=summary)
            status.log(f"Good Repos: {gr.describe()}", "success" if gr.ok else "warning")
        except Exception as exc:
            status.log(f"Good Repos error: {exc}", "warning")

        # v0.10.0 — Phase 1: the WEBSITES vault's own private mirror (a
        # second, independent VaultSeal). Silent no-op while the websites
        # pipeline is OFF (the default).
        try:
            ws = _vaultseal.websites_seal_from_config(cfg, run_summary=summary)
            if ws.sealed or ws.error:
                status.log(f"Websites vault seal: {ws.describe()}",
                           "success" if ws.ok else "warning")
        except Exception as exc:
            status.log(f"Websites vault seal error: {exc}", "warning")

        # v0.09.4 — Phase 5 CLEAR for the CLI (the GUI's anti-repeat final
        # step, previously missing here): the bot queue is only ever
        # "consumed" when EVERY link in the batch verified. get_all_clear()
        # treats failed/pending GitHub links as NOT clear, so a half-finished
        # batch leaves the messages un-read and the ID un-advanced — the
        # next run re-fetches exactly those messages and retries them.
        if success and bot_source:
            tracker = getattr(worker, "link_tracker", None)
            if bot_queue_max_id > 0 and tracker is not None:
                if tracker.get_all_clear():
                    old_id = 0
                    try:
                        old_id = int(cfg.get("last_processed_msg_id", 0) or 0)
                    except (TypeError, ValueError):
                        old_id = 0
                    if bot_queue_max_id > old_id and config_path:
                        cfg["last_processed_msg_id"] = int(bot_queue_max_id)
                        try:
                            from gitcurator.core.storage import write_config_file
                            write_config_file(config_path, _clean_config_for_save(cfg))
                            status.log(
                                f"📌 last_processed_msg_id advanced: {old_id} → "
                                f"{bot_queue_max_id} — the next --auto fetches only "
                                f"messages newer than that.", "success")
                        except Exception as exc:
                            status.log(
                                f"⚠️ Could not save last_processed_msg_id ({exc}) — "
                                f"this run is verified, but the next --auto will "
                                f"re-scan (dedup still protects against duplicates).",
                                "warning")
                    _clear_status = StatusLine()
                    _clear_status.start("Marking bot queue as read")
                    ok = _mark_bot_queue_read(cfg, _clear_status)
                    _clear_status.stop()
                    if ok:
                        status.log("✓ Bot queue marked as read — all links verified "
                                   "(Phase 5 CLEAR).", "success")
                else:
                    unverified = [
                        l for l in tracker.manifest.get("links", [])
                        if l.get("status") in ("failed", "processing", "pending")
                        and l.get("type") == "github"
                    ]
                    status.log(
                        f"⏸️ {len(unverified)} link(s) not verified — the bot queue "
                        f"stays UNREAD and last_processed_msg_id is NOT advanced; "
                        f"the next --auto re-fetches and retries them.", "warning")
            elif bot_queue_max_id > 0 and tracker is None:
                status.log("⏸️ No link manifest for this batch (empty vault path?) — "
                           "last_processed_msg_id not advanced; dedup still protects "
                           "against duplicates.", "warning")

        print()
        app.quit()

    worker.finished_signal.connect(_finish)

    # --- Ctrl+C: graceful stop (worker sets its stop flag; Qt loop exits) ----
    def _sigint(*_):
        status.log("Interrupt received — stopping batch…", "warning")
        try:
            worker.stop()
        except Exception:
            pass
        app.quit()

    try:
        signal.signal(signal.SIGINT, _sigint)
    except (ValueError, OSError):
        pass  # non-main thread (tests) — Ctrl+C simply not wired

    # Spinner heartbeat — doubles as the periodic return-to-Python that lets
    # the SIGINT handler above actually run inside app.exec().
    timer = QTimer()
    timer.timeout.connect(status.tick)
    timer.start(100)

    status.start("Processing batch", 1)
    worker.start()
    code = app.exec()
    timer.stop()
    status.stop()
    # app.exec()'s own return code is always 0 after quit() — the batch's
    # success flag (set in _finish) is what scripts and the .bat launcher
    # need: a failed batch must exit non-zero.
    return 0 if outcome["success"] else (code or 1)


def _section(title: str) -> None:
    """Full-width ────── title ────── divider (like the GUI log sections)."""
    w = min(term_width(), 118)
    t = f" {title} "
    if len(t) >= w:
        print(C.DIM + t + C.RESET)
        return
    pad = (w - len(t)) // 2
    print(C.DIM + "─" * pad + t + "─" * (w - pad - len(t)) + C.RESET)


def _check_line(level: str, label: str, detail: str) -> None:
    """One aligned pre-flight result line: '  ✓ GitHub token  …'."""
    color, icon = style_level(level)
    print(f"  {paint(icon, color)} {paint(label.ljust(15), C.BOLD)} {paint(detail, color)}")


def run_config_card(cfg: dict) -> None:
    """The boxed 'run configuration' snapshot (vault / bot / proxy / LLM /
    token) shown before --auto runs — mirrors the GUI's settings summary."""
    px = cfg.get("proxy") or {}
    if cfg.get("llm_provider", "ollama") == "ollama":
        llm_txt = "Ollama · " + ((cfg.get("ollama") or {}).get("model") or "?")
    elif cfg.get("llm_provider", "ollama") == "llamacpp":
        llm_txt = ("llama.cpp · " + (cfg.get("llamacpp_model") or "(auto-detect)")
                   + " @ " + (cfg.get("llamacpp_api_url")
                              or "http://127.0.0.1:8080/v1"))
    else:
        llm_txt = f"OpenAI-compatible · {cfg.get('cloud_model', '?')}"
    tok = str(cfg.get("github_token", "") or "")
    if len(tok) > 10:
        tok_txt = tok[:4] + "•" * 8 + tok[-4:]
    elif tok:
        tok_txt = "•" * len(tok)
    else:
        tok_txt = "(not set)"
    rows = [
        ("Vault", str(cfg.get("vault_path", "") or "(not set)")),
        ("Bot queue", ("@" + cfg["bot_username"]) if cfg.get("bot_username") else "(not set)"),
        ("Proxy", (f"{px.get('host', '?')}:{px.get('port', '?')} (on)"
                   if px.get("enabled") else "disabled")),
        ("LLM", llm_txt),
        ("GitHub token", tok_txt),
    ]
    label_w = 18
    avail = min(max(64, term_width() - 2), 118) - 2          # inner box width
    val_w = avail - label_w - 1
    shown = [(k, (v[: val_w - 1] + "…") if len(v) > val_w else v) for k, v in rows]
    title = " run configuration "
    lead = max(3, (avail - len(title)) // 2)
    print(paint("┌" + "─" * lead + title + "─" * (avail - lead - len(title)) + "┐", C.DIM))
    for k, v in shown:
        print(paint("│ ", C.DIM) + paint(k.ljust(label_w - 2), C.BOLD)
              + paint(" " + v, C.CYAN)
              + paint(" " * (avail - label_w - 1 - len(v)) + "│", C.DIM))
    print(paint("└" + "─" * avail + "┘", C.DIM))


def _preflight_github(cfg: dict):
    """Validate the GitHub token (same call the pipeline makes per repo).
    Returns (level, detail): level in success/warning/error."""
    token = cfg.get("github_token", "") or ""
    if not token:
        return ("warning", "no token — running unauthenticated (60 req/h limit)")
    try:
        from github import Auth, Github
        try:
            g = Github(auth=Auth.Token(token), timeout=15)
        except TypeError:                      # PyGithub < 1.57: no timeout kwarg
            g = Github(auth=Auth.Token(token))
        login = g.get_user().login
        return ("success", f"authenticated as {login}")
    except Exception as exc:
        msg = str(exc)
        if "Bad credentials" in msg or getattr(exc, "status", None) == 401:
            return ("error", "token REJECTED (401) — make a new one at github.com/settings/tokens")
        return ("error", f"GitHub unreachable ({exc.__class__.__name__}) — check internet")


def _preflight_proxy(cfg: dict):
    """TCP-reach the configured proxy (the Telethon fetch goes through it)."""
    px = cfg.get("proxy") or {}
    if not px.get("enabled"):
        return ("dim", "disabled — connecting directly")
    host = str(px.get("host", "127.0.0.1"))
    try:
        port = int(px.get("port", 0) or 0)
    except (TypeError, ValueError):
        port = 0
    t0 = time.time()
    try:
        with socket.create_connection((host, port), timeout=5):
            ms = int((time.time() - t0) * 1000)
        return ("success", f"{host}:{port} reachable ({px.get('type', 'socks5')}, {ms} ms)")
    except Exception as exc:
        return ("warning", f"{host}:{port} UNREACHABLE ({exc.__class__.__name__}) — "
                           f"start the proxy app or disable it in config.json")


def _preflight_llm(cfg: dict, config_path: str | None) -> bool:
    """Ollama server + installed-model check. THE v0.07.2 fix: a missing
    configured model opens the interactive picker here — BEFORE the batch —
    and the choice is persisted. Returns False only when the run cannot
    proceed (no models at all / user declined).
    v0.15.0 — llama.cpp engine detection: the llamacpp provider probes the
    configured URL (then scans the common ports), AUTO-FILLS the model from
    the running server and persists it — "its model detected
    automatically". False only when no server / no model at all."""
    if cfg.get("llm_provider", "ollama") == "llamacpp":
        from gitcurator.core import llm_client as _llm
        url = cfg.get("llamacpp_api_url", "") or _llm.LLAMACPP_DEFAULT_BASE
        key = cfg.get("llamacpp_api_key", "") or ""
        probe = _llm.probe_llamacpp(url, key)
        if not probe.get("found"):
            probe = _llm.detect_llamacpp(key) or probe
        if not probe.get("found"):
            _check_line("error", "LLM provider",
                        f"llama.cpp server not detected ({probe.get('detail')}) — "
                        "start it with: llama-server -m <model>.gguf --port 8080")
            return False
        if probe.get("base_url") and url != probe["base_url"] + "/v1":
            # The configured URL was stale — the scan found the server
            # elsewhere; persist the working URL.
            cfg["llamacpp_api_url"] = probe["base_url"] + "/v1"
            url = cfg["llamacpp_api_url"]
        model = _llm.resolve_llamacpp_model(
            cfg, probe.get("models"), probe.get("props_model"))
        if not model:
            _check_line("error", "LLM provider",
                        "llama.cpp up but no model loaded — start llama-server "
                        "with -m <model>.gguf")
            return False
        if not str(cfg.get("llamacpp_model", "") or "").strip():
            apply_model_choice(cfg, model, config_path)
            _check_line("success", "LLM provider",
                        f"llama.cpp up @ {probe['base_url']} · model '{model}' "
                        "(auto-detected — saved to config.json)"
                        + (" · still loading" if probe.get("ready") is False else ""))
            return True
        listed = (not probe.get("models")) or \
            cfg["llamacpp_model"].lower() in {n.lower() for n in probe["models"]}
        _check_line("success" if listed else "warning", "LLM provider",
                    f"llama.cpp up @ {probe['base_url']} · model '{cfg['llamacpp_model']}'"
                    + ("" if listed else " (NOT in the /v1/models list — trying anyway)")
                    + (" · still loading" if probe.get("ready") is False else ""))
        return True
    if cfg.get("llm_provider", "ollama") != "ollama":
        _check_line("success", "LLM provider",
                    f"OpenAI-compatible endpoint {cfg.get('cloud_api_url', '?')} · model '{cfg.get('cloud_model', '?')}'")
        return True
    oll = cfg.get("ollama") or {}
    base = oll.get("base_url", "http://127.0.0.1:11434")
    model = oll.get("model", "")
    try:
        import ollama as _ol
        from gitcurator.core import llm_client as _llm
        models = _llm.list_models_with_timeout(_ol.Client(host=base), 15)
    except Exception as exc:
        _check_line("warning", "LLM provider",
                    f"Ollama not reachable at {base} ({exc.__class__.__name__}) — "
                    f"the pipeline will try to auto-start it")
        return True
    if not models:
        _check_line("error", "LLM provider",
                    f"Ollama up @ {base} but NO models installed — run `ollama pull <model>` first")
        return False
    if model and model in models:
        _check_line("success", "LLM provider",
                    f"Ollama up @ {base} · {len(models)} model(s) · '{model}' ready")
        return True
    _check_line("warning", "LLM provider",
                f"Ollama up @ {base} · {len(models)} model(s) — "
                f"configured '{model}' is NOT pulled")
    choice = prompt_model_menu(model, models)
    if not choice:
        cli_print("No model selected — aborting. Run `ollama pull <name>` "
                  "(or fix config.json) and rerun.", "error")
        return False
    apply_model_choice(cfg, choice, config_path)
    _check_line("success", "LLM provider", f"switched to '{choice}' — saved to config.json")
    return True


def preflight_checks(cfg: dict, *, card: bool = False) -> bool:
    """GitHub token + proxy + LLM checks before any batch work. Returns
    False when the run must abort. Never touches the network twice when a
    check is irrelevant (proxy is checked only when enabled, cloud provider
    skips the Ollama probe)."""
    path = cfg.get("__config_path__")
    if card:
        run_config_card(cfg)
    _section("pre-flight checks")
    # -- GitHub token -------------------------------------------------------
    lvl, txt = _preflight_github(cfg)
    _check_line(lvl, "GitHub token", txt)
    if lvl == "error":
        cont = False
        try:
            cont = (hasattr(sys.stdin, "isatty") and sys.stdin.isatty()
                    and _ask("GitHub check failed — continue anyway? (y/N)", "n").lower().startswith("y"))
        except Exception:
            cont = False
        if not cont:
            cli_print("Aborting — fix the token (config.json / --init) and rerun.", "error")
            return False
    # -- Proxy --------------------------------------------------------------
    lvl, txt = _preflight_proxy(cfg)
    _check_line(lvl, "Proxy", txt)
    # -- LLM (includes the interactive model picker) ------------------------
    if not _preflight_llm(cfg, path):
        return False
    print()
    return True
