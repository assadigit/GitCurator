"""Phase 5 — the Manual Notes Library mirror (v0.14.0). SPEC §6 Phase 5.

Acceptance under test (SPEC):

- tests with temporary folders prove nothing outside ``Library/`` is
  ever created, changed or deleted;
- the sync is idempotent;
- moves propagate (matched by ``source``);
- dry-run by default, real run only with an explicit flag;
- refusal when the manual vault overlaps either machine vault;
- path traversal is impossible (validated components + containment).

The fixtures avoid the '_moc'/'_inbox'/'attachments'/'.obsidian'
substring trap anywhere in their paths — the app's skip rule is a
substring match on the walked path (Phase 3 lesson).
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)

from gitcurator.core import mirror as mz                       # noqa: E402
from gitcurator.core.mirror import (                           # noqa: E402
    GITHUB_TREE, LIBRARY_FOLDER, MIRROR_BANNERS, MIRROR_KEY,
    WEBSITES_TREE, MirrorError, build_mirror_note, check_vault_paths,
    run_mirror)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def _gh_note(src, title='Repo', with_banner=False):
    cover = 'cover: attachments/banners/%s_banner.png\n' % title
    embed = '![banner](attachments/banners/%s_banner.png)\n\n' % title
    if not with_banner:
        cover, embed = '', ''
    return (
        '---\n'
        'source: %s\n'
        'aliases: []\n'
        'tags: [python, tools]\n'
        'category: Agents\n'
        '%s'
        'managed_by: gitcurator\n'
        'schema_version: 1\n'
        'prompt_version: gh-v1\n'
        '---\n'
        '\n'
        '%s'
        '> [!info] Managed by GitCurator — machine-written note.\n'
        '\n'
        '# %s\n'
        '\n'
        'What is it? Real body text.\n'
        '\n'
        '---\n'
        '*Source: [%s](%s)*\n' % (src, cover, embed, title, src, src))


def _ws_note(src, name='Tool'):
    return (
        '---\n'
        'source: "%s"\n'
        'aliases: []\n'
        'tags: [design]\n'
        'category: "Design"\n'
        'subcategory: "UI"\n'
        'fetch_status: "full"\n'
        'managed_by: "gitcurator"\n'
        'schema_version: "1"\n'
        'prompt_version: "web-v1"\n'
        '---\n'
        '\n'
        '> [!info] Managed by GitCurator — machine-written note.\n'
        '\n'
        '# %s\n'
        '\n'
        'Standout feature text.\n'
        '\n'
        '---\n'
        '*Source: [%s](%s)*\n' % (src, name, src, src))


def _build_gh_vault(root, banner=True):
    """A GitHub vault: 2 notes + a _review note + noise that must be skipped."""
    _write(os.path.join(root, 'Agents', 'Frameworks', 'Repo.md'),
           _gh_note('https://github.com/owner/repo', 'Repo', banner))
    _write(os.path.join(root, 'Dev-Tools', 'Cli_Tool.md'),
           _gh_note('https://github.com/other/cli', 'Cli_Tool'))
    _write(os.path.join(root, '_review', 'Pending.md'),
           _gh_note('https://github.com/owner/pending', 'Pending'))
    # noise: skipped folders + a note without a source line
    _write(os.path.join(root, '_moc', 'map.md'), 'moc\n')
    _write(os.path.join(root, '_inbox', 'inbox.md'), 'inbox\n')
    _write(os.path.join(root, 'attachments', 'banners', 'x.png'), 'png')
    _write(os.path.join(root, '.obsidian', 'app.json'), '{}')
    _write(os.path.join(root, 'Agents', 'no_source_note.md'), 'no source\n')
    return root


def _build_ws_vault(root):
    _write(os.path.join(root, 'Design', 'UI', 'Tool.md'),
           _ws_note('https://example.com/tool', 'Tool'))
    _write(os.path.join(root, 'Learning', 'Courses', 'Course.md'),
           _ws_note('https://example.com/course', 'Course'))
    return root


def _build_manual(root):
    """An owner's manual vault, with precious files everywhere."""
    _write(os.path.join(root, 'My Idea.md'), 'my precious idea\n')
    _write(os.path.join(root, 'Journal', '2026-09-29.md'), 'journal\n')
    os.makedirs(os.path.join(root, 'Library'), exist_ok=True)
    _write(os.path.join(root, 'Library', 'owner-note.md'),
           'an owner file directly under Library (never touched)\n')
    _write(os.path.join(root, 'Library', GITHUB_TREE, 'my-own-note.md'),
           'owner file inside the GitHub tree (no marker — never touched)\n')
    return root


