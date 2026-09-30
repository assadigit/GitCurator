#!/usr/bin/env python3
"""test_v0230.py — the owner's five-request UI/LLM overhaul (v0.23.0).

  1. Main view: the Detect & Set Ollama / llama.cpp buttons are GONE
     (Settings → 🧠 LLM keeps them) — covered by test_detectset.
  2. Test Connection is a MODAL with per-subsystem rows (loading → ✅/⚠️/❌)
     and a Start Syncing button that unlocks (green) only when ALL rows
     are ok — ConnectionTestDialog lifecycle here.
  3. The About Me Wizard opens in BOTH themes — themed QDialog backgrounds
     (the old dialog rode the SYSTEM palette: app-light + OS-dark painted
     dark text on a dark window = "only opens in dark mode").
  4. LLM tab: TWO radios (Locally hosted LLM model / Cloud API model);
     local → the detect buttons; cloud → API URL/key/model covering BOTH
     Claude and OpenAI-compatible — llm_client's Claude Messages-API path
     (anthropic_chat/anthropic_list_models), the cloud_chat URL router,
     and the split context budget (llm_num_ctx + llm_max_output_tokens →
     num_predict/max_tokens).
  5. Input: the ID Range / Single Msg / Markers modes are GONE; Import txt
     file works with .txt AND .md.

Headless-safe: QT_QPA_PLATFORM=offscreen, no GUI is ever shown, all
network is 127.0.0.1 fake servers, and the real config.json is never
touched (CONFIG_FILE is pointed at a temp file before any window loads).
"""

import json
import os
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gitcurator.core import llm_client as _llm
from unittest import mock


# ---------------------------------------------------------------------------
# Fakes: one-route JSON servers (GET + POST capture)
# ---------------------------------------------------------------------------

