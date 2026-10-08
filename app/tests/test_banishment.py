"""tests/test_banishment.py — v0.58.0, the banishment.

The owner's ask (session, verbatim): "Some websites that are currently
stored in the vault are not favored anymore … they've changed their
terms or doesn't offer free services … the problem is that if I just
delete it's record from vault, system will re-fetch and restore it.
But I want a system that let me to delete and never fetch again some
websites … for example in tags of websites, we can have a meta-data
for this case, for example i choose 'delete' or a specific emoji."

v0.59.0 (same file, same law): the owner's follow-up — "When i write
'delete' tag, it autocompletes to 'auto_delete' is that correct?" and
"I want get a log of how many notes are wiped because of this method,
every run. for example '10 Websites Removed and will never fetch
again because you blah blah'" — so ``auto_delete`` / ``auto-delete``
join the banish words (the editor's suggested tag is obeyed), and the
run tally speaks every run (the zero run answers too).

Covered here (zero network — an injected fetch_fn and a fake LLM, the
house pattern):

* the markers — a tag counts when it CONTAINS 🗑️ or IS one of the
  words exactly (delete / banish / blacklist / purge; the tag
  "deleted-files" never fires); flow style AND the block style
  Obsidian's property editor writes; a true ``decommission:`` /
  ``banish:`` / ``blacklist:`` frontmatter key; ``#delete`` hashtag
  style. The Status-cell grammar: 🗑️ / the words, ♻️ revived outranks
  everything, dead/reviewed alone never banish, the graveyard's own
  stamps never collide.
* scan_banished_notes — a proper note in a category folder, a
  ``_review`` item, a hand-written marked note (kept, flagged), a note
  already resting in ``.trash`` (never re-found), a missing vault.
* _find_note_for_url — the ledger row first (verified on disk), the
  vault walk when the row was lost, '' when the note is already gone.
* banish_marked_notes — THE BURIAL: the note leaves the library for
  .trash/banished, the URL is dismissed (banished reason), the retry
  row drops, the processed row is forgotten (a real note's row — the
  exact false-success shape v0.57.0 closed), the half-fetched _review
  items sweep, the master table gains the record row; idempotent; a
  hand-written marked note is KEPT (the sacred law); dry-run moves
  nothing.
* THE LOOP, CLOSED — the owner's exact complaint: the marked note is
  banished at pipeline start; a future paste of the same URL is
  skipped (never fetched, no fresh note), even after another re-arm.
  And the table's own 🗑️ Status: a proper note is removed; a link
  whose note the owner ALREADY deleted by hand is dismissed DB-only —
  the silent-deletion re-fetch loop closed by the explicit verdict.
* the revive door — ♻️ on the banished record row undismisses; the
  link is fetched like new again.
* the gate's wording — process_link names the door (banished, not the
  graveyard's decommissioned, not the generic deleted-note line); the
  waiting classifier never reads a 🗑 row as " - ".
* the note itself teaches the door — build_website_note and
  build_review_note both carry the retire hint.

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


# -- the fakes (the test_decommission pattern, kept local) ------------------

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
            'what_it_does': ['One', 'Two'],
            'best_used_for': 'Use when you need to test the pipeline.',
            'pricing': 'free', 'login_required': 'no',
            'similar_tools': [], 'tags': ['design'],
            'confidence': 'high'})


def _tag_note(path, marker='🗑️'):
    """The owner's hand: add the delete mark to a note's tags line."""
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()
    if 'tags: [' in text:
        text = text.replace('tags: [', f'tags: [{marker}, ', 1)
    else:       # block style (Obsidian's property editor)
        text = text.replace('tags:\n', f'tags:\n  - {marker}\n', 1)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    return path


def _write_note(vault, relpath, url, tags='[design]',
                managed='gitcurator', extra_fm=''):
    """Hand-write a note the way build_website_note shapes it (frontmatter
    with source/managed_by/tags) — the test's direct file factory."""
    path = os.path.join(vault, relpath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(f"""---
source: "{url}"
aliases: []
tags: {tags}
category: "Design"
subcategory: "Assets & Resources"
fetch_status: "full"
pricing: "free"
login_required: "no"
date_processed: 2026-10-09
managed_by: "{managed}"
schema_version: "1"
prompt_version: "web-v2"
{extra_fm}---

> [!info] Managed by GitCurator — machine-written note.

# Test Site

> **TL;DR:** A test page about design.

## Best used for
Testing.

---
*Source: [{url}]({url})*
""")
    return path


class _BanishCase(unittest.TestCase):
    """Shared plumbing: temp vault + state db + pipeline factory."""

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix='banish-')
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

    def store_proper_note(self, url):
        """Run a link through the pipeline: a real, categorized note."""
        pipe = self.make_pipeline()
        r = pipe.process_link(url)
        self.assertEqual(r['outcome'], 'processed', r.get('error'))
        self.in_vault.add(r['canonical'])
        return r['canonical'], r['note_path']

    def all_logs(self):
        return '\n'.join(m for (_, m) in self.logs)

    def table_text(self):
        path = wp.decommission_table_path(self.vault)
        if not os.path.isfile(path):
            return ''
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()

    def write_table(self, rows):
        path = wp.decommission_table_path(self.vault)
        lines = [wp._GRAVEYARD_HEADER.format(now='2026-10-09 12:00')]
        for url, status in rows:
            from urllib.parse import urlparse
            domain = urlparse(url).netloc or 'unknown'
            lines.append(f"| - | 2026-10-09 | {url} | {domain} "
                         f"| test | {status} | |")
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        return path


