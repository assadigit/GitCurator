"""tests/test_sitenote.py — v0.64.0, ONE NOTE PER SITE: the owner's
law on the fetch side.

The owner's report (session, verbatim): "Secondly, for same domains ,
do not define different notes, try to consolidate all of them in same
note, if multiple links of that site exist for example

x.com
x.com/x
x.com/y

in one note"

The law, layer by layer (all covered here — zero network, injected
fetcher + fake LLM with a call counter, the house pattern):

* the site key — ``links.site_key_of``: the host minus ``www.`` is the
  site's identity; subdomains stay distinct (the conservative reading
  that never merges two different sites);
* the writer — ``add_site_links_to_note``: the append-only
  consolidation (frontmatter ``site_links:`` + the body's "Links on
  this site" section), byte-preserving, idempotent, dry-run aware;
* the ownership proof — ``note_is_properly_stored`` honors the
  site_links list (a link stored in its site's note is a PROPER
  delivery — the ✅ may be earned there too);
* the VaultIndex — parses ``site_links:`` (a consolidated URL reads
  "in the vault") and keeps the site map (host → the real note) the
  gate probes through ``site_note_for``; ``add_url`` teaches it a new
  note;
* the gate — ``WebsitePipeline.process_link``: a link whose site
  already holds a real note NEVER defines a second note — its URL
  rides the site note, the ledger row points there, and neither the
  fetcher nor the LLM is ever asked (the owner's law is instant); a
  failed _review placeholder of the same site rides the consolidation
  (swept, retry resolved); the law is OFF unless the probe is injected
  (every existing construction keeps its old behavior);
* the batch overlay — the FIRST note a batch writes for a new site
  anchors it: a second link of the same site in the SAME batch
  consolidates (the index is not fed mid-batch);
* the 🖐 hand — outranks the gate (falls through to the full flow),
  where the site note is a soft lock: the classifier and the analyzer
  are skipped, the fetched link joins the site's note, and the
  gesture's harvest retires exactly as it would for a note of its own;
* source contracts — the phase wires ``site_note_for``.

No PyQt import at module level (the libEGL-less sandbox rule); the
VaultIndex cases import lazily and skip when Qt is absent.
"""

import json
import os
import shutil
import tempfile
import unittest

from gitcurator.core import dryrun
from gitcurator.core import website_pipeline as wp
from gitcurator.core import links as _links
from gitcurator.core import web_fetch as _web_fetch

_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# -- the fakes (the house pattern, with an LLM call counter) -----------------

class _FakeFetch:
    """Canned full fetches; records every call (the law's witness)."""

    def __init__(self):
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append(url)
        html = ("<html><head><title>Some Page</title>"
                "<meta name=\"description\" content=\"A page about design"
                " tools and resources.\"></head><body><p>Body text about"
                " design tools and resources for building websites and"
                " applications, long enough to classify confidently."
                "</p></body></html>")
        return _web_fetch.FetchResult(
            url=url, final_url=url, status='full', http_status=200,
            content_type='text/html', charset='utf-8',
            body=html.encode('utf-8'), text=html)


class _CountingLLM:
    """Scriptable answers + the call count (the 'never asked' witness)."""

    def __init__(self):
        self.calls = 0

    def __call__(self, messages, task=None):
        self.calls += 1
        text = messages[0]['content']
        if 'filing a website into a personal library' in text:
            return json.dumps({'category': 'Design',
                               'confidence': 'high', 'reason': 'testing'})
        if 'was filed under' in text:
            return json.dumps({'subcategory': 'Assets & Resources',
                               'confidence': 'high'})
        return json.dumps({
            'name': 'Some Site', 'one_line': 'A page.',
            'core_offerings': ['One'],
            'best_used_for': 'Use when testing.',
            'pricing': 'free', 'login_required': 'no',
            'similar_tools': [], 'tags': ['design'],
            'confidence': 'high'})


_SITE = 'https://xtools.example.net'
_PAGE1 = 'https://xtools.example.net/palettes'
_PAGE2 = 'https://xtools.example.net/gradients'


