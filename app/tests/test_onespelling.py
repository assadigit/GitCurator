"""tests/test_onespelling.py — v0.64.0, THE ONE SPELLING: one link,
one slash apart, is one website — never processed twice.

The owner's report (session, verbatim): "One error: it processed a same
website two times , only one has slash, other doesnt :

https://cleanup.pictures/
https://cleanup.pictures"

The disease it closes: ``links.normalize_website_url`` stripped a
trailing ``/`` only when the path was LONGER than one character — the
ROOT path ``/`` survived, so ``https://cleanup.pictures/`` and
``https://cleanup.pictures`` were TWO canonical forms. Two keys in the
VaultIndex, two keys in every state ledger, two fetches, two notes,
for one site.

The law, layer by layer (all covered here — zero network, injected
fetcher + fake LLM, the house pattern):

* the normalizer — the root ``/`` is a trailing slash too: both of the
  owner's spellings fold to the bare domain; deeper paths, query
  strings and the GitHub normalizer's frozen behavior keep their own
  laws;
* the state boundary — ``WebsiteStateDB`` normalizes every URL that
  crosses into or out of a ledger method (write and probe alike), so a
  row written with one spelling answers a probe in the other — the
  tables hold one canonical key per link whatever spelling the caller
  carried;
* the healing pass — ``normalize_ledger_keys`` re-keys the historical
  rows (the owner's machine carries both spellings from the buggy
  era), merging duplicates under deterministic preferences (a live
  note beats a remembered one; more retry attempts beats fewer);
  idempotent, cheap, called from ``settle_the_ledger`` at EVERY door —
  before the meta guard, so an already-settled machine still heals;
* the pipeline — the owner's exact flow: a vault note for one spelling
  makes the OTHER spelling read "already in the websites vault" — the
  fetcher is never asked, the note is never duplicated.

No PyQt import at module level (the libEGL-less sandbox rule); the
VaultIndex cases import lazily and skip when Qt is absent.
"""

import os
import shutil
import sqlite3
import tempfile
import unittest

from gitcurator.core import dryrun
from gitcurator.core import website_pipeline as wp
from gitcurator.core import links as _links
from gitcurator.core import web_fetch as _web_fetch
from gitcurator.core.website_state import _key as state_key

import json


# -- the fakes (the test_settledledger pattern, kept local) -----------------

class _FakeFetch:
    """Canned fetch results; records every call (the law's witness)."""

    def __init__(self):
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append(url)
        html = ("<html><head><title>Cleanup Pictures</title>"
                "<meta name=\"description\" content=\"Remove objects from"
                " photos in one touch.\"></head><body><p>A tool to clean"
                " up pictures by wiping unwanted objects, with a brush and"
                " a simple editor for design work and retouching."
                "</p></body></html>")
        return _web_fetch.FetchResult(
            url=url, final_url=url, status='full', http_status=200,
            content_type='text/html', charset='utf-8',
            body=html.encode('utf-8'), text=html)


class _FakeLLM:
    """Scriptable classify/analyze answers (valid taxonomy names)."""

    def __call__(self, messages, task=None):
        text = messages[0]['content']
        if 'filing a website into a personal library' in text:
            return json.dumps({'category': 'Design',
                               'confidence': 'high', 'reason': 'testing'})
        if 'was filed under' in text:
            return json.dumps({'subcategory': 'Assets & Resources',
                               'confidence': 'high'})
        return json.dumps({
            'name': 'Cleanup Pictures', 'one_line': 'Wipe objects.',
            'core_offerings': ['One', 'Two'],
            'best_used_for': 'Use when you need to clean a photo.',
            'pricing': 'free', 'login_required': 'no',
            'similar_tools': [], 'tags': ['design'],
            'confidence': 'high'})


# The owner's exact pair, verbatim from the report.
_WITH_SLASH = 'https://cleanup.pictures/'
_NO_SLASH = 'https://cleanup.pictures'
_CANON = 'https://cleanup.pictures'

_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# 1. the normalizer itself (pure)
# ---------------------------------------------------------------------------

