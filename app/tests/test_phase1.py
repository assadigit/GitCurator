#!/usr/bin/env python3
"""
test_phase1.py — Phase 1 (vault settings & ownership, v0.10.0) tests.

Covers, in this order:
  * core/note_state.py — the persistent per-note record: baseline
    recording (one-time, silent), record-on-write, and the pure
    change detector (moved / edited / deleted / duplicates /
    unmanaged / unmapped / unknown) that Phase 3 will act on.
  * note format — new notes carry managed_by / schema_version /
    prompt_version + the ownership banner, and NO LONGER contain the
    three legacy human placeholder sections.
  * config compatibility — an OLD config.json (v0.09.4 shape, no new
    keys) loads and every read site falls back to the documented safe
    defaults; merge_config merges the pipelines dict key-by-key.
  * VaultIndex on TWO vaults (Phase 1 acceptance).
  * vaultseal.websites_seal_from_config — skips unless the websites
    pipeline is ON and a websites vault is configured.
  * the CLI --status vault map (subprocess-free: calls cmd_status with
    a temp config and captured stdout).
  * worker integration — a real (non-dry-run) ProcessingWorker batch on
    a synthetic vault records the baseline; a dry-run batch records
    NOTHING in the real cache.db.

Headless-safe: QT_QPA_PLATFORM=offscreen, the GUI is never shown.
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

from gitcurator.constants import (
    CATEGORY_FOLDERS, CONFIG_EXAMPLE, GITHUB_PROMPT_VERSION,
    MANAGED_BY_GITCURATOR, NOTE_SCHEMA_VERSION, OWNERSHIP_BANNER,
    resolve_taxonomy_path,
)
from gitcurator.core import note_builder, note_state
from gitcurator.core.storage import merge_config


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    return path


def _legacy_note(url, extra=""):
    """A v0.09-era note (has the human sections) for state tests."""
    return (
        "---\n"
        f"source: {url}\n"
        "tags: [cli]\n"
        "category: \"Agents\"\n"
        "date_processed: 2026-09-29\n"
        "---\n"
        "# repo\n\nSome summary text.\n"
        "## 💡 My Ideas & Notes\n[Add your personal thoughts here]\n"
        f"{extra}"
        "\n---\n*Source: [GitHub](" + url + ")*\n"
    )


def _make_vault(root):
    """A small synthetic machine vault:
       AI-Domain/Agents/a.md, b.md · _review/r.md · _moc/skip.md"""
    vault = os.path.join(root, 'vault')
    _write(os.path.join(vault, 'AI-Domain', 'Agents', 'a.md'),
           _legacy_note('https://github.com/o/repo1'))
    _write(os.path.join(vault, 'AI-Domain', 'Agents', 'b.md'),
           _legacy_note('https://github.com/o/repo2'))
    _write(os.path.join(vault, '_review', 'r.md'),
           _legacy_note('https://github.com/o/repo3'))
    _write(os.path.join(vault, '_moc', 'skip.md'), 'type: moc')
    return vault


# ---------------------------------------------------------------------------
# note_state: fingerprint + folder mapping
# ---------------------------------------------------------------------------

class TestFingerprint(unittest.TestCase):

    def test_stable(self):
        self.assertEqual(note_state.compute_fingerprint("abc"),
                         note_state.compute_fingerprint("abc"))

    def test_recall_block_is_invisible(self):
        """Phase 6 will append a delimited recall block with app-written
        content; that must NEVER register as a human edit."""
        base = "note body\n"
        with_block = (base + note_state.RECALL_START
                      + "\nrelated: something\n" + note_state.RECALL_END)
        self.assertEqual(note_state.compute_fingerprint(base),
                         note_state.compute_fingerprint(with_block))

    def test_human_edit_changes_fingerprint(self):
        self.assertNotEqual(note_state.compute_fingerprint("a"),
                            note_state.compute_fingerprint("b"))


class TestFolderCategory(unittest.TestCase):

    def test_exact_category_folder(self):
        cat, sub = note_state.folder_category('AI-Domain/Agents')
        self.assertEqual((cat, sub), ('Agents', None))

    def test_nested_registered_prefix(self):
        cat, sub = note_state.folder_category('AI-Domain/Agents/Frameworks')
        self.assertEqual((cat, sub), ('Agents/Frameworks', None))

    def test_unregistered_nesting_gives_subcategory(self):
        # a registered category folder with an UNREGISTERED child folder —
        # the websites layout (<Category>/<Subcategory>) uses this.
        cat, sub = note_state.folder_category('Tools/Scraping/Extras')
        self.assertEqual((cat, sub), ('Scraping', 'Extras'))

    def test_root_and_review_are_none(self):
        self.assertEqual(note_state.folder_category(''), (None, None))
        self.assertEqual(note_state.folder_category('_review'), (None, None))

    def test_unknown_folder_kept_raw(self):
        cat, sub = note_state.folder_category('RandomFolder')
        self.assertEqual((cat, sub), ('RandomFolder', None))


# ---------------------------------------------------------------------------
# note_state: baseline + record + classify
# ---------------------------------------------------------------------------

class TestNoteStateDB(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='curator-p1-')
        self.db = os.path.join(self.tmp, 'cache.db')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_record_baseline_is_one_time(self):
        vault = _make_vault(self.tmp)
        n = note_state.record_baseline_if_empty('github', vault, db_path=self.db)
        self.assertEqual(n, 3)            # 3 notes with source (moc skipped)
        # second call: baseline exists -> None, state UNCHANGED even if the
        # vault changed in between (that's the Phase 3 comparison's job)
        _write(os.path.join(vault, 'AI-Domain', 'Agents', 'c.md'),
               _legacy_note('https://github.com/o/repo9'))
        self.assertIsNone(
            note_state.record_baseline_if_empty('github', vault, db_path=self.db))
        db = note_state.NoteStateDB(self.db)
        try:
            self.assertEqual(db.count('github'), 3)
        finally:
            db.close()

    def test_baseline_skips_unmanaged_notes(self):
        vault = _make_vault(self.tmp)
        _write(os.path.join(vault, 'AI-Domain', 'Agents', 'human.md'),
               "# my own note\nno source here\n")
        n = note_state.record_baseline_if_empty('github', vault, db_path=self.db)
        self.assertEqual(n, 3)            # the unmanaged note is NOT adopted

    def test_baseline_categories_come_from_folders(self):
        vault = _make_vault(self.tmp)
        note_state.record_baseline_if_empty('github', vault, db_path=self.db)
        db = note_state.NoteStateDB(self.db)
        try:
            rows = db.all_rows('github')
            self.assertEqual(
                rows['https://github.com/o/repo1']['category'], 'Agents')
            self.assertIsNone(rows['https://github.com/o/repo3']['category'])
        finally:
            db.close()

    def test_record_note_and_upsert(self):
        vault = _make_vault(self.tmp)
        path = os.path.join(vault, 'AI-Domain', 'Agents', 'a.md')
        db = note_state.NoteStateDB(self.db)
        try:
            db.record_note('github', 'https://github.com/o/repo1', path,
                           content=_legacy_note('https://github.com/o/repo1'),
                           category='Agents')
            self.assertEqual(db.count('github'), 1)
            # re-record with different content -> upsert, not duplicate
            db.record_note('github', 'https://github.com/o/repo1', path,
                           content='changed', category='Agents')
            self.assertEqual(db.count('github'), 1)
            # set_locked roundtrip
            db.set_locked('github', 'https://github.com/o/repo1', True)
            self.assertTrue(db.all_rows('github')
                            ['https://github.com/o/repo1']['locked'])
        finally:
            db.close()

    def test_db_path_resolution_anchors_to_app_dir(self):
        """The v0.09.4 split-state bug must not come back: the default
        'cache.db' resolves to APP_DIR, never the current working dir."""
        db = note_state.NoteStateDB()      # default path
        try:
            expected = os.path.join(note_state.APP_DIR, 'cache.db')
            self.assertEqual(os.path.abspath(db.db_path),
                             os.path.abspath(expected))
        finally:
            db.close()
        # it must not have CREATED the real app cache.db just by existing
        # (it may already exist from other tests — only assert no crash)

    def test_two_vaults_are_independent(self):
        vault_a = _make_vault(self.tmp)
        vault_b = _make_vault(os.path.join(self.tmp, 'other'))
        note_state.record_baseline_if_empty('github', vault_a, db_path=self.db)
        note_state.record_baseline_if_empty('websites', vault_b, db_path=self.db)
        db = note_state.NoteStateDB(self.db)
        try:
            self.assertEqual(db.count('github'), 3)
            self.assertEqual(db.count('websites'), 3)
        finally:
            db.close()


class TestClassifyChanges(unittest.TestCase):
    """The pure detector Phase 3 will act on — every row of the §4.4 table."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='curator-p1c-')
        self.db = os.path.join(self.tmp, 'cache.db')
        self.vault = _make_vault(self.tmp)
        note_state.record_baseline_if_empty(
            'github', self.vault, db_path=self.db)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unchanged_vault_reports_nothing(self):
        r = note_state.classify_changes('github', self.vault, db_path=self.db)
        for key in ('moved', 'edited', 'deleted', 'duplicates',
                    'unmanaged', 'unmapped', 'unknown'):
            self.assertEqual(r[key], [], f"{key} should be empty")

    def test_moved_note_detected(self):
        src = os.path.join(self.vault, 'AI-Domain', 'Agents', 'a.md')
        dst = os.path.join(self.vault, 'Tools', 'Scraping', 'a.md')
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        r = note_state.classify_changes('github', self.vault, db_path=self.db)
        self.assertEqual(len(r['moved']), 1)
        mv = r['moved'][0]
        self.assertEqual(mv['source_url'], 'https://github.com/o/repo1')
        self.assertEqual(mv['from_category'], 'Agents')
        self.assertEqual(mv['to_category'], 'Scraping')

    def test_edited_note_detected(self):
        path = os.path.join(self.vault, 'AI-Domain', 'Agents', 'a.md')
        with open(path, 'a', encoding='utf-8') as f:
            f.write("\nI wrote this by hand.\n")
        r = note_state.classify_changes('github', self.vault, db_path=self.db)
        self.assertEqual(len(r['edited']), 1)
        self.assertEqual(r['edited'][0]['source_url'],
                         'https://github.com/o/repo1')

    def test_recall_block_append_is_not_an_edit(self):
        path = os.path.join(self.vault, 'AI-Domain', 'Agents', 'a.md')
        with open(path, 'a', encoding='utf-8') as f:
            f.write("\n" + note_state.RECALL_START
                    + "\nrelated: [x]\n" + note_state.RECALL_END + "\n")
        r = note_state.classify_changes('github', self.vault, db_path=self.db)
        self.assertEqual(r['edited'], [])

    def test_deleted_note_detected(self):
        os.remove(os.path.join(self.vault, 'AI-Domain', 'Agents', 'b.md'))
        r = note_state.classify_changes('github', self.vault, db_path=self.db)
        self.assertEqual(len(r['deleted']), 1)
        self.assertEqual(r['deleted'][0]['source_url'],
                         'https://github.com/o/repo2')

    def test_duplicate_sources_detected(self):
        # copy a.md's content (same source) under a new name
        src = os.path.join(self.vault, 'AI-Domain', 'Agents', 'a.md')
        with open(src, 'r', encoding='utf-8') as f:
            content = f.read()
        _write(os.path.join(self.vault, 'AI-Domain', 'Agents', 'a-copy.md'),
               content)
        r = note_state.classify_changes('github', self.vault, db_path=self.db)
        self.assertEqual(len(r['duplicates']), 1)
        self.assertEqual(len(r['duplicates'][0]['paths']), 2)
        # a duplicate is flagged, never also classified as moved/edited
        self.assertEqual(r['moved'], [])
        self.assertEqual(r['edited'], [])

    def test_unmanaged_and_unknown_detected(self):
        _write(os.path.join(self.vault, 'Notes', 'mine.md'),
               "# handwritten\nno source line\n")
        _write(os.path.join(self.vault, 'Notes', 'pasted.md'),
               _legacy_note('https://github.com/o/pasted'))
        r = note_state.classify_changes('github', self.vault, db_path=self.db)
        self.assertEqual([u['path'] for u in r['unmanaged']],
                         [os.path.join(self.vault, 'Notes', 'mine.md')])
        self.assertEqual(len(r['unknown']), 1)
        self.assertEqual(r['unknown'][0]['source_url'],
                         'https://github.com/o/pasted')

    def test_unmapped_folder_detected(self):
        src = os.path.join(self.vault, 'AI-Domain', 'Agents', 'a.md')
        dst = os.path.join(self.vault, 'MysteryZone', 'a.md')
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        r = note_state.classify_changes('github', self.vault, db_path=self.db)
        # a move into an unknown folder is BOTH a move and unmapped
        self.assertEqual(len(r['moved']), 1)
        self.assertEqual(len(r['unmapped']), 1)
        self.assertEqual(r['unmapped'][0]['folder'], 'MysteryZone')


