"""tests/test_mastertable.py — v0.47.0, the master note the owner was
promised.

The owner's report (session): "I also didn't get that table yet which
is supposed that i can decomission some links there, so they dont
fetch and retry again (for example dead sites). The review folder must
have a master note inside it, with tables, there you can decomission
links, mark as reviewed etc."

v0.44.0's table existed only when the manual picker had been used;
v0.47.0 makes ``<vault>/_review/DECOMMISSIONED.md`` THE master note,
refreshed after every batch: every waiting failure and every
auto-verdict retirement lives as a row, the owner's ✅ reviewed gesture
is a retirement door of its own, and retired links leave the startup
notice.

Covered here:

* the marker law — ✅/✔/☑/reviewed/kept/done read as REVIEWED;
  'unreviewed' NEVER does (it contains 'reviewed' but says the
  opposite); revived and dead statuses never do; dead beats reviewed
  when a hand-edited cell says both.
* the header language — the master note's header documents the whole
  emoji grammar (✅ / 🪦 / ♻️ / unreviewed / auto rows).
* the writer — ``notes`` fills the Notes column (pipe-safe: a note
  containing '|' cannot break the row; length-capped); ``status``
  pre-fills the Status cell (the auto rows carry '🪦 auto — <cat>').
* the ✅ consume pass — a reviewed row retires exactly like a burial
  (dismissed with the reviewed reason, retry dropped, placeholder
  swept, row stamped '✅ confirmed — reviewed'); idempotent; a dead
  cell that also says reviewed reads DEAD.
* the refresh — waiting rows from the whole retry queue (last error in
  Notes), lost-row placeholders, auto-verdict rows as '🪦 auto — dead';
  a dismissed waiting link is NOT waiting; nothing pending → no table
  (a cheap no-op); idempotent; owner-edited rows never clobbered.
* the pipeline hooks — run(), run_due_retries() and
  retry_review_backlog() all leave the table behind (THE owner
  complaint, closed); the 52x origin-down courtesy lives in
  test_ladder.
* the full circle — an auto-verdict 'dead' failure retires the link,
  the refresh writes its '🪦 auto — dead' row, the backlog scan (with
  the is_dismissed probe) stops showing it, the consume confirms the
  row and sweeps the placeholder: the owner SEES it, can ♻️ revive it,
  and it is never fetched again.
* release bookkeeping — VERSION, CHANGELOG, ci.yml, AGENTS.md.

No PyQt import at module level (the libEGL-less sandbox rule).
"""

import json
import os
import shutil
import tempfile
import unittest

from gitcurator.core import dryrun
from gitcurator.core import website_pipeline as wp
from gitcurator.core import web_fetch as _web_fetch


# -- the fakes (the test_decommission pattern, kept local) ---------------

class _FakeFetch:
    """Canned fetch results; whole-path failures simulate the wall."""

    def __init__(self, pages=None, fail_paths=(), fail_reason=None,
                 fail_category=''):
        self.pages = pages or {}
        self.fail_paths = set(fail_paths)
        self.fail_reason = fail_reason or 'HTTP 404 — not found'
        self.fail_category = fail_category
        self.calls = []

    def __call__(self, url, **kwargs):
        from urllib.parse import urlparse
        self.calls.append(url)
        path = urlparse(url).path or '/'
        if path in self.fail_paths:
            return _web_fetch.FetchResult(
                url=url, status='failed', reason=self.fail_reason,
                category=self.fail_category)
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