class _FakeJSONHandler(BaseHTTPRequestHandler):
    """GET/POST routes from ``server.routes`` — ``{path: (code, payload)}``;
    POST bodies are captured on ``server.seen`` as (path, headers, body)."""

    def do_GET(self):
        path = self.path.split("?")[0]
        route = getattr(self.server, "routes", {}).get(path)
        if route is None:
            self._reply(404, {"message": "Not Found"})
            return
        code, payload = route
        self._reply(code, payload)

    def do_POST(self):
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        if not hasattr(self.server, "seen"):
            self.server.seen = []
        self.server.seen.append((path, dict(self.headers), body))
        route = getattr(self.server, "routes", {}).get(path)
        if route is None:
            self._reply(404, {"message": "Not Found"})
            return
        code, payload = route
        self._reply(code, payload)

    def _reply(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def _start_json_server(routes):
    srv = HTTPServer(("127.0.0.1", 0), _FakeJSONHandler)
    srv.routes = dict(routes)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


def _stop_json_server(srv):
    srv.shutdown()
    srv.server_close()


def _dead_url():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    url = f"http://127.0.0.1:{s.getsockname()[1]}"
    s.close()
    return url


# ---------------------------------------------------------------------------
# 4a) The Claude wire format
# ---------------------------------------------------------------------------

class TestAnthropicChat(unittest.TestCase):

    def test_is_anthropic_url(self):
        self.assertTrue(_llm.is_anthropic_url("https://api.anthropic.com/v1"))
        self.assertTrue(_llm.is_anthropic_url("https://api.anthropic.com"))
        self.assertFalse(_llm.is_anthropic_url("https://api.openai.com/v1"))
        self.assertFalse(_llm.is_anthropic_url("http://127.0.0.1:8080/v1"))
        self.assertFalse(_llm.is_anthropic_url(""))
        self.assertFalse(_llm.is_anthropic_url(None))

    def test_anthropic_chat_headers_and_body(self):
        srv, base = _start_json_server({
            "/v1/messages": (200, {"content": [
                {"type": "text", "text": "{\"summary\": \"ok\"}"}]}),
        })
        try:
            out = _llm.anthropic_chat(
                base + "/v1", "sk-ant-test", "claude-sonnet-4-5",
                [{"role": "system", "content": "be strict"},
                 {"role": "user", "content": "analyze"}],
                10, max_output_tokens=2048)
            self.assertEqual(out, '{"summary": "ok"}')
            path, headers, body = srv.seen[0]
            self.assertEqual(path, "/v1/messages")
            # the Anthropic auth header set — NOT a Bearer
            self.assertEqual(headers.get("X-Api-Key"), "sk-ant-test")
            self.assertEqual(headers.get("Anthropic-Version"),
                             _llm.ANTHROPIC_VERSION_HEADER)
            self.assertNotIn("Authorization", headers)
            payload = json.loads(body)
            # the system message rides the top-level parameter
            self.assertEqual(payload["system"], "be strict")
            self.assertEqual(payload["messages"],
                             [{"role": "user", "content": "analyze"}])
            # max_tokens is REQUIRED on this API — the configured value wins
            self.assertEqual(payload["max_tokens"], 2048)
            self.assertEqual(payload["model"], "claude-sonnet-4-5")
        finally:
            _stop_json_server(srv)

    def test_anthropic_max_tokens_fallback_when_unset(self):
        srv, base = _start_json_server({
            "/v1/messages": (200, {"content": [{"type": "text", "text": "x"}]}),
        })
        try:
            _llm.anthropic_chat(base + "/v1", "k", "m",
                                [{"role": "user", "content": "hi"}], 10)
            _p, _h, body = srv.seen[0]
            self.assertEqual(json.loads(body)["max_tokens"],
                             _llm.ANTHROPIC_FALLBACK_MAX_TOKENS)
        finally:
            _stop_json_server(srv)

    def test_anthropic_json_mode_adds_the_system_instruction(self):
        srv, base = _start_json_server({
            "/v1/messages": (200, {"content": [{"type": "text", "text": "{}"}]}),
        })
        try:
            _llm.anthropic_chat(
                base + "/v1", "k", "m",
                [{"role": "system", "content": "sys"},
                 {"role": "user", "content": "hi"}],
                10, json_mode=True)
            _p, _h, body = srv.seen[0]
            system = json.loads(body)["system"]
            self.assertIn("ONLY a valid JSON object", system)
        finally:
            _stop_json_server(srv)

    def test_anthropic_joins_text_blocks(self):
        srv, base = _start_json_server({
            "/v1/messages": (200, {"content": [
                {"type": "text", "text": "part one "},
                {"type": "thinking", "thinking": "(internal)"},
                {"type": "text", "text": "part two"}]}),
        })
        try:
            out = _llm.anthropic_chat(base + "/v1", "k", "m",
                                      [{"role": "user", "content": "hi"}], 10)
            self.assertEqual(out, "part one \npart two")
        finally:
            _stop_json_server(srv)

    def test_anthropic_error_object_raises(self):
        srv, base = _start_json_server({
            "/v1/messages": (200, {"type": "error",
                                   "error": {"message": "bad model"}}),
        })
        try:
            with self.assertRaises(_llm.CloudLLMBadResponse):
                _llm.anthropic_chat(base + "/v1", "k", "m",
                                    [{"role": "user", "content": "hi"}], 10)
        finally:
            _stop_json_server(srv)

    def test_anthropic_http_error_raises(self):
        srv, base = _start_json_server({"/v1/messages": (401, {"m": "no"})})
        try:
            with self.assertRaises(_llm.CloudLLMHTTPError):
                _llm.anthropic_chat(base + "/v1", "k", "m",
                                    [{"role": "user", "content": "hi"}], 10)
        finally:
            _stop_json_server(srv)

    def test_anthropic_unreachable_raises_cloudllmerror(self):
        with self.assertRaises(_llm.CloudLLMError):
            _llm.anthropic_chat(_dead_url() + "/v1", "k", "m",
                                [{"role": "user", "content": "hi"}], 3)


class TestAnthropicListModels(unittest.TestCase):

    def test_lists_models_with_anthropic_headers(self):
        srv, base = _start_json_server({
            "/v1/models": (200, {"data": [
                {"id": "claude-sonnet-4-5"},
                {"id": "claude-opus-4-1"}]}),
        })
        try:
            names = _llm.anthropic_list_models(base + "/v1", "sk-ant")
            self.assertEqual(names, ["claude-sonnet-4-5", "claude-opus-4-1"])
        finally:
            _stop_json_server(srv)

    def test_http_error_raises(self):
        srv, base = _start_json_server({"/v1/models": (401, {"m": "no"})})
        try:
            with self.assertRaises(_llm.CloudLLMError):
                _llm.anthropic_list_models(base + "/v1", "sk-ant")
        finally:
            _stop_json_server(srv)


# ---------------------------------------------------------------------------
# 4b) cloud_chat — the URL decides the wire format (mocked, no network)
# ---------------------------------------------------------------------------

class TestCloudChatRouter(unittest.TestCase):

    def test_router_routes_by_url(self):
        calls = []

        def fake_anthropic(url, key, model, messages, timeout_s, **kw):
            calls.append(("anthropic", url))
            return "claude says hi"

        def fake_openai(url, key, model, messages, timeout_s, **kw):
            calls.append(("openai", url))
            return "gpt says hi"

        with mock.patch.object(_llm, "anthropic_chat", fake_anthropic), \
             mock.patch.object(_llm, "openai_chat", fake_openai):
            self.assertEqual(_llm.cloud_chat(
                "https://api.anthropic.com/v1", "k", "m", [], 5),
                "claude says hi")
            self.assertEqual(_llm.cloud_chat(
                "https://api.openai.com/v1", "k", "m", [], 5), "gpt says hi")
            self.assertEqual(_llm.cloud_chat(
                "http://localhost:8080/v1", "", "m", [], 5), "gpt says hi")
        self.assertEqual([c[0] for c in calls],
                         ["anthropic", "openai", "openai"])

    def test_router_forwards_the_budget_kwargs(self):
        seen = {}

        def fake_anthropic(url, key, model, messages, timeout_s, **kw):
            seen.update(kw)
            return "ok"

        with mock.patch.object(_llm, "anthropic_chat", fake_anthropic):
            _llm.cloud_chat("https://api.anthropic.com/v1", "k", "m", [], 5,
                            json_mode=True, num_ctx=160000,
                            max_output_tokens=32000, on_warn=None)
        self.assertEqual(seen.get("num_ctx"), 160000)
        self.assertEqual(seen.get("max_output_tokens"), 32000)
        self.assertTrue(seen.get("json_mode"))

    def test_preflight_cloud_routes_by_url(self):
        with mock.patch.object(_llm, "anthropic_list_models",
                               return_value=["claude-x"]) as fa, \
             mock.patch.object(_llm, "openai_list_models",
                               return_value=["gpt-x"]) as fo:
            ok, _msg, listed = _llm.preflight_cloud(
                "https://api.anthropic.com/v1", "k", "claude-x")
            self.assertTrue(ok)
            self.assertTrue(listed)
            ok, _msg, listed = _llm.preflight_cloud(
                "https://api.openai.com/v1", "k", "gpt-x")
            self.assertTrue(ok)
            self.assertTrue(listed)
        self.assertEqual(fa.call_count, 1)
        self.assertEqual(fo.call_count, 1)


# ---------------------------------------------------------------------------
# 4c) The split context budget: max_tokens / num_predict
# ---------------------------------------------------------------------------

class TestOutputTokens(unittest.TestCase):

    def test_openai_chat_sends_max_tokens_when_set(self):
        srv, base = _start_json_server({
            "/v1/chat/completions": (200, {"choices": [
                {"message": {"content": "ok"}}]}),
        })
        try:
            _llm.openai_chat(base + "/v1", "", "m",
                             [{"role": "user", "content": "hi"}], 10,
                             max_output_tokens=32000)
            _p, _h, body = srv.seen[0]
            self.assertEqual(json.loads(body)["max_tokens"], 32000)
            # unset → NOT sent (the server default stands)
            _llm.openai_chat(base + "/v1", "", "m",
                             [{"role": "user", "content": "hi"}], 10)
            _p, _h, body = srv.seen[1]
            self.assertNotIn("max_tokens", json.loads(body))
        finally:
            _stop_json_server(srv)

    def test_ollama_chat_sends_num_predict_when_set(self):
        class _Resp:
            class message:  # noqa: N801 — ollama-py shape
                content = "ok"
        seen = {}

        class _Client:
            def chat(self, **kwargs):
                seen.update(kwargs)
                return _Resp()
        _llm.ollama_chat(_Client(), "m",
                         [{"role": "user", "content": "hi"}], 10,
                         num_ctx=160000, num_predict=32000)
        self.assertEqual(seen["options"], {"num_ctx": 160000,
                                           "num_predict": 32000})
        seen.clear()
        # unset → only num_ctx (the old behavior)
        _llm.ollama_chat(_Client(), "m",
                         [{"role": "user", "content": "hi"}], 10,
                         num_ctx=8192)
        self.assertEqual(seen["options"], {"num_ctx": 8192})

    def test_worker_analyze_passes_the_budget_through(self):
        import gitcurator.gui.app as gui_app
        seen = []

        def recorder(api_url, api_key, model, messages,
                     json_mode=False, timeout_s=300, num_ctx=None,
                     max_output_tokens=None, on_warn=None):
            seen.append((num_ctx, max_output_tokens))
            return json.dumps({
                "summary": "ok", "how_it_works": "ok",
                "core_value": "ok", "features": ["a"],
                "difference": "ok", "category": "Uncategorized",
                "confidence": 50, "tags": []})

        gui_app.ProcessingWorker._call_cloud_llm = staticmethod(recorder)

        class _Log:
            def emit(self, *_a, **_k):
                pass
        w = gui_app.ProcessingWorker.__new__(gui_app.ProcessingWorker)
        w.config = {"llm_provider": "cloud",
                    "cloud_api_url": "http://127.0.0.1:9/v1",
                    "cloud_api_key": "k",
                    "cloud_model": "m",
                    "llm_num_ctx": 160000,
                    "llm_max_output_tokens": 32000}
        w.log_message = _Log()
        w.link_tracker = None
        w.is_running = True
        w._non_github_urls = []
        w._wait_for_llm_decision = lambda *a, **k: "skip"
        w._llm_analyze(None, "m", "repo", "d", [], "o", 1, 2)
        self.assertEqual(seen[0], (160000, 32000))


# ---------------------------------------------------------------------------
# The GUI cases — one shared, config-isolated QApplication harness
# ---------------------------------------------------------------------------

try:
    from PyQt6.QtWidgets import QApplication
    _APP = QApplication.instance() or QApplication([])
    _PYQT = True
except Exception:  # pragma: no cover — CI installs PyQt6
    _PYQT = False


@unittest.skipUnless(_PYQT, "PyQt6 not installed — GUI checks skipped")
class _GuiCase(unittest.TestCase):
    """Builds a REAL MainWindow against a temp config.json (never the
    repo's), chdir'd into a temp dir so about_me.md / reports never land
    in the checkout."""

    @classmethod
    def setUpClass(cls):
        import gitcurator.gui.app as gui_app
        # refactor/gui-app-split: load_config/save_config now live in
        # gitcurator.gui.main_window.vaults_config — the temp config path
        # must be swapped in on the owning module.
        import gitcurator.gui.main_window.vaults_config as gui_vaults
        cls.gui_app = gui_app
        cls.gui_vaults = gui_vaults
        cls._old_cwd = os.getcwd()
        cls._tmp = tempfile.mkdtemp(prefix="gk-v0230-")
        os.chdir(cls._tmp)
        cls._old_cfg = gui_vaults.CONFIG_FILE
        cls._cfg_path = os.path.join(cls._tmp, "config.json")
        with open(cls._cfg_path, "w", encoding="utf-8") as f:
            json.dump({"telegram_api_id": 1, "telegram_api_hash": "x",
                       "telegram_phone": "", "github_token": "",
                       "vault_path": cls._tmp}, f)
        gui_vaults.CONFIG_FILE = cls._cfg_path

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls._old_cwd)
        cls.gui_vaults.CONFIG_FILE = cls._old_cfg

    def _window(self):
        return self.gui_app.MainWindow()

    def _vault(self):
        import pathlib
        v = pathlib.Path(self._tmp) / "TestVault"
        v.mkdir(exist_ok=True)
        return str(v)


