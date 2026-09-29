#!/usr/bin/env python3
"""
embeddings.py — Phase 6, step 2 (SPEC §4.8 / §6): local embeddings.

"Embed only the recall field, the one-line description and the tags.
Never full text." This module turns those three fields into one short
embedding TEXT per note, sends it to a LOCAL embedding endpoint, and
keeps the vectors in SQLite (same cache.db, own table — the NoteStateDB
pattern).

Endpoints (provider-aware, mirroring the chat router):

* Ollama       — POST /api/embed  {"model", "input": [texts]}
                 (legacy /api/embeddings {"model", "prompt"} as fallback
                 for older servers, one text per call)
* llama.cpp /  — POST <base>/v1/embeddings  {"model", "input": [texts]}
  OpenAI-comp    (llama-server with --embeddings speaks this natively;
                 any OpenAI-compatible endpoint does)

Every loopback call bypasses the system proxy (the v0.15.1 rule — a
VPN/proxy client must never swallow a local probe). Vectors are stored
as JSON (pure stdlib, no numpy dependency) and compared with a plain
cosine — the corpus is ~10³ notes, not millions.

Pure stdlib, no PyQt — importable from the CLI, tests and CI.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from gitcurator.constants import APP_DIR
from gitcurator.core.llm_client import _is_loopback_url, _urlopen_direct

# Batching: Ollama and llama.cpp both accept an input list; 16 keeps
# single requests small and progress granular.
EMBED_BATCH = 16

# Provider defaults for the embedding model (config embedding_model wins).
OLLAMA_EMBED_DEFAULT = 'nomic-embed-text'


class EmbeddingError(Exception):
    """Any embedding failure — always carries a human-readable cause."""


# ---------------------------------------------------------------------------
# The embedding text (SPEC: recall field + one-line + tags, nothing else)
# ---------------------------------------------------------------------------

def embed_text_for(fields: Dict[str, str]) -> str:
    """The exact text embedded for a note (``recall_fields_for`` output):
    name, one-line description, the recall sentence and the tags — a few
    dozen words, NEVER the full note."""
    name = str(fields.get('name') or '').strip()
    one_line = str(fields.get('one_line') or '').strip()
    recall = str(fields.get('recall') or '').strip()
    tags = str(fields.get('tags') or '').strip()
    parts = [p for p in (name, one_line, recall,
                         f"tags: {tags}" if tags else '') if p]
    return "\n".join(parts)[:1200]


def hash_embed_text(text: str) -> str:
    """Staleness key: the embedding is refreshed only when the embedded
    text (not the whole note) changes."""
    return hashlib.sha256((text or '').encode('utf-8', 'replace')) \
        .hexdigest()


# ---------------------------------------------------------------------------
# Vector math (pure Python — no numpy in the dependency tree)
# ---------------------------------------------------------------------------

def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity; 0.0 for empty or mismatched vectors (never
    raises — a broken row degrades to 'unrelated', it does not kill the
    run)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = norm_a = norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / ((norm_a ** 0.5) * (norm_b ** 0.5))


# ---------------------------------------------------------------------------
# Endpoint calls (both never-proxy for loopback — the v0.15.1 rule)
# ---------------------------------------------------------------------------

def _post_json(url: str, payload: dict, timeout_s: float) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'})
    if _is_loopback_url(url):                    # v0.15.1 — never proxy
        opener = _urlopen_direct(req, timeout_s)
    else:
        opener = urllib.request.urlopen(req, timeout=timeout_s)
    with opener as resp:
        raw = resp.read().decode('utf-8', errors='replace')
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EmbeddingError(
            f"{url} returned a non-JSON body: {raw[:120]!r}") from exc


def ollama_embed(host: str, model: str, texts: List[str],
                 timeout_s: float = 60.0) -> List[List[float]]:
    """Embed via a local Ollama. Modern servers: one batched ``/api/embed``
    call; older ones: ``/api/embeddings`` per text (the fallback is
    detected, not configured — a 404/405 on /api/embed means 'old
    server', not 'dead server'). Returns one vector per text, input
    order."""
    host = (host or 'http://127.0.0.1:11434').rstrip('/')
    try:
        payload = _post_json(f'{host}/api/embed',
                             {'model': model, 'input': list(texts)},
                             timeout_s)
        vectors = payload.get('embeddings')
        if isinstance(vectors, list) and len(vectors) == len(texts):
            return [[float(x) for x in v] for v in vectors]
    except (EmbeddingError, urllib.error.HTTPError,
            urllib.error.URLError, OSError):
        pass                                        # fall through to legacy
    vectors = []
    for text in texts:
        payload = _post_json(f'{host}/api/embeddings',
                             {'model': model, 'prompt': text}, timeout_s)
        vec = payload.get('embedding')
        if not isinstance(vec, list):
            raise EmbeddingError(
                f"Ollama {host} returned no embedding for the model "
                f"'{model}' (pull it first: ollama pull {model})")
        vectors.append([float(x) for x in vec])
    return vectors


def openai_embed(api_url: str, model: str, texts: List[str],
                 api_key: str = '', timeout_s: float = 60.0
                 ) -> List[List[float]]:
    """Embed via any OpenAI-compatible ``/embeddings`` endpoint —
    llama-server with --embeddings, LM Studio, vLLM, or a cloud API.
    Returns one vector per text, input order (the response's ``index``
    field is honored, not assumed)."""
    base = (api_url or '').rstrip('/')
    if not base:
        raise EmbeddingError("no embeddings API URL configured")
    req = urllib.request.Request(
        base + '/embeddings',
        data=json.dumps({'model': model, 'input': list(texts)})
        .encode('utf-8'),
        headers={'Content-Type': 'application/json'})
    if api_key:
        req.add_header('Authorization', f'Bearer {api_key}')
    if _is_loopback_url(base):                   # v0.15.1 — never proxy
        resp_ctx = _urlopen_direct(req, timeout_s)
    else:
        resp_ctx = urllib.request.urlopen(req, timeout=timeout_s)
    with resp_ctx as resp:
        raw = resp.read().decode('utf-8', errors='replace')
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EmbeddingError(
            f"{base}/embeddings returned a non-JSON body") from exc
    data = payload.get('data')
    if not isinstance(data, list) or len(data) != len(texts):
        raise EmbeddingError(
            f"{base}/embeddings returned "
            f"{len(data) if isinstance(data, list) else 0} embedding(s) "
            f"for {len(texts)} text(s)")
    by_index = {}
    for item in data:
        if isinstance(item, dict) and isinstance(item.get('embedding'),
                                                  list):
            by_index[int(item.get('index', 0))] = \
                [float(x) for x in item['embedding']]
    try:
        return [by_index[i] for i in range(len(texts))]
    except KeyError as exc:
        raise EmbeddingError(
            f"{base}/embeddings response missing index {exc}") from exc


def build_embedding_call(config: Dict, model_override: str = ''
                         ) -> Tuple[Callable[[List[str]], List[List[float]]],
                                    str]:
    """Config → (embed_texts, model_label). Same provider routing as the
    chat paths: llamacpp → the detected server's /v1/embeddings; cloud →
    the configured endpoint; ollama (default) → /api/embed. The model
    comes from config ``embedding_model`` or the provider default."""
    cfg = config or {}
    provider = str(cfg.get('llm_provider', 'ollama') or 'ollama').lower()
    model = str(model_override or cfg.get('embedding_model', '')
                or '').strip()
    timeout = float(cfg.get('llm_timeout_s', 300) or 300) or 60.0

    if provider == 'llamacpp':
        from gitcurator.core import llm_client as _llm
        base = str(cfg.get('llamacpp_api_url', '')
                   or _llm.LLAMACPP_DEFAULT_BASE + '/v1')
        api_url = _llm.normalize_llamacpp_api_url(base)
        key = str(cfg.get('llamacpp_api_key', '') or '')
        if not model:
            model = str(cfg.get('llamacpp_model', '') or '').strip()
        label = f"llama.cpp {api_url} · {model or '(server model)'}"

        def embed(texts):
            return openai_embed(api_url, model, texts, key, timeout)
        return embed, label

    if provider == 'cloud' or provider == 'custom':
        api_url = str(cfg.get('cloud_api_url', '') or '').rstrip('/')
        key = str(cfg.get('cloud_api_key', '') or '')
        if not api_url:
            raise EmbeddingError(
                "cloud provider needs cloud_api_url for embeddings")
        if not model:
            model = str(cfg.get('cloud_model', '') or '').strip()
        if not model:
            raise EmbeddingError(
                "cloud provider needs an embedding model (config "
                "embedding_model, or --embed-model)")
        label = f"{api_url} · {model}"

        def embed(texts):
            return openai_embed(api_url, model, texts, key, timeout)
        return embed, label

    oll = cfg.get('ollama') or {}
    host = (oll.get('base_url') if isinstance(oll, dict) else '') \
        or str(cfg.get('ollama_url', '') or '') \
        or 'http://127.0.0.1:11434'
    model = model or str(oll.get('model', '') or '') \
        or OLLAMA_EMBED_DEFAULT
    label = f"Ollama {host} · {model}"

    def embed(texts):
        return ollama_embed(host, model, texts, timeout)
    return embed, label


# ---------------------------------------------------------------------------
# SQLite store (same cache.db, own table — the NoteStateDB pattern)
# ---------------------------------------------------------------------------

class EmbeddingStore:
    """Vectors keyed by (vault, source_url, model). JSON-serialized —
    pure stdlib. Own connection + lock; CacheDB is not touched."""

    def __init__(self, db_path: str = "cache.db"):
        if db_path == "cache.db":            # the v0.09.4 resolution rule
            db_path = os.path.join(APP_DIR, "cache.db")
        self.db_path = db_path
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False,
                                    timeout=30)
        self.conn.execute("PRAGMA busy_timeout = 30000")
        with self._lock:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS embeddings (
                    vault TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    model TEXT NOT NULL,
                    dim INTEGER NOT NULL,
                    vec TEXT NOT NULL,
                    text_hash TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (vault, source_url, model)
                )""")
            self.conn.commit()

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    def put(self, vault: str, source_url: str, model: str,
            vector: List[float], text_hash: str) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO embeddings (vault, source_url, model, dim,"
                " vec, text_hash, updated_at) VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(vault, source_url, model) DO UPDATE SET"
                " dim=excluded.dim, vec=excluded.vec,"
                " text_hash=excluded.text_hash,"
                " updated_at=excluded.updated_at",
                (vault, source_url, model, len(vector),
                 json.dumps(vector), text_hash,
                 time.strftime('%Y-%m-%d %H:%M:%S')))
            self.conn.commit()

    def load(self, vault: str, model: str) -> Dict[str, Tuple[List[float],
                                                              str]]:
        """{source_url: (vector, text_hash)} for one vault+model."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT source_url, vec, text_hash FROM embeddings"
                " WHERE vault=? AND model=?", (vault, model)).fetchall()
        out = {}
        for url, vec_json, text_hash in rows:
            try:
                out[url] = ([float(x) for x in json.loads(vec_json)],
                            text_hash)
            except (json.JSONDecodeError, TypeError, ValueError):
                continue                  # a broken row degrades to absent
        return out

    def drop_model(self, vault: str, model: str) -> int:
        with self._lock:
            cur = self.conn.execute(
                "DELETE FROM embeddings WHERE vault=? AND model=?",
                (vault, model))
            self.conn.commit()
            return cur.rowcount

    def count(self, vault: Optional[str] = None) -> int:
        with self._lock:
            if vault is None:
                return self.conn.execute(
                    "SELECT COUNT(*) FROM embeddings").fetchone()[0]
            return self.conn.execute(
                "SELECT COUNT(*) FROM embeddings WHERE vault=?",
                (vault,)).fetchone()[0]


# ---------------------------------------------------------------------------
# Refresh (the tool layer drives this)
# ---------------------------------------------------------------------------

def refresh_embeddings(entries: List[Dict], embed: Callable, store:
                       EmbeddingStore, vault: str, model: str, *,
                       force: bool = False,
                       progress: Optional[Callable[[str], None]] = None
                       ) -> Dict[str, int]:
    """Embed the stale notes only. ``entries`` are
    ``{source_url, fields}`` dicts (fields = recall_fields_for output);
    ``embed(texts)`` → vectors. Returns ``{embedded, skipped_fresh,
    failed}``. Failures never abort the batch — the note simply keeps
    its old vector (or stays absent)."""
    say = progress or (lambda _msg: None)
    existing = store.load(vault, model)
    embedded = skipped = failed = 0
    batch: List[Dict] = []

    def flush(batch_entries):
        nonlocal embedded, failed
        if not batch_entries:
            return
        try:
            vectors = embed([e['embed_text'] for e in batch_entries])
            if len(vectors) != len(batch_entries):
                raise EmbeddingError(
                    f"endpoint returned {len(vectors)} vector(s) for "
                    f"{len(batch_entries)} text(s)")
        except Exception as exc:                      # noqa: BLE001
            failed += len(batch_entries)
            say(f"  ! batch of {len(batch_entries)} failed: {exc}")
            batch_entries.clear()
            return
        for entry, vector in zip(batch_entries, vectors):
            store.put(vault, entry['source_url'], model, vector,
                      entry['text_hash'])
            embedded += 1
        batch_entries.clear()

    for entry in entries:
        text = embed_text_for(entry.get('fields') or {})
        text_hash = hash_embed_text(text)
        old = existing.get(entry.get('source_url', ''))
        if not force and old and old[1] == text_hash:
            skipped += 1
            continue
        batch.append({'source_url': entry.get('source_url', ''),
                      'embed_text': text, 'text_hash': text_hash})
        if len(batch) >= EMBED_BATCH:
            flush(batch)
    flush(batch)
    return {'embedded': embedded, 'skipped_fresh': skipped, 'failed': failed}
