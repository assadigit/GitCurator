#!/usr/bin/env python3
"""
test_phase3.py — Phase 3 (moves as corrections + the backfill, v0.12.0).

Covers, in this order:
  * front-matter surgery helpers (fm_replace_field / fm_swap_tag) — the
    targeted line edits §4.4 prescribes for moved notes;
  * the corrections log and dismissed list tables in cache.db;
  * apply_corrections on the GITHUB vault — moved (front-matter updated,
    locked, correction logged), deleted (dismissed), edited/duplicate/
    unmapped (report-only, files untouched), idempotency (run twice,
    second run does nothing);
  * apply_corrections on the WEBSITES vault with the taxonomy resolver —
    nested Category/Subcategory folders are legal (never 'unmapped'),
    subcategory line updated on moves, moving OUT of _review is a
    correction, unknown folders reported as unmapped;
  * run_start_check — silent baseline, then nothing on the second run;
    a dry-run records nothing and edits nothing;
  * move_summary_lines (the "9 notes moved from X to Y" report line);
  * the classifier locked-skip: a locked website note is never
    re-categorized by the model — the owner's folder wins;
  * the GitHub recategorize flow refuses to touch a locked note;
  * _moc master-index + goodrepos read the corrected category after a
    move (SPEC: "outputs stay correct after moves");
  * tools/backfill_websites.py — resumable checkpoint, dry-run, GitHub
    links skipped, politeness delay, interrupted-run resume.

Headless-safe: QT_QPA_PLATFORM=offscreen, the GUI is never shown.
"""

import os
import shutil
import sys
import tempfile
import time
import unittest

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)

from gitcurator.constants import CATEGORY_FOLDERS
from gitcurator.core import dryrun, note_state
from gitcurator.core.links import normalize_url
from gitcurator.core.note_state import (
    NoteStateDB, apply_corrections, classify_changes, fm_replace_field,
    fm_swap_tag, move_summary_lines, run_start_check, scan_vault,
)
from gitcurator.core.taxonomy import Category, Taxonomy

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    return path


