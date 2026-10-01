"""
gitcurator.core.connection_check — the unified "Test Connection" prober.

Owner request (v0.17.0): one button in the GUI (and the same thing in the
CLI) that tests — and shows IN THE LOG — that everything is up and ready:

  1. Vaults     — found and ready to be input to (exists + WRITABLE)
  2. Telegram   — connected: the bot queue readable + the account login
  3. LLM        — the ACTIVE provider works (cloud API / Ollama / llama.cpp)
  4. GitHub     — the repos are ready (token working)

Design rules (house style):
  * NEVER raises — every check returns result dicts and survives hostile
    configs, dead ports and missing folders.
  * Result shape: ``{'name': str, 'level': 'ok'|'warn'|'error'|'info',
    'detail': str}`` — one log line each.
  * Loopback HTTP never rides the system proxy (the v0.15.1 rule, reused
    from llm_client); api.github.com is NOT loopback, so it honors the
    owner's proxy/VPN exactly like the pipeline does.
  * The LIVE Telegram connection test needs the Telethon subprocess
    (session.session is single-user) — that leg belongs to the GUI/CLI
    orchestrators, not this module. :func:`telegram_live_result` maps the
    subprocess result dict into one result line for them.

Pure stdlib + gitcurator.core.llm_client. Importable from the GUI, the
CLI and tests without PyQt.
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
import time
import urllib.error
import urllib.request
from typing import Callable, Dict, List, Optional

from gitcurator.constants import APP_DIR, EXPECTED_WORKER_VERSION
from gitcurator.core import llm_client as _llm

# ---------------------------------------------------------------------------
# Levels + rendering
# ---------------------------------------------------------------------------

LEVEL_OK = "ok"
LEVEL_WARN = "warn"
LEVEL_ERROR = "error"
LEVEL_INFO = "info"

ICONS = {
    LEVEL_OK: "✅",
    LEVEL_WARN: "⚠️",
    LEVEL_ERROR: "❌",
    LEVEL_INFO: "ℹ️",
}

# GUI MainWindow.log_message levels
GUI_LEVELS = {
    LEVEL_OK: "success",
    LEVEL_WARN: "warning",
    LEVEL_ERROR: "error",
    LEVEL_INFO: "info",
}

# CLI cli_print / _check_line levels
CLI_LEVELS = {
    LEVEL_OK: "success",
    LEVEL_WARN: "warning",
    LEVEL_ERROR: "error",
    LEVEL_INFO: "info",
}

GITHUB_API_BASE = "https://api.github.com"


def _result(name: str, level: str, detail: str) -> Dict[str, str]:
    return {"name": name, "level": level, "detail": detail}


def render_line(result: Dict[str, str]) -> str:
    """One human line for a result dict: '✅ Name — detail'."""
    icon = ICONS.get(result.get("level", LEVEL_INFO), "•")
    return f"{icon} {result.get('name', '?')} — {result.get('detail', '')}"


# ---------------------------------------------------------------------------
# Small network helper (proxy-aware, loopback-direct)
# ---------------------------------------------------------------------------

def _http_get_json(url: str, timeout_s: float = 8.0) -> dict:
    """GET ``url`` → parsed JSON dict. Loopback targets bypass the system
    proxy (a VPN client can only break them); everything else honors it.
    Raises on any failure — callers in this module always catch."""
    req = urllib.request.Request(
        url, headers={"User-Agent": "GitCurator-connection-check"})
    if _llm._is_loopback_url(url):
        resp = _llm._urlopen_direct(req, timeout_s)
    else:
        resp = urllib.request.urlopen(req, timeout=timeout_s)
    try:
        raw = resp.read().decode("utf-8") or "{}"
    finally:
        try:
            resp.close()
        except Exception:
            pass
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("response is not a JSON object")
    return payload


# ---------------------------------------------------------------------------
# 1) Vaults — found AND writable (the first writability probe in the repo)
# ---------------------------------------------------------------------------

def vault_writable(path: str):
    """Write + delete a temp file inside ``path``. Returns ``(ok, detail)``
    — ``detail`` carries the exception on failure. Never raises, never
    leaves a file behind."""
    fd = None
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=".gc-write-test-", dir=path)
        os.write(fd, b"ok")
        os.close(fd)
        fd = None
        os.unlink(tmp)
        tmp = None
        return True, ""
    except Exception as exc:
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
        if tmp and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except Exception:
                pass
        return False, f"{type(exc).__name__}: {exc}"


def check_vault(path, kind: str, label: str) -> Dict[str, str]:
    """One vault row. ``kind``: 'github' (must exist), 'websites' (may be
    created by its pipeline), 'manual' (the owner creates it — the mirror
    never does)."""
    p = str(path or "").strip()
    if not p:
        if kind == "github":
            return _result(label, LEVEL_ERROR,
                           "not set — pick it in Settings ▸ Vault "
                           "(the app writes its notes here)")
        if kind == "websites":
            return _result(label, LEVEL_WARN,
                           "not set — the websites pipeline needs it "
                           "(Settings ▸ Vault)")
        return _result(label, LEVEL_INFO,
                       "not set — the Library mirror is optional "
                       "(Settings ▸ Vault)")
    if not os.path.isdir(p):
        if kind == "websites":
            parent = os.path.dirname(p.rstrip("/\\").rstrip("\\")) or os.curdir
            if os.path.isdir(parent):
                ok_w, why = vault_writable(parent)
                if ok_w:
                    return _result(label, LEVEL_INFO,
                                   f"not on disk yet — created by the pipeline "
                                   f"on its first run ({p})")
                return _result(label, LEVEL_ERROR,
                               f"cannot be created — the parent folder is not "
                               f"writable ({why})")
            return _result(label, LEVEL_ERROR,
                           f"cannot be created — parent folder missing ({parent})")
        if kind == "manual":
            return _result(label, LEVEL_WARN,
                           f"not found — create the vault in Obsidian yourself; "
                           f"the mirror never creates it ({p})")
        return _result(label, LEVEL_ERROR, f"missing on disk ({p})")
    ok_w, why = vault_writable(p)
    obs = " · Obsidian vault" if os.path.isdir(os.path.join(p, ".obsidian")) else ""
    if ok_w:
        return _result(label, LEVEL_OK,
                       f"found · writable — ready to receive notes{obs} ({p})")
    return _result(label, LEVEL_ERROR,
                   f"found but NOT writable — the app cannot save notes here "
                   f"({why})")


def check_vaults(config: dict) -> List[Dict[str, str]]:
    """The vault map as check rows: GitHub vault (always), Websites vault
    (checked when its pipeline is ON or a path is set), Manual vault."""
    out = [check_vault(config.get("vault_path"), "github", "GitHub vault")]
    pipes = config.get("pipelines") or {}
    ws_on = bool(pipes.get("websites", False))
    wp = str(config.get("website_vault_path", "") or "").strip()
    if ws_on:
        out.append(check_vault(wp, "websites", "Websites vault"))
    elif wp:
        out.append(_result("Websites vault", LEVEL_INFO,
                           f"set ({wp}) · websites pipeline is OFF — not used "
                           f"until you turn it on"))
    else:
        out.append(_result("Websites vault", LEVEL_INFO,
                           "not set · websites pipeline is OFF — nothing to check"))
    mp = str(config.get("manual_vault_path", "") or "").strip()
    if mp:
        out.append(check_vault(mp, "manual", "Manual vault"))
    else:
        out.append(_result("Manual vault", LEVEL_INFO,
                           "not set — the Library mirror is optional"))
    return out


# ---------------------------------------------------------------------------
# 2) LLM — the ACTIVE provider (cloud API / Ollama / llama.cpp)
# ---------------------------------------------------------------------------

def check_ollama(base_url: str, configured_model: str = "") -> Dict[str, str]:
    base = str(base_url or "http://127.0.0.1:11434").strip()
    url = base.rstrip("/") + "/api/tags"
    try:
        raw = _http_get_json(url, timeout_s=6.0)
    except Exception as exc:
        return _result("Ollama", LEVEL_ERROR,
                       f"not reachable at {base} ({type(exc).__name__}) — "
                       f"start the Ollama app, then retry")
    models = []
    try:
        for m in raw.get("models") or []:
            name = (m.get("name") or m.get("model") or "").strip()
            if name:
                models.append(name)
    except Exception:
        models = []
    if not models:
        return _result("Ollama", LEVEL_ERROR,
                       f"up @ {base} but NO models installed — run "
                       f"`ollama pull <model>` first")
    detail = f"up @ {base} · {len(models)} model(s)"
    cfg_model = str(configured_model or "").strip()
    if cfg_model:
        if cfg_model in models:
            return _result("Ollama", LEVEL_OK, detail + f" · '{cfg_model}' ready")
        return _result("Ollama", LEVEL_WARN,
                       detail + f" — configured '{cfg_model}' is NOT pulled "
                       f"(the app will prompt at run time)")
    return _result("Ollama", LEVEL_OK,
                   detail + " · no model configured (picked at run time)")


def check_llamacpp(config: dict) -> Dict[str, str]:
    url = str(config.get("llamacpp_api_url", "") or "").strip() \
        or _llm.LLAMACPP_DEFAULT_BASE
    key = str(config.get("llamacpp_api_key", "") or "")
    probe = _llm.probe_llamacpp(url, key)
    if not probe.get("found"):
        probe = _llm.detect_llamacpp(key) or probe
    if not probe.get("found"):
        return _result("llama.cpp", LEVEL_ERROR,
                       f"server not detected ({probe.get('detail') or 'no answer'}) "
                       f"— start it with: llama-server -m <model>.gguf --port 8080")
    base = probe.get("base_url") or url
    loading = " · still loading" if probe.get("ready") is False else ""
    model = _llm.resolve_llamacpp_model(config, probe.get("models"),
                                        probe.get("props_model"))
    if not model:
        return _result("llama.cpp", LEVEL_ERROR,
                       f"up @ {base} but no model loaded — start llama-server "
                       f"with -m <model>.gguf")
    return _result("llama.cpp", LEVEL_OK, f"up @ {base} · model '{model}'{loading}")


def check_cloud(config: dict) -> Dict[str, str]:
    url = str(config.get("cloud_api_url", "") or "").strip()
    key = str(config.get("cloud_api_key", "") or "")
    model = str(config.get("cloud_model", "") or "")
    if not url:
        return _result("Cloud API", LEVEL_ERROR,
                       "no API URL configured — Settings ▸ LLM")
    # v0.23.0 — preflight_cloud routes by URL: api.anthropic.com → the
    # Claude /v1/models preflight, everything else → OpenAI-compatible.
    flavor = ("Claude" if _llm.is_anthropic_url(url)
              else "OpenAI-compatible")
    try:
        endpoint_ok, message, model_listed = _llm.preflight_cloud(
            url, key, model, 15)
    except Exception as exc:  # preflight is never-a-gate; belt & braces
        return _result("Cloud API", LEVEL_ERROR,
                       f"{url} — check failed ({type(exc).__name__}: {exc})")
    if not endpoint_ok:
        return _result("Cloud API", LEVEL_ERROR, f"{url} ({flavor}) — {message}")
    detail = f"{url} ({flavor}) — {message}"
    if model_listed is True:
        return _result("Cloud API", LEVEL_OK, detail + f" · '{model}' listed")
    if model_listed is False:
        return _result("Cloud API", LEVEL_WARN,
                       detail + f" — model '{model}' NOT in the list "
                       f"(trying anyway)")
    return _result("Cloud API", LEVEL_OK,
                   detail + f" · model '{model}' (model list hidden — fine)")


def check_llm(config: dict) -> List[Dict[str, str]]:
    """The ACTIVE provider gets the live probe; a down provider earns one
    hint line pointing at the alternatives."""
    provider = str(config.get("llm_provider", "ollama") or "ollama")
    if provider == "llamacpp":
        out = [check_llamacpp(config)]
    elif provider == "cloud":
        out = [check_cloud(config)]
    else:
        oll = config.get("ollama") or {}
        out = [check_ollama(oll.get("base_url", "http://127.0.0.1:11434"),
                            oll.get("model", ""))]
    if out[0]["level"] == LEVEL_ERROR:
        out.append(_result("Hint", LEVEL_INFO,
                           "the active provider is down — start it, or switch "
                           "provider in Settings ▸ LLM (Ollama / cloud API / "
                           "llama.cpp)"))
    return out


# ---------------------------------------------------------------------------
# 3) GitHub — token valid + the vault backup repos ready
# ---------------------------------------------------------------------------

def _github_get(path: str, token: str, api_base: str = GITHUB_API_BASE,
                timeout_s: float = 15.0):
    """GET ``api_base + path`` with the Bearer token. Returns
    ``(status, payload, err)`` — status 0 means transport failure. Never
    raises. api.github.com is not loopback → the system proxy is honored,
    exactly like the pipeline's own calls."""
    url = api_base.rstrip("/") + path
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "GitCurator-connection-check",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read().decode("utf-8") or "{}"
            try:
                return resp.status, json.loads(body), None
            except Exception:
                return resp.status, None, "bad JSON from server"
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8") or ""
        except Exception:
            pass
        try:
            payload = json.loads(body)
        except Exception:
            payload = None
        return exc.code, payload, None
    except Exception as exc:
        return 0, None, f"{type(exc).__name__}: {exc}"


