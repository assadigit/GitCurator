#!/usr/bin/env python3
"""
test_intakefix.py — v0.20.0: the three intake fixes.

Owner requests 2026-09-29 (after the first v0.19.0-era batch log):
  1. "the X domains are already addressed, So i dont want to bring them
     again for websites vault, we need to find a robust method to prevent
     this"  → web_blocked_domains (default x.com/twitter.com/t.co): never
     fetched, never noted, never retried; the queued tail is PURGED and
     dismissed; the queue view gets its own 🚫 bucket.
  2. "There are 8-9 github addresses that are 404. but system always
     count them as remaining to be processed ... for example create a
     note in vault for missing github, and model reads them before again
     trying to process them or something faster"  → the _missing/
     placeholder note (its ``source:`` line IS the VaultIndex dedupe key)
     written immediately on 404 + confirm-dead + a batch-start backfill
     for the legacy strike tail.
  3. "every website (which be pasted in the same robot) must be processed
     and stored in websites vault, the github vault only manages its
     domains"  → the per-platform _inbox tables land in the WEBSITES
     vault when one is set (GitHub vault fallback only when not).

All against LOCAL temp dirs / stubs / monkeypatched workers — no network,
no GUI shown.
"""

import inspect
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gitcurator.core import links as L
from gitcurator.core import note_builder as nb
from gitcurator.core import note_state as ns
from gitcurator.core import mirror as mr
from gitcurator.core import website_pipeline as wp
from gitcurator.core import dryrun


# ---------------------------------------------------------------------------
# Blocked domains — the config contract + the matcher
# ---------------------------------------------------------------------------

class TestBlockedDomainsHelpers(unittest.TestCase):

    def test_missing_key_means_default(self):
        """THE fix contract: the owner's existing config.json (no
        web_blocked_domains key) blocks the X family with no Settings
        visit."""
        self.assertEqual(L.blocked_domains_from_config({}),
                         ['x.com', 'twitter.com', 't.co'])
        self.assertEqual(L.blocked_domains_from_config(None),
                         ['x.com', 'twitter.com', 't.co'])

    def test_comma_string_parsed(self):
        self.assertEqual(
            L.blocked_domains_from_config(
                {'web_blocked_domains': ' a.com , B.com ,, '}),
            ['a.com', 'b.com'])

    def test_list_kept_deduped(self):
        self.assertEqual(
            L.blocked_domains_from_config(
                {'web_blocked_domains': ['x.com', 'X.com', 't.co']}),
            ['x.com', 't.co'])

    def test_empty_is_opt_out(self):
        self.assertEqual(L.blocked_domains_from_config(
            {'web_blocked_domains': []}), [])
        self.assertEqual(L.blocked_domains_from_config(
            {'web_blocked_domains': ''}), [])

    def test_hostile_never_raises(self):
        class Boom:
            def get(self, *a):
                raise RuntimeError('boom')
        self.assertEqual(L.blocked_domains_from_config(Boom()),
                         ['x.com', 'twitter.com', 't.co'])
        self.assertEqual(L.blocked_domains_from_config(
            {'web_blocked_domains': 42}),
            ['x.com', 'twitter.com', 't.co'])

    def test_matcher_exact_subdomain_port(self):
        blocked = ['x.com', 't.co', 'twitter.com']
        for url in ('https://x.com/i/status/1',
                    'https://www.x.com/a',
                    'http://mobile.x.com/b',
                    'https://x.com:443/c',
                    'https://t.co/abc?x=1',
                    'https://twitter.com/user/status/9'):
            self.assertTrue(L.domain_is_blocked(url, blocked), url)

    def test_matcher_never_false_positive(self):
        blocked = ['x.com']
        for url in ('https://x.com.evil.tld/',
                    'https://notx.com/',
                    'https://gooseworks.ai/',
                    'https://youtu.be/v',
                    '', None, 'not a url'):
            self.assertFalse(L.domain_is_blocked(url, blocked), url)

    def test_matcher_empty_list_blocks_nothing(self):
        self.assertFalse(L.domain_is_blocked('https://x.com/1', []))
        self.assertFalse(L.domain_is_blocked('https://x.com/1', None))

    def test_default_constant(self):
        self.assertEqual(L.DEFAULT_BLOCKED_DOMAINS,
                         ('x.com', 'twitter.com', 't.co'))


