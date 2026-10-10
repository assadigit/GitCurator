"""tests/test_scangui.py — v0.62.0 THE GUI CONFIRM DOOR.

The owner's report (session, verbatim): "the scan now suggest new
folders to be made, but there is no modal or accept or confirm button
to actually LLM do them." The plan existed but the only gate that
could APPLY it was the Telegram round-trip — at the desk the proposal
was a dead letter.

The answer: the scan plan becomes a MODAL (ScanPlanDialog — 🗂️ Apply
plan / ✋ Keep everything) fed by the login-code pattern's own twin
(TestWorker's scan_confirm_requested → request_scan_confirm blocks the
worker thread → provide_scan_verdict hands the answer back), and the
job picks its door by config ``scan_confirm_door`` — 'gui' the default
whenever a GUI ask-gate is injected, 'telegram' the old round-trip.
Both doors guard the SAME enforcement: nothing moves or deletes until
the owner answers, whichever screen he answers on.

Covered here (the hermetic law — zero network, zero vault writes):

* the PURE display model (no Qt): the rows and totals, the 20-item
  cap with the honest count, the doors riding the deletion rows, the
  flags, and the never-crash contract for bad plans;
* the ask-gate (Qt): request blocks until provide lands, the verdict
  vocabulary is enforced (garbage reads as timeout), and the silent
  owner times out to the safe defer;
* the dialog's verdict doors (Qt): Apply → confirmed, Keep → declined,
  the safe default before any answer, the answering window's zero →
  timeout, and ask_scan_plan's round shape;
* the wiring (Qt): the mixin's handler routes the plan into the modal
  and the verdict back down; a broken modal is a decline with a
  warning, never a crash;
* the job's door selection (Qt): the GUI door is the default when the
  gate is injected (confirmed → the plan is applied with the same
  hands), 'telegram' pins the old round-trip (the gate is never
  called), 'gui' without a gate falls back with a warning, a broken
  gate is a safe defer, and declined/garbage answers touch nothing;
* the release bookkeeping (the house source-contract tests).
"""

import os
import shutil
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

from gitcurator.gui.scan_plan_dialog import (
    MAX_SHOWN, VERDICT_CONFIRMED, VERDICT_DECLINED, VERDICT_TIMEOUT,
    plan_display_model)

_PLAN = {
    'deletions': [
        {'url': 'https://t.example/trash-note', 'canonical':
         'https://t.example/trash-note', 'title': 'Trash note',
         'marker': 'Trash folder', 'door': 'trash folder', 'path': ''},
        {'url': 'https://t.example/tagged', 'canonical':
         'https://t.example/tagged', 'title': 'Tagged note',
         'marker': '🗑️', 'door': 'note tag', 'path': ''}],
    'kept_handwritten': 1,
    'moves': [{'note': 'p.md', 'path': '/nowhere/p.md', 'title': 'p',
               'from': '(vault root)', 'to': 'AI-Domain/Agents',
               'reason': 'it is an agents list'}],
    'new_folders': ['AI-Domain/Agents'],
    'summary': 'file the orphan with its kin',
    'inventory': {'total_notes': 12, 'root_notes': 1,
                  'uncategorized_notes': 2, 'hand_notes': 0,
                  'trash_notes': 1, 'folder_count': 3},
}