# ---------------------------------------------------------------------------
# 2) The Test Connection modal
# ---------------------------------------------------------------------------

class TestConnectionTestDialog(_GuiCase):

    def test_dialog_rows_and_gating(self):
        win = self._window()
        dlg = self.gui_app.ConnectionTestDialog(win)
        # four subsystem rows
        self.assertEqual(sorted(dlg._rows), [1, 2, 3, 4])
        # Start Syncing starts DISABLED ("before that it's turned off")
        self.assertFalse(dlg.start_sync_btn.isEnabled())
        # a checking row spins
        dlg.set_row_checking(1)
        self.assertTrue(dlg._rows[1]['spinning'])
        # a result lands as a detail line
        dlg.add_row_detail(1, "vault found · writable")
        self.assertIn("vault found", dlg._rows[1]['detail'].text())
        # everything EXCEPT telegram → still disabled
        for i in (1, 2, 3):
            dlg.finalize_row(i, 'ok')
        dlg.finish_all()
        self.assertFalse(dlg.start_sync_btn.isEnabled())
        # telegram ok too → enabled (the green state)
        dlg.finalize_row(4, 'ok')
        dlg.finish_all()
        self.assertTrue(dlg.start_sync_btn.isEnabled())
        # one error anywhere re-gates it
        dlg._verdicts[2] = 'error'
        dlg.finish_all()
        self.assertFalse(dlg.start_sync_btn.isEnabled())

    def test_verdict_marks_render(self):
        win = self._window()
        dlg = self.gui_app.ConnectionTestDialog(win)
        dlg.finalize_row(1, 'ok')
        self.assertIn("✅", dlg._rows[1]['status'].text())
        dlg.finalize_row(2, 'warn')
        self.assertIn("⚠️", dlg._rows[2]['status'].text())
        dlg.finalize_row(3, 'error')
        self.assertIn("❌", dlg._rows[3]['status'].text())
        self.assertFalse(dlg._rows[3]['spinning'])

    def test_battery_job_streams_structured_callbacks(self):
        class _Log:
            def emit(self, msg, level="info"):
                pass
        sections = []
        results = []
        cfg = {"vault_path": self._vault(), "llm_provider": "ollama",
               "ollama": {"base_url": _dead_url(), "model": "x"}}
        out = self.gui_app._connection_battery_job(
            cfg, _Log(),
            on_section=lambda title, idx, total:
                sections.append((title, idx, total)),
            on_result=lambda idx, r: results.append((idx, r.get("name"))))
        # four sections in order, results tagged with their section index
        self.assertEqual([s[0] for s in sections],
                         ["Vaults", "LLM", "GitHub", "Telegram"])
        self.assertTrue(all(isinstance(i, int) for i, _n in results))
        self.assertEqual(out["success"], True)
        self.assertEqual(len(out["sections"]), 4)


