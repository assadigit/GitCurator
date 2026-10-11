"""tests/test_reposettled.py — v0.63.3, THE SETTLED REPOS LEDGER: the
owner's law settles the re-count-everything reflex on the GitHub side.

The owner's report (session, verbatim): "Do the same for github repos,
I want you to only count from here and now on. since older ones are
processed. and not needed you to read from the beginning."

The disease it closes (the repos twin of the websites' v0.63.2 report):
every queue check re-read the FULL bot history and re-classified it —
repos already in the vault were fine, but every repo WITHOUT a note
(a fetch that failed and was retried, a note the owner deleted, a
repo the owner resolved by hand, or simply history the system never
batched) counted as PENDING work forever. "Process All", "Process
New", the retry buttons and Verify All's missing-list kept feeding
that same pile back into batches and banners — "from the beginning",
every single time.

The law, layer by layer (all covered here — zero network, the temp-vault
hermetic pattern; the GUI wiring is pinned by source contracts, the
test_retrycry / test_settledledger house style):

* the ledger — ``CacheDB.settle_existing_repos`` (one time, meta-key
  guarded): seeds ``repos_settled`` from every URL the cache knows
  (processed / failed / decommissioned) plus the queue door's extras
  (the FULL bot history — every repo already sent to the bot is an
  "older one", whatever became of it), and gives the repos retry queue
  its settlement date (cleared — no startup cry ever re-serves an old
  link); ``is_repo_settled`` / ``settle_repos`` / ``unsettle_repo`` /
  ``settled_repos_count`` / ``get_settled_repo_set`` keep their
  contracts; a second call is a no-op;
* the ♻️ door — ``reset_dead_links`` (the repos twin of the master
  table's ♻️ revive): removing a quarantine row un-settles the SAME
  urls (and only those — every other settled repo keeps its
  settlement), so a reset repo is fetched like new again;
* the reconciliation gate — ``LinkTracker.get_reconciliation_urls``
  drops settled github rows (``set_aside_settled`` — the twin of
  set_aside_websites): a legacy manifest's failed rows for repos the
  settlement addressed never cry "repos need retry" again, while the
  v0.38.0 vault-healing law and genuinely-missing repos are untouched;
* source contracts — the queue classification gains the settled bucket
  and runs THE SETTLEMENT at its own door; "Process All" and "Process
  New" split settled repos out of their payloads; the worker's GitHub
  loop skips settled repos before any API call with one honest
  aggregate line; Verify All counts settled as accounted-for (never
  missing); the retry doors drop settled rows; the manual-resolve
  verdicts settle; the startup truth pass stays quiet pre-settlement;
  the banner refreshes after a queue check; the release bookkeeping
  keeps its beat.

No PyQt import at module level (the libEGL-less sandbox rule) — the
GUI wiring is pinned by source contracts; the banner and hero routing
live in their own suites' laws.
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

import gitcurator.gui.cache_db as _cache_db_mod
from gitcurator.gui.cache_db import CacheDB, REPOS_SETTLED_META_KEY
from gitcurator.gui.link_tracker import LinkTracker


# ---------------------------------------------------------------------------
# Hermetic helpers (the test_retrycry pattern, kept local)
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
# 1. The ledger
# ---------------------------------------------------------------------------

class TestTheLedger(unittest.TestCase):
    """CacheDB's settled ledger — the twin of WebsiteStateDB's."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='reposettled-')
        self.db = CacheDB(os.path.join(self.tmp, 'cache.db'))
        # A pre-settlement shape: one stored repo, one unresolved failed
        # row, one quarantined 404.
        self.db.add_processed(1, 'https://github.com/owner/stored',
                              'owner', 'stored', 'note.md', 'AI')
        self.db.add_failed('https://github.com/owner/failed', 'timeout')
        self.db.decommission('https://github.com/owner/gone', '404')

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_settle_existing_seeds_every_table_and_clears_the_queue(self):
        rep = self.db.settle_existing_repos()
        self.assertEqual(rep['settled'], 3)
        self.assertEqual(rep['retries_cleared'], 1)
        self.assertFalse(rep['already'])
        # every seed is settled, under the normalized grammar
        for u in ('https://github.com/owner/stored',
                  'https://github.com/owner/failed',
                  'https://github.com/owner/gone'):
            self.assertTrue(self.db.is_repo_settled(u), u)
        # the retry queue got its settlement date
        self.assertEqual(self.db.get_failed_urls(), [])
        # the meta guard is set
        self.assertTrue(self.db.repos_ledger_settled())
        self.assertIsNotNone(self.db.get_meta(REPOS_SETTLED_META_KEY))

    def test_settle_existing_is_idempotent(self):
        first = self.db.settle_existing_repos()
        self.assertFalse(first['already'])
        second = self.db.settle_existing_repos(
            extra_urls=['https://github.com/owner/brandnew'])
        self.assertEqual(second,
                         {'settled': 0, 'retries_cleared': 0, 'already': True})
        # a later call can never quietly swallow a NEW repo into the law
        self.assertFalse(self.db.is_repo_settled(
            'https://github.com/owner/brandnew'))

    def test_settle_existing_takes_extra_urls(self):
        """The queue door's extras: the FULL bot history at the
        settlement door — a repo never stored, never failed, never
        quarantined is still an "older one" (already sent to the bot)."""
        rep = self.db.settle_existing_repos(extra_urls=[
            'https://github.com/owner/never-processed',
            'https://github.com/owner/stored',   # a duplicate spelling
        ])
        self.assertEqual(rep['settled'], 4)
        self.assertTrue(self.db.is_repo_settled(
            'https://github.com/owner/never-processed'))

    def test_settle_and_unsettle_round_trip(self):
        self.db.settle_existing_repos()
        # the owner's verdict door: settle_repos (the manual-resolve
        # dialog's "Mark as Processed" / "Decommission")
        self.assertEqual(
            self.db.settle_repos(['https://github.com/owner/fresh']), 1)
        self.assertTrue(self.db.is_repo_settled(
            'https://github.com/owner/fresh'))
        self.assertEqual(self.db.settled_repos_count(), 4)
        # idempotent
        self.assertEqual(
            self.db.settle_repos(['https://github.com/owner/fresh']), 0)
        # the ♻️ door back: unsettle_repo
        self.assertTrue(self.db.unsettle_repo(
            'https://github.com/owner/fresh'))
        self.assertFalse(self.db.is_repo_settled(
            'https://github.com/owner/fresh'))
        self.assertFalse(self.db.unsettle_repo(
            'https://github.com/owner/fresh'))

    def test_the_set_is_loaded_once_for_classification(self):
        """get_settled_repo_set — the once-per-queue-check shape (the
        same contract as get_dead_url_set)."""
        self.db.settle_existing_repos(
            extra_urls=['https://github.com/owner/extra'])
        s = self.db.get_settled_repo_set()
        self.assertEqual(len(s), 4)
        self.assertIn('https://github.com/owner/stored', s)
        self.assertIn('https://github.com/owner/extra', s)

    def test_a_broken_ledger_reads_as_empty_never_as_error(self):
        """The guarded probes: a broken DB degrades to 'not settled'
        (the old behavior), never raises."""
        db2 = CacheDB(os.path.join(self.tmp, 'other.db'))
        db2.close()
        db2.close()   # idempotent close — the connection is gone now
        self.assertFalse(db2.is_repo_settled('https://github.com/x/y'))
        self.assertEqual(db2.get_settled_repo_set(), set())
        self.assertEqual(db2.settled_repos_count(), 0)
        self.assertFalse(db2.repos_ledger_settled())

    def test_meta_kv_round_trip(self):
        self.db.set_meta('some-key', 'some-value')
        self.assertEqual(self.db.get_meta('some-key'), 'some-value')
        self.assertIsNone(self.db.get_meta('missing-key'))


