"""tests/test_decommission.py — v0.44.0, the graveyard.

The owner's ask: "The system must have a procedure for decommissioning
links, for example some websites are genuinely 404 or lost or abandoned.
but if I remove their links from _review folder, they will be fetched
again and get error again, we need a procedure for it, like the table we
had which I set an emoji for it (tick emoji as reviewed) so it never
fetches again."

Covered here (zero network — an injected fetch_fn and a fake LLM, the
house pattern):

* the table — DEAD_MARKERS (🪦 ❌ ☠️ 💀 dead retired decommissioned,
  case-insensitive substring) vs everything else (unreviewed / ✅ /
  blank); malformed rows skipped; missing table/vault no-ops.
* scan_review_backlog's graveyard filter — a dead-marked URL leaves the
  backlog (no notice, no retry offer); an unreviewed candidate row does
  not; the table file itself is never a backlog item.
* write_decommission_candidates — creates the header table; appends
  after the last data row; never duplicates a URL; never clobbers an
  owner-edited Status cell.
* consume_decommission_table — the full burial: dismissed (the
  never-fetch gate), retry-queue row dropped, failed processed row
  forgotten, app-owned failed placeholder FILE swept, row Status
  rewritten to "🪦 confirmed"; hand-edited placeholder kept; ♻️ revived
  removes the dismissal; idempotent; dry-run sweeps nothing.
* THE LOOP, CLOSED — the owner's exact complaint: a dead link whose
  placeholder he deleted still sat in the retry queue, so any re-arm
  fetched it again and wrote a FRESH error placeholder. With the
  graveyard row marked dead: the queue row is dropped at consume, the
  re-arm finds nothing, the link is never fetched — even a future paste
  of the same URL is skipped as decommissioned.

No PyQt import at module level (the libEGL-less sandbox rule) — the GUI
wiring (3-choice notice, More ▸ 🪦 Decommission dead links) is exercised
by the compile gate + CI's headless Qt suite.
"""

import json
import os
import shutil
import tempfile
import unittest

from gitcurator.core import dryrun
from gitcurator.core import website_pipeline as wp
from gitcurator.core import web_fetch as _web_fetch


# -- the fakes (the test_reviewretry pattern, kept local) ------------------

class _FakeFetch:
    """Canned fetch results; whole-path failures simulate the wall."""

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
                reason='HTTP 404 — not found')
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


class _GraveyardCase(unittest.TestCase):
    """Shared plumbing: temp vault + state db + pipeline factory."""

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix='graveyard-')
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

    def write_placeholder(self, url, name, fetch_status='failed',
                          managed=True):
        """Hand-write a _review note the way build_review_note does."""
        review_dir = os.path.join(self.vault, '_review')
        os.makedirs(review_dir, exist_ok=True)
        path = os.path.join(review_dir, name)
        note = wp.build_review_note(url, fetch_status,
                                    'Fetch failed: HTTP 404 — not found')
        if not managed:
            note = note.replace(
                'managed_by: "%s"' % wp.MANAGED_BY_GITCURATOR,
                'managed_by: "a-human"')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(note)
        return path

    def write_table(self, rows):
        """Write the graveyard table; rows = [(url, status), ...]."""
        path = wp.decommission_table_path(self.vault)
        lines = [wp._GRAVEYARD_HEADER.format(now='2026-10-08 12:00')]
        for url, status in rows:
            from urllib.parse import urlparse
            domain = urlparse(url).netloc or 'unknown'
            lines.append(f"| - | 2026-10-08 | {url} | {domain} "
                         f"| test | {status} | |")
        content = '\n'.join(lines)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return path

    def table_text(self):
        path = wp.decommission_table_path(self.vault)
        if not os.path.isfile(path):
            return ''
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()

    def all_logs(self):
        return '\n'.join(m for (_, m) in self.logs)


