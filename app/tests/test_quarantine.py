#!/usr/bin/env python3
"""
test_quarantine.py — regression tests for the v0.08 404 QUARANTINE.

Owner report: "there are 6-8 GitHub repos that are deleted and became 404;
the app repeats to find and 404 them again and add to the logs. After 2-3
tries across different sessions, the system should ignore those links."

Locked in here:
  1. Schema: decommissioned_repos gains fail_count (fresh DBs AND a
     migration on pre-v0.08 DBs — old rows become CONFIRMED dead so the
     fix takes effect on the owner's already-poisoned cache immediately).
  2. record_404 counts consecutive failures ACROSS sessions (separate
     CacheDB instances sharing one cache.db file).
  3. is_dead_link / get_dead_url_set return True/contain the URL only at
     DEAD_LINK_THRESHOLD (3); attempts 1-2 stay "unconfirmed".
  4. reset_dead_links clears the quarantine (false-positive recovery).
  5. DEAD_LINK_THRESHOLD exists and is 3 (the owner's "2-3 tries" spec).

Run:  python -m unittest tests.test_quarantine -v
"""

import os
import sqlite3
import sys
import tempfile
import unittest

# Make the app dir importable no matter where we run from.
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from gitcurator.gui.app import (  # noqa: E402
    DEAD_LINK_THRESHOLD,
    CacheDB,
)


class QuarantineTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmp.name, "cache.db")

    def tearDown(self):
        self._tmp.cleanup()

    # -- schema ----------------------------------------------------------

    def test_fresh_schema_has_fail_count(self):
        cache = CacheDB(self.db_path)
        cols = [r[1] for r in cache.cursor.execute(
            "PRAGMA table_info(decommissioned_repos)").fetchall()]
        cache.close()
        self.assertIn("fail_count", cols)

    def test_migration_marks_pre_v08_rows_confirmed(self):
        # A pre-v0.08 cache: decommissioned_repos WITHOUT fail_count.
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "CREATE TABLE decommissioned_repos ("
            " url TEXT PRIMARY KEY, reason TEXT, decommissioned_at TIMESTAMP)")
        conn.execute(
            "INSERT INTO decommissioned_repos VALUES "
            "('https://github.com/dead/repo', '404 Not Found', '2025-01-01')")
        conn.commit()
        conn.close()
        # Opening through CacheDB runs the migration; the pre-existing row
        # was 404'd in earlier sessions, so it must be CONFIRMED dead now.
        cache = CacheDB(self.db_path)
        self.assertTrue(cache.is_dead_link("https://github.com/dead/repo"))
        self.assertIn(
            "https://github.com/dead/repo", cache.get_dead_url_set())
        cache.close()

    # -- cross-session counting -------------------------------------------

    def test_record_404_counts_across_sessions(self):
        url = "https://github.com/gone/forever"
        counts = []
        for _session in range(DEAD_LINK_THRESHOLD):
            cache = CacheDB(self.db_path)          # NEW session, same DB file
            counts.append(cache.record_404(url))
            cache.close()
        self.assertEqual(counts, [1, 2, 3])
        # only the LAST session's entry is confirmed dead
        cache = CacheDB(self.db_path)
        self.assertEqual(counts[-1], DEAD_LINK_THRESHOLD)
        self.assertTrue(cache.is_dead_link(url))
        cache.close()

    def test_unconfirmed_entries_are_not_dead(self):
        url = "https://github.com/flaky/repo"
        cache = CacheDB(self.db_path)
        cache.record_404(url)
        cache.record_404(url)                      # 2 of 3 — still retriable
        self.assertFalse(cache.is_dead_link(url))
        self.assertNotIn(url, cache.get_dead_url_set())
        cache.close()

    def test_dead_set_only_contains_confirmed(self):
        live_url = "https://github.com/ok/repo"
        dead_url = "https://github.com/dead/repo"
        cache = CacheDB(self.db_path)
        for _ in range(DEAD_LINK_THRESHOLD):
            cache.record_404(dead_url)
        cache.record_404(live_url)                 # 1 attempt — unconfirmed
        dead = cache.get_dead_url_set()
        cache.close()
        self.assertIn(dead_url, dead)
        self.assertNotIn(live_url, dead)

    # -- reset (false-positive recovery) -----------------------------------

    def test_reset_dead_links_clears_everything(self):
        url = "https://github.com/private/now-public"
        cache = CacheDB(self.db_path)
        for _ in range(DEAD_LINK_THRESHOLD):
            cache.record_404(url)
        self.assertTrue(cache.is_dead_link(url))
        removed = cache.reset_dead_links()
        cache.close()
        self.assertGreaterEqual(removed, 1)
        cache = CacheDB(self.db_path)              # persists across sessions
        self.assertFalse(cache.is_dead_link(url))
        self.assertEqual(cache.get_dead_url_set(), set())
        cache.close()

    def test_reset_dead_links_single_url(self):
        keep = "https://github.com/dead/one"
        drop = "https://github.com/dead/two"
        cache = CacheDB(self.db_path)
        for u in (keep, drop):
            for _ in range(DEAD_LINK_THRESHOLD):
                cache.record_404(u)
        removed = cache.reset_dead_links(drop)
        cache.close()
        self.assertEqual(removed, 1)
        cache = CacheDB(self.db_path)
        self.assertTrue(cache.is_dead_link(keep))
        self.assertFalse(cache.is_dead_link(drop))
        cache.close()

    # -- legacy API stays compatible ---------------------------------------

    def test_get_all_decommissioned_shape_unchanged(self):
        cache = CacheDB(self.db_path)
        cache.record_404("https://github.com/dead/repo")
        rows = cache.get_all_decommissioned()
        cache.close()
        # legacy (url, reason) tuple shape — cloudflare_sync + old callers
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "https://github.com/dead/repo")
        self.assertEqual(rows[0][1], "404 Not Found")

    def test_threshold_is_three(self):
        # the owner's spec: "after 2-3 tries across different sessions"
        self.assertEqual(DEAD_LINK_THRESHOLD, 3)


