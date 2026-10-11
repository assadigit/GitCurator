"""tests/test_queuehistory.py — v0.64.1, THE QUEUE-HISTORY SETTLEMENT: the
false "N website link(s) pending" cry, closed.

The owner's report (session, verbatim): "Now fix this, despite everything
is fetched and processed, somehow the app says 85 sites need processing.
it's probably false positive, because in the procedure they'll get
skipped nonetheless."

The disease it closes: v0.63.2's settlement seeded only what the STATE
knew (processed / retry-queue / dismissed rows) plus the vault's own
truth (master-table rows, _review notes) — while the repos twin
(v0.63.3's settle_existing_repos) always took the queue door's FULL bot
history as extras. So a website link the owner sent to the bot that
never became a state row — never batched, a batch stopped midway, a
note deleted by hand outside the app — stayed UN-settled, and every
queue check counted it as PENDING forever: "📋 Queue: … 93 website
link(s) pending", "⏳ 93 pending", "▶ PROCESS (93)", "🟢 Fetched 93
website link(s) — click PROCESS to start." The batch would only skip
or instantly consolidate them — the count was a false positive, exactly
as the owner said.

The law, layer by layer (all covered here — zero network, an injected
fetch_fn and a fake LLM, the house pattern):

* the second pass — ``WebsiteStateDB.settle_queue_history`` (one time,
  its own meta key ``websites_queue_history_settled_at``): settles every
  URL the queue door hands it under the CANONICAL key (the one-spelling
  law rides free), resolves the retry rows of exactly those URLs (the
  queue itself is NOT bulk-cleared — post-law failures keep their own
  3-retry lifecycle), and a second call is a no-op;
* the door — ``settle_the_ledger(..., queue_urls=...)``: the owner's
  machine shape (the v0.63.2 settlement already stamped) runs the second
  pass alone; a fresh machine runs both; later doors are one cheap
  SELECT; ``queue_urls=None`` keeps the exact old shape; a state without
  the method (a mock, an older shape) never raises;
* the batch gate — ``WebsitePipeline.run`` skips a queue-history-settled
  link before any fetch (the procedure's own skip, now the count's truth
  too);
* the revive door — ♻️ un-settles a queue-history row like any other;
* source contracts — the worker queue door passes the FULL bot history
  (``queue_urls=non_github``); the GUI fallback runs the same healing;
  "Process All" carries a second-layer split that drops settled website
  links from the payload; the caught-up report gives the settled bucket
  its honest line; the release bookkeeping beats.

No PyQt import at module level (the libEGL-less sandbox rule) — the GUI
wiring is pinned by source contracts.
"""

import json
import os
import shutil
import tempfile
import unittest

from gitcurator.core import dryrun
from gitcurator.core import website_pipeline as wp
from gitcurator.core import web_fetch as _web_fetch


# -- the fakes (the test_settledledger pattern, kept local) -----------------

class _FakeFetch:
    """Canned fetch results; records every call (the law's witness)."""

    def __init__(self):
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append(url)
        html = ("<html><head><title>Test Site</title>"
                "<meta name=\"description\" content=\"A test page."
                "\"></head><body><p>Body text about design tools and "
                "resources for building websites and applications, long "
                "enough to classify confidently.</p></body></html>")
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


_URL = 'https://never-batched.example/article'
_CANON = wp.normalize_website_url(_URL)
_ERR = 'HTTP 403 — bot defense (server: cloudflare)'

_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _Case(unittest.TestCase):
    """Shared plumbing: temp vault + state db + log sink."""

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix='queuehist-')
        self.vault = os.path.join(self.tmp, 'websites')
        os.makedirs(os.path.join(self.vault, '_review'), exist_ok=True)
        self.db = wp.WebsiteStateDB(
            db_path=os.path.join(self.tmp, 'cache.db'))
        self.logs = []

    def tearDown(self):
        dryrun.disable()
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def log(self, m, l='info'):
        self.logs.append((l, m))

    def all_logs(self):
        return '\n'.join(m for (_, m) in self.logs)

    def make_pipeline(self):
        return wp.WebsitePipeline(
            config={'website_vault_path': self.vault,
                    'web_domain_delay_s': 0},
            llm_call=_FakeLLM(),
            vault_index_has=lambda u: False,
            state=self.db, fetch_fn=_FakeFetch(),
            log=self.log)


