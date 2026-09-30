#!/usr/bin/env python3
"""
test_phase4.py — Phase 4 (v0.13.0): LLM backends.

Covers (SPEC §6 Phase 4 + acceptance):
  * openai_chat against a LOCAL stdlib http.server: success (with
    response_format + Bearer header), a server that REJECTS
    response_format (clean fallback, memoized — only one doomed attempt
    per process), timeout (wall-clock TimeoutError, the same exception
    type the Ollama path raises), malformed JSON body, error objects,
    no-choices bodies, connection refused
  * /v1/models pre-flight: openai_list_models + preflight_openai
    (listed / not listed / route hidden — warn, never block)
  * ollama_chat: explicit options.num_ctx + format json on every call,
    no options when num_ctx is None/0, the over-budget warning
    (estimate_tokens), both ollama-py response shapes
  * the REAL ollama client library against a fake local Ollama server
    (protocol truth: options.num_ctx actually reaches the wire)
  * per-task model overrides: resolve_task_model + the real
    ProcessingWorker routers — _llm_analyze (models.analyze) and
    _run_website_phase (models.classify / models.analyze), attempt-1
    JSON mode on both providers
  * config compatibility: CONFIG_EXAMPLE carries the new keys,
    storage.merge_config defaults them for old configs and keeps
    user-set values
  * relabel: the one true CLOUD_PROVIDER_LABEL constant, used by the
    GUI radio + settings group, present in the CLI wizard and README
  * the golden runner backends: live_llm (OpenAI-compatible) and
    live_llm_ollama against the fake servers

Headless-safe: QT_QPA_PLATFORM=offscreen, no GUI is ever shown, no
network beyond 127.0.0.1.
"""

import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

import gitcurator.gui.app as gui_app
from gitcurator.core import dryrun
from gitcurator.core import llm_client as _llm
from gitcurator.core import website_pipeline as _wp
from gitcurator.core.storage import merge_config
from gitcurator.constants import CONFIG_EXAMPLE
from gitcurator.core.web_fetch import FetchResult

# Reuse the Phase-2 scriptable fakes (same contract, task-aware).
from tests.test_phase2 import _FakeFetch, _FakeLLM


# ---------------------------------------------------------------------------
# Fake OpenAI-compatible server (stdlib http.server)
# ---------------------------------------------------------------------------

class _FakeOpenAIHandler(BaseHTTPRequestHandler):
    """One handler, configurable through server.state + server.behavior.

    behavior keys:
      content_fn(body_dict) -> str   answer per request (default: 'ok')
      reject_response_format         400 whenever response_format is sent
      always_400                     400 no matter what
      malformed                      200 + a non-JSON body
      error_object                   200 + {"error": {...}}
      no_choices                     200 + {"choices": []}
      sleep_s                        stall the handler (timeout tests)
      models                         list for GET /v1/models
      hide_models                    404 for GET /v1/models
    """
    behavior = {}
    state = {'requests': []}

    def log_message(self, *args):  # silence the test output
        pass

    def _send(self, code, payload, raw=False):
        data = payload if raw else json.dumps(payload).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip('/').endswith('/models'):
            if self.server.behavior.get('hide_models'):
                self._send(404, {'error': 'not found'})
                return
            names = self.server.behavior.get('models', ['fake-model'])
            self._send(200, {'data': [{'id': n} for n in names]})
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
            'path': self.path,
            'body': payload,
            'auth': self.headers.get('Authorization', ''),
        })
        b = self.server.behavior
        if b.get('sleep_s'):
            time.sleep(float(b['sleep_s']))
        if b.get('always_400'):
            self._send(400, {'error': {'message': 'bad request'}})
            return
        if b.get('reject_response_format') and \
                'response_format' in payload:
            self._send(400, {'error': {
                'message': "response_format is not supported"}})
            return
        if b.get('malformed'):
            self._send(200, b'{"choices": [{"mes', raw=True)
            return
        if b.get('error_object'):
            self._send(200, {'error': {'message': 'boom from server'}})
            return
        if b.get('no_choices'):
            self._send(200, {'choices': []})
            return
        content = b.get('content_fn', lambda p: 'ok')(payload)
        self._send(200, {'choices': [{'message': {'content': content}}]})


