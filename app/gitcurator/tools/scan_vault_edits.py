#!/usr/bin/env python3
"""
scan_vault_edits.py — read-only vault scan (Phase 0 groundwork, v0.09.5).

Answers four questions about an Obsidian vault, WITHOUT changing a single
byte inside it:

  1. How many notes exist per category folder?
  2. Which notes are missing the ``source:`` frontmatter line (the app's
     identity key for every note)?
  3. Which notes share the same ``source:`` URL (duplicates)?
  4. Which notes have human-written content in the three LEGACY manual
     sections ("My Ideas & Notes", "Social Signal (Manual)", "Journal" —
     present in notes created before v0.10.0) — i.e. anything beyond the
     template placeholder text the app used to write?

A readable Markdown report is written OUTSIDE the vault (default:
``app/reports/scan/``). The tool refuses to write anything inside the
vault — it is read-only by construction.

Usage (from the app/ folder, or from anywhere via -m):
    python tools/scan_vault_edits.py "C:\\path\\to\\vault"
    python tools/scan_vault_edits.py "C:\\path\\to\\vault" --out "D:\\reports"
    python -m gitcurator.tools.scan_vault_edits "C:\\path\\to\\vault"

Windows-friendly: os.path only, every file opened with an explicit UTF-8
encoding, no symlinks. Pure standard library.
"""

import argparse
import os
import sys
from datetime import datetime

# ---------------------------------------------------------------------------
# Bootstrap: make the app/ folder importable no matter how we are launched
# (python tools/xxx.py, python -m gitcurator.tools.xxx, or a unittest import)
# ---------------------------------------------------------------------------
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)

from gitcurator.constants import APP_DIR, CATEGORY_FOLDERS
from gitcurator.core.links import normalize_url

# ===========================================================================
# CONFIGURATION (safe to edit)
# ===========================================================================
# Default folder for reports — always OUTSIDE the vault (inside the app
# folder, next to config.json).
DEFAULT_REPORT_DIR = os.path.join(APP_DIR, "reports", "scan")

# Folders VaultIndex also skips (substring match on the walked path,
# mirroring gui/app.py exactly). _review is deliberately NOT skipped —
# those are real notes.
SKIP_SUBSTRINGS = ['_moc', '_inbox', 'attachments', '.obsidian']

# The three human sections of a LEGACY GitHub note (created before v0.10.0),
# with the EXACT placeholder text such a note contained (see the v0.09.5
# core/note_builder.py build_note). Anything other than this text counts as
# human-written content.
#
# v0.10.0 — Phase 1: NEW notes no longer contain these sections (that
# writing moves to the owner's Manual Notes vault, SPEC §4.5), so on a
# freshly-built vault the scan reports the sections as 'missing' — which is
# the expected, handled state. The strings stay because every vault built
# before v0.10.0 (the owner's real 600+ notes) still has them.
HUMAN_SECTIONS = [
    ("## 💡 My Ideas & Notes",
     "[Add your personal thoughts here]"),
    ("## 📱 Social Signal (Manual)",
     "- **Source:** [Dropdown: Reddit/X/Instagram/GitHub Search/Other]\n"
     "- **Link:** [URL]\n"
     "- **Notes:** [Context]"),
    ("## 📔 Journal",
     "[Date] - [Your experiences]"),
]

# How much of the human-written content to quote in the report.
EXCERPT_CHARS = 120
# ===========================================================================


def _is_inside(child: str, parent: str) -> bool:
    """True when path ``child`` lies inside folder ``parent`` (or equals it)."""
    try:
        child_r = os.path.realpath(child)
        parent_r = os.path.realpath(parent)
        return os.path.commonpath([child_r, parent_r]) == parent_r
    except (ValueError, OSError):
        return False


def _folder_to_category(rel_posix: str):
    """Reverse-lookup: which category key does a note's folder belong to?

    CATEGORY_FOLDERS maps category key -> folder path (e.g.
    "Agents/Frameworks" -> "AI-Domain/Agents/Frameworks"). The LONGEST
    matching folder wins so nested categories resolve correctly.
    Returns ("_review",) for _review, ("",) when unmapped, else the key.
    """
    if rel_posix == "_review" or rel_posix.startswith("_review/"):
        return "_review"
    best = ""
    best_len = -1
    for key, folder in CATEGORY_FOLDERS.items():
        folder_norm = folder.replace("\\", "/").strip("/")
        if not folder_norm:
            continue
        if (rel_posix == folder_norm
                or rel_posix.startswith(folder_norm + "/")):
            if len(folder_norm) > best_len:
                best = key
                best_len = len(folder_norm)
    return best


