#!/usr/bin/env python3
"""test_soundsettings.py — v0.40.0: the Sound settings page + the
needs-retry voice.

The owner's ask (session v0.40.0): give the v0.39 chime a Settings UI
(``sound_enabled`` / ``sound_volume`` lived in config.json only), make
a batch WITH failed links stop sounding like a celebration, and keep
everything honest. Five surfaces, each covered here:

  1. The static surface — the volume-2 glyph (official lucide-static
     geometry), SettingsDialog's TAB_INFO entry, the QSlider QSS in
     BOTH palettes, config.example.json carrying the keys.
  2. The page itself — a real MainWindow (temp config): the Sound
     section registered, the toggle defaulting ON, the slider 0–100
     with the % label, config-driven init including garbage/NaN
     healing, and the toggle+slider → save_config round trip.
  3. The handlers — _sound_volume_value's 0–100 → 0.0–1.0 mapping
     (clamped on every path), the label-only drag ticks (no write
     storm), _save_sound_page's write-through.
  4. BatchSound.play_retry — the second voice: a separate cached
     effect, the same clamping / one-notice / ship-together failure
     contracts, and the v0.39 ``_effect`` introspection untouched.
  5. The routing — _play_batch_sound(needs_retry) picks the right
     voice, _celebrate_batch keys on failed_links, and
     _preview_batch_sound never blames the switch for an audio-less
     machine. Plus the asset pair (retry.wav: softer, descending) and
     the modal's honest failure heading (🏁, warning tone).

Headless-safe: QT_QPA_PLATFORM=offscreen, no GUI shown, no network,
the real config.json is never touched (CONFIG_FILE swapped to a temp
path on the owning module). QSoundEffect on an audio-less machine
accepts play() silently — asserted as a REQUEST (True), never as an
audible event.
"""

import json
import os
import shutil
import struct
import sys
import tempfile
import types
import unittest
import wave

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest import mock

from gitcurator.constants import APP_DIR
from gitcurator.gui.sound import (
    BatchSound, SUCCESS_WAV, RETRY_WAV, DEFAULT_SOUND_DIR,
)
from gitcurator.gui.main_window.processing_control import (
    ProcessingControlMixin,
)
from gitcurator.gui.main_window.vaults_config import VaultConfigMixin

try:
    from PyQt6.QtWidgets import (
        QApplication, QCheckBox, QDialog, QLabel, QMainWindow, QPushButton,
        QSlider,
    )
    _APP = QApplication.instance() or QApplication([])
    _PYQT = True
except Exception:  # pragma: no cover — CI installs PyQt6
    _PYQT = False

import gitcurator.gui.icons as _icons
import gitcurator.gui.theme as _theme
import gitcurator.gui.main_window.vaults_config as _gui_vaults
from gitcurator.gui.dialogs import SettingsDialog


# ---------------------------------------------------------------------------
# 1) The static surface — glyph, nav entry, QSS, example config
# ---------------------------------------------------------------------------

class TestStaticSurface(unittest.TestCase):

    def test_icons_pack_has_official_volume_2(self):
        # The Sound section's glyph — verbatim lucide-static v0.544.0
        # geometry (the two speaker arcs), 27 glyphs total now.
        self.assertIn('volume-2', _icons.ICONS)
        geom = _icons.ICONS['volume-2']
        # the speaker body + BOTH arc paths (volume-2, not volume-1)
        self.assertEqual(geom.count('<path'), 3)
        self.assertIn('M16 9a5 5 0 0 1 0 6', geom)     # the inner arc
        self.assertIn('M19.364 18.364', geom)          # the outer arc

    def test_tab_info_has_sound_entry(self):
        icon, desc = SettingsDialog.TAB_INFO['Sound']
        self.assertEqual(icon, 'volume-2')
        self.assertIn('chime', desc.lower())

    def test_qslider_rules_in_both_palettes(self):
        for name, t in (("LIGHT", _theme.LIGHT), ("DARK", _theme.DARK)):
            qss = _theme.build_qss(t)
            for rule in ("QSlider::groove:horizontal",
                         "QSlider::sub-page:horizontal",
                         "QSlider::add-page:horizontal",
                         "QSlider::handle:horizontal",
                         "QSlider::handle:horizontal:hover",
                         "QSlider::handle:horizontal:disabled"):
                self.assertIn(rule, qss, f"{rule} missing from {name}")
            # the rules ride the existing tokens (zero new color values)
            self.assertIn(t['progress_track'], qss)
            self.assertIn(t['progress_chunk'], qss)

    def test_config_example_carries_the_keys(self):
        with open(os.path.join(APP_DIR, 'config.example.json'),
                  encoding='utf-8') as f:
            example = json.load(f)
        self.assertIs(example['sound_enabled'], True)
        self.assertEqual(example['sound_volume'], 0.8)


