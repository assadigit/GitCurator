"""Characterization tests for the gui/app.py split (branch refactor/gui-app-split).

These pin the PUBLIC SURFACE of gitcurator.gui.app (names, kinds,
identities) and the observed behavior of its pure functions, so the
structural split into submodules cannot change anything observable.
They must pass before, during and after every extraction step.

Behavior values below were captured from the UNMODIFIED baseline
(tag baseline-before-split) on 2026-09-30.
"""

import os
import sys
import tempfile
import unittest

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gitcurator.gui.app as gui_app  # noqa: E402


class TestPublicSurface(unittest.TestCase):
    """The facade contract: everything importable at baseline stays importable."""

    def test_entry_point_main(self):
        self.assertTrue(callable(gui_app.main))

    def test_main_classes(self):
        for name in ("MainWindow", "ProcessingWorker", "TestWorker", "CacheDB",
                     "LinkTracker", "VaultIndex", "SettingsDialog",
                     "ConnectionTestDialog", "_GuiLogHandler"):
            self.assertIsInstance(getattr(gui_app, name), type, name)

    def test_main_window_is_qt_main_window(self):
        from PyQt6.QtWidgets import QMainWindow
        self.assertTrue(issubclass(gui_app.MainWindow, QMainWindow))

    def test_main_window_key_methods(self):
        for name in ("initUI", "closeEvent", "log_message", "start_processing",
                     "stop_processing", "processing_finished", "save_config",
                     "load_config", "check_bot_queue", "update_dashboard",
                     "apply_light_theme", "apply_dark_theme", "toggle_theme",
                     "test_all", "show_about_me_wizard", "_start_worker"):
            self.assertTrue(callable(getattr(gui_app.MainWindow, name, None)),
                            "MainWindow.%s missing" % name)

    def test_worker_key_methods(self):
        for name in ("run", "_run_impl", "_generate_master_index",
                     "_generate_final_report", "_generate_summary_log",
                     "_run_website_phase", "_llm_analyze", "_build_note",
                     "_record_missing_repo", "_backfill_missing_notes",
                     "_create_inbox_notes", "_pick_best_model"):
            self.assertTrue(callable(getattr(gui_app.ProcessingWorker, name, None)),
                            "ProcessingWorker.%s missing" % name)

    def test_cache_db_key_methods(self):
        for name in ("is_duplicate", "add_processed", "add_failed",
                     "get_failed_urls", "record_404", "confirm_dead",
                     "is_dead_link", "get_dead_url_set", "reset_dead_links",
                     "decommission", "is_decommissioned", "close"):
            self.assertTrue(callable(getattr(gui_app.CacheDB, name, None)),
                            "CacheDB.%s missing" % name)

    def test_link_tracker_key_methods(self):
        for name in ("intake", "mark_processing", "mark_processed",
                     "mark_recorded", "mark_failed", "mark_skipped",
                     "mark_blocked", "verify", "get_all_clear", "_save"):
            self.assertTrue(callable(getattr(gui_app.LinkTracker, name, None)),
                            "LinkTracker.%s missing" % name)

    def test_module_functions(self):
        for name in ("extract_github_urls", "clean_url", "normalize_url",
                     "dead_link_threshold", "classify_platform",
                     "_inbox_table_vault", "write_inbox_links_by_platform",
                     "find_obsidian_vaults", "run_headless",
                     "_is_process_running", "setup_logging",
                     "_import_telethon_fetcher", "fetch_github_urls_sync",
                     "TelegramFetcherError", "_run_telegram_worker",
                     "_telegram_test_job", "_bot_queue_job",
                     "_connection_battery_job", "_quick_detect_job",
                     "_safe_moc_name", "_install_gui_log_handler",
                     "_remove_gui_log_handler"):
            self.assertTrue(hasattr(gui_app, name), "module attr %s missing" % name)

    def test_module_constants(self):
        self.assertEqual(gui_app.DEAD_LINK_THRESHOLD, 3)
        self.assertIsInstance(gui_app.PLATFORM_INFO, dict)
        self.assertTrue(gui_app.__VERSION__.startswith("0.09"))
        self.assertTrue(gui_app._APP_DIR.endswith("app"))

    def test_platform_info_keys(self):
        self.assertEqual(sorted(gui_app.PLATFORM_INFO.keys()), [
            "arxiv", "huggingface", "linkedin", "medium", "other",
            "package_registry", "reddit", "x_twitter", "youtube"])

    def test_qt_star_names_exposed(self):
        for name in ("QWidget", "QMainWindow", "QApplication", "QTimer",
                     "QThread", "QDialog", "QLabel", "QPushButton",
                     "pyqtSignal", "Qt"):
            self.assertTrue(hasattr(gui_app, name), "Qt name %s missing" % name)

    def test_third_party_names_exposed(self):
        import github as _gh
        self.assertIs(gui_app.Github, _gh.Github)
        self.assertIs(gui_app.GithubException, _gh.GithubException)
        import ollama as _ollama
        self.assertIs(gui_app.ollama, _ollama)
        for name in ("Fore", "Style", "colorama", "load_dotenv"):
            self.assertTrue(hasattr(gui_app, name), name)

    def test_gitcurator_import_aliases_exposed(self):
        for name in ("_links", "_storage", "_note_builder", "_llm_client",
                     "_dryrun", "_note_state", "_website_pipeline",
                     "_connection_check", "_vaultseal", "_goodrepos",
                     "_icons", "TelegramLockManager",
                     "_run_worker_subprocess", "_kill_all_telegram_workers",
                     "_live_telegram_worker_count"):
            self.assertTrue(hasattr(gui_app, name), name)


