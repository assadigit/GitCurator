#!/usr/bin/env python3
"""
llm_client.py — timeout-wrapped Ollama calls + robust JSON extraction.

v30 — Fix (Timeouts on every external call): main.py:2699 called
``client.chat(**kwargs)`` with NO timeout. When Ollama hangs (model
loading, GPU contention, zombie server) the worker thread blocked
FOREVER — the GUI showed "Analyzing..." and the whole batch froze;
headless mode hung indefinitely. The same applied to ``client.list()``
(connection check at main.py:1315 and the settings model refresh).

``ollama`` python SDK accepts a ``timeout`` kwarg in recent versions
only, and the installed version varies — so every call here is wrapped
in a ThreadPoolExecutor with an explicit wall-clock timeout instead.
On timeout the caller gets TimeoutError; the leaked worker thread
finishes in the background and its (late) result is discarded. Threads
cannot be killed in Python — this is the standard, accepted trade-off.

Pure stdlib (+ optional ``ollama`` import at call sites) — unit-testable
without a server.
"""

import concurrent.futures
import ipaddress
import json
import os
import re
import subprocess
from typing import Any, Callable, List

DEFAULT_TIMEOUT_S = 300      # chat — generous, models are slow to warm
DEFAULT_LIST_TIMEOUT_S = 15  # list/connectivity — should be instant


# ---------------------------------------------------------------------------
# v0.15.1 — proxy-safe loopback helpers
#
# The owner's report: "the service is running on task manager, the app must
# automatically catch that!" — llama-server WAS running, detection still
# failed. Root cause #1: ``urllib.request.urlopen`` consults the system
# proxy (env HTTP_PROXY/HTTPS_PROXY + the Windows registry). The owner's
# machine runs a proxy/VPN client (GitCurator itself ships a proxy health
# monitor for Telegram) — a proxy that doesn't bypass 127.0.0.1 swallows
# every loopback request, so the probe "could not reach" a server that was
# up the whole time. Loopback traffic must NEVER ride a proxy.
# ---------------------------------------------------------------------------

def _is_loopback_url(url) -> bool:
    """True when the URL points at THIS machine (127.x.x.x / ::1 /
    localhost). Used to route loopback requests around the system proxy —
    a proxy can only break them. Accepts bare ``host:port`` spellings
    too (no scheme)."""
    from urllib.parse import urlparse
    raw = str(url or '').strip()
    if not raw:
        return False
    if '://' not in raw:
        raw = 'http://' + raw
    try:
        host = urlparse(raw).hostname or ''
    except ValueError:
        return False
    if not host:
        return False
    if host.lower() == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _urlopen_direct(req, timeout_s, context=None):
    """Open ``req`` WITHOUT ever consulting the system/env proxy — for
    loopback targets (llama.cpp / Ollama / local OpenAI-compatible
    endpoints) where a proxy client can only break things. Same
    HTTPError/URLError surface as ``urllib.request.urlopen``."""
    import urllib.request
    handlers = [urllib.request.ProxyHandler({})]  # {} = no proxy, period
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    return urllib.request.build_opener(*handlers).open(req, timeout=timeout_s)


# ---------------------------------------------------------------------------
# Timeout wrapper
# ---------------------------------------------------------------------------

def call_with_timeout(fn: Callable, timeout_s: float, *args, **kwargs) -> Any:
    """Run ``fn(*args, **kwargs)`` with a wall-clock timeout.

    Raises TimeoutError when ``timeout_s`` elapses. The underlying thread
    keeps running to completion (Python threads can't be killed) but its
    result is discarded — callers must treat that as a failed call.

    v30 (test-caught bug): the executor is shut down with wait=False on
    timeout — using a ``with`` block here would BLOCK on exit until the
    hung call finished, defeating the entire point of the timeout.
    """
    if timeout_s is None or timeout_s <= 0:
        return fn(*args, **kwargs)
    pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix='llm-call'
    )
    try:
        future = pool.submit(fn, *args, **kwargs)
        try:
            return future.result(timeout=timeout_s)
        except concurrent.futures.TimeoutError:
            raise TimeoutError(
                f"LLM call did not respond within {timeout_s:.0f}s"
            )
    finally:
        # Return control to the caller IMMEDIATELY — do not join the worker
        # thread. The late result (if any) is discarded.
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except TypeError:  # Python < 3.9
            pool.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Ollama call helpers (handle BOTH the old dict API and new object API)
# ---------------------------------------------------------------------------

def chat_with_timeout(client, timeout_s: float = DEFAULT_TIMEOUT_S, **kwargs) -> str:
    """Call ``client.chat(**kwargs)`` with a timeout; return the content str.

    Handles:
      - new ollama-py: response.message.content
      - old ollama-py: response as dict {'message': {'content': ...}}
      - degenerate: response is already a string
    """
    response = call_with_timeout(client.chat, timeout_s, **kwargs)
    return response_content(response)


def response_content(response) -> str:
    """Extract the assistant text from any ollama-py response shape."""
    if hasattr(response, 'message'):
        msg = response.message
        return getattr(msg, 'content', None) or ''
    if isinstance(response, dict):
        return response.get('message', {}).get('content', '') or ''
    return str(response) if response is not None else ''


def list_models_with_timeout(client, timeout_s: float = DEFAULT_LIST_TIMEOUT_S) -> List[str]:
    """Call ``client.list()`` with a timeout; return model names.

    Handles BOTH the old API (models is a list of dicts with 'name') and
    the new API (ListResponse with .models, each having .model attr).
    """
    resp = call_with_timeout(client.list, timeout_s)
    if hasattr(resp, 'models'):
        models = resp.models
    elif isinstance(resp, dict):
        models = resp.get('models', [])
    else:
        models = list(resp)

    names: List[str] = []
    for m in models:
        if hasattr(m, 'model'):
            name = m.model
        elif isinstance(m, dict):
            name = m.get('name') or m.get('model')
        else:
            name = None
        if name:
            names.append(name)
    return names


# ---------------------------------------------------------------------------
# Robust JSON extraction (moved verbatim from _llm_analyze._extract_json)
# ---------------------------------------------------------------------------

def extract_json(text: str) -> dict:
    """Robustly extract JSON from LLM output. Handles:
    - Pure JSON
    - JSON wrapped in ```json ... ``` markdown fences
    - JSON embedded in prose (finds first { ... })
    - Trailing/leading whitespace

    Raises ValueError when no JSON object is found.
    """
    text = (text or '').strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strip markdown fences
    fence_pattern = re.compile(r'```(?:json)?\s*(.*?)```', re.DOTALL)
    match = fence_pattern.search(text)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Extract first { ... } block (greedy enough to handle nested)
    start = text.find('{')
    if start != -1:
        # Find matching closing brace
        depth = 0
        for i, c in enumerate(text[start:], start):
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        pass
                    break

    raise ValueError("No valid JSON found in response")


