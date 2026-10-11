"""tests/test_settledledger.py — v0.63.2, THE SETTLED LEDGER: the
owner's law settles the fetch-everything reflex.

The owner's report (session, verbatim): "Do not fetch current websites
which are sent to bot, because they're already addressed and
processed. Fetch only websites, that are added to bot, from now on."

The disease it closes: three automatic doors kept re-fetching websites
the owner had long since addressed —
  * the caught-up sync's master-retry pass (v0.51.0) re-fetched every
    " - " row of the master table on EVERY sync, its reborn counters
    making "3 automatic retries" meaningless (a permanent 403 wall was
    re-fetched forever — "it does nothing, just take some time");
  * "Process All" fed the bot queue's FULL non-GitHub history into
    every batch, and the in-vault-but-failed exception re-fetched the
    walled placeholders each time;
  * the queue classification counted those same walled placeholders
    as PENDING forever, so the app kept saying work was waiting.

The law, layer by layer (all covered here — zero network, an injected
fetch_fn and a fake LLM, the house pattern):

* the ledger — ``WebsiteStateDB.settle_existing`` (one time, meta-key
  guarded): seeds ``websites_settled`` from every URL the state knows
  (processed / retry-queue / dismissed) plus the caller's extras, and
  gives the fetch-retry queue its settlement date (cleared — no
  backoff timer re-serves an old link); ``is_settled`` /
  ``settle_urls`` / ``unsettle`` / ``settled_count`` keep their
  contracts; a second call is a no-op;
* the split — ``split_settled_links`` (pure, the twin of
  split_dismissed_links): settled links leave the batch's count with
  one honest line; a broken probe never hides a link;
* the door helper — ``settle_the_ledger``: completes the seed with
  the vault's truth (the master table's data rows, the _review note
  URLs), speaks the settlement line ONCE, and pays one SELECT on
  every later call (the meta guard runs BEFORE the file scans);
* the scan — ``scan_master_waiting_rows``: settled rows never count
  as waiting (the caught-up check finds nothing to re-fetch);
* the run gate — ``WebsitePipeline.run``: a settled link is skipped
  ("never re-fetched"), the fetcher is never asked; the 🖐 hand
  outranks the settlement (the v0.56.0 precedent) and passes;
* the retry gates — ``run_due_retries`` and
  ``retry_review_backlog`` drop settled links before any fetch;
* the revive door — a ♻️ row in the master table UN-SETTLES its link
  (fetched like new again — the owner's own door through the law);
* source contracts — the caught-up sync reports the waiting rows but
  never auto-fetches them; "Process All" carries only the pending
  (newly-added) website links; the queue classification gains the
  settled bucket; every production door runs the settlement.

No PyQt import at module level (the libEGL-less sandbox rule) — the
GUI wiring is pinned by source contracts; the hero routing itself
lives in test_masterretry (its own law, updated with this one).
"""

import json
import os
import shutil
import tempfile
import types
import unittest

from gitcurator.core import dryrun
from gitcurator.core import website_pipeline as wp
from gitcurator.core import web_fetch as _web_fetch


# -- the fakes (the test_masterretry pattern, kept local) -----------------

class _FakeFetch:
    """Canned fetch results; records every call (the law's witness)."""

    def __init__(self, pages=None, fail_paths=()):
        self.pages = pages or {}
        self.fail_paths = set(fail_paths)
        self.calls = []

    def __call__(self, url, **kwargs):
        from urllib.parse import urlparse
        self.calls.append(url)
        path = urlparse(url).path or '/'
        if path in self.fail_paths:
            return _web_fetch.FetchResult(
                url=url, status='failed',
                reason='HTTP 403 — bot defense (server: cloudflare)')
        html = self.pages.get(path)
        if html is None:
            html = ("<html><head><title>Test Site</title>"
                    "<meta name=\"description\" content=\"A test page."
                    "\"></head><body><p>Body text about design tools and "
                    "resources for building websites and applications, "
                    "long enough to classify confidently.</p></body></html>")
        return _web_fetch.FetchResult(
            url=url, final_url=url, status='full', http_status=200,
            content_type='text/html', charset='utf-8',
            body=html.encode('utf-8'), text=html)