def _start_openai(behavior=None):
    server = HTTPServer(('127.0.0.1', 0), _FakeOpenAIHandler)
    server.behavior = dict(behavior or {})
    server.state = {'requests': []}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f'http://127.0.0.1:{server.server_port}/v1'


# ---------------------------------------------------------------------------
# Fake Ollama server (the real ollama client library speaks to it)
# ---------------------------------------------------------------------------

class _FakeOllamaHandler(BaseHTTPRequestHandler):
    behavior = {}
    state = {'requests': []}

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode('utf-8'))
        except Exception:
            payload = {}
        self.server.state['requests'].append(payload)
        data = json.dumps({
            'model': payload.get('model', 'x'),
            'message': {'role': 'assistant',
                        'content': '{"category": "Design"}'},
        }).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _start_ollama():
    server = HTTPServer(('127.0.0.1', 0), _FakeOllamaHandler)
    server.behavior = {}
    server.state = {'requests': []}
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f'http://127.0.0.1:{server.server_port}'


# ---------------------------------------------------------------------------
# openai_chat — success, JSON mode, auth
# ---------------------------------------------------------------------------

class TestOpenAIChatSuccess(unittest.TestCase):

    def setUp(self):
        _llm.reset_json_mode_memo()
        self.server, self.base = _start_openai()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(_llm.reset_json_mode_memo)

    def test_success_plain(self):
        out = _llm.openai_chat(self.base, 'k', 'm',
                               [{'role': 'user', 'content': 'hi'}], 10)
        self.assertEqual(out, 'ok')
        req = self.server.state['requests'][-1]
        self.assertEqual(req['body']['model'], 'm')
        self.assertEqual(req['auth'], 'Bearer k')
        self.assertNotIn('response_format', req['body'])  # off by default

    def test_json_mode_sends_response_format(self):
        _llm.openai_chat(self.base, 'k', 'm',
                         [{'role': 'user', 'content': 'hi'}], 10,
                         json_mode=True)
        body = self.server.state['requests'][-1]['body']
        self.assertEqual(body['response_format'], {'type': 'json_object'})

    def test_empty_content_is_a_valid_answer(self):
        self.server.behavior['content_fn'] = lambda p: ''
        out = _llm.openai_chat(self.base, 'k', 'm',
                               [{'role': 'user', 'content': 'hi'}], 10)
        self.assertEqual(out, '')

    def test_empty_key_sends_no_auth_header(self):
        _llm.openai_chat(self.base, '', 'm',
                         [{'role': 'user', 'content': 'hi'}], 10)
        self.assertEqual(self.server.state['requests'][-1]['auth'], '')


# ---------------------------------------------------------------------------
# openai_chat — the response_format fallback
# ---------------------------------------------------------------------------

class TestOpenAIChatFallback(unittest.TestCase):

    def setUp(self):
        _llm.reset_json_mode_memo()
        self.server, self.base = _start_openai(
            {'reject_response_format': True})
        self.addCleanup(self.server.shutdown)
        self.addCleanup(_llm.reset_json_mode_memo)

    def test_rejection_falls_back_cleanly(self):
        out = _llm.openai_chat(self.base, 'k', 'm',
                               [{'role': 'user', 'content': 'hi'}], 10,
                               json_mode=True)
        self.assertEqual(out, 'ok')
        # first attempt WITH response_format, retry WITHOUT
        bodies = [r['body'] for r in self.server.state['requests']]
        self.assertIn('response_format', bodies[0])
        self.assertNotIn('response_format', bodies[1])

    def test_fallback_is_memoized(self):
        _llm.openai_chat(self.base, 'k', 'm',
                         [{'role': 'user', 'content': 'hi'}], 10,
                         json_mode=True)
        n_after_first = len(self.server.state['requests'])
        # second call: NO doomed attempt — the memo skips response_format
        _llm.openai_chat(self.base, 'k', 'm',
                         [{'role': 'user', 'content': 'hi'}], 10,
                         json_mode=True)
        self.assertEqual(len(self.server.state['requests']),
                         n_after_first + 1)
        self.assertNotIn('response_format',
                         self.server.state['requests'][-1]['body'])
        self.assertTrue(_llm.json_mode_rejected(self.base))

    def test_other_400_without_json_mode_raises(self):
        self.server.behavior['always_400'] = True
        with self.assertRaises(_llm.CloudLLMHTTPError) as cm:
            _llm.openai_chat(self.base, 'k', 'm',
                             [{'role': 'user', 'content': 'hi'}], 10)
        self.assertEqual(cm.exception.code, 400)

    def test_400_with_json_mode_that_keeps_failing_raises(self):
        self.server.behavior['always_400'] = True
        with self.assertRaises(_llm.CloudLLMHTTPError):
            _llm.openai_chat(self.base, 'k', 'm',
                             [{'role': 'user', 'content': 'hi'}], 10,
                             json_mode=True)
        # both attempts happened (with, then without)
        self.assertEqual(len(self.server.state['requests']), 2)