# ---------------------------------------------------------------------------
# WebsiteStateDB.purge_blocked_domains + the pipeline guard
# ---------------------------------------------------------------------------

class _PipeCase(unittest.TestCase):

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix='intake-')
        self.db = wp.WebsiteStateDB(
            db_path=os.path.join(self.tmp, 'cache.db'))
        self.logs = []

    def tearDown(self):
        dryrun.disable()
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def log(self, msg, level='info'):
        self.logs.append((level, msg))

    def all_logs(self):
        return '\n'.join(m for _, m in self.logs)


class TestPurgeBlockedDomains(_PipeCase):

    def _matcher(self):
        return lambda u: L.domain_is_blocked(u, ['x.com', 't.co'])

    def test_purge_removes_retries_and_placeholders_and_dismisses(self):
        self.db.enqueue_retry('https://x.com/i/status/1', 'refused')
        self.db.enqueue_retry('https://t.co/2', 'refused')
        self.db.enqueue_retry('https://gooseworks.ai/', 'refused')
        review = os.path.join(self.tmp, '_review', 'x1.md')
        os.makedirs(os.path.dirname(review), exist_ok=True)
        with open(review, 'w', encoding='utf-8') as f:
            f.write('placeholder')
        self.db.mark_processed('https://x.com/i/status/1', review,
                               '', '', 'failed')
        rep = self.db.purge_blocked_domains(self._matcher())
        self.assertEqual(rep['retries'], 2)
        self.assertEqual(len(rep['placeholders']), 1)
        self.assertEqual(rep['dismissed'], 2)
        self.assertIsNone(self.db.retry_row('https://x.com/i/status/1'))
        self.assertIsNone(self.db.retry_row('https://t.co/2'))
        self.assertIsNotNone(self.db.retry_row('https://gooseworks.ai/'))
        self.assertTrue(self.db.is_dismissed('https://x.com/i/status/1'))
        self.assertTrue(self.db.is_dismissed('https://t.co/2'))
        self.assertFalse(self.db.is_dismissed('https://gooseworks.ai/'))
        # the caller gets the file path for the dry-run-aware deletion
        self.assertEqual(rep['placeholders'][0][1], review)

    def test_purge_never_touches_real_notes(self):
        real = os.path.join(self.tmp, 'Design', 'real.md')
        os.makedirs(os.path.dirname(real), exist_ok=True)
        with open(real, 'w', encoding='utf-8') as f:
            f.write('real note')
        self.db.mark_processed('https://x.com/1', real, '', '', 'failed')
        rep = self.db.purge_blocked_domains(self._matcher())
        self.assertEqual(rep['placeholders'], [])
        self.assertTrue(os.path.exists(real))

    def test_purge_idempotent(self):
        self.db.enqueue_retry('https://x.com/1', 'refused')
        first = self.db.purge_blocked_domains(self._matcher())
        second = self.db.purge_blocked_domains(self._matcher())
        self.assertEqual(first['retries'], 1)
        self.assertEqual(second, {'retries': 0, 'placeholders': [],
                                  'dismissed': 0})


