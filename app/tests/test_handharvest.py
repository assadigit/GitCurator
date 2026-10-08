"""tests/test_handharvest.py — v0.56.0, THE HAND'S HARVEST: the
gesture retires when its link's proper note lands.

The owner's report (session, verbatim): "New update: when newly
fetched website with hand (🖐) are fetched and stored correctly, the
system must automatically turn the hand emoji to green checkbox and
remove their half-fetched items from _review, because now they have
A Proper and categorized note."

Covered here (zero network — an injected fetch_fn, a fake LLM, the
house pattern; no browser, no PyQt at module level):

* the stamp — ``stamp_hand_delivered_rows``: a 🖐 row becomes
  `` ✅ hand-delivered — fetched <date> `` (a cell the table's own
  grammar reads as REVIEWED — final, never re-queued, never handed
  again); idempotent; a waiting " - " row is never the harvest's
  (that is stamp_stored_rows' business); death / reviewed / revived
  win and are never touched; URLs match by canonical form; a missing
  table reads as zero; the Last-updated line refreshes;
* the sweep — ``sweep_review_leftovers``: the link's app-owned
  ``_review`` items (the failed placeholder, the low-confidence
  'full' note — the owner's "half-fetched items", a partial) are
  removed; another link's note is untouched; the master table and
  the hand-delivered folder are never the sweep's; a hand-written
  note for the same source is KEPT and warned; ``keep_path`` is
  respected; a missing folder reads as zero; dry-run records;
* the catch-up — ``harvest_hand_rows`` via the refresh: the owner's
  BACKLOG (rows that stored correctly while the gesture waited — a
  state row with a real note outside _review) retires to ✅ and its
  leftovers are swept; a hand row that still waits (failed
  placeholder / no state row / dismissed / a _review note) is never
  harvested; a broken state probe harvests nothing; a NON-hand
  stored row keeps the 📁 stored law;
* the delivery — ``process_link`` end to end, the owner's exact
  flow: a walled link whose 3 retries BURNED OUT, the owner's 🖐 row,
  the fifth door's delivered page waiting in the folder — the hand
  REBORNS the burned-out counter, the delivered page is the fetch
  answer, the proper note lands, the row retires to ✅, the
  placeholder is swept, and the log tells the harvest's story;
  - the half-fetched exemption: a 🖐 link parked in _review with a
    NON-failed note (low confidence — fetch_status 'full') is NOT
    "already in the websites vault" — the delivery completes it and
    the half note is swept; the SAME link without the gesture keeps
    the old skip law;
  - a stored link without the gesture: still "already in the
    websites vault" (the control);
  - a burned-out link without gesture and without a delivered page:
    still "no more retries" (the control);
  - a machine-fetch success on a 🖐 row (no delivered page — the
    ladder got through): the harvest fires anyway (the law is about
    the ROW, not the door);
* the probes — ``_row_reads_hand`` / ``_has_app_review_note``: the
  table's grammar decides (death / reviewed / revived first, the
  first row wins); the mtime cache refreshes after a mid-run stamp
  (a retired row is never re-harvested); app-ownership is the
  frontmatter's ``managed_by`` line, any fetch_status;
* the run circle — ``run()`` ends with the refresh, so a hand link
  processed in the batch retires its row even if the step-7 hook was
  somehow skipped (belt and braces, both halves honest).

No PyQt import at module level (the libEGL-less sandbox rule).
"""

import json
import os
import shutil
import tempfile
import time
import unittest

from gitcurator.core import dryrun
from gitcurator.core import hand_delivery as hd
from gitcurator.core import website_pipeline as wp
from gitcurator.core import web_fetch as wf


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


_URL = 'https://walled.example.net/article'
_CANON = wp.normalize_website_url(_URL)
_ERR = 'HTTP 403 — bot defense (server: cloudflare)'
_TODAY = time.strftime('%Y-%m-%d')