# ---------------------------------------------------------------------------
# openai_chat — timeout / malformed / unreachable
# ---------------------------------------------------------------------------

class TestOpenAIChatFailures(unittest.TestCase):

    def setUp(self):
        _llm.reset_json_mode_memo()
        self.addCleanup(_llm.reset_json_mode_memo)

    def test_timeout_raises_timeouterror(self):
        server, base = _start_openai({'sleep_s': 5})
        self.addCleanup(server.shutdown)
        t0 = time.monotonic()
        with self.assertRaises(TimeoutError):
            _llm.openai_chat(base, 'k', 'm',
                             [{'role': 'user', 'content': 'hi'}], 1)
        self.assertLess(time.monotonic() - t0, 4)  # wall clock honored

    def test_malformed_body(self):
        server, base = _start_openai({'malformed': True})
        self.addCleanup(server.shutdown)
        with self.assertRaises(_llm.CloudLLMBadResponse) as cm:
            _llm.openai_chat(base, 'k', 'm',
                             [{'role': 'user', 'content': 'hi'}], 10)
        self.assertIn('non-JSON', str(cm.exception))

    def test_error_object_body(self):
        server, base = _start_openai({'error_object': True})
        self.addCleanup(server.shutdown)
        with self.assertRaises(_llm.CloudLLMBadResponse) as cm:
            _llm.openai_chat(base, 'k', 'm',
                             [{'role': 'user', 'content': 'hi'}], 10)
        self.assertIn('boom from server', str(cm.exception))

    def test_no_choices_body(self):
        server, base = _start_openai({'no_choices': True})
        self.addCleanup(server.shutdown)
        with self.assertRaises(_llm.CloudLLMBadResponse):
            _llm.openai_chat(base, 'k', 'm',
                             [{'role': 'user', 'content': 'hi'}], 10)

    def test_connection_refused(self):
        # port 9 (discard) — nothing listens there in the sandbox
        with self.assertRaises(_llm.CloudLLMError):
            _llm.openai_chat('http://127.0.0.1:9/v1', 'k', 'm',
                             [{'role': 'user', 'content': 'hi'}], 5)

    def test_empty_url(self):
        with self.assertRaises(_llm.CloudLLMError):
            _llm.openai_chat('', 'k', 'm',
                             [{'role': 'user', 'content': 'hi'}], 5)


# ---------------------------------------------------------------------------
# /v1/models pre-flight
# ---------------------------------------------------------------------------

class TestModelsPreflight(unittest.TestCase):

    def setUp(self):
        _llm.reset_json_mode_memo()
        self.addCleanup(_llm.reset_json_mode_memo)

    def test_model_listed(self):
        server, base = _start_openai({'models': ['a', 'b']})
        self.addCleanup(server.shutdown)
        ok, msg, listed = _llm.preflight_openai(base, 'k', 'a')
        self.assertTrue(ok)
        self.assertTrue(listed)

    def test_model_not_listed(self):
        server, base = _start_openai({'models': ['a', 'b']})
        self.addCleanup(server.shutdown)
        ok, msg, listed = _llm.preflight_openai(base, 'k', 'zzz')
        self.assertTrue(ok)
        self.assertFalse(listed)

    def test_hidden_route_is_not_an_error(self):
        server, base = _start_openai({'hide_models': True})
        self.addCleanup(server.shutdown)
        ok, msg, listed = _llm.preflight_openai(base, 'k', 'a')
        self.assertFalse(ok)
        self.assertIsNone(listed)

    def test_unreachable_endpoint(self):
        ok, msg, listed = _llm.preflight_openai(
            'http://127.0.0.1:9/v1', 'k', 'a')
        self.assertFalse(ok)
        self.assertIsNone(listed)