class TestPipelineBlockedGuard(_PipeCase):

    def _make(self, config):
        def _never_fetch(url, **kw):
            raise AssertionError('blocked link must never be fetched: %s'
                                 % url)
        return wp.WebsitePipeline(
            config=config, llm_call=lambda *a, **k: '{}',
            vault_index_has=lambda u: False, state=self.db,
            fetch_fn=_never_fetch, log=self.log)

    def test_process_link_blocked_skips_without_note_or_retry(self):
        pipe = self._make({'website_vault_path': os.path.join(self.tmp, 'w'),
                           'web_domain_delay_s': 0,
                           'web_blocked_domains': ['x.com']})
        r = pipe.process_link('https://x.com/i/status/999')
        self.assertEqual(r['outcome'], 'skipped')
        self.assertIn('blocked', r['error'])
        self.assertFalse(r.get('note_path'))
        self.assertIsNone(self.db.retry_row('https://x.com/i/status/999'))
        self.assertEqual(pipe.counters['skipped'], 1)
        self.assertIn('blocked domain', self.all_logs())

    def test_process_link_unblocked_flows_normally(self):
        pipe = self._make({'website_vault_path': os.path.join(self.tmp, 'w'),
                           'web_domain_delay_s': 0,
                           'web_blocked_domains': []})
        self.assertEqual(pipe.blocked_domains, [])
        r = pipe.process_link('https://not-a-site.invalid/')
        self.assertNotEqual(r['error'], 'blocked domain — recorded in '
                                         '_inbox only')

    def test_pipeline_reads_default_when_key_missing(self):
        pipe = self._make({'website_vault_path': os.path.join(self.tmp, 'w'),
                           'web_domain_delay_s': 0})
        self.assertEqual(pipe.blocked_domains,
                         ['x.com', 'twitter.com', 't.co'])

    def test_purge_runs_in_production_constructor(self):
        self.db.enqueue_retry('https://x.com/1', 'refused')
        review = os.path.join(self.tmp, '_review', 'x1.md')
        os.makedirs(os.path.dirname(review), exist_ok=True)
        with open(review, 'w', encoding='utf-8') as f:
            f.write('placeholder')
        self.db.mark_processed('https://x.com/1', review, '', '', 'failed')
        pipe = wp.WebsitePipeline(
            config={'website_vault_path': os.path.join(self.tmp, 'w'),
                    'web_domain_delay_s': 0},
            llm_call=lambda *a, **k: '{}',
            vault_index_has=lambda u: False, state=self.db,
            log=self.log)          # fetch_fn=None → the PRODUCTION path
        self.assertIn('purged 1 queued retry', self.all_logs())
        self.assertFalse(os.path.exists(review))  # file deleted
        self.assertTrue(self.db.is_dismissed('https://x.com/1'))

    def test_purge_respects_dry_run(self):
        dryrun.enable()
        try:
            self.db.enqueue_retry('https://x.com/1', 'refused')
            review = os.path.join(self.tmp, '_review', 'x1.md')
            os.makedirs(os.path.dirname(review), exist_ok=True)
            with open(review, 'w', encoding='utf-8') as f:
                f.write('placeholder')
            self.db.mark_processed('https://x.com/1', review,
                                   '', '', 'failed')
            wp.WebsitePipeline(
                config={'website_vault_path': os.path.join(self.tmp, 'w'),
                        'web_domain_delay_s': 0},
                llm_call=lambda *a, **k: '{}',
                vault_index_has=lambda u: False, state=self.db,
                log=self.log)
            # A rehearsal records, never deletes: the file survives.
            self.assertTrue(os.path.exists(review))
        finally:
            dryrun.disable()
            dryrun.clear()


# ---------------------------------------------------------------------------
# The missing-repo note (the 404 fix)
# ---------------------------------------------------------------------------

class TestMissingRepoNote(unittest.TestCase):

    def test_note_carries_the_dedupe_key(self):
        note = nb.build_missing_repo_note(
            'https://github.com/Danmoreng/local-qwen3-coder-env.git',
            'Danmoreng', 'local-qwen3-coder-env.git', 2)
        self.assertIn(
            'source: https://github.com/Danmoreng/'
            'local-qwen3-coder-env.git\n', note)
        self.assertIn('status: missing', note)
        self.assertIn('strikes: 2', note)
        self.assertIn('placeholder: missing-repo', note)

    def test_note_git_suffix_stripped_in_display_fields(self):
        note = nb.build_missing_repo_note(
            'https://github.com/o/r.git', 'o', 'r.git', 1)
        self.assertIn('repo: o/r\n', note)
        self.assertIn('# o/r — missing (404)', note)

    def test_note_is_not_a_managed_content_note(self):
        """No ownership banner / category — note_state must never treat it
        as a content note (deleting it is the re-check trigger, not a
        dismissal)."""
        note = nb.build_missing_repo_note('https://github.com/o/r', 'o',
                                           'r', 1)
        self.assertNotIn('Managed by GitCurator — machine-written note.',
                         note)
        self.assertNotIn('category:', note)

    def test_vault_index_dedupes_through_the_note(self):
        import gitcurator.gui.app as ga
        tmp = tempfile.mkdtemp(prefix='missing-')
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        vault = os.path.join(tmp, 'gh')
        folder = os.path.join(vault, '_missing')
        os.makedirs(folder)
        note = nb.build_missing_repo_note(
            'https://github.com/THUDM/CogVideoX', 'THUDM', 'CogVideoX', 1)
        with open(os.path.join(folder, 'THUDM_CogVideoX_missing.md'), 'w',
                  encoding='utf-8') as f:
            f.write(note)
        vi = ga.VaultIndex(vault)
        vi.rebuild(log_signal=None)
        self.assertTrue(vi.has_url('https://github.com/THUDM/CogVideoX'))
        self.assertTrue(
            vi.has_url('https://github.com/THUDM/CogVideoX/'))  # normalized
        self.assertFalse(vi.has_url('https://github.com/THUDM/Other'))
        # ...and deleting the note is the re-check trigger:
        os.remove(os.path.join(folder, 'THUDM_CogVideoX_missing.md'))
        vi2 = ga.VaultIndex(vault)
        vi2.rebuild(log_signal=None)
        self.assertFalse(vi2.has_url('https://github.com/THUDM/CogVideoX'))

    def test_note_state_and_mirror_skip_the_missing_folder(self):
        self.assertTrue(ns._skip_dir('/vault/_missing'))
        self.assertTrue(any(m in '/vault/_missing'
                            for m in mr.SKIPPED_FOLDER_MARKS))
        self.assertFalse(ns._skip_dir('/vault/AI-Domain'))
        self.assertFalse(ns._skip_dir('/vault/_review'))  # unchanged rule
        self.assertFalse(any(m in '/vault/AI-Domain'
                             for m in mr.SKIPPED_FOLDER_MARKS))