# ===========================================================================
# v0.13.0 — Phase 4: the OpenAI-compatible endpoint as a first-class backend
# (llama.cpp server, vLLM, LM Studio, cloud APIs) + the explicit context
# window. Everything below is pure stdlib and unit-testable against a fake
# local http.server — no model server required.
# ===========================================================================

# The explicit context window (tokens). Ollama's default num_ctx is small
# (often 2048/4096) and truncates the START of over-long prompts silently —
# setting it on every call makes the window a deliberate choice, and the
# estimate guard below warns BEFORE the server can chop anything quietly.
DEFAULT_NUM_CTX = 8192

# v0.23.0 — the OUTPUT half of the context budget. Models split their total
# window between the prompt and the answer (e.g. a 160k-total model with a
# 32k output cap leaves 128k for the prompt). 0/None = leave the output cap
# to the server's default (OpenAI-compatible: don't send max_tokens; Ollama:
# don't send num_predict). Anthropic REQUIRES max_tokens on every call —
# the Claude path falls back to this default when unset.
DEFAULT_MAX_OUTPUT_TOKENS = 0
ANTHROPIC_FALLBACK_MAX_TOKENS = 4096

# The canonical label for the cloud provider option (SPEC §6 Phase 4:
# relabel "Cloud API" — GUI radio, settings group, CLI wizard and README
# all import this single constant). v0.23.0: the parenthetical is gone —
# the label names exactly what the field is; the examples live in tooltips.
CLOUD_PROVIDER_LABEL = "OpenAI-compatible endpoint"

# v0.23.0 — the Claude/Anthropic API is a FIRST-CLASS cloud backend. The
# Messages API is NOT OpenAI-shaped (x-api-key + anthropic-version headers,
# /v1/messages, required max_tokens, content-blocks response), so the URL
# decides the wire format: an api.anthropic.com base routes to anthropic_chat,
# everything else to openai_chat (llama.cpp / vLLM / LM Studio / OpenAI /
# OpenRouter / Together / Workers AI…). cloud_chat is the single router both
# the GUI and the worker call.
ANTHROPIC_VERSION_HEADER = "2023-06-01"
ANTHROPIC_HOST_HINTS = ("api.anthropic.com", "anthropic.com")


def is_anthropic_url(api_url: str) -> bool:
    """True when the base URL points at the Anthropic API (api.anthropic.com
    or any *.anthropic.com host — enterprise gateways keep the host name)."""
    base = str(api_url or "").strip().lower()
    return any(hint in base for hint in ANTHROPIC_HOST_HINTS)

# Base URLs whose server REJECTED ``response_format`` (HTTP 400). Remembered
# so later calls skip the doomed attempt instead of paying for the round
# trip every time. Keyed by the normalized base URL; process-lifetime only.
_JSON_MODE_REJECTED = set()


class CloudLLMError(RuntimeError):
    """Base class for OpenAI-compatible endpoint failures (clear message,
    never a raw urllib traceback). Generic ``except Exception`` handlers in
    the worker/dialogs already catch this."""


class CloudLLMHTTPError(CloudLLMError):
    """The endpoint answered with an HTTP error status."""

    def __init__(self, code, body, message=None):
        self.code = int(code)
        self.body = (body or '')[:400]
        super().__init__(message or (
            f"endpoint answered HTTP {self.code}: {self.body[:200]}"))


class CloudLLMBadResponse(CloudLLMError):
    """HTTP 200 but the body is not a usable chat completion (malformed
    JSON, an embedded error object, or no choices array)."""


def json_mode_rejected(api_url: str) -> bool:
    """True when this base URL already rejected ``response_format``."""
    return (api_url or '').rstrip('/').lower() in _JSON_MODE_REJECTED


def reset_json_mode_memo() -> None:
    """Tests only: forget every remembered response_format rejection."""
    _JSON_MODE_REJECTED.clear()


def estimate_tokens(messages) -> int:
    """Rough prompt size in tokens (~4 chars per token for English prose
    plus a small per-message envelope). Deliberately conservative — used
    only for the over-budget WARNING, never for hard decisions."""
    total = 0
    for m in (messages or []):
        content = m.get('content') if isinstance(m, dict) else None
        total += len(str(content or '')) + 16
    return total // 4


def resolve_task_model(config, task, default_model):
    """v0.13.0 — per-task model override: ``config['models']['classify']``
    / ``['analyze']`` beat the single configured model for that task.
    Empty/missing override (or unknown task) keeps ``default_model`` —
    existing configs behave exactly as before."""
    if not task or not isinstance(config, dict):
        return default_model
    models = config.get('models')
    if isinstance(models, dict):
        override = str(models.get(task) or '').strip()
        if override:
            return override
    return default_model


# ---------------------------------------------------------------------------
# OpenAI-compatible chat (llama.cpp / vLLM / LM Studio / cloud)
# ---------------------------------------------------------------------------