def check_github(config: dict, api_base: str = GITHUB_API_BASE,
                 timeout_s: float = 15.0) -> List[Dict[str, str]]:
    out = []
    token = str(config.get("github_token", "") or "").strip()
    if not token:
        out.append(_result("GitHub token", LEVEL_WARN,
                           "not set — the app runs unauthenticated (60 req/h; "
                           "vault seals need a token)"))
        return out
    status, payload, err = _github_get("/user", token, api_base, timeout_s)
    if status == 0:
        out.append(_result("GitHub token", LEVEL_ERROR,
                           f"GitHub unreachable ({err}) — check internet / proxy"))
        return out
    if status == 401:
        out.append(_result("GitHub token", LEVEL_ERROR,
                           "REJECTED (401 Bad credentials) — make a fresh token "
                           "at github.com/settings/tokens (classic, 'repo' scope)"))
        return out
    if status != 200 or not isinstance(payload, dict):
        out.append(_result("GitHub token", LEVEL_ERROR,
                           f"unexpected response (HTTP {status})"))
        return out
    login = str(payload.get("login") or "?")
    out.append(_result("GitHub token", LEVEL_OK,
                       f"valid — account {login} (5000 req/h)"))
    # The vault backup repos (VaultSeal creates a missing one on the next
    # seal, so 404 is information, not failure).
    seal = config.get("vaultseal") or {}
    repos = []
    if bool(seal.get("enabled", True)):
        repo = str(seal.get("repo_name", "") or "").strip()
        if repo:
            repos.append(("Vault repo", repo))
    else:
        out.append(_result("Vault seal", LEVEL_INFO,
                           "disabled — the GitHub vault is not pushed "
                           "(Settings ▸ Backup)"))
    pipes = config.get("pipelines") or {}
    if bool(pipes.get("websites", False)):
        repo = str(config.get("website_repo_name", "") or "").strip()
        if repo:
            repos.append(("Websites repo", repo))
    for label, repo in repos:
        s2, p2, e2 = _github_get(f"/repos/{login}/{repo}", token,
                                 api_base, timeout_s)
        if s2 == 200 and isinstance(p2, dict):
            if p2.get("private", True):
                out.append(_result(label, LEVEL_OK, f"{repo} ready (private)"))
            else:
                out.append(_result(label, LEVEL_WARN,
                                   f"{repo} is PUBLIC — vault backups should be "
                                   f"private (github.com/{login}/{repo}/settings)"))
        elif s2 == 404:
            out.append(_result(label, LEVEL_INFO,
                               f"{repo} not on GitHub yet — created automatically "
                               f"on the next seal"))
        else:
            out.append(_result(label, LEVEL_WARN,
                               f"{repo} could not be checked (HTTP {s2}"
                               + (f", {e2}" if s2 == 0 and e2 else "") + ")"))
    return out


