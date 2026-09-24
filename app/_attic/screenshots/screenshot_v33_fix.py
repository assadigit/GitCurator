"""Re-shoot settings screenshots with correct theme order."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(__file__))

OUT = "/home/z/my-project/public/screens"
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)

from gitcurator.gui.app import MainWindow

w = MainWindow()

def set_theme(dark: bool):
    w._dark_mode = dark
    (w.apply_dark_theme if dark else w.apply_light_theme)()
    w._sync_theme_toggle_btn()
    w._refresh_button_styles()

# LIGHT settings
set_theme(False)
w._open_settings()
d = w.settings_dialog
d.repaint()
d.grab().save(f"{OUT}/settings-light.png")
print("saved settings-light (light)")

# DARK settings — Credentials
set_theme(True)
d.repaint()
d.grab().save(f"{OUT}/settings-dark.png")
print("saved settings-dark (dark)")

# DARK settings — Input page
d.nav.setCurrentRow(4)
d.repaint()
d.grab().save(f"{OUT}/settings-input-dark.png")
print("saved settings-input-dark")

print("DONE")