class TestMarkers(_BanishCase):

    def test_tag_marker_variants(self):
        marked = ('🗑️', 'delete', 'banish', 'blacklist', 'purge',
                  '🗑️ delete', '#delete', 'DELETE', 'Banish')
        for m in marked:
            fm = {'tags': [m], 'banish': {}}
            ok, why = wp._note_reads_banished(fm)
            self.assertTrue(ok, m)
            self.assertTrue(why, m)
        alive = ('design', 'tools', 'deleted-files', 'deletion-policy',
                 'mandatory', 'purge-like-syntax', '', 'delete-ish')
        for m in alive:
            fm = {'tags': [m], 'banish': {}}
            self.assertFalse(wp._note_reads_banished(fm)[0], m)

    def test_tag_among_other_tags(self):
        fm = {'tags': ['design', 'tools', 'delete'], 'banish': {}}
        ok, why = wp._note_reads_banished(fm)
        self.assertTrue(ok)
        self.assertEqual(why, 'delete')

    def test_auto_delete_tag_alias(self):
        # v0.59.0 — the owner's report: "When i write 'delete' tag, it
        # autocompletes to 'auto_delete' is that correct?" — the word
        # his editor suggests is obeyed exactly like the word he typed:
        for m in ('auto_delete', 'AUTO_DELETE', 'Auto_Delete',
                  'auto-delete', '#auto_delete'):
            fm = {'tags': [m], 'banish': {}}
            ok, why = wp._note_reads_banished(fm)
            self.assertTrue(ok, m)
            self.assertTrue(why, m)
        # still the exact-word law — these near-misses never fire:
        for m in ('automatic_delete', 'auto', 'auto-delete-policy',
                  'auto-deleted-files', 'xauto_deletex'):
            self.assertFalse(
                wp._note_reads_banished({'tags': [m], 'banish': {}})[0], m)

    def test_auto_delete_status_cell_fires(self):
        # the Status cell's substring law obeys the same alias (both
        # surfaces, one verdict):
        for s in ('auto_delete', 'Auto-Delete',
                  'auto_delete — decided', '🗑️ + auto_delete'):
            self.assertTrue(wp._status_is_banished(s), s)
        # the app's own 'auto' stamp alone stays a non-verdict:
        for s in ('auto', 'auto — dead', '📁 auto stored'):
            self.assertFalse(wp._status_is_banished(s), s)

    def test_frontmatter_key_variants(self):
        for key in ('decommission', 'banish', 'blacklist'):
            for val in ('true', 'True', 'yes', '1', 'on'):
                fm = {'tags': [], 'banish': {key: val}}
                self.assertTrue(
                    wp._note_reads_banished(fm)[0], f"{key}: {val}")
        for key in ('decommission', 'banish', 'blacklist'):
            for val in ('false', 'no', '0', 'off', ''):
                fm = {'tags': [], 'banish': {key: val}}
                self.assertFalse(
                    wp._note_reads_banished(fm)[0], f"{key}: {val}")
        # a key that is not ours never fires
        fm = {'tags': [], 'banish': {'retired': 'true'}}
        self.assertFalse(wp._note_reads_banished(fm)[0])

    def test_parse_flow_and_block_style_tags(self):
        flow = os.path.join(self.tmp, 'flow.md')
        with open(flow, 'w', encoding='utf-8') as f:
            f.write('---\nsource: "https://a.example/x"\n'
                    'tags: [design, delete]\nmanaged_by: "gitcurator"\n'
                    '---\nbody\n')
        fm = wp._parse_banish_frontmatter(flow)
        self.assertEqual(fm['tags'], ['design', 'delete'])
        self.assertEqual(fm['managed_by'], 'gitcurator')
        ok, why = wp._note_reads_banished(fm)
        self.assertTrue(ok)
        self.assertEqual(why, 'delete')

        block = os.path.join(self.tmp, 'block.md')
        with open(block, 'w', encoding='utf-8') as f:
            f.write('---\nsource: "https://a.example/y"\ntags:\n'
                    '  - design\n  - "🗑️"\n'
                    'category: "Design"\nmanaged_by: "gitcurator"\n'
                    '---\nbody\n')
        fm = wp._parse_banish_frontmatter(block)
        self.assertEqual(fm['tags'], ['design', '🗑️'])
        self.assertTrue(wp._note_reads_banished(fm)[0])

        # empty flow list and no frontmatter are not marks
        empty = os.path.join(self.tmp, 'empty.md')
        with open(empty, 'w', encoding='utf-8') as f:
            f.write('---\nsource: "https://a.example/z"\ntags: []\n'
                    'managed_by: "gitcurator"\n---\n')
        self.assertFalse(wp._note_reads_banished(
            wp._parse_banish_frontmatter(empty))[0])
        plain = os.path.join(self.tmp, 'plain.md')
        with open(plain, 'w', encoding='utf-8') as f:
            f.write('# just markdown, no frontmatter\n')
        self.assertIsNone(wp._parse_banish_frontmatter(plain))

    def test_status_is_banished_variants(self):
        banished = ('🗑️', '🗑️ banished', 'delete', 'Delete this one',
                    'blacklist', 'purge', 'banished by me',
                    '🪦 dead + delete')          # removal intent wins
        for s in banished:
            self.assertTrue(wp._status_is_banished(s), s)
        not_banished = ('', 'unreviewed', '✅ reviewed', '🪦 dead',
                        '☠️ gone', 'decommissioned', 'retired 2026',
                        '🖐 hand', 'walled — still trying',
                        '🪦 confirmed — decommissioned 2026-10-09',
                        '✅ confirmed — reviewed 2026-10-09',
                        '📁 stored')
        for s in not_banished:
            self.assertFalse(wp._status_is_banished(s), s)
        # the undo door outranks every burial
        for s in ('♻️ revived + delete', '♻️', 'restored'):
            self.assertFalse(wp._status_is_banished(s), s)

    def test_waiting_classifier_never_reads_banished(self):
        for s in ('🗑️ banished', 'delete', '🗑️ banished — confirmed '
                                      '2026-10-09'):
            self.assertFalse(wp._status_is_waiting(s), s)


