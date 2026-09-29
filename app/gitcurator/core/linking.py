#!/usr/bin/env python3
"""
linking.py — Phase 6, steps 3–4 (SPEC §4.8 / §6): candidate search,
LLM confirmation, the link store and the user-facing surfaces.

The pipeline (the tool layer drives it):

1. ``collect_note_fields`` — walk both machine vaults, read every
   managed note, extract the recall fields (name / one-line / recall /
   tags) keyed by the note's normalized ``source:`` URL (the identity
   everything else already uses).
2. ``find_candidate_pairs`` — cosine neighbors from the embedding store
   (recall field + one-line + tags embedded, never full text), across
   domains AND within a domain.
3. ``confirm_pair`` — one LLM call per candidate: yes/no + a short
   reason (prompt ``l01_confirm``; ``about_me.md`` is NOT used).
4. ``LinkStore`` — persisted suggestions with status
   pending/approved/rejected. A pair is suggested at most ONCE:
   rejected pairs never reappear, pending ones are not duplicated.
5. Output surfaces — and ONLY these (tested):
   * the ``Library/`` MIRROR copies get a delimited "Related (auto)"
     block (never the machine vaults, never the owner's notes);
   * one ``Suggestions`` note under ``Library/`` lists the pending
     suggestions as Obsidian checkboxes: tick to approve, strike the
     line through to reject. ``collect_decisions`` reads them back.
   * the per-note cap is 5–7 approved links (LINKS_CAP).

Pure stdlib, no PyQt — importable from the CLI, tests and CI.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from gitcurator.constants import APP_DIR
from gitcurator.core.links import normalize_url
from gitcurator.core.note_state import (VAULT_GITHUB, VAULT_WEBSITES,
                                        scan_vault)
from gitcurator.core.prompts import load_prompt
from gitcurator.core.recall import recall_fields_for
from gitcurator.core.storage import atomic_write_text

# The delimited "Related (auto)" block (mirror copies only).
RELATED_START = "<!-- gitcurator:related:start -->"
RELATED_END = "<!-- gitcurator:related:end -->"

# SPEC §4.8: "Cap of about 5 to 7 links per note."
LINKS_CAP = 7
LINKS_TARGET = 5

# Candidate search defaults.
DEFAULT_TOP_K = 8
DEFAULT_MIN_SCORE = 0.30

SUGGESTIONS_FILENAME = "Suggestions.md"

STATUS_PENDING = 'pending'
STATUS_APPROVED = 'approved'
STATUS_REJECTED = 'rejected'


# ---------------------------------------------------------------------------
# 1) Collect the recall fields from both machine vaults
# ---------------------------------------------------------------------------

def collect_note_fields(vaults: Dict[str, str]) -> Dict[str, Dict]:
    """``{'github': path, 'websites': path}`` →
    ``{source_url: {'vault', 'path', 'fields'}}``.

    Managed notes only (a ``source:`` line — the identity everything
    already keys on); the linking layer never touches unmanaged files.
    Duplicate sources keep the FIRST hit and record a warning entry
    (key ``__duplicate__`` counts them). Never raises."""
    out: Dict[str, Dict] = {}
    duplicates = 0
    for vault, vault_path in (vaults or {}).items():
        if not vault_path or not os.path.isdir(vault_path):
            continue
        for note in scan_vault(vault_path):
            if note.get('unmanaged'):
                continue
            url = note.get('source_url') or ''
            if not url:
                continue
            if url in out:
                duplicates += 1
                continue
            try:
                with open(note['path'], 'r', encoding='utf-8',
                          errors='replace') as f:
                    text = f.read()
            except OSError:
                continue
            out[url] = {'vault': vault, 'path': note['path'],
                        'fields': recall_fields_for(text, vault)}
    if duplicates:
        out['__duplicate__'] = {'count': duplicates}
    return out


# ---------------------------------------------------------------------------
# 2) Candidate pairs (cosine neighbors on the stored vectors)
# ---------------------------------------------------------------------------

def find_candidate_pairs(embeddings: Dict[str, Sequence[float]],
                         *, top_k: int = DEFAULT_TOP_K,
                         min_score: float = DEFAULT_MIN_SCORE
                         ) -> List[Tuple[str, str, float]]:
    """Neighbors across ALL notes (both vaults — "across domains and
    within a domain"). Returns ``[(key_a, key_b, score)]`` with each
    unordered pair listed once (a<b lexicographically), sorted by score
    descending. ``top_k`` bounds per-note candidates; ``min_score``
    prunes weak matches."""
    from gitcurator.core.embeddings import cosine
    keys = [k for k, v in embeddings.items() if v]
    best: Dict[Tuple[str, str], float] = {}
    for i, key_a in enumerate(keys):
        vec_a = embeddings[key_a]
        scored = []
        for key_b in keys:
            if key_b == key_a:
                continue
            score = cosine(vec_a, embeddings[key_b])
            if score >= min_score:
                scored.append((key_b, score))
        scored.sort(key=lambda s: s[1], reverse=True)
        for key_b, score in scored[:top_k]:
            pair = tuple(sorted((key_a, key_b)))
            if score > best.get(pair, -1.0):
                best[pair] = score
    return sorted(((a, b, s) for (a, b), s in best.items()),
                  key=lambda t: t[2], reverse=True)


# ---------------------------------------------------------------------------
# 3) The LLM confirmation
# ---------------------------------------------------------------------------

def build_confirm_prompt(fields_a: Dict[str, str],
                         fields_b: Dict[str, str]) -> str:
    """l01 filled for one pair (the loader refuses an unfilled slot)."""
    def slot(d, key, fallback):
        return str((d or {}).get(key) or '').strip()[:300] or fallback
    return load_prompt(
        'l01_confirm',
        A_NAME=slot(fields_a, 'name', '(unnamed)'),
        A_LINE=slot(fields_a, 'one_line', '(no description)'),
        A_RECALL=slot(fields_a, 'recall', '(not captured)'),
        A_TAGS=slot(fields_a, 'tags', '(none)'),
        B_NAME=slot(fields_b, 'name', '(unnamed)'),
        B_LINE=slot(fields_b, 'one_line', '(no description)'),
        B_RECALL=slot(fields_b, 'recall', '(not captured)'),
        B_TAGS=slot(fields_b, 'tags', '(none)'),
    )


def parse_confirm_reply(reply: str) -> Tuple[Optional[bool], str]:
    """LLM reply → (related, reason). ``None`` = unparseable → treated as
    NOT related (a wrong link costs more than a missing one)."""
    from gitcurator.core.llm_client import extract_json
    try:
        payload = extract_json(reply)
    except Exception:
        return None, ''
    related = payload.get('related')
    reason = str(payload.get('reason') or '').strip()[:200]
    if isinstance(related, bool):
        return related, reason
    if isinstance(related, str) and related.strip().lower() in (
            'true', 'yes', 'false', 'no'):
        return related.strip().lower() in ('true', 'yes'), reason
    return None, ''


def confirm_pair(fields_a: Dict[str, str], fields_b: Dict[str, str],
                 llm: Callable[[List[Dict]], str]) -> Tuple[Optional[bool],
                                                            str]:
    """One pair through the prompt → reply → parse pipeline."""
    reply = llm([{'role': 'user',
                  'content': build_confirm_prompt(fields_a, fields_b)}])
    return parse_confirm_reply(reply)


# ---------------------------------------------------------------------------
# 4) The link store
# ---------------------------------------------------------------------------

class LinkStore:
    """Persisted suggestions in cache.db (own table, own connection —
    the NoteStateDB pattern). A pair (stored sorted, a<b) is suggested
    at most once: rejected never reappears, pending is not duplicated,
    approved is kept for the Related blocks."""

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
                CREATE TABLE IF NOT EXISTS link_suggestions (
                    key_a TEXT NOT NULL,
                    key_b TEXT NOT NULL,
                    score REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    decided_at TEXT,
                    PRIMARY KEY (key_a, key_b)
                )""")
            self.conn.commit()

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    @staticmethod
    def _pair(a: str, b: str) -> Tuple[str, str]:
        return (a, b) if a <= b else (b, a)

    def known_pairs(self) -> Dict[Tuple[str, str], str]:
        """{(key_a, key_b): status} — everything ever suggested."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT key_a, key_b, status FROM link_suggestions") \
                .fetchall()
        return {(a, b): status for a, b, status in rows}

    def suggest(self, key_a: str, key_b: str, score: float,
                reason: str = '') -> Optional[str]:
        """Insert a NEW pending suggestion. Returns 'inserted' / the
        existing status when the pair is already known (a rejected pair
        is never suggested again — that is the point)."""
        a, b = self._pair(key_a, key_b)
        with self._lock:
            row = self.conn.execute(
                "SELECT status FROM link_suggestions WHERE key_a=? AND "
                "key_b=?", (a, b)).fetchone()
            if row:
                return row[0]
            self.conn.execute(
                "INSERT INTO link_suggestions (key_a, key_b, score,"
                " status, reason, created_at) VALUES (?,?,?,?,?,?)",
                (a, b, float(score), STATUS_PENDING, reason,
                 time.strftime('%Y-%m-%d %H:%M:%S')))
            self.conn.commit()
        return STATUS_PENDING

    def decide(self, key_a: str, key_b: str, status: str) -> bool:
        """pending → approved / rejected (decided_at stamped). Unknown
        pair or an already-decided one is a no-op returning False."""
        if status not in (STATUS_APPROVED, STATUS_REJECTED):
            raise ValueError(f"invalid status: {status}")
        a, b = self._pair(key_a, key_b)
        with self._lock:
            cur = self.conn.execute(
                "UPDATE link_suggestions SET status=?, decided_at=?"
                " WHERE key_a=? AND key_b=? AND status=?",
                (status, time.strftime('%Y-%m-%d %H:%M:%S'), a, b,
                 STATUS_PENDING))
            self.conn.commit()
            return cur.rowcount > 0

    def with_status(self, status: str) -> List[Dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT key_a, key_b, score, reason FROM link_suggestions"
                " WHERE status=? ORDER BY score DESC", (status,)).fetchall()
        return [{'key_a': a, 'key_b': b, 'score': s, 'reason': r}
                for a, b, s, r in rows]

    def approved_links(self) -> Dict[str, List[Dict]]:
        """{key: [{'other', 'reason'} …]} — the Related-block input,
        capped per note (strongest first, then alphabetical)."""
        links: Dict[str, List[Dict]] = {}
        for row in self.with_status(STATUS_APPROVED):
            for me, other in ((row['key_a'], row['key_b']),
                              (row['key_b'], row['key_a'])):
                links.setdefault(me, []).append(
                    {'other': other, 'reason': row['reason'],
                     'score': row['score']})
        for me, items in links.items():
            items.sort(key=lambda i: (-i['score'], i['other']))
            del items[LINKS_CAP:]
        return links

    def counts(self) -> Dict[str, int]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT status, COUNT(*) FROM link_suggestions"
                " GROUP BY status").fetchall()
        out = {STATUS_PENDING: 0, STATUS_APPROVED: 0, STATUS_REJECTED: 0}
        out.update({status: n for status, n in rows})
        return out


# ---------------------------------------------------------------------------
# 5a) The Suggestions note (written under Library/, read back for decisions)
# ---------------------------------------------------------------------------

def _note_title_for(fields: Optional[Dict]) -> str:
    name = str((fields or {}).get('name') or '').strip()
    return name or '(unnamed)'


def suggestions_path(manual_vault: str) -> str:
    return os.path.join(manual_vault, 'Library', SUGGESTIONS_FILENAME)


def build_suggestions_note(store: LinkStore,
                           fields_by_key: Dict[str, Dict]) -> str:
    """The Suggestions note: pending suggestions as Obsidian checkboxes
    (tick = approve, strike through = reject), then the approved and
    rejected history. Written ONLY under ``<manual>/Library/``."""
    now = time.strftime('%Y-%m-%d %H:%M')
    lines = [
        "---",
        "managed_by: gitcurator",
        "purpose: link suggestions",
        "---",
        "",
        "# Link suggestions",
        "",
        f"Generated {now}. Related notes are linked automatically once",
        "you approve them here.",
        "",
        "**How to decide:** tick a checkbox (`- [x]`) to APPROVE the",
        "pair; strike the whole line through (`~~…~~`) to REJECT it",
        "forever; leave it as `- [ ]` to keep it pending for later.",
        "Then run the collect step (Settings → More menu, or",
        "`python gitcurator/tools/build_links.py --collect`) — the",
        "Related (auto) blocks in the Library mirror update.",
        "",
    ]
    pending = store.with_status(STATUS_PENDING)
    lines.append(f"## Pending ({len(pending)})")
    lines.append("")
    if not pending:
        lines.append("_(nothing waiting for your decision)_")
    for row in pending:
        a = _note_title_for(fields_by_key.get(row['key_a'], {}).get('fields'))
        b = _note_title_for(fields_by_key.get(row['key_b'], {}).get('fields'))
        reason = row['reason'] or 'similar purpose'
        lines.append(
            f"- [ ] [[{a}]] ↔ [[{b}]] — {reason} (score "
            f"{row['score']:.2f})")
        lines.append(f"  - `{row['key_a']}` / `{row['key_b']}`")
    lines.append("")

    approved = store.with_status(STATUS_APPROVED)
    lines.append(f"## Approved ({len(approved)})")
    lines.append("")
    if not approved:
        lines.append("_(none yet)_")
    for row in approved:
        a = _note_title_for(fields_by_key.get(row['key_a'], {}).get('fields'))
        b = _note_title_for(fields_by_key.get(row['key_b'], {}).get('fields'))
        lines.append(f"- [[{a}]] ↔ [[{b}]] — {row['reason']}")
    lines.append("")

    rejected = store.with_status(STATUS_REJECTED)
    lines.append(f"## Rejected ({len(rejected)}) — never suggested again")
    lines.append("")
    if not rejected:
        lines.append("_(none)_")
    for row in rejected:
        a = _note_title_for(fields_by_key.get(row['key_a'], {}).get('fields'))
        b = _note_title_for(fields_by_key.get(row['key_b'], {}).get('fields'))
        lines.append(f"- ~~[[{a}]] ↔ [[{b}]]~~ — {row['reason']}")
    lines.append("")
    return "\n".join(lines)


_SUGGEST_CHECK_RE = re.compile(
    r'^\s*-\s*\[(?P<mark>[ xX])\]\s*(?P<body>.+?)\s*$')
_STRIKE_RE = re.compile(r'~~')


def collect_decisions(note_path: str) -> List[Dict]:
    """Read the Suggestions note back → ``[{pair, status}]`` decisions.

    Convention (documented in the note itself): a TICKED checkbox
    (``- [x]``) approves the pair on that line; a STRUCK-THROUGH line
    (``~~``) rejects it; anything else stays pending. Only lines that
    carry both source URLs (the indented ```url` / `url``` line pairs
    written by ``build_suggestions_note``) are read — free text is never
    misparsed as a decision."""
    decisions: List[Dict] = []
    if not note_path or not os.path.isfile(note_path):
        return decisions
    try:
        with open(note_path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.read().splitlines()
    except OSError:
        return decisions
    for i, line in enumerate(lines):
        match = _SUGGEST_CHECK_RE.match(line)
        if not match:
            continue
        # the URLs ride the next non-empty indented line
        urls: List[str] = []
        for follow in lines[i + 1:i + 3]:
            found = re.findall(r'`([^`]+)`', follow)
            if len(found) >= 2:
                urls = [found[0], found[1]]
                break
        if len(urls) != 2:
            continue
        struck = bool(_STRIKE_RE.search(match.group('body')))
        if match.group('mark').lower() == 'x' and not struck:
            status = STATUS_APPROVED
        elif struck:
            status = STATUS_REJECTED
        else:
            continue                     # still pending — not a decision
        decisions.append({'key_a': urls[0], 'key_b': urls[1],
                          'status': status})
    return decisions


def apply_decisions(store: LinkStore, decisions: List[Dict]) -> Dict[str,
                                                                     int]:
    """Apply collected decisions to the store. Returns
    ``{approved, rejected, ignored}`` (ignored = already decided or
    unknown pair)."""
    approved = rejected = ignored = 0
    for dec in decisions:
        changed = store.decide(dec['key_a'], dec['key_b'], dec['status'])
        if not changed:
            ignored += 1
        elif dec['status'] == STATUS_APPROVED:
            approved += 1
        else:
            rejected += 1
    return {'approved': approved, 'rejected': rejected, 'ignored': ignored}


# ---------------------------------------------------------------------------
# 5b) The "Related (auto)" block — mirror copies ONLY
# ---------------------------------------------------------------------------

_RELATED_BLOCK_RE = re.compile(
    re.escape(RELATED_START) + r".*?" + re.escape(RELATED_END),
    re.DOTALL)


def has_related_block(text: str) -> bool:
    return bool(_RELATED_BLOCK_RE.search(text or ''))


def extract_related_block(text: str) -> str:
    match = _RELATED_BLOCK_RE.search(text or '')
    return match.group(0) if match else ''


def render_related_block(links: List[Dict],
                         title_for: Callable[[str], str]) -> str:
    """The delimited block for one note: a small bulleted list of
    wiki-links (capped by the caller through ``approved_links``)."""
    lines = [RELATED_START, '**Related (auto):**']
    for item in links:
        title = title_for(item['other'])
        reason = str(item.get('reason') or '').strip()
        suffix = f" — {reason}" if reason else ''
        lines.append(f"- [[{title}]]{suffix}")
    lines.append(RELATED_END)
    return "\n".join(lines)


def insert_related_block(text: str, block: str) -> str:
    """Add or REPLACE the Related block at the end of a mirror note
    (idempotent; the same surgery rules as the recall block)."""
    text = text or ''
    if has_related_block(text):
        return _RELATED_BLOCK_RE.sub(lambda _m: block, text, count=1)
    stripped = text.rstrip()
    if not stripped:
        return block
    sep = '\r\n' if '\r\n' in text and text.count('\r\n') > \
        text.count('\n') / 2 else '\n'
    return stripped + sep + sep + block + sep


def apply_related_blocks(library_root: str, approved: Dict[str, List[Dict]],
                         fields_by_key: Dict[str, Dict],
                         writer: Optional[Callable[[str, str], None]] = None
                         ) -> List[str]:
    """Write the approved Related (auto) blocks into the MIRROR copies
    under ``<manual>/Library/`` — never the machine vaults, never the
    owner's notes (tested). Mirror copies are found by their
    ``mirror_of`` frontmatter value (the linking layer's key). Returns
    the relative paths updated/added."""
    from gitcurator.core.mirror import _MIRROR_RE, _unquote     # noqa: PLC2701
    write = writer or atomic_write_text
    updated: List[str] = []
    if not library_root or not os.path.isdir(library_root):
        return updated
    for root, _dirs, files in os.walk(library_root):
        if not _is_inside_library(root, library_root):
            continue
        for fname in files:
            if not fname.endswith('.md'):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, 'r', encoding='utf-8',
                          errors='replace') as f:
                    text = f.read()
            except OSError:
                continue
            head = text[:800]
            match = _MIRROR_RE.search(head)
            if not match:        # not a mirror copy — NEVER touched
                continue
            key = normalize_url(_unquote(match.group(1)).strip())
            links = approved.get(key)
            title_for = (lambda other: _note_title_for(
                (fields_by_key.get(other) or {}).get('fields')))
            new_block = (render_related_block(links, title_for)
                         if links else '')
            if new_block:
                new_text = insert_related_block(text, new_block)
            else:
                new_text = _RELATED_BLOCK_RE.sub('', text).rstrip() + \
                    ('\n' if text.endswith('\n') else '')
                if not has_related_block(text):
                    continue          # nothing to add, nothing to remove
            if new_text != text:
                write(fpath, new_text)
                updated.append(os.path.relpath(fpath, library_root)
                               .replace('\\', '/'))
    return updated


def _is_inside(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([os.path.realpath(path),
                                   os.path.realpath(root)]) \
            == os.path.realpath(root)
    except (ValueError, OSError):
        return False


def _is_inside_library(path: str, library_root: str) -> bool:
    return _is_inside(path, library_root)


# ---------------------------------------------------------------------------
# The mirror carry-over hook (Phase 5 ↔ Phase 6 integration)
# ---------------------------------------------------------------------------

def preserve_related_block(old_text: str, new_text: str) -> str:
    """Carry an existing Related (auto) block into a rebuilt mirror note.

    ``mirror._plan_tree`` rebuilds mirror copies from the machine vault
    on every sync — without this hook the rebuild would silently wipe
    the Related blocks the owner approved. Called with the OLD mirror
    text and the NEW built text; returns the new text with the old
    block appended (unchanged notes compare equal, so carries only cost
    bytes when something else actually changed)."""
    block = extract_related_block(old_text or '')
    if not block:
        return new_text or ''
    return insert_related_block(new_text or '', block)
