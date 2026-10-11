"""tests/test_reviewtable.py — v0.49.0, the table that lists them all.

The owner's report (session): "When app supposed to add tables so i can
set emojies for removed, dead etc or to decomission it? it currently
only adds links like before in _review." — v0.47.0's refresh listed the
fetch-failed pile (the retry queue) and the auto-verdict retirements,
but the OTHER _review populations — low classification confidence,
analysis failures, archived rescues, the classes that wait for HUMAN
eyes — landed as notes and never as rows: no Status cell, no emoji to
set, no retirement door. v0.49.0 makes the master table the ledger for
the WHOLE folder.

Covered here:

* ``scan_review_notes`` — every app-owned note in _review (ANY
  fetch_status) with its status carried; a hand-written note (no
  managed_by) and the table itself are never our call; a non-http
  source is skipped.
* ``_review_note_reason`` — the reason line out of the warning callout
  (exactly where build_review_note writes it); tolerant: no callout,
  a callout without a reason line, an unreadable file all return ''.
* the refresh's third population — a low-confidence note (status
  full) and an archived rescue (status partial) become 'unreviewed'
  rows whose Notes column carries "in _review (<status>): <reason>";
  the failed placeholder still comes from the backlog path (no double
  row); idempotent; owner-set Status cells never clobbered; a retired
  link's note is not waiting; nothing pending → no table.
* the consume sweep, widened — ✅ reviewed on a low-confidence row
  retires the link AND sweeps its placeholder; 🪦 dead on an
  archived-rescue row does the same; a hand-written note in _review
  is NEVER swept; ♻️ revived on a review row is a tolerated no-op.
* the full circle — a pipeline run whose classification confidence is
  low leaves the note AND the row behind: run() refreshes, the owner
  sets ✅ by hand, the next consume sweeps the note and dismisses the
  link.
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


# -- the fakes (the test_mastertable pattern, kept local) ---------------

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


class _LowConfidenceLLM:
    """The w01 answer that parks a link in _review: category fine,
    confidence LOW — the human-eye class v0.49.0 finally tables."""

    def __call__(self, messages, task=None):
        text = messages[0]['content']
        if 'filing a website into a personal library' in text:
            return json.dumps({'category': 'Design',
                               'confidence': 'low', 'reason': 'testing'})
        if 'was filed under' in text:
            return json.dumps({'subcategory': 'Assets & Resources',
                               'confidence': 'high'})
        return json.dumps({
            'name': 'Test Site', 'one_line': 'A test page about design.',
            'core_offerings': ['One', 'Two'],
            'best_used_for': 'Use when you need to test the pipeline.',
            'pricing': 'free', 'login_required': 'no',
            'similar_tools': [], 'tags': ['design'],
            'confidence': 'high'})


class _ReviewCase(unittest.TestCase):
    """Shared plumbing: temp vault + state db."""

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix='reviewtable-')
        self.vault = os.path.join(self.tmp, 'websites')
        os.makedirs(os.path.join(self.vault, '_review'), exist_ok=True)
        self.db = wp.WebsiteStateDB(
            db_path=os.path.join(self.tmp, 'cache.db'))
        self.in_vault = set()
        self.logs = []

    def tearDown(self):
        dryrun.disable()
        dryrun.clear()
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_review_note(self, url, name, fetch_status='full',
                           reason='classification confidence was low'):
        """An app-owned _review note — exactly what the pipeline's
        low-confidence / analysis-failure / archived paths write."""
        review_dir = os.path.join(self.vault, '_review')
        os.makedirs(review_dir, exist_ok=True)
        path = os.path.join(review_dir, name)
        note = wp.build_review_note(url, fetch_status, reason)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(note)
        return path

    def write_failed_placeholder(self, url, name):
        return self.write_review_note(
            url, name, fetch_status='failed',
            reason='Fetch failed: HTTP 404 — not found')

    def write_hand_note(self, url, name):
        """A note a human wrote — no managed_by, never our call."""
        path = os.path.join(self.vault, '_review', name)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(f"---\nsource: {url}\nfetch_status: full\n---\n\n"
                    f"# my own note\n")
        return path

    def table_path(self):
        return wp.decommission_table_path(self.vault)

    def table_text(self):
        with open(self.table_path(), encoding='utf-8') as f:
            return f.read()

    def set_status(self, url, status):
        """The owner's gesture: edit a row's Status cell by hand."""
        path = self.table_path()
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.read().splitlines()
        out = []
        for line in lines:
            if url in line and line.startswith('| ') \
                    and 'unreviewed' in line:
                parts = line.split('|')
                if len(parts) >= 8:
                    parts[6] = f" {status} "
                    line = '|'.join(parts)
            out.append(line)
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(out) + '\n')


class TestScanReviewNotes(_ReviewCase):

    def test_every_app_owned_note_is_scanned_with_its_status(self):
        self.write_review_note('https://a.example.net/1', 'a.md',
                                fetch_status='full')
        self.write_review_note('https://b.example.net/2', 'b.md',
                                fetch_status='partial',
                                reason='an archived copy of a dead page')
        self.write_failed_placeholder('https://c.example.net/3', 'c.md')
        items = wp.scan_review_notes(self.vault)
        by_url = {i['url']: i for i in items}
        self.assertEqual(len(items), 3)
        self.assertEqual(by_url['https://a.example.net/1']['fetch_status'],
                         'full')
        self.assertEqual(by_url['https://b.example.net/2']['fetch_status'],
                         'partial')
        self.assertEqual(by_url['https://c.example.net/3']['fetch_status'],
                         'failed')

    def test_hand_notes_and_the_table_itself_are_never_ours(self):
        self.write_hand_note('https://human.example.net/x', 'human.md')
        wp.write_decommission_candidates(
            self.vault, ['https://t.example.net/y'])
        items = wp.scan_review_notes(self.vault)
        urls = {i['url'] for i in items}
        self.assertNotIn('https://human.example.net/x', urls)
        self.assertNotIn('https://t.example.net/y', urls)
        self.assertEqual(items, [])

    def test_non_http_source_is_skipped(self):
        path = os.path.join(self.vault, '_review', 'odd.md')
        with open(path, 'w', encoding='utf-8') as f:
            f.write("---\nsource: not-a-url\nmanaged_by: gitcurator\n"
                    "fetch_status: full\n---\n")
        self.assertEqual(wp.scan_review_notes(self.vault), [])

    def test_missing_folder_is_an_empty_scan(self):
        self.assertEqual(
            wp.scan_review_notes(os.path.join(self.tmp, 'nope')), [])


class TestReviewNoteReason(_ReviewCase):

    def test_reads_the_reason_line_from_the_callout(self):
        path = self.write_review_note(
            'https://a.example.net/1', 'a.md', fetch_status='full',
            reason='classification confidence was low')
        self.assertEqual(wp._review_note_reason(path),
                         'classification confidence was low')

    def test_a_missing_callout_returns_empty(self):
        path = os.path.join(self.vault, '_review', 'plain.md')
        with open(path, 'w', encoding='utf-8') as f:
            f.write("---\nsource: https://a.example.net/1\n---\n\n"
                    "no callout here\n")
        self.assertEqual(wp._review_note_reason(path), '')

    def test_an_unreadable_file_returns_empty(self):
        self.assertEqual(
            wp._review_note_reason(os.path.join(self.tmp, 'gone.md')), '')


class TestRefreshReviewRows(_ReviewCase):

    def test_low_confidence_note_becomes_a_row_with_its_reason(self):
        self.write_review_note('https://low.example.net/1', 'low.md',
                               fetch_status='full')
        report = wp.refresh_master_table(self.db, self.vault)
        self.assertEqual(report['waiting'], 1)
        self.assertEqual(report['written'], 1)
        text = self.table_text()
        self.assertIn('https://low.example.net/1', text)
        self.assertIn('in _review (full)', text)
        self.assertIn('classification confidence was low', text)
        self.assertIn('unreviewed', text)
        # the row reads as unretired to every consumer of the table:
        rows = wp.scan_decommission_table(self.vault)
        self.assertFalse(wp._status_is_dead(
            rows['https://low.example.net/1']))

    def test_archived_rescue_note_becomes_a_row(self):
        self.write_review_note('https://arch.example.net/2', 'arch.md',
                               fetch_status='partial',
                               reason='an archived copy of a dead page')
        report = wp.refresh_master_table(self.db, self.vault)
        self.assertEqual(report['waiting'], 1)
        text = self.table_text()
        self.assertIn('https://arch.example.net/2', text)
        self.assertIn('in _review (partial)', text)
        self.assertIn('an archived copy of a dead page', text)

    def test_failed_placeholder_still_comes_from_the_backlog_path(self):
        # one failed placeholder whose state row was lost: the backlog
        # scan owns it (its note says 'state row was lost'), the new
        # population does NOT double it.
        self.write_failed_placeholder('https://f.example.net/3', 'f.md')
        report = wp.refresh_master_table(self.db, self.vault)
        self.assertEqual(report['waiting'], 1)
        self.assertEqual(report['written'], 1)
        self.assertIn('state row was lost', self.table_text())
        self.assertEqual(self.table_text().count('https://f.example.net/3'),
                         1)

    def test_retry_queue_and_review_note_together_one_row_each(self):
        self.db.enqueue_retry('https://r.example.net/4', 'HTTP 403')
        self.write_review_note('https://other.example.net/5', 'other.md',
                               fetch_status='partial')
        report = wp.refresh_master_table(self.db, self.vault)
        self.assertEqual(report['waiting'], 2)
        self.assertEqual(report['written'], 2)
        text = self.table_text()
        self.assertIn('HTTP 403', text)
        self.assertIn('in _review (partial)', text)

    def test_refresh_is_idempotent(self):
        self.write_review_note('https://i.example.net/6', 'i.md')
        wp.refresh_master_table(self.db, self.vault)
        report = wp.refresh_master_table(self.db, self.vault)
        self.assertEqual(report['written'], 0)
        self.assertEqual(report['waiting'], 1)

    def test_owner_set_status_is_never_clobbered(self):
        self.write_review_note('https://o.example.net/7', 'o.md')
        wp.refresh_master_table(self.db, self.vault)
        self.set_status('https://o.example.net/7', '✅ reviewed')
        report = wp.refresh_master_table(self.db, self.vault)
        self.assertEqual(report['written'], 0)
        self.assertIn('✅ reviewed', self.table_text())

    def test_a_retired_links_note_is_not_waiting(self):
        url = 'https://x.example.net/8'
        self.write_review_note(url, 'x.md', fetch_status='partial')
        self.db.dismiss(wp.normalize_website_url(url),
                        'auto-verdict: refused — the site refused us')
        report = wp.refresh_master_table(self.db, self.vault)
        self.assertEqual(report['waiting'], 0)
        # but its auto-verdict row exists:
        self.assertEqual(report['retired'], 1)

    def test_hand_notes_are_never_refreshed_into_rows(self):
        self.write_hand_note('https://human.example.net/x', 'human.md')
        report = wp.refresh_master_table(self.db, self.vault)
        self.assertEqual(report, {'waiting': 0, 'retired': 0, 'written': 0})
        self.assertFalse(os.path.exists(self.table_path()))

    def test_nothing_pending_no_table(self):
        report = wp.refresh_master_table(self.db, self.vault)
        self.assertEqual(report, {'waiting': 0, 'retired': 0, 'written': 0})
        self.assertFalse(os.path.exists(self.table_path()))


class TestConsumeReviewRows(_ReviewCase):

    def test_reviewed_row_sweeps_a_low_confidence_placeholder(self):
        url = 'https://rev.example.net/1'
        canonical = wp.normalize_website_url(url)
        note = self.write_review_note(url, 'rev.md', fetch_status='full')
        self.db.mark_processed(canonical, note, '', '', 'full')
        wp.refresh_master_table(self.db, self.vault)
        self.set_status(url, '✅ reviewed')
        report = wp.consume_decommission_table(self.db, self.vault)
        self.assertEqual(report['reviewed'], 1)
        self.assertTrue(self.db.is_dismissed(canonical))
        self.assertIn('reviewed',
                      self.db.dismissed_row(canonical)['reason'])
        # THE fix: the note is swept (the row is the record):
        self.assertFalse(os.path.exists(note))
        self.assertIn('✅ confirmed — reviewed', self.table_text())

    def test_dead_row_sweeps_an_archived_rescue_placeholder(self):
        url = 'https://dead.example.net/2'
        canonical = wp.normalize_website_url(url)
        note = self.write_review_note(
            url, 'dead.md', fetch_status='partial',
            reason='an archived copy of a dead page')
        self.db.mark_processed(canonical, note, '', '', 'partial')
        wp.refresh_master_table(self.db, self.vault)
        self.set_status(url, '🪦 dead')
        report = wp.consume_decommission_table(self.db, self.vault)
        self.assertEqual(report['dead'], 1)
        self.assertTrue(self.db.is_dismissed(canonical))
        self.assertFalse(os.path.exists(note))
        self.assertIn('🪦 confirmed — decommissioned', self.table_text())

    def test_a_hand_note_for_the_same_url_is_kept(self):
        url = 'https://mixed.example.net/3'
        note = self.write_review_note(url, 'app.md', fetch_status='full')
        hand = self.write_hand_note(url, 'hand.md')
        wp.refresh_master_table(self.db, self.vault)
        self.set_status(url, '✅ reviewed')
        wp.consume_decommission_table(self.db, self.vault)
        self.assertFalse(os.path.exists(note))       # app-owned: swept
        self.assertTrue(os.path.exists(hand))        # a human's: kept

    def test_failed_placeholder_sweep_still_works(self):
        # the pre-v0.49 behavior, unchanged (a regression guard):
        url = 'https://old.example.net/4'
        canonical = wp.normalize_website_url(url)
        note = self.write_failed_placeholder(url, 'old.md')
        self.db.mark_processed(canonical, note, '', '', 'failed')
        self.db.enqueue_retry(canonical, 'HTTP 403')
        wp.write_decommission_candidates(self.vault, [url])
        self.set_status(url, '🪦 dead')
        report = wp.consume_decommission_table(self.db, self.vault)
        self.assertEqual(report['dead'], 1)
        self.assertFalse(os.path.exists(note))

    def test_revived_on_a_review_row_is_a_tolerated_no_op(self):
        url = 'https://rev2.example.net/5'
        self.write_review_note(url, 'rev2.md', fetch_status='full')
        wp.refresh_master_table(self.db, self.vault)
        self.set_status(url, '♻️ revived')
        report = wp.consume_decommission_table(self.db, self.vault)
        self.assertEqual(report['dead'], 0)
        self.assertEqual(report['reviewed'], 0)
        self.assertEqual(report['revived'], 1)  # counted, dismissed never


class TestPipelineFullCircle(_ReviewCase):

    def make_pipeline(self, llm=None, fetch=None):
        cfg = {'website_vault_path': self.vault,
               'web_domain_delay_s': 0}
        return wp.WebsitePipeline(
            config=cfg, llm_call=llm or _LowConfidenceLLM(),
            vault_index_has=lambda u: u in self.in_vault,
            state=self.db, fetch_fn=fetch or _FakeFetch(),
            log=lambda m, l='info': self.logs.append((l, m)))

    def test_a_low_confidence_run_leaves_note_and_row_behind(self):
        # THE owner report, closed: the run parks the link in _review
        # "like before" — but now the table row exists in the same
        # breath, with the reason where the owner reads it.
        url = 'https://run.example.net/1'
        results = self.make_pipeline().run([url])
        self.assertEqual(results[0]['outcome'], 'review')
        self.assertIn('low', results[0]['error'])
        text = self.table_text()
        self.assertIn('https://run.example.net/1', text)
        self.assertIn('in _review (full)', text)
        self.assertIn('classification confidence was low', text)

    def test_the_owners_tick_closes_the_circle(self):
        url = 'https://run.example.net/2'
        canonical = wp.normalize_website_url(url)
        pipeline = self.make_pipeline()
        results = pipeline.run([url])
        note = results[0]['note_path']
        self.assertTrue(os.path.exists(note))
        self.set_status(url, '✅ reviewed')
        report = wp.consume_decommission_table(self.db, self.vault)
        self.assertEqual(report['reviewed'], 1)
        self.assertFalse(os.path.exists(note))
        self.assertTrue(self.db.is_dismissed(canonical))
        # and the NEXT run never fetches it again:
        pipeline2 = self.make_pipeline()
        results2 = pipeline2.run([url])
        self.assertEqual(results2[0]['outcome'], 'skipped')
        self.assertIn('dismissed', results2[0]['error'].lower())
        fetch = _FakeFetch()
        wp.WebsitePipeline(
            config={'website_vault_path': self.vault,
                    'web_domain_delay_s': 0},
            llm_call=_LowConfidenceLLM(),
            vault_index_has=lambda u: u in self.in_vault,
            state=self.db, fetch_fn=fetch,
            log=lambda m, l='info': self.logs.append((l, m))).run([url])
        self.assertNotIn(url, fetch.calls)


class TestReleaseBookkeeping(unittest.TestCase):
    """v0.49.0 ships as a real release: the VERSION pin, the CHANGELOG
    beat, the CI registration, the AGENTS.md listing."""

    def setUp(self):
        self.root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def _read(self, *parts):
        with open(os.path.join(self.root, *parts), 'r',
                  encoding='utf-8') as f:
            return f.read()

    def test_version_is_0490(self):
        self.assertEqual(self._read('VERSION').strip(), '0.66.0')

    def test_changelog_has_the_beat(self):
        text = self._read('CHANGELOG.md')
        self.assertIn('## [0.49.0]', text)
        self.assertIn('table that lists them all', text.lower())

    def test_ci_runs_this_module(self):
        text = self._read('.github', 'workflows', 'ci.yml')
        self.assertIn('tests.test_reviewtable', text)

    def test_agents_md_lists_this_module(self):
        text = self._read('AGENTS.md')
        self.assertIn('test_reviewtable', text)


if __name__ == '__main__':
    unittest.main()