class TestScan(_BanishCase):

    def test_scan_finds_proper_review_and_handwritten(self):
        proper = _write_note(self.vault, os.path.join(
            'Design', 'Assets & Resources', 'a.md'),
            'https://a.example/x', tags='[design, delete]')
        review = _write_note(self.vault, os.path.join(
            '_review', 'b.md'), 'https://b.example/y', tags='[🗑️]')
        hand = _write_note(self.vault, os.path.join(
            'Design', 'Assets & Resources', 'c.md'),
            'https://c.example/z', tags='[delete]', managed='a-human')
        unmarked = _write_note(self.vault, os.path.join(
            'Design', 'Assets & Resources', 'd.md'),
            'https://d.example/w', tags='[design]')
        items = wp.scan_banished_notes(self.vault)
        by_path = {it['path']: it for it in items}
        self.assertIn(proper, by_path)
        self.assertIn(review, by_path)
        self.assertIn(hand, by_path)
        self.assertNotIn(unmarked, by_path)
        self.assertTrue(by_path[proper]['app_owned'])
        self.assertTrue(by_path[review]['app_owned'])
        self.assertFalse(by_path[hand]['app_owned'])

    def test_scan_reads_the_frontmatter_key_too(self):
        path = _write_note(self.vault, os.path.join('Design', 'k.md'),
                           'https://k.example/x', tags='[design]',
                           extra_fm='decommission: true\n')
        items = wp.scan_banished_notes(self.vault)
        self.assertEqual([it['path'] for it in items], [path])
        self.assertIn('decommission', items[0]['marker'])
        self.assertTrue(items[0]['app_owned'])

    def test_scan_never_finds_a_note_in_trash(self):
        path = _write_note(self.vault, os.path.join('Design', 'a.md'),
                           'https://a.example/x', tags='[delete]')
        # the owner's earlier banishment rests in the hidden trash:
        trash = os.path.join(self.vault, wp.BANISH_QUARANTINE_RELPATH)
        os.makedirs(trash, exist_ok=True)
        shutil.copy(path, os.path.join(trash, 'a.md'))
        items = wp.scan_banished_notes(self.vault)
        self.assertEqual(
            [it['path'] for it in items if 'trash' in it['path']], [])
        # (the live copy is still found — only the trash copy is blind)

    def test_scan_missing_vault(self):
        self.assertEqual(wp.scan_banished_notes(self.vault), [])
        self.assertEqual(wp.scan_banished_notes(''), [])
        self.assertEqual(
            wp.scan_banished_notes(os.path.join(self.tmp, 'nope')), [])

    def test_find_note_for_url(self):
        canonical, note_path = self.store_proper_note(
            'https://example.com/site')
        # the ledger row answers, verified on disk:
        self.assertEqual(
            wp._find_note_for_url(self.vault, canonical, state=self.db),
            note_path)
        # the row lost (cache.db rebuilt) — the vault walk answers:
        self.db.forget_row(canonical)
        self.assertEqual(
            wp._find_note_for_url(self.vault, canonical, state=self.db),
            note_path)
        # the note hand-deleted — nothing to find:
        os.remove(note_path)
        self.assertEqual(
            wp._find_note_for_url(self.vault, canonical, state=self.db), '')


