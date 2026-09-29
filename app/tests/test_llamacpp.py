#!/usr/bin/env python3
"""
test_llamacpp.py — v0.15.0: llama.cpp engine detection.

Owner request 2026-09-29: "the app must have llama.cpp engine detection…
it must detect llama.cpp service and it's model detected automatically."
The app saw Ollama and the custom OpenAI-compatible endpoint; llama-server
now gets the same DETECTED treatment as Ollama.

Covers, all against a LOCAL stdlib http.server that mimics llama-server
(/props, /health, /v1/models, /v1/chat/completions) — no model server, no
network beyond 127.0.0.1:
  * normalize_llamacpp_api_url — every user spelling → the /v1 base
  * is_llamacpp_props / llamacpp_model_from_props — the positive llama.cpp
    identification (old + new /props shapes; OpenAI-ish bodies rejected)
  * probe_llamacpp — found/ready/models/model/detail; still-loading
    servers; hidden /props (not llama.cpp); hidden /v1/models (props
    fallback); dead addresses; the Bearer key reaches the wire
  * detect_llamacpp — the port scan finds the server, SKIPS a live
    non-llama.cpp OpenAI endpoint (never misreported), returns None when
    nothing matches
  * resolve_llamacpp_model — configured wins, then /v1/models[0], then
    the /props alias, then None ("model detected automatically")
  * config compatibility — CONFIG_EXAMPLE carries the llamacpp_* keys
    (both constants modules); merge_config preserves user values
  * the REAL ProcessingWorker._llm_analyze router with provider
    'llamacpp' — URL normalization, the models.analyze override, the
    empty-model fallback to the preflight-detected name, and one
    end-to-end call through the real openai_chat against the fake server
  * CLI: _preflight_llm auto-detects + persists the model, aborts with a
    clear message when nothing is detected; run_config_card names llama.cpp
  * the backfill tool: --provider llamacpp builds a working llm closure
    with the auto-detected model, and refuses clearly when no server runs
  * the label: one LLAMACPP_PROVIDER_LABEL constant, used by the GUI radio
    + settings group; the CLI and README name llama.cpp

Headless-safe: QT_QPA_PLATFORM=offscreen, no GUI is ever shown.
"""

import contextlib
import io
import json
import os
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import gitcurator.gui.app as gui_app
from gitcurator.core import dryrun
from gitcurator.core import llm_client as _llm
from gitcurator.core import website_pipeline as _wp
from gitcurator.core.storage import merge_config
from gitcurator.constants import CONFIG_EXAMPLE as CORE_CONFIG_EXAMPLE
from gitcurator.gui.constants import CONFIG_EXAMPLE as GUI_CONFIG_EXAMPLE


# ---------------------------------------------------------------------------
# Fake llama.cpp server (llama-server: /props, /health, /v1/models,
# /v1/chat/completions — the llama.cpp route shapes, per server.cpp)
# ---------------------------------------------------------------------------

_LLMACPP_PROPS = {
    'model_path': 'models/qwen2.5-3b-instruct-q4_k_m.gguf',
    'model_alias': 'qwen2.5-3b',
    'total_slots': 1,
    'default_generation_settings': {'temperature': 0.8, 'n_predict': -1},
}

_LLMACPP_CHAT = json.dumps({
    'summary': 'ok', 'how_it_works': 'ok', 'core_value': 'ok',
    'features': ['a'], 'difference': 'ok', 'category': 'Uncategorized',
    'confidence': 50, 'tags': []})