def _extract_section_body(content: str, heading: str):
    """Lines between ``heading`` and the next '## ' heading / '---' / EOF.

    Returns None when the heading is missing entirely. Empty edges are
    trimmed. Comparison-relevant whitespace is normalized by the caller.
    """
    lines = content.split('\n')
    start = None
    for i, ln in enumerate(lines):
        if ln.strip() == heading:
            start = i + 1
            break
    if start is None:
        return None
    body = []
    for ln in lines[start:]:
        s = ln.strip()
        if s.startswith('## ') or s == '---':
            break
        body.append(ln.rstrip())
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    return body


def _section_status(body, placeholder: str):
    """'missing' | 'template' | 'custom' for one human section."""
    if body is None:
        return 'missing'
    want = [l.strip() for l in placeholder.split('\n')]
    got = [l.strip() for l in body]
    return 'template' if got == want else 'custom'


def scan_vault(vault_path: str) -> dict:
    """Scan a vault read-only. Returns a plain dict of findings.

    Never raises for individual bad notes — a note that cannot be read is
    counted as unreadable and the scan continues.
    """
    vault_path = os.path.abspath(vault_path)
    result = {
        'vault_path': vault_path,
        'scanned_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'total_md_files': 0,
        'notes_scanned': 0,
        'skipped_files': 0,
        'unreadable_files': 0,
        'notes_per_category': {},      # category key -> count
        'review_notes': 0,
        'unmapped_folders': {},        # folder -> count
        'missing_source': [],          # list of relpaths
        'duplicate_sources': [],       # list of {url, paths}
        'human_edits': [],             # list of {path, sections:[{name,status,excerpt}]}
        'template_only_notes': 0,
    }
    source_map = {}  # normalized source url -> [relpath, ...]

    for root, dirs, files in os.walk(vault_path):
        # Mirror VaultIndex's skip rule exactly (substring on the path).
        if any(skip in root for skip in SKIP_SUBSTRINGS):
            result['skipped_files'] += sum(1 for f in files if f.endswith('.md'))
            continue
        for fname in files:
            if not fname.endswith('.md'):
                continue
            result['total_md_files'] += 1
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, vault_path)
            rel_posix = rel.replace(os.sep, '/')
            try:
                with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
            except Exception:
                result['unreadable_files'] += 1
                continue

            # -- category bucket ------------------------------------------------
            folder_posix = os.path.dirname(rel_posix)
            cat = _folder_to_category(folder_posix)
            if cat == '_review':
                result['review_notes'] += 1
            elif cat:
                result['notes_per_category'][cat] = \
                    result['notes_per_category'].get(cat, 0) + 1
            else:
                result['unmapped_folders'][folder_posix or '(vault root)'] = \
                    result['unmapped_folders'].get(folder_posix or '(vault root)', 0) + 1
            result['notes_scanned'] += 1

            # -- source: frontmatter -------------------------------------------
            # Same extraction rule as VaultIndex (first match, quotes stripped).
            source_url = None
            for ln in content.split('\n')[:40]:
                s = ln.strip()
                if s.startswith('source:'):
                    source_url = s[len('source:'):].strip().strip('"\'')
                    break
            if not source_url:
                result['missing_source'].append(rel_posix)
            else:
                norm = normalize_url(source_url)
                source_map.setdefault(norm, []).append(rel_posix)

            # -- human sections --------------------------------------------------
            sections = []
            has_custom = False
            for heading, placeholder in HUMAN_SECTIONS:
                body = _extract_section_body(content, heading)
                status = _section_status(body, placeholder)
                if status == 'custom':
                    has_custom = True
                    text = ' '.join(l.strip() for l in body if l.strip())
                    excerpt = text[:EXCERPT_CHARS] + ('…' if len(text) > EXCERPT_CHARS else '')
                    sections.append({
                        'name': heading,
                        'status': status,
                        'excerpt': excerpt or '(section present but emptied)',
                    })
                else:
                    sections.append({'name': heading, 'status': status, 'excerpt': ''})
            if has_custom:
                result['human_edits'].append({'path': rel_posix, 'sections': sections})
            else:
                result['template_only_notes'] += 1

    for url, paths in source_map.items():
        if len(paths) > 1:
            result['duplicate_sources'].append({'url': url, 'paths': sorted(paths)})
    result['duplicate_sources'].sort(key=lambda d: d['url'])
    return result