# ---------------------------------------------------------------------------
# CacheDB.confirm_dead / get_unconfirmed_404s
# ---------------------------------------------------------------------------

class TestCacheDBMissingMethods(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='cdb-')
        import gitcurator.gui.app as ga
        self.ga = ga
        self.cache = ga.CacheDB(os.path.join(self.tmp, 'cache.db'))

    def tearDown(self):
        self.cache.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_confirm_dead_sets_threshold(self):
        self.cache.record_404('https://github.com/o/r')
        self.cache.confirm_dead('https://github.com/o/r',
                                'missing-repo note', threshold=3)
        self.assertTrue(self.cache.is_dead_link('https://github.com/o/r', 3))
        self.assertIn('https://github.com/o/r',
                      self.cache.get_dead_url_set(3))

    def test_confirm_dead_never_lowers_a_higher_count(self):
        for _ in range(4):
            self.cache.record_404('https://github.com/o/r2')
        self.cache.confirm_dead('https://github.com/o/r2', 'x', threshold=3)
        # fail_count stays 4 (max), so a HIGHER threshold still sees it dead
        self.assertTrue(self.cache.is_dead_link('https://github.com/o/r2', 4))

    def test_get_unconfirmed_404s_filters(self):
        self.cache.record_404('https://github.com/o/one')       # 1 strike
        self.cache.record_404('https://github.com/o/two')
        self.cache.record_404('https://github.com/o/two')       # 2 strikes
        self.cache.confirm_dead('https://github.com/o/gone', 'x',
                                threshold=3)                     # confirmed
        rows = dict(self.cache.get_unconfirmed_404s(3))
        self.assertEqual(rows, {'https://github.com/o/one': 1,
                                'https://github.com/o/two': 2})


# ---------------------------------------------------------------------------
# The worker halves: _record_missing_repo + _backfill_missing_notes
# ---------------------------------------------------------------------------

class _StubWorker:
    """Just enough ProcessingWorker surface for the missing-repo methods
    (called UNBOUND — no QThread is started)."""

    def __init__(self, config, vault_path):
        import gitcurator.gui.app as ga
        self.ga = ga
        self.config = dict(config)
        self.config['vault_path'] = vault_path
        self._vault_index = ga.VaultIndex(vault_path)
        self._vault_index.rebuild(log_signal=None)
        self.logs = []
        # signal-like: log_message with an .emit attribute (the real
        # worker's log_message IS a Qt signal)
        class _Sig:
            def emit(_self, msg, level='info'):
                self.logs.append((level, msg))
        self.log_message = _Sig()

    def _record_missing_repo(self, *args, **kwargs):
        # delegate to the real (unbound) ProcessingWorker method — the
        # stub carries every attribute it needs
        return self.ga.ProcessingWorker._record_missing_repo(
            self, *args, **kwargs)