class TestStatusMarkers(_GraveyardCase):

    def test_dead_marker_variants(self):
        dead = ('🪦', '❌ dead', '☠️', '💀 gone', 'dead',
                'DEAD', 'Decommissioned', 'retired 2026',
                '🪦 dead — abandoned long ago')
        for s in dead:
            self.assertTrue(wp._status_is_dead(s), s)
        alive = ('', 'unreviewed', '✅ reviewed', 'maybe', 'later',
                 'walled — still trying', ' ')
        for s in alive:
            self.assertFalse(wp._status_is_dead(s), s)

    def test_revive_markers(self):
        for s in ('♻️ revived', 'revived', 'Restored',
                  '♻️ back from the dead'):
            self.assertTrue(wp._status_is_revived(s), s)
        for s in ('', 'unreviewed', '🪦 dead', 'dead'):
            self.assertFalse(wp._status_is_revived(s), s)

    def test_scan_missing_table_and_vault(self):
        self.assertEqual(wp.scan_decommission_table(self.vault), {})
        self.assertEqual(wp.scan_decommission_table(''), {})
        self.assertEqual(wp.scan_decommission_table(
            os.path.join(self.tmp, 'nope')), {})

    def test_malformed_rows_skipped_first_row_wins(self):
        path = wp.decommission_table_path(self.vault)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(wp._GRAVEYARD_HEADER.format(now='now'))
            f.write('| - | 2026-10-08 | https://a.example/x | a.example'
                    ' | t | 🪦 dead | |\n')
            f.write('| short | row |\n')                       # < 8 cols
            f.write('| - | d | ftp://not-http | x | t | 🪦 dead | |\n')
            f.write('| - | 2026-10-08 | https://a.example/x | a.example'
                    ' | t | unreviewed | |\n')                  # duplicate
        rows = wp.scan_decommission_table(self.vault)
        self.assertEqual(rows, {'https://a.example/x': '🪦 dead'})


class TestBacklogGraveyardFilter(_GraveyardCase):

    def test_dead_marked_placeholder_leaves_backlog(self):
        self.write_placeholder('https://example.com/gone', 'a.md')
        self.write_table([('https://example.com/gone', '🪦 dead')])
        self.assertEqual(wp.scan_review_backlog(self.vault), [])

    def test_unreviewed_candidate_row_stays_in_backlog(self):
        p = self.write_placeholder('https://example.com/walled', 'a.md')
        self.write_table([('https://example.com/walled', 'unreviewed')])
        items = wp.scan_review_backlog(self.vault)
        self.assertEqual(items, [{'url': 'https://example.com/walled',
                                  'path': p}])

    def test_revived_row_stays_in_backlog(self):
        # ♻️ revived is NOT dead — the link is alive again, so its failed
        # placeholder (if it still exists) belongs to the retry backlog.
        self.write_placeholder('https://example.com/back', 'a.md')
        self.write_table([('https://example.com/back', '♻️ revived')])
        self.assertEqual(len(wp.scan_review_backlog(self.vault)), 1)

    def test_table_file_itself_never_a_backlog_item(self):
        self.write_table([('https://example.com/x', 'unreviewed')])
        # even if the table somehow carried failed-looking frontmatter
        path = wp.decommission_table_path(self.vault)
        with open(path, 'a', encoding='utf-8') as f:
            f.write('---\nsource: https://example.com/x\n'
                    'fetch_status: "failed"\n'
                    'managed_by: "gitcurator"\n---\n')
        self.assertEqual(wp.scan_review_backlog(self.vault), [])


class TestWriteCandidates(_GraveyardCase):

    def test_creates_table_with_header_and_rows(self):
        n = wp.write_decommission_candidates(
            self.vault, ['https://example.com/gone',
                         'https://sub.example.org/lost'])
        self.assertEqual(n, 2)
        text = self.table_text()
        self.assertIn('# Review Master Table — decommission or approve',
                      text)
        self.assertIn('| # | Date | URL | Domain | Source | Status |',
                      text)
        self.assertIn('https://example.com/gone', text)
        self.assertIn('example.com', text)
        self.assertIn('sub.example.org', text)
        self.assertIn('unreviewed', text)

    def test_no_duplicates_no_owner_clobber(self):
        wp.write_decommission_candidates(
            self.vault, ['https://example.com/gone'])
        # the owner sets the emoji by hand:
        path = wp.decommission_table_path(self.vault)
        with open(path, 'r', encoding='utf-8') as f:
            text = f.read().replace(
                'https://example.com/gone | example.com | retry backlog '
                '| unreviewed |',
                'https://example.com/gone | example.com | retry backlog '
                '| 🪦 dead |')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
        # a later write adds only the NEW url — the burial survives:
        n = wp.write_decommission_candidates(
            self.vault, ['https://example.com/gone',
                         'https://example.com/other'])
        self.assertEqual(n, 1)
        text = self.table_text()
        self.assertIn('🪦 dead', text)
        self.assertEqual(text.count('https://example.com/gone'), 1)
        self.assertIn('https://example.com/other', text)

    def test_mark_urls_dead_flips_the_status_cell(self):
        wp.write_decommission_candidates(
            self.vault, ['https://example.com/gone'])
        marked = wp.mark_urls_dead_in_table(
            self.vault, ['https://example.com/gone'])
        self.assertEqual(marked, 1)
        self.assertIn('🪦 dead — decommissioned', self.table_text())

    def test_mark_urls_dead_appends_unknown_urls(self):
        # a burial is valid even for a link that never got a placeholder:
        marked = wp.mark_urls_dead_in_table(
            self.vault, ['https://example.com/never-fetched'])
        self.assertEqual(marked, 1)
        self.assertIn('https://example.com/never-fetched',
                      self.table_text())
        self.assertIn('🪦 dead — decommissioned', self.table_text())