class TestTheDisplayModel(unittest.TestCase):
    """plan_display_model — the pure layer (no Qt, no files)."""

    def test_the_rows_and_totals(self):
        m = plan_display_model(_PLAN)
        self.assertEqual(m['deletions_total'], 2)
        self.assertEqual(m['moves_total'], 1)
        self.assertEqual(m['new_folders_total'], 1)
        self.assertEqual(m['new_folders'], ['AI-Domain/Agents'])
        self.assertEqual(m['summary'], 'file the orphan with its kin')
        self.assertEqual(m['kept_handwritten'], 1)
        self.assertEqual(m['inventory']['total_notes'], 12)
        self.assertEqual(m['inventory']['trash_notes'], 1)
        self.assertTrue(m['has_deletions'])
        self.assertTrue(m['has_filing'])

    def test_the_doors_ride_the_deletion_rows(self):
        m = plan_display_model(_PLAN)
        doors = {d['door'] for d in m['deletions']}
        self.assertEqual(doors, {'trash folder', 'note tag'})

    def test_the_cap_twenty_shown_the_rest_counted(self):
        plan = dict(_PLAN)
        plan['deletions'] = [
            {'url': f'https://x.example/{i}', 'title': f'n{i}',
             'marker': '🗑️', 'door': 'note tag'}
            for i in range(MAX_SHOWN + 7)]
        plan['moves'] = [
            {'note': f'm{i}.md', 'from': '(vault root)', 'to': 'X',
             'reason': ''} for i in range(MAX_SHOWN + 3)]
        m = plan_display_model(plan)
        self.assertEqual(len(m['deletions']), MAX_SHOWN)
        self.assertEqual(m['deletions_total'], MAX_SHOWN + 7)
        self.assertEqual(len(m['moves']), MAX_SHOWN)
        self.assertEqual(m['moves_total'], MAX_SHOWN + 3)

    def test_a_bad_plan_never_crashes(self):
        for bad in (None, {}, {'deletions': 'nope',
                               'moves': [42, None, 'x'],
                               'new_folders': [3, None],
                               'inventory': 'junk',
                               'kept_handwritten': 'x',
                               'summary': None}):
            m = plan_display_model(bad)
            self.assertIsInstance(m, dict)
            self.assertEqual(m.get('deletions_total', 0), 0)
        m = plan_display_model({'deletions': [42, {'title': 'ok'}]})
        self.assertEqual(m['deletions_total'], 2)   # counted, shaped safe
        self.assertEqual(m['deletions'][0]['title'], '')
        # garbage list items are SHAPED SAFE but still counted (the
        # honest-total law: the modal never lies about how many there
        # are, whatever shape they came in)
        m = plan_display_model({'moves': [42, None, 'x']})
        self.assertEqual(m['moves_total'], 3)
        self.assertEqual(m['moves'][0]['note'], '')
        self.assertEqual(m['inventory']['total_notes'], 0)
        self.assertEqual(m['kept_handwritten'], 0)

    def test_the_summary_is_capped_at_400(self):
        plan = dict(_PLAN)
        plan['summary'] = 'x' * 900
        self.assertEqual(len(plan_display_model(plan)['summary']), 400)

    def test_the_verdict_vocabulary(self):
        self.assertEqual((VERDICT_CONFIRMED, VERDICT_DECLINED,
                          VERDICT_TIMEOUT),
                         ('confirmed', 'declined', 'timeout'))


# ---------------------------------------------------------------------------
# The Qt-guarded layers (CI installs PyQt6; offscreen at import time)
# ---------------------------------------------------------------------------

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication, QWidget
    _QAPP = QApplication.instance() or QApplication([])
    from gitcurator.gui.scan_plan_dialog import (
        ScanPlanDialog, ask_scan_plan)
    from gitcurator.gui.main_window.vault_scan_ui import VaultScanUiMixin
    from gitcurator.gui.processing_worker import TestWorker
    from gitcurator.gui import worker_jobs as _wjobs
    from gitcurator.core import vault_scan as _vs
    from gitcurator.core import website_pipeline as _wp
    _HAS_QT = True
except Exception:  # pragma: no cover — CI installs PyQt6
    _HAS_QT = False