class _HarvestCase(unittest.TestCase):
    """Shared plumbing: temp vault + state db + pipeline factory."""

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix='harvest-')
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

    def write_half_note(self, url, name):
        """A LOW-CONFIDENCE half-fetched item: a real app-owned _review
        note whose fetch SUCCEEDED but whose classification parked it
        for human eyes (fetch_status 'full' — the gate's blind spot
        before v0.56)."""
        return self.write_review_note(
            url, name, fetch_status='full',
            reason='classification confidence was low')

    def write_table(self, rows):
        """rows: [(url, status, notes)] → the master table, the
        writer's own grammar (7 columns, ' - ' in the # cell)."""
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

    def write_proper_note(self, url, category='Design',
                          name='proper-note.md'):
        """v0.57.0 — a REAL proper, categorized note ON DISK (the
        strict harvest's own bar: the file exists, app-owned, its
        source is THIS link, outside _review)."""
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

    def table_text(self):
        with open(wp.decommission_table_path(self.vault),
                  encoding='utf-8') as f:
            return f.read()

    def all_logs(self):
        return '\n'.join(m for (_, m) in self.logs)

    def deliver(self, url, body=b'<html><head><title>Hand-saved</title>'
                b'</head><body>saved by the owner</body></html>'):
        hd.enqueue_hand_delivery(self.vault, [url],
                                 walls={url: _ERR},
                                 log=lambda *a, **k: None)
        sug = hd.suggested_filename(url)
        path = os.path.join(hd.hand_delivery_dir(self.vault), sug)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(body)
        return path


# ---------------------------------------------------------------------------
# 1. the stamp (pure)
# ---------------------------------------------------------------------------

class TestHarvestStamp(_HarvestCase):

    def test_the_hand_row_retires_to_the_green_checkbox(self):
        self.write_table([(_URL, '🖐 hand — queued 2026-10-01', _ERR)])
        stamped = hd.stamp_hand_delivered_rows(self.vault, [_URL])
        self.assertEqual(stamped, 1)
        table = self.table_text()
        self.assertIn('✅ hand-delivered — fetched', table)
        self.assertNotIn('🖐', table)
        # the retired cell is the table's own SUCCESS verdict — the
        # grammar reads it as reviewed (final, never re-queued)
        rows = [r for r in table.splitlines() if r.startswith('| - ')]
        status = rows[0].split('|')[6]
        self.assertTrue(wp._status_is_reviewed(status))
        self.assertFalse(wp._status_is_waiting(status))

    def test_the_gesture_forms_all_retire(self):
        for s in ('🖐', '✋ hand', 'hand', '🖐 hand — queued 2026-10-01'):
            self.setUp()
            try:
                self.write_table([(_URL, s, '')])
                self.assertEqual(
                    hd.stamp_hand_delivered_rows(self.vault, [_URL]), 1)
                self.assertIn('✅ hand-delivered', self.table_text())
            finally:
                self.tearDown()

    def test_idempotent_once_retired(self):
        self.write_table([(_URL, '🖐 hand', '')])
        self.assertEqual(
            hd.stamp_hand_delivered_rows(self.vault, [_URL]), 1)
        self.assertEqual(
            hd.stamp_hand_delivered_rows(self.vault, [_URL]), 0)

    def test_a_waiting_row_is_never_the_harvests(self):
        # " - " is stamp_stored_rows' business (📁 stored), never the
        # harvest's — the two writers never fight over one cell
        self.write_table([(_URL, ' - ', _ERR)])
        self.assertEqual(
            hd.stamp_hand_delivered_rows(self.vault, [_URL]), 0)
        self.assertIn(' - ', self.table_text())

    def test_death_reviewed_revived_win(self):
        for s in ('🪦 dead', '✅ reviewed', '♻️ revived'):
            self.setUp()
            try:
                self.write_table([(_URL, s, '')])
                self.assertEqual(
                    hd.stamp_hand_delivered_rows(self.vault, [_URL]), 0)
                self.assertIn(s, self.table_text())
            finally:
                self.tearDown()

    def test_urls_match_by_canonical_form(self):
        row_url = 'https://www.walled.example.net/article'
        self.write_table([(row_url, '🖐 hand', '')])
        # called with the bare form — the canonical law bridges them
        self.assertEqual(
            hd.stamp_hand_delivered_rows(
                self.vault, ['https://walled.example.net/article']), 1)

    def test_missing_table_and_empty_urls_read_as_zero(self):
        self.assertEqual(
            hd.stamp_hand_delivered_rows(self.vault, [_URL]), 0)
        self.write_table([(_URL, '🖐 hand', '')])
        self.assertEqual(
            hd.stamp_hand_delivered_rows(self.vault, []), 0)

    def test_the_last_updated_line_refreshes(self):
        path = self.write_table([(_URL, '🖐 hand', '')])
        with open(path, encoding='utf-8') as f:
            body = f.read()
        with open(path, 'w', encoding='utf-8') as f:
            f.write('> Last updated: 2001-01-01 00:00\n' + body)
        hd.stamp_hand_delivered_rows(self.vault, [_URL])
        self.assertIn('Last updated: 20', self.table_text())
        self.assertNotIn('2001-01-01', self.table_text())

    def test_other_rows_are_untouched(self):
        other = 'https://other.example.org/x'
        self.write_table([(other, ' - ', 'waits'),
                          (_URL, '🖐 hand', '')])
        hd.stamp_hand_delivered_rows(self.vault, [_URL])
        table = self.table_text()
        self.assertIn('waits', table)
        self.assertIn('✅ hand-delivered', table)