def openai_chat(api_url, api_key, model, messages, timeout_s, *,
                json_mode=False, temperature=0.7, verify_tls=False,
                num_ctx=None, max_output_tokens=None, on_warn=None):
    """POST ``<api_url>/chat/completions`` through the SAME wall-clock
    timeout wrapper as Ollama (``call_with_timeout``) — a hung endpoint can
    no longer freeze a batch (SPEC §6 Phase 4).

    JSON mode: sends ``response_format: {"type": "json_object"}`` when
    ``json_mode`` is requested and this base URL has not already rejected
    it. A server that answers HTTP 400 to the parameter gets ONE clean
    retry without it; on success the rejection is memoized so later calls
    skip the doomed attempt entirely (the retry only ever happens once per
    endpoint per process).

    ``verify_tls=False`` preserves the v26 behavior: self-hosted llama.cpp /
    LM Studio endpoints often run self-signed certs. Plain-HTTP local
    servers are unaffected by the flag.

    Context window: OpenAI-compatible servers fix their window at LAUNCH
    (llama.cpp ``-c``, vLLM ``--max-model-len``) — no per-request knob
    exists, so ``num_ctx`` here only powers the over-budget warning: when
    the estimated prompt exceeds it, ``on_warn`` fires BEFORE the call.
    Nothing is ever truncated silently on either backend.

    Returns the assistant content string ('' when the body is well-formed
    but empty — callers decide what that means). Raises TimeoutError (wall
    clock), CloudLLMHTTPError (HTTP status), CloudLLMBadResponse (malformed
    body / no choices) or CloudLLMError (connection failure).
    """
    import urllib.error
    import urllib.request
    import ssl

    base = (api_url or '').rstrip('/')
    if not base:
        raise CloudLLMError("no API URL configured for the "
                            "OpenAI-compatible endpoint")

    # Over-budget warning (the "never truncate silently" half of the
    # context-window rule — the server's window is launch-time fixed, so
    # the client's job is to make an over-budget prompt VISIBLE).
    if num_ctx and int(num_ctx) > 0 and on_warn:
        est = estimate_tokens(messages)
        if est > int(num_ctx):
            on_warn(
                f"prompt ≈{est} tokens may exceed the server's context "
                f"window (llm_num_ctx={int(num_ctx)}) — launch llama.cpp "
                "with -c (or vLLM with --max-model-len) at least that "
                "large if answers degrade.")

    def _post(use_json_mode):
        body = {'model': model, 'messages': messages,
                'temperature': temperature}
        # v0.23.0 — the output cap (the second half of the context budget:
        # total window = prompt + output). Only sent when the user set one —
        # 0/None keeps the server's own default.
        if max_output_tokens and int(max_output_tokens) > 0:
            body['max_tokens'] = int(max_output_tokens)
        if use_json_mode:
            body['response_format'] = {'type': 'json_object'}
        data = json.dumps(body).encode('utf-8')
        headers = {'Content-Type': 'application/json'}
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'
        req = urllib.request.Request(
            base + '/chat/completions', data=data, headers=headers)
        ctx = ssl.create_default_context()
        if not verify_tls:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        # The socket timeout sits slightly ABOVE the wall-clock timeout so
        # the shared wrapper is the deterministic authority — callers always
        # get the same TimeoutError they get on the Ollama path.
        sock_timeout = timeout_s + 5 if timeout_s and timeout_s > 0 else 120
        # v0.15.1 — a LOOPBACK endpoint (local llama.cpp / LM Studio /
        # Ollama bridge) must bypass the system proxy; everything else
        # keeps normal proxy behavior (cloud endpoints may NEED it).
        if _is_loopback_url(base):
            return _urlopen_direct(req, sock_timeout, context=ctx)
        return urllib.request.urlopen(req, context=ctx,
                                      timeout=sock_timeout)

    def _read(resp):
        raw = resp.read().decode('utf-8', errors='replace')
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise CloudLLMBadResponse(
                "endpoint returned a non-JSON body: "
                + (raw[:160] or '(empty)'))

    def _content(payload):
        if not isinstance(payload, dict):
            raise CloudLLMBadResponse("endpoint returned a non-object body")
        err = payload.get('error')
        if err:
            msg = err.get('message') if isinstance(err, dict) else str(err)
            raise CloudLLMBadResponse(f"endpoint error object: {msg}")
        choices = payload.get('choices') or []
        if not choices:
            raise CloudLLMBadResponse("endpoint returned no choices")
        first = choices[0] or {}
        message = first.get('message') or {}
        content = message.get('content')
        return content if isinstance(content, str) else ''

    send_json = bool(json_mode) and not json_mode_rejected(base)
    try:
        try:
            resp = call_with_timeout(_post, timeout_s, send_json)
        except urllib.error.HTTPError as e:
            body_text = ''
            try:
                body_text = e.read().decode('utf-8', errors='replace')
            except Exception:
                pass
            if send_json and e.code == 400:
                # The server may be rejecting response_format itself —
                # fall back cleanly: ONE retry without the parameter.
                if on_warn:
                    on_warn("endpoint rejected response_format — "
                            "retrying without JSON mode (memoized)")
                try:
                    resp = call_with_timeout(_post, timeout_s, False)
                except urllib.error.HTTPError as e2:
                    body2 = ''
                    try:
                        body2 = e2.read().decode('utf-8', errors='replace')
                    except Exception:
                        pass
                    raise CloudLLMHTTPError(e2.code, body2)
                _JSON_MODE_REJECTED.add(base.lower())
                return _content(_read(resp))
            raise CloudLLMHTTPError(e.code, body_text)
        return _content(_read(resp))
    except urllib.error.URLError as e:
        raise CloudLLMError(
            f"could not reach the OpenAI-compatible endpoint {base}: {e}"
        ) from e


def openai_list_models(api_url, api_key, timeout_s=15):
    """GET ``<api_url>/models`` — returns the model id/name list. Raises
    CloudLLMError on anything but HTTP 200 (404 counts: llama.cpp with no
    models route, misconfigured proxies…)."""
    import urllib.error
    import urllib.request

    base = (api_url or '').rstrip('/')
    if not base:
        raise CloudLLMError("no API URL configured for the "
                            "OpenAI-compatible endpoint")
    req = urllib.request.Request(base + '/models')
    if api_key:
        req.add_header('Authorization', f'Bearer {api_key}')
    try:
        if _is_loopback_url(base):  # v0.15.1 — never proxy loopback
            with _urlopen_direct(req, timeout_s) as resp:
                raw = resp.read().decode('utf-8', errors='replace')
        else:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                raw = resp.read().decode('utf-8', errors='replace')
    except urllib.error.HTTPError as e:
        raise CloudLLMError(f"GET {base}/models answered HTTP {e.code}")
    except urllib.error.URLError as e:
        raise CloudLLMError(f"could not reach {base}/models: {e}")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise CloudLLMError(f"GET {base}/models returned a non-JSON body")
    data = payload.get('data') if isinstance(payload, dict) else None
    names = []
    for m in (data if isinstance(data, list) else []):
        if isinstance(m, dict):
            name = m.get('id') or m.get('name')
            if name:
                names.append(str(name))
        elif m:
            names.append(str(m))
    return names


def preflight_openai(api_url, api_key, model, timeout_s=15):
    """The ``/v1/models`` pre-flight check (SPEC §6 Phase 4). NEVER a gate:
    returns ``(endpoint_ok, message, model_listed)`` where ``model_listed``
    is None when the list could not be read. Callers log a warning at most
    — a server that hides /models (single-model llama.cpp builds, some
    proxies) is still perfectly usable."""
    try:
        names = openai_list_models(api_url, api_key, timeout_s)
    except CloudLLMError as e:
        return False, str(e), None
    listed = None
    if names:
        lowered = {n.lower() for n in names}
        listed = str(model or '').strip().lower() in lowered
    return True, f"{len(names)} model(s) listed", listed


# ---------------------------------------------------------------------------
# v0.23.0 — the Anthropic Claude API (the second cloud wire format)
# ---------------------------------------------------------------------------

