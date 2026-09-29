#!/usr/bin/env python3
"""
recall.py — Phase 6, step 1 (SPEC §4.8 / §6): recall hooks.

The linking layer relies on a "Best used for" recall field on every
note. Website notes have carried one in their body since Phase 2
("## Best used for"); GitHub notes never did. This module gives each
existing GitHub note a NEUTRAL, LLM-written recall sentence inside ONE
delimited app-managed block::

    <!-- gitcurator:recall:start -->
    **Best used for:** Use when you need to …
    <!-- gitcurator:recall:end -->

Contract points (all tested):

* The markers are the ones ``note_state`` has defined since Phase 1 —
  ``note_state.compute_fingerprint`` strips the block, so an app-added
  recall hook NEVER reads as a human edit (SPEC §4.4).
* ``insert_recall_block`` touches nothing else: the block is appended at
  the end of the note, an existing block is REPLACED in place, and the
  operation is idempotent (same sentence in, same bytes out).
* The prompt (``prompts/r01_recall.txt``) is neutral — it does NOT use
  ``about_me.md`` (SPEC: only the GitHub repo prompts personalize).
* The sentence is sanitized: it must start with "Use when you need to",
  be one line, and be short; anything else falls back to the explicit
  "not captured" placeholder rather than shipping a guess.
* Dry-run first: ``plan_recall_hooks`` computes the work, ``run_recall_hooks``
  returns per-note diffs, and nothing is written until the caller passes
  ``apply=True`` (the tool layer enforces the SPEC's "the owner approves
  20 before any bulk run" via ``--sample``).

Pure stdlib, no PyQt — importable from the CLI, tests and CI.
"""

from __future__ import annotations

import os
import re
from typing import Callable, Dict, List, Optional

from gitcurator.constants import APP_DIR
from gitcurator.core.note_state import (      # the markers live THERE
    RECALL_END, RECALL_START, VAULT_GITHUB, compute_fingerprint)
from gitcurator.core.prompts import load_prompt
from gitcurator.core.storage import atomic_write_text

# The fallback sentence — the same one website notes use when the LLM
# could not capture the field (never a guess; SPEC: omit rather than guess).
RECALL_PLACEHOLDER = "Use when you need to… (not captured — see the source link)."

# "Best used for" line rendered inside the block.
RECALL_LINE_PREFIX = "**Best used for:** "

# Sanitization bounds: one line, starting with the required words.
_RECALL_STARTS_WITH = "use when you need to"
_RECALL_MAX_LEN = 220          # ~25 words plus slack for punctuation

# ---------------------------------------------------------------------------
# Block operations (pure string surgery)
# ---------------------------------------------------------------------------

_RECALL_BLOCK_RE = re.compile(
    re.escape(RECALL_START) + r".*?" + re.escape(RECALL_END), re.DOTALL)


def strip_recall_block(text: str) -> str:
    """The note text WITHOUT the delimited recall block (and without the
    blank lines the block may have dragged along). This is the view the
    fingerprint sees — and the view a diff should show."""
    return _RECALL_BLOCK_RE.sub('', text or '').rstrip()


def has_recall_block(text: str) -> bool:
    return bool(_RECALL_BLOCK_RE.search(text or ''))


def get_recall_sentence(text: str) -> str:
    """The raw sentence inside the block ('' when absent). The
    ``**Best used for:**`` prefix and the markers are stripped."""
    match = _RECALL_BLOCK_RE.search(text or '')
    if not match:
        return ''
    inner = match.group(0)[len(RECALL_START):-len(RECALL_END)]
    inner = inner.strip()
    if inner.startswith(RECALL_LINE_PREFIX):
        inner = inner[len(RECALL_LINE_PREFIX):]
    return inner.strip()


def render_recall_block(sentence: str) -> str:
    """The full delimited block text for a (sanitized) sentence."""
    return (f"{RECALL_START}\n{RECALL_LINE_PREFIX}{sentence}\n"
            f"{RECALL_END}")


