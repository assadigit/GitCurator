#!/usr/bin/env python3
"""
test_phase0.py — Phase 0 groundwork tests (v0.09.5).

Covers the three safety tools and the dry-run mechanism, all against
SYNTHETIC vaults in temp folders (SPEC.md non-negotiable #1: never touch
a real vault in tests):

  - core/dryrun.py: switch, log, helpers, shadow cache, report renderer
  - core/storage.py gate: atomic writes recorded instead of performed
  - the real worker write slice (note + inbox tables + link manifest):
    nothing lands on disk during a dry-run, everything lands when off
  - tools/scan_vault_edits.py: findings + strictly read-only + report
    outside the vault
  - tools/snapshot_vault.py: complete valid zip + vault untouched +
    refuses in-vault output
  - tools/pick_golden_links.py: diverse non-GitHub selection, column
    detection, determinism, edge cases
  - CLI --dry-run flag plumbing (parser, run_batch_visual, ProcessingWorker)

Run:  python -m unittest tests.test_phase0 -v
"""

import csv
import hashlib
import inspect
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

# Make the app dir importable no matter where we run from.
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from gitcurator.core import dryrun
from gitcurator.core import note_builder
from gitcurator.core import storage
from gitcurator.tools import pick_golden_links
from gitcurator.tools import scan_vault_edits
from gitcurator.tools import snapshot_vault
from gitcurator.cli import build_parser, run_batch_visual, _write_dryrun_report
# gui.app needs PyQt6 (installed by CI, same as tests.test_quarantine).
from gitcurator.gui.app import ProcessingWorker, write_inbox_links_by_platform, LinkTracker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hash_tree(root):
    """Digest of every file under root (path + content) — proves a tool
    changed nothing."""
    items = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in sorted(filenames):
            fpath = os.path.join(dirpath, name)
            rel = os.path.relpath(fpath, root)
            with open(fpath, 'rb') as f:
                items[rel] = hashlib.sha256(f.read()).hexdigest()
    return items


def _build_note(url="https://github.com/owner/repo", repo_name="repo",
                category_key="Agents"):
    """A REAL GitHub note from the REAL builder (ground truth for the
    scan tool's template detection — if note_builder's placeholder text
    ever changes, these tests fail until the tool is updated).

    v0.10.0 — Phase 1: new notes no longer contain the three human
    placeholder sections, so the LEGACY sections are APPENDED here — the
    scan tool's whole job is detecting human writing in notes created
    BEFORE v0.10.0, and the fixture must look like one of those."""
    note = note_builder.build_note(
        url=url, repo_name=repo_name, owner="owner", org_name="Some Org",
        stars=10, forks=2, commit_count=5, cred_score=80, org_rep=7,
        summary="A" * 80, tags=["cli"], category_key=category_key,
        confidence=90, how_it_works="how", core_value="val",
        features=["f1", "f2", "f3"], difference="diff",
        primary_language="Python", languages=["Python"],
        short_summary="Useful repo.", quality_issues=[], is_low_quality=False,
    )
    legacy_tail = (
        "\n## 💡 My Ideas & Notes\n[Add your personal thoughts here]\n\n"
        "## 📱 Social Signal (Manual)\n"
        "- **Source:** [Dropdown: Reddit/X/Instagram/GitHub Search/Other]\n"
        "- **Link:** [URL]\n- **Notes:** [Context]\n\n"
        "## 📔 Journal\n[Date] - [Your experiences]\n"
    )
    marker = "\n---\n*Source: [GitHub]("
    if marker in note:
        note = note.replace(marker, legacy_tail + marker, 1)
    return note


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    return path


# ---------------------------------------------------------------------------
# dryrun module
# ---------------------------------------------------------------------------