def anthropic_list_models(api_url, api_key, timeout_s=15):
    """GET ``<api_url>/models`` with the Anthropic headers (x-api-key +
    anthropic-version). Returns the model id list. Raises CloudLLMError on
    anything but HTTP 200 — same contract as openai_list_models."""
    import urllib.error
    import urllib.request

    base = (api_url or '').rstrip('/')
    if not base:
        raise CloudLLMError("no API URL configured for the Claude endpoint")
    req = urllib.request.Request(base + '/models')
    req.add_header('x-api-key', api_key or '')
    req.add_header('anthropic-version', ANTHROPIC_VERSION_HEADER)
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode('utf-8', errors='replace')
    except urllib.error.HTTPError as e:
        raise CloudLLMError(f"GET {base}/models answered HTTP {e.code}")
    except urllib.error.URLError as e:
        raise CloudLLMError(f"could not reach {base}/models: {e}")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise CloudLLMError(f"GET {base}/models returned a non-JSON body")
    data = payload.get('data') if isinstance(payload, dict) else None
    names = []
    for m in (data if isinstance(data, list) else []):
        if isinstance(m, dict):
            mid = m.get('id') or m.get('name')
            if mid:
                names.append(str(mid))
    return names


def anthropic_chat(api_url, api_key, model, messages, timeout_s, *,
                   json_mode=False, temperature=0.7, verify_tls=False,
                   num_ctx=None, max_output_tokens=None, on_warn=None):
    """POST ``<api_url>/messages`` — the Anthropic Claude wire format.

    Same contract as ``openai_chat`` (wall-clock timeout via
    ``call_with_timeout``, clear CloudLLM* errors, '' on a well-formed but
    empty answer) with the Messages-API differences handled:

      * headers: ``x-api-key`` + ``anthropic-version`` (no Bearer)
      * the system message rides the top-level ``system`` parameter, and
        only user/assistant turns stay in ``messages``
      * ``max_tokens`` is REQUIRED by the API — the user's output cap when
        set, else ANTHROPIC_FALLBACK_MAX_TOKENS
      * the answer is a list of content blocks — the text blocks are joined
      * ``response_format`` does not exist on this API: json_mode only adds
        a JSON-only instruction to the system text (Claude follows it; the
        extract_json parser handles the rest)
    """
    import urllib.error
    import urllib.request
    import ssl

    base = (api_url or '').rstrip('/')
    if not base:
        raise CloudLLMError("no API URL configured for the Claude endpoint")

    if num_ctx and int(num_ctx) > 0 and on_warn:
        est = estimate_tokens(messages)
        if est > int(num_ctx):
            on_warn(
                f"prompt ≈{est} tokens may exceed the model's context window "
                f"(llm_num_ctx={int(num_ctx)}) — answers may degrade if the "
                "model can't see the whole prompt.")

    # Split the system messages out (Messages API: top-level `system`).
    system_parts = [str(m.get('content') or '') for m in (messages or [])
                    if isinstance(m, dict) and m.get('role') == 'system']
    chat_messages = [
        {'role': (m.get('role') if m.get('role') in ('user', 'assistant')
                  else 'user'),
         'content': str(m.get('content') or '')}
        for m in (messages or []) if isinstance(m, dict)
        and m.get('role') != 'system'
    ]
    system_text = "\n\n".join(p for p in system_parts if p)
    if json_mode and system_text:
        system_text = (system_text
                       + "\n\nReply with ONLY a valid JSON object — no prose, "
                         "no markdown fences.")

    def _post():
        body = {'model': model, 'messages': chat_messages,
                'max_tokens': (int(max_output_tokens)
                               if max_output_tokens and int(max_output_tokens) > 0
                               else ANTHROPIC_FALLBACK_MAX_TOKENS),
                'temperature': temperature}
        if system_text:
            body['system'] = system_text
        data = json.dumps(body).encode('utf-8')
        headers = {'Content-Type': 'application/json',
                   'anthropic-version': ANTHROPIC_VERSION_HEADER}
        if api_key:
            headers['x-api-key'] = api_key
        req = urllib.request.Request(
            base + '/messages', data=data, headers=headers)
        ctx = ssl.create_default_context()
        if not verify_tls:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        sock_timeout = timeout_s + 5 if timeout_s and timeout_s > 0 else 120
        return urllib.request.urlopen(req, context=ctx, timeout=sock_timeout)

    def _content(payload):
        if not isinstance(payload, dict):
            raise CloudLLMBadResponse("endpoint returned a non-object body")
        err = payload.get('error')
        if err:
            msg = err.get('message') if isinstance(err, dict) else str(err)
            raise CloudLLMBadResponse(f"endpoint error object: {msg}")
        blocks = payload.get('content')
        if not isinstance(blocks, list) or not blocks:
            raise CloudLLMBadResponse("endpoint returned no content blocks")
        texts = [str(b.get('text') or '') for b in blocks
                 if isinstance(b, dict) and b.get('type') == 'text']
        return '\n'.join(t for t in texts if t)

    try:
        resp = call_with_timeout(_post, timeout_s)
        raw = resp.read().decode('utf-8', errors='replace')
        try:
            return _content(json.loads(raw))
        except json.JSONDecodeError:
            raise CloudLLMBadResponse(
                "endpoint returned a non-JSON body: " + (raw[:160] or '(empty)'))
    except urllib.error.HTTPError as e:
        body_text = ''
        try:
            body_text = e.read().decode('utf-8', errors='replace')
        except Exception:
            pass
        raise CloudLLMHTTPError(e.code, body_text)
    except urllib.error.URLError as e:
        raise CloudLLMError(
            f"could not reach the Claude endpoint {base}: {e}") from e


def cloud_chat(api_url, api_key, model, messages, timeout_s, **kwargs):
    """v0.23.0 — the ONE cloud router: the URL decides the wire format.
    api.anthropic.com → anthropic_chat (Claude), everything else →
    openai_chat (OpenAI-compatible: llama.cpp, vLLM, LM Studio, OpenAI,
    OpenRouter, Together…). Same keyword arguments as both (json_mode,
    num_ctx, max_output_tokens, on_warn, temperature, verify_tls)."""
    if is_anthropic_url(api_url):
        return anthropic_chat(api_url, api_key, model, messages, timeout_s,
                              **kwargs)
    return openai_chat(api_url, api_key, model, messages, timeout_s,
                       **kwargs)


def preflight_cloud(api_url, api_key, model, timeout_s=15):
    """The model-list pre-flight for EITHER cloud wire format (the URL
    decides, exactly like cloud_chat). Same contract as preflight_openai:
    ``(endpoint_ok, message, model_listed)`` — never a gate."""
    try:
        if is_anthropic_url(api_url):
            names = anthropic_list_models(api_url, api_key, timeout_s)
        else:
            names = openai_list_models(api_url, api_key, timeout_s)
    except CloudLLMError as e:
        return False, str(e), None
    listed = None
    if names:
        lowered = {n.lower() for n in names}
        listed = str(model or '').strip().lower() in lowered
    return True, f"{len(names)} model(s) listed", listed


