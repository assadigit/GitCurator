"""tests/test_noteredo.py — v0.57.0, THE NOTE IS THE SUCCESS: the ✅
is earned by the note itself, and a note that is not properly stored
is redone.

The owner's report (session, verbatim): "I've noticed that chrome
still fetches a new session, moreover it correctly loads the
websites, and websites gets loaded on those tabs, and in logs it
shows this '✅' but actually the llm model does not actually work on
that website and create a proper note. also the app must refetch and
generate notes, if they notes aren't properly stored. For example
websiteX has hand emoji. and it's fetched and became ✅ in the table,
but actually it's note is not properly saved and only saved under
_review folder, so it's false success and must be redo."

Covered here (zero network — an injected fetch_fn, a fake LLM, the
house pattern; no browser, no PyQt at module level):

* the test — ``note_is_properly_stored``: no state row, a failed
  fetch, a ``_review`` path, a missing file, a not-app-owned note, a
  stranger's source — each fails with its own reason; a real
  app-owned note on disk for THIS link outside _review passes;
* the redo-consume — ``take_hand_delivered(allow_consumed=True)``:
  a consumed page is re-read in place (the file is the record), the
  queue is NOT re-stamped (the first consume date stays the truth);
  without the flag the old law holds (None); the fresh-consume law
  (stamp on take) is unchanged; the auto-door verdict still
  discards an error page even on a redo;
* the redo scan — ``scan_master_redo_rows``: a delivered hand link
  with a half-fetched _review note IS the redo set (with its
  reason); a delivered hand link with a proper note is NOT; an
  un-delivered hand row is the Chrome pass's set, never the redo's;
  death / reviewed / revived win; a dismissed link is never redone;
  ``state=None`` and a broken probe answer nothing (the hermetic
  law); a missing table reads as empty;
* the redo, end to end — ``process_link``: THE OWNER'S EXACT CASE —
  a hand link delivered and consumed in an earlier run whose note
  came out half-fetched (low confidence, ``_review``,
  fetch_status 'full'): the redo-consume re-reads the page (the
  machine doors are never asked — ``fetch.calls == []``), the LLM
  writes the proper note, the row retires to ✅, the half-fetched
  item is swept;
* the routing fix — the CLI's chrome-retry passes the delivered
  pages as WEBSITE links (``non_github``), never as GitHub urls,
  and includes the redo set in the re-run (the owner's "the LLM
  does not actually work on that website" was exactly the old call
  shape feeding website URLs to the GitHub loop);
* the GUI wiring — the hero's caught-up flow runs the redo pass
  before "All caught up" may be said; the redo pass starts the
  Websites mini-batch with the redo URLs as website links; the
  delivered-page re-run does the same (the routing law at the GUI
  seam).

No PyQt import at module level (the libEGL-less sandbox rule).
"""

import json
import os
import shutil
import tempfile
import time
import types
import unittest
from unittest import mock

from gitcurator.core import dryrun
from gitcurator.core import hand_delivery as hd
from gitcurator.core import website_pipeline as wp
from gitcurator.core import web_fetch as wf


# -- the fakes (the test_handharvest pattern, kept local) -----------------

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
            return wf.FetchResult(
                url=url, status='failed', reason=self.fail_reason)
        html = self.pages.get(path)
        if html is None:
            html = ("<html><head><title>Test Site</title>"
                    "<meta name=\"description\" content=\"A test page."
                    "\"></head><body><p>Body text about design tools and "
                    "resources for building websites and applications, "
                    "long enough to classify confidently.</p></body></html>")
        return wf.FetchResult(
            url=url, final_url=url, status='full', http_status=200,
            content_type='text/html', charset='utf-8',
            body=html.encode('utf-8'), text=html)


class _FakeLLM:
    """Scriptable classify/analyze answers (valid taxonomy names)."""

    def __init__(self, category='Design',
                 subcategory='Assets & Resources'):
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


class _LowConfidenceLLM:
    """The half-fetched answer: classification parks in _review."""

    def __call__(self, messages, task=None):
        text = messages[0]['content']
        if 'filing a website into a personal library' in text:
            return json.dumps({'category': 'Design',
                               'confidence': 'low', 'reason': 'not sure'})
        return json.dumps({'subcategory': '', 'confidence': 'low'})


_URL = 'https://walled.example.net/article'
_CANON = wp.normalize_website_url(_URL)
_ERR = 'HTTP 403 — bot defense (server: cloudflare)'
_TODAY = time.strftime('%Y-%m-%d')