def render_markdown(result: dict) -> str:
    """Render scan findings as a readable Markdown report."""
    r = result
    vault_name = os.path.basename(r['vault_path']) or r['vault_path']
    lines = []
    lines.append(f"# Vault scan — {vault_name}")
    lines.append("")
    lines.append(f"*Scanned: {r['scanned_at']} · tool: `tools/scan_vault_edits.py` "
                 "(read-only — nothing inside the vault was changed)*")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Notes scanned (category folders + _review): **{r['notes_scanned']}**")
    lines.append(f"- Files skipped (_moc / _inbox / attachments / .obsidian): {r['skipped_files']}")
    if r['unreadable_files']:
        lines.append(f"- ⚠️ Files that could not be read: {r['unreadable_files']}")
    lines.append(f"- Notes missing `source:`: **{len(r['missing_source'])}**")
    lines.append(f"- Duplicate `source:` URLs: **{len(r['duplicate_sources'])}**")
    lines.append(f"- Notes with human-written content: **{len(r['human_edits'])}** "
                 f"({r['template_only_notes']} contain only the untouched template)")
    lines.append("")

    lines.append("## Notes per category")
    lines.append("")
    if r['notes_per_category'] or r['review_notes'] or r['unmapped_folders']:
        lines.append("| Category | Notes |")
        lines.append("|----------|-------|")
        for cat in sorted(r['notes_per_category']):
            lines.append(f"| {cat} | {r['notes_per_category'][cat]} |")
        lines.append(f"| _review (awaiting review) | {r['review_notes']} |")
        for folder in sorted(r['unmapped_folders']):
            lines.append(f"| ⚠️ unmapped: {folder} | {r['unmapped_folders'][folder]} |")
    else:
        lines.append("*(no notes found)*")
    lines.append("")

    lines.append("## Notes missing `source:`")
    lines.append("")
    if r['missing_source']:
        lines.append("These notes cannot be matched by the app's anti-duplicate "
                     "index (it keys on `source:`):")
        lines.append("")
        for p in sorted(r['missing_source']):
            lines.append(f"- `{p}`")
    else:
        lines.append("None — every note carries its `source:` line. ✅")
    lines.append("")

    lines.append("## Duplicate `source:` URLs")
    lines.append("")
    if r['duplicate_sources']:
        for d in r['duplicate_sources']:
            lines.append(f"- `{d['url']}` ({len(d['paths'])} notes):")
            for p in d['paths']:
                lines.append(f"  - `{p}`")
    else:
        lines.append("None. ✅")
    lines.append("")

    lines.append("## Notes with human-written content")
    lines.append("")
    lines.append("(Anything beyond the template placeholders in *My Ideas & Notes*, "
                 "*Social Signal (Manual)* or *Journal*. The app must never "
                 "overwrite these.)")
    lines.append("")
    if r['human_edits']:
        for e in sorted(r['human_edits'], key=lambda x: x['path']):
            parts = []
            for s in e['sections']:
                if s['status'] == 'custom':
                    parts.append(f"**{s['name']}** — “{s['excerpt']}”")
            lines.append(f"- `{e['path']}`")
            for p in parts:
                lines.append(f"  - {p}")
    else:
        lines.append("None — every note still contains only the template "
                     "placeholders. ✅")
    lines.append("")
    return '\n'.join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="scan_vault_edits",
        description="Read-only scan of an Obsidian vault: notes per category, "
                    "missing/duplicate source URLs, and human-written content "
                    "in the three manual sections. Writes a Markdown report "
                    "OUTSIDE the vault.")
    parser.add_argument("vault", help="path to the vault folder to scan")
    parser.add_argument("--out", default=None,
                        help=f"report folder (default: {DEFAULT_REPORT_DIR}; "
                             "must be outside the vault)")
    args = parser.parse_args(argv)

    vault = os.path.abspath(args.vault)
    if not os.path.isdir(vault):
        print(f"ERROR: vault folder not found: {vault}")
        return 1

    out_dir = os.path.abspath(args.out or DEFAULT_REPORT_DIR)
    if _is_inside(out_dir, vault):
        print("ERROR: the report folder must be OUTSIDE the vault — this tool "
              "never writes anything into the vault it scans.")
        return 2

    result = scan_vault(vault)
    report = render_markdown(result)

    try:
        os.makedirs(out_dir, exist_ok=True)
        vault_name = os.path.basename(vault) or "vault"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        report_path = os.path.join(out_dir, f"scan_{vault_name}_{stamp}.md")
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report)
    except Exception as exc:
        print(f"ERROR: could not write the report: {exc}")
        return 3

    # Console summary (same numbers as the report)
    print(f"Vault scan — {vault}")
    print(f"  Notes scanned:            {result['notes_scanned']}")
    print(f"  Files skipped:            {result['skipped_files']}")
    print(f"  Missing source:           {len(result['missing_source'])}")
    print(f"  Duplicate source URLs:    {len(result['duplicate_sources'])}")
    print(f"  Human-written content:    {len(result['human_edits'])}")
    print(f"  Report:                   {report_path}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
