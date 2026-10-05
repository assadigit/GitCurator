#!/usr/bin/env python3
"""test_batchfinish.py — v0.39.0: the batch-finish fanfare.

The owner's ask: "play an audio for success and finishing of the batch
along with success modal". Three pieces, each covered here:

  1. gitcurator/gui/sound.py — BatchSound, the failure-proof
     QSoundEffect wrapper (missing module / missing WAV / volume
     clamping / one-time notice / effect reuse).
  2. ProcessingWorker._build_batch_summary — the structured scorecard
     dict the GUI renders (stopped flag, repos, websites, failures).
  3. ProcessingControlMixin.processing_finished routing + the modal:
     a naturally-finished batch gets chime + scorecard; stopped / no-work
     / crashed batches keep the plain box; the modal's rows, tooltips,
     tones and footnote render from a summary dict; the shutdown guard
     never opens a modal no one can dismiss.

Headless-safe: QT_QPA_PLATFORM=offscreen, no GUI shown (the modal test
auto-accepts through the nested event loop), no network, the real
config.json is never touched. QSoundEffect on an audio-less machine
accepts play() silently — asserted as a REQUEST (True), never as an
audible event.
"""

import os
import sys
import importlib
import types
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest import mock

from gitcurator.gui.sound import BatchSound
from gitcurator.gui.processing_worker import ProcessingWorker
from gitcurator.gui.main_window.processing_control import (
    ProcessingControlMixin,
)

try:
    from PyQt6.QtWidgets import (
        QApplication, QDialog, QLabel, QPushButton, QMainWindow,
    )
    _APP = QApplication.instance() or QApplication([])
    _PYQT = True
except Exception:  # pragma: no cover — CI installs PyQt6
    _PYQT = False

import gitcurator.gui.theme as _theme


# ---------------------------------------------------------------------------
# 1) BatchSound — the chime wrapper
# ---------------------------------------------------------------------------

class _FakeEffect:
    """Records what QSoundEffect would do — hermetic on machines with
    no audio stack at all (the CI runners: QtMultimedia cannot even be
    imported there, libpulse is absent)."""

    def __init__(self):
        self.source = None
        self.volume = None
        self.plays = 0

    def setSource(self, s):
        self.source = s

    def setVolume(self, v):
        self.volume = v

    def play(self):
        self.plays += 1


class _FakeQtMultimedia:
    """Stands in for the PyQt6.QtMultimedia module inside a BatchSound
    (seeded via ``bs._qt_multimedia = _FakeQtMultimedia()``) so the
    file/reuse/volume branches are testable WITHOUT any audio backend."""

    def __init__(self):
        self.effects = []

    def QSoundEffect(self):  # noqa: N802 — Qt's spelling
        e = _FakeEffect()
        self.effects.append(e)
        return e


def _seeded_sound():
    bs = BatchSound()
    bs._qt_multimedia = _FakeQtMultimedia()
    return bs


class TestBatchSound(unittest.TestCase):

    def _log_recorder(self):
        rec = []

        def _log(msg, level="info"):
            rec.append((level, msg))
        return _log, rec

    def test_missing_dir_returns_false_and_logs_once(self):
        log, rec = self._log_recorder()
        bs = BatchSound(sound_dir="/nonexistent-sounds")
        bs._qt_multimedia = _FakeQtMultimedia()  # hermetic module
        self.assertFalse(bs.play_success(volume=0.8, log=log))
        self.assertIn("chime missing", bs.unavailable_reason)
        self.assertEqual(len(rec), 1)          # exactly one notice
        # Second play: still False, NO second log line (one-time rule)
        self.assertFalse(bs.play_success(volume=0.8, log=log))
        self.assertEqual(len(rec), 1)

    def test_environment_contract(self):
        """The honest per-machine contract, asserted on BOTH machine
        classes: with a loadable QtMultimedia the shipped WAV plays
        (True, no warnings); without one (CI runners — no libpulse) it
        is a silent no-op with the one-time unavailable notice."""
        log, rec = self._log_recorder()
        bs = BatchSound()
        try:
            importlib.import_module("PyQt6.QtMultimedia")
            module_ok = True
        except Exception:
            module_ok = False
        ok = bs.play_success(volume=0.5, log=log)
        self.assertEqual(ok, module_ok)
        if module_ok:
            self.assertEqual(bs.unavailable_reason, "")
            self.assertEqual(rec, [])
        else:
            self.assertIn("Qt audio module unavailable",
                          bs.unavailable_reason)
            self.assertEqual(len(rec), 1)

    def test_effect_reused_across_plays(self):
        bs = _seeded_sound()
        self.assertTrue(bs.play_success())
        first = bs._effect
        self.assertTrue(bs.play_success())
        self.assertIs(bs._effect, first)       # cached, never rebuilt
        self.assertEqual(first.plays, 2)       # replayed, not replaced
        self.assertIsNotNone(first.source)     # the WAV was handed over

    def test_volume_clamped_into_qt_range(self):
        bs = _seeded_sound()
        bs.play_success(volume=5.0)            # user garbage → 1.0
        self.assertAlmostEqual(bs._effect.volume, 1.0)
        bs2 = _seeded_sound()
        bs2.play_success(volume="loud")        # non-numeric → default
        self.assertAlmostEqual(bs2._effect.volume, 0.8)

    def test_qtmultimedia_unavailable(self):
        # A minimal environment without the Qt audio libs: the lazy
        # import fails → silent no-op + the ONE-TIME notice.
        log, rec = self._log_recorder()
        bs = BatchSound()
        with mock.patch.dict(sys.modules, {"PyQt6.QtMultimedia": None}):
            self.assertFalse(bs.play_success(volume=0.8, log=log))
        self.assertIn("Qt audio module unavailable", bs.unavailable_reason)
        self.assertEqual(len(rec), 1)
        self.assertIn("🔕", rec[0][1])          # the friendly glyph line
        # The failure is remembered — later calls are instant no-ops
        self.assertFalse(bs.play_success(volume=0.8, log=log))
        self.assertEqual(len(rec), 1)