# ---------------------------------------------------------------------------
# 2. the sweep (pure)
# ---------------------------------------------------------------------------

class TestSweepLeftovers(_HarvestCase):

    def test_the_failed_placeholder_is_swept(self):
        self.write_review_note(_URL, 'walled.example.net.md')
        swept = wp.sweep_review_leftovers(self.vault, _CANON)
        self.assertEqual(swept, 1)
        self.assertFalse(os.path.exists(
            os.path.join(self.vault, '_review',
                         'walled.example.net.md')))

    def test_the_half_fetched_items_are_swept(self):
        # the owner's exact words: "remove their half-fetched items
        # from _review" — the low-confidence 'full' note and the
        # partial ride with the failed placeholder
        self.write_review_note(_URL, 'a.md', fetch_status='failed')
        self.write_half_note(_URL, 'a_v1.md')
        self.write_review_note(_URL, 'b.md', fetch_status='partial',
                               reason='archived copy')
        self.assertEqual(wp.sweep_review_leftovers(self.vault, _CANON), 3)

    def test_another_links_note_is_untouched(self):
        self.write_review_note(_URL, 'a.md')
        self.write_review_note('https://other.example.org/x', 'b.md')
        self.assertEqual(wp.sweep_review_leftovers(self.vault, _CANON), 1)
        self.assertTrue(os.path.exists(
            os.path.join(self.vault, '_review', 'b.md')))

    def test_the_master_table_is_never_swept(self):
        self.write_table([(_URL, '🖐 hand', '')])
        self.assertEqual(wp.sweep_review_leftovers(self.vault, _CANON), 0)
        self.assertIn('Review Master Table', self.table_text())

    def test_a_hand_written_note_is_kept_and_warned(self):
        review_dir = os.path.join(self.vault, '_review')
        path = os.path.join(review_dir, 'owners.md')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(f'---\nsource: {_URL}\n---\n\nthe owner wrote '
                    'this one by hand\n')
        logs = []
        swept = wp.sweep_review_leftovers(
            self.vault, _CANON, log=lambda m, l='info': logs.append(m))
        self.assertEqual(swept, 0)
        self.assertTrue(os.path.exists(path))
        self.assertTrue(any('kept' in m for m in logs))

    def test_keep_path_is_respected(self):
        path = self.write_review_note(_URL, 'a.md')
        self.assertEqual(
            wp.sweep_review_leftovers(self.vault, _CANON,
                                      keep_path=path), 0)
        self.assertTrue(os.path.exists(path))

    def test_missing_folder_and_no_source_read_as_zero(self):
        shutil.rmtree(os.path.join(self.vault, '_review'))
        self.assertEqual(wp.sweep_review_leftovers(self.vault, _CANON), 0)
        self.write_review_note(_URL, 'a.md')
        # a note whose source is another link is not this sweep's
        self.assertEqual(
            wp.sweep_review_leftovers(
                self.vault, wp.normalize_website_url(
                    'https://elsewhere.example.org/')), 0)

    def test_dry_run_records_without_removing(self):
        path = self.write_review_note(_URL, 'a.md')
        dryrun.enable()
        try:
            swept = wp.sweep_review_leftovers(self.vault, _CANON)
        finally:
            dryrun.disable()
        self.assertEqual(swept, 1)
        self.assertTrue(os.path.exists(path))