# ---------------------------------------------------------------------------
# ollama_chat — the explicit context window
# ---------------------------------------------------------------------------

class _FakeOllamaClient:
    """Records chat kwargs; answers in either ollama-py shape."""

    def __init__(self, shape='new'):
        self.calls = []
        self.shape = shape

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        if self.shape == 'new':
            class _M:
                content = '{"category": "Design"}'
            class _R:
                message = _M()
            return _R()
        return {'message': {'content': '{"category": "Design"}'}}


class TestOllamaChat(unittest.TestCase):

    def test_num_ctx_and_format_on_every_call(self):
        client = _FakeOllamaClient()
        _llm.ollama_chat(client, 'm',
                         [{'role': 'user', 'content': 'hi'}], 10,
                         num_ctx=8192)
        self.assertEqual(client.calls[0]['options'], {'num_ctx': 8192})
        self.assertEqual(client.calls[0]['format'], 'json')

    def test_no_options_when_num_ctx_off(self):
        client = _FakeOllamaClient()
        _llm.ollama_chat(client, 'm',
                         [{'role': 'user', 'content': 'hi'}], 10,
                         num_ctx=None)
        _llm.ollama_chat(client, 'm',
                         [{'role': 'user', 'content': 'hi'}], 10,
                         num_ctx=0)
        for call in client.calls:
            self.assertNotIn('options', call)

    def test_json_mode_off(self):
        client = _FakeOllamaClient()
        _llm.ollama_chat(client, 'm',
                         [{'role': 'user', 'content': 'hi'}], 10,
                         json_mode=False, num_ctx=None)
        self.assertNotIn('format', client.calls[0])

    def test_over_budget_warning_fires(self):
        client = _FakeOllamaClient()
        warnings = []
        _llm.ollama_chat(client, 'm',
                         [{'role': 'user', 'content': 'x' * 40_000}], 10,
                         num_ctx=512, on_warn=warnings.append)
        self.assertEqual(len(warnings), 1)
        self.assertIn('512', warnings[0])

    def test_no_warning_when_within_budget(self):
        client = _FakeOllamaClient()
        warnings = []
        _llm.ollama_chat(client, 'm',
                         [{'role': 'user', 'content': 'x' * 100}], 10,
                         num_ctx=8192, on_warn=warnings.append)
        self.assertEqual(warnings, [])

    def test_both_response_shapes(self):
        for shape in ('new', 'old'):
            client = _FakeOllamaClient(shape=shape)
            out = _llm.ollama_chat(client, 'm',
                                   [{'role': 'user', 'content': 'hi'}], 10)
            self.assertEqual(out, '{"category": "Design"}')


class TestOllamaRealHTTP(unittest.TestCase):
    """The REAL ollama client library against a fake local server —
    proves options.num_ctx and format actually reach the wire."""

    def test_real_client_sends_num_ctx(self):
        import ollama
        server, host = _start_ollama()
        self.addCleanup(server.shutdown)
        client = ollama.Client(host=host)
        out = _llm.ollama_chat(client, 'llama3',
                               [{'role': 'user', 'content': 'hi'}], 10,
                               num_ctx=4096)
        self.assertEqual(out, '{"category": "Design"}')
        payload = server.state['requests'][-1]
        self.assertEqual(payload['options'], {'num_ctx': 4096})
        self.assertEqual(payload['format'], 'json')
        self.assertEqual(payload['model'], 'llama3')


# ---------------------------------------------------------------------------
# Per-task model overrides
# ---------------------------------------------------------------------------

