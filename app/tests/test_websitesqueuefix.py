#!/usr/bin/env python3
"""
test_websitesqueuefix.py — v0.24.1: the "websites never sync" queue fix.

Owner report 2026-09-30 (the v0.24.0 Windows build log):
  "it doesn't sync and see new sites, for example I add new sites (not
   github projects), the app is supposed to detect them > process them >
   put them in the websites vault but in logs it says: … All 621 GitHub
   repos already in vault! 0 pending. … All caught up — nothing undone in
   the bot queue."

Root cause — the bot-queue "pending" classification was GITHUB-ONLY:
  * ``_bot_queue_job`` classified ``urls`` against the GitHub vault and
    returned ``non_github_urls`` raw (never classified anywhere);
  * ``check_bot_queue``/``process_bot_queue``/the hero SYNC→PROCESS flip
    all keyed off that GitHub-only pending list, so a caught-up GitHub
    vault (0 pending repos) meant "All caught up" even with HUNDREDS of
    unprocessed website links in the queue — and the Websites pipeline
    never ran (the ProcessingWorker has supported websites-only batches
    since v0.11.0, but no caller ever started one from the queue flow).

The fix, layer by layer:
  1. ``worker_jobs._bot_queue_job`` now classifies the non-GitHub links
     against the WEBSITES vault (its own VaultIndex keyed by
     links.normalize_website_url) + the WebsiteStateDB dedupe layers —
     result gains pending_website_urls / websites_*_count buckets, and
     the websites_pipeline_off / websites_no_vault hint flags.
  2. ``bot_queue.check_bot_queue`` passes website_vault_path +
     websites_pipeline_on into the job, stores
     ``_bot_queue_pending_websites``, and shows BOTH pipelines in the
     badge / queue report / log lines.
  3. ``bot_queue.process_bot_queue`` + ``processing_control.
     start_processing`` start a websites-only batch when the GitHub side
     is caught up.
  4. ``hero._after_sync_fetch`` / ``_begin_hero_processing`` /
     ``_sync_run_button`` flip the hero button on EITHER pipeline's
     pending count.
  5. ``processing_finished`` Phase 5 gates on the batch's own
     ``_bot_source`` provenance + manifest, so a websites-only bot batch
     verifies and consumes the queue (and an import/retry batch never
     does, stale GUI lists notwithstanding).
  6. The CLI (--auto) gets the same classification and the same
     both-pipelines "pending" gate.

All against LOCAL temp dirs / stubs / monkeypatched workers — no network,
no GUI shown. The mixin-flow cases need the PyQt6 stack (offscreen) and
skip gracefully when it is unavailable (worker-side cases always run).
"""

import json
import os
import shutil
import tempfile
import types
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# ---------------------------------------------------------------------------
# Harness: worker_jobs imports PyQt6 only through gitcurator.gui.icons —
# stub it when the Qt stack can't load (CI installs the full stack and the
# mixin tests below then run; the worker-side cases work either way).
# ---------------------------------------------------------------------------
def _ensure_icons_importable():
    try:
        import gitcurator.gui.icons  # noqa: F401
        return
    except Exception:  # noqa: BLE001 — PyQt6 or its Qt libs missing
        import sys as _sys
        if "gitcurator.gui.icons" in _sys.modules:
            del _sys.modules["gitcurator.gui.icons"]
        fake = types.ModuleType("gitcurator.gui.icons")
        fake.set_btn_icon = lambda *a, **k: None
        _sys.modules["gitcurator.gui.icons"] = fake


_ensure_icons_importable()

from gitcurator.core import links as L  # noqa: E402
from gitcurator.core import website_pipeline as wp  # noqa: E402
from gitcurator.gui import vault_index as vi_mod  # noqa: E402
from gitcurator.gui import worker_jobs as wj  # noqa: E402


def _note(source):
    return (f"---\nsource: {source}\ntitle: x\ndate_processed: 2026-09-30\n"
            f"managed_by: gitcurator\n---\n\nbody text\n")


class _Log:
    """log_signal stand-in: collect + remember warnings for assertions."""

    def __init__(self):
        self.lines = []

    def emit(self, msg, level="info"):
        self.lines.append((level, msg))