def _read(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def _github_note(url, category_key, tags=('cli',)):
    """A current-format GitHub note (stamps from Phase 1, no legacy
    placeholder sections)."""
    tag_line = 'tags: [' + ', '.join(tags) + ']' if tags else 'tags: []'
    return (
        "---\n"
        f"source: {url}\n"
        "aliases: []\n"
        f"{tag_line}\n"
        f'category: "{category_key}"\n'
        "stars: 10\n"
        'managed_by: "GitCurator"\n'
        "---\n"
        f"# repo\n\nSummary text for {url}.\n"
    )


def _website_note(url, category, subcategory, name="Site"):
    sub = f'"{subcategory}"' if subcategory else '""'
    return (
        "---\n"
        f"source: {url}\n"
        "aliases: []\n"
        "tags: []\n"
        f'category: "{category}"\n'
        f"subcategory: {sub}\n"
        'fetch_status: "ok"\n'
        'managed_by: "GitCurator"\n'
        "---\n"
        f"# {name}\n\n> **TL;DR:** a site\n"
    )


def _mini_taxonomy():
    return Taxonomy(
        categories=[
            Category('Design', 'design things',
                     ['UI/UX', 'Inspiration']),
            Category('Development', 'dev things', ['Tools']),
        ],
        judgment_rules='')


def _github_vault(root):
    """Two managed notes in AI-Domain/Agents, one _review note."""
    vault = os.path.join(root, 'gh')
    _write(os.path.join(vault, CATEGORY_FOLDERS['Agents'], 'a.md'),
           _github_note('https://github.com/o/repo1', 'Agents'))
    _write(os.path.join(vault, CATEGORY_FOLDERS['Agents'], 'b.md'),
           _github_note('https://github.com/o/repo2', 'Agents',
                        tags=('cli', 'Agents')))
    _write(os.path.join(vault, '_review', 'r.md'),
           _github_note('https://github.com/o/repo3', 'Uncategorized'))
    return vault


# ---------------------------------------------------------------------------
# 1. Front-matter surgery (pure)
# ---------------------------------------------------------------------------

class TestFrontmatterSurgery(unittest.TestCase):

    def test_replace_category_line(self):
        note = _github_note('https://github.com/o/r', 'Agents')
        out, changed = fm_replace_field(note, 'category',
                                        'category: "Scraping"')
        self.assertTrue(changed)
        self.assertIn('category: "Scraping"', out)
        self.assertNotIn('category: "Agents"', out)
        # everything else survives untouched
        self.assertIn('source: https://github.com/o/r', out)
        self.assertIn('# repo', out)

    def test_insert_missing_field_before_closing_fence(self):
        note = "---\nsource: u\n---\nbody\n"
        out, changed = fm_replace_field(note, 'category_locked',
                                        'category_locked: true')
        self.assertTrue(changed)
        self.assertEqual(
            out, "---\nsource: u\ncategory_locked: true\n---\nbody\n")

    def test_no_change_when_identical(self):
        note = _github_note('u', 'Agents')
        out, changed = fm_replace_field(note, 'category', 'category: "Agents"')
        self.assertFalse(changed)
        self.assertEqual(out, note)

    def test_file_without_frontmatter_untouched(self):
        note = "# just a heading\n\ncategory: nothing\n"
        out, changed = fm_replace_field(note, 'category', 'category: "X"')
        self.assertFalse(changed)
        self.assertEqual(out, note)

    def test_unterminated_frontmatter_untouched(self):
        note = "---\nsource: u\ncategory: x\n"      # no closing fence
        out, changed = fm_replace_field(note, 'category', 'category: "X"')
        self.assertFalse(changed)
        self.assertEqual(out, note)

    def test_field_match_requires_colon_prefix(self):
        # 'categories:' must NOT match a 'category:' replacement
        note = "---\ncategories: [a]\ncategory: x\n---\n"
        out, changed = fm_replace_field(note, 'category', 'category: "y"')
        self.assertTrue(changed)
        self.assertIn('categories: [a]', out)
        self.assertIn('category: "y"', out)

    def test_swap_tag_removes_and_adds(self):
        note = _github_note('u', 'Agents', tags=('cli', 'Agents'))
        out, changed = fm_swap_tag(note, remove='Agents', add='Scraping')
        self.assertTrue(changed)
        self.assertIn('tags: [Scraping, cli]', out)

    def test_swap_tag_keeps_tags_when_category_absent(self):
        note = _github_note('u', 'Agents', tags=('cli', 'ai'))
        out, changed = fm_swap_tag(note, remove='Agents', add='Scraping')
        self.assertTrue(changed)
        self.assertIn('tags: [Scraping, cli, ai]', out)

    def test_swap_tag_empty_becomes_empty_list(self):
        note = "---\nsource: u\ntags: [Agents]\n---\n"
        out, changed = fm_swap_tag(note, remove='Agents', add='')
        self.assertTrue(changed)
        self.assertIn('tags: []', out)


# ---------------------------------------------------------------------------
# 2. Corrections log + dismissed list tables
# ---------------------------------------------------------------------------

class TestCorrectionsAndDismissedTables(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='p3_tables_')
        self.db = NoteStateDB(os.path.join(self.tmp, 'cache.db'))

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_correction_log_roundtrip(self):
        self.assertEqual(self.db.correction_count('github'), 0)
        self.db.log_correction('github', 'https://x', 'Agents', 'Scraping',
                               'p/old.md', 'p/new.md')
        self.db.log_correction('github', 'https://y', 'MCP', 'Agents')
        self.assertEqual(self.db.correction_count('github'), 2)
        self.assertEqual(self.db.correction_count('websites'), 0)
        recent = self.db.recent_corrections('github', limit=1)
        self.assertEqual(recent[0]['source_url'], 'https://y')  # newest
        history = self.db.corrections_for('github', 'https://x')
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['from_category'], 'Agents')
        self.assertEqual(history[0]['to_category'], 'Scraping')

    def test_dismiss_roundtrip(self):
        self.assertFalse(self.db.is_dismissed('github', 'https://x'))
        self.db.dismiss('github', 'https://x', 'gone.md')
        self.assertTrue(self.db.is_dismissed('github', 'https://x'))
        self.assertFalse(self.db.is_dismissed('websites', 'https://x'))
        self.assertEqual(self.db.dismissed_count('github'), 1)
        # dismiss twice = still one row (INSERT OR REPLACE)
        self.db.dismiss('github', 'https://x')
        self.assertEqual(self.db.dismissed_count('github'), 1)
        self.assertTrue(self.db.clear_dismissed('github', 'https://x'))
        self.assertFalse(self.db.is_dismissed('github', 'https://x'))
        self.assertFalse(self.db.clear_dismissed('github', 'https://x'))