class TestTheBurial(_BanishCase):

    def test_the_full_burial(self):
        canonical, note_path = self.store_proper_note(
            'https://example.com/site')
        _tag_note(note_path)                       # the owner's hand
        # the link also owns a half-fetched _review leftover:
        leftover = os.path.join(self.vault, '_review', 'site-left.md')
        with open(leftover, 'w', encoding='utf-8') as f:
            f.write(wp.build_review_note(
                canonical, 'review', 'low classification confidence'))
        self.db.enqueue_retry(canonical, 'HTTP 500')

        report = wp.banish_marked_notes(self.db, self.vault,
                                        log=lambda *a, **k: None)
        self.assertEqual(report['marked'], 1)
        self.assertEqual(report['banished'], 1)
        self.assertEqual(report['notes_moved'], 1)
        self.assertEqual(report['kept_handwritten'], 0)
        self.assertEqual(report['review_swept'], 1)
        # the note left the library for the hidden trash:
        self.assertFalse(os.path.exists(note_path))
        trash = os.path.join(self.vault, wp.BANISH_QUARANTINE_RELPATH)
        self.assertTrue(
            os.path.isfile(os.path.join(trash,
                                        os.path.basename(note_path))))
        # the leftover went with it:
        self.assertFalse(os.path.exists(leftover))
        # the ledger: dismissed with the banished reason, retry dropped,
        # processed row forgotten (a REAL note's row — forget_failed_row
        # refuses those; forget_row is the banishment's own door):
        self.assertTrue(self.db.is_dismissed(canonical))
        reason = self.db.dismissed_row(canonical)['reason']
        self.assertTrue(reason.startswith(wp.BANISH_REASON_PREFIX), reason)
        self.assertIsNone(self.db.retry_row(canonical))
        self.assertIsNone(self.db.processed_row(canonical))
        # the record row landed in the master table:
        self.assertIn('🗑️ banished — confirmed', self.table_text())
        self.assertIn(canonical, self.table_text())

    def test_idempotent(self):
        canonical, note_path = self.store_proper_note(
            'https://example.com/site')
        _tag_note(note_path)
        first = wp.banish_marked_notes(self.db, self.vault,
                                       log=lambda *a, **k: None)
        text_after_first = self.table_text()
        trash_listing = os.listdir(
            os.path.join(self.vault, wp.BANISH_QUARANTINE_RELPATH))
        second = wp.banish_marked_notes(self.db, self.vault,
                                        log=lambda *a, **k: None)
        self.assertEqual(second['marked'], 0)        # the note rests in
        self.assertEqual(second['banished'], 0)      # .trash — never
        self.assertEqual(second['rows_written'], 0)  # re-found, never
        self.assertEqual(self.table_text(), text_after_first)  # re-rowed
        self.assertEqual(os.listdir(os.path.join(
            self.vault, wp.BANISH_QUARANTINE_RELPATH)), trash_listing)

    def test_handwritten_marked_note_is_kept(self):
        path = _write_note(self.vault, os.path.join(
            'Design', 'hand.md'), 'https://h.example/x',
            tags='[delete]', managed='a-human')
        report = wp.banish_marked_notes(self.db, self.vault,
                                        log=lambda *a, **k: None)
        self.assertEqual(report['marked'], 1)
        self.assertEqual(report['banished'], 0)
        self.assertEqual(report['kept_handwritten'], 1)
        self.assertTrue(os.path.exists(path))     # the sacred law
        self.assertFalse(self.db.is_dismissed(
            wp.normalize_website_url('https://h.example/x')))

    def test_dry_run_moves_nothing(self):
        canonical, note_path = self.store_proper_note(
            'https://example.com/site')
        _tag_note(note_path)
        dryrun.enable()
        try:
            report = wp.banish_marked_notes(self.db, self.vault,
                                            log=lambda *a, **k: None)
        finally:
            dryrun.disable()
        self.assertEqual(report['marked'], 1)
        self.assertEqual(report['banished'], 1)
        self.assertEqual(report['notes_moved'], 0)
        # every file mutation rehearsed only:
        self.assertTrue(os.path.exists(note_path))
        self.assertFalse(os.path.isdir(os.path.join(
            self.vault, wp.BANISH_QUARANTINE_RELPATH)))

    def test_forget_row_removes_a_real_notes_row(self):
        # forget_failed_row REFUSES a real note's row (its guard);
        # forget_row is the banishment's own door and takes it:
        canonical, note_path = self.store_proper_note(
            'https://example.com/site')
        self.assertFalse(self.db.forget_failed_row(canonical))
        self.assertTrue(self.db.forget_row(canonical))
        self.assertIsNone(self.db.processed_row(canonical))
        self.assertFalse(self.db.forget_row(canonical))   # idempotent


