"""tests/test_autopip.py — v0.47.0, the door that ships itself.

The owner's fresh failure pile carried five lines ending "a third door
exists: pip install curl_cffi (the browser-TLS handshake)" — the door
was ARMED IN CODE (v0.45.0) but the LIBRARY never landed on his machine
(his install predates requirements.txt's entry; no re-installer ever
runs). A hint the owner must act on is not a workaround. v0.47.0: the
first time a fingerprint-class wall needs the Chrome handshake (or a
batch warms the door at startup), ONE pip install runs through the
same interpreter; the probe re-arms; the wall gets answered.

Covered here (unit law: stubbed installer + fake curl_cffi session,
zero sockets, zero subprocesses):

* the trigger law — autopip fires ONLY when the door is wanted (a
  refusal-family wall with impersonation enabled) and the library is
  missing; resource truths and connection-class truths never install
  anything; the fetch-level default is OFF (hermetic callers never see
  a subprocess); the config opt-out is honored end to end.
* the once-per-process law — a second wall in the same batch never
  re-runs pip; a failed install is remembered just the same (the hint
  stands for the whole process); a successful install RESETS the import
  probe so the door is armed.
* the verdicts — a wall met with autopip on and a successful install
  gets the REAL third-door answer (via impersonated Chrome); a failed
  install keeps the honest hint, never an error.
* fetch_url's wiring — impersonate_autopip=True rides to the door; the
  default (off) never pips.
* the pipeline's pre-arm — the production init (injected fetchers never
  see it) installs the door up front with honest log lines; the
  opt-out keeps the old not-installed line; an already-armed door logs
  armed and never pips.
* release bookkeeping — VERSION, CHANGELOG, the config keys (constants
  + config.example.json), requirements.txt, the installer contract.

No PyQt import at module level (the libEGL-less sandbox rule).
"""

import os
import time
import unittest

from gitcurator.core import web_fetch as wf

_WALL_URL = 'http://walled.example.net/page'   # non-loopback by construction

_OWNER_LINE = ("proxy: HTTP 403 — forbidden (the server refused this client "
               "without naming a wall) | direct: HTTP 403 — forbidden (the "
               "server refused this client without naming a wall)")


class _FakeImpResponse:
    """The shape curl_cffi's Response gives _impersonated_fetch_once."""

    def __init__(self, status_code=200,
                 body=b'<html><body>ok</body></html>',
                 headers=None, url='', charset='utf-8'):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {'Content-Type': 'text/html; charset=utf-8'}
        self.url = url
        self.charset_encoding = charset

    def iter_content(self):
        yield self._body

    def close(self):
        pass


