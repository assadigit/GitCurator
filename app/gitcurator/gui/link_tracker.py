"""LinkTracker — moved verbatim from gitcurator/gui/app.py (branch refactor/gui-app-split; see docs/history/REFACTOR_PLAN.md)."""

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

_APP_DIR = APP_DIR

from gitcurator.gui.link_helpers import normalize_url

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
        # v0.38.0 — the reconciliation false-positive fix: the tracker can
        # now consult the VAULT ITSELF (the same VaultIndex ground truth
        # the dedupe layer uses) before declaring a link unfinished.
        # ``_injected_index`` is the index a running worker shares (no
        # second vault scan); ``_gt_index`` caches a lazily-built one for
        # standalone tracker instances (startup read, banner refresh).
        self._injected_index = None
        self._gt_index = None
        # How many links the LAST get_reconciliation_urls() pass healed
        # against the vault (read by the startup logger to explain the
        # banner's truth to the owner).
        self.reconciled_vault_hits = 0

    def set_vault_index(self, index):
        """v0.38.0 — share an ALREADY-BUILT VaultIndex (the worker builds
        one at batch start and keeps it incrementally updated; a second
        full vault scan per finished batch would be pure waste)."""
        self._injected_index = index

    def _ground_truth(self):
        """v0.38.0 — the vault ground truth for reconciliation decisions:
        a VaultIndex keyed by normalized source URL, or None when the
        vault cannot be scanned. Injected index first, then a lazily-
        built one cached for the instance's lifetime. Never raises — a
        failed scan just falls back to the pure-manifest behavior."""
        if self._injected_index is not None:
            return self._injected_index
        if self._gt_index is not None:
            return self._gt_index
        if not (self.vault_path and os.path.isdir(self.vault_path)):
            return None
        try:
            # Deferred import — vault_index pulls the GUI icon kit; the
            # CLI paths that import this module must not pay for it.
            from gitcurator.gui.vault_index import VaultIndex
            idx = VaultIndex(self.vault_path)
            idx.rebuild()
            self._gt_index = idx
            return idx
        except Exception:
            return None

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
        """Phase 2: Mark a link as being processed.

        v0.06 — Perf: the transient "processing" state is NO LONGER written
        to disk. The old code rewrote the ENTIRE manifest JSON 2-3× per link
        (processing → processed/failed) — a 500-link batch performed ~1,000
        full-manifest writes. Crash recovery is unaffected: a link left as
        "pending" by a crash is treated exactly like one left as
        "processing" (both are retried); the durable terminal states
        (processed / failed / skipped / recorded) still save immediately."""
        link = self._find_link(url)
        if link:
            link["status"] = "processing"

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

    def mark_blocked(self, url: str, reason: str):
        """v0.21.0 — Mark a link as blocked/self-domain (never fetched).
        A distinct terminal status so the verification report can account
        for it explicitly instead of hiding it inside 'skipped'.
        v0.35.0 — the _inbox row is no longer the record for banned/self
        links (they are never collected at all); the manifest entry ITSELF
        is the record, and verify() counts it without demanding a row."""
        link = self._find_link(url)
        if link:
            link["status"] = "blocked"
            link["error"] = reason
            self._save()

    def verify(self, log_signal=None, extra_inbox_dirs=None) -> dict:
        """Phase 3: Verify all links have their expected output.
        Returns a verification report dict.

        v0.21.0 — FULL ACCOUNTING: the report now buckets every link the
        batch actually touched. The v0.20.0 websites-pipeline runs showed
        "GitHub processed: 0 / Non-GitHub recorded: 0 / ALL LINKS
        VERIFIED" while 16 links went through the Websites pipeline and
        247 were blocked — every link accounted for, but the REPORT
        couldn't say so. New buckets: websites notes / _review / skipped,
        blocked (omitted by design — v0.35.0: no _inbox row needed),
        pending (pipeline off/not set), and github-pending;
        ``accounted``/``unaccounted`` reconcile the sum against ``total``
        and the verdict requires BOTH no failures and a clean
        reconciliation. ``extra_inbox_dirs`` adds places to look for
        _inbox rows (the Websites vault's _inbox — where the tables live
        since v0.20.0; still used by the 'recorded' bucket)."""
        report = {
            "total": len(self.manifest["links"]),
            "github_processed": 0,
            "github_failed": 0,
            "github_skipped": 0,
            "github_pending": 0,
            "non_github_recorded": 0,
            "non_github_failed": 0,
            "websites_processed": 0,
            "websites_review": 0,
            "websites_skipped": 0,
            "blocked_recorded": 0,
            "non_github_pending": 0,
            "verification_passed": True,
            "failed_links": []
        }
        inbox_dirs = [os.path.join(self.vault_path, "_inbox")]
        for d in (extra_inbox_dirs or []):
            if d and d not in inbox_dirs:
                inbox_dirs.append(d)

        def _url_in_inbox(url: str) -> Optional[bool]:
            """True/False when the answer is known; None when the tables
            could not be read (tolerant — never false-fail on I/O)."""
            found = False
            read_any = False
            for inbox_dir in inbox_dirs:
                if not os.path.isdir(inbox_dir):
                    continue
                try:
                    for fname in os.listdir(inbox_dir):
                        if not fname.endswith('.md'):
                            continue
                        fpath = os.path.join(inbox_dir, fname)
                        try:
                            with open(fpath, 'r', encoding='utf-8') as f:
                                content = f.read()
                            read_any = True
                        except Exception:
                            continue
                        if url in content or normalize_url(url) in content:
                            found = True
                            break
                except Exception:
                    continue
                if found:
                    break
            if found:
                return True
            return None if not read_any else False

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
                        # v0.38.0 — a stale note_path is not automatically a
                        # failure: the note may have MOVED inside the vault
                        # (the owner's own reorganization). The ground truth
                        # is keyed by source URL, not by path — if the note
                        # is still in the vault, re-point the row at its new
                        # home and count it processed. Only a note that is
                        # gone from BOTH the recorded path and the vault is
                        # a real failure.
                        _new_path = self._vault_path_for(link["url"])
                        if _new_path:
                            link["note_path"] = _new_path
                            report["github_processed"] += 1
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
                elif link["status"] in ("pending", "processing"):
                    # A GitHub link that was never processed nor skipped is
                    # REAL unfinished work (get_all_clear blocks the bot-queue
                    # mark-read for exactly this reason) — the report must
                    # say so instead of being silently absent from the sum.
                    # v0.38.0 — BUT the manifest is a ledger, not the truth:
                    # if the note is already IN THE VAULT (an interrupted
                    # batch's intake rows that a later batch stored, or the
                    # owner's own move), the link is done — heal the row to
                    # skipped and account it, instead of crying pending.
                    if self._vault_has(link["url"]):
                        link["status"] = "skipped"
                        link["error"] = "already in vault (reconciled)"
                        link["note_path"] = self._vault_path_for(link["url"]) \
                            or link.get("note_path")
                        link["processed_at"] = datetime.now().isoformat()
                        report["github_skipped"] += 1
                    else:
                        report["github_pending"] += 1
                        report["verification_passed"] = False
            elif link["type"] == "non-github":
                if link["status"] == "recorded":
                    # v25 pre-flight: non-GitHub links are now spread across
                    # per-platform files in _inbox/ (x_twitter_links.md,
                    # reddit_links.md, ...). Scan ALL .md files in _inbox/
                    # for the URL — if it appears in ANY of them, the link
                    # is verified. We also still check the legacy
                    # non_github_links.md for backward compatibility with
                    # batches that ran on v24 or earlier.
                    found = _url_in_inbox(link["url"])
                    if found is False:
                        link["status"] = "failed"
                        link["error"] = "URL not found in any _inbox/*.md file"
                        report["non_github_failed"] += 1
                        report["failed_links"].append(link)
                        report["verification_passed"] = False
                    else:
                        # found, or the tables were unreadable — the intake
                        # writer is best-effort; never false-fail on I/O.
                        report["non_github_recorded"] += 1
                elif link["status"] == "blocked":
                    # v0.21.0 — blocked/self-domain links used to need an
                    # _inbox row as their record. v0.35.0 — the owner's
                    # omission rule: banned links are NEVER collected (no
                    # note, no _review, no _inbox row) — this manifest
                    # entry IS the record, and it counts as accounted all
                    # by itself.
                    report["blocked_recorded"] += 1
                elif link["status"] == "processed":
                    # v0.21.0 — the Websites pipeline wrote a note (a _review
                    # placeholder counts: it IS a note, with a retry
                    # scheduled). Verify the file is really on disk.
                    note_path = link.get("note_path")
                    ok_note = bool(note_path) and os.path.isfile(note_path) \
                        and os.path.getsize(note_path) > 50
                    if ok_note:
                        if "_review" in (note_path or "").replace("\\", "/"):
                            report["websites_review"] += 1
                        else:
                            report["websites_processed"] += 1
                    else:
                        link["status"] = "failed"
                        link["error"] = "websites note file missing"
                        report["non_github_failed"] += 1
                        report["failed_links"].append(link)
                        report["verification_passed"] = False
                elif link["status"] == "skipped":
                    # Websites pipeline dedupe / dismissed / retries
                    # exhausted — a terminal, accounted outcome.
                    report["websites_skipped"] += 1
                elif link["status"] == "failed":
                    report["non_github_failed"] += 1
                    report["failed_links"].append(link)
                    report["verification_passed"] = False
                elif link["status"] in ("pending", "processing"):
                    # Non-GitHub pending = the Websites pipeline is off or
                    # no vault is set (the manifest warning at intake says
                    # so) — recorded as its own bucket, NOT a failure
                    # (same semantics as get_all_clear).
                    report["non_github_pending"] += 1

        # Reconciliation: every bucket sums to the total, or something
        # escaped every path (a bug — loud, never silent).
        report["accounted"] = sum(
            report[k] for k in (
                "github_processed", "github_failed", "github_skipped",
                "github_pending", "non_github_recorded",
                "non_github_failed", "websites_processed",
                "websites_review", "websites_skipped", "blocked_recorded",
                "non_github_pending"))
        report["unaccounted"] = report["total"] - report["accounted"]
        report["accounting_ok"] = report["unaccounted"] == 0

        self._save()

        if log_signal:
            log_signal.emit("=" * 50, "info")
            log_signal.emit("🔍 VERIFICATION REPORT", "info")
            log_signal.emit("=" * 50, "info")
            log_signal.emit(f"   Total links: {report['total']}", "info")
            log_signal.emit(f"   GitHub processed: {report['github_processed']}", "success" if report['github_processed'] > 0 else "info")
            log_signal.emit(f"   GitHub skipped (dedup): {report['github_skipped']}", "info")
            if report['github_pending']:
                log_signal.emit(f"   GitHub pending (unfinished!): {report['github_pending']}", "warning")
            log_signal.emit(f"   GitHub failed: {report['github_failed']}", "error" if report['github_failed'] > 0 else "info")
            if report['non_github_recorded']:
                log_signal.emit(f"   Non-GitHub recorded (inbox): {report['non_github_recorded']}", "info")
            if report['websites_processed'] or report['websites_review'] or report['websites_skipped']:
                log_signal.emit(f"   Websites notes: {report['websites_processed']}", "success" if report['websites_processed'] > 0 else "info")
                log_signal.emit(f"   Websites in _review (retry scheduled): {report['websites_review']}", "info")
                log_signal.emit(f"   Websites skipped (dedup): {report['websites_skipped']}", "info")
            if report['blocked_recorded']:
                log_signal.emit(f"   Blocked/self domains (omitted by design — never collected): {report['blocked_recorded']}", "info")
            if report['non_github_pending']:
                log_signal.emit(f"   Non-GitHub pending (websites pipeline off / no vault): {report['non_github_pending']}", "warning")
            if report['non_github_failed']:
                log_signal.emit(f"   Non-GitHub failed: {report['non_github_failed']}", "error")
            if report['accounting_ok']:
                log_signal.emit(f"   🧮 Accounting: {report['accounted']}/{report['total']} links accounted for ✓", "info")
            else:
                log_signal.emit(f"   🧮 Accounting: only {report['accounted']}/{report['total']} accounted for — {report['unaccounted']} escaped every bucket!", "error")
            if report["verification_passed"] and report["accounting_ok"]:
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

    def _vault_has(self, url: str) -> bool:
        """v0.38.0 — is this URL's note already in the vault? (Ground-truth
        helper; False whenever the vault cannot be scanned — a missing
        index must degrade to the old manifest-only behavior.)"""
        idx = self._ground_truth()
        if idx is None:
            return False
        try:
            return bool(idx.has_url(url))
        except Exception:
            return False

    def vault_has(self, url: str) -> bool:
        """v0.38.0 — public face of _vault_has: the finished-batch logger
        lists only GENUINELY unfinished links (the same truth
        get_all_clear uses, so the log line and the verdict can never
        disagree)."""
        return self._vault_has(url)

    def _vault_path_for(self, url: str):
        """v0.38.0 — the note path the vault currently holds for this URL,
        or None. Used to re-point rows whose recorded note_path went
        stale (the owner moved notes inside the vault)."""
        idx = self._ground_truth()
        if idx is None:
            return None
        try:
            return idx.get_path(url)
        except Exception:
            return None

    def get_all_clear(self) -> bool:
        """Phase 5: Check if ALL links are verified (no failures).
        v29.4 fix: 'skipped' and 'recorded' are both OK. 'pending' is only
        a failure for GitHub links (they should have been processed or skipped).
        Non-GitHub 'pending' links are duplicates that were already recorded
        in a previous batch — they're not real failures.
        v0.38.0 — a pending GitHub link whose note IS in the vault is NOT
        unfinished work: an interrupted batch left the row pending and a
        later batch (or the owner) stored the note anyway. The vault is
        the ground truth — such a link no longer blocks the bot-queue
        mark-read with a false 'not verified' verdict."""
        for link in self.manifest["links"]:
            if link["status"] == "failed":
                # v0.38.0 — even a FAILED row is done if the note is in the
                # vault (the failure predates a successful later attempt).
                if not self._vault_has(link["url"]):
                    return False
            if link["status"] == "processing":
                if not self._vault_has(link["url"]):
                    return False
            # GitHub "pending" = real failure (should have been processed
            # or skipped) — unless the vault already holds the note.
            if link["status"] == "pending" and link.get("type") == "github":
                if not self._vault_has(link["url"]):
                    return False
            # Non-GitHub "pending" = duplicate, already recorded elsewhere — OK
        return True

    def _save(self):
        """Atomic save — write to temp file then rename."""
        self._save_manifest_dict(self.manifest)

    def _save_manifest_dict(self, manifest: dict):
        """v0.38.0 — the atomic writer factored out of _save so
        reconciliation can persist a HEALED previous manifest without
        touching self.manifest (the next batch's intake must never
        inherit healed rows through a shared in-memory object)."""
        # v0.09.5 — Phase 0 (dry-run): the manifest is recorded, not
        # written, while a --dry-run batch is active.
        if _dryrun.is_enabled():
            _dryrun.record('write', self.manifest_path,
                           note='links_manifest.json (link tracker)')
            return
        import tempfile
        try:
            tmp_fd, tmp_path = tempfile.mkstemp(dir=self.vault_path, suffix='.tmp')
            try:
                with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
                    json.dump(manifest, f, indent=2, default=str)
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
        """Phase 4: Get URLs from previous manifest that need retry.

        v0.38.0 — THE FALSE-POSITIVE FIX. The old logic trusted the
        manifest alone: every pending/processing/failed row was declared
        a retry, and every processed row whose recorded note_path no
        longer resolved was declared a retry too. But the manifest is a
        LEDGER, not the truth — the bot queue re-serves the entire
        history on every sync, so a batch interrupted early (Stop, app
        close, crash) leaves hundreds of rows "pending" that are, in
        fact, ALREADY STORED in the vault from earlier batches. The
        owner saw "620 links from the previous batch need retry" while
        all 620 projects sat safely in the vault.

        The vault itself is the ground truth (the same VaultIndex the
        dedupe layer treats as authoritative). Reconciliation now heals
        against it:
          - pending/processing/failed rows whose note IS in the vault →
            healed to skipped ("already in vault (reconciled)"), never
            retried, and the healed manifest is written back so the
            banner, the startup log and get_all_clear all agree;
          - processed rows with a stale note_path (the owner moved the
            note inside the vault) → re-pointed at the note's new path;
          - only links absent from BOTH the manifest's good graces and
            the vault are returned for retry.
        ``self.reconciled_vault_hits`` carries the healed count for the
        startup log line. A vault that cannot be scanned degrades to
        the old manifest-only behavior."""
        self.reconciled_vault_hits = 0
        prev = self.load_previous_manifest()
        if not prev:
            return []
        failed = []
        healed = False
        for link in prev.get("links", []):
            if link["status"] in ("failed", "processing", "pending"):
                if self._vault_has(link["url"]):
                    # Stored — the ledger just never got its terminal
                    # update. Heal the row; it is nobody's retry.
                    link["status"] = "skipped"
                    link["error"] = "already in vault (reconciled)"
                    link["note_path"] = self._vault_path_for(link["url"]) \
                        or link.get("note_path")
                    link["processed_at"] = datetime.now().isoformat()
                    healed = True
                    self.reconciled_vault_hits += 1
                else:
                    failed.append(link["url"])
            elif link["status"] == "processed":
                # Check if note still exists — at the recorded path, or
                # anywhere in the vault (a move is not a loss).
                note_path = link.get("note_path")
                if not note_path or not os.path.isfile(note_path):
                    new_path = self._vault_path_for(link["url"])
                    if new_path:
                        link["note_path"] = new_path
                        healed = True
                    else:
                        failed.append(link["url"])
        if healed:
            # Persist the HEALED previous manifest. self.manifest stays
            # untouched — it belongs to the next batch's intake.
            self._save_manifest_dict(prev)
        return failed