# ---------------------------------------------------------------------------
# 3) Themed dialog surfaces (the wizard light-mode fix)
# ---------------------------------------------------------------------------

class TestThemedDialogs(_GuiCase):

    def test_both_themes_pin_every_dialog_background(self):
        win = self._window()
        win.apply_light_theme()
        self.assertIn("QDialog { background-color: #FBF8F2; }",
                      win.styleSheet())
        win.apply_dark_theme()
        self.assertIn("QDialog { background-color: #221E2E; }",
                      win.styleSheet())
        win.apply_light_theme()

    def test_wizard_opens_in_light_mode(self):
        from PyQt6.QtWidgets import QDialog
        win = self._window()
        win._dark_mode = False
        win.apply_light_theme()
        opened = []
        orig = QDialog.exec
        QDialog.exec = lambda self: (opened.append(self.windowTitle()), 0)[1]
        try:
            win.show_about_me_wizard()   # must NOT raise in light mode
        finally:
            QDialog.exec = orig
        self.assertEqual(opened, ["📝 About Me Wizard"])

    def test_wizard_no_hardcoded_dark_text(self):
        import inspect
        src = inspect.getsource(
            self.gui_app.MainWindow.show_about_me_wizard)
        # the old bug: a hardcoded #423A52 intro color unreadable on the
        # themed dark surface. The setStyleSheet call must carry ONLY the
        # font/padding (the themed QWidget rule colors the text now).
        import re
        m = re.search(r'intro\.setStyleSheet\("([^"]*)"\)', src)
        self.assertIsNotNone(m, "the intro setStyleSheet call is gone?")
        self.assertNotIn("color", m.group(1))
        self.assertNotIn("#", m.group(1))
        # the buttons ride the tracked design-system styles now
        self.assertIn("self._style_btn(cancel_btn, 'secondary')", src)
        self.assertIn("self._style_btn(generate_btn, 'primary')", src)


