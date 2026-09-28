#!/usr/bin/env python3
"""
pick_golden_links.py — build the candidate golden set (Phase 0, v0.09.5).

Reads the owner's bookmarks export (``unique_links.csv`` — the path is
given as an argument; the tool inspects the file's columns itself) and
selects a DIVERSE set of up to 30 links as the candidate "golden set"
for the future Websites pipeline (Phase 2). The owner approves the final
list in Phase 2 — this file only produces candidates.

Selection rules (deterministic — run it twice, get the same list):
  1. URLs are de-duplicated (same normalization the app uses).
  2. GitHub repository links are excluded (they belong to the existing
     GitHub pipeline, not the Websites pipeline) — but they are counted
     and reported.
  3. The remaining links are grouped by domain; the picker cycles
     domain-by-domain, largest groups first, one link at a time, until
     30 are chosen. That spreads the set across as many domains as
     possible instead of letting one big site fill the whole list.
  4. If fewer than 30 non-GitHub links exist, all of them are selected
     and the report says so.

Output: ``app/tests/golden/websites_candidates.json`` (path configurable
with --out). Pure standard library.

Usage (from the app/ folder, or from anywhere via -m):
    python tools/pick_golden_links.py "C:\\path\\to\\unique_links.csv"
    python tools/pick_golden_links.py links.csv --count 20 --out picks.json
    python -m gitcurator.tools.pick_golden_links "C:\\links.csv"
"""

import argparse
import csv
import io
import json
import os
import sys
from datetime import datetime

# ---------------------------------------------------------------------------
# Bootstrap: make the app/ folder importable no matter how we are launched
# ---------------------------------------------------------------------------
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)

from gitcurator.constants import APP_DIR
from gitcurator.core.links import clean_url, domain_of, is_github_url, normalize_url

# ===========================================================================
# CONFIGURATION (safe to edit)
# ===========================================================================
# How many candidates to pick (SPEC Phase 0: "a diverse set of 30 links").
TARGET_COUNT = 30

# Where the candidate list is written by default.
DEFAULT_OUTPUT = os.path.join(APP_DIR, "tests", "golden", "websites_candidates.json")

# Header names tried (case-insensitive, exact first) to find the URL column.
URL_COLUMN_EXACT = ('url', 'link', 'address', 'href', 'site', 'website')
URL_COLUMN_CONTAINS = ('url', 'link')


def _find_named(header, exact_names):
    """Index of a header cell matching one of ``exact_names``, or None."""
    cells = [(cell or '').strip().lower() for cell in header]
    for want in exact_names:
        for i, cell in enumerate(cells):
            if cell == want:
                return i
    return None


# Optional extra columns worth carrying into the JSON when present.
CATEGORY_COLUMN_EXACT = ('category', 'categories', 'folder', 'collection', 'topic')
TITLE_COLUMN_EXACT = ('title', 'name', 'label')
# ===========================================================================


def _sniff_delimiter(sample: str) -> str:
    """Pick the delimiter that splits the sample into CONSISTENT columns.

    csv.Sniffer is easily confused by quoted commas ("Example, Inc";…), so
    this checks each candidate directly: the delimiter under which every
    non-empty row has the SAME column count (more than one) wins. Ties
    prefer the wider split. Falls back to comma."""
    best = ','
    best_score = (False, 1)   # (consistent, column count)
    for delim in (',', ';', '\t'):
        rows = [r for r in csv.reader(io.StringIO(sample), delimiter=delim) if r]
        if not rows:
            continue
        counts = {len(r) for r in rows}
        consistent = len(counts) == 1
        width = max(counts)
        if width <= 1:
            continue   # a delimiter that splits nothing
        score = (consistent, width)
        if score > best_score:
            best = delim
            best_score = score
    return best


def _read_rows(csv_path: str):
    """Read all non-empty rows. Tolerates BOM, ; and tab delimiters, and
    non-UTF-8 bytes (falls back to latin-1 so the file always opens)."""
    last_err = None
    for encoding in ('utf-8-sig', 'latin-1'):
        try:
            with open(csv_path, 'r', encoding=encoding, newline='') as f:
                sample = f.read(8192)
                f.seek(0)
                delim = _sniff_delimiter(sample)
                return [row for row in csv.reader(f, delimiter=delim)
                        if any((cell or '').strip() for cell in row)]
        except UnicodeDecodeError as exc:
            last_err = exc
            continue
    raise ValueError(f"could not decode {csv_path}: {last_err}")


def _find_column_by_name(header):
    """Index of a header cell whose name says 'this is the URL', or None."""
    cells = [(cell or '').strip().lower() for cell in header]
    for want in URL_COLUMN_EXACT:
        for i, cell in enumerate(cells):
            if cell == want:
                return i
    for want in URL_COLUMN_CONTAINS:
        for i, cell in enumerate(cells):
            if want in cell:
                return i
    return None


