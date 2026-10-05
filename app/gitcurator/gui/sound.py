#!/usr/bin/env python3
"""
gitcurator.gui.sound — the batch-finish chime (v0.39.0).

WHAT THIS IS
============
The owner asked the next version to "play an audio for success and
finishing of the batch along with success modal". This module is the
audio half: a thin, failure-proof wrapper around Qt's ``QSoundEffect``
that plays ``assets/sounds/success.wav`` the moment a processing batch
runs to natural completion (MainWindow.processing_finished →
_celebrate_batch). The visual half — the Batch Complete scorecard
modal — lives in gui/main_window/processing_control.py.

DESIGN RULES
============
1. **The batch never waits on audio.** Everything here is best-effort:
   a missing QtMultimedia, a missing WAV, or a machine with no audio
   backend degrades to a silent no-op with (at most) ONE log line —
   never an exception, never a retry, never a stall.
2. **Lazy everything.** QtMultimedia is imported on first play, not at
   module import — environments without the optional audio libs can
   still import gitcurator.gui.sound (the smoke/CLI paths) cleanly.
3. **One effect, kept alive.** QSoundEffect must outlive its play()
   call or the sound is cut; the instance is cached on self and the
   caller (MainWindow) holds the BatchSound object for the session.
4. **No audio backend ≠ error.** On headless CI, offscreen probes and
   audio-less servers Qt accepts the play() and simply stays silent
   (status → Error). That is not worth a warning — only genuine
   setup problems (missing module / missing file) are logged once.
"""

import os

from gitcurator.constants import APP_DIR

DEFAULT_SOUND_DIR = os.path.join(APP_DIR, "assets", "sounds")
SUCCESS_WAV = "success.wav"


class BatchSound:
    """Session-scoped player for the app's UI sounds.

    Instantiate once and keep it (MainWindow holds ``self._batch_sound``);
    the wrapped QSoundEffect is created on first play and reused —
    replaying resets the position, exactly the chime semantics we want.
    """

    def __init__(self, sound_dir=None):
        self._sound_dir = sound_dir or DEFAULT_SOUND_DIR
        self._effect = None            # cached QSoundEffect (or None)
        self._qt_multimedia = None     # module ref once imported
        self._unavailable_reason = ""  # set once; never retried loudly

    # -- introspection (tests + the GUI's one-time log) ------------------

    @property
    def unavailable_reason(self) -> str:
        """'' while the sound system looks usable; otherwise the one-line
        reason playback is a no-op (missing module / missing file)."""
        return self._unavailable_reason

    # -- the one public call ---------------------------------------------

    def play_success(self, volume: float = 0.8, log=None) -> bool:
        """Play the batch-success chime. Returns True when a playback was
        actually requested (Qt accepted it — audible on any machine with
        a working audio backend), False when this is a silent no-op.

        ``log`` is the MainWindow.log_message-style callback used for the
        ONE-TIME unavailable notice; None means fully silent."""
        if self._unavailable_reason:
            return False

        # 1) QtMultimedia (lazy — optional in minimal environments).
        #    import_module on purpose: it goes through the import system
        #    (a package-attribute shortcut would mask an uninstallable
        #    module that was somehow left on the PyQt6 package object).
        if self._qt_multimedia is None:
            try:
                import importlib
                self._qt_multimedia = importlib.import_module(
                    "PyQt6.QtMultimedia")
            except Exception as e:  # ImportError or the platform plugin
                self._fail(f"Qt audio module unavailable ({e})", log)
                return False

        # 2) The WAV asset
        wav = os.path.join(self._sound_dir, SUCCESS_WAV)
        if not os.path.isfile(wav):
            self._fail(f"success chime missing ({wav})", log)
            return False

        # 3) Build (once) + play. A QSoundEffect without an audio backend
        #    accepts this silently — that is NOT a failure (rule 4).
        try:
            if self._effect is None:
                from PyQt6.QtCore import QUrl
                self._effect = self._qt_multimedia.QSoundEffect()
                self._effect.setSource(QUrl.fromLocalFile(wav))
            self._effect.setVolume(_clamp(volume, 0.8))
            self._effect.play()
            return True
        except Exception as e:
            # Real breakage (effect construction, source loading) — say it
            # once, then stop trying for the session.
            self._effect = None
            self._fail(f"audio playback unavailable ({e})", log)
            return False

    # -- internals --------------------------------------------------------

    def _fail(self, reason: str, log):
        """Remember the failure and report it exactly once."""
        self._unavailable_reason = reason
        if log is not None:
            try:
                log(f"🔕 Success chime disabled — {reason}", "warning")
            except Exception:
                pass


def _clamp(volume: float, default: float) -> float:
    """Config volumes are user data — clamp into QSoundEffect's [0, 1]."""
    try:
        v = float(volume)
    except (TypeError, ValueError):
        return default
    if v != v:  # NaN
        return default
    return max(0.0, min(1.0, v))