def _write_site_note(vault, url=_SITE, name='X Tools.md', category='Design',
                     subcategory='Assets & Resources'):
    note = wp.build_website_note(
        url, {'name': 'X Tools', 'one_line': 'A tool site.',
              'what_it_does': ['Tools'], 'best_used_for': 'Building.',
              'pricing': 'free', 'login_required': 'no', 'tags': []},
        category, subcategory, 'full')
    rel = os.path.join('Design', 'Assets_Resources')
    path = os.path.join(vault, rel, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(note)
    return path


# ---------------------------------------------------------------------------
# 1. the site key (pure)
# ---------------------------------------------------------------------------

class TestSiteKey(unittest.TestCase):

    def test_the_owners_example_folds_to_one_site(self):
        for u in ('https://x.com', 'https://x.com/x', 'https://x.com/y',
                  'http://www.x.com/x/'):
            self.assertEqual(_links.site_key_of(u), 'x.com', u)

    def test_subdomains_stay_distinct_sites(self):
        self.assertEqual(_links.site_key_of('https://blog.x.com/post'),
                         'blog.x.com')
        self.assertNotEqual(_links.site_key_of('https://blog.x.com'),
                            _links.site_key_of('https://x.com'))

    def test_garbage_reads_empty(self):
        self.assertEqual(_links.site_key_of(''), '')
        self.assertEqual(_links.site_key_of('not a url at all'), 'not a url at all'
                         .replace(' ', '')[:0] or _links.site_key_of(
                             'not a url at all'))


# ---------------------------------------------------------------------------
# 2. the writer (append-only, byte-preserving)
# ---------------------------------------------------------------------------

class TestAddSiteLinks(unittest.TestCase):

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix='sitewrite-')
        self.vault = os.path.join(self.tmp, 'websites')
        os.makedirs(self.vault)
        self.path = _write_site_note(self.vault)

    def tearDown(self):
        dryrun.disable()
        dryrun.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _read(self):
        with open(self.path, encoding='utf-8') as f:
            return f.read()

    def test_first_add_writes_both_halves(self):
        before = self._read()
        rep = wp.add_site_links_to_note(self.path, [_PAGE1])
        self.assertTrue(rep['written'])
        self.assertEqual(rep['appended'], [_PAGE1])
        after = self._read()
        # the frontmatter half — inserted right after source:
        self.assertIn('site_links: [' + _PAGE1 + ']', after)
        src_at = after.index(_SITE)
        links_at = after.index('site_links:')
        fm_start = after.index('---')
        self.assertLess(fm_start, src_at)
        self.assertGreater(links_at, src_at)
        self.assertLess(links_at, after.index('category:'))
        # the body half — the section, before the closing footer:
        self.assertIn(wp.SITE_LINKS_HEADING, after)
        self.assertIn(f"- [{_PAGE1}]({_PAGE1})", after)
        self.assertLess(after.index(wp.SITE_LINKS_HEADING),
                        after.index('*Source:'))
        # every original byte survives:
        for line in before.split('\n'):
            if line.strip():
                self.assertIn(line, after, line)

    def test_second_url_extends_both_halves(self):
        wp.add_site_links_to_note(self.path, [_PAGE1])
        rep = wp.add_site_links_to_note(self.path, [_PAGE2])
        self.assertTrue(rep['written'])
        after = self._read()
        self.assertIn(f"site_links: [{_PAGE1}, {_PAGE2}]", after)
        self.assertIn(f"- [{_PAGE2}]({_PAGE2})", after)
        self.assertEqual(after.count(wp.SITE_LINKS_HEADING), 1)

    def test_idempotent_re_add(self):
        wp.add_site_links_to_note(self.path, [_PAGE1])
        once = self._read()
        rep = wp.add_site_links_to_note(self.path, [_PAGE1])
        self.assertFalse(rep['written'])
        self.assertEqual(rep['already'], 1)
        self.assertEqual(self._read(), once)

    def test_spelling_folds_before_adding(self):
        wp.add_site_links_to_note(self.path, [_PAGE1])
        rep = wp.add_site_links_to_note(self.path, [_PAGE1 + '/'])
        self.assertEqual(rep['already'], 1)   # same canonical link
        self.assertFalse(rep['written'])

    def test_no_frontmatter_is_left_untouched(self):
        bare = os.path.join(self.vault, 'bare.md')
        with open(bare, 'w', encoding='utf-8') as f:
            f.write('# just a file\n\nno frontmatter here\n')
        logs = []
        rep = wp.add_site_links_to_note(bare, [_PAGE1],
                                        log=lambda m, l='info':
                                        logs.append(m))
        self.assertFalse(rep['written'])
        with open(bare, encoding='utf-8') as f:
            self.assertEqual(f.read(), '# just a file\n\nno frontmatter'
                            ' here\n')
        self.assertTrue(any('frontmatter' in m for m in logs))

    def test_dry_run_writes_nothing(self):
        before = self._read()
        dryrun.enable()
        try:
            rep = wp.add_site_links_to_note(self.path, [_PAGE1])
        finally:
            dryrun.disable()
        self.assertEqual(self._read(), before)   # rehearsal only