# ---------------------------------------------------------------------------
# 4) Telegram — local config + session + bot + proxy (the LIVE leg is the
#    orchestrators' job; this maps its result into one line)
# ---------------------------------------------------------------------------

def telegram_live_result(result, mode: str) -> Dict[str, str]:
    """Map a ``_telegram_test_job`` / ``_bot_queue_job`` subprocess result
    into one check result. ``mode``: 'bot' (queue fetch — proves account
    login AND the bot chat) or 'account' (Saved-Messages preview — account
    login only). Never raises."""
    if not isinstance(result, dict):
        return _result("Live connection", LEVEL_ERROR,
                       f"unexpected worker result ({type(result).__name__})")
    if result.get("success"):
        if mode == "bot":
            n = len(result.get("urls") or [])
            max_id = result.get("max_message_id")
            extra = f" · newest message id {max_id}" if max_id else ""
            return _result("Live connection", LEVEL_OK,
                           f"connected — account login OK · bot queue readable "
                           f"({n} link(s) waiting{extra})")
        preview = result.get("preview") or {}
        total = preview.get("total_count", 0)
        return _result("Live connection", LEVEL_OK,
                       f"connected — account login OK (Saved Messages "
                       f"reachable, {total} message(s))")
    err = str(result.get("error") or "unknown error")
    low = err.lower()
    if "session" in low and ("not found" in low or "run 'python test.py'" in low):
        return _result("Live connection", LEVEL_ERROR,
                       "account not logged in — no session file; log in first "
                       "(GUI: Test Telegram & GitHub · CLI: --login)")
    return _result("Live connection", LEVEL_ERROR, err[:300])