# ---------------------------------------------------------------------------
# 3. apply_corrections — GitHub vault
# ---------------------------------------------------------------------------

class TestApplyCorrectionsGithub(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='p3_gh_')
        self.db_path = os.path.join(self.tmp, 'cache.db')
        self.db = NoteStateDB(self.db_path)
        self.vault = _github_vault(self.tmp)
        # baseline: record what exists now
        note_state.record_baseline_if_empty('github', self.vault,
                                            db_path=self.db_path)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _classify_and_apply(self):
        changes = classify_changes('github', self.vault, db_path=self.db_path)
        return changes, apply_corrections('github', self.vault, changes,
                                          self.db)

    def test_moved_note_is_a_correction(self):
        a = os.path.join(self.vault, CATEGORY_FOLDERS['Agents'], 'a.md')
        dest_dir = os.path.join(self.vault, CATEGORY_FOLDERS['Scraping'])
        os.makedirs(dest_dir, exist_ok=True)
        shutil.move(a, os.path.join(dest_dir, 'a.md'))

        changes, applied = self._classify_and_apply()
        self.assertEqual(len(changes['moved']), 1)
        self.assertEqual(applied['moved_applied'], 1)

        new_path = os.path.join(dest_dir, 'a.md')
        content = _read(new_path)
        self.assertIn('category: "Scraping"', content)
        self.assertIn('category_locked: true', content)
        self.assertNotIn('category: "Agents"', content)

        # state row refreshed: new path, new category, locked
        row = self.db.row_for('github', 'https://github.com/o/repo1')
        self.assertEqual(row['path'], new_path)
        self.assertEqual(row['category'], 'Scraping')
        self.assertTrue(row['locked'])

        # one corrections-log row
        self.assertEqual(self.db.correction_count('github'), 1)
        hist = self.db.corrections_for('github', 'https://github.com/o/repo1')
        self.assertEqual(hist[0]['to_category'], 'Scraping')

    def test_second_run_changes_nothing(self):
        a = os.path.join(self.vault, CATEGORY_FOLDERS['Agents'], 'a.md')
        dest_dir = os.path.join(self.vault, CATEGORY_FOLDERS['Scraping'])
        os.makedirs(dest_dir, exist_ok=True)
        shutil.move(a, os.path.join(dest_dir, 'a.md'))
        self._classify_and_apply()

        changes2, applied2 = self._classify_and_apply()
        self.assertEqual(changes2['moved'], [])
        self.assertEqual(applied2['moved_applied'], 0)
        self.assertEqual(applied2['dismissed'], 0)
        self.assertEqual(self.db.correction_count('github'), 1)  # not doubled

    def test_edited_note_is_report_only(self):
        b = os.path.join(self.vault, CATEGORY_FOLDERS['Agents'], 'b.md')
        content = _read(b)
        _write(b, content + "\nA hand-written line.\n")

        changes, applied = self._classify_and_apply()
        self.assertEqual(len(changes['edited']), 1)
        self.assertEqual(applied['moved_applied'], 0)
        # file untouched by the app
        self.assertIn('A hand-written line.', _read(b))
        # no lock, no correction
        row = self.db.row_for('github', 'https://github.com/o/repo2')
        self.assertFalse(row['locked'])
        self.assertEqual(self.db.correction_count('github'), 0)

    def test_deleted_note_is_dismissed(self):
        a = os.path.join(self.vault, CATEGORY_FOLDERS['Agents'], 'a.md')
        os.remove(a)

        changes, applied = self._classify_and_apply()
        self.assertEqual(len(changes['deleted']), 1)
        self.assertEqual(applied['dismissed'], 1)
        self.assertTrue(
            self.db.is_dismissed('github', 'https://github.com/o/repo1'))

        # second run: already dismissed, nothing new
        changes2, applied2 = self._classify_and_apply()
        self.assertEqual(applied2['dismissed'], 0)

    def test_duplicates_are_flagged_not_touched(self):
        a = os.path.join(self.vault, CATEGORY_FOLDERS['Agents'], 'a.md')
        dest_dir = os.path.join(self.vault, CATEGORY_FOLDERS['MCP'])
        os.makedirs(dest_dir, exist_ok=True)
        shutil.copy(a, os.path.join(dest_dir, 'a-copy.md'))

        changes, applied = self._classify_and_apply()
        self.assertEqual(len(changes['duplicates']), 1)
        self.assertEqual(applied['moved_applied'], 0)
        # BOTH copies untouched (still the original category line)
        self.assertIn('category: "Agents"', _read(a))
        self.assertIn('category: "Agents"',
                      _read(os.path.join(dest_dir, 'a-copy.md')))

    def test_move_into_unknown_folder_is_unmapped_report_only(self):
        a = os.path.join(self.vault, CATEGORY_FOLDERS['Agents'], 'a.md')
        weird = os.path.join(self.vault, 'NotACategory')
        os.makedirs(weird, exist_ok=True)
        shutil.move(a, os.path.join(weird, 'a.md'))

        changes, applied = self._classify_and_apply()
        self.assertEqual(len(changes['unmapped']), 1)
        self.assertEqual(applied['moved_applied'], 0)   # not a correction
        self.assertEqual(applied['moved_unmapped'], 1)
        # the note STAYS there, untouched (SPEC: "Keep it there")
        self.assertIn('category: "Agents"',
                      _read(os.path.join(weird, 'a.md')))
        row = self.db.row_for('github', 'https://github.com/o/repo1')
        self.assertFalse(row['locked'])

    def test_fingerprint_excludes_trailing_whitespace(self):
        # a whitespace-only tail never reads as an edit (Phase-1 regression
        # guard, still true through the Phase 3 actor)
        a = os.path.join(self.vault, CATEGORY_FOLDERS['Agents'], 'a.md')
        _write(a, _read(a) + "   \n\n")
        changes, _ = self._classify_and_apply()
        self.assertEqual(changes['edited'], [])