class _FakeLlamaCppHandler(BaseHTTPRequestHandler):
    """One handler, configurable through server.behavior.

    behavior keys:
      props            dict served at /props (default: llama.cpp shape)
      hide_props       404 for /props (a non-llama.cpp server)
      not_llamacpp     serve an OpenAI-ish dict at /props instead
      health_code      /health status (default 200)
      health_status    /health body status (default 'ok'; 'loading' → 503-ish)
      models           list for /v1/models (default ['qwen2.5-3b'])
      hide_models      404 for /v1/models (old builds / proxies)
      chat_content     the /v1/chat/completions answer
    """
    behavior = {}
    state = {'requests': []}

    def log_message(self, *args):
        pass

    def _send(self, code, payload):
        data = json.dumps(payload).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.server.state['requests'].append({
            'path': self.path, 'auth': self.headers.get('Authorization', ''),
            'method': 'GET'})
        b = self.server.behavior
        if self.path.rstrip('/').endswith('/props'):
            if b.get('hide_props'):
                self._send(404, {'error': 'not found'})
                return
            props = b.get('props', _LLMACPP_PROPS)
            if b.get('not_llamacpp'):
                props = {'data': [{'id': 'some-model'}], 'object': 'list'}
            self._send(200, props)
            return
        if self.path.rstrip('/').endswith('/health'):
            status = b.get('health_status', 'ok')
            code = int(b.get('health_code', 200))
            if status == 'loading' and 'health_code' not in b:
                code = 503
            self._send(code, {'status': status, 'slots': []})
            return
        if self.path.rstrip('/').endswith('/models'):
            if b.get('hide_models'):
                self._send(404, {'error': 'not found'})
                return
            names = b.get('models', ['qwen2.5-3b'])
            self._send(200, {'object': 'list',
                             'data': [{'id': n, 'object': 'model'}
                                      for n in names]})
            return
        self._send(404, {'error': 'no such route'})

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode('utf-8'))
        except Exception:
            payload = {}
        self.server.state['requests'].append({
            'path': self.path, 'auth': self.headers.get('Authorization', ''),
            'method': 'POST', 'body': payload})
        content = self.server.behavior.get('chat_content', _LLMACPP_CHAT)
        self._send(200, {'choices': [{'message': {'role': 'assistant',
                                                  'content': content}}]})


def _start_llamacpp(behavior=None):
    server = HTTPServer(('127.0.0.1', 0), _FakeLlamaCppHandler)
    server.behavior = dict(behavior or {})
    server.state = {'requests': []}
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f'http://127.0.0.1:{server.server_port}'


def _stop(server):
    """Full stop: end the serve loop AND close the listening socket (a
    shutdown()-only server keeps its port reserved until GC — the source
    of socket-exhaustion flakes in long suites)."""
    server.shutdown()
    server.server_close()


# A live NON-llama.cpp OpenAI-compatible endpoint (no /props route).
class _FakePlainOpenAIHandler(_FakeLlamaCppHandler):
    def do_GET(self):
        if self.path.rstrip('/').endswith('/props'):
            self.server.state['requests'].append({'path': self.path,
                                                  'method': 'GET',
                                                  'auth': ''})
            self._send(404, {'error': 'not found'})
            return
        super().do_GET()


def _start_plain_openai():
    server = HTTPServer(('127.0.0.1', 0), _FakePlainOpenAIHandler)
    server.behavior = {'models': ['gpt-fake']}
    server.state = {'requests': []}
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _dead_port():
    """A port number that nothing listens on (bind, read the port, close)."""
    import socket
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---------------------------------------------------------------------------
# normalize_llamacpp_api_url — every user spelling
# ---------------------------------------------------------------------------

class TestNormalizeUrl(unittest.TestCase):

    def test_bare_host_port(self):
        self.assertEqual(
            _llm.normalize_llamacpp_api_url('127.0.0.1:8080'),
            'http://127.0.0.1:8080/v1')

    def test_scheme_no_path(self):
        self.assertEqual(
            _llm.normalize_llamacpp_api_url('http://127.0.0.1:8080'),
            'http://127.0.0.1:8080/v1')

    def test_trailing_slash(self):
        self.assertEqual(
            _llm.normalize_llamacpp_api_url('http://127.0.0.1:8080/'),
            'http://127.0.0.1:8080/v1')

    def test_full_base_with_v1_kept(self):
        self.assertEqual(
            _llm.normalize_llamacpp_api_url('http://127.0.0.1:8080/v1'),
            'http://127.0.0.1:8080/v1')

    def test_deeper_path_preserved(self):
        # reverse proxies: a path beyond the bare host is the user's choice
        self.assertEqual(
            _llm.normalize_llamacpp_api_url('http://host:8080/llamacpp'),
            'http://host:8080/llamacpp')

    def test_empty_is_the_default(self):
        self.assertEqual(
            _llm.normalize_llamacpp_api_url(''),
            _llm.LLAMACPP_DEFAULT_BASE + '/v1')
        self.assertEqual(
            _llm.normalize_llamacpp_api_url(None),
            _llm.LLAMACPP_DEFAULT_BASE + '/v1')

    def test_whitespace_stripped(self):
        self.assertEqual(
            _llm.normalize_llamacpp_api_url('  http://127.0.0.1:8080  '),
            'http://127.0.0.1:8080/v1')