def _find_column_by_data(rows):
    """Index of the column whose values look like URLs, or None."""
    if not rows:
        return None
    width = max(len(r) for r in rows)
    for i in range(width):
        values = [(r[i] if len(r) > i else '') or '' for r in rows[:20]]
        values = [v.strip() for v in values if v.strip()]
        if values and sum(1 for v in values if _urlish(v)) >= max(1, len(values) // 2):
            return i
    return None


def _urlish(value) -> bool:
    v = (value or '').strip().lower()
    return v.startswith(('http://', 'https://', 'www.'))


def pick_candidates(csv_path: str, target: int = TARGET_COUNT) -> dict:
    """Analyze the CSV and pick the diverse candidate set (pure — writes
    nothing; the caller/main writes the JSON)."""
    rows = _read_rows(csv_path)
    if not rows:
        raise ValueError(f"{csv_path} contains no rows")

    first_row = rows[0]
    url_col = _find_column_by_name(first_row)
    if url_col is not None:
        has_header = True
        data_rows = rows[1:]
    else:
        # No recognizable URL column NAME. Either the file is headerless
        # (row 0 already contains links) or the header uses unusual names.
        if any(_urlish(c) for c in first_row):
            has_header = False
            data_rows = rows
        else:
            has_header = True
            data_rows = rows[1:]
        url_col = _find_column_by_data(data_rows)
        if url_col is None:
            columns = ', '.join((c or '').strip() or f'col{i}'
                                for i, c in enumerate(first_row))
            raise ValueError(
                "could not find a URL column in this CSV. Columns seen: "
                f"[{columns}]. Expected a column named url/link/address, or a "
                "column whose rows contain http(s) links.")
    cat_col = _find_named(first_row, CATEGORY_COLUMN_EXACT) if has_header else None
    title_col = _find_named(first_row, TITLE_COLUMN_EXACT) if has_header else None

    seen = set()
    unique_links = []   # dicts: url, domain, category, title
    github_count = 0
    dupes = 0
    for row in data_rows:
        if len(row) <= url_col:
            continue
        raw = (row[url_col] or '').strip()
        if not raw:
            continue
        url = clean_url(raw)
        key = normalize_url(url)
        if not key or key in seen:
            dupes += 1
            continue
        seen.add(key)
        if is_github_url(url):
            github_count += 1
            continue
        unique_links.append({
            'url': url,
            'domain': domain_of(url),
            'category': ((row[cat_col] or '').strip() if cat_col is not None
                         and len(row) > cat_col else None),
            'title': ((row[title_col] or '').strip() if title_col is not None
                      and len(row) > title_col else None),
        })

    # Group by domain; round-robin across domains (largest first) so the
    # selection spreads as widely as possible. Deterministic.
    groups = {}
    for item in unique_links:
        groups.setdefault(item['domain'] or '(no domain)', []).append(item)
    domain_order = sorted(groups, key=lambda d: (-len(groups[d]), d))

    picked = []
    if groups:
        idx = 0
        remaining = sum(len(g) for g in groups.values())
        while len(picked) < target and remaining > 0:
            d = domain_order[idx % len(domain_order)]
            if groups[d]:
                picked.append(groups[d].pop(0))
                remaining -= 1
            idx += 1

    return {
        'source_csv': os.path.abspath(csv_path),
        'counts': {
            'csv_rows': len(data_rows),
            'duplicate_urls_removed': dupes,
            'unique_urls': len(unique_links) + github_count,
            'github_links_excluded': github_count,
            'non_github_links': len(unique_links),
            'domains_represented': len({p['domain'] for p in picked}),
            'selected': len(picked),
            'target': target,
        },
        'candidates': picked,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="pick_golden_links",
        description="Read a bookmarks CSV and pick a diverse set of up to 30 "
                    "non-GitHub links as the candidate golden set for the "
                    "Websites pipeline (Phase 2 approves the final list).")
    parser.add_argument("csv", help="path to unique_links.csv (columns are "
                                    "detected automatically)")
    parser.add_argument("--count", type=int, default=TARGET_COUNT,
                        help=f"how many links to pick (default: {TARGET_COUNT})")
    parser.add_argument("--out", default=DEFAULT_OUTPUT,
                        help=f"output JSON path (default: {DEFAULT_OUTPUT})")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.csv):
        print(f"ERROR: CSV file not found: {args.csv}")
        return 1

    try:
        result = pick_candidates(args.csv, target=max(1, args.count))
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1

    payload = {
        'description': (
            "Candidate golden set for the Websites pipeline (Phase 2). "
            "NOT yet approved — Phase 2 finalizes tests/golden/websites.json "
            "from this list with the owner."),
        'generated': datetime.now().isoformat(timespec='seconds'),
        'source_csv': result['source_csv'],
        'counts': result['counts'],
        'candidates': result['candidates'],
    }

    out_path = os.path.abspath(args.out)
    try:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        print(f"ERROR: could not write {out_path}: {exc}")
        return 3

    c = result['counts']
    print(f"Golden-set candidates written: {out_path}")
    print(f"  CSV rows:                 {c['csv_rows']}")
    print(f"  Duplicate URLs removed:   {c['duplicate_urls_removed']}")
    print(f"  GitHub links excluded:    {c['github_links_excluded']} "
          "(they belong to the GitHub pipeline)")
    print(f"  Non-GitHub links:         {c['non_github_links']}")
    print(f"  Selected candidates:      {c['selected']} of {c['target']} target "
          f"across {c['domains_represented']} domains")
    if c['selected'] < c['target']:
        print("  ⚠️ Fewer non-GitHub links available than the target — "
              "all of them were selected.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
