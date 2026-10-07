"""tests/test_reviewretry.py — v0.42.0, the _review backlog retry.

The owner's ask: "The app must have an option to scan the _review folder
on vaults and retry them — currently over 100 links lie there because of
error 403 or 405. System checks _review, gives the user a notice, do you
want to retry this again? If yes the app fetches and stores them
correctly."

Covered here (zero network — an injected fetch_fn and a fake LLM, the
house pattern):

* scan_review_backlog — only APP-OWNED fetch-failed placeholders qualify
  (hand-written notes and non-failed review notes are human territory);
  missing vault/_review folder; legacy _v1/_v2 stacking all returned.
* retry_review_backlog —
  - THE WALL: a link whose 3 automatic retries burned out (attempts=3)
    is skipped by process_link forever — the driver re-arms the queue,
    re-fetches under the (injected) new fetcher, stores the real note
    and removes the placeholder.
  - the state row was LOST: the driver re-registers it from the disk
    truth so the link upgrades in ONE run (not "already in the vault").
  - re-failure: the placeholder is refreshed in place, the retry queue
    gets a fresh set of 3 attempts, nothing is dropped.
  - legacy stacking: one source, two placeholder files — after the
    verdict ONE real note exists and BOTH placeholders are gone.
  - a real note already exists: the stale placeholder is cleaned, the
    real note untouched, the link skipped politely.
  - dismissed links: never re-added, placeholders untouched (the
    dismissal contract).
  - dry-run: the placeholder survives (every mutation is gated).

No PyQt import at module level (the libEGL-less sandbox rule) — the GUI
wiring (startup notice, More menu, worker mode) is exercised by the
compile gate + CI's headless Qt suite.
"""

import json
import os
import shutil
import tempfile
import unittest

from gitcurator.core import dryrun
from gitcurator.core import website_pipeline as wp
from gitcurator.core import web_fetch as _web_fetch


# -- the fakes (the test_phase2 pattern, kept local so this module never
#    imports anything Qt-touching) -----------------------------------------

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


class _BacklogCase(unittest.TestCase):
    """Shared plumbing: temp vault + state db + pipeline factory."""

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix='rvretry-')
        self.vault = os.path.join(self.tmp, 'websites')
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
        """Hand-write a _review note the way build_review_note does (plus
        knobs for the scan's negative cases)."""
        review_dir = os.path.join(self.vault, '_review')
        os.makedirs(review_dir, exist_ok=True)
        path = os.path.join(review_dir, name)
        note = wp.build_review_note(url, fetch_status,
                                    'Fetch failed: HTTP 403 — bot defense')
        if not managed:
            note = note.replace(
                'managed_by: "%s"' % wp.MANAGED_BY_GITCURATOR,
                'managed_by: "a-human"')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(note)
        return path

    def all_logs(self):
        return '\n'.join(m for (_, m) in self.logs)


class TestScanReviewBacklog(_BacklogCase):

    def test_app_owned_failed_placeholder_qualifies(self):
        p = self.write_placeholder('https://example.com/walled', 'a.md')
        items = wp.scan_review_backlog(self.vault)
        self.assertEqual(items, [{'url': 'https://example.com/walled',
                                  'path': p}])

    def test_hand_written_note_is_human_territory(self):
        self.write_placeholder('https://example.com/mine', 'a.md',
                               managed=False)
        self.assertEqual(wp.scan_review_backlog(self.vault), [])

    def test_non_failed_review_note_needs_human_eyes(self):
        self.write_placeholder('https://example.com/unsure', 'a.md',
                               fetch_status='partial')
        self.assertEqual(wp.scan_review_backlog(self.vault), [])

    def test_missing_vault_and_folder(self):
        self.assertEqual(wp.scan_review_backlog(''), [])
        self.assertEqual(
            wp.scan_review_backlog(os.path.join(self.tmp, 'nope')), [])
        os.makedirs(self.vault, exist_ok=True)  # vault but no _review
        self.assertEqual(wp.scan_review_backlog(self.vault), [])

    def test_legacy_stacking_all_returned_sorted(self):
        self.write_placeholder('https://example.com/walled', 'b.md')
        self.write_placeholder('https://example.com/walled', 'a.md')
        items = wp.scan_review_backlog(self.vault)
        self.assertEqual([os.path.basename(i['path']) for i in items],
                         ['a.md', 'b.md'])
        self.assertEqual(len({i['url'] for i in items}), 1)

    def test_scan_finds_the_pipeline_written_placeholder(self):
        # integration: a REAL failing process_link writes the placeholder
        # the scan must find.
        pipe = self.make_pipeline(
            fetch=_FakeFetch(fail_paths=['/walled']))
        r = pipe.process_link('https://example.com/walled')
        self.assertEqual(r['outcome'], 'review')
        items = wp.scan_review_backlog(self.vault)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['url'],
                         'https://example.com/walled')


