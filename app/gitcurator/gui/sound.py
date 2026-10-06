#!/usr/bin/env python3
"""
gitcurator.gui.sound — the batch-finish chime (v0.39.0) and its
needs-retry variant (v0.40.0).

WHAT THIS IS
============
The owner asked the next version to "play an audio for success and
finishing of the batch along with success modal". This module is the
audio half: a thin, failure-proof wrapper around Qt's ``QSoundEffect``
that plays ``assets/sounds/success.wav`` the moment a processing batch
runs to natural completion (MainWindow.processing_finished →
_celebrate_batch). The visual half — the Batch Complete scorecard
modal — lives in gui/main_window/processing_control.py.

v0.40.0 — the second voice: a batch that finishes WITH failed links no
longer sounds like a celebration. The same wrapper plays the softer
descending ``assets/sounds/retry.wav`` instead (the scorecard's amber
"Needs retry" row is the visual twin). One chime system, one config
pair (``sound_enabled`` / ``sound_volume`` — now settable from Settings
→ Sound), two assets.

DESIGN RULES
============
1. **The batch never waits on audio.** Everything here is best-effort:
   a missing QtMultimedia, a missing WAV, or a machine with no audio
   backend degrades to a silent no-op with (at most) ONE log line —
   never an exception, never a retry, never a stall.
2. **Lazy everything.** QtMultimedia is imported on first play, not at
   module import — environments without the optional audio libs can
   still import gitcurator.gui.sound (the smoke/CLI paths) cleanly.
3. **One effect per asset, kept alive.** QSoundEffect must outlive its
   play() call or the sound is cut; each asset's instance is cached in
   ``_effects`` and the caller (MainWindow) holds the BatchSound object
   for the session — replaying resets the position, exactly the chime
   semantics we want.
4. **No audio backend ≠ error.** On headless CI, offscreen probes and
   audio-less servers Qt accepts the play() and simply stays silent
   (status → Error). That is not worth a warning — only genuine
   setup problems (missing module / missing file) are logged once.
5. **Missing assets poison the whole player, on purpose.** The two
   WAVs ship together in one folder inside one zip; if either is
   gone the install is broken, so the first missing file sets the
   session's ``unavailable_reason`` (one notice, then silence). That
   is the v0.39 contract, kept verbatim for both voices.
"""

import os

from gitcurator.constants import APP_DIR

DEFAULT_SOUND_DIR = os.path.join(APP_DIR, "assets", "sounds")
SUCCESS_WAV = "success.wav"
RETRY_WAV = "retry.wav"


class BatchSound:
    """Session-scoped player for the app's UI sounds.

    Instantiate once and keep it (MainWindow holds ``self._batch_sound``);
    each wrapped QSoundEffect is created on first play and reused —
    replaying resets the position, exactly the chime semantics we want.
    """

    def __init__(self, sound_dir=None):
        self._sound_dir = sound_dir or DEFAULT_SOUND_DIR
        self._effects = {}            # wav name -> cached QSoundEffect
        self._qt_multimedia = None     # module ref once imported
        self._unavailable_reason = ""  # set once; never retried loudly

    # -- introspection (tests + the GUI's one-time log) ------------------

    @property
    def unavailable_reason(self) -> str:
        """'' while the sound system looks usable; otherwise the one-line
        reason playback is a no-op (missing module / missing file)."""
        return self._unavailable_reason

    @property
    def _effect(self):
        """The SUCCESS chime's cached QSoundEffect (v0.39 introspection
        contract — tests read it directly; None before the first play)."""
        return self._effects.get(SUCCESS_WAV)

    # -- the public calls --------------------------------------------------

    def play_success(self, volume: float = 0.8, log=None) -> bool:
        """Play the batch-success chime (clean finish). Returns True when
        a playback was actually requested (Qt accepted it — audible on
        any machine with a working audio backend), False when this is a
        silent no-op.

        ``log`` is the MainWindow.log_message-style callback used for the
        ONE-TIME unavailable notice; None means fully silent."""
        return self._play(SUCCESS_WAV, volume, log)

    def play_retry(self, volume: float = 0.8, log=None) -> bool:
        """v0.40.0 — play the needs-retry variant: a batch that finished
        with failed links. Softer and descending by construction (the
        asset itself peaks lower than success.wav), so the SAME user
        volume applies to both voices and the Settings preview is the
        truth. Same failure contract as play_success."""
        return self._play(RETRY_WAV, volume, log)

    # -- internals --------------------------------------------------------

    def _play(self, wav_name: str, volume: float, log) -> bool:
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

        # 2) The WAV asset (missing = broken install → rule 5)
        wav = os.path.join(self._sound_dir, wav_name)
        if not os.path.isfile(wav):
            self._fail(f"{wav_name} chime missing ({wav})", log)
            return False

        # 3) Build (once per asset) + play. A QSoundEffect without an
        #    audio backend accepts this silently — that is NOT a failure
        #    (rule 4).
        try:
            effect = self._effects.get(wav_name)
            if effect is None:
                from PyQt6.QtCore import QUrl
                effect = self._qt_multimedia.QSoundEffect()
                effect.setSource(QUrl.fromLocalFile(wav))
                self._effects[wav_name] = effect
            effect.setVolume(_clamp(volume, 0.8))
            effect.play()
            return True
        except Exception as e:
            # Real breakage (effect construction, source loading) — say
            # it once, then stop trying for the session.
            self._effects.pop(wav_name, None)
            self._fail(f"audio playback unavailable ({e})", log)
            return False

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