# ---------------------------------------------------------------------------
# 4. apply_corrections — Websites vault (taxonomy resolver)
# ---------------------------------------------------------------------------

class TestApplyCorrectionsWebsites(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='p3_web_')
        self.db_path = os.path.join(self.tmp, 'cache.db')
        self.db = NoteStateDB(self.db_path)
        self.taxonomy = _mini_taxonomy()
        self.vault = os.path.join(self.tmp, 'web')
        _write(os.path.join(self.vault, 'Design', 'UI/UX', 's.md'),
               _website_note('https://site.example/', 'Design', 'UI/UX'))
        _write(os.path.join(self.vault, '_review', 'wait.md'),
               _website_note('https://wait.example/', '', ''))
        note_state.record_baseline_if_empty('websites', self.vault,
                                            db_path=self.db_path)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _classify_and_apply(self):
        changes = classify_changes('websites', self.vault,
                                   db_path=self.db_path,
                                   taxonomy=self.taxonomy)
        return changes, apply_corrections('websites', self.vault, changes,
                                          self.db, taxonomy=self.taxonomy)

    def test_valid_nested_folder_is_not_unmapped(self):
        # the Phase 3 fix: taxonomy folders, not CATEGORY_FOLDERS, decide
        changes, _ = self._classify_and_apply()
        self.assertEqual(changes['unmapped'], [])
        self.assertEqual(changes['moved'], [])
        self.assertEqual(changes['edited'], [])

    def test_move_between_subcategories_updates_lines(self):
        src = os.path.join(self.vault, 'Design', 'UI/UX', 's.md')
        dest_dir = os.path.join(self.vault, 'Design', 'Inspiration')
        os.makedirs(dest_dir, exist_ok=True)
        shutil.move(src, os.path.join(dest_dir, 's.md'))

        changes, applied = self._classify_and_apply()
        self.assertEqual(applied['moved_applied'], 1)
        content = _read(os.path.join(dest_dir, 's.md'))
        self.assertIn('category: "Design"', content)
        self.assertIn('subcategory: "Inspiration"', content)
        self.assertNotIn('subcategory: "UI/UX"', content)
        self.assertIn('category_locked: true', content)
        row = self.db.row_for('websites',
                              normalize_url('https://site.example/'))
        self.assertEqual(row['subcategory'], 'Inspiration')
        self.assertTrue(row['locked'])

    def test_move_between_categories(self):
        src = os.path.join(self.vault, 'Design', 'UI/UX', 's.md')
        dest_dir = os.path.join(self.vault, 'Development', 'Tools')
        os.makedirs(dest_dir, exist_ok=True)
        shutil.move(src, os.path.join(dest_dir, 's.md'))

        changes, applied = self._classify_and_apply()
        self.assertEqual(applied['moved_applied'], 1)
        content = _read(os.path.join(dest_dir, 's.md'))
        self.assertIn('category: "Development"', content)
        self.assertIn('subcategory: "Tools"', content)

    def test_move_out_of_review_is_a_correction(self):
        src = os.path.join(self.vault, '_review', 'wait.md')
        dest_dir = os.path.join(self.vault, 'Design', 'Inspiration')
        os.makedirs(dest_dir, exist_ok=True)
        shutil.move(src, os.path.join(dest_dir, 'wait.md'))

        changes, applied = self._classify_and_apply()
        self.assertEqual(applied['moved_applied'], 1)
        content = _read(os.path.join(dest_dir, 'wait.md'))
        self.assertIn('category: "Design"', content)
        self.assertIn('subcategory: "Inspiration"', content)

    def test_unknown_website_folder_is_unmapped(self):
        src = os.path.join(self.vault, 'Design', 'UI/UX', 's.md')
        weird = os.path.join(self.vault, 'MyStuff')
        os.makedirs(weird, exist_ok=True)
        shutil.move(src, os.path.join(weird, 's.md'))

        changes, applied = self._classify_and_apply()
        self.assertEqual(len(changes['unmapped']), 1)
        self.assertEqual(applied['moved_applied'], 0)
        self.assertEqual(applied['moved_unmapped'], 1)
        self.assertIn('category: "Design"',
                      _read(os.path.join(weird, 's.md')))

    def test_unknown_subcategory_is_unmapped(self):
        src = os.path.join(self.vault, 'Design', 'UI/UX', 's.md')
        dest_dir = os.path.join(self.vault, 'Design', 'NotARealSub')
        os.makedirs(dest_dir, exist_ok=True)
        shutil.move(src, os.path.join(dest_dir, 's.md'))

        changes, _ = self._classify_and_apply()
        self.assertEqual(len(changes['unmapped']), 1)