class TestResolveTaskModel(unittest.TestCase):

    def test_override_wins(self):
        cfg = {'models': {'classify': 'big-ctx', 'analyze': ''}}
        self.assertEqual(
            _llm.resolve_task_model(cfg, 'classify', 'default'), 'big-ctx')
        self.assertEqual(
            _llm.resolve_task_model(cfg, 'analyze', 'default'), 'default')

    def test_missing_or_empty_keeps_default(self):
        self.assertEqual(
            _llm.resolve_task_model({}, 'classify', 'd'), 'd')
        self.assertEqual(
            _llm.resolve_task_model({'models': {}}, 'classify', 'd'), 'd')
        self.assertEqual(
            _llm.resolve_task_model(
                {'models': {'classify': '  '}}, 'classify', 'd'), 'd')

    def test_unknown_task_and_bad_config(self):
        self.assertEqual(
            _llm.resolve_task_model({'models': {'classify': 'x'}},
                                    None, 'd'), 'd')
        self.assertEqual(
            _llm.resolve_task_model({'models': 'nope'}, 'classify', 'd'),
            'd')
        self.assertEqual(
            _llm.resolve_task_model(None, 'classify', 'd'), 'd')


class TestEstimateTokens(unittest.TestCase):

    def test_scales_with_content(self):
        small = _llm.estimate_tokens([{'content': 'x' * 400}])
        big = _llm.estimate_tokens([{'content': 'x' * 4000}])
        self.assertGreater(big, small * 5)
        self.assertEqual(_llm.estimate_tokens([]), 0)


# ---------------------------------------------------------------------------
# Config compatibility
# ---------------------------------------------------------------------------

class TestConfigCompat(unittest.TestCase):

    def test_example_carries_new_keys(self):
        self.assertEqual(CONFIG_EXAMPLE['llm_num_ctx'], 8192)
        self.assertEqual(CONFIG_EXAMPLE['models'],
                         {'classify': '', 'analyze': ''})

    def test_old_config_gets_defaults(self):
        merged = merge_config(
            {'llm_num_ctx': CONFIG_EXAMPLE['llm_num_ctx'],
             'models': dict(CONFIG_EXAMPLE['models'])},
            {'vault_path': '/x'})  # an old config: none of the new keys
        self.assertEqual(merged['llm_num_ctx'], 8192)
        self.assertEqual(merged['models'], {'classify': '', 'analyze': ''})
        self.assertEqual(merged['vault_path'], '/x')

    def test_user_values_survive(self):
        merged = merge_config(
            {'llm_num_ctx': 8192, 'models': {'classify': '', 'analyze': ''}},
            {'llm_num_ctx': 4096,
             'models': {'classify': 'llama3:70b', 'analyze': ''}})
        self.assertEqual(merged['llm_num_ctx'], 4096)
        self.assertEqual(merged['models']['classify'], 'llama3:70b')


# ---------------------------------------------------------------------------
# The real worker routers
# ---------------------------------------------------------------------------

class _Log:
    """log_message signal stand-in: emit(msg, level)."""

    def __init__(self):
        self.lines = []

    def emit(self, msg, level='info'):
        self.lines.append((level, msg))


