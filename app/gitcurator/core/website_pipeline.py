#!/usr/bin/env python3
"""
website_pipeline.py — the Websites pipeline (Phase 2, SPEC §4.3 + §4.6).

Per link:
    1. canonicalize   — core/links.normalize_website_url
    2. dedupe         — the websites VaultIndex (in-memory, passed in) +
                        the ``websites_processed`` table + the dismissed
                        list + a ``_review`` note with fetch_status failed
                        is RETRIED (upgraded), not skipped
    3. fetch          — core/web_fetch (timeout, size cap, rate limit, UA)
    4. extract        — core/web_extract (title, description, main text)
    5. classify       — two passes with the taxonomy (category, then
                        subcategory); every answer validated against the
                        parsed names, 2 retries, then ``_review``; low
                        confidence also goes to ``_review``
    6. analyze        — w03 prompt; rule: omit rather than guess
    7. build & write  — atomic write into
                        <website_vault>/<Category>/<Subcategory?>/<Name>.md
    8. (seal happens in the batch finish path, not here)

Failure handling (SPEC §4.3): a link that cannot be fetched still gets a
minimal note in ``_review`` with ``fetch_status: failed`` and is retried
automatically up to 3 times over several days (state in cache.db). Nothing
is silently dropped.

Dry-run: every vault write goes through storage.atomic_write_text /
dryrun.makedirs (already gated by core/dryrun), and the caller passes a
state DB pointed at the SHADOW cache — so a dry-run records nothing.

No PyQt. The GUI/CLI worker injects ``llm_call`` (a plain
messages -> str callable) and ``vault_index_has`` (dedupe probe).
"""

import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional
from urllib.parse import urlparse

from gitcurator.constants import (
    APP_DIR, MANAGED_BY_GITCURATOR, NOTE_SCHEMA_VERSION, OWNERSHIP_BANNER,
)
from gitcurator.core import dryrun as _dryrun
from gitcurator.core import hand_delivery as _hand_delivery
from gitcurator.core import prompts as _prompts
from gitcurator.core import web_extract as _web_extract
from gitcurator.core import web_fetch as _web_fetch
from gitcurator.core import links as _links
from gitcurator.core.links import (
    is_gist_url, normalize_website_url, site_key_of,
)
from gitcurator.core.note_builder import (
    sanitize_body_text, sanitize_seq_item, sanitize_short_summary,
    sanitize_tags, yaml_scalar,
)
from gitcurator.core.storage import atomic_write_text, safe_filename, unique_path
from gitcurator.core.taxonomy import Taxonomy, load_taxonomy_from_config
# v0.25.0 — the state ledger moved to core/website_state.py
# (re-exported here so import paths and test patch targets are unchanged).
from gitcurator.core.website_state import (  # noqa: F401
    MAX_FETCH_RETRIES, RETRY_BACKOFF_DAYS, SETTLED_META_KEY,
    QUEUE_HISTORY_SETTLED_META_KEY, WebsiteStateDB,
)

# Bump when the website prompt set changes shape (SPEC Appendix A).
# web-v2 (v0.27.0): decision-oriented body — "What it does" replaces the
# marketing-prone "Core offerings"/"Standout feature" pair, unknown values
# are omitted instead of printed as "unknown", and two new sections carry
# the practical facts (price terms, license, framework, install) and honest
# caveats. "Best used for" keeps its exact heading (recall/linking parses
# it) and "Similar tools" stays the closing section (the recall regex
# needs a heading after "Best used for").
WEBSITE_PROMPT_VERSION = "web-v2"

# ===========================================================================
# CONFIGURATION (safe to edit)
# ===========================================================================
REVIEW_FOLDER = "_review"
FETCH_TIMEOUT_S = 20
FETCH_MAX_BYTES = 2_000_000
DOMAIN_DELAY_S = 2.0
CLASSIFY_RETRIES = 2             # SPEC §4.6: retry up to 2 times, then _review
LLM_EXCERPT_CHARS = 4000         # page text given to the model
MIN_TEXT_FOR_ANALYSIS = 80       # below this, the model gets title+desc only
LOW_CONFIDENCE = "low"           # confidence that routes to _review




# ===========================================================================
# The website note builder
# ===========================================================================

def build_website_note(url: str, analysis: Dict, category: str,
                       subcategory: str, fetch_status: str,
                       tags: Optional[List[str]] = None) -> str:
    """Build a website note (frontmatter + body) from SANITIZED-safe inputs.

    Every value that reaches YAML goes through the note_builder sanitizers
    (non-negotiable #6: LLM output is untrusted input). ``category`` and
    ``subcategory`` are taxonomy-validated BEFORE this function is called;
    they are still re-sanitized here — defense in depth.
    """
    name = sanitize_short_summary(analysis.get('name') or '')[:120] \
        or "Untitled site"
    one_line = sanitize_short_summary(analysis.get('one_line') or '')
    # web-v2: "what_it_does" replaces "core_offerings" (old-shape dicts —
    # the offline golden runner, any in-flight analyses — still render).
    does = analysis.get('what_it_does') or analysis.get('core_offerings') or []
    if isinstance(does, str):
        does = [does]
    does_lines = [sanitize_body_text(o, max_len=200) for o in does]
    does_lines = [o for o in does_lines if o.strip()]
    best_used_for = sanitize_body_text(analysis.get('best_used_for') or '',
                                       max_len=400)
    pricing = str(analysis.get('pricing') or 'unknown').strip().lower()
    if pricing not in ('free', 'freemium', 'paid', 'unknown'):
        pricing = 'unknown'
    pricing_detail = sanitize_body_text(analysis.get('pricing_detail') or '',
                                        max_len=200)
    login_required = str(analysis.get('login_required') or 'unknown').strip().lower()
    if login_required not in ('yes', 'no', 'unknown'):
        login_required = 'unknown'
    practical = analysis.get('practical_details') or []
    if isinstance(practical, str):
        practical = [practical]
    practical_lines = [sanitize_body_text(p, max_len=200) for p in practical]
    practical_lines = [p for p in practical_lines if p.strip()][:4]
    watch = analysis.get('watch_out') or []
    if isinstance(watch, str):
        watch = [watch]
    watch_lines = [sanitize_body_text(w, max_len=200) for w in watch]
    watch_lines = [w for w in watch_lines if w.strip()][:2]
    similar = sanitize_tags(analysis.get('similar_tools') or [], max_items=5)
    note_tags = sanitize_tags(list(tags or []) + list(analysis.get('tags') or []),
                              max_items=8)
    if is_gist_url(url) and 'snippet' not in [t.lower() for t in note_tags]:
        note_tags.append('snippet')       # SPEC §4.2: gists get #snippet

    cat_yaml = yaml_scalar(category)
    sub_yaml = yaml_scalar(subcategory) if subcategory else '""'
    url_yaml = yaml_scalar(url)
    tags_yaml = "tags: [" + ", ".join(note_tags) + "]" if note_tags \
        else "tags: []"

    # --- body (web-v2: only what is known; no "unknown" filler lines) ---
    # The recall/linking layer regex-parses "## Best used for" and needs a
    # heading after it, so "Best used for" always renders (with the
    # not-captured fallback line) and "Similar tools" always closes.
    best_used_line = best_used_for or \
        "Use when you need to… (not captured — see the source link)."
    similar_md = ", ".join(similar) if similar else "—"

    detail_lines = []
    if pricing_detail:
        if (pricing in ('free', 'freemium', 'paid')
                and not pricing_detail.lower().startswith(pricing)):
            detail_lines.append(f"Pricing: {pricing} — {pricing_detail}")
        else:
            detail_lines.append(f"Pricing: {pricing_detail}")
    elif pricing in ('free', 'freemium', 'paid'):
        detail_lines.append(f"Pricing: {pricing}")
    if login_required in ('yes', 'no'):
        detail_lines.append(
            "Sign-up required: yes" if login_required == 'yes'
            else "Sign-up required: no")
    detail_lines.extend(practical_lines)

    sections = []
    if does_lines:
        sections.append("## What it does\n" +
                        "\n".join(f"- {o}" for o in does_lines))
    sections.append("## Best used for\n" + best_used_line)
    if detail_lines:
        sections.append("## Practical details\n" +
                        "\n".join(f"- {d}" for d in detail_lines))
    if watch_lines:
        sections.append("## Watch out\n" +
                        "\n".join(f"- {w}" for w in watch_lines))
    sections.append("## Similar tools\n" + similar_md)
    body_md = "\n\n".join(sections)

    return f"""---
source: {url_yaml}
aliases: []
{tags_yaml}
category: {cat_yaml}
subcategory: {sub_yaml}
fetch_status: "{fetch_status}"
pricing: "{pricing}"
login_required: "{login_required}"
date_processed: {datetime.now().strftime("%Y-%m-%d")}
managed_by: "{MANAGED_BY_GITCURATOR}"
schema_version: "{NOTE_SCHEMA_VERSION}"
prompt_version: "{WEBSITE_PROMPT_VERSION}"
---

{OWNERSHIP_BANNER}

# {name}

> **TL;DR:** {one_line or '—'}

{body_md}

---
*Source: [{url}]({url})*

*Not useful anymore? Tag this note 🗑️ or delete / auto_delete —
typed inline anywhere in the note (Obsidian's own tag syntax) or
added in the tags property — the next run counts it, asks you to
confirm on Telegram, and on your 🗑️ Delete it leaves the library and
never fetches this site again (v0.60.0).*
"""


def build_review_note(url: str, fetch_status: str, reason: str,
                      title: str = '', tags: Optional[List[str]] = None) -> str:
    """Minimal _review note for links that could not be fully processed
    (SPEC §4.3: "a link that cannot be fetched still gets a minimal note
    in _review with fetch_status: failed")."""
    note_tags = sanitize_tags(list(tags or []), max_items=6)
    if is_gist_url(url) and 'snippet' not in [t.lower() for t in note_tags]:
        note_tags.append('snippet')
    tags_yaml = "tags: [" + ", ".join(note_tags) + "]" if note_tags \
        else "tags: []"
    title = sanitize_short_summary(title or '')[:120] or url
    return f"""---
source: {yaml_scalar(url)}
aliases: []
{tags_yaml}
category: ""
subcategory: ""
fetch_status: "{fetch_status}"
pricing: "unknown"
login_required: "unknown"
date_processed: {datetime.now().strftime("%Y-%m-%d")}
managed_by: "{MANAGED_BY_GITCURATOR}"
schema_version: "{NOTE_SCHEMA_VERSION}"
prompt_version: "{WEBSITE_PROMPT_VERSION}"
---

{OWNERSHIP_BANNER}

# {title}

> [!warning] Needs review — {fetch_status}
> {sanitize_body_text(reason or 'The page could not be processed fully.', max_len=400)}

This note is a placeholder created automatically. The link was recorded so
it is never lost; it will be retried automatically.

---
*Source: [{url}]({url})*

*Not useful anymore? Tag this note 🗑️ or delete / auto_delete —
typed inline anywhere in the note (Obsidian's own tag syntax) or
added in the tags property — the next run counts it, asks you to
confirm on Telegram, and on your 🗑️ Delete it leaves the library and
never fetches this site again (v0.60.0).*
"""


# ===========================================================================
# v0.64.0 — ONE NOTE PER SITE: the consolidation's own writer
# ===========================================================================

#: The body section every consolidated note carries (the human's half
#: of the law — every extra link of the site, one list, one note).
SITE_LINKS_HEADING = '## Links on this site'

_SITE_LINKS_LINE_RE = re.compile(r'^site_links:\s*(.*)$', re.MULTILINE)


def _parse_site_links_list(raw: str) -> List[str]:
    """One YAML-ish single-line list (``site_links: [a, b]``) → its
    items. The same string-scan the tags parse uses; canonical URLs
    never carry commas (query pairs ride ``&`` — the one-spelling
    normalizer's own grammar), so a comma split is the honest read."""
    inner = (raw or '').strip()
    if inner.startswith('[') and inner.endswith(']'):
        inner = inner[1:-1]
    return [t.strip().strip('"').strip("'")
            for t in inner.split(',') if t.strip()]


def _fmt_site_links_list(urls: List[str]) -> str:
    return 'site_links: [' + ', '.join(urls) + ']'


def add_site_links_to_note(path: str, urls: List[str],
                           log: Optional[Callable] = None
                           ) -> Dict:
    """v0.64.0 — ONE NOTE PER SITE, the append-only writer.

    The owner's law (session, verbatim): "for same domains, do not
    define different notes, try to consolidate all of them in same
    note, if multiple links of that site exist". This is the ONE
    sanctioned rewrite of an existing note: every URL of the same
    site that is not already recorded rides TWO places —

    * the frontmatter's ``site_links: [..]`` line (the machine's
      half: VaultIndex parses it, so a consolidated link reads "in
      the vault" exactly like a note of its own would);
    * the body's ``## Links on this site`` section (the owner's
      half: one visible list, filed before the closing source
      footer).

    Every existing byte is preserved — the frontmatter line is
    INSERTED after ``source:`` (or replaced in place, list extended,
    when it already exists); the body section is INSERTED before the
    final ``---`` + ``*Source:*`` footer (or extended when it
    already exists). Idempotent (an already-recorded URL adds
    nothing); dry-run aware; an unexpected shape (no frontmatter,
    unreadable) is skipped with a warning, never mangled. Returns
    ``{'appended': [urls actually added], 'already': n, 'written':
    bool}``; never raises."""
    log = log or (lambda *a, **k: None)
    out: Dict = {'appended': [], 'already': 0, 'written': False}
    if not path or not urls or not os.path.isfile(path):
        return out
    canon = []
    seen = set()
    for u in (urls or []):
        try:
            c = normalize_website_url(u)
        except Exception:
            c = str(u or '').strip()
        if c and c not in seen:
            seen.add(c)
            canon.append(c)
    if not canon:
        return out
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
    except Exception as e:
        log(f"⚠️ One note per site: could not read "
            f"{os.path.basename(path)}: {e} — the link is recorded in "
            f"the ledger only", "warning")
        return out
    lines = content.split('\n')
    if not lines or lines[0].strip() != '---':
        log(f"⚠️ One note per site: {os.path.basename(path)} carries no "
            f"frontmatter block — left untouched (the link is recorded "
            f"in the ledger only)", "warning")
        return out
    # ---- the frontmatter half -------------------------------------------
    fm_end = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == '---':
            fm_end = i
            break
    if fm_end < 0:
        log(f"⚠️ One note per site: {os.path.basename(path)}'s frontmatter "
            f"never closes — left untouched", "warning")
        return out
    existing: List[str] = []
    site_line_at = -1
    source_line_at = -1
    for i in range(1, fm_end):
        m = _SITE_LINKS_LINE_RE.match(lines[i])
        if m:
            site_line_at = i
            existing = _parse_site_links_list(m.group(1))
        if lines[i].strip().lower().startswith('source:'):
            source_line_at = i
    have = set(existing)
    add = [c for c in canon if c not in have]
    if not add:
        out['already'] = len(canon)
        return out
    merged = existing + add
    new_line = _fmt_site_links_list(merged)
    if site_line_at >= 0:
        lines[site_line_at] = new_line
    elif source_line_at >= 0:
        lines.insert(source_line_at + 1, new_line)
    else:
        lines.insert(1, new_line)
        fm_end += 1
    # ---- the body half ---------------------------------------------------
    body = '\n'.join(lines[fm_end + 1:])
    new_links_md = '\n'.join(f"- [{c}]({c})" for c in add)
    if SITE_LINKS_HEADING in body:
        # extend the existing section: append after its last bullet
        b_lines = body.split('\n')
        head_at = next(i for i, l in enumerate(b_lines)
                       if l.strip() == SITE_LINKS_HEADING)
        last_bullet = head_at
        for i in range(head_at + 1, len(b_lines)):
            if b_lines[i].strip().startswith('- '):
                last_bullet = i
        b_lines.insert(last_bullet + 1, new_links_md)
        body = '\n'.join(b_lines)
    else:
        section = f"{SITE_LINKS_HEADING}\n{new_links_md}\n"
        # insert before the closing footer block (the final '---' that
        # precedes the *Source: line); fall back to the very end.
        idx = body.rfind('\n---')
        if idx >= 0 and '*Source:' in body[idx:]:
            body = body[:idx + 1] + section + body[idx + 1:]
        else:
            body = (body.rstrip('\n') + '\n\n' + section).rstrip('\n') \
                + '\n'
    new_content = '\n'.join(lines[:fm_end + 1]) + '\n' + body
    try:
        atomic_write_text(path, new_content)
        out['appended'] = add
        out['written'] = True
    except Exception as e:
        log(f"⚠️ One note per site: could not write "
            f"{os.path.basename(path)}: {e} — the link is recorded in "
            f"the ledger only", "warning")
    return out


# ===========================================================================
# v0.42.0 — the _review backlog: scan + retry
# ===========================================================================

def _parse_review_frontmatter(path: str) -> Optional[Dict[str, str]]:
    """Read the three keys the backlog retry needs from a note's
    frontmatter (source / fetch_status / managed_by). None when the file
    has no frontmatter block or cannot be read. String-scan only — core
    never grows a YAML dependency for this."""
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.read().splitlines()
    except Exception:
        return None
    if not lines or lines[0].strip() != '---':
        return None
    out: Dict[str, str] = {}
    for line in lines[1:]:
        s = line.strip()
        if s == '---':
            break
        if ':' not in s:
            continue
        key, _, val = s.partition(':')
        key = key.strip().lower()
        val = val.strip().strip('"').strip("'")
        if key in ('source', 'fetch_status', 'managed_by'):
            out[key] = val
    return out


def scan_review_backlog(vault_path: str,
                        log: Optional[Callable] = None,
                        is_dismissed: Optional[Callable] = None
                        ) -> List[Dict]:
    """v0.42.0 — the owner's _review backlog scanner.

    Walks ``<vault>/_review/*.md`` and returns the APP-OWNED
    fetch-failed placeholders (``managed_by: gitcurator`` +
    ``fetch_status: failed``): the links the pre-v0.41 honest-bot
    User-Agent got walled on (HTTP 403/405 from bot defenses) — exactly
    the pile the browser-grade presentation deserves to re-try. A
    hand-written review note and a non-failed review note (low
    classification confidence and friends) are NONE of our business:
    they wait for human eyes, as designed.

    v0.44.0 — the graveyard filter: a URL already marked dead in
    ``_review/DECOMMISSIONED.md`` is BURIED, not backlogged — the
    startup notice and the retry driver stop seeing it (the placeholder
    file itself is swept by the next consume).

    v0.47.0 — ``is_dismissed(url) -> bool`` (optional, the caller
    injects a WebsiteStateDB partial): a link RETIRED by the fetcher's
    own auto-verdict (dead / paywalled / refused) leaves the backlog
    too — its placeholder note is the record, not a waiting wall; the
    master table is where the owner sees and ♻️-revives it. Without the
    callable the scan stays a pure file read (the hermetic law).

    Returns ``[{'url': source, 'path': note_path}, ...]`` — sorted by
    filename for a deterministic order; one source may appear more than
    once (legacy ``_v1``/``_v2`` stacking from older apps) — the retry
    driver consolidates. Pure reads; no state DB, no network."""
    review_dir = os.path.join(vault_path or '', REVIEW_FOLDER)
    if not vault_path or not os.path.isdir(review_dir):
        return []
    try:
        names = sorted(os.listdir(review_dir))
    except Exception as e:
        if log:
            log(f"⚠️ Could not list {review_dir}: {e}", "warning")
        return []
    dead = set()
    table_rows = scan_decommission_table(vault_path)
    for u, st in table_rows.items():
        if _status_is_dead(st):
            dead.add(normalize_website_url(u))
    items: List[Dict] = []
    for name in names:
        if not name.lower().endswith('.md') or name == DECOMMISSION_TABLE:
            continue
        path = os.path.join(review_dir, name)
        if not os.path.isfile(path):
            continue
        fm = _parse_review_frontmatter(path)
        if not fm:
            continue
        if fm.get('managed_by', '').lower() != MANAGED_BY_GITCURATOR:
            continue  # a human's note — never our call
        if fm.get('fetch_status', '').lower() != 'failed':
            continue  # in _review for OTHER reasons — human eyes
        url = (fm.get('source') or '').strip()
        if not url.lower().startswith(('http://', 'https://')):
            continue
        canonical = normalize_website_url(url)
        if canonical in dead:
            continue  # v0.44.0 — buried in the graveyard, not backlogged
        if is_dismissed is not None:
            try:
                if is_dismissed(canonical):
                    continue  # v0.47.0 — retired (auto-verdict/owner),
                    # not waiting — the master table is its ledger
            except Exception:
                pass  # a broken probe never hides a waiting link
        items.append({'url': url, 'path': path})
    return items


def scan_review_notes(vault_path: str,
                      log: Optional[Callable] = None) -> List[Dict]:
    """v0.49.0 — EVERY app-owned note waiting in ``_review``.

    The owner's report: "it currently only adds links like before in
    _review" — links land in the folder as notes (low classification
    confidence, analysis failures, archived rescues — the classes that
    wait for HUMAN eyes, not retries), but the master table only ever
    listed the fetch-failed ones, so these had no row, no Status cell,
    no emoji to set. This scan is the table's eyes for the whole
    folder: every ``managed_by: gitcurator`` note (ANY fetch_status),
    each ``{'url', 'path', 'fetch_status'}``. A hand-written note (no
    frontmatter / no managed_by key) is a human's — never our call.
    Sorted by filename for a deterministic order; pure file reads; no
    state DB, no network (the same law as
    :func:`scan_review_backlog`)."""
    review_dir = os.path.join(vault_path or '', REVIEW_FOLDER)
    if not vault_path or not os.path.isdir(review_dir):
        return []
    try:
        names = sorted(os.listdir(review_dir))
    except Exception as e:
        if log:
            log(f"⚠️ Could not list {review_dir}: {e}", "warning")
        return []
    items: List[Dict] = []
    for name in names:
        if not name.lower().endswith('.md') or name == DECOMMISSION_TABLE:
            continue
        path = os.path.join(review_dir, name)
        if not os.path.isfile(path):
            continue
        fm = _parse_review_frontmatter(path)
        if not fm or fm.get('managed_by', '').lower() != MANAGED_BY_GITCURATOR:
            continue  # a human's note — never our call
        url = (fm.get('source') or '').strip()
        if not url.lower().startswith(('http://', 'https://')):
            continue
        items.append({'url': url, 'path': path,
                      'fetch_status': (fm.get('fetch_status') or '').strip()})
    return items


def _review_note_reason(path: str) -> str:
    """v0.49.0 — the reason line out of a ``_review`` note's warning
    callout. :func:`build_review_note` writes exactly one callout
    (``> [!warning] Needs review — <status>``) followed by one
    ``> <reason>`` line; this reads that line back so the master
    table's Notes column can tell the owner WHY a link waits. Tolerant
    pure read: anything unexpected returns '' (a missing reason never
    breaks the refresh)."""
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.read().splitlines()
    except Exception:
        return ''
    for i, line in enumerate(lines):
        if '[!warning]' not in line:
            continue
        for j in range(i + 1, min(i + 4, len(lines))):
            s = lines[j].strip()
            if s.startswith('>'):
                reason = s.lstrip('>').strip()
                if reason:
                    return reason
            elif s:
                break
    return ''


# ===========================================================================
# v0.44.0 — the graveyard: dead links get a burial, not a haunting
# ===========================================================================

#: The decommission ledger lives where the bodies are: one markdown table
#: per vault at ``<vault>/_review/DECOMMISSIONED.md``. It is the SAME
#: gesture the owner already uses on the ``_inbox`` platform tables —
#: set the emoji in the Status column — raised here to a burial rite:
#: some sites are GONE for good (a real 404, a lost page, an abandoned
#: domain), and deleting the ``_review`` placeholder alone cannot say
#: that (the retry-queue row survives it and resurrects the fetch, then
#: the failure writes a fresh placeholder — the loop the owner closed
#: by asking for this). The dead-marked row is the explicit, durable
#: owner decision: never fetched, never retried, never re-registered.
DECOMMISSION_TABLE = "DECOMMISSIONED.md"
#: What reads as DEAD in a Status cell — the burial emojis plus the
#: plain words (substring + case-insensitive, so "🪦 dead — gone" and
#: "Decommissioned 2026" both count). ✅ / unreviewed / blank never do.
DEAD_MARKERS = ("🪦", "❌", "☠️", "💀", "decommissioned", "retired", "dead")
#: What reads as REVIVED — the graveyard's other door: a link buried by
#: mistake (or a domain that came back) is fetched like new again.
REVIVE_MARKERS = ("♻️", "revived", "restored", "un-decommissioned")
#: v0.47.0 — what reads as REVIEWED-AND-KEPT in a Status cell: the
#: owner's original gesture on the _inbox tables ("tick emoji as
#: reviewed so it never fetches again") raised to the master table's
#: second retirement door. A ✅ row retires the link WITHOUT the
#: graveyard's death sentence — the owner handled this link's fate by
#: hand, so it is never fetched, never retried again, and its _review
#: placeholder is swept; ♻️ revived still brings it back. 'unreviewed'
#: (the pre-filled default) NEVER counts; a revived Status never
#: counts either.
REVIEWED_MARKERS = ("✅", "✔", "☑", "reviewed", "kept", "done")

#: v0.48.0 — the fourth door's suffix: a failure whose class the three
#: machine doors could not open (refusal family / bot defense / TLS
#: fingerprint) gains this line, so the _review note, the master table's
#: Notes, and the log all name the door that CAN answer it — the owner's
#: own Chrome (More ▸ 🖐 Hand-deliver walled links, the table's 🖐 hand
#: Status, or --hand-delivery on the CLI).
HAND_DOOR_HINT = (" — the fourth door: 🖐 hand-deliver it (More ▸ "
                   "Hand-deliver walled links opens the link in your "
                   "real Chrome; save the page into the hand-delivered "
                   "folder and the next batch takes it from there)")


#: v0.58.0 — THE BANISHMENT: what reads as DELETE-ME on a note's OWN
#: body (the owner's ask, verbatim: "I decide to remove and never fetch
#: that URL again … in tags of websites, we can have a meta-data for
#: this case, for example I choose 'delete' or a specific emoji").
#: 🗑️ is the emoji; the plain words are the keyboard door. A tag reads
#: as a banish mark when it CONTAINS the emoji (🗑️ / "🗑️ delete") or
#: IS one of the words exactly (the tag "deleted-files" on a human's
#: note must never fire) — tags are single words, so the tight match is
#: the honest one. The same verdict rides a boolean frontmatter key
#: (``decommission: true``) for owners who type YAML faster than emoji.
#: v0.59.0 — ``auto_delete`` joins the words: the owner's report
#: (session): "When i write 'delete' tag, it autocompletes to
#: 'auto_delete' is that correct?" — Obsidian suggests the tag his
#: vault already knows, and the app must obey the word his editor
#: puts under his thumb, not fight it. The hyphen twin rides along
#: (``auto-delete``) — same word, the keyboard's other spelling.
#: v0.60.1 — the words live in TWO places now: the frontmatter ``tags``
#: list (the properties panel) AND the note body's own inline tags
#: (``#auto-delete`` typed in the text — Obsidian's natural tagging,
#: the owner's report: "I tagged one note as 'auto-delete' but it
#: didn't detect"). Same exact-word law on both surfaces; the
#: confirmation gate stays the net (a scraped hashtag can only ride
#: the ask, never delete on its own).
BANISH_EMOJI = "🗑️"
BANISH_WORDS = ("delete", "banish", "blacklist", "purge",
                "auto_delete", "auto-delete")