# ---------------------------------------------------------------------------
# 5. run_start_check — baseline, idempotency, dry-run
# ---------------------------------------------------------------------------

class TestRunStartCheck(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='p3_start_')
        self.db_path = os.path.join(self.tmp, 'cache.db')
        self.db = NoteStateDB(self.db_path)
        self.vault = _github_vault(self.tmp)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)
        dryrun.disable()

    def test_baseline_then_nothing(self):
        result = run_start_check('github', self.vault, db=self.db)
        self.assertEqual(result['baseline'], 3)      # a, b + the _review note
        self.assertEqual(result['applied']['moved_applied'], 0)
        # second run: baseline already exists, nothing flagged
        result2 = run_start_check('github', self.vault, db=self.db)
        self.assertIsNone(result2['baseline'])
        for key in ('moved', 'edited', 'deleted', 'duplicates', 'unmapped'):
            self.assertEqual(result2['changes'][key], [], key)

    def test_full_cycle_via_run_start_check(self):
        # baseline FIRST (the first run after the feature ships), then the
        # owner moves a note, then the next run turns it into a correction
        run_start_check('github', self.vault, db=self.db)
        a = os.path.join(self.vault, CATEGORY_FOLDERS['Agents'], 'a.md')
        dest_dir = os.path.join(self.vault, CATEGORY_FOLDERS['Scraping'])
        os.makedirs(dest_dir, exist_ok=True)
        shutil.move(a, os.path.join(dest_dir, 'a.md'))

        result = run_start_check('github', self.vault, db=self.db)
        self.assertEqual(result['applied']['moved_applied'], 1)
        # idempotent: nothing on the second pass
        result2 = run_start_check('github', self.vault, db=self.db)
        self.assertEqual(result2['applied']['moved_applied'], 0)
        self.assertEqual(result2['changes']['moved'], [])

    def test_dry_run_records_nothing(self):
        run_start_check('github', self.vault, db=self.db)   # baseline first
        a = os.path.join(self.vault, CATEGORY_FOLDERS['Agents'], 'a.md')
        dest_dir = os.path.join(self.vault, CATEGORY_FOLDERS['Scraping'])
        os.makedirs(dest_dir, exist_ok=True)
        shutil.move(a, os.path.join(dest_dir, 'a.md'))

        dryrun.enable()
        result = run_start_check('github', self.vault, db=None,
                                 db_path=self.db_path)
        dryrun.disable()

        # detection still works…
        self.assertEqual(len(result['changes']['moved']), 1)
        self.assertTrue(result['applied'].get('dry_run'))
        # …but nothing was recorded and the file was NOT edited
        self.assertEqual(self.db.count('github'), 3)   # the baseline only
        self.assertEqual(self.db.correction_count('github'), 0)
        self.assertEqual(self.db.dismissed_count('github'), 0)
        self.assertIn('category: "Agents"',
                      _read(os.path.join(dest_dir, 'a.md')))

    def test_missing_vault_returns_none(self):
        self.assertIsNone(run_start_check('github', '', db=self.db))
        self.assertIsNone(
            run_start_check('github', os.path.join(self.tmp, 'nope'),
                            db=self.db))