@unittest.skipUnless(_HAS_QT, "PyQt6 unavailable")
class TestTheAskGate(unittest.TestCase):
    """TestWorker's scan ask-gate — the login-code pattern's twin."""

    def _worker(self):
        return TestWorker(lambda *a, **k: {}, 'gate-test')

    def test_request_blocks_until_provide_lands(self):
        w = self._worker()
        out = []
        t = threading.Thread(
            target=lambda: out.append(
                w.request_scan_confirm({'plan': 1}, timeout_s=30)))
        t.start()
        time.sleep(0.2)                 # the request is now parked
        self.assertTrue(t.is_alive())
        w.provide_scan_verdict('confirmed')
        t.join(timeout=5)
        self.assertFalse(t.is_alive())
        self.assertEqual(out, ['confirmed'])

    def test_the_declined_answer_rides_back(self):
        w = self._worker()
        out = []
        t = threading.Thread(
            target=lambda: out.append(
                w.request_scan_confirm({}, timeout_s=30)))
        t.start()
        time.sleep(0.1)
        w.provide_scan_verdict('declined')
        t.join(timeout=5)
        self.assertEqual(out, ['declined'])

    def test_a_garbage_verdict_reads_as_timeout(self):
        w = self._worker()
        out = []
        t = threading.Thread(
            target=lambda: out.append(
                w.request_scan_confirm({}, timeout_s=30)))
        t.start()
        time.sleep(0.1)
        w.provide_scan_verdict('maybe-tuesday')
        t.join(timeout=5)
        self.assertEqual(out, ['timeout'])

    def test_the_silent_owner_times_out_to_the_safe_defer(self):
        w = self._worker()
        start = time.monotonic()
        v = w.request_scan_confirm({}, timeout_s=1)   # the 1s floor
        self.assertEqual(v, 'timeout')
        self.assertGreaterEqual(time.monotonic() - start, 0.9)


@unittest.skipUnless(_HAS_QT, "PyQt6 unavailable")
class TestTheDialogDoors(unittest.TestCase):
    """ScanPlanDialog — Apply / Keep / the answering window."""

    def setUp(self):
        # the dialog's parent must OUTLIVE the dialog (a deleted parent
        # deletes the child — the C++ ownership law)
        self._parents = []

    def _dialog(self, timeout_s=300):
        parent = QWidget()
        parent._style_btn = lambda btn, kind: btn
        self._parents.append(parent)
        return ScanPlanDialog(parent, plan_display_model(_PLAN),
                              timeout_s=timeout_s)

    def test_the_default_verdict_is_the_safe_one(self):
        self.assertEqual(self._dialog().verdict(), VERDICT_DECLINED)

    def test_apply_is_the_only_confirmed_path(self):
        dlg = self._dialog()
        dlg._on_apply()
        self.assertEqual(dlg.verdict(), VERDICT_CONFIRMED)

    def test_keep_declines(self):
        dlg = self._dialog()
        dlg._on_keep()
        self.assertEqual(dlg.verdict(), VERDICT_DECLINED)

    def test_the_answering_window_zero_is_a_timeout(self):
        dlg = self._dialog()
        dlg._remaining = 1
        dlg._tick()
        self.assertEqual(dlg.verdict(), VERDICT_TIMEOUT)

    def test_the_clock_ticks_without_a_verdict(self):
        dlg = self._dialog(timeout_s=120)
        first = dlg._clock.text()
        dlg._tick()
        self.assertNotEqual(dlg._clock.text(), first)
        self.assertEqual(dlg.verdict(), VERDICT_DECLINED)

    def test_ask_scan_plan_round_shape(self):
        parents = []

        def _parent():
            p = QWidget()
            p._style_btn = lambda btn, kind: btn
            parents.append(p)
            return p

        def fake_exec(self):
            self._on_apply()      # the owner clicked Apply while open
            return 1
        with mock.patch.object(ScanPlanDialog, 'exec', fake_exec):
            self.assertEqual(ask_scan_plan(_parent(), _PLAN),
                             VERDICT_CONFIRMED)

        def fake_exec_keep(self):
            self._on_keep()
            return 0
        with mock.patch.object(ScanPlanDialog, 'exec', fake_exec_keep):
            self.assertEqual(ask_scan_plan(_parent(), _PLAN),
                             VERDICT_DECLINED)