class TestConsume(_GraveyardCase):

    def _wall_in(self, url, path='/gone'):
        """Process a failing link, exhaust its 3 retries: the exact state
        of the owner's dead-link pile."""
        pipe = self.make_pipeline(fetch=_FakeFetch(fail_paths=[path]))
        r = pipe.process_link(url)
        self.assertEqual(r['outcome'], 'review')
        canonical = r['canonical']
        for _ in range(2):
            self.db.enqueue_retry(canonical, 'HTTP 404')
        self.assertEqual(self.db.retry_row(canonical)['attempts'], 3)
        return canonical, r['note_path']

    def test_the_full_burial(self):
        url = 'https://example.com/gone'
        canonical, placeholder = self._wall_in(url)
        self.write_table([(url, '🪦 dead')])
        report = wp.consume_decommission_table(self.db, self.vault,
                                               log=lambda *a, **k: None)
        self.assertEqual(report['dead'], 1)
        self.assertEqual(report['placeholders_swept'], 1)
        self.assertEqual(report['rows_confirmed'], 1)
        self.assertFalse(os.path.exists(placeholder))
        self.assertTrue(self.db.is_dismissed(canonical))
        self.assertIsNone(self.db.retry_row(canonical))
        self.assertIsNone(self.db.processed_row(canonical))
        self.assertIn('🪦 confirmed — decommissioned', self.table_text())

    def test_hand_edited_placeholder_kept(self):
        url = 'https://example.com/edited'
        canonical, placeholder = self._wall_in(url, path='/edited')
        # the owner edited the placeholder — it is his now:
        with open(placeholder, 'r', encoding='utf-8') as f:
            note = f.read().replace(
                'managed_by: "%s"' % wp.MANAGED_BY_GITCURATOR,
                'managed_by: "a-human"')
        with open(placeholder, 'w', encoding='utf-8') as f:
            f.write(note)
        self.write_table([(url, '🪦 dead')])
        report = wp.consume_decommission_table(self.db, self.vault,
                                               log=lambda *a, **k: None)
        # the FILE is kept (ownership law) — the DB burial still holds:
        self.assertEqual(report['placeholders_swept'], 0)
        self.assertTrue(os.path.exists(placeholder))
        self.assertTrue(self.db.is_dismissed(canonical))

    def test_revive_removes_the_dismissal_and_refetches(self):
        url = 'https://example.com/back'
        canonical, placeholder = self._wall_in(url, path='/back')
        self.write_table([(url, '🪦 dead')])
        wp.consume_decommission_table(self.db, self.vault,
                                      log=lambda *a, **k: None)
        self.assertTrue(self.db.is_dismissed(canonical))
        # the owner changes his mind:
        self.write_table([(url, '♻️ revived')])
        report = wp.consume_decommission_table(self.db, self.vault,
                                               log=lambda *a, **k: None)
        self.assertEqual(report['revived'], 1)
        self.assertFalse(self.db.is_dismissed(canonical))
        # the revived link is fetched like new — and succeeds:
        fetch = _FakeFetch()
        pipe = self.make_pipeline(fetch=fetch)
        r = pipe.process_link(url)
        self.assertEqual(r['outcome'], 'processed')
        self.assertIn(url, fetch.calls)

    def test_consume_idempotent(self):
        url = 'https://example.com/gone'
        canonical, placeholder = self._wall_in(url)
        self.write_table([(url, '🪦 dead')])
        first = wp.consume_decommission_table(self.db, self.vault,
                                              log=lambda *a, **k: None)
        text_after_first = self.table_text()
        second = wp.consume_decommission_table(self.db, self.vault,
                                               log=lambda *a, **k: None)
        self.assertEqual(second['dead'], 1)          # still reads dead
        self.assertEqual(second['rows_confirmed'], 0)  # byte-stable
        self.assertEqual(self.table_text(), text_after_first)
        self.assertTrue(self.db.is_dismissed(canonical))

    def test_dry_run_sweeps_nothing(self):
        url = 'https://example.com/gone'
        self._wall_in(url)
        self.write_table([(url, '🪦 dead')])
        dryrun.enable()
        try:
            report = wp.consume_decommission_table(
                self.db, self.vault, log=lambda *a, **k: None)
        finally:
            dryrun.disable()
        self.assertEqual(report['dead'], 1)
        self.assertEqual(report['placeholders_swept'], 0)
        # every file mutation rehearsed only: the placeholder AND the
        # table's Status cell both survive untouched.
        self.assertTrue(os.path.exists(
            wp.decommission_table_path(self.vault)))
        self.assertEqual(len([n for n in os.listdir(
            os.path.join(self.vault, '_review')) if n.endswith('.md')]), 2)
        self.assertIn('🪦 dead', self.table_text())
        # v0.60.0 — the header's prose now TEACHES the gate ("on your
        # confirm…"), so the assertion targets the STAMP shape
        # ("confirmed —"), not the bare word:
        self.assertNotIn('confirmed —', self.table_text())

    def test_missing_table_is_a_no_op(self):
        report = wp.consume_decommission_table(self.db, self.vault,
                                               log=lambda *a, **k: None)
        # v0.48.0 — the report gained 'handed' (the fourth door's
        # queue gesture count); v0.58.0 — 'banished' + 'notes_moved'
        # (the 🗑️ removal pass); v0.59.0 — 'banished_urls' (the run
        # tally's list); v0.60.0 — 'pending_banish' (the confirmation
        # gate's held rows); still all zeros/empty when the table is
        # missing.
        self.assertEqual(report, {'dead': 0, 'reviewed': 0, 'banished': 0,
                                  'revived': 0, 'handed': 0,
                                  'placeholders_swept': 0,
                                  'notes_moved': 0,
                                  'rows_confirmed': 0,
                                  'banished_urls': [],
                                  'pending_banish': 0})