class QueueFixBase(unittest.TestCase):
    """Temp GitHub vault + Websites vault + state DB + a fake fetch."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gc_wqfix_")
        self.gh_vault = os.path.join(self.tmp, "gh")
        self.web_vault = os.path.join(self.tmp, "web")
        os.makedirs(self.gh_vault)
        os.makedirs(self.web_vault)
        self.cache_db = os.path.join(self.tmp, "cache.db")

        # All repos already curated (the owner's 621-repo situation,
        # distilled to two).
        for i, repo in enumerate(("owner/repo1", "owner/repo2")):
            with open(os.path.join(self.gh_vault, f"n{i}.md"), "w",
                      encoding="utf-8") as fh:
                fh.write(_note(f"https://github.com/{repo}"))

        # Two real website notes.
        for i, src in enumerate(("https://example.com/",
                                 "https://done.org/page")):
            with open(os.path.join(self.web_vault, f"w{i}.md"), "w",
                      encoding="utf-8") as fh:
                fh.write(_note(src))

        self.log = _Log()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers -----------------------------------------------------------

    def _seed_state(self):
        """cache.db with one processed row + one dismissed row, pointed at
        by a WebsiteStateDB-patch used for BOTH seeding and the job (the
        job's default constructor reads APP_DIR/cache.db — never the real
        one during tests)."""
        _orig = wp.WebsiteStateDB
        state = _orig(db_path=self.cache_db)
        state.mark_processed("https://recorded.com/tool", "x.md",
                             "Tools", "", "full")
        # canonical of a bare domain KEEPS its root slash
        # (normalize_website_url drops '/' only on non-root paths) — state
        # rows always store the pipeline's canonical form.
        state.dismiss("https://deleted.com/", "note deleted by owner")
        state.close()

        def _factory(db_path="cache.db"):
            return _orig(db_path=self.cache_db)

        patcher = mock.patch.object(wp, "WebsiteStateDB", _factory)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run_job(self, non_github, *, websites_on=True, web_vault=None,
                 mark_read=False, **extra):
        """_bot_queue_job with the Telegram fetch stubbed to the given
        link lists (repos = the two already-in-vault ones)."""
        repos = ["https://github.com/owner/repo1",
                 "https://github.com/owner/repo2"]
        fake = {
            "success": True,
            "urls": repos,
            "non_github_urls": list(non_github),
            "total_messages": 9,
            "bot_queue": True,
            "max_message_id": 1263504,
            "raw_url_count": len(repos) + len(non_github) + 5,
            "duplicates_removed": 5,
            "min_id_used": 0,
        }
        with mock.patch.object(wj, "_run_telegram_worker",
                               return_value=fake):
            return wj._bot_queue_job(
                123, "hash", "+98...", {"enabled": False},
                "githubfetcherbot", self.log,
                mark_read=mark_read,
                vault_path=self.gh_vault,
                blocked_domains=L.blocked_domains_from_config({}),
                self_domains=L.self_domains_from_config({}),
                website_vault_path=(web_vault if web_vault is not None
                                    else self.web_vault),
                websites_pipeline_on=websites_on,
                **extra,
            )

    def _warnings(self):
        return [m for lvl, m in self.log.lines if lvl == "warning"]


# ---------------------------------------------------------------------------
# 1. The classification itself (runs everywhere — no Qt needed)
# ---------------------------------------------------------------------------

class TestQueueWebsiteClassification(QueueFixBase):

    def test_owners_exact_scenario_websites_pending(self):
        """0 pending repos + new site links → the websites ARE pending."""
        self._seed_state()
        res = self._run_job([
            "https://newsite.com/article",      # never seen
            "https://another.org/",             # never seen
        ])
        self.assertEqual(res.get("pending_urls"), [])
        self.assertEqual(res.get("pending_website_urls"),
                         ["https://newsite.com/article", "https://another.org/"])

    def test_every_dedupe_bucket(self):
        """in-vault / processed / dismissed / blocked / self / pending."""
        self._seed_state()
        res = self._run_job([
            "https://example.com/",             # real note in the vault
            "https://newsite.com/article",      # pending
            "https://x.com/u/status/1",         # blocked domain
            "https://github-to-obsidian-bot.aliassadi-plus.workers.dev/auth/?token=e8d1",
            "https://deleted.com/",             # dismissed
            "https://recorded.com/tool",        # processed (cache table)
        ])
        self.assertEqual(res.get("pending_website_urls"),
                         ["https://newsite.com/article"])
        self.assertEqual(res.get("websites_in_vault_count"), 1)
        self.assertEqual(res.get("websites_processed_count"), 1)
        self.assertEqual(res.get("websites_dismissed_count"), 1)
        self.assertEqual(res.get("websites_blocked_count"), 1)
        self.assertEqual(res.get("websites_self_count"), 1)
        self.assertEqual(res.get("websites_vault_index_count"), 2)

    def test_www_and_utm_variants_match_vault_note(self):
        """A www/utm-decorated repeat of a curated site is NOT pending."""
        self._seed_state()
        res = self._run_job([
            "https://www.example.com/?utm_source=x",   # == example.com
            "http://done.org/page/",                   # == done.org/page
        ])
        self.assertEqual(res.get("pending_website_urls"), [])
        self.assertEqual(res.get("websites_in_vault_count"), 2)

    def test_failed_review_placeholder_is_pending(self):
        """A fetch-failed _review placeholder (retries left) stays PENDING
        — the pipeline's upgrade path re-processes it."""
        # _review notes ARE indexed (VaultIndex only skips _moc/_inbox/
        # attachments/.obsidian). State rows always store the CANONICAL url
        # (the pipeline records normalize_website_url(url) — a bare domain
        # keeps its root slash).
        rev = os.path.join(self.web_vault, "_review")
        os.makedirs(rev)
        with open(os.path.join(rev, "flaky.md"), "w", encoding="utf-8") as fh:
            fh.write(_note("https://flaky.org/"))
        _orig = wp.WebsiteStateDB
        state = _orig(db_path=self.cache_db)
        state.mark_processed("https://flaky.org/",
                             os.path.join(rev, "flaky.md"), "", "", "failed")
        state.close()

        def _factory(db_path="cache.db"):
            return _orig(db_path=self.cache_db)

        patcher = mock.patch.object(wp, "WebsiteStateDB", _factory)
        patcher.start()
        self.addCleanup(patcher.stop)

        res = self._run_job(["https://flaky.org/"])
        self.assertEqual(res.get("pending_website_urls"), ["https://flaky.org/"])
        self.assertEqual(res.get("websites_in_vault_count"), 0)

    def test_pipeline_off_hint(self):
        """Websites pipeline OFF → no pending websites + the hint flag."""
        res = self._run_job(["https://newsite.com/article"],
                            websites_on=False)
        self.assertEqual(res.get("pending_website_urls"), [])
        self.assertTrue(res.get("websites_pipeline_off"))
        self.assertIsNone(res.get("websites_no_vault"))

    def test_no_vault_hint(self):
        """Pipeline ON but no Websites vault → hint flag, nothing pending."""
        res = self._run_job(["https://newsite.com/article"],
                            web_vault="")
        self.assertEqual(res.get("pending_website_urls"), [])
        self.assertTrue(res.get("websites_no_vault"))
        self.assertIsNone(res.get("websites_pipeline_off"))

    def test_no_non_github_links(self):
        """A GitHub-only queue: empty classification, no crash."""
        self._seed_state()
        res = self._run_job([])
        self.assertEqual(res.get("pending_website_urls"), [])
        self.assertEqual(res.get("pending_urls"), [])

    def test_legacy_call_signature_still_works(self):
        """Old callers (no new kwargs) get an empty websites list — the
        GitHub path is byte-identical to the pre-fix behavior."""
        repos = ["https://github.com/owner/repo1"]
        fake = {"success": True, "urls": repos, "non_github_urls": ["https://x.org/"],
                "raw_url_count": 2, "duplicates_removed": 0}
        with mock.patch.object(wj, "_run_telegram_worker", return_value=fake):
            res = wj._bot_queue_job(123, "hash", "+98...", {"enabled": False},
                                    "githubfetcherbot", self.log,
                                    vault_path=self.gh_vault)
        self.assertEqual(res.get("pending_urls"), [])       # repo1 is in vault
        self.assertEqual(res.get("pending_website_urls"), [])

    def test_failed_classification_degrades_to_all_pending(self):
        """A broken Websites vault path → a loud warning + every non-GitHub
        link pending (never silently 'all caught up')."""
        res = self._run_job(["https://a.org/", "https://b.org/"],
                            web_vault=os.path.join(self.tmp, "nope"))
        self.assertEqual(res.get("pending_website_urls"),
                         ["https://a.org/", "https://b.org/"])
        self.assertTrue(any("Websites vault not found" in m
                            for m in self._warnings()))


