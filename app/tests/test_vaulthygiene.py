"""tests/test_vaulthygiene.py — v0.66.0 THE GRAPH'S CLEAN HANDS.

The owner's report (session, verbatim): "In the github projects repo,
the app created some empty notes, which also changed the graph look of
the vault. is it necessary or some kind of bug? there are many empty
notes, which when you want to delete them, obsidian [warns] that some
notes are linked to it. Fix it."

The archaeology: the v25-era master-index/MOC generator wrote
``_index.md`` + ``_moc/*.md`` hub notes whose ``[[wiki-links]]`` welded
every repo note into their graph, linked banished notes hidden inside
``.trash`` (the walk never skipped it — GHOST nodes), and clicking a
ghost link birthed an EMPTY note that Obsidian then refuses to delete
cleanly. v0.66.0 retires the generator and replaces it with the vault
hygiene pass:

  1. app-owned scaffold (``_index.md`` / ``_moc/*.md``, proven by
     frontmatter) is REMOVED — the backlink walls fall;
  2. content-free empty stub notes are RETIRED to
     ``.trash/empty-stubs`` (recoverable, invisible);
  3. legacy root reports (``_processing_report_*.md`` /
     ``processing_summary_*.txt``) are retired to
     ``.trash/retired-reports``; new reports live in ``app/reports``.

Laws under test: the owner's writing is sacred (a handwritten note,
even a short one, is never touched); an owner file inside ``_moc/``
keeps the folder; nothing outside the vault is written; the batch
itself no longer writes ANY .md to the vault root.
"""

import os
import shutil
import tempfile
import unittest

from gitcurator.core import dryrun
from gitcurator.gui.worker.reports import WorkerReportsMixin


class _Log:
    """Signal stub: collects (msg, level) pairs; never raises."""

    def __init__(self):
        self.lines = []

    def emit(self, msg, level='info'):
        self.lines.append((str(msg), level))


class _Worker(WorkerReportsMixin):
    """The bareest harness the mixin needs (config + log signal)."""

    def __init__(self, config):
        self.config = config
        self.log_message = _Log()


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    return path