class TestNeverFetchAgain(_GraveyardCase):
    """THE LOOP, CLOSED — the owner's exact complaint."""

    def test_decommissioned_link_is_never_fetched(self):
        url = 'https://example.com/gone'
        self._wall_in(url)
        self.write_table([(url, '🪦 dead')])
        wp.consume_decommission_table(self.db, self.vault,
                                      log=lambda *a, **k: None)
        fetch = _FakeFetch()
        pipe = self.make_pipeline(fetch=fetch)
        r = pipe.process_link(url)
        self.assertEqual(r['outcome'], 'skipped')
        self.assertIn('decommissioned', r['error'])
        self.assertNotIn(url, fetch.calls)
        self.assertIn('🪦', self.all_logs())

    def _wall_in(self, url, path='/gone'):
        pipe = self.make_pipeline(fetch=_FakeFetch(fail_paths=[path]))
        r = pipe.process_link(url)
        self.assertEqual(r['outcome'], 'review')
        canonical = r['canonical']
        for _ in range(2):
            self.db.enqueue_retry(canonical, 'HTTP 404')
        return canonical, r['note_path']

    def test_the_delete_then_refetch_loop_is_closed(self):
        # THE scenario: the owner deletes the placeholder from _review,
        # but the link stays queued — any re-arm (backlog retry, proxy
        # epoch) fetches it AGAIN and a fresh error placeholder returns.
        url = 'https://example.com/dead'
        canonical, placeholder = self._wall_in(url, path='/dead')
        os.remove(placeholder)              # the owner's deletion
        self.db.rearm_retries()             # v0.42.0's backlog re-arm
        due = self.db.due_retries()
        self.assertIn(canonical, due)       # the loop, reproduced

        # the burial: one dead-marked row, then the next pipeline start:
        self.write_table([(url, '🪦 dead')])
        fetch = _FakeFetch()
        pipe = self.make_pipeline(fetch=fetch)   # __init__ consumes
        self.assertNotIn(canonical, self.db.due_retries())
        self.db.rearm_retries()             # even ANOTHER re-arm later
        self.assertNotIn(canonical, self.db.due_retries())
        r = pipe.process_link(url)
        self.assertEqual(r['outcome'], 'skipped')
        self.assertNotIn(url, fetch.calls)
        # and no fresh placeholder was written:
        leftovers = [n for n in os.listdir(
            os.path.join(self.vault, '_review'))
            if n.endswith('.md') and n != wp.DECOMMISSION_TABLE]
        self.assertEqual(leftovers, [])

    def test_future_paste_of_the_same_link_is_skipped(self):
        url = 'https://example.com/gone'
        canonical, _ = self._wall_in(url)
        self.write_table([(url, '🪦 dead')])
        wp.consume_decommission_table(self.db, self.vault,
                                      log=lambda *a, **k: None)
        # months later the same URL arrives in a fresh batch:
        fetch = _FakeFetch()
        pipe = self.make_pipeline(fetch=fetch)
        results = pipe.run([url])
        self.assertEqual(results[0]['outcome'], 'skipped')
        self.assertIn('decommissioned', results[0]['error'])
        self.assertEqual(fetch.calls, [])

    def test_retry_driver_leaves_dismissed_placeholders_untouched(self):
        url = 'https://example.com/gone'
        canonical, placeholder = self._wall_in(url)
        self.write_table([(url, '🪦 dead')])
        wp.consume_decommission_table(self.db, self.vault,
                                      log=lambda *a, **k: None)
        # a stale scan result (taken before the burial) is handed to the
        # driver: the dismissed contract holds — no fetch, no file touch.
        fetch = _FakeFetch()
        pipe = self.make_pipeline(fetch=fetch)
        items = [{'url': url, 'path': placeholder}]
        results = pipe.retry_review_backlog(items)
        self.assertEqual(results[0]['outcome'], 'skipped')
        self.assertEqual(fetch.calls, [])

    def test_pipeline_init_sweeps_the_dead_placeholder(self):
        url = 'https://example.com/gone'
        self._wall_in(url)
        self.write_table([(url, '🪦 dead')])
        # constructing the pipeline alone performs the burial:
        self.make_pipeline()
        leftovers = [n for n in os.listdir(
            os.path.join(self.vault, '_review'))
            if n.endswith('.md') and n != wp.DECOMMISSION_TABLE]
        self.assertEqual(leftovers, [])
        self.assertIn('🪦 Master table:', self.all_logs())