# ---------------------------------------------------------------------------
# 2. The URL equivalence rules the classification relies on
# ---------------------------------------------------------------------------

class TestWebsiteUrlEquivalence(unittest.TestCase):

    def test_tracking_and_www_and_scheme(self):
        self.assertEqual(
            L.normalize_website_url("https://www.newsite.com/article?utm_source=x"),
            "https://newsite.com/article")
        self.assertEqual(
            L.normalize_website_url("http://Example.com/page/"),
            "https://example.com/page")
        # idempotent — the classifier canonicalizes per link
        once = L.normalize_website_url("https://www.newsite.com/article?utm_source=x")
        self.assertEqual(L.normalize_website_url(once), once)

    def test_identity_params_kept(self):
        self.assertEqual(
            L.normalize_website_url("https://youtube.com/watch?v=abc"),
            "https://youtube.com/watch?v=abc")

    def test_vault_index_matches_decorated_variants(self):
        tmp = tempfile.mkdtemp(prefix="gc_wqvi_")
        try:
            with open(os.path.join(tmp, "n.md"), "w", encoding="utf-8") as fh:
                fh.write(_note("https://newsite.com/article"))
            vi = vi_mod.VaultIndex(tmp, normalizer=L.normalize_website_url)
            vi.rebuild()
            self.assertTrue(vi.has_url("https://www.newsite.com/article?utm_source=x"))
            self.assertFalse(vi.has_url("https://other.org/"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 3. The GUI mixin flow (needs the PyQt6 stack — offscreen in CI)
# ---------------------------------------------------------------------------

try:
    from gitcurator.gui.main_window.bot_queue import BotQueueMixin
    from gitcurator.gui.main_window.hero import HeroMixin
    _QT_OK = True
except BaseException:  # noqa: BLE001 — PyQt6 stack unavailable (bot_queue sys.exits)
    _QT_OK = False


@unittest.skipUnless(_QT_OK, "PyQt6 stack unavailable (runs in CI)")
class TestMixinFlow(unittest.TestCase):
    """The three GUI decision points that used to be GitHub-only."""

    def _window(self, *, repos=None, websites=None, non_github=None):
        win = BotQueueMixin()
        win.calls = []
        win._bot_queue_urls = list(repos or [])
        win._bot_queue_pending_websites = list(websites or [])
        win._bot_queue_non_github = list(non_github or [])
        win._bot_queue_duplicates = 0
        win._bot_queue_raw_count = 0
        win.logs = []

        def _log(msg, level="info"):
            win.logs.append((level, msg))

        def _confirm(count, source):
            win.calls.append(("confirm", count, source))
            return True

        def _start(urls, bot_source=False, non_github_urls=None,
                   intake_duplicates=0, raw_url_count=0):
            win.calls.append(("start", list(urls or []),
                              bot_source, list(non_github_urls or [])))

        win.log_message = _log
        win._confirm_batch = _confirm
        win._start_worker_with_urls = _start
        return win

    def test_process_bot_queue_websites_only_batch(self):
        """0 repos + N pending websites → the batch STARTS (the fix)."""
        win = self._window(websites=["https://newsite.com/article"],
                           non_github=["https://newsite.com/article"])
        win.process_bot_queue()
        starts = [c for c in win.calls if c[0] == "start"]
        self.assertEqual(len(starts), 1)
        _, urls, bot_source, non_github = starts[0]
        self.assertEqual(urls, [])
        self.assertEqual(non_github, ["https://newsite.com/article"])
        self.assertTrue(bot_source)
        self.assertTrue(any("website link(s)" in m for _, m in win.logs))

    def test_process_bot_queue_mixed_batch(self):
        """Repos AND websites → one batch carrying both payloads."""
        win = self._window(repos=["https://github.com/o/r"],
                           websites=["https://newsite.com/article"],
                           non_github=["https://newsite.com/article"])
        win.process_bot_queue()
        starts = [c for c in win.calls if c[0] == "start"]
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0][1], ["https://github.com/o/r"])
        self.assertEqual(starts[0][3], ["https://newsite.com/article"])
        confirms = [c for c in win.calls if c[0] == "confirm"]
        self.assertEqual(confirms[0][1], 2)  # 1 repo + 1 website

    def test_process_bot_queue_nothing_pending_bails(self):
        win = self._window()
        win.process_bot_queue()
        self.assertFalse([c for c in win.calls if c[0] == "start"])
        self.assertTrue(any("No items in queue" in m for _, m in win.logs))

    # -- hero flow ---------------------------------------------------------

    class _Txt:
        def __init__(self, s=""):
            self._s = s

        def text(self):
            return self._s

    def _hero(self, *, repos=None, websites=None, import_file=""):
        hero = HeroMixin()
        hero.states = []
        hero._bot_queue_urls = list(repos or [])
        hero._bot_queue_pending_websites = list(websites or [])
        hero.import_file = self._Txt(import_file)
        hero.logs = []
        hero.log_message = lambda msg, level="info": hero.logs.append((level, msg))
        hero._set_hero_state = lambda state: hero.states.append(state)
        hero.progress_bar = types.SimpleNamespace(
            setFormat=lambda fmt: setattr(hero, "fmt", fmt))
        hero.progress_count = types.SimpleNamespace(
            setText=lambda t: setattr(hero, "cnt", t),
            setToolTip=lambda t: None)
        return hero

    def test_after_sync_fetch_flips_on_websites_only(self):
        """The owner's exact flow: fetch finds 0 repos, N websites →
        PROCESS (not 'All caught up')."""
        hero = self._hero(websites=["https://newsite.com/article"])
        hero._after_sync_fetch("bot_check", {"success": True})
        self.assertIn("process", hero.states)
        self.assertNotIn("sync", hero.states)
        self.assertTrue(any("website link(s)" in m and "PROCESS" in m
                            for _, m in hero.logs))

    def test_after_sync_fetch_all_caught_up_only_when_both_done(self):
        hero = self._hero()
        hero._after_sync_fetch("bot_check", {"success": True})
        self.assertEqual(hero.states, ["sync"])
        self.assertTrue(any("All caught up" in m for _, m in hero.logs))

    def test_after_sync_fetch_mixed_counts(self):
        hero = self._hero(repos=["https://github.com/o/r"],
                          websites=["https://a.org/", "https://b.org/"])
        hero._after_sync_fetch("bot_check", {"success": True})
        self.assertIn("process", hero.states)
        self.assertEqual(hero.cnt, "0 / 3")
        self.assertEqual(hero.fmt, "3 ready to process")

    def test_begin_hero_processing_routes_websites_to_bot_queue(self):
        hero = self._hero(websites=["https://newsite.com/article"])
        routed = []
        hero.process_bot_queue = lambda: routed.append(True)
        hero.start_processing = lambda: routed.append("import")
        hero._begin_hero_processing()
        self.assertEqual(routed, [True])  # bot queue wins, import never runs

    def test_sync_run_button_label_counts_websites(self):
        hero = self._hero()
        hero._hero_state = "process"
        hero.start_btn = types.SimpleNamespace(
            text=lambda: "PROCESS",
            setText=lambda t: setattr(hero, "label", t),
            isEnabled=lambda: True,
            setEnabled=lambda b: None, setVisible=lambda b: None)
        hero.stop_btn = types.SimpleNamespace(setVisible=lambda b: None)
        hero._bot_queue_urls = ["https://github.com/o/r"]
        hero._bot_queue_pending_websites = ["https://a.org/"]
        hero._sync_run_button()
        self.assertEqual(hero.label, "PROCESS (2)")