BANISH_KEYS = ("decommission", "banish", "blacklist")
#: v0.59.0 — THE TALLY: the log line every run answers with (the
#: owner's ask, verbatim: "I want get a log of how many notes are
#: wiped because of this method, every run … '10 Websites Removed
#: and will never fetch again because you blah blah'").
BANISH_TALLY_PREFIX = "🗑️ Run tally:"
#: Where banished notes go — inside Obsidian's hidden .trash (the
#: banned-domain sweep's precedent: out of the library, invisible to
#: VaultIndex/mirror/directory/the Website Directory, recoverable by
#: hand; ♻️ revived + a hand move brings a mistaken burial back).
BANISH_QUARANTINE_RELPATH = os.path.join(".trash", "banished")
#: v0.62.0 — THE TRASH DOOR: the owner's most natural deletion gesture
#: is a MOVE, not a tag (session, verbatim: "What if, we create a folder
#: called trash, every note which goes to trash will be deleted from
#: vault and never fetch again"). A note the owner moves into a
#: root-level ``Trash`` folder (any spelling — Trash/trash/TRASH, its
#: subfolders too) reads as carrying the delete verdict: the SAME
#: grammar, the SAME confirmation gate, the SAME enforcement as the
#: tag doors (the URL is blacklisted — never fetched again — the note
#: leaves the library for ``.trash/banished``, the master table holds
#: the ♻️-revivable record row). Hand-written notes in Trash are KEPT
#: with a warning (the sacred law — the app never deletes what it did
#: not write; the owner deletes his own notes by hand in Obsidian).
#: The folder name never becomes a destination: nothing ever proposes
#: moving a note INTO the trash, and the vault scan's inventory walks
#: past it (it is the waiting room, not the library).
TRASH_FOLDER_NAMES = ("trash",)
#: The log/record label the trash door's items carry.
TRASH_MARKER = "Trash folder"
TRASH_GESTURE = "Trash folder move"
#: The dismissal reason prefix the skip gate reads (process_link names
#: the door so the log tells a banishment from a graveyard burial).
BANISH_REASON_PREFIX = "banished by owner"
#: v0.60.0 — THE CONFIRMATION GATE: the banishment no longer fires on
#: detection alone. Every run opens with the vault scan counting the
#: marked notes, the number goes to the owner (the Telegram ask), and
#: NOTHING is removed until he answers (the owner's ask, verbatim:
#: "At the beginning of every run, system scans vault, find what I've
#: marked to delete, system detects them, show me them their numbers,
#: so I ensure that system successfully detected them, I confirm
#: deletion, then they will get deleted"). The verdicts: 'confirmed'
#: (delete now), 'declined' (keep, ask again next run), 'timeout' (no
#: answer in the window — keep, ask again), 'auto' (the old v0.58/v0.59
#: behavior, config ``banish_confirm: false``), 'defer' (marks exist
#: but no confirmation channel was reachable/injected — the safe
#: default: keep everything, ask again next run).
BANISH_GATE_PREFIX = "🗑️ Vault scan:"
#: v0.60.0 — the state-table row the worker keeps the ask in
#: (``banish_confirm:<id>`` — pending/confirmed/declined/timeout).
BANISH_GATE_ENDPOINT = "/api/banish"


def decommission_table_path(vault_path: str) -> str:
    """The graveyard file for one vault (``<vault>/_review/DECOMMISSIONED.md``)."""
    return os.path.join(vault_path or '', REVIEW_FOLDER,
                        DECOMMISSION_TABLE)


#: v0.52.0 — the canonical column set every stamping site writes into
#: (parts index 6 = the Status cell; GFM drops cells beyond the header
#: count, so a table whose header lost columns would hide the stamps).
_TABLE_HEADER_CELLS = 7
_CANONICAL_HEADER = ('| # | Date | URL | Domain | Source | Status | '
                     'Notes |')
_CANONICAL_SEPARATOR = '|---|------|-----|--------|--------|--------|-------|'


def _is_separator_line(line: str) -> bool:
    """A markdown table's ``|---|---|`` row (dashes, colons, spaces,
    pipes — nothing else)."""
    s = line.strip()
    if not s.startswith('|'):
        return False
    return all(c in '-:| \t' for c in s)


def _ensure_table_shape(path: str, log: Optional[Callable] = None) -> bool:
    """v0.52.0 — restore the table's full grammar when the owner's hand
    has trimmed it.

    The owner's screenshot: the master table with FOUR columns
    (``# | Date | URL | Domain``) — the Source/Status/Notes cells gone,
    the verdict emojis living in the # column. The PARSE reads any
    shape now (:func:`_parse_decommission_rows`), but the app's own
    stamps write the Status cell — and GFM drops every cell beyond the
    header's count, so a stamp into a trimmed table would be INVISIBLE
    in the rendered note. This pass, run by every table WRITER before
    it stamps, pads the header + separator back to the canonical seven
    columns and pads every short data row to the same width — the
    owner's cells (his # emojis included) are never touched, only
    empty cells are appended. Idempotent (a full-width table is a
    no-op read); atomic; never raises. Returns True when the file was
    rewritten."""
    log = log or (lambda *a, **k: None)
    if not path or not os.path.isfile(path):
        return False
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.read().splitlines()
    except Exception as e:
        log(f"⚠️ Table shape check skipped — the table could not be "
            f"read: {e}", "warning")
        return False
    changed = False
    # the header: the first pipe-row that is not a separator
    header_idx = None
    for i, line in enumerate(lines):
        if line.startswith('|') and not _is_separator_line(line):
            header_idx = i
            break
    if header_idx is not None:
        header_cells = len(
            [c for c in lines[header_idx].split('|')[1:-1]]) \
            if lines[header_idx].strip().endswith('|') \
            else len(lines[header_idx].split('|')) - 1
        if header_cells < _TABLE_HEADER_CELLS:
            lines[header_idx] = _CANONICAL_HEADER
            if header_idx + 1 < len(lines) \
                    and _is_separator_line(lines[header_idx + 1]):
                lines[header_idx + 1] = _CANONICAL_SEPARATOR
            changed = True
            log(f"📋 Master table: the trimmed header is restored to the "
                f"full seven columns (the # column is still yours — the "
                f"emoji there reads exactly like the Status cell)",
                "info")
    # the data rows: pad every short pipe-row that carries a link
    for i, line in enumerate(lines):
        if not (line.startswith('| ') and 'http' in line):
            continue
        parts = line.split('|')
        if len(parts) < 2 + _TABLE_HEADER_CELLS:
            parts = parts + [''] * (2 + _TABLE_HEADER_CELLS
                                    - len(parts))
            lines[i] = '|'.join(parts)
            changed = True
    if not changed:
        return False
    try:
        atomic_write_text(path,
                          '\n'.join(lines).rstrip('\n') + '\n')
    except Exception as e:
        log(f"⚠️ Table shape could not be restored: {e}", "warning")
        return False
    return True


def _parse_decommission_rows(path: str) -> List[Dict]:
    """Data rows of the graveyard table: ``[{'url', 'status', 'icon',
    'notes', 'line', 'raw'}, ...]``. Same split-the-pipes parse the
    _inbox tables use (URL = column 4, Status = column 7). Malformed
    rows are skipped, never raised — a hand-edited table must never
    crash a batch.

    v0.52.0 — THE ICON COLUMN SPEAKS. The owner's report (session,
    verbatim): "see I set hand emoji, but those links didn't refetched
    using scrapping manually on chrome" — his screenshot showed the
    master table with FOUR columns (``# | Date | URL | Domain``) and
    the verdict emojis set in the FIRST cell (the # column), the way
    the legend's own emoji lines read. The old parse demanded all
    seven columns (``len(parts) < 8`` → row invisible) and read the
    verdict only from the Status cell — so every icon gesture was
    blind: 🖐 rows never queued, 💀 rows never buried, " - " rows the
    app itself never wrote never retried. Now:

      * a row needs only FOUR cells to exist (``# | Date | URL |
        Domain`` — the URL stays column 4 in every shape the app
        writes and the owner trims to, with a scan fallback for
        hand-made shapes);
      * ``icon`` is the # cell, ``status`` is the Status cell — and
        the row's ``status`` value is the COMBINED gesture text
        (icon + Status), so every verdict predicate
        (:func:`_status_is_dead` / ``_status_is_reviewed`` /
        ``_status_is_revived`` / ``_status_is_waiting`` / the fourth
        door's ``_status_is_hand``) reads the emoji wherever the
        owner put it. The # column is the owner's column now."""
    rows: List[Dict] = []
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.read().splitlines()
    except Exception:
        return rows
    for idx, line in enumerate(lines):
        if not (line.startswith('| ') and 'http' in line):
            continue
        parts = line.split('|')
        if len(parts) < 5:      # | # | Date | URL | Domain | (the 4-col law)
            continue
        url = parts[3].strip()
        if not url.lower().startswith(('http://', 'https://')):
            # a hand-made shape — the URL may sit in any early cell
            url = ''
            for cell in parts[1:6]:
                c = (cell or '').strip()
                if c.lower().startswith(('http://', 'https://')):
                    url = c
                    break
            if not url:
                continue
        icon = parts[1].strip()
        if icon in ('-', '—', ''):      # the app's own # cell — no gesture
            icon = ''
        status_cell = parts[6].strip() if len(parts) >= 8 else ''
        gesture = ' '.join(x for x in (icon, status_cell) if x).strip()
        rows.append({'url': url, 'status': gesture, 'icon': icon,
                     'notes': parts[7].strip() if len(parts) > 7 else '',
                     'line': idx, 'raw': line})
    return rows


def _status_is_dead(status: str) -> bool:
    s = (status or '').strip().lower()
    if not s:
        return False
    return any(m in s for m in DEAD_MARKERS)


def _status_is_revived(status: str) -> bool:
    s = (status or '').strip().lower()
    if not s:
        return False
    return any(m in s for m in REVIVE_MARKERS)


def _status_is_banished(status: str) -> bool:
    """v0.58.0 — does a master-table gesture read as BANISHED (🗑️ /
    delete / banish / blacklist / purge / auto_delete — the substring
    law of the other verdicts, applied to the combined icon + Status
    text)?

    Precedence: ♻️ revived wins (it is the undo door of every burial —
    a hand-edited cell that says both means "bring it back"). Beyond
    that the banishment is the STRONGEST retirement in the grammar:
    dead (🪦) and reviewed (✅) dismiss the URL and sweep _review
    placeholders, but 🗑️ ALSO removes the note from the library and
    forgets its ledger row — when a cell says both dead and delete, the
    owner's removal intent wins (the note must go)."""
    s = (status or '').strip().lower()
    if not s:
        return False
    if _status_is_revived(s):
        return False            # the undo door outranks every burial
    if BANISH_EMOJI in s:
        return True
    words = {w for w in BANISH_WORDS if w in s}
    return bool(words)


def _status_is_reviewed(status: str) -> bool:
    """v0.47.0 — True when a Status cell reads as reviewed-and-kept
    (the owner's ✅ gesture). 'unreviewed' (the pre-filled default)
    never counts — it CONTAINS 'reviewed' but says the opposite; a
    revived Status never counts (the ♻️ wins); a DEAD status never
    reaches here (the caller checks dead first — death wins when a
    hand-edited cell says both).
    v0.52.0 — the combined gesture (icon + Status cells) needs the
    marker to win over the default: an ✅ in the # column over an
    'unreviewed' Status cell is a REVIEWED row (the icon column's
    law), so the tick markers are checked BEFORE the 'unreviewed'
    exclusion — the word 'reviewed' alone still needs the exclusion
    (it is a substring of the default)."""
    s = (status or '').strip().lower()
    if not s:
        return False
    if any(m in s for m in REVIVE_MARKERS):
        return False
    if any(m in s for m in ("✅", "✔", "☑", "kept", "done")):
        return True     # a tick marker beats the other cell's default
    if 'unreviewed' in s:
        return False
    return 'reviewed' in s


def _status_is_waiting(status: str) -> bool:
    """v0.51.0 — the " - " verdict: a Status cell that carries NO
    decision at all. The owner's report (session): "app must look at
    decomissioned note, and try to fetch again those that dont have
    skeleton or red cross or such emojies and have this state ' - '" —
    so the waiting state is everything the table's grammar does not
    read as a verdict: blank, ' - ', '—', 'unreviewed', or any text
    that is neither dead (🪦 ❌ ☠️ 💀 dead/retired/decommissioned) nor
    reviewed (✅ …) nor revived (♻️ …) nor the fourth door's queue
    (🖐 hand / queued), nor one of the app's own stamps ('confirmed',
    'auto', '📁 stored' — the row a SUCCESSFUL retry leaves behind).
    A waiting row is the owner saying "this link is valid, its fetch
    failed, try again" — exactly the rows the caught-up check must
    fetch again before "everything is up to date" may be said."""
    s = (status or '').strip()
    if not s:
        return True                     # blank — still waiting
    if _status_is_dead(s) or _status_is_reviewed(s) or _status_is_revived(s):
        return False
    if _status_is_banished(s):
        return False                    # v0.58.0 — 🗑️ owns the row: the
        # banishment pass consumes it, never the caught-up retry
    if _hand_delivery._status_is_hand(s):
        return False                    # the fourth door owns it
    low = s.lower()
    if 'queued' in low or 'confirmed' in low or 'auto' in low:
        return False                    # a stamp the app itself wrote
    if 'stored' in low:
        return False                    # v0.51.0 — the retry succeeded
    return True


def scan_master_waiting_rows(vault_path: str,
                             state: Optional[object] = None,
                             log: Optional[Callable] = None
                             ) -> List[Dict]:
    """v0.51.0 — the " - " rows of the master table, classified.

    The owner's law: before declaring everything up to date the app
    must check the decommissioned note and find the rows that should
    be retried — the rows WITHOUT a verdict emoji whose state is
    " - ". This scan is that check's eyes: every data row of
    ``_review/DECOMMISSIONED.md`` whose Status cell reads as waiting
    (:func:`_status_is_waiting`), each classified by what the link
    itself says on disk and (when a state ledger is injected) in
    cache.db:

      * ``kind: 'fetch'`` — the machine's to retry: the link has a
        FAILED app-owned ``_review`` placeholder, or sits in the retry
        queue, or has no note anywhere at all (a hand-added row — the
        owner wants it fetched like new). This is the set the
        caught-up check retries.
      * ``kind: 'eyes'`` — the owner's to decide, never re-fetched:
        the link's ``_review`` note is a NON-failed one (low
        classification confidence, analysis failure, archived rescue —
        the human-eye classes of v0.49.0).
      * dropped — retired (dead / reviewed / revived / dismissed /
        auto-verdict) or already stored (a real note in a category
        folder: the refresh stamps those rows '📁 stored'), or
        SETTLED (v0.63.2 — the owner's law: already sent to the bot
        and addressed; never machine-fetched again; ♻️ is the door
        back).

    ``state`` is OPTIONAL (the hermetic law): without it the scan is a
    pure file read and the queue/stored probes are skipped (a
    hand-added row for an already-stored link then reads 'fetch' —
    harmless, :meth:`process_link` skips it as "already in the
    websites vault"). Returns
    ``[{'url', 'status', 'notes', 'path', 'kind'}, ...]`` — first row
    wins on a hand-added duplicate URL, sorted by the table's own
    row order. Never raises on a hand-edited table."""
    out: List[Dict] = []
    path = decommission_table_path(vault_path)
    if not vault_path or not os.path.isfile(path):
        return out
    # one pass over _review pairs every app-owned note with its source
    notes: Dict[str, Dict] = {}
    for it in scan_review_notes(vault_path):
        cu = normalize_website_url(it.get('url') or '')
        if cu and cu not in notes:
            notes[cu] = it
    seen: set = set()
    for row in _parse_decommission_rows(path):
        if not _status_is_waiting(row['status']):
            continue
        canonical = normalize_website_url(row['url'])
        if not canonical or canonical in seen:
            continue
        note = notes.get(canonical)
        kind = 'fetch'
        placeholder = ''
        if note is not None:
            placeholder = note.get('path') or ''
            if (note.get('fetch_status') or '').strip().lower() != 'failed':
                kind = 'eyes'           # a human-eye note — not ours
        else:
            kind = 'fetch'              # no note at all — fetch it
        if state is not None and kind == 'fetch':
            try:
                if state.is_dismissed(canonical):
                    continue            # retired — ♻️ is its door back
                # v0.63.2 — THE SETTLED LEDGER: a link the owner already
                # sent to the bot and addressed is not "waiting" for the
                # machine — the owner's law (verbatim): "Do not fetch
                # current websites which are sent to bot, because
                # they're already addressed and processed." ♻️ revived
                # is the door back (consume pass 2 un-settles).
                if state.is_settled(canonical):
                    continue            # settled — addressed, never re-fetched
                prior = state.processed_row(canonical)
                if prior is not None \
                        and prior.get('fetch_status') != 'failed' \
                        and '_review' not in str(
                            prior.get('note_path') or ''
                        ).replace('\\', '/'):
                    continue            # already stored — not waiting
            except Exception as e:
                if log:
                    log(f"⚠️ Master-table state probe skipped for "
                        f"{row['url']}: {e}", "warning")
                # a broken probe never hides a waiting link (the law)
        seen.add(canonical)
        out.append({'url': row['url'], 'status': row['status'],
                    'notes': row.get('notes') or '',
                    'path': placeholder, 'kind': kind})
    return out


def scan_master_hand_rows(vault_path: str,
                          log: Optional[Callable] = None) -> List[Dict]:
    """v0.52.0 — the master table's 🖐 hand rows, the ones the fifth
    door owes a Chrome scrape to.

    The owner's report (session, verbatim): "see I set hand emoji, but
    those links didn't refetched using scrapping manually on chrome"
    — the 🖐 gesture (in the # column OR the Status cell, the icon
    column's law) is not a request for the owner to press Ctrl+S
    anymore; it is a request for the app's own Chrome scraping pass
    (:func:`gitcurator.core.chrome_tabs.deliver_pages_via_chrome`):
    launch the owner's real Chrome, one tab per link, take the live
    DOM, deliver the page into ``_review/hand-delivered/`` — a real
    fetch the next pipeline pass consumes.

    Which rows qualify:

      * the combined gesture reads HAND
        (:func:`gitcurator.core.hand_delivery._status_is_hand`), and
        no stronger verdict outranks it (dead / reviewed / revived —
        the precedence law);
      * the page is NOT already delivered — a queue.json entry marked
        consumed, or whose suggested page file already sits in the
        hand-delivered folder, is done (the record stays, the link is
        not re-scraped);
      * loopback never opens a browser (the v0.15.1 law).

    Returns ``[{'url', 'wall', 'status'}, ...]`` in the table's own
    row order, deduped by canonical URL (first row wins). Pure file
    reads (the table + the fourth door's queue) — no state DB, no
    network; never raises on a hand-edited table."""
    out: List[Dict] = []
    path = decommission_table_path(vault_path)
    if not vault_path or not os.path.isfile(path):
        return out
    delivered: set = set()
    queue: Dict = {}
    try:
        from gitcurator.core import hand_delivery as _hd
        queue = _hd._read_queue(vault_path)
        for url, meta in (queue.get('links') or {}).items():
            suggested = (meta or {}).get('suggested') \
                or _hd.suggested_filename(url)
            page_landed = os.path.isfile(
                os.path.join(_hd.hand_delivery_dir(vault_path),
                             suggested))
            if (meta or {}).get('consumed') or page_landed:
                delivered.add(normalize_website_url(url))
    except Exception:
        delivered = set()   # a broken queue never hides a hand row
    seen: set = set()
    for row in _parse_decommission_rows(path):
        s = row['status']
        if _status_is_dead(s) or _status_is_reviewed(s) \
                or _status_is_revived(s):
            continue
        if not _hand_delivery._status_is_hand(s):
            continue
        canonical = normalize_website_url(row['url'])
        if not canonical or canonical in seen:
            continue
        if canonical in delivered:
            continue        # the page already landed — it is consumed
        try:
            from gitcurator.core.chrome_tabs import is_loopback_url
            if is_loopback_url(canonical):
                continue    # never opens a browser (v0.15.1)
        except Exception:
            pass
        seen.add(canonical)
        wall = row.get('notes') or ''
        out.append({'url': row['url'], 'wall': wall, 'status': s})
    return out


def scan_master_redo_rows(vault_path: str, state=None,
                          log: Optional[Callable] = None) -> List[Dict]:
    """v0.57.0 — THE NOTE IS THE SUCCESS, the redo scan: the 🖐 hand
    rows whose delivery was a FALSE SUCCESS — the page was delivered
    (a queue.json entry consumed, or its page file sitting in the
    hand-delivered folder) but the note never became a proper,
    categorized note in the vault
    (:func:`note_is_properly_stored` fails: no state row, a failed
    fetch, a half-fetched ``_review`` item, a file missing on disk, a
    note that is not the app's own for this link).

    The owner's report (session, verbatim): "the app must refetch and
    generate notes, if they notes aren't properly stored … it's
    fetched and became ✅ in the table, but actually it's note is not
    properly saved and only saved under _review folder, so it's false
    success and must be redo." This scan IS the redo set — every row
    here is owed another pass through the full pipeline (the delivered
    page re-read in place by :func:`take_hand_delivered`'s
    ``allow_consumed`` — no new Chrome tab, the LLM asked again, the
    note rebuilt).

    Which rows qualify:

      * the combined gesture reads HAND, no stronger verdict
        outranks it (dead / reviewed / revived — the precedence law);
      * the link's delivery HAPPENED (queue row consumed or the
        suggested page file exists) — an un-delivered hand row is the
        Chrome pass's set (:func:`scan_master_hand_rows`), never the
        redo's;
      * the state ledger says the note is NOT properly stored
        (``state=None`` degrades honestly: nothing can be proven
        false, so nothing is redone — the hermetic law; a broken
        probe answers the same).

    Returns ``[{'url', 'reason'}]`` in the table's row order, deduped
    by canonical URL (first row wins). Pure file + state reads; never
    raises on a hand-edited table."""
    out: List[Dict] = []
    log = log or (lambda *a, **k: None)
    path = decommission_table_path(vault_path)
    if not vault_path or not os.path.isfile(path):
        return out
    if state is None:
        return out        # nothing can be proven false — the hermetic law
    delivered: set = set()
    folder = ''
    try:
        from gitcurator.core import hand_delivery as _hd
        queue = _hd._read_queue(vault_path)
        folder = _hd.hand_delivery_dir(vault_path)
        for url, meta in (queue.get('links') or {}).items():
            suggested = (meta or {}).get('suggested') \
                or _hd.suggested_filename(url)
            page_landed = os.path.isfile(os.path.join(folder, suggested))
            if (meta or {}).get('consumed') or page_landed:
                delivered.add(normalize_website_url(url))
    except Exception:
        delivered = set()   # a broken queue never hides a false success
    seen: set = set()
    try:
        for row in _parse_decommission_rows(path):
            s = row['status']
            if _status_is_dead(s) or _status_is_reviewed(s) \
                    or _status_is_revived(s):
                continue
            if not _hand_delivery._status_is_hand(s):
                continue
            canonical = normalize_website_url(row['url'])
            if not canonical or canonical in seen:
                continue
            if canonical not in delivered:
                continue    # no delivery yet — the Chrome pass's set
            try:
                if state.is_dismissed(canonical):
                    continue    # retired rows are the verdicts' business
                prior = state.processed_row(canonical)
            except Exception:
                continue    # a broken probe redoes nothing
            proper, why = note_is_properly_stored(prior)
            if proper:
                continue    # the note IS properly stored — done, honestly
            seen.add(canonical)
            out.append({'url': canonical,
                        'reason': why or 'the note is not properly stored'})
    except Exception as e:
        log(f"⚠️ Redo scan skipped: {e}", "warning")
        return out
    return out


def scan_decommission_table(vault_path: str) -> Dict[str, str]:
    """v0.44.0 — read the owner's burial decisions.

    Returns ``{url: status}`` for EVERY data row (the caller decides
    what counts — see ``_status_is_dead`` / ``_status_is_revived``);
    first row wins on a hand-added duplicate URL. Pure file read; no
    state DB, no network — the same law as :func:`scan_review_backlog`."""
    path = decommission_table_path(vault_path)
    if not vault_path or not os.path.isfile(path):
        return {}
    out: Dict[str, str] = {}
    for row in _parse_decommission_rows(path):
        out.setdefault(row['url'], row['status'])
    return out


_GRAVEYARD_HEADER = """# Review Master Table — decommission or approve

> The master note for EVERY link waiting in _review — fetch failures
> waiting for retries AND the human-eye classes (low classification
> confidence, analysis failures, archived rescues): one row per link,
> every link with a Status cell you can set. Edit ONLY the Status
> column — do not delete rows. The # column is yours too: an emoji in
> the FIRST cell of a row (🖐 / 💀 / ✅ / ♻️) reads exactly like the
> same emoji in the Status cell — set it wherever is faster.
> ✅ reviewed — you handled this link: never fetched, never retried
>   again, its _review placeholder is swept.
> 🪦 dead / ❌ dead / ☠️ dead / 💀 dead — the link is dead: same
>   never-fetch retirement, same placeholder sweep.
> 🗑️ banished / delete / auto_delete — the site outlived its welcome
>   (terms changed, no longer free, no longer useful): the STRONGEST
>   retirement — the note itself is REMOVED from the library
>   (recoverable in .trash/banished) AND the URL is blacklisted,
>   never fetched again. ♻️ revived undoes it. You can also set
>   this verdict INSIDE the note itself: open any note and add
>   the tag 🗑️ (or "delete" / "auto_delete") — the next run shows
>   you the count and ASKS first (the Telegram deletion review:
>   "N notes marked for deletion" — 🗑️ Delete / ✋ Keep); on your
>   confirm it removes the note, blacklists the URL, and writes
>   the record row here. Every run answers with the tally line:
>   how many were removed and never fetched again because you
>   confirmed them.
> ♻️ revived (in place of a dead or ✅ Status) — the link is fetched
>   like new again.
> 🖐 hand — the fourth door's gesture, the fifth door's engine: set
>   it (the # cell or the Status cell) and GitCurator itself opens
>   your real Chrome, one tab per hand row, takes the live pages, and
>   processes them as real fetches — no manual saving (you can still
>   save a page into _review/hand-delivered/ yourself; a delivered
>   page is consumed either way). NOT a retirement — the link keeps
>   waiting until the page is delivered. When the delivery STORES
>   the proper, categorized note, the row retires itself:
>   ✅ hand-delivered — fetched <date> (the half-fetched _review
>   item is swept — the note in the vault is the record).
> blank / unreviewed / " - " — still waiting: the caught-up check
>   retries these rows BEFORE "everything is up to date" is said
>   (the link is valid, its fetch failed — the machine tries it
>   again; what still fails keeps waiting, its Status decides).
> 📁 stored — the retry succeeded: the link's note is in the vault
>   (the row is the record, nothing more to do).
> Rows marked "auto" were retired by the fetcher's own verdict
> (dead / paywalled / refused) — set ♻️ revived to disagree.
> v0.60.2 — THE COMPACT TABLE: rows whose verdict is WRITTEN
>   (confirmed / banished / stored / hand-delivered / auto) LEAVE this
>   table after every batch — their records live in the state DB and
>   .trash/banished, and this table keeps only the links that still
>   need you. To REVIVE any retired link (bring it back to be fetched
>   like new), add a row with ♻️ and its URL — any shape works:
>
>     | ♻️ | | https://the-link.example/its-path | | | | |
>
>   (indented here so it stays an instruction, not a row) — the next
>   run reads your row and un-retires the link.
> Auto-updated after every batch. Last updated: {now}

| # | Date | URL | Domain | Source | Status | Notes |
|---|------|-----|--------|--------|--------|-------|
"""


