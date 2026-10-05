#!/usr/bin/env python3
"""
website_directory.py — THE LAW sweep + the categorized Website Directory
(v0.28.0).

Two jobs, both run-side, both idempotent, both dry-run aware:

1. ``sweep_banned_notes`` — enforce the owner's law ("EVERY X And GITHUB
   domain (all of its group) must be banned from showing on websites
   directory … Hugginface, Github, Twitter (X), Instagram, Facebook,
   Linkedin" — extended at v0.35.0 with YouTube, the Google
   share/drive/docs family and the social-media majors) against the vault
   ON DISK, not just the intake: every APP-OWNED note whose ``source:``
   frontmatter is on a banned domain is moved to
   ``<vault>/.trash/banned-domains/`` (Obsidian's hidden trash;
   VaultSeal never backs it up, VaultIndex/mirror/note_state never read
   it). Hand-written notes are never touched — the user's own content is
   sacred; they are counted and reported instead. This is what cleans
   the legacy ``x_com_i_status_*.md`` review pile written before the law
   existed — the first run after updating moves it out, once, forever.
   v0.35.0 — the law's PLATFORM tables under ``_inbox`` leave too: a
   platform whose every domain is banned (X/Twitter, YouTube, LinkedIn,
   HuggingFace) is never collected anymore, so its review-queue table is
   quarantined to the same .trash folder instead of lingering as a
   dead "collection" the owner would have to curate by hand.

2. ``build_website_directory`` — regenerate ``<vault>/000 📚 Website
   Directory.md``: the consolidated, categorized, clickable index the
   owner asked for ("I'm looking for color-palette makers — I can easily
   find them … click on their link, knowing a short description").
   Categories in taxonomy order → subcategories → one line per site:
   a wiki-link to the note (alias = its title), the TL;DR one-liner,
   the pricing word when known, and a ↗ external link. Rebuilt after
   every websites batch; the file is app-owned (``kind: directory``) so
   overwriting it never violates the never-rewrite-user-notes rule.

No PyQt. No network. Stdlib only — importable from the GUI worker, the
headless CLI, the backfill tool, and the CI gate.
"""

import os
import re
from datetime import datetime
from typing import Dict, List, Optional

from gitcurator.constants import MANAGED_BY_GITCURATOR
from gitcurator.core import dryrun as _dryrun
from gitcurator.core import links as _links
from gitcurator.core.storage import atomic_write_text, unique_path

# The directory note (app-owned; sorts to the TOP of the vault so it is
# the first thing the owner sees when the vault opens).
DIRECTORY_FILENAME = "000 📚 Website Directory.md"

# Where swept (banned) notes go — inside Obsidian's hidden .trash so they
# disappear from the vault view but stay recoverable by hand.
QUARANTINE_RELPATH = os.path.join(".trash", "banned-domains")

# Folders the DIRECTORY walk never enters (system/record folders +
# the pipeline's own queues — they are not library notes).
_SKIP_DIRS = {"_inbox", "_review", "_missing", "_moc", "attachments",
              ".obsidian", ".trash", ".git"}

# The SWEEP is broader: it ENTERS _review/_missing/_moc (the legacy
# banned-domain pile lives in _review — the owner's screenshot) and
# only skips the dot-folders. The _inbox NOTE walk is skipped (the
# tables are handled separately below — v0.35.0 quarantines the banned
# platforms' tables whole).
_SKIP_DIRS_SWEEP = {"_inbox", ".obsidian", ".trash", ".git"}

# Frontmatter keys the parsers look for (light regex — notes are small).
_FM_LINE_RE = re.compile(r'^([A-Za-z0-9_]+):\s*(.*?)\s*$')
_TLDR_RE = re.compile(r'^>\s*\*\*TL;DR:\*\*\s*(.+?)\s*$', re.MULTILINE)
_H1_RE = re.compile(r'^#\s+(.+?)\s*$', re.MULTILINE)

# TL;DR trimming for directory lines.
_TLDR_MAX = 140
_TITLE_MAX = 80


def _strip_yaml_quotes(value: str) -> str:
    """Strip one matching pair of quotes the note writers may add."""
    v = (value or '').strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
        return v[1:-1]
    return v