# ---------------------------------------------------------------------------
# 2) The worker's structured scorecard (headless __new__ harness)
# ---------------------------------------------------------------------------

def _harness_worker(**attrs):
    """The Task-42 headless shape: __new__-built worker, attributes
    pre-set by the test (never __init__ — no signal machinery)."""
    w = ProcessingWorker.__new__(ProcessingWorker)
    w.config = {}
    w.log_message = _NullLog()
    w.link_tracker = None
    w.is_running = True
    w._website_summary = None
    w.processed = 0
    w.total = 0
    for k, v in attrs.items():
        setattr(w, k, v)
    return w


class _NullLog:
    def emit(self, *a, **k):
        pass


class _StubTracker:
    def __init__(self, statuses):
        self.manifest = {"links": [
            {"url": f"https://x/{i}", "status": s}
            for i, s in enumerate(statuses)]}


class TestBuildBatchSummary(unittest.TestCase):

    def test_clean_github_batch(self):
        w = _harness_worker(processed=18, total=20)
        s = w._build_batch_summary(new_notes=18,
                                   report_path="/v/_processing_report_x.md",
                                   summary_path="/v/_summary.txt")
        self.assertFalse(s['stopped'])
        self.assertEqual(s['github_processed'], 18)
        self.assertEqual(s['github_total'], 20)
        self.assertEqual(s['new_notes'], 18)
        self.assertIsNone(s['websites'])
        self.assertEqual(s['failed_links'], 0)
        self.assertEqual(s['report_path'], "/v/_processing_report_x.md")
        self.assertEqual(s['summary_path'], "/v/_summary.txt")

    def test_stopped_batch_flagged(self):
        w = _harness_worker(is_running=False, processed=3, total=620)
        s = w._build_batch_summary()
        self.assertTrue(s['stopped'])   # the GUI withholds the fanfare

    def test_websites_counters(self):
        w = _harness_worker(
            processed=0, total=0,
            _website_summary={'counters': {'processed': 7, 'review': 2,
                                           'skipped': 1, 'failed': 0,
                                           'retried': 0, 'upgraded': 0},
                              'results': [], 'vault': '/wv'})
        s = w._build_batch_summary()
        self.assertEqual(s['websites'], {'processed': 7, 'review': 2,
                                         'skipped': 1, 'failed': 0})

    def test_failed_links_counted_from_manifest(self):
        w = _harness_worker(
            link_tracker=_StubTracker(
                ["processed", "processed", "failed", "skipped", "failed"]))
        s = w._build_batch_summary()
        self.assertEqual(s['failed_links'], 2)

    def test_no_tracker_means_zero_failed(self):
        w = _harness_worker(link_tracker=None)
        self.assertEqual(w._build_batch_summary()['failed_links'], 0)

    def test_broken_manifest_is_zero_failed(self):
        class _Broken:
            manifest = None  # .get on None → AttributeError → swallowed
        w = _harness_worker(link_tracker=_Broken())
        s = w._build_batch_summary()
        self.assertEqual(s['failed_links'], 0)


# ---------------------------------------------------------------------------
# 3) processing_finished routing + the sound gate (bare-mixin stubs)
# ---------------------------------------------------------------------------