def check_telegram_local(config: dict,
                         session_file: Optional[str] = None
                         ) -> List[Dict[str, str]]:
    out = []
    api_id = str(config.get("telegram_api_id", "") or "").strip()
    api_hash = str(config.get("telegram_api_hash", "") or "").strip()
    phone = str(config.get("telegram_phone", "") or "").strip()
    if api_id and api_hash and phone:
        shown = phone if len(phone) <= 5 else phone[:5] + "•••"
        out.append(_result("Credentials", LEVEL_OK,
                           f"API id/hash set · phone {shown}"))
    else:
        missing = [n for n, v in (("api id", api_id), ("api hash", api_hash),
                                  ("phone", phone)) if not v]
        out.append(_result("Credentials", LEVEL_ERROR,
                           "incomplete — missing " + ", ".join(missing) +
                           " (Settings ▸ Credentials)"))
    if session_file is None:
        session_file = os.path.join(APP_DIR, "session.session")
    if os.path.isfile(session_file):
        out.append(_result("Account session", LEVEL_OK,
                           f"session file found — logged in on this machine "
                           f"({os.path.basename(session_file)})"))
    else:
        out.append(_result("Account session", LEVEL_WARN,
                           "no session file — the account is not logged in yet "
                           "(GUI: Test Telegram & GitHub · CLI: --login)"))
    bot = str(config.get("bot_username", "") or "").strip().lstrip("@")
    if bot:
        out.append(_result("Bot", LEVEL_OK,
                           f"@{bot} configured — the queue is read through "
                           f"your account session"))
    else:
        out.append(_result("Bot", LEVEL_WARN,
                           "username not set — SYNC cannot fetch your link "
                           "queue (Settings ▸ Bot)"))
    px = config.get("proxy") or {}
    if px.get("enabled"):
        host = str(px.get("host", "127.0.0.1") or "127.0.0.1")
        try:
            port = int(px.get("port", 0) or 0)
        except (TypeError, ValueError):
            port = 0
        t0 = time.time()
        try:
            with socket.create_connection((host, port), timeout=5):
                ms = int((time.time() - t0) * 1000)
            out.append(_result("Proxy", LEVEL_OK,
                               f"{host}:{port} reachable "
                               f"({px.get('type', 'socks5')}, {ms} ms)"))
        except Exception as exc:
            out.append(_result("Proxy", LEVEL_ERROR,
                               f"{host}:{port} UNREACHABLE "
                               f"({type(exc).__name__}) — start the proxy app; "
                               f"Telegram needs it in Iran"))
    else:
        out.append(_result("Proxy", LEVEL_WARN,
                           "disabled — Telegram connects directly (fine "
                           "outside Iran, blocked inside)"))
    return out