def _parse_note(path: str) -> Optional[Dict]:
    """Light frontmatter + TL;DR + H1 parse of one note file.

    Returns ``None`` when the file has no frontmatter at all (not a
    GitCurator-shaped note). OWNERSHIP is the caller's decision: the
    dict carries ``app_owned`` (``managed_by: gitcurator``) so the sweep
    can move app-owned notes while counting hand-written ones, and the
    directory builder can index only what the app wrote. Never raises."""
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            text = f.read(512_000)
    except OSError:
        return None
    if not text.startswith('---'):
        return None
    # '---\n<fm lines>\n---\n<body>' — the opening '---' has no newline
    # before it, so split('\n---') puts the frontmatter in parts[0] and
    # the body (which may itself contain a '\n---' rule line) after it.
    parts = text.split('\n---', 2)
    if len(parts) < 2:
        return None
    fm = {}
    for line in parts[0].splitlines():
        m = _FM_LINE_RE.match(line)
        if m:
            fm[m.group(1)] = _strip_yaml_quotes(m.group(2))
    # Rebuild the body: parts[1] (+ the removed '\n---' + parts[2] when
    # the body carries its own '---' rule line before the source link).
    body = parts[1]
    if len(parts) > 2:
        body = parts[1] + '\n---' + parts[2]
    tldr_m = _TLDR_RE.search(body)
    tldr = tldr_m.group(1).strip() if tldr_m else ''
    if tldr in ('\u2014', '-', ''):
        tldr = ''
    h1_m = _H1_RE.search(body)
    title = h1_m.group(1).strip() if h1_m else ''
    return {
        'app_owned': fm.get('managed_by') == MANAGED_BY_GITCURATOR,
        'source': fm.get('source', ''),
        'category': fm.get('category', ''),
        'subcategory': fm.get('subcategory', ''),
        'pricing': fm.get('pricing', 'unknown'),
        'fetch_status': fm.get('fetch_status', ''),
        'tldr': tldr,
        'title': title,
    }


def _iter_note_files(vault_path: str, for_sweep: bool = False):
    """Yield every .md under ``vault_path`` except the system folders,
    sorted for deterministic output. ``for_sweep=True`` walks the
    pipeline's own queues too (_review holds the legacy banned pile) —
    only the _inbox record tables and dot-folders are always skipped."""
    skip = _SKIP_DIRS_SWEEP if for_sweep else _SKIP_DIRS
    if not vault_path or not os.path.isdir(vault_path):
        return
    for root, dirs, files in os.walk(vault_path):
        dirs[:] = sorted(d for d in dirs
                         if d not in skip and not d.startswith('.'))
        for name in sorted(files):
            if name.lower().endswith('.md') and name != DIRECTORY_FILENAME:
                yield os.path.join(root, name)


def _count_review_notes(vault_path: str) -> int:
    """How many notes wait in the pipeline's _review folder."""
    review_dir = os.path.join(vault_path, '_review')
    if not os.path.isdir(review_dir):
        return 0
    try:
        return len([n for n in os.listdir(review_dir)
                    if n.lower().endswith('.md')])
    except OSError:
        return 0


# ---------------------------------------------------------------------------
# 1. THE LAW — the banned-domain sweep
# ---------------------------------------------------------------------------