# ---------------------------------------------------------------------------
# /props identification — the positive llama.cpp fingerprint
# ---------------------------------------------------------------------------

class TestPropsIdentification(unittest.TestCase):

    def test_new_props_shape(self):
        self.assertTrue(_llm.is_llamacpp_props(_LLMACPP_PROPS))

    def test_old_build_without_alias(self):
        self.assertTrue(_llm.is_llamacpp_props({
            'model_path': 'models/foo.gguf',
            'default_generation_settings': {},
            'total_slots': 1}))

    def test_openai_payload_rejected(self):
        self.assertFalse(_llm.is_llamacpp_props(
            {'data': [{'id': 'm'}], 'object': 'list'}))

    def test_empty_and_nondict_rejected(self):
        self.assertFalse(_llm.is_llamacpp_props({}))
        self.assertFalse(_llm.is_llamacpp_props([1, 2]))
        self.assertFalse(_llm.is_llamacpp_props('llama.cpp'))

    def test_model_from_props_alias(self):
        self.assertEqual(
            _llm.llamacpp_model_from_props(_LLMACPP_PROPS), 'qwen2.5-3b')

    def test_model_from_props_basename_fallbacks(self):
        self.assertEqual(_llm.llamacpp_model_from_props({
            'model_path': 'models/foo.Q4_K_M.gguf'}),
            'foo.Q4_K_M.gguf')
        # the placeholder alias is skipped, the path basename used
        self.assertEqual(_llm.llamacpp_model_from_props({
            'model_alias': 'unknown',
            'model_path': '/home/x/bar.gguf'}), 'bar.gguf')
        # windows-style paths too
        self.assertEqual(_llm.llamacpp_model_from_props({
            'model_path': 'C:\\models\\baz.gguf'}), 'baz.gguf')

    def test_model_from_props_nothing(self):
        self.assertIsNone(_llm.llamacpp_model_from_props({}))
        self.assertIsNone(_llm.llamacpp_model_from_props(None))


# ---------------------------------------------------------------------------
# probe_llamacpp — one address, the full picture
# ---------------------------------------------------------------------------

