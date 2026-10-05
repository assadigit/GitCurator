#!/usr/bin/env python3
"""
test_lawfix.py — v0.28.0: THE LAW + the Website Directory.

The owner's law (2026-10-01): "EVERY X And GITHUB domain (all of its
group) must be banned from showing on websites directory. … This 3 are
forbiddan: Hugginface, Github, Twitter (X), Instagram, Facebook,
Linkedin." — plus the companion feature: "consolidated categorized
lists … a directory which I can easily click on their link, knowing a
short description about them."

Covered here:
  1. links.LAW_BLOCKED_DOMAINS / LAW_PLATFORM_DOMAINS semantics (the
     union floor; the platform half keeps GitHub repo links flowing)
  2. website_directory.sweep_banned_notes — the legacy x_com_i_status_*
     _review pile leaves the vault for .trash/banned-domains; hand-
     written notes and _inbox tables are sacred; idempotent; dry-run safe
  3. website_directory.build_website_directory — the categorized
     clickable index (taxonomy order, wiki-links, TL;DR, pricing, ↗)
  4. the production pipeline constructor runs the sweep
  5. the bot-queue split (repo filter = platform law, websites = full)
  6. VaultIndex / note_state / mirror skip .trash

All against LOCAL temp dirs / stubs — no network, no GUI shown.
"""

import os
import shutil
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gitcurator.core import links as L
from gitcurator.core import dryrun
from gitcurator.core import website_pipeline as wp
from gitcurator.core import website_directory as wd
from gitcurator.core import mirror as mr
from gitcurator.core import note_state as ns
from gitcurator.core.taxonomy import load_taxonomy_from_config