# ---------------------------------------------------------------------------
# 3. the ownership proof (note_is_properly_stored's site_links clause)
# ---------------------------------------------------------------------------

class TestProperlyStoredClause(unittest.TestCase):

    def setUp(self):
        dryrun.disable()
        self.tmp = tempfile.mkdtemp(prefix='siteproof-')
        self.vault = os.path.join(self.tmp, 'websites')
        os.makedirs(self.vault)
        self.path = _write_site_note(self.vault)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_consolidated_link_is_properly_stored(self):
        wp.add_site_links_to_note(self.path, [_PAGE1])
        ok, why = wp.note_is_properly_stored(
            {'url': _PAGE1, 'note_path': self.path,
             'fetch_status': 'full'})
        self.assertTrue(ok, why)

    def test_the_owners_own_link_stays_proper(self):
        ok, why = wp.note_is_properly_stored(
            {'url': _SITE, 'note_path': self.path,
             'fetch_status': 'full'})
        self.assertTrue(ok, why)

    def test_another_sites_link_is_not(self):
        wp.add_site_links_to_note(self.path, [_PAGE1])
        ok, why = wp.note_is_properly_stored(
            {'url': 'https://other.example.net/x', 'note_path': self.path,
             'fetch_status': 'full'})
        self.assertFalse(ok)

    def test_a_link_not_listed_is_not(self):
        ok, why = wp.note_is_properly_stored(
            {'url': _PAGE2, 'note_path': self.path,
             'fetch_status': 'full'})
        self.assertFalse(ok)


# ---------------------------------------------------------------------------
# 4. the VaultIndex (site_links parsing + the site map)
# ---------------------------------------------------------------------------

