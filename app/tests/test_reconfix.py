#!/usr/bin/env python3
"""
test_reconfix.py — v0.38.0: the reconciliation false-positive fix.

The owner's report (screenshot vvb.png): "despite those 620 projects
are correctly stored in vault, app shows 'false positive' of needing
retry" — the amber banner read "620 links from the previous batch need
retry" with a "Retry 620" button, while every one of the 620 projects
was safely in the vault.

Root cause: LinkTracker's reconciliation (and verify / get_all_clear)
trusted the MANIFEST ALONE. The bot queue re-serves the entire history
on every sync, so a batch interrupted early (Stop, app close, crash,
rate limit) leaves hundreds of intake rows "pending" that are, in fact,
already stored from earlier batches. Reconciliation read those stale
rows and cried retry — and the retry itself would have burned one
GitHub API call per vault-present link (the dedup ran AFTER get_repo),
feeding the very rate-limit loop that interrupted the batch.

Covered here (hermetic — temp vaults, fake GitHub client, no network):
  1. get_reconciliation_urls — the owner's exact scenario: a manifest
     full of pending/failed/processing rows whose notes ARE in the
     vault → zero retries, every row healed to skipped (with its real
     note_path), the healed manifest persisted, the healed count
     surfaced on the tracker.
  2. the same read stays honest for genuinely missing links (mixed
     batches return only the absent), for old shapes (no vault notes →
     the manifest-only verdict of v0.32), and for lost notes
     (processed + note really gone → retry).
  3. moved notes — a processed row whose note_path went stale (the
     owner reorganized the vault) is re-pointed at the note's new home,
     never retried.
  4. no leakage — healing writes the previous manifest dict, never the
     tracker's live manifest (the next batch's intake must inherit
     nothing).
  5. verify() — pending rows heal against the vault (skipped bucket,
     accounting still reconciles); processed rows with a stale path
     re-point instead of failing; processed rows with a truly lost
     note still fail (the old guarantee).
  6. get_all_clear — the bot-queue mark-read gate: failed / processing
     / pending GitHub rows whose notes are in the vault no longer
     block; genuinely absent ones still do. An injected index (the
     worker's live one) is reused without a rescan.
  7. the worker's dedup ORDER — the vault-index skip now fires BEFORE
     the GitHub API call: a real ProcessingWorker batch over a
     vault-present URL never touches get_repo (the 620-call API burn
     is gone), while a genuinely-new URL still goes through the API
     path, and the worker shares its live index with the batch's
     LinkTracker.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)

from gitcurator.gui.link_tracker import LinkTracker
from gitcurator.gui.vault_index import VaultIndex


# ---------------------------------------------------------------------------
# Hermetic helpers
# ---------------------------------------------------------------------------

def _note(vault, rel, url, body="x" * 300):
    """Write a vault note whose frontmatter source is the URL (the
    VaultIndex dedupe key)."""
    path = os.path.join(vault, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"---\nsource: {url}\ntitle: t\n---\n{body}\n")
    return path


def _row(url, status, note_path=None, error=None, kind="github"):
    return {"url": url, "normalized": url, "type": kind, "status": status,
            "note_path": note_path, "error": error, "processed_at": None}


def _manifest(vault, links, batch_id="b1"):
    with open(os.path.join(vault, "links_manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump({"batch_id": batch_id, "source": "bot",
                   "total_links": len(links), "links": links}, f)


def _load_manifest(vault):
    with open(os.path.join(vault, "links_manifest.json"), encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 1-4. get_reconciliation_urls — the banner's truth
# ---------------------------------------------------------------------------

class TestReconciliation(unittest.TestCase):
    """Every case drives the REAL reconciliation read on a temp vault."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="reconfix-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(os.path.join(self.vault, "AI"), exist_ok=True)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_owners_scenario_all_stored(self):
        """The screenshot: pending/failed/processing rows whose notes are
        all in the vault → ZERO retries, every row healed + persisted."""
        urls = [f"https://github.com/o/r{i}" for i in range(620)]
        for u in urls:
            _note(self.vault, f"AI/{u.rsplit('/', 1)[1]}.md", u)
        _manifest(self.vault, [_row(u, status)
                               for u, status in
                               [(urls[0], "pending"),
                                (urls[1], "processing"),
                                (urls[2], "failed")]] +
                               [_row(u, "pending") for u in urls[3:]])
        t = LinkTracker(self.vault)
        retry = t.get_reconciliation_urls()
        self.assertEqual(retry, [])
        self.assertEqual(t.reconciled_vault_hits, 620)
        healed = {l["url"]: l for l in _load_manifest(self.vault)["links"]}
        for u in urls:
            self.assertEqual(healed[u]["status"], "skipped", u)
            self.assertEqual(healed[u]["error"], "already in vault (reconciled)")
            self.assertTrue(healed[u]["note_path"], u)
            self.assertTrue(healed[u]["processed_at"], u)

    def test_mixed_batch_returns_only_the_absent(self):
        """Half stored, half missing → only the missing are retried."""
        _note(self.vault, "AI/stored.md", "https://github.com/o/stored")
        _manifest(self.vault, [
            _row("https://github.com/o/stored", "pending"),
            _row("https://github.com/o/missing", "pending"),
        ])
        retry = LinkTracker(self.vault).get_reconciliation_urls()
        self.assertEqual(retry, ["https://github.com/o/missing"])

    def test_old_shape_manifest_only_verdict(self):
        """No vault notes → the v0.32 behavior is untouched (the smoke
        suite's banner case depends on it)."""
        _manifest(self.vault, [
            _row("https://github.com/a/one", "failed"),
            _row("https://github.com/b/two", "pending"),
            _row("https://github.com/c/three", "processing"),
            _row("https://github.com/d/four", "skipped"),   # never a retry
        ])
        t = LinkTracker(self.vault)
        retry = t.get_reconciliation_urls()
        self.assertEqual(sorted(retry), [
            "https://github.com/a/one", "https://github.com/b/two",
            "https://github.com/c/three"])
        self.assertEqual(t.reconciled_vault_hits, 0)

    def test_moved_note_repointed_not_retried(self):
        """processed + stale note_path (owner reorganized the vault) →
        the row is re-pointed at the note's new home, never retried."""
        new_path = _note(self.vault, "Moved/one.md",
                         "https://github.com/a/one")
        stale = os.path.join(self.vault, "AI", "one.md")   # no longer there
        _manifest(self.vault, [_row("https://github.com/a/one", "processed",
                                    note_path=stale)])
        retry = LinkTracker(self.vault).get_reconciliation_urls()
        self.assertEqual(retry, [])
        self.assertEqual(_load_manifest(self.vault)["links"][0]["note_path"],
                         new_path)

    def test_lost_note_still_retried(self):
        """processed + the note really gone (not at the recorded path,
        not anywhere in the vault) → retried (data loss is real)."""
        _manifest(self.vault, [
            _row("https://github.com/a/gone", "processed",
                 note_path=os.path.join(self.vault, "AI", "gone.md"))])
        retry = LinkTracker(self.vault).get_reconciliation_urls()
        self.assertEqual(retry, ["https://github.com/a/gone"])

    def test_no_manifest_is_empty(self):
        self.assertEqual(LinkTracker(self.vault).get_reconciliation_urls(), [])

    def test_healing_never_leaks_into_the_live_manifest(self):
        """The healed PREVIOUS dict is what gets written; the tracker's
        own manifest stays the fresh empty skeleton (the next batch's
        intake must inherit nothing)."""
        _note(self.vault, "AI/one.md", "https://github.com/a/one")
        _manifest(self.vault, [_row("https://github.com/a/one", "pending")])
        t = LinkTracker(self.vault)
        before = json.dumps(t.manifest, sort_keys=True)
        t.get_reconciliation_urls()
        self.assertEqual(json.dumps(t.manifest, sort_keys=True), before)
        self.assertEqual(t.manifest["links"], [])

    def test_second_read_is_stable(self):
        """Healing is idempotent — a second reconciliation of the healed
        manifest still returns zero (the rows are now skipped)."""
        _note(self.vault, "AI/one.md", "https://github.com/a/one")
        _manifest(self.vault, [_row("https://github.com/a/one", "pending")])
        t1 = LinkTracker(self.vault)
        self.assertEqual(t1.get_reconciliation_urls(), [])
        t2 = LinkTracker(self.vault)
        self.assertEqual(t2.get_reconciliation_urls(), [])
        self.assertEqual(t2.reconciled_vault_hits, 0)