# ---------------------------------------------------------------------------
# The Cloudflare bot Worker version (staleness check, v0.25.0)
# ---------------------------------------------------------------------------

def _version_cmp(a: str, b: str) -> int:
    """Compare two dotted version strings numerically (0.22.0 vs 0.25.0).
    Non-numeric segments compare equal to 0; returns -1/0/1."""
    def parts(v):
        out = []
        for chunk in str(v).strip().lstrip("v").split("."):
            digits = "".join(ch for ch in chunk if ch.isdigit())
            out.append(int(digits) if digits else 0)
        while len(out) < 3:
            out.append(0)
        return out[:3]
    pa, pb = parts(a), parts(b)
    return (pa > pb) - (pa < pb)


def check_worker_version(config: dict,
                         http_get=None) -> Optional[Dict[str, str]]:
    """When a Cloudflare Worker URL is configured, compare its deployed
    version (``GET <url>/health`` → ``{"version": ...}``) with
    ``EXPECTED_WORKER_VERSION`` so a stale bot is detectable from Test
    Connection. Returns a result dict, or ``None`` when no worker URL is
    configured (the sync is optional — nothing to check, nothing to say).
    Never raises. ``http_get`` (url) → dict is injectable for tests.

    The fetch deliberately BYPASSES the system proxy, exactly like the
    sync client it mirrors (``cloud/cloudflare_sync.py``): owners behind
    an intercepting proxy (v2rayN & co.) would otherwise see a false
    ⚠️/❌ for a Worker that works."""
    url = (config or {}).get("cloudflare_worker_url", "")
    if not url:
        return None

    def _direct_get(u, timeout_s=8.0):
        req = urllib.request.Request(
            u, headers={"User-Agent": "GitCurator-connection-check"})
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}))  # no proxy, like cloudflare_sync
        with opener.open(req, timeout=timeout_s) as resp:
            try:
                raw = resp.read().decode("utf-8") or "{}"
            finally:
                try:
                    resp.close()
                except Exception:
                    pass
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("non-dict /health payload")
        return payload

    fetch = http_get or _direct_get
    try:
        data = fetch(url.rstrip("/") + "/health", timeout_s=8.0)
    except Exception as exc:
        enabled = bool((config or {}).get("cloudflare_enabled", False))
        level = LEVEL_ERROR if enabled else LEVEL_WARN
        return _result("Bot Worker", level,
                       "reachable check failed "
                       f"({type(exc).__name__}) — {url.rstrip('/')}/health")
    deployed = (data or {}).get("version", "")
    if not deployed:
        return _result("Bot Worker", LEVEL_WARN,
                       "no version reported by /health — redeploy the "
                       "Worker (deploy-latest.sh / deploy-latest.ps1 in "
                       "app/cloudflare-bot)")
    cmp = _version_cmp(str(deployed), str(EXPECTED_WORKER_VERSION))
    if cmp == 0:
        return _result("Bot Worker", LEVEL_OK,
                       f"v{deployed} — matches this app "
                       f"(expected v{EXPECTED_WORKER_VERSION})")
    if cmp < 0:
        return _result(
            "Bot Worker", LEVEL_WARN,
            f"v{deployed} deployed, this app expects v{EXPECTED_WORKER_VERSION} "
            "— an older bot may miss newer link handling; update it: "
            "cd app/cloudflare-bot && bash deploy-latest.sh "
            "(Windows: .\\deploy-latest.ps1)")
    return _result(
        "Bot Worker", LEVEL_INFO,
        f"v{deployed} deployed is NEWER than this app expects "
        f"(v{EXPECTED_WORKER_VERSION}) — update the desktop app when "
        "convenient; the bot stays backward compatible")