class TestTheNormalizer(unittest.TestCase):

    def test_the_owners_exact_pair_folds_to_one_spelling(self):
        """The report, verbatim: https://cleanup.pictures/ and
        https://cleanup.pictures processed twice — now one canonical
        form, one key, one note."""
        self.assertEqual(_links.normalize_website_url(_WITH_SLASH), _CANON)
        self.assertEqual(_links.normalize_website_url(_NO_SLASH), _CANON)

    def test_root_slash_law_in_the_source(self):
        """The fix's own words ride the module (the source contract)."""
        src = open(os.path.join(
            _REPO_ROOT, 'app', 'gitcurator', 'core', 'links.py'),
            encoding='utf-8').read()
        self.assertIn("if path == '/' and not kept:", src)
        self.assertIn("elif path == '' and kept:", src)
        self.assertIn('THE ONE SPELLING', src)

    def test_deeper_paths_keep_their_identity(self):
        self.assertEqual(
            _links.normalize_website_url('https://site.com/x/'),
            'https://site.com/x')
        self.assertEqual(
            _links.normalize_website_url('https://site.com/x'),
            'https://site.com/x')
        # double root slash folds too (one spelling, however typed)
        self.assertEqual(
            _links.normalize_website_url('https://site.com//'),
            'https://site.com')

    def test_query_and_tracking_laws_unchanged(self):
        self.assertEqual(
            _links.normalize_website_url('https://site.com/?utm_source=x'),
            'https://site.com')
        self.assertEqual(
            _links.normalize_website_url('https://site.com/?p=42'),
            'https://site.com/?p=42')
        # the bare-query spelling folds onto the pretty root form
        self.assertEqual(
            _links.normalize_website_url('https://site.com?p=42'),
            'https://site.com/?p=42')

    def test_http_www_fragment_laws_unchanged(self):
        self.assertEqual(
            _links.normalize_website_url('http://WWW.Cleanup.Pictures/#top'),
            _CANON)

    def test_github_normalizer_frozen(self):
        """SPEC §4.3.1: the GitHub/VaultIndex normalizer keeps its own
        behavior — the fix touches the WEBSITE normalizer only."""
        self.assertEqual(_links.normalize_url('https://github.com/o/r/'),
                         'https://github.com/o/r')
        self.assertEqual(_links.normalize_url('https://github.com/o/r'),
                         'https://github.com/o/r')


# ---------------------------------------------------------------------------
# 2. the state boundary (write + probe under one spelling)
# ---------------------------------------------------------------------------

class _StateCase(unittest.TestCase):

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix='spelling-')
        self.db = wp.WebsiteStateDB(
            db_path=os.path.join(self.tmp, 'cache.db'))

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestTheStateBoundary(_StateCase):

    def test_written_with_slash_probed_without(self):
        """A row written with one spelling answers a probe in the
        other — the table holds one canonical key per link."""
        self.db.mark_processed(_WITH_SLASH, 'note.md', 'Design',
                               'Assets', 'full')
        self.assertTrue(self.db.is_processed(_NO_SLASH))
        row = self.db.processed_row(_NO_SLASH)
        self.assertIsNotNone(row)
        self.assertEqual(row['note_path'], 'note.md')

    def test_written_without_slash_probed_with(self):
        self.db.mark_processed(_NO_SLASH, 'note.md', 'Design',
                               'Assets', 'full')
        self.assertTrue(self.db.is_processed(_WITH_SLASH))
        self.assertIsNotNone(self.db.processed_row(_WITH_SLASH))

    def test_retry_queue_one_spelling(self):
        self.db.enqueue_retry(_WITH_SLASH, 'HTTP 403')
        self.db.enqueue_retry(_NO_SLASH, 'HTTP 403')   # same key: attempts 2
        row = self.db.retry_row(_WITH_SLASH)
        self.assertEqual(row['attempts'], 2)
        self.db.resolve_retry(_NO_SLASH)               # either spelling clears
        self.assertIsNone(self.db.retry_row(_WITH_SLASH))

    def test_dismissed_one_spelling(self):
        self.db.dismiss(_WITH_SLASH, 'note deleted by owner')
        self.assertTrue(self.db.is_dismissed(_NO_SLASH))
        row = self.db.dismissed_row(_NO_SLASH)
        self.assertIn('deleted', row['reason'])

    def test_settled_one_spelling(self):
        self.db.settle_urls([_WITH_SLASH, _NO_SLASH, '', None])
        self.assertEqual(self.db.settled_count(), 1)
        self.assertTrue(self.db.is_settled(_NO_SLASH))
        self.assertTrue(self.db.is_settled(_WITH_SLASH))
        self.assertTrue(self.db.unsettle(_WITH_SLASH))
        self.assertFalse(self.db.is_settled(_NO_SLASH))

    def test_state_key_helper(self):
        self.assertEqual(state_key(_WITH_SLASH), _CANON)
        self.assertEqual(state_key(_NO_SLASH), _CANON)
        self.assertEqual(state_key(''), '')
        self.assertIsNone(state_key(None) or None)   # never raises