class TestPureBehavior(unittest.TestCase):
    """Observed-at-baseline behavior of the pure helpers."""

    def test_extract_github_urls(self):
        text = ("see https://github.com/foo/bar and "
                "http://github.com/baz/qux plus https://example.com/x")
        self.assertEqual(gui_app.extract_github_urls(text),
                         ["https://github.com/foo/bar",
                          "https://github.com/baz/qux"])

    def test_clean_url(self):
        self.assertEqual(gui_app.clean_url(
            "https://github.com/foo/bar?tab=readme"),
            "https://github.com/foo/bar?tab=readme")

    def test_normalize_url(self):
        self.assertEqual(gui_app.normalize_url("https://www.github.com/Foo/Bar/"),
                         "https://www.github.com/Foo/Bar")
        self.assertEqual(gui_app.normalize_url("https://github.com/foo/bar/"),
                         "https://github.com/foo/bar")

    def test_classify_platform(self):
        f = gui_app.classify_platform
        self.assertEqual(f("https://x.com/post/123"), "x_twitter")
        self.assertEqual(f("https://t.me/s/somechannel"), "other")
        self.assertEqual(f("https://www.youtube.com/watch?v=abc"), "youtube")
        self.assertEqual(f("https://github.com/a/b"), "github")
        self.assertEqual(f("https://example.com/page"), "other")

    def test_safe_moc_name(self):
        f = gui_app._safe_moc_name
        self.assertEqual(f("AI & ML"), "AI & ML")
        self.assertEqual(f("Software/Tools!"), "Software_Tools!")
        self.assertEqual(f("  spaced name  "), "spaced name")

    def test_inbox_table_vault(self):
        f = gui_app._inbox_table_vault
        self.assertEqual(f({"website_vault_path": "/w", "vault_path": "/v"}), "/w")
        self.assertEqual(f({"vault_path": "/v"}), "/v")
        self.assertEqual(f({}), "")

    def test_dead_link_threshold(self):
        f = gui_app.dead_link_threshold
        self.assertEqual(f({"notfound_strike_threshold": 7}), 7)
        self.assertEqual(f({"notfound_strike_threshold": 1}), 2)  # clamped
        self.assertEqual(f({"notfound_strike_threshold": "x"}), 3)
        self.assertEqual(f({}), 3)

    def test_is_process_running(self):
        self.assertTrue(gui_app._is_process_running(os.getpid()))
        self.assertFalse(gui_app._is_process_running(999999))


class TestConstructorsSmoke(unittest.TestCase):
    """Constructors keep working on temporary paths (no network, no GUI)."""

    def test_cache_db_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = gui_app.CacheDB(os.path.join(tmp, "cache.db"))
            try:
                self.assertFalse(db.is_duplicate("https://github.com/x/y"))
            finally:
                db.close()
                db.close()  # idempotent by design

    def test_vault_index_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            vi = gui_app.VaultIndex(tmp)
            self.assertEqual(vi.count, 0)
            self.assertFalse(vi.has_url("https://github.com/a/b"))

    def test_link_tracker_construct(self):
        with tempfile.TemporaryDirectory() as tmp:
            lt = gui_app.LinkTracker(os.path.join(tmp, "manifest.json"))
            self.assertTrue(lt.get_all_clear())  # nothing tracked => all clear


if __name__ == "__main__":
    unittest.main()
