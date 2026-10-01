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
    def _generate_master_index(self):
        """Generate/update master index (_index.md) + per-category MOCs (_moc/).
        Incremental — adds new entries with timestamps, keeps old entries."""
        try:
            vault_path = self.config.get('vault_path', '')
            if not vault_path or not os.path.isdir(vault_path):
                return

            moc_dir = os.path.join(vault_path, "_moc")
            # v0.09.5 — Phase 0 (dry-run): recorded, not performed.
            _dryrun.makedirs(moc_dir, exist_ok=True)

            # Scan vault for all notes
            notes_by_category = {}
            review_notes = []
            all_notes = []

            for root, dirs, files in os.walk(vault_path):
                # Skip _moc, _inbox, attachments folders
                if any(skip in root for skip in ['_moc', '_inbox', 'attachments', '.obsidian']):
                    continue
                for fname in files:
                    if not fname.endswith('.md'):
                        continue
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, 'r', encoding='utf-8') as f:
                            content = f.read(800)
                        cat_match = re.search(r'category:\s*(.+)', content)
                        cat = cat_match.group(1).strip() if cat_match else "Uncategorized"
                        stars_match = re.search(r'stars:\s*(\d+)', content)
                        stars = int(stars_match.group(1)) if stars_match else 0
                        lang_match = re.search(r'primary_language:\s*(.+)', content)
                        lang = lang_match.group(1).strip() if lang_match else "N/A"
                        cred_match = re.search(r'credibility_score:\s*([\d.]+)', content)
                        cred = float(cred_match.group(1)) if cred_match else 0
                        source_match = re.search(r'source:\s*(.+)', content)
                        source = source_match.group(1).strip() if source_match else ""

                        note_info = {
                            'name': fname[:-4],  # without .md
                            'category': cat,
                            'stars': stars,
                            'language': lang,
                            'credibility': cred,
                            'source': source,
                            'path': fpath,
                        }
                        all_notes.append(note_info)
                        if cat not in notes_by_category:
                            notes_by_category[cat] = []
                        notes_by_category[cat].append(note_info)
                        if '_review' in root:
                            review_notes.append(note_info)
                    except Exception:
                        pass

            # Generate master _index.md (full regeneration — it's a dashboard)
            index_path = os.path.join(vault_path, "_index.md")
            lines = []
            lines.append("---")
            lines.append("type: master-index")
            lines.append(f"last_updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
            lines.append(f"total_projects: {len(all_notes)}")
            lines.append("---")
            lines.append("")
            lines.append("# 📚 Projects Master Index")
            lines.append("")
            lines.append(f"> Auto-generated. Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
            lines.append(f"> Total projects: **{len(all_notes)}** | Categories: **{len(notes_by_category)}** | Review queue: **{len(review_notes)}**")
            lines.append("")
            lines.append("## 📁 By Category")
            lines.append("")
            for cat in sorted(notes_by_category.keys()):
                notes = notes_by_category[cat]
                lines.append(f"### {cat} ({len(notes)})")
                lines.append(f"→ [[_moc/{_safe_moc_name(cat)}|View MOC]]")
                lines.append("")
                # Top 5 by stars
                top = sorted(notes, key=lambda x: -x['stars'])[:5]
                for n in top:
                    lines.append(f"- [[{n['name']}]] — ⭐ {n['stars']} · 🔧 {n['language']} · 📊 {n['credibility']}/100")
                if len(notes) > 5:
                    lines.append(f"- ... and {len(notes) - 5} more in [[_moc/{_safe_moc_name(cat)}|MOC]]")
                lines.append("")

            # Review queue
            if review_notes:
                lines.append("## 🔍 Review Queue")
                lines.append("")
                for n in review_notes:
                    lines.append(f"- [[{n['name']}]] — ⚠️ Low confidence")
                lines.append("")

            # Top credibility
            if all_notes:
                top_cred = sorted(all_notes, key=lambda x: -x['credibility'])[:10]
                lines.append("## 🏆 Top Credibility (Top 10)")
                lines.append("")
                for i, n in enumerate(top_cred, 1):
                    lines.append(f"{i}. [[{n['name']}]] — 📊 {n['credibility']}/100")
                lines.append("")

            # By language
            lang_counts = {}
            for n in all_notes:
                lang = n['language']
                lang_counts[lang] = lang_counts.get(lang, 0) + 1
            if lang_counts:
                lines.append("## 💻 By Language")
                lines.append("")
                for lang, count in sorted(lang_counts.items(), key=lambda x: -x[1]):
                    lines.append(f"- {lang}: {count} projects")
                lines.append("")

            lines.append("---")
            lines.append(f"*This index is auto-updated after each processing run.*")

            # v0.09.5 — Phase 0 (dry-run): recorded, not performed.
            _dryrun.write_text(index_path, '\n'.join(lines))

            # Generate per-category MOCs
            for cat, notes in notes_by_category.items():
                moc_filename = _safe_moc_name(cat) + '.md'
                moc_path = os.path.join(moc_dir, moc_filename)

                moc_lines = []
                moc_lines.append("---")
                moc_lines.append("type: moc")
                moc_lines.append(f"category: {cat}")
                moc_lines.append(f"last_updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
                moc_lines.append(f"project_count: {len(notes)}")
                moc_lines.append("---")
                moc_lines.append("")
                moc_lines.append(f"# 📁 {cat}")
                moc_lines.append("")
                moc_lines.append(f"> {len(notes)} projects in this category")
                moc_lines.append("")
                moc_lines.append("## Projects")
                moc_lines.append("")
                for n in sorted(notes, key=lambda x: -x['stars']):
                    moc_lines.append(f"- [[{n['name']}]] — ⭐ {n['stars']} · 🔧 {n['language']} · 📊 {n['credibility']}/100")
                moc_lines.append("")
                moc_lines.append(f"← Back to [[_index|Master Index]]")

                # v0.09.5 — Phase 0 (dry-run): recorded, not performed.
                _dryrun.write_text(moc_path, '\n'.join(moc_lines))

            self.log_message.emit(
                f"📚 Master index updated: {len(all_notes)} projects, {len(notes_by_category)} MOCs generated",
                "success"
            )
        except Exception as e:
            self.log_message.emit(f"Failed to generate master index: {e}", "warning")

    def _generate_final_report(self, link_tracker_report=None):
        """v25 pre-flight: generate a comprehensive Markdown report in the
        vault root after processing finishes.

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
            report_path = os.path.join(vault_path, f"_processing_report_{timestamp}.md")

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
                    lines.append(f"| 🚫 Blocked/self domains (recorded in _inbox) | {link_tracker_report.get('blocked_recorded', 0)} |")
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
                    # Group by platform for the report
                    platform_counts = {}
                    for u in non_github:
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
        Saved in the vault root as 'processing_summary_YYYYMMDD_HHMMSS.txt'."""
        try:
            vault_path = self.config.get('vault_path', '')
            if not vault_path or not os.path.isdir(vault_path):
                return None

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"processing_summary_{timestamp}.txt"
            filepath = os.path.join(vault_path, filename)

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
            # to accumulate in the vault root forever (and VaultSeal
            # committed every one of them). Keep only the newest
            # ``summary_keep_last`` (default 10). The matcher is the exact
            # processing_summary_*.txt pattern — nothing else is ever
            # touched, and dry-run records the removals instead of
            # performing them. ``summary_keep_last`` <= 0 keeps everything.
            keep = int((self.config or {}).get('summary_keep_last', 10) or 0)
            if keep > 0:
                try:
                    existing = sorted(
                        f for f in os.listdir(vault_path)
                        if f.startswith('processing_summary_')
                        and f.endswith('.txt'))
                    for old_name in existing[:-keep]:
                        _dryrun.remove(os.path.join(vault_path, old_name))
                except OSError:
                    pass

            return filepath
        except Exception as e:
            self.log_message.emit(f"Failed to generate summary log: {e}", "warning")
            return None
