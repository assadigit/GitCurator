#!/usr/bin/env python3
"""
test_retrycry.py — v0.63.1: the false retry cries, silenced by the truth.

The owner's report (v0.63.0 session, verbatim): "A weird message shows
on the GUI, which ask for retrying repos and websites. for example xxx
repos need retry — retry now! … every repo and website is processed and
tidy, and nothing needs retry. even clicking on retry it does nothing,
just take sometimes, to show there is no update and nothing message
(empty and fully processed message)."

Root causes — every "needs retry" cry was reading a ledger nobody ever
settled:
  1. CacheDB.failed_repos — the startup "N repos failed in previous
     runs" line counted rows that could NEVER resolve: WEBSITE urls
     enqueued by Verify Vault's Phase-3 fallback (the queue's only
     resolver is the GitHub pipeline's success path), repos resolved
     under drifted URL spellings (exact-string match), repos that later
     took a skip path ("already in vault" never resolved anything), and
     plain STACKING (a bare INSERT per failure — one repo counted many
     times over).
  2. LinkTracker.get_reconciliation_urls — the banner's count probed
     only the GITHUB vault, so every website row (walled fetches with
     _review placeholders, stored notes with empty paths) read as
     "needs retry" forever; the banner's button then fed them to the
     GitHub loop, which can only answer "Skipping non-GitHub URL" — the
     owner's "clicking on retry it does nothing", exactly.
  3. get_all_clear — the same vault-blind probe held the bot-queue
     mark-read hostage ("Some repos failed — NOT marked as read") while
     the Websites pipeline's own retry queue had the links all along.

Covered here (hermetic — temp vaults, no network):
  1. failed_rows_truth — the pure classifier behind the startup truth
     pass: vault-present repos are stale, absent ones are missing,
     website urls are never the repos queue's business, no probe
     degrades to the old loud behavior.
  2. CacheDB — add_failed never stacks and keys by the normalized URL;
     mark_failed_resolved matches both spellings (raw legacy rows and
     normalized keys); resolve_failed_urls settles a whole list.
  3. get_reconciliation_urls — github-only counting; website rows are
     set aside (honest count on set_aside_websites) and never cried;
     the v0.38.0 healing law is untouched.
  4. get_all_clear — non-github rows never block; a genuinely-missing
     github row still does (the old guarantee).
  5. source contracts — the startup check runs the truth pass, Verify
     Vault enqueues github links only, the worker settles queue rows on
     every terminal skip, the banner speaks of repo links, and the
     changelog keeps its beat.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)

_REPO_ROOT = os.path.dirname(_APP_ROOT)

from gitcurator.gui.cache_db import CacheDB, failed_rows_truth
from gitcurator.gui.link_tracker import LinkTracker


# ---------------------------------------------------------------------------
# Hermetic helpers
# ---------------------------------------------------------------------------

def _note(vault, rel, url, body="x" * 300):
    """Write a vault note whose frontmatter source is the URL (the
    VaultIndex dedupe key)."""
    path = os.path.join(vault, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"---\nsource: {url}\ntitle: t\n---\n{body}\n")
    return path


def _row(url, status, note_path=None, error=None, kind="github"):
    return {"url": url, "normalized": url, "type": kind, "status": status,
            "note_path": note_path, "error": error, "processed_at": None}


def _manifest(vault, links, batch_id="b1"):
    with open(os.path.join(vault, "links_manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump({"batch_id": batch_id, "source": "bot",
                   "total_links": len(links), "links": links}, f)


# ---------------------------------------------------------------------------
# 1. failed_rows_truth — the pure classifier
# ---------------------------------------------------------------------------

class TestFailedRowsTruth(unittest.TestCase):
    """The startup truth pass's brain — no DB, no files, just the rows
    and a probe."""

    ROWS = [
        ("https://example.com/walled-article", "failed Phase 3 verification"),
        ("https://github.com/owner/stale", "timeout"),
        ("https://github.com/owner/missing", "gave up"),
        ("", "a null-url row is nobody's retry"),
    ]

    def test_website_rows_are_never_the_repos_queue(self):
        stale, missing, websites = failed_rows_truth(self.ROWS)
        self.assertEqual(websites,
                         ["https://example.com/walled-article"])
        self.assertNotIn("https://example.com/walled-article",
                         stale + missing)

    def test_probe_separates_stale_from_missing(self):
        stale, missing, websites = failed_rows_truth(
            self.ROWS, github_has=lambda u: "stale" in u)
        self.assertEqual(stale, ["https://github.com/owner/stale"])
        self.assertEqual(missing, ["https://github.com/owner/missing"])
        self.assertEqual(websites, ["https://example.com/walled-article"])

    def test_no_probe_keeps_the_old_loud_behavior(self):
        """A broken/absent vault index must not silence a genuine cry —
        every github row reads as missing (the old count's behavior)."""
        stale, missing, _w = failed_rows_truth(self.ROWS, github_has=None)
        self.assertEqual(stale, [])
        self.assertEqual(sorted(missing), ["https://github.com/owner/missing",
                                           "https://github.com/owner/stale"])

    def test_null_urls_are_skipped_entirely(self):
        for bucket in failed_rows_truth(self.ROWS):
            self.assertNotIn("", bucket)
            self.assertNotIn(None, bucket)

    def test_legacy_row_shapes_degrade_quietly(self):
        """Rows arrive as tuples from get_failed_urls, but a stray dict
        or None never breaks the pass."""
        stale, missing, websites = failed_rows_truth(
            [None, {"url": "https://example.com/x"},
             ("https://github.com/o/r", "e")])
        self.assertEqual(websites, ["https://example.com/x"])
        self.assertEqual(missing, ["https://github.com/o/r"])


# ---------------------------------------------------------------------------
# 2. CacheDB — the queue stops lying
# ---------------------------------------------------------------------------

class TestRetryQueueHonesty(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="retrycry-db-")
        self.db = os.path.join(self.tmp, "cache.db")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_add_failed_never_stacks(self):
        """Three failures of the same URL = ONE unresolved row (the old
        bare INSERT stacked them — the startup cry counted one repo
        many times over)."""
        cache = CacheDB(self.db)
        for i in range(3):
            cache.add_failed("https://github.com/o/r", f"failure {i}")
        rows = cache.get_failed_urls()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "failure 2")   # the fresher error wins
        cache.close()

    def test_add_failed_keys_by_the_normalized_url(self):
        """Trailing-slash and scheme drift land on the SAME row."""
        cache = CacheDB(self.db)
        cache.add_failed("https://github.com/o/r", "one")
        cache.add_failed("https://github.com/o/r/", "two")
        self.assertEqual(cache.get_failed_count(), 1)
        cache.close()

    def test_resolve_matches_both_spellings(self):
        """The v0.63.1 law: a row stored raw (legacy) or normalized
        resolves under either spelling — the exact drift that used to
        leave the queue crying forever."""
        cache = CacheDB(self.db)
        # a legacy-shaped row, written the old way (raw string)
        with cache._lock:
            cache.cursor.execute(
                "INSERT INTO failed_repos (url, error, failed_at, "
                "retry_count) VALUES (?, ?, ?, 0)",
                ("https://github.com/Owner/Repo", "legacy row",
                 "2026-01-01T00:00:00"))
            cache.conn.commit()
        # resolved under a trailing-slash spelling
        cache.mark_failed_resolved("https://github.com/Owner/Repo/")
        self.assertEqual(cache.get_failed_count(), 0)
        cache.close()

    def test_resolve_failed_urls_settles_a_whole_list(self):
        """The truth pass's bulk settle: stale + website rows clear in
        one call, and the count survives agreement with get_failed_urls."""
        cache = CacheDB(self.db)
        cache.add_failed("https://github.com/o/stale", "timeout")
        cache.add_failed("https://example.com/walled", "phase 3")
        cache.add_failed("https://github.com/o/missing", "gave up")
        flipped = cache.resolve_failed_urls(
            ["https://github.com/o/stale",
             "https://example.com/walled"])
        self.assertEqual(flipped, 2)
        self.assertEqual([r[0] for r in cache.get_failed_urls()],
                         ["https://github.com/o/missing"])
        self.assertEqual(cache.get_failed_count(), 1)
        cache.close()

    def test_resolved_history_is_never_touch_by_add(self):
        """add_failed updates only the UNRESOLVED row — the resolved
        history of a URL (its past failures) stays as the record."""
        cache = CacheDB(self.db)
        cache.add_failed("https://github.com/o/r", "first")
        cache.mark_failed_resolved("https://github.com/o/r")
        cache.add_failed("https://github.com/o/r", "second")
        with cache._lock:
            rows = cache.cursor.execute(
                "SELECT url, error, resolved FROM failed_repos").fetchall()
        cache.close()
        self.assertEqual(len(rows), 2)
        # one fresh unresolved cry + one resolved history row
        self.assertEqual(sorted(r[2] for r in rows), [0, 1])
        self.assertEqual(
            sorted(r[1] for r in rows), ["first", "second"])