class _FakeLLM:
    """Scriptable classify/analyze answers (valid taxonomy names)."""

    def __init__(self, category='Design', subcategory='Assets & Resources'):
        self.category = category
        self.subcategory = subcategory

    def __call__(self, messages, task=None):
        text = messages[0]['content']
        if 'filing a website into a personal library' in text:
            return json.dumps({'category': self.category,
                               'confidence': 'high', 'reason': 'testing'})
        if 'was filed under' in text:
            return json.dumps({'subcategory': self.subcategory,
                               'confidence': 'high'})
        return json.dumps({
            'name': 'Test Site', 'one_line': 'A test page about design.',
            'core_offerings': ['One', 'Two'],
            'best_used_for': 'Use when you need to test the pipeline.',
            'pricing': 'free', 'login_required': 'no',
            'similar_tools': [], 'tags': ['design'],
            'confidence': 'high'})


_URL = 'https://walled.example.net/article'
_CANON = wp.normalize_website_url(_URL)
_ERR = 'HTTP 403 — bot defense (server: cloudflare)'

_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _LedgerCase(unittest.TestCase):
    """Shared plumbing: temp vault + state db + pipeline factory."""

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix='settled-')
        self.vault = os.path.join(self.tmp, 'websites')
        os.makedirs(os.path.join(self.vault, '_review'), exist_ok=True)
        self.db = wp.WebsiteStateDB(
            db_path=os.path.join(self.tmp, 'cache.db'))
        self.in_vault = set()
        self.logs = []

    def tearDown(self):
        dryrun.disable()
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_pipeline(self, llm=None, fetch=None, config=None):
        cfg = {'website_vault_path': self.vault,
               'web_domain_delay_s': 0}
        cfg.update(config or {})
        return wp.WebsitePipeline(
            config=cfg, llm_call=llm or _FakeLLM(),
            vault_index_has=lambda u: u in self.in_vault,
            state=self.db, fetch_fn=fetch or _FakeFetch(),
            log=lambda m, l='info': self.logs.append((l, m)))

    def write_placeholder(self, url, name, fetch_status='failed'):
        review_dir = os.path.join(self.vault, '_review')
        os.makedirs(review_dir, exist_ok=True)
        path = os.path.join(review_dir, name)
        note = wp.build_review_note(url, fetch_status,
                                    'Fetch failed: ' + _ERR)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(note)
        return path

    def write_table(self, rows):
        """rows: [(url, status, notes)] → the master table, the writer's
        own grammar (7 columns, ' - ' in the # cell)."""
        lines = ['# Review Master Table — decommission or approve',
                 '',
                 '| # | Date | URL | Domain | Source | Status | Notes |',
                 '|---|------|-----|--------|--------|--------|-------|']
        for url, status, notes in rows:
            domain = url.split('/')[2] if url.count('/') >= 2 else 'x'
            lines.append(f"| - | 2026-10-10 | {url} | {domain} "
                         f"| test | {status} | {notes} |")
        path = wp.decommission_table_path(self.vault)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        return path

    def all_logs(self):
        return '\n'.join(m for (_, m) in self.logs)


# ---------------------------------------------------------------------------
# 1. the ledger itself (pure state)
# ---------------------------------------------------------------------------

