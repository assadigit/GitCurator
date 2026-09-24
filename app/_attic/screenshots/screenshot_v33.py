"""v33 redesign — capture REAL screenshots of the redesigned GUI (offscreen
QWidget.grab → PNG). Produces: main view (light+dark), running state,
settings window (light+dark)."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(__file__))

OUT = "/home/z/my-project/public/screens"
os.makedirs(OUT, exist_ok=True)

from PyQt6.QtWidgets import QApplication

app = QApplication(sys.argv)

from gitcurator.gui.app import MainWindow

w = MainWindow()

def log_few():
    w.log_message("📬 Auto-checking bot queue on startup...", "info")
    w.log_message("✅ Proxy 127.0.0.1:10808 reachable", "success")
    w.log_message("📬 24 pending repos found in queue", "info")
    w.log_message("⚠️ 3 repos failed in previous runs — use Retry Failed", "warning")

def set_theme(dark: bool):
    w._dark_mode = dark
    (w.apply_dark_theme if dark else w.apply_light_theme)()
    w._sync_theme_toggle_btn()
    w._refresh_button_styles()

def shot(name):
    w.repaint()
    w.grab().save(f"{OUT}/{name}.png")
    print("saved", name)

# --- 1. Main view, light ---
set_theme(False)
log_few()
shot("main-light")

# --- 2. Main view, dark ---
set_theme(True)
shot("main-dark")

# --- 3. Running state (SYNC → STOP, progress 10/20, logs streaming) ---
w.start_btn.setEnabled(False)
w.stop_btn.setEnabled(True)
w._sync_run_button()
w._processing_start_time = __import__("datetime").datetime.now()
w.update_progress(10, 20)
w.update_status("owner/repo")
w.log_message("🔍 github.com/zai-org/zai-code — analyzing", "info")
w.log_message("🧠 Ollama: categorizing…", "info")
w.log_message("📝 Note written: zai-org/zai-code.md", "success")
w.log_message("❌ 401 Bad credentials — token dropped for this batch", "error")
shot("main-running-dark")

# restore idle
w.start_btn.setEnabled(True)
w.stop_btn.setEnabled(False)
w._sync_run_button()
w._hide_progress_bar()

# --- 4/5. Settings window (light + dark), on top of dimmed-free main view ---
w._open_settings()
d = w.settings_dialog
d.repaint()
d.grab().save(f"{OUT}/settings-light.png")
print("saved settings-light")

set_theme(True)
d.repaint()
d.grab().save(f"{OUT}/settings-dark.png")
print("saved settings-dark")

d.nav.setCurrentRow(4)  # 📥 Input page
d.repaint()
d.grab().save(f"{OUT}/settings-input-dark.png")
print("saved settings-input-dark")

print("DONE")