def _read(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


class VaultHygieneTestBase(unittest.TestCase):

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix='hygiene-')
        self.vault = os.path.join(self.tmp, 'GitHubProjects')
        os.makedirs(os.path.join(self.vault, 'Tools', 'Automation'))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- the polluted shape (the owner's machine, distilled) -----------

    def _pollute(self):
        """The owner's vault shape: the v25 scaffold, ghost-born empty
        stubs, legacy root reports — and the notes that must SURVIVE."""
        v = self.vault
        # 1. the app-owned scaffold (the ghost-link breeders)
        _write(os.path.join(v, '_index.md'),
               '---\ntype: master-index\ntotal_projects: 700\n---\n'
               '# Projects Master Index\n- [[agent_Automation_python]]\n'
               '- [[banished_repo_Automation_rust]]\n')
        _write(os.path.join(v, '_moc', 'Automation.md'),
               '---\ntype: moc\ncategory: Automation\n---\n'
               '# Automation\n- [[agent_Automation_python]]\n')
        # an owner file squatting in _moc — sacred, keeps the folder
        self.owner_moc_file = _write(
            os.path.join(v, '_moc', 'my-own-notes.md'),
            '# My own map\n- something I wrote by hand\n')
        # 2. the ghost-born empty stubs (heading-only, no frontmatter)
        self.stub_root = _write(
            os.path.join(v, 'subtitle-translator_LLM-Tools_typescript.md'),
            '# subtitle-translator LLM-Tools typescript\n')
        self.stub_folder = _write(
            os.path.join(v, 'Tools', 'Automation',
                         'ghost_Automation_python.md'),
            '\n\n')
        # 3. the legacy root reports
        self.old_report = _write(
            os.path.join(v, '_processing_report_20260101_000001.md'),
            '# Processing Report — 2026-01-01\n| table |\n')
        self.old_summary = _write(
            os.path.join(v, 'processing_summary_20260101_000001.txt'),
            'SUMMARY\n')
        # 4. the survivors
        self.real_note = _write(
            os.path.join(v, 'Tools', 'Automation',
                         'agent_Automation_python.md'),
            '---\nsource: "https://github.com/o/agent"\ncategory: '
            '"Automation"\nmanaged_by: gitcurator\n---\n# agent\n\n'
            'A real curated note with a body.\n')
        self.owner_note = _write(
            os.path.join(v, 'Tools', 'Automation', 'my-thoughts.md'),
            '# Just a heading I wrote myself\n')
        self.owner_rich = _write(
            os.path.join(v, 'ideas.md'),
            '# Ideas\n\n- a real list of my own\n')
        # the manifest + undo list are working files, not notes
        _write(os.path.join(v, 'links_manifest.json'), '{}')
        _write(os.path.join(v, '_undo_last_batch.txt'), '')


class TestHygienePass(VaultHygieneTestBase):

    def _run(self):
        worker = _Worker({'vault_path': self.vault})
        worker._vault_hygiene_pass()
        return worker

    def test_scaffold_removed_and_backlinks_healed(self):
        self._pollute()
        self._run()
        self.assertFalse(os.path.exists(
            os.path.join(self.vault, '_index.md')))
        self.assertFalse(os.path.exists(
            os.path.join(self.vault, '_moc', 'Automation.md')))

    def test_owner_file_in_moc_keeps_folder(self):
        self._pollute()
        self._run()
        self.assertTrue(os.path.isfile(self.owner_moc_file))

    def test_empty_stubs_retired_recoverably(self):
        self._pollute()
        self._run()
        self.assertFalse(os.path.exists(self.stub_root))
        self.assertFalse(os.path.exists(self.stub_folder))
        retired_root = os.path.join(
            self.vault, '.trash', 'empty-stubs',
            'subtitle-translator_LLM-Tools_typescript.md')
        retired_folder = os.path.join(
            self.vault, '.trash', 'empty-stubs',
            'ghost_Automation_python.md')
        self.assertTrue(os.path.isfile(retired_root))
        self.assertTrue(os.path.isfile(retired_folder))
        # bytes preserved — recoverable by hand
        self.assertIn('subtitle-translator', _read(retired_root))

    def test_legacy_root_reports_retired(self):
        self._pollute()
        self._run()
        self.assertFalse(os.path.exists(self.old_report))
        self.assertFalse(os.path.exists(self.old_summary))
        self.assertTrue(os.path.isfile(os.path.join(
            self.vault, '.trash', 'retired-reports',
            '_processing_report_20260101_000001.md')))
        self.assertTrue(os.path.isfile(os.path.join(
            self.vault, '.trash', 'retired-reports',
            'processing_summary_20260101_000001.txt')))

    def test_real_notes_and_owner_writing_survive(self):
        self._pollute()
        self._run()
        self.assertTrue(os.path.isfile(self.real_note))
        self.assertTrue(os.path.isfile(self.owner_note))
        self.assertTrue(os.path.isfile(self.owner_rich))
        # working files stay
        self.assertTrue(os.path.isfile(os.path.join(
            self.vault, 'links_manifest.json')))
        self.assertTrue(os.path.isfile(os.path.join(
            self.vault, '_undo_last_batch.txt')))

    def test_a_stub_with_frontmatter_is_never_touched(self):
        # frontmatter = somebody's file, even when the body is empty
        guarded = _write(
            os.path.join(self.vault, 'Tools', 'Automation',
                         'guarded_Automation_python.md'),
            '---\nsource: "https://github.com/o/guarded"\n---\n')
        self._run()
        self.assertTrue(os.path.isfile(guarded))

    def test_content_free_note_with_a_non_app_name_is_never_touched(self):
        # THE SACRED LAW: an owner's own empty/heading-only note whose
        # filename does not carry the app's note shape stays put
        guarded = _write(
            os.path.join(self.vault, 'Tools', 'Automation',
                         'my-thoughts.md'),
            '# Just a heading I wrote myself\n')
        blank = _write(
            os.path.join(self.vault, 'Tools', 'Automation',
                         'scratchpad.md'), '')
        self._run()
        self.assertTrue(os.path.isfile(guarded))
        self.assertTrue(os.path.isfile(blank))

    def test_a_stub_with_body_text_is_never_touched(self):
        guarded = _write(
            os.path.join(self.vault, 'Tools', 'Automation', 'words.md'),
            '# words\n\na single line of real writing\n')
        self._run()
        self.assertTrue(os.path.isfile(guarded))

    def test_two_headings_is_writing_not_a_stub(self):
        guarded = _write(
            os.path.join(self.vault, 'two_Automation.md'), '# one\n# two\n')
        self._run()
        self.assertTrue(os.path.isfile(guarded))

    def test_clean_vault_reports_clean_and_touches_nothing(self):
        self._pollute()
        # remove the pollution by hand → only survivors remain
        shutil.rmtree(os.path.join(self.vault, '_moc'))
        os.remove(os.path.join(self.vault, '_index.md'))
        os.remove(self.stub_root)
        os.remove(self.stub_folder)
        os.remove(self.old_report)
        os.remove(self.old_summary)
        before = sorted(os.listdir(self.vault))
        worker = self._run()
        after = sorted(os.listdir(self.vault))
        self.assertEqual(before, after)
        self.assertTrue(any('clean' in m for m, _ in worker.log_message.lines))

    def test_idempotent_second_run_is_a_noop(self):
        self._pollute()
        self._run()
        worker = self._run()   # again
        self.assertTrue(any('clean' in m for m, _ in worker.log_message.lines))
        self.assertTrue(os.path.isfile(self.real_note))

    def test_no_vault_configured_is_a_silent_noop(self):
        worker = _Worker({'vault_path': ''})
        worker._vault_hygiene_pass()      # must not raise
        worker = _Worker({'vault_path': os.path.join(self.tmp, 'nope')})
        worker._vault_hygiene_pass()      # must not raise

    def test_the_log_tells_the_owner_the_story(self):
        self._pollute()
        worker = self._run()
        joined = ' '.join(m for m, _ in worker.log_message.lines)
        self.assertIn('master index', joined)
        self.assertIn('empty stub', joined)
        self.assertIn('legacy report', joined)

    def test_dry_run_rehearses_without_touching_the_vault(self):
        self._pollute()
        dryrun.enable()
        try:
            worker = _Worker({'vault_path': self.vault})
            worker._vault_hygiene_pass()
        finally:
            dryrun.disable()
        # nothing actually moved
        self.assertTrue(os.path.isfile(
            os.path.join(self.vault, '_index.md')))
        self.assertTrue(os.path.exists(self.stub_root))
        self.assertTrue(os.path.exists(self.old_report))

    def test_a_handwritten_index_is_never_removed(self):
        # an _index.md WITHOUT the app's type frontmatter is the owner's
        handwritten = _write(
            os.path.join(self.vault, '_index.md'),
            '# My own index\n- [[something]]\n')
        self._run()
        self.assertTrue(os.path.isfile(handwritten))

    def test_a_handwritten_moc_is_never_removed(self):
        handwritten = _write(
            os.path.join(self.vault, '_moc', 'mine.md'),
            '# My own MOC\n')
        self._run()
        self.assertTrue(os.path.isfile(handwritten))


class TestStubClassifier(unittest.TestCase):
    """The content-free classifier, edge by edge."""

    def test_empty_string_is_a_stub(self):
        self.assertTrue(WorkerReportsMixin._is_empty_stub(''))

    def test_whitespace_only_is_a_stub(self):
        self.assertTrue(WorkerReportsMixin._is_empty_stub('\n\n \t\n'))

    def test_single_heading_is_a_stub(self):
        self.assertTrue(
            WorkerReportsMixin._is_empty_stub('# a name\n\n'))

    def test_heading_without_space_marker_is_a_stub(self):
        # a bare '#' line counts as a heading, not content
        self.assertTrue(WorkerReportsMixin._is_empty_stub('#\n'))

    def test_frontmatter_is_never_a_stub(self):
        self.assertFalse(
            WorkerReportsMixin._is_empty_stub('---\nsource: x\n---\n'))

    def test_body_text_is_never_a_stub(self):
        self.assertFalse(
            WorkerReportsMixin._is_empty_stub('# t\n\nreal words\n'))

    def test_list_is_never_a_stub(self):
        self.assertFalse(
            WorkerReportsMixin._is_empty_stub('# t\n\n- an item\n'))

    def test_two_headings_are_writing(self):
        self.assertFalse(
            WorkerReportsMixin._is_empty_stub('# one\n# two\n'))

    def test_lone_hash_is_treated_as_empty(self):
        # '#' alone is not a heading worth keeping — content-free either way
        self.assertTrue(WorkerReportsMixin._is_empty_stub('#'))


class TestNoteNameShape(unittest.TestCase):
    """The app-note-name gate: the ghost-link children carry the app's
    ``<repo>_<Category>_<tag>.md`` shape; an owner's filename does not."""

    def test_canonical_repo_note_shape_matches(self):
        self.assertTrue(WorkerReportsMixin._has_note_name_shape(
            'subtitle-translator_LLM-Tools_typescript.md'))

    def test_two_segment_shape_matches(self):
        self.assertTrue(WorkerReportsMixin._has_note_name_shape(
            'agent_Automation.md'))

    def test_owner_filename_does_not_match(self):
        self.assertFalse(WorkerReportsMixin._has_note_name_shape(
            'my-thoughts.md'))
        self.assertFalse(WorkerReportsMixin._has_note_name_shape(
            'ideas.md'))
        self.assertFalse(WorkerReportsMixin._has_note_name_shape(
            'subtitle-translator.md'))

    def test_random_underscore_name_does_not_match(self):
        self.assertFalse(WorkerReportsMixin._has_note_name_shape(
            'my_automaton_notes.md'))

    def test_no_underscore_does_not_match(self):
        self.assertFalse(WorkerReportsMixin._has_note_name_shape(
            'Automation.md'))


if __name__ == '__main__':
    unittest.main()