# ---------------------------------------------------------------------------
# The battery + the verdict
# ---------------------------------------------------------------------------

def run_local_checks(config: dict,
                     on_section: Optional[Callable] = None,
                     on_result: Optional[Callable] = None,
                     session_file: Optional[str] = None) -> List[List]:
    """Vaults + LLM + GitHub + Telegram-local, in order. Returns the
    sections as ``[[title, [result, …]], …]`` (mutable — orchestrators
    append the live-Telegram result to the last section). ``on_section``
    gets ``(title, index, total)``, ``on_result`` gets each result dict;
    exceptions inside the callbacks are swallowed. Never raises.

    v0.25.0: when a Cloudflare Worker URL is configured, its version check
    joins the Telegram section as one extra line (a stale deployed bot is
    a Telegram-side problem). Unchanged — all four sections — otherwise."""
    def telegram_section():
        results = check_telegram_local(config, session_file)
        try:
            worker_check = check_worker_version(config)
        except Exception:
            worker_check = None
        if worker_check is not None:
            results = list(results) + [worker_check]
        return results

    groups = [
        ("Vaults", lambda: check_vaults(config)),
        ("LLM", lambda: check_llm(config)),
        ("GitHub", lambda: check_github(config)),
        ("Telegram", telegram_section),
    ]
    sections: List[List] = []
    total = len(groups)
    for idx, (title, fn) in enumerate(groups, 1):
        try:
            results = fn()
        except Exception as exc:  # a crashed check must not kill the rest
            results = [_result(title, LEVEL_ERROR,
                               f"check crashed ({type(exc).__name__}: {exc})")]
        sections.append([title, results])
        if on_section is not None:
            try:
                on_section(title, idx, total)
            except Exception:
                pass
        if on_result is not None:
            for r in results:
                try:
                    on_result(r)
                except Exception:
                    pass
    return sections