# ---------------------------------------------------------------------------
# 3. the catch-up (the backlog the previous runs left behind)
# ---------------------------------------------------------------------------

class TestHarvestCatchUp(_HarvestCase):

    def test_the_backlog_row_retires_and_sweeps(self):
        # THE OWNER'S BACKLOG: the link stored correctly in an earlier
        # run while the gesture waited — the table still shows 🖐.
        # v0.57.0: "stored correctly" now means the note is ON DISK
        # (note_is_properly_stored — the strict harvest's own bar)
        proper = self.write_proper_note(_URL)
        self.db.mark_processed(
            _CANON, proper,
            'Design', '', 'full')
        self.write_review_note(_URL, 'a.md')     # the leftover
        self.write_table([(_URL, '🖐 hand', '')])
        harvested = wp.harvest_hand_rows(self.db, self.vault)
        self.assertEqual(harvested, 1)
        self.assertIn('✅ hand-delivered', self.table_text())
        self.assertFalse(os.path.exists(
            os.path.join(self.vault, '_review', 'a.md')))

    def test_a_hand_row_that_still_waits_is_untouched(self):
        self.db.mark_processed(
            _CANON, os.path.join(self.vault, '_review', 'a.md'),
            '', '', 'failed')
        self.write_table([(_URL, '🖐 hand', '')])
        self.assertEqual(
            wp.harvest_hand_rows(self.db, self.vault), 0)
        self.assertIn('🖐', self.table_text())

    def test_a_review_note_row_is_untouched(self):
        # a NON-failed _review note (the low-confidence park) is human
        # eyes' business — the CATCH-UP never harvests it (the
        # delivery-time exemption is the only path that completes it)
        self.db.mark_processed(
            _CANON, os.path.join(self.vault, '_review', 'a.md'),
            '', '', 'full')
        self.write_table([(_URL, '🖐 hand', '')])
        self.assertEqual(
            wp.harvest_hand_rows(self.db, self.vault), 0)

    def test_no_state_row_or_dismissed_is_untouched(self):
        self.write_table([(_URL, '🖐 hand', '')])
        self.assertEqual(
            wp.harvest_hand_rows(self.db, self.vault), 0)
        self.db.dismiss(_CANON, 'owner retired it')
        self.db.mark_processed(
            _CANON, os.path.join(self.vault, 'Design', 'n.md'),
            'Design', '', 'full')
        self.assertEqual(
            wp.harvest_hand_rows(self.db, self.vault), 0)

    def test_a_state_row_pointing_at_a_missing_file_is_a_false_success(self):
        # v0.57.0 — THE NOTE IS THE SUCCESS: a state row that remembers
        # a note the vault no longer carries (deleted, moved, a vault
        # switch) is the owner's exact "false success" — the ✅ is
        # DENIED and the row keeps its gesture for the redo pass
        self.db.mark_processed(
            _CANON, os.path.join(self.vault, 'Design', 'gone.md'),
            'Design', '', 'full')
        self.write_table([(_URL, '🖐 hand', '')])
        self.assertEqual(
            wp.harvest_hand_rows(self.db, self.vault), 0)
        self.assertIn('🖐', self.table_text())
        self.assertNotIn('✅ hand-delivered', self.table_text())

    def test_a_broken_state_probe_harvests_nothing(self):
        proper = self.write_proper_note(_URL)
        self.db.mark_processed(
            _CANON, proper, 'Design', '', 'full')
        self.write_table([(_URL, '🖐 hand', '')])

        class _Broken:
            def is_dismissed(self, url):
                raise RuntimeError('probe down')

            def processed_row(self, url):
                raise RuntimeError('probe down')

        self.assertEqual(
            wp.harvest_hand_rows(_Broken(), self.vault), 0)
        self.assertIn('🖐', self.table_text())

    def test_a_non_hand_stored_row_keeps_the_stored_law(self):
        self.db.mark_processed(
            _CANON, os.path.join(self.vault, 'Design', 'n.md'),
            'Design', '', 'full')
        self.write_table([(_URL, ' - ', 'old error')])
        wp.refresh_master_table(self.db, self.vault,
                                log=lambda *a, **k: None)
        table = self.table_text()
        self.assertIn('📁 stored', table)
        self.assertNotIn('✅ hand-delivered', table)