# ---------------------------------------------------------------------------
# Ollama chat with the explicit context window
# ---------------------------------------------------------------------------

def ollama_chat(client, model, messages, timeout_s, *, json_mode=True,
                num_ctx=None, num_predict=None, on_warn=None):
    """``client.chat`` with the SAME wall-clock timeout wrapper as always,
    plus the explicit context window: ``options.num_ctx`` is sent on every
    call (Ollama's own default is small and truncates long prompts from
    the front SILENTLY — an explicit window turns that into a deliberate,
    visible choice). When the estimated prompt exceeds ``num_ctx`` the
    caller is warned through ``on_warn`` BEFORE the call — nothing is ever
    chopped quietly. ``num_ctx=None`` (or 0) leaves the window to the
    server. v0.23.0: ``num_predict`` (the OUTPUT half of the context
    budget — e.g. 32k output on a 160k-total model) is sent as
    options.num_predict when > 0; None/0 keeps the server default. Returns
    the assistant content string (both ollama-py response shapes handled
    by ``response_content``)."""
    kwargs = {'model': model, 'messages': messages}
    if num_ctx and int(num_ctx) > 0:
        num_ctx = int(num_ctx)
        kwargs['options'] = {'num_ctx': num_ctx}
        if num_predict and int(num_predict) > 0:
            kwargs['options']['num_predict'] = int(num_predict)
        est = estimate_tokens(messages)
        if est > num_ctx and on_warn:
            on_warn(
                f"prompt ≈{est} tokens exceeds the context window "
                f"(num_ctx={num_ctx}) — the model may not see the start "
                "of it. Raise llm_num_ctx in Settings if answers degrade.")
    if json_mode:
        kwargs['format'] = 'json'
    response = call_with_timeout(client.chat, timeout_s, **kwargs)
    return response_content(response)


# ===========================================================================
# v0.15.0 — llama.cpp engine detection (owner request 2026-09-29: "the app
# must have llama.cpp engine detection… it must detect llama.cpp service
# and its model detected automatically"). llama.cpp's llama-server is its
# own first-class provider (config value 'llamacpp') DETECTED like Ollama
# instead of hand-configured like the cloud endpoint:
#
#   * /props  — a llama.cpp-ONLY route; its JSON shape positively
#     identifies the server (vLLM/LM Studio/other OpenAI-compatible
#     servers 404 here — that is the whole point of the check).
#   * /health — 200 = model ready, 503 = still loading.
#   * /v1/models — the loaded model's id (alias or file name).
#
# Chat itself rides the OpenAI-compatible path (openai_chat) — llama.cpp
# speaks that protocol natively — so the timeout wrapper, JSON mode with
# memoized fallback and the over-budget warning all apply unchanged.
# Everything here is pure stdlib and unit-testable against a fake local
# http.server.
# ===========================================================================

# The canonical label for the llama.cpp provider option (GUI radio +
# settings group + CLI, mirroring CLOUD_PROVIDER_LABEL).
LLAMACPP_PROVIDER_LABEL = "llama.cpp server (local)"

# llama-server's default address (``llama-server -m model.gguf`` listens
# here without any --host/--port flags).
LLAMACPP_DEFAULT_BASE = "http://127.0.0.1:8080"

# Ports the Detect button / auto-detect scan probes, in order. 8080 is
# the llama-server default; the rest are common manual choices (5001 is
# koboldcpp's default, 1234 LM Studio's, 9000/8084/8085 seen in the
# wild). The scan positively identifies llama.cpp through /props (or the
# v0.15.1 /v1/models fingerprints), so anything else answering on these
# ports (a dev server on 8080, vLLM on 8000…) is skipped — never
# misreported. The RUNNING-PROCESS ports (llamacpp_process_ports) are
# probed BEFORE this list, so a llama-server on any other --port is
# caught too.
LLAMACPP_SCAN_PORTS = (8080, 8081, 8082, 8083, 8084, 8085,
                       8000, 5000, 5001, 1234, 9000)

# Per-port probe budget. Dead local ports refuse instantly; only a
# live-but-not-llama.cpp server ever costs the full budget.
LLAMACPP_PROBE_TIMEOUT_S = 2.0

# /props keys that fingerprint llama.cpp across server builds (very old
# builds lack model_alias; none of these appear in other servers' /props
# bodies).
_LLAMACPP_PROPS_MARKERS = ('model_path', 'model_alias',
                           'default_generation_settings', 'total_slots')


def normalize_llamacpp_api_url(url):
    """User input → the OpenAI-compatible base URL for a llama.cpp server.

    Accepts every shape people actually type — ``127.0.0.1:8080``,
    ``http://127.0.0.1:8080``, ``http://127.0.0.1:8080/``, a full base
    ``http://127.0.0.1:8080/v1`` — and returns the ``…/v1`` base that
    ``openai_chat`` expects. Empty input yields the llama-server default.
    A URL that already carries a deeper path (reverse proxies) is kept
    as-is; only the bare host[:port] gets ``/v1`` appended.
    """
    raw = str(url or '').strip()
    if not raw:
        raw = LLAMACPP_DEFAULT_BASE
    if '://' not in raw:
        raw = 'http://' + raw
    base = raw.rstrip('/')
    from urllib.parse import urlparse
    try:
        parts = urlparse(base)
    except ValueError:
        return LLAMACPP_DEFAULT_BASE + '/v1'
    if parts.path in ('', '/'):
        return base + '/v1'
    return base


def is_llamacpp_props(payload) -> bool:
    """True when a JSON body has the llama.cpp ``/props`` shape.

    /props is a llama.cpp-only route, so anything answering here is almost
    certainly llama-server — but the marker keys (model_path /
    model_alias / default_generation_settings / total_slots) still guard
    against a reverse-proxied OTHER service that happens to expose /props.
    OpenAI-style payloads, plain dicts and non-dicts are all rejected.
    """
    if not isinstance(payload, dict):
        return False
    return any(marker in payload for marker in _LLAMACPP_PROPS_MARKERS)


