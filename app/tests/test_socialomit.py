#!/usr/bin/env python3
"""
test_socialomit.py — v0.35.0: THE LAW, second reading + the omission intake.

The owner's words (2026-10-05): "omit all youtube and every social media
links. ONLY ONLY ONLY websites that aren't social media domains, and
github" — "it created a note for this https://share.google/… app must not
process [it] google drive /sheets/docs link" — "must not collect youtube
links for note or review, same for X, and hugging face links" — "in
_inbox of website vault, app curated 'other links' which almost most of
them must be ommited, because some of them are already addressed and
stored in correct notes and formats inside the vault, and some must be
ommitted like x and t.co domains" — and "I want the log to show, where
each link goes, for example [Vault Name] Item X processed and stored."

Covered here:
  1. the LAW's second reading: YouTube (the whole group), the Google
     share/drive/docs family, the social-media majors — all banned;
     Reddit / Medium / arXiv / npm / pypi still allowed (content
     platforms the owner curates)
  2. the pipeline omits a banned link ENTIRELY (never fetched, no note,
     no _review, no retry) and the legacy note on a newly-banned domain
     (the owner's exact "Knowledge, Research & Reference" YouTube note)
     is quarantined by the run-start sweep
  3. classify_platform — moved to core, suffix-anchored (look-alike
     hosts like notyoutube.com no longer classify as youtube)
  4. the _inbox intake writer: banned links are never collected (no
     row); links already stored as notes in the vault are not re-added
  5. prune_inbox_tables — legacy banned rows and already-stored rows
     leave the tables; everything else is kept VERBATIM
  6. the destination logs: "[Vault Name] Item X processed and stored"
     for both pipelines (the websites side functionally, the GitHub
     side by wiring)
  7. wiring: the callers pass the law into the writer; the websites
     phase prunes the tables after every batch; the pipeline-off intake
     marks banned links blocked instead of recorded

All against LOCAL temp dirs / stubs — no network, no GUI shown.
"""