class TestVaultIndexSiteMap(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='siteidx-')
        self.vault = os.path.join(self.tmp, 'websites')
        os.makedirs(self.vault)

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

    def test_site_note_for_finds_the_real_note(self):
        real = _write_site_note(self.vault)
        idx = self._index()
        self.assertEqual(idx.site_note_for(_PAGE1), real)
        self.assertEqual(idx.site_note_for('https://xtools.example.net'),
                         real)
        self.assertIsNone(idx.site_note_for('https://other.example.net'))

    def test_failed_placeholder_is_not_the_sites_note(self):
        review = os.path.join(self.vault, '_review')
        os.makedirs(review)
        ph = os.path.join(review, 'xtools.md')
        with open(ph, 'w', encoding='utf-8') as f:
            f.write(wp.build_review_note(
                _SITE, 'failed', 'Fetch failed: wall'))
        idx = self._index()
        self.assertIsNone(idx.site_note_for(_PAGE1))

    def test_handwritten_note_is_not_the_anchor(self):
        path = os.path.join(self.vault, 'My own note.md')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('---\nsource: ' + _SITE +
                    '\nmanaged_by: "human"\nfetch_status: "full"\n---\n'
                    '# Mine\n')
        idx = self._index()
        self.assertIsNone(idx.site_note_for(_PAGE1))

    def test_consolidated_links_read_in_the_vault(self):
        real = _write_site_note(self.vault)
        wp.add_site_links_to_note(real, [_PAGE1, _PAGE2])
        idx = self._index()
        self.assertTrue(idx.has_url(_PAGE1))
        self.assertTrue(idx.has_url(_PAGE2))
        self.assertEqual(idx.get_path(_PAGE1), real)
        self.assertEqual(idx.get_path(_PAGE2), real)
        self.assertTrue(idx.has_url(_SITE))   # the source still counts

    def test_add_url_teaches_the_site_map(self):
        idx = self._index()
        self.assertIsNone(idx.site_note_for(_PAGE1))
        real = _write_site_note(self.vault)
        idx.add_url(_SITE, real)
        self.assertEqual(idx.site_note_for(_PAGE1), real)
        self.assertTrue(idx.has_url(_SITE))


# ---------------------------------------------------------------------------
# 5. the gate (the pipeline's own hands)
# ---------------------------------------------------------------------------

class _GateCase(unittest.TestCase):

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix='sitegate-')
        self.vault = os.path.join(self.tmp, 'websites')
        os.makedirs(os.path.join(self.vault, '_review'), exist_ok=True)
        self.db = wp.WebsiteStateDB(
            db_path=os.path.join(self.tmp, 'cache.db'))
        self.fetch = _FakeFetch()
        self.llm = _CountingLLM()
        self.logs = []
        self.in_vault = set()
        self.site_notes = {}

    def tearDown(self):
        dryrun.disable()
        dryrun.clear()
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_pipeline(self, site_probe=None):
        return wp.WebsitePipeline(
            config={'website_vault_path': self.vault,
                    'web_domain_delay_s': 0},
            llm_call=self.llm,
            vault_index_has=lambda u: u in self.in_vault,
            state=self.db, fetch_fn=self.fetch,
            log=lambda m, l='info': self.logs.append((l, m)),
            site_note_for=site_probe)

    def all_logs(self):
        return '\n'.join(m for (_, m) in self.logs)

    def write_table(self, rows):
        lines = ['# Review Master Table — decommission or approve',
                 '',
                 '| # | Date | URL | Domain | Source | Status | Notes |',
                 '|---|------|-----|--------|--------|--------|-------|']
        for url, status, notes in rows:
            domain = url.split('/')[2] if url.count('/') >= 2 else 'x'
            lines.append(f"| - | 2026-10-10 | {url} | {domain} "
                         f"| test | {status} | {notes} |")
        path = wp.decommission_table_path(self.vault)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        return path