@unittest.skipUnless(_HAS_QT, "PyQt6 unavailable")
class TestTheWiring(unittest.TestCase):
    """The mixin's handler — the plan up, the verdict down."""

    def _win(self):
        win = VaultScanUiMixin()
        win.config = {}
        win.logs = []

        def _log(m, l='info'):
            win.logs.append((l, m))
        win.log_message = _log
        return win

    def _stub_worker(self, delivered):
        return types.SimpleNamespace(
            provide_scan_verdict=lambda v: delivered.append(v))

    def test_the_verdict_rides_back_down(self):
        win = self._win()
        delivered = []
        w = self._stub_worker(delivered)
        with mock.patch('gitcurator.gui.scan_plan_dialog.ask_scan_plan',
                        lambda parent, plan, timeout_s=300.0:
                        VERDICT_CONFIRMED):
            win._on_scan_confirm_requested(dict(_PLAN), w)
        self.assertEqual(delivered, [VERDICT_CONFIRMED])

    def test_a_broken_modal_is_a_decline_never_a_crash(self):
        win = self._win()
        delivered = []
        w = self._stub_worker(delivered)

        def boom(parent, plan, timeout_s=300.0):
            raise RuntimeError('no screen')
        with mock.patch('gitcurator.gui.scan_plan_dialog.ask_scan_plan',
                        boom):
            win._on_scan_confirm_requested({}, w)   # never raises
        self.assertEqual(delivered, [VERDICT_DECLINED])
        self.assertTrue(any('could not open' in m for _l, m in win.logs))

    def test_the_timeout_s_rides_the_config(self):
        win = self._win()
        win.config = {'scan_confirm_timeout_s': 42}
        delivered = []
        w = self._stub_worker(delivered)
        seen = {}

        def fake(parent, plan, timeout_s=300.0):
            seen['timeout_s'] = timeout_s
            return VERDICT_DECLINED
        with mock.patch('gitcurator.gui.scan_plan_dialog.ask_scan_plan',
                        fake):
            win._on_scan_confirm_requested({}, w)
        self.assertEqual(seen['timeout_s'], 42.0)