# ---------------------------------------------------------------------------
# 2) The page — a real MainWindow on a temp config
# ---------------------------------------------------------------------------

@unittest.skipUnless(_PYQT, "PyQt6 not installed — GUI checks skipped")
class TestSoundPage(unittest.TestCase):

    def _window(self, extra_config=None):
        """A real MainWindow backed by a scratch config.json (the
        TestGuiRoundTrip pattern: CONFIG_FILE swapped on the OWNING
        module, so the round trip never touches the real file)."""
        import gitcurator.gui.app as ga
        tmp = tempfile.mkdtemp(prefix='gc-sound-')
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        cfg = {'vault_path': os.path.join(tmp, 'gh'),
               'website_vault_path': os.path.join(tmp, 'web')}
        cfg.update(extra_config or {})
        cfg_path = os.path.join(tmp, 'config.json')
        with open(cfg_path, 'w', encoding='utf-8') as f:
            json.dump(cfg, f)
        orig = _gui_vaults.CONFIG_FILE
        _gui_vaults.CONFIG_FILE = cfg_path
        self.addCleanup(setattr, _gui_vaults, 'CONFIG_FILE', orig)
        return ga.MainWindow(), cfg_path

    def test_page_registered_with_widgets(self):
        w, _ = self._window()
        names = [name for _page, name in w._settings_pages]
        self.assertIn('Sound', names)
        # the toggle exists and defaults ON (a fresh config has no key)
        self.assertIsInstance(w.sound_enabled_check, QCheckBox)
        self.assertTrue(w.sound_enabled_check.isChecked())
        # the slider is 0–100; the % label spells its initial value
        self.assertIsInstance(w.sound_volume_slider, QSlider)
        self.assertEqual(w.sound_volume_slider.minimum(), 0)
        self.assertEqual(w.sound_volume_slider.maximum(), 100)
        self.assertEqual(w.sound_volume_slider.value(), 80)   # default 0.8
        self.assertEqual(w.sound_volume_label.text(), "80%")

    def test_slider_initialises_from_config(self):
        w, _ = self._window({'sound_volume': 0.42, 'sound_enabled': False})
        self.assertEqual(w.sound_volume_slider.value(), 42)
        self.assertEqual(w.sound_volume_label.text(), "42%")
        self.assertFalse(w.sound_enabled_check.isChecked())

    def test_garbage_volume_heals_off_scale(self):
        # non-numeric → the 0.8 default; out-of-range → clamped INTO
        # range (the widget never starts off-scale)
        w, _ = self._window({'sound_volume': 'loud'})
        self.assertEqual(w.sound_volume_slider.value(), 80)
        w2, _ = self._window({'sound_volume': 7.5})
        self.assertEqual(w2.sound_volume_slider.value(), 100)

    def test_toggle_and_slider_round_trip_through_save(self):
        w, cfg_path = self._window({'custom_owner_key': 'keep me'})
        w.sound_enabled_check.setChecked(False)
        w.sound_volume_slider.setValue(30)
        w.save_config()
        with open(cfg_path, encoding='utf-8') as f:
            on_disk = json.load(f)
        self.assertIs(on_disk['sound_enabled'], False)
        self.assertAlmostEqual(on_disk['sound_volume'], 0.3)
        # the merge never drops keys the page knows nothing about
        self.assertEqual(on_disk['custom_owner_key'], 'keep me')

    def test_drag_ticks_do_not_write(self):
        # _on_sound_volume_changed moves ONLY the label — the write
        # happens on slider RELEASE (_save_sound_page), so scrubbing
        # never becomes a config.json write storm.
        w, cfg_path = self._window()
        writes = []
        w.save_config = lambda: writes.append(True)  # type: ignore
        w.sound_volume_slider.setValue(13)
        w.sound_volume_slider.setValue(57)
        self.assertEqual(w.sound_volume_label.text(), "57%")
        self.assertEqual(writes, [])                 # no write while scrubbing