class TestWorkerRouters(unittest.TestCase):
    """_llm_analyze and _run_website_phase honor the per-task overrides,
    attempt-1 JSON mode and the shared helpers — through the REAL
    ProcessingWorker code with _call_cloud_llm replaced by a recorder."""

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix='p4worker-')
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self._orig_cloud = gui_app.ProcessingWorker._call_cloud_llm
        self._orig_app_dir = _wp.APP_DIR
        _wp.APP_DIR = self.tmp  # WebsiteStateDB() defaults land in tmp
        self.addCleanup(self._restore)

    def _restore(self):
        # v0.15.0: staticmethod re-wrap — class access unwraps the original
        # into a plain function, and assigning that back would turn every
        # LATER self._call_cloud_llm(...) into a bound call (self passed as
        # api_url). Latent until test_llamacpp's end-to-end router test ran
        # after these recorder tests in one process.
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

    def test_llm_analyze_uses_analyze_override_and_json_mode(self):
        seen = []

        def recorder(api_url, api_key, model, messages,
                     json_mode=False, timeout_s=300, num_ctx=None,
                     max_output_tokens=None, on_warn=None):
            seen.append((model, json_mode))
            return json.dumps({
                'summary': 'ok', 'how_it_works': 'ok',
                'core_value': 'ok', 'features': ['a'],
                'difference': 'ok', 'category': 'Uncategorized',
                'confidence': 50, 'tags': []})

        gui_app.ProcessingWorker._call_cloud_llm = staticmethod(recorder)
        cfg = {'llm_provider': 'cloud',
               'cloud_api_url': 'http://127.0.0.1:9/v1',
               'cloud_api_key': '',
               'cloud_model': 'base-model',
               'models': {'analyze': 'analyze-override'}}
        w = self._worker(cfg)
        result = w._llm_analyze(
            None, 'base-model', 'repo-x', 'desc', [], 'owner', 1, 2)
        self.assertEqual(seen[0], ('analyze-override', True))
        self.assertEqual(result['summary'], 'ok')

    def test_website_phase_routes_classify_and_analyze(self):
        seen = []

        def recorder(api_url, api_key, model, messages,
                     json_mode=False, timeout_s=300, num_ctx=None,
                     on_warn=None):
            text = messages[0]['content'] if messages else ''
            seen.append((model, json_mode))
            if 'filing a website into a personal library' in text:
                return json.dumps({'category': 'Design',
                                   'confidence': 'high', 'reason': 'x'})
            if 'was filed under' in text:
                return json.dumps({'subcategory': 'none',
                                   'confidence': 'high'})
            return json.dumps({
                'name': 'Test Site', 'one_line': 'A site.',
                'core_offerings': ['a'], 'standout_feature': '',
                'best_used_for': 'x', 'pricing': 'unknown',
                'login_required': 'unknown', 'similar_tools': [],
                'tags': ['t'], 'confidence': 'high'})

        gui_app.ProcessingWorker._call_cloud_llm = staticmethod(recorder)
        vault = os.path.join(self.tmp, 'websites')
        os.makedirs(vault, exist_ok=True)
        cfg = {'llm_provider': 'cloud',
               'cloud_api_url': 'http://127.0.0.1:9/v1',
               'cloud_api_key': '',
               'cloud_model': 'base-model',
               'models': {'classify': 'classifier-x',
                          'analyze': 'analyzer-y'},
               'pipelines': {'websites': True},
               'website_vault_path': vault,
               'web_fetch_timeout_s': 5, 'web_domain_delay_s': 0,
               'web_fetch_max_bytes': 100000}
        w = self._worker(cfg)
        w._non_github_urls = ['https://example.com/p4-tool']

        # canned fetch (no network): a normal-looking page
        def fake_fetch(url, *args, **kwargs):
            return FetchResult(
                url=url, final_url=url, status='full', reason='',
                http_status=200, content_type='text/html', charset='utf-8',
                body=b'<html><body><p>A design tool page.</p></body></html>',
                text='<html><body><p>A design tool page.</p></body></html>',
                elapsed_s=0.0)

        import gitcurator.core.web_fetch as _wf
        orig_fetch = _wf.fetch_url
        _wf.fetch_url = fake_fetch
        try:
            summary = w._run_website_phase(None, None,
                                           ollama_client=None,
                                           ollama_model='ignored')
        finally:
            _wf.fetch_url = orig_fetch

        self.assertIsNotNone(summary)
        models = {m for m, _ in seen}
        self.assertEqual(models, {'classifier-x', 'analyzer-y'})
        self.assertTrue(all(json_mode for _, json_mode in seen),
                        'every call must run in JSON mode')
        # the note landed in the taxonomy-shaped vault tree
        counters = summary['counters']
        self.assertEqual(counters['processed'], 1)
        rel_note = None
        for root, _dirs, files in os.walk(vault):
            for f in files:
                if f.endswith('.md'):
                    rel_note = os.path.relpath(
                        os.path.join(root, f), vault)
        self.assertTrue(rel_note and rel_note.startswith('Design'),
                        f'note not filed under Design/: {rel_note}')