def insert_recall_block(text: str, sentence: str) -> str:
    """Add or REPLACE the recall block, touching nothing else.

    A missing block is appended at the end of the note (after a blank
    line). An existing block is replaced in place — same position, so
    re-running with the same sentence is byte-identical (idempotent).
    Line endings follow the note's own dominant ending (CRLF vaults stay
    CRLF).
    """
    text = text or ''
    crlf = '\r\n' in text and text.count('\r\n') > text.count('\n') / 2
    sep = '\r\n' if crlf else '\n'
    block = render_recall_block(sentence)
    if crlf:
        block = block.replace('\n', '\r\n')
    if has_recall_block(text):
        # function replacement → the block is inserted literally (no
        # backslash-reference surprises)
        return _RECALL_BLOCK_RE.sub(lambda _m: block, text, count=1)
    stripped = text.rstrip()
    if not stripped:
        return block
    return stripped + sep + sep + block + sep


def sanitize_recall_sentence(raw) -> str:
    """LLM output → a shippable recall sentence. Must start with the
    required words (case-insensitive) AND name something after them, be
    a single short line; anything else (marketingese, a bare prefix,
    multi-line, empty, refusals) becomes the explicit placeholder — the
    app never ships a guess."""
    sentence = str(raw or '').strip()
    # strip surrounding quotes the model sometimes adds
    if len(sentence) >= 2 and sentence[0] == sentence[-1] \
            and sentence[0] in ('"', "'", '`'):
        sentence = sentence[1:-1].strip()
    # one line only
    sentence = sentence.splitlines()[0].strip() if sentence else ''
    if not sentence:
        return RECALL_PLACEHOLDER
    lowered = sentence.lower()
    if not lowered.startswith(_RECALL_STARTS_WITH):
        return RECALL_PLACEHOLDER
    # a BARE prefix ("Use when you need to" and nothing after) names no
    # problem — that is a refusal in disguise
    if len(lowered) <= len(_RECALL_STARTS_WITH) + 1:
        return RECALL_PLACEHOLDER
    if len(sentence) > _RECALL_MAX_LEN:
        # cut at the last word boundary inside the bound — a recall line
        # longer than the bound is a model rambling, not a hard error
        cut = sentence[:_RECALL_MAX_LEN].rsplit(' ', 1)[0].rstrip(',;:')
        sentence = cut if cut.lower().startswith(_RECALL_STARTS_WITH) \
            and len(cut) > len(_RECALL_STARTS_WITH) + 1 \
            else RECALL_PLACEHOLDER
    return sentence


# ---------------------------------------------------------------------------
# Field extraction (best-effort parsers over the note formats in the wild)
# ---------------------------------------------------------------------------

_FRONT_TAGS_RE = re.compile(r'^tags:\s*\[(.*?)\]\s*$', re.MULTILINE)
_TITLE_RE = re.compile(r'^#\s+(.+?)\s*$', re.MULTILINE)
_TLDR_RE = re.compile(r'>\s*\*\*TL;DR:\*\*\s*(.+)', re.MULTILINE)
_SECTION_RES = {
    'summary': re.compile(
        r'^##\s*What is it\?\s*$\r?\n+(.*?)\r?\n^##\s', re.MULTILINE
        | re.DOTALL),
    'features': re.compile(
        r'^##\s*Key Features & Technologies\s*$\r?\n+(.*?)\r?\n^##\s',
        re.MULTILINE | re.DOTALL),
}


def _section(text: str, key: str, max_len: int = 600) -> str:
    match = _SECTION_RES[key].search(text or '')
    if not match:
        return ''
    return match.group(1).strip()[:max_len]


def _tags(text: str) -> str:
    match = _FRONT_TAGS_RE.search(text or '')
    if not match:
        return ''
    return ', '.join(t.strip() for t in match.group(1).split(',')
                     if t.strip())