# ---------------------------------------------------------------------------
# 1. the second pass itself (pure state)
# ---------------------------------------------------------------------------

class TestTheSecondPass(unittest.TestCase):

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix='qh-ledger-')
        self.db = wp.WebsiteStateDB(
            db_path=os.path.join(self.tmp, 'cache.db'))

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_settles_the_never_batched_history(self):
        rep = self.db.settle_queue_history(
            extra_urls=[_URL, 'https://another.example/'])
        self.assertFalse(rep['already'])
        self.assertEqual(rep['settled'], 2)
        self.assertTrue(self.db.is_settled(_URL))
        self.assertIsNotNone(
            self.db.get_meta(wp.QUEUE_HISTORY_SETTLED_META_KEY))

    def test_one_spelling_one_settled_row(self):
        # the owner's slash pair — the same link, two spellings, one
        # verdict (v0.64.0's law rides free through the second pass)
        rep = self.db.settle_queue_history(
            extra_urls=['https://cleanup.pictures/',
                        'https://cleanup.pictures'])
        self.assertEqual(rep['settled'], 1)
        self.assertTrue(self.db.is_settled('https://cleanup.pictures/'))
        self.assertTrue(self.db.is_settled('https://cleanup.pictures'))
        self.assertEqual(self.db.settled_count(), 1)

    def test_second_call_is_a_no_op(self):
        self.db.settle_queue_history(extra_urls=[_URL])
        # a link added AFTER the pass is never swept by a second call
        rep = self.db.settle_queue_history(
            extra_urls=['https://later.example/'])
        self.assertTrue(rep['already'])
        self.assertEqual(rep['settled'], 0)
        self.assertFalse(self.db.is_settled('https://later.example/'))

    def test_retry_rows_of_settled_urls_resolve(self):
        # a mid-flight failure whose link IS in the bot history: the
        # owner's word settles it, its retries stop firing, the banner
        # stops crying for it
        self.db.enqueue_retry(_URL, _ERR)
        self.db.settle_queue_history(extra_urls=[_URL])
        self.assertEqual(
            [r['url'] for r in self.db.all_retry_rows()], [])
        self.assertTrue(self.db.is_settled(_URL))

    def test_the_retry_queue_is_not_bulk_cleared(self):
        # a post-law failure whose link is NOT in the history keeps its
        # own honest 3-retry lifecycle — the clear belonged to the first
        # settlement (v0.63.2), not this pass
        self.db.enqueue_retry('https://fresh-failure.example/', _ERR)
        self.db.settle_queue_history(extra_urls=[_URL])
        rows = [r['url'] for r in self.db.all_retry_rows()]
        self.assertEqual(
            rows, [wp.normalize_website_url(
                'https://fresh-failure.example/')])

    def test_count_reports_only_the_rows_it_inserted(self):
        # the first settlement already settled this URL: the second
        # pass reports 0 NEW rows for it, honestly
        self.db.settle_existing(extra_urls=[_URL])
        rep = self.db.settle_queue_history(extra_urls=[_URL])
        self.assertFalse(rep['already'])
        self.assertEqual(rep['settled'], 0)
        self.assertTrue(self.db.is_settled(_URL))

    def test_broken_and_empty_inputs_settle_nothing(self):
        rep = self.db.settle_queue_history(
            extra_urls=['', None])
        self.assertFalse(rep['already'])
        self.assertEqual(rep['settled'], 0)
        self.assertEqual(self.db.settled_count(), 0)


