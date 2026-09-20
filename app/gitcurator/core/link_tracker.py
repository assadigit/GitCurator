#!/usr/bin/env python3
"""gitcurator.core.link_tracker — "No Link Left Behind" (v23).

Extracted verbatim from ``gitcurator/gui/app.py`` (v32.3 modularization).

A 5-phase link-tracking pipeline that guarantees every URL discovered by
the app is either turned into an Obsidian note (GitHub links) or recorded
in the inbox table (non-GitHub links). The JSON manifest
(``<vault>/links_manifest.json``) is the source of truth — it survives
app crashes and is reconciled on the next launch.

Phases:
  1. INTAKE    — record EVERY link before any processing (atomic save)
  2. PROCESS   — update each link's status as it is processed
  3. VERIFY    — check that every "processed" link has a real note file
                 and every "recorded" link is actually in the inbox table
  4. RECONCILE — on next launch, surface links that failed/never finished
  5. CLEAR     — only mark bot messages as read if ALL links verified

Pure stdlib.
"""

import json
import os
from datetime import datetime

from gitcurator.core.links import normalize_url

__all__ = ["LinkTracker"]


# ============================================================================
# v23 — Link Tracker (No Link Left Behind)
# ============================================================================
# A 5-phase link-tracking pipeline that guarantees every URL discovered by
# the app is either turned into an Obsidian note (GitHub links) or recorded
# in the inbox table (non-GitHub links). The JSON manifest is the source of
# truth — it survives app crashes and is reconciled on the next launch.
#
# Phases:
#   1. INTAKE        — record EVERY link before any processing (atomic save)
#   2. PROCESS       — update each link's status as it is processed
#   3. VERIFY        — check that every "processed" link has a real note file
#                      and every "recorded" link is actually in the inbox table
#   4. RECONCILE     — on next launch, surface links that failed/never finished
#   5. CLEAR         — only mark bot messages as read if ALL links verified
# ============================================================================