class TestDryRunModule(unittest.TestCase):

    def setUp(self):
        dryrun.disable()
        dryrun.clear()

    def tearDown(self):
        dryrun.disable()
        dryrun.clear()

    def test_default_off_and_helpers_perform_real_operations(self):
        tmp = tempfile.mkdtemp(prefix='curator-dry-')
        try:
            self.assertFalse(dryrun.is_enabled())
            dryrun.makedirs(os.path.join(tmp, 'a'), exist_ok=True)
            dryrun.write_text(os.path.join(tmp, 'a', 'f.txt'), 'hello')
            dryrun.append_text(os.path.join(tmp, 'a', 'f.txt'), '!')
            dryrun.write_text(os.path.join(tmp, 'mv.txt'), 'x')
            dryrun.move(os.path.join(tmp, 'mv.txt'), os.path.join(tmp, 'moved.txt'))
            self.assertEqual(dryrun.entry_count(), 0)  # nothing recorded
            with open(os.path.join(tmp, 'a', 'f.txt'), encoding='utf-8') as f:
                self.assertEqual(f.read(), 'hello!')
            self.assertTrue(os.path.exists(os.path.join(tmp, 'moved.txt')))
            dryrun.remove(os.path.join(tmp, 'moved.txt'))
            self.assertFalse(os.path.exists(os.path.join(tmp, 'moved.txt')))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_enabled_helpers_log_instead_of_touching_disk(self):
        tmp = tempfile.mkdtemp(prefix='curator-dry-')
        try:
            dryrun.enable()
            target = os.path.join(tmp, 'note.md')
            dryrun.makedirs(os.path.join(tmp, 'folder'), exist_ok=True)
            dryrun.write_text(target, '---\nsource: x\n---\n')
            dryrun.append_text(target, 'more')
            dryrun.write_text(os.path.join(tmp, 'mv.txt'), 'x')
            dryrun.move(os.path.join(tmp, 'mv.txt'), os.path.join(tmp, 'elsewhere.txt'))
            dryrun.remove(target)
            # disk untouched
            self.assertEqual(os.listdir(tmp), [])
            # log complete, with previews
            ops = [e['op'] for e in dryrun.entries()]
            self.assertIn('makedirs', ops)
            self.assertIn('write', ops)
            self.assertIn('append', ops)
            self.assertIn('move', ops)
            self.assertIn('remove', ops)
            previews = [e['preview'] for e in dryrun.entries()
                        if e['op'] == 'write']
            self.assertTrue(any('source: x' in p for p in previews),
                            f'previews missing content: {previews}')
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_enable_clears_previous_log_disable_keeps_entries(self):
        dryrun.enable()
        dryrun.record('write', '/tmp/x.md', content='abc')
        self.assertEqual(dryrun.entry_count(), 1)
        dryrun.disable()
        self.assertEqual(dryrun.entry_count(), 1)   # kept for the report
        self.assertFalse(dryrun.is_enabled())
        dryrun.enable()
        self.assertEqual(dryrun.entry_count(), 0)   # fresh log per run

    def test_storage_atomic_writes_are_gated(self):
        tmp = tempfile.mkdtemp(prefix='curator-dry-')
        try:
            dryrun.enable()
            p1 = os.path.join(tmp, 'vault', 'note.md')
            storage.atomic_write_text(p1, 'note body')
            storage.atomic_write_bytes(os.path.join(tmp, 'b.png'), b'\x89PNG')
            storage.write_config_file(os.path.join(tmp, 'config.json'),
                                      {"a": 1})
            self.assertEqual(os.listdir(tmp), [])   # nothing written at all
            self.assertEqual(dryrun.entry_count(), 3)
            # config writes are recorded too — a dry-run must not advance
            # last_processed_msg_id / model choices in the real config.
            ops = [e['path'] for e in dryrun.entries()]
            self.assertTrue(any(p.endswith('config.json') for p in ops))
            # and no temp leftovers from the gated atomic writers
            dryrun.disable()
            storage.atomic_write_text(p1, 'note body')
            with open(p1, encoding='utf-8') as f:
                self.assertEqual(f.read(), 'note body')
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_shadow_cache_reads_real_state_writes_are_discarded(self):
        tmp = tempfile.mkdtemp(prefix='curator-dry-')
        try:
            real = os.path.join(tmp, 'cache.db')
            con = sqlite3.connect(real)
            con.execute("CREATE TABLE t (k TEXT)")
            con.execute("INSERT INTO t VALUES ('real-state')")
            con.commit()
            con.close()

            dryrun.enable()
            shadow = dryrun.shadow_cache_path(real)
            self.assertNotEqual(os.path.realpath(shadow), os.path.realpath(real))
            # the shadow sees the real rows…
            con = sqlite3.connect(shadow)
            rows = con.execute("SELECT k FROM t").fetchall()
            self.assertEqual(rows, [('real-state',)])
            # …but writes land only in the shadow
            con.execute("INSERT INTO t VALUES ('dry-run-state')")
            con.commit()
            con.close()
            con = sqlite3.connect(real)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM t").fetchone()[0], 1)
            con.close()

            dryrun.disable()
            # after disable, the real cache is still untouched
            con = sqlite3.connect(real)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM t").fetchone()[0], 1)
            con.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_shadow_cache_missing_source_never_deadlocks(self):
        """Regression: the fresh-install case (no real cache.db yet) once
        deadlocked — shadow_cache_path held the log lock and then called
        record(), which takes the same lock. Caught live during the
        Phase 0 end-to-end demo. Run in a thread with a timeout so a
        regression FAILS instead of hanging the suite."""
        import threading
        tmp = tempfile.mkdtemp(prefix='curator-dry-')
        try:
            missing = os.path.join(tmp, 'does-not-exist.db')
            dryrun.enable()
            box = {}

            def _call():
                box['path'] = dryrun.shadow_cache_path(missing)

            t = threading.Thread(target=_call, daemon=True)
            t.start()
            t.join(timeout=5)
            self.assertFalse(t.is_alive(), 'shadow_cache_path deadlocked')
            # the returned path is usable: CacheDB-style open + table works
            con = sqlite3.connect(box['path'])
            con.execute("CREATE TABLE t (k TEXT)")
            con.close()
            # the fresh-install case is recorded as info, not a crash
            self.assertTrue(any(e['op'] == 'info' for e in dryrun.entries()))
            dryrun.disable()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_report_markdown_renders_entries(self):
        dryrun.enable()
        dryrun.record('write', '/vault/Notes/note.md', content='---\nsource: x\n---')
        dryrun.record('makedirs', '/vault/Notes', note='create folder')
        md = dryrun.render_report_markdown("Test report")
        self.assertIn('Nothing below was written', md)
        self.assertIn('note.md', md)
        self.assertIn('makedirs', md)
        self.assertIn('2 operation(s)', md)


