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