# ---------------------------------------------------------------------------
# 4. End-to-end offscreen MainWindow — the REAL widget flow
#    (check_bot_queue's _on_finished renders the actual queue panel; only
#    the Telegram subprocess + the batch launcher are stubbed)
# ---------------------------------------------------------------------------

try:
    import gitcurator.gui.app as gui_app
    import gitcurator.gui.main_window.bot_queue as bot_queue_mod
    import gitcurator.gui.main_window.vaults_config as gui_vaults
    _QT_OK = _QT_OK and True
except BaseException:  # noqa: BLE001
    _QT_OK = False


class _FakeSignal:
    """Minimal signal: direct-connection semantics (same thread)."""

    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in list(self._slots):
            slot(*args)


class _FakeTestWorker:
    """TestWorker stand-in: start() runs the job SYNCHRONOUSLY on the
    caller's thread and emits finished_signal — check_bot_queue's whole
    _on_finished chain then executes against the real widgets."""

    def __init__(self, fn, test_name, *args, **kwargs):
        self._fn = fn
        self._test_name = test_name
        self._args = args
        self._kwargs = kwargs
        self.log_message = _FakeSignal()
        self.finished_signal = _FakeSignal()
        self.code_requested = _FakeSignal()

    def request_code(self, prompt_type="CODE"):
        """Code callback — interactive auth never happens in this test."""
        return ""

    def start(self):
        try:
            result = self._fn(*self._args, **self._kwargs)
        except BaseException as exc:  # noqa: BLE001 — mirror TestWorker.run
            result = {"success": False,
                      "error": f"{type(exc).__name__}: {exc}"}
        self.finished_signal.emit(self._test_name, result or {})


