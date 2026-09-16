#!/usr/bin/env python3
"""
storage.py — atomic filesystem writes + path utilities.

v30 — Fix (Atomic note/banner writes): main.py wrote notes with a plain
open(path, 'w') (main.py:1812) and banners the same way (main.py:2871).
A crash / power loss / disk-full mid-write left a TRUNCATED note that the
cache then recorded as processed — silent data corruption. This module
guarantees readers only ever see the old complete file or the new complete
file, never a partial one.

Also home for the collision-safe filename logic that used to live inline
in the worker loop, and a pure, unit-testable config merge used by
MainWindow.save_config() (v30 — Fix: merge, never whitelist-rebuild).

Pure stdlib — no PyQt, no third-party deps.
"""

import json
import os
import re
import tempfile
from typing import Dict, Any


# ---------------------------------------------------------------------------
# Atomic writes
# ---------------------------------------------------------------------------

def atomic_write_text(path: str, content: str, encoding: str = 'utf-8') -> None:
    """Atomically write ``content`` to ``path``.

    Writes to a NamedTemporaryFile in the SAME directory (so os.replace
    stays on one filesystem), fsyncs, then os.replace()s over the target.
    Either the old file survives untouched or the new complete file exists.
    """
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix='.tmp_', suffix=os.path.splitext(os.path.basename(path))[1] or '.tmp',
        dir=directory,
    )
    try:
        with os.fdopen(fd, 'w', encoding=encoding, newline='') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        # Windows: os.replace over an open target fails — but our writers
        # are the only writers and targets are closed by the time we get
        # here. os.replace is atomic on both NTFS and POSIX.
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_write_bytes(path: str, data: bytes) -> None:
    """Atomically write binary ``data`` to ``path`` (banners, images)."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix='.tmp_', suffix=os.path.splitext(os.path.basename(path))[1] or '.tmp',
        dir=directory,
    )
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def safe_filename(name: str) -> str:
    """Make a string safe for use as a filename component.

    Strips everything outside [a-zA-Z0-9-_] — this ALSO defeats path
    traversal (no '..' or '/' or '\\\\' can survive).
    """
    return re.sub(r'[^a-zA-Z0-9\-_]+', '_', name or '')


def unique_path(path: str) -> str:
    """Return a non-colliding path: appends _v1, _v2, ... if ``path`` exists.

    Race note: the caller re-checks existence immediately before writing;
    if the final write must be exclusive, atomic_write_* + this function is
    still a huge improvement over the previous open('w') non-atomic write.
    """
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    counter = 1
    while True:
        candidate = f"{base}_v{counter}{ext}"
        if not os.path.exists(candidate):
            return candidate
        counter += 1


def build_note_filename(repo_name: str, category_key: str, tags) -> str:
    """Canonical note filename: <repo>_<category>_<first-tag>.md."""
    safe_name = safe_filename(repo_name)
    secondary = safe_filename(tags[0]) if tags else "misc"
    category = safe_filename(category_key.replace('/', '_'))
    return f"{safe_name}_{category}_{secondary}.md"


# ---------------------------------------------------------------------------
# Config merge (pure — unit-testable without PyQt)
# ---------------------------------------------------------------------------

def merge_config(existing: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    """Merge UI-derived ``updates`` into ``existing`` WITHOUT dropping keys.

    v30 — Fix: save_config() used to whitelist-rebuild the dict from ~25
    hardcoded keys, silently DESTROYING every other key in config.json
    (cloudflare_install_id, cloudflare_shared_secret, gdrive_*,
    hmac_secret, user-added keys...). It also hardcoded
    timeout_per_repo=60 / max_retries=3 / delay_between_api_calls=0.5,
    stomping user-tuned values on every save.

    Rules:
      - unknown keys in ``existing`` are PRESERVED
      - nested dicts (e.g. 'ollama', 'proxy') are MERGED key-by-key,
        not replaced wholesale
      - keys in ``updates`` win over ``existing``
      - tuning keys (timeout_per_repo, max_retries,
        delay_between_api_calls) are only defaulted when ABSENT —
        never overwritten
    """
    merged: Dict[str, Any] = dict(existing)
    for key, value in updates.items():
        if (key in merged and isinstance(merged[key], dict)
                and isinstance(value, dict)):
            nested = dict(merged[key])
            nested.update(value)
            merged[key] = nested
        else:
            merged[key] = value
    # Tuning keys: default only, never stomp.
    merged.setdefault('timeout_per_repo', 60)
    merged.setdefault('max_retries', 3)
    merged.setdefault('delay_between_api_calls', 0.5)
    return merged


def write_config_file(path: str, config: Dict[str, Any]) -> None:
    """Pretty-print + atomically write a config dict (UTF-8, no BOM)."""
    atomic_write_text(path, json.dumps(config, indent=2, ensure_ascii=False))