# ---------------------------------------------------------------------------
# the real worker write slice, dry-run ON vs OFF
# ---------------------------------------------------------------------------

class TestDryRunBatchSlice(unittest.TestCase):
    """Exercises the ACTUAL write functions the batch uses (storage note
    write, the real per-platform inbox writer, the real LinkTracker
    manifest save) — not re-implementations."""

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.vault = tempfile.mkdtemp(prefix='curator-vault-')

    def tearDown(self):
        dryrun.disable()
        dryrun.clear()
        shutil.rmtree(self.vault, ignore_errors=True)

    def _slice(self):
        """One note + one inbox row + the link manifest — the worker's
        per-URL write pattern (folder, note, inbox, manifest)."""
        folder = os.path.join(self.vault, 'AI-Domain', 'Agents')
        dryrun.makedirs(folder, exist_ok=True)
        note_path = os.path.join(folder, 'repo_Agents_cli.md')
        storage.atomic_write_text(note_path, _build_note())
        write_inbox_links_by_platform(
            self.vault, ['https://example.com/article'], source='Import')
        tracker = LinkTracker(self.vault)
        tracker.set_source('import')
        tracker.intake(['https://github.com/owner/repo'],
                       ['https://example.com/article'])
        return note_path

    def test_slice_writes_nothing_when_dry_run_enabled(self):
        dryrun.enable()
        try:
            note_path = self._slice()
        finally:
            dryrun.disable()
        # the vault is COMPLETELY untouched — no notes, no folders,
        # no inbox tables, no manifest, no temp leftovers
        self.assertEqual(os.listdir(self.vault), [])
        # and every intended write is in the log
        logged = [e['path'] for e in dryrun.entries()]
        self.assertTrue(any(p.endswith('repo_Agents_cli.md') for p in logged))
        self.assertTrue(any(os.path.basename(p) == 'other_links.md'
                            for p in logged), f'inbox write missing: {logged}')
        self.assertTrue(any(os.path.basename(p) == 'links_manifest.json'
                            for p in logged), f'manifest write missing: {logged}')
        # the recorded note preview contains the real note content
        note_entry = [e for e in dryrun.entries()
                      if e['path'].endswith('repo_Agents_cli.md')][0]
        self.assertIn('source', note_entry['preview'].lower())

    def test_slice_performs_real_writes_when_disabled(self):
        note_path = self._slice()
        self.assertTrue(os.path.isfile(note_path))
        self.assertTrue(os.path.isfile(os.path.join(
            self.vault, '_inbox', 'other_links.md')))
        self.assertTrue(os.path.isfile(os.path.join(
            self.vault, 'links_manifest.json')))
        self.assertEqual(dryrun.entry_count(), 0)