class TestTheLedger(unittest.TestCase):

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix='ledger-')
        self.db = wp.WebsiteStateDB(
            db_path=os.path.join(self.tmp, 'cache.db'))

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_settle_existing_seeds_every_table_and_clears_the_queue(self):
        self.db.mark_processed('https://a.example/', 'n.md',
                               'Design', 'Assets', 'full')
        self.db.enqueue_retry('https://b.example/', _ERR)
        self.db.enqueue_retry('https://b.example/', _ERR)   # attempts 2
        self.db.dismiss('https://c.example/', 'note deleted by owner')
        rep = self.db.settle_existing()
        self.assertEqual(rep['settled'], 3)
        self.assertEqual(rep['retries_cleared'], 1)
        self.assertFalse(rep['already'])
        for u in ('https://a.example/', 'https://b.example/',
                  'https://c.example/'):
            self.assertTrue(self.db.is_settled(u), u)
        # the queue got its settlement date — no backoff timer left
        self.assertEqual(self.db.all_retry_rows(), [])
        self.assertEqual(self.db.due_retries(), [])
        # the meta key is stamped
        self.assertIsNotNone(self.db.get_meta(wp.SETTLED_META_KEY))

    def test_settle_existing_is_idempotent(self):
        self.db.mark_processed('https://a.example/', 'n.md',
                               '', '', 'full')
        first = self.db.settle_existing()
        # a NEW row after the settlement is NOT swept by a second call
        self.db.mark_processed('https://new.example/', 'n2.md',
                               '', '', 'failed')
        self.db.enqueue_retry('https://new.example/', _ERR)
        second = self.db.settle_existing()
        self.assertTrue(second['already'])
        self.assertEqual(second['settled'], 0)
        self.assertEqual(second['retries_cleared'], 0)
        self.assertFalse(self.db.is_settled('https://new.example/'))
        self.assertEqual(first['settled'], 1)

    def test_settle_existing_takes_extra_urls(self):
        # a hand-added master-table row with NO note and NO state row —
        # the vault-level completion still settles it
        rep = self.db.settle_existing(
            extra_urls=['https://hand.example/', 'https://hand.example/',
                        '', None])
        self.assertEqual(rep['settled'], 1)
        self.assertTrue(self.db.is_settled('https://hand.example/'))

    def test_settle_urls_and_unsettle_round_trip(self):
        self.assertEqual(self.db.settle_urls(
            ['https://x.example/', 'https://x.example/']), 1)
        self.assertTrue(self.db.is_settled('https://x.example/'))
        self.assertEqual(self.db.settled_count(), 1)
        # the ♻️ door back
        self.assertTrue(self.db.unsettle('https://x.example/'))
        self.assertFalse(self.db.is_settled('https://x.example/'))
        # honest False for a URL that was never settled
        self.assertFalse(self.db.unsettle('https://y.example/'))


# ---------------------------------------------------------------------------
# 2. the split (pure)
# ---------------------------------------------------------------------------

class TestTheSplit(unittest.TestCase):

    def test_settled_links_leave_the_count(self):
        logs = []
        kept, settled = wp.split_settled_links(
            None, [], log=lambda m, l='info': logs.append(m))
        self.assertEqual((kept, settled), ([], []))
        self.assertEqual(logs, [])

        class _State:
            def is_settled(self, u):
                # v0.64.0 — the probe arrives CANONICAL (no root
                # slash): the split normalizes before it asks.
                return u == 'https://a.example'

        kept, settled = wp.split_settled_links(
            _State(),
            ['https://a.example/', 'https://b.example/'],
            log=lambda m, l='info': logs.append(m))
        self.assertEqual(kept, ['https://b.example/'])
        self.assertEqual(settled, ['https://a.example/'])
        self.assertEqual(len(logs), 1)
        self.assertIn('settled', logs[0])
        self.assertIn('never fetched again', logs[0])

    def test_a_broken_probe_never_hides_a_link(self):
        class _Broken:
            def is_settled(self, u):
                raise RuntimeError('boom')

        kept, settled = wp.split_settled_links(
            _Broken(), ['https://a.example/'])
        self.assertEqual(kept, ['https://a.example/'])
        self.assertEqual(settled, [])

    def test_normalization_is_the_key(self):
        class _State:
            def __init__(self):
                self.asked = []

            def is_settled(self, u):
                self.asked.append(u)
                return True

        st = _State()
        kept, settled = wp.split_settled_links(
            st, ['https://A.example/page?utm=x'])
        self.assertEqual(settled, ['https://A.example/page?utm=x'])
        self.assertEqual(kept, [])
        # the probe saw the CANONICAL form, not the raw spelling
        self.assertEqual(st.asked,
                         [wp.normalize_website_url(
                             'https://A.example/page?utm=x')])


# ---------------------------------------------------------------------------
# 3. the door helper (settle_the_ledger)
# ---------------------------------------------------------------------------