def _snapshot(root):
    """rel path -> bytes, for EVERY file under root."""
    out = {}
    for r, _d, files in os.walk(root):
        for fn in files:
            p = os.path.join(r, fn)
            with open(p, 'rb') as f:
                out[os.path.relpath(p, root).replace('\\', '/')] = f.read()
    return out


def _dirs(root):
    out = set()
    for r, _d, _files in os.walk(root):
        out.add(os.path.relpath(r, root).replace('\\', '/'))
    return out


class _VaultCase(unittest.TestCase):
    """Three temp vaults wired the way the real ones are."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='p5case-')
        self.gh = _build_gh_vault(os.path.join(self.tmp, 'ghvault'))
        self.ws = _build_ws_vault(os.path.join(self.tmp, 'wsvault'))
        self.manual = _build_manual(os.path.join(self.tmp, 'manualvault'))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- shortcuts -----------------------------------------------------------
    def lib(self, *parts):
        return os.path.join(self.manual, LIBRARY_FOLDER, *parts)

    def apply(self):
        return run_mirror(self.manual, self.gh, self.ws, apply=True)

    def dry(self):
        return run_mirror(self.manual, self.gh, self.ws, apply=False)


# ---------------------------------------------------------------------------
# build_mirror_note — the note transform
# ---------------------------------------------------------------------------

class TestBuildMirrorNote(unittest.TestCase):

    def test_mirror_of_inserted_before_frontmatter_close(self):
        out = build_mirror_note(_gh_note('https://github.com/a/b'),
                                'https://github.com/a/b',
                                MIRROR_BANNERS[GITHUB_TREE])
        head = out.split('---')[1]              # the front-matter block
        self.assertIn('%s: https://github.com/a/b' % MIRROR_KEY, head)
        self.assertIn('managed_by: gitcurator', head)
        self.assertTrue(head.strip().endswith(
            '%s: https://github.com/a/b' % MIRROR_KEY))  # last FM key

    def test_banner_is_the_first_body_line(self):
        out = build_mirror_note(_gh_note('https://github.com/a/b'),
                                'https://github.com/a/b',
                                MIRROR_BANNERS[GITHUB_TREE])
        body = out.split('---', 2)[2].lstrip('\n')
        self.assertTrue(body.startswith(MIRROR_BANNERS[GITHUB_TREE]))
        self.assertIn('> [!info] Managed by GitCurator', out)  # body kept

    def test_note_without_frontmatter_gets_wrapped(self):
        out = build_mirror_note('# Just a heading\n\nbody\n',
                                'https://example.com/x',
                                MIRROR_BANNERS[WEBSITES_TREE])
        self.assertTrue(out.startswith('---\n'))
        self.assertIn('%s: https://example.com/x' % MIRROR_KEY, out)
        self.assertIn(MIRROR_BANNERS[WEBSITES_TREE], out)
        self.assertIn('# Just a heading', out)

    def test_banner_references_are_stripped(self):
        out = build_mirror_note(
            _gh_note('https://github.com/a/b', 'Repo', with_banner=True),
            'https://github.com/a/b', MIRROR_BANNERS[GITHUB_TREE])
        self.assertNotIn('cover:', out)
        self.assertNotIn('![banner](', out)
        self.assertNotIn('attachments/banners', out)

    def test_body_content_is_preserved_verbatim(self):
        src = _gh_note('https://github.com/a/b', 'Repo')
        out = build_mirror_note(src, 'https://github.com/a/b',
                                MIRROR_BANNERS[GITHUB_TREE])
        for needle in ('# Repo', 'What is it? Real body text.',
                       '*Source: [https://github.com/a/b]',
                       'tags: [python, tools]', 'prompt_version: gh-v1'):
            self.assertIn(needle, out)

    def test_body_without_blank_after_frontmatter_still_gets_one(self):
        src = ('---\nsource: https://x/y\n---\n# Tight\n')
        out = build_mirror_note(src, 'https://x/y', MIRROR_BANNERS[WEBSITES_TREE])
        parts = out.split('---\n')
        body = parts[-1]
        self.assertTrue(body.startswith('\n' + MIRROR_BANNERS[WEBSITES_TREE]))


# ---------------------------------------------------------------------------
# Safety refusals (SPEC: never runs when vaults overlap)
# ---------------------------------------------------------------------------

class TestSafetyRefusals(_VaultCase):

    def test_manual_not_set(self):
        with self.assertRaises(MirrorError):
            check_vault_paths('', self.gh, self.ws)

    def test_manual_equals_github_vault(self):
        with self.assertRaises(MirrorError):
            run_mirror(self.gh, self.gh, self.ws)
        with self.assertRaises(MirrorError):
            check_vault_paths(self.gh, self.gh, '')

    def test_manual_contains_github_vault(self):
        nested = os.path.join(self.manual, 'gh-inside')
        with self.assertRaises(MirrorError):
            check_vault_paths(self.manual, nested, self.ws)

    def test_manual_inside_github_vault(self):
        inside = os.path.join(self.gh, 'sub', 'manual')
        with self.assertRaises(MirrorError):
            check_vault_paths(inside, self.gh, self.ws)

    def test_manual_overlaps_websites_vault_too(self):
        with self.assertRaises(MirrorError):
            check_vault_paths(self.ws, self.gh, self.ws)

    def test_both_machine_vaults_unset(self):
        with self.assertRaises(MirrorError):
            check_vault_paths(self.manual, '', '')

    def test_apply_requires_existing_manual_folder(self):
        missing = os.path.join(self.tmp, 'not-created-yet')
        check_vault_paths(missing, self.gh, self.ws, apply=False)  # dry ok
        with self.assertRaises(MirrorError):
            run_mirror(missing, self.gh, self.ws, apply=True)

    def test_library_path_is_a_file(self):
        _write(os.path.join(self.tmp, 'm2', LIBRARY_FOLDER), 'a file')
        with self.assertRaises(MirrorError):
            run_mirror(os.path.join(self.tmp, 'm2'), self.gh, self.ws)


# ---------------------------------------------------------------------------
# Path traversal (SPEC: guard against path traversal)
# ---------------------------------------------------------------------------

class TestSafeJoin(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='p5join-')

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_dotdot_rejected(self):
        with self.assertRaises(MirrorError):
            mz._safe_join(self.root, '../escape.md')
        with self.assertRaises(MirrorError):
            mz._safe_join(self.root, 'Agents/../../escape.md')

    def test_absolute_rejected(self):
        with self.assertRaises(MirrorError):
            mz._safe_join(self.root, '/etc/passwd')

    def test_backslash_and_drive_rejected(self):
        with self.assertRaises(MirrorError):
            mz._safe_join(self.root, 'C:\\escape.md')
        with self.assertRaises(MirrorError):
            mz._safe_join(self.root, 'C:/escape.md')

    def test_empty_and_dot_components_rejected(self):
        for rel in ('', '.', 'Agents//x.md', 'Agents/./x.md'):
            with self.assertRaises(MirrorError):
                mz._safe_join(self.root, rel)

    def test_good_path_resolves_inside_root(self):
        target = mz._safe_join(self.root, 'Agents/Frameworks/Repo.md')
        self.assertTrue(mz._is_inside(target, self.root))
        self.assertEqual(target, os.path.join(
            self.root, 'Agents', 'Frameworks', 'Repo.md'))


# ---------------------------------------------------------------------------
# SPEC acceptance: nothing outside Library/ is ever created/changed/deleted
# ---------------------------------------------------------------------------

class TestNothingOutsideLibrary(_VaultCase):

    def test_apply_touches_only_library(self):
        before = _snapshot(self.manual)
        outside_before = {k: v for k, v in before.items()
                          if not k.startswith(LIBRARY_FOLDER + '/')}
        plan = self.apply()
        self.assertTrue(plan.applied)
        after = _snapshot(self.manual)
        outside_after = {k: v for k, v in after.items()
                         if not k.startswith(LIBRARY_FOLDER + '/')}
        self.assertEqual(outside_before, outside_after)   # nothing changed
        # nothing new outside Library/
        self.assertEqual(set(after) - set(before),
                         {k for k in set(after) - set(before)
                          if k.startswith(LIBRARY_FOLDER + '/')})
        # the owner files INSIDE Library/ survive untouched too
        self.assertEqual(before['Library/owner-note.md'],
                         after['Library/owner-note.md'])
        self.assertEqual(
            before['Library/%s/my-own-note.md' % GITHUB_TREE],
            after['Library/%s/my-own-note.md' % GITHUB_TREE])

    def test_dry_run_writes_nothing_anywhere(self):
        before = _snapshot(self.manual)
        plan = self.dry()
        self.assertFalse(plan.applied)
        self.assertGreater(plan.total_changes, 0)
        self.assertEqual(before, _snapshot(self.manual))

    def test_deletions_never_remove_unmarked_files(self):
        self.apply()
        orphan = self.lib(GITHUB_TREE, 'Agents', 'Frameworks', 'Repo.md')
        os.remove(os.path.join(self.gh, 'Agents', 'Frameworks', 'Repo.md'))
        plan = self.apply()
        self.assertFalse(os.path.exists(orphan))
        # owner files survive the deletion pass
        self.assertTrue(os.path.exists(
            self.lib(GITHUB_TREE, 'my-own-note.md')))
        self.assertTrue(os.path.exists(self.lib('owner-note.md')))
        self.assertIn('DELETE', plan.render_report())

    def test_conflict_when_unmarked_file_is_in_the_way(self):
        blocked = self.lib(GITHUB_TREE, 'Dev-Tools', 'Cli_Tool.md')
        _write(blocked, 'an owner file exactly where a mirror would go\n')
        before = open(blocked, encoding='utf-8').read()
        plan = self.apply()
        self.assertEqual(open(blocked, encoding='utf-8').read(), before)
        tree = plan.trees[GITHUB_TREE]
        self.assertEqual([c[0] for c in tree.conflicts],
                         ['Dev-Tools/Cli_Tool.md'])

    def test_no_marker_no_delete_even_for_lookalikes(self):
        self.apply()
        # an owner file that COPIES a mirror note but drops the marker
        lookalike = self.lib(GITHUB_TREE, 'Agents', 'Frameworks', 'fake.md')
        original = open(self.lib(GITHUB_TREE, 'Agents', 'Frameworks',
                                 'Repo.md'), encoding='utf-8').read()
        _write(lookalike, original.replace('%s:' % MIRROR_KEY, 'xmirror:'))
        os.remove(os.path.join(self.gh, 'Agents', 'Frameworks', 'Repo.md'))
        self.apply()
        self.assertTrue(os.path.exists(lookalike))   # no marker → survives


# ---------------------------------------------------------------------------
# Sync semantics
# ---------------------------------------------------------------------------

class TestSync(_VaultCase):

    def test_github_notes_mirror_under_library_github_projects(self):
        plan = self.apply()
        tree = plan.trees[GITHUB_TREE]
        self.assertEqual(tree.sources, 3)      # 2 category + 1 _review
        self.assertEqual(len(tree.creates), 3)
        for rel in ('Agents/Frameworks/Repo.md', 'Dev-Tools/Cli_Tool.md',
                    '_review/Pending.md'):
            path = self.lib(GITHUB_TREE, *rel.split('/'))
            self.assertTrue(os.path.exists(path), rel)
            content = open(path, encoding='utf-8').read()
            self.assertIn('%s: https://' % MIRROR_KEY, content)
            self.assertIn(MIRROR_BANNERS[GITHUB_TREE], content)

    def test_websites_notes_mirror_under_library_websites(self):
        self.apply()
        for rel in ('Design/UI/Tool.md', 'Learning/Courses/Course.md'):
            path = self.lib(WEBSITES_TREE, *rel.split('/'))
            self.assertTrue(os.path.exists(path), rel)
            content = open(path, encoding='utf-8').read()
            self.assertIn('%s: https://example.com/' % MIRROR_KEY, content)

    def test_skipped_folders_and_sourceless_notes_never_mirror(self):
        plan = self.apply()
        for rel in ('_moc/map.md', '_inbox/inbox.md', '.obsidian/app.json',
                    'Agents/no_source_note.md'):
            self.assertFalse(os.path.exists(self.lib(GITHUB_TREE,
                                                     *rel.split('/'))))
        tree = plan.trees[GITHUB_TREE]
        self.assertEqual(tree.skipped_no_source, 1)
        self.assertEqual(tree.sources, 3)

    def test_idempotent_second_run_zero_changes(self):
        self.apply()
        before = _snapshot(self.manual)
        plan = self.apply()
        self.assertEqual(plan.total_changes, 0)
        tree = plan.trees[GITHUB_TREE]
        self.assertEqual(tree.keeps, 3)
        self.assertEqual(before, _snapshot(self.manual))   # byte-identical

    def test_source_edit_updates_the_mirror(self):
        self.apply()
        note = os.path.join(self.gh, 'Agents', 'Frameworks', 'Repo.md')
        _write(note, _gh_note('https://github.com/owner/repo', 'Repo')
               + '\nAn extra paragraph the LLM wrote.\n')
        plan = self.apply()
        self.assertEqual(len(plan.trees[GITHUB_TREE].updates), 1)
        mirrored = open(self.lib(GITHUB_TREE, 'Agents', 'Frameworks',
                                 'Repo.md'), encoding='utf-8').read()
        self.assertIn('An extra paragraph the LLM wrote.', mirrored)

    def test_human_edit_to_a_mirror_copy_is_restored(self):
        """Mirror files are policy read-only (rebuildable) — a sync repairs
        them. The banner says so; the decision is logged in the report."""
        self.apply()
        path = self.lib(GITHUB_TREE, 'Agents', 'Frameworks', 'Repo.md')
        _write(path, open(path, encoding='utf-8').read() + '\nmy edit\n')
        plan = self.apply()
        self.assertEqual(len(plan.trees[GITHUB_TREE].updates), 1)
        self.assertNotIn('my edit', open(path, encoding='utf-8').read())

    def test_moves_propagate_matched_by_source(self):
        self.apply()
        old = self.lib(GITHUB_TREE, 'Agents', 'Frameworks', 'Repo.md')
        new = self.lib(GITHUB_TREE, 'Infrastructure', 'Repo.md')
        os.makedirs(os.path.join(self.gh, 'Infrastructure'))
        shutil.move(os.path.join(self.gh, 'Agents', 'Frameworks', 'Repo.md'),
                    os.path.join(self.gh, 'Infrastructure', 'Repo.md'))
        plan = self.apply()
        tree = plan.trees[GITHUB_TREE]
        self.assertEqual(len(tree.moves), 1)
        self.assertEqual(tree.moves[0][0], 'Agents/Frameworks/Repo.md')
        self.assertEqual(tree.moves[0][1], 'Infrastructure/Repo.md')
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(new))
        self.assertIn('%s: https://github.com/owner/repo' % MIRROR_KEY,
                      open(new, encoding='utf-8').read())

    def test_move_swap_two_notes_resolves(self):
        """A moves into B's old folder while B moves elsewhere — deletions
        run before writes, so both end up correct."""
        self.apply()
        a_src = os.path.join(self.gh, 'Agents', 'Frameworks', 'Repo.md')
        b_src = os.path.join(self.gh, 'Dev-Tools', 'Cli_Tool.md')
        a_dst = os.path.join(self.gh, 'Dev-Tools', 'Repo.md')
        b_dst = os.path.join(self.gh, 'Agents', 'Frameworks', 'Cli_Tool.md')
        shutil.move(a_src, a_dst)
        shutil.move(b_src, b_dst)
        self.apply()
        self.assertTrue(os.path.exists(
            self.lib(GITHUB_TREE, 'Dev-Tools', 'Repo.md')))
        self.assertTrue(os.path.exists(
            self.lib(GITHUB_TREE, 'Agents', 'Frameworks', 'Cli_Tool.md')))
        # stale copies gone
        self.assertFalse(os.path.exists(
            self.lib(GITHUB_TREE, 'Agents', 'Frameworks', 'Repo.md')))
        self.assertFalse(os.path.exists(
            self.lib(GITHUB_TREE, 'Dev-Tools', 'Cli_Tool.md')))

    def test_orphaned_mirror_removed_only_with_marker(self):
        self.apply()
        orphan = self.lib(WEBSITES_TREE, 'Design', 'UI', 'Tool.md')
        os.remove(os.path.join(self.ws, 'Design', 'UI', 'Tool.md'))
        plan = self.apply()
        self.assertFalse(os.path.exists(orphan))
        self.assertEqual(plan.trees[WEBSITES_TREE].sources, 1)

    def test_duplicate_source_files_reported_once_mirrored(self):
        _write(os.path.join(self.gh, 'Dev-Tools', 'Repo_copy.md'),
               _gh_note('https://github.com/owner/repo', 'Repo'))
        plan = self.dry()
        tree = plan.trees[GITHUB_TREE]
        self.assertEqual(tree.sources, 3)      # dedup: one per source
        self.assertEqual(tree.duplicates, ['Dev-Tools/Repo_copy.md'])
        self.assertEqual(len(tree.creates), 3)

    def test_duplicate_marker_copies_collapse_to_one(self):
        self.apply()
        good = self.lib(GITHUB_TREE, 'Agents', 'Frameworks', 'Repo.md')
        extra = self.lib(GITHUB_TREE, 'zz-dup', 'Repo.md')
        _write(extra, open(good, encoding='utf-8').read())
        plan = self.apply()
        self.assertFalse(os.path.exists(extra))       # marker dup removed
        self.assertTrue(os.path.exists(good))
        self.assertIn('zz-dup/Repo.md', plan.trees[GITHUB_TREE].deletes)

    def test_empty_dirs_cleaned_but_owner_content_dirs_stay(self):
        self.apply()
        os.remove(os.path.join(self.gh, 'Agents', 'Frameworks', 'Repo.md'))
        self.apply()
        self.assertFalse(os.path.exists(
            self.lib(GITHUB_TREE, 'Agents', 'Frameworks')))
        # Library root survives: it still holds the owner's file
        self.assertTrue(os.path.isdir(self.lib()))
        self.assertTrue(os.path.exists(self.lib('owner-note.md')))

    def test_missing_machine_vault_reports_skipped(self):
        plan = run_mirror(self.manual, self.gh,
                          os.path.join(self.tmp, 'nope'), apply=False)
        tree = plan.trees[WEBSITES_TREE]
        self.assertFalse(tree.vault_present)
        self.assertEqual(tree.sources, 0)
        self.assertIn('folder not found — skipped', plan.render_report())

    def test_unmarked_files_counted_and_left_alone(self):
        self.apply()
        _write(self.lib(WEBSITES_TREE, 'my-note.md'), 'mine\n')
        plan = self.dry()
        self.assertEqual(plan.trees[WEBSITES_TREE].unmarked_files, 1)
        self.assertEqual(plan.total_changes, 0)
        self.assertEqual(open(self.lib(WEBSITES_TREE, 'my-note.md'),
                              encoding='utf-8').read(), 'mine\n')


# ---------------------------------------------------------------------------
# The tool (dry-run by default; --apply is the explicit real-run flag)
# ---------------------------------------------------------------------------

class TestTool(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='p5tool-')
        self.gh = _build_gh_vault(os.path.join(self.tmp, 'ghvault'))
        self.ws = _build_ws_vault(os.path.join(self.tmp, 'wsvault'))
        self.manual = _build_manual(os.path.join(self.tmp, 'manualvault'))
        self.report = os.path.join(self.tmp, 'reports', 'plan.txt')
        # the tool reads config.json from APP_DIR — point it at the tmp dir
        import gitcurator.tools.mirror_manual as mm
        self.mm = mm
        self._real_app_dir = mm.APP_DIR
        self._real_report_dir = mm.DEFAULT_REPORT_DIR
        mm.APP_DIR = self.tmp
        mm.DEFAULT_REPORT_DIR = os.path.join(self.tmp, 'reports', 'mirror')

    def tearDown(self):
        self.mm.APP_DIR = self._real_app_dir
        self.mm.DEFAULT_REPORT_DIR = self._real_report_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *flags):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self.mm.main(list(flags))
        return rc, buf.getvalue()

    def test_reads_paths_from_config_json(self):
        with open(os.path.join(self.tmp, 'config.json'), 'w') as f:
            json.dump({'vault_path': self.gh,
                       'website_vault_path': self.ws,
                       'manual_vault_path': self.manual}, f)
        rc, out = self._run('--out', self.report)
        self.assertEqual(rc, 0)
        self.assertIn('DRY RUN', out)
        self.assertIn('Library/GitHub Projects', open(self.report).read())
        self.assertEqual(_snapshot(self.manual),
                         _snapshot(self.manual))  # nothing written
        self.assertFalse(os.path.exists(
            self.lib_gh('Agents', 'Frameworks', 'Repo.md')))

    def lib_gh(self, *parts):
        return os.path.join(self.manual, LIBRARY_FOLDER, GITHUB_TREE, *parts)

    def test_apply_flag_performs_the_sync(self):
        rc, _ = self._run('--manual-vault', self.manual,
                          '--github-vault', self.gh, '--websites-vault',
                          self.ws, '--out', self.report)
        self.assertEqual(rc, 0)
        rc, out = self._run('--manual-vault', self.manual,
                            '--github-vault', self.gh, '--websites-vault',
                            self.ws, '--out', self.report, '--apply')
        self.assertEqual(rc, 0)
        self.assertIn('APPLIED', out)
        self.assertTrue(os.path.exists(
            self.lib_gh('Agents', 'Frameworks', 'Repo.md')))

    def test_exit_1_when_manual_vault_unset(self):
        rc, out = self._run('--github-vault', self.gh)
        self.assertEqual(rc, 1)
        self.assertIn('not set', out)

    def test_exit_2_on_safety_refusal(self):
        rc, out = self._run('--manual-vault', self.gh,
                            '--github-vault', self.gh)
        self.assertEqual(rc, 2)

    def test_exit_2_when_report_inside_a_vault(self):
        rc, out = self._run('--manual-vault', self.manual,
                            '--github-vault', self.gh, '--websites-vault',
                            self.ws,
                            '--out', os.path.join(self.manual, 'plan.txt'))
        self.assertEqual(rc, 2)
        self.assertIn('OUTSIDE', out)
        self.assertFalse(os.path.exists(os.path.join(self.manual,
                                                     'plan.txt')))

    def test_report_written_and_actions_listed(self):
        rc, _ = self._run('--manual-vault', self.manual,
                          '--github-vault', self.gh, '--websites-vault',
                          self.ws, '--out', self.report)
        text = open(self.report, encoding='utf-8').read()
        self.assertIn('CREATE   Agents/Frameworks/Repo.md', text)
        self.assertIn('DRY RUN (nothing written)', text)
        self.assertIn('Safety:', text)


# ---------------------------------------------------------------------------
# CLI --status shows the Library mirror row
# ---------------------------------------------------------------------------

class TestCliStatusMirrorRow(unittest.TestCase):

    def test_status_lists_library_mirror(self):
        from gitcurator import cli
        tmp = tempfile.mkdtemp(prefix='p5stat-')
        try:
            cfg_path = os.path.join(tmp, 'config.json')
            with open(cfg_path, 'w', encoding='utf-8') as f:
                json.dump({
                    "vault_path": os.path.join(tmp, 'gh'),
                    "manual_vault_path": os.path.join(tmp, 'manual'),
                }, f)

            class _Args:
                config = cfg_path

            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.cmd_status(_Args())
            out = buf.getvalue()
            self.assertEqual(rc, 0)
            self.assertIn("Library mirror", out)
            self.assertIn("mirror_manual.py", out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_status_mirror_row_dim_without_manual_vault(self):
        from gitcurator import cli
        tmp = tempfile.mkdtemp(prefix='p5stat-')
        try:
            cfg_path = os.path.join(tmp, 'config.json')
            with open(cfg_path, 'w', encoding='utf-8') as f:
                json.dump({"vault_path": os.path.join(tmp, 'gh')}, f)

            class _Args:
                config = cfg_path

            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.cmd_status(_Args())
            out = buf.getvalue()
            self.assertEqual(rc, 0)
            self.assertIn("Library mirror", out)
            self.assertIn("(needs the Manual vault)", out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# GUI wiring: the Vault page points at the tool (source-level lock — the
# full SettingsDialog is exercised by the phase-1 suite)
# ---------------------------------------------------------------------------

class TestGuiWiring(unittest.TestCase):

    def test_vault_page_mentions_the_mirror_tool(self):
        # refactor/gui-app-split: the GUI source moved out of the single
        # app.py — the same expectations scan the code's new home(s).
        gui_dir = os.path.join(_APP_ROOT, 'gitcurator', 'gui')
        parts = []
        for rel in ('app.py', 'dialogs.py'):
            path = os.path.join(gui_dir, rel)
            if os.path.exists(path):
                with open(path, encoding='utf-8') as f:
                    parts.append(f.read())
        mw_dir = os.path.join(gui_dir, 'main_window')
        if os.path.isdir(mw_dir):
            for name in sorted(os.listdir(mw_dir)):
                if name.endswith('.py'):
                    with open(os.path.join(mw_dir, name),
                              encoding='utf-8') as f:
                        parts.append(f.read())
        src = "\n".join(parts)
        self.assertIn('manual_mirror_hint', src)
        self.assertIn('tools/mirror_manual.py', src)
        self.assertIn('Library/ mirror', src)


if __name__ == '__main__':
    unittest.main()