# ---------------------------------------------------------------------------
# tools/scan_vault_edits.py
# ---------------------------------------------------------------------------

class TestScanVaultTool(unittest.TestCase):

    def setUp(self):
        self.vault = tempfile.mkdtemp(prefix='curator-scan-')
        self.out = tempfile.mkdtemp(prefix='curator-scanout-')

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)
        shutil.rmtree(self.out, ignore_errors=True)

    def _make_vault(self):
        """A synthetic GitHub vault with one of everything the scan finds."""
        note_tpl = _build_note(url="https://github.com/o/repo1")
        _write(os.path.join(self.vault, 'AI-Domain', 'Agents',
                            'a.md'), note_tpl)
        # same template, custom human content
        edited = _build_note(url="https://github.com/o/repo2")
        edited = edited.replace('[Add your personal thoughts here]',
                                'I use this weekly for triage.')
        _write(os.path.join(self.vault, 'AI-Domain', 'Agents', 'Frameworks',
                            'b.md'), edited)
        no_source = _build_note(url="https://github.com/o/repo3")
        no_source = no_source.replace('source: "https://github.com/o/repo3"\n', '')
        _write(os.path.join(self.vault, 'Tools', 'Scraping', 'c.md'), no_source)
        _write(os.path.join(self.vault, 'Uncategorized', 'd.md'),
               _build_note(url="https://github.com/o/repo4"))
        _write(os.path.join(self.vault, 'Uncategorized', 'e.md'),
               _build_note(url="https://github.com/o/repo4"))  # duplicate
        _write(os.path.join(self.vault, '_review', 'f.md'),
               _build_note(url="https://github.com/o/repo6"))
        _write(os.path.join(self.vault, 'RandomFolder', 'g.md'),
               _build_note(url="https://github.com/o/repo7"))
        _write(os.path.join(self.vault, '_moc', 'index.md'), 'type: moc')
        _write(os.path.join(self.vault, '_inbox', 'other_links.md'),
               '| - | 2026-01-01 | https://x.com/a | x.com | Bot | unreviewed | |')
        return note_tpl

    def test_scan_reports_all_findings(self):
        self._make_vault()
        r = scan_vault_edits.scan_vault(self.vault)

        self.assertEqual(r['notes_scanned'], 7)
        self.assertEqual(r['skipped_files'], 2)          # _moc + _inbox
        self.assertEqual(r['notes_per_category'].get('Agents'), 1)
        self.assertEqual(r['notes_per_category'].get('Agents/Frameworks'), 1)
        self.assertEqual(r['notes_per_category'].get('Scraping'), 1)
        self.assertEqual(r['notes_per_category'].get('Uncategorized'), 2)
        self.assertEqual(r['review_notes'], 1)
        self.assertEqual(r['unmapped_folders'].get('RandomFolder'), 1)
        # missing source
        self.assertEqual(r['missing_source'],
                         [os.path.join('Tools', 'Scraping', 'c.md')])
        # duplicate source (normalized, 2 notes)
        self.assertEqual(len(r['duplicate_sources']), 1)
        self.assertEqual(r['duplicate_sources'][0]['url'],
                         'https://github.com/o/repo4')
        self.assertEqual(len(r['duplicate_sources'][0]['paths']), 2)
        # human edits: exactly the edited Ideas section
        self.assertEqual(len(r['human_edits']), 1)
        edit = r['human_edits'][0]
        self.assertTrue(edit['path'].endswith('b.md'))
        statuses = {s['status'] for s in edit['sections']}
        self.assertEqual(statuses, {'custom', 'template'})
        ideas = [s for s in edit['sections'] if 'Ideas' in s['name']][0]
        self.assertIn('weekly for triage', ideas['excerpt'])
        # template-only count: 6 template notes (a, c, d, e, f, g)
        self.assertEqual(r['template_only_notes'], 6)

    def test_scan_is_strictly_read_only(self):
        self._make_vault()
        before = _hash_tree(self.vault)
        scan_vault_edits.scan_vault(self.vault)
        scan_vault_edits.render_markdown(scan_vault_edits.scan_vault(self.vault))
        self.assertEqual(_hash_tree(self.vault), before)
        # and it refuses to place the report inside the vault
        rc = scan_vault_edits.main(
            [self.vault, '--out', os.path.join(self.vault, 'reports')])
        self.assertEqual(rc, 2)
        self.assertEqual(_hash_tree(self.vault), before)

    def test_scan_main_writes_report_outside_vault(self):
        self._make_vault()
        before = _hash_tree(self.vault)
        rc = scan_vault_edits.main([self.vault, '--out', self.out])
        self.assertEqual(rc, 0)
        self.assertEqual(_hash_tree(self.vault), before)   # untouched
        reports = os.listdir(self.out)
        self.assertEqual(len(reports), 1)
        with open(os.path.join(self.out, reports[0]), encoding='utf-8') as f:
            md = f.read()
        self.assertIn('Vault scan', md)
        self.assertIn('Notes missing `source:`', md)
        self.assertIn('Duplicate `source:` URLs', md)
        self.assertIn('human-written content', md)
        self.assertIn('RandomFolder', md)                  # unmapped folder
        # a missing vault is a clean error
        self.assertEqual(scan_vault_edits.main(
            [os.path.join(self.out, 'nope')]), 1)