def _redirect_db(tmp):  # retained for reference; the APP_DIR patch above
    """(unused — kept as documentation of the alternative approach)"""
    return None


# ---------------------------------------------------------------------------
# Relabel — one constant, used everywhere the option is named
# ---------------------------------------------------------------------------

class TestRelabel(unittest.TestCase):

    # v0.23.0 — the parenthetical is gone (owner request: "remove
    # parenthesis"); the examples live in tooltips now.
    LABEL = 'OpenAI-compatible endpoint'

    def test_the_constant(self):
        self.assertEqual(_llm.CLOUD_PROVIDER_LABEL, self.LABEL)

    def _source(self, module_name):
        import inspect
        import importlib
        mod = importlib.import_module(module_name)
        with open(inspect.getsourcefile(mod), encoding='utf-8') as f:
            return f.read()

    def test_gui_uses_the_constant(self):
        src = self._source('gitcurator.gui.app')
        # the radio button AND the settings group box
        self.assertGreaterEqual(
            src.count('_llm_client.CLOUD_PROVIDER_LABEL'), 2)
        self.assertNotIn('Cloud API (OpenAI compatible)', src)

    def test_cli_names_the_option(self):
        src = self._source('gitcurator.cli')
        self.assertIn('OpenAI-compatible endpoint', src)
        self.assertIn('llama.cpp', src)

    def test_readme_names_the_option(self):
        readme = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), 'README.md')
        with open(readme, encoding='utf-8') as f:
            src = f.read()
        self.assertIn('OpenAI-compatible endpoint', src)


# ---------------------------------------------------------------------------
# The golden runner backends
# ---------------------------------------------------------------------------

class _OllamaFlakyHandler(BaseHTTPRequestHandler):
    """502s the first N POSTs (transient upstream errors), then works."""
    fail_left = 2
    count = {'n': 0}

    def log_message(self, *args):
        pass

    def _raw(self, code, payload):
        data = json.dumps(payload).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        self.rfile.read(length)
        _OllamaFlakyHandler.count['n'] += 1
        if _OllamaFlakyHandler.count['n'] <= _OllamaFlakyHandler.fail_left:
            self._raw(502, {'error': 'upstream busy'})
            return
        self._raw(200, {'model': 'x', 'done': True, 'message': {
            'role': 'assistant', 'content': '{"category": "Design"}'}})