# ---------------------------------------------------------------------------
# 3. get_reconciliation_urls — the banner counts what it can act on
# ---------------------------------------------------------------------------

class TestReconciliationGithubOnly(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="retrycry-rec-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(os.path.join(self.vault, "AI"), exist_ok=True)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_website_rows_are_set_aside_never_cried(self):
        """A walled website (failed, placeholder lives in the Websites
        vault) and a stored website with an empty note path were the
        permanent false cries — now they leave the count with an honest
        set-aside tally."""
        _manifest(self.vault, [
            _row("https://example.com/walled", "failed", error="403 wall",
                 kind="non-github"),
            _row("https://example.com/stored2", "processed", note_path="",
                 kind="non-github"),
        ])
        t = LinkTracker(self.vault)
        self.assertEqual(t.get_reconciliation_urls(), [])
        self.assertEqual(t.set_aside_websites, 2)

    def test_stored_website_with_valid_path_needs_no_aside(self):
        """A processed website whose note path resolves is simply fine —
        not a cry, not even an aside."""
        ws_note = _note(os.path.join(self.tmp, "ws"), "Design/site.md",
                        "https://example.com/stored")
        _manifest(self.vault, [
            _row("https://example.com/stored", "processed",
                 note_path=ws_note, kind="non-github"),
        ])
        t = LinkTracker(self.vault)
        self.assertEqual(t.get_reconciliation_urls(), [])
        self.assertEqual(t.set_aside_websites, 0)

    def test_github_law_untouched_healing(self):
        """The v0.38.0 regression wall: pending github rows whose notes
        are in the vault still heal (skipped, persisted, counted)."""
        _note(self.vault, "AI/one.md", "https://github.com/a/one")
        _manifest(self.vault, [
            _row("https://github.com/a/one", "pending"),
            _row("https://github.com/a/gone", "pending"),
        ])
        t = LinkTracker(self.vault)
        self.assertEqual(t.get_reconciliation_urls(),
                         ["https://github.com/a/gone"])
        self.assertEqual(t.reconciled_vault_hits, 1)
        healed = json.load(open(os.path.join(
            self.vault, "links_manifest.json"), encoding="utf-8"))
        by = {l["url"]: l for l in healed["links"]}
        self.assertEqual(by["https://github.com/a/one"]["status"], "skipped")

    def test_mixed_batch_only_github_counts(self):
        """The owner's exact composite: walled websites + a genuinely
        missing repo → the banner cries ONE repo link, the websites are
        set aside, the button can act on everything it counts."""
        _manifest(self.vault, [
            _row("https://example.com/w1", "failed", error="403",
                 kind="non-github"),
            _row("https://example.com/w2", "processing",
                 kind="non-github"),
            _row("https://github.com/a/gone", "failed", error="500"),
        ])
        t = LinkTracker(self.vault)
        self.assertEqual(t.get_reconciliation_urls(),
                         ["https://github.com/a/gone"])
        self.assertEqual(t.set_aside_websites, 2)

    def test_read_is_stable_across_instances(self):
        """Banner read == click read: two fresh trackers see the same
        count and the same set-aside tally (no drift between the cry
        and the act)."""
        _manifest(self.vault, [
            _row("https://example.com/w1", "failed", kind="non-github"),
            _row("https://github.com/a/gone", "pending"),
        ])
        t1 = LinkTracker(self.vault)
        t2 = LinkTracker(self.vault)
        self.assertEqual(t1.get_reconciliation_urls(),
                         t2.get_reconciliation_urls())
        self.assertEqual(t1.set_aside_websites,
                         t2.set_aside_websites)


# ---------------------------------------------------------------------------
# 4. get_all_clear — website rows never hold the queue hostage
# ---------------------------------------------------------------------------

class TestAllClearWebsiteRows(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="retrycry-clr-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(os.path.join(self.vault, "AI"), exist_ok=True)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _tracker_with(self, *rows):
        t = LinkTracker(self.vault)
        t.manifest = {"batch_id": "b", "source": "bot",
                      "total_links": len(rows), "links": list(rows)}
        return t

    def test_website_failed_does_not_block(self):
        """The walled website's ledger is the Websites pipeline's own
        retry queue — the bot-queue mark-read is no longer its hostage."""
        t = self._tracker_with(
            _row("https://example.com/w", "failed", error="403 wall",
                 kind="non-github"))
        self.assertTrue(t.get_all_clear())

    def test_website_processing_does_not_block(self):
        t = self._tracker_with(
            _row("https://example.com/w", "processing", kind="non-github"))
        self.assertTrue(t.get_all_clear())

    def test_github_missing_still_blocks(self):
        """The old guarantee: a genuinely-missing github row is real
        unfinished work and still blocks (kept from v0.38.0)."""
        t = self._tracker_with(_row("https://github.com/a/gone", "failed"))
        self.assertFalse(t.get_all_clear())

    def test_github_in_vault_still_does_not_block(self):
        _note(self.vault, "AI/one.md", "https://github.com/a/one")
        t = self._tracker_with(_row("https://github.com/a/one", "failed"))
        self.assertTrue(t.get_all_clear())

    def test_pending_non_github_rule_unchanged(self):
        """The v29.4 rule the reconfix suite pinned: non-github pending
        never blocks (a duplicate, already recorded elsewhere)."""
        t = self._tracker_with(
            _row("https://example.com/x", "pending", kind="non-github"))
        self.assertTrue(t.get_all_clear())


# ---------------------------------------------------------------------------
# 5. source contracts — the cry sites run the truth
# ---------------------------------------------------------------------------

class TestSourceContracts(unittest.TestCase):
    """The GUI wiring, pinned the house way (source contracts): the
    startup check runs the truth pass, Verify Vault enqueues github
    links only, the worker settles queue rows on the terminal skips,
    the banner speaks of repo links, the changelog keeps its beat."""

    def _read(self, *parts):
        with open(os.path.join(_REPO_ROOT, *parts), encoding="utf-8") as f:
            return f.read()

    def test_startup_check_runs_the_truth_pass(self):
        src = self._read("app", "gitcurator", "gui", "main_window",
                         "window.py")
        self.assertIn("failed_rows_truth", src)
        self.assertIn("resolve_failed_urls", src)
        # the naive count-only cry is retired
        self.assertNotIn("get_failed_count()", src)

    def test_verify_vault_enqueues_github_only(self):
        src = self._read("app", "gitcurator", "gui", "main_window",
                         "dashboard.py")
        self.assertIn("fl.get('type') == 'github'", src)

    def test_worker_settles_the_queue_on_terminal_skips(self):
        src = self._read("app", "gitcurator", "gui",
                         "processing_worker.py")
        for marker in ('"already in vault"', '"non-GitHub URL"',
                       '"404 quarantine (confirmed dead)"',
                       '"dismissed (note deleted)"',
                       '"invalid GitHub URL"', '"already in cache"'):
            self.assertIn(marker, src)
        self.assertGreaterEqual(src.count("cache.mark_failed_resolved(url)"), 6)

    def test_banner_speaks_of_repo_links(self):
        src = self._read("app", "gitcurator", "gui", "main_window",
                         "ui.py")
        self.assertIn("repo link{'s' if count != 1 else ''}", src)

    def test_version_and_changelog_beat(self):
        self.assertEqual(self._read("VERSION").strip(), "0.64.0")
        text = self._read("CHANGELOG.md")
        self.assertIn("## [0.63.1]", text)
        self.assertIn("retry", text.lower())


if __name__ == '__main__':
    unittest.main(verbosity=2)