class _RedoCase(unittest.TestCase):
    """Shared plumbing: temp vault + state db + pipeline factory."""

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix='noteredo-')
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

    def write_review_note(self, url, name, fetch_status='failed',
                          reason='Fetch failed: ' + _ERR):
        review_dir = os.path.join(self.vault, '_review')
        os.makedirs(review_dir, exist_ok=True)
        path = os.path.join(review_dir, name)
        note = wp.build_review_note(url, fetch_status, reason)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(note)
        return path

    def write_half_note(self, url, name='a.md'):
        """The owner's 'only saved under _review' shape: a real
        app-owned _review note whose fetch SUCCEEDED but whose
        classification parked it for human eyes."""
        return self.write_review_note(
            url, name, fetch_status='full',
            reason='classification confidence was low')

    def write_proper_note(self, url, category='Design',
                          name='proper-note.md'):
        folder = os.path.join(self.vault, category)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        note = wp.build_website_note(
            url, {'name': 'Proper Note', 'one_line': 'A proper note.',
                  'what_it_does': ['It works.'],
                  'best_used_for': 'Use it when testing.',
                  'pricing': 'free', 'login_required': 'no'},
            category, '', 'full')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(note)
        return path

    def write_table(self, rows):
        """rows: [(url, status, notes)] → the master table."""
        lines = ['# Review Master Table — decommission or approve',
                 '',
                 '| # | Date | URL | Domain | Source | Status | Notes |',
                 '|---|------|-----|--------|--------|--------|-------|']
        for url, status, notes in rows:
            domain = url.split('/')[2] if url.count('/') >= 2 else 'x'
            lines.append(f"| - | {_TODAY} | {url} | {domain} "
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

    def deliver(self, url, body=b'<html><head><title>Hand-saved</title>'
                b'</head><body>saved by the owner</body></html>',
                consume=False, door=''):
        """Enqueue + write the page; optionally mark it consumed (the
        FALSE-SUCCESS shape: consumed in an earlier run, the note
        never became proper). ``door='auto'`` stamps the queue row as
        the app's own delivery (the verdict law's jurisdiction)."""
        hd.enqueue_hand_delivery(self.vault, [url],
                                 walls={url: _ERR},
                                 log=lambda *a, **k: None,
                                 door=door)
        sug = hd.suggested_filename(url)
        path = os.path.join(hd.hand_delivery_dir(self.vault), sug)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(body)
        if consume:
            queue = hd._read_queue(self.vault)
            queue['links'][url]['consumed'] = '2026-10-01 10:00'
            hd.atomic_write_text(
                hd.queue_path(self.vault),
                json.dumps(queue, indent=2, ensure_ascii=False))
        return path


# ---------------------------------------------------------------------------
# 1. the test (pure)
# ---------------------------------------------------------------------------

class TestProperNote(_RedoCase):

    def test_no_state_row_is_not_proper(self):
        ok, why = wp.note_is_properly_stored(None)
        self.assertFalse(ok)
        self.assertIn('never landed', why)

    def test_a_failed_fetch_is_not_proper(self):
        ok, why = wp.note_is_properly_stored(
            {'url': _CANON, 'note_path': 'x.md',
             'fetch_status': 'failed'})
        self.assertFalse(ok)
        self.assertIn('failed', why)

    def test_a_review_path_is_not_proper(self):
        # the owner's exact words: "only saved under _review folder"
        half = self.write_half_note(_URL)
        ok, why = wp.note_is_properly_stored(
            {'url': _CANON, 'note_path': half,
             'fetch_status': 'full'})
        self.assertFalse(ok)
        self.assertIn('_review', why)

    def test_a_missing_file_is_not_proper(self):
        ok, why = wp.note_is_properly_stored(
            {'url': _CANON,
             'note_path': os.path.join(self.vault, 'Design', 'gone.md'),
             'fetch_status': 'full'})
        self.assertFalse(ok)
        self.assertIn('missing on disk', why)

    def test_a_strangers_note_is_not_proper(self):
        # a file that exists but is not the app's own note for THIS
        # link — a same-named stranger is not a delivery
        stranger = self.write_proper_note('https://other.example.net/x')
        ok, why = wp.note_is_properly_stored(
            {'url': _CANON, 'note_path': stranger,
             'fetch_status': 'full'})
        self.assertFalse(ok)
        self.assertIn('another link', why)

    def test_a_hand_written_note_is_not_proper(self):
        path = os.path.join(self.vault, 'Design', 'human.md')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(f'---\nsource: {_URL}\n---\n\n# Mine\n')
        ok, why = wp.note_is_properly_stored(
            {'url': _CANON, 'note_path': path, 'fetch_status': 'full'})
        self.assertFalse(ok)
        self.assertIn('not app-owned', why)

    def test_a_real_note_on_disk_is_proper(self):
        proper = self.write_proper_note(_URL)
        ok, why = wp.note_is_properly_stored(
            {'url': _CANON, 'note_path': proper,
             'fetch_status': 'full'})
        self.assertTrue(ok, why)
        self.assertEqual(why, '')

    def test_a_broken_row_is_simply_not_proper(self):
        ok, _ = wp.note_is_properly_stored('not-a-dict')
        self.assertFalse(ok)

    def test_windows_review_paths_fail_too(self):
        ok, why = wp.note_is_properly_stored(
            {'url': _CANON, 'note_path': 'C:\\vault\\_review\\a.md',
             'fetch_status': 'full'})
        self.assertFalse(ok)
        self.assertIn('_review', why)


# ---------------------------------------------------------------------------
# 2. the redo-consume (take_hand_delivered)
# ---------------------------------------------------------------------------

class TestRedoConsume(_RedoCase):

    def test_a_consumed_page_is_none_without_the_flag(self):
        # the old law: consumed means answered, never re-taken
        self.deliver(_URL, consume=True)
        self.assertIsNone(
            hd.take_hand_delivered(self.vault, _CANON))

    def test_allow_consumed_re_reads_the_page(self):
        page = self.deliver(_URL, consume=True)
        res = hd.take_hand_delivered(self.vault, _CANON,
                                     allow_consumed=True)
        self.assertIsNotNone(res)
        self.assertEqual(res.status, 'full')
        with open(page, 'rb') as f:
            self.assertEqual(res.body, f.read())

    def test_allow_consumed_does_not_restamp_the_queue(self):
        # the first consume date is the record — a redo re-reads, it
        # never rewrites history
        self.deliver(_URL, consume=True)
        hd.take_hand_delivered(self.vault, _CANON, allow_consumed=True)
        queue = hd._read_queue(self.vault)
        self.assertEqual(queue['links'][_URL]['consumed'],
                         '2026-10-01 10:00')

    def test_a_fresh_take_still_stamps(self):
        # the fresh-consume law is unchanged: taking stamps the queue
        self.deliver(_URL)
        res = hd.take_hand_delivered(self.vault, _CANON)
        self.assertIsNotNone(res)
        queue = hd._read_queue(self.vault)
        self.assertTrue(queue['links'][_URL].get('consumed'))

    def test_the_auto_door_verdict_discards_even_on_a_redo(self):
        # an error page the app delivered is never re-consumed as a
        # fetch — the v0.53 verdict law holds on the redo path too
        body = ("<html><head><title>Error</title></head><body>"
                "<h1>ERR_CONNECTION_RESET</h1></body></html>"
                ).encode('utf-8')
        self.deliver(_URL, body=body, consume=True, door='auto')
        with mock.patch.object(hd, '_auto_page_is_real',
                               return_value=(False, 'chrome error page')):
            res = hd.take_hand_delivered(self.vault, _CANON,
                                         allow_consumed=True)
        self.assertIsNone(res)


# ---------------------------------------------------------------------------
# 3. the redo scan (pure + state)
# ---------------------------------------------------------------------------

class TestRedoScan(_RedoCase):

    def test_the_owners_exact_case_is_the_redo_set(self):
        # delivered + consumed + only a half-fetched _review note →
        # "false success and must be redo"
        half = self.write_half_note(_URL)
        self.db.mark_processed(_CANON, half, '', '', 'full')
        self.deliver(_URL, consume=True)
        self.write_table([(_URL, '🖐 hand', '')])
        rows = wp.scan_master_redo_rows(self.vault, state=self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['url'], _CANON)
        self.assertIn('_review', rows[0]['reason'])

    def test_a_proper_note_is_not_the_redos(self):
        proper = self.write_proper_note(_URL)
        self.db.mark_processed(_CANON, proper, 'Design', '', 'full')
        self.deliver(_URL, consume=True)
        self.write_table([(_URL, '🖐 hand', '')])
        self.assertEqual(
            wp.scan_master_redo_rows(self.vault, state=self.db), [])

    def test_no_state_row_after_delivery_is_the_redo_set(self):
        # consumed, but the note never even landed — the falsest of
        # the false successes
        self.deliver(_URL, consume=True)
        self.write_table([(_URL, '🖐 hand', '')])
        rows = wp.scan_master_redo_rows(self.vault, state=self.db)
        self.assertEqual(len(rows), 1)
        self.assertIn('never landed', rows[0]['reason'])

    def test_an_undelivered_hand_row_is_the_chrome_passs(self):
        # no queue row, no page file — that row wants a Chrome tab,
        # never a redo
        self.write_table([(_URL, '🖐 hand', '')])
        self.assertEqual(
            wp.scan_master_redo_rows(self.vault, state=self.db), [])

    def test_a_page_file_without_the_consume_stamp_is_delivered(self):
        # the delivery wrote the file but the consume never happened
        # (the routing bug's exact residue) — the redo takes it too
        half = self.write_half_note(_URL)
        self.db.mark_processed(_CANON, half, '', '', 'full')
        self.deliver(_URL)                    # no consume stamp
        self.write_table([(_URL, '🖐 hand', '')])
        rows = wp.scan_master_redo_rows(self.vault, state=self.db)
        self.assertEqual(len(rows), 1)

    def test_the_verdicts_win(self):
        for s in ('🪦 dead', '✅ reviewed', '♻️ revived'):
            self.setUp()
            try:
                half = self.write_half_note(_URL)
                self.db.mark_processed(_CANON, half, '', '', 'full')
                self.deliver(_URL, consume=True)
                self.write_table([(_URL, s, '')])
                self.assertEqual(
                    wp.scan_master_redo_rows(self.vault, state=self.db),
                    [])
            finally:
                self.tearDown()

    def test_a_dismissed_link_is_never_redone(self):
        half = self.write_half_note(_URL)
        self.db.mark_processed(_CANON, half, '', '', 'full')
        self.db.dismiss(_CANON, 'owner retired it')
        self.deliver(_URL, consume=True)
        self.write_table([(_URL, '🖐 hand', '')])
        self.assertEqual(
            wp.scan_master_redo_rows(self.vault, state=self.db), [])

    def test_no_state_degrades_to_nothing(self):
        # the hermetic law: nothing can be proven false
        half = self.write_half_note(_URL)
        self.deliver(_URL, consume=True)
        self.write_table([(_URL, '🖐 hand', '')])
        self.assertEqual(
            wp.scan_master_redo_rows(self.vault, state=None), [])

    def test_a_broken_probe_redoes_nothing(self):
        half = self.write_half_note(_URL)
        self.db.mark_processed(_CANON, half, '', '', 'full')
        self.deliver(_URL, consume=True)
        self.write_table([(_URL, '🖐 hand', '')])

        class _Broken:
            def is_dismissed(self, url):
                raise RuntimeError('probe down')

            def processed_row(self, url):
                raise RuntimeError('probe down')

        self.assertEqual(
            wp.scan_master_redo_rows(self.vault, state=_Broken()), [])

    def test_a_missing_table_reads_empty(self):
        self.assertEqual(
            wp.scan_master_redo_rows(self.vault, state=self.db), [])

    def test_duplicate_rows_first_wins(self):
        half = self.write_half_note(_URL)
        self.db.mark_processed(_CANON, half, '', '', 'full')
        self.deliver(_URL, consume=True)
        self.write_table([(_URL, '🖐 hand', ''),
                          (_URL, '🪦 dead', '')])
        self.assertEqual(
            len(wp.scan_master_redo_rows(self.vault, state=self.db)), 1)


# ---------------------------------------------------------------------------
# 4. THE OWNER'S EXACT REDO, end to end (process_link)
# ---------------------------------------------------------------------------

class TestPipelineRedo(_RedoCase):

    def test_the_false_success_is_redone_and_harvested(self):
        # delivered + consumed in an earlier run; the note came out
        # half-fetched; the row still shows 🖐. The redo: the page is
        # re-read (the machine doors never asked), the LLM writes the
        # proper note, the row retires, the half note is swept.
        half = self.write_half_note(_URL)
        self.db.mark_processed(_CANON, half, '', '', 'full')
        self.in_vault.add(_CANON)
        self.deliver(_URL, consume=True)
        self.write_table([(_URL, '🖐 hand', '')])
        # the redo scan finds it first (the GUI/CLI trigger's source)
        rows = wp.scan_master_redo_rows(self.vault, state=self.db)
        self.assertEqual(len(rows), 1)
        fetch = _FakeFetch(fail_paths=['/article'])   # the wall stands
        pipe = self.make_pipeline(fetch=fetch)
        res = pipe.process_link(_URL)
        # the delivered page answered — the machine door was never asked
        self.assertEqual(fetch.calls, [])
        self.assertEqual(res['outcome'], 'processed')
        self.assertTrue(os.path.exists(res['note_path']))
        self.assertNotIn('_review', res['note_path'].replace('\\', '/'))
        # THE HARVEST: the row retired, the half-fetched item swept
        self.assertIn('✅ hand-delivered', self.table_text())
        self.assertNotIn('🖐', self.table_text())
        self.assertFalse(os.path.exists(half))
        # and the redo set is now empty — the note IS the success
        self.assertEqual(
            wp.scan_master_redo_rows(self.vault, state=self.db), [])

    def test_the_redo_survives_burned_out_retries(self):
        # the burned-out counter + the consumed page + the half note:
        # the gesture reborns the counter, the redo-consume answers
        for _ in range(3):
            self.db.enqueue_retry(_CANON, _ERR)
        half = self.write_half_note(_URL)
        self.db.mark_processed(_CANON, half, '', '', 'full')
        self.in_vault.add(_CANON)
        self.deliver(_URL, consume=True)
        self.write_table([(_URL, '🖐 hand', '')])
        fetch = _FakeFetch(fail_paths=['/article'])
        pipe = self.make_pipeline(fetch=fetch)
        res = pipe.process_link(_URL)
        self.assertEqual(fetch.calls, [])
        self.assertEqual(res['outcome'], 'processed')
        self.assertIn('✅ hand-delivered', self.table_text())
        self.assertIn('reborn', self.all_logs())

    def test_a_low_confidence_redo_is_not_a_false_success(self):
        # the honest bounded loop: the LLM says low confidence AGAIN —
        # the row KEEPS its gesture (no ✅), the half note stays the
        # record, and the redo set still lists the link (the next
        # session asks again)
        self.write_half_note(_URL)
        self.in_vault.add(_CANON)
        self.deliver(_URL, consume=True)
        # no state row yet — the half note is in the vault only
        self.write_table([(_URL, '🖐 hand', '')])
        fetch = _FakeFetch(fail_paths=['/article'])
        pipe = self.make_pipeline(llm=_LowConfidenceLLM(), fetch=fetch)
        res = pipe.process_link(_URL)
        self.assertEqual(fetch.calls, [])
        self.assertEqual(res['outcome'], 'review')
        self.assertIn('NOT properly stored yet', self.all_logs())
        self.assertNotIn('✅ hand-delivered', self.table_text())
        self.assertIn('🖐', self.table_text())
        # the state row now records the half note → still the redo set
        rows = wp.scan_master_redo_rows(self.vault, state=self.db)
        self.assertEqual(len(rows), 1)

    def test_the_failed_placeholder_redo_uses_the_page_too(self):
        # the older shape: a failed placeholder (the wall's note) with
        # a consumed page — the redo answers the fetch with the page
        placeholder = self.write_review_note(_URL, 'a.md')
        self.db.mark_processed(_CANON, placeholder, '', '', 'failed')
        self.in_vault.add(_CANON)
        self.deliver(_URL, consume=True)
        self.write_table([(_URL, '🖐 hand', '')])
        fetch = _FakeFetch(fail_paths=['/article'])
        pipe = self.make_pipeline(fetch=fetch)
        res = pipe.process_link(_URL)
        self.assertEqual(fetch.calls, [])
        self.assertEqual(res['outcome'], 'processed')
        self.assertIn('✅ hand-delivered', self.table_text())
        self.assertFalse(os.path.exists(placeholder))

    def test_a_non_hand_link_keeps_the_old_consume_law(self):
        # allow_consumed is the GESTURE's privilege: a plain link with
        # a consumed page still falls to the machine doors (the wall
        # answers again — the old law untouched)
        half = self.write_half_note(_URL)
        self.db.mark_processed(_CANON, half, '', '', 'full')
        self.in_vault.add(_CANON)
        self.deliver(_URL, consume=True)
        self.write_table([(_URL, ' - ', '')])
        fetch = _FakeFetch(fail_paths=['/article'])
        pipe = self.make_pipeline(fetch=fetch)
        res = pipe.process_link(_URL)
        # " - " rows are the master-retry pass's business — skipped
        # here as "already in the websites vault" (the old law)
        self.assertEqual(res['outcome'], 'skipped')


# ---------------------------------------------------------------------------
# 5. the routing fix (the CLI call shape)
# ---------------------------------------------------------------------------

class _CliCase(unittest.TestCase):
    """Shared plumbing: a temp vault + config, and the APP_DIR patch
    that keeps the CLI's cache.db writes inside the tmp dir (the
    test_phase4 hermetic pattern, extended to the two modules that
    anchor the state ledger)."""

    def setUp(self):
        dryrun.disable()
        self.tmp = tempfile.mkdtemp(prefix='cliroute-')
        self.vault = os.path.join(self.tmp, 'websites')
        os.makedirs(os.path.join(self.vault, '_review'), exist_ok=True)
        self._cfg_path = os.path.join(self.tmp, 'config.json')
        with open(self._cfg_path, 'w', encoding='utf-8') as f:
            json.dump({'website_vault_path': self.vault,
                       'pipelines': {'websites': True, 'github': False},
                       'llm_provider': 'cloud',
                       'cloud_api_url': 'http://x',
                       'cloud_api_key': 'k'}, f)
        import gitcurator.constants as _consts
        from gitcurator.core import website_state as _wstate
        self._consts = _consts
        self._wstate = _wstate
        self._orig_consts_dir = _consts.APP_DIR
        self._orig_wstate_dir = _wstate.APP_DIR
        _consts.APP_DIR = self.tmp      # the CLI's function-level import
        _wstate.APP_DIR = self.tmp      # WebsiteStateDB()'s default
        self.addCleanup(self._restore_dirs)

    def _restore_dirs(self):
        self._consts.APP_DIR = self._orig_consts_dir
        self._wstate.APP_DIR = self._orig_wstate_dir
        dryrun.disable()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _args(self):
        return types.SimpleNamespace(
            config=self._cfg_path, vault=None, dry_run=False)

    def _write_hand_row(self):
        """A 🖐 hand row in the master table (the CLI's links source —
        a hand row that has NOT been delivered yet wants a tab)."""
        lines = ['# t', '', '| # | Date | URL | Domain | Source | '
                 'Status | Notes |',
                 '|---|------|-----|--------|--------|--------|'
                 '-------|',
                 f"| - | {_TODAY} | {_URL} | d | test | 🖐 hand |  |"]
        with open(wp.decommission_table_path(self.vault), 'w',
                  encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')

    def _half_fetched_vault(self):
        """The false-success shape on disk: a 🖐 row + a consumed
        queue entry + a half-fetched _review note + the state row."""
        half = os.path.join(self.vault, '_review', 'a.md')
        with open(half, 'w', encoding='utf-8') as f:
            f.write(wp.build_review_note(
                _URL, 'full', 'classification confidence was low'))
        lines = ['# t', '', '| # | Date | URL | Domain | Source | '
                 'Status | Notes |',
                 '|---|------|-----|--------|--------|--------|'
                 '-------|',
                 f"| - | {_TODAY} | {_URL} | d | test | 🖐 hand |  |"]
        with open(wp.decommission_table_path(self.vault), 'w',
                  encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        hd.enqueue_hand_delivery(self.vault, [_URL], walls={_URL: _ERR},
                                 log=lambda *a, **k: None)
        sug = hd.suggested_filename(_URL)
        page = os.path.join(hd.hand_delivery_dir(self.vault), sug)
        os.makedirs(os.path.dirname(page), exist_ok=True)
        with open(page, 'wb') as f:
            f.write(b'<html><body>the page</body></html>')
        queue = hd._read_queue(self.vault)
        queue['links'][_URL]['consumed'] = '2026-10-01 10:00'
        hd.atomic_write_text(hd.queue_path(self.vault),
                             json.dumps(queue, indent=2))
        state = wp.WebsiteStateDB()
        try:
            state.mark_processed(_CANON, half, '', '', 'full')
        finally:
            state.close()


class TestCLIRouting(_CliCase):

    def test_delivered_pages_ride_as_website_links(self):
        from gitcurator import cli as cli_mod
        from gitcurator.core import chrome_tabs as ct
        self._write_hand_row()      # the link that wants a tab
        seen = {}

        def _fake_deliver(vault, links, log=None, config=None, **kw):
            return {'delivered': 1, 'failed': 0, 'urls': [_URL],
                    'failed_links': [], 'folder': ''}

        def _fake_batch(cfg, mode, *, urls=None, non_github=None,
                        **kw):
            seen.update(mode=mode, urls=list(urls or []),
                        non_github=list(non_github or []))
            return 0

        with mock.patch.object(ct, 'deliver_pages_via_chrome',
                               _fake_deliver), \
                mock.patch.object(cli_mod, 'run_batch_visual',
                                  _fake_batch), \
                mock.patch.object(cli_mod, 'preflight_checks',
                                  lambda cfg: True), \
                mock.patch.object(cli_mod, 'cli_print',
                                  lambda *a, **k: None):
            rc = cli_mod.cmd_chrome_retry(self._args())
        self.assertEqual(rc, 0)
        # THE ROUTING LAW: website URLs in non_github, NEVER in urls —
        # the old call fed them to the GitHub loop and the LLM never
        # ran ("in logs it shows this ✅ but actually the llm model
        # does not actually work on that website")
        self.assertEqual(seen['urls'], [])
        self.assertIn(_URL, seen['non_github'])

    def test_the_redo_set_joins_the_rerun(self):
        from gitcurator import cli as cli_mod
        from gitcurator.core import chrome_tabs as ct
        self._half_fetched_vault()
        seen = {}

        def _fake_deliver(vault, links, log=None, config=None, **kw):
            # nothing NEW was delivered (the page landed long ago)
            return {'delivered': 0, 'failed': 0, 'urls': [],
                    'failed_links': [], 'folder': ''}

        def _fake_batch(cfg, mode, *, urls=None, non_github=None,
                        **kw):
            seen.update(non_github=list(non_github or []))
            return 0

        with mock.patch.object(ct, 'deliver_pages_via_chrome',
                               _fake_deliver), \
                mock.patch.object(cli_mod, 'run_batch_visual',
                                  _fake_batch), \
                mock.patch.object(cli_mod, 'preflight_checks',
                                  lambda cfg: True), \
                mock.patch.object(cli_mod, 'cli_print',
                                  lambda *a, **k: None):
            rc = cli_mod.cmd_chrome_retry(self._args())
        self.assertEqual(rc, 0)
        # the redo set rode the re-run — no Chrome tab was opened
        self.assertEqual(seen['non_github'], [_CANON])

    def test_no_delivered_and_no_redo_is_the_honest_one(self):
        from gitcurator import cli as cli_mod
        from gitcurator.core import chrome_tabs as ct
        self._write_hand_row()      # the link that wants a tab
        msgs = []

        def _fake_deliver(vault, links, log=None, config=None, **kw):
            return {'delivered': 0, 'failed': 1, 'urls': [],
                    'failed_links': [{'url': _URL,
                                      'error': 'chrome refused'}],
                    'folder': ''}

        with mock.patch.object(ct, 'deliver_pages_via_chrome',
                               _fake_deliver), \
                mock.patch.object(cli_mod, 'run_batch_visual',
                                  lambda *a, **kw: 0), \
                mock.patch.object(cli_mod, 'preflight_checks',
                                  lambda cfg: True), \
                mock.patch.object(cli_mod, 'cli_print',
                                  lambda m, l='info', **k:
                                  msgs.append(m)):
            rc = cli_mod.cmd_chrome_retry(self._args())
        self.assertEqual(rc, 1)
        self.assertTrue(any('delivered nothing' in m for m in msgs))


# ---------------------------------------------------------------------------
# 6. the GUI wiring (the mixins, guarded)
# ---------------------------------------------------------------------------

try:
    from gitcurator.gui.main_window.hero import HeroMixin
    from gitcurator.gui.main_window.processing_control import (
        ProcessingControlMixin)
    _QT_OK = True
except BaseException:  # noqa: BLE001 — PyQt6 stack unavailable
    _QT_OK = False


@unittest.skipUnless(_QT_OK, "PyQt6 stack unavailable (runs in CI)")
class TestHeroRedoRouting(unittest.TestCase):
    """The caught-up claim may only be said after the redo check: a
    hand link whose note is not properly stored is work, not
    up-to-date. Bare-mixin stubs, the queue-fix suite's _hero
    pattern."""

    class _Txt:
        def __init__(self, s=""):
            self._s = s

        def text(self):
            return self._s

    def _hero(self, redo_calls=None):
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
        hero._scan_master_waiting = lambda: []
        hero._master_waiting_eyes = 0
        hero._master_starts = []
        hero._start_master_retry = lambda: hero._master_starts.append(1)
        hero._scan_master_hand = lambda: []
        hero._start_chrome_tab_retry = lambda links: None
        hero._log_caught_up_state = lambda: hero.logs.append(
            ("info", "CAUGHT_UP_STATE"))
        hero._batch_running = False

        def _redo():
            if redo_calls is not None:
                redo_calls.append(1)
            # the real pass starts a batch when it finds work
            hero._batch_running = True
        hero._maybe_redo_hand_notes = _redo
        return hero

    def test_redo_work_holds_back_the_caught_up_claim(self):
        hero = self._hero(redo_calls=[])
        hero._after_sync_fetch("bot_check", {"success": True})
        self.assertEqual(hero._batch_running, True)
        self.assertFalse(any("All caught up" in m for _, m in hero.logs))
        self.assertFalse(any(m == "CAUGHT_UP_STATE"
                             for _, m in hero.logs))

    def test_no_redo_work_keeps_the_old_law(self):
        hero = self._hero()
        # the pass runs but finds nothing → no batch → the claim stands
        hero._maybe_redo_hand_notes = lambda: None
        hero._after_sync_fetch("bot_check", {"success": True})
        self.assertEqual(hero.states, ["sync"])
        self.assertTrue(any("All caught up" in m for _, m in hero.logs))
        self.assertTrue(any(m == "CAUGHT_UP_STATE"
                            for _, m in hero.logs))

    def test_a_bare_hero_without_the_collaborator_is_unchanged(self):
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


@unittest.skipUnless(_QT_OK, "PyQt6 stack unavailable (runs in CI)")
class TestRedoPassWiring(unittest.TestCase):
    """The redo pass itself (the processing-control mixin, bare): the
    once-per-session law bounds it, the mini-batch rides as WEBSITE
    links, and the busy/delivery gates hold. The scan seam
    (``_scan_master_redo``) is overridden — the scan itself is tested
    at the core level (TestRedoScan)."""

    def _win(self, rows=None):
        win = ProcessingControlMixin()
        win.logs = []
        win.log_message = lambda msg, level="info": win.logs.append(
            (level, msg))
        win.config = {'pipelines': {'websites': True, 'github': False},
                      'website_vault_path': self.vault}
        win._closing = False
        win.isVisible = lambda: True
        win._batch_running = False
        win._chrome_delivery_busy = False
        win._chrome_delivery_pending = 0
        win._starts = []
        win._start_worker_with_urls = \
            lambda urls, **kw: win._starts.append((list(urls or []),
                                                   dict(kw)))
        win._scan_master_redo = lambda: list(rows or [])
        return win

    def setUp(self):
        dryrun.disable()
        self.tmp = tempfile.mkdtemp(prefix='redogui-')
        self.vault = os.path.join(self.tmp, 'websites')
        os.makedirs(os.path.join(self.vault, '_review'), exist_ok=True)

    def tearDown(self):
        dryrun.disable()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_pass_starts_the_website_mini_batch(self):
        win = self._win(rows=[{'url': _CANON,
                               'reason': 'the note is half-fetched in '
                                         '_review'}])
        win._maybe_redo_hand_notes()
        self.assertEqual(len(win._starts), 1)
        urls, kw = win._starts[0]
        # THE ROUTING LAW at the GUI seam: website links, never
        # GitHub urls
        self.assertEqual(urls, [])
        self.assertEqual(kw.get('non_github_urls'), [_CANON])
        self.assertTrue(any('NOT properly stored' in m
                            for _, m in win.logs))

    def test_once_per_session_is_the_bound(self):
        win = self._win(rows=[{'url': _CANON, 'reason': 'x'}])
        win._maybe_redo_hand_notes()
        self.assertEqual(len(win._starts), 1)
        # the second call in the SAME session starts nothing (the
        # LLM's answer will not change on an immediate re-ask)
        win._batch_running = False
        win._maybe_redo_hand_notes()
        self.assertEqual(len(win._starts), 1)

    def test_a_pending_delivery_holds_the_pass(self):
        win = self._win(rows=[{'url': _CANON, 'reason': 'x'}])
        win._chrome_delivery_busy = True
        win._maybe_redo_hand_notes()
        self.assertEqual(win._starts, [])
        win._chrome_delivery_busy = False
        win._chrome_delivery_pending = 3
        win._maybe_redo_hand_notes()
        self.assertEqual(win._starts, [])

    def test_a_running_batch_holds_the_pass(self):
        win = self._win(rows=[{'url': _CANON, 'reason': 'x'}])
        win._batch_running = True
        win._maybe_redo_hand_notes()
        self.assertEqual(win._starts, [])

    def test_no_redo_rows_is_a_quiet_noop(self):
        win = self._win(rows=[])
        win._maybe_redo_hand_notes()
        self.assertEqual(win._starts, [])

    def test_the_websites_pipeline_gate_holds(self):
        win = self._win(rows=[{'url': _CANON, 'reason': 'x'}])
        win.config = {'pipelines': {'websites': False, 'github': True},
                      'website_vault_path': self.vault}
        win._maybe_redo_hand_notes()
        self.assertEqual(win._starts, [])

    def test_a_start_failure_is_honest_not_fatal(self):
        win = self._win(rows=[{'url': _CANON, 'reason': 'x'}])

        def _boom(urls, **kw):
            raise RuntimeError('no batch today')
        win._start_worker_with_urls = _boom
        win._maybe_redo_hand_notes()
        self.assertTrue(any('could not start' in m for _, m in win.logs))


# ---------------------------------------------------------------------------
# 7. release bookkeeping
# ---------------------------------------------------------------------------

class TestReleaseBookkeeping(unittest.TestCase):

    def _read(self, *parts):
        with open(os.path.join(self.root, *parts), 'r',
                  encoding='utf-8') as f:
            return f.read()

    def setUp(self):
        self.root = os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))

    def test_version_pin(self):
        self.assertIn('0.63.2', self._read('VERSION'))

    def test_changelog_beat(self):
        text = self._read('CHANGELOG.md')
        self.assertIn('## [0.57.0]', text)
        self.assertIn('note is the success', text)
        self.assertIn('scan_master_redo_rows', text)
        self.assertIn('note_is_properly_stored', text)

    def test_ci_registers_the_suite(self):
        text = self._read('.github', 'workflows', 'ci.yml')
        self.assertIn('tests.test_noteredo', text)

    def test_agents_lists_the_module(self):
        text = self._read('AGENTS.md')
        self.assertIn('test_noteredo', text)

    def test_the_redo_scan_is_part_of_the_tables_api(self):
        # the API contract: the scan + the test + the redo-consume are
        # exported where the other table scans live
        self.assertTrue(callable(wp.scan_master_redo_rows))
        self.assertTrue(callable(wp.note_is_properly_stored))
        self.assertTrue(callable(hd.take_hand_delivered))


if __name__ == '__main__':
    unittest.main()
