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

v0.15.1 (owner report: "it still doesnt auto-detect llama cpp, the service
is running on task manager") — the AUTOMATIC catch:
  * proxy-safe loopback — probe/list/chat still work with HTTP(S)_PROXY
    env vars pointing at a dead proxy (the owner runs a VPN/proxy client;
    loopback must never ride it); _is_loopback_url table
  * process-based discovery — _parse_tasklist_pids +
    _parse_netstat_listening_ports (canned Windows output), the windows +
    posix paths over a fake _run_cmd, detect_llamacpp probing the running
    llama-server's ports FIRST (via='process') then the expanded scan list
    (via='scan')
  * secondary identification — /props-less llama.cpp builds fingerprint
    on /v1/models: owned_by 'llama.cpp', .gguf ids, the Server header;
    plain-OpenAI/vLLM shapes are still never misreported
  * llamacpp_autodetect_decision — the startup switch policy (dead Ollama
    / keyless cloud → switch; anything working → hint only)
  * the GUI applies it — MainWindow._apply_llamacpp_autodetect exercised
    unbound on a stub (provider switch, URL, model, save, log lines);
    the startup hook's guard + wiring

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
from unittest import mock

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
      models_owned_by  owned_by value for every /v1/models entry (v0.15.1)
      server_header    'Server' response header on /v1/models (v0.15.1)
    """
    behavior = {}
    state = {'requests': []}

    def log_message(self, *args):
        pass

    def _send(self, code, payload, extra_headers=None):
        data = json.dumps(payload).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
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
            owned = b.get('models_owned_by')
            data = []
            for n in names:
                entry = {'id': n, 'object': 'model'}
                if owned is not None:
                    entry['owned_by'] = owned
                data.append(entry)
            extra = {}
            if b.get('server_header'):
                extra['Server'] = str(b['server_header'])
            self._send(200, {'object': 'list', 'data': data},
                       extra_headers=extra or None)
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
        # Hermetic (v0.15.1): detect_llamacpp now probes the RUNNING
        # process's ports too — pin them to [] so the host machine can
        # never leak a real llama-server into these scan tests.
        patcher = mock.patch.object(_llm, 'llamacpp_process_ports',
                                    return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

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
                     max_output_tokens=None, on_warn=None):
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
        # v0.23.0 — the two-level radios: the llama.cpp ENGINE radio is the
        # short plain label ("🦙 llama.cpp") and the provider constant names
        # the settings group box.
        self.assertGreaterEqual(
            src.count('_llm_client.LLAMACPP_PROVIDER_LABEL'), 1)
        self.assertIn('QRadioButton("🦙 llama.cpp")', src)
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


# ---------------------------------------------------------------------------
# v0.15.1 — proxy-safe loopback (the owner runs a VPN/proxy client; a
# system proxy that doesn't bypass 127.0.0.1 must never swallow local
# probes, model lists or chat calls)
# ---------------------------------------------------------------------------

_PROXY_ENV_KEYS = ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy',
                   'ALL_PROXY', 'all_proxy')


class _ProxyEnv:
    """Context manager: point every proxy env var at a DEAD local port —
    any code path that still consults the proxy fails loudly."""

    def __enter__(self):
        import socket
        s = socket.socket()
        s.bind(('127.0.0.1', 0))
        self.dead = f'http://127.0.0.1:{s.getsockname()[1] + 1}'
        s.close()
        self.saved = {k: os.environ.get(k) for k in _PROXY_ENV_KEYS}
        for k in _PROXY_ENV_KEYS:
            os.environ[k] = self.dead
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return False


class TestProxySafeLoopback(unittest.TestCase):

    def setUp(self):
        self.server, self.root = _start_llamacpp()
        self.addCleanup(_stop, self.server)

    def test_probe_ignores_proxy_env(self):
        with _ProxyEnv():
            probe = _llm.probe_llamacpp(self.root)
        self.assertTrue(probe['found'], probe['detail'])
        self.assertEqual(probe['model'], 'qwen2.5-3b')

    def test_list_models_ignores_proxy_env(self):
        with _ProxyEnv():
            names = _llm.openai_list_models(self.root + '/v1', '', 5)
        self.assertEqual(names, ['qwen2.5-3b'])

    def test_chat_ignores_proxy_env(self):
        with _ProxyEnv():
            reply = _llm.openai_chat(self.root + '/v1', '', 'qwen2.5-3b',
                                     [{'role': 'user', 'content': 'hi'}],
                                     timeout_s=10)
        self.assertEqual(json.loads(reply)['summary'], 'ok')

    def test_is_loopback_url_table(self):
        yes = ('http://127.0.0.1:8080/v1', '127.0.0.1:8080',
               'http://localhost:11434', 'http://LOCALHOST:8080/v1',
               'http://[::1]:8080/v1', 'http://127.8.8.8:9')
        no = ('http://192.168.1.5:8080', 'http://api.openai.com/v1',
              'http://[::ffff:192.168.1.5]:8080', '', None,
              'http://example.com', 'not a url')
        for u in yes:
            self.assertTrue(_llm._is_loopback_url(u), u)
        for u in no:
            self.assertFalse(_llm._is_loopback_url(u), u)


# ---------------------------------------------------------------------------
# v0.15.1 — process-based discovery ("the service is running on task
# manager"): llama-server's ACTUAL listening ports from the OS process
# table, whatever --port it was given
# ---------------------------------------------------------------------------

_TASKLIST = ('"llama-server.exe","4242","Console","1","123,456 K"\r\n'
             '"ollama.exe","5150","Console","1","456,789 K"\r\n'
             '"llamafile","7171","Console","1","111 K"\r\n'
             '"chrome.exe","9999","Console","1","999,999 K"\r\n'
             '"llama-server.exe","notapid","Console","1","1 K"\r\n')

_NETSTAT = (
    '  TCP    127.0.0.1:8080          0.0.0.0:0              LISTENING       4242\r\n'
    '  TCP    [::]:8080               [::]:0                 LISTENING       4242\r\n'
    '  TCP    0.0.0.0:51717           0.0.0.0:0              LISTENING       4242\r\n'
    '  TCP    127.0.0.1:11434         0.0.0.0:0              LISTENING       5150\r\n'
    '  TCP    127.0.0.1:9999          0.0.0.0:0              LISTENING       7171\r\n'
    '  TCP    192.168.1.5:5177        44.3.2.1:443           ESTABLISHED     4242\r\n'
    '  TCP    127.0.0.1:5188          127.0.0.1:5189         TIME_WAIT       0\r\n')

_SS_OUT = (
    'State   Recv-Q Send-Q Local Address:Port   Peer Address:Port  Process\n'
    'LISTEN  0      4096   127.0.0.1:51717        0.0.0.0:*   '
    'users:(("llama-server",pid=1234,fd=3))\n'
    'LISTEN  0      5      127.0.0.1:3031         0.0.0.0:*   '
    'users:(("python3",pid=555,fd=4))\n')


class TestProcessDiscovery(unittest.TestCase):

    def test_tasklist_parse(self):
        pids = _llm._parse_tasklist_pids(_TASKLIST)
        self.assertEqual(pids, {4242, 7171})  # llama-server + llamafile;
        # NOT ollama/chrome; the non-numeric PID row is skipped

    def test_tasklist_empty_and_garbage(self):
        self.assertEqual(_llm._parse_tasklist_pids(''), set())
        self.assertEqual(_llm._parse_tasklist_pids(None), set())
        self.assertEqual(_llm._parse_tasklist_pids('随机 text\n"普通"'), set())

    def test_netstat_parse_v4_v6_and_noise(self):
        ports = _llm._parse_netstat_listening_ports(_NETSTAT, {4242, 7171})
        # 8080 twice (v4+v6) deduped, 51717 (0.0.0.0 bind), 9999 (llamafile);
        # ollama's 11434 (other pid) + ESTABLISHED/TIME_WAIT noise skipped
        self.assertEqual(ports, [8080, 51717, 9999])

    def test_windows_path_over_fake_commands(self):
        def fake_run(argv, timeout_s=5.0):
            return _TASKLIST if argv[0] == 'tasklist' else _NETSTAT
        with mock.patch.object(_llm, '_run_cmd', side_effect=fake_run):
            ports = _llm._llamacpp_ports_windows()
        self.assertEqual(ports, [8080, 51717, 9999])
        # tasklist dead → no netstat needed → []
        with mock.patch.object(_llm, '_run_cmd', return_value=''):
            self.assertEqual(_llm._llamacpp_ports_windows(), [])

    def test_posix_path_over_fake_ss(self):
        with mock.patch.object(_llm, '_run_cmd', return_value=_SS_OUT):
            ports = _llm._llamacpp_ports_posix()
        self.assertEqual(ports, [51717])

    def test_detect_probes_process_ports_first(self):
        # llama-server on a random port NO guess list contains — found via
        # the RUNNING PROCESS's port (the owner's Task-Manager situation)
        server, root = _start_llamacpp()
        self.addCleanup(_stop, server)
        with mock.patch.object(_llm, 'llamacpp_process_ports',
                               return_value=[server.server_port]):
            probe = _llm.detect_llamacpp(ports=())
        self.assertIsNotNone(probe)
        self.assertTrue(probe['found'])
        self.assertEqual(probe['base_url'], root)
        self.assertEqual(probe.get('via'), 'process')
        self.assertEqual(probe['model'], 'qwen2.5-3b')

    def test_detect_marks_scan_provenance(self):
        server, root = _start_llamacpp()
        self.addCleanup(_stop, server)
        with mock.patch.object(_llm, 'llamacpp_process_ports',
                               return_value=[]):
            probe = _llm.detect_llamacpp(
                ports=(server.server_port,))
        self.assertIsNotNone(probe)
        self.assertEqual(probe.get('via'), 'scan')

    def test_scan_ports_cover_the_common_choices(self):
        ports = _llm.LLAMACPP_SCAN_PORTS
        self.assertEqual(ports[0], 8080)  # the llama-server default first
        for expected in (8080, 8081, 8082, 8083, 8084, 8085, 8000, 5000,
                         5001, 1234, 9000):
            self.assertIn(expected, ports, expected)
        self.assertEqual(len(ports), len(set(ports)))  # no duplicates


# ---------------------------------------------------------------------------
# v0.15.1 — secondary identification: /props-less llama.cpp builds
# fingerprint on /v1/models; other servers are still never misreported
# ---------------------------------------------------------------------------

class TestSecondaryIdentification(unittest.TestCase):

    def setUp(self):
        self.server, self.root = _start_llamacpp()
        self.addCleanup(_stop, self.server)

    def test_owned_by_fingerprint(self):
        self.server.behavior.update(
            {'hide_props': True, 'models': ['qwen2.5-3b'],
             'models_owned_by': 'llama.cpp'})
        probe = _llm.probe_llamacpp(self.root)
        self.assertTrue(probe['found'])
        self.assertEqual(probe['identified_by'], 'models:owned_by')
        self.assertEqual(probe['model'], 'qwen2.5-3b')

    def test_gguf_id_fingerprint(self):
        # no --alias: llama-server advertises the loaded GGUF file itself
        self.server.behavior.update(
            {'hide_props': True,
             'models': ['models/qwen2.5-3b-instruct-q4_k_m.gguf']})
        probe = _llm.probe_llamacpp(self.root)
        self.assertTrue(probe['found'])
        self.assertEqual(probe['identified_by'], 'models:gguf-id')
        self.assertEqual(probe['model'],
                         'models/qwen2.5-3b-instruct-q4_k_m.gguf')

    def test_server_header_fingerprint(self):
        self.server.behavior.update(
            {'hide_props': True, 'models': ['whatever'],
             'server_header': 'llama.cpp'})
        probe = _llm.probe_llamacpp(self.root)
        self.assertTrue(probe['found'])
        self.assertEqual(probe['identified_by'], 'models:server-header')

    def test_plain_openai_not_misreported(self):
        # an OpenAI-ish proxy: no /props, no fingerprints → NOT llama.cpp
        self.server.behavior.update(
            {'hide_props': True, 'models': ['gpt-4o-mini'],
             'models_owned_by': 'organization'})
        probe = _llm.probe_llamacpp(self.root)
        self.assertFalse(probe['found'])
        self.assertIn('404', probe['detail'])

    def test_vllm_style_not_misreported(self):
        # vLLM ids are path-like but not .gguf — must stay unrecognized
        self.server.behavior.update(
            {'hide_props': True,
             'models': ['meta-llama/Meta-Llama-3-8B-Instruct']})
        probe = _llm.probe_llamacpp(self.root)
        self.assertFalse(probe['found'])


# ---------------------------------------------------------------------------
# v0.15.1 — the startup switch policy (pure, Qt-free)
# ---------------------------------------------------------------------------

_FOUND = {'found': True, 'base_url': 'http://127.0.0.1:8080',
          'model': 'qwen2.5-3b', 'models': ['qwen2.5-3b']}


class TestAutodetectDecision(unittest.TestCase):

    def test_switches_when_ollama_dead(self):
        # the owner's exact situation: default provider ollama, no Ollama
        # running, llama-server up → the app catches llama.cpp itself
        cfg = {'llm_provider': 'ollama',
               'llamacpp_api_url': 'http://127.0.0.1:8080/v1'}
        d = _llm.llamacpp_autodetect_decision(cfg, _FOUND, False)
        self.assertTrue(d['switch'])
        self.assertEqual(d['model'], 'qwen2.5-3b')
        self.assertIn('caught automatically', d['message'])
        self.assertIn('Ollama is not running', d['message'])

    def test_hints_when_ollama_alive(self):
        # a WORKING Ollama is never overridden — hint only
        cfg = {'llm_provider': 'ollama'}
        d = _llm.llamacpp_autodetect_decision(cfg, _FOUND, True)
        self.assertFalse(d['switch'])
        self.assertIn('Settings', d['message'])
        self.assertEqual(d['level'], 'info')

    def test_switches_when_cloud_keyless(self):
        cfg = {'llm_provider': 'cloud', 'cloud_api_key': ''}
        d = _llm.llamacpp_autodetect_decision(cfg, _FOUND)
        self.assertTrue(d['switch'])
        self.assertIn('no cloud API key', d['message'])

    def test_hints_when_cloud_keyed(self):
        cfg = {'llm_provider': 'cloud', 'cloud_api_key': 'sk-live-xyz'}
        d = _llm.llamacpp_autodetect_decision(cfg, _FOUND)
        self.assertFalse(d['switch'])

    def test_refresh_when_already_llamacpp(self):
        # already selected: refresh the URL/model, never "switch"
        cfg = {'llm_provider': 'llamacpp',
               'llamacpp_api_url': 'http://127.0.0.1:8080/v1',
               'llamacpp_model': 'qwen2.5-3b'}
        d = _llm.llamacpp_autodetect_decision(cfg, _FOUND, None)
        self.assertFalse(d['switch'])
        self.assertIsNone(d['url'])       # unchanged → nothing to write
        self.assertIsNone(d['model'])
        # …but a MOVED server (different port) refreshes the URL
        moved = dict(_FOUND, base_url='http://127.0.0.1:51717')
        d2 = _llm.llamacpp_autodetect_decision(cfg, moved, None)
        self.assertFalse(d2['switch'])
        self.assertEqual(d2['url'], 'http://127.0.0.1:51717/v1')
        # …and an empty URL field gets filled too
        d3 = _llm.llamacpp_autodetect_decision(
            {'llm_provider': 'llamacpp'}, _FOUND, None)
        self.assertEqual(d3['url'], 'http://127.0.0.1:8080/v1')

    def test_no_override_custom_provider(self):
        cfg = {'llm_provider': 'cloud', 'cloud_api_key': 'sk-x',
               'cloud_api_url': 'http://my-endpoint/v1'}
        d = _llm.llamacpp_autodetect_decision(cfg, _FOUND)
        self.assertFalse(d['switch'])
        self.assertIsNone(d['url'])

    def test_not_found_is_silent(self):
        d = _llm.llamacpp_autodetect_decision(
            {'llm_provider': 'ollama'}, {'found': False}, None)
        self.assertFalse(d['switch'])
        self.assertEqual(d['message'], '')


# ---------------------------------------------------------------------------
# v0.15.1 — the GUI applies the decision (unbound-method tests on a stub:
# no MainWindow is ever instantiated — the wiring stays thin + tested)
# ---------------------------------------------------------------------------

class _FakeCombo:
    def __init__(self, text=''):
        self._items, self._text = [], text

    def clear(self):
        self._items = []

    def addItem(self, n):
        self._items.append(n)

    def insertItem(self, i, n):
        self._items.insert(i, n)

    def setCurrentText(self, t):
        self._text = t

    def setCurrentIndex(self, i):
        self._text = self._items[i]

    def currentText(self):
        return self._text

    def findText(self, t):
        return self._items.index(t) if t in self._items else -1


class _FakeEdit:
    def __init__(self, text=''):
        self._text = text

    def text(self):
        return self._text

    def setText(self, t):
        self._text = t


class _FakeRadio:
    def __init__(self):
        self.checked = False

    def setChecked(self, v):
        self.checked = bool(v)


class _AutodetectStub:
    """Just enough MainWindow surface for _apply_llamacpp_autodetect.
    The model-combo filler is borrowed from the REAL MainWindow (as an
    unbound function) so the apply path is exercised end-to-end."""

    _closing = False
    _fill_llamacpp_models = gui_app.MainWindow._fill_llamacpp_models

    def __init__(self, config):
        self.config = config
        self.llamacpp_model = _FakeCombo(config.get('llamacpp_model', ''))
        self.llamacpp_api_url = _FakeEdit(
            config.get('llamacpp_api_url', ''))
        self.llm_provider_llamacpp = _FakeRadio()
        self.saved = 0
        self.logs = []

    def log_message(self, msg, level="info"):
        self.logs.append((msg, level))

    def save_config(self):
        self.saved += 1


class TestApplyAutodetect(unittest.TestCase):

    def test_switch_applied_end_to_end(self):
        # the owner's golden path: llama-server found (via the fake
        # server), Ollama dead → provider switched, URL + model filled,
        # config saved, success logged
        server, root = _start_llamacpp()
        self.addCleanup(_stop, server)
        config = {'llm_provider': 'ollama',
                  'ollama': {'base_url': 'http://127.0.0.1:11434'},
                  'llamacpp_api_url': 'http://127.0.0.1:8080/v1',
                  'llamacpp_model': ''}
        probe = _llm.probe_llamacpp(root)
        decision = _llm.llamacpp_autodetect_decision(config, probe, False)
        stub = _AutodetectStub(config)
        gui_app.MainWindow._apply_llamacpp_autodetect(
            stub, {'probe': probe, 'decision': decision})
        self.assertEqual(config['llm_provider'], 'llamacpp')
        self.assertTrue(stub.llm_provider_llamacpp.checked)
        self.assertEqual(config['llamacpp_api_url'], root + '/v1')
        self.assertEqual(stub.llamacpp_api_url.text(), root + '/v1')
        self.assertEqual(config['llamacpp_model'], 'qwen2.5-3b')
        self.assertEqual(stub.llamacpp_model.currentText(), 'qwen2.5-3b')
        self.assertEqual(stub.saved, 1)
        self.assertTrue(any('caught automatically' in m for m, _ in stub.logs))
        self.assertTrue(any('saved to Settings' in m for m, _ in stub.logs))

    def test_hint_only_changes_nothing(self):
        # working Ollama → hint line only; nothing switched, nothing saved
        server, root = _start_llamacpp()
        self.addCleanup(_stop, server)
        config = {'llm_provider': 'ollama',
                  'llamacpp_api_url': 'http://127.0.0.1:8080/v1',
                  'llamacpp_model': 'old-choice'}
        probe = _llm.probe_llamacpp(root)
        decision = _llm.llamacpp_autodetect_decision(config, probe, True)
        stub = _AutodetectStub(config)
        gui_app.MainWindow._apply_llamacpp_autodetect(
            stub, {'probe': probe, 'decision': decision})
        self.assertEqual(config['llm_provider'], 'ollama')
        self.assertFalse(stub.llm_provider_llamacpp.checked)
        self.assertEqual(config['llamacpp_model'], 'old-choice')
        self.assertEqual(stub.saved, 0)
        self.assertEqual(len(stub.logs), 1)  # the hint, nothing else

    def test_missing_server_warns_llamacpp_provider(self):
        # provider IS llama.cpp but no server → one actionable warning
        config = {'llm_provider': 'llamacpp'}
        stub = _AutodetectStub(config)
        gui_app.MainWindow._apply_llamacpp_autodetect(
            stub, {'probe': {'found': False}, 'decision': None})
        self.assertEqual(len(stub.logs), 1)
        self.assertEqual(stub.logs[0][1], 'warning')
        self.assertIn('llama-server', stub.logs[0][0])
        self.assertEqual(stub.saved, 0)

    def test_missing_server_silent_for_other_providers(self):
        config = {'llm_provider': 'ollama'}
        stub = _AutodetectStub(config)
        gui_app.MainWindow._apply_llamacpp_autodetect(
            stub, {'probe': {'found': False}, 'decision': None})
        self.assertEqual(stub.logs, [])
        self.assertEqual(stub.saved, 0)


class TestStartupWiring(unittest.TestCase):

    def test_signal_and_methods_wired(self):
        import inspect
        self.assertTrue(hasattr(gui_app.MainWindow,
                                '_llamacpp_autodetect_signal'))
        for name in ('_startup_llamacpp_autodetect',
                     '_apply_llamacpp_autodetect'):
            self.assertTrue(callable(getattr(gui_app.MainWindow, name)),
                            name)
        # __init__ schedules the startup probe
        src = inspect.getsource(gui_app.MainWindow.__init__)
        self.assertIn('_startup_llamacpp_autodetect', src)
        # the detector actually probes: configured URL first, then detect
        det = inspect.getsource(
            gui_app.MainWindow._startup_llamacpp_autodetect)
        self.assertIn('probe_llamacpp', det)
        self.assertIn('detect_llamacpp', det)

    def test_guard_done_flag_skips_detection(self):
        # once per session: a second call must not even spawn the thread
        started = []

        class _RecThread:
            def __init__(self, *a, **k):
                started.append(k)

            def start(self):
                pass

        class _Done:
            _llamacpp_autodetect_done = True
            _closing = False
            # NOTE: no `config` — any detection attempt would need it

        import types
        orig = gui_app.threading
        gui_app.threading = types.SimpleNamespace(Thread=_RecThread)
        try:
            gui_app.MainWindow._startup_llamacpp_autodetect(_Done())
            done = _Done()
            done._llamacpp_autodetect_done = False
            done._closing = True   # closing guard: also no thread
            gui_app.MainWindow._startup_llamacpp_autodetect(done)
        finally:
            gui_app.threading = orig
        self.assertEqual(started, [])


if __name__ == '__main__':
    unittest.main()