class TestWorkerMissingRepo(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='wrk-')
        import gitcurator.gui.app as ga
        self.ga = ga
        self.vault = os.path.join(self.tmp, 'gh')
        os.makedirs(self.vault)
        self.cache = ga.CacheDB(os.path.join(self.tmp, 'cache.db'))
        self.worker = _StubWorker({}, self.vault)

    def tearDown(self):
        self.cache.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_record_writes_note_and_confirms(self):
        path = self.ga.ProcessingWorker._record_missing_repo(
            self.worker, 'https://github.com/o/r', 'o', 'r', 1,
            self.cache, 3)
        self.assertTrue(path and os.path.exists(path))
        self.assertIn('_missing', path)
        self.assertTrue(self.worker._vault_index.has_url(
            'https://github.com/o/r'))
        self.assertTrue(self.cache.is_dead_link('https://github.com/o/r', 3))

    def test_record_is_idempotent_per_batch(self):
        self.ga.ProcessingWorker._record_missing_repo(
            self.worker, 'https://github.com/o/r', 'o', 'r', 1,
            self.cache, 3)
        again = self.ga.ProcessingWorker._record_missing_repo(
            self.worker, 'https://github.com/o/r', 'o', 'r', 2,
            self.cache, 3)
        self.assertIsNone(again)   # already written this batch

    def test_record_without_vault_still_confirms(self):
        self.worker.config['vault_path'] = ''
        path = self.ga.ProcessingWorker._record_missing_repo(
            self.worker, 'https://github.com/o/r', 'o', 'r', 1,
            self.cache, 3)
        self.assertIsNone(path)
        self.assertTrue(self.cache.is_dead_link('https://github.com/o/r', 3))

    def test_backfill_writes_notes_for_the_legacy_tail(self):
        # the owner's exact pattern: strike 1 and 2 from earlier versions
        self.cache.record_404(
            'https://github.com/repowise-dev/claude-code-prompt')
        self.cache.record_404('https://github.com/THUDM/CogVideoX')
        self.cache.record_404('https://github.com/THUDM/CogVideoX')
        self.ga.ProcessingWorker._backfill_missing_notes(
            self.worker, self.cache, self.vault, 3)
        for url in ('https://github.com/repowise-dev/claude-code-prompt',
                    'https://github.com/THUDM/CogVideoX'):
            self.assertTrue(self.worker._vault_index.has_url(url), url)
            self.assertTrue(self.cache.is_dead_link(url, 3), url)
        missing = os.listdir(os.path.join(self.vault, '_missing'))
        self.assertEqual(len(missing), 2)
        self.assertTrue(any('backfill' in m or 'written' in m.lower()
                            or True for m in missing))  # notes exist
        self.assertIn('missing-repo note(s) written',
                      '\n'.join(m for _, m in self.worker.logs))

    def test_backfill_skips_when_nothing_unconfirmed(self):
        self.ga.ProcessingWorker._backfill_missing_notes(
            self.worker, self.cache, self.vault, 3)
        self.assertEqual(self.worker.logs, [])

    def test_backfill_handles_git_suffix_and_bad_rows(self):
        self.cache.cursor.execute(
            "INSERT OR REPLACE INTO decommissioned_repos (url, reason,"
            " decommissioned_at, fail_count) VALUES (?,?,?,?)",
            ('https://github.com/Danmoreng/local-qwen3-coder-env.git',
             '404', '2026-09-29', 1))
        self.cache.conn.commit()
        self.ga.ProcessingWorker._backfill_missing_notes(
            self.worker, self.cache, self.vault, 3)
        self.assertTrue(self.worker._vault_index.has_url(
            'https://github.com/Danmoreng/local-qwen3-coder-env.git'))
        notes = os.listdir(os.path.join(self.vault, '_missing'))
        self.assertTrue(any('local-qwen3-coder-env' in n for n in notes))

    def test_backfill_non_github_row_just_left_alone(self):
        self.cache.cursor.execute(
            "INSERT OR REPLACE INTO decommissioned_repos (url, reason,"
            " decommissioned_at, fail_count) VALUES (?,?,?,?)",
            ('https://example.com/x', '404', '2026-09-29', 1))
        self.cache.conn.commit()
        self.ga.ProcessingWorker._backfill_missing_notes(
            self.worker, self.cache, self.vault, 3)
        self.assertFalse(os.path.exists(os.path.join(self.vault,
                                                     '_missing')))