def _table_cell(text: str, limit: int = 96) -> str:
    """v0.47.0 — one markdown-table-safe cell: pipes and newlines are
    the table's structure, so they become '·' / spaces; anything longer
    than ``limit`` chars is truncated with an ellipsis (a table row
    stays a row)."""
    s = str(text or '').replace('|', '·').replace('\n', ' ').replace('\r', ' ')
    s = ' '.join(s.split())
    if len(s) > limit:
        s = s[:limit - 1].rstrip() + '…'
    return s


def write_decommission_candidates(vault_path: str, urls: List[str],
                                  source: str = 'retry backlog',
                                  log: Optional[Callable] = None,
                                  notes: Optional[Dict[str, str]] = None,
                                  status: str = 'unreviewed'
                                  ) -> int:
    """v0.44.0 — pre-fill the graveyard with the failed links waiting in
    ``_review`` so the owner only has to SET THE EMOJI (his gesture, the
    ``_inbox`` tables' law). Rows are APPENDED after the last data row;
    URLs already present (any Status) are never duplicated, owner-edited
    rows are never touched. Creates the table with its header when the
    file does not exist. Atomic + dry-run aware (the house writer).
    v0.47.0 — ``notes`` fills the Notes column per URL (the last error
    or the auto-verdict — table-sanitized, length-capped) and ``status``
    is the pre-filled Status cell (the refresh writes '🪦 auto — <cat>'
    rows for the fetcher's own retirements; the default stays
    'unreviewed'). Returns how many new rows were written."""
    log = log or (lambda *a, **k: None)
    path = decommission_table_path(vault_path)
    if not vault_path:
        return 0
    review_dir = os.path.join(vault_path, REVIEW_FOLDER)
    try:
        os.makedirs(review_dir, exist_ok=True)
    except Exception as e:
        log(f"⚠️ Could not create {review_dir}: {e}", "warning")
        return 0
    rows = _parse_decommission_rows(path) if os.path.isfile(path) else []
    existing = {r['url'] for r in rows}
    date_str = datetime.now().strftime('%Y-%m-%d')
    notes = notes or {}
    status = _table_cell(status, limit=48) or 'unreviewed'
    new_rows = []
    for url in urls:
        if not url or url in existing:
            continue
        existing.add(url)
        try:
            from urllib.parse import urlparse
            domain = urlparse(url).netloc or 'unknown'
        except Exception:
            domain = 'unknown'
        note = _table_cell(notes.get(url, ''))
        new_rows.append(
            f"| - | {date_str} | {_links.scrub_url_token(url)} | "
            f"{_table_cell(domain, limit=64)} "
            f"| {_table_cell(source, limit=32)} | {status}"
            + (f" | {note} |" if note else " | |"))
    if not new_rows:
        return 0
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    if os.path.isfile(path):
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.read().splitlines()
        except Exception as e:
            log(f"⚠️ Could not read the graveyard table: {e}", "warning")
            return 0
        last_data = 0
        for i, line in enumerate(lines):
            if line.startswith('| ') and 'http' in line:
                last_data = i
        lines = lines[:last_data + 1] + new_rows + lines[last_data + 1:]
        lines = [f"> Last updated: {now}"
                 if line.startswith('> Last updated:') else line
                 for line in lines]
        content = '\n'.join(lines).rstrip('\n') + '\n'
    else:
        content = _GRAVEYARD_HEADER.format(now=now) \
            + '\n'.join(new_rows) + '\n'
    try:
        atomic_write_text(path, content)
    except Exception as e:
        log(f"⚠️ Could not write the graveyard table: {e}", "warning")
        return 0
    log(f"📋 {len(new_rows)} row(s) written to {DECOMMISSION_TABLE} (the "
        f"master table — set Status to 🪦 dead to bury or ✅ reviewed to "
        f"keep; ♻️ revived brings one back)", "info")
    return len(new_rows)


def mark_urls_dead_in_table(vault_path: str, urls: List[str],
                            log: Optional[Callable] = None) -> int:
    """v0.44.0 — set the burial marker on chosen rows (the in-app
    picker's hand — it writes the SAME Status cell the owner would edit
    by hand in Obsidian, so the table stays the one ledger). Rows whose
    URL is not in the table are appended as dead first (a burial is
    valid even for a link that never got a placeholder). Returns how
    many rows were marked."""
    log = log or (lambda *a, **k: None)
    path = decommission_table_path(vault_path)
    if not vault_path:
        return 0
    wanted = list(dict.fromkeys(urls))  # dedupe, keep order
    if not wanted:
        return 0
    # Append the missing ones as fresh rows, then flip every Status.
    write_decommission_candidates(vault_path, wanted, source='picker',
                                  log=None)
    rows = _parse_decommission_rows(path)
    if not rows:
        return 0
    date_str = datetime.now().strftime('%Y-%m-%d')
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.read().splitlines()
    except Exception as e:
        log(f"⚠️ Could not read the graveyard table: {e}", "warning")
        return 0
    marked = 0
    for row in rows:
        if row['url'] not in wanted:
            continue
        parts = row['raw'].split('|')
        if len(parts) < 8:
            continue
        parts[6] = f" 🪦 dead — decommissioned {date_str} "
        lines[row['line']] = '|'.join(parts)
        marked += 1
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    lines = [f"> Last updated: {now}"
             if line.startswith('> Last updated:') else line
             for line in lines]
    try:
        atomic_write_text(path, '\n'.join(lines).rstrip('\n') + '\n')
    except Exception as e:
        log(f"⚠️ Could not write the graveyard table: {e}", "warning")
        return 0
    if marked:
        log(f"🪦 {marked} link(s) marked dead in {DECOMMISSION_TABLE}",
            "info")
    return marked


def stamp_stored_rows(state, vault_path: str,
                      log: Optional[Callable] = None) -> int:
    """v0.51.0 — the truth stamp: a master-table row whose Status still
    reads as waiting (" - " / blank / unreviewed) while its link is
    ALREADY STORED (a processed row with a real note outside _review)
    is rewritten to ``📁 stored — <date>``. The waiting rows are the
    caught-up check's retry set — without this stamp the table lies
    "waiting" about links whose retries long succeeded, and the owner
    re-reads " - " rows that are done. Owner-set verdicts (🪦 ✅ ♻️ 🖐)
    are NEVER touched (the same law as every table writer); a broken
    state probe stamps nothing (never crashes a batch). Returns how
    many rows were stamped."""
    log = log or (lambda *a, **k: None)
    path = decommission_table_path(vault_path)
    if not vault_path or not os.path.isfile(path):
        return 0
    rows = _parse_decommission_rows(path)
    if not rows:
        return 0
    date_str = datetime.now().strftime('%Y-%m-%d')
    stamped = 0
    lines = None
    for row in rows:
        if not _status_is_waiting(row['status']):
            continue
        canonical = normalize_website_url(row['url'])
        if not canonical:
            continue
        try:
            if state.is_dismissed(canonical):
                continue        # retired rows are the verdicts' business
            prior = state.processed_row(canonical)
        except Exception:
            continue            # a broken probe stamps nothing
        if prior is None:
            continue
        if prior.get('fetch_status') == 'failed':
            continue            # the placeholder story — still waiting
        if '_review' in str(prior.get('note_path') or '').replace('\\', '/'):
            continue            # a _review note waits for human eyes
        parts = row['raw'].split('|')
        if len(parts) < 8:
            continue
        parts[6] = f" 📁 stored — fetched {date_str} "
        if lines is None:
            try:
                with open(path, 'r', encoding='utf-8',
                          errors='replace') as f:
                    lines = f.read().splitlines()
            except Exception as e:
                log(f"⚠️ Could not re-read the master table: {e}",
                    "warning")
                return stamped
        if lines and row['line'] < len(lines):
            lines[row['line']] = '|'.join(parts)
            stamped += 1
    if stamped:
        now = datetime.now().strftime('%Y-%m-%d %H:%M')
        lines = [f"> Last updated: {now}"
                 if line.startswith('> Last updated:') else line
                 for line in lines]
        try:
            atomic_write_text(path,
                              '\n'.join(lines).rstrip('\n') + '\n')
        except Exception as e:
            log(f"⚠️ Could not write the master table: {e}", "warning")
            return 0
        log(f"📁 {stamped} master-table row(s) stamped 'stored' — their "
            f"retries succeeded, the notes are in the vault", "info")
    return stamped


def sweep_review_leftovers(vault_path: str, canonical: str,
                           keep_path: str = '',
                           log: Optional[Callable] = None) -> int:
    """v0.56.0 — THE HAND'S HARVEST, the sweep: remove the link's
    HALF-FETCHED items from ``_review`` once its proper, categorized
    note has landed in the vault.

    The owner's words (session, verbatim): "remove their half-fetched
    items from _review, because now they have A Proper and categorized
    note." A half-fetched item is any app-owned note still sitting in
    ``<vault>/_review/`` for this source — the failed placeholder the
    wall left behind, a low-confidence note from an earlier era, a
    partial or an analysis-failure note — whatever the successful
    delivery made obsolete. The ownership test is the folder's own
    frontmatter law (``managed_by: gitcurator`` — the same test
    :func:`scan_review_notes` and :func:`_remove_scanned_placeholder`
    use): a hand-written note is a human's, KEPT with a warning (the
    duplicate detector's business, never ours). The master table
    (``DECOMMISSIONED.md``) and the hand-delivered folder are never
    touched (the table is the ledger, the delivered page is the
    owner's record). Dry-run aware (every file mutation in the
    pipeline goes through core/dryrun); never raises. Returns how
    many notes were swept."""
    log = log or (lambda *a, **k: None)
    if not vault_path or not canonical:
        return 0
    review_dir = os.path.join(vault_path, REVIEW_FOLDER)
    try:
        names = sorted(os.listdir(review_dir))
    except Exception:
        return 0        # no folder / unreadable — nothing to sweep
    swept = 0
    for name in names:
        if not name.lower().endswith('.md') or name == DECOMMISSION_TABLE:
            continue
        path = os.path.join(review_dir, name)
        if not os.path.isfile(path):
            continue    # the hand-delivered folder is a directory
        try:
            if keep_path and os.path.normpath(path) \
                    == os.path.normpath(keep_path):
                continue
            fm = _parse_review_frontmatter(path)
        except Exception:
            continue    # an unreadable note is never our call
        if not fm or fm.get('managed_by', '').lower() \
                != MANAGED_BY_GITCURATOR:
            source = ''
            try:
                with open(path, 'r', encoding='utf-8',
                          errors='replace') as f:
                    for line in f.read().splitlines()[:12]:
                        if line.strip().lower().startswith('source:'):
                            source = line.split(':', 1)[1].strip()
                            break
            except Exception:
                source = ''
            if source and _same_source(source, canonical):
                log(f"⚠️ kept {name} — it looks hand-written for the "
                    f"same source; the proper note and it now "
                    f"coexist (the duplicate detector's call, never "
                    f"ours)", "warning")
            continue
        source = (fm.get('source') or '').strip()
        if not source or not _same_source(source, canonical):
            continue    # another link's note — untouched
        try:
            _dryrun.remove(path)
            swept += 1
            log(f"♻️ swept the half-fetched _review item {name} — the "
                f"proper, categorized note replaced it", "info")
        except Exception as e:
            log(f"⚠️ could not sweep {path}: {e}", "warning")
    return swept


def _same_source(source: str, canonical: str) -> bool:
    """v0.56.0 — does a note's frontmatter ``source`` point at the
    same link as ``canonical``? Both sides normalized (a hand-edited
    note may carry the URL in whatever shape the owner pasted). Pure;
    anything unreadable is simply not the same link."""
    try:
        return bool(source) and bool(canonical) \
            and normalize_website_url(source) == canonical
    except Exception:
        return False


def _note_lists_site_link(path: str, canonical: str) -> bool:
    """v0.64.0 — does the note's ``site_links:`` frontmatter list carry
    this canonical link? (ONE NOTE PER SITE's ownership proof: the note
    belongs to the site, the link rides the list.) Pure read; False on
    any doubt."""
    if not path or not canonical or not os.path.isfile(path):
        return False
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            head = f.read(4000)
    except Exception:
        return False
    m = _SITE_LINKS_LINE_RE.search(head)
    if not m:
        return False
    return canonical in _parse_site_links_list(m.group(1))


def note_is_properly_stored(prior: Optional[Dict]) -> (bool, str):
    """v0.57.0 — THE NOTE IS THE SUCCESS, the test: does a state row's
    note amount to a PROPER, CATEGORIZED note in the vault?

    The owner's report (session, verbatim): "For example websiteX has
    hand emoji. and it's fetched and became ✅ in the table, but
    actually it's note is not properly saved and only saved under
    _review folder, so it's false success and must be redo." A ✅ may
    only ever mean the note ITSELF is properly stored, so every
    half-fetched shape fails the test — each with its own one-line
    reason (the redo pass and the logs tell it):

      * no state row, or fetch_status ``failed`` — the wall's
        placeholder story, never a note;
      * the note path under ``_review`` — a half-fetched item (low
        confidence, partial, an analysis failure), waiting for eyes;
      * the file missing on disk — a note the state remembers but the
        vault no longer carries (deleted, moved, a vault switch);
      * frontmatter that is not the app's own note FOR THIS link — a
        same-named stranger is not a delivery.

    Pure; never raises (anything unreadable is simply not proper)."""
    try:
        if not prior:
            return False, 'no processed row — the note never landed'
        if str(prior.get('fetch_status') or '') == 'failed':
            return False, 'the row records a failed fetch'
        path = str(prior.get('note_path') or '').replace('\\', '/')
        if not path:
            return False, 'no note path recorded'
        if '/_review/' in f'/{path.strip("/")}/':
            return False, 'the note is half-fetched in _review'
        if not os.path.isfile(path):
            return False, 'the note file is missing on disk'
        fm = _parse_review_frontmatter(path)
        if not fm or str(fm.get('managed_by') or '').lower() \
                != MANAGED_BY_GITCURATOR:
            return False, 'the note is not app-owned'
        source = str(fm.get('source') or '').strip()
        if not source:
            return False, 'the note carries no source link'
        try:
            own = normalize_website_url(
                str(prior.get('url') or prior.get('canonical') or ''))
        except Exception:
            own = ''
        if own and normalize_website_url(source) != own:
            # v0.64.0 — ONE NOTE PER SITE: a note whose site_links list
            # carries this link is a PROPER delivery too — the link
            # consolidated into its site's note (the owner's law: one
            # note per site, the extra links ride the list). The site
            # key must agree (the note is the SITE's; the link is one
            # of the site's pages — a stray same-named stranger with a
            # pasted link still fails).
            if not (own and site_key_of(own)
                    and site_key_of(own) == site_key_of(source)
                    and _note_lists_site_link(path, own)):
                return False, 'the note belongs to another link'
        return True, ''
    except Exception:
        return False, 'the note could not be verified'


def harvest_hand_rows(state, vault_path: str,
                      log: Optional[Callable] = None) -> int:
    """v0.56.0 — THE HAND'S HARVEST, the catch-up pass: a 🖐 row whose
    link is ALREADY STORED (a processed row with a real note outside
    ``_review`` — the exact situation the owner described, the runs
    that stored correctly while the gesture waited) is retired to
    ``✅ hand-delivered`` and its half-fetched _review items are
    swept. The owner's law applied to the backlog, so the table stops
    showing the door's gesture for links whose notes are already in
    the vault. The state probe is optional (the hermetic law) and a
    broken probe harvests nothing (the same law as
    :func:`stamp_stored_rows`); owner-set verdicts stronger than the
    gesture are never touched (the precedence law).

    v0.57.0 — THE NOTE IS THE SUCCESS: the "already stored" test is
    :func:`note_is_properly_stored` — the ✅ is only earned when the
    note is ON DISK, app-owned, for THIS link, outside ``_review``.
    A state row pointing at a half-fetched ``_review`` item, or at a
    file the vault no longer carries, is a FALSE success — the row
    keeps its gesture and joins the redo pass
    (:func:`scan_master_redo_rows`) instead. Returns rows
    harvested."""
    log = log or (lambda *a, **k: None)
    path = decommission_table_path(vault_path)
    if not vault_path or not os.path.isfile(path):
        return 0
    retired: List[str] = []
    try:
        for row in _parse_decommission_rows(path):
            s = row['status']
            if _status_is_dead(s) or _status_is_reviewed(s) \
                    or _status_is_revived(s):
                continue    # a stronger verdict owns the cell
            if not _hand_delivery._status_is_hand(s):
                continue    # only the gesture retires here
            canonical = normalize_website_url(row['url'])
            if not canonical or canonical in retired:
                continue
            try:
                if state.is_dismissed(canonical):
                    continue    # retired rows are the verdicts' business
                prior = state.processed_row(canonical)
            except Exception:
                continue    # a broken probe harvests nothing
            proper, _why = note_is_properly_stored(prior)
            if prior is None or not proper:
                continue    # no proper, categorized note — no harvest
            retired.append(canonical)
    except Exception as e:
        log(f"⚠️ Hand-harvest scan skipped: {e}", "warning")
        return 0
    harvested = 0
    if retired:
        harvested = _hand_delivery.stamp_hand_delivered_rows(
            vault_path, retired, log=log)
        for canonical in retired:
            try:
                sweep_review_leftovers(vault_path, canonical, log=log)
            except Exception as e:
                log(f"⚠️ Hand-harvest sweep skipped for {canonical}: "
                    f"{e}", "warning")
    return harvested


# ===========================================================================
# v0.58.0 — THE BANISHMENT: delete and never fetch again
#
# The owner's ask (session, verbatim): "Some websites that are
# currently stored in the vault are not favored anymore … they've
# changed their terms or doesn't offer free services … if I just
# delete it's record from vault, system will re-fetch and restore it.
# But I want a system that let me to delete and never fetch again some
# websites … in tags of websites, we can have a meta-data for this
# case, for example I choose 'delete' or a specific emoji."
#
# The vault is a garden, not an archive: sites change their terms,
# drop their free tier, die quietly. The owner keeps it "tiny,
# essential and practical without hoarding wasteful websites" — and
# the deletion loop he named is real: a silently deleted note is an
# ACCIDENT to this app (v0.57.0's redo pass regenerates it — the note
# is the success), so plain deletion always comes back. The banishment
# is the DELIBERATE gesture: mark the note (🗑️ tag / delete /
# decommission: true) or mark the master-table row (🗑️ Status) — the
# next run removes the note, blacklists the URL (the never-fetch
# dismissal gate), and leaves the record row revivable (♻️).
# ===========================================================================

def _parse_banish_frontmatter(path: str) -> Optional[Dict]:
    """v0.58.0 — the frontmatter the banishment reads: ``source``,
    ``managed_by`` (ownership — a hand-written note is never our call),
    the ``tags`` LIST (flow style ``tags: [a, b]`` AND the block style
    Obsidian's property editor writes — ``tags:`` on its own line, the
    items as ``  - a`` lines below), and the banish boolean keys
    (``decommission: / banish: / blacklist:``). None when the file has
    no frontmatter block or cannot be read. String-scan only — the
    house law (core never grows a YAML dependency)."""
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.read(200_000).splitlines()
    except Exception:
        return None
    if not lines or lines[0].strip() != '---':
        return None
    tags: List[str] = []
    banish_values: Dict[str, str] = {}
    out: Dict[str, str] = {}
    in_tag_block = False
    for line in lines[1:]:
        s = line.strip()
        if s == '---':
            break
        if in_tag_block and s.startswith('- '):
            tags.append(s[2:].strip().strip('"').strip("'").strip())
            continue
        in_tag_block = False
        if ':' not in s:
            continue
        key, _, val = s.partition(':')
        key = key.strip().lower()
        val = val.strip()
        if key == 'tags':
            inner = val
            if inner.startswith('[') and inner.endswith(']'):
                inner = inner[1:-1]
            elif not inner:
                in_tag_block = True      # block style — items follow
            for item in inner.split(','):
                item = item.strip().strip('"').strip("'").strip()
                if item:
                    tags.append(item)
            continue
        clean = val.strip('"').strip("'").strip()
        if key in BANISH_KEYS:
            banish_values[key] = clean
        if key in ('source', 'managed_by'):
            out[key] = clean
    out['tags'] = tags
    out['banish'] = banish_values
    return out


def _note_reads_banished(fm: Optional[Dict]) -> (bool, str):
    """v0.58.0 — does a note's own frontmatter carry the owner's delete
    verdict? Pure predicate over what :func:`_parse_banish_frontmatter`
    read. A tag counts when it CONTAINS the emoji (``🗑️``, ``🗑️
    delete``) or IS a banish word exactly (``delete``, and since
    v0.59.0 ``auto_delete`` — the word Obsidian autocompletes the
    owner's typed "delete" to; the tag ``deleted-files`` never
    fires); a boolean key counts when its value
    reads true/yes/1/on. Returns ``(verdict, the marker that fired)``
    — the marker rides the log line and the table row so the owner sees
    WHICH of his gestures the app obeyed."""
    if not fm:
        return False, ''
    for t in (fm.get('tags') or []):
        s = str(t).strip().lower().lstrip('#').strip()
        if not s:
            continue
        if BANISH_EMOJI in s:
            return True, str(t).strip()
        if s in BANISH_WORDS:
            return True, str(t).strip()
    for key, val in (fm.get('banish') or {}).items():
        if key not in BANISH_KEYS:
            continue    # a stranger's key is never our verdict
        if str(val).strip().lower() in ('true', 'yes', '1', 'on'):
            return True, f"{key}: {val}"
    return False, ''


#: v0.60.1 — the body-tag scan's stop set: an Obsidian inline tag ends
#: at whitespace, another ``#``, or any of these (``#delete.`` is the
#: tag ``delete`` plus sentence punctuation). A heading never parses
#: (``# word`` has the space; ``##word`` starts with the ``#`` run).
_BODY_TAG_STOPS = " \t\r\n,.;:!?)]}'\"»«…—>"


def _is_trash_folder(rel: str) -> bool:
    """v0.62.0 — is this vault-relative folder path the owner's Trash?

    Root-level only (``Trash``, ``trash`` — case-insensitive, the
    spelling is the owner's choice) and its subfolders
    (``Trash/whatever``). A ``SomeDir/Trash`` deep inside the tree is a
    category folder that happens to share the name — never the door
    (the convention is ONE visible waiting room at the top, the same
    way Obsidian's own trash is a root folder). Pure predicate."""
    rel = (rel or '').strip().replace('\\', '/').strip('/').lower()
    if not rel or rel == '.':
        return False
    first = rel.split('/', 1)[0]
    return first in TRASH_FOLDER_NAMES


def _note_body_banish_tag(path: str) -> str:
    """v0.60.1 — the note BODY's own inline tags.

    The owner's report (session, verbatim): "I tagged one note as
    'auto-delete' but it didn't detect" — Obsidian's natural tagging
    is typing ``#auto-delete`` in the note TEXT; the frontmatter
    ``tags:`` list is the properties panel's storage, and v0.58–v0.60
    only read that one. The eyes learn the body: a token counts when
    it is ``#`` + a non-space run (Obsidian's own law — a tag has no
    space between the ``#`` and the word; a heading's ``##`` or
    ``# `` never parses) preceded by a line start, whitespace, or an
    opening bracket, and the run contains 🗑️ or IS a banish word
    exactly (``#deleted-files`` and ``#delete-me`` never fire — the
    same tight match as the frontmatter door). The frontmatter block
    and fenced code blocks are skipped (the app's own hint lines
    spell the words in quotes, never as ``#`` tokens; a snippet's
    ``#purge`` comment never fires). Pure string scan; never raises.
    Returns the marker as typed (``#auto-delete``) or ``''``.
    """
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.read(200_000).splitlines()
    except Exception:
        return ''
    if not lines:
        return ''
    # skip the frontmatter block (its own door already read it)
    start = len(lines)             # unterminated block: all frontmatter
    if lines[0].strip() == '---':
        for i in range(1, len(lines)):
            if lines[i].strip() == '---':
                start = i + 1
                break
    in_fence = False
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith('```') or stripped.startswith('~~~'):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for i, ch in enumerate(line):
            if ch != '#':
                continue
            if i + 1 >= len(line):
                continue
            nxt = line[i + 1]
            if nxt in ' \t#':
                continue        # "# word" (a heading) or "##" — never a tag
            prev = line[i - 1] if i > 0 else ''
            if prev and prev not in ' \t([':
                continue        # mid-word (C#, a URL's #fragment) — not a tag
            j = i + 1
            while j < len(line) and line[j] not in _BODY_TAG_STOPS \
                    and line[j] != '#':
                j += 1
            token = line[i + 1:j].strip()
            if not token:
                continue
            low = token.lower()
            if BANISH_EMOJI in low or low in BANISH_WORDS:
                return '#' + token
    return ''


def scan_banished_notes(vault_path: str,
                        log: Optional[Callable] = None) -> List[Dict]:
    """v0.58.0 — every note in the library carrying the banish mark.

    Walks the vault the sweep walks (category folders, ``_review``,
    ``_missing``, ``_moc`` — the note can be marked wherever it lives;
    ``_inbox`` record tables and dot-folders are skipped, so a note
    already resting in ``.trash/banished`` is never re-found). Returns
    ``[{'url', 'path', 'marker', 'door', 'app_owned'}, ...]`` sorted by
    the walk's deterministic order — hand-written marked notes ride the
    list with ``app_owned: False`` so the burial can keep + warn (the
    sacred law) instead of silently ignoring them. v0.62.0 — THE TRASH
    DOOR joins the grammar as the third read: a note the owner MOVED
    into the root ``Trash`` folder (any spelling, its subfolders too)
    carries the verdict by placement alone (``door: 'trash folder'``)
    — the tag doors stay first (a tag's marker wins when the owner
    both marked and moved). Pure file reads; no state DB, no network;
    never raises."""
    log = log or (lambda *a, **k: None)
    out: List[Dict] = []
    if not vault_path or not os.path.isdir(vault_path):
        return out
    for root, dirs, files in os.walk(vault_path):
        dirs[:] = sorted(d for d in dirs
                         if d != '_inbox' and not d.startswith('.'))
        rel_root = os.path.relpath(root, vault_path).replace(os.sep, '/')
        in_trash = _is_trash_folder(rel_root)
        for name in sorted(files):
            if not name.lower().endswith('.md'):
                continue
            path = os.path.join(root, name)
            if not os.path.isfile(path):
                continue
            fm = _parse_banish_frontmatter(path)
            if not fm:
                continue            # not a frontmattered note — a human's
            marked, marker = _note_reads_banished(fm)
            door = 'note tag'
            if not marked:
                # v0.60.1 — the BODY's own inline tags: the owner's
                # natural "tag the note" is typing #auto-delete in the
                # text (Obsidian's inline tags — the report: "I tagged
                # one note as 'auto-delete' but it didn't detect").
                # The frontmatter door stays first (its marker wins
                # when both are present); the body is the second read.
                marker = _note_body_banish_tag(path)
                marked = bool(marker)
            if not marked and in_trash:
                # v0.62.0 — THE TRASH DOOR, the third read: the move
                # itself is the verdict (the owner's ask, verbatim:
                # "every note which goes to trash will be deleted from
                # vault and never fetch again"). Placement needs no
                # tag at all; the tags above simply win the marker
                # when the owner marked AND moved.
                marker = TRASH_MARKER
                marked = True
                door = 'trash folder'
            if not marked:
                continue
            url = (fm.get('source') or '').strip()
            if not url.lower().startswith(('http://', 'https://')):
                continue            # the directory note / tagless files
            out.append({'url': url, 'path': path, 'marker': marker,
                        'door': door,
                        'app_owned': fm.get('managed_by', '').lower()
                        == MANAGED_BY_GITCURATOR})
    return out