@unittest.skipUnless(_HAS_QT, "PyQt6 unavailable")
class TestTheJobDoors(unittest.TestCase):
    """_vault_scan_job's door selection — one grammar, two doors."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='scangui-')
        self.vault = os.path.join(self.tmp, 'websites')
        os.makedirs(self.vault, exist_ok=True)
        self.logs = []
        self.applied = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def log(self, m, l='info'):
        self.logs.append((l, m))

    def all_logs(self):
        return '\n'.join(m for (_, m) in self.logs)

    def _run(self, cfg=None, confirm_gui=None):
        cfg = dict(cfg or {})
        cfg['website_vault_path'] = self.vault
        plan = dict(_PLAN)
        apply_calls = self.applied

        # the job calls log_signal.emit(msg, level) — a signal-shaped
        # stub (a bare lambda has no .emit and the job would swallow
        # every log line)
        sig = types.SimpleNamespace(
            emit=lambda m, l='info': self.log(m, l))

        def _build(vault, llm_call=None, log=None, config=None):
            return plan

        def _apply(p, vault, state, log=None):
            apply_calls.append((p, vault))
            return {'folders_created': 1, 'notes_moved': 1,
                    'moved': [], 'failed_moves': 0, 'banished': 2}
        with mock.patch.object(_vs, 'build_scan_plan', _build), \
                mock.patch.object(_vs, 'apply_scan_plan', _apply), \
                mock.patch.object(_wp, 'WebsiteStateDB',
                                  lambda *a, **k: types.SimpleNamespace(
                                      close=lambda: None)):
            return _wjobs._vault_scan_job(cfg, sig,
                                           confirm_gui=confirm_gui)

    def test_the_gui_door_is_the_default_when_injected(self):
        seen = {}

        def gate(plan, timeout_s=300.0):
            seen['plan'] = dict(plan or {})
            seen['timeout_s'] = timeout_s
            return VERDICT_CONFIRMED
        out = self._run(confirm_gui=gate)
        self.assertEqual(out['verdict'], 'confirmed')
        self.assertEqual(out['applied'], 2)      # the report's own counts
        self.assertEqual(out['moved'], 1)
        self.assertEqual(out['folders_created'], 1)
        self.assertEqual(len(self.applied), 1)   # applied ONCE
        self.assertEqual(self.applied[0][1], self.vault)
        self.assertIn('on your screen', self.all_logs())
        self.assertEqual(seen['timeout_s'], 300.0)
        self.assertEqual(seen['plan']['moves'][0]['note'], 'p.md')

    def test_the_confirmed_plan_applies_with_the_same_hands(self):
        out = self._run(confirm_gui=lambda p, timeout_s=300.0:
                        VERDICT_CONFIRMED)
        self.assertEqual(out['verdict'], 'confirmed')
        # the applied plan IS the built plan — byte-for-byte the same ask
        self.assertEqual(self.applied[0][0]['new_folders'],
                         ['AI-Domain/Agents'])

    def test_telegram_pin_never_calls_the_gate(self):
        seen = []

        def gate(plan, timeout_s=300.0):
            seen.append(plan)
            return VERDICT_CONFIRMED
        out = self._run(cfg={'scan_confirm_door': 'telegram'},
                        confirm_gui=gate)
        self.assertEqual(seen, [])                 # the gate stayed shut
        # unpaired Telegram → the safe defer, nothing applied
        self.assertEqual(out['verdict'], 'defer')
        self.assertEqual(self.applied, [])
        self.assertIn('not paired/enabled', self.all_logs())

    def test_gui_pinned_without_a_gate_warns_and_falls_back(self):
        out = self._run(cfg={'scan_confirm_door': 'gui'},
                        confirm_gui=None)
        self.assertIn("set to 'gui' but this launch has no GUI",
                      self.all_logs())
        self.assertEqual(out['verdict'], 'defer')
        self.assertEqual(self.applied, [])

    def test_a_declined_answer_touches_nothing(self):
        out = self._run(confirm_gui=lambda p, timeout_s=300.0:
                        VERDICT_DECLINED)
        self.assertEqual(out['verdict'], 'declined')
        self.assertEqual(self.applied, [])
        self.assertIn('you said keep everything', self.all_logs())

    def test_a_garbage_answer_reads_as_a_timeout(self):
        out = self._run(confirm_gui=lambda p, timeout_s=300.0: 'maybe')
        self.assertEqual(out['verdict'], 'timeout')
        self.assertEqual(self.applied, [])
        self.assertIn('no answer in time', self.all_logs())

    def test_a_broken_gate_is_a_safe_defer(self):
        def boom(plan, timeout_s=300.0):
            raise RuntimeError('the screen fell over')
        out = self._run(confirm_gui=boom)
        self.assertEqual(out['verdict'], 'defer')
        self.assertEqual(self.applied, [])
        self.assertIn('The GUI confirm door failed', self.all_logs())

    def test_the_old_shape_still_runs_without_a_gate(self):
        # the v0.61.x call shape (no confirm_gui) keeps working — the
        # telegram door, unpaired here, answers the safe defer
        out = self._run()
        self.assertEqual(out['verdict'], 'defer')
        self.assertEqual(self.applied, [])


class TestReleaseBookkeeping(unittest.TestCase):
    """The house source-contract tests."""

    def setUp(self):
        self.root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def _read(self, *parts):
        with open(os.path.join(self.root, *parts), 'r',
                  encoding='utf-8') as f:
            return f.read()

    def test_version_is_0620(self):
        self.assertEqual(self._read('VERSION').strip(), '0.63.0')

    def test_ci_and_agents_know_the_module(self):
        ci = self._read('.github', 'workflows', 'ci.yml')
        self.assertIn('tests.test_scangui', ci)
        agents = self._read('AGENTS.md')
        self.assertIn('tests.test_scangui', agents)

    def test_changelog_has_the_door(self):
        text = self._read('CHANGELOG.md')
        self.assertIn('## [0.62.0]', text)
        self.assertIn('ScanPlanDialog', text)
        flat = ' '.join(text.split())    # the prose wraps — flatten it
        self.assertIn('no modal or accept or confirm button', flat)


if __name__ == '__main__':
    unittest.main()