# ---------------------------------------------------------------------------
# 2. The ♻️ door (the repos twin of the master table's revive)
# ---------------------------------------------------------------------------

class TestTheReviveDoor(unittest.TestCase):
    """reset_dead_links removes BOTH marks — the quarantine row AND the
    settlement — for exactly the urls whose quarantine row is going."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='reposettled-revive-')
        self.db = CacheDB(os.path.join(self.tmp, 'cache.db'))
        self.db.settle_existing_repos(extra_urls=[
            'https://github.com/q/dead',
            'https://github.com/q/other-dead',
            'https://github.com/q/alive-and-processed',
        ])
        self.db.add_processed(7, 'https://github.com/q/alive-and-processed',
                              'q', 'alive-and-processed', 'n.md', 'AI')

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_single_url_reset_un_settles_it(self):
        self.db.decommission('https://github.com/q/dead', '404')
        removed = self.db.reset_dead_links('https://github.com/q/dead')
        self.assertEqual(removed, 1)
        self.assertFalse(self.db.is_repo_settled(
            'https://github.com/q/dead'))
        # fetched like new again: not in any dedupe layer
        self.assertFalse(self.db.is_decommissioned(
            'https://github.com/q/dead'))

    def test_whole_table_reset_un_settles_only_the_quarantined(self):
        self.db.decommission('https://github.com/q/dead', '404')
        self.db.decommission('https://github.com/q/other-dead', '404')
        removed = self.db.reset_dead_links()
        self.assertEqual(removed, 2)
        # both quarantined urls are un-settled (fetched like new)…
        self.assertFalse(self.db.is_repo_settled(
            'https://github.com/q/dead'))
        self.assertFalse(self.db.is_repo_settled(
            'https://github.com/q/other-dead'))
        # …but every OTHER settled repo keeps its settlement (the law
        # holds for the rest of the history).
        self.assertTrue(self.db.is_repo_settled(
            'https://github.com/q/alive-and-processed'))
        self.assertEqual(self.db.settled_repos_count(), 1)

    def test_resetting_an_empty_quarantine_is_a_noop(self):
        self.assertEqual(self.db.reset_dead_links(), 0)
        self.assertEqual(self.db.settled_repos_count(), 3)


# ---------------------------------------------------------------------------
# 3. The reconciliation gate (the banner's honesty)
# ---------------------------------------------------------------------------

class TestTheReconciliationGate(unittest.TestCase):
    """get_reconciliation_urls — settled github rows never cry (the
    twin of the v0.63.1 website-rows law)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='reposettled-recon-')
        self.vault = os.path.join(self.tmp, 'ghvault')
        os.makedirs(self.vault, exist_ok=True)
        # APP_DIR redirected so the tracker's settled probe (CacheDB()
        # default path) lands in the temp dir, never the real app dir
        # (the test_phase4 rule).
        self._orig_app_dir = _cache_db_mod.APP_DIR
        _cache_db_mod.APP_DIR = self.tmp
        self.cache = CacheDB(os.path.join(self.tmp, 'cache.db'))
        # THE SETTLEMENT has run on this machine
        self.cache.settle_existing_repos(extra_urls=[
            'https://github.com/o/settled-failed',
            'https://github.com/o/settled-pending',
        ])

    def tearDown(self):
        _cache_db_mod.APP_DIR = self._orig_app_dir
        self.cache.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_settled_failed_rows_never_cry(self):
        _manifest(self.vault, [
            _row('https://github.com/o/settled-failed', 'failed',
                 error='boom'),
            _row('https://github.com/o/missing', 'failed', error='boom'),
        ])
        tracker = LinkTracker(self.vault)
        urls = tracker.get_reconciliation_urls()
        self.assertEqual(urls, ['https://github.com/o/missing'])
        self.assertEqual(tracker.set_aside_settled, 1)

    def test_settled_pending_rows_never_cry(self):
        _manifest(self.vault, [
            _row('https://github.com/o/settled-pending', 'pending'),
        ])
        tracker = LinkTracker(self.vault)
        self.assertEqual(tracker.get_reconciliation_urls(), [])
        self.assertEqual(tracker.set_aside_settled, 1)

    def test_unsettled_rows_keep_the_old_behavior(self):
        """A genuinely-missing repo (added AFTER the settlement, failed
        its fetch) still cries — the law never silences new work."""
        _manifest(self.vault, [
            _row('https://github.com/o/new-failure', 'failed',
                 error='rate limit'),
        ])
        tracker = LinkTracker(self.vault)
        self.assertEqual(tracker.get_reconciliation_urls(),
                         ['https://github.com/o/new-failure'])
        self.assertEqual(tracker.set_aside_settled, 0)

    def test_vault_healing_law_untouched(self):
        """The v0.38.0 healing law outranks the count either way: a
        failed row whose note IS in the vault is healed, never retried
        — settled or not."""
        _note(self.vault, 'AI/stored.md', 'https://github.com/o/stored')
        _manifest(self.vault, [
            _row('https://github.com/o/stored', 'failed', error='old'),
        ])
        tracker = LinkTracker(self.vault)
        self.assertEqual(tracker.get_reconciliation_urls(), [])
        self.assertEqual(tracker.reconciled_vault_hits, 1)
        self.assertEqual(tracker.set_aside_settled, 0)

    def test_website_rows_stay_set_aside(self):
        """The v0.63.1 law survives this one untouched (regression
        guard while we are in here)."""
        _manifest(self.vault, [
            _row('https://example.com/walled', 'failed', error='403',
                 kind='website'),
            _row('https://github.com/o/settled-failed', 'failed',
                 error='boom'),
        ])
        tracker = LinkTracker(self.vault)
        self.assertEqual(tracker.get_reconciliation_urls(), [])
        self.assertEqual(tracker.set_aside_websites, 1)
        self.assertEqual(tracker.set_aside_settled, 1)

    def test_a_broken_cache_never_resurrects_a_cry(self):
        """The probe is guarded: with cache.db unreadable the read
        degrades to the OLD behavior (the manifest's truth), never
        raises and never hides a settled row it cannot see."""
        _manifest(self.vault, [
            _row('https://github.com/o/settled-failed', 'failed',
                 error='boom'),
        ])
        _cache_db_mod.APP_DIR = os.path.join(self.tmp, 'nonexistent-dir')
        tracker = LinkTracker(self.vault)
        # the probe opens a FRESH empty cache there: nothing settled,
        # the old loud behavior holds (a broken probe never lies quiet)
        urls = tracker.get_reconciliation_urls()
        self.assertEqual(urls, ['https://github.com/o/settled-failed'])