class TestTheDoorHelper(_LedgerCase):

    def test_the_settlement_completes_from_the_vaults_truth(self):
        # a walled placeholder (state row + note on disk) and a
        # hand-added master-table row (no note, no state row)
        self.write_placeholder(_URL, 'walled.md')
        self.db.mark_processed(_CANON, os.path.join(
            self.vault, '_review', 'walled.md'), '', '', 'failed')
        self.write_table([('https://hand.example/', ' - ', '')])
        rep = wp.settle_the_ledger(
            self.db, self.vault, log=lambda m, l='info':
            self.logs.append((l, m)))
        self.assertFalse(rep['already'])
        self.assertEqual(rep['settled'], 2)
        self.assertTrue(self.db.is_settled(_CANON))
        self.assertTrue(self.db.is_settled(
            wp.normalize_website_url('https://hand.example/')))
        self.assertIn('THE SETTLEMENT', self.all_logs())
        self.assertIn('never fetched again', self.all_logs())

    def test_the_meta_guard_makes_later_calls_cheap_and_silent(self):
        wp.settle_the_ledger(self.db, self.vault,
                             log=lambda m, l='info':
                             self.logs.append((l, m)))
        self.assertEqual(len(self.logs), 1)
        # a state row added AFTER the settlement is never swept
        self.db.mark_processed('https://new.example/', 'n.md',
                               '', '', 'failed')
        rep = wp.settle_the_ledger(self.db, self.vault,
                                   log=lambda m, l='info':
                                   self.logs.append((l, m)))
        self.assertTrue(rep['already'])
        self.assertFalse(self.db.is_settled('https://new.example/'))
        # no second settlement line — the guard ran before anything else
        self.assertEqual(len(self.logs), 1)

    def test_a_broken_state_never_raises(self):
        class _Broken:
            def get_meta(self, key):
                raise RuntimeError('boom')

            def settle_existing(self, extra_urls=None):
                raise RuntimeError('boom')

        rep = wp.settle_the_ledger(_Broken(), self.vault,
                                   log=lambda m, l='info':
                                   self.logs.append((l, m)))
        self.assertTrue(rep['already'])
        self.assertEqual(rep['settled'], 0)
        self.assertTrue(any('skipped' in m for _, m in self.logs))


# ---------------------------------------------------------------------------
# 4. the scan (settled rows never wait)
# ---------------------------------------------------------------------------

class TestTheScan(_LedgerCase):

    def test_settled_rows_never_count_as_waiting(self):
        self.write_table([(_URL, ' - ', ''), ])
        self.write_placeholder(_URL, 'walled.md')
        self.db.mark_processed(_CANON, os.path.join(
            self.vault, '_review', 'walled.md'), '', '', 'failed')
        # before the settlement: the old law — the row waits
        before = wp.scan_master_waiting_rows(self.vault, state=self.db)
        self.assertEqual([i['kind'] for i in before], ['fetch'])
        # settle, then re-scan: the row is gone
        self.db.settle_existing()
        after = wp.scan_master_waiting_rows(self.vault, state=self.db)
        self.assertEqual(after, [])

    def test_post_settlement_failures_still_wait(self):
        # a NEWLY-added website whose fetch failed after the law arrived
        # keeps its waiting row (its own 3 retries, its own doors)
        self.db.settle_existing()
        self.write_table([(_URL, ' - ', '')])
        self.write_placeholder(_URL, 'walled.md')
        self.db.mark_processed(_CANON, os.path.join(
            self.vault, '_review', 'walled.md'), '', '', 'failed')
        rows = wp.scan_master_waiting_rows(self.vault, state=self.db)
        self.assertEqual([i['kind'] for i in rows], ['fetch'])


# ---------------------------------------------------------------------------
# 5. the run gate (the batch never fetches a settled link)
# ---------------------------------------------------------------------------