# ---------------------------------------------------------------------------
# 3. the healing pass (the historical rows, re-keyed)
# ---------------------------------------------------------------------------

class TestTheHealingPass(_StateCase):

    def _raw_insert(self, table, cols, vals):
        with self.db._lock:
            self.db.conn.execute(
                f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) "
                f"VALUES ({','.join('?' * len(cols))})", vals)
            self.db.conn.commit()

    def test_both_spellings_merge_to_one_processed_row(self):
        """The owner's machine shape: two rows for one link (the buggy
        era wrote both spellings). The pass merges them — the row whose
        note exists on disk wins."""
        alive = os.path.join(self.tmp, 'Cleanup Pictures.md')
        with open(alive, 'w', encoding='utf-8') as f:
            f.write('note')
        self._raw_insert(
            'websites_processed',
            ('url', 'note_path', 'category', 'subcategory',
             'fetch_status', 'processed_at'),
            (_WITH_SLASH, alive, 'Design', 'Assets', 'full', '2026-10-01'))
        self._raw_insert(
            'websites_processed',
            ('url', 'note_path', 'category', 'subcategory',
             'fetch_status', 'processed_at'),
            (_NO_SLASH, 'gone.md', '', '', 'failed', '2026-10-02'))
        rep = self.db.normalize_ledger_keys()
        self.assertGreaterEqual(rep['rekeyed'], 1)
        self.assertGreaterEqual(rep['merged'], 1)
        with self.db._lock:
            rows = self.db.conn.execute(
                "SELECT url, note_path FROM websites_processed").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], _CANON)
        self.assertEqual(rows[0][1], alive)   # the live note won

    def test_retry_rows_merge_by_attempts(self):
        self._raw_insert(
            'website_retry_queue',
            ('url', 'attempts', 'last_error', 'next_attempt_at',
             'first_failed_at', 'last_failed_at'),
            (_NO_SLASH, 1, 'e', '2026-10-01', '2026-10-01', '2026-10-01'))
        self._raw_insert(
            'website_retry_queue',
            ('url', 'attempts', 'last_error', 'next_attempt_at',
             'first_failed_at', 'last_failed_at'),
            (_WITH_SLASH, 3, 'e', '2026-10-03', '2026-10-01', '2026-10-03'))
        self.db.normalize_ledger_keys()
        row = self.db.retry_row(_CANON)
        self.assertEqual(row['attempts'], 3)   # closer to retirement wins

    def test_settled_and_dismissed_merge_first_wins(self):
        self._raw_insert('websites_settled',
                         ('url', 'settled_at'),
                         (_WITH_SLASH, '2026-10-05'))
        self._raw_insert('websites_settled',
                         ('url', 'settled_at'),
                         (_NO_SLASH, '2026-10-06'))
        self.db.normalize_ledger_keys()
        with self.db._lock:
            rows = self.db.conn.execute(
                "SELECT url, settled_at FROM websites_settled").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], _CANON)
        self.assertEqual(rows[0][1], '2026-10-06')   # first by URL order
        # (the no-slash spelling sorts first — prefix before extension)

    def test_idempotent_second_call(self):
        self._raw_insert('websites_settled',
                         ('url', 'settled_at'), (_WITH_SLASH, '2026-10-05'))
        first = self.db.normalize_ledger_keys()
        self.assertGreaterEqual(first['rekeyed'], 1)
        second = self.db.normalize_ledger_keys()
        self.assertEqual(second['rekeyed'], 0)
        self.assertEqual(second['merged'], 0)

    def test_no_rows_is_a_quiet_noop(self):
        rep = self.db.normalize_ledger_keys()
        self.assertEqual(rep, {'rekeyed': 0, 'merged': 0})