def _write_note(path, url, category='', subcategory='', pricing='unknown',
                title='Untitled', tldr='', managed=True,
                fetch_status='full'):
    """Write a v2-shaped website note (the pipeline's own format)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fm = [
        '---',
        f'source: "{url}"',
        'aliases: []',
        'tags: []',
        f'category: "{category}"',
        f'subcategory: "{subcategory}"',
        f'fetch_status: "{fetch_status}"',
        f'pricing: "{pricing}"',
        'login_required: "unknown"',
        'date_processed: 2026-10-01',
        f'managed_by: "{("gitcurator" if managed else "human")}"',
        'schema_version: "1"',
        'prompt_version: "web-v2"',
        '---',
        '',
        '> [!info] Managed by GitCurator — machine-written note.',
        '',
        f'# {title}',
        '',
        f'> **TL;DR:** {tldr}',
        '',
        '## What it does',
        '',
        f'- {tldr or title}',
        '',
        '## Best used for',
        'Use when you need to…',
        '',
        '## Similar tools',
        '—',
        '',
        f'---',
        f'*Source: [{url}]({url})*',
        '',
    ]
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(fm))


class _VaultCase(unittest.TestCase):
    """A temp websites vault + captured logs."""

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix='lawfix-')
        self.vault = os.path.join(self.tmp, 'webvault')
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

    def note(self, rel):
        return os.path.join(self.vault, *rel.split('/'))

    def quarantine(self):
        return os.path.join(self.vault, '.trash', 'banned-domains')


# ---------------------------------------------------------------------------
# 1. The sweep — the legacy x_com_i_status_* pile leaves the vault
# ---------------------------------------------------------------------------

class TestLawSweep(_VaultCase):

    def test_sweep_moves_the_legacy_review_pile(self):
        """The owner's exact screenshot: a wall of x_com_i_status_*.md
        files under _review — every one leaves the library."""
        pile = []
        for i, sid in enumerate(('2011934783064826', '2011943712111707',
                                 '2011947543668256')):
            p = self.note(f'_review/x_com_i_status_{sid}.md')
            _write_note(p, f'https://x.com/i/status/{sid}',
                        fetch_status='failed', title=f'tweet {i}')
            pile.append(p)
        _write_note(self.note('Design/Assets_Resources/Real_Site.md'),
                    'https://reverseui.com/', category='Design',
                    subcategory='Assets & Resources', title='Real Site')
        report = wd.sweep_banned_notes(
            self.vault, L.blocked_domains_from_config({}), log=self.log)
        self.assertEqual(len(report['moved']), 3)
        for p in pile:
            self.assertFalse(os.path.exists(p), p)
            q = os.path.join(self.quarantine(), os.path.basename(p))
            self.assertTrue(os.path.exists(q), q)
        # the real site is untouched
        self.assertTrue(os.path.exists(
            self.note('Design/Assets_Resources/Real_Site.md')))
        self.assertIn('Law sweep', self.all_logs())

    def test_sweep_moves_real_category_notes_on_law_domains(self):
        """The law has no 'but it processed fine' exception — a REAL note
        on t.co / huggingface / instagram leaves too."""
        for rel, url in (
                ('Design/Tools/tco_note.md', 'https://t.co/abc'),
                ('AI_Tools_(General)/hf_note.md',
                 'https://huggingface.co/org/model'),
                ('Random__Curiosities/ig_note.md',
                 'https://www.instagram.com/p/xyz/')):
            _write_note(self.note(rel), url, title='x')
        report = wd.sweep_banned_notes(
            self.vault, L.blocked_domains_from_config({}), log=self.log)
        self.assertEqual(len(report['moved']), 3)
        self.assertEqual(report['kept_handwritten'], 0)

    def test_sweep_keeps_handwritten_notes_and_counts_them(self):
        """The owner's own writing is sacred — counted + reported, never
        moved."""
        _write_note(self.note('Notes/my_own_x_thread.md'),
                    'https://x.com/thread', managed=False, title='My notes')
        report = wd.sweep_banned_notes(
            self.vault, L.blocked_domains_from_config({}), log=self.log)
        self.assertEqual(report['moved'], [])
        self.assertEqual(report['kept_handwritten'], 1)
        self.assertTrue(os.path.exists(
            self.note('Notes/my_own_x_thread.md')))
        self.assertIn('hand-written', self.all_logs())

    def test_sweep_quarantines_banned_platform_tables(self):
        """v0.35.0 — the owner's omission rule ("must not collect youtube
        links for note or review, same for X, and hugging face"): a
        platform whose EVERY domain is banned has its _inbox table
        quarantined whole; partially-banned platforms and the 'other'
        catch-all keep theirs (their banned ROWS are pruned by the intake
        writer's prune pass instead)."""
        inbox = self.note('_inbox')
        os.makedirs(inbox, exist_ok=True)
        for fname in ('x_twitter_links.md', 'youtube_links.md',
                      'linkedin_links.md', 'huggingface_links.md',
                      'reddit_links.md', 'medium_links.md',
                      'other_links.md'):
            with open(os.path.join(inbox, fname), 'w',
                      encoding='utf-8') as f:
                f.write(f'# {fname}\n\n| - | 2026-10-05 | '
                        f'https://{fname.split("_")[0]}.example/x '
                        f'| x | Bot | unreviewed | |\n')
        report = wd.sweep_banned_notes(
            self.vault, L.blocked_domains_from_config({}), log=self.log)
        # the four fully-banned platforms' tables left the vault…
        self.assertEqual(
            sorted(t[0] for t in report['moved_tables']),
            ['huggingface', 'linkedin', 'x_twitter', 'youtube'])
        for platform in ('x_twitter', 'youtube', 'linkedin',
                         'huggingface'):
            self.assertFalse(os.path.exists(
                os.path.join(inbox, f'{platform}_links.md')), platform)
            self.assertTrue(os.path.exists(
                os.path.join(self.quarantine(),
                             f'{platform}_links.md')), platform)
        # …while reddit (allowed), medium (allowed) and the 'other'
        # catch-all stay untouched in place.
        for fname in ('reddit_links.md', 'medium_links.md',
                      'other_links.md'):
            self.assertTrue(os.path.exists(os.path.join(inbox, fname)),
                            fname)
        self.assertIn('never collected', self.all_logs())

    def test_sweep_keeps_a_partially_banned_platforms_table(self):
        """A platform with only SOME domains banned keeps its table — the
        ban list decides, not the platform's existence."""
        inbox = self.note('_inbox')
        os.makedirs(inbox, exist_ok=True)
        table = os.path.join(inbox, 'reddit_links.md')
        with open(table, 'w', encoding='utf-8') as f:
            f.write('# reddit\n')
        report = wd.sweep_banned_notes(
            self.vault, ['x.com', 't.co', 'twitter.com', 'youtube.com'],
            log=self.log)
        self.assertEqual(report['moved_tables'], [])
        self.assertTrue(os.path.exists(table))

    def test_sweep_is_idempotent(self):
        _write_note(self.note('_review/x_com_1.md'), 'https://x.com/1')
        wd.sweep_banned_notes(
            self.vault, L.blocked_domains_from_config({}), log=self.log)
        second = wd.sweep_banned_notes(
            self.vault, L.blocked_domains_from_config({}), log=self.log)
        self.assertEqual(second['moved'], [])
        self.assertEqual(len(os.listdir(self.quarantine())), 1)

    def test_sweep_respects_dry_run(self):
        dryrun.enable()
        try:
            _write_note(self.note('_review/x_com_1.md'), 'https://x.com/1')
            report = wd.sweep_banned_notes(
                self.vault, L.blocked_domains_from_config({}), log=self.log)
            self.assertEqual(len(report['moved']), 1)
            # a rehearsal records, never performs: the file survives
            self.assertTrue(os.path.exists(self.note('_review/x_com_1.md')))
            self.assertFalse(os.path.exists(self.quarantine()))
            self.assertGreater(dryrun.entry_count(), 0)
        finally:
            dryrun.disable()
            dryrun.clear()

    def test_sweep_name_collision_gets_unique_names(self):
        _write_note(self.note('_review/x_com_1.md'), 'https://x.com/1')
        _write_note(self.note('Design/x_com_1.md'), 'https://x.com/1b')
        report = wd.sweep_banned_notes(
            self.vault, L.blocked_domains_from_config({}), log=self.log)
        self.assertEqual(len(report['moved']), 2)
        names = sorted(os.listdir(self.quarantine()))
        self.assertEqual(len(names), 2)
        self.assertNotEqual(names[0], names[1])

    def test_sweep_on_missing_vault_is_a_no_op(self):
        report = wd.sweep_banned_notes(
            os.path.join(self.tmp, 'nope'), ['x.com'], log=self.log)
        self.assertEqual(report, {'moved': [], 'kept_handwritten': 0,
                                  'scanned': 0, 'moved_tables': []})


# ---------------------------------------------------------------------------
# 2. The Website Directory — consolidated, categorized, clickable
# ---------------------------------------------------------------------------

class TestWebsiteDirectory(_VaultCase):

    def _seed(self):
        _write_note(
            self.note('Design/Assets_Resources/Reverse_UI.md'),
            'https://reverseui.com/', category='Design',
            subcategory='Assets & Resources', pricing='free',
            title='Reverse UI',
            tldr='68 animated web UI components, copy-paste Tailwind')
        _write_note(
            self.note('Design/Assets_Resources/Coolors.md'),
            'https://coolors.co/', category='Design',
            subcategory='Assets & Resources', pricing='freemium',
            title='Coolors',
            tldr='Color palette maker with generators and export')
        _write_note(
            self.note('Security_Privacy_Tools/HaveIBeenPwned.md'),
            'https://haveibeenpwned.com/', category='Security & Privacy Tools',
            pricing='paid', title='HaveIBeenPwned',
            tldr='Breach checker for your accounts')
        _write_note(
            self.note('Random__Curiosities/Curiosity.md'),
            'https://curiosity.example/', category='Random / Curiosities',
            title='A Curiosity', tldr='Odd but lovely')
        # NOT in the directory: review placeholder, hand-written, law note
        _write_note(self.note('_review/Waiting_Site.md'),
                    'https://waitingsite.com/', fetch_status='failed',
                    title='Waiting Site')
        _write_note(self.note('Notes/my_own.md'), 'https://myblog.example/',
                    managed=False, title='My own writing')
        _write_note(self.note('Design/Assets_Resources/x_note.md'),
                    'https://x.com/thread', title='An X thread')
        self.taxonomy = load_taxonomy_from_config({})

    def test_directory_groups_by_taxonomy_order_not_alphabetical(self):
        """'Security & Privacy Tools' (taxonomy position 9) must come
        BEFORE 'Random / Curiosities' (taxonomy position 14, last) —
        alphabetical order would flip them."""
        self._seed()
        out = wd.build_website_directory(self.vault, taxonomy=self.taxonomy,
                                         log=self.log)
        self.assertIsNotNone(out)
        with open(out['path'], encoding='utf-8') as f:
            text = f.read()
        self.assertLess(text.index('## Security & Privacy Tools'),
                        text.index('## Random / Curiosities'))
        self.assertLess(text.index('## Design'),
                        text.index('## Security & Privacy Tools'))

    def test_directory_entries_are_clickable_with_description(self):
        self._seed()
        out = wd.build_website_directory(self.vault, taxonomy=self.taxonomy,
                                         log=self.log)
        with open(out['path'], encoding='utf-8') as f:
            text = f.read()
        # wiki-link to the note (forward slashes) + alias + TL;DR + pricing
        # + the live external link — one scannable line
        self.assertIn(
            '[[Design/Assets_Resources/Reverse_UI|Reverse UI]] — '
            '68 animated web UI components, copy-paste Tailwind — free — '
            '[↗](https://reverseui.com/)', text)
        self.assertIn(
            '[[Design/Assets_Resources/Coolors|Coolors]] — '
            'Color palette maker with generators and export — freemium — '
            '[↗](https://coolors.co/)', text)
        self.assertIn('### Assets & Resources (2)', text)
        self.assertIn('## Design (2)', text)
        self.assertIn('paid', text)          # HaveIBeenPwned pricing
        self.assertIn('[↗](https://curiosity.example/)', text)

    def test_directory_excludes_review_handwritten_and_law_notes(self):
        self._seed()
        out = wd.build_website_directory(self.vault, taxonomy=self.taxonomy,
                                         log=self.log)
        with open(out['path'], encoding='utf-8') as f:
            text = f.read()
        self.assertNotIn('Waiting Site', text)        # _review
        self.assertNotIn('My own writing', text)      # hand-written
        self.assertNotIn('An X thread', text)         # law domain
        # but the review count is reported — nothing silently missing
        self.assertIn('> [!todo]', text)
        self.assertIn('1 link(s) still waiting', text)
        self.assertEqual(out['sites'], 4)
        self.assertEqual(out['review'], 1)

    def test_directory_regenerates_identically(self):
        self._seed()
        one = wd.build_website_directory(self.vault, taxonomy=self.taxonomy,
                                         log=self.log)
        with open(one['path'], encoding='utf-8') as f:
            text1 = f.read()
        two = wd.build_website_directory(self.vault, taxonomy=self.taxonomy,
                                         log=self.log)
        with open(two['path'], encoding='utf-8') as f:
            text2 = f.read()
        self.assertEqual(text1, text2)

    def test_directory_file_is_not_a_dedupe_key(self):
        """The directory note has no source: line, so the VaultIndex
        dedupe layer never indexes it (it is an index, not a site)."""
        self._seed()
        out = wd.build_website_directory(self.vault, taxonomy=self.taxonomy,
                                         log=self.log)
        from gitcurator.gui import app as ga
        vi = ga.VaultIndex(self.vault,
                           normalizer=L.normalize_website_url)
        vi.rebuild(log_signal=None)
        # 4 real sites + 1 review + 1 handwritten + 1 law note = 7
        # source-bearing .md files — the DIRECTORY file is not one of them.
        self.assertEqual(vi.count, 7)
        self.assertFalse(vi.has_url('https://not-in-vault.example/'))

    def test_directory_on_empty_vault_returns_none(self):
        self.assertIsNone(wd.build_website_directory(
            self.vault, taxonomy=load_taxonomy_from_config({}),
            log=self.log))

    def test_directory_long_tldr_is_trimmed_at_a_word_boundary(self):
        _write_note(
            self.note('Design/Tools/Wordy.md'),
            'https://wordy.example/', category='Design',
            title='Wordy',
            tldr='word ' * 60)
        out = wd.build_website_directory(self.vault,
                                         taxonomy=load_taxonomy_from_config({}),
                                         log=self.log)
        with open(out['path'], encoding='utf-8') as f:
            text = f.read()
        line = [ln for ln in text.splitlines() if 'Wordy' in ln][0]
        self.assertIn('…', line)
        self.assertLess(len(line), 260)


# ---------------------------------------------------------------------------
# 3. The pipeline constructor runs the sweep (production only)
# ---------------------------------------------------------------------------

class TestPipelineSweepIntegration(_VaultCase):

    def test_production_constructor_sweeps_and_purges(self):
        """End-to-end: a production-mode pipeline construction (what
        every batch does first) clears a seeded legacy pile — DB-known
        _review placeholders are purged+deleted by the v0.20.0 bookkeeping
        pass (the _inbox/D1 row is the record), and any file the DB does
        NOT know is quarantined by the v0.28.0 sweep. Either way the
        library is clean and the URL can never come back."""
        db = wp.WebsiteStateDB(db_path=os.path.join(self.tmp, 'cache.db'))
        try:
            # (a) DB-known legacy placeholder — purged + deleted
            review = self.note('_review/x_com_i_status_2011934783064826.md')
            _write_note(review, 'https://x.com/i/status/2011934783064826',
                        fetch_status='failed', title='tweet')
            db.enqueue_retry('https://x.com/i/status/2011934783064826',
                             'refused')
            db.mark_processed('https://x.com/i/status/2011934783064826',
                              review, '', '', 'failed')
            # (b) DB-unknown legacy note (cache was cleared once) — swept
            orphan = self.note('Design/Assets_Resources/hf_note.md')
            _write_note(orphan, 'https://huggingface.co/org/model',
                        category='Design', subcategory='Assets & Resources',
                        title='An HF model')
            pipe = wp.WebsitePipeline(
                config={'website_vault_path': self.vault,
                        'web_domain_delay_s': 0},
                llm_call=lambda *a, **k: '{}',
                vault_index_has=lambda u: False, state=db,
                log=self.log)          # fetch_fn=None → PRODUCTION path
            # (a) gone — purged + deleted by the bookkeeping pass
            self.assertFalse(os.path.exists(review))
            # (b) gone — moved to the quarantine by the law sweep
            self.assertFalse(os.path.exists(orphan))
            self.assertTrue(os.path.exists(os.path.join(
                self.quarantine(), 'hf_note.md')))
            # …the bookkeeping agrees on both…
            self.assertTrue(db.is_dismissed(
                'https://x.com/i/status/2011934783064826'))
            self.assertIsNone(db.retry_row(
                'https://x.com/i/status/2011934783064826'))
            # …and the run log says so in plain words.
            self.assertIn('Law sweep', self.all_logs())
            self.assertIn('purged 1 queued retry', self.all_logs())
            self.assertEqual(pipe.counters['skipped'], 0)
        finally:
            db.close()

    def test_hermetic_constructor_does_not_sweep(self):
        """An injected fetch_fn (tests / golden run) never touches the
        vault — hermetic stays hermetic."""
        db = wp.WebsiteStateDB(db_path=os.path.join(self.tmp, 'cache.db'))
        try:
            review = self.note('_review/x_com_1.md')
            _write_note(review, 'https://x.com/1')
            wp.WebsitePipeline(
                config={'website_vault_path': self.vault,
                        'web_domain_delay_s': 0},
                llm_call=lambda *a, **k: '{}',
                vault_index_has=lambda u: False, state=db,
                fetch_fn=lambda url, **kw: (_ for _ in ()).throw(
                    AssertionError('never fetched')),
                log=self.log)
            self.assertTrue(os.path.exists(review))
            self.assertFalse(os.path.exists(self.quarantine()))
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 4. The bot-queue split — GitHub repo links keep flowing
# ---------------------------------------------------------------------------

class TestBotQueueLawSplit(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='lawq-')
        import gitcurator.gui.worker_jobs as wj
        self.wj = wj
        self.gh_vault = os.path.join(self.tmp, 'gh')
        self.web_vault = os.path.join(self.tmp, 'web')
        os.makedirs(self.gh_vault)
        os.makedirs(self.web_vault)
        self.lines = []
        self.log = type('Log', (), {
            'emit': staticmethod(lambda msg, level='info':
                                 self.lines.append(msg))})()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, urls, non_github, blocked, website_blocked='unset'):
        fake = {'success': True, 'urls': urls, 'non_github_urls': non_github,
                'total_messages': 1, 'bot_queue': True,
                'max_message_id': 1, 'raw_url_count': len(urls) + len(non_github),
                'duplicates_removed': 0, 'min_id_used': 0}
        kwargs = {}
        if website_blocked != 'unset':
            kwargs['website_blocked_domains'] = website_blocked
        with mock.patch.object(self.wj, '_run_telegram_worker',
                               return_value=fake):
            return self.wj._bot_queue_job(
                123, 'hash', '+98…', {'enabled': False},
                'githubfetcherbot', self.log,
                vault_path=self.gh_vault,
                blocked_domains=blocked,
                self_domains=[],
                website_vault_path=self.web_vault,
                websites_pipeline_on=True,
                **kwargs)

    def test_split_github_flows_gists_blocked(self):
        """The wiring the GUI/CLI now use: platform law for the repo
        filter, full law for the websites filter."""
        res = self._run(
            ['https://github.com/owner/repo'],
            ['https://gist.github.com/o/abc123', 'https://x.com/1',
             'https://example.com/x'],
            blocked=L.platform_domains_from_config({}),
            website_blocked=L.blocked_domains_from_config({}))
        # the GitHub repo link is PENDING — never blocked by the law
        self.assertEqual(res['pending_urls'], ['https://github.com/owner/repo'])
        self.assertEqual(res['blocked_count'], 0)
        # gists + x.com are blocked at the websites layer; example.com flows
        self.assertEqual(res['websites_blocked_count'], 2)
        self.assertEqual(res['pending_website_urls'],
                         ['https://example.com/x'])

    def test_the_old_single_list_would_have_blocked_github(self):
        """Regression documentation: passing the FULL law as the repo
        filter (the pre-split wiring) blanket-bans github.com — this is
        exactly why the platform half exists."""
        res = self._run(
            ['https://github.com/owner/repo'], [],
            blocked=L.blocked_domains_from_config({}))   # full law, no split
        self.assertEqual(res['blocked_count'], 1)
        self.assertEqual(res['pending_urls'], [])