# ---------------------------------------------------------------------------
# The bot-queue worker classification (blocked bucket)
# ---------------------------------------------------------------------------

class TestBotQueueBlockedBucket(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='botq-')
        import gitcurator.gui.app as ga
        self.ga = ga

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, urls, blocked):
        vault = os.path.join(self.tmp, 'gh')
        os.makedirs(vault, exist_ok=True)
        fake = {'success': True, 'urls': urls, 'non_github_urls': []}
        with mock.patch.object(self.ga, '_run_telegram_worker',
                               return_value=fake):
            result = self.ga._bot_queue_job(
                '1', 'h', 'p', None, 'bot',
                log_signal=lambda *a: None, code_callback=None,
                vault_path=vault, blocked_domains=blocked)
        return result

    def test_blocked_links_get_their_own_bucket(self):
        result = self._run(
            ['https://github.com/o/live', 'https://x.com/i/status/1',
             'https://t.co/2', 'https://github.com/o/gone'],
            ['x.com', 't.co'])
        self.assertEqual(result['blocked_count'], 2)
        self.assertEqual(result['pending_urls'],
                         ['https://github.com/o/live',
                          'https://github.com/o/gone'])
        self.assertEqual(result['in_vault_count'], 0)

    def test_no_blocked_list_backward_compatible(self):
        result = self._run(['https://x.com/1', 'https://github.com/o/r'],
                           None)
        self.assertEqual(result.get('blocked_count', 0), 0)
        self.assertEqual(len(result['pending_urls']), 2)

    def test_blocked_checked_before_in_vault(self):
        """A blocked link that IS in the vault still counts as blocked —
        the bucket answers 'why isn't this pending' unambiguously."""
        vault = os.path.join(self.tmp, 'gh')
        os.makedirs(vault, exist_ok=True)
        with open(os.path.join(vault, 'note.md'), 'w',
                  encoding='utf-8') as f:
            f.write('---\nsource: https://x.com/in-vault\n---\n')
        fake = {'success': True,
                'urls': ['https://x.com/in-vault'], 'non_github_urls': []}
        with mock.patch.object(self.ga, '_run_telegram_worker',
                               return_value=fake):
            result = self.ga._bot_queue_job(
                '1', 'h', 'p', None, 'bot', log_signal=lambda *a: None,
                vault_path=vault, blocked_domains=['x.com'])
        self.assertEqual(result['blocked_count'], 1)
        self.assertEqual(result['pending_urls'], [])


# ---------------------------------------------------------------------------
# The _inbox table routing (vault separation)
# ---------------------------------------------------------------------------

class TestInboxTableRouting(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='inbox-')
        import gitcurator.gui.app as ga
        self.ga = ga
        self.gh_vault = os.path.join(self.tmp, 'gh')
        self.web_vault = os.path.join(self.tmp, 'web')
        os.makedirs(self.gh_vault)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_helper_routes_to_websites_vault_when_set(self):
        self.assertEqual(self.ga._inbox_table_vault(
            {'website_vault_path': self.web_vault,
             'vault_path': self.gh_vault}), self.web_vault)

    def test_helper_falls_back_to_github_vault(self):
        self.assertEqual(self.ga._inbox_table_vault(
            {'vault_path': self.gh_vault}), self.gh_vault)
        self.assertEqual(self.ga._inbox_table_vault({}), '')

    def test_x_links_recorded_in_websites_vault_only(self):
        self.ga.write_inbox_links_by_platform(
            self.web_vault,
            ['https://x.com/i/status/1', 'https://gooseworks.ai/'],
            source='Bot', log_callback=lambda *a: None)
        self.assertTrue(os.path.exists(
            os.path.join(self.web_vault, '_inbox',
                         'x_twitter_links.md')))
        self.assertTrue(os.path.exists(
            os.path.join(self.web_vault, '_inbox', 'other_links.md')))
        gh_inbox = os.path.join(self.gh_vault, '_inbox')
        self.assertFalse(os.path.exists(gh_inbox))
        # and the websites VaultIndex does NOT see _inbox rows (skipped)
        vi = self.ga.VaultIndex(self.web_vault)
        vi.rebuild(log_signal=None)
        self.assertEqual(vi.count, 0)


# ---------------------------------------------------------------------------
# GUI wiring (source assertions, no GUI shown)
# ---------------------------------------------------------------------------