# ---------------------------------------------------------------------------
# tools/snapshot_vault.py
# ---------------------------------------------------------------------------

class TestSnapshotTool(unittest.TestCase):

    def setUp(self):
        self.vault = tempfile.mkdtemp(prefix='curator-snap-')
        self.out = tempfile.mkdtemp(prefix='curator-snapout-')

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)
        shutil.rmtree(self.out, ignore_errors=True)

    def _make_vault(self):
        _write(os.path.join(self.vault, 'AI-Domain', 'Agents', 'a.md'),
               _build_note())
        _write(os.path.join(self.vault, 'تست_🧪', 'unicode.md'), '# یادداشت')
        png = os.path.join(self.vault, 'attachments', 'banners', 'x_banner.png')
        os.makedirs(os.path.dirname(png), exist_ok=True)
        with open(png, 'wb') as f:
            f.write(b'\x89PNG\r\n\x1a\n' + bytes(range(256)) * 4)
        os.makedirs(os.path.join(self.vault, 'empty-folder'))  # no files
        _write(os.path.join(self.vault, '.obsidian', 'app.json'), '{}')

    def test_snapshot_zip_is_complete_and_valid(self):
        self._make_vault()
        before = _hash_tree(self.vault)
        result = snapshot_vault.snapshot_vault(self.vault, self.out)

        zip_path = result['zip_path']
        self.assertTrue(_is_outside(zip_path, self.vault))
        self.assertEqual(result['file_count'], 4)   # a.md, unicode.md, png, app.json
        self.assertEqual(result['skipped'], [])
        with zipfile.ZipFile(zip_path) as zf:
            self.assertIsNone(zf.testzip())   # CRCs valid
            names = set(zf.namelist())
        self.assertIn('AI-Domain/Agents/a.md', names)
        self.assertIn('تست_🧪/unicode.md', names)          # unicode filename
        self.assertIn('attachments/banners/x_banner.png', names)
        self.assertIn('.obsidian/app.json', names)
        self.assertIn('empty-folder/', names)             # empty dir kept
        # content survives byte-for-byte
        with zipfile.ZipFile(zip_path) as zf:
            self.assertEqual(
                zf.read('attachments/banners/x_banner.png'),
                b'\x89PNG\r\n\x1a\n' + bytes(range(256)) * 4)
        # and the vault was not touched
        self.assertEqual(_hash_tree(self.vault), before)

    def test_snapshot_refuses_in_vault_output_and_bad_path(self):
        self._make_vault()
        with self.assertRaises(ValueError):
            snapshot_vault.snapshot_vault(
                self.vault, os.path.join(self.vault, 'backups'))
        with self.assertRaises(ValueError):
            snapshot_vault.snapshot_vault(os.path.join(self.out, 'missing'))
        rc = snapshot_vault.main(
            [self.vault, '--out', os.path.join(self.vault, 'backups')])
        self.assertEqual(rc, 2)
        self.assertEqual(sorted(os.listdir(self.vault)), sorted(
            ['.obsidian', 'AI-Domain', 'attachments', 'empty-folder', 'تست_🧪']))


