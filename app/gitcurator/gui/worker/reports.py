"""WorkerReportsMixin — the end-of-run reporting — the master _index, the final report, and the summary log.

Moved verbatim from gitcurator/gui/processing_worker.py at
v0.25.0 (hygiene pass); processing_worker.py composes this mixin
onto the ProcessingWorker shell. Bodies are byte-identical.
"""


import sys
import os
import re
import json
import time
import urllib.error
import sqlite3
import subprocess
import html as _html_module
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any
import threading
import logging
from logging.handlers import RotatingFileHandler

from gitcurator.constants import (
    APP_DIR, COLORS, CONFIG_FILE, CONFIG_EXAMPLE, CATEGORY_FOLDERS,
    CATEGORY_KEYS, DEFAULT_SYSTEM_PROMPT,
)
from gitcurator.core import links as _links
from gitcurator.core import storage as _storage
from gitcurator.core import note_builder as _note_builder
from gitcurator.core import llm_client as _llm_client
from gitcurator.core import dryrun as _dryrun
from gitcurator.core import note_state as _note_state
from gitcurator.core import website_pipeline as _website_pipeline
from gitcurator.core import connection_check as _connection_check
from gitcurator.integrations import vaultseal as _vaultseal
from gitcurator.integrations import goodrepos as _goodrepos
from gitcurator.gui.telegram_lock import TelegramLockManager
from gitcurator.gui import icons as _icons
from gitcurator.integrations.subprocess_runner import (
    run_telegram_worker as _run_worker_subprocess,
    kill_all_workers as _kill_all_telegram_workers,
    live_worker_count as _live_telegram_worker_count,
)

try:
    from PyQt6.QtWidgets import *
    from PyQt6.QtCore import *
    from PyQt6.QtGui import *
except ImportError:
    print("PyQt6 is not installed. Please run: pip install PyQt6")
    sys.exit(1)

try:
    import github
    from github import Github, GithubException, Auth
except ImportError:
    print("PyGithub is not installed. Please run: pip install PyGithub")
    sys.exit(1)

try:
    import ollama
except ImportError:
    print("Ollama Python client is not installed. Please run: pip install ollama")
    sys.exit(1)

_APP_DIR = APP_DIR

from gitcurator.gui.cache_db import CacheDB

from gitcurator.gui.dead_links import DEAD_LINK_THRESHOLD, dead_link_threshold

from gitcurator.gui.link_helpers import clean_url, normalize_url

from gitcurator.gui.link_tracker import LinkTracker

from gitcurator.gui.platform_intake import (
    PLATFORM_INFO,
    _inbox_table_vault,
    classify_platform,
    write_inbox_links_by_platform,
)

from gitcurator.gui.vault_index import VaultIndex, _safe_moc_name

from gitcurator.gui.worker_jobs import _run_telegram_worker


