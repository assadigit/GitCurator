"""v33.1 screenshots — compact main view + fixed Vault/LLM settings pages."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(__file__))

OUT = "/home/z/my-project/public/screens"
os.makedirs(OUT, exist_ok=True)
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer

app = QApplication(sys.argv)

from gitcurator.gui.app import MainWindow

w = MainWindow()
w.show()

def set_theme(dark: bool):
    w._dark_mode = dark
    (w.apply_dark_theme if dark else w.apply_light_theme)()
    w._sync_theme_toggle_btn()
    w._refresh_button_styles()

def shot_main(name):
    w.repaint()
    w.grab().save(f"{OUT}/{name}.png")
    print(f"saved {name}")

def shot_dlg(name):
    d = w.settings_dialog
    d.repaint()
    d.grab().save(f"{OUT}/{name}.png")
    print(f"saved {name}")

def step1():
    # Simulate the user's long vault path so the shot is realistic
    w.vault_combo.clear()
    w.vault_combo.addItem("G:/Docs/Github Projects Obsidian Vault/Github Projects(Automated)")
    w.vault_combo.setCurrentIndex(0)
    # A few realistic models in the LLM combo
    w.ollama_model.clear()
    w.ollama_model.addItem("qwen3.8b:7b-instruct-q4_K_M-latest")
    w.ollama_model.addItem("llama3.2:3b")
    w.ollama_model.setCurrentIndex(0)
    set_theme(False)
    w.log_message("🟢 GitCurator ready — press SYNC to process the bot queue.", "info")
    w.log_message("🔗 Proxy 127.0.0.1:10808 — Connected", "success")
    w.log_message("🧠 Ollama ready — qwen3.8b:7b", "success")
    shot_main("main-light")
    QTimer.singleShot(100, step2)

def step2():
    set_theme(True)
    shot_main("main-dark")
    QTimer.singleShot(100, step3)

def step3():
    # Running state: STOP + 10/20 counter + progress
    w.update_progress(10, 20)
    w.progress_bar.setValue(50)
    w.start_btn.setEnabled(False)
    w.stop_btn.setEnabled(True)
    w._sync_run_button()
    w.log_message("🔍 github.com/vercel/next.js — analyzing", "info")
    w.log_message("📝 Note written: vercel/next.js.md", "success")
    shot_main("main-running-dark")
    # restore idle
    w.progress_bar.setValue(0)
    w._hide_progress_bar()
    w.start_btn.setEnabled(True)
    w.stop_btn.setEnabled(False)
    w._sync_run_button()
    QTimer.singleShot(100, step4)

def step4():
    # Settings — Vault page LIGHT (the fixed page: label → combo → buttons, no gap)
    set_theme(False)
    w._open_settings()
    d = w.settings_dialog
    d.nav.setCurrentRow(2)
    app.processEvents()
    shot_dlg("settings-vault-light")
    QTimer.singleShot(100, step5)

def step5():
    # Settings — LLM page LIGHT (Refresh + Start Server now inside the viewport)
    d = w.settings_dialog
    d.nav.setCurrentRow(3)
    app.processEvents()
    shot_dlg("settings-llm-light")
    QTimer.singleShot(100, step6)

def step6():
    # Settings — Credentials DARK (whole dialog re-theme check)
    set_theme(True)
    d = w.settings_dialog
    d.nav.setCurrentRow(0)
    app.processEvents()
    shot_dlg("settings-dark")
    QTimer.singleShot(100, step7)

def step7():
    # Settings — Input page DARK
    d = w.settings_dialog
    d.nav.setCurrentRow(4)
    app.processEvents()
    shot_dlg("settings-input-dark")
    d.close()
    print("DONE")
    app.quit()

QTimer.singleShot(400, step1)
app.exec()