# ---------------------------------------------------------------------------
# 4. the delivery (the owner's exact flow, end to end)
# ---------------------------------------------------------------------------

class TestHarvestDelivery(_HarvestCase):

    def test_the_owners_exact_flow_burned_out_then_delivered(self):
        # walled → 3 retries burned out → the owner sets 🖐 → the
        # fifth door delivers the page → the re-run completes it all
        for _ in range(3):
            self.db.enqueue_retry(_CANON, _ERR)
        self.assertEqual(self.db.retry_row(_CANON)['attempts'], 3)
        placeholder = self.write_review_note(_URL, 'a.md')
        self.in_vault.add(_CANON)
        self.write_table([(_URL, '🖐 hand — queued 2026-10-01', _ERR)])
        self.deliver(_URL)
        fetch = _FakeFetch(fail_paths=['/article'])
        pipe = self.make_pipeline(fetch=fetch)
        res = pipe.process_link(_URL)
        # the machine door was never asked — the delivered page answered
        self.assertEqual(fetch.calls, [])
        self.assertEqual(res['outcome'], 'processed')
        self.assertTrue(os.path.exists(res['note_path']))
        # THE HARVEST: the row retired, the placeholder swept
        table = self.table_text()
        self.assertIn('✅ hand-delivered — fetched', table)
        self.assertNotIn('🖐', table)
        self.assertFalse(os.path.exists(placeholder))
        self.assertIn("the hand's harvest", self.all_logs())
        self.assertIn('reborn', self.all_logs())
        # the link is DONE: the retired row is final for every scan
        self.assertEqual(wp.scan_master_hand_rows(self.vault), [])
        waiting = wp.scan_master_waiting_rows(self.vault)
        self.assertEqual([w for w in waiting
                          if w['url'] == _URL], [])

    def test_the_hand_answer_with_a_fresh_retry(self):
        # the simpler shape: first failure, the gesture, the delivery
        self.db.enqueue_retry(_CANON, _ERR)
        placeholder = self.write_review_note(_URL, 'a.md')
        self.in_vault.add(_CANON)
        self.write_table([(_URL, '🖐 hand', '')])
        self.deliver(_URL)
        pipe = self.make_pipeline(fetch=_FakeFetch(
            fail_paths=['/article']))
        res = pipe.process_link(_URL)
        self.assertEqual(res['outcome'], 'processed')
        self.assertIn('✅ hand-delivered', self.table_text())
        self.assertFalse(os.path.exists(placeholder))

    def test_the_half_fetched_exemption_completes_a_parked_link(self):
        # a 🖐 link parked in _review with a NON-failed note (low
        # confidence) was skipped "already in the websites vault"
        # forever — the gesture now says FINISH THIS ONE
        half = self.write_half_note(_URL, 'a.md')
        self.in_vault.add(_CANON)
        self.db.mark_processed(
            _CANON, half, '', '', 'full')
        self.write_table([(_URL, '🖐 hand', '')])
        self.deliver(_URL)
        pipe = self.make_pipeline(fetch=_FakeFetch(
            fail_paths=['/article']))
        res = pipe.process_link(_URL)
        self.assertEqual(res['outcome'], 'processed')
        self.assertIn('✅ hand-delivered', self.table_text())
        self.assertFalse(os.path.exists(half))
        self.assertIn("the hand's harvest", self.all_logs())

    def test_the_same_parked_link_without_the_gesture_still_skips(self):
        # the old law holds for every link without the gesture — the
        # exemption is the hand's, not a blanket change
        half = self.write_half_note(_URL, 'a.md')
        self.in_vault.add(_CANON)
        self.db.mark_processed(_CANON, half, '', '', 'full')
        self.write_table([(_URL, ' - ', '')])
        self.deliver(_URL)
        pipe = self.make_pipeline(fetch=_FakeFetch())
        res = pipe.process_link(_URL)
        self.assertEqual(res['outcome'], 'skipped')
        self.assertEqual(res['error'], 'already in the websites vault')
        self.assertTrue(os.path.exists(half))

    def test_a_stored_link_without_the_gesture_is_the_control(self):
        self.db.mark_processed(
            _CANON, os.path.join(self.vault, 'Design', 'n.md'),
            'Design', '', 'full')
        self.in_vault.add(_CANON)
        self.write_table([(_URL, ' - ', '')])
        pipe = self.make_pipeline(fetch=_FakeFetch())
        res = pipe.process_link(_URL)
        self.assertEqual(res['outcome'], 'skipped')

    def test_burned_out_without_gesture_or_page_keeps_the_old_law(self):
        for _ in range(3):
            self.db.enqueue_retry(_CANON, _ERR)
        placeholder = self.write_review_note(_URL, 'a.md')
        self.in_vault.add(_CANON)
        self.db.mark_processed(_CANON, placeholder, '', '', 'failed')
        self.write_table([(_URL, ' - ', '')])
        pipe = self.make_pipeline(fetch=_FakeFetch())
        res = pipe.process_link(_URL)
        self.assertEqual(res['outcome'], 'skipped')
        self.assertIn('no more retries', res['error'])

    def test_a_delivered_page_alone_reborns_the_counter(self):
        # the picker's queue (a delivered page waiting) reborns even
        # without a hand-marked row — the page IS the ask
        for _ in range(3):
            self.db.enqueue_retry(_CANON, _ERR)
        placeholder = self.write_review_note(_URL, 'a.md')
        self.in_vault.add(_CANON)
        self.db.mark_processed(_CANON, placeholder, '', '', 'failed')
        self.deliver(_URL)
        fetch = _FakeFetch(fail_paths=['/article'])
        pipe = self.make_pipeline(fetch=fetch)
        res = pipe.process_link(_URL)
        self.assertEqual(res['outcome'], 'processed')
        self.assertEqual(fetch.calls, [])
        self.assertIn('reborn', self.all_logs())

    def test_a_machine_success_on_a_hand_row_is_harvested_too(self):
        # the ladder got through on its own — the law is about the
        # ROW (the owner's gesture), not the door that answered
        self.write_table([(_URL, '🖐 hand', '')])
        pipe = self.make_pipeline(fetch=_FakeFetch())
        res = pipe.process_link(_URL)
        self.assertEqual(res['outcome'], 'processed')
        self.assertIn('✅ hand-delivered', self.table_text())
        self.assertIn("the hand's harvest", self.all_logs())

    def test_run_ends_with_the_refresh_harvest(self):
        # the belt-and-braces half: the batch's last move retires the
        # row even if the step-7 hook was somehow skipped
        proper = self.write_proper_note(_URL)
        self.db.mark_processed(
            _CANON, proper, 'Design', '', 'full')
        self.write_table([(_URL, '🖐 hand', '')])
        other = 'https://fresh.example.net/page'
        pipe = self.make_pipeline(fetch=_FakeFetch())
        pipe.run([other])
        self.assertIn('✅ hand-delivered', self.table_text())