# ---------------------------------------------------------------------------
# 4. the door helper (settle_the_ledger heals even a settled machine)
# ---------------------------------------------------------------------------

class TestTheDoorHeals(unittest.TestCase):

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix='spelldoor-')
        self.vault = os.path.join(self.tmp, 'websites')
        os.makedirs(os.path.join(self.vault, '_review'), exist_ok=True)
        self.db = wp.WebsiteStateDB(
            db_path=os.path.join(self.tmp, 'cache.db'))
        self.logs = []

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_healing_runs_before_the_meta_guard(self):
        """A machine that settled long ago (the meta key stamped) still
        heals: a stale-spelling row written by the buggy era is re-keyed
        on the very next door — the 🩹 line says so."""
        # settle the machine once (stamps the meta key)
        self.db.settle_existing(extra_urls=[_NO_SLASH])
        self.assertTrue(self.db.is_settled(_NO_SLASH))
        # a stale row from the buggy era lands later (raw insert)
        with self.db._lock:
            self.db.conn.execute(
                "INSERT OR REPLACE INTO websites_settled (url, settled_at)"
                " VALUES (?,?)", (_WITH_SLASH, '2026-10-08'))
            self.db.conn.commit()
        rep = wp.settle_the_ledger(
            self.db, self.vault, log=lambda m, l='info': self.logs.append(m))
        self.assertTrue(rep['already'])          # the settlement itself held
        self.assertTrue(self.db.is_settled(_NO_SLASH))
        with self.db._lock:
            rows = self.db.conn.execute(
                "SELECT url FROM websites_settled").fetchall()
        self.assertEqual(sorted(r[0] for r in rows), [_CANON])
        self.assertIn('🩹', '\n'.join(self.logs))

    def test_source_contract_the_call_lives_before_the_guard(self):
        src = open(os.path.join(
            _REPO_ROOT, 'app', 'gitcurator', 'core',
            'website_pipeline.py'), encoding='utf-8').read()
        heal_at = src.find("normalize_ledger_keys', None)")
        guard_at = src.find('if state.get_meta(SETTLED_META_KEY):')
        self.assertGreater(heal_at, 0)
        self.assertGreater(guard_at, 0)
        self.assertLess(heal_at, guard_at)


# ---------------------------------------------------------------------------
# 5. the pipeline (the owner's exact flow, end to end)
# ---------------------------------------------------------------------------

