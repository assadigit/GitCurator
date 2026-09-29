#!/usr/bin/env python3
"""
backfill_websites.py — load the owner's existing bookmarks into the
Websites vault (Phase 3, v0.12.0, SPEC §6).

Reads a bookmarks CSV (the owner's ``unique_links.csv`` export or any
file with a URL column), routes links the same way the app does
(GitHub repos and github.io pages belong to the GitHub pipeline; gists
and everything else go to the Websites pipeline), and processes them
SLOWLY through the real WebsitePipeline machinery.

Guarantees:
  - resumable: a per-URL checkpoint (``backfill_state`` table in
    cache.db). An interrupted run picks up exactly where it stopped —
    re-running the tool never reprocesses a finished link.
  - polite: a fixed pause between links (``--delay``, default 3 s) on
    top of the pipeline's own per-domain pause.
  - small batches: ``--limit N`` (default 30) NEW links per run.
  - dry-run first: ``--dry-run`` writes nothing anywhere (shadow cache),
    reports exactly what WOULD happen.
  - progress: one line per link, a heartbeat, a final summary, and an
    optional Markdown report (``--report``).

Usage (from the app/ folder):
    python gitcurator/tools/backfill_websites.py ../unique_links.csv --dry-run
    python gitcurator/tools/backfill_websites.py ../unique_links.csv --limit 30
    python gitcurator/tools/backfill_websites.py ../unique_links.csv --delay 5 \\
        --api-url https://api.cloudflare.com/client/v4/accounts/<id>/ai/v1 \\
        --api-key <token> --model @cf/meta/llama-3.1-8b-instruct

Exit codes: 0 = batch finished (or nothing to do), 2 = configuration
error, 130 = interrupted (safe to re-run — it resumes).
"""

import argparse
import csv
import os
import sqlite3
import sys
import time
from datetime import datetime

# Make the app importable from the tools folder
# (…/app/gitcurator/tools -> …/app).
_HERE = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.dirname(os.path.dirname(_HERE))
if _APP not in sys.path:
    sys.path.insert(0, _APP)

from gitcurator.constants import APP_DIR  # noqa: E402
from gitcurator.core import dryrun as _dryrun  # noqa: E402
from gitcurator.core import links as _links  # noqa: E402
from gitcurator.core import note_state as _note_state  # noqa: E402
from gitcurator.core import website_pipeline as _wp  # noqa: E402
from gitcurator.core.taxonomy import load_taxonomy_from_config  # noqa: E402
from gitcurator.core.web_fetch import DomainRateLimiter  # noqa: E402
from gitcurator.tools.run_golden_websites import live_llm  # noqa: E402

DEFAULT_DELAY_S = 3.0
DEFAULT_LIMIT = 30

# The same URL-column heuristics pick_golden_links.py uses (the tool that
# read this CSV first in Phase 0).
_URL_COLUMNS_EXACT = ('url', 'link', 'address', 'href', 'site', 'website')
_URL_COLUMNS_PARTIAL = ('url', 'link', 'address', 'site')


# ---------------------------------------------------------------------------
# CSV reading + routing
# ---------------------------------------------------------------------------

def read_csv_links(csv_path):
    """Return ([website_links], stats). GitHub-pipeline links are excluded
    here (they are counted, never processed) — the same routing the app
    applies on every batch (SPEC §4.2)."""
    with open(csv_path, 'r', encoding='utf-8-sig', newline='') as f:
        reader = csv.reader(f)
        header = next(reader, None) or []
        col = None
        lowered = [h.strip().lower() for h in header]
        for want in _URL_COLUMNS_EXACT:
            if want in lowered:
                col = lowered.index(want)
                break
        if col is None:
            for i, h in enumerate(lowered):
                if any(p in h for p in _URL_COLUMNS_PARTIAL):
                    col = i
                    break
        if col is None:
            raise SystemExit(
                f"No URL column found in {csv_path} (headers: {header[:8]})")

        seen = set()
        stats = {'rows': 0, 'duplicates': 0, 'github': 0, 'websites': 0}
        links = []
        for row in reader:
            if not row or col >= len(row):
                continue
            stats['rows'] += 1
            raw = (row[col] or '').strip()
            if not raw or raw.startswith('#'):
                continue
            try:
                cleaned = _links.clean_url(raw)
            except Exception:
                cleaned = raw
            key = _links.normalize_website_url(cleaned) or cleaned
            if key in seen:
                stats['duplicates'] += 1
                continue
            seen.add(key)
            mapped = _links.map_github_io_url(cleaned)
            if cleaned.startswith('https://github.com/') and \
                    'gist.' not in cleaned:
                stats['github'] += 1     # the GitHub pipeline owns it
                continue
            if mapped:
                stats['github'] += 1     # github.io -> its repo: same
                continue
            stats['websites'] += 1
            links.append(cleaned)
    return links, stats


