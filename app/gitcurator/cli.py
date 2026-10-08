#!/usr/bin/env python3
"""
GitCurator CLI — the visual terminal client (v0.09, merged lineage).

A full-color, animated command-line companion to the PyQt6 GUI. It runs the
EXACT same pipeline (ProcessingWorker → LLM → Obsidian vault → VaultSeal →
Good Repos) through a QCoreApplication, so every fix in the GUI pipeline
applies here too — but the output is a live terminal experience:

    ✦ colored log levels          (info / success / warning / error)
    ✦ braille spinner animation   (⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏)
    ✦ determinate progress bar    (█████████░░░░░░░ 12/40 · owner/repo)
    ✦ credentials stored locally  (config.json — same file the GUI uses)
    ✦ one-command full-auto mode  (--auto: bot SYNC → process → seal → publish)
    ✦ double-click .bat launchers (GitCurator-CLI.bat / GitCurator-CLI-Setup.bat)
    ✦ pre-flight checks + model picker (v0.07.2: token / proxy / Ollama are
      verified BEFORE the run; a not-pulled model opens an interactive
      numbered menu — the choice is saved to config.json — instead of
      failing every repo in the batch)

Usage
-----
    python main.py --cli --init          # first-run wizard (saves config.json)
    python main.py --cli --login         # Telegram login: enter the verification code
    python main.py --cli --auto          # fully automatic run (bot → vault)
    python main.py --cli --status        # config + cache + strike summary
    python main.py --cli --import-file urls.txt
    python main.py --cli --from-id 123 --to-id 456
    python main.py --cli --offset-start 100 --count 50
    python main.py --cli --single-id 12345
    python main.py --cli --retry-failed  # reprocess the retry queue
    python main.py --cli --mark-read     # clear the bot's unread queue

Everything is zero-extra-dependency: the visuals are plain ANSI escapes
(colorama is used only when present, to enable ANSI on legacy Windows
consoles). The pipeline itself needs the same requirements.txt as the GUI.

Entry points
------------
    python main.py --cli …          (thin shim, keeps historical UX)
    python gitcurator/cli.py …      (direct)
    python -m gitcurator.cli …      (module)
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

# v0.25.0 — these three modules were split out of cli.py (verbatim
# moves); every name stays importable from gitcurator.cli unchanged.
from gitcurator.cli_terminal import (  # noqa: F401
    C, paint, _harden_stdio, _gui_symbol, style_level, term_width, mask,
    rule, cli_print, banner, render_bar, StatusLine, _strip_ansi,
    _LEVEL_STYLE, _PRINT_LOCK, SPIN_FRAMES,
)
from gitcurator.cli_settings import (  # noqa: F401
    _config_path, load_config, save_config, _ask, _mask_token,
    _clean_config_for_save, smart_model_pick, prompt_model_menu,
    apply_model_choice,
)
from gitcurator.cli_run import (  # noqa: F401
    _LogShim, _code_prompt, _links_blocked_domains, _links_self_domains,
    fetch_bot_queue, _mark_bot_queue_read, _write_dryrun_report,
    run_batch_visual, _section, _check_line, run_config_card,
    _preflight_github, _preflight_proxy, _preflight_llm, preflight_checks,
)


# ----------------------------------------------------------------------------
# --init : first-run wizard (credentials stored locally in config.json)
# ----------------------------------------------------------------------------

def cmd_init(args) -> int:
    banner()
    path = _config_path(args.config)
    existing = load_config(path) or {}
    print(paint("First-run setup — answers are saved to ", C.BOLD) + paint(path, C.CYAN))
    print(paint("Press Enter to keep the [current] value. Secrets stay on this machine.\n", C.DIM))

    cfg = dict(existing)

    # -- vault ---------------------------------------------------------------
    cfg["vault_path"] = _ask("Obsidian vault folder", cfg.get("vault_path", ""))
    while cfg["vault_path"] and not os.path.isdir(cfg["vault_path"]):
        cli_print("That folder does not exist — try again (or leave empty to skip).", "warning")
        cfg["vault_path"] = _ask("Obsidian vault folder", "")

    # -- telegram ------------------------------------------------------------
    print(paint("\n— Telegram (api.telegram.org credentials) —", C.BOLD))
    api_id = _ask("API ID", str(cfg.get("telegram_api_id", "") or ""))
    cfg["telegram_api_id"] = int(api_id) if api_id.isdigit() else 0
    cfg["telegram_api_hash"] = _ask("API hash", cfg.get("telegram_api_hash", ""))
    cfg["telegram_phone"] = _ask("Phone (+98…)", cfg.get("telegram_phone", ""))

    # -- bot -----------------------------------------------------------------
    print(paint("\n— Bot queue (your dedicated bot chat) —", C.BOLD))
    cfg["bot_username"] = _ask("Bot username (without @)", cfg.get("bot_username", "")).lstrip("@")

    # -- github --------------------------------------------------------------
    print(paint("\n— GitHub (higher API rate limit with a token) —", C.BOLD))
    print(paint("    create one at github.com/settings/tokens (public_repo read is enough)", C.DIM))
    cfg["github_token"] = _ask("GitHub token", cfg.get("github_token", ""))

    # -- llm -----------------------------------------------------------------
    print(paint("\n— LLM provider —", C.BOLD))
    # v0.13.0 — Phase 4 relabel: the option is any OpenAI-compatible
    # endpoint (llama.cpp server, vLLM, LM Studio, cloud). The CONFIG
    # VALUE stays 'cloud' — old configs load unchanged.
    # v0.15.0 — llama.cpp engine detection: llama-server is its OWN option
    # (3 / 'llamacpp') — detected like Ollama (server + model found
    # automatically) instead of hand-configured.
    _saved_prov = cfg.get("llm_provider", "ollama")
    prov = _ask("Provider: 1) ollama  2) OpenAI-compatible endpoint "
                "(llama.cpp, vLLM, LM Studio, cloud)  3) llama.cpp "
                "(local, auto-detected)",
                "llamacpp" if _saved_prov == "llamacpp"
                else ("ollama" if _saved_prov == "ollama" else "cloud"))
    if "3" in prov or "llama" in prov.lower():
        prov = "llamacpp"
    elif "2" in prov or "cloud" in prov.lower() or "openai" in prov.lower():
        prov = "cloud"
    else:
        prov = "ollama"
    cfg["llm_provider"] = prov
    if prov == "ollama":
        oll = cfg.get("ollama", {}) or {}
        cfg["ollama"] = {
            "base_url": _ask("Ollama base URL", oll.get("base_url", "http://127.0.0.1:11434")),
            "model": _ask("Ollama model", oll.get("model", "qwen2.5-coder:7b")),
        }
    elif prov == "llamacpp":
        from gitcurator.core import llm_client as _llm
        cfg["llamacpp_api_url"] = _ask(
            "llama.cpp server URL (llama-server default http://127.0.0.1:8080)",
            cfg.get("llamacpp_api_url", "http://127.0.0.1:8080/v1"))
        cfg["llamacpp_api_key"] = _ask(
            "API key (empty unless llama-server was started with --api-key)",
            cfg.get("llamacpp_api_key", ""))
        # "its model detected automatically": when the server is running,
        # its loaded model is offered as the default right here.
        _probe = _llm.probe_llamacpp(cfg["llamacpp_api_url"],
                                     cfg["llamacpp_api_key"])
        _default_model = ""
        if _probe.get("found"):
            cli_print(f"llama.cpp detected: {_probe['detail']}", "success")
            _default_model = _probe.get("model") or ""
        else:
            cli_print(f"llama.cpp not detected yet ({_probe.get('detail')}) "
                      "— the model is auto-detected when the server runs.",
                      "warning")
        cfg["llamacpp_model"] = _ask(
            "Model" + (f" (detected: {_default_model})" if _default_model else " (empty = auto-detect)"),
            cfg.get("llamacpp_model", "") or _default_model)
    else:
        cfg["cloud_api_url"] = _ask("Endpoint base URL (e.g. http://localhost:8080/v1 for llama.cpp)", cfg.get("cloud_api_url", "https://api.openai.com/v1"))
        cfg["cloud_api_key"] = _ask("API key (empty for local servers)", cfg.get("cloud_api_key", ""))
        cfg["cloud_model"] = _ask("Model name", cfg.get("cloud_model", "gpt-4o-mini"))

    # -- proxy ---------------------------------------------------------------
    print(paint("\n— Proxy (leave disabled if Telegram is reachable directly) —", C.BOLD))
    px = cfg.get("proxy", {}) or {}
    en = _ask("Enable proxy? (y/N)", "y" if px.get("enabled") else "n").lower().startswith("y")
    cfg["proxy"] = {
        "enabled": en,
        "type": _ask("Proxy type (socks5/http)", px.get("type", "socks5")) if en else px.get("type", "socks5"),
        "host": _ask("Proxy host", px.get("host", "127.0.0.1")) if en else px.get("host", "127.0.0.1"),
        "port": int(_ask("Proxy port", str(px.get("port", 10808))) or 10808) if en else px.get("port", 10808),
    }

    # -- 404 quarantine --------------------------------------------------------
    print(paint("\n— Deleted-repo policy —", C.BOLD))
    thr = _ask("Quarantine a repo as dead after N consecutive 404s (2–10)", str(cfg.get("notfound_strike_threshold", 3)))
    try:
        cfg["notfound_strike_threshold"] = max(2, min(10, int(thr)))
    except ValueError:
        cfg["notfound_strike_threshold"] = 3

    if not save_config(path, cfg):
        return 1

    # Summary card
    print()
    print(paint("┌─ saved ─────────────────────────────────────────┐", C.GREEN))
    print(paint("│ ", C.GREEN) + paint("config.json", C.BOLD) + paint(" written — credentials stay local", C.GREEN).ljust(64) + paint("│", C.GREEN))
    print(paint("└──────────────────────────────────────────────────┘", C.GREEN))
    print()

    # v0.09.3 — the Telegram verification-code login used to be GUI-only;
    # offer it right here so the setup wizard is a complete first-run
    # experience. Non-interactive stdin (pipes / CI) skips the offer.
    interactive = False
    try:
        interactive = bool(sys.stdin.isatty())
    except Exception:
        pass
    logged_in = False
    if interactive:
        ans = _ask("Log in to Telegram now (enter the verification code)? (Y/n)", "y").strip().lower()
        if ans.startswith("y"):
            print()
            logged_in, why = _run_login_flow(cfg)
            if not logged_in:
                cli_print(f"Login not completed ({why}).", "warning")
                cli_print("Retry any time with: " + paint("python main.py --cli --login", C.BOLD), "info")

    print()
    if logged_in:
        cli_print("Setup complete and logged in — double-click "
                  + paint("GitCurator-CLI.bat", C.BOLD) + " (Windows) or run:", "success")
    else:
        cli_print("Setup complete. Log in before the first run when convenient:", "success")
        print(paint("      python main.py --cli --login", C.CYAN))
        cli_print("then double-click " + paint("GitCurator-CLI.bat", C.BOLD) + " (Windows) or run:", "info")
    print(paint("      python main.py --cli --auto", C.CYAN))
    return 0


# ----------------------------------------------------------------------------
# --login : interactive Telegram login (v0.09.3 — the verification-code entry
# used to be reachable only through the GUI's dialogs)
# ----------------------------------------------------------------------------

def _run_login_flow(cfg: dict) -> tuple:
    """Connect to Telegram with the saved credentials (through the proxy).

    If the session is missing or expired, the worker sends the verification
    code to the configured phone and the user types it here (plus the 2FA
    password when the account has one). On success the session file is
    saved — every future run (CLI or GUI) skips the login. Runs the SAME
    subprocess engine as the GUI's Test Connectivity button. Returns
    ``(ok, error_summary)``.
    """
    try:
        _telegram_test_job = _gui_symbol("_telegram_test_job")
    except ImportError as exc:
        cli_print(f"{exc}", "error")
        return False, "pipeline engine unavailable"

    phone = cfg.get("telegram_phone", "")
    proxy = cfg.get("proxy", {}) or {}
    px_note = (f"{proxy.get('host', '?')}:{proxy.get('port', '?')} (on)"
               if proxy.get("enabled") else "direct")
    print(paint("Telegram login", C.BOLD)
          + paint(f"  ·  {phone}  ·  proxy {px_note}", C.DIM))
    print(paint("A code will be sent only if the saved session is missing/expired.", C.DIM))
    print()

    status = StatusLine()
    status.start("Connecting to Telegram")
    shim = _LogShim(status)
    result_box: dict = {}
    done = threading.Event()

    def _job():
        try:
            result_box["r"] = _telegram_test_job(
                str(cfg.get("telegram_api_id", 0) or 0),
                cfg.get("telegram_api_hash", ""),
                phone,
                proxy,
                shim,
                code_callback=_code_prompt,
            )
        except Exception as exc:                       # pragma: no cover
            result_box["r"] = {"success": False, "error": f"{type(exc).__name__}: {exc}"}
        finally:
            done.set()

    threading.Thread(target=_job, name="cli-telegram-login", daemon=True).start()
    while not done.is_set():
        while shim.queue:
            msg, lvl = shim.queue.popleft()
            status.log(msg, lvl)
        status.tick()
        time.sleep(0.09)
    while shim.queue:
        msg, lvl = shim.queue.popleft()
        status.log(msg, lvl)
    status.stop()
    print()

    result = result_box.get("r") or {"success": False, "error": "login thread died"}
    if result.get("success"):
        return True, ""
    return False, str(result.get("error", "unknown error"))


def cmd_login(args) -> int:
    """Interactive Telegram login from the terminal (the GUI equivalent:
    Test Connectivity / any fetch with an expired session)."""
    banner()
    path = _config_path(args.config)
    cfg = load_config(path)
    if cfg is None:
        cli_print(f"No config at {path}. Run " + paint("python main.py --cli --init", C.BOLD) + " first.", "error")
        return 1
    cfg["__config_path__"] = path

    missing = [name for name, val in [
        ("telegram_api_id", cfg.get("telegram_api_id")),
        ("telegram_api_hash", cfg.get("telegram_api_hash")),
        ("telegram_phone", cfg.get("telegram_phone")),
    ] if not val]
    if missing:
        cli_print("Missing settings: " + paint(", ".join(missing), C.RED), "error")
        cli_print("Fix with: " + paint("python main.py --cli --init", C.BOLD), "info")
        return 1

    ok, err = _run_login_flow(cfg)
    if ok:
        print(paint("┌─ telegram ────────────────────────────────────────┐", C.GREEN))
        print(paint("│ ", C.GREEN) + paint("session saved", C.BOLD)
              + paint(" — future runs skip the login", C.GREEN).ljust(66) + paint("│", C.GREEN))
        print(paint("└───────────────────────────────────────────────────┘", C.GREEN))
        print()
        cli_print("Next: double-click " + paint("GitCurator-CLI.bat", C.BOLD)
                  + " or run " + paint("python main.py --cli --auto", C.BOLD), "info")
        return 0

    cli_print(f"Login failed: {err}", "error")
    err_l = err.lower()
    if "flood" in err_l or "rate-limited" in err_l:
        cli_print("Telegram rate-limited the code request — wait the stated time, then rerun.", "info")
    elif any(k in err_l for k in ("code", "auth", "session", "password", "sign in", "login")):
        cli_print("Rerun " + paint("python main.py --cli --login", C.BOLD)
                  + " once your proxy node works — a fresh code will be sent.", "info")
    else:
        cli_print("Check the proxy (v2ray running? exit node reachable?) and rerun "
                  + paint("python main.py --cli --login", C.BOLD) + ".", "info")
    return 1


# ----------------------------------------------------------------------------
# --status : config + cache + 404-strike summary
# ----------------------------------------------------------------------------

def cmd_status(args) -> int:
    banner()
    path = _config_path(args.config)
    cfg = load_config(path)
    if cfg is None:
        cli_print(f"No config at {path} — run `--cli --init` first.", "warning")
        return 1

    vault = cfg.get("vault_path", "")
    print(paint("Configuration ", C.BOLD) + paint(f"({path})", C.DIM))
    print(rule())

    # v0.10.0 — Phase 1: the vault map. Same live status as the GUI's 📁
    # Vault page: websites folders that don't exist yet are "will be
    # created" (its pipeline creates them); the Manual vault is the
    # owner's to create.
    def _vault_cell(raw, kind):
        p = (raw or "").strip()
        if not p:
            return C.DIM + "(not set)" + C.RESET
        if os.path.isdir(p):
            return paint("  ✓ exists", C.GREEN)
        if kind == "websites":
            return paint("  ◌ will be created", C.YELLOW)
        return paint("  ✗ not found", C.RED)

    _pipes = cfg.get("pipelines") or {}
    _gh_on = bool(_pipes.get("github", True))
    _ws_on = bool(_pipes.get("websites", False))
    _pipelines_cell = (
        "github " + (paint("ON", C.GREEN) if _gh_on else paint("OFF", C.RED))
        + " · websites " + (paint("ON", C.GREEN) if _ws_on else paint("OFF", C.DIM)))

    rows = [
        ("Vault", vault + (paint("  ✓ exists", C.GREEN) if vault and os.path.isdir(vault) else paint("  ✗ missing", C.RED))),
        ("Websites vault", _vault_cell(cfg.get("website_vault_path", ""), "websites")),
        ("Manual vault", _vault_cell(cfg.get("manual_vault_path", ""), "manual")),
        ("Library mirror", (C.DIM + "tools/mirror_manual.py — dry-run first, then --apply" + C.RESET)
         if (cfg.get("manual_vault_path") or "").strip()
         else C.DIM + "(needs the Manual vault)" + C.RESET),
        ("Websites repo", (cfg.get("website_repo_name") or "").strip() or C.DIM + "(not set)" + C.RESET),
        ("Pipelines", _pipelines_cell),
        ("Telegram API ID", str(cfg.get("telegram_api_id", "") or C.DIM + "(not set)" + C.RESET)),
        ("Telegram hash", mask(str(cfg.get("telegram_api_hash", "")))),
        ("Phone", mask(str(cfg.get("telegram_phone", "")), keep=5)),
        ("Bot", "@" + cfg.get("bot_username", "") if cfg.get("bot_username") else C.DIM + "(not set)" + C.RESET),
        ("GitHub token", mask(str(cfg.get("github_token", "")))),
        ("LLM", cfg.get("llm_provider", "ollama") + (
            f" · {cfg.get('ollama', {}).get('model', '?')}" if cfg.get("llm_provider", "ollama") == "ollama"
            else (f" · {cfg.get('llamacpp_model', '') or '(auto-detect)'}" if cfg.get("llm_provider", "ollama") == "llamacpp"
                  else f" · {cfg.get('cloud_model', '?')}"))
         + C.DIM + f" · num_ctx={cfg.get('llm_num_ctx', 8192)}"
         + (f" · classify='{_m.get('classify')}'" if (_m := (cfg.get('models') or {})).get('classify') else "")
         + (f" · analyze='{_m.get('analyze')}'" if _m.get('analyze') else "") + C.RESET),
        ("Proxy", (f"{cfg['proxy'].get('type')}://{cfg['proxy'].get('host')}:{cfg['proxy'].get('port')}"
                   if (cfg.get("proxy") or {}).get("enabled") else C.DIM + "disabled" + C.RESET)),
        ("404 quarantine threshold", str(cfg.get("notfound_strike_threshold", 3)) + C.DIM + " (dead after N consecutive 404s)" + C.RESET),
    ]
    for key, val in rows:
        print(f"  {paint(key.ljust(22), C.BOLD)} {val}")

    # v0.10.0 — Phase 1: the taxonomy file the Phase 2 pipeline will parse.
    try:
        from gitcurator.constants import resolve_taxonomy_path
        _tax = resolve_taxonomy_path(cfg)
        _tax_cell = (paint("  ✓ exists", C.GREEN) if os.path.isfile(_tax)
                     else paint("  ✗ missing", C.RED))
        print(f"  {paint('Taxonomy'.ljust(22), C.BOLD)} {os.path.basename(_tax)}{_tax_cell}")
    except Exception:
        pass  # never let a status command fail on this

    # v0.07.2 — is the configured Ollama model actually installed?
    if cfg.get("llm_provider", "ollama") == "ollama":
        try:
            import ollama as _ol
            from gitcurator.core import llm_client as _llm
            oll = cfg.get("ollama") or {}
            base = oll.get("base_url", "http://127.0.0.1:11434")
            models = _llm.list_models_with_timeout(_ol.Client(host=base), 10)
            model = oll.get("model", "")
            if model and model not in models:
                cli_print(f"Configured model '{model}' is NOT installed "
                          f"({len(models)} model(s) available).", "warning")
                cli_print("  → the next batch run opens the model picker "
                          "(or change it via --init / config.json).", "info")
            elif models:
                print(f"  {paint('Ollama'.ljust(22), C.BOLD)} "
                      f"{len(models)} model(s) installed · '{model}' ready")
        except Exception:
            pass  # server down — the batch pre-flight reports it in detail
    elif cfg.get("llm_provider", "ollama") == "llamacpp":
        # v0.15.0 — llama.cpp engine detection: probe + (when the
        # configured URL is dead) detect: the RUNNING llama-server
        # process's ports (v0.15.1) + the common ports — then report the
        # server + its model. Never fails the status command.
        try:
            from gitcurator.core import llm_client as _llm
            url = cfg.get("llamacpp_api_url", "") or _llm.LLAMACPP_DEFAULT_BASE
            key = cfg.get("llamacpp_api_key", "") or ""
            probe = _llm.probe_llamacpp(url, key)
            if not probe.get("found"):
                probe = _llm.detect_llamacpp(key) or probe
            if probe.get("found"):
                model = (cfg.get("llamacpp_model", "")
                         or probe.get("model") or "(none loaded)")
                print(f"  {paint('llama.cpp'.ljust(22), C.BOLD)} "
                      f"{probe['detail']} · configured '{model}'")
            else:
                cli_print(f"llama.cpp server not detected ({probe.get('detail')}) — "
                          "start it with: llama-server -m <model>.gguf --port 8080",
                          "warning")
        except Exception:
            pass  # never let a status command fail on this
    print()

    # CacheDB stats (pulls in the GUI module — needs the same requirements.txt
    # as the GUI; degrades gracefully when PyQt6 is absent)
    try:
        CacheDB = _gui_symbol("CacheDB")
        cache = CacheDB()
        processed = len(cache.get_all_processed_urls())
        decommissioned = len(cache.get_all_decommissioned())
        failed = cache.get_failed_count()
        # v0.09 (merge): the unified 404 quarantine — attempts in progress
        # AND confirmed-dead rows (the v0.07 strike table is migrated into
        # decommissioned_repos.fail_count on first open).
        dead_link_threshold = _gui_symbol("dead_link_threshold")
        quarantined = cache.get_dead_urls(dead_link_threshold(cfg))
        all_rows = cache.get_quarantine_stats()
        cache.close()
        confirmed = {u for u, _r, _c, _a in quarantined}
        print(paint("Cache (cache.db)", C.BOLD))
        print(rule())
        print(f"  {paint('Processed repos'.ljust(22), C.BOLD)} {processed}")
        print(f"  {paint('Decommissioned (404)'.ljust(22), C.BOLD)} {decommissioned}")
        print(f"  {paint('Retry queue'.ljust(22), C.BOLD)} {failed}")
        print(f"  {paint('404 quarantine'.ljust(22), C.BOLD)} "
              f"{len(confirmed)} confirmed / {len(all_rows)} tracked")
        for url, reason, count, at in all_rows[:10]:
            flag = paint(" ⛔", C.RED) if url in confirmed else ""
            print(f"      {paint('•', C.YELLOW)} {url}  "
                  f"{paint(f'{count} attempt(s) · {str(at)[:16]}', C.DIM)}{flag}")
        if len(all_rows) > 10:
            print(paint(f"      … {len(all_rows) - 10} more", C.DIM))
    except Exception as exc:
        cli_print(f"Cache stats unavailable ({exc})", "warning")
    print()

    # v0.10.0 — Phase 1: the note-state baseline — the persistent per-note
    # record (path + fingerprint + category) that lets Phase 3 treat the
    # owner's folder moves as corrections instead of damage. Pure-stdlib
    # module; the db file is only opened when it already exists (a status
    # command must never create state).
    # v0.11.0 — Phase 2: the same block now shows the websites pipeline's
    # own tables (processed / retry queue / dismissed) the same way.
    try:
        from gitcurator.core import note_state as _note_state
        from gitcurator.constants import APP_DIR as _APP_DIR
        _db_file = os.path.join(_APP_DIR, "cache.db")
        if os.path.exists(_db_file):
            _ns = _note_state.NoteStateDB(_db_file)
            _gh_count = _ns.count(_note_state.VAULT_GITHUB)
            _web_count = _ns.count(_note_state.VAULT_WEBSITES)
            # v0.12.0 — Phase 3: corrections + dismissed counts.
            _gh_corr = _ns.correction_count(_note_state.VAULT_GITHUB)
            _web_corr = _ns.correction_count(_note_state.VAULT_WEBSITES)
            _gh_dis = _ns.dismissed_count(_note_state.VAULT_GITHUB)
            _web_dis = _ns.dismissed_count(_note_state.VAULT_WEBSITES)
            _ns.close()
            print(paint("Note state (moves-as-corrections record)", C.BOLD))
            print(rule())
            _extra = ("" if _gh_count else C.DIM
                      + "  (the first real run records it)" + C.RESET)
            print(f"  {paint('GitHub vault baseline'.ljust(22), C.BOLD)} "
                  f"{_gh_count} note(s) recorded{_extra}")
            print(f"  {paint('Websites vault record'.ljust(22), C.BOLD)} "
                  f"{_web_count} note(s) recorded")
            print(f"  {paint('Corrections applied'.ljust(22), C.BOLD)} "
                  f"{_gh_corr + _web_corr} "
                  f"(github {_gh_corr} · websites {_web_corr})")
            print(f"  {paint('Dismissed URLs'.ljust(22), C.BOLD)} "
                  f"{_gh_dis + _web_dis} "
                  f"(github {_gh_dis} · websites {_web_dis})")
            print()
    except Exception as exc:
        cli_print(f"Note state unavailable ({exc})", "warning")
    try:
        from gitcurator.core import website_pipeline as _wp
        from gitcurator.constants import APP_DIR as _APP_DIR
        _db_file = os.path.join(_APP_DIR, "cache.db")
        if os.path.exists(_db_file):
            _ws = _wp.WebsiteStateDB(_db_file)
            try:
                _proc = _ws.conn.execute(
                    "SELECT COUNT(*) FROM websites_processed").fetchone()[0]
                _retry = _ws.conn.execute(
                    "SELECT COUNT(*), "
                    "SUM(CASE WHEN attempts >= ? THEN 1 ELSE 0 END) "
                    "FROM website_retry_queue",
                    (_wp.MAX_FETCH_RETRIES,)).fetchone()
                _dismissed = _ws.conn.execute(
                    "SELECT COUNT(*) FROM dismissed_urls").fetchone()[0]
            finally:
                _ws.close()
            print(paint("Websites pipeline (cache.db)", C.BOLD))
            print(rule())
            print(f"  {paint('Processed websites'.ljust(22), C.BOLD)} {_proc}")
            print(f"  {paint('Fetch retry queue'.ljust(22), C.BOLD)} "
                  f"{_retry[0]} pending"
                  + (f" ({_retry[1]} exhausted, kept in _review)"
                     if _retry[1] else ""))
            print(f"  {paint('Dismissed URLs'.ljust(22), C.BOLD)} {_dismissed}")
            print()
    except Exception as exc:
        cli_print(f"Websites state unavailable ({exc})", "warning")
    return 0


# ----------------------------------------------------------------------------
# v0.17.0 — --test-connection : the same four-subsystem check as the GUI's
# Test Connection button (vaults · LLM · GitHub · Telegram live)
# ----------------------------------------------------------------------------

def _connection_live_telegram(cfg: dict) -> dict:
    """One live Telegram probe for --test-connection: the BOT QUEUE when a
    bot is configured (one subprocess proves both the account login AND
    the bot chat — the queue is read through the user's own session), else
    the Saved-Messages preview (account login only).

    Runs the job in a worker thread while the main thread animates the
    spinner — the fetch_bot_queue pattern, minus the vault filtering.
    Interactive login works: a missing session triggers the code prompt."""
    try:
        if str(cfg.get("bot_username", "") or "").strip():
            _job = _gui_symbol("_bot_queue_job")

            def _run(shim):
                return _job(str(cfg.get("telegram_api_id", 0) or 0),
                            cfg.get("telegram_api_hash", ""),
                            cfg.get("telegram_phone", ""),
                            cfg.get("proxy", {}) or {},
                            str(cfg.get("bot_username", "") or ""),
                            shim, code_callback=_code_prompt,
                            mark_read=False, min_id=0, vault_path=None)
        else:
            _job = _gui_symbol("_telegram_test_job")

            def _run(shim):
                return _job(str(cfg.get("telegram_api_id", 0) or 0),
                            cfg.get("telegram_api_hash", ""),
                            cfg.get("telegram_phone", ""),
                            cfg.get("proxy", {}) or {},
                            shim, code_callback=_code_prompt)
    except ImportError as exc:
        return {"success": False, "error": str(exc)}

    status = StatusLine()
    status.start("testing Telegram (live)")
    shim = _LogShim(status)
    result_box: dict = {}
    done = threading.Event()

    def _thread():
        try:
            result_box["r"] = _run(shim)
        except Exception as exc:                       # pragma: no cover
            result_box["r"] = {"success": False,
                               "error": f"{type(exc).__name__}: {exc}"}
        finally:
            done.set()

    threading.Thread(target=_thread, name="cli-connection-tg",
                     daemon=True).start()
    while not done.is_set():
        while shim.queue:
            msg, lvl = shim.queue.popleft()
            status.log(msg, lvl)
        status.tick()
        time.sleep(0.09)
    while shim.queue:
        msg, lvl = shim.queue.popleft()
        status.log(msg, lvl)
    status.stop()
    return result_box.get("r", {"success": False,
                                "error": "probe thread died"})


def cmd_test_connection(args) -> int:
    """v0.17.0 — 🔌 Test Connection: is everything up and ready?

    The CLI twin of the GUI's Test Connection button — checks and prints:
      [1/4] Vaults    — found + writable (ready to receive notes)
      [2/4] LLM       — the ACTIVE provider: cloud API / Ollama / llama.cpp
      [3/4] GitHub    — token valid + the backup repos ready
      [4/4] Telegram  — credentials/session/bot/proxy + a LIVE connection
    and ends with a one-line verdict. Exit code 0 when no error-level
    issue was found (warnings don't fail), 1 otherwise."""
    banner()
    path = _config_path(args.config)
    cfg = load_config(path)
    if cfg is None:
        cli_print(f"No config at {path} — run `--cli --init` first.", "warning")
        return 1
    from gitcurator.core import connection_check as cc

    print(paint("Test Connection — is everything up and ready?", C.BOLD))
    print(paint("vaults · LLM · GitHub · Telegram (live)", C.DIM))
    print()

    def _sec(title, idx, total):
        _section(f"[{idx}/{total}] {title}")

    def _res(r):
        _check_line(cc.CLI_LEVELS.get(r.get("level"), "info"),
                    r.get("name", "?"), r.get("detail", ""))

    sections = cc.run_local_checks(cfg, on_section=_sec, on_result=_res)

    # The live Telegram leg — right under section 4's local lines.
    creds_ok = all(str(cfg.get(k, "") or "").strip() for k in
                   ("telegram_api_id", "telegram_api_hash", "telegram_phone"))
    if creds_ok:
        mode = "bot" if str(cfg.get("bot_username", "") or "").strip() else "account"
        result = _connection_live_telegram(cfg)
        line = cc.telegram_live_result(result, mode)
        _check_line(cc.CLI_LEVELS.get(line["level"], "info"),
                    line["name"], line["detail"])
        if sections:
            sections[-1][1].append(line)
    else:
        skip = {
            "name": "Live connection",
            "level": cc.LEVEL_INFO,
            "detail": "skipped — credentials incomplete (run --init)",
        }
        _check_line("info", skip["name"], skip["detail"])
        if sections:
            sections[-1][1].append(skip)

    s = cc.summarize(sections)
    print()
    _check_line(cc.CLI_LEVELS.get(s["level"], "info"), "VERDICT",
                s["headline"])
    return 0 if s["level"] != cc.LEVEL_ERROR else 1


# ----------------------------------------------------------------------------
# v0.18.0 — --detect-llm {ollama,llamacpp} : the CLI twin of the GUI's
# "Detect & Set" quick-switch buttons (probe → model menu → set + SAVE)
# ----------------------------------------------------------------------------

def cmd_detect_llm(args) -> int:
    """⚡ Detect & Set LLM — the one-click fast lane between the two local
    engines the owner runs side by side ("sometimes I use llama.cpp model,
    sometimes ollama"):

      --detect-llm ollama     probe the Ollama server (config URL →
                              /api/tags), list its models, pick one (a
                              numbered menu when several are installed),
                              set provider + model + URL and SAVE.
      --detect-llm llamacpp   find the running llama-server (configured
                              URL first, then its PROCESS's listening
                              ports — any --port — then the common ports),
                              same pick + set + save.

    Exit 0 = provider set + saved; 1 = engine not found / no model /
    choice aborted. ``--yes`` auto-picks the current (or first) model
    without the menu — the non-interactive path."""
    banner()
    path = _config_path(args.config)
    cfg = load_config(path)
    if cfg is None:
        cli_print(f"No config at {path} — run `--cli --init` first.",
                  "warning")
        return 1
    provider = str(getattr(args, "detect_llm", "") or "").lower()
    label = "Ollama" if provider == "ollama" else "llama.cpp"
    icon = "🧠" if provider == "ollama" else "🦙"

    try:
        _job = _gui_symbol("_quick_detect_job")
    except ImportError as exc:
        cli_print(f"GUI modules unavailable: {exc}", "error")
        return 1

    print(paint(f"{icon} Detect & Set {label} — probing the local engine…",
                C.BOLD))
    print()
    status = StatusLine()
    status.start(f"detecting {label}")
    shim = _LogShim(status)
    result_box: dict = {}
    done = threading.Event()

    def _thread():
        try:
            result_box["r"] = _job(provider, cfg, shim)
        except Exception as exc:                       # pragma: no cover
            result_box["r"] = {"success": False,
                               "detail": f"{type(exc).__name__}: {exc}"}
        finally:
            done.set()

    threading.Thread(target=_thread, name="cli-detect-llm",
                     daemon=True).start()
    while not done.is_set():
        while shim.queue:
            msg, lvl = shim.queue.popleft()
            status.log(msg, lvl)
        status.tick()
        time.sleep(0.09)
    while shim.queue:
        msg, lvl = shim.queue.popleft()
        status.log(msg, lvl)
    status.stop()
    result = result_box.get("r") or {}

    if not result.get("success"):
        cli_print(f"Detect & Set {label}: the probe crashed — "
                  f"{result.get('detail', '?')}", "error")
        return 1
    if not result.get("found"):
        if provider == "ollama":
            cli_print(f"Ollama is not running at "
                      f"{result.get('base_url', '')} "
                      f"({result.get('detail', '')}).", "error")
            cli_print("Start the Ollama app (or `ollama serve`), then run "
                      "this again.", "info")
        else:
            cli_print(f"No llama.cpp server found "
                      f"({result.get('detail', '')}).", "error")
            cli_print("Start it with:  llama-server -m <model>.gguf "
                      "--port 8080", "info")
            cli_print("then run this again.", "info")
        return 1

    base = str(result.get("base_url", "") or "")
    models = [m for m in (result.get("models") or []) if m]
    where = f"at {base}"
    if provider == "llamacpp" and result.get("via") == "process":
        where += " (found via the running llama-server process)"
    print(paint(f"✅ {label} detected {where}", C.GREEN))
    if not models:
        if provider == "ollama":
            cli_print("Ollama is up but NO models are installed — pull one "
                      "with:  ollama pull <model>", "error")
        else:
            cli_print("llama.cpp is up but no model could be read — start "
                      "llama-server with -m <model>.gguf", "error")
        return 1

    # Pick the model: the only one directly; a numbered menu when several
    # (the CLI twin of the GUI's model-selection dialog).
    current = ""
    if provider == "ollama":
        oll = cfg.get("ollama") or {}
        current = str(oll.get("model", "") or "").strip() \
            if isinstance(oll, dict) else ""
    else:
        current = str(cfg.get("llamacpp_model", "") or "").strip()
    if len(models) == 1:
        choice = models[0]
        print(f"   model: {paint(choice, C.CYAN)} "
              "(the only one installed)")
    else:
        print(f"   {len(models)} models detected — pick one:")
        for i, name in enumerate(models, 1):
            mark = "  ← current" if name == current else ""
            print(f"   {paint(str(i), C.CYAN)}. {name}"
                  + (paint(mark, C.DIM) if mark else ""))
        if getattr(args, "yes", False):
            choice = current if current in models else models[0]
            print(paint(f"   --yes → using '{choice}'", C.DIM))
        else:
            default = current if current in models else models[0]
            try:
                raw = input(
                    paint("? ", C.CYAN)
                    + f"Model number [1-{len(models)}] "
                    + f"(Enter = {default}): ").strip()
            except EOFError:
                raw = ""
            if not raw:
                choice = default
            elif raw.isdigit() and 1 <= int(raw) <= len(models):
                choice = models[int(raw) - 1]
            else:
                cli_print(f"'{raw}' is not a valid choice — aborted, "
                          "nothing changed.", "warning")
                return 1

    # SET + SAVE (the same keys the GUI buttons write — one source of
    # truth, read by the next batch on either side).
    if provider == "ollama":
        cfg["llm_provider"] = "ollama"
        oll = cfg.get("ollama")
        if not isinstance(oll, dict):
            oll = {}
            cfg["ollama"] = oll
        oll["base_url"] = base
        oll["model"] = choice
    else:
        cfg["llm_provider"] = "llamacpp"
        cfg["llamacpp_api_url"] = base.rstrip("/") + "/v1"
        cfg["llamacpp_model"] = choice
    if not save_config(path, _clean_config_for_save(cfg)):
        return 1
    shown = base if provider == "ollama" else cfg["llamacpp_api_url"]
    print()
    print(paint(f"✅ LLM provider SET to {label} — {shown} · "
                f"model '{choice}'", C.GREEN))
    print(paint(f"   saved to {path} — the next batch uses it immediately.",
                C.DIM))
    return 0


def cmd_list_dead(args) -> int:
    """v0.09 (merge) — ported from the v0.08 companion CLI: list every
    CONFIRMED-dead link (attempts >= threshold) and every in-progress
    attempt row, then remind how to reset."""
    banner()
    path = _config_path(args.config)
    cfg = load_config(path) or {}
    try:
        CacheDB = _gui_symbol("CacheDB")
        cache = CacheDB()
        try:
            dead_link_threshold = _gui_symbol("dead_link_threshold")
            dead = cache.get_dead_urls(dead_link_threshold(cfg))
            all_rows = cache.get_quarantine_stats()
        finally:
            cache.close()
    except Exception as exc:
        cli_print(f"Quarantine unavailable ({exc})", "error")
        return 1
    thr = dead_link_threshold(cfg)
    print(paint(f"404 quarantine (threshold {thr})", C.BOLD))
    print(rule())
    if not all_rows:
        print(paint("  ✓ empty — no 404 attempts recorded.", C.GREEN))
        return 0
    confirmed = {u for u, _r, _c, _a in dead}
    for url, reason, count, at in all_rows:
        flag = paint("  ⛔ QUARANTINED", C.RED) if url in confirmed else ""
        print(f"  {paint('•', C.YELLOW)} {url}")
        print(f"      {paint(f'attempt {count}/{thr} · since {str(at)[:10]} · {reason}', C.DIM)}{flag}")
    print()
    print(paint("Reset with: ", C.DIM) + paint("--cli --reset-dead", C.CYAN))
    return 0


def cmd_reset_dead(args) -> int:
    """v0.09 (merge) — ported from the v0.08 companion CLI: clear the whole
    404 quarantine so every link gets a fresh set of attempts."""
    banner()
    try:
        CacheDB = _gui_symbol("CacheDB")
        cache = CacheDB()
        try:
            removed = cache.reset_dead_links()
        finally:
            cache.close()
    except Exception as exc:
        cli_print(f"Quarantine reset failed ({exc})", "error")
        return 1
    cli_print(f"♻️ 404 quarantine reset — {removed} link(s) will be processed again.", "success")
    return 0


def cmd_hand_delivery(args) -> int:
    """v0.48.0 — the fourth door on the CLI: list every retry-queued
    link whose last failure was a WALL (403 / bot defense / TLS
    fingerprint — the class the three machine doors could not open),
    queue them all for hand-delivery, and open each in the owner's
    REAL Chrome. The saved pages (Ctrl+S, 'Webpage, HTML Only', the
    suggested filename — see the README in the hand-delivered folder)
    are consumed as REAL fetches by the next --auto / --retry-failed
    run. Nothing is retired; the links keep waiting."""
    banner()
    path = _config_path(args.config)
    cfg = load_config(path) or {}
    vault = (cfg.get("website_vault_path") or "").strip()
    if not vault:
        cli_print("website_vault_path is not set — run --cli --init first.",
                  "warning")
        return 1
    from gitcurator.core import hand_delivery as _hd
    from gitcurator.core import website_pipeline as _wp
    from gitcurator.constants import APP_DIR as _APP_DIR
    _db_file = os.path.join(_APP_DIR, "cache.db")
    if not os.path.exists(_db_file):
        cli_print("No cache.db yet — nothing has failed (nothing is "
                  "walled).", "success")
        return 0
    state = _wp.WebsiteStateDB(_db_file)
    try:
        rows = _hd.walled_retry_rows(state)
    finally:
        state.close()
    print(paint("Walled links (the fourth door — your real Chrome)", C.BOLD))
    print(rule())
    if not rows:
        print(paint("  ✓ empty — no retry-queued link ends in a wall.",
                    C.GREEN))
        return 0
    for r in rows:
        wall = (r.get("wall") or "")[:100]
        att = r.get("attempts", 0)
        line = f"attempt {att} · the wall: {wall}"
        print(f"  {paint('•', C.YELLOW)} {r['url']}")
        print(f"      {paint(line, C.DIM)}")
    print()
    urls = [r["url"] for r in rows]
    walls = {r["url"]: r.get("wall") or "" for r in rows}
    report = _hd.enqueue_hand_delivery(
        vault, urls, walls=walls, log=lambda *_a, **_k: None,
        open_chrome=True)
    cli_print(f"🖐 {report['added']} link(s) queued for hand-delivery, "
              f"{report['opened']} opened in your browser.", "success")
    folder = _hd.hand_delivery_dir(vault)
    print(paint("Next: in Chrome, Ctrl+S → 'Webpage, HTML Only' → the "
                "suggested filename → this folder:", C.DIM))
    print(paint(f"  {folder}", C.CYAN))
    print(paint("The next --auto / --retry-failed run consumes every "
                "delivered page as a REAL fetch.", C.DIM))
    return 0


# ----------------------------------------------------------------------------
# --chrome-retry : v0.50.0 — the fifth door (the automatic tab retry)
# ----------------------------------------------------------------------------

def cmd_chrome_retry(args) -> int:
    """v0.50.0 — the fifth door on the CLI: every link waiting in the
    websites retry queue (the fetch-failed pile — each with its last
    error) is re-fetched AUTOMATICALLY through the owner's own real
    Chrome: the app launches a fresh Chrome session (a throwaway
    profile — the running window is never touched), starts one tab
    per link, waits for each page to load, and takes the live DOM.
    Every delivered page is then processed in the same run as a REAL
    fetch (the notes land, the retries clear). Links that still fail
    keep waiting; nothing is retired."""
    banner()
    path = _config_path(args.config)
    cfg = load_config(path) or {}
    vault = (cfg.get("website_vault_path") or "").strip()
    if not vault:
        cli_print("website_vault_path is not set — run --cli --init first.",
                  "warning")
        return 1
    if not (cfg.get("pipelines") or {}).get("websites", False):
        cli_print("The websites pipeline is off — turn it on first "
                  "(Settings → 📁 Vault, or config \"pipelines\").",
                  "warning")
        return 1
    from gitcurator.core import chrome_tabs as _ct
    from gitcurator.core import website_pipeline as _wp
    from gitcurator.constants import APP_DIR as _APP_DIR
    _db_file = os.path.join(_APP_DIR, "cache.db")
    links = []
    if not os.path.exists(_db_file):
        # v0.52.0 — no cache.db only means no MACHINE fetch has failed;
        # the 🖐 hand rows still ask for their Chrome scrape
        cli_print("No cache.db yet — no machine fetch has failed; the "
                  "hand rows still get their Chrome pass.", "info")
    else:
        state = _wp.WebsiteStateDB(_db_file)
        try:
            for row in state.all_retry_rows():
                url = (row.get("url") or "").strip()
                if not url.lower().startswith(("http://", "https://")) \
                        or _ct.is_loopback_url(url):
                    continue
                if state.is_dismissed(url):
                    continue  # the ladder's own verdict answered it
                links.append({"url": url,
                              "error": str(row.get("last_error") or "")})
        finally:
            state.close()
    # v0.52.0 — the 🖐 hand rows join the fifth door's list: the
    # owner's gesture (the # cell or the Status cell) asks for THIS
    # pass — the app scrapes them in his real Chrome, no Ctrl+S
    try:
        for hand_row in _wp.scan_master_hand_rows(vault):
            if not any(l["url"] == hand_row.get("url") for l in links):
                links.append({
                    "url": hand_row.get("url") or "",
                    "error": str(hand_row.get("wall")
                                 or "the 🖐 hand gesture in the master "
                                    "table")})
    except Exception as _e:
        cli_print(f"⚠️ Hand-row scan skipped: {_e}", "warning")
    # v0.57.0 — THE NOTE IS THE SUCCESS, the redo set: 🖐 hand links
    # whose page was already delivered (a consumed queue row or the
    # file in the folder) but whose note is NOT properly stored (the
    # false-success classes — half-fetched _review items, missing
    # files). They ride the re-run directly: no new Chrome tab (the
    # delivered page is the record, the pipeline re-reads it), the
    # LLM asked again for the proper, categorized note. Computed
    # BEFORE the empty check — a redo set alone is work too.
    redo_urls: list = []
    try:
        _redo_state = _wp.WebsiteStateDB(_db_file) \
            if os.path.exists(_db_file) else _wp.WebsiteStateDB()
        try:
            redo_urls = [r.get("url") for r in
                         _wp.scan_master_redo_rows(vault, state=_redo_state)
                         if r.get("url")]
        finally:
            _redo_state.close()
    except Exception as _e:
        cli_print(f"⚠️ Redo scan skipped: {_e}", "warning")
    print(paint("Failed links waiting for the fifth door "
                "(your real Chrome, one tab each)", C.BOLD))
    print(rule())
    if not links and not redo_urls:
        print(paint("  ✓ empty — no fetch-failed link is waiting, no "
                    "hand note needs a redo.", C.GREEN))
        return 0
    if redo_urls:
        print(paint("  🔁 redo — hand note(s) not properly stored:",
                    C.YELLOW))
        for u in redo_urls:
            print(f"      {paint(u, C.DIM)}")
    for l in links:
        err = (l.get("error") or "")[:100]
        print(f"  {paint('•', C.YELLOW)} {l['url']}")
        if err:
            print(f"      {paint(err, C.DIM)}")
    print()
    if getattr(args, "dry_run", False):
        report = _ct.deliver_pages_via_chrome(vault, links,
                                              log=lambda *_a, **_k: None,
                                              config=cfg)
        cli_print(f"🤖 Dry-run — Chrome is never launched; the fifth "
                  f"door rehearsed {len(links)} link(s), delivered "
                  f"{report.get('delivered', 0)}.", "success")
        return 0
    # v0.07.2 — same pre-flight as --auto / --retry-failed (the pages
    # are processed right after they are delivered, and the processing
    # needs the LLM).
    if not preflight_checks(cfg):
        return 1
    cfg["__config_path__"] = path
    report = _ct.deliver_pages_via_chrome(
        vault, links, log=cli_print, config=cfg)
    delivered = list(report.get("urls") or [])
    failed = int(report.get("failed") or 0)
    failed_links = list(report.get("failed_links") or [])
    first_errs = "; ".join(
        str(f.get("error") or "")[:90]
        for f in failed_links[:3] if f.get("error"))
    if not delivered and not redo_urls:
        cli_print(f"🤖 The fifth door delivered nothing — {failed} "
                  f"tab(s) failed"
                  + (f" ({first_errs})" if first_errs else "")
                  + "; the links keep waiting.", "warning")
        return 1
    _reprocess = delivered + [u for u in redo_urls
                              if u not in delivered]
    if delivered:
        cli_print(f"🤖 {len(delivered)} page(s) delivered from your real "
                  f"Chrome — processing them now through the Websites "
                  f"pipeline as real fetches…", "success")
    if redo_urls:
        cli_print(f"🔁 {len(redo_urls)} hand note(s) not properly stored — "
                  f"re-generating them from the delivered pages (the LLM "
                  f"writes the proper, categorized notes)", "info")
    # v0.57.0 — THE ROUTING FIX: the delivered pages are WEBSITE links
    # and ride the batch as ``non_github`` (the Websites pipeline),
    # never as GitHub urls — the old 'direct' call fed them to the
    # GitHub loop, which skipped every one as "non-GitHub URL" while
    # the log told its ✅ story and no note was ever written.
    rc = run_batch_visual(cfg, "direct", urls=[],
                          non_github=_reprocess,
                          bot_source=False, vault_arg=args.vault,
                          dry_run=bool(getattr(args, "dry_run", False)))
    if failed:
        cli_print(f"⚠️ {failed} tab(s) failed — those links keep "
                  f"waiting in the retry queue."
                  + (f" ({first_errs})" if first_errs else ""),
                  "warning")
    return rc


# ----------------------------------------------------------------------------
# --auto : fully automatic run (bot SYNC → process → seal → publish)
# ----------------------------------------------------------------------------

def cmd_auto(args) -> int:
    banner()
    path = _config_path(args.config)
    cfg = load_config(path)
    if cfg is None:
        cli_print(f"No config at {path}. Run " + paint("python main.py --cli --init", C.BOLD) + " first.", "error")
        return 1
    cfg["__config_path__"] = path

    if args.strikes:
        try:
            cfg["notfound_strike_threshold"] = max(2, int(args.strikes))
        except ValueError:
            pass
    if args.vault:
        cfg["vault_path"] = args.vault

    vault = cfg.get("vault_path", "")
    missing = [name for name, val in [
        ("vault path", vault and os.path.isdir(vault)),
        ("telegram_api_id", cfg.get("telegram_api_id")),
        ("telegram_api_hash", cfg.get("telegram_api_hash")),
        ("telegram_phone", cfg.get("telegram_phone")),
        ("bot_username", cfg.get("bot_username")),
    ] if not val]
    if missing:
        cli_print("Missing settings: " + paint(", ".join(missing), C.RED), "error")
        cli_print("Fix with: " + paint("python main.py --cli --init", C.BOLD), "info")
        return 1

    # v0.07.2 — pre-flight (token / proxy / Ollama model) BEFORE the fetch:
    # a missing model is caught and picked here, not 1 421 messages later.
    if not preflight_checks(cfg, card=True):
        return 1

    status = StatusLine()
    # v0.09.4 — Layer 3 of the anti-repeat mechanism (the GUI's "Process
    # New" semantics): fetch only bot messages NEWER than the last VERIFIED
    # batch. last_processed_msg_id is advanced ONLY after a fully-verified
    # run (see run_batch_visual's Phase 5 CLEAR), so a failed link keeps its
    # messages un-skipped and gets retried next run. 0 (first run) = full
    # history + vault-dedup classification, exactly like the GUI.
    last_id = 0
    try:
        last_id = int(cfg.get("last_processed_msg_id", 0) or 0)
    except (TypeError, ValueError):
        last_id = 0
    if last_id > 0:
        print(paint(f"Skipping bot messages up to ID {last_id} "
                    f"(last verified batch — set last_processed_msg_id to 0 in "
                    f"config.json to re-scan the full history).", C.DIM))
        print()
    print(paint("Phase 1 — SYNC: fetching undone items from the bot queue…", C.BOLD))
    print()
    status.start("Fetching bot queue")
    result = fetch_bot_queue(cfg, status, min_id=last_id)
    status.stop()
    print()

    if not result.get("success"):
        err = str(result.get("error", "unknown error"))
        cli_print(f"Fetch failed: {err}", "error")
        # v0.09.3 — session/auth failures now have a CLI remedy
        if any(k in err.lower() for k in ("code", "auth", "session", "password", "sign in", "login")):
            cli_print("Tip: run " + paint("python main.py --cli --login", C.BOLD)
                      + " to (re-)authenticate with a fresh verification code.", "info")
        return 1

    pending = result.get("pending_urls") or []
    in_vault = result.get("in_vault_count", 0)
    decomm = result.get("decommissioned_count", 0)
    non_github = result.get("non_github_urls", []) or []
    vault_index_count = result.get("vault_index_count")
    # v0.24.1 — Fix (websites never sync): the WEBSITES side of the queue.
    # "pending" now covers both pipelines — a caught-up GitHub vault no
    # longer masks unprocessed website links.
    pending_web = result.get("pending_website_urls") or []
    web_in_vault = int(result.get("websites_in_vault_count", 0) or 0)
    web_processed = int(result.get("websites_processed_count", 0) or 0)

    print(paint("Queue: ", C.BOLD)
          + paint(f"{len(pending)} repo(s) pending", C.GREEN if pending else C.DIM) + paint(" · ", C.DIM)
          + paint(f"{in_vault} already in vault", C.DIM) + paint(" · ", C.DIM)
          + paint(f"{decomm} decommissioned", C.DIM) + paint(" · ", C.DIM)
          + paint(f"{len(pending_web)} website(s) pending", C.GREEN if pending_web else C.DIM)
          + paint(f" · {len(non_github)} non-GitHub", C.DIM))
    if result.get("websites_vault_index_count") is not None:
        print(paint(f"Websites vault — {result['websites_vault_index_count']} note(s) indexed · "
                    f"{web_in_vault + web_processed} already done", C.DIM))
    elif non_github and result.get("websites_pipeline_off"):
        cli_print(f"{len(non_github)} non-GitHub link(s) are waiting as _inbox rows — "
                  "the Websites pipeline is OFF (config.json: pipelines.websites), "
                  "so they are never curated into the Websites vault.", "info")
    elif non_github and result.get("websites_no_vault"):
        cli_print("Websites pipeline is ON but website_vault_path is empty — "
                  "non-GitHub links stay in _inbox.", "info")

    # v0.09.4 — visibility + guard for the dedup layers. The vault index is
    # the app's ground truth for "already done" (a URL with a note in the
    # vault is processed, regardless of what any cache says). The queue
    # classification above silently degrades to "everything is pending"
    # when vault_path points at a directory that contains none of the
    # notes (wrong path in config.json — the GUI uses its live vault
    # dropdown, the CLI only has the file). That exact signature (pending
    # links, ZERO in vault, ZERO notes indexed) previously meant a full
    # re-processing run into the wrong vault. Show the counts and stop.
    cache_processed = -1  # -1 = unknown (PyQt6-stack unavailable)
    try:
        CacheDB = _gui_symbol("CacheDB")
        _cache = CacheDB()
        cache_processed = len(_cache.get_all_processed_urls())
        _cache.close()
    except Exception:
        pass
    if vault_index_count is not None or cache_processed >= 0:
        parts = []
        if vault_index_count is not None:
            parts.append(f"vault index: {vault_index_count} note(s)")
        if cache_processed >= 0:
            parts.append(f"cache: {cache_processed} processed repo(s)")
        print(paint("Dedup ground truth — " + " · ".join(parts), C.DIM))

    if (pending and in_vault == 0
            and (vault_index_count or 0) == 0 and cache_processed > 0):
        print()
        cli_print("This looks like a WRONG VAULT PATH, so I stopped before "
                  "processing:", "warning")
        cli_print(f"  · the vault at {vault} indexed 0 notes (no 'source:' "
                  "frontmatter found)", "info")
        cli_print(f"  · the cache knows {cache_processed} already-processed "
                  "repos — your notes live somewhere else", "info")
        cli_print("  · processing now would duplicate ~all "
                  f"{len(pending)} link(s) into that empty vault", "info")
        print()
        cli_print("Fix: point vault_path at the vault your notes are actually "
                  "written to —", "info")
        cli_print("  · re-run " + paint("python main.py --cli --init", C.BOLD)
                  + " (Enter keeps every current value; fix only the vault), or",
                  "info")
        cli_print("  · run once with " + paint("--vault C:\\path\\to\\your\\real\\vault", C.BOLD)
                  + " to test it, or", "info")
        cli_print("  · fix \"vault_path\" in config.json directly.", "info")
        if getattr(args, "yes", False):
            print()
            cli_print("(--yes mode never re-processes on this signature. If you "
                      "REALLY want a fresh vault re-run, start the same command "
                      "without --yes and confirm.)", "info")
            return 1
        try:
            ans = input(paint("? ", C.CYAN)
                        + f"Process all {len(pending)} links into this empty "
                          "vault anyway? " + paint("[y/N] ", C.CYAN)).strip().lower()
        except EOFError:
            ans = "n"
        if not ans.startswith("y"):
            cli_print("Cancelled — nothing was processed. Fix vault_path first.",
                      "warning")
            return 1

    if not pending and not pending_web:
        cli_print("All caught up — nothing to process. 🎉", "success")
        if non_github and not pending_web:
            # Websites are either done or off — the hint above already says
            # which; nothing else to do here.
            pass
        return 0

    _total_batch = len(pending) + len(pending_web)
    if getattr(args, "yes", False) is False and _total_batch > 10 and sys.stdin.isatty():
        ans = input(paint("? ", C.CYAN)
                    + f"{_total_batch} item(s) ready ({len(pending)} repo(s) + "
                    f"{len(pending_web)} website(s)) — process them all? "
                    + paint("[Y/n] ", C.CYAN)).strip().lower()
        if ans.startswith("n"):
            cli_print("Cancelled — nothing was processed.", "warning")
            return 0

    print()
    print(paint("Phase 2 — PROCESS: curating into the Obsidian vault…", C.BOLD))
    print()
    return run_batch_visual(
        cfg, "direct", urls=pending,
        bot_source=True, non_github=non_github,
        intake_duplicates=result.get("duplicates_removed", 0),
        raw_url_count=result.get("raw_url_count", len(pending) + len(non_github)),
        vault_arg=args.vault,
        bot_queue_max_id=int(result.get("max_message_id", 0) or 0),
        dry_run=bool(getattr(args, "dry_run", False)),  # v0.09.5 — Phase 0
    )


# ----------------------------------------------------------------------------
# Utility actions
# ----------------------------------------------------------------------------

def cmd_mark_read(args) -> int:
    """Clear the bot's unread queue (marks every bot message as read)."""
    banner()
    path = _config_path(args.config)
    cfg = load_config(path)
    if cfg is None:
        cli_print(f"No config at {path} — run --cli --init first.", "error")
        return 1
    if not (cfg.get("bot_username") and cfg.get("telegram_api_hash")):
        cli_print("bot_username / Telegram credentials missing in config.", "error")
        return 1

    try:
        _bot_queue_job = _gui_symbol("_bot_queue_job")
    except ImportError as exc:
        cli_print(f"{exc}", "error")
        return 1
    status = StatusLine()
    status.start("Marking bot queue as read")
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
        except Exception as exc:
            result_box["r"] = {"success": False, "error": str(exc)}
        finally:
            done.set()

    threading.Thread(target=_job, daemon=True).start()
    while not done.is_set():
        while shim.queue:
            status.log(*shim.queue.popleft())
        status.tick()
        time.sleep(0.09)
    while shim.queue:
        status.log(*shim.queue.popleft())
    status.stop()
    r = result_box.get("r", {})
    if r.get("success"):
        cli_print("Bot queue cleared — all messages marked as read.", "success")
        return 0
    cli_print(f"Failed: {r.get('error', 'unknown')}", "error")
    return 1


def cmd_retry_failed(args) -> int:
    """Reprocess every unresolved URL in the retry queue (failed_repos)."""
    banner()
    path = _config_path(args.config)
    cfg = load_config(path)
    if cfg is None:
        cli_print(f"No config at {path} — run --cli --init first.", "error")
        return 1
    cfg["__config_path__"] = path
    if args.strikes:
        try:
            cfg["notfound_strike_threshold"] = max(2, int(args.strikes))
        except ValueError:
            pass

    # v0.07.2 — same pre-flight as --auto (token / proxy / model picker).
    if not preflight_checks(cfg):
        return 1

    from gitcurator.gui.app import CacheDB
    cache = CacheDB()
    failed = [row[0] for row in cache.get_failed_urls()]
    cache.close()
    if not failed:
        cli_print("Retry queue is empty — nothing to reprocess.", "success")
        return 0

    cli_print(f"Retrying {len(failed)} failed URL(s)…", "info")
    return run_batch_visual(cfg, "direct", urls=failed, bot_source=False,
                            vault_arg=args.vault,
                            dry_run=bool(getattr(args, "dry_run", False)))  # v0.09.5 — Phase 0


# ----------------------------------------------------------------------------
# Manual batch modes (same inputs as the legacy headless CLI, now visual)
# ----------------------------------------------------------------------------

def cmd_manual_batch(args) -> int:
    banner()
    path = _config_path(args.config)
    cfg = load_config(path)
    if cfg is None:
        cli_print(f"No config at {path} — run --cli --init first.", "error")
        return 1
    cfg["__config_path__"] = path
    if args.strikes:
        try:
            cfg["notfound_strike_threshold"] = max(2, int(args.strikes))
        except ValueError:
            pass

    # v0.07.2 — same pre-flight as --auto (token / proxy / model picker).
    if not preflight_checks(cfg):
        return 1

    if args.import_file:
        if not os.path.isfile(args.import_file):
            cli_print(f"Import file not found: {args.import_file}", "error")
            return 1
        cli_print(f"Mode: import from {args.import_file}", "info")
        return run_batch_visual(cfg, "import", import_file=args.import_file, vault_arg=args.vault,
                                dry_run=bool(getattr(args, "dry_run", False)))  # v0.09.5 — Phase 0

    if args.single_id:
        cli_print(f"Mode: single Telegram message {args.single_id}", "info")
        from gitcurator.gui.app import _import_telethon_fetcher
        status = StatusLine()
        status.start(f"Fetching message {args.single_id}")
        shim = _LogShim(status)
        result_box: dict = {}
        done = threading.Event()

        def _job():
            try:
                fetch_sync, _err = _import_telethon_fetcher()
                if not fetch_sync:
                    result_box["r"] = {"success": False, "error": "telethon unavailable"}
                    return
                result_box["r"] = fetch_sync(
                    api_id=cfg.get("telegram_api_id", 0),
                    api_hash=cfg.get("telegram_api_hash", ""),
                    phone=cfg.get("telegram_phone", ""),
                    proxy=cfg.get("proxy", {}) or {},
                    from_id=args.single_id, to_id=args.single_id,
                    preview_only=False,
                )
            except Exception as exc:
                result_box["r"] = {"success": False, "error": str(exc)}
            finally:
                done.set()

        threading.Thread(target=_job, daemon=True).start()
        while not done.is_set():
            while shim.queue:
                status.log(*shim.queue.popleft())
            status.tick()
            time.sleep(0.09)
        status.stop()
        r = result_box.get("r", {})
        if not r.get("success"):
            cli_print(f"Fetch failed: {r.get('error')}", "error")
            return 1
        urls = r.get("urls", [])
        cli_print(f"Fetched {len(urls)} GitHub URL(s).", "success")
        if not urls:
            return 0
        return run_batch_visual(cfg, "direct", urls=urls, vault_arg=args.vault,
                                dry_run=bool(getattr(args, "dry_run", False)))  # v0.09.5 — Phase 0

    if args.from_id is not None and args.to_id is not None:
        cli_print(f"Mode: Telegram range {args.from_id} → {args.to_id}", "info")
        return run_batch_visual(cfg, "telegram_ids",
                                range_from=args.from_id, range_to=args.to_id,
                                vault_arg=args.vault,
                                dry_run=bool(getattr(args, "dry_run", False)))  # v0.09.5 — Phase 0

    if args.offset_start is not None and args.count is not None:
        cli_print(f"Mode: Telegram offset {args.offset_start} +{args.count}", "info")
        return run_batch_visual(cfg, "telegram_offset",
                                offset_start=args.offset_start, offset_count=args.count,
                                vault_arg=args.vault,
                                dry_run=bool(getattr(args, "dry_run", False)))  # v0.09.5 — Phase 0

    cli_print("Specify a mode: --auto, --retry-failed, --import-file, --single-id, "
              "--from-id/--to-id, --offset-start/--count (see --help).", "warning")
    return 1


# ----------------------------------------------------------------------------
# Argument parsing + entry point
# ----------------------------------------------------------------------------

def build_parser():
    import argparse
    p = argparse.ArgumentParser(
        prog="gitcurator-cli",
        description="GitCurator visual CLI — colored, animated, fully automatic curation.",
        epilog="Tip: GitCurator-CLI.bat runs --auto with one double-click.",
    )
    p.add_argument("--init", action="store_true",
                   help="first-run wizard: ask for credentials and save config.json locally")
    p.add_argument("--login", action="store_true",
                   help="Telegram login: connect, enter the verification code (and 2FA password), save the session")
    p.add_argument("--auto", action="store_true",
                   help="fully automatic run: bot SYNC → process → seal → publish")
    p.add_argument("--list-dead", action="store_true",
                   help="list the 404 quarantine (confirmed-dead + attempts)")
    p.add_argument("--reset-dead", action="store_true",
                   help="clear the 404 quarantine (every link gets fresh attempts)")
    p.add_argument("--hand-delivery", action="store_true",
                   help="v0.48.0 — the fourth door: list every walled link "
                        "(403/bot-defense/TLS), queue it for hand-delivery "
                        "and open it in your real Chrome; the saved page "
                        "becomes a real fetch on the next run")
    p.add_argument("--chrome-retry", action="store_true",
                   help="v0.50.0 — the fifth door: re-fetch every failed "
                        "link AUTOMATICALLY through your own real Chrome "
                        "(a fresh session, one tab per link, the live DOM "
                        "taken as the content) and process the delivered "
                        "pages as real fetches in the same run")
    p.add_argument("--status", action="store_true",
                   help="show config summary + cache/retry/404-quarantine stats")
    p.add_argument("--test-connection", action="store_true",
                   help="v0.17.0 — check that everything is up and ready: vaults "
                        "(found+writable) · Telegram (bot + account login, live) · "
                        "LLM (API/ollama/llama.cpp) · GitHub (token + backup repos)")
    p.add_argument("--detect-llm", choices=("ollama", "llamacpp"),
                   metavar="{ollama,llamacpp}",
                   help="v0.18.0 — Detect & Set a local LLM engine: probe it, "
                        "pick the model (menu when several), set provider + "
                        "model + URL and save. The fast lane the GUI's two "
                        "Detect & Set buttons use")
    p.add_argument("--retry-failed", action="store_true",
                   help="reprocess every URL in the retry queue")
    p.add_argument("--mark-read", action="store_true",
                   help="clear the bot's unread queue (mark all as read)")
    p.add_argument("--from-id", type=int, help="Telegram range mode: start message ID")
    p.add_argument("--to-id", type=int, help="Telegram range mode: end message ID")
    p.add_argument("--offset-start", type=int, help="Telegram offset mode: start ID")
    p.add_argument("--count", type=int, help="Telegram offset mode: message count")
    p.add_argument("--import-file", type=str,
                   help="batch-import from a .txt/.md file, one address per "
                        "line (GitHub repos AND websites; Markdown links, "
                        "bullets and trailing notes are fine)")
    p.add_argument("--single-id", type=int, help="fetch a single Telegram message by ID")
    p.add_argument("--vault", type=str, help="override the vault path for this run")
    p.add_argument("--config", type=str, help="config file path (default: app/config.json)")
    p.add_argument("--strikes", type=int, metavar="N",
                   help="override notfound_strike_threshold for this run (min 2)")
    p.add_argument("--yes", "-y", action="store_true",
                   help="skip the >10-repos confirmation in --auto")
    p.add_argument("--dry-run", action="store_true",
                   help="v0.09.5 — safety net: log every vault write instead of "
                        "performing it (also skips sealing, publishing, "
                        "marking the bot queue read, and state updates). "
                        "Combine with any batch mode: --auto, --import-file, "
                        "--retry-failed, ranges")
    p.add_argument("--no-color", action="store_true", help="disable colored output")
    return p


def cli_main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    C.enable(force=False)
    if args.no_color:
        C._ENABLED = False
        for name in ("RESET", "BOLD", "DIM", "ITALIC", "RED", "GREEN", "YELLOW",
                     "CYAN", "MAGENTA", "BLUE", "WHITE", "BG_RED", "BG_GREEN"):
            setattr(C, name, "")

    if args.init:
        return cmd_init(args)
    if args.login:
        return cmd_login(args)
    if args.status:
        return cmd_status(args)
    if args.test_connection:
        return cmd_test_connection(args)
    if getattr(args, "detect_llm", None):
        return cmd_detect_llm(args)
    if args.list_dead:
        return cmd_list_dead(args)
    if args.reset_dead:
        return cmd_reset_dead(args)
    if getattr(args, "hand_delivery", None):
        return cmd_hand_delivery(args)
    if getattr(args, "chrome_retry", None):
        return cmd_chrome_retry(args)
    if args.mark_read:
        return cmd_mark_read(args)

    # Batch modes (share the config + strike plumbing)
    if args.auto:
        return cmd_auto(args)
    if args.retry_failed:
        return cmd_retry_failed(args)
    if any([args.import_file, args.single_id is not None,
            args.from_id is not None, args.to_id is not None,
            args.offset_start is not None, args.count is not None]):
        return cmd_manual_batch(args)

    # Nothing selected — friendly help
    banner()
    print(paint("Nothing to do — pick a mode:\n", C.BOLD))
    print(f"  {paint('--init', C.CYAN):24} first-run wizard (saves credentials locally)")
    print(f"  {paint('--login', C.CYAN):24} Telegram login: enter the verification code")
    print(f"  {paint('--auto', C.CYAN):24} fully automatic run (SYNC → PROCESS → SEAL)")
    print(f"  {paint('--status', C.CYAN):24} vault map + config + cache + note-state summary")
    print(f"  {paint('--test-connection', C.CYAN):24} vaults · LLM · GitHub · Telegram — is everything up and ready?")
    print(f"  {paint('--detect-llm X', C.CYAN):24} Detect & Set a local engine (ollama | llamacpp) — probe, pick, save")
    print(f"  {paint('--list-dead', C.CYAN):24} list the 404 quarantine (dead links)")
    print(f"  {paint('--reset-dead', C.CYAN):24} clear the 404 quarantine")
    print(f"  {paint('--hand-delivery', C.CYAN):24} the fourth door — open walled links in your real Chrome")
    print(f"  {paint('--retry-failed', C.CYAN):24} reprocess the retry queue")
    print(f"  {paint('--import-file F', C.CYAN):24} curate URLs from a text file")
    print(f"  {paint('--from-id A --to-id B', C.CYAN):24} curate a Telegram ID range")
    print(f"  {paint('--offset-start A --count N', C.CYAN):24} curate N messages from an offset")
    print(f"  {paint('--single-id N', C.CYAN):24} curate one Telegram message")
    print(f"  {paint('--mark-read', C.CYAN):24} clear the bot's unread queue")
    print()
    print(paint("Windows tip: double-click GitCurator-CLI.bat for the automatic run.", C.DIM))
    return 0



if __name__ == "__main__":
    sys.exit(cli_main())