class TestTheRunGate(_LedgerCase):

    def test_settled_link_is_skipped_without_a_fetch(self):
        self.db.settle_urls([_CANON])
        fetch = _FakeFetch()
        p = self.make_pipeline(fetch=fetch)
        results = p.run([_URL])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['outcome'], 'skipped')
        self.assertIn('settled', results[0]['error'])
        self.assertIn('never re-fetched', results[0]['error'])
        # THE LAW: the fetcher was never asked
        self.assertEqual(fetch.calls, [])
        self.assertEqual(p.counters['skipped'], 1)
        self.assertEqual(p.counters['processed'], 0)
        self.assertIn('settled', self.all_logs())

    def test_the_hand_gesture_outranks_the_settlement(self):
        # the owner's 🖐 says "finish this one" — the delivered page (or
        # the gesture row) passes even a settled link (v0.56.0's law)
        self.db.settle_urls([_CANON])
        fetch = _FakeFetch()
        p = self.make_pipeline(fetch=fetch)
        p._row_reads_hand = lambda canonical: canonical == _CANON
        results = p.run([_URL])
        self.assertEqual(fetch.calls, [_URL])
        self.assertEqual(results[0]['outcome'], 'processed')
        self.assertEqual(p.counters['processed'], 1)

    def test_delivered_page_outranks_the_settlement(self):
        # a page already waiting in the hand-delivered folder — the
        # owner saved it himself; the pipeline consumes it
        from gitcurator.core import hand_delivery as _hd
        self.db.settle_urls([_CANON])
        fetch = _FakeFetch()
        p = self.make_pipeline(fetch=fetch)
        _hd.enqueue_hand_delivery(self.vault, [_URL])
        hd_dir = _hd.hand_delivery_dir(self.vault)
        os.makedirs(hd_dir, exist_ok=True)
        with open(os.path.join(
                hd_dir, _hd.suggested_filename(_URL)), 'w',
                encoding='utf-8') as f:
            f.write('<html><body>delivered page</body></html>')
        results = p.run([_URL])
        self.assertEqual(results[0]['outcome'], 'processed')
        # the machine fetcher was never asked — the delivered page WAS
        # the fetch
        self.assertEqual(fetch.calls, [])

    def test_unsettled_links_run_unchanged(self):
        fetch = _FakeFetch()
        p = self.make_pipeline(fetch=fetch)
        results = p.run([_URL])
        self.assertEqual(fetch.calls, [_URL])
        self.assertEqual(results[0]['outcome'], 'processed')


# ---------------------------------------------------------------------------
# 6. the retry gates (due retries + the _review backlog)
# ---------------------------------------------------------------------------

class TestTheRetryGates(_LedgerCase):

    def test_due_retries_drop_settled_links(self):
        self.db.settle_existing()      # the machine settled (queue cleared)
        # a settled URL re-entered the queue against the law (an
        # unsettle/undismiss race, a hand-added row)
        self.db.enqueue_retry(_CANON, _ERR)
        self.db.reset_retry_attempts(_CANON)   # due NOW
        fetch = _FakeFetch()
        p = self.make_pipeline(fetch=fetch)
        self.db.settle_urls([_CANON])
        results = p.run_due_retries()
        self.assertEqual(results, [])
        self.assertEqual(fetch.calls, [])
        self.assertIn('settled', self.all_logs())

    def test_due_retries_run_post_settlement_failures(self):
        self.db.settle_existing()
        self.db.enqueue_retry(_CANON, _ERR)   # a NEW failure, due later…
        # …force it due now
        self.db.reset_retry_attempts(_CANON)
        fetch = _FakeFetch()
        p = self.make_pipeline(fetch=fetch)
        results = p.run_due_retries()
        self.assertEqual(fetch.calls, [_URL])
        self.assertEqual(len(results), 1)

    def test_the_backlog_retry_drops_settled_placeholders(self):
        path = self.write_placeholder(_URL, 'walled.md')
        self.db.mark_processed(_CANON, path, '', '', 'failed')
        self.db.settle_urls([_CANON])
        fetch = _FakeFetch()
        p = self.make_pipeline(fetch=fetch)
        results = p.retry_review_backlog(
            [{'url': _URL, 'path': path, 'kind': 'placeholder'}])
        self.assertEqual(results, [])
        self.assertEqual(fetch.calls, [])
        self.assertIn('left the _review backlog retry', self.all_logs())

    def test_the_backlog_retry_runs_post_settlement_placeholders(self):
        self.db.settle_existing()
        path = self.write_placeholder(_URL, 'walled.md')
        self.db.mark_processed(_CANON, path, '', '', 'failed')
        fetch = _FakeFetch()
        p = self.make_pipeline(fetch=fetch)
        results = p.retry_review_backlog(
            [{'url': _URL, 'path': path, 'kind': 'placeholder'}])
        self.assertEqual(fetch.calls, [_URL])
        self.assertEqual(len(results), 1)