def sweep_banned_notes(vault_path: str, blocked_domains: List[str],
                       log=None) -> Dict:
    """Move every app-owned note on a banned domain out of the vault's
    library into ``<vault>/.trash/banned-domains/``.

    Only APP-OWNED notes move (``managed_by: gitcurator`` in the
    frontmatter). Hand-written notes with a banned ``source:`` are the
    owner's — counted, reported, never touched.

    v0.35.0 — after the note walk, the banned PLATFORMS' _inbox tables
    (``_inbox/x_twitter_links.md``, ``youtube_links.md``, …) are
    quarantined to the same ``.trash/banned-domains/`` folder: the
    owner's omission rule ("must not collect youtube links for note or
    review, same for X, and hugging face") retires those collections
    entirely. A table moves only when EVERY domain of its platform is
    on the ban list (partially-banned platforms — and 'other', which is
    not a platform — keep their tables; their banned ROWS are pruned by
    the intake writer's prune pass instead).

    Dry-run aware (moves are recorded, not performed). Idempotent — the
    quarantine folder is skipped by the walk. Returns
    ``{'moved': [(url, src, dst)], 'kept_handwritten': n, 'scanned': n,
    'moved_tables': [(platform, src, dst)]}``.
    """
    log = log or (lambda *a, **k: None)
    report = {'moved': [], 'kept_handwritten': 0, 'scanned': 0,
              'moved_tables': []}
    if not vault_path or not os.path.isdir(vault_path):
        return report
    if not blocked_domains:
        return report
    quarantine_dir = os.path.join(vault_path, QUARANTINE_RELPATH)
    for path in _iter_note_files(vault_path, for_sweep=True):
        report['scanned'] += 1
        note = _parse_note(path)
        if note is None:
            continue
        url = note.get('source') or ''
        if not url:
            continue
        if not _links.domain_is_blocked(url, blocked_domains):
            continue
        rel = os.path.relpath(path, vault_path)
        if not note.get('app_owned'):
            # The owner's own writing — sacred. Counted + reported only.
            report['kept_handwritten'] += 1
            continue
        # Every app-owned banned note leaves the library — _review
        # placeholders AND real notes alike (the law has no "but it
        # processed fine" exception).
        dst = unique_path(os.path.join(quarantine_dir,
                                       os.path.basename(path)))
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            _dryrun.makedirs(quarantine_dir, exist_ok=True)
            _dryrun.write_text(dst, content)
            _dryrun.remove(path)
        except OSError as e:
            log(f"⚠️ Law sweep could not move {rel}: {e}", "warning")
            continue
        report['moved'].append((url, path, dst))
    if report['moved']:
        log(f"🧹 Law sweep: moved {len(report['moved'])} banned-domain "
            f"note(s) to {QUARANTINE_RELPATH.replace(os.sep, '/')} — the "
            f"manifest's blocked bucket is the record", "info")
    if report['kept_handwritten']:
        log(f"✍️ Law sweep: {report['kept_handwritten']} hand-written "
            f"note(s) with banned sources kept — they are yours; delete "
            f"them by hand if you want them gone", "info")
    # v0.35.0 — the banned platforms' _inbox tables are collections of
    # links the app must never collect anymore; quarantine them whole.
    # (Row-level pruning for the remaining tables happens in the intake
    # writer's prune pass — gui/platform_intake.prune_inbox_tables.)
    _banned = {str(d).strip().lower().lstrip('.') for d in blocked_domains}
    _banned.discard('')
    for platform, table_file in _links.PLATFORM_TABLE_FILES.items():
        domains = [str(d).strip().lower().lstrip('.')
                   for d in _links.PLATFORM_DOMAINS.get(platform, ())]
        if not domains or not all(d in _banned for d in domains):
            continue   # not a platform, or partially banned → keep the table
        src = os.path.join(vault_path, '_inbox', table_file)
        if not os.path.isfile(src):
            continue
        dst = unique_path(os.path.join(quarantine_dir, table_file))
        try:
            with open(src, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            _dryrun.makedirs(quarantine_dir, exist_ok=True)
            _dryrun.write_text(dst, content)
            _dryrun.remove(src)
        except OSError as e:
            log(f"⚠️ Law sweep could not move _inbox/{table_file}: {e}",
                "warning")
            continue
        report['moved_tables'].append((platform, src, dst))
    if report['moved_tables']:
        _names = ', '.join(sorted(f"_inbox/{os.path.basename(s)}"
                                  for _, s, _ in report['moved_tables']))
        log(f"🧹 Law sweep: quarantined {len(report['moved_tables'])} "
            f"banned-platform table(s) ({_names}) — those platforms are "
            f"never collected (notes or review) anymore", "info")
    return report


# ---------------------------------------------------------------------------
# 2. The Website Directory — the consolidated categorized index
# ---------------------------------------------------------------------------

def _trim(text: str, limit: int) -> str:
    """Trim to ``limit`` chars at a word boundary with an ellipsis."""
    text = (text or '').strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(' ', 1)[0]
    return (cut or text[:limit]).rstrip(' ,;:.-') + '…'


def _alias_safe(title: str) -> str:
    """Strip the characters that break a wiki-link ALIAS."""
    for ch in '[]|^#':
        title = title.replace(ch, '')
    return title.strip()


def _category_order(names_in_use, taxonomy) -> List[str]:
    """Taxonomy order first, then any leftover/unknown names (e.g. from an
    older taxonomy) alphabetically."""
    ordered = []
    if taxonomy is not None:
        for c in taxonomy.category_names:
            if c in names_in_use:
                ordered.append(c)
    leftovers = sorted(n for n in names_in_use if n not in ordered
                       and (n or '').strip())
    return ordered + leftovers + ([None] if None in names_in_use else [])


def build_website_directory(vault_path: str, taxonomy=None,
                            config: Optional[dict] = None,
                            log=None) -> Optional[Dict]:
    """(Re)generate ``<vault>/000 📚 Website Directory.md``.

    Groups every app-owned website note by its frontmatter category →
    subcategory (taxonomy order, leftovers last) and writes one line per
    site: a wiki-link to the note, the TL;DR one-liner, the pricing word
    when known, and a ↗ link to the live site. Notes waiting in
    ``_review`` are not listed — they are counted in the footer callout
    so nothing looks silently missing.

    The directory file itself is app-owned (``kind: directory``) and has
    no ``source:`` line, so the VaultIndex dedupe layer never sees it.
    Returns ``{'path', 'sites', 'categories', 'review'}`` or ``None``
    when the vault is missing/empty (nothing to index yet).
    """
    log = log or (lambda *a, **k: None)
    if not vault_path or not os.path.isdir(vault_path):
        return None
    if taxonomy is None and config is not None:
        try:
            from gitcurator.core.taxonomy import load_taxonomy_from_config
            taxonomy = load_taxonomy_from_config(config)
        except Exception:
            taxonomy = None

    # Collect: category -> subcategory -> [(title, path, note), ...]
    tree: Dict[str, Dict[str, List[Dict]]] = {}
    sites = 0
    blocked = _links.blocked_domains_from_config(config or {})
    for path in _iter_note_files(vault_path):
        note = _parse_note(path)
        if note is None or not note.get('app_owned'):
            continue          # only the app's own notes are indexed
        url = note.get('source') or ''
        if not url:
            continue
        if _links.domain_is_blocked(url, blocked):
            continue          # the law: never listed, whatever it is
        sites += 1
        cat = (note.get('category') or '').strip() or 'Uncategorized'
        sub = (note.get('subcategory') or '').strip()
        tree.setdefault(cat, {}).setdefault(sub, []).append({
            'title': note.get('title') or os.path.splitext(
                os.path.basename(path))[0],
            'relpath': os.path.relpath(path, vault_path),
            'url': url,
            'tldr': note.get('tldr', ''),
            'pricing': note.get('pricing', 'unknown'),
        })

    if not tree:
        return None

    lines = []
    lines.append("---")
    lines.append(f'managed_by: "{MANAGED_BY_GITCURATOR}"')
    lines.append('kind: "directory"')
    lines.append(f'generated: "{datetime.now().strftime("%Y-%m-%d")}"')
    lines.append("---")
    lines.append("")
    lines.append("# 📚 Website Directory")
    lines.append("")
    n_cats = len([c for c in tree if tree[c]])
    lines.append(f"> [!info] Auto-generated index of the curated library — "
                 f"{sites} website(s) across {n_cats} categor(ies). Click a "
                 f"name to open its note (details inside); ↗ opens the site "
                 f"in your browser.")
    lines.append("")

    for cat in _category_order({c for c in tree if tree[c]}, taxonomy):
        subs = tree[cat]
        cat_total = sum(len(v) for v in subs.values())
        lines.append(f"## {cat} ({cat_total})")
        lines.append("")
        # Subcategories in taxonomy order, then the empty-key group last.
        sub_names = set(subs)
        ordered_subs = []
        if taxonomy is not None and cat != 'Uncategorized':
            for s in taxonomy.subcategories_of(cat):
                if s in sub_names:
                    ordered_subs.append(s)
        ordered_subs += sorted(s for s in sub_names
                               if s not in ordered_subs and s)
        if '' in sub_names:
            ordered_subs.append('')
        for sub in ordered_subs:
            entries = sorted(subs[sub], key=lambda e: (
                e['title'].lower(), e['relpath'].lower()))
            if sub:
                lines.append(f"### {sub} ({len(entries)})")
                lines.append("")
            for e in entries:
                link = e['relpath'].replace('\\', '/')
                link = link[:-3] if link.lower().endswith('.md') else link
                alias = _alias_safe(_trim(e['title'], _TITLE_MAX)) \
                    or 'Untitled'
                parts = [f"[[{link}|{alias}]]"]
                if e['tldr']:
                    parts.append(_trim(e['tldr'], _TLDR_MAX))
                if e['pricing'] in ('free', 'freemium', 'paid'):
                    parts.append(e['pricing'])
                parts.append(f"[↗]({e['url']})")
                lines.append("- " + " — ".join(p for p in parts if p))
            lines.append("")

    review = _count_review_notes(vault_path)
    if review:
        lines.append(f"> [!todo] 📥 {review} link(s) still waiting in "
                     f"`_review/` (fetch failed / needs a look) — they "
                     f"join this directory once processed.")
        lines.append("")
    lines.append("*Regenerated automatically after every curation run — "
                 "edits here are overwritten.*")

    out_path = os.path.join(vault_path, DIRECTORY_FILENAME)
    try:
        _dryrun.makedirs(vault_path, exist_ok=True)
        atomic_write_text(out_path, "\n".join(lines) + "\n")
    except OSError as e:
        log(f"⚠️ Website Directory could not be written: {e}", "warning")
        return None
    return {'path': out_path, 'sites': sites, 'categories': n_cats,
            'review': review}