# ---------------------------------------------------------------------------
# tools/pick_golden_links.py
# ---------------------------------------------------------------------------

class TestGoldenLinksTool(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='curator-golden-')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _csv(self, text, name='links.csv'):
        path = os.path.join(self.tmp, name)
        with open(path, 'w', encoding='utf-8-sig', newline='') as f:
            f.write(text)
        return path

    def test_picks_diverse_set_excluding_github(self):
        # 40 distinct domains, 3 links each — plus github links and dupes
        rows = ['url,category']
        for i in range(40):
            for j in range(3):
                rows.append(f'https://domain{i}.example/page{j},cat{i % 5}')
        rows.append('https://github.com/owner/repo,code')
        rows.append('https://github.com/owner/repo,code')          # dupe
        rows.append('https://domain0.example/page0,cat0')          # dupe
        path = self._csv('\n'.join(rows) + '\n')

        result = pick_golden_links.pick_candidates(path, target=30)
        c = result['counts']
        self.assertEqual(c['selected'], 30)
        self.assertEqual(c['github_links_excluded'], 1)   # dupe collapsed first
        self.assertEqual(c['duplicate_urls_removed'], 2)
        self.assertEqual(c['domains_represented'], 30)    # maximally diverse
        picked = result['candidates']
        self.assertEqual(len({p['domain'] for p in picked}), 30)
        self.assertFalse(any('github.com' in p['url'] for p in picked))
        # every candidate carries the optional category column
        self.assertTrue(all(p['category'].startswith('cat') for p in picked))
        # deterministic: same input -> same selection
        again = pick_golden_links.pick_candidates(path, target=30)
        self.assertEqual([p['url'] for p in again['candidates']],
                         [p['url'] for p in picked])

    def test_column_and_dialect_variants(self):
        # semicolon delimiter, 'Link' header, quoted fields, BOM (utf-8-sig)
        path = self._csv(
            'Title;Link;Category\n'
            '"Example, Inc";https://example.com/1;tools\n'
            'Other;https://other.example/2;docs\n', name='semi.csv')
        result = pick_golden_links.pick_candidates(path, target=30)
        urls = [c['url'] for c in result['candidates']]
        self.assertEqual(sorted(urls),
                         ['https://example.com/1', 'https://other.example/2'])
        titles = {c['url']: c['title'] for c in result['candidates']}
        self.assertEqual(titles['https://example.com/1'], 'Example, Inc')
        cats = {c['url']: c['category'] for c in result['candidates']}
        self.assertEqual(cats['https://other.example/2'], 'docs')

    def test_fewer_than_target_selects_all(self):
        rows = ['url'] + [f'https://site{i}.example/' for i in range(8)]
        path = self._csv('\n'.join(rows) + '\n')
        result = pick_golden_links.pick_candidates(path, target=30)
        self.assertEqual(result['counts']['selected'], 8)
        self.assertEqual(result['counts']['non_github_links'], 8)

    def test_headerless_single_column_csv(self):
        path = self._csv(
            'https://a.example/1\nhttps://b.example/2\n'
            'https://github.com/o/r\nhttps://a.example/1\n', name='plain.csv')
        result = pick_golden_links.pick_candidates(path, target=30)
        self.assertEqual(result['counts']['selected'], 2)
        self.assertEqual(result['counts']['github_links_excluded'], 1)
        self.assertEqual(result['counts']['duplicate_urls_removed'], 1)

    def test_main_writes_candidate_json(self):
        rows = ['url,category'] + \
               [f'https://d{i}.example/x,cat{i}' for i in range(5)]
        path = self._csv('\n'.join(rows) + '\n')
        out = os.path.join(self.tmp, 'golden', 'websites_candidates.json')
        rc = pick_golden_links.main([path, '--out', out])
        self.assertEqual(rc, 0)
        with open(out, encoding='utf-8') as f:
            payload = json.load(f)
        self.assertIn('candidates', payload)
        self.assertIn('counts', payload)
        self.assertEqual(payload['counts']['selected'], 5)
        self.assertEqual(len(payload['candidates']), 5)
        self.assertEqual(rc, pick_golden_links.main([path, '--out', out]))