class TestThePrune(_GraveyardCase):
    """v0.60.2 — THE COMPACT TABLE (the owner's ask, verbatim: "Prune
    the decommissioned table: remove links that reached a terminal
    verdict (stored properly / blocked / banished) — keep only pending
    ones. Keep the ♻️ revive flow working (compact list or
    revive-by-URL). I don't need that old long table")."""

    def test_every_terminal_verdict_leaves_pending_stays(self):
        self.write_table([
            ('https://t.example/banished', '🗑️ banished — confirmed 2026-09-02'),
            ('https://t.example/dead', '🪦 confirmed — decommissioned 2026-09-03'),
            ('https://t.example/reviewed', '✅ confirmed — reviewed 2026-09-04'),
            ('https://t.example/hand', '✅ hand-delivered — fetched 2026-09-05'),
            ('https://t.example/stored', '📁 stored — 2026-09-06'),
            ('https://t.example/auto', '🪦 auto — refused'),
            ('https://p.example/waiting', ' - '),
            ('https://p.example/unreviewed', 'unreviewed'),
            ('https://p.example/fresh-dead', '🪦 dead'),
            ('https://p.example/fresh-banish', '🗑️ banished'),
            ('https://p.example/hand-queued', '🖐 hand — queued 2026-10-01'),
            ('https://p.example/revived', '♻️ revived'),
        ])
        self.logs.clear()
        r = wp.prune_decommission_table(self.vault,
                                        log=lambda m, l='info':
                                        self.logs.append((l, m)))
        self.assertEqual(r['pruned'], 6)
        self.assertEqual(r['kept'], 6)
        text = self.table_text()
        for gone in ('t.example/banished', 't.example/dead',
                     't.example/reviewed', 't.example/hand',
                     't.example/stored', 't.example/auto'):
            self.assertNotIn(gone, text)
        for stays in ('p.example/waiting', 'p.example/unreviewed',
                      'p.example/fresh-dead', 'p.example/fresh-banish',
                      'p.example/hand-queued', 'p.example/revived'):
            self.assertIn(stays, text)
        # the header + the legend (the revive-by-URL instruction) stay:
        self.assertIn('Review Master Table', text)
        self.assertIn('♻️', text)
        # one honest line speaks the prune:
        self.assertIn('Master table pruned: 6 terminal row(s)',
                      self.all_logs())

    def test_the_prune_is_idempotent(self):
        self.write_table([
            ('https://t.example/banished', '🗑️ banished — confirmed 2026-09-02'),
            ('https://p.example/waiting', ' - '),
        ])
        wp.prune_decommission_table(self.vault, log=lambda *a, **k: None)
        text_after_first = self.table_text()
        r2 = wp.prune_decommission_table(self.vault,
                                         log=lambda *a, **k: None)
        self.assertEqual(r2['pruned'], 0)
        self.assertEqual(r2['kept'], 1)
        self.assertEqual(self.table_text(), text_after_first)

    def test_a_pending_only_table_is_untouched(self):
        self.write_table([('https://p.example/waiting', ' - ')])
        before = self.table_text()
        r = wp.prune_decommission_table(self.vault,
                                        log=lambda *a, **k: None)
        self.assertEqual(r['pruned'], 0)
        self.assertEqual(self.table_text(), before)

    def test_the_legend_example_is_not_a_row(self):
        # the legend's revive-by-URL example must stay an INSTRUCTION:
        # a fresh table's parse sees only the intended data rows.
        self.write_table([('https://p.example/waiting', ' - ')])
        rows = wp._parse_decommission_rows(
            wp.decommission_table_path(self.vault))
        self.assertEqual([r['url'] for r in rows],
                         ['https://p.example/waiting'])
        # and the legend carries the instruction itself:
        self.assertIn('REVIVE any retired link', self.table_text())

    def test_revive_by_url_after_the_prune(self):
        # the owner's flow, end to end: a banished link's terminal row
        # left the table — the DB dismissal + .trash/banished hold the
        # record — and the legend's revive-by-URL row brings it back:
        url = 'https://r.example/retired'
        canonical = wp.normalize_website_url(url)
        self.db.dismiss(canonical, 'banished by owner — the prune test')
        self.write_table([
            (url, '🗑️ banished — confirmed 2026-09-02')])
        wp.prune_decommission_table(self.vault, log=lambda *a, **k: None)
        self.assertNotIn(url, self.table_text())
        self.assertTrue(self.db.is_dismissed(canonical))
        # the owner adds the legend's row (the trimmed hand shape — the
        # URL may sit in any early cell):
        path = wp.decommission_table_path(self.vault)
        with open(path, 'a', encoding='utf-8') as f:
            f.write(f"\n| ♻️ | | {url} | | | | |\n")
        wp.consume_decommission_table(self.db, self.vault,
                                      log=lambda *a, **k: None)
        self.assertFalse(self.db.is_dismissed(canonical))

    def test_dry_run_prunes_nothing(self):
        self.write_table([
            ('https://t.example/banished', '🗑️ banished — confirmed 2026-09-02'),
            ('https://p.example/waiting', ' - '),
        ])
        before = self.table_text()
        dryrun.enable()
        try:
            r = wp.prune_decommission_table(self.vault,
                                            log=lambda *a, **k: None)
        finally:
            dryrun.disable()
        self.assertEqual(self.table_text(), before)   # rehearsed only

    def test_refresh_prunes_terminal_rows(self):
        # the refresh (every batch's end) is where the prune rides:
        self.db.enqueue_retry('https://p.example/waiting', 'HTTP 403')
        self.write_table([
            ('https://t.example/old', '🪦 auto — dead'),
            ('https://t.example/banished', '🗑️ banished — confirmed 2026-09-02'),
        ])
        wp.refresh_master_table(self.db, self.vault,
                                log=lambda *a, **k: None)
        text = self.table_text()
        self.assertNotIn('t.example/old', text)
        self.assertNotIn('t.example/banished', text)
        self.assertIn('https://p.example/waiting', text)


class TestReleaseBookkeeping(unittest.TestCase):
    """The house source-contract tests (the test_bothdoors pattern)."""

    def setUp(self):
        self.root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def _read(self, *parts):
        with open(os.path.join(self.root, *parts), 'r',
                  encoding='utf-8') as f:
            return f.read()

    def test_version_is_0440(self):
        self.assertEqual(self._read('VERSION').strip(), '0.61.0')

    def test_changelog_has_the_beat(self):
        text = self._read('CHANGELOG.md')
        self.assertIn('## [0.44.0]', text)
        self.assertIn('graveyard', text.lower())

    def test_ci_and_agents_know_the_module(self):
        ci = self._read('.github', 'workflows', 'ci.yml')
        self.assertIn('tests.test_decommission', ci)
        agents = self._read('AGENTS.md')
        self.assertIn('tests.test_decommission', agents)


if __name__ == '__main__':
    unittest.main()