class TestProbe(unittest.TestCase):

    def setUp(self):
        self.server, self.root = _start_llamacpp()
        self.addCleanup(_stop, self.server)

    def test_found_ready_with_models(self):
        probe = _llm.probe_llamacpp(self.root)
        self.assertTrue(probe['found'])
        self.assertTrue(probe['ready'])
        self.assertEqual(probe['models'], ['qwen2.5-3b'])
        self.assertEqual(probe['model'], 'qwen2.5-3b')
        self.assertEqual(probe['props_model'], 'qwen2.5-3b')
        self.assertEqual(probe['base_url'], self.root)
        self.assertIn('ready', probe['detail'])
        self.assertIn(self.root, probe['detail'])

    def test_model_prefers_v1_models_over_props(self):
        self.server.behavior['models'] = ['advertised-name']
        probe = _llm.probe_llamacpp(self.root)
        self.assertEqual(probe['model'], 'advertised-name')
        self.assertEqual(probe['props_model'], 'qwen2.5-3b')

    def test_still_loading(self):
        self.server.behavior['health_status'] = 'loading'
        probe = _llm.probe_llamacpp(self.root)
        self.assertTrue(probe['found'])
        self.assertFalse(probe['ready'])
        self.assertIn('loading', probe['detail'])

    def test_hidden_v1_models_falls_back_to_props(self):
        self.server.behavior['hide_models'] = True
        probe = _llm.probe_llamacpp(self.root)
        self.assertTrue(probe['found'])
        self.assertEqual(probe['models'], [])
        self.assertEqual(probe['model'], 'qwen2.5-3b')  # the /props alias

    def test_hidden_props_is_not_llamacpp(self):
        self.server.behavior['hide_props'] = True
        probe = _llm.probe_llamacpp(self.root)
        self.assertFalse(probe['found'])
        self.assertIn('404', probe['detail'])

    def test_openai_style_props_is_not_llamacpp(self):
        self.server.behavior['not_llamacpp'] = True
        probe = _llm.probe_llamacpp(self.root)
        self.assertFalse(probe['found'])
        self.assertIn('not llama.cpp', probe['detail'])

    def test_dead_address_never_raises(self):
        probe = _llm.probe_llamacpp(f'http://127.0.0.1:{_dead_port()}')
        self.assertFalse(probe['found'])
        self.assertIn('could not reach', probe['detail'])

    def test_api_key_reaches_the_wire(self):
        _llm.probe_llamacpp(self.root, api_key='sekrit')
        gets = [r for r in self.server.state['requests']
                if r['method'] == 'GET']
        self.assertTrue(gets)
        self.assertTrue(all(r['auth'] == 'Bearer sekrit' for r in gets))

    def test_raw_user_spelling_accepted(self):
        # '127.0.0.1:PORT' — no scheme, no /v1 — still probes the server
        hostport = self.root.split('//', 1)[1]
        probe = _llm.probe_llamacpp(hostport)
        self.assertTrue(probe['found'])
        self.assertEqual(probe['base_url'], self.root)


# ---------------------------------------------------------------------------
# detect_llamacpp — the port scan
# ---------------------------------------------------------------------------

class TestDetect(unittest.TestCase):

    def setUp(self):
        self.llama_server, self.llama_root = _start_llamacpp()
        self.openai_server = _start_plain_openai()
        self.addCleanup(_stop, self.llama_server)
        self.addCleanup(_stop, self.openai_server)

    def test_finds_the_server(self):
        probe = _llm.detect_llamacpp(
            ports=(self.llama_server.server_port,))
        self.assertIsNotNone(probe)
        self.assertTrue(probe['found'])
        self.assertEqual(probe['base_url'], self.llama_root)
        self.assertEqual(probe['model'], 'qwen2.5-3b')

    def test_skips_live_non_llamacpp_servers(self):
        # a plain OpenAI-compatible endpoint on an earlier port is probed
        # and SKIPPED (no /props), then the real llama.cpp is found
        probe = _llm.detect_llamacpp(ports=(
            self.openai_server.server_port,
            self.llama_server.server_port))
        self.assertIsNotNone(probe)
        self.assertEqual(probe['base_url'], self.llama_root)

    def test_only_non_llamacpp_servers_is_none(self):
        self.assertIsNone(_llm.detect_llamacpp(ports=(
            self.openai_server.server_port,)))

    def test_dead_ports_is_none(self):
        self.assertIsNone(_llm.detect_llamacpp(ports=(
            _dead_port(), _dead_port())))


# ---------------------------------------------------------------------------
# resolve_llamacpp_model — "its model detected automatically"
# ---------------------------------------------------------------------------

class TestResolveModel(unittest.TestCase):

    def test_configured_wins(self):
        self.assertEqual(
            _llm.resolve_llamacpp_model({'llamacpp_model': 'my-pick'},
                                        ['server-model'], 'props-model'),
            'my-pick')

    def test_first_listed_when_unset(self):
        self.assertEqual(
            _llm.resolve_llamacpp_model({'llamacpp_model': ''},
                                        ['a', 'b'], 'props-model'),
            'a')

    def test_props_when_no_list(self):
        self.assertEqual(
            _llm.resolve_llamacpp_model({}, [], 'props-model'),
            'props-model')

    def test_none_when_nothing(self):
        self.assertIsNone(_llm.resolve_llamacpp_model({}, [], None))
        self.assertIsNone(_llm.resolve_llamacpp_model(None, [], None))


# ---------------------------------------------------------------------------
# Config compatibility — old configs load unchanged, new keys default
# ---------------------------------------------------------------------------