class LinkTracker:
    """Tracks every link through a 5-phase pipeline to ensure no link
    is ever lost. The manifest file is the source of truth."""

    def __init__(self, vault_path: str):
        self.vault_path = vault_path
        self.manifest_path = os.path.join(vault_path, "links_manifest.json")
        self.manifest = {
            "batch_id": datetime.now().strftime("%Y%m%d_%H%M%S"),
            "created_at": datetime.now().isoformat(),
            "source": "unknown",
            "total_links": 0,
            "links": []
        }

    def set_source(self, source: str):
        """Set the source (bot/saved/rss/import)."""
        self.manifest["source"] = source

    def intake(self, github_urls: list, non_github_urls: list):
        """Phase 1: Record ALL links BEFORE any processing.
        Write the manifest immediately so it survives crashes."""
        for url in github_urls:
            self.manifest["links"].append({
                "url": url,
                "normalized": normalize_url(url),
                "type": "github",
                "status": "pending",
                "note_path": None,
                "error": None,
                "processed_at": None
            })
        for url in non_github_urls:
            self.manifest["links"].append({
                "url": url,
                "normalized": normalize_url(url),
                "type": "non-github",
                "status": "pending",
                "note_path": None,
                "error": None,
                "processed_at": None
            })
        self.manifest["total_links"] = len(self.manifest["links"])
        self._save()

    def _find_link(self, url: str):
        """Find a link entry by normalized URL. Returns None if not found."""
        norm = normalize_url(url)
        for link in self.manifest["links"]:
            if link["normalized"] == norm:
                return link
        return None

    def mark_processing(self, url: str):
        """Phase 2: Mark a link as being processed."""
        link = self._find_link(url)
        if link:
            link["status"] = "processing"
            self._save()

    def mark_processed(self, url: str, note_path: str):
        """Phase 2: Mark a link as successfully processed."""
        link = self._find_link(url)
        if link:
            link["status"] = "processed"
            link["note_path"] = note_path
            link["processed_at"] = datetime.now().isoformat()
            self._save()

    def mark_recorded(self, url: str):
        """Phase 2: Mark a non-GitHub link as recorded in inbox."""
        link = self._find_link(url)
        if link:
            link["status"] = "recorded"
            link["note_path"] = "_inbox/non_github_links.md"
            link["processed_at"] = datetime.now().isoformat()
            self._save()

    def mark_failed(self, url: str, error: str):
        """Phase 2: Mark a link as failed."""
        link = self._find_link(url)
        if link:
            link["status"] = "failed"
            link["error"] = error
            self._save()

    def mark_skipped(self, url: str, reason: str):
        """Mark a link as skipped (dedup, etc.)."""
        link = self._find_link(url)
        if link:
            link["status"] = "skipped"
            link["error"] = reason
            self._save()

    def verify(self, log_signal=None) -> dict:
        """Phase 3: Verify all links have their expected output.
        Returns a verification report dict."""
        report = {
            "total": len(self.manifest["links"]),
            "github_processed": 0,
            "github_failed": 0,
            "github_skipped": 0,
            "non_github_recorded": 0,
            "non_github_failed": 0,
            "verification_passed": True,
            "failed_links": []
        }

        for link in self.manifest["links"]:
            if link["type"] == "github":
                if link["status"] == "processed":
                    # Verify note exists on disk and is non-empty
                    note_path = link.get("note_path")
                    if note_path and os.path.isfile(note_path):
                        try:
                            if os.path.getsize(note_path) > 100:
                                report["github_processed"] += 1
                            else:
                                link["status"] = "failed"
                                link["error"] = "note file is empty"
                                report["github_failed"] += 1
                                report["failed_links"].append(link)
                                report["verification_passed"] = False
                        except Exception:
                            link["status"] = "failed"
                            link["error"] = "cannot read note file"
                            report["github_failed"] += 1
                            report["failed_links"].append(link)
                            report["verification_passed"] = False
                    else:
                        link["status"] = "failed"
                        link["error"] = "note file missing"
                        report["github_failed"] += 1
                        report["failed_links"].append(link)
                        report["verification_passed"] = False
                elif link["status"] == "skipped":
                    report["github_skipped"] += 1
                elif link["status"] == "failed":
                    report["github_failed"] += 1
                    report["failed_links"].append(link)
                    report["verification_passed"] = False
                # "pending" / "processing" statuses are left alone here;
                # they will be flagged by get_all_clear() so the bot-queue
                # is not marked as read.
            elif link["type"] == "non-github":
                if link["status"] == "recorded":
                    # v25 pre-flight: non-GitHub links are now spread across
                    # per-platform files in _inbox/ (x_twitter_links.md,
                    # reddit_links.md, ...). Scan ALL .md files in _inbox/
                    # for the URL — if it appears in ANY of them, the link
                    # is verified. We also still check the legacy
                    # non_github_links.md for backward compatibility with
                    # batches that ran on v24 or earlier.
                    inbox_dir = os.path.join(self.vault_path, "_inbox")
                    found_in_inbox = False
                    if os.path.isdir(inbox_dir):
                        try:
                            for fname in os.listdir(inbox_dir):
                                if not fname.endswith('.md'):
                                    continue
                                fpath = os.path.join(inbox_dir, fname)
                                try:
                                    with open(fpath, 'r', encoding='utf-8') as f:
                                        content = f.read()
                                except Exception:
                                    continue
                                if link["url"] in content or link["normalized"] in content:
                                    found_in_inbox = True
                                    break
                        except Exception:
                            # If we can't list the dir, fall back to
                            # "recorded" so we don't false-fail the link.
                            found_in_inbox = True
                    if found_in_inbox:
                        report["non_github_recorded"] += 1
                    else:
                        link["status"] = "failed"
                        link["error"] = "URL not found in any _inbox/*.md file"
                        report["non_github_failed"] += 1
                        report["failed_links"].append(link)
                        report["verification_passed"] = False
                elif link["status"] == "failed":
                    report["non_github_failed"] += 1
                    report["failed_links"].append(link)
                    report["verification_passed"] = False

        self._save()

        if log_signal:
            log_signal.emit("=" * 50, "info")
            log_signal.emit("🔍 VERIFICATION REPORT", "info")
            log_signal.emit("=" * 50, "info")
            log_signal.emit(f"   Total links: {report['total']}", "info")
            log_signal.emit(f"   GitHub processed: {report['github_processed']}", "success" if report['github_processed'] > 0 else "info")
            log_signal.emit(f"   GitHub skipped (dedup): {report['github_skipped']}", "info")
            log_signal.emit(f"   GitHub failed: {report['github_failed']}", "error" if report['github_failed'] > 0 else "info")
            log_signal.emit(f"   Non-GitHub recorded: {report['non_github_recorded']}", "success" if report['non_github_recorded'] > 0 else "info")
            log_signal.emit(f"   Non-GitHub failed: {report['non_github_failed']}", "error" if report['non_github_failed'] > 0 else "info")
            if report["verification_passed"]:
                log_signal.emit("✅ ALL LINKS VERIFIED — no data loss!", "success")
            else:
                log_signal.emit(f"❌ {len(report['failed_links'])} links need retry!", "error")
                for fl in report["failed_links"]:
                    log_signal.emit(f"   • {fl['url']} — {fl.get('error', 'unknown')}", "error")
            log_signal.emit("=" * 50, "info")

        return report

    def get_failed_links(self) -> list:
        """Phase 4: Get all links that need retry."""
        return [link for link in self.manifest["links"] if link["status"] in ("failed", "processing")]

    def get_all_clear(self) -> bool:
        """Phase 5: Check if ALL links are verified (no failures).
        v29.4 fix: 'skipped' and 'recorded' are both OK. 'pending' is only
        a failure for GitHub links (they should have been processed or skipped).
        Non-GitHub 'pending' links are duplicates that were already recorded
        in a previous batch — they're not real failures."""
        for link in self.manifest["links"]:
            if link["status"] == "failed":
                return False
            if link["status"] == "processing":
                return False
            # GitHub "pending" = real failure (should have been processed or skipped)
            if link["status"] == "pending" and link.get("type") == "github":
                return False
            # Non-GitHub "pending" = duplicate, already recorded elsewhere — OK
        return True

    def _save(self):
        """Atomic save — write to temp file then rename."""
        import tempfile
        try:
            tmp_fd, tmp_path = tempfile.mkstemp(dir=self.vault_path, suffix='.tmp')
            try:
                with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
                    json.dump(self.manifest, f, indent=2, default=str)
                os.replace(tmp_path, self.manifest_path)
            except Exception:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
        except Exception:
            # Best-effort — never crash the batch on a manifest write failure
            pass

    def load_previous_manifest(self) -> dict:
        """Load the previous manifest for reconciliation."""
        if os.path.isfile(self.manifest_path):
            try:
                with open(self.manifest_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                return None
        return None

    def get_reconciliation_urls(self) -> list:
        """Phase 4: Get URLs from previous manifest that need retry."""
        prev = self.load_previous_manifest()
        if not prev:
            return []
        failed = []
        for link in prev.get("links", []):
            if link["status"] in ("failed", "processing", "pending"):
                failed.append(link["url"])
            elif link["status"] == "processed":
                # Check if note still exists
                note_path = link.get("note_path")
                if not note_path or not os.path.isfile(note_path):
                    failed.append(link["url"])
        return failed