class MergedLineageTests(unittest.TestCase):
    """v0.09 (lineage merge) — the unified quarantine keeps the v0.07
    lineage's guarantees ON TOP of the v0.08 machinery:

      * the threshold is CONFIGURABLE (notfound_strike_threshold — GUI
        spinbox, CLI --strikes N, config.json; v0.08 had it hardcoded),
      * consecutive-miss semantics: a SUCCESSFUL fetch resets the counter
        (v0.08 counted attempts forever — stale strikes could quarantine
        a live repo after one more transient miss),
      * the v0.07 notfound_strikes table migrates into fail_count on
        first open (so our-lineage caches keep their history),
      * get_quarantine_stats shows in-progress AND confirmed rows.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmp.name, "cache.db")

    def tearDown(self):
        self._tmp.cleanup()

    def test_threshold_is_configurable(self):
        from gitcurator.gui.app import dead_link_threshold
        self.assertEqual(dead_link_threshold({}), 3)
        self.assertEqual(dead_link_threshold({"notfound_strike_threshold": 5}), 5)
        self.assertEqual(dead_link_threshold({"notfound_strike_threshold": 0}), 2)
        self.assertEqual(dead_link_threshold({"notfound_strike_threshold": "bogus"}), 3)

    def test_threshold_parameter_steers_confirmation(self):
        url = "https://github.com/patient/repo"
        cache = CacheDB(self.db_path)
        for _ in range(3):
            cache.record_404(url)                  # 3 attempts
        self.assertTrue(cache.is_dead_link(url))           # default thr 3
        self.assertFalse(cache.is_dead_link(url, 5))       # configured thr 5
        self.assertEqual(cache.get_dead_url_set(5), set()) # not confirmed at 5
        self.assertEqual(len(cache.get_dead_urls(5)), 0)
        cache.close()

    def test_success_resets_the_counter(self):
        # The worker calls reset_dead_links(url) when get_repo SUCCEEDS —
        # consecutive semantics (a restored repo never carries stale strikes).
        url = "https://github.com/back/from-the-dead"
        cache = CacheDB(self.db_path)
        cache.record_404(url)
        cache.record_404(url)                      # 2 of 3
        cache.reset_dead_links(url)                # SUCCESS — counter fresh
        cache.close()
        cache = CacheDB(self.db_path)              # next session
        self.assertEqual(cache.record_404(url), 1) # started over, not 3
        self.assertFalse(cache.is_dead_link(url))
        cache.close()

    def test_v007_strike_table_migrates(self):
        # A v0.07-lineage cache: notfound_strikes table with counts.
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "CREATE TABLE notfound_strikes ("
            " url TEXT PRIMARY KEY, strikes INTEGER,"
            " first_seen TIMESTAMP, last_seen TIMESTAMP)")
        conn.execute(
            "INSERT INTO notfound_strikes VALUES "
            "('https://github.com/a/gone', 2, '2025-01-01', '2025-01-02')")
        conn.execute(
            "INSERT INTO notfound_strikes VALUES "
            "('https://github.com/b/gone', 3, '2025-01-01', '2025-01-02')")
        # a URL already decommissioned in the same DB (lower count — the
        # migration must keep the HIGHER of the two)
        conn.execute(
            "CREATE TABLE decommissioned_repos ("
            " url TEXT PRIMARY KEY, reason TEXT, decommissioned_at TIMESTAMP,"
            " fail_count INTEGER NOT NULL DEFAULT 3)")
        conn.execute(
            "INSERT INTO decommissioned_repos VALUES "
            "('https://github.com/b/gone', '404 Not Found', '2025-01-01', 1)")
        conn.commit()
        conn.close()

        cache = CacheDB(self.db_path)              # migration runs on open
        stats = {url: count for url, _r, count, _a in cache.get_quarantine_stats()}
        cache.close()
        self.assertEqual(stats.get("https://github.com/a/gone"), 2)
        self.assertEqual(stats.get("https://github.com/b/gone"), 3)  # MAX kept
        # old table dropped — one system, one table
        conn = sqlite3.connect(self.db_path)
        left = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='table' AND name='notfound_strikes'").fetchone()[0]
        conn.close()
        self.assertEqual(left, 0)

    def test_quarantine_stats_shows_in_progress_rows(self):
        live = "https://github.com/still/there"
        dead = "https://github.com/long/gone"
        cache = CacheDB(self.db_path)
        cache.record_404(live)                     # 1 attempt — in progress
        for _ in range(3):
            cache.record_404(dead)                 # confirmed
        rows = cache.get_quarantine_stats()
        cache.close()
        urls = {u for u, _r, _c, _a in rows}
        self.assertEqual(urls, {live, dead})       # BOTH visible (unlike
        # get_dead_urls, which lists confirmed only)
        # most-strikes first
        self.assertEqual(rows[0][0], dead)


if __name__ == "__main__":
    unittest.main(verbosity=2)