# ---------------------------------------------------------------------------
# 4. get_all_clear — the settled skip's manifest shape
# ---------------------------------------------------------------------------

class TestGetAllClear(unittest.TestCase):
    """The run gate marks settled repos 'skipped' in the manifest — and
    'skipped' has always been all-clear (the v29.4 rule)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='reposettled-clear-')
        self.vault = os.path.join(self.tmp, 'ghvault')
        os.makedirs(self.vault, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _tracker(self, links):
        _manifest(self.vault, links)
        t = LinkTracker(self.vault)
        t.manifest = t.load_previous_manifest()
        return t

    def test_a_batch_of_settled_skips_is_all_clear(self):
        t = self._tracker([
            _row('https://github.com/o/settled-one', 'skipped'),
            _row('https://github.com/o/settled-two', 'skipped'),
        ])
        self.assertTrue(t.get_all_clear())

    def test_a_genuinely_failed_row_still_blocks(self):
        t = self._tracker([
            _row('https://github.com/o/new-failure', 'failed',
                 error='boom'),
        ])
        self.assertFalse(t.get_all_clear())


# ---------------------------------------------------------------------------
# 5. Source contracts — every production door, pinned
# ---------------------------------------------------------------------------

class TestSourceContracts(unittest.TestCase):

    def _read(self, *parts):
        with open(os.path.join(_REPO_ROOT, *parts), encoding="utf-8") as f:
            return f.read()

    def test_the_queue_door_runs_the_settlement_and_buckets(self):
        src = self._read("app", "gitcurator", "gui", "worker_jobs.py")
        # THE SETTLEMENT at the queue's own door (the first door the app
        # touches after an upgrade — the startup auto-check lands here)
        self.assertIn("settle_existing_repos", src)
        self.assertIn("THE SETTLEMENT (repos)", src)
        # the settled bucket in the classification
        self.assertIn("repos_settled_count", src)
        self.assertIn("settled_repo_urls", src)

    def test_the_gui_fallback_classifies_settled_too(self):
        src = self._read("app", "gitcurator", "gui", "main_window",
                         "bot_queue.py")
        self.assertIn("repos_settled_count = int(result.get("
                      "'repos_settled_count', 0) or 0)", src)
        self.assertIn("_settled_repo_urls", src)
        # the display speaks the settled line
        self.assertIn("🤝 Repos settled:", src)

    def test_process_all_splits_settled_repos_out(self):
        src = self._read("app", "gitcurator", "gui", "main_window",
                         "bot_queue.py")
        self.assertIn("_settled_kept_out", src)
        self.assertIn("excluded from the batch", src)

    def test_process_new_splits_settled_re_sends_out(self):
        src = self._read("app", "gitcurator", "gui", "main_window",
                         "bot_queue.py")
        self.assertIn("_settled_resends", src)
        self.assertIn("re-sent repo link(s) are", src)

    def test_verify_all_counts_settled_as_accounted(self):
        src = self._read("app", "gitcurator", "gui", "main_window",
                         "bot_queue.py")
        self.assertIn("github_settled", src)
        self.assertIn("Settled (addressed)", src)
        # settled repos land in the accounted-for sum, never missing
        self.assertIn("github_decommissioned + github_settled", src)

    def test_the_manual_resolve_verdicts_settle(self):
        src = self._read("app", "gitcurator", "gui", "main_window",
                         "bot_queue.py")
        # do_mark_processed / do_decomm / do_mark_all all settle
        self.assertGreaterEqual(src.count("cache.settle_repos"), 3)

    def test_the_workers_github_loop_skips_settled(self):
        src = self._read("app", "gitcurator", "gui",
                         "processing_worker.py")
        self.assertIn("_repos_settled", src)
        self.assertIn("settled_repos_skipped", src)
        # the skip sits BEFORE the GitHub API call — the owner's law
        # never spends a rate-limit token on settled history
        self.assertIn("settled — addressed (the owner's law)", src)
        # ONE honest aggregate line, never a per-URL skip pile
        self.assertIn("settled repo link(s) skipped", src)

    def test_the_retry_doors_drop_settled_rows(self):
        src = self._read("app", "gitcurator", "gui", "main_window",
                         "dashboard.py")
        self.assertIn("_settled_retry_skips", src)
        self.assertIn("_settled_verify_skips", src)

    def test_the_quarantine_reset_is_the_door_back(self):
        src = self._read("app", "gitcurator", "gui",
                         "cache_db.py")
        # reset_dead_links un-settles what it removes
        self.assertIn("DELETE FROM repos_settled", src)
        dash = self._read("app", "gitcurator", "gui", "main_window",
                          "dashboard.py")
        self.assertIn("their settlement is gone too", dash)

    def test_the_reconciliation_drops_settled_rows(self):
        src = self._read("app", "gitcurator", "gui", "link_tracker.py")
        self.assertIn("set_aside_settled", src)
        self.assertIn("get_settled_repo_set", src)

    def test_the_startup_truth_pass_stays_quiet_pre_settlement(self):
        src = self._read("app", "gitcurator", "gui", "main_window",
                         "window.py")
        self.assertIn("_repos_settled_yet", src)
        self.assertIn("repos_ledger_settled", src)
        # pre-settlement rows get the quiet info line, never the cry
        self.assertIn("pre-settlement retry row(s)", src)

    def test_the_banner_refreshes_after_a_queue_check(self):
        src = self._read("app", "gitcurator", "gui", "main_window",
                         "bot_queue.py")
        self.assertIn("_refresh_retry_banner", src)

    def test_the_ledger_tables_exist(self):
        src = self._read("app", "gitcurator", "gui", "cache_db.py")
        self.assertIn("CREATE TABLE IF NOT EXISTS repos_settled", src)
        self.assertIn("CREATE TABLE IF NOT EXISTS cache_meta", src)
        self.assertIn("REPOS_SETTLED_META_KEY = 'repos_settled_at'", src)

    def test_version_and_changelog_beat(self):
        self.assertEqual(self._read("VERSION").strip(), "0.64.0")
        text = self._read("CHANGELOG.md")
        self.assertIn("## [0.63.3]", text)
        self.assertIn("settled", text.lower())

    def test_ci_lists_this_module(self):
        ci = self._read(".github", "workflows", "ci.yml")
        self.assertIn("tests.test_reposettled", ci)

    def test_agents_md_lists_this_module(self):
        agents = self._read("AGENTS.md")
        self.assertIn("tests.test_reposettled", agents)

    def test_the_user_agent_string_beats(self):
        src = self._read("app", "gitcurator", "cloud",
                         "cloudflare_sync.py")
        self.assertIn("GitCurator/0.64.0", src)


if __name__ == "__main__":
    unittest.main()