# ---------------------------------------------------------------------------
# 6. move_summary_lines
# ---------------------------------------------------------------------------

class TestMoveSummaryLines(unittest.TestCase):

    def test_groups_and_pluralizes(self):
        corr = [
            {'from_category': 'Agents', 'to_category': 'Scraping'},
            {'from_category': 'Agents', 'to_category': 'Scraping'},
            {'from_category': 'MCP', 'to_category': 'Agents'},
        ]
        lines = move_summary_lines(corr)
        self.assertIn('2 notes moved from Agents to Scraping', lines)
        self.assertIn('1 note moved from MCP to Agents', lines)
        self.assertEqual(move_summary_lines([]), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)


# ---------------------------------------------------------------------------
# 7. The classifier locked-skip (through the real pipeline)
# ---------------------------------------------------------------------------

from tests.test_phase2 import _FakeFetch, _FakeLLM, _PipeCase  # noqa: E402
from gitcurator.core import website_pipeline as _wp  # noqa: E402


class TestClassifierLockedSkip(_PipeCase):
    """SPEC §6 Phase 3: 'The classifier skips locked notes.' The owner's
    placement of a locked note beats the model — exercised here through
    the real WebsitePipeline with the fake LLM (which always answers
    'Design / Assets & Resources' for anything it doesn't know)."""

    def test_locked_note_beats_the_model(self):
        ns_db = NoteStateDB(os.path.join(self.tmp, 'ns.db'))
        # The owner moved this note to Design/Print & Editorial Design by
        # hand (real taxonomy names); the fake model would have said
        # Design / "Assets & Resources" for this page.
        locked_path = os.path.join(self.vault, 'Design',
                                   'Print_&_Editorial_Design',
                                   'Test_Site.md')
        ns_db.upsert('websites', 'https://example.com/tool', locked_path,
                     'f' * 64, 'Design', 'Print & Editorial Design',
                     locked=True)
        try:
            pipe = _wp.WebsitePipeline(
                config={'website_vault_path': self.vault,
                        'web_domain_delay_s': 0},
                llm_call=_FakeLLM(),
                vault_index_has=lambda u: u in self.in_vault,
                state=self.db, fetch_fn=_FakeFetch(),
                note_state_db=ns_db,
                log=lambda m, l='info': self.logs.append((l, m)))
            r = pipe.process_link('https://example.com/tool?utm_source=x')
        finally:
            ns_db.close()

        self.assertEqual(r['outcome'], 'processed')
        # NOT the model's answer — the owner's locked placement:
        self.assertEqual(r['category'], 'Design')
        self.assertEqual(r['subcategory'], 'Print & Editorial Design')
        rel = os.path.relpath(r['note_path'], self.vault)
        self.assertTrue(rel.startswith('Design'))
        note = open(r['note_path'], encoding='utf-8').read()
        self.assertIn('category: "Design"', note)
        self.assertIn('subcategory: "Print & Editorial Design"', note)
        self.assertIn("locked note", " ".join(m for _, m in self.logs))


# ---------------------------------------------------------------------------
# 8. goodrepos + the _moc walk stay correct after a move
# ---------------------------------------------------------------------------