# ---------------------------------------------------------------------------
# CLI --dry-run plumbing
# ---------------------------------------------------------------------------

def _is_inside(path, parent):
    try:
        return os.path.commonpath([os.path.realpath(path),
                                   os.path.realpath(parent)]) \
            == os.path.realpath(parent)
    except (ValueError, OSError):
        return False


def _is_outside(path, parent):
    return not _is_inside(path, parent)


class TestCliDryRunFlag(unittest.TestCase):

    def setUp(self):
        dryrun.disable()
        dryrun.clear()

    def tearDown(self):
        dryrun.disable()
        dryrun.clear()

    def test_parser_accepts_dry_run_flag(self):
        args = build_parser().parse_args(['--auto', '--dry-run'])
        self.assertTrue(args.dry_run)
        args = build_parser().parse_args(
            ['--import-file', 'urls.txt', '--dry-run'])
        self.assertTrue(args.dry_run)
        args = build_parser().parse_args(['--auto'])
        self.assertFalse(args.dry_run)   # default unchanged

    def test_run_batch_visual_signature_has_dry_run(self):
        sig = inspect.signature(run_batch_visual)
        self.assertIn('dry_run', sig.parameters)
        self.assertFalse(sig.parameters['dry_run'].default)

    def test_processing_worker_accepts_dry_run(self):
        # needs a QCoreApplication for QObject construction (offscreen)
        from PyQt6.QtCore import QCoreApplication
        app = QCoreApplication.instance() or QCoreApplication([])
        worker = ProcessingWorker(config={}, mode='direct', headless=True,
                                  dry_run=True)
        self.assertTrue(worker._dry_run)
        worker2 = ProcessingWorker(config={}, mode='direct', headless=True)
        self.assertFalse(worker2._dry_run)   # GUI default unchanged

    def test_worker_run_flips_switch_and_always_restores_it(self):
        from PyQt6.QtCore import QCoreApplication
        QCoreApplication.instance() or QCoreApplication([])
        messages = []
        worker = ProcessingWorker(config={'vault_path': ''}, mode='direct',
                                  urls=[], headless=True, dry_run=True)
        worker.log_message.connect(lambda m, l: messages.append(m))
        worker.run()   # synchronous: "No URLs found" path
        self.assertFalse(dryrun.is_enabled())   # restored even on early exit
        self.assertTrue(any('DRY-RUN' in m for m in messages),
                        f'dry-run banner missing from: {messages}')

    def test_dryrun_report_helper_writes_outside_vault(self):
        dryrun.enable()
        dryrun.record('write', '/vault/note.md', content='x')
        dryrun.disable()
        report_tmp = tempfile.mkdtemp(prefix='curator-clirep-')
        try:
            with mock.patch('gitcurator.constants.APP_DIR', report_tmp):
                path = _write_dryrun_report()
            self.assertIsNotNone(path)
            self.assertTrue(_is_outside(path, os.path.join(report_tmp, 'vault')))
            with open(path, encoding='utf-8') as f:
                self.assertIn('note.md', f.read())
        finally:
            shutil.rmtree(report_tmp, ignore_errors=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