def summarize(sections: List[List]) -> Dict:
    """One verdict per section, then one headline. Returns
    ``{'level', 'headline', 'ready', 'total', 'errors', 'warnings'}``."""
    per = []
    for _title, results in sections or []:
        levels = [r.get("level") for r in (results or [])] or [LEVEL_INFO]
        if LEVEL_ERROR in levels:
            per.append(LEVEL_ERROR)
        elif LEVEL_WARN in levels:
            per.append(LEVEL_WARN)
        else:
            per.append(LEVEL_OK)
    ready = sum(1 for lv in per if lv == LEVEL_OK)
    errors = sum(1 for lv in per if lv == LEVEL_ERROR)
    warnings = sum(1 for lv in per if lv == LEVEL_WARN)
    level = LEVEL_ERROR if errors else (LEVEL_WARN if warnings else LEVEL_OK)
    if level == LEVEL_OK:
        headline = ("ALL SYSTEMS READY — vaults, Telegram, LLM and GitHub "
                    "are up")
    else:
        bits = []
        if errors:
            bits.append(f"{errors} error(s)")
        if warnings:
            bits.append(f"{warnings} warning(s)")
        headline = (f"{ready}/{len(per)} subsystems ready — "
                    f"{' and '.join(bits)} (see the lines above)")
    return {"level": level, "headline": headline, "ready": ready,
            "total": len(per), "errors": errors, "warnings": warnings}