def llamacpp_model_from_props(props):
    """Best model name out of a /props payload: ``model_alias`` (the
    ``--alias`` value or the file stem — what llama-server advertises on
    /v1/models), else the basename of ``model_path``
    (``models/foo.Q4_K_M.gguf`` → ``foo.Q4_K_M.gguf``). None when the
    payload names nothing (the placeholder alias 'unknown' is skipped)."""
    if not isinstance(props, dict):
        return None
    alias = str(props.get('model_alias') or '').strip()
    if alias and alias.lower() != 'unknown':
        return alias
    path = str(props.get('model_path') or '').strip()
    if path:
        return path.replace('\\', '/').rstrip('/').rsplit('/', 1)[-1]
    return None


def _llamacpp_models_fingerprint(payload, headers=None):
    """v0.15.1 — llama.cpp fingerprints on a ``/v1/models`` response, used
    ONLY when /props did not identify the server (old builds and some
    forks 404 /props). Returns the fingerprint name — 'server-header'
    (llama.cpp's HTTP layer sets ``Server: llama.cpp``), 'owned_by'
    (llama-server tags its entries ``"owned_by": "llama.cpp"``),
    'gguf-id' (the advertised id is the loaded GGUF file) — or None.
    vLLM / LM Studio / plain OpenAI proxies match none of these, so they
    are still never misreported.

    ``headers`` may be a dict OR an email.message.Message (the live
    response object): EVERY Server header occurrence is checked —
    ``dict(msg)`` would collapse duplicates to the first value and miss
    a llama.cpp line hiding behind a stdlib one."""
    try:
        header_pairs = list((headers or {}).items())
    except Exception:
        header_pairs = []
    for k, v in header_pairs:
        if str(k).lower() == 'server' and 'llama.cpp' in str(v).lower():
            return 'server-header'
    data = payload.get('data') if isinstance(payload, dict) else None
    for m in (data if isinstance(data, list) else []):
        if not isinstance(m, dict):
            continue
        if str(m.get('owned_by', '') or '').lower() == 'llama.cpp':
            return 'owned_by'
        if str(m.get('id', '') or '').lower().endswith('.gguf'):
            return 'gguf-id'
    return None


def _model_ids_from_models_payload(payload):
    """``/v1/models`` body → the id/name list (same extraction rules as
    openai_list_models, for a payload already in hand)."""
    names = []
    data = payload.get('data') if isinstance(payload, dict) else None
    for m in (data if isinstance(data, list) else []):
        if isinstance(m, dict):
            name = m.get('id') or m.get('name')
            if name:
                names.append(str(name))
        elif m:
            names.append(str(m))
    return names


def probe_llamacpp(base_url, api_key='', timeout_s=LLAMACPP_PROBE_TIMEOUT_S):
    """Probe ONE address for a llama.cpp server. NEVER raises — returns a
    result dict:

      found     True only when the server positively identifies as
                llama.cpp — /props (primary), or — v0.15.1 — the llama.cpp
                fingerprints on /v1/models for builds that 404 /props
      ready     True/False from ``/health`` (False = model still loading),
                None when /health is unavailable
      models    model ids from ``GET /v1/models`` ([] when hidden/failed —
                old builds and proxies legitimately hide the route)
      model     the BEST model name — first /v1/models entry, else the
                /props alias/basename; None when nothing names it
      props_model  the /props-derived name (before the /v1/models override)
      identified_by  'props' | 'models:<fingerprint>' (v0.15.1)
      base_url  the normalized ROOT (no /v1) that was probed
      detail    one human-readable summary line

    v0.15.1 — proxy-safe: every request here bypasses the system/env
    proxy entirely (llama-server is a local service; a VPN/proxy client
    that doesn't bypass 127.0.0.1 must never swallow the probe).

    ``base_url`` accepts every user spelling (``normalize_llamacpp_api_url``
    runs first), so probing the raw Settings field is safe.
    """
    import urllib.error
    import urllib.request

    api = normalize_llamacpp_api_url(base_url)
    root = api[:-3] if api.endswith('/v1') else api

    def _get_json(url):
        req = urllib.request.Request(url)
        if api_key:
            req.add_header('Authorization', f'Bearer {api_key}')
        with _urlopen_direct(req, timeout_s) as resp:
            return (resp.getcode(),
                    resp.read().decode('utf-8', errors='replace'),
                    resp.headers)  # the Message object — keeps ALL headers

    result = {'found': False, 'ready': None, 'models': [], 'model': None,
              'props_model': None, 'identified_by': None, 'base_url': root,
              'detail': ''}

    # 1) /props — the primary positive llama.cpp identification.
    props = None
    props_err = ''
    try:
        code, raw, _hdrs = _get_json(root + '/props')
        if code == 200:
            try:
                props = json.loads(raw)
            except json.JSONDecodeError:
                props = None
    except urllib.error.HTTPError as e:
        props_err = f"/props answered HTTP {e.code}"
    except Exception as e:
        props_err = f"could not reach {root} ({type(e).__name__})"
    if props is not None and is_llamacpp_props(props):
        result['found'] = True
        result['identified_by'] = 'props'
        result['props_model'] = llamacpp_model_from_props(props)
    elif props is not None:
        props_err = "/props answered but is not llama.cpp"

    # 1b) v0.15.1 — secondary identification: /props-less llama.cpp builds
    # (old releases, some forks) still fingerprint on /v1/models. The
    # payload fetched here is reused for the model list in step 3.
    models_payload = None
    models_headers = {}
    if not result['found']:
        try:
            code, raw, models_headers = _get_json(api + '/models')
            if code == 200:
                try:
                    models_payload = json.loads(raw)
                except json.JSONDecodeError:
                    models_payload = None
        except Exception:
            models_payload = None
        if models_payload is not None:
            fingerprint = _llamacpp_models_fingerprint(
                models_payload, models_headers)
            if fingerprint:
                result['found'] = True
                result['identified_by'] = f'models:{fingerprint}'
        if not result['found'] and not props_err:
            props_err = "no llama.cpp /props at this address"

    if not result['found']:
        result['detail'] = props_err or "no llama.cpp server at this address"
        return result

    # 2) /health — ready vs still-loading. llama-server answers 503 while
    # the model loads, and urllib raises on non-2xx — so the HTTPError
    # itself carries the code (a 503 here IS the "loading" answer).
    code = None
    raw = ''
    try:
        code, raw, _hdrs = _get_json(root + '/health')
    except urllib.error.HTTPError as e:
        code = e.code
        try:
            raw = e.read().decode('utf-8', errors='replace')
        except Exception:
            raw = ''
    except Exception:
        code = None  # unreachable /health — not an error
    if code is not None:
        body = {}
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {}
        if code == 200 and str(body.get('status', 'ok')).lower() != 'loading':
            result['ready'] = True
        else:
            result['ready'] = False

    # 3) /v1/models — the model list (a missing route is tolerated). The
    # secondary identification already fetched it → reuse that payload.
    if models_payload is not None:
        result['models'] = _model_ids_from_models_payload(models_payload)
    else:
        try:
            result['models'] = openai_list_models(api, api_key, int(timeout_s))
        except CloudLLMError:
            result['models'] = []

    # Best name: what the server ADVERTISES (/v1/models) wins; the /props
    # name is the fallback for builds that hide the route.
    result['model'] = (result['models'][0] if result['models']
                       else result['props_model'])

    state = ('ready' if result['ready'] else
             'still loading' if result['ready'] is False else
             'state unknown')
    result['detail'] = (
        f"llama.cpp server at {root} ({state})"
        + (f" · model '{result['model']}'" if result['model'] else ''))
    return result