# ---------------------------------------------------------------------------
# 5) Input — import txt file (.txt AND .md), modes removed
# ---------------------------------------------------------------------------

class TestInputImportOnly(_GuiCase):

    def test_input_tab_structure(self):
        win = self._window()
        self.assertTrue(hasattr(win, "import_file"))
        self.assertTrue(hasattr(win, "import_group"))
        # every removed mode's widget is gone
        for gone in ("mode_telegram", "mode_keyword", "mode_single",
                     "mode_import", "range_group", "marker_group",
                     "single_group", "range_from", "range_to",
                     "offset_toggle", "offset_start", "offset_count",
                     "marker_hash", "kw_toggle", "keyword_start",
                     "keyword_end", "single_id"):
            self.assertFalse(hasattr(win, gone), gone)

    def test_import_accepts_md_and_txt_in_dialog_filter(self):
        import inspect
        src = inspect.getsource(self.gui_app.MainWindow.select_import_file)
        self.assertIn("*.txt *.md", src)

    def test_start_processing_import_path(self):
        win = self._window()
        win.vault_combo.setCurrentText(self._vault())
        win._bot_queue_urls = []
        shown = []
        win._show_custom_message_box = lambda *a, **k: shown.append(a)
        started = []
        win._start_worker = lambda *a, **k: started.append(a)

        # no file → the helpful message
        win.import_file.setText("")
        win.start_processing()
        self.assertEqual(shown[-1][0], "Nothing to Process")

        # a .md file → the import worker starts (the .md support)
        import pathlib
        f = pathlib.Path(self._tmp) / "links.md"
        f.write_text("# my links\n\nhttps://github.com/foo/bar\n"
                     "https://example.com\n", encoding="utf-8")
        win.import_file.setText(str(f))
        win.start_processing()
        self.assertEqual(started[-1][0], "import")
        self.assertEqual(started[-1][5], str(f))

    def test_worker_import_reads_md_file(self):
        class _Log:
            def emit(self, *_a, **_k):
                pass
        w = self.gui_app.ProcessingWorker.__new__(
            self.gui_app.ProcessingWorker)
        w.log_message = _Log()
        import pathlib
        f = pathlib.Path(self._tmp) / "links.md"
        f.write_text("# heading (the '# comment' rule still applies)\n"
                     "https://github.com/o/r\n"
                     "\n"
                     "https://example.com/site\n", encoding="utf-8")
        w.import_file = str(f)
        w._create_inbox_notes = lambda urls, source=None: None
        github_urls = w._fetch_from_import()
        self.assertEqual(github_urls, ["https://github.com/o/r"])
        self.assertEqual(w._non_github_urls, ["https://example.com/site"])