from gitcurator.integrations.goodrepos import GoodRepos  # noqa: E402


class TestGoodreposMocAfterMoves(unittest.TestCase):
    """SPEC §6 Phase 3: 'Confirm goodrepos.py and the _moc master-index
    outputs stay correct after moves.' Both read the vault the same way:
    folder walk + front-matter category line. After a correction both
    agree with the owner's placement — no stale category anywhere."""

    def setUp(self):
        # NOTE: the tmp prefix must never contain a special-folder mark
        # ('_moc', '_inbox', ...) — note_state._skip_dir matches substrings
        # in the whole walked path and would skip the entire vault.
        self.tmp = tempfile.mkdtemp(prefix='p3_moves_')
        self.db_path = os.path.join(self.tmp, 'cache.db')
        self.db = NoteStateDB(self.db_path)
        self.vault = _github_vault(self.tmp)
        run_start_check('github', self.vault, db=self.db)  # baseline

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_directory_and_state_agree_after_a_move(self):
        a = os.path.join(self.vault, CATEGORY_FOLDERS['Agents'], 'a.md')
        dest_dir = os.path.join(self.vault, CATEGORY_FOLDERS['Scraping'])
        os.makedirs(dest_dir, exist_ok=True)
        shutil.move(a, os.path.join(dest_dir, 'a.md'))
        run_start_check('github', self.vault, db=self.db)  # correction

        # 1. goodrepos: listed under the NEW folder, front-matter agrees
        gr = GoodRepos(vault_path=self.vault)
        cats = gr.scan_vault()
        self.assertIn(CATEGORY_FOLDERS['Scraping'], cats)
        notes = cats[CATEGORY_FOLDERS['Scraping']]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].category, 'Scraping')
        self.assertEqual(notes[0].rel_path,
                         CATEGORY_FOLDERS['Scraping'] + '/a.md')

        # 2. the same walk the _moc generator uses (note_state.scan_vault):
        #    new path, new category, at the owner's placement
        moved = [n for n in scan_vault(self.vault)
                 if n['source_url'] == 'https://github.com/o/repo1'][0]
        self.assertEqual(moved['category'], 'Scraping')
        self.assertEqual(os.path.dirname(moved['path']), dest_dir)

        # 3. no stale category line anywhere in the note
        content = _read(moved['path'])
        self.assertNotIn('category: "Agents"', content)
        self.assertIn('category_locked: true', content)



# ---------------------------------------------------------------------------
# 9. tools/backfill_websites.py — resumable, polite, dry-run
# ---------------------------------------------------------------------------

from argparse import Namespace  # noqa: E402
from gitcurator.tools import backfill_websites as bw  # noqa: E402


def _ns(csv_path, **kw):
    base = dict(csv=csv_path, limit=30, delay=0, dry_run=False, report='',
                provider='', api_url='', api_key='', model='', timeout=0)
    base.update(kw)
    return Namespace(**base)