class TestTheLoopClosed(_BanishCase):
    """THE OWNER'S EXACT COMPLAINT — the delete-then-refetch loop."""

    def test_marked_note_is_banished_and_never_refetched(self):
        canonical, note_path = self.store_proper_note(
            'https://example.com/site')
        _tag_note(note_path)
        # months later, a fresh batch arrives with the same URL:
        fetch = _FakeFetch()
        pipe = self.make_pipeline(fetch=fetch)   # __init__ banishes
        self.assertFalse(os.path.exists(note_path))
        results = pipe.run(['https://example.com/site'])
        self.assertEqual(results[0]['outcome'], 'skipped')
        self.assertIn('banished', results[0]['error'])
        self.assertNotIn('https://example.com/site', fetch.calls)
        # and no fresh note was written anywhere:
        for root, dirs, files in os.walk(self.vault):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for name in files:
                self.assertNotIn(name, ('Test Site.md',),
                                 'a fresh note was written for a '
                                 'blacklisted URL')

    def test_auto_delete_tag_closes_the_loop(self):
        # the autocomplete word closes the exact same loop (v0.59.0 —
        # the tag the owner's editor puts under his thumb works):
        canonical, note_path = self.store_proper_note(
            'https://example.com/site')
        _tag_note(note_path, marker='auto_delete')
        fetch = _FakeFetch()
        pipe = self.make_pipeline(fetch=fetch)   # __init__ banishes
        self.assertFalse(os.path.exists(note_path))
        results = pipe.run(['https://example.com/site'])
        self.assertEqual(results[0]['outcome'], 'skipped')
        self.assertIn('banished', results[0]['error'])
        self.assertNotIn('https://example.com/site', fetch.calls)

    def test_rearm_never_resurrects_a_banished_url(self):
        canonical, note_path = self.store_proper_note(
            'https://example.com/site')
        _tag_note(note_path)
        fetch = _FakeFetch()
        self.make_pipeline(fetch=fetch)          # __init__ banishes
        self.db.rearm_retries()                  # any later re-arm
        self.assertNotIn(canonical, self.db.due_retries())
        r = self.make_pipeline(fetch=fetch).process_link(
            'https://example.com/site')
        self.assertEqual(r['outcome'], 'skipped')
        self.assertIn('banished', r['error'])
        self.assertNotIn('https://example.com/site', fetch.calls)

    def test_pipeline_init_runs_the_banishment(self):
        canonical, note_path = self.store_proper_note(
            'https://example.com/site')
        _tag_note(note_path)
        self.make_pipeline()                     # construction alone
        self.assertFalse(os.path.exists(note_path))
        self.assertIn('🗑️', self.all_logs())

    def test_skip_line_names_the_door(self):
        canonical, note_path = self.store_proper_note(
            'https://example.com/site')
        _tag_note(note_path)
        fetch = _FakeFetch()
        pipe = self.make_pipeline(fetch=fetch)
        r = pipe.process_link('https://example.com/site')
        self.assertEqual(r['outcome'], 'skipped')
        self.assertIn('banished by owner', r['error'])
        self.assertIn('blacklisted', r['error'])
        self.assertIn('🗑️', self.all_logs())
        self.assertIn('♻️', self.all_logs())   # the undo door is named

    def test_the_note_hint_line(self):
        note = wp.build_website_note(
            'https://example.com/site', {'name': 'Test Site',
                                         'one_line': 'A page.',
                                         'tags': ['design']},
            'Design', 'Assets & Resources', 'full')
        self.assertIn('🗑️', note)
        self.assertIn('never fetch', note)
        review = wp.build_review_note(
            'https://example.com/walled', 'failed', 'HTTP 403')
        self.assertIn('🗑️', review)


