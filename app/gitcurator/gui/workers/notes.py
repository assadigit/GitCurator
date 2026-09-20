#!/usr/bin/env python3
"""gitcurator.gui.workers.notes — the note-generation mixin.

Master index, per-repo inbox notes, the final report and the summary log
(verbatim methods of the original ProcessingWorker).

"""

import os, re
from datetime import datetime

from gitcurator.gui._qt import *  # noqa: F401,F403
from gitcurator.gui.workers._deps import *  # noqa: F401,F403
__all__ = ["NoteGenerationMixin"]


class NoteGenerationMixin:
    """NoteGenerationMixin — see module docstring (methods are verbatim moves)."""


    def _generate_master_index(self):
        """Generate/update master index (_index.md) + per-category MOCs (_moc/).
        Incremental — adds new entries with timestamps, keeps old entries."""
        try:
            vault_path = self.config.get('vault_path', '')
            if not vault_path or not os.path.isdir(vault_path):
                return

            moc_dir = os.path.join(vault_path, "_moc")
            os.makedirs(moc_dir, exist_ok=True)

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

            with open(index_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines))

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

                with open(moc_path, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(moc_lines))

            self.log_message.emit(
                f"📚 Master index updated: {len(all_notes)} projects, {len(notes_by_category)} MOCs generated",
                "success"
            )
        except Exception as e:
            self.log_message.emit(f"Failed to generate master index: {e}", "warning")

    def _create_inbox_notes(self, non_github_urls, source="Saved"):
        """Classify non-GitHub links by platform and write to per-platform files.

        v25 pre-flight: previously every non-GitHub link landed in a single
        ``_inbox/non_github_links.md`` file. For 200-300 link batches, this
        became an unmanageable wall of mixed-platform URLs. Each platform now
        gets its own .md file (x_twitter_links.md, reddit_links.md, ...).

        The actual work is delegated to the module-level
        ``write_inbox_links_by_platform`` helper so MainWindow.check_bot_queue
        and ProcessingWorker.run() share the exact same code path."""
        write_inbox_links_by_platform(
            self.config.get('vault_path', ''),
            non_github_urls,
            source=source,
            log_callback=self.log_message.emit,
        )

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
                lines.append(f"| ✅ Non-GitHub recorded | {link_tracker_report.get('non_github_recorded', 0)} |")
                lines.append(f"| ❌ Non-GitHub failed | {link_tracker_report.get('non_github_failed', 0)} |")
                verdict = ("✅ ALL LINKS VERIFIED — NO DATA LOSS!"
                           if link_tracker_report.get('verification_passed')
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

            lines.append("---")
            lines.append(f"*Report generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")

            try:
                with open(report_path, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(lines))
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

            with open(filepath, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines))

            return filepath
        except Exception as e:
            self.log_message.emit(f"Failed to generate summary log: {e}", "warning")
            return None