import inspect
import json
import os
import shutil
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gitcurator.core import links as L
from gitcurator.core import dryrun
from gitcurator.core import web_fetch as _web_fetch
from gitcurator.core import website_pipeline as wp
from gitcurator.core import website_directory as wd
from gitcurator.gui.platform_intake import (
    PLATFORM_INFO, classify_platform, prune_inbox_tables,
    write_inbox_links_by_platform,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _write_note(path, url, category='', subcategory='', title='Untitled',
                managed=True, fetch_status='full'):
    """A v2-shaped website note (the pipeline's own format)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fm = [
        '---',
        f'source: "{url}"',
        'aliases: []',
        'tags: []',
        f'category: "{category}"',
        f'subcategory: "{subcategory}"',
        f'fetch_status: "{fetch_status}"',
        'pricing: "unknown"',
        'login_required: "unknown"',
        'date_processed: 2026-10-05',
        f'managed_by: "{("gitcurator" if managed else "human")}"',
        'schema_version: "1"',
        'prompt_version: "web-v2"',
        '---',
        '',
        '> [!info] Managed by GitCurator — machine-written note.',
        '',
        f'# {title}',
        '',
        '> **TL;DR:** a test note',
        '',
        '## What it does',
        '',
        '- a test note',
        '',
        '## Best used for',
        'Use when you need to…',
        '',
        '## Similar tools',
        '—',
        '',
        '---',
        f'*Source: [{url}]({url})*',
        '',
    ]
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(fm))


class _FakeFetch:
    """Canned fetch: every page is a classifiable design site; a fetch of
    a banned URL is a hard failure of the test (the gate must stop it)."""

    def __init__(self):
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append(url)
        html = ("<html><head><title>Test Site</title>"
                "<meta name=\"description\" content=\"A test page.\">"
                "</head><body><p>Body text about design tools and "
                "resources for building websites and applications, long "
                "enough to classify confidently.</p></body></html>")
        return _web_fetch.FetchResult(
            url=url, final_url=url, status='full', http_status=200,
            content_type='text/html', charset='utf-8',
            body=html.encode('utf-8'), text=html)


class _FakeLLM:
    """The phase-2 scriptable LLM: Design / Assets & Resources / Test Site."""

    def __call__(self, messages, task=None):
        text = messages[0]['content']
        if 'filing a website into a personal library' in text:
            return json.dumps({'category': 'Design',
                               'confidence': 'high', 'reason': 't'})
        if 'was filed under' in text:
            return json.dumps({'subcategory': 'Assets & Resources',
                               'confidence': 'medium'})
        return json.dumps({
            'name': 'Test Site', 'one_line': 'A test page.',
            'what_it_does': ['One', 'Two'],
            'best_used_for': 'Use when you need to test the pipeline.',
            'pricing': 'free', 'login_required': 'no',
            'similar_tools': [], 'tags': ['design'],
            'confidence': 'high'})


class _Case(unittest.TestCase):
    """A temp websites vault + captured logs + a pipeline factory."""

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix='socialomit-')
        self.vault = os.path.join(self.tmp, 'WebSites Vault')
        os.makedirs(self.vault)
        self.logs = []

    def tearDown(self):
        dryrun.disable()
        dryrun.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def log(self, msg, level='info'):
        self.logs.append((level, msg))

    def all_logs(self):
        return '\n'.join(m for _, m in self.logs)

    def note(self, *rel):
        return os.path.join(self.vault, *rel)

    def quarantine(self):
        return os.path.join(self.vault, '.trash', 'banned-domains')


# ---------------------------------------------------------------------------
# 1. The law's second reading
# ---------------------------------------------------------------------------

class TestTheSecondReading(_Case):

    def test_youtube_group_banned(self):
        """The owner's exact failing link plus every YouTube host form."""
        law = list(L.LAW_BLOCKED_DOMAINS)
        for url in ('https://youtu.be/-RmLiZXY27o?is=4ciKl08r-QMXt5vm',
                    'https://www.youtube.com/watch?v=abc',
                    'https://m.youtube.com/watch?v=abc',
                    'https://music.youtube.com/watch?v=abc',
                    'https://youtube.com/shorts/abc',
                    'https://www.youtube-nocookie.com/embed/abc'):
            self.assertTrue(L.domain_is_blocked(url, law), url)

    def test_google_share_and_docs_family_banned(self):
        law = list(L.LAW_BLOCKED_DOMAINS)
        for url in ('https://share.google/BT28UZDrSm0Vp34Hk',
                    'https://drive.google.com/file/d/x/view',
                    'https://docs.google.com/document/d/x/edit',
                    'https://docs.google.com/spreadsheets/d/x/edit',
                    'https://docs.google.com/presentation/d/x/edit',
                    'https://forms.google.com/e/xyz'):
            self.assertTrue(L.domain_is_blocked(url, law), url)
        # the plain search engine is NOT part of the family
        self.assertFalse(L.domain_is_blocked(
            'https://www.google.com/search?q=x', law))

    def test_social_media_majors_banned(self):
        law = list(L.LAW_BLOCKED_DOMAINS)
        for url in ('https://www.tiktok.com/@user/video/1',
                    'https://vm.tiktok.com/abc/',
                    'https://www.threads.net/@user/post/1',
                    'https://www.snapchat.com/add/user',
                    'https://www.pinterest.com/pin/1/',
                    'https://pin.it/abc',
                    'https://www.twitch.tv/user',
                    'https://discord.gg/invite',
                    'https://discord.com/invite/abc',
                    'https://t.me/somechannel/42',
                    'https://wa.me/989123456789',
                    'https://vk.com/wall1_2',
                    'https://bsky.app/profile/user.bsky.social',
                    'https://weibo.com/u/1'):
            self.assertTrue(L.domain_is_blocked(url, law), url)

    def test_content_platforms_still_allowed(self):
        """Reddit, Medium, arXiv and the package registries are curated
        content, not social-media feeds — the golden set's reddit wiki
        is an approved Knowledge, Research & Reference link."""
        law = list(L.LAW_BLOCKED_DOMAINS)
        for url in ('https://www.reddit.com/r/FREEMEDIAHECKYEAH/wiki/reading',
                    'https://old.reddit.com/r/x',
                    'https://medium.com/@user/article',
                    'https://arxiv.org/abs/2601.00001',
                    'https://www.npmjs.com/package/left-pad',
                    'https://pypi.org/project/requests/'):
            self.assertFalse(L.domain_is_blocked(url, law), url)

    def test_config_can_still_add_but_never_remove(self):
        self.assertEqual(L.blocked_domains_from_config(
            {'web_blocked_domains': 'reddit.com'}),
            list(L.LAW_BLOCKED_DOMAINS) + ['reddit.com'])
        self.assertEqual(L.blocked_domains_from_config(
            {'web_blocked_domains': []}), list(L.LAW_BLOCKED_DOMAINS))


# ---------------------------------------------------------------------------
# 2. The pipeline omits banned links entirely
# ---------------------------------------------------------------------------

class TestPipelineOmission(_Case):

    def _make(self, extra=None):
        cfg = {'website_vault_path': self.vault, 'web_domain_delay_s': 0}
        cfg.update(extra or {})
        return wp.WebsitePipeline(
            config=cfg, llm_call=_FakeLLM(),
            vault_index_has=lambda u: False,
            state=wp.WebsiteStateDB(
                db_path=os.path.join(self.tmp, 'cache.db')),
            fetch_fn=_FakeFetch(), log=self.log)

    def test_youtube_link_is_omitted_entirely(self):
        pipe = self._make()
        r = pipe.process_link(
            'https://youtu.be/-RmLiZXY27o?is=4ciKl08r-QMXt5vm')
        self.assertEqual(r['outcome'], 'skipped')
        self.assertIn('omitted', r['error'])
        self.assertFalse(r.get('note_path'))
        # no note ANYWHERE in the vault — not even a _review placeholder
        for root, dirs, files in os.walk(self.tmp):
            for f in files:
                self.assertFalse(f.endswith('.md'), f)
        # the log names the vault + the omission
        self.assertIn('banned domain', self.all_logs())
        self.assertIn('omitted', self.all_logs())
        self.assertIn(os.path.basename(self.vault), self.all_logs())

    def test_share_google_link_is_omitted_entirely(self):
        pipe = self._make()
        r = pipe.process_link('https://share.google/BT28UZDrSm0Vp34Hk')
        self.assertEqual(r['outcome'], 'skipped')
        self.assertEqual(pipe.counters['skipped'], 1)

    def test_legacy_youtube_note_is_swept_by_the_law(self):
        """The owner's real-world case: a YouTube note sitting in
        'Knowledge, Research & Reference' from before the second
        reading — the run-start sweep quarantines it."""
        _write_note(
            self.note('Knowledge__Research___Reference',
                      'Big_Video_Essay.md'),
            'https://www.youtube.com/watch?v=-RmLiZXY27o',
            category='Knowledge, Research & Reference',
            title='Big Video Essay')
        _write_note(self.note('Design', 'Real_Site.md'),
                    'https://coolors.co/', category='Design',
                    title='Real Site')
        report = wd.sweep_banned_notes(
            self.vault, L.blocked_domains_from_config({}), log=self.log)
        self.assertEqual(len(report['moved']), 1)
        self.assertFalse(os.path.exists(
            self.note('Knowledge__Research___Reference',
                      'Big_Video_Essay.md')))
        self.assertTrue(os.path.exists(
            os.path.join(self.quarantine(), 'Big_Video_Essay.md')))
        self.assertTrue(os.path.exists(self.note('Design', 'Real_Site.md')))


# ---------------------------------------------------------------------------
# 3. classify_platform — core, suffix-anchored
# ---------------------------------------------------------------------------

class TestClassifyPlatform(unittest.TestCase):

    def test_platform_routing(self):
        f = L.classify_platform
        self.assertEqual(f('https://www.youtube.com/watch?v=abc'), 'youtube')
        self.assertEqual(f('https://youtu.be/abc'), 'youtube')
        self.assertEqual(f('https://x.com/post/123'), 'x_twitter')
        self.assertEqual(f('https://t.co/abc'), 'x_twitter')
        self.assertEqual(f('https://redd.it/abc'), 'reddit')
        self.assertEqual(f('https://www.reddit.com/r/x'), 'reddit')
        self.assertEqual(f('https://github.com/a/b'), 'github')
        self.assertEqual(f('https://gist.github.com/a/1'), 'github')
        self.assertEqual(f('https://arxiv.org/abs/1'), 'arxiv')
        self.assertEqual(f('https://pypi.org/project/x/'), 'package_registry')

    def test_lookalike_hosts_are_not_the_platform(self):
        f = L.classify_platform
        # the old gui substring test matched these as the platform
        self.assertEqual(f('https://notyoutube.com/watch'), 'other')
        self.assertEqual(f('https://x.com.evil.tld/post'), 'other')
        self.assertEqual(f('https://medium.com.example.org/@u'), 'other')

    def test_gui_reexports_share_the_core_table(self):
        self.assertIs(classify_platform, L.classify_platform)
        for platform, (_display, filename) in PLATFORM_INFO.items():
            self.assertEqual(L.PLATFORM_TABLE_FILES.get(platform), filename,
                             platform)


# ---------------------------------------------------------------------------
# 4. The _inbox intake writer — never collect banned, never re-collect stored
# ---------------------------------------------------------------------------

class TestInboxOmission(_Case):

    def test_banned_links_are_never_collected(self):
        logs = []
        added = write_inbox_links_by_platform(
            self.vault,
            ['https://youtu.be/-RmLiZXY27o',
             'https://x.com/i/status/1',
             'https://t.co/abc',
             'https://share.google/BT28UZDrSm0Vp34Hk',
             'https://www.tiktok.com/@u/video/1',
             'https://huggingface.co/org/model'],
            source='Bot', log_callback=lambda m, l: logs.append(m),
            blocked_domains=L.blocked_domains_from_config({}))
        self.assertEqual(added, 0)
        self.assertFalse(os.path.exists(self.note('_inbox')))
        self.assertTrue(any('omitted' in m for m in logs), logs)
        self.assertTrue(any('banned' in m for m in logs), logs)

    def test_fresh_links_still_land_in_the_tables(self):
        added = write_inbox_links_by_platform(
            self.vault, ['https://coolors.co/', 'https://arxiv.org/abs/1'],
            source='Bot', log_callback=self.log,
            blocked_domains=L.blocked_domains_from_config({}))
        self.assertEqual(added, 2)
        self.assertTrue(os.path.exists(
            self.note('_inbox', 'other_links.md')))
        self.assertTrue(os.path.exists(
            self.note('_inbox', 'arxiv_links.md')))

    def test_links_already_stored_as_notes_are_not_re_added(self):
        _write_note(self.note('Design', 'Coolors.md'),
                    'https://coolors.co/', category='Design',
                    title='Coolors')
        logs = []
        added = write_inbox_links_by_platform(
            self.vault, ['https://coolors.co/', 'https://fresh.example/x'],
            source='Bot', log_callback=lambda m, l: logs.append(m),
            blocked_domains=[])
        self.assertEqual(added, 1)      # only the fresh one
        with open(self.note('_inbox', 'other_links.md'),
                  encoding='utf-8') as f:
            text = f.read()
        self.assertNotIn('coolors.co', text)
        self.assertIn('https://fresh.example/x', text)
        self.assertTrue(any('already stored' in m for m in logs), logs)

    def test_a_banned_only_batch_still_prunes_the_legacy_tables(self):
        """Even when every link of the batch is banned (nothing to write),
        the legacy rows in the existing tables are pruned on the pass."""
        os.makedirs(self.note('_inbox'), exist_ok=True)
        with open(self.note('_inbox', 'other_links.md'), 'w',
                  encoding='utf-8') as f:
            f.write('# 🔗 Other — Review Queue\n\n'
                    '| - | 2026-10-05 | https://x.com/i/status/1 | x.com '
                    '| Bot | unreviewed | |\n')
        write_inbox_links_by_platform(
            self.vault, ['https://youtu.be/abc'], source='Bot',
            log_callback=self.log,
            blocked_domains=L.blocked_domains_from_config({}))
        with open(self.note('_inbox', 'other_links.md'),
                  encoding='utf-8') as f:
            self.assertNotIn('x.com', f.read())


# ---------------------------------------------------------------------------
# 5. prune_inbox_tables — the legacy-row cleanup
# ---------------------------------------------------------------------------

class TestPruneTables(_Case):

    def _seed_other_links(self):
        os.makedirs(self.note('_inbox'), exist_ok=True)
        header = ('# 🔗 Other — Review Queue\n\n'
                  '> Auto-updated. DO NOT delete rows.\n'
                  '> Last updated: 2026-10-01 10:00\n\n'
                  '| # | Date | URL | Domain | Source | Status | Notes |\n'
                  '|---|------|-----|--------|--------|--------|-------|\n')
        rows = [
            # banned: x + t.co (the owner's exact complaint)
            '| - | 2026-10-01 | https://x.com/i/status/1 | x.com | Bot | unreviewed | |',
            '| - | 2026-10-01 | https://t.co/Mxdio85wvQ | t.co | Bot | unreviewed | |',
            # banned: the second reading's additions
            '| - | 2026-10-02 | https://youtu.be/-RmLiZXY27o | youtu.be | Bot | unreviewed | |',
            '| - | 2026-10-02 | https://share.google/BT28UZ | share.google | Bot | unreviewed | |',
            # already stored as a note in the vault
            '| - | 2026-10-03 | https://coolors.co/ | coolors.co | Bot | unreviewed | |',
            # fresh + owner-reviewed rows: both stay, VERBATIM
            '| - | 2026-10-03 | https://fresh.example/tool | fresh.example | Bot | unreviewed | |',
            '| - | 2026-10-03 | https://reviewed.example/tool | reviewed.example | Bot | ✅ reviewed | keep me |',
        ]
        with open(self.note('_inbox', 'other_links.md'), 'w',
                  encoding='utf-8') as f:
            f.write(header + '\n'.join(rows) + '\n')
        _write_note(self.note('Design', 'Coolors.md'),
                    'https://coolors.co/', category='Design',
                    title='Coolors')

    def _other_links_text(self):
        with open(self.note('_inbox', 'other_links.md'),
                  encoding='utf-8') as f:
            return f.read()

    def test_banned_and_stored_rows_leave(self):
        self._seed_other_links()
        dropped = prune_inbox_tables(
            self.vault, blocked_domains=L.blocked_domains_from_config({}),
            log_callback=self.log)
        self.assertEqual(dropped, 5)
        text = self._other_links_text()
        for gone in ('x.com/i/status', 't.co/Mxdio', 'youtu.be',
                     'share.google', 'coolors.co'):
            self.assertNotIn(gone, text, gone)
        # the fresh and the owner-reviewed rows survive verbatim
        self.assertIn('https://fresh.example/tool', text)
        self.assertIn('✅ reviewed', text)
        self.assertIn('keep me', text)
        # the "Last updated" stamp was refreshed
        self.assertNotIn('2026-10-01 10:00', text)
        self.assertTrue(any('pruned' in m for m in
                            (msg for _, msg in self.logs)), self.logs)

    def test_stored_urls_set_covers_the_just_processed_batch(self):
        """The websites phase passes the batch's fresh results as a set —
        rows for links stored THIS run are pruned without a rescan."""
        self._seed_other_links()
        dropped = prune_inbox_tables(
            self.vault, blocked_domains=[],
            stored_urls={'https://fresh.example/tool'},
            log_callback=self.log)
        self.assertEqual(dropped, 1)
        text = self._other_links_text()
        self.assertNotIn('fresh.example', text)
        # everything else (banned included) is NOT this call's business
        self.assertIn('x.com/i/status', text)

    def test_prune_with_an_explicit_empty_scope_is_a_noop(self):
        """A caller that passes an explicit (empty) stored-set and no ban
        list — e.g. a dry probe — touches nothing: 'no scope given' means
        the rows keep their state, the file is byte-identical."""
        self._seed_other_links()
        before = self._other_links_text()
        dropped = prune_inbox_tables(
            self.vault, blocked_domains=[], stored_urls=set(),
            log_callback=self.log)
        self.assertEqual(dropped, 0)
        self.assertEqual(self._other_links_text(), before)

    def test_bare_prune_still_cleans_stored_rows(self):
        """The bare call (no ban list given) = the stored-row cleanup only:
        the coolors.co row leaves, the banned rows stay (the write path
        passes the law in, so its own prune covers them)."""
        self._seed_other_links()
        dropped = prune_inbox_tables(self.vault, log_callback=self.log)
        self.assertEqual(dropped, 1)
        text = self._other_links_text()
        self.assertNotIn('coolors.co', text)
        self.assertIn('x.com/i/status', text)

    def test_prune_respects_dry_run(self):
        self._seed_other_links()
        before = self._other_links_text()
        dryrun.enable()
        try:
            dropped = prune_inbox_tables(
                self.vault, blocked_domains=L.blocked_domains_from_config({}),
                log_callback=self.log)
            self.assertGreater(dropped, 0)
            # a rehearsal records, never performs: the file survives intact
            self.assertEqual(self._other_links_text(), before)
            self.assertGreater(dryrun.entry_count(), 0)
        finally:
            dryrun.disable()
            dryrun.clear()


# ---------------------------------------------------------------------------
# 6. The destination logs — "[Vault Name] Item X processed and stored"
# ---------------------------------------------------------------------------

class TestDestinationLogs(_Case):

    def test_websites_success_log_shows_vault_and_note_path(self):
        pipe = wp.WebsitePipeline(
            config={'website_vault_path': self.vault,
                    'web_domain_delay_s': 0},
            llm_call=_FakeLLM(), vault_index_has=lambda u: False,
            state=wp.WebsiteStateDB(
                db_path=os.path.join(self.tmp, 'cache.db')),
            fetch_fn=_FakeFetch(), log=self.log)
        r = pipe.process_link('https://example.com/tool')
        self.assertEqual(r['outcome'], 'processed')
        rel = os.path.relpath(r['note_path'], self.vault)\
            .replace(os.sep, '/')
        success = [m for lvl, m in self.logs if lvl == 'success']
        self.assertEqual(len(success), 1, self.logs)
        line = success[0]
        vault_name = os.path.basename(self.vault)
        self.assertIn(f'[{vault_name}]', line)
        self.assertIn('Test Site', line)
        self.assertIn('processed and stored', line)
        self.assertIn(f'→ {rel}', line)

    def test_github_side_log_shows_vault_and_note_path(self):
        """The GitHub pipeline's per-repo success log (the worker) —
        source-pinned: the wiring lives in ProcessingWorker._run_impl's
        note-write path."""
        import gitcurator.gui.app as ga
        src = inspect.getsource(ga.ProcessingWorker._run_impl)
        self.assertIn('processed and stored', src)
        self.assertIn('_vault_name', src)
        self.assertIn('_rel_path', src)

    def test_websites_phase_prunes_the_tables_after_the_batch(self):
        import gitcurator.gui.app as ga
        src = inspect.getsource(ga.ProcessingWorker._run_website_phase)
        self.assertIn('prune_inbox_tables(', src)
        self.assertIn('stored_urls=', src)


# ---------------------------------------------------------------------------
# 7. Wiring — the callers pass the law into the writer
# ---------------------------------------------------------------------------

class TestWiring(unittest.TestCase):

    def test_bot_queue_paths_pass_blocked_domains(self):
        import gitcurator.gui.app as ga
        for fn in ('check_bot_queue', 'process_new_bot_queue'):
            src = inspect.getsource(getattr(ga.MainWindow, fn))
            self.assertIn('blocked_domains=', src, fn)

    def test_worker_inbox_writer_passes_blocked_domains(self):
        import gitcurator.gui.app as ga
        src = inspect.getsource(ga.ProcessingWorker._create_inbox_notes)
        self.assertIn('blocked_domains=', src)

    def test_pipeline_off_intake_marks_banned_links_blocked(self):
        """With the Websites pipeline OFF, banned links used to be
        'recorded' (an _inbox row); v0.35.0 marks them 'blocked' —
        no row is ever written for them."""
        import gitcurator.gui.app as ga
        src = inspect.getsource(ga.ProcessingWorker._run_impl)
        self.assertIn('mark_blocked', src)
        self.assertIn('_recordable', src)

    def test_manifest_blocked_counts_without_inbox_rows(self):
        from gitcurator.gui.link_tracker import LinkTracker
        t = LinkTracker(self.id())     # path unused for this check
        t.manifest['links'] = [{
            'url': 'https://youtu.be/xyz', 'type': 'non-github',
            'status': 'blocked', 'note_path': None,
            'error': 'blocked domain', 'processed_at': None}]
        rep = t.verify()
        self.assertEqual(rep['blocked_recorded'], 1)
        self.assertTrue(rep['verification_passed'])
        self.assertTrue(rep['accounting_ok'])


if __name__ == '__main__':
    unittest.main()