# ---------------------------------------------------------------------------
# 7. the revive door (♻️ un-settles)
# ---------------------------------------------------------------------------

class TestTheReviveDoor(_LedgerCase):

    def test_a_revived_row_un_settles_its_link(self):
        self.db.settle_urls([_CANON])
        self.write_table([(_URL, '♻️ revived', '')])
        wp.consume_decommission_table(self.db, self.vault,
                                      log=lambda m, l='info':
                                      self.logs.append((l, m)))
        self.assertFalse(self.db.is_settled(_CANON))
        self.assertIn('the settlement is gone', self.all_logs())
        # and the link is fetched like new again
        fetch = _FakeFetch()
        p = self.make_pipeline(fetch=fetch)
        results = p.run([_URL])
        self.assertEqual(fetch.calls, [_URL])
        self.assertEqual(results[0]['outcome'], 'processed')

    def test_a_settled_link_without_a_revive_stays_settled(self):
        self.db.settle_urls([_CANON])
        self.write_table([(_URL, ' - ', '')])
        wp.consume_decommission_table(self.db, self.vault,
                                      log=lambda m, l='info':
                                      self.logs.append((l, m)))
        self.assertTrue(self.db.is_settled(_CANON))


# ---------------------------------------------------------------------------
# 8. source contracts — the GUI wiring, pinned the house way
# ---------------------------------------------------------------------------

class TestSourceContracts(unittest.TestCase):

    def _read(self, *parts):
        with open(os.path.join(_REPO_ROOT, *parts), encoding="utf-8") as f:
            return f.read()

    def test_the_caught_up_sync_reports_but_never_auto_fetches(self):
        src = self._read("app", "gitcurator", "gui", "main_window",
                         "hero.py")
        self.assertIn("NOT re-fetched on my own", src)
        # the v0.51.0 auto trigger is retired
        self.assertNotIn("self._start_master_retry()\n                "
                         "_work_started = True", src)

    def test_process_all_carries_only_new_websites(self):
        src = self._read("app", "gitcurator", "gui", "main_window",
                         "bot_queue.py")
        self.assertIn("non_github_urls=(getattr("
                      "self, '_bot_queue_pending_websites', [])", src)
        # the full-history payload is retired
        self.assertNotIn(
            "non_github_urls=getattr(self, '_bot_queue_non_github', []),",
            src)

    def test_the_queue_classification_has_the_settled_bucket(self):
        src = self._read("app", "gitcurator", "gui", "worker_jobs.py")
        self.assertIn("websites_settled_count", src)
        self.assertIn("settle_the_ledger", src)
        self.assertIn("is_settled", src)

    def test_every_production_door_runs_the_settlement(self):
        phase = self._read("app", "gitcurator", "gui", "worker",
                           "website_phase.py")
        self.assertIn("settle_the_ledger", phase)
        scan = self._read("app", "gitcurator", "gui", "main_window",
                          "processing_control.py")
        self.assertIn("settle_the_ledger", scan)

    def test_the_pipeline_owns_the_gates(self):
        src = self._read("app", "gitcurator", "core",
                         "website_pipeline.py")
        for marker in ("split_settled_links", "settle_the_ledger",
                       "_hand_outranks_settlement", "is_settled",
                       "state.unsettle"):
            self.assertIn(marker, src)

    def test_version_and_changelog_beat(self):
        self.assertEqual(self._read("VERSION").strip(), "0.66.0")
        text = self._read("CHANGELOG.md")
        self.assertIn("## [0.63.2]", text)
        self.assertIn("settled", text.lower())

    def test_ci_lists_this_module(self):
        self.assertIn("tests.test_settledledger",
                      self._read(".github", "workflows", "ci.yml"))

    def test_agents_md_lists_this_module(self):
        self.assertIn("test_settledledger", self._read("AGENTS.md"))


if __name__ == '__main__':
    unittest.main(verbosity=2)