class _MasterCase(unittest.TestCase):
    """Shared plumbing: temp vault + state db + pipeline factory."""

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix='mastertable-')
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
                                    'Fetch failed: HTTP 404 — not found')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(note)
        return path

    def table_path(self):
        return wp.decommission_table_path(self.vault)

    def table_text(self):
        with open(self.table_path(), encoding='utf-8') as f:
            return f.read()

    def write_table_rows(self, rows):
        """v0.60.2 — hand-write data rows into the master table (the
        owner's own editing shape: URL + Status)."""
        from urllib.parse import urlparse
        lines = [wp._GRAVEYARD_HEADER.format(now='2026-10-09 12:00')]
        for url, status in rows:
            domain = urlparse(url).netloc or 'unknown'
            lines.append(f"| - | 2026-10-09 | {url} | {domain} "
                         f"| test | {status} | |")
        with open(self.table_path(), 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        return self.table_path()


class TestMarkerLaw(unittest.TestCase):

    def test_reviewed_markers(self):
        for s in ('✅', '✅ reviewed', '✔ done', '☑ kept', 'reviewed',
                  'Reviewed 2026', 'kept', 'done'):
            self.assertTrue(wp._status_is_reviewed(s), s)

    def test_unreviewed_never_counts(self):
        # the pre-filled default CONTAINS 'reviewed' but says the
        # opposite — the owner's own table ships full of these:
        for s in ('', 'unreviewed', 'UNREVIEWED', 'still unreviewed',
                  'later', 'maybe'):
            self.assertFalse(wp._status_is_reviewed(s), s)

    def test_revived_and_dead_never_count_as_reviewed(self):
        for s in ('♻️ revived', 'revived', '🪦 dead', 'dead',
                  'decommissioned'):
            self.assertFalse(wp._status_is_reviewed(s), s)

    def test_dead_beats_reviewed_when_a_cell_says_both(self):
        self.assertTrue(wp._status_is_dead('✅ dead'))
        # the consume's own precedence: dead is checked first
        status = '✅ dead'
        dead = wp._status_is_dead(status)
        reviewed = (not dead) and wp._status_is_reviewed(status)
        self.assertTrue(dead)
        self.assertFalse(reviewed)


class TestHeaderLanguage(_MasterCase):

    def test_the_master_note_header_documents_the_grammar(self):
        wp.write_decommission_candidates(self.vault,
                                         ['https://example.com/x'])
        text = self.table_text()
        self.assertIn('# Review Master Table', text)
        self.assertIn('✅ reviewed', text)
        self.assertIn('🪦 dead', text)
        self.assertIn('♻️ revived', text)
        self.assertIn('unreviewed', text)
        self.assertIn('auto', text)


class TestWriter(_MasterCase):

    def test_notes_fill_the_notes_column_pipe_safe(self):
        wp.write_decommission_candidates(
            self.vault, ['https://example.com/x'],
            notes={'https://example.com/x':
                   'proxy: HTTP 403 | direct: HTTP 523 — Cloudflare'})
        text = self.table_text()
        self.assertIn('proxy: HTTP 403', text)
        # the pipe in the note became '·' — the row stays one row:
        self.assertNotIn(' | direct: HTTP 523 — Cloudflare |',
                         text.split('example.com | ')[1])
        for line in text.splitlines():
            if 'example.com/x' in line and line.startswith('| '):
                self.assertEqual(len(line.split('|')), 9)

    def test_long_notes_are_capped(self):
        wp.write_decommission_candidates(
            self.vault, ['https://example.com/x'],
            notes={'https://example.com/x': 'x' * 500})
        for line in self.table_text().splitlines():
            if 'example.com/x' in line and line.startswith('| '):
                self.assertLessEqual(len(line), 200)

    def test_status_prefills_the_auto_rows(self):
        wp.write_decommission_candidates(
            self.vault, ['https://example.com/dead-domain'],
            source='auto-verdict',
            notes={'https://example.com/dead-domain':
                   'dead — the domain itself is dead'},
            status='🪦 auto — dead')
        text = self.table_text()
        self.assertIn('🪦 auto — dead', text)
        self.assertIn('dead — the domain itself is dead', text)
        # and it READS as dead to every consumer of the table:
        rows = wp.scan_decommission_table(self.vault)
        self.assertTrue(wp._status_is_dead(
            rows['https://example.com/dead-domain']))


class TestConsumeReviewed(_MasterCase):

    def test_reviewed_row_retires_like_a_burial(self):
        url = 'https://example.com/stuck'
        canonical = wp.normalize_website_url(url)
        placeholder = self.write_placeholder(url, 'a.md')
        self.db.mark_processed(canonical, placeholder, '', '', 'failed')
        self.db.enqueue_retry(canonical, 'HTTP 403')
        wp.write_decommission_candidates(self.vault, [url])
        wp.mark_urls_dead_in_table(self.vault, [url], log=None) \
            if False else None
        # the owner sets the tick by hand:
        path = self.table_path()
        text = self.table_text().replace('unreviewed', '✅ reviewed')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
        report = wp.consume_decommission_table(self.db, self.vault)
        self.assertEqual(report['reviewed'], 1)
        self.assertEqual(report['dead'], 0)
        # retired: dismissed with the reviewed reason, queue dropped,
        # placeholder swept, processed row forgotten:
        self.assertTrue(self.db.is_dismissed(canonical))
        self.assertIn('reviewed', self.db.dismissed_row(canonical)['reason'])
        self.assertIsNone(self.db.retry_row(canonical))
        self.assertFalse(os.path.exists(placeholder))
        self.assertIsNone(self.db.processed_row(canonical))
        self.assertIn('✅ confirmed — reviewed', self.table_text())

    def test_reviewed_consume_is_idempotent(self):
        url = 'https://example.com/stuck'
        self.db.enqueue_retry(wp.normalize_website_url(url), 'HTTP 403')
        wp.write_decommission_candidates(self.vault, [url])
        path = self.table_path()
        with open(path, 'r', encoding='utf-8') as f:
            text = f.read().replace('unreviewed', '✅ reviewed')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
        wp.consume_decommission_table(self.db, self.vault)
        first = self.table_text()
        wp.consume_decommission_table(self.db, self.vault)
        self.assertEqual(first, self.table_text())

    def test_dead_cell_saying_reviewed_too_reads_dead(self):
        url = 'https://example.com/both'
        wp.write_decommission_candidates(self.vault, [url])
        path = self.table_path()
        with open(path, 'r', encoding='utf-8') as f:
            text = f.read().replace('unreviewed', '✅ dead')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
        report = wp.consume_decommission_table(self.db, self.vault)
        self.assertEqual(report['dead'], 1)
        self.assertEqual(report['reviewed'], 0)


class TestRefresh(_MasterCase):

    def test_waiting_rows_carry_the_last_error(self):
        self.db.enqueue_retry('https://a.example.net/1',
                              'proxy: HTTP 403 | direct: HTTP 403')
        report = wp.refresh_master_table(self.db, self.vault)
        self.assertEqual(report['waiting'], 1)
        self.assertEqual(report['written'], 1)
        text = self.table_text()
        self.assertIn('https://a.example.net/1', text)
        self.assertIn('proxy: HTTP 403', text)
        self.assertIn('unreviewed', text)

    def test_dismissed_links_are_not_waiting(self):
        self.db.enqueue_retry('https://b.example.net/2', 'HTTP 403')
        self.db.dismiss('https://b.example.net/2', 'auto-verdict: dead — x')
        report = wp.refresh_master_table(self.db, self.vault)
        self.assertEqual(report['waiting'], 0)
        # but it IS listed as retired:
        self.assertEqual(report['retired'], 1)

    def test_auto_verdicts_stay_out_of_the_compact_table(self):
        # v0.60.2 — the owner's compact-list law: auto-verdict rows
        # are no longer WRITTEN ("I don't need that old long table") —
        # the retired count is still spoken, the record lives in the
        # state DB, and nothing terminal ever lands in the table:
        self.db.dismiss('https://c.example.net/3',
                        'auto-verdict: dead — the domain itself is dead')
        report = wp.refresh_master_table(self.db, self.vault)
        self.assertEqual(report['retired'], 1)   # still spoken
        self.assertEqual(report['written'], 0)   # never written
        self.assertFalse(os.path.exists(self.table_path()))
        row = self.db.dismissed_row('https://c.example.net/3')
        self.assertIn('the domain itself is dead', row['reason'])
        # and a leftover auto row from the v0.47–v0.60 era still leaves
        # at the next refresh's prune:
        self.write_table_rows([
            ('https://old.example.net/9', '🪦 auto — dead')])
        wp.refresh_master_table(self.db, self.vault)
        self.assertNotIn('https://old.example.net/9', self.table_text())

    def test_lost_row_placeholder_is_waiting(self):
        self.write_placeholder('https://d.example.net/4', 'lost.md')
        report = wp.refresh_master_table(self.db, self.vault)
        self.assertEqual(report['waiting'], 1)
        self.assertIn('state row was lost', self.table_text())

    def test_nothing_pending_no_table_created(self):
        report = wp.refresh_master_table(self.db, self.vault)
        self.assertEqual(report, {'waiting': 0, 'retired': 0, 'written': 0})
        self.assertFalse(os.path.exists(self.table_path()))

    def test_refresh_is_idempotent_and_never_clobbers(self):
        self.db.enqueue_retry('https://e.example.net/5', 'HTTP 403')
        wp.refresh_master_table(self.db, self.vault)
        # the owner sets the emoji on the row:
        path = self.table_path()
        with open(path, 'r', encoding='utf-8') as f:
            text = f.read().replace('unreviewed', '✅ reviewed')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
        report = wp.refresh_master_table(self.db, self.vault)
        self.assertEqual(report['written'], 0)
        self.assertIn('✅ reviewed', self.table_text())


class TestPipelineHooks(_MasterCase):

    def test_run_leaves_the_master_table_behind(self):
        url = 'https://example.com/walled'
        canonical = wp.normalize_website_url(url)
        pipe = self.make_pipeline(fetch=_FakeFetch(
            fail_paths=['/walled'],
            fail_reason='proxy: HTTP 403 | direct: HTTP 403'))
        results = pipe.run([url])
        self.assertEqual(results[0]['outcome'], 'review')
        # THE owner complaint, closed: the table EXISTS after a batch.
        self.assertTrue(os.path.exists(self.table_path()))
        self.assertIn(url, self.table_text())
        self.assertIn('proxy: HTTP 403', self.table_text())
        self.assertIn('unreviewed', self.table_text())

    def test_run_due_retries_leaves_the_table_behind(self):
        url = 'https://example.com/due'
        canonical = wp.normalize_website_url(url)
        self.db.enqueue_retry(canonical, 'HTTP 403')
        # make the retry due NOW:
        from datetime import datetime, timedelta
        with self.db._lock:
            self.db.conn.execute(
                "UPDATE website_retry_queue SET next_attempt_at=?"
                " WHERE url=?", ((datetime.now() -
                                  timedelta(hours=1)).isoformat(
                    timespec='seconds'), canonical))
            self.db.conn.commit()
        pipe = self.make_pipeline(fetch=_FakeFetch(
            fail_paths=['/due'],
            fail_reason='proxy: HTTP 403 | direct: HTTP 403'))
        pipe.run_due_retries()
        self.assertTrue(os.path.exists(self.table_path()))
        self.assertIn(url, self.table_text())

    def test_retry_review_backlog_leaves_the_table_behind(self):
        url = 'https://example.com/lost'
        self.write_placeholder(url, 'a.md')
        self.in_vault.add(wp.normalize_website_url(url))
        pipe = self.make_pipeline(fetch=_FakeFetch(
            fail_paths=['/lost'],
            fail_reason='proxy: HTTP 403 | direct: HTTP 403'))
        items = wp.scan_review_backlog(self.vault)
        pipe.retry_review_backlog(items)
        self.assertTrue(os.path.exists(self.table_path()))
        self.assertIn(url, self.table_text())

    def test_the_scan_hides_retired_links_when_probed(self):
        url = 'https://example.com/retired'
        canonical = wp.normalize_website_url(url)
        self.write_placeholder(url, 'a.md')
        self.db.dismiss(canonical, 'auto-verdict: dead — the domain is gone')
        # without the probe (the hermetic pure read) it still shows:
        self.assertEqual(len(wp.scan_review_backlog(self.vault)), 1)
        # with the probe it leaves the backlog:
        items = wp.scan_review_backlog(
            self.vault, is_dismissed=self.db.is_dismissed)
        self.assertEqual(items, [])

    def test_a_broken_probe_never_hides_a_waiting_link(self):
        self.write_placeholder('https://example.com/waiting', 'a.md')

        def broken(url):
            raise RuntimeError('db locked')

        items = wp.scan_review_backlog(
            self.vault, is_dismissed=broken)
        self.assertEqual(len(items), 1)


class TestTheFullCircle(_MasterCase):
    """The owner's exact story, end to end (v0.60.2 — the compact
    edition): a dead-domain link fails, the ladder names it, the
    auto-verdict retires it, the refresh speaks the retirement but
    writes NO row (the state DB is the record), the notice stops
    showing it, the dismissal is the never-fetch gate — and the
    revive-by-URL row (the legend's instruction) brings it back."""

    def test_dead_domain_link_full_circle(self):
        url = 'https://gone.example.net/dead'
        canonical = wp.normalize_website_url(url)
        pipe = self.make_pipeline(fetch=_FakeFetch(
            fail_paths=['/dead'],
            fail_reason=('proxy: HTTP 403 — forbidden | direct: '
                         'connection: [Errno 11001] getaddrinfo failed | '
                         'dns: NXDOMAIN even via DNS-over-HTTPS — the '
                         'domain itself is dead'),
            fail_category='dead'))
        results = pipe.run([url])
        self.assertEqual(results[0]['outcome'], 'review')
        # 1. retired by the auto-verdict, retry row never scheduled:
        self.assertTrue(self.db.is_dismissed(canonical))
        self.assertIsNone(self.db.retry_row(canonical))
        # 2. v0.60.2 — the compact table: the retirement is SPOKEN
        # (the report counts it) but NO row is written; the record is
        # the state DB's dismissal row:
        self.assertFalse(os.path.exists(self.table_path()))
        row = self.db.dismissed_row(canonical)
        self.assertTrue(row['reason'].startswith('auto-verdict:'))
        # 3. the notice/backlog scan no longer shows it:
        items = wp.scan_review_backlog(
            self.vault, is_dismissed=self.db.is_dismissed)
        self.assertEqual(items, [])
        # 4. never fetched again — a future paste is skipped:
        pipe2 = self.make_pipeline(fetch=_FakeFetch(
            fail_paths=['/dead'], fail_reason='HTTP 404',
            fail_category='dead'))
        results = pipe2.run([url])
        self.assertEqual(results[0]['outcome'], 'skipped')
        self.assertIn('never fetched', results[0]['error'])
        # 5. ...until the revive-by-URL row (the legend's instruction —
        # the compact table's answer to a pruned verdict):
        self.write_table_rows([
            (url, '♻️ revived')])
        wp.consume_decommission_table(self.db, self.vault)
        self.assertFalse(self.db.is_dismissed(canonical))


class TestSourceContracts(unittest.TestCase):
    """The house source-contract tests."""

    def setUp(self):
        self.root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def _read(self, *parts):
        with open(os.path.join(self.root, *parts), 'r',
                  encoding='utf-8') as f:
            return f.read()

    def test_version_is_0470(self):
        self.assertEqual(self._read('VERSION').strip(), '0.63.1')

    def test_changelog_has_the_beat(self):
        text = self._read('CHANGELOG.md')
        self.assertIn('## [0.47.0]', text)
        self.assertIn('master table', text.lower())

    def test_ci_runs_this_module(self):
        text = self._read('.github', 'workflows', 'ci.yml')
        self.assertIn('tests.test_mastertable', text)

    def test_agents_md_lists_this_module(self):
        text = self._read('AGENTS.md')
        self.assertIn('test_mastertable', text)


if __name__ == '__main__':
    unittest.main()