class TestConfigCompat(unittest.TestCase):

    def test_core_config_example_carries_the_keys(self):
        self.assertEqual(CORE_CONFIG_EXAMPLE['llamacpp_api_url'],
                         'http://127.0.0.1:8080/v1')
        self.assertEqual(CORE_CONFIG_EXAMPLE['llamacpp_api_key'], '')
        self.assertEqual(CORE_CONFIG_EXAMPLE['llamacpp_model'], '')

    def test_gui_config_example_carries_the_keys(self):
        self.assertEqual(GUI_CONFIG_EXAMPLE['llamacpp_api_url'],
                         'http://127.0.0.1:8080/v1')
        self.assertEqual(GUI_CONFIG_EXAMPLE['llamacpp_model'], '')

    def test_merge_config_preserves_llamacpp_values(self):
        old = {'llm_provider': 'llamacpp',
               'llamacpp_api_url': 'http://127.0.0.1:9090/v1',
               'llamacpp_model': 'mistral-7b'}
        merged = merge_config(old, {'github_token': 'x'})
        self.assertEqual(merged['llamacpp_api_url'],
                         'http://127.0.0.1:9090/v1')
        self.assertEqual(merged['llamacpp_model'], 'mistral-7b')

    def test_old_config_without_keys_is_fine(self):
        # an old config has no llamacpp_* keys — reads fall back to the
        # defaults exactly like the cloud keys always have
        old = {'llm_provider': 'ollama'}
        merged = merge_config(old, {})
        self.assertEqual(merged.get('llamacpp_api_url',
                                    _llm.LLAMACPP_DEFAULT_BASE + '/v1'),
                         'http://127.0.0.1:8080/v1')


# ---------------------------------------------------------------------------
# The real worker router — _llm_analyze with provider 'llamacpp'
# ---------------------------------------------------------------------------

class _Log:
    def __init__(self):
        self.lines = []

    def emit(self, msg, level='info'):
        self.lines.append((level, msg))