# ---------------------------------------------------------------------------
# 2. the door helper (settle_the_ledger with queue_urls)
# ---------------------------------------------------------------------------

class TestTheDoor(_Case):

    def test_the_owners_machine_shape_first_pass_already_ran(self):
        # v0.63.2's settlement stamped its meta key long ago; the queue
        # door now arrives with the FULL bot history — the second pass
        # speaks once and settles the never-batched pile
        self.db.settle_existing()
        self.assertEqual(self.db.settled_count(), 0)
        rep = wp.settle_the_ledger(
            self.db, self.vault, log=self.log,
            queue_urls=[_URL, 'https://another.example/'])
        # the door's own report keeps the first pass's shape…
        self.assertTrue(rep['already'])
        # …while the ledger holds the second pass's truth
        self.assertEqual(self.db.settled_count(), 2)
        self.assertTrue(self.db.is_settled(_URL))
        self.assertIn('THE QUEUE-HISTORY SETTLEMENT', self.all_logs())
        self.assertIn('never counted', self.all_logs())
        self.assertIn('2 link(s)', self.all_logs())

    def test_a_fresh_machine_runs_both_passes(self):
        rep = wp.settle_the_ledger(
            self.db, self.vault, log=self.log,
            queue_urls=[_URL])
        self.assertFalse(rep['already'])
        self.assertEqual(rep['settled'], 0)   # no state rows to seed
        self.assertTrue(self.db.is_settled(_URL))
        self.assertIn('THE SETTLEMENT', self.all_logs())

    def test_later_doors_are_silent_and_cheap(self):
        wp.settle_the_ledger(self.db, self.vault, log=self.log,
                             queue_urls=[_URL])
        n_logs = len(self.logs)
        rep = wp.settle_the_ledger(
            self.db, self.vault, log=self.log,
            queue_urls=[_URL, 'https://later.example/'])
        self.assertTrue(rep['already'])
        self.assertFalse(self.db.is_settled('https://later.example/'))
        self.assertEqual(len(self.logs), n_logs)

    def test_no_queue_urls_keeps_the_exact_old_shape(self):
        # the batch door and the caught-up scan call without queue_urls:
        # one cheap SELECT, nothing settled, nothing logged
        self.db.settle_existing()
        rep = wp.settle_the_ledger(self.db, self.vault, log=self.log)
        self.assertTrue(rep['already'])
        self.assertEqual(len(self.logs), 0)

    def test_a_state_without_the_method_never_raises(self):
        class _Older:
            def get_meta(self, key):
                return '2026-01-01T00:00:00'

        wp.settle_the_ledger(_Older(), self.vault, log=self.log,
                             queue_urls=[_URL])
        self.assertTrue(any('Queue-history settlement skipped' in m
                            for _, m in self.logs))

    def test_zero_new_rows_stay_silent(self):
        self.db.settle_existing(extra_urls=[_URL])
        wp.settle_the_ledger(self.db, self.vault, log=self.log,
                             queue_urls=[_URL])
        self.assertNotIn('THE QUEUE-HISTORY SETTLEMENT', self.all_logs())


# ---------------------------------------------------------------------------
# 3. the batch gate (the procedure's skip is the count's truth)
# ---------------------------------------------------------------------------

