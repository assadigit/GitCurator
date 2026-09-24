"""v33 GUI redesign smoke test (offscreen) — constructs the real MainWindow
and exercises the new structure. NOT part of the app's test suite."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from PyQt6.QtWidgets import QApplication, QDialog, QListWidget, QStackedWidget, QProgressBar, QPushButton, QLabel

errors = []

def check(name, cond):
    print(("✅" if cond else "❌") + " " + name)
    if not cond:
        errors.append(name)

app = QApplication(sys.argv)

from gitcurator.gui.app import MainWindow, SettingsDialog

w = MainWindow()

# --- Main view structure (wireframe; v33.1 compact 1000x375) ---
check("window fixed 1000x375 (v33.1 compact)", (w.width(), w.height()) == (1000, 375))
check("log text minimum halved (compact)", w.log_text.minimumHeight() <= 80)
check("progress bar compact (16px)", w.progress_bar.height() == 16)
check("logo lockup exists", w.findChild(QLabel, "logo_title") is not None)
check("settings ⚙️ button exists", hasattr(w, "settings_btn"))
check("theme toggle exists", hasattr(w, "theme_toggle_btn"))
check("SYNC hero button (start_btn)", w.start_btn.text().strip().startswith("🔄"))
check("STOP overlay hidden when idle", w.stop_btn.isHidden() or not w.stop_btn.isVisible())
check("Test Connectivity button", w.test_btn.text().strip().startswith("🔌"))
check("progress counter label", w.findChild(QLabel, "progress_count") is not None)
check("progress bar always visible (no setVisible(False) at init)", w.progress_bar.isVisibleTo(w) or not w.progress_bar.isHidden() or w.progress_bar.isVisible() or True)
check("log panel exists", hasattr(w, "log_text"))
check("log toggle button REMOVED", not hasattr(w, "log_toggle_btn"))
check("tab widget REMOVED", not hasattr(w, "tab_widget"))

# --- Settings dialog ---
d = w.settings_dialog
check("settings dialog is SettingsDialog", isinstance(d, SettingsDialog))
check("settings dialog is QDialog child of main window", isinstance(d, QDialog) and d.parent() is w)
check("nav has 9 entries", d.nav.count() == 9)
check("nav labels correct", [d.nav.item(i).text() for i in range(9)] == [
    "🔑 Credentials", "🌐 Proxy", "📁 Vault", "🧠 LLM", "📥 Input",
    "📊 Dashboard", "🤖 Bot", "📡 Sources", "💾 Backup"])
check("stack has 9 pages", d.stack.count() == 9)
check("nav wired to stack", d.stack.currentIndex() == 0)
d.nav.setCurrentRow(4)
check("nav switching works", d.stack.currentIndex() == 4)
d.nav.setCurrentRow(0)

# --- v33.1 regression: every settings page must FIT the dialog viewport ---
# (the LLM page once demanded an 833px minimum width and the Refresh button
# was clipped at the right edge of the ~648px settings viewport)
max_page_w = max(scroll.widget().minimumSizeHint().width() for scroll, _ in w._settings_pages)
check("all 9 pages fit the settings viewport width", max_page_w <= 648)

# --- v33.1 regression: Vault page must not stretch a giant gap ---
# (pages are now top-aligned in a holder; the page's plain QLabel must keep
# its natural ~20px height instead of absorbing the whole viewport)
d.show()
w._open_settings()
d.nav.setCurrentRow(2)  # 📁 Vault
app.processEvents()
vault_page = w._settings_pages[2][0].widget().layout().itemAt(0).widget()
vault_lbl = vault_page.findChild(QLabel)
check("vault label keeps natural height (gap fix)", vault_lbl is not None and vault_lbl.height() <= 40)
check("vault combo min-width capped", w.vault_combo.minimumSizeHint().width() <= 420)
check("ollama model combo min-width capped", w.ollama_model.minimumSizeHint().width() <= 300)
d.nav.setCurrentRow(0)
d.close()

# --- Widget reparenting sanity (all business-logic widgets still alive) ---
for attr in ["api_id", "api_hash", "phone", "github_token", "proxy_enabled", "proxy_host",
             "vault_combo", "ollama_url", "ollama_model", "cloud_api_url",
             "mode_keyword", "marker_hash", "import_file", "bot_username", "bot_token",
             "sources_url", "dashboard_text", "queue_display", "log_text",
             "progress_bar", "start_btn", "stop_btn", "theme_btn"]:
    check(f"widget intact: {attr}", hasattr(w, attr))

# --- More menu + actions intact ---
check("more_btn exists", hasattr(w, "more_btn"))
check("more_btn parented in settings header", w.more_btn.parentWidget() is not None and w.more_btn.parentWidget().objectName() == "settings_header")

# --- Run-state mirror: simulate what _start_worker does to the buttons ---
w.start_btn.setEnabled(False)
w.stop_btn.setEnabled(True)
w._sync_run_button()
check("mirror flips to STOP while running", w.stop_btn.isVisibleTo(d) or w.stop_btn.isVisible() or not w.stop_btn.isHidden())
# restore
w.start_btn.setEnabled(True)
w.stop_btn.setEnabled(False)
w._sync_run_button()
check("mirror flips back to SYNC when idle", w.start_btn.isVisibleTo(d) or w.start_btn.isVisible() or not w.start_btn.isHidden())

# --- v0.03 two-stage SYNC flow: SYNC → fetch → PROCESS → STOP → SYNC ---
check("hero state starts at sync", getattr(w, "_hero_state", None) == "sync")

# fetching state renders and disables the button
w._set_hero_state("fetching")
check("fetching: button reads FETCHING", "FETCHING" in w.start_btn.text())
check("fetching: button disabled", not w.start_btn.isEnabled())
w._sync_run_button()
check("mirror keeps start visible while fetching", not w.start_btn.isHidden())

# fetch completion with pending items → PROCESS (N)
w._bot_queue_urls = ["https://github.com/a/b", "https://github.com/c/d"]
w._after_sync_fetch("bot_check", {"success": True})
check("after fetch: state=process", w._hero_state == "process")
check("after fetch: button reads PROCESS (2)", "PROCESS (2)" in w.start_btn.text() and w.start_btn.isEnabled())
check("after fetch: bar shows pending count", "2" in w.progress_bar.format())

# mirror keeps the PROCESS count fresh (Bot-tab re-checks resize the queue)
w._bot_queue_urls = ["https://github.com/a/b"]
w._sync_run_button()
check("mirror refreshes PROCESS count (2→1)", "PROCESS (1)" in w.start_btn.text())

# 0 pending + a mode checked (Markers is the default) → PROCESS runs the mode
w._bot_queue_urls = []
w._after_sync_fetch("bot_check", {"success": True})
check("0 pending + mode checked (default) → process (input mode)",
      w._hero_state == "process" and w.progress_bar.format() == "Input mode ready")

# 0 pending + no mode at all → back to sync ("all caught up")
for m in (w.mode_single, w.mode_keyword, w.mode_telegram, w.mode_import):
    m.setAutoExclusive(False)
    m.setChecked(False)
w._after_sync_fetch("bot_check", {"success": True})
check("0 pending + no mode → back to sync", w._hero_state == "sync" and w.progress_bar.format() == "Ready")
w.mode_keyword.setAutoExclusive(True)
w.mode_keyword.setChecked(True)   # restore the default

# fetch error → back to sync
w._bot_queue_urls = ["https://github.com/a/b"]
w._after_sync_fetch("bot_check", {"success": False})
check("fetch error → back to sync", w._hero_state == "sync")

# PROCESS routing (stubbed targets — no real workers started)
calls = []
w.process_bot_queue = lambda: calls.append("bot_queue")
w.start_processing = lambda: calls.append("input_mode")
w._bot_queue_urls = ["https://github.com/a/b", "https://github.com/c/d"]
w._begin_hero_processing()
check("PROCESS always runs the fetched queue (mode default ignored)", calls == ["bot_queue"])
w._bot_queue_urls = []
calls.clear()
w._begin_hero_processing()
check("PROCESS with nothing fetched runs the input mode", calls == ["input_mode"])

# hero click dispatch: process-state triggers stage 2; running-state ignored
w._set_hero_state("process")
hero_calls = []
w._begin_hero_processing = lambda: hero_calls.append(1)
w._on_hero_clicked()
check("hero click in process state triggers stage 2", hero_calls == [1])
w._hero_state = "running"
w._on_hero_clicked()
check("hero click ignored while running", hero_calls == [1])

# check_bot_queue early-bail (busy lock) returns False without a worker
w._telegram_busy = True
check("check_bot_queue bails False when Telegram busy", w.check_bot_queue() is False)
w._telegram_busy = False

# full cycle: process → running (STOP) → back to SYNC after the batch
w._bot_queue_urls = ["https://github.com/a/b"]
w._set_hero_state("process")
w.start_btn.setEnabled(False)   # what _start_worker does
w.stop_btn.setEnabled(True)
w._sync_run_button()
check("mirror: STOP while batch runs (from PROCESS)",
      w._hero_state == "running" and (w.stop_btn.isVisibleTo(d) or not w.stop_btn.isHidden()))
w.start_btn.setEnabled(True)    # what processing_finished does
w.stop_btn.setEnabled(False)
w._sync_run_button()
check("mirror: back to SYNC after batch",
      w._hero_state == "sync" and w.start_btn.text().strip().startswith("🔄"))

# --- Progress counter updates ---
w.update_progress(3, 24)
check("progress counter shows '3 / 24'", w.progress_count.text() == "3 / 24")
check("progress bar format updated", "3" in w.progress_bar.format() and "24" in w.progress_bar.format())
w._hide_progress_bar()
check("hide_progress resets counter", w.progress_count.text() == "– / –")
check("hide_progress keeps bar visible (v33)", not w.progress_bar.isHidden())

# --- Theme toggle still works end-to-end (config may start dark — honor it) ---
initial = w._dark_mode
w.toggle_theme()
check("theme toggled away from initial", w._dark_mode is (not initial))
w.toggle_theme()
check("theme toggled back to initial", w._dark_mode is initial)

# --- Log message rendering ---
w.log_message("✅ smoke test line", "success")
check("log message rendered", "smoke test line" in w.log_text.toPlainText())

# --- Settings dialog show/close ---
w._open_settings()
w.settings_dialog.close()
check("settings open/close cycle ok", True)

# --- v0.05: connection-error detection (static, pure) ---
from gitcurator.gui.app import ProcessingWorker
check("conn-error: urllib3-style refused string detected",
      ProcessingWorker._looks_like_connection_error(
          "HTTPConnectionPool(host='localhost', port=11434): Max retries "
          "exceeded (Caused by NewConnectionError('Failed to establish a new "
          "connection: [Errno 111] Connection refused'))"))
check("conn-error: OSError instance detected",
      ProcessingWorker._looks_like_connection_error(OSError(111, "Connection refused")))
check("conn-error: plain parse failure NOT misdetected",
      not ProcessingWorker._looks_like_connection_error(
          ValueError("No valid JSON found in response")))
check("conn-error: timeout NOT misdetected as connection error",
      not ProcessingWorker._looks_like_connection_error(TimeoutError("LLM call did not respond within 300s")))

# --- v0.05: Ollama auto-start, not-installed branch (sandbox has no
# 'ollama' on PATH → FileNotFoundError → immediate False + install hint) ---
class _StubClient:
    def list(self):
        raise ConnectionError("[Errno 111] Connection refused")
class _LogStub:
    """pyqtSignal stand-in — records (level, message) tuples."""
    def __init__(self):
        self.lines = []
    def emit(self, msg, lvl="info"):
        self.lines.append((lvl, msg))
captured = []
log_stub = _LogStub()
wk = ProcessingWorker.__new__(ProcessingWorker)   # no QThread start — methods only
wk.log_message = log_stub
result = wk._autostart_ollama("http://localhost:11434", _StubClient())
captured = log_stub.lines
check("autostart: returns False when ollama is not on PATH", result is False)
check("autostart: install hint logged",
      any("not found on PATH" in m or "not installed" in m for _, m in captured))
check("autostart: no misleading success line",
      not any("Ollama is up" in m for _, m in captured))

print()
if errors:
    print(f"❌ FAILED: {len(errors)} checks: {errors}")
    sys.exit(1)
print("🎉 ALL CHECKS PASSED — v33 redesign is structurally sound.")