class TestTheTally(_BanishCase):
    """v0.59.0 — the owner's ask: "I want get a log of how many notes
    are wiped because of this method, every run. for example '10
    Websites Removed and will never fetch again because you blah
    blah'"."""

    def _tally_lines(self):
        return [m for (_, m) in self.logs
                if m.startswith('🗑️ Run tally:')]

    def test_tally_line_names_the_count_and_the_contract(self):
        # both doors in one run: a note tagged 🗑️ AND a table row
        # marked delete — the tally speaks ONCE, with the count:
        a, note_a = self.store_proper_note('https://a.example/x')
        b, note_b = self.store_proper_note('https://b.example/y')
        _tag_note(note_a)                       # the note's own door
        self.write_table([('https://b.example/y', 'delete')])
        self.logs.clear()
        pipe = self.make_pipeline()             # __init__ banishes
        tally = self._tally_lines()
        self.assertEqual(len(tally), 1)         # once per run
        self.assertIn('2 website(s) removed', tally[0])
        self.assertIn('never fetched again', tally[0])
        self.assertIn('you marked', tally[0])
        self.assertIn('♻️', tally[0])           # the undo door is named
        self.assertEqual(set(pipe.banished_urls), {a, b})
        self.assertFalse(os.path.exists(note_a))
        self.assertFalse(os.path.exists(note_b))

    def test_tally_line_speaks_on_a_quiet_run(self):
        # "every run" — the zero run answers too (a count, not silence):
        self.make_pipeline()
        self.assertEqual(self._tally_lines(), [
            '🗑️ Run tally: 0 websites removed this run — no 🗑️ / '
            'delete / auto_delete marks in the library'])

    def test_tally_dedupes_across_the_doors(self):
        # one URL marked BOTH on its note and in the table is ONE
        # banishment — the tally counts websites, not gestures:
        canonical, note_path = self.store_proper_note(
            'https://example.com/site')
        _tag_note(note_path)
        self.write_table([('https://example.com/site', '🗑️')])
        self.logs.clear()
        pipe = self.make_pipeline()
        self.assertEqual(pipe.banished_urls, [canonical])
        tally = self._tally_lines()
        self.assertEqual(len(tally), 1)
        self.assertIn('1 website(s) removed', tally[0])

    def test_the_reports_carry_the_banished_urls(self):
        # the two doors' reports carry the canonical lists the tally
        # rolls up — the worker's summary reads them at the run's end:
        canonical, note_path = self.store_proper_note(
            'https://example.com/site')
        _tag_note(note_path)
        r = wp.banish_marked_notes(self.db, self.vault,
                                   log=lambda *a, **k: None)
        self.assertEqual(r['urls'], [canonical])
        # a vault without the table answers with the empty list:
        r_empty = wp.consume_decommission_table(
            self.db, os.path.join(self.tmp, 'nowhere'),
            log=lambda *a, **k: None)
        self.assertEqual(r_empty['banished_urls'], [])
        # and the table door's report carries its own list:
        other, _note2 = self.store_proper_note(
            'https://other.example/y')
        self.write_table([('https://other.example/y', 'delete')])
        r2 = wp.consume_decommission_table(self.db, self.vault,
                                           log=lambda *a, **k: None)
        self.assertEqual(r2['banished_urls'], [other])