def _routing_window(worker, config=None):
    """A bare ProcessingControlMixin with every collaborator stubbed —
    the TestMixinFlow pattern: only the ROUTING is under test."""
    win = ProcessingControlMixin()
    win.calls = []
    win.worker = worker
    win.config = dict(config or {})
    win.start_btn = types.SimpleNamespace(setEnabled=lambda b: None)
    win._batch_running = True
    win.progress_bar = types.SimpleNamespace(setFormat=lambda f: None)
    win._bot_queue_urls = []
    win._pending_last_processed_update = 0
    win.log_message = lambda msg, level="info": win.calls.append(
        ("log", level, msg))
    win._set_hero_state = lambda s: win.calls.append(("hero", s))
    win._refresh_pipeline_counter = lambda: None
    win._release_telegram_lock = lambda src: None
    win._set_pipeline_state = lambda s: win.calls.append(("state", s))
    win._schedule_progress_hide = lambda delay_ms=2500: None
    win._show_custom_message_box = lambda title, message, success=True: \
        win.calls.append(("box", title, success))
    win._celebrate_batch = lambda summary, elapsed="": win.calls.append(
        ("celebrate", summary, elapsed))
    return win


_SUMMARY = {
    'stopped': False, 'github_processed': 18, 'github_total': 20,
    'new_notes': 18, 'websites': None, 'failed_links': 0,
    'report_path': '/v/_processing_report_x.md', 'summary_path': '',
}


class TestFanfareRouting(unittest.TestCase):

    def test_success_with_summary_celebrates(self):
        win = _routing_window(types.SimpleNamespace(
            batch_summary=dict(_SUMMARY), link_tracker=None,
            _bot_source=False))
        win.processing_finished(True, "Processed 18 out of 20 repos.")
        celebrates = [c for c in win.calls if c[0] == "celebrate"]
        self.assertEqual(len(celebrates), 1)
        self.assertEqual(celebrates[0][1]['github_processed'], 18)
        # the plain box is NOT shown for a naturally-finished batch
        self.assertFalse([c for c in win.calls if c[0] == "box"])

    def test_stopped_batch_keeps_plain_box(self):
        _s = dict(_SUMMARY, stopped=True)
        win = _routing_window(types.SimpleNamespace(
            batch_summary=_s, link_tracker=None, _bot_source=False))
        win.processing_finished(True, "Processed 3 out of 620 repos.")
        self.assertFalse([c for c in win.calls if c[0] == "celebrate"])
        boxes = [c for c in win.calls if c[0] == "box"]
        self.assertEqual(boxes, [("box", "Processing Complete", True)])

    def test_no_summary_keeps_plain_box(self):
        # The old-shape worker (no batch_summary attribute) and the
        # no-work early returns — plain box, no fanfare.
        win = _routing_window(types.SimpleNamespace(
            link_tracker=None, _bot_source=False))
        win.processing_finished(True, "No URLs found.")
        self.assertFalse([c for c in win.calls if c[0] == "celebrate"])
        self.assertEqual([c for c in win.calls if c[0] == "box"],
                         [("box", "Processing Complete", True)])

    def test_worker_none_keeps_plain_box(self):
        win = _routing_window(None)
        win.processing_finished(True, "No URLs found.")
        self.assertFalse([c for c in win.calls if c[0] == "celebrate"])
        self.assertEqual(len([c for c in win.calls if c[0] == "box"]), 1)

    def test_failed_batch_keeps_error_box(self):
        win = _routing_window(types.SimpleNamespace(
            batch_summary=dict(_SUMMARY), link_tracker=None,
            _bot_source=False))
        win.processing_finished(False, "Batch crashed: boom")
        self.assertFalse([c for c in win.calls if c[0] == "celebrate"])
        self.assertEqual([c for c in win.calls if c[0] == "box"],
                         [("box", "Processing Error", False)])

    def test_sound_disabled_short_circuits(self):
        win = _routing_window(None, config={'sound_enabled': False})
        self.assertFalse(win._play_batch_sound())
        self.assertFalse(hasattr(win, '_batch_sound'))  # never built

    def test_sound_enabled_plays_real_asset(self):
        win = _routing_window(None, config={})
        win._batch_sound = _seeded_sound()   # hermetic (CI has no audio)
        self.assertTrue(win._play_batch_sound())        # default: ON
        self.assertEqual(win._batch_sound._effect.plays, 1)

    def test_sound_volume_fallback_on_garbage(self):
        win = _routing_window(None, config={'sound_volume': 'loud'})
        win._batch_sound = _seeded_sound()
        self.assertTrue(win._play_batch_sound())
        self.assertAlmostEqual(win._batch_sound._effect.volume, 0.8)


# ---------------------------------------------------------------------------
# 4) The modal itself — a real QWidget host, built (never exec'd)
# ---------------------------------------------------------------------------