@unittest.skipUnless(_QT_OK, "PyQt6 stack unavailable (runs in CI)")
class TestOffscreenMainWindowFlow(unittest.TestCase):
    """The owner's log, replayed on the real window: 0 pending repos +
    new website links → the queue panel shows them, the hero button
    flips to PROCESS, and PROCESS starts a websites-only batch."""

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls._old_cwd = os.getcwd()
        cls._tmp = tempfile.mkdtemp(prefix="gc_wq_e2e_")
        os.chdir(cls._tmp)
        cls.gh = os.path.join(cls._tmp, "gh")
        os.makedirs(cls.gh)
        for i, repo in enumerate(("owner/repo1", "owner/repo2")):
            with open(os.path.join(cls.gh, f"n{i}.md"), "w",
                      encoding="utf-8") as fh:
                fh.write(_note(f"https://github.com/{repo}"))
        cls._old_cfg = gui_vaults.CONFIG_FILE
        cls._cfg_path = os.path.join(cls._tmp, "config.json")
        with open(cls._cfg_path, "w", encoding="utf-8") as fh:
            json.dump({
                "telegram_api_id": 123, "telegram_api_hash": "x",
                "telegram_phone": "+98...", "bot_username": "githubfetcherbot",
                "vault_path": cls.gh, "website_vault_path": "",
                "pipelines": {"github": True, "websites": True},
                "proxy": {"enabled": False},
            }, fh)
        gui_vaults.CONFIG_FILE = cls._cfg_path

    def setUp(self):
        # a FRESH Websites vault per test (classification + _inbox writes
        # must not leak between the two flows)
        self.web = os.path.join(self._tmp, f"web_{next(self._counter)}")
        os.makedirs(self.web)

    _counter = iter(range(1000))

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls._old_cwd)
        gui_vaults.CONFIG_FILE = cls._old_cfg
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _check_queue_with_fake_fetch(self, win, non_github):
        """Drive check_bot_queue end-to-end: the REAL _bot_queue_job runs
        (only the Telethon subprocess is stubbed), its _on_finished renders
        the real widgets, and the hero on_done callback fires."""
        # Point THIS test's fresh Websites vault through the SETTINGS
        # WIDGET — check_bot_queue calls save_config() (which rebuilds the
        # live config from the widgets) BEFORE the job's closure reads
        # config['website_vault_path'], so a bare dict mutation is wiped.
        if hasattr(win, 'website_vault_input'):
            win.website_vault_input.setText(self.web)
        win.config['website_vault_path'] = self.web
        repos = ["https://github.com/owner/repo1",
                 "https://github.com/owner/repo2"]
        fake = {"success": True, "urls": repos,
                "non_github_urls": list(non_github),
                "total_messages": 5, "bot_queue": True,
                "max_message_id": 1263504,
                "raw_url_count": len(repos) + len(non_github),
                "duplicates_removed": 0, "min_id_used": 0}
        cache_db = os.path.join(self._tmp, "cache.db")
        _orig_state = wp.WebsiteStateDB

        def _factory(db_path="cache.db"):
            return _orig_state(db_path=cache_db)

        with mock.patch.object(wj, "_run_telegram_worker",
                               return_value=fake), \
                mock.patch.object(wp, "WebsiteStateDB", _factory), \
                mock.patch.object(bot_queue_mod, "TestWorker", _FakeTestWorker):
            win.check_bot_queue(on_done=win._after_sync_fetch)

    def test_full_sync_flow_websites_only(self):
        win = gui_app.MainWindow()
        self._check_queue_with_fake_fetch(
            win, ["https://newsite.com/article", "https://another.org/"])

        # 1. the pending classification landed on the window
        self.assertEqual(win._bot_queue_urls, [])
        self.assertEqual(win._bot_queue_pending_websites,
                         ["https://newsite.com/article", "https://another.org/"])
        # 2. the badge counts BOTH pipelines
        self.assertIn("2 pending", win.pending_badge.text())
        # 3. the queue panel shows the websites section
        panel = win.queue_display.toPlainText()
        self.assertIn("PENDING WEBSITES (2", panel)
        self.assertIn("https://newsite.com/article", panel)
        self.assertIn("newsite.com/article", panel)
        self.assertIn("WEBSITES pipeline", panel)
        # 4. the hero flipped to PROCESS (not 'All caught up')
        self.assertEqual(win._hero_state, "process")
        self.assertEqual(win.start_btn.text(), "PROCESS (2)")
        # 5. the _inbox record layer wrote the links (v0.20.0 behavior
        #    unchanged: the tables land in the WEBSITES vault)
        inbox = os.path.join(self.web, "_inbox", "other_links.md")
        self.assertTrue(os.path.exists(inbox))
        with open(inbox, encoding="utf-8") as fh:
            rows = fh.read()
        self.assertIn("https://newsite.com/article", rows)
        self.assertIn("https://another.org/", rows)

        # 6. PROCESS starts a websites-only batch (launcher captured)
        started = []
        with mock.patch.object(
                type(win), "_start_worker_with_urls",
                lambda self, urls, **kw: started.append((list(urls or []), kw))):
            win._begin_hero_processing()
        self.assertEqual(len(started), 1)
        urls, kw = started[0]
        self.assertEqual(urls, [])                       # GitHub is done
        self.assertTrue(kw.get("bot_source"))
        self.assertIn("https://newsite.com/article", kw.get("non_github_urls"))
        win.close()

    def test_full_sync_flow_all_caught_up_when_websites_done(self):
        # both links already have notes in the Websites vault
        for i, src in enumerate(("https://newsite.com/article",
                                 "https://another.org/")):
            with open(os.path.join(self.web, f"done{i}.md"), "w",
                      encoding="utf-8") as fh:
                fh.write(_note(src))
        win = gui_app.MainWindow()
        self._check_queue_with_fake_fetch(
            win, ["https://newsite.com/article", "https://another.org/"])

        self.assertEqual(win._bot_queue_urls, [])
        self.assertEqual(win._bot_queue_pending_websites, [])
        self.assertIn("0 pending", win.pending_badge.text())
        self.assertIn("All GitHub repos are already in the vault!",
                      win.queue_display.toPlainText())
        self.assertIn("All non-GitHub links are processed into the "
                      "Websites vault!",
                      win.queue_display.toPlainText())
        self.assertEqual(win._hero_state, "sync")
        win.close()


if __name__ == "__main__":
    unittest.main()