def detect_llamacpp(api_key='', ports=None,
                    host='127.0.0.1', timeout_s=LLAMACPP_PROBE_TIMEOUT_S,
                    include_process_ports=True):
    """Find a llama.cpp server on this machine (v0.15.1 — the auto-detect
    engine behind "the app must automatically catch that!"). Candidate
    ports, in order:

      1. the LISTENING ports of every running llama-server/llamafile
         PROCESS (``llamacpp_process_ports`` — tasklist+netstat on
         Windows, ss on POSIX) — catches ANY ``--port``, including ports
         no guess list would ever contain;
      2. the common llama-server ports (``ports``, default
         LLAMACPP_SCAN_PORTS — pass an explicit list/() to restrict).

    Returns the probe result of the FIRST server that positively
    identifies as llama.cpp — its ``base_url`` names where, and ``via``
    ('process' | 'scan') names which candidate list found it — or ``None``
    when nothing matches. Dead local ports refuse instantly, so the scan
    is fast when nothing is running; live non-llama.cpp servers cost at
    most one probe timeout each and are skipped, never misreported."""
    proc_ports = []
    if include_process_ports:
        try:
            proc_ports = [int(p) for p in llamacpp_process_ports()]
        except Exception:
            proc_ports = []
    scan_ports = list(LLAMACPP_SCAN_PORTS if ports is None else (ports or ()))
    candidates = []
    for port in proc_ports + scan_ports:
        if port not in candidates:
            candidates.append(port)
    for port in candidates:
        probe = probe_llamacpp(f'http://{host}:{int(port)}', api_key,
                               timeout_s)
        if probe.get('found'):
            probe['via'] = 'process' if port in proc_ports else 'scan'
            return probe
    return None


# ===========================================================================
# v0.15.1 — process-based discovery ("the service is running on task
# manager"): read llama-server's ACTUAL listening ports from the OS
# process table instead of guessing. Pure stdlib; every entry point is
# wrapped so nothing here can ever raise or hang the caller.
# ===========================================================================

# Image basenames (lowercase, .exe stripped) that count as llama-server.
_LLAMACPP_PROCESS_NAMES = ('llama-server', 'llamafile', 'ik_llama_server',
                           'llama-server-bin')


def _run_cmd(argv, timeout_s=5.0):
    """``subprocess.run`` with a hard timeout, text output and NO console
    window on Windows (the packaged GUI runs windowless — a flashing cmd
    popup on every Detect would be a regression). Returns stdout or '' on
    ANY failure."""
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' \
        else 0
    try:
        out = subprocess.run(argv, capture_output=True, text=True,
                             timeout=timeout_s, creationflags=flags)
        return out.stdout or ''
    except Exception:
        return ''


def _parse_tasklist_pids(csv_text):
    """Windows ``tasklist /FO CSV /NH`` stdout → the set of PIDs whose
    image is a llama-server. Lines look like::

        "llama-server.exe","12345","Console","1","123,456 K"

    csv handles the quoting; a non-numeric PID column (localized headers,
    summary rows) is skipped. Pure string parsing — unit-tested against
    canned output on any OS."""
    import csv as _csv
    pids = set()
    for row in _csv.reader(str(csv_text or '').splitlines()):
        if len(row) < 2:
            continue
        image = row[0].strip().lower()
        if image.endswith('.exe'):
            image = image[:-4]
        if image not in _LLAMACPP_PROCESS_NAMES \
                and not image.startswith('llama-server'):
            continue
        pid = row[1].strip()
        if pid.isdigit():
            pids.add(int(pid))
    return pids


def _parse_netstat_listening_ports(netstat_text, pids):
    """Windows ``netstat -ano -p TCP`` stdout → ordered unique LISTENING
    ports owned by ``pids``. Handles IPv4 (``127.0.0.1:8080``) and IPv6
    (``[::]:8080`` / ``[::1]:8080``) local addresses; ESTABLISHED /
    TIME_WAIT noise is skipped. Pure string parsing — unit-tested against
    canned output on any OS."""
    ports = []
    for line in str(netstat_text or '').splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != 'TCP':
            continue
        if parts[3].upper() != 'LISTENING':
            continue
        local = parts[1]
        if ':' not in local:
            continue
        try:
            port = int(local.rsplit(':', 1)[1].strip('[]'))
        except ValueError:
            continue
        try:
            pid = int(parts[4])
        except ValueError:
            continue
        if pid in pids and port not in ports:
            ports.append(port)
    return ports


def _llamacpp_ports_windows():
    """tasklist → llama-server PIDs → netstat → their LISTENING ports."""
    pids = _parse_tasklist_pids(_run_cmd(['tasklist', '/FO', 'CSV', '/NH']))
    if not pids:
        return []
    return _parse_netstat_listening_ports(
        _run_cmd(['netstat', '-ano', '-p', 'TCP']), pids)


def _llamacpp_ports_posix():
    """``ss -ltnp`` (netstat fallback) → the LISTENING ports of any
    llama-server/llamafile process. A typical line::

        LISTEN 0 4096 127.0.0.1:8080 0.0.0.0:0 \
            users:(("llama-server",pid=1234,fd=3))
    """
    out = _run_cmd(['ss', '-ltnp']) or _run_cmd(['netstat', '-ltnp'])
    ports = []
    for line in out.splitlines():
        low = line.lower()
        if 'llama-server' not in low and 'llamafile' not in low \
                and 'ik_llama_server' not in low:
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        local = parts[3]
        if ':' not in local:
            continue
        try:
            port = int(local.rsplit(':', 1)[1].strip('[]'))
        except ValueError:
            continue
        if port not in ports:
            ports.append(port)
    return ports


def llamacpp_process_ports():
    """The listening ports of every llama-server/llamafile process on
    this machine — the Task-Manager guarantee (v0.15.1): if llama-server
    is RUNNING, its port is here, whatever ``--port`` it was given.
    Windows: tasklist + netstat; POSIX: ss/netstat. Never raises, never
    blocks longer than the 5s subprocess cap; [] on any failure or when
    nothing runs."""
    try:
        if os.name == 'nt':
            return _llamacpp_ports_windows()
        return _llamacpp_ports_posix()
    except Exception:
        return []