# ---------------------------------------------------------------------------
# 3) The handlers — bare VaultConfigMixin hosts (the TestMixinFlow shape)
# ---------------------------------------------------------------------------

@unittest.skipUnless(_PYQT, "PyQt6 not installed — GUI checks skipped")
class TestVolumeHandlers(unittest.TestCase):

    def _host(self, config=None, with_widgets=True):
        host = type("_VolHost", (VaultConfigMixin,), {})()
        host.config = dict(config or {})
        if with_widgets:
            host.sound_volume_slider = QSlider()
            host.sound_volume_slider.setRange(0, 100)
            host.sound_volume_label = QLabel("80%")
            host.sound_enabled_check = QCheckBox()
        host._saved = []
        host.save_config = lambda: host._saved.append(True)  # type: ignore
        return host

    def test_volume_maps_0_100_into_unit_range(self):
        for ticks, expected in ((0, 0.0), (37, 0.37), (100, 1.0)):
            host = self._host()
            host.sound_volume_slider.setValue(ticks)
            self.assertAlmostEqual(host._sound_volume_value(), expected)

    def test_volume_clamps_config_garbage(self):
        # no slider (widget missing) → the config value, healed:
        # non-numeric/NaN → 0.8; out-of-range → clamped into [0, 1]
        self.assertAlmostEqual(
            self._host({'sound_volume': 'loud'}, False)._sound_volume_value(),
            0.8)
        self.assertAlmostEqual(
            self._host({'sound_volume': float('nan')},
                       False)._sound_volume_value(), 0.8)
        self.assertAlmostEqual(
            self._host({'sound_volume': 7.5}, False)._sound_volume_value(),
            1.0)
        self.assertAlmostEqual(
            self._host({'sound_volume': -3}, False)._sound_volume_value(),
            0.0)

    def test_label_follows_ticks_without_saving(self):
        host = self._host()
        host._on_sound_volume_changed(64)
        self.assertEqual(host.sound_volume_label.text(), "64%")
        self.assertEqual(host._saved, [])

    def test_save_sound_page_writes_through(self):
        # *args swallowed — Qt signals pass checked-state payloads
        host = self._host()
        host._save_sound_page(True)
        self.assertEqual(host._saved, [True])


# ---------------------------------------------------------------------------
# 4) BatchSound.play_retry — the second voice (hermetic)
# ---------------------------------------------------------------------------

