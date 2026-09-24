"""v0.03 screenshots — two-stage SYNC flow (SYNC → fetch → PROCESS)."""
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

def step1():
    # Realistic vault + model, light theme, idle SYNC state
    w.vault_combo.clear()
    w.vault_combo.addItem("G:/Docs/Github Projects Obsidian Vault/Github Projects(Automated)")
    w.vault_combo.setCurrentIndex(0)
    w.ollama_model.clear()
    w.ollama_model.addItem("qwen3.8b:7b-instruct-q4_K_M-latest")
    w.ollama_model.addItem("llama3.2:3b")
    w.ollama_model.setCurrentIndex(0)
    set_theme(False)
    w.log_message("🟢 GitCurator ready — press SYNC to fetch undone items from the bot.", "info")
    w.log_message("🔗 Proxy 127.0.0.1:10808 — Connected", "success")
    w.log_message("🧠 Ollama ready — qwen3.8b:7b", "success")
    shot_main("main-light")
    QTimer.singleShot(100, step2)

def step2():
    # v0.03 stage 1 → 2: SYNC clicked, fetch done → PROCESS (3)
    w.log_message("📬 Checking bot queue (@githubfetcherbot)...", "info")
    w.log_message("📚 Vault index: 142 notes indexed", "info")
    # simulate the exact post-fetch state _after_sync_fetch renders
    w._bot_queue_urls = [
        "https://github.com/vercel/next.js",
        "https://github.com/pydantic/pydantic",
        "https://github.com/fastapi/fastapi",
    ]
    w._after_sync_fetch("bot_check", {"success": True})
    w._sync_run_button()
    shot_main("main-process-light")
    QTimer.singleShot(100, step3)

def step3():
    # Same PROCESS stage in dark theme
    set_theme(True)
    shot_main("main-process-dark")
    QTimer.singleShot(100, step4)

def step4():
    # Restore idle + running state for the (unchanged) dark running shot
    w._set_hero_state("sync")
    w._bot_queue_urls = []
    w.progress_bar.setFormat("Ready")
    w.update_progress(10, 20)
    w.progress_bar.setValue(50)
    w.start_btn.setEnabled(False)
    w.stop_btn.setEnabled(True)
    w._sync_run_button()
    w.log_message("🔍 github.com/vercel/next.js — analyzing", "info")
    w.log_message("📝 Note written: vercel/next.js.md", "success")
    shot_main("main-running-dark")
    w.progress_bar.setValue(0)
    w._hide_progress_bar()
    w.start_btn.setEnabled(True)
    w.stop_btn.setEnabled(False)
    w._sync_run_button()
    # refresh the dark main shot too (wording changed in v0.03)
    w.log_text.clear()
    w.log_message("🟢 GitCurator ready — press SYNC to fetch undone items from the bot.", "info")
    w.log_message("🔗 Proxy 127.0.0.1:10808 — Connected", "success")
    shot_main("main-dark")
    print("DONE")
    app.quit()

QTimer.singleShot(400, step1)
app.exec()