class TestWorkerRouter(unittest.TestCase):
    """The GitHub analyze router through the REAL ProcessingWorker code,
    mirroring the Phase-4 router tests: _call_cloud_llm replaced by a
    recorder for the routing facts, plus one end-to-end call against the
    fake llama.cpp server through the REAL openai_chat."""

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix='llamacpp-worker-')
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self._orig_cloud = gui_app.ProcessingWorker._call_cloud_llm
        self._orig_app_dir = _wp.APP_DIR
        _wp.APP_DIR = self.tmp
        self.addCleanup(self._restore)

    def _restore(self):
        # staticmethod re-wrap: class access unwraps the original into a
        # plain function, and assigning that back would turn every later
        # self._call_cloud_llm(...) into a BOUND call (self passed as
        # api_url) — the end-to-end test below caught exactly that.
        gui_app.ProcessingWorker._call_cloud_llm = staticmethod(
            self._orig_cloud)
        _wp.APP_DIR = self._orig_app_dir

    def _worker(self, config):
        w = gui_app.ProcessingWorker.__new__(gui_app.ProcessingWorker)
        w.config = config
        w.log_message = _Log()
        w.link_tracker = None
        w.is_running = True
        w._non_github_urls = []
        return w

    def test_routes_through_openai_path_with_analyze_override(self):
        seen = []

        def recorder(api_url, api_key, model, messages,
                     json_mode=False, timeout_s=300, num_ctx=None,
                     on_warn=None):
            seen.append((api_url, model, json_mode))
            return _LLMACPP_CHAT

        gui_app.ProcessingWorker._call_cloud_llm = staticmethod(recorder)
        cfg = {'llm_provider': 'llamacpp',
               'llamacpp_api_url': 'http://127.0.0.1:9/v1',
               'llamacpp_api_key': '',
               'llamacpp_model': 'base-model',
               'models': {'analyze': 'analyze-override'}}
        w = self._worker(cfg)
        result = w._llm_analyze(
            None, 'base-model', 'repo-x', 'desc', [], 'owner', 1, 2)
        self.assertEqual(seen[0],
                         ('http://127.0.0.1:9/v1', 'analyze-override', True))
        self.assertEqual(result['summary'], 'ok')

    def test_url_is_normalized(self):
        seen = []

        def recorder(api_url, api_key, model, messages, **kwargs):
            seen.append(api_url)
            return _LLMACPP_CHAT

        gui_app.ProcessingWorker._call_cloud_llm = staticmethod(recorder)
        cfg = {'llm_provider': 'llamacpp',
               'llamacpp_api_url': '127.0.0.1:9',   # raw user spelling
               'llamacpp_model': 'm'}
        w = self._worker(cfg)
        w._llm_analyze(None, 'm', 'repo-x', 'desc', [], 'owner', 1, 2)
        self.assertEqual(seen, ['http://127.0.0.1:9/v1'])

    def test_empty_model_falls_back_to_the_preflight_name(self):
        seen = []

        def recorder(api_url, api_key, model, messages, **kwargs):
            seen.append(model)
            return _LLMACPP_CHAT

        gui_app.ProcessingWorker._call_cloud_llm = staticmethod(recorder)
        cfg = {'llm_provider': 'llamacpp',
               'llamacpp_api_url': 'http://127.0.0.1:9/v1',
               'llamacpp_model': ''}   # not yet auto-filled
        w = self._worker(cfg)
        # 'detected-model' = what the batch preflight resolved + passed in
        w._llm_analyze(None, 'detected-model', 'repo-x', 'desc', [],
                       'owner', 1, 2)
        self.assertEqual(seen, ['detected-model'])

    def test_end_to_end_against_the_fake_server(self):
        server, root = _start_llamacpp({'models': ['qwen2.5-3b']})
        self.addCleanup(_stop, server)
        cfg = {'llm_provider': 'llamacpp',
               'llamacpp_api_url': root + '/v1',
               'llamacpp_api_key': '',
               'llamacpp_model': 'qwen2.5-3b'}
        w = self._worker(cfg)
        result = w._llm_analyze(
            None, 'qwen2.5-3b', 'repo-x', 'desc', [], 'owner', 1, 2)
        self.assertEqual(result['summary'], 'ok')
        posts = [r for r in server.state['requests']
                 if r['method'] == 'POST']
        self.assertTrue(posts)
        self.assertEqual(posts[0]['path'], '/v1/chat/completions')
        self.assertEqual(posts[0]['body']['model'], 'qwen2.5-3b')


# ---------------------------------------------------------------------------
# CLI — preflight + run card
# ---------------------------------------------------------------------------

class TestCLI(unittest.TestCase):

    def setUp(self):
        self.server, self.root = _start_llamacpp()
        self.addCleanup(_stop, self.server)
        from gitcurator import cli
        self.cli = cli

    def test_preflight_auto_detects_and_persists_the_model(self):
        cfg = {'llm_provider': 'llamacpp',
               'llamacpp_api_url': self.root + '/v1',
               'llamacpp_api_key': ''}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ok = self.cli._preflight_llm(cfg, None)
        self.assertTrue(ok)
        self.assertEqual(cfg['llamacpp_model'], 'qwen2.5-3b')  # auto-filled
        self.assertIn('auto-detected', out.getvalue())

    def test_preflight_aborts_when_nothing_is_detected(self):
        dead = f'http://127.0.0.1:{_dead_port()}'
        cfg = {'llm_provider': 'llamacpp',
               'llamacpp_api_url': dead,
               'llamacpp_api_key': ''}
        orig = _llm.detect_llamacpp
        _llm.detect_llamacpp = lambda *a, **k: None  # scan finds nothing
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                ok = self.cli._preflight_llm(cfg, None)
        finally:
            _llm.detect_llamacpp = orig
        self.assertFalse(ok)
        self.assertIn('llama-server', out.getvalue())

    def test_preflight_switches_to_the_scanned_url(self):
        # configured URL dead, scan finds the server elsewhere
        dead = f'http://127.0.0.1:{_dead_port()}'
        cfg = {'llm_provider': 'llamacpp',
               'llamacpp_api_url': dead,
               'llamacpp_api_key': ''}
        orig = _llm.detect_llamacpp
        _llm.detect_llamacpp = lambda *a, **k: _llm.probe_llamacpp(self.root)
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                ok = self.cli._preflight_llm(cfg, None)
        finally:
            _llm.detect_llamacpp = orig
        self.assertTrue(ok)
        self.assertEqual(cfg['llamacpp_api_url'], self.root + '/v1')

    def test_run_config_card_names_llamacpp(self):
        cfg = {'llm_provider': 'llamacpp',
               'llamacpp_api_url': 'http://127.0.0.1:8080/v1',
               'llamacpp_model': 'qwen2.5-3b',
               'github_token': '', 'vault_path': ''}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.cli.run_config_card(cfg)
        text = out.getvalue()
        self.assertIn('llama.cpp', text)
        self.assertIn('qwen2.5-3b', text)