def ollama_reachable(base_url='http://127.0.0.1:11434', timeout_s=1.5):
    """Quick socket-level check (v0.15.1 auto-switch policy): is an
    Ollama server listening at ``base_url``? No HTTP, no ollama SDK
    import, never raises — used to decide whether switching an
    unresponsive default provider to a detected llama.cpp server is
    warranted."""
    from urllib.parse import urlparse
    import socket
    try:
        parts = urlparse(str(base_url or '')
                         or 'http://127.0.0.1:11434')
        host = parts.hostname or '127.0.0.1'
        port = parts.port or 11434
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(float(timeout_s))
        try:
            return sock.connect_ex((host, int(port))) == 0
        finally:
            sock.close()
    except Exception:
        return False


def detect_ollama(base_url='http://127.0.0.1:11434', timeout_s=4.0):
    """v0.18.0 — the Ollama twin of :func:`probe_llamacpp`: ONE ``/api/tags``
    probe that answers "is the Ollama server up, and which models are
    installed?". Never raises — the "🧠 Detect & Set Ollama" fast lane.

    Raw HTTP (no ollama SDK): thread-safe for background workers and
    testable against a plain stdlib http.server. Loopback targets bypass
    the system proxy (the v0.15.1 rule); a remote Ollama host honors it.

    Returns a result dict shaped like probe_llamacpp's:

      found     True when /api/tags answered with parsable JSON
      base_url  the normalized base (scheme://host:port — no trailing slash)
      models    the installed model names (``[]`` when none / unparsable)
      detail    one human-readable line ('up · N model(s)' or the failure)
    """
    import urllib.request

    raw = str(base_url or '').strip() or 'http://127.0.0.1:11434'
    if '://' not in raw:
        raw = 'http://' + raw
    base = raw.rstrip('/')
    req = urllib.request.Request(base + '/api/tags',
                                 headers={'User-Agent': 'GitCurator-detect'})
    try:
        if _is_loopback_url(base):
            resp = _urlopen_direct(req, timeout_s)
        else:
            resp = urllib.request.urlopen(req, timeout=timeout_s)
        try:
            body = resp.read().decode('utf-8', errors='replace') or '{}'
        finally:
            try:
                resp.close()
            except Exception:
                pass
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError('response is not a JSON object')
        models = []
        for m in payload.get('models') or []:
            if not isinstance(m, dict):
                continue
            name = str(m.get('name') or m.get('model') or '').strip()
            if name:
                models.append(name)
        # de-duplicate, keep the server's order
        seen = set()
        ordered = [n for n in models
                   if not (n in seen or seen.add(n))]
        detail = f'up · {len(ordered)} model(s)' if ordered \
            else 'up but NO models installed'
        return {'found': True, 'base_url': base, 'models': ordered,
                'detail': detail}
    except Exception as e:
        return {'found': False, 'base_url': base, 'models': [],
                'detail': f'{type(e).__name__}: {e}'}


def llamacpp_autodetect_decision(config, probe, ollama_up=None):
    """v0.15.1 — what the STARTUP auto-detect should do once a llama.cpp
    server has been found (the owner: "the app must automatically catch
    that!"). Pure function — the GUI thread applies the result; the policy
    stays unit-testable without Qt.

    Policy — never override a provider choice that WORKS:
      * provider already 'llamacpp'     → refresh URL/model (no switch)
      * provider 'ollama' + Ollama DOWN → SWITCH (the default provider's
        server is dead; the local engine the user actually has running
        takes over — this is the owner's exact situation)
      * provider 'cloud' + no API key  → SWITCH (cloud is unusable)
      * anything else (a working Ollama, a keyed cloud endpoint, a custom
        endpoint) → NO switch, one hint line only

    Returns a dict: ``switch`` (bool), ``url`` (the /v1 URL to persist —
    set only when it differs from the configured one), ``model`` (the
    detected model name, or None), ``message`` (one log line for the
    GUI), ``level`` (its log level)."""
    cfg = config if isinstance(config, dict) else {}
    if not (probe or {}).get('found'):
        return {'switch': False, 'url': None, 'model': None,
                'message': '', 'level': 'info'}
    root = (probe.get('base_url') or LLAMACPP_DEFAULT_BASE).rstrip('/')
    url = root if root.endswith('/v1') else root + '/v1'
    model = probe.get('model')
    provider = str(cfg.get('llm_provider', 'ollama') or 'ollama').lower()
    configured_url = str(cfg.get('llamacpp_api_url', '') or '').strip()
    configured_model = str(cfg.get('llamacpp_model', '') or '').strip()
    url_changed = configured_url != url
    model_changed = bool(model) and model != configured_model
    where = f"🦙 llama.cpp detected at {root}" \
        + (f" · model '{model}'" if model else '')

    if provider == 'llamacpp':
        note = where
        if url_changed and configured_url:
            note += f" (was {configured_url})"
        return {'switch': False,
                'url': url if url_changed else None,
                'model': model if model_changed else None,
                'message': note, 'level': 'success'}

    switch = False
    why = ''
    if provider == 'ollama' and ollama_up is False:
        switch = True
        why = " — Ollama is not running, switching the LLM provider to " \
              "llama.cpp"
    elif provider == 'cloud' and not str(
            cfg.get('cloud_api_key', '') or '').strip():
        switch = True
        why = " — no cloud API key is configured, switching the LLM " \
              "provider to llama.cpp"
    if switch:
        return {'switch': True, 'url': url if url_changed else None,
                'model': model or '',
                'message': f"🦙 llama.cpp caught automatically at {root}"
                + (f" · model '{model}'" if model else '') + why + ".",
                'level': 'success'}
    return {'switch': False, 'url': None, 'model': None,
            'message': where + " — Settings → 🧠 LLM → 🦙 llama.cpp to "
            "use it.", 'level': 'info'}


def resolve_llamacpp_model(config, models, props_model=None):
    """v0.15.0 — "its model detected automatically": the model to use for
    llama.cpp calls. Order: (1) the configured ``llamacpp_model`` when set
    (a deliberate choice always wins — llama-server serves the loaded
    model, and the caller warns when it is not in the advertised list);
    (2) the first advertised ``/v1/models`` entry; (3) the ``/props``
    alias/basename; (4) None (caller decides — usually a clear error)."""
    configured = str((config or {}).get('llamacpp_model') or '').strip()
    if configured:
        return configured
    if models:
        return models[0]
    return props_model or None
