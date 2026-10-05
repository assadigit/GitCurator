#!/usr/bin/env python3
"""
test_resyncfix.py — v0.36.0: the mirror resync (GitHub follows the vault).

The owner's report: "we use github to backup our links … I deleted
everything on the obsidian website vault, but github still shows those
links that were wrongly presented. how to fix this issue? I can think of
a button in settings, that resyncs vault with github, so github repo
will follow and sync with obsidian vault by clicking it, it must show a
warning modal as well."

Covered here (hermetic — REAL local bare git repos, no network, no GUI):
  1. VaultSeal.resync — deletions reach the mirror (the exact report:
     wrongly-created notes deleted in Obsidian leave the GitHub repo)
  2. the fresh-vault shape (vault deleted and recreated vs a populated
     mirror): a reconciliation commit whose tree is the vault EXACTLY —
     no union-merge resurrection of the deleted files
  3. history is NEVER rewritten (the mirror's old tip stays an
     ancestor; main is never force-pushed)
  4. idempotence, the empty mirror, additions+deletions together, the
     delta counts, describe()/to_dict()
  5. guards: no token / no vault → a clear error, never a crash
  6. resync_from_config — both vaults, each into its own mirror, with
     the explicit-action switches (enabled/auto_push forced on)
  7. wiring: the Settings → Backup button, the WARNING MODAL before
     anything moves, the worker, and the CLI --resync twin
"""

import inspect
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gitcurator.integrations import vaultseal as vs_mod
from gitcurator.integrations.vaultseal import SealResult, VaultSeal


# ---------------------------------------------------------------------------
# Hermetic git helpers (the sealfix pattern)
# ---------------------------------------------------------------------------