# ---------------------------------------------------------------------------
# The backfill tool — --provider llamacpp
# ---------------------------------------------------------------------------

class TestBackfillRouter(unittest.TestCase):

    def setUp(self):
        self.server, self.root = _start_llamacpp()
        self.addCleanup(_stop, self.server)
        from gitcurator.tools import backfill_websites as _bf
        self.bf = _bf

    def _args(self, **over):
        args = {'provider': 'llamacpp', 'api_url': '', 'api_key': '',
                'model': '', 'timeout': 10}
        args.update(over)
        return SimpleNamespace(**args)

    def test_builds_a_working_closure_with_autodetected_model(self):
        config = {'llm_provider': 'llamacpp',
                  'llamacpp_api_url': self.root + '/v1',
                  'llamacpp_api_key': '',
                  'llamacpp_model': ''}   # empty → auto-detect
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            llm = self.bf._build_llm_call(config, self._args())
        reply = llm([{'role': 'user', 'content': 'hi'}])
        self.assertEqual(json.loads(reply)['summary'], 'ok')
        posts = [r for r in self.server.state['requests']
                 if r['method'] == 'POST']
        self.assertEqual(posts[-1]['body']['model'], 'qwen2.5-3b')
        self.assertIn('llama.cpp', out.getvalue())

    def test_refuses_clearly_when_no_server(self):
        dead = f'http://127.0.0.1:{_dead_port()}'
        config = {'llm_provider': 'llamacpp',
                  'llamacpp_api_url': dead,
                  'llamacpp_api_key': '',
                  'llamacpp_model': ''}
        orig = _llm.detect_llamacpp
        _llm.detect_llamacpp = lambda *a, **k: None
        try:
            with self.assertRaises(SystemExit) as cm:
                self.bf._build_llm_call(config, self._args())
        finally:
            _llm.detect_llamacpp = orig
        self.assertIn('llama-server', str(cm.exception))


# ---------------------------------------------------------------------------
# The label — one constant, used everywhere the option is named
# ---------------------------------------------------------------------------

class TestLabel(unittest.TestCase):

    def test_the_constant(self):
        self.assertEqual(_llm.LLAMACPP_PROVIDER_LABEL,
                         'llama.cpp server (local)')
        self.assertEqual(_llm.LLAMACPP_DEFAULT_BASE,
                         'http://127.0.0.1:8080')
        # the scan starts at the llama-server default port
        self.assertEqual(_llm.LLAMACPP_SCAN_PORTS[0], 8080)

    def _source(self, module_name):
        import importlib
        import inspect
        mod = importlib.import_module(module_name)
        with open(inspect.getsourcefile(mod), encoding='utf-8') as f:
            return f.read()

    def test_gui_uses_the_constant(self):
        src = self._source('gitcurator.gui.app')
        # the radio button AND the settings group box
        self.assertGreaterEqual(
            src.count('_llm_client.LLAMACPP_PROVIDER_LABEL'), 2)
        # the three provider values persist through save_config
        self.assertIn('"llamacpp"', src)

    def test_cli_names_the_option(self):
        src = self._source('gitcurator.cli')
        self.assertIn('llama.cpp', src)
        self.assertIn('llamacpp', src)

    def test_readme_names_the_option(self):
        readme = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), 'README.md')
        with open(readme, encoding='utf-8') as f:
            src = f.read()
        self.assertIn('llama.cpp', src)
        self.assertIn('llamacpp', src)


if __name__ == '__main__':
    unittest.main()