class TestRetryReviewBacklog(_BacklogCase):

    def _wall_in(self, url, path='/walled'):
        """Process a failing link, then exhaust its 3 retries: the exact
        state of the owner's 100+ link pile."""
        pipe = self.make_pipeline(fetch=_FakeFetch(fail_paths=[path]))
        r = pipe.process_link(url)
        self.assertEqual(r['outcome'], 'review')
        canonical = r['canonical']
        for _ in range(2):  # 1 attempt came with the failure; +2 = 3
            self.db.enqueue_retry(canonical, 'HTTP 403')
        self.assertEqual(self.db.retry_row(canonical)['attempts'], 3)
        return canonical, r['note_path']

    def test_the_wall_comes_down(self):
        url = 'https://example.com/walled'
        canonical, placeholder = self._wall_in(url)
        self.in_vault.add(canonical)  # the VaultIndex sees _review notes
        # THE WALL, proven: the normal path skips this link forever.
        freed = self.make_pipeline()  # fetch succeeds now
        r = freed.process_link(url)
        self.assertEqual(r['outcome'], 'skipped')
        self.assertIn('no more retries', r['error'])
        # The driver: re-arm + full pipeline + placeholder replaced.
        items = wp.scan_review_backlog(self.vault)
        results = freed.retry_review_backlog(items)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['outcome'], 'processed')
        note = results[0]['note_path']
        self.assertIn('fetch_status: "full"',
                      open(note, encoding='utf-8').read())
        rel = os.path.relpath(note, self.vault)
        self.assertTrue(rel.startswith(os.path.join('Design', '')))
        self.assertFalse(os.path.exists(placeholder))
        self.assertIsNone(self.db.retry_row(canonical))
        self.assertIn('re-armed', self.all_logs())
        self.assertEqual(freed.counters['upgraded'], 1)
        self.assertEqual(freed.counters['retried'], 1)

    def test_state_row_lost_upgrades_in_one_run(self):
        # A placeholder exists on disk; cache.db was rebuilt (no row).
        url = 'https://example.com/lost'
        placeholder = self.write_placeholder(url, 'a.md')
        canonical = wp.normalize_website_url(url)
        self.in_vault.add(canonical)
        pipe = self.make_pipeline()
        items = wp.scan_review_backlog(self.vault)
        results = pipe.retry_review_backlog(items)
        self.assertEqual(results[0]['outcome'], 'processed')
        # ONE run: real note written, placeholder gone, row now real.
        self.assertFalse(os.path.exists(placeholder))
        row = self.db.processed_row(canonical)
        self.assertEqual(row['fetch_status'], 'full')
        self.assertIn('state row was lost', self.all_logs())

    def test_refailure_refreshes_and_requeues(self):
        url = 'https://example.com/stuck'
        canonical, placeholder = self._wall_in(url, path='/stuck')
        self.in_vault.add(canonical)
        # The wall is still up for THIS link: the retry fails again.
        pipe = self.make_pipeline(fetch=_FakeFetch(fail_paths=['/stuck']))
        items = wp.scan_review_backlog(self.vault)
        results = pipe.retry_review_backlog(items)
        self.assertEqual(results[0]['outcome'], 'review')
        # Nothing dropped: exactly ONE placeholder still waits for this
        # source (with note_state wired, production refreshes it in place;
        # without it, the old name is replaced by a fresh one — either
        # way the never-stack-duplicates invariant holds), and the queue
        # has a FRESH set of attempts (re-arm + this one failure = 1).
        fresh = results[0]['note_path']
        self.assertTrue(os.path.exists(fresh))
        body = open(fresh, encoding='utf-8').read()
        self.assertIn('fetch_status: "failed"', body)
        self.assertIn('Needs review', body)
        review_files = [n for n in os.listdir(
            os.path.join(self.vault, '_review'))
            if n.endswith('.md') and n != wp.DECOMMISSION_TABLE]
        self.assertEqual(len(review_files), 1)
        # v0.47.0 — the refailure also leaves the MASTER TABLE behind
        # (the owner finally gets it): the failed link waits there as a
        # row too, with the last error in its Notes column.
        table = wp.decommission_table_path(self.vault)
        self.assertTrue(os.path.exists(table))
        self.assertIn('https://example.com/stuck',
                      open(table, encoding='utf-8').read())
        row = self.db.retry_row(canonical)
        self.assertEqual(row['attempts'], 1)

    def test_legacy_stacking_one_note_one_source(self):
        url = 'https://example.com/stacked'
        p1 = self.write_placeholder(url, 'a.md')
        p2 = self.write_placeholder(url, 'b.md')
        self.in_vault.add(wp.normalize_website_url(url))
        pipe = self.make_pipeline()
        items = wp.scan_review_backlog(self.vault)
        self.assertEqual(len(items), 2)
        results = pipe.retry_review_backlog(items)
        self.assertEqual(len(results), 1)  # ONE verdict for ONE source
        self.assertEqual(results[0]['outcome'], 'processed')
        self.assertFalse(os.path.exists(p1))
        self.assertFalse(os.path.exists(p2))
        note = results[0]['note_path']
        self.assertIn('fetch_status: "full"',
                      open(note, encoding='utf-8').read())

    def test_real_note_exists_stale_placeholder_cleaned(self):
        # The owner hand-upgraded the link: a real note in the vault +
        # a state row that is no longer 'failed' + a leftover _review
        # placeholder. The retry skips politely; the stale file goes.
        url = 'https://example.com/done'
        canonical = wp.normalize_website_url(url)
        real = os.path.join(self.vault, 'Design', 'Test_Site.md')
        os.makedirs(os.path.dirname(real), exist_ok=True)
        with open(real, 'w', encoding='utf-8') as f:
            f.write('owner note')
        self.db.mark_processed(canonical, real, 'Design', '', 'full')
        self.in_vault.add(canonical)
        stale = self.write_placeholder(url, 'a.md')
        pipe = self.make_pipeline()
        items = wp.scan_review_backlog(self.vault)
        results = pipe.retry_review_backlog(items)
        self.assertEqual(results[0]['outcome'], 'skipped')
        self.assertIn('already in', results[0]['error'])
        self.assertFalse(os.path.exists(stale))
        self.assertTrue(os.path.exists(real))  # untouched
        with open(real, encoding='utf-8') as f:
            self.assertEqual(f.read(), 'owner note')

    def test_dismissed_link_untouched(self):
        # The dismissal contract: the owner deleted this link's note —
        # never re-add, never touch what still lies around.
        url = 'https://example.com/gone'
        canonical = wp.normalize_website_url(url)
        self.db.dismiss(canonical, 'owner deleted the note')
        placeholder = self.write_placeholder(url, 'a.md')
        pipe = self.make_pipeline()
        items = wp.scan_review_backlog(self.vault)
        results = pipe.retry_review_backlog(items)
        self.assertEqual(results[0]['outcome'], 'skipped')
        self.assertIn('dismissed', results[0]['error'])
        self.assertTrue(os.path.exists(placeholder))

    def test_dry_run_mutates_nothing(self):
        url = 'https://example.com/rehearse'
        placeholder = self.write_placeholder(url, 'a.md')
        self.in_vault.add(wp.normalize_website_url(url))
        pipe = self.make_pipeline()
        items = wp.scan_review_backlog(self.vault)
        dryrun.enable()
        try:
            results = pipe.retry_review_backlog(items)
        finally:
            dryrun.disable()
        # The verdict ran, but every mutation was rehearsed only:
        self.assertEqual(len(results), 1)
        self.assertTrue(os.path.exists(placeholder))
        self.assertEqual(
            len([n for n in os.listdir(os.path.join(self.vault, '_review'))
                 if n.endswith('.md')]), 1)

    def test_should_continue_stops_cleanly(self):
        urls = ['https://example.com/one', 'https://example.com/two']
        for i, u in enumerate(urls):
            self.write_placeholder(u, '%d.md' % i)
            self.in_vault.add(wp.normalize_website_url(u))
        pipe = self.make_pipeline()
        items = wp.scan_review_backlog(self.vault)
        results = pipe.retry_review_backlog(
            items, should_continue=lambda: False)
        self.assertEqual(results, [])
        self.assertIn('stopped by user', self.all_logs())
        # Both placeholders keep waiting.
        self.assertEqual(len(wp.scan_review_backlog(self.vault)), 2)

    def test_on_progress_fires_once_per_unique_url(self):
        url = 'https://example.com/dup'
        self.write_placeholder(url, 'a.md')
        self.write_placeholder(url, 'b.md')
        self.in_vault.add(wp.normalize_website_url(url))
        pipe = self.make_pipeline()
        items = wp.scan_review_backlog(self.vault)
        seen = []
        pipe.retry_review_backlog(items, on_progress=seen.append)
        self.assertEqual(seen, [url])


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