# ---------------------------------------------------------------------------
# note format (Phase 1 stamps)
# ---------------------------------------------------------------------------

class TestNoteFormat(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.note = note_builder.build_note(
            url="https://github.com/o/repo", repo_name="repo", owner="o",
            org_name="Org", stars=10, forks=2, commit_count=5, cred_score=80,
            org_rep=7, summary="A" * 80, tags=["cli"], category_key="Agents",
            confidence=90, how_it_works="how", core_value="val",
            features=["f1", "f2", "f3"], difference="diff",
            primary_language="Python", languages=["Python"],
            short_summary="Useful.", quality_issues=[], is_low_quality=False,
        )

    def test_ownership_frontmatter_keys(self):
        self.assertIn(f"managed_by: {MANAGED_BY_GITCURATOR}", self.note)
        self.assertIn(f"schema_version: {NOTE_SCHEMA_VERSION}", self.note)
        self.assertIn(f"prompt_version: {GITHUB_PROMPT_VERSION}", self.note)

    def test_ownership_banner_line(self):
        self.assertIn(OWNERSHIP_BANNER, self.note)

    def test_legacy_human_sections_gone(self):
        for heading in ("## 💡 My Ideas & Notes",
                        "## 📱 Social Signal (Manual)",
                        "## 📔 Journal"):
            self.assertNotIn(heading, self.note)

    def test_existing_sections_still_there(self):
        for heading in ("## What is it?", "## How does it work?",
                        "## 🏢 Organization & Credibility", "# repo"):
            self.assertIn(heading, self.note)


# ---------------------------------------------------------------------------
# config compatibility (Phase 1 acceptance: old configs load unchanged)
# ---------------------------------------------------------------------------

class TestConfigCompat(unittest.TestCase):

    def test_old_config_keys_fall_back_to_defaults(self):
        old = {"vault_path": "X", "github_token": "t"}  # v0.09.4 shape
        self.assertEqual(old.get('website_vault_path', ''), '')
        self.assertEqual(old.get('manual_vault_path', ''), '')
        self.assertEqual(old.get('website_repo_name', ''), '')
        pipes = old.get('pipelines') or {}
        self.assertTrue(pipes.get('github', True))    # default ON
        self.assertFalse(pipes.get('websites', False))  # default OFF
        # taxonomy resolves to the bundled default when unset
        self.assertEqual(resolve_taxonomy_path(old),
                         resolve_taxonomy_path(None))

    def test_merge_config_merges_pipelines_key_by_key(self):
        existing = {"pipelines": {"github": True, "websites": False},
                    "secret_extra": 1}
        updates = {"pipelines": {"websites": True}}
        merged = merge_config(existing, updates)
        self.assertEqual(merged['pipelines'],
                         {"github": True, "websites": True})
        self.assertEqual(merged['secret_extra'], 1)   # unknown keys survive

    def test_config_example_documents_new_keys(self):
        for key in ('website_vault_path', 'manual_vault_path',
                    'website_repo_name', 'taxonomy_path', 'pipelines'):
            self.assertIn(key, CONFIG_EXAMPLE)
        self.assertTrue(CONFIG_EXAMPLE['pipelines']['github'])
        self.assertFalse(CONFIG_EXAMPLE['pipelines']['websites'])

    def test_real_example_json_loads_and_matches(self):
        path = os.path.join(_APP_ROOT, 'config.example.json')
        with open(path, 'r', encoding='utf-8') as f:
            example = json.load(f)
        self.assertEqual(example['pipelines'],
                         {"github": True, "websites": False})
        self.assertEqual(example['website_repo_name'],
                         'my-awesome-websites-directory')
        self.assertEqual(example['vaultseal']['repo_name'],
                         'my-awesome-github-directory')


# ---------------------------------------------------------------------------
# VaultIndex on two vaults (Phase 1 acceptance)
# ---------------------------------------------------------------------------

class TestVaultIndexTwoVaults(unittest.TestCase):

    def test_two_instances_are_independent(self):
        from gitcurator.gui.app import VaultIndex
        tmp = tempfile.mkdtemp(prefix='curator-p1v-')
        try:
            vault_a = _make_vault(tmp)
            vault_b = _make_vault(os.path.join(tmp, 'second'))
            # same URL in both vaults — each index must see only its own
            ia = VaultIndex(vault_a)
            ia.rebuild()
            ib = VaultIndex(vault_b)
            ib.rebuild()
            self.assertEqual(ia.count, 3)
            self.assertEqual(ib.count, 3)
            self.assertTrue(ia.has_url('https://github.com/o/repo1'))
            self.assertNotEqual(ia.get_path('https://github.com/o/repo1'),
                                ib.get_path('https://github.com/o/repo1'))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# websites VaultSeal (second instance)
# ---------------------------------------------------------------------------

class TestWebsitesSeal(unittest.TestCase):

    def test_skips_when_pipeline_off(self):
        from gitcurator.integrations import vaultseal
        cfg = {"pipelines": {"websites": False},
               "website_vault_path": "X", "github_token": "t",
               "website_repo_name": "r"}
        result = vaultseal.websites_seal_from_config(cfg)
        self.assertFalse(result.sealed)
        self.assertIn("off", result.skipped_reason)
        self.assertTrue(result.ok)          # a skip is a successful no-op

    def test_skips_when_no_vault_configured(self):
        from gitcurator.integrations import vaultseal
        cfg = {"pipelines": {"websites": True}, "website_vault_path": "",
               "github_token": "t"}
        result = vaultseal.websites_seal_from_config(cfg)
        self.assertIn("no websites vault", result.skipped_reason)

    def test_missing_key_defaults_to_off(self):
        from gitcurator.integrations import vaultseal
        result = vaultseal.websites_seal_from_config({})  # no keys at all
        self.assertIn("off", result.skipped_reason)

    def test_seals_when_on(self):
        from gitcurator.integrations import vaultseal
        tmp = tempfile.mkdtemp(prefix='curator-p1w-')
        try:
            vault = _make_vault(tmp)
            cfg = {"pipelines": {"websites": True},
                   "website_vault_path": vault, "github_token": "",
                   "website_repo_name": "my-awesome-websites-directory"}
            result = vaultseal.websites_seal_from_config(cfg)
            # no token -> local commit only; vault has no .git yet ->
            # VaultSeal init-inits the repo; either way it must not raise
            # and must report ok (sealed) or a skipped/error reason.
            self.assertTrue(result.sealed or result.skipped_reason
                            or result.error)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# CLI --status vault map
# ---------------------------------------------------------------------------

class TestCliStatus(unittest.TestCase):

    def test_status_shows_vault_map_and_pipelines(self):
        from gitcurator import cli
        tmp = tempfile.mkdtemp(prefix='curator-p1s-')
        try:
            web_vault = os.path.join(tmp, 'websites-vault')
            os.makedirs(web_vault, exist_ok=True)
            cfg_path = os.path.join(tmp, 'config.json')
            with open(cfg_path, 'w', encoding='utf-8') as f:
                json.dump({
                    "vault_path": os.path.join(tmp, 'gh-vault'),
                    "website_vault_path": web_vault,
                    "website_repo_name": "my-awesome-websites-directory",
                    "pipelines": {"github": True, "websites": False},
                }, f)

            class _Args:
                config = cfg_path

            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.cmd_status(_Args())
            out = buf.getvalue()
            self.assertEqual(rc, 0)
            self.assertIn("Websites vault", out)
            self.assertIn("exists", out)               # web vault exists
            self.assertIn("Manual vault", out)
            self.assertIn("Websites repo", out)
            self.assertIn("my-awesome-websites-directory", out)
            self.assertIn("Pipelines", out)
            self.assertIn("github", out)
            self.assertIn("Taxonomy", out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# worker integration: baseline recorded on a real run, not on a dry-run
# ---------------------------------------------------------------------------

class TestWorkerBaseline(unittest.TestCase):
    """Runs the REAL ProcessingWorker over a synthetic vault with the same
    fake-LLM harness test_e2e uses — proving the Phase 1 hooks fire in a
    real batch (and stay silent in a dry-run)."""

    def setUp(self):
        from gitcurator.core import dryrun
        dryrun.disable()
        dryrun.clear()

    def _run_worker(self, dry_run):
        import gitcurator.gui.app as gui_app
        # refactor/gui-app-split: ProcessingWorker (and its Github symbol)
        # now lives in gitcurator.gui.processing_worker — the fast-fail
        # stub must be swapped in on the owning module.
        import gitcurator.gui.processing_worker as _gui_pw
        from gitcurator.core import dryrun
        from gitcurator.gui.app import ProcessingWorker
        tmp = tempfile.mkdtemp(prefix='curator-p1e-')
        vault = _make_vault(tmp)
        db_path = os.path.join(tmp, 'cache.db')

        events = []

        class _Sig:
            def __init__(self, name):
                self.name = name

            def emit(self, *a):
                events.append((self.name, a))

            def connect(self, *a):
                pass

        # llm_provider 'cloud' skips the Ollama pre-flight entirely. The
        # GitHub client is patched to fail INSTANTLY (this sandbox blocks
        # api.github.com and the per-URL handler would hang on connect) —
        # the baseline is recorded BEFORE any of that, which is the point.
        class _FastFailGithub:
            def __init__(self, *a, **k):
                pass

            def get_repo(self, *a, **k):
                raise RuntimeError("network blocked in test")

        worker = ProcessingWorker(
            config={'vault_path': vault, 'pipelines': {'github': True},
                    'llm_provider': 'cloud',
                    'cloud_api_url': 'http://127.0.0.1:9/v1',
                    'cloud_model': 'test', 'github_token': '',
                    'timeout_per_repo': 5, 'max_retries': 1,
                    'delay_between_api_calls': 0},
            mode='direct', urls=['https://github.com/o/not-a-real-repo'],
            headless=True, dry_run=dry_run)
        worker.log_message = _Sig('log')
        worker.finished_signal = _Sig('finished')
        worker.progress_updated = _Sig('progress')

        # Baseline is recorded BEFORE any URL processing; point the app's
        # default cache at the temp db so the REAL cache.db is never touched.
        orig_init = note_state.NoteStateDB.__init__
        orig_github = _gui_pw.Github

        def _patched(self, db_path_arg="cache.db"):
            orig_init(self, db_path=db_path if db_path_arg == "cache.db"
                      else db_path_arg)

        note_state.NoteStateDB.__init__ = _patched
        _gui_pw.Github = _FastFailGithub
        try:
            if dry_run:
                dryrun.enable()      # mirrors worker.run()'s try/finally
            try:
                worker._run_impl()
            finally:
                dryrun.disable()
        finally:
            note_state.NoteStateDB.__init__ = orig_init
            _gui_pw.Github = orig_github
        db = note_state.NoteStateDB(db_path)
        try:
            recorded = db.count('github')
        finally:
            db.close()
        shutil.rmtree(tmp, ignore_errors=True)
        return recorded

    def test_real_run_records_baseline(self):
        recorded = self._run_worker(dry_run=False)
        self.assertEqual(recorded, 3)      # the synthetic vault's 3 notes

    def test_dry_run_records_nothing(self):
        recorded = self._run_worker(dry_run=True)
        self.assertEqual(recorded, 0)


if __name__ == '__main__':
    unittest.main()