class TestTheBatchGate(_Case):

    def test_a_queue_history_settled_link_is_skipped_without_a_fetch(self):
        # the exact shape of the owner's 85/93: a link with NO note, NO
        # state row, NO verdict — only the bot's history. Settled by the
        # queue door, the batch's own gate skips it before any fetch
        wp.settle_the_ledger(self.db, self.vault, log=self.log,
                             queue_urls=[_URL])
        p = self.make_pipeline()
        results = p.run([_URL])
        self.assertEqual(results[0]['outcome'], 'skipped')
        self.assertIn('settled', results[0]['error'])
        self.assertIn('never re-fetched', results[0]['error'])
        self.assertEqual(p.fetch_fn.calls, [])
        self.assertEqual(p.counters['skipped'], 1)
        self.assertIn('settled', self.all_logs())

    def test_a_newly_added_link_still_runs_the_full_flow(self):
        # only links added FROM NOW ON are fetched — the law's other half
        p = self.make_pipeline()
        results = p.run(['https://added-today.example/tool'])
        self.assertEqual(results[0]['outcome'], 'processed')
        self.assertEqual(p.fetch_fn.calls,
                         ['https://added-today.example/tool'])

    def test_the_hand_still_outranks_the_settlement(self):
        wp.settle_the_ledger(self.db, self.vault, log=self.log,
                             queue_urls=[_URL])
        p = self.make_pipeline()
        p._row_reads_hand = lambda canonical: canonical == _CANON
        results = p.run([_URL])
        self.assertEqual(p.fetch_fn.calls, [_URL])
        self.assertEqual(results[0]['outcome'], 'processed')


# ---------------------------------------------------------------------------
# 4. the revive door (♻️ un-settles a queue-history row)
# ---------------------------------------------------------------------------

class TestTheReviveDoor(_Case):

    def test_a_revived_link_is_fetched_like_new(self):
        wp.settle_the_ledger(self.db, self.vault, log=self.log,
                             queue_urls=[_URL])
        self.assertTrue(self.db.unsettle(_URL))
        p = self.make_pipeline()
        results = p.run([_URL])
        self.assertEqual(p.fetch_fn.calls, [_URL])
        self.assertEqual(results[0]['outcome'], 'processed')


# ---------------------------------------------------------------------------
# 5. source contracts — the GUI wiring, pinned the house way
# ---------------------------------------------------------------------------

class TestSourceContracts(unittest.TestCase):

    def _read(self, *parts):
        with open(os.path.join(_REPO_ROOT, *parts), encoding="utf-8") as f:
            return f.read()

    def test_the_worker_queue_door_passes_the_full_history(self):
        src = self._read("app", "gitcurator", "gui", "worker_jobs.py")
        self.assertIn("queue_urls=non_github", src)

    def test_the_gui_fallback_runs_the_same_healing(self):
        src = self._read("app", "gitcurator", "gui", "main_window",
                         "bot_queue.py")
        self.assertIn("queue_urls=non_github", src)

    def test_process_all_carries_the_second_layer_split(self):
        src = self._read("app", "gitcurator", "gui", "main_window",
                         "bot_queue.py")
        self.assertIn("_settled_web_kept_out", src)
        self.assertIn("excluded from the batch", src)

    def test_the_caught_up_report_names_the_settled_websites(self):
        src = self._read("app", "gitcurator", "gui", "main_window",
                         "bot_queue.py")
        self.assertIn("website link(s) are ", src)
        self.assertIn("settled — addressed and processed", src)

    def test_the_door_helper_owns_the_second_pass(self):
        src = self._read("app", "gitcurator", "core",
                         "website_pipeline.py")
        self.assertIn("settle_queue_history", src)
        self.assertIn("QUEUE_HISTORY_SETTLED_META_KEY", src)
        state = self._read("app", "gitcurator", "core",
                           "website_state.py")
        self.assertIn("def settle_queue_history", state)

    def test_version_and_changelog_beat(self):
        self.assertEqual(self._read("VERSION").strip(), "0.66.0")
        text = self._read("CHANGELOG.md")
        self.assertIn("## [0.64.1]", text)  # history stays
        self.assertIn("queue-history", text.lower())

    def test_ci_lists_this_module(self):
        self.assertIn("tests.test_queuehistory",
                      self._read(".github", "workflows", "ci.yml"))

    def test_agents_md_lists_this_module(self):
        self.assertIn("test_queuehistory", self._read("AGENTS.md"))


if __name__ == '__main__':
    unittest.main(verbosity=2)