# ---------------------------------------------------------------------------
# 5. the probes (pure, cached honestly)
# ---------------------------------------------------------------------------

class TestProbes(_HarvestCase):

    def test_row_reads_hand_by_the_tables_grammar(self):
        pipe = self.make_pipeline(_FakeFetch())
        self.write_table([(_URL, '🖐 hand', '')])
        self.assertTrue(pipe._row_reads_hand(_CANON))
        for s in ('🪦 dead', '✅ reviewed', '♻️ revived', ' - ',
                  'unreviewed'):
            self.setUp()
            try:
                pipe = self.make_pipeline(_FakeFetch())
                self.write_table([(_URL, s, '')])
                self.assertFalse(pipe._row_reads_hand(_CANON))
            finally:
                self.tearDown()

    def test_row_reads_hand_no_table_no_row(self):
        pipe = self.make_pipeline(_FakeFetch())
        self.assertFalse(pipe._row_reads_hand(_CANON))
        self.write_table([('https://other.example.org/x', '🖐 hand',
                           '')])
        self.assertFalse(pipe._row_reads_hand(_CANON))

    def test_the_cache_refreshes_after_a_mid_run_stamp(self):
        pipe = self.make_pipeline(_FakeFetch())
        self.write_table([(_URL, '🖐 hand', '')])
        self.assertTrue(pipe._row_reads_hand(_CANON))
        hd.stamp_hand_delivered_rows(self.vault, [_CANON])
        # the mtime-keyed cache re-reads — a retired row is never
        # re-harvested
        self.assertFalse(pipe._row_reads_hand(_CANON))

    def test_has_app_review_note_any_status(self):
        pipe = self.make_pipeline(_FakeFetch())
        self.write_review_note(_URL, 'a.md')
        self.assertTrue(pipe._has_app_review_note(_CANON))
        self.setUp()
        try:
            pipe = self.make_pipeline(_FakeFetch())
            self.write_half_note(_URL, 'a.md')
            self.assertTrue(pipe._has_app_review_note(_CANON))
        finally:
            self.tearDown()

    def test_has_app_review_note_hand_written_is_no(self):
        pipe = self.make_pipeline(_FakeFetch())
        path = os.path.join(self.vault, '_review', 'owners.md')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(f'---\nsource: {_URL}\n---\n\nby hand\n')
        self.assertFalse(pipe._has_app_review_note(_CANON))

    def test_has_app_review_note_missing_folder_is_no(self):
        shutil.rmtree(os.path.join(self.vault, '_review'))
        pipe = self.make_pipeline(_FakeFetch())
        self.assertFalse(pipe._has_app_review_note(_CANON))