class TestBackfillTool(unittest.TestCase):

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix='p3_backfill_')
        self.vault = os.path.join(self.tmp, 'websites')
        os.makedirs(self.vault, exist_ok=True)
        # The tool reads config.json / cache.db from APP_DIR — point the
        # module's APP_DIR at the temp dir (test isolation).
        self._real_app_dir = bw.APP_DIR
        bw.APP_DIR = self.tmp
        with open(os.path.join(self.tmp, 'config.json'), 'w',
                  encoding='utf-8') as f:
            import json
            json.dump({'website_vault_path': self.vault,
                       'llm_provider': 'cloud',
                       'cloud_api_url': 'http://127.0.0.1:9/v1',
                       'cloud_model': 'test'}, f)
        self.csv = os.path.join(self.tmp, 'links.csv')
        with open(self.csv, 'w', encoding='utf-8', newline='') as f:
            f.write("URL,Title\n"
                    "https://example.com/tool?utm_source=x,Tool\n"
                    "https://another.example/page,Page\n"
                    "https://github.com/o/repo,Repo\n"
                    "https://someone.github.io/site,Pages\n"
                    "https://example.com/tool,DUPE\n"
                    "https://gist.github.com/u/abc123,Gist\n")

    def tearDown(self):
        bw.APP_DIR = self._real_app_dir
        dryrun.disable()
        dryrun.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, **kw):
        out = []
        code = bw.run(_ns(self.csv, **kw), llm=_FakeLLM(),
                      fetch_fn=_FakeFetch(), sleep_fn=lambda s: None,
                      progress=out.append)
        return code, out

    def _vault_notes(self):
        found = []
        for root, dirs, files in os.walk(self.vault):
            dirs[:] = [d for d in dirs
                       if d not in ('_moc', '_inbox', 'attachments')]
            for fn in files:
                if fn.endswith('.md'):
                    found.append(os.path.join(root, fn))
        return found

    def test_routing_and_notes(self):
        code, out = self._run()
        self.assertEqual(code, 0)
        text = "\n".join(out)
        self.assertIn('3 website link(s)', text)          # incl. the gist
        self.assertIn('2 GitHub-pipeline link(s) excluded', text)
        self.assertIn('1 duplicate(s) removed', text)
        # v0.28.0 — THE LAW: the gist (GitHub group) is excluded at the
        # CSV layer, counted as banned, never fetched, never noted.
        self.assertIn('1 banned-domain link(s) excluded (the law)', text)
        notes = self._vault_notes()
        # tool + page + the Website Directory (v0.28.0 — no gist note!)
        self.assertEqual(len(notes), 3)
        self.assertFalse(any('gist' in open(n, encoding='utf-8').read()
                              for n in notes))
        directory = os.path.join(self.vault, '000 📚 Website Directory.md')
        self.assertTrue(os.path.exists(directory))
        dir_text = open(directory, encoding='utf-8').read()
        # both sites are listed with their live links (the fake LLM names
        # them all "Test Site", so assert by source URL)
        self.assertIn('[↗](https://example.com/tool)', dir_text)
        self.assertIn('[↗](https://another.example/page)', dir_text)
        # checkpoint: everything handled, resumable (the banned gist too)
        st = bw.BackfillState(os.path.join(self.tmp, 'cache.db'))
        counts = st.counts()
        st.close()
        self.assertEqual(counts.get('done'), 3)

    def test_resume_second_run_does_nothing(self):
        code1, _ = self._run()
        self.assertEqual(code1, 0)
        before = self._vault_notes()
        code2, out2 = self._run()
        self.assertEqual(code2, 0)
        self.assertIn('0 processed', "\n".join(out2))
        self.assertEqual(self._vault_notes(), before)     # no duplicates

    def test_interrupted_run_resumes(self):
        # The fetch raises KeyboardInterrupt on the SECOND link — the
        # checkpoint keeps the first, the re-run finishes the rest.
        class _InterruptingFetch(_FakeFetch):
            def __init__(self):
                super().__init__()
                self.n = 0

            def __call__(self, url, **kw):
                self.n += 1
                if self.n == 2:
                    raise KeyboardInterrupt
                return super().__call__(url, **kw)

        out = []
        code = bw.run(_ns(self.csv), llm=_FakeLLM(),
                      fetch_fn=_InterruptingFetch(),
                      sleep_fn=lambda s: None, progress=out.append)
        self.assertEqual(code, 130)
        # the first site note + the freshly rebuilt directory (v0.28.0)
        self.assertEqual(len(self._vault_notes()), 2)

        code2, out2 = self._run()
        self.assertEqual(code2, 0)
        # only the interrupted page remains — the gist is checkpointed
        # as banned (never retried), the tool as done
        self.assertIn('1 processed', "\n".join(out2))
        # both sites + the directory
        self.assertEqual(len(self._vault_notes()), 3)

    def test_limit_one_per_batch(self):
        code, out = self._run(limit=1)
        self.assertEqual(code, 0)
        self.assertIn('1 processed', "\n".join(out))
        # 1 site note + the directory (v0.28.0)
        self.assertEqual(len(self._vault_notes()), 2)
        code2, out2 = self._run(limit=1)
        self.assertIn('1 processed', "\n".join(out2))
        # 2 site notes + the directory
        self.assertEqual(len(self._vault_notes()), 3)

    def test_dry_run_records_nothing(self):
        code, out = self._run(dry_run=True)
        self.assertEqual(code, 0)
        self.assertEqual(self._vault_notes(), [])         # nothing written
        # nothing recorded in the real cache.db either (shadow was used)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'cache.db')))

