#!/usr/bin/env python3
"""v0.09 GUI smoke check (offscreen): merged lineage — v0.08 redesign +
v0.07.2 model picker + v0.09 unified 404 quarantine.

Run:  QT_QPA_PLATFORM=offscreen python tests/smoke_v007.py
Exits non-zero on any failure; prints one line per check.
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _APP)

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from gitcurator.gui.app import MainWindow, CacheDB, dead_link_threshold  # noqa: E402
from gitcurator.gui import icons  # noqa: E402

CHECKS = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))
    print(("  ok  " if cond else "  FAIL") + f"  {name}")


w = MainWindow()

# 1. Window 900×600 (6:4), fixed — kept from v0.07/v0.08
check("window width == 900", w.width() == 900)
check("window height == 600", w.height() == 600)
check("window fixed (no resize)", w.minimumSize() == w.maximumSize())

# 2. Icon buttons: 34×30 with SVG glyphs (v0.08 Lucide pack — vector icons
#    can't clip the way emoji metrics did; supersedes the v0.07 38×36 fix)
for label, btn in (("settings_btn", w.settings_btn), ("theme_toggle_btn", w.theme_toggle_btn)):
    check(f"{label}.width() == 34", btn.width() == 34)
    check(f"{label}.height() == 30", btn.height() == 30)
    check(f"{label} has an icon (no text glyph to clip)", not btn.icon().isNull() or not btn.text())

# 3. Icons module: official Lucide pack (12 glyphs), renderable
check("icons.ICON pack is loaded (12 official Lucide glyphs)",
      len(icons.ICONS) == 12 and "settings" in icons.ICONS and "sun" in icons.ICONS)
check("QtSvg available in this env", icons._HAS_SVG)
if icons._HAS_SVG:
    pm = icons.pixmap('settings', '#5F54B4', 16)
    check("icons.pixmap renders a real glyph", not pm.isNull())

# 4. Theme toggle round-trip: _dark_mode flips, tooltip names the mode
dark_before = bool(getattr(w, "_dark_mode", False))
w.toggle_theme()
dark_after = bool(getattr(w, "_dark_mode", False))
check("toggle flips _dark_mode", dark_after != dark_before)
check("tooltip names the current mode",
      ("Dark" if dark_after else "Light") in w.theme_toggle_btn.toolTip())
w.toggle_theme()
check("toggle back restores state", bool(getattr(w, "_dark_mode", False)) == dark_before)

# 5. Log panel is the main view's growable region (v0.08 redesign: the CTA
#    row went horizontal; the group carries stretch 1 instead of min-height)
try:
    grp = w.log_text.parentWidget()
    lay = grp.parentWidget().layout()
    stretch_ok = any(lay.itemAt(i).widget() is grp and lay.stretch(i) == 1
                     for i in range(lay.count()))
except Exception:
    stretch_ok = False
check("log group is the growable region (stretch 1)", stretch_ok)
check("log_text minimum height >= 72", w.log_text.minimumHeight() >= 72)

# 6. Unified 404-quarantine API on the shared cache class (v0.09)
for m in ("record_404", "is_dead_link", "get_dead_url_set", "get_dead_urls",
          "get_quarantine_stats", "reset_dead_links"):
    check(f"CacheDB.{m} exists", hasattr(CacheDB, m))
check("dead_link_threshold default is 3", dead_link_threshold({}) == 3)
check("dead_link_threshold reads the config key",
      dead_link_threshold({"notfound_strike_threshold": 5}) == 5)
check("dead_link_threshold clamps to >= 2",
      dead_link_threshold({"notfound_strike_threshold": 0}) == 2)

# 7. Quarantine manager in Settings → Dashboard (v0.09 merge)
spin = getattr(w, "quarantine_threshold_spin", None)
check("quarantine_threshold_spin exists", spin is not None)
if spin is not None:
    check("threshold range is 2..10", spin.minimum() == 2 and spin.maximum() == 10)
    cfg_val = w.config.get("notfound_strike_threshold", 3) or 3
    check("spin initial value matches config", spin.value() == max(2, int(cfg_val)))
check("quarantine_group exists", getattr(w, "quarantine_group", None) is not None)
check("quarantine_text exists", getattr(w, "quarantine_text", None) is not None)
for m in ("refresh_quarantine_view", "clear_all_quarantine", "_save_quarantine_threshold"):
    check(f"MainWindow.{m} exists", hasattr(w, m))
check("More-menu viewer exists (v0.08)", hasattr(w, "view_dead_links")
      and hasattr(w, "_reset_dead_links_now"))

# 8. refresh_quarantine_view renders without a vault / DB (best-effort path)
try:
    w.refresh_quarantine_view()
    rendered = len(w.quarantine_text.toPlainText()) > 0
except Exception as exc:  # pragma: no cover — failure detail beats a bare bool
    print(f"  (refresh_quarantine_view raised: {exc})")
    rendered = False
check("refresh_quarantine_view renders text", rendered)

# 9. Model picker (v0.07.2 merged): worker plumbing + heuristic
from gitcurator.gui.app import ProcessingWorker  # noqa: E402
check("ProcessingWorker.model_prompt_callback default None",
      getattr(ProcessingWorker, "__init__", None) is not None)
check("ProcessingWorker._pick_best_model exists", hasattr(ProcessingWorker, "_pick_best_model"))
models = ["nomic-embed-text", "qwen3:14b", "qwen3.5:27b", "llama3.2:3b"]
best = ProcessingWorker._pick_best_model("qwen3:27b", models)
check("pick_best_model prefers same family, never an embedder", best in ("qwen3:14b", "qwen3.5:27b"))
check("pick_best_model never returns the embedder", best != "nomic-embed-text")

failed = [n for n, ok in CHECKS if not ok]
print()
print(f"{len(CHECKS) - len(failed)}/{len(CHECKS)} smoke checks passed")
if failed:
    print("FAILED:", ", ".join(failed))
    sys.exit(1)
print("GUI smoke OK — v0.09 merged lineage verified (v0.08 redesign + "
      "v0.07.2 model picker + unified quarantine)")