# ---------------------------------------------------------------------------
# 6. release bookkeeping
# ---------------------------------------------------------------------------

class TestReleaseBookkeeping(unittest.TestCase):
    """The harvest ships as a real release: the VERSION pin, the
    CHANGELOG beat, the CI registration, the AGENTS.md listing."""

    def setUp(self):
        self.root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def _read(self, *parts):
        with open(os.path.join(self.root, *parts), 'r',
                  encoding='utf-8') as f:
            return f.read()

    def test_version_pin(self):
        self.assertEqual(self._read('VERSION').strip(), '0.57.0')

    def test_changelog_beat(self):
        changelog = self._read('CHANGELOG.md')
        self.assertIn('## [0.57.0]', changelog)
        self.assertIn("the hand emoji to green checkbox", changelog)

    def test_ci_registers_the_suite(self):
        ci = self._read('.github', 'workflows', 'ci.yml')
        self.assertIn('tests.test_handharvest', ci)

    def test_agents_lists_the_module(self):
        agents = self._read('AGENTS.md')
        self.assertIn('test_handharvest', agents)

    def test_the_harvest_is_part_of_the_doors_api(self):
        # the stamp is exported where every door's public name lives —
        # the callers (the pipeline's step-7 hook, the catch-up pass)
        # import it by name, so the contract is the release's to keep.
        # (The DEPLOYMENT.md v0.56.0 record is a POST-RELEASE commit
        # on main — the house ritual every release follows — so it is
        # deliberately NOT a tag-time gate; the live worker's story
        # is verified in the record itself.)
        from gitcurator.core import hand_delivery as hd
        self.assertIn('stamp_hand_delivered_rows', hd.__all__)


if __name__ == '__main__':
    unittest.main()