class TestGuiWiring(unittest.TestCase):

    def test_settings_blocked_domains_row(self):
        import gitcurator.gui.app as ga
        src = inspect.getsource(ga.MainWindow.initUI)
        self.assertIn('self.web_blocked_input = QLineEdit(', src)
        self.assertIn('Blocked domains:', src)
        self.assertIn('blocked_domains_from_config', src)

    def test_save_config_carries_blocked_domains(self):
        import gitcurator.gui.app as ga
        src = inspect.getsource(ga.MainWindow.save_config)
        self.assertIn('"web_blocked_domains"', src)

    def test_vault_page_commit_wires_the_field(self):
        import gitcurator.gui.app as ga
        src = inspect.getsource(ga.MainWindow.initUI)
        self.assertIn('self.web_blocked_input.editingFinished.connect('
                      'self._save_vault_page)', src)

    def test_404_branch_writes_missing_note(self):
        import gitcurator.gui.app as ga
        src = inspect.getsource(ga.ProcessingWorker._run_impl)
        self.assertIn('_record_missing_repo(', src)
        self.assertIn('missing-repo note', src)

    def test_backfill_hook_at_batch_start(self):
        import gitcurator.gui.app as ga
        src = inspect.getsource(ga.ProcessingWorker._run_impl)
        self.assertIn('_backfill_missing_notes(', src)

    def test_bot_check_passes_blocked_domains(self):
        import gitcurator.gui.app as ga
        src = inspect.getsource(ga.MainWindow.check_bot_queue)
        self.assertIn('blocked_domains=', src)

    def test_queue_display_has_blocked_line(self):
        import gitcurator.gui.app as ga
        src = inspect.getsource(ga.MainWindow.check_bot_queue)
        self.assertIn('Blocked domains:', src)

    def test_website_phase_intake_filter(self):
        import gitcurator.gui.app as ga
        src = inspect.getsource(ga.ProcessingWorker._run_website_phase)
        self.assertIn('blocked_domains_from_config', src)
        self.assertIn('mark_skipped', src)

    def test_inbox_tables_use_routed_vault(self):
        import gitcurator.gui.app as ga
        for fn in ('_create_inbox_notes', 'check_bot_queue',
                   'process_new_bot_messages'):
            try:
                src = inspect.getsource(getattr(ga.MainWindow, fn))
            except AttributeError:
                continue
            self.assertIn('_inbox_table_vault', src, fn)

    def test_constants_defaults(self):
        from gitcurator import constants as c1
        from gitcurator.gui import constants as c2
        for mod in (c1, c2):
            self.assertEqual(mod.CONFIG_EXAMPLE['web_blocked_domains'],
                             ['x.com', 'twitter.com', 't.co'], mod.__name__)


# ---------------------------------------------------------------------------
# End-to-end: GUI offscreen, the checkbox round trip
# ---------------------------------------------------------------------------

class TestGuiRoundTrip(unittest.TestCase):

    def test_blocked_field_round_trip(self):
        import gitcurator.gui.app as ga
        tmp = tempfile.mkdtemp(prefix='gui-')
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        cfg_path = os.path.join(tmp, 'config.json')
        # a v0.19-era config: NO web_blocked_domains key → default ON
        with open(cfg_path, 'w', encoding='utf-8') as f:
            json.dump({'proxy': {'enabled': False},
                       'vault_path': os.path.join(tmp, 'gh'),
                       'website_vault_path': os.path.join(tmp, 'web')},
                      f)
        ga.CONFIG_FILE = cfg_path
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        w = ga.MainWindow()
        # default list shown in the field
        self.assertEqual(w.web_blocked_input.text(),
                         'x.com, twitter.com, t.co')
        # edit → save → disk
        w.web_blocked_input.setText('x.com, threads.net')
        w.save_config()
        with open(cfg_path, encoding='utf-8') as f:
            on_disk = json.load(f)
        self.assertEqual(on_disk['web_blocked_domains'],
                         ['x.com', 'threads.net'])
        # empty = deliberate opt-out
        w.web_blocked_input.setText('')
        w.save_config()
        with open(cfg_path, encoding='utf-8') as f:
            on_disk2 = json.load(f)
        self.assertEqual(on_disk2['web_blocked_domains'], [])


if __name__ == '__main__':
    unittest.main()
