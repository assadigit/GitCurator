"""tests/test_masterretry.py — v0.51.0, the caught-up check reads the
table: " - " rows are retried before "up to date" is said.

The owner's report (session, verbatim): "Currently app says everything
is uptodate, but actually app must look at decomissioned note, and try
to fetch again those that dont have skeleton or red cross or such
emojies and have this state ' - ' … so before declaring everything is
uptodate it must check 'decomissioned' note and find those that should
be retried."

Covered here (zero network — an injected fetch_fn and a fake LLM, the
house pattern):

* the waiting verdict — ``_status_is_waiting``: the " - " family
  (blank, '-', '—', 'unreviewed') waits; the owner's named emojis
  (skeleton ☠️ / red cross ❌ and friends) and every other verdict
  (✅ reviewed / ♻️ revived / 🖐 hand / the app's own stamps) never do;
* the scan — ``scan_master_waiting_rows``: only " - " rows come back,
  classified 'fetch' (failed placeholder / no note — the machine's) or
  'eyes' (a non-failed _review note — the owner's); verdict rows,
  dismissed links and ALREADY-STORED links never appear; the state
  probes are optional (the hermetic law) and a broken probe never
  hides a waiting link;
* the reborn counter — ``WebsiteStateDB.reset_retry_attempts``: ONE
  burned-out row is reset (attempts 3 → 0, due now), the rest of the
  queue keeps its backoff; an absent row is an honest False;
* the driver — ``retry_master_waiting``:
  - THE OWNER'S EXACT CASE: a link whose 3 automatic retries burned
    out is skipped by process_link forever ("no more retries") — the
    " - " row says otherwise, so the pass reborns the counter, re-
    fetches under the (injected) fetcher, stores the real note, and
    the master row is stamped '📁 stored';
  - re-failure: the row stays " - ", the verdict says "still wait";
  - the "eyes" rows and the ☠️ rows are never fetched;
  - an empty table agrees with "everything is up to date";
  - should_continue stops cleanly; on_progress fires per link;
* the truth stamp — ``stamp_stored_rows`` via the refresh: a stale
  " - " row for an already-stored link becomes '📁 stored'; an
  owner-set verdict on the same link is never touched;
* the caught-up routing — ``_after_sync_fetch``: waiting rows → the
  master-retry batch starts and "All caught up" is NEVER said; no
  waiting rows → the old caught-up law unchanged; a bare hero without
  the collaborator → the old law unchanged (the guarded-call idiom).

No PyQt import at module level (the libEGL-less sandbox rule) — the
hero-routing class imports the mixin under the same try/except guard
the queue-fix suite uses; the worker/phase wiring is exercised by the
compile gate + CI's headless Qt suite.
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


# -- the fakes (the test_reviewretry pattern, kept local) -----------------

class _FakeFetch:
    """Canned fetch results; whole-path failures simulate the wall."""

    def __init__(self, pages=None, fail_paths=(), fail_reason=None):
        self.pages = pages or {}
        self.fail_paths = set(fail_paths)
        self.fail_reason = fail_reason \
            or 'HTTP 403 — bot defense (server: cloudflare)'
        self.calls = []

    def __call__(self, url, **kwargs):
        from urllib.parse import urlparse
        self.calls.append(url)
        path = urlparse(url).path or '/'
        if path in self.fail_paths:
            return _web_fetch.FetchResult(
                url=url, status='failed', reason=self.fail_reason)
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


class _MasterCase(unittest.TestCase):
    """Shared plumbing: temp vault + state db + pipeline factory."""

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix='mretry-')
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
            lines.append(f"| - | 2026-10-09 | {url} | {domain} "
                         f"| test | {status} | {notes} |")
        path = wp.decommission_table_path(self.vault)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        return path

    def table_text(self):
        with open(wp.decommission_table_path(self.vault),
                  encoding='utf-8') as f:
            return f.read()

    def all_logs(self):
        return '\n'.join(m for (_, m) in self.logs)


# ---------------------------------------------------------------------------
# 1. the waiting verdict (pure)
# ---------------------------------------------------------------------------

class TestWaitingVerdict(unittest.TestCase):

    def test_the_dash_family_waits(self):
        for s in ('', ' ', '-', ' - ', '—', '–', 'unreviewed',
                  'UNREVIEWED', 'still unreviewed', 'maybe later'):
            self.assertTrue(wp._status_is_waiting(s), repr(s))

    def test_the_owners_named_emojis_never_wait(self):
        # "those that dont have skeleton or red cross or such emojies"
        for s in ('☠️', '❌', '🪦', '💀', '☠️ dead', '❌ dead — gone',
                  'dead', 'retired', 'Decommissioned 2026'):
            self.assertFalse(wp._status_is_waiting(s), repr(s))

    def test_the_other_verdicts_never_wait(self):
        for s in ('✅ reviewed', '✅', 'kept', 'done',
                  '♻️ revived', 'restored',
                  '🖐 hand — queued 2026-10-09', 'queued',
                  '🪦 confirmed — decommissioned 2026-10-09',
                  '✅ confirmed — reviewed 2026-10-09',
                  '🪦 auto — paywalled', '📁 stored — fetched 2026-10-09'):
            self.assertFalse(wp._status_is_waiting(s), repr(s))


# ---------------------------------------------------------------------------
# 2. the scan (pure file read + optional state probes)
# ---------------------------------------------------------------------------

class TestScanMasterWaiting(_MasterCase):

    def test_only_dash_rows_come_back(self):
        self.write_placeholder(_URL, 'a.md')
        self.write_table([
            (_URL, 'unreviewed', 'HTTP 403'),
            ('https://gone.example.org/x', '☠️', ''),
            ('https://kept.example.org/y', '✅ reviewed', ''),
            ('https://auto.example.org/z', '🪦 auto — dead', ''),
            ('https://hand.example.org/w', '🖐 hand — queued 2026-10-09',
             ''),
        ])
        rows = wp.scan_master_waiting_rows(self.vault)
        self.assertEqual([r['url'] for r in rows], [_URL])
        self.assertEqual(rows[0]['kind'], 'fetch')
        self.assertTrue(rows[0]['path'].endswith('a.md'))

    def test_a_failed_placeholder_is_fetch_a_non_failed_note_is_eyes(self):
        self.write_placeholder(_URL, 'fail.md', 'failed')
        self.write_placeholder('https://low.example.org/conf',
                               'low.md', 'full')
        self.write_table([
            (_URL, ' - ', ''),
            ('https://low.example.org/conf', ' - ', ''),
        ])
        rows = {r['url']: r for r in
                wp.scan_master_waiting_rows(self.vault)}
        self.assertEqual(rows[_URL]['kind'], 'fetch')
        self.assertEqual(
            rows['https://low.example.org/conf']['kind'], 'eyes')

    def test_a_hand_added_row_with_no_note_is_fetch(self):
        self.write_table([('https://fresh.example.net/new', ' - ', '')])
        rows = wp.scan_master_waiting_rows(self.vault)
        self.assertEqual(rows[0]['kind'], 'fetch')
        self.assertEqual(rows[0]['path'], '')

    def test_dismissed_links_are_not_waiting(self):
        self.write_placeholder(_URL, 'a.md')
        self.db.dismiss(_CANON, 'auto-verdict: dead — 404')
        self.write_table([(_URL, ' - ', '')])
        self.assertEqual(
            wp.scan_master_waiting_rows(self.vault, state=self.db), [])

    def test_stored_links_are_not_waiting(self):
        # the truth-stamp story: a processed row with a REAL note (not
        # a _review placeholder) means the retry long succeeded
        self.db.mark_processed(_CANON,
                               os.path.join(self.vault, 'Design', 'n.md'),
                               'Design', '', 'full')
        self.write_table([(_URL, ' - ', '')])
        self.assertEqual(
            wp.scan_master_waiting_rows(self.vault, state=self.db), [])

    def test_without_state_the_pure_read_still_finds_the_row(self):
        # the hermetic law: no state → no probes → the row is waiting
        self.write_table([(_URL, ' - ', '')])
        rows = wp.scan_master_waiting_rows(self.vault)
        self.assertEqual([r['url'] for r in rows], [_URL])

    def test_a_broken_probe_never_hides_a_waiting_link(self):
        self.write_placeholder(_URL, 'a.md')
        self.write_table([(_URL, ' - ', '')])

        class _Boom:
            def is_dismissed(self, url):
                raise RuntimeError('probe is down')

        rows = wp.scan_master_waiting_rows(self.vault, state=_Boom(),
                                           log=lambda *a, **k: None)
        self.assertEqual([r['url'] for r in rows], [_URL])

    def test_duplicate_rows_first_wins_and_missing_table_is_empty(self):
        self.write_table([
            (_URL, ' - ', 'first'),
            (_URL, 'unreviewed', 'second'),
        ])
        rows = wp.scan_master_waiting_rows(self.vault)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['notes'], 'first')
        self.assertEqual(
            wp.scan_master_waiting_rows(
                os.path.join(self.tmp, 'nope')), [])


# ---------------------------------------------------------------------------
# 3. the reborn counter (state)
# ---------------------------------------------------------------------------

class TestResetRetryAttempts(_MasterCase):

    def test_one_burned_out_row_is_reborn(self):
        for _ in range(3):
            self.db.enqueue_retry(_URL, _ERR)
        self.assertEqual(self.db.retry_row(_URL)['attempts'], 3)
        other = 'https://other.example.org/p'
        self.db.enqueue_retry(other, _ERR)
        self.assertTrue(self.db.reset_retry_attempts(_CANON))
        row = self.db.retry_row(_CANON)
        self.assertEqual(row['attempts'], 0)
        self.assertIn('next_attempt_at', row)
        # surgical: the OTHER row keeps its attempt count
        self.assertEqual(self.db.retry_row(other)['attempts'], 1)

    def test_an_absent_row_is_an_honest_false(self):
        self.assertFalse(self.db.reset_retry_attempts(_CANON))


# ---------------------------------------------------------------------------
# 4. the driver
# ---------------------------------------------------------------------------

class TestRetryMasterWaiting(_MasterCase):

    def test_the_owners_exact_case_the_burned_out_row_is_fetched_again(self):
        # 1. the link fails 3 times — its retries burn out, a
        #    placeholder waits in _review, the master table says " - "
        fetch = _FakeFetch(fail_paths=['/article'])
        pipe = self.make_pipeline(fetch=fetch)
        for _ in range(3):
            pipe.process_link(_URL)
        placeholder = self.db.processed_row(_CANON)['note_path']
        self.assertTrue(os.path.exists(placeholder))
        # …and the app would say "everything is up to date": the queue
        # is not due (attempts=3), process_link skips forever —
        self.assertEqual(self.db.due_retries(), [])
        res_old = pipe.process_link(_URL)
        self.assertEqual(res_old['outcome'], 'skipped')
        self.assertIn('no more retries', res_old['error'])
        # 2. the caught-up check: the " - " row is found and retried
        self.write_table([(_URL, 'unreviewed', _ERR)])
        fetch2 = _FakeFetch()                    # the wall is down now
        pipe2 = self.make_pipeline(fetch=fetch2)
        self.in_vault = {_CANON}                 # the placeholder counts
        results = pipe2.retry_master_waiting()
        self.assertEqual([r['outcome'] for r in results], ['processed'])
        self.assertEqual(fetch2.calls, [_URL])   # really fetched again
        # 3. the ledger agrees: retry row gone; v0.60.2 — the compact
        # table: the stored row LEAVES (the note in the vault is the
        # record now, not a table row):
        self.assertIsNone(self.db.retry_row(_CANON))
        self.assertNotIn(_URL, self.table_text())
        self.assertIn("every ' - ' fetch row got its retry",
                      self.all_logs())

    def test_refailure_keeps_the_row_waiting_and_says_so(self):
        fetch = _FakeFetch(fail_paths=['/article'])
        pipe = self.make_pipeline(fetch=fetch)
        pipe.process_link(_URL)
        self.write_table([(_URL, ' - ', _ERR)])
        fetch2 = _FakeFetch(fail_paths=['/article'])
        pipe2 = self.make_pipeline(fetch=fetch2)
        results = pipe2.retry_master_waiting()
        self.assertEqual([r['outcome'] for r in results], ['review'])
        self.assertEqual(fetch2.calls, [_URL])
        self.assertIn('still wait', self.all_logs())
        self.assertIn(' - ', self.table_text())       # row unchanged
        self.assertIsNotNone(self.db.retry_row(_CANON))  # re-queued

    def test_eyes_rows_are_never_fetched(self):
        self.write_placeholder('https://low.example.org/conf',
                               'low.md', 'full')
        self.write_table([('https://low.example.org/conf', ' - ', '')])
        fetch = _FakeFetch()
        pipe = self.make_pipeline(fetch=fetch)
        results = pipe.retry_master_waiting()
        self.assertEqual(results, [])
        self.assertEqual(fetch.calls, [])
        self.assertIn('wait for YOUR eyes', self.all_logs())

    def test_skull_rows_are_never_fetched(self):
        self.write_table([('https://gone.example.org/x', '☠️', '')])
        fetch = _FakeFetch()
        pipe = self.make_pipeline(fetch=fetch)
        self.assertEqual(pipe.retry_master_waiting(), [])
        self.assertEqual(fetch.calls, [])

    def test_an_empty_table_agrees_with_up_to_date(self):
        fetch = _FakeFetch()
        pipe = self.make_pipeline(fetch=fetch)
        self.assertEqual(pipe.retry_master_waiting(), [])
        self.assertIn("no ' - ' row is waiting", self.all_logs())
        self.assertEqual(fetch.calls, [])

    def test_should_continue_stops_and_on_progress_fires(self):
        self.write_table([
            ('https://a.example.org/1', ' - ', ''),
            ('https://b.example.org/2', ' - ', ''),
        ])
        fetch = _FakeFetch()
        pipe = self.make_pipeline(fetch=fetch)
        seen = []
        stop = iter([True, False])          # allow one, stop before two

        results = pipe.retry_master_waiting(
            should_continue=lambda: next(stop),
            on_progress=seen.append)
        self.assertEqual(seen, ['https://a.example.org/1'])
        self.assertEqual(len(results), 1)
        self.assertIn('stopped by user', self.all_logs())


# ---------------------------------------------------------------------------
# 5. the truth stamp (the refresh makes stale rows honest)
# ---------------------------------------------------------------------------

class TestStampStoredRows(_MasterCase):

    def test_a_stale_dash_row_for_a_stored_link_is_stamped(self):
        self.db.mark_processed(_CANON,
                               os.path.join(self.vault, 'Design', 'n.md'),
                               'Design', '', 'full')
        self.write_table([(_URL, ' - ', 'old error')])
        self.assertEqual(
            wp.stamp_stored_rows(self.db, self.vault), 1)
        self.assertIn('📁 stored', self.table_text())
        # idempotent: a second pass finds nothing to stamp
        self.assertEqual(
            wp.stamp_stored_rows(self.db, self.vault), 0)

    def test_an_owner_verdict_is_never_touched(self):
        self.db.mark_processed(_CANON,
                               os.path.join(self.vault, 'Design', 'n.md'),
                               'Design', '', 'full')
        self.write_table([(_URL, '☠️', '')])
        self.assertEqual(
            wp.stamp_stored_rows(self.db, self.vault), 0)
        self.assertIn('☠️', self.table_text())

    def test_a_waiting_link_is_not_stamped(self):
        self.db.enqueue_retry(_CANON, _ERR)
        self.db.mark_processed(_CANON,
                               os.path.join(self.vault, '_review', 'a.md'),
                               '', '', 'failed')
        self.write_table([(_URL, ' - ', _ERR)])
        self.assertEqual(
            wp.stamp_stored_rows(self.db, self.vault), 0)

    def test_the_refresh_stamps_via_the_batch(self):
        # the full circle: refresh_master_table (every batch's last
        # move) stamps the stale row — and v0.60.2 prunes it the same
        # pass (a stored link is TERMINAL: its note in the vault is
        # the record, the table keeps only what still needs the owner)
        self.db.mark_processed(_CANON,
                               os.path.join(self.vault, 'Design', 'n.md'),
                               'Design', '', 'full')
        self.write_table([(_URL, ' - ', 'old error')])
        wp.refresh_master_table(self.db, self.vault,
                                log=lambda *a, **k: None)
        self.assertNotIn(_URL, self.table_text())
        # the honesty mechanism itself is unchanged (the unit law):
        self.assertEqual(
            wp.stamp_stored_rows(self.db, self.vault), 0)  # already gone


# ---------------------------------------------------------------------------
# 6. the caught-up routing (the hero mixin, guarded)
# ---------------------------------------------------------------------------

try:
    from gitcurator.gui.main_window.hero import HeroMixin
    _QT_OK = True
except BaseException:  # noqa: BLE001 — PyQt6 stack unavailable
    _QT_OK = False


@unittest.skipUnless(_QT_OK, "PyQt6 stack unavailable (runs in CI)")
class TestHeroCaughtUpRouting(unittest.TestCase):
    """The owner's exact complaint surface: the SYNC that finds nothing
    new must NOT say "All caught up" while " - " rows wait — it starts
    the master-retry batch instead. Bare-mixin stubs, the queue-fix
    suite's _hero pattern."""

    class _Txt:
        def __init__(self, s=""):
            self._s = s

        def text(self):
            return self._s

    def _hero(self, waiting=None, eyes=0):
        hero = HeroMixin()
        hero.states = []
        hero._bot_queue_urls = []
        hero._bot_queue_pending_websites = []
        hero.import_file = self._Txt("")
        hero.logs = []
        hero.log_message = lambda msg, level="info": hero.logs.append(
            (level, msg))
        hero._set_hero_state = lambda state: hero.states.append(state)
        hero.progress_bar = types.SimpleNamespace(
            setFormat=lambda fmt: setattr(hero, "fmt", fmt))
        hero.progress_count = types.SimpleNamespace(
            setText=lambda t: None, setToolTip=lambda t: None)
        hero._scan_master_waiting = lambda: list(waiting or [])
        hero._master_waiting_eyes = eyes
        hero._master_starts = []
        hero._start_master_retry = lambda: hero._master_starts.append(1)
        hero._log_caught_up_state = lambda: hero.logs.append(
            ("info", "CAUGHT_UP_STATE"))
        return hero

    def test_waiting_rows_start_the_pass_not_the_claim(self):
        hero = self._hero(waiting=[{'url': _URL, 'kind': 'fetch'}],
                          eyes=2)
        hero._after_sync_fetch("bot_check", {"success": True})
        self.assertEqual(hero._master_starts, [1])
        self.assertTrue(any("' - ' row(s)" in m for _, m in hero.logs))
        self.assertTrue(any("wait for your eyes" in m
                            for _, m in hero.logs))
        # the empty-state claim is NEVER made while rows wait
        self.assertFalse(any("All caught up" in m for _, m in hero.logs))
        self.assertFalse(any(m == "CAUGHT_UP_STATE"
                             for _, m in hero.logs))

    def test_no_waiting_rows_keeps_the_old_law(self):
        hero = self._hero(waiting=[])
        hero._after_sync_fetch("bot_check", {"success": True})
        self.assertEqual(hero._master_starts, [])
        self.assertEqual(hero.states, ["sync"])
        self.assertTrue(any("All caught up" in m for _, m in hero.logs))
        self.assertTrue(any(m == "CAUGHT_UP_STATE"
                            for _, m in hero.logs))

    def test_a_bare_hero_without_the_collaborator_is_unchanged(self):
        # the guarded-call idiom: the mixin-level flow (and its
        # headless test stubs) predate the collaborator — the old
        # caught-up law must hold untouched
        hero = HeroMixin()
        hero.states = []
        hero._bot_queue_urls = []
        hero._bot_queue_pending_websites = []
        hero.import_file = self._Txt("")
        hero.logs = []
        hero.log_message = lambda msg, level="info": hero.logs.append(
            (level, msg))
        hero._set_hero_state = lambda state: hero.states.append(state)
        hero.progress_bar = types.SimpleNamespace(setFormat=lambda f: None)
        hero.progress_count = types.SimpleNamespace(
            setText=lambda t: None, setToolTip=lambda t: None)
        hero._log_caught_up_state = lambda: None
        hero._after_sync_fetch("bot_check", {"success": True})
        self.assertEqual(hero.states, ["sync"])
        self.assertTrue(any("All caught up" in m for _, m in hero.logs))


# ---------------------------------------------------------------------------
# 7. release bookkeeping
# ---------------------------------------------------------------------------

class TestReleaseBookkeeping(unittest.TestCase):

    def setUp(self):
        self.root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def _read(self, *parts):
        with open(os.path.join(self.root, *parts), 'r',
                  encoding='utf-8') as f:
            return f.read()

    def test_version_is_0510(self):
        self.assertEqual(self._read('VERSION').strip(), '0.63.0')

    def test_changelog_mentions_the_pass(self):
        text = self._read('CHANGELOG.md')
        self.assertIn('## [0.51.0]', text)
        self.assertIn('retry_master_waiting', text)

    def test_ci_lists_this_module(self):
        self.assertIn('tests.test_masterretry',
                      self._read('.github', 'workflows', 'ci.yml'))

    def test_agents_md_lists_this_module(self):
        self.assertIn('test_masterretry', self._read('AGENTS.md'))


if __name__ == '__main__':
    unittest.main()