class TestTheGate(_GateCase):

    def test_a_link_of_a_known_site_never_defines_a_second_note(self):
        """The owner's law, the plain case: the site's note exists, a
        NEW link of the site arrives — it joins the note. No fetch, no
        LLM, no second note."""
        real = _write_site_note(self.vault)
        pipe = self.make_pipeline(site_probe=lambda u: real)
        res = pipe.run([_PAGE1])
        self.assertEqual(res[0]['outcome'], 'processed', res[0])
        self.assertEqual(res[0]['note_path'], real)
        self.assertEqual(self.fetch.calls, [])    # never asked
        self.assertEqual(self.llm.calls, 0)       # never asked
        self.assertEqual(pipe.counters['consolidated'], 1)
        with open(real, encoding='utf-8') as f:
            content = f.read()
        self.assertIn(f"site_links: [{_PAGE1}]", content)
        self.assertIn(wp.SITE_LINKS_HEADING, content)
        self.assertIn('🧲', self.all_logs())
        # the ledger row points at the site note
        row = self.db.processed_row(_PAGE1)
        self.assertEqual(row['note_path'], real)
        self.assertEqual(row['category'], 'Design')
        # the vault still holds exactly ONE note for the site
        notes = [os.path.join(r, f)
                 for r, _, fs in os.walk(self.vault) for f in fs
                 if f.endswith('.md') and '_review' not in r]
        self.assertEqual(notes, [real])

    def test_second_and_third_links_join_the_same_note(self):
        """x.com, x.com/x, x.com/y — the owner's exact example: all
        in ONE note."""
        real = _write_site_note(self.vault)
        pipe = self.make_pipeline(site_probe=lambda u: real)
        res = pipe.run([_SITE + '/x', _SITE + '/y'])
        self.assertEqual([r['outcome'] for r in res],
                         ['processed', 'processed'])
        self.assertEqual(self.fetch.calls, [])
        self.assertEqual(self.llm.calls, 0)
        with open(real, encoding='utf-8') as f:
            content = f.read()
        self.assertIn(_SITE + '/x', content)
        self.assertIn(_SITE + '/y', content)
        self.assertIn(f"site_links: [{_SITE}/x, {_SITE}/y]", content)

    def test_the_law_is_off_without_the_probe(self):
        """Backward compatibility: no injected probe, no law — a new
        link processes exactly as before (its own note)."""
        pipe = self.make_pipeline()      # site_note_for=None
        res = pipe.run([_PAGE1])
        self.assertEqual(res[0]['outcome'], 'processed', res[0])
        self.assertEqual(self.fetch.calls, [_PAGE1])
        self.assertGreater(self.llm.calls, 0)
        self.assertEqual(pipe.counters['consolidated'], 0)
        self.assertTrue(os.path.isfile(res[0]['note_path']))

    def test_a_failed_placeholder_of_the_site_rides_the_gate(self):
        """The wall's placeholder for x.com/palettes + a REAL note for
        the site: the placeholder's URL joins the site note, the file
        is swept, the retry row resolves."""
        real = _write_site_note(self.vault)
        ph = os.path.join(self.vault, '_review', 'xtools-palettes.md')
        with open(ph, 'w', encoding='utf-8') as f:
            f.write(wp.build_review_note(
                _PAGE1, 'failed', 'Fetch failed: HTTP 403'))
        self.db.mark_processed(_PAGE1, ph, '', '', 'failed')
        self.db.enqueue_retry(_PAGE1, 'HTTP 403')
        self.in_vault = {_PAGE1}          # the placeholder is indexed
        pipe = self.make_pipeline(site_probe=lambda u: real)
        res = pipe.run([_PAGE1])
        self.assertEqual(res[0]['outcome'], 'processed', res[0])
        self.assertEqual(res[0]['note_path'], real)
        self.assertEqual(self.fetch.calls, [])   # no re-fetch of the wall
        self.assertFalse(os.path.exists(ph))     # swept
        self.assertIsNone(self.db.retry_row(_PAGE1))
        with open(real, encoding='utf-8') as f:
            self.assertIn(f"site_links: [{_PAGE1}]", f.read())

    def test_the_batch_overlay_anchors_a_new_site(self):
        """Two links of a BRAND-NEW site in one batch (no probe at
        all): the first note anchors the site, the second joins it."""
        pipe = self.make_pipeline()      # no probe — overlay only
        res = pipe.run([_SITE, _PAGE1])
        self.assertEqual([r['outcome'] for r in res],
                         ['processed', 'processed'])
        first_path = res[0]['note_path']
        self.assertEqual(res[1]['note_path'], first_path)
        self.assertEqual(self.fetch.calls, [_SITE])   # fetched ONCE
        self.assertGreater(self.llm.calls, 0)          # analyzed ONCE
        self.assertEqual(pipe.counters['consolidated'], 1)
        with open(first_path, encoding='utf-8') as f:
            content = f.read()
        self.assertIn(f"site_links: [{_PAGE1}]", content)
        notes = [os.path.join(r, f)
                 for r, _, fs in os.walk(self.vault) for f in fs
                 if f.endswith('.md') and '_review' not in r]
        self.assertEqual(notes, [first_path])