# ---------------------------------------------------------------------------
# 4d) The LLM tab structure — two host radios, engine radios, fields
# ---------------------------------------------------------------------------

class TestLlmTabStructure(_GuiCase):

    def test_two_host_radios(self):
        win = self._window()
        self.assertEqual(win.llm_host_local.text(),
                         "🖥️ Locally hosted LLM model")
        self.assertEqual(win.llm_host_cloud.text(), "☁️ Cloud API model")
        # the old three-way provider radio row is gone
        self.assertFalse(hasattr(win, "llm_provider_cloud"))

    def test_local_shows_engines_cloud_shows_fields(self):
        win = self._window()
        # default (ollama saved) → local host + engine groups
        win.llm_host_local.setChecked(True)
        win.llm_provider_ollama.setChecked(True)
        self.assertFalse(win.local_llm_group.isHidden())
        self.assertTrue(win.cloud_group.isHidden())
        self.assertFalse(win.ollama_group.isHidden())
        self.assertTrue(win.llamacpp_group.isHidden())
        # engine flip inside local
        win.llm_provider_llamacpp.setChecked(True)
        self.assertTrue(win.ollama_group.isHidden())
        self.assertFalse(win.llamacpp_group.isHidden())
        # cloud host → cloud fields, the whole local group hidden
        win.llm_host_cloud.setChecked(True)
        self.assertTrue(win.local_llm_group.isHidden())
        self.assertFalse(win.cloud_group.isHidden())

    def test_provider_persists_through_save(self):
        win = self._window()
        win.vault_combo.setCurrentText(self._vault())
        for host, engine, want in (
                (True, "ollama", "ollama"),
                (True, "llamacpp", "llamacpp"),
                (False, None, "cloud")):
            win.llm_host_local.setChecked(host)
            win.llm_host_cloud.setChecked(not host)
            if engine:
                getattr(win, f"llm_provider_{engine}").setChecked(True)
            win.save_config()
            self.assertEqual(win.config["llm_provider"], want)

    def test_context_budget_fields_exist_and_persist(self):
        win = self._window()
        win.vault_combo.setCurrentText(self._vault())
        self.assertTrue(hasattr(win, "llm_num_ctx"))
        self.assertTrue(hasattr(win, "llm_max_output_tokens"))
        # the owner's example: 160k total / 32k output
        win.llm_num_ctx.setText("160000")
        win.llm_max_output_tokens.setText("32000")
        win.save_config()
        self.assertEqual(win.config["llm_num_ctx"], 160000)
        self.assertEqual(win.config["llm_max_output_tokens"], 32000)
        # garbage never breaks a save (the lenient field contract)
        win.llm_max_output_tokens.setText("thirty-two thousand")
        win.save_config()
        self.assertEqual(win.config["llm_max_output_tokens"], 32000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