class TestTableGesture(_BanishCase):
    """The master table's own 🗑️ Status — the second surface."""

    def test_proper_note_is_removed_via_the_table(self):
        canonical, note_path = self.store_proper_note(
            'https://example.com/site')
        self.write_table([('https://example.com/site', '🗑️ banished')])
        report = wp.consume_decommission_table(self.db, self.vault,
                                               log=lambda *a, **k: None)
        self.assertEqual(report['banished'], 1)
        self.assertEqual(report['notes_moved'], 1)
        self.assertFalse(os.path.exists(note_path))
        self.assertTrue(os.path.isfile(os.path.join(
            self.vault, wp.BANISH_QUARANTINE_RELPATH,
            os.path.basename(note_path))))
        self.assertTrue(self.db.is_dismissed(canonical))
        self.assertTrue(self.db.dismissed_row(canonical)['reason']
                        .startswith(wp.BANISH_REASON_PREFIX))
        self.assertIsNone(self.db.processed_row(canonical))
        self.assertIn('🗑️ banished — confirmed', self.table_text())

    def test_table_gesture_consume_is_idempotent(self):
        canonical, note_path = self.store_proper_note(
            'https://example.com/site')
        self.write_table([('https://example.com/site', '🗑️ banished')])
        wp.consume_decommission_table(self.db, self.vault,
                                      log=lambda *a, **k: None)
        text_after_first = self.table_text()
        second = wp.consume_decommission_table(self.db, self.vault,
                                               log=lambda *a, **k: None)
        self.assertEqual(second['banished'], 1)     # still reads the mark
        self.assertEqual(second['notes_moved'], 0)  # nothing left to move
        self.assertEqual(self.table_text(), text_after_first)
        self.assertEqual(len(os.listdir(os.path.join(
            self.vault, wp.BANISH_QUARANTINE_RELPATH))), 1)

    def test_hand_deleted_note_is_dismissed_db_only(self):
        # THE LOOP the owner named: he deletes the note by hand, but the
        # ledger row remains — any re-arm would refetch and RESTORE it.
        canonical, note_path = self.store_proper_note(
            'https://example.com/site')
        os.remove(note_path)                    # the owner's deletion
        self.assertIsNotNone(self.db.processed_row(canonical))
        # he marks the table row — the verdict that closes the loop:
        self.write_table([('https://example.com/site', 'delete')])
        report = wp.consume_decommission_table(self.db, self.vault,
                                               log=lambda *a, **k: None)
        self.assertEqual(report['banished'], 1)
        self.assertEqual(report['notes_moved'], 0)
        self.assertIsNone(self.db.processed_row(canonical))
        self.assertTrue(self.db.is_dismissed(canonical))
        fetch = _FakeFetch()
        results = self.make_pipeline(fetch=fetch).run(
            ['https://example.com/site'])
        self.assertEqual(results[0]['outcome'], 'skipped')
        self.assertIn('banished', results[0]['error'])
        self.assertEqual(fetch.calls, [])

    def test_banish_beats_dead_on_a_both_marked_cell(self):
        canonical, note_path = self.store_proper_note(
            'https://example.com/site')
        self.write_table([('https://example.com/site',
                           '🪦 dead + 🗑️ delete')])
        report = wp.consume_decommission_table(self.db, self.vault,
                                               log=lambda *a, **k: None)
        # the removal intent won: the note LEFT, the stamp is the 🗑️ one
        self.assertEqual(report['banished'], 1)
        self.assertEqual(report['dead'], 0)
        self.assertFalse(os.path.exists(note_path))
        self.assertIn('🗑️ banished — confirmed', self.table_text())

    def test_review_placeholder_is_swept_with_the_burial(self):
        # a walled link's placeholder + its table row, both marked for
        # removal — the whole site leaves the vault in one pass:
        url = 'https://example.com/walled'
        pipe = self.make_pipeline(fetch=_FakeFetch(fail_paths=['/walled']))
        r = pipe.process_link(url)
        self.assertEqual(r['outcome'], 'review')
        canonical = r['canonical']
        for _ in range(2):
            self.db.enqueue_retry(canonical, 'HTTP 404')
        self.write_table([(url, '🗑️ delete')])
        report = wp.consume_decommission_table(self.db, self.vault,
                                               log=lambda *a, **k: None)
        self.assertEqual(report['banished'], 1)
        self.assertEqual(report['notes_moved'], 1)
        self.assertFalse(os.path.exists(r['note_path']))
        self.assertIsNone(self.db.retry_row(canonical))
        self.assertIsNone(self.db.processed_row(canonical))