@unittest.skipUnless(_PYQT, "PyQt6 not installed — GUI checks skipped")
class TestSuccessModalContent(unittest.TestCase):

    def _host(self):
        """A minimal REAL QWidget host carrying the mixin — the modal's
        parent and widget machinery must be genuine Qt."""
        host = type("_ModalHost", (QMainWindow, ProcessingControlMixin), {})()
        host.config = {}
        host._closing = False
        host.calls = []
        host.log_message = lambda msg, level="info": host.calls.append(
            ("log", level, msg))
        host._style_btn = lambda btn, kind: btn   # variants not under test
        host._animate_dialog = lambda d: None
        host.show()                                 # isVisible() must pass
        return host

    def _modal(self, host, summary, elapsed=" in 1m 4s"):
        """Build the REAL modal via _build_batch_success_dialog — the
        build/exec split exists exactly so tests never drive a nested
        event loop (and never touch QDialog.exec, which earlier suites
        monkeypatch)."""
        return host._build_batch_success_dialog(summary, elapsed)

    @staticmethod
    def _texts(dialog):
        return [l.text() for l in dialog.findChildren(QLabel)]

    def test_modal_rows_full_summary(self):
        host = self._host()
        dlg = self._modal(host, dict(
            _SUMMARY,
            report_path='/home/o/Vault/_processing_report_20260210_0304.md'))
        self.assertIsNotNone(dlg)
        texts = self._texts(dlg)
        self.assertIn(" Batch Complete!", texts)
        self.assertIn("Repos curated", texts)
        self.assertIn("18 of 20", texts)
        self.assertIn("Status", texts)
        self.assertIn("✓ All links processed cleanly", texts)
        self.assertIn("Elapsed", texts)
        self.assertIn("1m 4s", texts)              # " in " stripped
        self.assertIn("Final report", texts)
        self.assertIn("_processing_report_20260210_0304.md", texts)
        # the full path rides the tooltip (the v0.37 one-line rule)
        report_vals = [l for l in dlg.findChildren(QLabel)
                       if l.text() == "_processing_report_20260210_0304.md"]
        self.assertEqual(report_vals[0].toolTip(),
                         '/home/o/Vault/_processing_report_20260210_0304.md')
        # footnote: the undo affordance (18 new notes)
        self.assertTrue(any("18 new note(s)" in t and "Undo Last Batch" in t
                            for t in texts))
        # OK button, primary-styled, default
        btns = dlg.findChildren(QPushButton)
        self.assertEqual([b.text() for b in btns], ["OK"])
        self.assertTrue(btns[0].isDefault())

    def test_modal_warning_row_when_failures(self):
        host = self._host()
        dlg = self._modal(host, dict(_SUMMARY, failed_links=2,
                                     github_processed=18))
        texts = self._texts(dlg)
        self.assertIn("Needs retry", texts)
        self.assertIn("2 link(s) — next run", texts)
        self.assertNotIn("✓ All links processed cleanly", texts)
        warns = [l for l in dlg.findChildren(QLabel)
                 if l.text() == "2 link(s) — next run"]
        self.assertEqual(warns[0].property("tone"), "warning")

    def test_modal_websites_only_batch(self):
        host = self._host()
        dlg = self._modal(host, {
            'stopped': False, 'github_processed': 0, 'github_total': 0,
            'new_notes': 7,
            'websites': {'processed': 7, 'review': 2, 'skipped': 1,
                         'failed': 0},
            'failed_links': 0, 'report_path': '', 'summary_path': ''})
        texts = self._texts(dlg)
        # no repos row for a websites-only batch…
        self.assertNotIn("Repos curated", texts)
        # …but the websites tally, all-clear and footnote are there
        self.assertIn("Websites", texts)
        self.assertIn("7 saved · 2 to review · 1 skipped", texts)
        self.assertIn("✓ All links processed cleanly", texts)
        self.assertTrue(any("7 new note(s)" in t for t in texts))
        # no report row when the report path is empty
        self.assertNotIn("Final report", texts)

    def test_modal_shutdown_guard(self):
        # A window that is closing / not visible must NEVER open a modal
        # no one can dismiss (the v0.06 zombie rule).
        host = self._host()
        host.close()                               # isVisible() → False
        host._show_batch_success_modal(dict(_SUMMARY))
        self.assertTrue(any("modal skipped" in m for _, _, m in host.calls))


# ---------------------------------------------------------------------------
# 5) The QSS tone variants — both palettes
# ---------------------------------------------------------------------------

class TestQssTones(unittest.TestCase):

    def test_tone_rules_in_both_palettes(self):
        for t in (_theme.LIGHT, _theme.DARK):
            qss = _theme.build_qss(t)
            self.assertIn('QLabel#cc_row_name[tone="success"]', qss)
            self.assertIn('QLabel#cc_row_name[tone="warning"]', qss)
            # zero new color values — the rules reuse the message-box
            # tone tokens verbatim
            self.assertIn(t['msg_success'], qss)
            self.assertIn(t['msg_warning'], qss)


if __name__ == "__main__":
    unittest.main(verbosity=2)