# ---------------------------------------------------------------------------
# 5. verify() — the dashboard report heals the same way
# ---------------------------------------------------------------------------

class TestVerifyHealing(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="reconfix-v-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_pending_in_vault_counts_skipped(self):
        _note(self.vault, "AI/one.md", "https://github.com/a/one")
        t = LinkTracker(self.vault)
        t.manifest = {"batch_id": "b", "source": "bot", "total_links": 2,
                      "links": [
                          _row("https://github.com/a/one", "pending"),
                          _row("https://github.com/z/new", "pending"),
                      ]}
        rep = t.verify()
        self.assertEqual(rep["github_skipped"], 1)
        self.assertEqual(rep["github_pending"], 1)      # z/new is real work
        self.assertFalse(rep["verification_passed"])
        self.assertTrue(rep["accounting_ok"])
        self.assertEqual(t.manifest["links"][0]["status"], "skipped")
        self.assertTrue(t.manifest["links"][0]["note_path"].endswith("one.md"))

    def test_processed_stale_path_repointed(self):
        new_path = _note(self.vault, "Moved/one.md",
                         "https://github.com/a/one")
        t = LinkTracker(self.vault)
        t.manifest = {"batch_id": "b", "source": "bot", "total_links": 1,
                      "links": [_row("https://github.com/a/one", "processed",
                                     note_path=os.path.join(self.vault,
                                                            "AI", "one.md"))]}
        rep = t.verify()
        self.assertEqual(rep["github_processed"], 1)
        self.assertEqual(rep["github_failed"], 0)
        self.assertTrue(rep["verification_passed"])
        self.assertEqual(t.manifest["links"][0]["note_path"], new_path)

    def test_processed_lost_note_still_fails(self):
        t = LinkTracker(self.vault)
        t.manifest = {"batch_id": "b", "source": "bot", "total_links": 1,
                      "links": [_row("https://github.com/a/gone", "processed",
                                     note_path=os.path.join(self.vault,
                                                            "AI", "gone.md"))]}
        rep = t.verify()
        self.assertEqual(rep["github_failed"], 1)
        self.assertFalse(rep["verification_passed"])
        self.assertEqual(rep["failed_links"][0]["error"],
                         "note file missing")

    def test_processed_valid_note_unchanged(self):
        p = _note(self.vault, "AI/one.md", "https://github.com/a/one")
        t = LinkTracker(self.vault)
        t.manifest = {"batch_id": "b", "source": "bot", "total_links": 1,
                      "links": [_row("https://github.com/a/one", "processed",
                                     note_path=p)]}
        rep = t.verify()
        self.assertEqual(rep["github_processed"], 1)
        self.assertTrue(rep["verification_passed"])


# ---------------------------------------------------------------------------
# 6. get_all_clear — the bot-queue mark-read gate
# ---------------------------------------------------------------------------

class TestAllClearGroundTruth(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="reconfix-c-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _manifest_with(self, *rows):
        t = LinkTracker(self.vault)
        t.manifest = {"batch_id": "b", "source": "bot",
                      "total_links": len(rows), "links": list(rows)}
        return t

    def test_failed_in_vault_does_not_block(self):
        _note(self.vault, "AI/one.md", "https://github.com/a/one")
        t = self._manifest_with(_row("https://github.com/a/one", "failed"))
        self.assertTrue(t.get_all_clear())

    def test_failed_absent_still_blocks(self):
        t = self._manifest_with(_row("https://github.com/a/gone", "failed"))
        self.assertFalse(t.get_all_clear())

    def test_processing_in_vault_does_not_block(self):
        _note(self.vault, "AI/one.md", "https://github.com/a/one")
        t = self._manifest_with(
            _row("https://github.com/a/one", "processing"))
        self.assertTrue(t.get_all_clear())

    def test_pending_github_in_vault_does_not_block(self):
        _note(self.vault, "AI/one.md", "https://github.com/a/one")
        t = self._manifest_with(
            _row("https://github.com/a/one", "pending"))
        self.assertTrue(t.get_all_clear())

    def test_pending_github_absent_still_blocks(self):
        t = self._manifest_with(
            _row("https://github.com/a/gone", "pending"))
        self.assertFalse(t.get_all_clear())

    def test_pending_non_github_never_blocks(self):
        """The v29.4 rule is unchanged: non-GitHub pending is a duplicate
        already recorded elsewhere (websites pipeline semantics)."""
        t = self._manifest_with(
            _row("https://example.com/x", "pending", kind="non-github"))
        self.assertTrue(t.get_all_clear())

    def test_injected_index_is_reused_not_rebuilt(self):
        """The worker shares its live index (set_vault_index); the
        ground-truth reads must use THAT object — no second vault scan."""
        _note(self.vault, "AI/one.md", "https://github.com/a/one")
        idx = VaultIndex(self.vault)
        idx.rebuild()
        t = LinkTracker(self.vault)
        t.set_vault_index(idx)
        self.assertIs(t._ground_truth(), idx)
        t.manifest = {"batch_id": "b", "source": "bot", "total_links": 1,
                      "links": [_row("https://github.com/a/one", "failed")]}
        self.assertTrue(t.get_all_clear())
        self.assertIs(t._ground_truth(), idx)

    def test_vault_has_public_face(self):
        _note(self.vault, "AI/one.md", "https://github.com/a/one")
        t = LinkTracker(self.vault)
        self.assertTrue(t.vault_has("https://github.com/a/one"))
        self.assertFalse(t.vault_has("https://github.com/a/gone"))


# ---------------------------------------------------------------------------
# 7. the worker's dedup ORDER — no GitHub API call for stored links
# ---------------------------------------------------------------------------

class TestWorkerDedupOrder(unittest.TestCase):
    """A REAL ProcessingWorker batch (the phase-1 harness pattern): one
    URL already stored in the vault, one genuinely new. The stored one
    must be skipped by the vault index BEFORE get_repo; the new one
    still reaches the API (404 path → missing-repo note)."""

    def setUp(self):
        from gitcurator.core import dryrun
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix="reconfix-w-")
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault, exist_ok=True)
        self.db_path = os.path.join(self.tmp, "cache.db")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_vault_dedup_before_api_call(self):
        import gitcurator.gui.processing_worker as _gui_pw
        from gitcurator.core import note_state
        from gitcurator.gui.app import ProcessingWorker
        from github import GithubException

        stored = "https://github.com/o/stored"
        fresh = "https://github.com/o/fresh404"
        _note(self.vault, "AI/stored.md", stored)

        events = []

        class _Sig:
            def __init__(self, name):
                self.name = name

            def emit(self, *a):
                events.append((self.name, a))

            def connect(self, *a):
                pass

        api_calls = []

        class _Rate:
            remaining = 5000

            class core:
                pass

        class _CountingGithub:
            def __init__(self, *a, **k):
                pass

            def get_rate_limit(self):
                return _Rate()

            def get_repo(self, full):
                api_calls.append(full)
                raise GithubException(404, {"message": "Not Found"})

        worker = ProcessingWorker(
            config={'vault_path': self.vault,
                    'pipelines': {'github': True},
                    'llm_provider': 'cloud',
                    'cloud_api_url': 'http://127.0.0.1:9/v1',
                    'cloud_model': 'test', 'github_token': '',
                    'timeout_per_repo': 5, 'max_retries': 1,
                    'delay_between_api_calls': 0},
            mode='direct', urls=[stored, fresh],
            headless=True, dry_run=False)
        worker.log_message = _Sig('log')
        worker.finished_signal = _Sig('finished')
        worker.progress_updated = _Sig('progress')
        worker.status_updated = _Sig('status')

        orig_ns_init = note_state.NoteStateDB.__init__
        orig_github = _gui_pw.Github

        _ns_db = self.db_path

        def _patched_ns(self, db_path_arg="cache.db"):
            orig_ns_init(self, db_path=_ns_db
                         if db_path_arg == "cache.db" else db_path_arg)

        # Hermetic CacheDB too — the worker constructs it with the
        # default path; point that at the temp db (never the real one).
        from gitcurator.gui.cache_db import CacheDB
        orig_cache_init = CacheDB.__init__
        _cache_db = self.db_path

        def _patched_cache(self, db_path="cache.db"):
            orig_cache_init(self, db_path=_cache_db
                            if db_path == "cache.db" else db_path)

        note_state.NoteStateDB.__init__ = _patched_ns
        CacheDB.__init__ = _patched_cache
        _gui_pw.Github = _CountingGithub
        try:
            worker._run_impl()
        finally:
            note_state.NoteStateDB.__init__ = orig_ns_init
            CacheDB.__init__ = orig_cache_init
            _gui_pw.Github = orig_github

        # THE fix: the stored URL never reached the API; only the fresh
        # one did (its 404 wrote a missing-repo note).
        self.assertEqual(api_calls, ["o/fresh404"])

        logs = [a[0] for name, a in events if name == 'log']
        self.assertTrue(any("Already in vault" in m and "o/stored" in m
                            for m in logs), logs[-30:])

        # The manifest: both terminal, the stored one a dedup skip.
        manifest = _load_manifest(self.vault)
        by_url = {l["url"]: l for l in manifest["links"]}
        self.assertEqual(by_url[stored]["status"], "skipped")
        self.assertEqual(by_url[stored]["error"], "already in vault")
        self.assertEqual(by_url[fresh]["status"], "skipped")

        # The batch's tracker verified clear against the SHARED index.
        self.assertIsNotNone(worker.link_tracker)
        self.assertIs(worker.link_tracker._injected_index,
                      worker._vault_index)

        # And the ground-truth gate agrees: everything is accounted for.
        self.assertTrue(worker.link_tracker.get_all_clear())


if __name__ == '__main__':
    unittest.main()