class _FakeEffect:
    """Records what QSoundEffect would do — hermetic on machines with
    no audio stack at all (the v0.39 fixture shape, shared verbatim
    semantics)."""

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
    so both voices are testable WITHOUT any audio backend."""

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


class TestRetryVoice(unittest.TestCase):

    def _log_recorder(self):
        rec = []

        def _log(msg, level="info"):
            rec.append((level, msg))
        return _log, rec

    def test_play_retry_plays_the_retry_asset(self):
        bs = _seeded_sound()
        self.assertTrue(bs.play_retry(volume=0.6))
        effect = bs._effects[RETRY_WAV]
        self.assertEqual(effect.plays, 1)
        self.assertTrue(effect.source.toLocalFile().endswith(RETRY_WAV))
        self.assertAlmostEqual(effect.volume, 0.6)

    def test_two_voices_two_effects_both_cached(self):
        bs = _seeded_sound()
        bs.play_success()
        bs.play_retry()
        bs.play_retry()
        success_e = bs._effects[SUCCESS_WAV]
        retry_e = bs._effects[RETRY_WAV]
        self.assertIsNot(success_e, retry_e)          # one per asset
        self.assertEqual((success_e.plays, retry_e.plays), (1, 2))
        # the v0.39 introspection contract is untouched: _effect IS
        # the success chime's instance (None before the first play).
        self.assertIs(bs._effect, success_e)

    def test_v39_effect_property_is_none_before_first_play(self):
        bs = _seeded_sound()
        self.assertIsNone(bs._effect)

    def test_missing_retry_poisons_the_whole_player(self):
        # The two WAVs ship together in one zip: if retry.wav is gone
        # the install is broken, so the FIRST missing file sets the
        # session's unavailable_reason (one notice, then silence) —
        # and BOTH voices go quiet (the v0.39 contract, kept verbatim
        # for the pair). success.wav alone is NOT enough.
        tmp = tempfile.mkdtemp(prefix='gc-sounds-')
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        shutil.copy(os.path.join(DEFAULT_SOUND_DIR, SUCCESS_WAV),
                    os.path.join(tmp, SUCCESS_WAV))
        log, rec = self._log_recorder()
        bs = BatchSound(sound_dir=tmp)
        bs._qt_multimedia = _FakeQtMultimedia()
        self.assertFalse(bs.play_retry(volume=0.8, log=log))
        self.assertIn("retry.wav chime missing", bs.unavailable_reason)
        self.assertEqual(len(rec), 1)
        # the whole player is poisoned — success is silent too
        self.assertFalse(bs.play_success(volume=0.8, log=log))
        self.assertEqual(len(rec), 1)                 # still ONE notice

    def test_retry_volume_clamped_into_qt_range(self):
        bs = _seeded_sound()
        bs.play_retry(volume=5.0)                     # garbage → 1.0
        self.assertAlmostEqual(bs._effects[RETRY_WAV].volume, 1.0)
        bs2 = _seeded_sound()
        bs2.play_retry(volume="soft")                 # non-numeric → 0.8
        self.assertAlmostEqual(bs2._effects[RETRY_WAV].volume, 0.8)

    def test_qtmultimedia_unavailable_is_one_notice(self):
        log, rec = self._log_recorder()
        bs = BatchSound()
        with mock.patch.dict(sys.modules, {"PyQt6.QtMultimedia": None}):
            self.assertFalse(bs.play_retry(volume=0.8, log=log))
        self.assertIn("Qt audio module unavailable", bs.unavailable_reason)
        self.assertEqual(len(rec), 1)
        self.assertIn("🔕", rec[0][1])                # the friendly glyph
        self.assertFalse(bs.play_retry(volume=0.8, log=log))
        self.assertEqual(len(rec), 1)                 # never repeated


# ---------------------------------------------------------------------------
# 5) The routing — which voice a batch (or a preview) plays
# ---------------------------------------------------------------------------

def _bare_mixin(config=None):
    win = ProcessingControlMixin()
    win.config = dict(config or {})
    win.calls = []
    win.log_message = lambda msg, level="info": win.calls.append(
        ("log", level, msg))
    return win


_SUMMARY = {
    'stopped': False, 'github_processed': 6, 'github_total': 6,
    'new_notes': 6, 'websites': None, 'failed_links': 0,
    'report_path': '', 'summary_path': '',
}


class TestVoiceRouting(unittest.TestCase):

    def test_needs_retry_picks_the_retry_voice(self):
        win = _bare_mixin({'sound_volume': 0.55})
        win._batch_sound = _seeded_sound()
        self.assertTrue(win._play_batch_sound(needs_retry=True))
        bs = win._batch_sound
        self.assertEqual(bs._effects[RETRY_WAV].plays, 1)
        self.assertNotIn(SUCCESS_WAV, bs._effects)    # success never built
        self.assertAlmostEqual(bs._effects[RETRY_WAV].volume, 0.55)

    def test_clean_finish_picks_the_success_voice(self):
        win = _bare_mixin({})
        win._batch_sound = _seeded_sound()
        self.assertTrue(win._play_batch_sound())      # default: clean
        bs = win._batch_sound
        self.assertEqual(bs._effects[SUCCESS_WAV].plays, 1)
        self.assertNotIn(RETRY_WAV, bs._effects)

    def test_celebrate_keys_on_failed_links(self):
        win = _bare_mixin({})
        routed = []
        win._play_batch_sound = lambda needs_retry=False: \
            routed.append(needs_retry) or True
        win._show_batch_success_modal = lambda summary, elapsed="": None
        win._celebrate_batch(dict(_SUMMARY, failed_links=3))
        self.assertEqual(routed, [True])
        for summary in (dict(_SUMMARY, failed_links=0),
                        dict(_SUMMARY, failed_links='x'),  # garbage → clean
                        None):                            # no summary
            routed.clear()
            win._celebrate_batch(summary)
            self.assertEqual(routed, [False])

    def test_preview_blames_the_switch_when_muted(self):
        win = _bare_mixin({'sound_enabled': False})
        self.assertFalse(win._preview_batch_sound())
        self.assertTrue(any("🔇" in m and "switched off" in m
                            for _tag, _lvl, m in win.calls))
        # the gate short-circuited before the player was even built
        self.assertFalse(hasattr(win, '_batch_sound'))

    def test_preview_never_blames_the_switch_for_audioless(self):
        # BatchSound already said it once (its 🔕 line) — the preview
        # must not add a misleading "switched off" hint on top.
        win = _bare_mixin({})
        bs = BatchSound()
        bs._unavailable_reason = "preset (audio-less machine)"
        win._batch_sound = bs
        self.assertFalse(win._preview_batch_sound(needs_retry=True))
        self.assertFalse([m for _lvl, m in win.calls if "🔇" in m])

    def test_preview_plays_the_requested_voice(self):
        win = _bare_mixin({})
        win._batch_sound = _seeded_sound()
        self.assertTrue(win._preview_batch_sound(needs_retry=True))
        self.assertEqual(win._batch_sound._effects[RETRY_WAV].plays, 1)
        self.assertTrue(win._preview_batch_sound(needs_retry=False))
        self.assertEqual(win._batch_sound._effects[SUCCESS_WAV].plays, 1)
        self.assertFalse([m for _lvl, m in win.calls if "🔇" in m])


# ---------------------------------------------------------------------------
# 6) The asset pair — retry.wav is softer AND descending
# ---------------------------------------------------------------------------

class TestAssetPair(unittest.TestCase):

    @staticmethod
    def _wav(path):
        with wave.open(path) as w:
            frames = w.readframes(w.getnframes())
            samples = struct.unpack('<%dh' % (len(frames) // 2), frames)
            return {
                'rate': w.getframerate(), 'width': w.getsampwidth(),
                'channels': w.getnchannels(),
                'dur': w.getnframes() / w.getframerate(),
                'peak': max(abs(s) for s in samples) / 32767.0,
                'samples': samples,
            }

    @staticmethod
    def _dominant_hz(samples, rate):
        """Zero-crossing dominant-frequency estimate — good enough to
        tell C6 (~1046 Hz) from G5 (~784 Hz) apart on pure tones."""
        zc = sum(1 for i in range(1, len(samples))
                 if (samples[i - 1] < 0) != (samples[i] < 0))
        return zc * rate / (2 * len(samples))

    def test_retry_wav_format(self):
        # the same PCM shape as success.wav (Qt's QSoundEffect path)
        d = self._wav(os.path.join(DEFAULT_SOUND_DIR, RETRY_WAV))
        self.assertEqual((d['rate'], d['width'], d['channels']),
                         (44100, 2, 1))
        self.assertGreaterEqual(d['dur'], 0.5)      # a real two-tone
        self.assertLessEqual(d['dur'], 0.9)         # …but quicker than success

    def test_retry_is_softer_than_success(self):
        # the softness is BAKED INTO the asset, so one user volume
        # applies to both voices and the preview is the truth
        retry = self._wav(os.path.join(DEFAULT_SOUND_DIR, RETRY_WAV))
        success = self._wav(os.path.join(DEFAULT_SOUND_DIR, SUCCESS_WAV))
        self.assertLess(retry['peak'], success['peak'])
        self.assertLess(retry['peak'], 0.6)          # genuinely soft
        self.assertGreater(success['peak'], 0.6)

    def test_retry_descends_and_success_ascends(self):
        # the two voices are distinguishable by contour alone: retry
        # falls (C6 → G5), success rises (E5 → G5 → C6)
        retry = self._wav(os.path.join(DEFAULT_SOUND_DIR, RETRY_WAV))
        success = self._wav(os.path.join(DEFAULT_SOUND_DIR, SUCCESS_WAV))
        r_first = self._dominant_hz(retry['samples'][:len(retry['samples']) // 2],
                                    retry['rate'])
        r_second = self._dominant_hz(retry['samples'][len(retry['samples']) // 2:],
                                     retry['rate'])
        self.assertGreater(r_first, r_second)        # descending
        s_first = self._dominant_hz(success['samples'][:len(success['samples']) // 3],
                                    success['rate'])
        s_third = self._dominant_hz(success['samples'][2 * len(success['samples']) // 3:],
                                    success['rate'])
        self.assertLess(s_first, s_third)            # ascending


# ---------------------------------------------------------------------------
# 7) The modal's honest failure heading — a real QWidget host
# ---------------------------------------------------------------------------

@unittest.skipUnless(_PYQT, "PyQt6 not installed — GUI checks skipped")
class TestFailureHeading(unittest.TestCase):

    def _host(self):
        host = type("_ModalHost", (QMainWindow, ProcessingControlMixin), {})()
        host.config = {}
        host._closing = False
        host.calls = []
        host.log_message = lambda msg, level="info": host.calls.append(
            ("log", level, msg))
        host._style_btn = lambda btn, kind: btn
        host._animate_dialog = lambda d: None
        host.show()
        return host

    def _heading(self, summary):
        host = self._host()
        dlg = host._build_batch_success_dialog(summary, " in 1m 4s")
        titles = [l for l in dlg.findChildren(QLabel)
                  if l.text().strip().startswith("Batch Complete")]
        glyphs = [l.text() for l in dlg.findChildren(QLabel)
                  if l.text().strip() in ("🎉", "🏁")]
        return titles[0], glyphs[0]

    def test_clean_batch_is_a_party(self):
        title, glyph = self._heading(dict(_SUMMARY))
        self.assertEqual(glyph, "🎉")
        self.assertEqual(title.text().strip(), "Batch Complete!")
        self.assertEqual(title.property("tone"), "success")

    def test_failed_batch_is_a_flag_not_a_party(self):
        # "done, but look at me": no party emoji, no exclamation mark,
        # warning tone — the twin of the amber Needs-retry row and the
        # softer retry.wav
        title, glyph = self._heading(dict(_SUMMARY, failed_links=2))
        self.assertEqual(glyph, "🏁")
        self.assertEqual(title.text().strip(), "Batch Complete")
        self.assertEqual(title.property("tone"), "warning")


if __name__ == "__main__":
    unittest.main(verbosity=2)