def _title(text: str) -> str:
    match = _TITLE_RE.search(text or '')
    return match.group(1).strip() if match else ''


def _tldr(text: str) -> str:
    match = _TLDR_RE.search(text or '')
    return match.group(1).strip() if match else ''


def extract_github_fields(text: str) -> Dict[str, str]:
    """GitHub note → the r01 prompt slots. Best-effort, never raises."""
    return {
        'REPO_NAME': _title(text),
        'TLDR': _tldr(text)[:300],
        'SUMMARY': _section(text, 'summary'),
        'FEATURES': _section(text, 'features'),
        'TAGS': _tags(text),
    }


_BEST_USED_RE = re.compile(
    r'^##\s*Best used for\s*$\r?\n+(.*?)\r?\n^##\s', re.MULTILINE
    | re.DOTALL)


def extract_website_fields(text: str) -> Dict[str, str]:
    """Website note → the recall fields (the body already carries them —
    no LLM needed for the field itself; this feeds the embedding text and
    the confirm prompt)."""
    best = ''
    match = _BEST_USED_RE.search(text or '')
    if match:
        best = match.group(1).strip()[:400]
    return {
        'name': _title(text),
        'one_line': _tldr(text)[:300],
        'best_used_for': best,
        'tags': _tags(text),
    }


def recall_fields_for(text: str, vault: str) -> Dict[str, str]:
    """Uniform recall view of a note from either vault:
    ``{name, one_line, recall, tags}`` (recall = the Best-used-for
    sentence — the delimited block on GitHub notes, the body section on
    website notes)."""
    if vault == VAULT_GITHUB:
        github = extract_github_fields(text)
        return {'name': github['REPO_NAME'], 'one_line': github['TLDR'],
                'recall': get_recall_sentence(text),
                'tags': github['TAGS']}
    web = extract_website_fields(text)
    # uniform contract: 'recall' IS the Best-used-for field
    return {'name': web['name'], 'one_line': web['one_line'],
            'recall': web['best_used_for'], 'tags': web['tags']}


# ---------------------------------------------------------------------------
# Planning + running (the tool layer drives these)
# ---------------------------------------------------------------------------

def build_recall_prompt(fields: Dict[str, str]) -> str:
    """r01 filled from extracted fields (the loader refuses a missing
    slot — a half-filled prompt is never sent)."""
    return load_prompt(
        'r01_recall',
        REPO_NAME=fields.get('REPO_NAME') or '(unknown repository)',
        TLDR=fields.get('TLDR') or '(not captured)',
        SUMMARY=fields.get('SUMMARY') or '(not captured)',
        FEATURES=fields.get('FEATURES') or '(not captured)',
        TAGS=fields.get('TAGS') or '(none)',
    )


def plan_recall_hooks(vault_path: str, force: bool = False) -> List[Dict]:
    """Read-only: which GitHub-vault notes need a recall hook?

    Returns one dict per note WITH a source line (the linking layer's
    identity): ``{path, source_url, has_block, current}``. Without
    ``force``, notes that already carry a block are marked done (the
    caller skips them); with it they are re-planned for a refresh.
    Unmanaged notes (no ``source:``) are skipped — the app does not write
    into files it does not own.
    """
    from gitcurator.core.note_state import scan_vault
    planned = []
    for note in scan_vault(vault_path):
        if note.get('unmanaged') or not note.get('path'):
            continue
        try:
            with open(note['path'], 'r', encoding='utf-8',
                      errors='replace') as f:
                text = f.read()
        except OSError:
            continue
        planned.append({
            'path': note['path'],
            'source_url': note.get('source_url', ''),
            'has_block': has_recall_block(text),
            'current': get_recall_sentence(text),
        })
    if not force:
        planned = [p for p in planned if not p['has_block']]
    return planned