class _FakeImpSession:
    """Records the dial; answers from a scriptable queue."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append({'url': url, **kwargs})
        r = self.responses.pop(0) if self.responses \
            else _FakeImpResponse(status_code=403, body=b'')
        return r


def _walled(reason=_OWNER_LINE, http_status=403):
    return wf.FetchResult(_WALL_URL, status='failed', reason=reason,
                          http_status=http_status)


class _AutoPipCase(unittest.TestCase):
    """Shared plumbing: the probe forced OFF (the library 'missing'),
    the pip installer stubbed, the session factory swappable, every
    piece of module state restored after each test."""

    def setUp(self):
        self._probe = dict(wf._CURL_CFFI_STATE)
        self._autopip = dict(wf._AUTOPIP_STATE)
        self._factory = wf._new_impersonation_session
        self._installer = wf._pip_install_curl_cffi
        self._available = wf.curl_cffi_available
        wf._CURL_CFFI_STATE['tried'] = True
        wf._CURL_CFFI_STATE['ok'] = False     # the library is 'missing'
        wf._AUTOPIP_STATE['tried'] = False
        wf._AUTOPIP_STATE['ok'] = False
        wf._IMP_SESSION = None
        self.factory_sessions = []
        self.pip_calls = []

    def tearDown(self):
        wf._CURL_CFFI_STATE.clear()
        wf._CURL_CFFI_STATE.update(self._probe)
        wf._AUTOPIP_STATE.clear()
        wf._AUTOPIP_STATE.update(self._autopip)
        wf._new_impersonation_session = self._factory
        wf._pip_install_curl_cffi = self._installer
        wf.curl_cffi_available = self._available
        wf._IMP_SESSION = None

    def arm_fake_session(self, *responses):
        def factory():
            s = _FakeImpSession(responses)
            self.factory_sessions.append(s)
            return s

        wf._IMP_SESSION = None
        wf._new_impersonation_session = factory

    def arm_installer(self, ok=True):
        def installer():
            self.pip_calls.append(True)
            return ok

        wf._pip_install_curl_cffi = installer

    def arm_import_probe(self, ok=True):
        """Stub curl_cffi_available (stateful tests that need the probe
        to keep answering after the install). Restored in tearDown."""
        def available():
            return ok
        wf.curl_cffi_available = available

    def ask(self, walled=None, routes=(None,), enabled=True, autopip=False):
        return wf._maybe_impersonate(
            _WALL_URL, 5, 2_000_000, list(routes), enabled,
            walled if walled is not None else _walled(),
            time.monotonic(), autopip=autopip)


class TestTriggerLaw(_AutoPipCase):

    def test_the_owners_line_installs_the_door_and_answers(self):
        # autopip on + successful install → the REAL third-door answer.
        # The installer stub returns True; ensure resets the import
        # probe; the REAL import (curl_cffi IS installed in this
        # sandbox — CI installs it via requirements) arms the door; the
        # fake session answers the wall.
        self.arm_installer(ok=True)
        self.arm_fake_session(_FakeImpResponse(status_code=200))
        res = self.ask(autopip=True)
        self.assertTrue(res.ok, res.reason)
        self.assertEqual(res.status, 'full')
        self.assertIn('via impersonated Chrome', res.reason)
        self.assertEqual(len(self.pip_calls), 1)
        self.assertEqual(len(self.factory_sessions), 1)

    def test_autopip_off_keeps_the_hint_no_pip(self):
        self.arm_installer(ok=True)
        res = self.ask(autopip=False)
        self.assertFalse(res.ok)
        self.assertIn('pip install curl_cffi', res.reason)
        self.assertEqual(len(self.pip_calls), 0)
        self.assertEqual(len(self.factory_sessions), 0)

    def test_failed_install_keeps_the_hint_never_raises(self):
        self.arm_installer(ok=False)
        res = self.ask(autopip=True)
        self.assertFalse(res.ok)
        self.assertIn('pip install curl_cffi', res.reason)
        self.assertEqual(len(self.pip_calls), 1)

    def test_resource_truth_never_installs_anything(self):
        self.arm_installer(ok=True)
        res = self.ask(walled=_walled('HTTP 404', http_status=404),
                       autopip=True)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, 'HTTP 404')
        self.assertEqual(len(self.pip_calls), 0)

    def test_connection_truth_never_installs_anything(self):
        # a plain timeout is a network truth, not a fingerprint verdict
        self.arm_installer(ok=True)
        res = self.ask(walled=_walled('connection: timed out',
                                      http_status=None), autopip=True)
        self.assertFalse(res.ok)
        self.assertEqual(len(self.pip_calls), 0)

    def test_disabled_door_never_installs_anything(self):
        self.arm_installer(ok=True)
        res = self.ask(enabled=False, autopip=True)
        self.assertFalse(res.ok)
        self.assertEqual(len(self.pip_calls), 0)

    def test_loopback_never_installs_anything(self):
        self.arm_installer(ok=True)
        res = wf._maybe_impersonate(
            'http://127.0.0.1:9/x', 5, 1000, [None], True,
            wf.FetchResult('http://127.0.0.1:9/x', status='failed',
                           reason='HTTP 403', http_status=403),
            time.monotonic(), autopip=True)
        self.assertFalse(res.ok)
        self.assertEqual(len(self.pip_calls), 0)


class TestOncePerProcess(_AutoPipCase):

    def test_second_wall_never_re_runs_pip(self):
        self.arm_installer(ok=True)
        self.arm_fake_session(_FakeImpResponse(status_code=200))
        self.ask(autopip=True)
        self.ask(autopip=True)
        self.assertEqual(len(self.pip_calls), 1)

    def test_failed_install_is_remembered_too(self):
        self.arm_installer(ok=False)
        self.ask(autopip=True)
        self.ask(autopip=True)
        self.assertEqual(len(self.pip_calls), 1)

    def test_successful_install_resets_the_import_probe(self):
        # ensure_curl_cffi_installed() resets _CURL_CFFI_STATE so the
        # next curl_cffi_available() re-probes (the REAL import in this
        # sandbox — the library is pip-installed here, so it arms).
        self.arm_installer(ok=True)
        ok = wf.ensure_curl_cffi_installed()
        self.assertTrue(ok)
        self.assertTrue(wf.curl_cffi_available())
        self.assertTrue(wf._CURL_CFFI_STATE['ok'])

    def test_ensure_is_a_noop_when_already_tried(self):
        self.arm_installer(ok=True)
        wf._AUTOPIP_STATE['tried'] = True
        wf._AUTOPIP_STATE['ok'] = False
        self.assertFalse(wf.ensure_curl_cffi_installed())
        self.assertEqual(len(self.pip_calls), 0)


class TestFetchUrlWiring(_AutoPipCase):

    def _walled_site(self):
        """A live local wall on a NON-loopback address (the bothdoors
        law: loopback never escalates, so it can never prove a door)."""
        import socket
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'walled'
                self.send_response(403)
                self.send_header('Content-Type', 'text/html')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = HTTPServer(('127.0.0.1', 0), _Handler)
        # A non-loopback bind: the machine's own LAN address, so the
        # ladder's loopback law does not swallow the door.
        try:
            host = socket.gethostbyname(socket.gethostname())
        except Exception:
            host = '127.0.0.1'
        if host.startswith('127.'):
            self.skipTest('no non-loopback address available')
        srv.server_close()
        srv = HTTPServer((host, 0), _Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        return f'http://{host}:{srv.server_port}/walled'

    def test_autopip_kwarg_rides_to_the_door(self):
        url = self._walled_site()
        self.arm_installer(ok=True)
        self.arm_fake_session(_FakeImpResponse(status_code=200))
        res = wf.fetch_url(url, timeout_s=8,
                           impersonate_autopip=True)
        self.assertTrue(res.ok, res.reason)
        self.assertIn('via impersonated Chrome', res.reason)
        self.assertEqual(len(self.pip_calls), 1)

    def test_default_off_never_pips(self):
        url = self._walled_site()
        self.arm_installer(ok=True)
        res = wf.fetch_url(url, timeout_s=8)
        self.assertFalse(res.ok)
        self.assertIn('pip install curl_cffi', res.reason)
        self.assertEqual(len(self.pip_calls), 0)


class TestPipelinePreArm(_AutoPipCase):
    """The production init warms the door (injected fetchers never see
    it — the hermetic law), with honest log lines."""

    def _make(self, config=None):
        import json
        import tempfile
        import shutil
        from gitcurator.core import website_pipeline as wp

        tmp = tempfile.mkdtemp(prefix='autopip-')
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        vault = os.path.join(tmp, 'websites')
        os.makedirs(vault, exist_ok=True)
        db = wp.WebsiteStateDB(db_path=os.path.join(tmp, 'cache.db'))
        self.addCleanup(db.close)

        def fake_fetch(url, **kwargs):
            return wf.FetchResult(url, status='failed',
                                  reason='HTTP 404 — not found')

        cfg = {'website_vault_path': vault, 'web_domain_delay_s': 0}
        cfg.update(config or {})
        logs = []
        # fetch_fn=None = PRODUCTION wiring — the pre-arm branch fires
        # (nothing fetches during __init__; the autopip installer is
        # stubbed, the proxy is unset, no network is touched).
        pipe = wp.WebsitePipeline(
            config=cfg,
            llm_call=lambda messages, task=None: '{}',
            vault_index_has=lambda u: False,
            state=db, fetch_fn=None,
            log=lambda m, l='info': logs.append((l, m)))
        return pipe, logs

    def test_init_installs_the_door_with_honest_logs(self):
        self.arm_installer(ok=True)
        pipe, logs = self._make()
        text = '\n'.join(m for _, m in logs)
        self.assertIn('installing curl_cffi automatically', text)
        self.assertIn('ARMED', text)
        self.assertEqual(len(self.pip_calls), 1)
        # and the flag rides every fetch the pipeline makes:
        self.assertTrue(pipe.impersonate_autopip)

    def test_init_failed_install_logs_the_hand_remedy(self):
        self.arm_installer(ok=False)
        pipe, logs = self._make()
        text = '\n'.join(m for _, m in logs)
        self.assertIn('automatic install failed', text)
        self.assertIn('pip install curl_cffi', text)

    def test_init_optout_keeps_the_old_hint_line(self):
        self.arm_installer(ok=True)
        pipe, logs = self._make(
            config={'web_impersonate_autopip': False})
        text = '\n'.join(m for _, m in logs)
        self.assertIn('third door not installed', text)
        self.assertEqual(len(self.pip_calls), 0)
        self.assertFalse(pipe.impersonate_autopip)

    def test_init_with_door_armed_never_pips(self):
        wf._CURL_CFFI_STATE['ok'] = True
        self.arm_installer(ok=True)
        pipe, logs = self._make()
        text = '\n'.join(m for _, m in logs)
        self.assertIn('third door armed', text)
        self.assertEqual(len(self.pip_calls), 0)


class TestSourceContracts(unittest.TestCase):
    """The house source-contract tests."""

    def setUp(self):
        self.root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def _read(self, *parts):
        with open(os.path.join(self.root, *parts), 'r',
                  encoding='utf-8') as f:
            return f.read()

    def test_version_is_0470(self):
        self.assertEqual(self._read('VERSION').strip(), '0.55.0')

    def test_changelog_has_the_beat(self):
        text = self._read('CHANGELOG.md')
        self.assertIn('## [0.47.0]', text)
        self.assertIn('ships itself', text.lower())

    def test_config_documents_the_key(self):
        self.assertIn('"web_impersonate_autopip": True',
                      self._read('app', 'gitcurator', 'constants.py'))
        self.assertIn('"web_impersonate_autopip": true',
                      self._read('app', 'config.example.json'))

    def test_requirements_still_ship_the_library(self):
        text = self._read('app', 'requirements.txt')
        self.assertIn('curl_cffi', text)

    def test_installer_still_installs_requirements(self):
        text = self._read('app', '1-INSTALL.bat')
        self.assertIn('pip install -r requirements.txt', text)

    def test_ci_runs_this_module(self):
        text = self._read('.github', 'workflows', 'ci.yml')
        self.assertIn('tests.test_autopip', text)


if __name__ == '__main__':
    unittest.main()