def split_dismissed_links(state, urls: List[str],
                          log: Optional[Callable] = None
                          ) -> (List[str], List[str]):
    """v0.60.1 — the batch count's own honesty.

    The owner's report (session, verbatim): "it still counts
    decommissioned links as unprocessed, then skip them in process."
    Links whose verdict is already written — the graveyard's dead, the
    banishment's blacklisted, the fetcher's own auto-verdicts, the
    SAME gate :meth:`process_link` enforces link-by-link — leave the
    batch's COUNT before processing starts, with one honest line
    instead of a per-link skip pile. Returns ``(kept, dismissed)``;
    the state probe is guarded (a broken DB never hides a link — the
    worst case is the old behavior, the per-link gate); never raises.
    """
    log = log or (lambda *a, **k: None)
    kept: List[str] = []
    dropped: List[str] = []
    for u in (urls or []):
        try:
            c = normalize_website_url(u)
            if c and state is not None and state.is_dismissed(c):
                dropped.append(u)
                continue
        except Exception:
            pass        # a broken probe never hides a link
        kept.append(u)
    if dropped:
        log(
            f"🪦 {len(dropped)} link(s) already have their verdict "
            f"(decommissioned / banished / auto-retired) — excluded "
            f"from the run's count before processing: never fetched "
            f"again, never counted as unprocessed (♻️ in the master "
            f"table revives any of them)", "info")
    return kept, dropped


def split_settled_links(state, urls: List[str],
                        log: Optional[Callable] = None
                        ) -> (List[str], List[str]):
    """v0.63.2 — THE SETTLED LEDGER's own honesty (the twin of
    :func:`split_dismissed_links`).

    The owner's report (session, verbatim): "Do not fetch current
    websites which are sent to bot, because they're already addressed
    and processed. Fetch only websites, that are added to bot, from
    now on." Links already in the settled ledger leave the batch's
    count BEFORE processing starts, with one honest line instead of a
    per-link skip pile. Returns ``(kept, settled)``; the state probe
    is guarded (a broken DB never hides a link — the worst case is
    the old behavior, the per-link gates); never raises.

    NB: the 🖐 hand gesture outranks the settlement exactly as it
    outranks the burned-out retry counter (v0.56.0) — but THAT
    exemption needs the pipeline's own probes (the master-table row,
    the delivered folder), so it lives in :meth:`WebsitePipeline.run`
    and the backlog driver, not in this pure state-level split."""
    log = log or (lambda *a, **k: None)
    kept: List[str] = []
    settled: List[str] = []
    for u in (urls or []):
        try:
            c = normalize_website_url(u)
            if c and state is not None and state.is_settled(c):
                settled.append(u)
                continue
        except Exception:
            pass        # a broken probe never hides a link
        kept.append(u)
    if settled:
        log(
            f"🤝 {len(settled)} link(s) are settled — already sent to "
            f"the bot and addressed (the owner's law) — excluded from "
            f"the run's count before processing: never fetched again "
            f"(♻️ revived in the master table un-settles any of them)",
            "info")
    return kept, settled


def settle_the_ledger(state, vault_path: str,
                      log: Optional[Callable] = None,
                      queue_urls: Optional[List[str]] = None) -> Dict:
    """v0.63.2 — THE SETTLEMENT, run at every production door (the
    websites phase, the bot-queue classification, the caught-up scan).

    The owner's report (session, verbatim): "Do not fetch current
    websites which are sent to bot, because they're already addressed
    and processed. Fetch only websites, that are added to bot, from
    now on." ONE time per machine — guarded by the
    ``websites_settled_at`` meta key — every website the system
    already knows is settled (never machine-fetched again): the state
    ledger's own URLs (stored, walled, dismissed) plus the vault's
    truth (every data-row URL of the master table, every ``_review``
    note URL — hand-added rows and lost-state placeholders included),
    and the fetch-retry queue gets its settlement date (cleared — no
    backoff timer re-serves an old link). Later calls are one cheap
    SELECT (the meta guard runs BEFORE the file scans — the v0.06
    anti-freeze rule). Returns
    ``{'settled': n, 'retries_cleared': m, 'already': bool}``; never
    raises on the caller's head (a broken settlement logs and defers
    to :meth:`WebsiteStateDB.settle_existing`'s own guard).

    v0.64.1 — ``queue_urls`` (the queue door passes the FULL bot
    history it just read) runs THE QUEUE-HISTORY SETTLEMENT after the
    first pass: a second one-time seed (its own meta key) that settles
    every link the owner already sent to the bot even when no state
    row ever existed for it — the never-batched history that kept
    counting as PENDING (the owner's report: "despite everything is
    fetched and processed, somehow the app says 85 sites need
    processing… in the procedure they'll get skipped nonetheless").
    Only the queue door passes it; the other doors keep the cheap
    first-pass-only shape."""
    log = log or (lambda *a, **k: None)
    # v0.64.0 — THE ONE SPELLING's healing pass, at every door and
    # BEFORE the meta guard: the historical spellings (the owner's
    # report — one link, one slash apart, processed twice) are re-keyed
    # under the fixed normalizer, so a settled/dismissed/processed row
    # written with the root slash still answers a probe without it
    # (and vice versa). Idempotent and cheap; a state object without
    # the method (a mock, an older shape) is simply skipped.
    try:
        _heal = getattr(state, 'normalize_ledger_keys', None)
        if callable(_heal):
            _rep = _heal() or {}
            if _rep.get('rekeyed'):
                log(f"🩹 The one spelling: {_rep.get('rekeyed')} ledger "
                    f"row(s) re-keyed to their canonical URL form"
                    + (f" ({_rep.get('merged')} duplicate spelling(s) "
                       f"merged)" if _rep.get('merged') else ""),
                    "info")
    except Exception:
        pass    # a broken heal never blocks the settlement
    try:
        _first_already = bool(state.get_meta(SETTLED_META_KEY))
    except Exception:
        _first_already = False   # fall through — settle_existing guards again
    if _first_already and queue_urls is None:
        # the old shape, unchanged: this door has nothing new to do
        return {'settled': 0, 'retries_cleared': 0, 'already': True}
    rep: Dict = {'settled': 0, 'retries_cleared': 0, 'already': True}
    if not _first_already:
        extra: List[str] = []
        if vault_path and os.path.isdir(vault_path):
            try:
                extra += [r.get('url') or '' for r in _parse_decommission_rows(
                    decommission_table_path(vault_path))]
            except Exception:
                pass    # a hand-edited table never blocks the settlement
            try:
                extra += [it.get('url') or '' for it in
                          scan_review_notes(vault_path)]
            except Exception:
                pass    # an unreadable _review never blocks the settlement
        extra = [normalize_website_url(u) for u in extra if u]
        try:
            rep = state.settle_existing(extra_urls=extra) or {}
        except Exception as e:
            log(f"⚠️ Settlement skipped: {e}", "warning")
            rep = {'settled': 0, 'retries_cleared': 0, 'already': True}
        if not rep.get('already'):
            log(
                f"🤝 THE SETTLEMENT: {rep.get('settled', 0)} website link(s) "
                f"the bot already delivered are settled — addressed and "
                f"processed, never fetched again; "
                f"{rep.get('retries_cleared', 0)} queued retry(ies) "
                f"cleared. Only websites added from now on are fetched "
                f"(♻️ revived in the master table un-settles any of them)",
                "info")
    # v0.64.1 — THE QUEUE-HISTORY SETTLEMENT, the queue door only (the
    # websites twin of v0.63.3's repos extras): every link the bot's
    # history carried at this door — including the never-batched pile
    # no state row ever recorded — is settled, one time, under its own
    # meta key. A state object without the method (a mock, an older
    # shape) is skipped with one warning, never raised.
    if queue_urls is not None:
        try:
            _qh = state.settle_queue_history(extra_urls=queue_urls) or {}
        except Exception as e:
            _qh = {}
            log(f"⚠️ Queue-history settlement skipped: {e}", "warning")
        if not _qh.get('already') and _qh.get('settled'):
            log(
                f"🤝 THE QUEUE-HISTORY SETTLEMENT (websites): "
                f"{_qh.get('settled', 0)} link(s) the bot already "
                f"delivered are settled — they were never batched into "
                f"a state row, so the queue kept counting them as "
                f"pending; addressed and processed now, never counted "
                f"or fetched again. Only websites added from now on are "
                f"counted (♻️ revived in the master table un-settles "
                f"any of them)",
                "info")
    if _first_already:
        return {'settled': 0, 'retries_cleared': 0, 'already': True}
    return rep


def scan_pending_banishments(vault_path: str,
                             log: Optional[Callable] = None) -> Dict:
    """v0.60.0 — THE GATE'S EYES: what WOULD be banished this run.

    The detection half of the confirmation gate (the owner's ask,
    verbatim: "At the beginning of every run, system scans vault, find
    what I've marked to delete, system detects them, show me them
    their numbers"). Both doors, one list, deduped by canonical URL (a
    link marked on its note AND in the table is ONE pending deletion):
    the note-tag door (:func:`scan_banished_notes` — app-owned marked
    notes only; a hand-written marked note is the owner's to delete by
    hand, counted separately) and the master-table door
    (:func:`scan_decommission_table` — 🗑-marked rows that are not yet
    confirmed-stamped; a ``🗑️ banished — confirmed <date>`` row is
    history, not a pending ask). v0.62.0 — the note-tag door now
    carries the TRASH DOOR too (a note the owner moved into the root
    ``Trash`` folder rides the list with ``door: 'trash folder'`` —
    the move is the verdict). Pure file reads; no state DB, no
    network; never raises. Returns ``{'items': [{'url', 'canonical',
    'title', 'marker', 'door', 'path'}], 'kept_handwritten': int}``."""
    log = log or (lambda *a, **k: None)
    out: Dict = {'items': [], 'kept_handwritten': 0}
    if not vault_path or not os.path.isdir(vault_path):
        return out
    seen: set = set()
    for it in scan_banished_notes(vault_path, log=log):
        if not it.get('app_owned'):
            out['kept_handwritten'] += 1
            continue
        canonical = normalize_website_url(it.get('url') or '')
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        out['items'].append({
            'url': it.get('url') or canonical, 'canonical': canonical,
            'title': os.path.splitext(os.path.basename(
                it.get('path') or ''))[0] or canonical,
            'marker': it.get('marker') or 'delete mark',
            'door': it.get('door') or 'note tag',
            'path': it.get('path') or ''})
    try:
        table = scan_decommission_table(vault_path)
    except Exception as e:
        log(f"⚠️ Banish-gate table scan skipped: {e}", "warning")
        table = {}
    for url, status in (table or {}).items():
        if not _status_is_banished(status or ''):
            continue
        if 'confirmed' in (status or '').lower():
            continue        # history, not a pending ask
        canonical = normalize_website_url(url or '')
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        try:
            domain = urlparse(canonical).netloc or canonical
        except Exception:
            domain = canonical
        out['items'].append({
            'url': url, 'canonical': canonical, 'title': domain,
            'marker': status or '', 'door': 'master-table gesture',
            'path': ''})
    return out


def _banish_note_file(vault_path: str, path: str,
                      log: Optional[Callable] = None) -> str:
    """v0.58.0 — move ONE note file into ``<vault>/.trash/banished/``
    (Obsidian's hidden trash: out of the library — VaultIndex, the
    mirror, and the Website Directory never read dot-folders — but
    recoverable by hand; a mistaken burial is one file move away from
    undone). The banned-domain sweep's own mechanics, dry-run aware.
    Returns the destination path, or '' when the move failed (the
    dismissal still holds — the gate is the DB, the file is the
    furniture)."""
    log = log or (lambda *a, **k: None)
    if not vault_path or not path or not os.path.isfile(path):
        return ''
    quarantine = os.path.join(vault_path, BANISH_QUARANTINE_RELPATH)
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        dst = unique_path(os.path.join(quarantine,
                                       os.path.basename(path)))
        _dryrun.makedirs(quarantine, exist_ok=True)
        _dryrun.write_text(dst, content)
        _dryrun.remove(path)
        # honesty (the graveyard's law): the move only counts when the
        # bytes actually moved — a dry-run rehearsal leaves the note in
        # place, so it reports '' and the caller says so.
        if os.path.isfile(dst) and not os.path.exists(path):
            return dst
        return ''
    except Exception as e:
        log(f"⚠️ could not move {os.path.basename(path)} to "
            f"{BANISH_QUARANTINE_RELPATH.replace(os.sep, '/')}: {e}",
            "warning")
        return ''


def _find_note_for_url(vault_path: str, canonical: str,
                       state=None, log: Optional[Callable] = None) -> str:
    """v0.58.0 — the note FILE for one canonical URL (the table-gesture
    burial needs it: the row names the link, the note holds the file).

    The state ledger's row first — verified on disk (the app's own
    note, for THIS link — the v0.57 law: a row pointing at a stranger
    or at nothing is not a delivery); then a walk of the library
    (category folders + ``_review``) for any app-owned note whose
    ``source:`` normalizes to the canonical (the row was lost, the note
    was moved by hand). ``''`` when nothing is found — the note is
    already gone (a hand deletion, an earlier banishment) and the
    burial proceeds DB-only. Never raises."""
    log = log or (lambda *a, **k: None)
    if not canonical:
        return ''
    if state is not None:
        try:
            prior = state.processed_row(canonical)
        except Exception:
            prior = None
        if prior:
            path = str(prior.get('note_path') or '')
            if path and os.path.isfile(path):
                fm = _parse_review_frontmatter(path)
                if fm and fm.get('managed_by', '').lower() \
                        == MANAGED_BY_GITCURATOR \
                        and _same_source(fm.get('source') or '', canonical):
                    return path
    if not vault_path or not os.path.isdir(vault_path):
        return ''
    for root, dirs, files in os.walk(vault_path):
        dirs[:] = sorted(d for d in dirs
                         if d != '_inbox' and not d.startswith('.'))
        for name in sorted(files):
            if not name.lower().endswith('.md'):
                continue
            p = os.path.join(root, name)
            if not os.path.isfile(p):
                continue
            fm = _parse_review_frontmatter(p)
            if not fm or fm.get('managed_by', '').lower() \
                    != MANAGED_BY_GITCURATOR:
                continue
            if _same_source(fm.get('source') or '', canonical):
                return p
    return ''


def _banish_url(state, vault_path: str, canonical: str,
                note_path: str, marker: str, gesture: str,
                log: Optional[Callable] = None) -> Dict:
    """v0.58.0 — THE BURIAL CORE (both doors meet here: the note's own
    tag and the master table's Status cell).

    DB first — the never-fetch gate must hold even when every file
    operation below fails (the graveyard's law): the URL is dismissed
    with the banished reason (the skip gate and the log name the door),
    its retry-queue row is dropped (the re-fetch loop, closed). Then
    the furniture: the note FILE leaves the library for
    ``.trash/banished``, the processed row is forgotten (the ledger
    must not outlive the note it pointed at — the exact false-success
    shape v0.57.0 closed), and the link's half-fetched ``_review``
    items go with it. Dry-run aware; never raises. Returns
    ``{'dismissed', 'moved', 'row_forgotten', 'review_swept'}``."""
    log = log or (lambda *a, **k: None)
    report = {'dismissed': False, 'moved': '', 'row_forgotten': False,
              'review_swept': 0}
    if not canonical:
        return report
    reason = (f"{BANISH_REASON_PREFIX} — 🗑️ {gesture} "
              f"({marker or 'delete mark'}); the note left the library, "
              f"the URL is blacklisted, never fetched again (v0.58.0)")
    try:
        state.dismiss(canonical, reason)
        report['dismissed'] = True
    except Exception as e:
        log(f"⚠️ Banishment DB write skipped for {canonical}: {e}",
            "warning")
    try:
        state.resolve_retry(canonical)
    except Exception:
        pass    # the dismissal is the gate; the queue row is furniture
    if note_path and os.path.isfile(note_path):
        dst = _banish_note_file(vault_path, note_path, log=log)
        report['moved'] = dst
        if dst and not os.path.exists(note_path):
            try:
                state.forget_row(canonical)
                report['row_forgotten'] = True
            except Exception as e:
                log(f"⚠️ Could not forget the processed row for "
                    f"{canonical}: {e}", "warning")
    else:
        # the note is already gone (a hand deletion, an earlier
        # banishment) — a ledger row pointing at nothing is exactly the
        # false-success shape the owner reported; forget it.
        try:
            state.forget_row(canonical)
            report['row_forgotten'] = True
        except Exception:
            pass
    try:
        report['review_swept'] = sweep_review_leftovers(
            vault_path, canonical, log=log)
    except Exception as e:
        log(f"⚠️ Banishment _review sweep skipped for {canonical}: {e}",
            "warning")
    return report


def banish_marked_notes(state, vault_path: str,
                        log: Optional[Callable] = None) -> Dict:
    """v0.58.0 — THE BANISHMENT, the note-tag door: enforce the owner's
    delete verdicts written on the notes THEMSELVES.

    Every APP-OWNED note in the library whose tags carry 🗑️ (or
    ``delete`` / ``banish`` / ``blacklist`` / ``purge``), or whose
    frontmatter carries a true ``decommission:`` / ``banish:`` /
    ``blacklist:`` key, or which the owner MOVED into the root ``Trash``
    folder (v0.62.0 — the trash door: the move is the verdict), leaves
    the vault for ``.trash/banished`` and its URL is blacklisted —
    dismissed with the banished reason, so the never-fetch gate holds
    for every future arrival (a re-paste, a re-arm, a fresh batch
    months later: skipped, 🗑️ in the log). The master table gains the
    record row (``🗑️ banished — confirmed <date>``, ♻️ revivable like
    any burial — the Source column names the door that fired).
    A hand-written note carrying the mark — or resting in the Trash
    folder — is KEPT with a warning (the sacred law — the app never
    deletes what it did not write; the owner deletes his own notes by
    hand in Obsidian, the natural gesture).

    Idempotent: a banished note rests in ``.trash`` (the scan never
    enters dot-folders) and the table row is never duplicated (the
    writer's law). Dry-run aware. Tolerated everywhere (bookkeeping
    never kills a batch). Returns ``{'marked', 'banished',
    'notes_moved', 'kept_handwritten', 'review_swept', 'rows_written',
    'urls'}`` — ``urls`` is the canonical list the run's 🗑️ tally
    counts (v0.59.0)."""
    log = log or (lambda *a, **k: None)
    report = {'marked': 0, 'banished': 0, 'notes_moved': 0,
              'kept_handwritten': 0, 'review_swept': 0, 'rows_written': 0,
              'urls': []}
    if not vault_path or not os.path.isdir(vault_path):
        return report
    items = scan_banished_notes(vault_path, log=log)
    if not items:
        return report            # the cheap no-op — most runs
    date_str = datetime.now().strftime('%Y-%m-%d')
    seen: set = set()
    record_urls: List[str] = []
    record_notes: Dict[str, str] = {}
    record_rows_tag: List[str] = []          # v0.62.0 — per-door rows
    record_notes_tag: Dict[str, str] = {}
    record_rows_trash: List[str] = []
    record_notes_trash: Dict[str, str] = {}
    for it in items:
        report['marked'] += 1
        via_trash = (it.get('door') == 'trash folder')
        if not it.get('app_owned'):
            report['kept_handwritten'] += 1
            if via_trash:
                log(f"✍️ kept {os.path.basename(it['path'])} — it rests in "
                    f"the Trash folder but is hand-written (yours); the "
                    f"app never deletes what it did not write — delete it "
                    f"by hand in Obsidian if you want it gone", "warning")
            else:
                log(f"✍️ kept {os.path.basename(it['path'])} — it carries "
                    f"the 🗑️ mark but is hand-written (yours); delete it "
                    f"by hand in Obsidian if you want it gone", "warning")
            continue
        canonical = normalize_website_url(it.get('url') or '')
        if not canonical or canonical in seen:
            continue            # two marked notes, one link — one burial
        seen.add(canonical)
        gesture = TRASH_GESTURE if via_trash else 'note tag'
        b = _banish_url(state, vault_path, canonical, it.get('path'),
                        it.get('marker') or '', gesture, log=log)
        report['review_swept'] += b['review_swept']
        if not b['dismissed']:
            continue            # the DB refused — the verdict is not law
        report['banished'] += 1
        record_urls.append(canonical)
        if via_trash:
            record_rows_trash.append(canonical)
            record_notes_trash[canonical] = \
                "🗑️ you moved it to the Trash folder"
        else:
            record_rows_tag.append(canonical)
            record_notes_tag[canonical] = f"🗑️ marked on the note: " \
                                          f"{it.get('marker') or 'delete'}"
        if b['moved']:
            report['notes_moved'] += 1
            if via_trash:
                log(f"🗑️ {canonical}: banished — you moved it to the "
                    f"Trash folder; it left the library for "
                    f"{BANISH_QUARANTINE_RELPATH.replace(os.sep, '/')} "
                    f"and the URL is blacklisted (never fetched again; "
                    f"♻️ revived in the master table undoes it)", "info")
            else:
                log(f"🗑️ {canonical}: banished — the note carried the "
                    f"delete mark; it left the library for "
                    f"{BANISH_QUARANTINE_RELPATH.replace(os.sep, '/')} "
                    f"and the URL is blacklisted (never fetched again; ♻️ "
                    f"revived in the master table undoes it)", "info")
        elif _dryrun.is_enabled():
            log(f"🗑️ {canonical}: banishment REHEARSED (dry-run) — the "
                f"URL is blacklisted in the shadow cache and the note's "
                f"move to "
                f"{BANISH_QUARANTINE_RELPATH.replace(os.sep, '/')}"
                f" was recorded, not performed", "info")
        else:
            log(f"🗑️ {canonical}: banished — the URL is blacklisted "
                f"(never fetched again); the note file could not be "
                f"moved, it stays where it is (an orphan now — the "
                f"next run retries the move)", "warning")
    if record_urls:
        try:
            written = 0
            if record_rows_tag:
                written += write_decommission_candidates(
                    vault_path, record_rows_tag, source='note tag',
                    notes=record_notes_tag,
                    status=f"🗑️ banished — confirmed {date_str}", log=log)
            if record_rows_trash:
                written += write_decommission_candidates(
                    vault_path, record_rows_trash, source='Trash folder',
                    notes=record_notes_trash,
                    status=f"🗑️ banished — confirmed {date_str}", log=log)
            report['rows_written'] = written
        except Exception as e:
            log(f"⚠️ Banishment record rows skipped: {e}", "warning")
        log(f"🗑️ Banishment: {report['banished']} note(s) removed from "
            f"the library and blacklisted — "
            f"{report['kept_handwritten']} hand-written marked note(s) "
            f"kept (yours); the master table holds the record rows "
            f"(♻️ revivable)", "info")
    elif report['kept_handwritten']:
        log(f"🗑️ Banishment: {report['kept_handwritten']} marked note(s) "
            f"are hand-written and were KEPT (yours) — nothing "
            f"banished", "info")
    report['urls'] = list(record_urls)
    return report


def banish_twins_in_other_vault(other_vault: str, canonicals: List[str],
                                log: Optional[Callable] = None) -> int:
    """v0.60.0 — the "both vaults" promise of the confirmed deletion.

    The owner's contract (session, verbatim): "then they will get
    deleted from both vaults and never be fetched again. also deleted
    from github." A banished link's note lives in the Websites vault —
    but a twin may rest in the OTHER vault (a hand move, a mirror
    copy). After the owner CONFIRMS the deletion, this sweep walks the
    other vault (the config ``vault_path`` — the GitHub-projects vault)
    for APP-OWNED notes whose ``source:`` normalizes into
    ``canonicals`` and moves each to that vault's own
    ``.trash/banished`` (the sacred law holds: hand-written notes are
    never touched — the walk only ever moves notes the app wrote).
    GitHub itself needs no sweep: VaultSeal pushes the vault with
    ``git add -A`` and ``.trash/`` is gitignored — the next seal's
    commit takes the deletion to the backup repo on its own. Dry-run
    aware; never raises; returns the number of twin notes moved."""
    log = log or (lambda *a, **k: None)
    moved = 0
    if not other_vault or not os.path.isdir(other_vault) or not canonicals:
        return moved
    wanted = {normalize_website_url(u or '') for u in canonicals}
    wanted.discard('')
    if not wanted:
        return moved
    for root, dirs, files in os.walk(other_vault):
        dirs[:] = sorted(d for d in dirs
                         if d != '_inbox' and not d.startswith('.'))
        for name in sorted(files):
            if not name.lower().endswith('.md'):
                continue
            p = os.path.join(root, name)
            if not os.path.isfile(p):
                continue
            try:
                fm = _parse_review_frontmatter(p)
            except Exception:
                fm = None
            if not fm or fm.get('managed_by', '').lower() \
                    != MANAGED_BY_GITCURATOR:
                continue
            if normalize_website_url(fm.get('source') or '') not in wanted:
                continue
            dst = _banish_note_file(other_vault, p, log=log)
            if dst:
                moved += 1
                log(f"🗑️ {fm.get('source')}: twin note removed from the "
                    f"other vault too ({os.path.basename(p)} → "
                    f"{BANISH_QUARANTINE_RELPATH.replace(os.sep, '/')}) "
                    f"— the confirmed deletion reaches both vaults",
                    "info")
    return moved