# ---------------------------------------------------------------------------
# 5. The .trash skip lists — quarantined notes stop counting everywhere
# ---------------------------------------------------------------------------

class TestTrashSkipLists(unittest.TestCase):

    def test_vault_index_skips_trash(self):
        import gitcurator.gui.app as ga
        with tempfile.TemporaryDirectory(prefix='vi-') as tmp:
            vault = os.path.join(tmp, 'v')
            _write_note(os.path.join(vault, 'Design', 'Real.md'),
                        'https://example.com/')
            _write_note(os.path.join(vault, '.trash', 'banned-domains',
                                     'x_com_1.md'), 'https://x.com/1')
            vi = ga.VaultIndex(vault, normalizer=L.normalize_website_url)
            vi.rebuild(log_signal=None)
            self.assertEqual(vi.count, 1)
            self.assertFalse(vi.has_url('https://x.com/1'))

    def test_note_state_and_mirror_skip_trash(self):
        self.assertTrue(ns._skip_dir('/vault/.trash/banned-domains'))
        self.assertTrue(any(m in '/vault/.trash/banned-domains'
                            for m in mr.SKIPPED_FOLDER_MARKS))
        self.assertFalse(ns._skip_dir('/vault/Design'))
        self.assertFalse(ns._skip_dir('/vault/_review'))   # unchanged rule


if __name__ == '__main__':
    unittest.main()