# ---------------------------------------------------------------------------
# The checkpoint (SPEC: "resumable (checkpoint in cache.db)")
# ---------------------------------------------------------------------------

class BackfillState:
    """Per-URL checkpoint table in the SAME cache.db the app uses. In a
    dry-run the shadow cache is used instead — a rehearsal records
    nothing (the Phase 0 contract)."""

    def __init__(self, db_path):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, timeout=30)
        self.conn.execute("PRAGMA busy_timeout = 30000")
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS backfill_state (
                url TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                note_path TEXT,
                error TEXT,
                ts TEXT NOT NULL
            )
        """)
        self.conn.commit()

    def set(self, url, status, note_path='', error=''):
        self.conn.execute(
            "INSERT OR REPLACE INTO backfill_state "
            "(url, status, note_path, error, ts) VALUES (?,?,?,?,?)",
            (url, status, note_path or '', error or '',
             datetime.now().isoformat(timespec='seconds')))
        self.conn.commit()

    def done_urls(self):
        rows = self.conn.execute(
            "SELECT url FROM backfill_state "
            "WHERE status IN ('done','skipped-github','dismissed')").fetchall()
        return {r[0] for r in rows}

    def counts(self):
        rows = self.conn.execute(
            "SELECT status, COUNT(*) FROM backfill_state "
            "GROUP BY status").fetchall()
        return dict(rows)

    def close(self):
        try:
            self.conn.close()
        except sqlite3.Error:
            pass


# ---------------------------------------------------------------------------
# Plumbing: vault sources probe (website-normalized, stdlib-only)
# ---------------------------------------------------------------------------

def _vault_source_set(vault_path):
    """Every source: URL already on disk in the websites vault, normalized
    the website way (query params are part of the identity). The same
    ground truth the app's VaultIndex provides — without importing the
    GUI module (a tool stays PyQt-free)."""
    import re
    sources = set()
    head_re = re.compile(r'^source:\s*(.+)$', re.MULTILINE)
    for root, dirs, files in os.walk(vault_path):
        dirs[:] = [d for d in dirs if d not in
                   ('_moc', '_inbox', 'attachments', '.obsidian')]
        for fname in files:
            if not fname.endswith('.md'):
                continue
            try:
                with open(os.path.join(root, fname), 'r',
                          encoding='utf-8', errors='replace') as f:
                    head = f.read(800)
            except OSError:
                continue
            m = head_re.search(head)
            if not m:
                continue
            norm = _links.normalize_website_url(
                m.group(1).strip().strip('"\''))
            if norm:
                sources.add(norm)
    return sources


def _build_llm_call(config, args):
    """Same router as the worker's website phase: cloud (OpenAI-compatible
    — llama.cpp / vLLM / Cloudflare Workers AI all speak it) or a local
    Ollama."""
    provider = (args.provider or config.get('llm_provider', 'ollama'))
    if provider == 'cloud' or args.api_url:
        api_url = args.api_url or config.get('cloud_api_url', '')
        api_key = args.api_key or config.get('cloud_api_key', '')
        model = args.model or config.get('cloud_model', '')
        if not api_url or not model:
            raise SystemExit(
                "cloud provider needs --api-url and --model "
                "(or config.json cloud_api_url/cloud_model)")
        timeout = float(args.timeout or config.get('llm_timeout_s', 300)
                        or 300)
        print(f"  LLM: {model} via {api_url}")
        return live_llm(api_url, api_key, model, timeout)

    import ollama
    from gitcurator.core.llm_client import call_with_timeout
    host = config.get('ollama_url', 'http://127.0.0.1:11434')
    model = args.model or config.get('ollama_model', '') or 'llama3'
    timeout = float(config.get('llm_timeout_s', 300) or 300)
    client = ollama.Client(host=host)

    def llm(messages):
        response = call_with_timeout(
            client.chat, timeout, model=model, messages=messages,
            format='json')
        if hasattr(response, 'message'):
            return response.message.content or ""
        if isinstance(response, dict):
            return response.get('message', {}).get('content', '')
        return str(response)

    print(f"  LLM: {model} via Ollama ({host})")
    return llm


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def run(args, llm=None, fetch_fn=None, sleep_fn=time.sleep,
        progress=print) -> int:
    """Process one backfill batch. Returns an exit code. ``llm``/``fetch_fn``
    are test hooks (production builds them from config)."""
    csv_path = args.csv
    if not os.path.isfile(csv_path):
        print(f"CSV not found: {csv_path}", file=sys.stderr)
        return 2

    # Config: the app's own (config.json) + CLI overrides.
    import json
    config = {}
    cfg_path = os.path.join(APP_DIR, 'config.json')
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
        except (OSError, json.JSONDecodeError):
            config = {}

    website_vault = (config.get('website_vault_path') or '').strip()
    if not website_vault:
        print("No website_vault_path in config.json — set the Websites "
              "vault in Settings → 📁 Vault first.", file=sys.stderr)
        return 2

    if args.dry_run:
        _dryrun.enable()

    try:
        links, stats = read_csv_links(csv_path)

        # Where state lives: the REAL cache.db, or the shadow during a
        # dry-run (a rehearsal records nothing).
        if _dryrun.is_enabled():
            state_db_path = _dryrun.shadow_cache_path(
                os.path.join(APP_DIR, 'cache.db'))
        else:
            state_db_path = os.path.join(APP_DIR, 'cache.db')

        backfill = BackfillState(state_db_path)
        wp_state = _wp.WebsiteStateDB(db_path=state_db_path)
        ns_db = None if _dryrun.is_enabled() else \
            _note_state.NoteStateDB(state_db_path)

        try:
            taxonomy = load_taxonomy_from_config(config)
            done = backfill.done_urls()
            vault_sources = _vault_source_set(website_vault)
            delay = float(args.delay if args.delay is not None
                          else DEFAULT_DELAY_S)
            limit = int(args.limit or DEFAULT_LIMIT)

            progress(f"📚 Backfill: {stats['rows']} CSV rows -> "
                     f"{stats['websites']} website link(s) "
                     f"({stats['github']} GitHub-pipeline link(s) excluded, "
                     f"{stats['duplicates']} duplicate(s) removed)")
            progress(f"   Checkpoint: {len(done)} link(s) already handled; "
                     f"batch limit {limit}, delay {delay}s")

            if llm is None:
                llm = _build_llm_call(config, args)

            pipeline = _wp.WebsitePipeline(
                config={**config, 'website_vault_path': website_vault},
                llm_call=llm,
                vault_index_has=lambda u: u in vault_sources,
                state=wp_state,
                note_state_db=ns_db,
                taxonomy=taxonomy,
                fetch_fn=fetch_fn,          # None = the real polite fetcher
                rate_limiter=DomainRateLimiter(delay))

            handled = 0
            counts = {'processed': 0, 'review': 0, 'skipped': 0,
                      'failed': 0, 'excluded': 0}
            exit_code = 0
            for url in links:
                key = _links.normalize_website_url(url) or url
                if key in done or wp_state.is_processed(key) \
                        or wp_state.is_dismissed(key) or key in vault_sources:
                    counts['skipped'] += 1
                    backfill.set(key, 'done', note_path='(already handled)')
                    continue
                if handled >= limit:
                    progress(f"⏸️ Batch limit reached ({limit}) — re-run "
                             "the tool to continue. It resumes here.")
                    break
                try:
                    r = pipeline.process_link(url)
                except KeyboardInterrupt:
                    progress("⏸️ Interrupted — the checkpoint is saved. "
                             "Re-run the tool; it resumes here.")
                    exit_code = 130
                    break
                outcome = r.get('outcome', 'failed')
                counts['processed' if outcome == 'processed'
                       else outcome if outcome in counts else 'failed'] += 1
                handled += 1
                note_path = r.get('note_path', '')
                error = r.get('error', '') or ''
                backfill.set(key,
                             'done' if outcome in ('processed', 'review')
                             else 'failed',
                             note_path=note_path, error=error)
                icon = {'processed': '✅', 'review': '📥',
                        'skipped': '⏭️', 'failed': '❌'}.get(outcome, '❌')
                progress(f"  {icon} {url} -> {outcome}"
                         + (f" ({note_path})" if note_path else '')
                         + (f" [{error}]" if error else ''))
                if handled % 10 == 0:
                    progress(f"  … {handled} processed so far "
                             f"({counts['processed']} notes, "
                             f"{counts['review']} to review)")
                sleep_fn(delay)

            progress("─" * 60)
            progress(f"🏁 Backfill batch done: {counts['processed']} "
                     f"processed, {counts['review']} to review, "
                     f"{counts['skipped']} already handled, "
                     f"{counts['failed']} failed.")
            state_counts = backfill.counts()
            progress(f"   Checkpoint totals: "
                     + ", ".join(f"{k}={v}" for k, v in
                                 sorted(state_counts.items())))
            if args.report:
                _write_report(args.report, stats, counts, state_counts)
                progress(f"   Report: {args.report}")
            return exit_code
        finally:
            backfill.close()
            wp_state.close()
            if ns_db is not None:
                ns_db.close()
    finally:
        if args.dry_run:
            _dryrun.disable()
            _dryrun.clear()


def _write_report(path, stats, counts, state_counts):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    lines = [
        "# Websites backfill — batch report",
        "",
        f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*",
        "",
        "## Intake",
        "",
        f"- CSV rows: {stats['rows']}",
        f"- Website links: {stats['websites']}",
        f"- GitHub-pipeline links excluded: {stats['github']}",
        f"- Duplicates removed: {stats['duplicates']}",
        "",
        "## This batch",
        "",
        f"- Processed: {counts['processed']}",
        f"- To review (`_review/`): {counts['review']}",
        f"- Already handled (checkpoint/vault): {counts['skipped']}",
        f"- Failed (will retry via the pipeline's retry queue): "
        f"{counts['failed']}",
        "",
        "## Checkpoint totals (all runs)",
        "",
    ]
    for k, v in sorted(state_counts.items()):
        lines.append(f"- {k}: {v}")
    with open(path, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='backfill_websites',
        description="Load existing bookmarks into the Websites vault "
                    "(resumable, polite, dry-run first).")
    parser.add_argument('csv', help="bookmarks CSV (e.g. unique_links.csv)")
    parser.add_argument('--limit', type=int, default=DEFAULT_LIMIT,
                        help=f"new links per run (default {DEFAULT_LIMIT})")
    parser.add_argument('--delay', type=float, default=DEFAULT_DELAY_S,
                        help=f"seconds between links (default "
                             f"{DEFAULT_DELAY_S})")
    parser.add_argument('--dry-run', action='store_true',
                        help="write nothing anywhere; report what would "
                             "happen")
    parser.add_argument('--report', default='',
                        help="also write a Markdown report to this path")
    parser.add_argument('--provider', choices=('ollama', 'cloud'),
                        default='', help="override llm_provider")
    parser.add_argument('--api-url', default='',
                        help="OpenAI-compatible endpoint "
                             "(overrides cloud_api_url)")
    parser.add_argument('--api-key', default='',
                        help="API key (overrides cloud_api_key)")
    parser.add_argument('--model', default='',
                        help="model name (overrides cloud_model / "
                             "ollama_model)")
    parser.add_argument('--timeout', type=float, default=0,
                        help="per-call timeout in seconds")
    args = parser.parse_args(argv)
    return run(args)


if __name__ == '__main__':
    sys.exit(main())