class TestGoldenBackends(unittest.TestCase):

    def setUp(self):
        _llm.reset_json_mode_memo()
        self.addCleanup(_llm.reset_json_mode_memo)

    def test_live_llm_openai_backend(self):
        from gitcurator.tools.run_golden_websites import live_llm
        server, base = _start_openai(
            {'content_fn': lambda p: '{"category": "Design"}'})
        self.addCleanup(server.shutdown)
        llm = live_llm(base, 'k', 'm', 10)
        self.assertEqual(llm([{'role': 'user', 'content': 'hi'}],
                             task='classify'),
                         '{"category": "Design"}')
        body = server.state['requests'][-1]['body']
        self.assertEqual(body['response_format'], {'type': 'json_object'})

    def test_live_llm_ollama_backend(self):
        from gitcurator.tools.run_golden_websites import live_llm_ollama
        server, host = _start_ollama()
        self.addCleanup(server.shutdown)
        llm = live_llm_ollama(host, 'llama3', 10, num_ctx=2048)
        out = llm([{'role': 'user', 'content': 'hi'}], task='analyze')
        self.assertEqual(out, '{"category": "Design"}')
        payload = server.state['requests'][-1]
        self.assertEqual(payload['options'], {'num_ctx': 2048})

    def test_live_llm_ollama_retries_transient_5xx(self):
        """Parity with the openai backend (found in the v0.13.0 backends
        comparison run): one upstream hiccup must not send the link to
        _review — transient 5xx/429 errors are retried, twice."""
        from unittest import mock
        from gitcurator.tools.run_golden_websites import live_llm_ollama
        server = HTTPServer(('127.0.0.1', 0), _OllamaFlakyHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        llm = live_llm_ollama(
            f'http://127.0.0.1:{server.server_port}', 'llama3', 10)
        with mock.patch('time.sleep') as slept:
            out = llm([{'role': 'user', 'content': 'hi'}])
        self.assertEqual(out, '{"category": "Design"}')
        self.assertEqual(_OllamaFlakyHandler.count['n'], 3)
        self.assertEqual(slept.call_count, 2)

    def test_offline_fake_accepts_task(self):
        from gitcurator.tools.run_golden_websites import offline_llm
        entries = [{'url': 'https://example.com/x',
                    'expected_category': 'Design',
                    'expected_subcategory': '',
                    'notes': 'n'}]
        llm = offline_llm(entries)
        out = llm([{'role': 'user',
                    'content': 'filing a website into a personal library\n'
                               'Website: https://example.com/x'}],
                  task='classify')
        self.assertEqual(json.loads(out)['category'], 'Design')


# ---------------------------------------------------------------------------
# The deferred Phase-3 few-shot hook: corrections as classifier examples
# ---------------------------------------------------------------------------

class TestCorrectionExamples(unittest.TestCase):
    """w01 carries PAST_CORRECTIONS; the pipeline builds it from the
    note_state corrections log (this URL's history first, then the
    owner's recent moves)."""

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix='p4corr-')
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.vault = os.path.join(self.tmp, 'websites')
        os.makedirs(self.vault, exist_ok=True)
        self.db = _wp.WebsiteStateDB(
            db_path=os.path.join(self.tmp, 'cache.db'))
        self.in_vault = set()
        self.logs = []

    def make_pipeline(self, llm, note_state_db=None):
        cfg = {'website_vault_path': self.vault,
               'web_domain_delay_s': 0}
        return _wp.WebsitePipeline(
            config=cfg, llm_call=llm,
            vault_index_has=lambda u: u in self.in_vault,
            state=self.db, fetch_fn=_FakeFetch(),
            note_state_db=note_state_db,
            log=lambda m, l='info': self.logs.append((l, m)))

    def test_no_db_means_none(self):
        pipe = self.make_pipeline(_FakeLLM(), note_state_db=None)
        self.assertEqual(pipe._correction_examples('https://x.com/a'),
                         '(none)')

    def test_own_history_and_recent_moves(self):
        from gitcurator.core import note_state as _ns
        ns = _ns.NoteStateDB(os.path.join(self.tmp, 'ns.db'))
        self.addCleanup(ns.close)
        ns.log_correction(_ns.VAULT_WEBSITES, 'https://x.com/a',
                          'Design', 'AI Tools')
        ns.log_correction(_ns.VAULT_WEBSITES, 'https://y.com/b',
                          'Design', 'Web Tools')
        ns.log_correction(_ns.VAULT_WEBSITES, 'https://y.com/b',
                          'Design', 'Web Tools')  # duplicate pair
        pipe = self.make_pipeline(_FakeLLM(), note_state_db=ns)
        out = pipe._correction_examples('https://x.com/a')
        self.assertIn('this exact website', out)
        self.assertIn('Design', out)
        self.assertIn('AI Tools', out)
        self.assertIn('https://y.com/b', out)
        # the duplicate (Design -> Web Tools) pair appears once
        self.assertEqual(out.count('Web Tools'), 1)

    def test_prompt_carries_the_slot(self):
        from gitcurator.core import note_state as _ns
        ns = _ns.NoteStateDB(os.path.join(self.tmp, 'ns.db'))
        self.addCleanup(ns.close)
        ns.log_correction(_ns.VAULT_WEBSITES, 'https://slot.test/a',
                          'Design', 'AI Tools')
        fake = _FakeLLM()
        pipe = self.make_pipeline(fake, note_state_db=ns)
        r = pipe.process_link('https://slot.test/a')
        self.assertEqual(r['outcome'], 'processed')
        # the w01 prompt the model saw: template FILLED — the examples are
        # in, the raw slot marker is gone
        w01 = fake.calls[0]
        self.assertNotIn('{{PAST_CORRECTIONS}}', w01)
        self.assertIn('this exact website', w01)
        self.assertIn('AI Tools', w01)


if __name__ == '__main__':
    unittest.main()