def consume_decommission_table(state, vault_path: str,
                               log: Optional[Callable] = None,
                               apply_banish: bool = True) -> Dict:
    """v0.44.0/v0.47.0 — enforce the owner's master-table decisions.

    v0.58.0 — pass 0 is THE BANISHMENT: every 🗑-marked row (🗑️ /
    delete / banish / blacklist / purge — :func:`_status_is_banished`)
    gets the STRONGEST retirement in the grammar. Dead (🪦) and
    reviewed (✅) dismiss the URL and sweep ``_review`` placeholders;
    banished ALSO removes the note itself from the library (found on
    disk via the ledger row or a vault walk — a proper, categorized
    note in a category folder is exactly the case the owner named:
    "Website X is currently stored under a folder in my obsidian …
    it's not good or useful anymore") and forgets the processed row
    so the ledger never points at a note the vault no longer carries.
    The URL is dismissed with the banished reason (the never-fetch
    gate — even a future paste is skipped), and the row is stamped
    ``🗑️ banished — confirmed <date>``. A banish row whose note is
    ALREADY gone (the owner deleted the file by hand and marked the
    row — his exact workaround for the re-fetch loop he reported) is
    dismissed DB-only, the loop closed. When a cell says both dead and
    delete the banishment wins (the removal intent is the stronger
    sentence); ♻️ revived outranks everything (it is the undo door).

    For every dead-marked row (``_status_is_dead``): the URL is
    ``dismiss()``ed (the never-fetch gate process_link already honors —
    even a future paste of the same link is skipped), its retry-queue
    row is dropped (the re-fetch loop the owner reported, closed), its
    FAILED ``_review`` placeholder FILES are swept (the same re-read
    app-owned-failed ownership check the v0.42.0 cleanup uses — a
    hand-edited placeholder is the owner's, kept), the failed processed
    row is forgotten, and the row's Status is rewritten to
    ``🪦 confirmed — decommissioned <date>`` so the table itself shows
    the burial.

    v0.47.0 — every ✅-reviewed row (``_status_is_reviewed``, the
    owner's tick gesture) gets the SAME retirement: dismissed with the
    reviewed reason, retry row dropped, placeholder swept, processed
    row forgotten, Status rewritten to ``✅ confirmed — reviewed
    <date>``. The only difference from a burial is the wording — both
    are the never-fetch-again gate (that is what the owner asked for:
    "tick emoji as reviewed so it never fetches again").

    v0.48.0 — every 🖐-hand row (``_hand_delivery._status_is_hand``,
    the fourth door's gesture) is QUEUED for hand-delivery — never
    retired: the link joins queue.json, the row is stamped
    ``🖐 hand — queued <date>``, and it keeps waiting until the owner
    delivers the page. Idempotent (a queued-stamped row never
    re-stamps); death and reviewed still win when a hand-edited cell
    says both.

    v0.52.0 — THE ICON COLUMN SPEAKS here too: the gesture is read
    from the # cell OR the Status cell (the parse's combined text),
    a table the owner trimmed to four columns is restored to the
    full seven BEFORE any stamp is written
    (:func:`_ensure_table_shape` — a stamp into a trimmed table
    would be invisible in the rendered note), and the hand log line
    says the fifth door's truth: the app itself scrapes hand rows in
    the owner's Chrome now (the end-of-run pass and the caught-up
    check both deliver them automatically).

    v0.49.0 — the sweep (pass 3) takes ANY app-owned ``_review``
    placeholder, not just fetch-failed ones: a ✅/🪦 on a
    low-confidence or archived-rescue row sweeps that note too (the
    row is the record).

    For every ♻️-revived row: the dismissal is removed — the link is
    fetched like new the next time it appears.

    Idempotent — a confirmed row still carries its marker, so a second
    run re-dismisses harmlessly (INSERT OR REPLACE) while the Status
    rewrite is byte-stable ('confirmed' rows are skipped). Dead beats
    reviewed when a hand-edited cell says both (the burial is the
    stronger sentence). Tolerated everywhere (the table is bookkeeping,
    never a batch killer). Returns ``{'dead', 'reviewed', 'banished',
    'revived', 'handed', 'placeholders_swept', 'notes_moved',
    'rows_confirmed', 'banished_urls'}`` — ``banished_urls`` is the
    canonical list the run's 🗑️ tally counts (v0.59.0).

    v0.60.0 — ``apply_banish=False`` is THE CONFIRMATION GATE's hold:
    the 🗑 rows are counted into ``pending_banish`` and LEFT ALONE (the
    owner has not confirmed yet — the marks stay, the notes stay),
    while the dead/reviewed/hand passes run unchanged (those verdicts
    were already the owner's explicit hand; only the DESTRUCTIVE door
    waits for the ask).

    v0.60.2 — the hold holds in the TALLY too: ``banished_urls`` is
    the list of verdicts WRITTEN this run (an ``apply_banish=False``
    call answers ``[]`` — found in a REAL channel-verification run
    whose timeout tally claimed removals that never happened while
    every note sat untouched in the vault)."""
    log = log or (lambda *a, **k: None)
    report = {'dead': 0, 'reviewed': 0, 'banished': 0, 'revived': 0,
              'handed': 0, 'placeholders_swept': 0, 'notes_moved': 0,
              'rows_confirmed': 0, 'banished_urls': [],
              'pending_banish': 0}
    path = decommission_table_path(vault_path)
    if not vault_path or not os.path.isfile(path):
        return report
    # v0.52.0 — a trimmed table gets its full grammar back first, so
    # every stamp below lands in a cell the rendered note can show
    try:
        _ensure_table_shape(path, log=log)
    except Exception:
        pass    # a shape the app cannot restore must not kill the pass
    rows = _parse_decommission_rows(path)
    if not rows:
        return report
    lines = None
    date_str = datetime.now().strftime('%Y-%m-%d')
    retired_canonicals: List[str] = []

    # ---- pass 0 (v0.58.0): the banishments (🗑️ — the strongest
    # retirement; it runs FIRST so the removal intent wins over a
    # dead/reviewed stamp on the same hand-edited row).
    # v0.60.0 — THE GATE: with apply_banish=False the 🗑 rows are only
    # COUNTED (pending_banish) — the owner has not confirmed, so the
    # marks and the notes stay exactly as they are. -----------------------
    banished_canonicals: List[str] = []
    # v0.60.2 — the REAL-RUN honesty law: ``banished_urls`` is the list
    # of verdicts WRITTEN this run (what actually left), never the mere
    # presence of a 🗑 status. The live channel verification caught a
    # timeout run whose table carried two banished-status rows (one
    # pending gesture, one long-confirmed history) claiming "2
    # website(s) removed this run" while the vault still held every
    # note — the gate's hold must hold in the TALLY too.
    banished_consumed: List[str] = []
    for row in rows:
        if not _status_is_banished(row['status']):
            continue
        canonical = normalize_website_url(row['url'])
        if not canonical or canonical in banished_canonicals:
            continue
        banished_canonicals.append(canonical)
        if not apply_banish:
            report['pending_banish'] += 1
            continue    # the gate's hold — nothing consumed, nothing moved
        report['banished'] += 1
        banished_consumed.append(canonical)
        note_path = _find_note_for_url(vault_path, canonical, state=state,
                                       log=log)
        b = _banish_url(state, vault_path, canonical, note_path,
                        row['status'] or '', 'master-table gesture',
                        log=log)
        if b.get('moved'):
            report['notes_moved'] += 1
        # Confirm the row (byte-stable rewrite — the marker stays).
        parts = row['raw'].split('|')
        if len(parts) >= 8 and 'confirmed' not in row['status'].lower():
            parts[6] = f" 🗑️ banished — confirmed {date_str} "
            if lines is None:
                try:
                    with open(path, 'r', encoding='utf-8',
                              errors='replace') as f:
                        lines = f.read().splitlines()
                except Exception as e:
                    log(f"⚠️ Could not re-read the master table: {e}",
                        "warning")
                    lines = []
            if lines and row['line'] < len(lines):
                lines[row['line']] = '|'.join(parts)
                report['rows_confirmed'] += 1

    # ---- pass 1: the retirements (DB first — the gate must hold even
    # if a file sweep fails below) ---------------------------------------
    for row in rows:
        if _status_is_banished(row['status']):
            continue    # v0.58.0 — the banishment owns this row (the
            # note LEFT; a dead/reviewed stamp would only dismiss it)
        dead = _status_is_dead(row['status'])
        reviewed = (not dead) and _status_is_reviewed(row['status'])
        if not (dead or reviewed):
            continue
        canonical = normalize_website_url(row['url'])
        if not canonical:
            continue
        retired_canonicals.append(canonical)
        if dead:
            report['dead'] += 1
            reason = ('decommissioned by owner — graveyard table '
                      '(v0.44.0)')
            stamp = f" 🪦 confirmed — decommissioned {date_str} "
        else:
            report['reviewed'] += 1
            reason = ('retired by owner (reviewed ✅) — the master '
                      'table (v0.47.0); never fetched again')
            stamp = f" ✅ confirmed — reviewed {date_str} "
        try:
            state.dismiss(canonical, reason)
            state.resolve_retry(canonical)
            state.forget_failed_row(canonical)
        except Exception as e:
            log(f"⚠️ Master-table DB write skipped for {row['url']}: {e}",
                "warning")
        # Confirm the row (byte-stable rewrite — the marker stays).
        parts = row['raw'].split('|')
        if len(parts) >= 8 and 'confirmed' not in row['status'].lower():
            parts[6] = stamp
            if lines is None:
                try:
                    with open(path, 'r', encoding='utf-8',
                              errors='replace') as f:
                        lines = f.read().splitlines()
                except Exception as e:
                    log(f"⚠️ Could not re-read the master table: {e}",
                        "warning")
                    lines = []
            if lines and row['line'] < len(lines):
                lines[row['line']] = '|'.join(parts)
                report['rows_confirmed'] += 1

    # ---- pass 1.5 (v0.48.0): the fourth door's gestures -----------------
    # A 🖐 hand Status is NOT a retirement: the link is queued for
    # hand-delivery (queue.json + README in the hand-delivered folder),
    # the row is stamped, and the link keeps waiting. Death, reviewed
    # and revived all outrank it; a queued-stamped row never re-stamps
    # (idempotency — the second batch sees 'queued' and moves on).
    hand_urls: List[str] = []
    for row in rows:
        s = row['status']
        if (_status_is_dead(s) or _status_is_revived(s)
                or _status_is_reviewed(s)):
            continue
        if not _hand_delivery._status_is_hand(s):
            continue
        if 'queued' in s.lower():
            continue  # already stamped by a previous pass
        canonical = normalize_website_url(row['url'])
        if not canonical:
            continue
        hand_urls.append(canonical)
        report['handed'] += 1
        parts = row['raw'].split('|')
        if len(parts) >= 8:
            parts[6] = f" 🖐 hand — queued {date_str} "
            if lines is None:
                try:
                    with open(path, 'r', encoding='utf-8',
                              errors='replace') as f:
                        lines = f.read().splitlines()
                except Exception as e:
                    log(f"⚠️ Could not re-read the master table: {e}",
                        "warning")
                    lines = []
            if lines and row['line'] < len(lines):
                lines[row['line']] = '|'.join(parts)
    if hand_urls:
        try:
            _hand_delivery.enqueue_hand_delivery(
                vault_path, hand_urls, log=log)
        except Exception as e:
            log(f"⚠️ Hand-delivery queue write skipped: {e}", "warning")
        else:
            # v0.52.0 — the fifth door's truth: the app scrapes these
            # itself now (the end-of-run pass opens the owner's Chrome,
            # one tab per link, and takes the pages) — Ctrl+S stays as
            # the owner's own option, not the obligation
            log(f"🖐 {report['handed']} hand row(s) queued — your real "
                f"Chrome opens for them at the end of this run (one "
                f"tab per link, the live pages taken as real fetches; "
                f"saving a page into the hand-delivered folder yourself "
                f"still works too)", "info")

    # ---- pass 2: the revivals -------------------------------------------
    for row in rows:
        if not _status_is_revived(row['status']):
            continue
        canonical = normalize_website_url(row['url'])
        if not canonical:
            continue
        report['revived'] += 1
        try:
            _revive_parts = []
            if state.undismiss(canonical):
                _revive_parts.append("the dismissal is gone")
            # v0.63.2 — the settled ledger honors the ♻️ the same way:
            # a revived link is fetched like new again — the owner's
            # own door through THE SETTLEMENT (the twin of the
            # graveyard's undismiss, and the ONLY machine-opened one).
            if state.unsettle(canonical):
                _revive_parts.append("the settlement is gone")
            if _revive_parts:
                log(f"♻️ {row['url']}: revived — "
                    f"{' and '.join(_revive_parts)}, it will be fetched "
                    f"like new", "info")
        except Exception as e:
            log(f"⚠️ Graveyard revival skipped for {row['url']}: {e}",
                "warning")

    # ---- pass 3: sweep the retired links' placeholder FILES --------------
    # Walk _review like the backlog scan does — this catches both the
    # tracked placeholder (processed row knew it) and a LOST-row one.
    # v0.47.0 — a ✅-reviewed link's placeholder is swept too (the
    # owner handled this link's fate; the table row is the record).
    # v0.49.0 — ANY app-owned placeholder, not just fetch-failed ones:
    # a low-confidence classification note or an archived rescue
    # waits in _review for the owner's move, and the row's ✅/🪦 IS
    # that move — the note's job is done, the row is the record.
    # managed_by stays the ownership proof: a note without it is a
    # human's, kept whatever its status.
    if retired_canonicals:
        retired_set = set(retired_canonicals)
        review_dir = os.path.join(vault_path, REVIEW_FOLDER)
        try:
            names = sorted(os.listdir(review_dir))
        except Exception:
            names = []
        for name in names:
            if not name.lower().endswith('.md') or name == DECOMMISSION_TABLE:
                continue
            p = os.path.join(review_dir, name)
            if not os.path.isfile(p):
                continue
            fm = _parse_review_frontmatter(p)
            if not fm or fm.get('managed_by', '').lower() \
                    != MANAGED_BY_GITCURATOR:
                continue  # a human's note — never our call
            src = normalize_website_url((fm.get('source') or '').strip())
            if src not in retired_set:
                continue
            try:
                _dryrun.remove(p)
                # dry-run records the sweep without deleting — the count
                # stays honest (a rehearsed burial sweeps nothing):
                if not os.path.exists(p):
                    report['placeholders_swept'] += 1
            except Exception as e:
                log(f"⚠️ Could not sweep placeholder {name}: {e}",
                    "warning")

    # ---- pass 4: write the confirmed rows back ---------------------------
    if lines is not None:
        now = datetime.now().strftime('%Y-%m-%d %H:%M')
        lines = [f"> Last updated: {now}"
                 if line.startswith('> Last updated:') else line
                 for line in lines]
        try:
            atomic_write_text(path,
                              '\n'.join(lines).rstrip('\n') + '\n')
        except Exception as e:
            log(f"⚠️ Could not write the master table: {e}",
                "warning")

    if report['banished']:
        log(f"🗑️ Master table: {report['banished']} link(s) banished — "
            f"{report['notes_moved']} note(s) removed from the library "
            f"(.trash/banished), URLs blacklisted, never fetched again "
            f"(♻️ revived undoes any)", "info")
    if report['dead']:
        log(f"🪦 Master table: {report['dead']} link(s) decommissioned — "
            f"dismissed, {report['placeholders_swept']} placeholder(s) "
            f"swept, never fetched again", "info")
    if report['reviewed']:
        log(f"✅ Master table: {report['reviewed']} link(s) retired as "
            f"reviewed — dismissed, never fetched again (♻️ revived "
            f"brings any back)", "info")
    # v0.60.2 — the honesty law: only the verdicts WRITTEN this run
    # ride the report's list (an apply_banish=False hold answers [];
    # a confirmed history row re-consumed idempotently still counts —
    # the same set the auto/confirmed path always carried).
    report['banished_urls'] = list(banished_consumed)
    return report


def _status_is_terminal(status: str) -> bool:
    """v0.60.2 — a master-table row whose story is WRITTEN.

    The owner's ask (session, verbatim): "Prune the decommissioned
    table: remove links that reached a terminal verdict (stored
    properly / blocked / banished) — keep only pending ones. I don't
    need that old long table." A row is terminal when its verdict is
    already enforced and its record lives elsewhere (the state DB's
    dismissal row + .trash/banished + the run logs): every CONFIRMED
    stamp (🗑️ banished — / 🪦 confirmed — decommissioned / ✅
    confirmed — reviewed), the harvest's final green verdict
    (✅ hand-delivered — fetched), the stored-properly stamp (📁
    stored — the note is in the vault), and the fetcher's own
    auto-verdict rows (🪦 auto — dead/paywalled/refused: the
    "blocked" class). A PENDING gesture never matches — a fresh 🪦
    dead or 🗑️ banish mark waits for its consume; a ♻️ revived row
    stays while its revival is in flight (the undo door is never
    furniture)."""
    s = (status or '').strip().lower()
    if not s:
        return False
    if _status_is_revived(s):
        return False            # the undo door stays visible, in flight
    if 'confirmed' in s:
        return True             # every consume/harvest confirm stamp
    if 'hand-delivered' in s:
        return True             # the harvest's final green verdict
    if 'stored' in s:
        return True             # 📁 stored — the note is in the vault
    if 'auto —' in s:
        return True             # the fetcher's own verdict rows
    return False


def prune_decommission_table(vault_path: str,
                             log: Optional[Callable] = None) -> Dict:
    """v0.60.2 — the table keeps only what still needs the owner.

    Every terminal row (:func:`_status_is_terminal` — verdicts already
    written and enforced) LEAVES the table; the pending ones (waiting
    " - " / unreviewed rows, fresh owner gestures not yet consumed,
    🖐 hand-queued rows, ♻️ revivals in flight) stay. The verdicts'
    records live on where they always did — the state DB's dismissal
    rows, .trash/banished, the run logs — and the REVIVE door stays
    open for every one of them: add a row with ♻️ and the URL (any
    shape the parse reads — ``| ♻️ | | https://the-link | | | | |``)
    and the next run's consume un-retires the link (the legend at the
    top of the table says so). The header and its legend are never
    touched; a table whose every row left keeps its header (the
    legend is the revive-by-URL instruction). Pure file rewrite,
    atomic + dry-run aware; never raises. Returns
    ``{'pruned', 'kept', 'pruned_urls'}``."""
    log = log or (lambda *a, **k: None)
    out: Dict = {'pruned': 0, 'kept': 0, 'pruned_urls': []}
    path = decommission_table_path(vault_path)
    if not vault_path or not os.path.isfile(path):
        return out
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.read().splitlines()
    except Exception as e:
        log(f"⚠️ Master-table prune skipped — the table could not be "
            f"read: {e}", "warning")
        return out
    rows = _parse_decommission_rows(path)
    if not rows:
        return out            # nothing data-shaped — the legend stands
    drop_lines: set = set()
    for row in rows:
        if _status_is_terminal(row['status'] or ''):
            drop_lines.add(row['line'])
            out['pruned'] += 1
            out['pruned_urls'].append(
                normalize_website_url(row['url']) or row['url'])
        else:
            out['kept'] += 1
    if not out['pruned']:
        return out            # a pending-only table is already the law
    kept_lines = [ln for i, ln in enumerate(lines)
                  if i not in drop_lines]
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    kept_lines = [f"> Auto-updated after every batch. Last updated: {now}"
                  if ln.startswith('> Auto-updated after every batch.')
                  else ln
                  for ln in kept_lines]
    try:
        atomic_write_text(
            path, '\n'.join(kept_lines).rstrip('\n') + '\n')
    except Exception as e:
        log(f"⚠️ Master-table prune could not be written: {e}",
            "warning")
        return {'pruned': 0, 'kept': out['kept'] + out['pruned'],
                'pruned_urls': []}
    if out['pruned']:
        log(f"📋 Master table pruned: {out['pruned']} terminal "
            f"row(s) left the table ({out['kept']} pending row(s) "
            f"stay) — their verdicts live in the state DB and "
            f".trash/banished; to revive any of them, add a row with "
            f"♻️ and the URL (the legend above shows the shape)", "info")
    return out


# ===========================================================================
# v0.47.0 — the master table: the ledger the owner was promised
# ===========================================================================

def refresh_master_table(state, vault_path: str,
                         log: Optional[Callable] = None) -> Dict:
    """v0.47.0 — keep the master note honest after every batch.

    The owner's report: "I also didn't get that table yet" — v0.44.0
    only wrote ``_review/DECOMMISSIONED.md`` from the manual picker
    action, so a vault whose owner never opened More ▸ Decommission
    never saw the table at all. Now every batch refreshes it:

    * every WAITING failure (the whole retry queue — attempts, last
      error) and every scanned ``_review`` placeholder whose state row
      was lost, as fresh 'unreviewed' rows with the last error in the
      Notes column.

    v0.60.2 — THE COMPACT TABLE (the owner's ask: "Prune the
    decommissioned table: remove links that reached a terminal
    verdict (stored properly / blocked / banished) — keep only
    pending ones. I don't need that old long table"): terminal rows
    leave at the end of every refresh
    (:func:`prune_decommission_table` — confirmed stamps, stored
    stamps, hand-delivered verdicts, auto-verdict rows), the
    auto-verdict population is no longer written (the state DB and
    the logs are their record), and every retired link stays
    revivable BY URL: a row with ♻️ and the URL — the legend at the
    top of the table shows the shape.

    v0.49.0 — the third population, the owner's report: "it currently
    only adds links like before in _review" — the links that wait for
    HUMAN eyes, not retries (low classification confidence, analysis
    failures, archived rescues) landed as notes but never as ROWS, so
    the folder filled with links nobody could retire from a table.
    Every app-owned ``_review`` note (any fetch_status) is a row now:
    source 'review', Status 'unreviewed', the note's own reason in the
    Notes column — the same Status grammar answers them (🪦 dead /
    ✅ reviewed retire AND sweep the note; ♻️ revived re-fetches).

    Existing rows are never duplicated or clobbered (the writer's law);
    a vault with nothing waiting and nothing retired stays table-less
    (a cheap no-op). Tolerated everywhere — the master table is
    bookkeeping, never a batch killer. Returns
    ``{'waiting', 'retired', 'written'}``."""
    log = log or (lambda *a, **k: None)
    report = {'waiting': 0, 'retired': 0, 'written': 0}
    if not vault_path:
        return report
    # v0.52.0 — a trimmed table gets its full grammar back first (the
    # stamp this pass writes must land in a cell the note can render)
    try:
        _ensure_table_shape(decommission_table_path(vault_path),
                            log=log)
    except Exception:
        pass        # the shape never blocks the refresh
    try:
        waiting_urls: List[str] = []
        waiting_notes: Dict[str, str] = {}
        dismissed_cache: Dict[str, bool] = {}

        def _is_retired(u: str) -> bool:
            if u not in dismissed_cache:
                try:
                    dismissed_cache[u] = bool(state.is_dismissed(u))
                except Exception:
                    dismissed_cache[u] = False
            return dismissed_cache[u]

        for row in state.all_retry_rows():
            u = row.get('url') or ''
            if not u or _is_retired(u):
                continue  # retired — its row comes below, not here
            waiting_urls.append(u)
            waiting_notes[u] = str(row.get('last_error') or '')
            report['waiting'] += 1
        for it in scan_review_backlog(vault_path, is_dismissed=_is_retired):
            u = it.get('url') or ''
            if not u or u in waiting_notes:
                continue
            waiting_urls.append(u)
            waiting_notes[u] = ('placeholder in _review (state row was '
                                'lost)')
            report['waiting'] += 1
        # v0.49.0 — the human-eye classes: every OTHER app-owned note in
        # _review (low classification confidence, analysis failures,
        # archived rescues). Fetch-failed placeholders are the backlog
        # scan's jurisdiction (above); a RETIRED link's row already
        # exists (auto-verdict or the owner's own emoji).
        for it in scan_review_notes(vault_path):
            u = it.get('url') or ''
            if not u or u in waiting_notes:
                continue
            if (it.get('fetch_status') or '').strip().lower() == 'failed':
                continue  # a failed placeholder is a retry row or a
                # lost-row backlog item — both counted above
            cu = normalize_website_url(u)
            if cu in waiting_notes:
                continue  # canonical twin already waiting
            if _is_retired(cu):
                continue
            reason = _review_note_reason(it.get('path') or '')
            fs = (it.get('fetch_status') or '').strip() or 'review'
            waiting_urls.append(cu)
            waiting_notes[cu] = (f"in _review ({fs}): "
                                 + (reason or
                                    'waiting for the owner\u2019s move'))
            report['waiting'] += 1

        retired_urls: List[str] = []
        retired_notes: Dict[str, str] = {}
        for row in state.dismissed_rows('auto-verdict:'):
            u = row.get('url') or ''
            if not u:
                continue
            verdict = str(row.get('reason') or '')[len('auto-verdict:'):].strip()
            retired_urls.append(u)
            retired_notes[u] = verdict
            report['retired'] += 1

        # v0.60.2 — THE COMPACT TABLE: the auto-verdict rows are no
        # longer WRITTEN (the owner's ask: "I don't need that old long
        # table"). The verdict's record lives in the state DB and the
        # run logs; the table keeps only what still needs the owner
        # (the waiting rows below); a retired link comes back through
        # the revive-by-URL row (the legend's instruction). Writing
        # them here would also churn: the prune below removes them the
        # same pass, and the next refresh (its table-dedupe now blind)
        # would re-write every one — write-then-delete forever.
        n1 = write_decommission_candidates(
            vault_path, waiting_urls, source='waiting',
            log=None, notes=waiting_notes) if waiting_urls else 0
        report['written'] = n1
        # v0.51.0 — the truth stamp: rows still marked " - " whose
        # retries have since SUCCEEDED are stamped '📁 stored' — the
        # waiting set stays honest, so the caught-up check (and the
        # owner's eyes) never retry links that are already in the vault.
        # (Report shape unchanged — the stamp is logged, not counted.)
        try:
            stamp_stored_rows(state, vault_path, log=log)
        except Exception as e:  # bookkeeping never kills a batch
            log(f"⚠️ Master-table stored-stamp skipped: {e}", "warning")
        # v0.56.0 — THE HAND'S HARVEST: 🖐 rows whose links are already
        # stored retire to the green checkbox (✅ hand-delivered) and
        # their half-fetched _review items are swept — the owner's law,
        # applied to the backlog at every batch's end. (Report shape
        # unchanged — the harvest is logged, not counted.)
        try:
            harvest_hand_rows(state, vault_path, log=log)
        except Exception as e:  # bookkeeping never kills a batch
            log(f"⚠️ Hand-harvest pass skipped: {e}", "warning")
        # v0.60.2 — THE PRUNE: terminal rows leave the table (the
        # owner's compact-list ask) — the consume's confirm stamps, the
        # stored stamps, the harvest's green verdicts, and any leftover
        # auto-verdict rows from the v0.47–v0.60 era. The pending rows
        # stay; the header and its revive-by-URL legend stay.
        try:
            prune_decommission_table(vault_path, log=log)
        except Exception as e:  # bookkeeping never kills a batch
            log(f"⚠️ Master-table prune skipped: {e}", "warning")
        if report['written']:
            log(f"📋 Master table refreshed: {report['waiting']} waiting "
                f"(fetch failures + links parked in _review), "
                f"{report['retired']} auto-retired link(s) — "
                f"{report['written']} new row(s) in "
                f"{DECOMMISSION_TABLE} (🪦 dead / ✅ reviewed retire; ♻️ "
                f"revived re-fetches; 🖐 hand queues for the fourth "
                f"door — your real Chrome; terminal rows leave the "
                f"table — the state DB and .trash keep their records)",
                "info")
    except Exception as e:  # bookkeeping never kills a batch
        log(f"⚠️ Master table refresh skipped: {e}", "warning")
    return report


# ===========================================================================
# The pipeline
# ===========================================================================