class TestReviveDoor(_BanishCase):

    def test_revive_brings_a_banished_link_back(self):
        canonical, note_path = self.store_proper_note(
            'https://example.com/site')
        _tag_note(note_path)
        wp.banish_marked_notes(self.db, self.vault,
                               log=lambda *a, **k: None)
        self.assertTrue(self.db.is_dismissed(canonical))
        # the vault index is rebuilt per run and never reads .trash —
        # the banished note stopped counting as "in the vault":
        self.in_vault.discard(canonical)
        # the owner changes his mind — the record row carries ♻️:
        self.write_table([('https://example.com/site', '♻️ revived')])
        report = wp.consume_decommission_table(self.db, self.vault,
                                               log=lambda *a, **k: None)
        self.assertEqual(report['revived'], 1)
        self.assertFalse(self.db.is_dismissed(canonical))
        # the link is fetched like new — and succeeds:
        fetch = _FakeFetch()
        pipe = self.make_pipeline(fetch=fetch)
        r = pipe.process_link('https://example.com/site')
        self.assertEqual(r['outcome'], 'processed', r.get('error'))
        self.assertIn('https://example.com/site', fetch.calls)


class TestReleaseBookkeeping(unittest.TestCase):
    """The house source-contract tests (the test_bothdoors pattern)."""

    def setUp(self):
        self.root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def _read(self, *parts):
        with open(os.path.join(self.root, *parts), 'r',
                  encoding='utf-8') as f:
            return f.read()

    def test_version_is_0590(self):
        self.assertEqual(self._read('VERSION').strip(), '0.59.0')

    def test_changelog_has_the_beat(self):
        text = self._read('CHANGELOG.md')
        self.assertIn('## [0.58.0]', text)
        self.assertIn('banish', text.lower())
        self.assertIn('## [0.59.0]', text)
        self.assertIn('tally', text.lower())

    def test_ci_and_agents_know_the_module(self):
        ci = self._read('.github', 'workflows', 'ci.yml')
        self.assertIn('tests.test_banishment', ci)
        agents = self._read('AGENTS.md')
        self.assertIn('tests.test_banishment', agents)

    def test_the_note_builder_carries_the_door(self):
        # the hint is the discoverability surface — the owner lives in
        # Obsidian, the door must live where he curates:
        text = self._read('app', 'gitcurator', 'core',
                          'website_pipeline.py')
        self.assertIn('BANISH_QUARANTINE_RELPATH', text)
        self.assertIn('banish_marked_notes', text)
        self.assertIn('BANISH_TALLY_PREFIX', text)   # v0.59.0 — the
        self.assertIn('banished_urls', text)         # tally + reports


if __name__ == '__main__':
    unittest.main()
