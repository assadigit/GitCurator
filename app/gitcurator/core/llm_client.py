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
import json
import re
from typing import Any, Callable, List

DEFAULT_TIMEOUT_S = 300      # chat — generous, models are slow to warm
DEFAULT_LIST_TIMEOUT_S = 15  # list/connectivity — should be instant


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

# The canonical label for the cloud provider option (SPEC §6 Phase 4:
# relabel "Cloud API" — GUI radio, settings group, CLI wizard and README
# all import this single constant).
CLOUD_PROVIDER_LABEL = (
    "OpenAI-compatible endpoint (llama.cpp, vLLM, LM Studio, cloud)")

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
                num_ctx=None, on_warn=None):
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
# Ollama chat with the explicit context window
# ---------------------------------------------------------------------------

def ollama_chat(client, model, messages, timeout_s, *, json_mode=True,
                num_ctx=None, on_warn=None):
    """``client.chat`` with the SAME wall-clock timeout wrapper as always,
    plus the explicit context window: ``options.num_ctx`` is sent on every
    call (Ollama's own default is small and truncates long prompts from
    the front SILENTLY — an explicit window turns that into a deliberate,
    visible choice). When the estimated prompt exceeds ``num_ctx`` the
    caller is warned through ``on_warn`` BEFORE the call — nothing is ever
    chopped quietly. ``num_ctx=None`` (or 0) leaves the window to the
    server. Returns the assistant content string (both ollama-py response
    shapes handled by ``response_content``)."""
    kwargs = {'model': model, 'messages': messages}
    if num_ctx and int(num_ctx) > 0:
        num_ctx = int(num_ctx)
        kwargs['options'] = {'num_ctx': num_ctx}
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