def _run_git(path, *args):
    r = subprocess.run(["git", "-C", path, *args], capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout.strip()


def _bare(path):
    subprocess.run(["git", "init", "--bare", "-b", "main", path],
                   capture_output=True, text=True, timeout=60)
    return path


def _remote_tree(bare):
    return [ln for ln in
            _run_git(bare, "ls-tree", "-r", "--name-only", "main").splitlines()
            if ln.strip()]


def _is_ancestor(repo, maybe_ancestor, descendant):
    r = subprocess.run(["git", "-C", repo, "merge-base", "--is-ancestor",
                        maybe_ancestor, descendant],
                       capture_output=True, text=True, timeout=60)
    return r.returncode == 0


def _write(path, name, content):
    with open(os.path.join(path, name), "w", encoding="utf-8") as f:
        f.write(content)


def _init_vault(path):
    os.makedirs(path, exist_ok=True)
    _run_git(path, "init", "-b", "main")
    _run_git(path, "config", "user.email", "t@example.com")
    _run_git(path, "config", "user.name", "T")
    return path


def _sealer(vault):
    return VaultSeal(vault_path=vault, token="tok", repo_name="mirror",
                     auto_push=True)


# ---------------------------------------------------------------------------
# 1-5. VaultSeal.resync against real local bare mirrors
# ---------------------------------------------------------------------------

class TestResync(unittest.TestCase):
    """Every case drives the REAL resync() against a local bare repo."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="resyncfix-")
        self.bare = _bare(os.path.join(self.tmp, "mirror.git"))
        self.vault = _init_vault(os.path.join(self.tmp, "vault"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_mirror_with_vault(self, *files):
        """Push the vault's files to the mirror (the pre-bug state)."""
        for name in files:
            _write(self.vault, name, f"# {name}\n" + "x" * 100)
        _run_git(self.vault, "add", "-A")
        _run_git(self.vault, "commit", "-m", "seal: the old state", "--quiet")
        _run_git(self.vault, "push", self.bare, "HEAD:refs/heads/main")
        return _run_git(self.bare, "rev-parse", "main")

    def test_deletions_reach_the_mirror(self):
        """The owner's exact report: wrongly-created notes deleted in
        Obsidian must leave the GitHub mirror too."""
        old_tip = self._seed_mirror_with_vault(
            "good-note.md", "wrong-youtube-note.md", "wrong-share-google.md")
        os.remove(os.path.join(self.vault, "wrong-youtube-note.md"))
        os.remove(os.path.join(self.vault, "wrong-share-google.md"))

        res = _sealer(self.vault).resync(push_url=self.bare)

        self.assertTrue(res.ok, res.describe())
        self.assertTrue(res.pushed)
        self.assertEqual(_remote_tree(self.bare), ["good-note.md"])
        self.assertEqual(res.files_deleted, 2)
        self.assertEqual(res.files_added, 0)
        self.assertTrue(res.commit_message.startswith("resync:"))
        # history kept: the old tip is still an ancestor of the new main
        self.assertTrue(_is_ancestor(self.bare, old_tip, "main"))
        # and the local vault is left clean (the commit landed on HEAD)
        self.assertEqual(
            _run_git(self.vault, "status", "--porcelain"), "")

    def test_resync_is_idempotent(self):
        self._seed_mirror_with_vault("a.md", "b.md")
        os.remove(os.path.join(self.vault, "b.md"))
        first = _sealer(self.vault).resync(push_url=self.bare)
        self.assertTrue(first.ok, first.describe())
        second = _sealer(self.vault).resync(push_url=self.bare)
        self.assertTrue(second.ok, second.describe())
        self.assertEqual(second.skipped_reason,
                         "mirror already matches the vault")
        self.assertFalse(second.sealed)

    def test_fresh_vault_vs_populated_mirror_never_resurrects(self):
        """The deleted-everything shape: a brand-new local repo (no .git
        history — the vault folder was deleted and Obsidian recreated it)
        against a populated mirror. The old seal's union-merge would bring
        every deleted file BACK; the resync's reconciliation commit makes
        the mirror tree the vault EXACTLY."""
        seed = _init_vault(os.path.join(self.tmp, "seed"))
        _write(seed, "keep.md", "# keep\n" + "x" * 100)
        _write(seed, "wrong-note.md", "# wrong\n" + "x" * 100)
        _run_git(seed, "add", "-A")
        _run_git(seed, "commit", "-m", "seal: both files", "--quiet")
        _run_git(seed, "push", self.bare, "HEAD:refs/heads/main")
        seed_tip = _run_git(self.bare, "rev-parse", "main")

        # the owner's fresh vault: only what should exist
        fresh = _init_vault(os.path.join(self.tmp, "fresh-vault"))
        _write(fresh, "keep.md", "# keep\n" + "x" * 100)

        res = _sealer(fresh).resync(push_url=self.bare)

        self.assertTrue(res.ok, res.describe())
        self.assertTrue(res.pushed)
        self.assertEqual(_remote_tree(self.bare), ["keep.md"])
        self.assertEqual(res.files_deleted, 1)
        # both histories kept: the mirror's old tip is still an ancestor
        self.assertTrue(_is_ancestor(self.bare, seed_tip, "main"))

    def test_mirror_ahead_the_vault_wins(self):
        """A web-UI edit on the mirror (or a second machine) does not
        survive a resync: the vault is the source of truth."""
        old_tip = self._seed_mirror_with_vault("note.md")
        other = _init_vault(os.path.join(self.tmp, "other"))
        _run_git(other, "fetch", self.bare)
        _run_git(other, "checkout", "-b", "work", "FETCH_HEAD")
        _write(other, "web-edit.md", "# edited on github\n" + "x" * 100)
        _run_git(other, "add", "-A")
        _run_git(other, "commit", "-m", "web edit", "--quiet")
        _run_git(other, "push", self.bare, "HEAD:refs/heads/main")

        res = _sealer(self.vault).resync(push_url=self.bare)

        self.assertTrue(res.ok, res.describe())
        self.assertEqual(_remote_tree(self.bare), ["note.md"])
        self.assertEqual(res.files_deleted, 1)
        self.assertTrue(_is_ancestor(self.bare, old_tip, "main"))

    def test_additions_and_deletions_together(self):
        self._seed_mirror_with_vault("old-a.md", "old-b.md")
        os.remove(os.path.join(self.vault, "old-a.md"))
        _write(self.vault, "new-c.md", "# new\n" + "x" * 100)

        res = _sealer(self.vault).resync(push_url=self.bare)

        self.assertTrue(res.ok, res.describe())
        self.assertEqual(sorted(_remote_tree(self.bare)),
                         ["new-c.md", "old-b.md"])
        self.assertEqual(res.files_deleted, 1)
        self.assertEqual(res.files_added, 1)

    def test_empty_mirror_first_resync(self):
        _write(self.vault, "first.md", "# first\n" + "x" * 100)
        res = _sealer(self.vault).resync(push_url=self.bare)
        self.assertTrue(res.ok, res.describe())
        self.assertTrue(res.pushed)
        self.assertEqual(_remote_tree(self.bare), ["first.md"])

    def test_describe_and_to_dict_carry_the_delta(self):
        self._seed_mirror_with_vault("gone.md", "stays.md")
        os.remove(os.path.join(self.vault, "gone.md"))
        res = _sealer(self.vault).resync(push_url=self.bare)
        line = res.describe()
        self.assertIn("resync: mirror follows the vault", line)
        self.assertIn("1 file(s) deleted", line)
        d = res.to_dict()
        self.assertEqual(d["files_deleted"], 1)
        self.assertEqual(d["files_added"], 0)
        self.assertTrue(d["pushed"])

    def test_no_token_is_a_clear_error(self):
        _write(self.vault, "a.md", "x" * 50)
        res = VaultSeal(vault_path=self.vault, token="", repo_name="m",
                        auto_push=True).resync(push_url=self.bare)
        self.assertFalse(res.ok)
        self.assertFalse(res.pushed)
        self.assertIn("token", (res.error or ""))

    def test_missing_vault_is_a_clear_error(self):
        res = VaultSeal(vault_path=os.path.join(self.tmp, "nope"),
                        token="tok", repo_name="m").resync(push_url=self.bare)
        self.assertFalse(res.ok)
        self.assertIn("not found", (res.error or ""))

    def test_sealresult_defaults_unchanged(self):
        """The two new fields default to 0 — every existing seal result
        shape is untouched."""
        r = SealResult(sealed=True, files_changed=3)
        self.assertEqual(r.files_added, 0)
        self.assertEqual(r.files_deleted, 0)
        d = r.to_dict()
        self.assertEqual(d["files_added"], 0)
        self.assertEqual(d["files_deleted"], 0)


# ---------------------------------------------------------------------------
# 6. resync_from_config — both vaults, explicit-action semantics
# ---------------------------------------------------------------------------

class TestResyncFromConfig(unittest.TestCase):

    def _capture(self, config):
        """Run resync_from_config with a stubbed VaultSeal that records
        every construction (no git, no network)."""
        made = []

        class FakeSeal:
            def __init__(self, vault_path, token, repo_name,
                         auto_push, enabled, log=None):
                made.append({"vault_path": vault_path, "token": token,
                             "repo_name": repo_name,
                             "auto_push": auto_push, "enabled": enabled})
                self.repo_name = repo_name        # the bridge reads it
                self._res = SealResult(skipped_reason="stub")

            def resync(self):
                return self._res

        with mock.patch.object(vs_mod, "VaultSeal", FakeSeal):
            out = vs_mod.resync_from_config(config, log=None)
        return made, out

    def test_both_vaults_resynced(self):
        made, out = self._capture({
            "vault_path": "/v", "github_token": "tok",
            "website_vault_path": "/w", "website_repo_name": "webrepo",
            "pipelines": {"websites": True},
            "vaultseal": {"repo_name": "ghrepo"},
        })
        self.assertEqual(len(made), 2)
        gh, web = made
        self.assertEqual(gh["vault_path"], "/v")
        self.assertEqual(gh["repo_name"], "ghrepo")
        self.assertEqual(web["vault_path"], "/w")
        self.assertEqual(web["repo_name"], "webrepo")
        for m in made:
            self.assertEqual(m["token"], "tok")
            # explicit owner action: the post-run switches never gate it
            self.assertTrue(m["auto_push"])
            self.assertTrue(m["enabled"])
        self.assertIn("github", out)
        self.assertIn("websites", out)

    def test_websites_skipped_when_pipeline_off(self):
        made, out = self._capture({
            "vault_path": "/v", "github_token": "tok",
            "website_vault_path": "/w",
            "pipelines": {"websites": False},
        })
        self.assertEqual(len(made), 1)      # only the GitHub vault
        self.assertEqual(out["websites"].skipped_reason,
                         "websites pipeline off or no websites vault")

    def test_no_vaults_configured(self):
        made, out = self._capture({"github_token": "tok"})
        self.assertEqual(len(made), 1)      # the GitHub sealer is still built
        self.assertEqual(out["github"].skipped_reason,
                         "no GitHub vault configured")


# ---------------------------------------------------------------------------
# 7. Wiring — the Settings button, the warning modal, the CLI twin
# ---------------------------------------------------------------------------

class TestWiring(unittest.TestCase):

    def test_backup_tab_has_the_resync_button(self):
        import gitcurator.gui.app as ga
        src = inspect.getsource(ga.MainWindow._create_backup_tab)
        self.assertIn("vaultseal_resync_btn", src)
        self.assertIn("Resync Mirror", src)
        self.assertIn("self._vaultseal_resync", src)

    def test_resync_shows_the_warning_modal_first(self):
        import gitcurator.gui.app as ga
        src = inspect.getsource(ga.MainWindow._vaultseal_resync)
        self.assertIn("_show_custom_question", src)          # the modal
        self.assertIn("Resync Mirrors with GitHub?", src)    # its title
        self.assertIn("DELETED from the mirror repo", src)   # the warning
        self.assertIn("resync_from_config", src)             # the action

    def test_resync_runs_in_a_worker_thread(self):
        import gitcurator.gui.app as ga
        src = inspect.getsource(ga.MainWindow._vaultseal_resync)
        self.assertIn("QThread", src)
        self.assertIn("log_line", src)          # live progress in the log

    def test_cli_has_the_resync_flag(self):
        src = inspect.getsource(vs_mod)
        self.assertIn('"--resync"', src)
        self.assertIn("sealer.resync()", src)


if __name__ == "__main__":
    unittest.main()