class WorkerReportsMixin:
    # ---- moved verbatim; see module docstring ----
    @classmethod
    def _reports_dir(cls) -> str:
        """v0.66.0 — where the run reports live: the app's ``reports/``
        folder by default, overridable via the ``reports_dir`` config key
        (a relative value resolves against the app dir). The vault root
        is NEVER a reports home — the vault is the library, not the
        filing cabinet."""
        return os.path.join(APP_DIR, 'reports')

    def _reports_dir_for(self) -> str:
        """The CONFIG-aware reports dir (``reports_dir`` overrides the
        default; empty/relative values fall back to the app's folder)."""
        override = str((self.config or {}).get('reports_dir') or '').strip()
        if override and os.path.isabs(override):
            return override
        return self._reports_dir()

    def _generate_master_index(self):
        """v0.66.0 — RETIRED (kept as the compat shim: the batch's call
        site and the CLI twins still speak the old name). The v25-era
        master index (_index.md) + per-category MOCs (_moc/*.md) are the
        owner's reported graph pollution: every note in the vault gained
        a backlink to a hub file, every banished note in .trash became a
        GHOST node (the walk never skipped .trash), and clicking a ghost
        link created an EMPTY note that Obsidian then refuses to delete
        cleanly ("some notes are linked to it"). The pass now runs the
        hygiene cleanup instead — see _vault_hygiene_pass."""
        return self._vault_hygiene_pass()

    # v0.66.0 — the folders the hygiene walk never enters (system /
    # hidden / app-machinery folders — the same set the rest of the
    # vault walks honor, plus the Library mirror tree).
    _HYGIENE_SKIP_DIRS = ('.obsidian', '.git', '.trash', '_missing',
                          '_moc', '_inbox', 'attachments', 'Library',
                          '__pycache__')

    # An EMPTY STUB: no frontmatter, under 256 bytes, and nothing but
    # blank lines + at most one heading line (the shape Obsidian leaves
    # when a ghost [[wiki-link]] is clicked into existence). Anything
    # richer — frontmatter, body text, a list — is somebody's writing
    # and is never touched.
    _STUB_MAX_BYTES = 256

    @classmethod
    def _is_empty_stub(cls, content: str) -> bool:
        """True when a note is content-free: no frontmatter, no body —
        blank lines and at most one heading line (the shape Obsidian
        leaves when a ghost [[wiki-link]] is clicked into existence —
        an empty file, whitespace, or a lone auto-generated heading).
        Anything richer — frontmatter, body text, a list — is somebody's
        writing and is never touched. Never raises."""
        if content is None:
            return True
        if content.startswith('---'):
            return False        # frontmatter = a structured file, never ours to judge
        headings = 0
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith('#'):
                headings += 1
                if headings > 1:
                    return False
                continue
            return False            # real content — somebody's writing
        return True

    @classmethod
    def _has_note_name_shape(cls, fname: str) -> bool:
        """True when a filename carries the app's canonical note shape:
        ``<repo>_<Category>_<tag>.md`` — a CATEGORY_KEY appears as an
        underscore-delimited segment. The ghost-link children inherit
        their names from the retired index's ``[[wiki-links]]``, which
        came from the app's own note filenames — so THIS shape, plus
        content-free, is the fingerprint of the empty notes the owner
        reported. An owner's own filename (my-thoughts.md, ideas.md)
        never carries a category segment and is never touched."""
        stem = fname[:-3] if fname.endswith('.md') else fname
        if '_' not in stem:
            return False
        parts = stem.split('_')
        return any(p in CATEGORY_KEYS for p in parts)

    def _vault_hygiene_pass(self):
        """v0.66.0 — THE GRAPH'S CLEAN HANDS (the owner's report, verbatim:
        "the app created some empty notes, which also changed the graph
        look of the vault … when you want to delete them, Obsidian
        [warns] that some notes are linked to it. Fix it.").

        What the old reporting left in the vault, and what this pass
        does about it — every step dry-run aware, never raises, and
        NOTHING the app cannot prove it owns (or that is not literally
        content-free) is ever touched:

        1. ``_index.md`` + ``_moc/*.md`` — the retired master-index
           scaffold. Removed ONLY when the frontmatter proves the app
           wrote it (``type: master-index`` / ``type: moc``); the empty
           ``_moc/`` folder goes too. This heals the backlink walls:
           repo notes become deletable again, and the ghost
           ``[[wiki-links]]`` that bred empty notes are gone.
        2. Empty stub notes — content-free .md files carrying the app's
           note-name shape (``<repo>_<Category>_<tag>.md`` — the
           Obsidian-born children of clicked ghost links; no frontmatter,
           ≤ one heading line) — RETIRED into ``.trash/empty-stubs/``
           (recoverable by hand, invisible to the vault's walks). An
           owner's own empty note (any other filename) is never touched.
        3. Legacy root reports — the accumulated
           ``_processing_report_*.md`` notes and
           ``processing_summary_*.txt`` files the pre-v0.66 batches
           dropped at the vault root (one per batch — graph nodes and
           root clutter alike). New reports live in ``app/reports/``;
           the legacy pile is retired to ``.trash/retired-reports/``.

        The manifest (``links_manifest.json``) and the undo list
        (``_undo_last_batch.txt``) are WORKING FILES, not notes — they
        stay (Obsidian does not index .json/.txt by default).
        """
        try:
            vault_path = self.config.get('vault_path', '')
            if not vault_path or not os.path.isdir(vault_path):
                return
            removed_index = 0      # _index.md + MOC files (app-owned)
            retired_stubs = 0      # content-free notes → .trash/empty-stubs
            retired_reports = 0    # legacy root reports → .trash/retired-reports

            def _log(msg, level='info'):
                try:
                    self.log_message.emit(msg, level)
                except Exception:
                    pass

            def _frontmatter_type(path):
                """The ``type:`` frontmatter value, or '' — head-read
                only, never raises."""
                try:
                    with open(path, 'r', encoding='utf-8',
                              errors='replace') as f:
                        head = f.read(400)
                except OSError:
                    return ''
                if not head.startswith('---'):
                    return ''
                m = re.search(r'^type:\s*(.+)$', head, re.MULTILINE)
                return m.group(1).strip().strip('"\'') if m else ''

            def _retire(path, subdir):
                """Move one file into <vault>/.trash/<subdir>/ — bytes
                preserved, name uniquified, dry-run aware. Returns True
                when the move landed (or was rehearsed)."""
                dst_dir = os.path.join(vault_path, '.trash', subdir)
                try:
                    _dryrun.makedirs(dst_dir, exist_ok=True)
                    dst = _storage.unique_path(
                        os.path.join(dst_dir, os.path.basename(path)))
                    _dryrun.move(path, dst)
                    return True
                except Exception:
                    return False

            # ---- 1. the retired master-index scaffold -------------------
            index_path = os.path.join(vault_path, '_index.md')
            if os.path.isfile(index_path) \
                    and _frontmatter_type(index_path) == 'master-index':
                _dryrun.remove(index_path)
                removed_index += 1
            moc_dir = os.path.join(vault_path, '_moc')
            if os.path.isdir(moc_dir):
                for fname in os.listdir(moc_dir):
                    if not fname.endswith('.md'):
                        continue
                    fpath = os.path.join(moc_dir, fname)
                    if not os.path.isfile(fpath):
                        continue
                    if _frontmatter_type(fpath) == 'moc':
                        _dryrun.remove(fpath)
                        removed_index += 1
                # prune the folder when the app's files were its only
                # residents (an owner file keeps the folder — sacred).
                # os.remove cannot take a directory — rmdir only succeeds
                # when it is genuinely empty, so an owner file keeps the
                # folder by construction.
                try:
                    if _dryrun.is_enabled():
                        _dryrun.record(
                            'remove', moc_dir,
                            note='delete the emptied _moc folder')
                    elif not os.listdir(moc_dir):
                        os.rmdir(moc_dir)
                except Exception:
                    pass

            # ---- 2. the Obsidian-born empty stubs ----------------------
            # (anywhere in the visible vault — root included: a clicked
            # ghost link births its empty note wherever Obsidian stands)
            stub_targets = []
            for root, dirs, files in os.walk(vault_path):
                dirs[:] = [d for d in dirs
                           if d not in self._HYGIENE_SKIP_DIRS]
                for fname in files:
                    if not fname.endswith('.md'):
                        continue
                    fpath = os.path.join(root, fname)
                    try:
                        if os.path.getsize(fpath) > self._STUB_MAX_BYTES:
                            continue
                        with open(fpath, 'r', encoding='utf-8',
                                  errors='replace') as f:
                            content = f.read(self._STUB_MAX_BYTES + 1)
                    except OSError:
                        continue
                    if self._is_empty_stub(content) \
                            and self._has_note_name_shape(fname):
                        stub_targets.append(fpath)
            for fpath in stub_targets:
                if _retire(fpath, 'empty-stubs'):
                    retired_stubs += 1

            # ---- 3. the legacy root reports ----------------------------
            for fname in os.listdir(vault_path):
                fpath = os.path.join(vault_path, fname)
                if fname.startswith('_processing_report_') \
                        and fname.endswith('.md') and os.path.isfile(fpath):
                    if _retire(fpath, 'retired-reports'):
                        retired_reports += 1
                elif fname.startswith('processing_summary_') \
                        and fname.endswith('.txt') and os.path.isfile(fpath):
                    if _retire(fpath, 'retired-reports'):
                        retired_reports += 1

            # ---- the honest summary -------------------------------------
            if removed_index:
                _log(f"🧹 Vault hygiene: the retired master index "
                     f"({removed_index} file(s) — _index.md/_moc) left the "
                     f"vault — its links no longer weld your graph or block "
                     f"note deletions", "info")
            if retired_stubs:
                _log(f"🧹 Vault hygiene: {retired_stubs} empty stub note(s) "
                     f"(content-free — the ghost-link children) retired to "
                     f".trash/empty-stubs — recoverable by hand", "info")
            if retired_reports:
                _log(f"🧹 Vault hygiene: {retired_reports} legacy report "
                     f"file(s) retired from the vault root to "
                     f".trash/retired-reports (new reports live in the app's "
                     f"reports folder, not your vault)", "info")
            if not (removed_index or retired_stubs or retired_reports):
                try:
                    self.log_message.emit(
                        "🧹 Vault hygiene: clean — no index scaffold, no "
                        "empty stubs, no legacy reports in the vault.",
                        "info")
                except Exception:
                    pass
        except Exception as e:
            try:
                self.log_message.emit(
                    f"⚠️ Vault hygiene pass skipped: {e}", "warning")
            except Exception:
                pass

    def _generate_final_report(self, link_tracker_report=None):
        """v25 pre-flight: generate a comprehensive Markdown report after
        processing finishes.

        v0.66.0 — THE VAULT IS THE LIBRARY, NOT THE FILING CABINET: the
        report now lives in the app's own ``app/reports/`` folder (next
        to the recall + golden artifacts) instead of the vault root —
        one .md per batch used to accumulate as graph nodes and root
        clutter in the owner's Obsidian vault (his report: "the app
        created some empty notes, which also changed the graph look of
        the vault"). The legacy root pile is retired by the hygiene
        pass; this writer never touches the vault again.

        The report always runs — even if some links failed — so the user has
        a complete audit trail. It includes:

          * Summary table (total / processed / failed / skipped / categories)
          * LinkTracker verification report (when present)
          * Repos grouped by category
          * Full list of processed repos with credibility + banner status
          * Failed links with their error messages (for retry)
          * Intake duplicate count (raw vs unique URLs from the bot queue)

        Returns the path to the written report, or None on failure."""
        try:
            vault_path = self.config.get('vault_path', '')
            if not vault_path or not os.path.isdir(vault_path):
                return None

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            reports_dir = self._reports_dir_for()
            # v0.66.0 — dry-run aware: a rehearsal records, never creates.
            _dryrun.makedirs(reports_dir, exist_ok=True)
            report_path = os.path.join(
                reports_dir, f"_processing_report_{timestamp}.md")

            processed = getattr(self, '_processed_log', [])
            total = self.total
            success_count = self.processed  # incremented only on real writes
            logged_count = len(processed)   # _processed_log has one entry per success
            failed_count = max(0, total - success_count)

            # Count categories (from _processed_log)
            categories = {}
            for p in processed:
                cat = p.get('category', 'Uncategorized')
                categories[cat] = categories.get(cat, 0) + 1

            # Build report
            lines = []
            lines.append(f"# 📊 Processing Report — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
            lines.append("")
            lines.append(f"> Source: `{getattr(self, '_bot_source', False) and 'bot' or 'import/telegram'}` "
                         f"| Batch size: {total} | Worker v25")
            lines.append("")

            # Intake duplicates (Feature 7)
            intake_dupes = getattr(self, '_intake_duplicates', 0)
            if intake_dupes > 0:
                unique_total = total + len(getattr(self, '_non_github_urls', []) or [])
                raw_total = getattr(self, '_raw_url_count', 0) or (unique_total + intake_dupes)
                lines.append(f"> 🔄 **{intake_dupes} duplicate URL(s) removed during intake** "
                             f"({unique_total} unique from {raw_total} total)")
                lines.append("")

            lines.append("## 📈 Summary")
            lines.append("")
            lines.append("| Metric | Count |")
            lines.append("|--------|-------|")
            lines.append(f"| 📬 Total links in batch | {total} |")
            lines.append(f"| ✅ Successfully processed | {success_count} |")
            lines.append(f"| ❌ Failed (will retry) | {failed_count} |")
            # "Skipped" = total - success - failed. When verification ran, the
            # LinkTracker report gives a more accurate breakdown below.
            skipped_count = max(0, total - success_count - failed_count)
            if link_tracker_report:
                skipped_count = link_tracker_report.get('github_skipped', skipped_count)
            lines.append(f"| ⏭️ Skipped (dedup) | {skipped_count} |")
            lines.append(f"| 📁 Categories used | {len(categories)} |")
            lines.append("")

            # v0.12.0 — Phase 3: the note-state section (SPEC: "add its
            # results (moved, edited, deleted, duplicate, unmanaged,
            # unmapped) to the run report").
            ns_run = getattr(self, '_note_state_run', None) or {}
            ns_lines = []
            for _vkey, _ns in ns_run.items():
                if not _ns:
                    continue
                _ch = _ns.get('changes') or {}
                _ap = _ns.get('applied') or {}
                _counts = [
                    _ap.get('moved_applied', 0),
                    _ap.get('moved_unmapped', 0),
                    _ap.get('dismissed', 0),
                    len(_ch.get('edited') or []),
                    len(_ch.get('duplicates') or []),
                    len(_ch.get('unmanaged') or []),
                    len(_ch.get('unmapped') or []),
                    len(_ch.get('unknown') or []),
                ]
                if not any(_counts) and _ns.get('baseline') is None:
                    continue
                _vname = 'GitHub' if _vkey == 'github' else 'Websites'
                ns_lines.append(f"### {_vname} vault")
                ns_lines.append("")
                if _ns.get('baseline') is not None:
                    ns_lines.append(
                        f"- 📋 Baseline recorded: {_ns['baseline']} notes "
                        "(first run after v0.12.0 — nothing flagged)")
                if _ap.get('moved_applied'):
                    ns_lines.append(f"- 📌 Moves accepted as corrections: "
                                    f"{_ap['moved_applied']}")
                    for _ml in _note_state.move_summary_lines(
                            _ap.get('corrections') or []):
                        ns_lines.append(f"  - {_ml}")
                if _ap.get('moved_unmapped'):
                    ns_lines.append(
                        f"- 📍 Moved into unmapped folders (kept as-is): "
                        f"{_ap['moved_unmapped']}")
                if _ap.get('dismissed'):
                    ns_lines.append(
                        f"- 🗑️ Deleted notes dismissed (never re-added): "
                        f"{_ap['dismissed']}")
                if _ch.get('edited'):
                    ns_lines.append(f"- ✏️ Edited by hand (skipped, listed): "
                                    f"{len(_ch['edited'])}")
                    for _e in _ch['edited'][:10]:
                        ns_lines.append(f"  - {_e.get('path')}")
                if _ch.get('duplicates'):
                    ns_lines.append(
                        f"- ⚠️ Duplicates (flagged, untouched): "
                        f"{len(_ch['duplicates'])}")
                if _ch.get('unmanaged'):
                    ns_lines.append(
                        f"- 📄 Unmanaged files (no source, ignored): "
                        f"{len(_ch['unmanaged'])}")
                if _ch.get('unmapped'):
                    ns_lines.append(
                        f"- ❓ Notes in unmapped folders (kept, reported): "
                        f"{len(_ch['unmapped'])}")
                    for _u in _ch['unmapped'][:10]:
                        ns_lines.append(
                            f"  - {_u.get('folder')} — {_u.get('path')}")
                if _ch.get('unknown'):
                    ns_lines.append(
                        f"- ❔ Notes with an unrecorded source (listed): "
                        f"{len(_ch['unknown'])}")
                ns_lines.append("")
            if getattr(self, '_dismissed_skipped', 0):
                ns_lines.append(
                    f"- 🚫 Dismissed URLs skipped in this batch: "
                    f"{self._dismissed_skipped}")
                ns_lines.append("")
            if ns_lines:
                lines.append("## 🔄 Note State (moves are corrections)")
                lines.append("")
                lines.extend(ns_lines)

            # LinkTracker verification report
            if link_tracker_report:
                lines.append("## 🔍 Verification Report")
                lines.append("")
                lines.append("| Check | Result |")
                lines.append("|-------|--------|")
                lines.append(f"| 🔍 Total links verified | {link_tracker_report.get('total', 0)} |")
                lines.append(f"| ✅ GitHub processed | {link_tracker_report.get('github_processed', 0)} |")
                lines.append(f"| ⏭️ GitHub skipped (dedup) | {link_tracker_report.get('github_skipped', 0)} |")
                lines.append(f"| ❌ GitHub failed | {link_tracker_report.get('github_failed', 0)} |")
                if link_tracker_report.get('github_pending'):
                    lines.append(f"| ⏳ GitHub pending (unfinished) | {link_tracker_report.get('github_pending', 0)} |")
                if link_tracker_report.get('non_github_recorded'):
                    lines.append(f"| ✅ Non-GitHub recorded (inbox) | {link_tracker_report.get('non_github_recorded', 0)} |")
                if link_tracker_report.get('websites_processed') or link_tracker_report.get('websites_review') or link_tracker_report.get('websites_skipped'):
                    lines.append(f"| 🌐 Websites notes | {link_tracker_report.get('websites_processed', 0)} |")
                    lines.append(f"| 🗂️ Websites in _review (retry scheduled) | {link_tracker_report.get('websites_review', 0)} |")
                    lines.append(f"| ⏭️ Websites skipped (dedup) | {link_tracker_report.get('websites_skipped', 0)} |")
                if link_tracker_report.get('blocked_recorded'):
                    lines.append(f"| 🚫 Blocked/self domains (omitted by design — never collected) | {link_tracker_report.get('blocked_recorded', 0)} |")
                if link_tracker_report.get('non_github_pending'):
                    lines.append(f"| ⏳ Non-GitHub pending (websites pipeline off / no vault) | {link_tracker_report.get('non_github_pending', 0)} |")
                if link_tracker_report.get('non_github_failed'):
                    lines.append(f"| ❌ Non-GitHub failed | {link_tracker_report.get('non_github_failed', 0)} |")
                lines.append(f"| 🧮 Accounting | {link_tracker_report.get('accounted', 0)}/{link_tracker_report.get('total', 0)} accounted for" + (" ✓" if link_tracker_report.get('accounting_ok') else f" — {link_tracker_report.get('unaccounted', 0)} unaccounted!") + " |")
                verdict = ("✅ ALL LINKS VERIFIED — NO DATA LOSS!"
                           if (link_tracker_report.get('verification_passed')
                               and link_tracker_report.get('accounting_ok'))
                           else "❌ SOME LINKS NEED RETRY")
                lines.append(f"| 🎯 Overall verdict | {verdict} |")
                lines.append("")

            # By category
            if categories:
                lines.append("## 📁 Repos by Category")
                lines.append("")
                lines.append("| Category | Count |")
                lines.append("|----------|-------|")
                for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
                    lines.append(f"| {cat} | {count} |")
                lines.append("")

            # All processed repos
            if processed:
                lines.append("## 📋 All Processed Repos")
                lines.append("")
                lines.append("| # | Repo | Category | Credibility | Banner |")
                lines.append("|---|------|----------|-------------|--------|")
                for i, p in enumerate(processed, 1):
                    repo = p.get('repo', 'unknown')
                    cat = p.get('category', 'Uncategorized')
                    cred = p.get('credibility', 0)
                    banner = '🖼️' if p.get('banner') else '—'
                    lines.append(f"| {i} | {repo} | {cat} | {cred}/100 | {banner} |")
                lines.append("")

            # Failed links (from LinkTracker)
            if link_tracker_report and link_tracker_report.get('failed_links'):
                lines.append("## ❌ Failed Links (Will Retry)")
                lines.append("")
                for fl in link_tracker_report['failed_links']:
                    lines.append(f"- `{fl.get('url', '?')}` — {fl.get('error', 'unknown error')}")
                lines.append("")
                lines.append("> Failed links are kept in the manifest and "
                             "surfaced for retry on the next app launch "
                             "(Dashboard → 🔍 Verify Vault).")
                lines.append("")

            # Non-GitHub links recorded (brief summary)
            non_github = getattr(self, '_non_github_urls', []) or []
            if non_github:
                # v0.11.0 — Phase 2: the heading reflects reality — with the
                # websites pipeline ON the links went there, not to _inbox.
                _pipes = (self.config or {}).get('pipelines') or {}
                _wp_on = bool(_pipes.get('websites', False))
                if _wp_on:
                    lines.append("## 🌐 Non-GitHub Links (Websites pipeline)")
                    lines.append("")
                    lines.append(f"{len(non_github)} link(s) were processed "
                                 "by the Websites pipeline (see its section "
                                 "below).")
                    lines.append("")
                else:
                    # v0.35.0 — banned links are never collected: the
                    # platform table counts only the recordable ones, and
                    # the omitted count is its own line.
                    _law = _links.blocked_domains_from_config(self.config)
                    _recordable, _omitted = [], 0
                    for u in non_github:
                        if _links.domain_is_blocked(u, _law):
                            _omitted += 1
                        else:
                            _recordable.append(u)
                    # Group by platform for the report
                    platform_counts = {}
                    for u in _recordable:
                        p = classify_platform(u)
                        if p == 'github':
                            p = 'other'
                        platform_counts[p] = platform_counts.get(p, 0) + 1
                    lines.append("## 📥 Non-GitHub Links (recorded in _inbox/)")
                    lines.append("")
                    lines.append("| Platform | Count |")
                    lines.append("|----------|-------|")
                    for p, c in sorted(platform_counts.items(), key=lambda x: -x[1]):
                        display_name = PLATFORM_INFO.get(p, ('🔗 Other', 'other_links.md'))[0]
                        lines.append(f"| {display_name} | {c} |")
                    lines.append("")
                    if _omitted:
                        lines.append(f"🚫 {_omitted} banned-domain link(s) "
                                     "omitted — never collected (no note, "
                                     "no _review, no _inbox row).")
                        lines.append("")

            # v0.11.0 — Phase 2: the websites pipeline's own section.
            _ws = getattr(self, '_website_summary', None)
            if _ws:
                _c = _ws.get('counters', {})
                lines.append("## 🌐 Websites Pipeline")
                lines.append("")
                lines.append(f"Vault: `{_ws.get('vault', '')}`")
                lines.append("")
                lines.append("| Outcome | Count |")
                lines.append("|---------|-------|")
                for key, label in (('processed', 'processed'),
                                   ('review', 'needs review (_review)'),
                                   ('upgraded', 'upgraded from _review'),
                                   ('retried', 'fetch retries attempted'),
                                   ('skipped', 'skipped (already known)'),
                                   ('failed', 'failed')):
                    if _c.get(key):
                        lines.append(f"| {label} | {_c[key]} |")
                lines.append("")
                per_link = _ws.get('results') or []
                if per_link:
                    lines.append("| Link | Outcome | Filed under |")
                    lines.append("|------|---------|-------------|")
                    for r in per_link:
                        filed = r.get('category') or ''
                        if r.get('subcategory'):
                            filed += f" / {r['subcategory']}"
                        if not filed:
                            filed = r.get('error') or ''
                        link_md = f"[{r.get('url', '?')}]({r.get('url', '')})"
                        lines.append(f"| {link_md} | {r.get('outcome', '?')} | {filed} |")
                    lines.append("")

            lines.append("---")
            lines.append(f"*Report generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")

            try:
                # v0.09.5 — Phase 0 (dry-run): recorded, not performed.
                _dryrun.write_text(report_path, '\n'.join(lines))
            except Exception as write_err:
                self.log_message.emit(f"⚠️ Failed to write final report: {write_err}", "warning")
                return None

            return report_path
        except Exception as e:
            try:
                self.log_message.emit(f"⚠️ Failed to generate final report: {e}", "warning")
            except Exception:
                pass
            return None

    def _generate_summary_log(self):
        """Generate a .txt summary of processed repos after a run.

        v0.66.0 — THE VAULT IS THE LIBRARY: the summary now lives in
        the app's ``app/reports/`` folder (was the vault root — one
        .txt per batch accumulated as root clutter and rode every
        VaultSeal commit). The rotation keeps the newest
        ``summary_keep_last`` (default 10) there; the legacy root pile
        is retired by the hygiene pass."""
        try:
            vault_path = self.config.get('vault_path', '')
            if not vault_path or not os.path.isdir(vault_path):
                return None

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"processing_summary_{timestamp}.txt"
            reports_dir = self._reports_dir_for()
            # v0.66.0 — dry-run aware: a rehearsal records, never creates.
            _dryrun.makedirs(reports_dir, exist_ok=True)
            filepath = os.path.join(reports_dir, filename)

            processed = getattr(self, '_processed_log', [])
            total = self.total
            success_count = len(processed)
            skipped = total - success_count

            lines = []
            lines.append("=" * 60)
            lines.append("GITHUB PROJECT CURATOR - PROCESSING SUMMARY")
            lines.append("=" * 60)
            lines.append(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            lines.append(f"Total URLs: {total}")
            lines.append(f"Notes created: {success_count}")
            lines.append(f"Skipped (duplicates/errors): {skipped}")
            lines.append(f"Banners downloaded: {sum(1 for p in processed if p.get('banner'))}")
            lines.append("=" * 60)
            lines.append("")

            if processed:
                lines.append("PROCESSED REPOS:")
                lines.append("-" * 60)
                for i, p in enumerate(processed, 1):
                    lines.append(f"{i}. {p['repo']}")
                    lines.append(f"   URL: {p['url']}")
                    lines.append(f"   Category: {p['category']}")
                    lines.append(f"   Credibility: {p['credibility']}/100")
                    lines.append(f"   Banner: {'Yes' if p.get('banner') else 'No'}")
                    lines.append(f"   Note: {os.path.basename(p['note_path'])}")
                    lines.append("")
            else:
                lines.append("No repos were processed in this run.")
                lines.append("")

            # Non-GitHub links section
            non_github = getattr(self, '_non_github_urls', [])
            if non_github:
                lines.append("=" * 60)
                _pipes = (self.config or {}).get('pipelines') or {}
                if _pipes.get('websites', False):
                    lines.append("NON-GITHUB LINKS (processed by the Websites pipeline)")
                    lines.append("=" * 60)
                    lines.append(f"Count: {len(non_github)}")
                    lines.append("Outcome: see the run report / the log above")
                else:
                    lines.append("NON-GITHUB LINKS (not processed — review manually)")
                    lines.append("=" * 60)
                    lines.append(f"Count: {len(non_github)}")
                    lines.append("Stub notes created in: _inbox/ folder")
                lines.append("-" * 60)
                for i, url in enumerate(non_github, 1):
                    lines.append(f"{i}. {url}")
                lines.append("")

            lines.append("=" * 60)
            lines.append("END OF SUMMARY")
            lines.append("=" * 60)

            # v0.09.5 — Phase 0 (dry-run): recorded, not performed.
            _dryrun.write_text(filepath, '\n'.join(lines))

            # v0.26.0 — SWOT fix (the v0.06 P3 item): run summaries used
            # to accumulate forever (and VaultSeal committed every one of
            # them). Keep only the newest ``summary_keep_last`` (default
            # 10). v0.66.0 — the rotation now reads the app's reports
            # folder (the writer's new home); the vault root is never
            # listed again (its legacy pile belongs to the hygiene pass).
            # The matcher is the exact processing_summary_*.txt pattern —
            # nothing else is ever touched, and dry-run records the
            # removals instead of performing them.
            # ``summary_keep_last`` <= 0 keeps everything.
            keep = int((self.config or {}).get('summary_keep_last', 10) or 0)
            if keep > 0:
                try:
                    existing = sorted(
                        f for f in os.listdir(reports_dir)
                        if f.startswith('processing_summary_')
                        and f.endswith('.txt'))
                    for old_name in existing[:-keep]:
                        _dryrun.remove(os.path.join(reports_dir, old_name))
                except OSError:
                    pass

            return filepath
        except Exception as e:
            self.log_message.emit(f"Failed to generate summary log: {e}", "warning")
            return None