class TestThePipelineOneSpelling(unittest.TestCase):

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix='spellpipe-')
        self.vault = os.path.join(self.tmp, 'websites')
        os.makedirs(os.path.join(self.vault, '_review'), exist_ok=True)
        self.db = wp.WebsiteStateDB(
            db_path=os.path.join(self.tmp, 'cache.db'))
        self.fetch = _FakeFetch()
        self.logs = []

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _pipeline(self, in_vault):
        return wp.WebsitePipeline(
            config={'website_vault_path': self.vault,
                    'web_domain_delay_s': 0},
            llm_call=_FakeLLM(),
            vault_index_has=lambda u: u in in_vault,
            state=self.db, fetch_fn=self.fetch,
            log=lambda m, l='info': self.logs.append((l, m)))

    def test_second_spelling_is_already_in_the_vault(self):
        """A note exists for one spelling → the other spelling reads
        'already in the websites vault' (and, in the same batch, as a
        duplicate of the first — one key, one skip story) — the fetcher
        is never asked twice, the note is never duplicated."""
        canon = wp.normalize_website_url(_NO_SLASH)
        in_vault = {canon}            # the index keyed by canonical form
        pipe = self._pipeline(in_vault)
        res = pipe.run([_WITH_SLASH, _NO_SLASH])
        self.assertEqual(len(res), 2)
        self.assertEqual(res[0]['outcome'], 'skipped')
        self.assertEqual(res[0]['error'], 'already in the websites vault')
        self.assertEqual(res[1]['outcome'], 'skipped')
        self.assertIn(res[1]['error'],
                      ('duplicate within batch',
                       'already in the websites vault'))
        self.assertEqual(self.fetch.calls, [])   # never fetched, not once
        self.assertEqual(pipe.counters['skipped'], 1)   # the in-vault skip;
        # the within-batch twin rides the results list (the run report's
        # own count) without a second counter — one key, one skip story

    def test_state_ledger_holds_one_row(self):
        canon = wp.normalize_website_url(_NO_SLASH)
        pipe = self._pipeline({canon})
        pipe.run([_WITH_SLASH, _NO_SLASH])
        with self.db._lock:
            rows = self.db.conn.execute(
                "SELECT url FROM websites_processed").fetchall()
        self.assertEqual(rows, [])    # skips never wrote a row — and the
        # two spellings could not have made two rows if they had

    def test_settled_pair_never_refetched(self):
        """Both spellings settled (the v0.63.2 law, now under one key):
        a re-send of either is dropped before the fetcher."""
        self.db.settle_urls([_WITH_SLASH])
        pipe = self._pipeline(set())
        res = pipe.run([_WITH_SLASH, _NO_SLASH])
        for r in res:
            self.assertEqual(r['outcome'], 'skipped', r)
        self.assertEqual(self.fetch.calls, [])
        self.assertEqual(self.db.settled_count(), 1)


# ---------------------------------------------------------------------------
# 6. the VaultIndex (the real keying, lazily imported)
# ---------------------------------------------------------------------------

class TestTheVaultIndexKeys(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='spellidx-')
        self.vault = os.path.join(self.tmp, 'websites')
        os.makedirs(os.path.join(self.vault, 'Design', 'Assets'),
                    exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _index(self):
        try:
            from gitcurator.gui.vault_index import VaultIndex
        except Exception as e:      # Qt absent — the sandbox rule
            self.skipTest(f'VaultIndex not importable here: {e}')
        idx = VaultIndex(self.vault,
                         normalizer=_links.normalize_website_url)
        idx.rebuild()
        return idx

    def test_one_note_answers_both_spellings(self):
        path = os.path.join(self.vault, 'Design', 'Assets',
                            'Cleanup Pictures.md')
        note = wp.build_website_note(
            _NO_SLASH,
            {'name': 'Cleanup Pictures', 'one_line': 'Wipe objects.',
             'what_it_does': ['Wipe'], 'best_used_for': 'Cleaning.',
             'pricing': 'free', 'login_required': 'no', 'tags': []},
            'Design', 'Assets & Resources', 'full')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(note)
        idx = self._index()
        self.assertTrue(idx.has_url(_WITH_SLASH))
        self.assertTrue(idx.has_url(_NO_SLASH))
        self.assertEqual(idx.get_path(_WITH_SLASH), path)
        self.assertEqual(idx.count, 1)   # one note, not two


# ---------------------------------------------------------------------------
# 8. release bookkeeping (the house ritual)
# ---------------------------------------------------------------------------

class TestReleaseBookkeeping(unittest.TestCase):

    def setUp(self):
        self.root = _REPO_ROOT

    def _read(self, *parts):
        with open(os.path.join(self.root, *parts), 'r',
                  encoding='utf-8') as f:
            return f.read()

    def test_version_is_0640(self):
        self.assertEqual(self._read('VERSION').strip(), '0.64.0')

    def test_changelog_has_the_spelling(self):
        text = self._read('CHANGELOG.md')
        self.assertIn('## [0.64.0]', text)
        self.assertIn('THE ONE SPELLING', text)
        flat = ' '.join(text.split())
        self.assertIn('only one has slash', flat)   # the report, verbatim

    def test_ci_and_agents_know_the_module(self):
        ci = self._read('.github', 'workflows', 'ci.yml')
        self.assertIn('tests.test_onespelling', ci)
        agents = self._read('AGENTS.md')
        self.assertIn('tests.test_onespelling', agents)


if __name__ == '__main__':      # pragma: no cover
    unittest.main()