class WebsitePipeline:
    """Runs the per-link flow. One instance per batch.

    ``llm_call(messages, task=None) -> str`` is injected (the worker
    routes to Ollama or an OpenAI-compatible endpoint exactly like the
    GitHub pipeline; the golden runner injects a fake). ``task`` is
    'classify' for the w01/w02 passes and 'analyze' for w03 — the router
    uses it for the per-task model overrides (models.classify /
    models.analyze, v0.13.0 Phase 4). ``vault_index_has(url) -> bool |
    path`` probes the websites VaultIndex (the ground-truth dedupe
    layer).
    """

    def __init__(self, config: dict, llm_call: Callable,
                 vault_index_has: Callable,
                 state: WebsiteStateDB,
                 log: Optional[Callable] = None,
                 taxonomy: Optional[Taxonomy] = None,
                 note_state_db=None,
                 rate_limiter: Optional[_web_fetch.DomainRateLimiter] = None,
                 fetch_fn=None,
                 banish_confirm: Optional[Callable] = None,
                 site_note_for: Optional[Callable] = None):
        from gitcurator.constants import resolve_taxonomy_path
        self.config = config or {}
        self.llm_call = llm_call
        self.vault_index_has = vault_index_has
        self.state = state
        self.log = log or (lambda *a, **k: None)
        self.note_state_db = note_state_db
        # v0.64.0 — ONE NOTE PER SITE's probe: ``site_note_for(url) ->
        # path | None`` answers "does the vault already hold the SITE's
        # note for this link's host?" (the websites VaultIndex builds
        # the site map on its walk). None (the default — every existing
        # test construction) leaves the law off: the pipeline behaves
        # exactly as before, one note per link.
        self.site_note_for = site_note_for
        # Optional fetch injection (tests + the offline golden run stub
        # this so NO network is touched; production leaves it None).
        self.fetch_fn = fetch_fn or _web_fetch.fetch_url
        # v0.19.0 — Web fetches through the proxy (Settings → Proxy): the
        # blocked-web fix. x.com / t.co / youtu.be connections are REFUSED
        # on the owner's direct line (poisoned DNS) while the rest of the
        # web fetches fine — so when a proxy is configured AND reachable,
        # every fetch of this batch rides it (DNS at the proxy). NEVER
        # applied to an injected fetch_fn: the golden run must stay
        # offline and tests must stay hermetic.
        self.web_proxy = None
        if fetch_fn is None:
            self.web_proxy = _web_fetch.proxy_from_config(self.config)
            if self.web_proxy is not None:
                _ok, _why = _web_fetch.web_proxy_preflight(self.web_proxy)
                if _ok:
                    self.log(
                        f"🌐 Web fetches via "
                        f"{_web_fetch.proxy_label(self.web_proxy)} proxy "
                        f"(Settings → 🌐 Proxy) — v0.43.0 both doors: the "
                        f"direct line is still tried when the proxy path "
                        f"fails or is walled (403/405/429/451)", "info")
                    _proxy = self.web_proxy

                    def _proxied_fetch(url, **kwargs):
                        kwargs.setdefault('proxy', _proxy)
                        return _web_fetch.fetch_url(url, **kwargs)

                    self.fetch_fn = _proxied_fetch
                    self._maybe_rearm_retries()
                else:
                    self.log(
                        f"⚠️ Web proxy "
                        f"{_web_fetch.proxy_label(self.web_proxy)} NOT "
                        f"reachable — fetching DIRECT. {_why} x.com / "
                        f"YouTube links will keep failing until the proxy "
                        f"client is up.", "warning")
                    self.web_proxy = None
        # v0.41.0 — an owner-set User-Agent (config.json "web_user_agent";
        # empty/missing = the default browser-grade Chrome UA). Wraps the
        # SAME production fetcher as the proxy wrap (both compose), and —
        # like the proxy wrap — NEVER touches an injected fetch_fn (the
        # golden run and tests stay hermetic).
        _custom_ua = str(self.config.get('web_user_agent') or '').strip()
        if _custom_ua and fetch_fn is None:
            _ua_base_fetch = self.fetch_fn

            def _ua_fetch(url, **kwargs):
                kwargs.setdefault('user_agent', _custom_ua)
                return _ua_base_fetch(url, **kwargs)

            self.fetch_fn = _ua_fetch
        self.taxonomy = taxonomy or load_taxonomy_from_config(self.config)
        self.vault_path = (self.config.get('website_vault_path') or '').strip()
        # v0.35.0 — the display name of the Websites vault for the
        # per-item destination logs ("[Vault Name] Item X processed and
        # stored" — the owner asked the log to show where each link goes).
        self._vault_name = os.path.basename(self.vault_path) \
            if self.vault_path else 'Websites vault'
        # v0.44.0 — the graveyard: consume the owner's burial decisions
        # from <vault>/_review/DECOMMISSIONED.md BEFORE anything fetches
        # (dead links leave the retry queue and the processed ledger, their
        # placeholders are swept, they are dismissed — the never-fetch gate
        # process_link already honors). Not gated on fetch_fn: the tests
        # (and the offline golden run) exercise it with the injected
        # fetcher; a vault without the table is a cheap no-op.
        self._graveyard_urls: set = set()
        # v0.60.0 — THE CONFIRMATION GATE: the banishment no longer
        # fires on detection alone. The run opens with the vault scan
        # (scan_pending_banishments — both doors, one deduped list),
        # the count is SPOKEN in the log and asked over the injected
        # ``banish_confirm`` channel (the Telegram round-trip the GUI
        # worker and the CLI both wire in — the owner's ask, verbatim:
        # "At the beginning of every run, system scans vault, find what
        # I've marked to delete, system detects them, show me them
        # their numbers, so I ensure that system successfully detected
        # them, I confirm deletion, then they will get deleted from
        # both vaults and never be fetched again. also deleted from
        # github"). NOTHING is removed until the owner answers:
        # confirmed -> both doors enforce (the notes leave the vault,
        # their twins leave the other vault, the URLs are blacklisted,
        # the next VaultSeal push drops them from GitHub);
        # declined / timeout -> the marks and the notes stay, the next
        # run asks again; no channel at all -> the SAFE default: defer
        # (config ``banish_confirm: false`` restores the v0.58/v0.59
        # auto-delete for owners who want the old reflex). The verdict
        # and the pending count ride ``self.banish_gate`` for the
        # run's summary.
        self.banished_urls: List[str] = []
        self.banish_gate: Dict = {'verdict': 'auto', 'pending': 0,
                                  'banished': 0}
        _banished_run: List[str] = []
        _gate_verdict = 'auto'
        _gate_pending = 0
        _gate_report = None
        if self.vault_path and os.path.isdir(self.vault_path):
            try:
                _gate_scan = scan_pending_banishments(
                    self.vault_path, log=self.log) or {}
            except Exception as e:  # bookkeeping never kills a batch
                self.log(f"⚠️ Banish-gate scan skipped: {e}", "warning")
                _gate_scan = {}
            _pending_items = list(_gate_scan.get('items') or [])
            _gate_pending = len(_pending_items)
            if _pending_items:
                # the number goes FIRST — "so I ensure that system
                # successfully detected them":
                self.log(
                    f"{BANISH_GATE_PREFIX} {_gate_pending} note(s) marked "
                    f"for deletion (🗑️ / delete / auto_delete) — asking "
                    f"you to confirm before anything is removed", "info")
                if banish_confirm is not None:
                    try:
                        _gate_res = banish_confirm(_pending_items, self.log)
                    except Exception as e:
                        self.log(
                            f"⚠️ Banish confirmation failed: {e} — nothing "
                            f"deleted, the marks stay", "warning")
                        _gate_res = None
                    if isinstance(_gate_res, dict):
                        _gate_verdict = str(
                            _gate_res.get('verdict') or 'defer')
                        _gate_report = _gate_res.get('report') or None
                    elif isinstance(_gate_res, str) and _gate_res:
                        _gate_verdict = _gate_res
                    else:
                        _gate_verdict = 'defer'
                elif str(self.config.get('banish_confirm', True)).strip() \
                        .lower() in ('false', '0', 'no', 'off'):
                    _gate_verdict = 'auto'    # the owner's explicit opt-out
                else:
                    # no channel injected and no opt-out — the SAFE
                    # default: the marks stay, the next run asks again.
                    _gate_verdict = 'defer'
                    self.log(
                        f"⚠️ {_gate_pending} marked note(s) found, but no "
                        f"confirmation channel is reachable (the Telegram "
                        f"worker is not paired/enabled) — nothing deleted; "
                        f"the marks stay, I'll ask again next run (config "
                        f"\"banish_confirm\": false restores the old "
                        f"auto-delete)", "warning")
                if _gate_verdict == 'confirmed':
                    self.log(
                        f"✅ You confirmed the deletion — the "
                        f"{_gate_pending} marked note(s) leave the vault "
                        f"now (twins in the other vault too, if any); "
                        f"GitHub drops them on the next seal", "info")
                elif _gate_verdict == 'declined':
                    self.log(
                        f"👌 You kept them — the {_gate_pending} marked "
                        f"note(s) stay in the vault; the marks stay too, "
                        f"I'll ask again next run", "info")
                elif _gate_verdict == 'timeout':
                    self.log(
                        f"⌛ No answer in time — the {_gate_pending} marked "
                        f"note(s) stay; the marks stay, I'll ask again "
                        f"next run", "info")
        # v0.44.0 — the graveyard: consume the owner's burial decisions
        # from <vault>/_review/DECOMMISSIONED.md BEFORE anything fetches
        # (dead links leave the retry queue and the processed ledger, their
        # placeholders are swept, they are dismissed — the never-fetch gate
        # process_link already honors). Not gated on fetch_fn: the tests
        # (and the offline golden run) exercise it with the injected
        # fetcher; a vault without the table is a cheap no-op.
        # v0.60.0 — the table's own 🗑 pass 0 rides the GATE: dead /
        # reviewed / hand verdicts are the owner's explicit hand and run
        # unchanged; the DESTRUCTIVE door waits for the confirmation
        # (apply_banish below — the gate's hold).
        if self.vault_path and os.path.isdir(self.vault_path):
            try:
                _consume_report = consume_decommission_table(
                    self.state, self.vault_path, log=self.log,
                    apply_banish=(_gate_verdict in ('auto', 'confirmed'))
                ) or {}
                _banished_run.extend(
                    _consume_report.get('banished_urls') or [])
                for _u, _st in scan_decommission_table(
                        self.vault_path).items():
                    if _status_is_dead(_st):
                        self._graveyard_urls.add(
                            normalize_website_url(_u))
            except Exception as e:  # bookkeeping never kills a batch
                self.log(f"⚠️ Graveyard consume skipped: {e}", "warning")
        # v0.58.0 — THE BANISHMENT: the owner's delete verdicts written
        # on the notes themselves (a 🗑️ / delete / banish / blacklist /
        # purge / auto_delete tag, or a true decommission: frontmatter
        # key) are enforced BEFORE anything fetches — the marked notes
        # leave the library for .trash/banished, their URLs are
        # blacklisted (the never-fetch gate), the ledger rows are
        # forgotten, and the master table holds the record rows (♻️
        # revivable). The table's own 🗑️ Status gesture rode the consume
        # above (pass 0). Not gated on fetch_fn: a pure file+DB pass
        # (the hermetic law — the tests exercise it with the injected
        # fetcher); a vault with no marked notes is a cheap walk.
        # v0.60.0 — THE GATE owns this door now: the pass runs only when
        # the owner confirmed (or opted into auto); a declined/timeout/
        # deferred run leaves the marked notes and their marks alone.
        if _gate_verdict in ('auto', 'confirmed') \
                and self.vault_path and os.path.isdir(self.vault_path):
            try:
                _note_report = banish_marked_notes(
                    self.state, self.vault_path, log=self.log) or {}
                _banished_run.extend(_note_report.get('urls') or [])
            except Exception as e:  # bookkeeping never kills a batch
                self.log(f"⚠️ Banishment pass skipped: {e}", "warning")
            # v0.60.0 — the "both vaults" promise: the confirmed (or
            # auto) banishment sweeps the OTHER vault (config
            # vault_path — the GitHub-projects vault) for twin notes
            # of the banished links and moves them to its own
            # .trash/banished; GitHub itself is the next seal's job
            # (git add -A + .trash/ ignored — the deletion commits
            # and pushes away).
            if _banished_run:
                _other = str(self.config.get('vault_path') or '').strip()
                if _other and os.path.isdir(_other) \
                        and os.path.realpath(_other) != os.path.realpath(
                            self.vault_path):
                    try:
                        _twins = banish_twins_in_other_vault(
                            _other, _banished_run, log=self.log)
                        if _twins:
                            self.log(
                                f"🗑️ Both vaults: {_twins} twin note(s) "
                                f"removed from the other vault; GitHub "
                                f"drops them on the next seal", "info")
                    except Exception as e:
                        self.log(f"⚠️ Twin sweep skipped: {e}", "warning")
        # v0.59.0 — THE TALLY, spoken every run (the zero run answers
        # too — "how many were wiped" is a number, and 0 is one).
        # v0.60.0 — the KEPT run answers with its reason (the gate's
        # story belongs in the same line):
        self.banished_urls = list(dict.fromkeys(_banished_run))
        self.banish_gate = {'verdict': _gate_verdict,
                            'pending': _gate_pending,
                            'banished': len(self.banished_urls)}
        if self.banished_urls:
            self.log(
                f"{BANISH_TALLY_PREFIX} {len(self.banished_urls)} "
                f"website(s) removed this run and never fetched again "
                f"— you marked them for deletion (🗑️ / delete / "
                f"auto_delete); the notes rest in "
                f"{BANISH_QUARANTINE_RELPATH.replace(os.sep, '/')}, the "
                f"URLs are blacklisted, and ♻️ revived on the record row "
                f"undoes any of them", "info")
        elif _gate_pending and _gate_verdict != 'auto':
            _why = {'declined': "you said keep",
                    'timeout': "no answer in time",
                    'defer': "no confirmation channel"}.get(
                        _gate_verdict, _gate_verdict)
            self.log(
                f"{BANISH_TALLY_PREFIX} 0 websites removed this run — "
                f"{_gate_pending} marked note(s) KEPT ({_why}; the marks "
                f"stay, I'll ask again next run)", "info")
        else:
            self.log(
                f"{BANISH_TALLY_PREFIX} 0 websites removed this run — no "
                f"🗑️ / delete / auto_delete marks in the library", "info")
        # v0.60.0 — the gate's answer to Telegram: the confirmed ask
        # gets its closing line (the count that ACTUALLY left — the
        # owner's example: "10 Websites Removed and will never fetch
        # again because you …"). Decline/timeout already edited their
        # own message at the answer; nothing to report here.
        if _gate_verdict == 'confirmed' and _gate_report is not None:
            try:
                _gate_report(len(self.banished_urls))
            except Exception as e:
                self.log(
                    f"⚠️ Banish confirmation report failed: {e}",
                    "warning")
        self.taxonomy_path = resolve_taxonomy_path(self.config)
        # v0.20.0 — blocked domains (the X fix): these links are already
        # addressed as rows in the _inbox platform tables; the pipeline
        # must never fetch, note, or retry them.
        self.blocked_domains = _links.blocked_domains_from_config(self.config)
        # v0.21.0 — self domains (the app's own bot): the bot's auth links
        # (…/auth/?token=<hex>) land in the same Telegram chat the curator
        # reads; fetching them would hit the owner's own OAuth flow and
        # store live tokens in _review notes. Same never-fetch treatment.
        self.self_domains = _links.self_domains_from_config(self.config)
        if (self.blocked_domains or self.self_domains) and fetch_fn is None:
            # Production only (an injected fetch_fn = the hermetic golden
            # run / tests). Purge queued retries + _review placeholders
            # ONCE so previously-queued blocked links stop coming back.
            self._enforce_blocked_domains()
            # v0.28.0 — THE LAW, disk edition: any app-owned note whose
            # source is on a banned domain (the legacy x_com_i_status_*
            # _review pile, notes written by an older app, a since-banned
            # domain) leaves the library for .trash/banned-domains. Runs
            # on every batch; idempotent (the first run cleans history,
            # later runs are no-ops).
            if self.vault_path and os.path.isdir(self.vault_path):
                try:
                    from gitcurator.core import \
                        website_directory as _webdir
                    _webdir.sweep_banned_notes(
                        self.vault_path, self.blocked_domains,
                        log=self.log)
                except Exception as e:
                    self.log(f"⚠️ Banned-domain sweep skipped: {e}",
                             "warning")
        # Fetch politeness knobs are config-overridable (tests use small
        # timeouts; the owner can raise them for slow connections).
        self.fetch_timeout_s = float(
            self.config.get('web_fetch_timeout_s', FETCH_TIMEOUT_S)
            or FETCH_TIMEOUT_S)
        self.fetch_max_bytes = int(
            self.config.get('web_fetch_max_bytes', FETCH_MAX_BYTES)
            or FETCH_MAX_BYTES)
        # v0.45.0 — THE THIRD DOOR: when both stdlib doors are walled
        # with a refusal-family answer (the Cloudflare-challenge class),
        # curl_cffi re-asks the URL with Chrome's own TLS/HTTP2
        # handshake — the one presentation urllib cannot make. ON by
        # default when the library is importable; config
        # "web_impersonate_fallback": false opts out. The flag rides
        # every fetch call (the injected test fetchers take **kwargs,
        # so they never break).
        self.impersonate_fallback = self.config.get(
            'web_impersonate_fallback') is not False
        # v0.47.0 — THE DOOR THAT SHIPS ITSELF: when the third door is
        # wanted but curl_cffi is missing, ONE pip install runs through
        # this interpreter and arms it (the owner's five "a third door
        # exists: pip install curl_cffi" lines were a missing LIBRARY,
        # not a missing feature — his install predates requirements'
        # entry). Config "web_impersonate_autopip": false opts out.
        self.impersonate_autopip = self.config.get(
            'web_impersonate_autopip') is not False
        if fetch_fn is None and self.impersonate_fallback:
            # Production only (the golden run and injected tests stay
            # hermetic). One honest line per batch: the door's state —
            # and the self-install when it is wanted and missing.
            if _web_fetch.curl_cffi_available():
                self.log(
                    "🤝 v0.45.0 third door armed: a refusal-family wall on "
                    "every door is re-asked with Chrome's own TLS "
                    "handshake (curl_cffi) — the "
                    "'bot defense (Cloudflare: challenge)' class", "info")
            elif self.impersonate_autopip:
                self.log(
                    "🤝 v0.47.0: the third door is missing — installing "
                    "curl_cffi automatically (one-time, ~a minute)…",
                    "info")
                if _web_fetch.ensure_curl_cffi_installed():
                    self.log(
                        "🤝 third door ARMED — curl_cffi installed "
                        "automatically; fingerprint-class walls get "
                        "Chrome's own handshake from now on", "info")
                else:
                    self.log(
                        "ℹ️ the automatic install failed (offline? no pip?) "
                        "— run 'pip install curl_cffi' by hand; the "
                        "stdlib doors keep working meanwhile", "info")
            else:
                self.log(
                    "ℹ️ v0.45.0 third door not installed (pip install "
                    "curl_cffi — the browser-TLS handshake for "
                    "Cloudflare-class challenges); stdlib doors only",
                    "info")
        # v0.46.0 — THE LADDER: the dead-page rescue ladder (URL
        # variants, then the Wayback Machine's archived copy) and the
        # DNS-over-HTTPS verdict ride every production fetch, and every
        # failure carries its CATEGORY so retries go only to what time
        # can heal. Config "web_archive_fallback" / "web_doh_probe"
        # (both default ON) opt the two new rungs out; the injected
        # test fetchers take **kwargs, so they never break.
        self.archive_fallback = self.config.get(
            'web_archive_fallback') is not False
        self.doh_probe = self.config.get('web_doh_probe') is not False
        if fetch_fn is None and (self.archive_fallback or self.doh_probe):
            _rungs = []
            if self.archive_fallback:
                _rungs.append('dead pages climb variants then the Wayback '
                              'Machine')
            if self.doh_probe:
                _rungs.append('DNS failures get the DNS-over-HTTPS verdict')
            self.log("🪜 v0.46.0 ladder armed: " + '; '.join(_rungs)
                     + "; 429/503 pay Retry-After into the domain limiter",
                     "info")
        # v0.48.0 — the fourth door (the owner's real Chrome): a
        # hand-delivered page in <vault>/_review/hand-delivered/ answers
        # the fetch BEFORE any machine door is asked, walled failures
        # gain the door's hint, and the master table's 🖐 hand gesture
        # queues links for it. Config "web_hand_delivery": false opts
        # the whole door out (default ON — the check is one file stat
        # per link, and the queue never exists unless the owner asked).
        self.hand_delivery = self.config.get('web_hand_delivery') is not False
        self.rate_limiter = rate_limiter or _web_fetch.DomainRateLimiter(
            float(self.config.get('web_domain_delay_s', DOMAIN_DELAY_S)
                  or DOMAIN_DELAY_S))
        # Per-batch counters for the run report.
        self.counters = {'processed': 0, 'review': 0, 'skipped': 0,
                         'retried': 0, 'failed': 0, 'upgraded': 0,
                         'consolidated': 0}
        self.last_results: List[Dict] = []
        # v0.64.0 — ONE NOTE PER SITE's batch-local overlay: the site
        # map was built before this batch began, so the FIRST note a
        # batch writes for a new site registers here — a second link
        # of the same site in the SAME batch finds it (the injected
        # probe alone would miss it; the phase does not feed the index
        # mid-batch).
        self._batch_site_notes: Dict[str, str] = {}

    # -- helpers -----------------------------------------------------------

    PROXY_EPOCH_KEY = 'web_proxy_epoch'

    def _enforce_blocked_domains(self) -> None:
        """v0.20.0 — purge the state DB of blocked-domain links (retry
        queue rows + failed _review placeholder records, both marked
        dismissed) and delete the placeholder FILES. v0.21.0 — the same
        purge now covers SELF domains (the app's own bot auth links that
        were queued before this release, complete with their live tokens).
        Idempotent; every failure is tolerated (bookkeeping never kills a
        batch). File deletions are dry-run-aware."""
        try:
            report = self.state.purge_blocked_domains(
                lambda u: _links.domain_is_blocked(u, self.blocked_domains))
            deleted_files = 0
            for _url, path in report.get('placeholders', []):
                try:
                    if os.path.exists(path):
                        _dryrun.remove(path)
                        deleted_files += 1
                except Exception:
                    pass
            if report['retries'] or report['placeholders']:
                self.log(
                    f"🚫 Blocked domains ({', '.join(self.blocked_domains)}): "
                    f"purged {report['retries']} queued retry(ies) + "
                    f"{len(report['placeholders'])} _review placeholder(s) "
                    f"({deleted_files} file(s) deleted) — banned links "
                    f"are never collected (no note, no _review, no _inbox "
                    f"row)",
                    "info")
        except Exception as e:
            self.log(f"⚠️ Blocked-domain purge skipped: {e}", "warning")
        if not self.self_domains:
            return
        try:
            report = self.state.purge_blocked_domains(
                lambda u: _links.domain_is_self(u, self.self_domains))
            deleted_files = 0
            for _url, path in report.get('placeholders', []):
                try:
                    if os.path.exists(path):
                        _dryrun.remove(path)
                        deleted_files += 1
                except Exception:
                    pass
            if report['retries'] or report['placeholders']:
                self.log(
                    f"🔒 Self domains ({', '.join(self.self_domains)} — the "
                    f"app's own bot): purged {report['retries']} queued "
                    f"retry(ies) + {len(report['placeholders'])} _review "
                    f"placeholder(s) ({deleted_files} file(s) deleted, tokens "
                    f"gone with them) — never fetched, the _inbox row is the "
                    f"record", "info")
        except Exception as e:
            self.log(f"⚠️ Self-domain purge skipped: {e}", "warning")

    def _maybe_rearm_retries(self) -> None:
        """v0.19.0 — when the ACTIVE web proxy differs from the last one
        this state DB saw (first proxy ever, or a changed host/port/type),
        reset the whole retry queue: those failures queued while fetching
        direct deserve an immediate retry through the tunnel instead of
        waiting out their backoff. Once per proxy epoch — a later batch
        with the SAME proxy never re-arms again (the retry cap keeps its
        meaning)."""
        try:
            epoch = _web_fetch.proxy_label(self.web_proxy)
            if self.state.get_meta(self.PROXY_EPOCH_KEY) == epoch:
                return
            count = self.state.rearm_retries()
            self.state.set_meta(self.PROXY_EPOCH_KEY, epoch)
            if count:
                self.log(
                    f"🔁 Web proxy active — re-armed {count} queued "
                    f"retry(ies) for an immediate retry through the proxy",
                    "info")
        except Exception as e:  # bookkeeping must never kill the batch
            self.log(f"⚠️ Retry re-arm skipped: {e}", "warning")

    def _llm_json(self, messages: List[Dict], task: str = None) -> Dict:
        """One LLM call + robust JSON extraction. ``task`` tags the pass
        ('classify' / 'analyze') so the router can apply the per-task
        model override. Raises ValueError on an empty/unparseable answer
        (callers retry, then fall back to _review)."""
        from gitcurator.core.llm_client import extract_json
        content = self.llm_call(messages, task=task)
        if not content or not str(content).strip():
            raise ValueError("model returned an empty response")
        return extract_json(str(content))

    def _correction_examples(self, url: str) -> str:
        """v0.13.0 — the deferred Phase-3 few-shot hook: past corrections
        as classifier examples (needs the Phase-4 models.classify override
        to be safe on context budget — now shipped). This URL's own
        correction history first (strongest signal: the owner already
        moved THIS site once), then up to three of the owner's most
        recent moves in the Websites vault. '(none)' when there is
        nothing (the common case on a fresh install)."""
        if self.note_state_db is None:
            return "(none)"
        from gitcurator.core import note_state as _note_state
        lines: List[str] = []
        try:
            own = self.note_state_db.corrections_for(
                _note_state.VAULT_WEBSITES, url)
        except Exception as e:
            self.log(f"⚠️ corrections lookup failed for {url}: {e}",
                     "warning")
            own = []
        for c in own[:3]:
            lines.append(
                f"- this exact website: the owner moved it "
                f"{c['from_category'] or '(none)'} -> "
                f"{c['to_category'] or '(none)'}")
        try:
            recent = self.note_state_db.recent_corrections(
                _note_state.VAULT_WEBSITES, limit=6)
        except Exception as e:
            self.log(f"⚠️ recent-corrections lookup failed: {e}", "warning")
            recent = []
        seen = set()
        for c in recent:
            if c['source_url'] == url:
                continue
            key = (c['from_category'], c['to_category'])
            if key in seen:
                continue
            seen.add(key)
            lines.append(
                f"- {c['source_url']}: the owner moved it "
                f"{c['from_category'] or '(none)'} -> "
                f"{c['to_category'] or '(none)'}")
            if len(seen) >= 3:
                break
        return "\n".join(lines) if lines else "(none)"

    def _classify_category(self, url: str, title: str, description: str,
                           text: str) -> tuple:
        """Pass 1 (SPEC §4.6): category names + one-line definitions + the
        judgment rules. Validates the answer; retries up to CLASSIFY_RETRIES
        with a corrective nudge; returns (category, confidence) or ('', '')."""
        prompt = _prompts.load_prompt(
            'w01_category',
            CATEGORY_NAMES_WITH_ONE_LINE_DEFINITIONS=self.taxonomy
            .category_one_liner(),
            JUDGMENT_RULES=self.taxonomy.judgment_rules or "(none)",
            PAST_CORRECTIONS=self._correction_examples(url),
            URL=url, TITLE=title or "(unknown)",
            META_DESCRIPTION=description or "(none)",
            TEXT_EXCERPT=text[:LLM_EXCERPT_CHARS] or "(no page text)")
        messages = [{"role": "user", "content": prompt}]
        last_bad = ""
        for attempt in range(1 + CLASSIFY_RETRIES):
            try:
                answer = self._llm_json(messages, task='classify')
            except ValueError as e:
                last_bad = f"unparseable answer: {e}"
                continue
            cat = str(answer.get('category') or '').strip()
            conf = str(answer.get('confidence') or '').strip().lower()
            if self.taxonomy.is_category(cat):
                return cat, conf or 'medium'
            last_bad = f"category {cat!r} is not in the taxonomy"
            # Corrective retry: tell the model exactly what it did wrong.
            messages = [{"role": "user", "content": prompt + (
                f"\n\nYour previous answer was rejected: {last_bad}. "
                "Answer again with the EXACT name of one category from the "
                "list, as JSON.")}]
        self.log(f"⚠️ {url}: category classification failed after "
                 f"{1 + CLASSIFY_RETRIES} attempts ({last_bad}) — filing "
                 "under _review", "warning")
        return '', ''

    def _classify_subcategory(self, url: str, category: str, title: str,
                              description: str, text: str) -> tuple:
        """Pass 2: only the chosen category's subcategories."""
        prompt = _prompts.load_prompt(
            'w02_subcategory',
            CATEGORY=category,
            SUBCATEGORIES_WITH_DEFINITIONS=self.taxonomy
            .subcategory_one_liner(category),
            URL=url, TITLE=title or "(unknown)",
            META_DESCRIPTION=description or "(none)",
            TEXT_EXCERPT=text[:LLM_EXCERPT_CHARS] or "(no page text)")
        messages = [{"role": "user", "content": prompt}]
        for attempt in range(1 + CLASSIFY_RETRIES):
            try:
                answer = self._llm_json(messages, task='classify')
            except ValueError:
                continue
            sub = str(answer.get('subcategory') or '').strip()
            conf = str(answer.get('confidence') or '').strip().lower()
            if sub.lower() == 'none' or sub == '':
                return '', conf or 'medium'
            if self.taxonomy.is_subcategory_of(category, sub):
                return sub, conf or 'medium'
            messages = [{"role": "user", "content": prompt + (
                f"\n\nYour previous answer {sub!r} is not one of the listed "
                "subcategories. Answer again with the EXACT name of one "
                "subcategory from the list, or \"none\".")}]
        return '', ''   # no subcategory — still file under the category

    def _analyze(self, url: str, title: str, description: str,
                 text: str, category: str) -> Dict:
        """w03: the note fields. Rule: omit rather than guess — enforced by
        the prompt itself; unparseable answers raise to the caller."""
        # SPEC Appendix A note: when the page text is missing or clearly
        # partial, the model gets only the title and description.
        if len(text or '') < MIN_TEXT_FOR_ANALYSIS:
            excerpt = "(no usable page text — classify from title and description)"
        else:
            excerpt = text[:LLM_EXCERPT_CHARS]
        prompt = _prompts.load_prompt(
            'w03_analyze',
            TAG_HINTS=self.taxonomy.tag_hints_of(category) or "(none)",
            URL=url, TITLE=title or "(unknown)",
            META_DESCRIPTION=description or "(none)",
            TEXT_EXCERPT=excerpt)
        answer = self._llm_json([{"role": "user", "content": prompt}],
                                task='analyze')
        if not isinstance(answer, dict):
            raise ValueError("analyze answer was not a JSON object")
        return answer

    # -- writing -----------------------------------------------------------

    def _note_path(self, category: str, subcategory: str, name: str) -> str:
        rel = self.taxonomy.folder_relpath(category, subcategory or None)
        fname = safe_filename(name) or "untitled-site"
        if not fname.lower().endswith('.md'):
            fname += '.md'
        path = os.path.join(self.vault_path, rel, fname)
        return unique_path(path)

    def _write_note(self, path: str, content: str) -> None:
        # _dryrun.makedirs records instead of creating while a dry-run is
        # active (and is a plain makedirs otherwise); atomic_write_text is
        # gated the same way — dry-run writes NOTHING, by construction.
        _dryrun.makedirs(os.path.dirname(path), exist_ok=True)
        atomic_write_text(path, content)

    def _record(self, url: str, path: str, content: str, category: str,
                subcategory: str, fetch_status: str) -> None:
        """Persist dedupe + note-state bookkeeping. In a dry-run the state
        DB is the shadow cache, so nothing real is recorded."""
        self.state.mark_processed(url, path, category, subcategory,
                                  fetch_status)
        if self.note_state_db is not None:
            try:
                from gitcurator.core import note_state as _note_state
                from gitcurator.core.links import normalize_website_url
                self.note_state_db.record_note(
                    _note_state.VAULT_WEBSITES, url, path, content=content,
                    category=category, subcategory=subcategory,
                    normalizer=normalize_website_url)
            except Exception:
                pass  # bookkeeping must never break a run

    def _app_owns_old_note(self, canonical: str, old_path: str) -> bool:
        """True when the file at ``old_path`` is EXACTLY the app-written
        note recorded in note_state (fingerprint unchanged — no human
        edit). Only then may the pipeline replace or remove it."""
        if self.note_state_db is None or not old_path:
            return False
        try:
            from gitcurator.core import note_state as _note_state
            row = self.note_state_db.row_for(
                _note_state.VAULT_WEBSITES, canonical)
            if not row or not row.get('fingerprint'):
                return False
            if os.path.normpath(row.get('path') or '') != \
                    os.path.normpath(old_path):
                return False
            with open(old_path, 'r', encoding='utf-8',
                      errors='replace') as f:
                content = f.read()
            return _note_state.compute_fingerprint(content) \
                == row['fingerprint']
        except Exception:
            return False

    def _cleanup_replaced_review_note(self, canonical: str,
                                      old_path: str) -> None:
        """After a successful upgrade, remove the old app-owned _review
        placeholder — otherwise two files would carry the same source
        (the exact duplicate situation SPEC §4.4 flags). A hand-edited
        placeholder is NEVER removed; Phase 3's duplicate detector will
        surface it instead."""
        if not old_path or not os.path.exists(old_path):
            return
        if self._app_owns_old_note(canonical, old_path):
            _dryrun.remove(old_path)
            self.log(f"♻️ upgraded note replaced its _review placeholder "
                     f"({os.path.basename(old_path)})", "info")
        else:
            self.log(f"⚠️ kept the old _review note {old_path!r} — it looks "
                     "hand-edited; both files now carry the same source",
                     "warning")

    @staticmethod
    def _read_note_placement(path: str) -> (str, str, str):
        """v0.64.0 — a note's own (category, subcategory,
        fetch_status) from its frontmatter, for the consolidation's
        ledger row (the site note's placement IS the link's placement —
        the folder is the category). ('', '', 'full') on any doubt —
        bookkeeping defaults, never failures."""
        cat = sub = status = ''
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.read(4000).splitlines()
        except Exception:
            return '', '', 'full'
        if not lines or lines[0].strip() != '---':
            return '', '', 'full'
        for line in lines[1:]:
            s = line.strip()
            if s == '---':
                break
            low = s.lower()
            if low.startswith('category:'):
                cat = s.partition(':')[2].strip().strip('"').strip("'")
            elif low.startswith('subcategory:'):
                sub = s.partition(':')[2].strip().strip('"').strip("'")
            elif low.startswith('fetch_status:'):
                status = s.partition(':')[2].strip().strip('"').strip("'")
        return cat, sub, (status or 'full')

    def _consolidate_into_site_note(self, url: str, canonical: str,
                                    result: Dict, site_note: str,
                                    prior: Optional[Dict] = None,
                                    upgraded: bool = False) -> Dict:
        """v0.64.0 — ONE NOTE PER SITE, the gate's hands: the link's
        URL rides the site note's links list (frontmatter +
        ``## Links on this site``), the ledger row points at the site
        note, any app-owned failed placeholder of the link is swept,
        and its retry row resolves — the site is KNOWN, no second note
        is ever defined. Neither the fetcher nor the LLM is asked (the
        owner's law is instant). Returns the SUCCESS-shaped result
        (outcome 'processed', the site note's path); never raises —
        a broken consolidation falls back to the honest skip."""
        cat, sub, status = self._read_note_placement(site_note)
        rep = add_site_links_to_note(site_note, [canonical], log=self.log)
        if not rep.get('written') and not rep.get('already'):
            # the note could not take the link — the law cannot hold;
            # fall back to the plain skip so nothing is invented
            result['outcome'] = 'skipped'
            result['error'] = ("the site's note could not be extended — "
                               "the link stays queued for the full flow")
            self.counters['skipped'] += 1
            self.last_results.append(result)
            return result
        try:
            if prior and prior.get('fetch_status') == 'failed' \
                    and self._is_review_path(prior.get('note_path') or ''):
                # the sweep re-reads the file at removal time (the
                # scan's own law): an app-owned failed placeholder
                # leaves, a hand-edited one stays and warns
                self._remove_scanned_placeholder(
                    prior.get('note_path') or '')
        except Exception:
            pass
        try:
            self.state.resolve_retry(canonical)
        except Exception:
            pass
        try:
            with open(site_note, 'r', encoding='utf-8',
                      errors='replace') as f:
                _content = f.read()
        except Exception:
            _content = ''
        self._record(canonical, site_note, _content, cat, sub, status)
        # the site's anchor — a later link of the same site in this
        # batch finds it in the overlay
        try:
            self._batch_site_notes.setdefault(
                site_key_of(canonical), site_note)
        except Exception:
            pass
        # v0.64.0 — the 🖐 hand's delivery lands here too: the link is
        # properly stored (in its site's note — note_is_properly_stored
        # honors the site_links list), so the gesture retires exactly
        # as it would for a note of its own.
        try:
            self._harvest_hand_row(canonical, site_note,
                                   fetch_status=status)
        except Exception:
            pass    # the harvest never breaks a consolidation
        rel = os.path.relpath(site_note, self.vault_path) \
            .replace(os.sep, '/') if self.vault_path else site_note
        self.counters['consolidated'] = \
            self.counters.get('consolidated', 0) + 1
        result.update(outcome='processed', note_path=site_note,
                      category=cat, subcategory=sub,
                      fetch_status=status, error='')
        self.last_results.append(result)
        self.log(
            f"🧲 [{self._vault_name}] {url}: the site's note already "
            f"holds this domain — the link joined it (one note per "
            f"site) → {rel}", "success")
        return result

    # -- v0.56.0: THE HAND'S HARVEST — the per-link probes --------------

    def _table_rows_cached(self) -> List[Dict]:
        """The master table's rows, parsed once per file STATE
        (mtime+size keyed — a stamp the harvest writes mid-run
        refreshes the cache honestly, so a retired row is never
        re-harvested). Unreadable / missing reads as empty; never
        raises; never a gate."""
        try:
            if not self.vault_path:
                return []
            path = decommission_table_path(self.vault_path)
            if not os.path.isfile(path):
                return []
            st = os.stat(path)
            key = (path, st.st_mtime_ns, st.st_size)
            if getattr(self, '_hand_table_key', None) != key:
                self._hand_table_key = key
                self._hand_table_rows = _parse_decommission_rows(path)
            return self._hand_table_rows
        except Exception:
            return []

    def _row_reads_hand(self, canonical: str) -> bool:
        """v0.56.0 — does the master table carry the 🖐 gesture for
        this link? The table's own grammar decides (the precedence
        law: death / reviewed / revived win first; first row wins on
        a hand-added duplicate). A pure probe — never a gate, and a
        table that cannot be read answers NO (the harvest and the
        exemption simply do not fire; the old laws hold)."""
        if not canonical:
            return False
        for row in self._table_rows_cached():
            try:
                if normalize_website_url(row.get('url') or '') != canonical:
                    continue
            except Exception:
                continue
            s = row.get('status') or ''
            if _status_is_dead(s) or _status_is_reviewed(s) \
                    or _status_is_revived(s):
                return False
            return _hand_delivery._status_is_hand(s)
        return False

    def _has_app_review_note(self, canonical: str) -> bool:
        """v0.56.0 — does this link own an app-written note in
        ``_review`` (ANY fetch_status — the half-fetched classes:
        failed placeholders, low-confidence, partial, analysis
        failures)? The folder scan is cached per folder STATE
        (mtime+size keyed — a sweep or a fresh placeholder refreshes
        it); the frontmatter's ``managed_by`` line is the ownership
        law. Pure; never raises; unreadable reads as NO."""
        if not canonical or not self.vault_path:
            return False
        try:
            review_dir = os.path.join(self.vault_path, REVIEW_FOLDER)
            key = ('review', os.stat(review_dir).st_mtime_ns,
                   os.stat(review_dir).st_size)
            if getattr(self, '_hand_review_key', None) != key:
                self._hand_review_key = key
                self._hand_review_rows = scan_review_notes(self.vault_path)
            for it in self._hand_review_rows:
                if _same_source(it.get('url') or '', canonical):
                    return True
        except Exception:
            return False
        return False

    def _harvest_hand_row(self, canonical: str, note_path: str,
                          fetch_status: str = 'full') -> None:
        """v0.56.0 — THE HAND'S HARVEST, the delivery-time half: a
        link whose master-table row carries the 🖐 gesture just landed
        a PROPER, categorized note (this is the only caller — the
        success path of :meth:`_process_link_inner`), so the gesture
        is retired into the green checkbox
        (:func:`gitcurator.core.hand_delivery.stamp_hand_delivered_rows`)
        and every app-owned half-fetched ``_review`` item for this
        source is swept (:func:`sweep_review_leftovers`). Never
        raises; never a gate — a link without the gesture simply
        keeps the waiting family's laws (``📁 stored`` and friends).

        v0.57.0 — THE NOTE IS THE SUCCESS: the stamp itself is EARNED
        by :func:`note_is_properly_stored` (the note on disk, the
        app's own, for THIS link, outside ``_review``) — a write that
        cannot be proven proper leaves the gesture in place for the
        redo pass instead of a green checkbox over a half-fetched
        item (the owner's "false success" law)."""
        if not self.vault_path or not canonical:
            return
        if not self._row_reads_hand(canonical):
            return
        try:
            proper, _why = note_is_properly_stored(
                {'url': canonical, 'note_path': note_path,
                 'fetch_status': fetch_status})
        except Exception:
            proper = False
        if not proper:
            self.log(f"🖐 [{self._vault_name}] {canonical}: the note could "
                     f"not be proven properly stored — the row keeps its "
                     f"gesture (the redo pass asks the LLM again; a ✅ is "
                     f"only earned by the note itself)", "warning")
            return
        stamped = 0
        try:
            stamped = _hand_delivery.stamp_hand_delivered_rows(
                self.vault_path, [canonical], log=self.log)
        except Exception as e:
            self.log(f"⚠️ Hand-harvest stamp skipped for {canonical}: "
                     f"{e}", "warning")
        swept = 0
        try:
            swept = sweep_review_leftovers(
                self.vault_path, canonical, keep_path=note_path,
                log=self.log)
        except Exception as e:
            self.log(f"⚠️ Hand-harvest sweep skipped for {canonical}: "
                     f"{e}", "warning")
        if stamped or swept:
            self._hand_table_key = None   # the stamp changed the table
            self.log(f"✅ [{self._vault_name}] the hand's harvest: the 🖐 "
                     f"row retired to the green checkbox (✅ "
                     f"hand-delivered)"
                     + (f", {swept} half-fetched _review item(s) swept"
                        if swept else "")
                     + " — the proper, categorized note is in the vault",
                     "success")

    # -- the per-link flow ---------------------------------------------------

    def process_link(self, url: str) -> Dict:
        """Run the full per-link pipeline. Returns a result dict (never
        raises — one failing link never stops the batch)."""
        result = {'url': url, 'canonical': '', 'outcome': 'failed',
                  'note_path': '', 'category': '', 'subcategory': '',
                  'fetch_status': '', 'error': ''}
        try:
            return self._process_link_inner(url, result)
        except Exception as e:
            result['error'] = f"{type(e).__name__}: {e}"
            self.log(f"❌ Website pipeline error for {url}: {result['error']}",
                     "error")
            # SPEC §4.3: nothing is silently dropped — an unexpected error
            # (e.g. an LLM outage mid-classification) still leaves a
            # _review note behind, UNLESS this link already got one (the
            # error may have happened after a successful write).
            if not result.get('note_path'):
                try:
                    canonical = result.get('canonical') \
                        or normalize_website_url(url)
                    note = build_review_note(
                        canonical, 'failed',
                        f"Pipeline error: {result['error']}")
                    path = self._review_path(canonical)
                    self._write_note(path, note)
                    self._record(canonical, path, note, '', '', 'failed')
                    result.update(outcome='review', note_path=path,
                                  canonical=canonical)
                    self.counters['review'] += 1
                except Exception:
                    pass  # the result dict still carries the error
            self.counters['failed'] += 1
            self.last_results.append(result)
            return result

    def _process_link_inner(self, url: str, result: Dict) -> Dict:
        canonical = normalize_website_url(url)
        result['canonical'] = canonical

        # ---- 1b. never-fetch domains (v0.20.0 blocked + v0.21.0 self) ---
        # Never fetched, never noted, never retried — whatever path
        # brought the link here (batch, retry, direct, import). v0.35.0:
        # banned links are omitted ENTIRELY (no _inbox row either — the
        # manifest's blocked bucket is the count).
        if self.blocked_domains and _links.domain_is_blocked(
                url, self.blocked_domains):
            result['outcome'] = 'skipped'
            result['error'] = 'blocked domain — omitted (never collected)'
            self.counters['skipped'] += 1
            self.last_results.append(result)
            self.log(f"🚫 [{self._vault_name}] {url}: banned domain — "
                     "omitted (never fetched, never noted, never "
                     "collected)", "info")
            return result
        if self.self_domains and _links.domain_is_self(url, self.self_domains):
            result['outcome'] = 'skipped'
            result['error'] = ("self domain (the app's own bot) — recorded "
                               "in _inbox only")
            self.counters['skipped'] += 1
            self.last_results.append(result)
            self.log(f"🔒 {_links.scrub_url_token(url)}: self domain (the "
                     f"app's own bot) — never fetched, the _inbox row is "
                     f"the record", "info")
            return result

        # ---- 2. dedupe (SPEC §4.3 step 2) --------------------------------
        _auto_verdict = ''
        if self.state.is_dismissed(canonical):
            result['outcome'] = 'skipped'
            try:
                _drow = self.state.dismissed_row(canonical) or {}
                _dreason = str(_drow.get('reason') or '')
            except Exception:
                _dreason = ''
            if _dreason.startswith('auto-verdict:'):
                _auto_verdict = _dreason[len('auto-verdict:'):].strip()
            if canonical in self._graveyard_urls:
                result['error'] = ('decommissioned by owner (graveyard) — '
                                   'never fetched again')
            elif _dreason.startswith(BANISH_REASON_PREFIX):
                # v0.58.0 — the banishment: the note left the library AND
                # the URL is blacklisted (the delete-never-refetch
                # contract the owner asked for, verbatim)
                result['error'] = ('banished by owner (🗑️) — the note was '
                                   'removed and the URL blacklisted; '
                                   'never fetched again')
            elif _auto_verdict:
                # v0.46.0 — the fetcher's own verdict retired this link
                # (dead / paywalled / refused): the graveyard's gate,
                # earned by the ladder instead of the owner's hand.
                result['error'] = (f'retired by auto-verdict '
                                   f'({_auto_verdict}) — never fetched '
                                   'again (revive via the graveyard if '
                                   'it returns)')
            else:
                result['error'] = 'dismissed (note was deleted)'
            self.counters['skipped'] += 1
            self.last_results.append(result)
            if canonical in self._graveyard_urls:
                self.log(f"🪦 {url}: decommissioned by owner — skipped "
                         "(the graveyard table's verdict)", "info")
            elif _dreason.startswith(BANISH_REASON_PREFIX):
                self.log(f"🗑️ {url}: banished by owner — skipped (the "
                         f"note is gone, the URL is blacklisted; ♻️ "
                         f"revived in the master table brings it back)",
                         "info")
            elif _auto_verdict:
                self.log(f"🪦 {url}: skipped — auto-verdict: "
                         f"{_auto_verdict} (revive via the graveyard if "
                         "the link returns)", "info")
            return result

        in_vault = self.vault_index_has(canonical)
        prior = self.state.processed_row(canonical)
        # v0.56.0 — THE HAND'S HARVEST, the gate's half-fetched
        # exemption: a link whose master-table row carries the 🖐
        # gesture and whose vault presence is an app-owned _review
        # note (ANY fetch_status — the low-confidence / partial /
        # analysis-failure classes, not just 'failed') is NOT "already
        # in the websites vault": it is a HALF-FETCHED item the owner
        # asked the doors to complete, so the pipeline runs and the
        # harvest replaces it with the proper, categorized note. The
        # gesture is never a retirement (the v0.48 law) — this is its
        # positive half: the hand may also mean "finish this one".
        _hand_gesture = self._row_reads_hand(canonical)
        _half_fetched_hand = (in_vault and _hand_gesture
                              and self._has_app_review_note(canonical))
        if in_vault and not (
                (prior and prior.get('fetch_status') == 'failed'
                 and self._is_review_path(prior.get('note_path')))
                or _half_fetched_hand):
            # Already a real note (or a non-failed _review note): skip.
            result['outcome'] = 'skipped'
            result['error'] = 'already in the websites vault'
            self.counters['skipped'] += 1
            self.last_results.append(result)
            return result

        # ---- 2b. ONE NOTE PER SITE (v0.64.0) ------------------------------
        # The owner's law (verbatim): "for same domains, do not define
        # different notes, try to consolidate all of them in same note,
        # if multiple links of that site exist". A link whose SITE
        # already holds a real note in the vault never gets a second
        # note: its URL rides the site note's links list, the ledger
        # row points at the site note, and neither the fetcher nor the
        # LLM is ever asked. The 🖐 hand outranks the law exactly as it
        # outranks the settlement (v0.56.0's precedent): a gestured
        # link falls through to the full flow — where the site note
        # acts as a soft lock (the note is the answer; the doors are
        # just the delivery). A failed _review placeholder of the same
        # site (no hand) rides the consolidation too: its URL joins
        # the site note's list, the placeholder is swept, the retry
        # row resolves — the site is KNOWN; the wall's page is not a
        # second note.
        _site_note = ''
        if self.site_note_for is not None:
            try:
                _site_note = self.site_note_for(canonical) or ''
            except Exception:
                _site_note = ''
        if not _site_note:
            # the batch-local overlay (a site this very batch noted)
            _site_note = self._batch_site_notes.get(
                site_key_of(canonical), '') or ''
        if _site_note and not os.path.isfile(_site_note):
            _site_note = ''      # a stale probe never lies
        if _site_note and self._is_review_path(_site_note):
            _site_note = ''      # a placeholder is not the site's note
        if _site_note and not _hand_gesture:
            return self._consolidate_into_site_note(
                url, canonical, result, _site_note, prior=prior,
                upgraded=bool(in_vault and prior))

        retry = self.state.retry_row(canonical)
        if retry and retry['attempts'] >= MAX_FETCH_RETRIES:
            # v0.56.0 — THE HAND'S REBORN COUNTER: a burned-out retry
            # row is skipped by process_link forever ("no more
            # retries"), and the waiting family's reborn
            # (retry_master_waiting) never touches 🖐 rows — so the
            # owner's exact flow (walled → 3 retries burned → 🖐 →
            # the fifth door delivers the page) would die at this
            # gate with the page WAITING in the folder. The gesture —
            # or a delivered page already sitting there — reborns
            # ONE counter (the same surgical per-link reborn the
            # master-retry pass uses, never a queue-wide reset):
            # the hand says "finish this one now".
            _hand_reborn = _hand_gesture
            if not _hand_reborn and self.hand_delivery and self.vault_path:
                try:
                    _hand_reborn = any(
                        _same_source(d.get('url') or '', canonical)
                        for d in _hand_delivery.collect_delivered(
                            self.vault_path))
                except Exception:
                    _hand_reborn = False
            if _hand_reborn:
                try:
                    self.state.reset_retry_attempts(canonical)
                    retry = self.state.retry_row(canonical)
                    self.log(f"🖐 {url}: the hand reborn the burned-out "
                             f"retry counter — the gesture (or the "
                             f"delivered page) says finish this one",
                             "info")
                except Exception as e:
                    self.log(f"⚠️ could not reborn the retry counter "
                             f"for {url}: {e}", "warning")
        if retry and retry['attempts'] >= MAX_FETCH_RETRIES:
            # A failed-fetch _review note already exists and retries are
            # exhausted: leave it, report, never drop silently.
            result['outcome'] = 'skipped'
            result['error'] = (f"fetch failed {retry['attempts']} times — "
                               "kept in _review, no more retries")
            self.counters['skipped'] += 1
            self.last_results.append(result)
            return result

        upgraded = bool(in_vault and prior
                        and (prior.get('fetch_status') == 'failed'
                             or (_half_fetched_hand
                                 and self._is_review_path(
                                     prior.get('note_path') or ''))))

        # v0.12.0 — Phase 3 (locked-skip, SPEC §4.4/§6): a note the owner
        # moved by hand is LOCKED — its folder placement beats the
        # classifier. When a locked row exists (e.g. a _review placeholder
        # the owner moved into a category folder, later upgraded by a
        # successful retry), the model's category/subcategory are ignored
        # and the locked row's values are written instead.
        locked_row = None
        if self.note_state_db is not None:
            try:
                from gitcurator.core import note_state as _note_state
                _row = self.note_state_db.row_for(
                    _note_state.VAULT_WEBSITES, canonical)
                if _row is not None and _row.get('locked') \
                        and _row.get('category'):
                    locked_row = _row
            except Exception:
                locked_row = None

        # ---- 3. fetch ------------------------------------------------------
        # v0.48.0 — the fourth door answers FIRST: a page the owner
        # delivered by hand (their real Chrome saved it into
        # <vault>/_review/hand-delivered/) IS the fetch — the machine
        # doors are never asked, the story rides in the reason, the
        # queued file is consumed (kept in place — it is the record).
        # v0.57.0 — THE NOTE IS THE SUCCESS, the redo-consume: a link
        # whose row carries the 🖐 gesture may re-read an ALREADY
        # CONSUMED page (the false-success redo — the delivered page is
        # the record, the LLM is asked again for the proper, categorized
        # note; a link with a proper note never reaches this step — the
        # dedupe gate answers first).
        fetch = None
        if self.hand_delivery:
            try:
                fetch = _hand_delivery.take_hand_delivered(
                    self.vault_path, canonical, log=self.log,
                    allow_consumed=_hand_gesture)
            except Exception as e:
                self.log(f"⚠️ Hand-delivery check skipped: {e}", "warning")
        if fetch is None:
            fetch = self.fetch_fn(
                url, timeout_s=self.fetch_timeout_s,
                max_bytes=self.fetch_max_bytes,
                rate_limiter=self.rate_limiter,
                impersonate_fallback=self.impersonate_fallback,
                archive_fallback=self.archive_fallback,
                doh_probe=self.doh_probe,
                impersonate_autopip=self.impersonate_autopip)
        else:
            self.log(f"🖐 [{self._vault_name}] {url}: {fetch.reason} — "
                     "the hand-delivered page is the fetch answer",
                     "success")
        result['fetch_status'] = fetch.status

        if not fetch.ok:
            # ---- failure handling: minimal _review note + retry queue -----
            # v0.46.0 — the CATEGORY decides whether time can heal the
            # failure. dead / paywalled / refused / bad_url never requeue:
            # the note is still written (no link left behind), the retry
            # row is DROPPED (nothing to wait for), and the link is
            # auto-dismissed with the verdict as the reason — the same
            # never-fetch-again gate the graveyard uses, earned here by
            # the fetcher's own verdict instead of the owner's hand. The
            # owner can revive (♻️) or decommission (🪦) as usual.
            _no_heal = (getattr(fetch, 'category', '') or '') in (
                'dead', 'paywalled', 'refused', 'bad_url')
            # v0.48.0 — a WALL the three machine doors could not open
            # names the fourth door in the reason itself: the line rides
            # into the _review note, the master table's Notes column,
            # and the retry queue's last_error (the picker's data).
            if self.hand_delivery and _hand_delivery.is_walled_reason(
                    fetch.reason):
                fetch.reason = (fetch.reason or '') + HAND_DOOR_HINT
            if _no_heal:
                if retry:
                    self.state.resolve_retry(canonical)
                self.state.dismiss(
                    canonical,
                    f"auto-verdict: {fetch.category} — {fetch.reason}")
            else:
                self.state.enqueue_retry(canonical, fetch.reason)
            note = build_review_note(canonical, 'failed',
                                     f"Fetch failed: {fetch.reason}")
            # Re-failure: overwrite the app-owned placeholder in place when
            # possible (same path, atomic write) instead of stacking
            # _v1/_v2 duplicates; a hand-edited placeholder is kept and a
            # fresh path is used.
            path = self._review_path(canonical, prior=prior)
            self._write_note(path, note)
            self._record(canonical, path, note, '', '', 'failed')
            result.update(outcome='review', note_path=path,
                          error=f"fetch failed: {fetch.reason}")
            self.counters['review' if not upgraded else 'upgraded'] += 1
            self.last_results.append(result)
            if _no_heal:
                self.log(f"🪦 [{self._vault_name}] {url}: fetch failed "
                         f"({fetch.reason}) — minimal note in _review; "
                         f"no retry scheduled (category: "
                         f"{fetch.category} — the verdict does not heal "
                         "with time; revive via the graveyard if the "
                         "link returns)", "warning")
            else:
                self.log(f"📥 [{self._vault_name}] {url}: fetch failed "
                         f"({fetch.reason}) — minimal note in _review, "
                         "retry scheduled", "warning")
            return result

        if (getattr(fetch, 'category', '') or '') == 'archived':
            # ---- v0.46.0 — an archived rescue files under _review -------
            # Real content served by the Wayback Machine, but the LIVE
            # page is gone: like every other partial it waits for the
            # owner's move (a dead link's snapshot is the owner's call —
            # keep, move, or bury), and unlike a failure it RESOLVES the
            # retry row (the fetch itself succeeded; the note is indexed
            # by the vault, so it is never re-fetched).
            page = _web_extract.extract_from_bytes(fetch.body, fetch.charset)
            reason = fetch.reason or 'archived copy of a dead page'
            note = build_review_note(canonical, 'partial', reason,
                                     title=page.title or '')
            path = self._review_path(canonical, prior=prior)
            self._write_note(path, note)
            self._record(canonical, path, note, '', '', 'partial')
            if retry:
                self.state.resolve_retry(canonical)
            result.update(outcome='review', note_path=path, error=reason,
                          fetch_status='partial')
            self.counters['review' if not upgraded else 'upgraded'] += 1
            self.last_results.append(result)
            self.log(f"🗄️ [{self._vault_name}] {url}: {reason} — filed "
                     "under _review (the live page is gone)", "warning")
            return result

        # ---- 4. extract ----------------------------------------------------
        page = _web_extract.extract_from_bytes(fetch.body, fetch.charset)
        fetch_status = fetch.status           # full | partial (pdf/size)
        if fetch_status == 'full':
            if page.is_js_shell:
                fetch_status = 'partial'
            elif page.is_paywall:
                fetch_status = 'partial'
        title = page.title or ''
        description = page.meta_description or ''

        # ---- 5. classify ---------------------------------------------------
        # v0.64.0 — ONE NOTE PER SITE's soft lock: a 🖐 hand link whose
        # site already holds a real note does not need the classifier
        # either — the site note's own placement IS the answer (the
        # doors are just the delivery). A locked row (the owner's own
        # placement for THIS link) still wins first.
        _site_lock = bool(_site_note) and locked_row is None
        if locked_row is not None:
            # The owner already placed this note — no model call, no
            # _review: their correction IS the classification.
            category = locked_row['category']
            subcategory = locked_row.get('subcategory') or ''
            self.log(f"🔒 {url}: locked note — owner's placement "
                     f"({category}"
                     + (f" / {subcategory}" if subcategory else "")
                     + ") kept, classifier skipped", "info")
        elif _site_lock:
            category, subcategory, _st = self._read_note_placement(_site_note)
            self.log(f"🔗 {url}: the site's note is the answer — its "
                     f"placement ({category}"
                     + (f" / {subcategory}" if subcategory else "")
                     + ") kept, classifier and analysis skipped (one note "
                     f"per site)", "info")
        else:
            category, conf1 = self._classify_category(
                canonical, title, description, page.text)
            if not category or conf1 == LOW_CONFIDENCE:
                # Low confidence is NOT a fetch failure — no fetch retry. The
                # note waits in _review for the owner's move (a correction,
                # SPEC §4.4), and the run report lists it.
                reason = ("classification confidence was low"
                          if category else "no valid category after retries")
                note = build_review_note(
                    canonical, fetch_status, reason, title=title)
                path = self._review_path(canonical)
                self._write_note(path, note)
                self._record(canonical, path, note, '', '', fetch_status)
                result.update(outcome='review', note_path=path, error=reason,
                              fetch_status=fetch_status)
                self.counters['review'] += 1
                self.last_results.append(result)
                # v0.57.0 — THE NOTE IS THE SUCCESS: for a 🖐 hand link
                # this is NOT a success — the note is half-fetched, so
                # the row keeps its gesture and the redo pass
                # (scan_master_redo_rows) asks the LLM again. The log
                # says so instead of letting a delivery's earlier ✅
                # lines tell a false story (the owner's law).
                self.log(f"🗂️ [{self._vault_name}] {url}: {reason} — filed "
                         "under _review"
                         + (" — NOT properly stored yet: the 🖐 row keeps "
                            "its gesture and the redo pass will re-read the "
                            "delivered page and ask the LLM again"
                            if _hand_gesture else ""), "warning")
                return result

            subcategory, conf2 = self._classify_subcategory(
                canonical, category, title, description, page.text)
            if conf2 == LOW_CONFIDENCE:
                subcategory = ''      # low-confidence subcategory: category only

        # ---- 6. analyze ----------------------------------------------------
        analysis: Dict = {}
        if not _site_lock:
            try:
                analysis = self._analyze(canonical, title, description,
                                         page.text, category)
            except Exception as e:
                # Analysis failed but classification succeeded: still
                # write a review note — the link must never be silently
                # dropped.
                note = build_review_note(
                    canonical, fetch_status,
                    f"Analysis failed: {e}", title=title)
                path = self._review_path(canonical)
                self._write_note(path, note)
                self._record(canonical, path, note, category, subcategory,
                             fetch_status)
                result.update(outcome='review', note_path=path,
                              error=f"analysis failed: {e}",
                              category=category, subcategory=subcategory,
                              fetch_status=fetch_status)
                self.counters['review'] += 1
                self.last_results.append(result)
                # v0.57.0 — the redo story for a 🖐 hand link (the note is
                # half-fetched; the redo pass asks the LLM again)
                if _hand_gesture:
                    self.log(f"🗂️ [{self._vault_name}] {url}: analysis "
                             f"failed — filed under _review — NOT properly "
                             f"stored yet: the 🖐 row keeps its gesture and "
                             f"the redo pass will re-read the delivered page "
                             f"and ask the LLM again", "warning")
                return result

        # ---- 7. build & write ------------------------------------------------
        if _site_note:
            # v0.64.0 — ONE NOTE PER SITE, the 🖐 hand's delivery: the
            # fetched page answered, the site's note is where the link
            # belongs — it joins the note, no second note is defined
            # (the soft lock above already skipped the classifier and
            # the analyzer; the doors were just the delivery).
            return self._consolidate_into_site_note(
                url, canonical, result, _site_note, prior=prior,
                upgraded=upgraded)
        name = str(analysis.get('name') or title or canonical)
        note = build_website_note(canonical, analysis, category, subcategory,
                                  fetch_status)
        path = self._note_path(category, subcategory, name)
        self._write_note(path, note)
        # Upgrade cleanup BEFORE _record: the ownership proof compares the
        # note_state row's path with the OLD path — recording first would
        # make the app look like it no longer owns the placeholder.
        if upgraded and prior:
            self._cleanup_replaced_review_note(canonical,
                                               prior.get('note_path') or '')
        self._record(canonical, path, note, category, subcategory,
                     fetch_status)
        # v0.64.0 — ONE NOTE PER SITE: this note is now the site's
        # anchor — a later link of the same site in this batch (or a
        # re-send) consolidates into it instead of defining a second
        # note.
        try:
            self._batch_site_notes.setdefault(
                site_key_of(canonical), path)
        except Exception:
            pass
        # Full success: any pending fetch-retry for this link is resolved.
        if retry:
            self.state.resolve_retry(canonical)
        # v0.56.0 — THE HAND'S HARVEST (the owner's law, verbatim):
        # "when newly fetched website with hand (🖐) are fetched and
        # stored correctly, the system must automatically turn the
        # hand emoji to green checkbox and remove their half-fetched
        # items from _review". THIS is the moment — the proper,
        # categorized note just landed — so a link whose row carries
        # the gesture retires it (✅ hand-delivered) and its app-owned
        # _review leftovers are swept. Tolerated everywhere: the
        # harvest never breaks a delivery.
        try:
            self._harvest_hand_row(canonical, path,
                                   fetch_status=fetch_status)
        except Exception as e:
            self.log(f"⚠️ Hand harvest skipped for {url}: {e}", "warning")

        result.update(outcome='processed', note_path=path, category=category,
                      subcategory=subcategory, fetch_status=fetch_status,
                      error='')
        self.counters['processed' if not upgraded else 'upgraded'] += 1
        self.last_results.append(result)
        # v0.35.0 — the owner asked the log to SHOW where each link goes:
        # "[Vault Name] Item X processed and stored". The vault name is
        # the configured Websites vault's folder; the arrow is the note's
        # path INSIDE the vault (category folders included).
        _rel = os.path.relpath(path, self.vault_path).replace(os.sep, '/') \
            if self.vault_path else path
        self.log(
            f"✅ [{self._vault_name}] {name} processed and stored "
            f"→ {_rel}"
            + (f" ({fetch_status})" if fetch_status != 'full' else ''),
            "success")
        return result

    def _review_path(self, canonical: str, prior: Optional[Dict] = None) -> str:
        """Path for a _review note. When ``prior`` points at an existing
        app-owned _review placeholder for the same URL, that SAME path is
        returned so the atomic write replaces it (no _v1/_v2 stacking)."""
        if prior and prior.get('note_path') \
                and self._is_review_path(prior['note_path']) \
                and os.path.exists(prior['note_path']) \
                and self._app_owns_old_note(canonical, prior['note_path']):
            return prior['note_path']
        from urllib.parse import urlparse
        host = urlparse(canonical).netloc or 'unknown'
        fname = safe_filename(host + urlparse(canonical).path)[:120] \
            or 'review'
        if not fname.lower().endswith('.md'):
            fname += '.md'
        return unique_path(os.path.join(self.vault_path, REVIEW_FOLDER, fname))

    @staticmethod
    def _is_review_path(path: str) -> bool:
        return bool(path) and (REVIEW_FOLDER in (path or '').replace('\\', '/'))

    # -- batch entry --------------------------------------------------------

    def run(self, urls: List[str], should_continue=None,
            on_progress=None) -> List[Dict]:
        """Process a batch of non-GitHub links (deduped upstream by the
        caller; dedupe is re-checked per link here anyway).
        ``should_continue`` (optional) is polled between links so a GUI
        Stop button can end the phase cleanly.
        ``on_progress(url)`` (v0.37.0, optional) fires once per link BEFORE
        it is processed — the batch's progress bar finally moves during a
        websites phase too (it used to be GitHub-loop-only, so a
        websites-only batch showed a frozen bar while notes were added).
        Within-batch duplicates count too: the caller's position advances
        for every link it handed us, mirroring the GitHub loop."""
        results = []
        seen = set()
        # v0.48.0 — the fourth door's pull: links whose hand-delivered
        # page is WAITING in the folder join the batch uninvited (the
        # owner's gesture — saving the page — says "process me now";
        # the batch must never sit on it waiting for a retry backoff).
        if self.hand_delivery and self.vault_path:
            try:
                ready = [d['url'] for d in _hand_delivery.collect_delivered(
                    self.vault_path)]
                ready = [u for u in ready
                         if normalize_website_url(u) not in
                         {normalize_website_url(x) for x in urls}]
                if ready:
                    self.log(f"🖐 Hand-delivery: {len(ready)} delivered "
                             f"page(s) waiting in the hand-delivered "
                             f"folder — they join this batch", "info")
                    urls = list(urls) + ready
            except Exception:
                pass  # the pull never breaks the batch
        for url in urls:
            if should_continue is not None and not should_continue():
                self.log("⏹️ Websites pipeline stopped by user — remaining "
                         "links stay queued for the next run", "warning")
                break
            if on_progress is not None:
                try:
                    on_progress(url)
                except Exception:
                    pass
            canonical = normalize_website_url(url)
            if canonical in seen:
                # Within-batch duplicate: REPORT it (never silently drop —
                # the run report's counts must add up).
                results.append({
                    'url': url, 'canonical': canonical, 'outcome': 'skipped',
                    'note_path': '', 'category': '', 'subcategory': '',
                    'fetch_status': '', 'error': 'duplicate within batch'})
                continue
            seen.add(canonical)
            # v0.63.2 — THE SETTLED LEDGER's gate: a link the owner
            # already sent to the bot and addressed is never fetched
            # again — whatever path delivered it here (the bot queue's
            # full history, a re-send in a new message, an import).
            # The owner's 🖐 hand outranks the settlement exactly as it
            # outranks the burned-out retry counter (v0.56.0): a
            # gesture row or a delivered page waiting in the folder
            # says "finish this one" and passes.
            try:
                if canonical and self.state.is_settled(canonical) \
                        and not self._hand_outranks_settlement(canonical):
                    result = {
                        'url': url, 'canonical': canonical,
                        'outcome': 'skipped', 'note_path': '',
                        'category': '', 'subcategory': '',
                        'fetch_status': '',
                        'error': ('settled — already sent to the bot and '
                                  'addressed (the owner\'s law); never '
                                  're-fetched')}
                    results.append(result)
                    self.last_results.append(result)
                    self.counters['skipped'] += 1
                    self.log(
                        f"🤝 {url}: settled — already addressed and "
                        f"processed; never fetched again (the owner's "
                        f"law; ♻️ revived in the master table "
                        f"un-settles it)", "info")
                    continue
            except Exception:
                pass  # a broken probe never hides a link
            results.append(self.process_link(url))
        self._refresh_master_table()
        return results

    def _hand_outranks_settlement(self, canonical: str) -> bool:
        """v0.63.2 — does the owner's 🖐 hand outrank the settlement for
        this link? The gesture on the master-table row, or a delivered
        page already waiting in the hand-delivered folder — the same
        probes the burned-out counter reborn uses (v0.56.0). Never
        raises; False on any doubt (the settlement holds)."""
        try:
            if self._row_reads_hand(canonical):
                return True
        except Exception:
            pass
        if self.hand_delivery and self.vault_path:
            try:
                return any(
                    _same_source(d.get('url') or '', canonical)
                    for d in _hand_delivery.collect_delivered(
                        self.vault_path))
            except Exception:
                return False
        return False

    def run_due_retries(self, should_continue=None, on_progress=None) -> List[Dict]:
        """Retry fetch-failed links whose backoff elapsed (SPEC §4.3:
        "retried automatically up to 3 times over several days"). Called by
        the batch BEFORE the fresh links. ``on_progress`` (v0.37.0) has
        the same per-link contract as :meth:`run`.

        v0.60.1 — the count's honesty: a due link whose verdict was
        already written (dismissed — the same gate process_link
        enforces) leaves the count BEFORE the pass, with the one
        honest line (:func:`split_dismissed_links`) instead of a
        skip pile inside it.

        v0.63.2 — THE SETTLED LEDGER: settled links never re-fetch
        (belt & braces — the settlement cleared the queue, so a
        settled URL here means one that re-entered against the law;
        :func:`split_settled_links` drops it with the honest line)."""
        due = self.state.due_retries()
        if not due:
            return []
        _due_kept, _due_dropped = split_dismissed_links(
            self.state, due, log=self.log)
        if _due_dropped:
            due = _due_kept
        _due_kept2, _due_settled = split_settled_links(
            self.state, due, log=self.log)
        if _due_settled:
            due = _due_kept2
        if not due:
            return []
        self.log(f"🔁 Retrying {len(due)} fetch-failed website link(s) "
                 "whose backoff elapsed", "info")
        results = []
        for u in due:
            if should_continue is not None and not should_continue():
                self.log("⏹️ Websites retry pass stopped by user", "warning")
                break
            if on_progress is not None:
                try:
                    on_progress(u)
                except Exception:
                    pass
            results.append(self.process_link(u))
        self.counters['retried'] = len(due)
        self._refresh_master_table()
        return results

    # -- v0.42.0: the _review backlog retry driver --------------------------

    def retry_review_backlog(self, items: List[Dict],
                             should_continue: Optional[Callable] = None,
                             on_progress: Optional[Callable] = None
                             ) -> List[Dict]:
        """v0.42.0 — retry the scanned ``_review`` backlog (the owner's
        ask: 100+ links walled off by 403/405 fetch refusals under the
        pre-v0.41 honest-bot User-Agent).

        ``items`` is what :func:`scan_review_backlog` returned. Per
        unique URL:

          * the whole retry queue is RE-ARMED first (attempts=0). The
            wall pile is exactly the links whose 3 automatic retries
            burned out under the OLD fetcher; without the re-arm
            process_link would skip them as "no more retries" forever.
            Same precedent as the proxy-epoch re-arm (v0.19.0).
          * a placeholder whose state row was LOST (cache.db rebuilt,
            note written by an older app) gets the row re-registered
            from the disk truth — so the link routes through the
            upgrade path in ONE run instead of "already in the vault".
          * :meth:`process_link` does the rest: fetch (v0.41's
            browser-grade presentation), extract, classify, analyze,
            atomic write — and on success the upgrade path replaces the
            old placeholder (SPEC §4.4's one-note-per-source rule).
          * any OTHER app-owned failed placeholder for the same source
            (legacy ``_v1``/``_v2`` stacking) is cleaned after the
            verdict: one source, one note.

        A link that fails AGAIN keeps its placeholder (refreshed in
        place) and re-enters the retry queue with a fresh set of 3 —
        nothing is silently dropped (SPEC §4.3 holds). Returns the
        per-link result dicts (same shape as :meth:`run`)."""
        results: List[Dict] = []
        if not items:
            return results
        try:
            rearmed = self.state.rearm_retries()
            if rearmed:
                self.log(
                    f"🔁 _review backlog retry: re-armed {rearmed} queued "
                    f"retry(ies) — their 3 attempts burned out under the "
                    f"old fetcher; every link gets a fresh set", "info")
        except Exception as e:  # bookkeeping never kills the batch
            self.log(f"⚠️ Retry re-arm skipped: {e}", "warning")
        # Consolidate the scan: one source may own several placeholder
        # files; process the URL once, clean the leftovers after the
        # verdict (dict order = scan order = sorted filenames).
        by_url: Dict[str, List[str]] = {}
        for it in items:
            by_url.setdefault(it.get('url') or '', []).append(
                it.get('path') or '')
        by_url.pop('', None)
        # v0.63.2 — THE SETTLED LEDGER: settled placeholders never
        # re-fetch (the owner's law — already sent to the bot and
        # addressed); the 🖐 gesture outranks the settlement (v0.56.0's
        # law, the same exemption run() gives).
        try:
            _settled_keys = [
                u for u in by_url
                if (normalize_website_url(u) or '')
                and self.state.is_settled(normalize_website_url(u))
                and not self._hand_outranks_settlement(
                    normalize_website_url(u))]
        except Exception:
            _settled_keys = []
        if _settled_keys:
            for u in _settled_keys:
                by_url.pop(u, None)
            self.log(
                f"🤝 {len(_settled_keys)} settled link(s) left the "
                f"_review backlog retry — already sent to the bot and "
                f"addressed; never fetched again (♻️ revived in the "
                f"master table un-settles any of them)", "info")
        self.counters['retried'] = self.counters.get('retried', 0) \
            + len(by_url)
        for url, paths in by_url.items():
            if should_continue is not None and not should_continue():
                self.log("⏹️ _review backlog retry stopped by user — the "
                         "remaining placeholders keep waiting", "warning")
                break
            if on_progress is not None:
                try:
                    on_progress(url)
                except Exception:
                    pass
            canonical = normalize_website_url(url)
            try:
                prior = self.state.processed_row(canonical)
            except Exception:
                prior = None
            if prior is None and paths:
                # State row lost — the note on disk is the proof. Register
                # the row the older app SHOULD have written (a failed
                # placeholder at this path) so process_link routes this
                # link through the upgrade path, not "already in the
                # vault". Never clobbers an existing row (prior is None).
                try:
                    self.state.mark_processed(
                        canonical, paths[0], '', '', 'failed')
                    if self.state.retry_row(canonical) is None:
                        self.state.enqueue_retry(
                            canonical,
                            're-discovered _review placeholder (state row '
                            'was lost)')
                    self.log(
                        f"🗂️ {url}: state row was lost — re-registered its "
                        f"_review placeholder before retrying", "info")
                except Exception as e:
                    self.log(f"⚠️ Row re-registration skipped for {url}: "
                             f"{e}", "warning")
            r = self.process_link(url)
            fresh_path = r.get('note_path') or ''
            outcome = r.get('outcome')
            error = r.get('error') or ''
            # Stale duplicate cleanup: only when THIS run produced a note
            # (processed / review) or the link is already stored for real
            # ("already in the websites vault") — never on a dismissed or
            # blocked skip (those contracts forbid touching the files).
            if outcome in ('processed', 'review') \
                    or (outcome == 'skipped'
                        and 'already in' in error):
                for p in paths:
                    if p and p != fresh_path:
                        self._remove_scanned_placeholder(p)
            results.append(r)
        self._refresh_master_table()
        return results

    # -- v0.51.0: the master-table waiting pass ----------------------------

    def retry_master_waiting(self, should_continue: Optional[Callable] = None,
                             on_progress: Optional[Callable] = None
                             ) -> List[Dict]:
        """v0.51.0 — the caught-up check's own retry pass.

        The owner's report (session, verbatim): "Currently app says
        everything is uptodate, but actually app must look at
        decomissioned note, and try to fetch again those that dont
        have skeleton or red cross or such emojies and have this state
        ' - ' … so before declaring everything is uptodate it must
        check 'decomissioned' note and find those that should be
        retried." This driver IS that check: the " - " rows of the
        master table (no verdict emoji, no decision — the link is
        valid, its fetch failed) are fetched AGAIN before any
        "everything is up to date" may be said.

        Per row (``scan_master_waiting_rows`` with this pipeline's own
        state — retired and already-stored links never appear):

          * a link whose 3 automatic retries burned out gets its
            counter reborn (:meth:`WebsiteStateDB.reset_retry_attempts`
            — ONE row, not the whole queue) so :meth:`process_link`
            fetches it instead of skipping "no more retries" forever;
          * :meth:`process_link` does the rest — the full pipeline
            (fetch → extract → classify → analyze → store), the
            upgrade path replacing the placeholder on success, the
            re-failure refreshing it in place;
          * the "eyes" rows (low-confidence / analysis / archived
            notes waiting for the OWNER's move) are never fetched —
            only counted and named.

        Ends with the honest verdict — a re-scan of the table: what
        still waits is said out loud ("N row(s) still wait — the doors
        are 🖐 hand / the Chrome tab-retry / the Status cell"), so
        "everything is up to date" is only ever true when no " - "
        fetch row remains. Returns the per-link result dicts (the
        same shape as :meth:`run` — the end-of-run Chrome-tab offer
        reads them)."""
        results: List[Dict] = []
        if not self.vault_path:
            return results
        items = scan_master_waiting_rows(self.vault_path, state=self.state,
                                         log=self.log)
        fetch_items = [it for it in items if it.get('kind') == 'fetch']
        eyes = [it for it in items if it.get('kind') == 'eyes']
        if not fetch_items:
            if eyes:
                self.log(
                    f"📋 Master table: no ' - ' fetch row is waiting — "
                    f"{len(eyes)} row(s) wait for YOUR eyes (set ✅ "
                    f"reviewed or 🪦 dead in the table; ♻️ revived "
                    f"re-fetches)", "info")
            else:
                self.log(
                    "✅ Master table: no ' - ' row is waiting — the "
                    "table agrees with 'everything is up to date'",
                    "success")
            return results
        self.log(
            f"🔁 Master table: {len(fetch_items)} ' - ' row(s) waiting "
            f"(valid link, failed fetch, no verdict) — fetching them "
            f"again before 'everything is up to date' is said",
            "info")
        self.counters['retried'] = self.counters.get('retried', 0) \
            + len(fetch_items)
        for it in fetch_items:
            if should_continue is not None and not should_continue():
                self.log("⏹️ Master-table waiting pass stopped by user — "
                         "the remaining rows keep waiting", "warning")
                break
            if on_progress is not None:
                try:
                    on_progress(it.get('url') or '')
                except Exception:
                    pass
            url = it.get('url') or ''
            canonical = normalize_website_url(url)
            # A burned-out retry row would be skipped as "no more
            # retries" — the owner's " - " says otherwise: reborn.
            try:
                if canonical and self.state.retry_row(canonical) is not None:
                    self.state.reset_retry_attempts(canonical)
            except Exception as e:
                self.log(f"⚠️ Retry re-arm skipped for {url}: {e}",
                         "warning")
            results.append(self.process_link(url))
        self._refresh_master_table()
        # the honest verdict — a re-scan says what still waits
        try:
            after = scan_master_waiting_rows(self.vault_path,
                                             state=self.state)
            still = [a for a in after if a.get('kind') == 'fetch']
            eyes_after = [a for a in after if a.get('kind') == 'eyes']
        except Exception:
            still, eyes_after = [], []
        if still:
            self.log(
                f"⚠️ Master table: {len(still)} ' - ' row(s) still wait — "
                f"the machine doors had their say; the rest is yours: "
                f"set the Status (🪦 dead / ✅ reviewed), queue 🖐 hand, "
                f"or retry in your real Chrome (the end-of-run offer / "
                f"More ▸ 🤖 Chrome tab-retry)", "warning")
        else:
            self.log(
                "✅ Master table: every ' - ' fetch row got its retry — "
                "the table is honest now", "success")
        if eyes_after:
            self.log(
                f"📋 {len(eyes_after)} row(s) wait for YOUR eyes (set ✅ "
                f"or 🪦 in the table; ♻️ revived re-fetches)", "info")
        return results

    def _refresh_master_table(self) -> None:
        """v0.47.0 — one call at every driver's end (run /
        run_due_retries / retry_review_backlog): the master table at
        ``<vault>/_review/DECOMMISSIONED.md`` gets every waiting failure
        and every auto-verdict retirement as rows — the owner finally
        GETS the table (it existed only when the manual picker had been
        used before). Tolerated everywhere; the dry-run law applies
        through the writer."""
        if not self.vault_path:
            return
        try:
            refresh_master_table(self.state, self.vault_path,
                                 log=self.log)
        except Exception as e:  # bookkeeping never kills a batch
            self.log(f"⚠️ Master table refresh skipped: {e}", "warning")

    def _remove_scanned_placeholder(self, path: str) -> None:
        """Remove one scanned _review placeholder — but ONLY while it
        still reads as an app-owned fetch-failed note (the scan's own
        test, re-read at removal time, so a hand-edit that happened in
        between is respected — same law as
        _cleanup_replaced_review_note). Dry-run aware: every file
        mutation in the pipeline goes through core/dryrun."""
        if not path or not os.path.exists(path):
            return
        if not self._is_review_path(path):
            return
        fm = _parse_review_frontmatter(path)
        if not fm \
                or fm.get('managed_by', '').lower() != MANAGED_BY_GITCURATOR \
                or fm.get('fetch_status', '').lower() != 'failed':
            self.log(
                f"⚠️ kept {os.path.basename(path)} — it no longer reads as "
                f"an app-owned failed placeholder (hand-edited?)",
                "warning")
            return
        try:
            _dryrun.remove(path)
            self.log(f"🧹 removed stale _review placeholder "
                     f"{os.path.basename(path)}", "info")
        except Exception as e:
            self.log(f"⚠️ could not remove {path}: {e}", "warning")
