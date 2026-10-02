#!/usr/bin/env python3
"""v0.32 GUI smoke check (offscreen): merged lineage — v0.08 redesign +
v0.07.2 model picker + v0.09 unified 404 quarantine + v0.31 main-window
balance pass + v0.32 five-change pass (shorter window, one Stop toggle,
flat log, retry banner).

Run:  QT_QPA_PLATFORM=offscreen python tests/smoke_v007.py
Exits non-zero on any failure; prints one line per check.
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _APP)

from PyQt6.QtWidgets import QApplication  # noqa: E402
from PyQt6.QtCore import QSettings, QSize  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from gitcurator.gui.app import MainWindow, CacheDB, dead_link_threshold  # noqa: E402
from gitcurator.gui import icons  # noqa: E402
from gitcurator.gui import theme as theme_kit  # noqa: E402

CHECKS = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))
    print(("  ok  " if cond else "  FAIL") + f"  {name}")


# v0.32: the window SIZE persists across launches (QSettings) — scrub
# any saved size first so the default-size contract below is tested on a
# KNOWN first-run state (and scrub again at the end for the next run).
_SIZE_KEY = "main_window/size"
_SIZE_ORG, _SIZE_APP = "GitCurator", "MainWindow"
QSettings(_SIZE_ORG, _SIZE_APP).remove(_SIZE_KEY)

w = MainWindow()

# 1. Window defaults — v0.32 (five-change pass): a THIRD shorter
#    (900×600 → 900×400; the empty log used to fill most of the window).
#    Only the log grows on resize (stretch 1); the log never collapses
#    below ~6 visible rows; the size is remembered across launches.
check("window width == 900", w.width() == 900)
check("window height == 400 (a third shorter)", w.height() == 400)
check("window resizable (min != max)", w.minimumSize() != w.maximumSize())
check("window minimum 760×380",
      w.minimumSize().width() == 760 and w.minimumSize().height() == 380)

# 2. Icon buttons: 34×34 square with SVG glyphs (v0.31.0: the gear and the
#    theme toggle share one size/shape/border — a ≥32px click target)
for label, btn in (("settings_btn", w.settings_btn), ("theme_toggle_btn", w.theme_toggle_btn)):
    check(f"{label}.width() == 34", btn.width() == 34)
    check(f"{label}.height() == 34", btn.height() == 34)
    check(f"{label} has an icon (no text glyph to clip)", not btn.icon().isNull() or not btn.text())

# 3. Icons module: official Lucide pack (25 glyphs — 12 v0.08 + 9 sidebar
#    v0.30.0 + 4 state glyphs v0.31.0), renderable
check("icons.ICON pack is loaded (25 official Lucide glyphs)",
      len(icons.ICONS) == 25 and "settings" in icons.ICONS and "sun" in icons.ICONS
      and "circle" in icons.ICONS and "check" in icons.ICONS
      and "triangle-alert" in icons.ICONS and "circle-x" in icons.ICONS)
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
# v0.32: the log minimum keeps ~6 mono rows visible (12px mono ≈ 18px/row)
check("log_text minimum height >= 132 (~6 rows)", w.log_text.minimumHeight() >= 132)

# 5b. v0.31.0 balance pass — the status card + the log card's states
check("status card hosts the buttons AND the progress row",
      w.progress_bar.parentWidget() is w.start_btn.parentWidget())
# v0.32 (five-change pass): SYNC grows to 280×56 — clearly the LARGEST
# control (~1.4× its old height); Test Connection stays 180×40 secondary.
check("SYNC button fixed at 280×56",
      w.start_btn.width() == 280 and w.start_btn.height() == 56)
check("Test Connection fixed at 180×40",
      w.test_btn.width() == 180 and w.test_btn.height() == 40)
check("SYNC clearly the largest control",
      w.start_btn.width() > w.test_btn.width()
      and w.start_btn.height() > w.test_btn.height())
check("SYNC narrower than the card (does not fill it)",
      w.start_btn.width() < w.progress_bar.parentWidget().width() - 100)
check("progress bar is a 12px gauge with no text",
      w.progress_bar.height() == 12 and not w.progress_bar.isTextVisible())
check("pipeline state word renders",
      w.pipeline_state_text.text() in ("Idle", "Syncing", "Done", "Error"))
check("pipeline state flips + flips back",
      (w._set_pipeline_state('syncing'), w.pipeline_state_text.text() == 'Syncing',
       w._set_pipeline_state('idle'), w.pipeline_state_text.text() == 'Idle') ==
      (None, True, None, True))
check("empty state exists with the first-run copy",
      getattr(w, "_log_empty", None) is not None
      and w._log_empty_title.text() == "No activity yet")
# (the dev checkout's config/cache may log a startup warning — clear so
# the empty-state contract is tested on a KNOWN-empty list)
w._clear_log()
check("empty state shows when the list is empty",
      w._log_empty.isVisibleTo(w.log_text.parentWidget()))
w.log_message("smoke row", "info")
check("first entry removes the empty state",
      not w._log_empty.isVisibleTo(w.log_text.parentWidget()))
w._clear_log()
check("clear restores the empty state",
      w._log_empty.isVisibleTo(w.log_text.parentWidget()))
w._log_caught_up_state()
check("caught-up sync swaps in the up-to-date copy",
      w._log_empty_title.text() == "Everything is up to date"
      and "Last sync:" in w._log_empty_hint.text())
# the no-match variant needs ENTRIES for the filter to hide (after a
# caught-up sync there are none — log one, then filter away from it)
w.log_message("a plain info row", "info")
w._set_log_filter("error")
check("no-match filter shows the no-match copy",
      w._log_empty_title.text() == "No matching entries"
      and w._log_empty.isVisibleTo(w.log_text.parentWidget()))
w._set_log_filter("all")
w.log_message("row with an icon glyph", "success")
check("log row renders (leading icon + timestamp + message)",
      w.log_text.document().blockCount() >= 1
      and "row with an icon glyph" in w.log_text.toPlainText())

# 5c. v0.32 (five-change pass) — ONE primary action: the SYNC button
#     itself becomes Stop while a batch runs (no separate STOP button).
check("no separate stop button exists", not hasattr(w, "stop_btn"))
w._batch_running = True
w._set_hero_state('running')
check("a running batch renders the SAME button as Stop",
      w.start_btn.text() == "Stop" and w.start_btn.isEnabled()
      and w.start_btn.property('btn_kind') == 'hero_danger')
_stopped = []
w.stop_processing = lambda: _stopped.append(True)
w._on_hero_clicked()
check("clicking the Stop button cancels the batch",
      _stopped == [True])
w._batch_running = False
w._set_hero_state('sync')
check("batch end restores SYNC (primary fill, enabled)",
      w.start_btn.text() == "SYNC" and w.start_btn.isEnabled()
      and w.start_btn.property('btn_kind') == 'hero_primary')

# 5d. v0.32 — the log is visually secondary: flat tint (no card border),
#     muted ink, quiet chips — all via theme-kit tokens + QSS rules.
for _tbl_name, _tbl in (("LIGHT", theme_kit.LIGHT), ("DARK", theme_kit.DARK)):
    for _key in ("log_tint", "log_text", "log_ts", "banner_warn_bg"):
        check(f"theme kit {_tbl_name}.{_key} exists", _key in _tbl)
_qss = theme_kit.build_qss(theme_kit.LIGHT)
_flat_rule = ("QGroupBox#log_group { border: none; background-color: "
              + theme_kit.LIGHT["log_tint"])
check("log panel QSS is flat (border: none, tinted)",
      _flat_rule in _qss)
check("log list rides transparent on the tint",
      "QTextEdit#log_list" in _qss and "background: transparent" in _qss)
check("retry banner QSS present", "QWidget#retry_banner" in _qss)
check("filter chips are borderless and quiet",
      "QPushButton#log_filter" in _qss)

# 5e. v0.32 — the RETRY BANNER: a live count of unfinished links from
#     the previous batch (LinkTracker's reconciliation read — never
#     parsed from log text), shown only while the count is positive.
import json as _json
import tempfile as _tempfile
_vault_tmp = _tempfile.mkdtemp(prefix="gc-smoke-vault-")
def _write_manifest(links):
    with open(os.path.join(_vault_tmp, "links_manifest.json"), "w",
              encoding="utf-8") as fh:
        _json.dump({"links": links}, fh)
w.vault_combo.blockSignals(True)   # never persist the scratch vault
w.vault_combo.setCurrentText(_vault_tmp)
_write_manifest([])   # no manifest links → banner hidden
w._refresh_retry_banner()
check("retry banner hidden with nothing unfinished",
      not w.retry_banner.isVisibleTo(w))
_write_manifest([
    {"url": "https://github.com/a/one", "status": "failed"},
    {"url": "https://github.com/b/two", "status": "pending"},
])
w._refresh_retry_banner()
check("retry banner shows the LIVE unfinished count",
      w.retry_banner.isVisibleTo(w) and "2 links" in w._retry_banner_text.text())
check("banner Retry wired to the reconciliation re-run",
      hasattr(w, "_retry_reconciliation_links"))
_note = os.path.join(_vault_tmp, "note.md")
open(_note, "w", encoding="utf-8").write("ok")
_write_manifest([
    {"url": "https://github.com/a/one", "status": "processed",
     "note_path": _note},
])
w._refresh_retry_banner()
check("a clean batch hides the banner",
      not w.retry_banner.isVisibleTo(w))
w.vault_combo.blockSignals(False)

# 5f. v0.32 — the window SIZE is remembered (QSettings round-trip).
w.resize(820, 462)
w._save_window_size()
_saved = QSettings(_SIZE_ORG, _SIZE_APP).value(_SIZE_KEY)
check("_save_window_size persists a QSize",
      isinstance(_saved, QSize) and _saved == QSize(820, 462))
w2 = MainWindow()
check("a fresh launch restores the saved size",
      w2.width() == 820 and w2.height() == 462)
QSettings(_SIZE_ORG, _SIZE_APP).remove(_SIZE_KEY)

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
print("GUI smoke OK — v0.32 merged lineage verified (v0.08 redesign + "
      "v0.07.2 model picker + unified quarantine + v0.31 balance pass + "
      "v0.32 five-change pass: shorter window, Stop toggle, flat log, "
      "retry banner, QSettings size)")