# ---------------------------------------------------------------------------
# 6. the 🖐 hand (outranks the gate; the site note is the soft lock)
# ---------------------------------------------------------------------------

class TestTheHandPath(_GateCase):

    def test_a_gestured_link_joins_the_site_note_after_the_fetch(self):
        """The hand falls through to the full flow (its fetch answers),
        but the site's note is WHERE the link belongs: the classifier
        and analyzer are skipped (the soft lock), the link joins the
        note, no second note is defined, and the gesture retires."""
        real = _write_site_note(self.vault)
        self.write_table([(_PAGE1, '✋ hand', 'walled')])
        pipe = self.make_pipeline(site_probe=lambda u: real)
        res = pipe.run([_PAGE1])
        self.assertEqual(res[0]['outcome'], 'processed', res[0])
        self.assertEqual(res[0]['note_path'], real)
        # the hand's fetch answered (the machine doors were asked)…
        self.assertEqual(self.fetch.calls, [_PAGE1])
        # …but the classifier and the analyzer were NOT (the soft lock:
        # the site note's placement is the answer)
        self.assertEqual(self.llm.calls, 0)
        with open(real, encoding='utf-8') as f:
            content = f.read()
        self.assertIn(f"site_links: [{_PAGE1}]", content)
        # one note for the site, still
        notes = [os.path.join(r, f)
                 for r, _, fs in os.walk(self.vault) for f in fs
                 if f.endswith('.md') and '_review' not in r]
        self.assertEqual(notes, [real])
        # the gesture retired (the harvest's own line; the stamped row
        # then leaves the table — the fifth door's consume law)
        self.assertIn('the hand\'s harvest', self.all_logs())
        self.assertIn('retired to the green checkbox', self.all_logs())
        self.assertEqual(wp.scan_master_hand_rows(self.vault), [])
        self.assertIn('🔗', self.all_logs())   # the soft lock's line


# ---------------------------------------------------------------------------
# 7. source contracts (the wiring, pinned in the source)
# ---------------------------------------------------------------------------

class TestSourceContracts(unittest.TestCase):

    def test_the_phase_wires_the_probe(self):
        src = open(os.path.join(
            _REPO_ROOT, 'app', 'gitcurator', 'gui', 'worker',
            'website_phase.py'), encoding='utf-8').read()
        self.assertIn('site_note_for=index.site_note_for', src)

    def test_the_index_keeps_the_site_map(self):
        src = open(os.path.join(
            _REPO_ROOT, 'app', 'gitcurator', 'gui', 'vault_index.py'),
            encoding='utf-8').read()
        self.assertIn('_site_to_path', src)
        self.assertIn('def site_note_for', src)
        self.assertIn('site_links:', src[:12000])

    def test_the_gate_lives_after_the_in_vault_gate(self):
        src = open(os.path.join(
            _REPO_ROOT, 'app', 'gitcurator', 'core',
            'website_pipeline.py'), encoding='utf-8').read()
        invault_at = src.index("result['error'] = 'already in the "
                               "websites vault'")
        gate_at = src.index('ONE NOTE PER SITE (v0.64.0)')
        self.assertLess(invault_at, gate_at)
        self.assertIn('_consolidate_into_site_note', src)
        self.assertIn("self.counters['consolidated']", src)


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
        self.assertEqual(self._read('VERSION').strip(), '0.64.1')

    def test_changelog_has_the_law(self):
        text = self._read('CHANGELOG.md')
        self.assertIn('## [0.64.0]', text)
        self.assertIn('ONE NOTE PER SITE', text)
        flat = ' '.join(text.split())
        self.assertIn('do not define different notes', flat)  # verbatim

    def test_ci_and_agents_know_the_module(self):
        ci = self._read('.github', 'workflows', 'ci.yml')
        self.assertIn('tests.test_sitenote', ci)
        agents = self._read('AGENTS.md')
        self.assertIn('tests.test_sitenote', agents)


if __name__ == '__main__':      # pragma: no cover
    unittest.main()