def run_recall_hooks(plan: List[Dict], llm: Callable[[List[Dict]], str],
                     *, apply: bool = False, limit: Optional[int] = None,
                     progress: Optional[Callable[[str], None]] = None,
                     writer: Optional[Callable[[str, str], None]] = None
                     ) -> List[Dict]:
    """Run the plan: extract fields → prompt → LLM → sanitize → (write).

    ``llm(messages)`` is the same closure the other tools build (task
    tag ``analyze``). Dry-run by default — every entry in the result
    carries the before/after block text for the diff report. ``apply``
    writes through ``atomic_write_text`` (or the ``writer`` hook the
    tests inject). Never raises on a single note's failure: the entry
    gets ``error`` and the run continues.
    """
    say = progress or (lambda _msg: None)
    write = writer or atomic_write_text
    results: List[Dict] = []
    todo = plan[:limit] if limit else plan
    for entry in todo:
        out = dict(entry)
        results.append(out)
        try:
            with open(entry['path'], 'r', encoding='utf-8',
                      errors='replace') as f:
                text = f.read()
            prompt = build_recall_prompt(extract_github_fields(text))
            reply = llm([{'role': 'user', 'content': prompt}])
            from gitcurator.core.llm_client import extract_json
            try:
                payload = extract_json(reply)
                raw = payload.get('best_used_for', '')
            except Exception:
                raw = str(reply)
            sentence = sanitize_recall_sentence(raw)
            new_block = render_recall_block(sentence)
            old_block = (_RECALL_BLOCK_RE.search(text).group(0)
                         if has_recall_block(text) else '')
            out.update({'sentence': sentence, 'old_block': old_block,
                        'new_block': new_block})
            if entry.get('has_block') and entry.get('current') == sentence:
                out['unchanged'] = True
                say(f"  = {os.path.basename(entry['path'])} — unchanged")
            else:
                out['unchanged'] = False
                if apply:
                    write(entry['path'],
                          insert_recall_block(text, sentence))
                    out['written'] = True
                    say(f"  + {os.path.basename(entry['path'])} — hook "
                        f"written" + (" (replaced)" if entry['has_block']
                                      else ""))
                else:
                    say(f"  ~ {os.path.basename(entry['path'])} — would "
                        f"write: {sentence[:70]}")
        except Exception as exc:                       # noqa: BLE001
            out['error'] = f"{type(exc).__name__}: {exc}"
            say(f"  ! {os.path.basename(entry['path'])} — {out['error']}")
    return results


def recall_report(results: List[Dict], vault_path: str,
                  applied: bool) -> str:
    """The human-readable report (written under app/reports/recall/)."""
    mode = "APPLIED" if applied else "DRY RUN (nothing written)"
    lines = [f"GitCurator — recall hooks — {mode}",
             f"GitHub vault: {vault_path}",
             f"Notes in this run: {len(results)}", ""]
    for r in results:
        name = os.path.basename(r.get('path', '?'))
        if r.get('error'):
            lines.append(f"ERROR    {name} — {r['error']}")
        elif r.get('unchanged'):
            lines.append(f"KEEP     {name} — already current")
        elif r.get('written'):
            lines.append(f"WRITTEN  {name} — {r.get('sentence', '')}")
        else:
            lines.append(f"PLANNED  {name} — {r.get('sentence', '')}")
    placeholder = sum(1 for r in results
                      if r.get('sentence') == RECALL_PLACEHOLDER)
    if placeholder:
        lines.append("")
        lines.append(f"{placeholder} note(s) got the 'not captured' "
                     "placeholder — the source text was too thin to name "
                     "a problem (deliberate: omit rather than guess).")
    lines.append("")
    lines.append("The block is delimited and fingerprint-invisible: "
                 "note_state will not read it as a human edit.")
    return "\n".join(lines) + "\n"


def report_dir() -> str:
    path = os.path.join(APP_DIR, 'reports', 'recall')
    os.makedirs(path, exist_ok=True)
    return path
