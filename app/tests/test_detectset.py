#!/usr/bin/env python3
"""
test_detectset.py — v0.18.0: the "Detect & Set" quick-switch fast lane.

Owner request 2026-09-29: "I want these two buttons: Detect and Set ollama /
Detect and Set llama.cpp. In my system I have both llama.cpp and ollama;
sometimes I use llama.cpp model, sometimes ollama, so I want two buttons
that detect, and set the model for me. If there were multiple models for
each, a menu like the current one should help user to select their desired
model. The point is that there is still not dedicated way to connect fast
to llama.cpp."

Covers, all against LOCAL stdlib http.servers / temp dirs / monkeypatched
probes — no network beyond 127.0.0.1, no GUI shown:
  * llm_client.detect_ollama — /api/tags found + models (name key, model
    key, dedup, order); up-but-empty; dead port; non-dict JSON; URL
    normalization (no scheme, trailing slash); hostile inputs never raise
  * _quick_detect_job (the background half) — ollama path against a fake
    server (config junk → the default URL); llamacpp path via monkey-
    patched probe/detect (configured-URL hit, process-port fallback,
    props_model merged into the model list); hostile configs never raise
  * _apply_quick_detect (the SET half) on a stubbed window — not-found /
    no-models / single-model auto-set / multi-model menu pick / cancel;
    the exact config keys + save_config call; the llama.cpp /v1 URL rule
  * GUI wiring — both buttons on the MAIN screen, the Quick switch row in
    Settings → 🧠 LLM, the model-selection dialog source, the runner
    guards (batch running, double-click)
  * the CLI twin --detect-llm — parses + dispatches + help lists it;
    missing config rc=1; fake Ollama single-model rc=0 + config SAVED;
    multi-model menu via patched input (pick / invalid / --yes); dead
    server rc=1; llamacpp via monkeypatched probes rc=0 + keys saved

Headless-safe: QT_QPA_PLATFORM=offscreen, no GUI is ever shown.
"""

import contextlib
import inspect
import io
import json
import os
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gitcurator.core import llm_client as _llm


# ---------------------------------------------------------------------------
# Fakes: the same one-route JSON server test_connection.py uses
# ---------------------------------------------------------------------------

class _FakeJSONHandler(BaseHTTPRequestHandler):
    """GET routes from ``server.routes`` — ``{path: (code, payload)}``."""

    def do_GET(self):
        path = self.path.split("?")[0]
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
    """A loopback URL that is guaranteed to refuse connections."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    url = f"http://127.0.0.1:{s.getsockname()[1]}"
    s.close()
    return url


class _FakeLogSignal:
    def __init__(self):
        self.lines = []

    def emit(self, msg, level="info"):
        self.lines.append((str(msg), str(level)))


# ---------------------------------------------------------------------------
# llm_client.detect_ollama — the Ollama twin of probe_llamacpp
# ---------------------------------------------------------------------------

class TestDetectOllama(unittest.TestCase):

    def test_found_with_models_name_key(self):
        srv, url = _start_json_server({
            "/api/tags": (200, {"models": [
                {"name": "llama3:latest"},
                {"name": "qwen2.5:7b"},
            ]})})
        self.addCleanup(_stop_json_server, srv)
        r = _llm.detect_ollama(url)
        self.assertTrue(r["found"])
        self.assertEqual(r["base_url"], url)
        self.assertEqual(r["models"], ["llama3:latest", "qwen2.5:7b"])
        self.assertIn("2 model(s)", r["detail"])

    def test_model_key_fallback_and_dedup_keeps_order(self):
        srv, url = _start_json_server({
            "/api/tags": (200, {"models": [
                {"model": "gemma2:2b"},
                {"name": "gemma2:2b"},        # duplicate, different key
                {"name": ""},
                {"model": None},
                {"name": "mistral:latest"},
            ]})})
        self.addCleanup(_stop_json_server, srv)
        r = _llm.detect_ollama(url)
        self.assertEqual(r["models"], ["gemma2:2b", "mistral:latest"])

    def test_up_but_no_models(self):
        srv, url = _start_json_server({
            "/api/tags": (200, {"models": []})})
        self.addCleanup(_stop_json_server, srv)
        r = _llm.detect_ollama(url)
        self.assertTrue(r["found"])
        self.assertEqual(r["models"], [])
        self.assertIn("NO models", r["detail"])

    def test_dead_port_never_raises(self):
        r = _llm.detect_ollama(_dead_url())
        self.assertFalse(r["found"])
        self.assertEqual(r["models"], [])
        self.assertTrue(r["detail"])

    def test_non_dict_json_is_not_found(self):
        srv, url = _start_json_server({
            "/api/tags": (200, ["not", "a", "dict"])})
        self.addCleanup(_stop_json_server, srv)
        r = _llm.detect_ollama(url)
        self.assertFalse(r["found"])
        self.assertTrue(r["detail"])

    def test_missing_tags_route_is_not_found(self):
        # an HTTP server that 404s /api/tags (i.e. NOT an Ollama server)
        srv, url = _start_json_server({"/": (200, {})})
        self.addCleanup(_stop_json_server, srv)
        r = _llm.detect_ollama(url)
        self.assertFalse(r["found"])

    def test_url_normalization_scheme_and_slash(self):
        srv, url = _start_json_server({
            "/api/tags": (200, {"models": [{"name": "m1"}]})})
        self.addCleanup(_stop_json_server, srv)
        host = url.split("://", 1)[1]
        for spelling in (url, url + "/", host):
            r = _llm.detect_ollama(spelling)
            self.assertTrue(r["found"], spelling)
            self.assertEqual(r["base_url"], url)

    def test_hostile_inputs_never_raise(self):
        for bad in (None, "", "   ", "::::", "http://", 123, {"a": 1}):
            r = _llm.detect_ollama(bad, timeout_s=0.5)
            self.assertIsInstance(r, dict)
            self.assertIn("found", r)
            self.assertFalse(r["found"])  # nothing sane to find — no crash

    def test_default_url_is_the_ollama_port(self):
        # the default must stay the Ollama default (11434), never invented
        src = inspect.getsource(_llm.detect_ollama)
        self.assertIn("127.0.0.1:11434", src)


# ---------------------------------------------------------------------------
# _quick_detect_job — the background half (probe ONE engine)
# ---------------------------------------------------------------------------

class TestQuickDetectJob(unittest.TestCase):

    def test_ollama_against_fake_server(self):
        import gitcurator.gui.app as gui_app
        srv, url = _start_json_server({
            "/api/tags": (200, {"models": [{"name": "llama3:latest"},
                                           {"name": "qwen2.5:7b"}]})})
        self.addCleanup(_stop_json_server, srv)
        sig = _FakeLogSignal()
        out = gui_app._quick_detect_job(
            "ollama", {"ollama": {"base_url": url}}, sig)
        self.assertTrue(out["success"])
        self.assertTrue(out["found"])
        self.assertEqual(out["base_url"], url)
        self.assertEqual(out["models"], ["llama3:latest", "qwen2.5:7b"])
        self.assertTrue(any("Detecting Ollama" in m for m, _ in sig.lines))

    def test_ollama_dead_port(self):
        import gitcurator.gui.app as gui_app
        sig = _FakeLogSignal()
        out = gui_app._quick_detect_job(
            "ollama", {"ollama": {"base_url": _dead_url()}}, sig)
        self.assertTrue(out["success"])   # the probe RAN
        self.assertFalse(out["found"])    # the engine did not answer
        self.assertEqual(out["models"], [])

    def test_ollama_hostile_config_uses_default_never_raises(self):
        import gitcurator.gui.app as gui_app
        for cfg in ({}, {"ollama": None}, {"ollama": "junk"},
                    {"ollama": {"base_url": None}},
                    {"ollama": {"base_url": "   "}}, None, "junk"):
            sig = _FakeLogSignal()
            out = gui_app._quick_detect_job("ollama", cfg, sig)
            self.assertTrue(out["success"], cfg)
            self.assertFalse(out["found"], cfg)  # no live server at default

    def test_llamacpp_configured_url_hit(self):
        import gitcurator.gui.app as gui_app
        probe = {"found": True, "ready": True, "base_url": "http://127.0.0.1:8080",
                 "models": ["qwen2.5-7b-q4"], "model": "qwen2.5-7b-q4",
                 "props_model": "qwen2.5-7b-q4", "detail": "up"}
        sig = _FakeLogSignal()
        with mock.patch.object(_llm, "probe_llamacpp",
                               return_value=probe) as p, \
                mock.patch.object(_llm, "detect_llamacpp") as d:
            out = gui_app._quick_detect_job(
                "llamacpp",
                {"llamacpp_api_url": "http://127.0.0.1:8080/v1"}, sig)
            p.assert_called_once()
            d.assert_not_called()  # configured URL hit → no scan needed
        self.assertTrue(out["success"])
        self.assertTrue(out["found"])
        self.assertEqual(out["via"], "configured")
        self.assertEqual(out["models"], ["qwen2.5-7b-q4"])

    def test_llamacpp_process_port_fallback(self):
        import gitcurator.gui.app as gui_app
        miss = {"found": False, "models": [], "model": None,
                "props_model": None, "detail": "no answer",
                "base_url": "http://127.0.0.1:8080"}
        hit = {"found": True, "ready": None, "base_url": "http://127.0.0.1:8090",
               "models": [], "model": "llama-3-8b.Q4_K_M.gguf",
               "props_model": "llama-3-8b.Q4_K_M.gguf",
               "detail": "up", "via": "process"}
        sig = _FakeLogSignal()
        with mock.patch.object(_llm, "probe_llamacpp", return_value=miss), \
                mock.patch.object(_llm, "detect_llamacpp", return_value=hit):
            out = gui_app._quick_detect_job("llamacpp", {}, sig)
        self.assertTrue(out["found"])
        self.assertEqual(out["base_url"], "http://127.0.0.1:8090")
        self.assertEqual(out["via"], "process")
        # props_model merged into the advertised model list
        self.assertEqual(out["models"], ["llama-3-8b.Q4_K_M.gguf"])
        self.assertEqual(out["props_model"], "llama-3-8b.Q4_K_M.gguf")

    def test_llamacpp_nothing_running(self):
        import gitcurator.gui.app as gui_app
        miss = {"found": False, "models": [], "model": None,
                "props_model": None, "detail": "no answer",
                "base_url": "http://127.0.0.1:8080"}
        sig = _FakeLogSignal()
        with mock.patch.object(_llm, "probe_llamacpp", return_value=miss), \
                mock.patch.object(_llm, "detect_llamacpp", return_value=None):
            out = gui_app._quick_detect_job("llamacpp", {}, sig)
        self.assertTrue(out["success"])
        self.assertFalse(out["found"])

    def test_llamacpp_hostile_config_never_raises(self):
        import gitcurator.gui.app as gui_app
        miss = {"found": False, "models": [], "detail": "x",
                "base_url": "http://127.0.0.1:8080"}
        for cfg in ({}, {"llamacpp_api_url": None},
                    {"llamacpp_api_url": "   "}, None, 7):
            sig = _FakeLogSignal()
            with mock.patch.object(_llm, "probe_llamacpp",
                                   return_value=miss), \
                    mock.patch.object(_llm, "detect_llamacpp",
                                      return_value=None):
                out = gui_app._quick_detect_job("llamacpp", cfg, sig)
            self.assertTrue(out["success"], cfg)
            self.assertFalse(out["found"], cfg)

    def test_crash_inside_probe_is_caught(self):
        import gitcurator.gui.app as gui_app

        def _boom(*_a, **_k):
            raise RuntimeError("boom")

        sig = _FakeLogSignal()
        with mock.patch.object(_llm, "detect_ollama", side_effect=_boom):
            out = gui_app._quick_detect_job("ollama", {}, sig)
        self.assertFalse(out["success"])
        self.assertIn("RuntimeError", out["detail"])


# ---------------------------------------------------------------------------
# _apply_quick_detect — the SET half, on a stubbed window (no Qt)
# ---------------------------------------------------------------------------

class _StubCombo:
    def __init__(self):
        self.items = []
        self.current = ""

    def clear(self):
        self.items = []

    def addItem(self, name):
        self.items.append(name)

    def setCurrentText(self, name):
        self.current = name

    def currentText(self):
        return self.current


class _StubEdit:
    def __init__(self, text=""):
        self._text = text

    def text(self):
        return self._text

    def setText(self, t):
        self._text = t


class _StubWindow:
    """The attributes _apply_quick_detect touches — no Qt anywhere."""

    def __init__(self, config=None, dialog_choice=""):
        self.config = config if config is not None else {}
        self.logs = []
        self.saved = 0
        self.dialog_calls = []
        self.dialog_choice = dialog_choice
        self.fill_calls = []
        self.ollama_model = _StubCombo()
        self.ollama_url = _StubEdit()
        self.llamacpp_api_url = _StubEdit()

    def log_message(self, msg, level="info"):
        self.logs.append((str(msg), str(level)))

    def save_config(self):
        self.saved += 1

    def _quick_model_dialog(self, provider, base_url, models, current=""):
        self.dialog_calls.append((provider, base_url, list(models), current))
        return self.dialog_choice

    def _fill_llamacpp_models(self, models, props_model=None, pick=None):
        # mirrors the real helper's contract (records + syncs config)
        self.fill_calls.append((list(models or []), props_model, pick))
        names = [n for n in (models or []) if n]
        if props_model and props_model not in names:
            names.append(props_model)
        choice = pick or (names[0] if names else "")
        self.config["llamacpp_model"] = choice
        return choice


def _apply(win, provider, result):
    import gitcurator.gui.app as gui_app
    return gui_app.MainWindow._apply_quick_detect(win, provider, result)


class TestApplyQuickDetect(unittest.TestCase):

    def test_probe_crash_is_an_error_no_save(self):
        win = _StubWindow()
        _apply(win, "ollama", {"success": False, "detail": "RuntimeError: x"})
        self.assertTrue(any(lvl == "error" for _m, lvl in win.logs))
        self.assertEqual(win.saved, 0)

    def test_ollama_not_found_error_with_remedy(self):
        win = _StubWindow()
        _apply(win, "ollama", {"success": True, "found": False,
                               "base_url": "http://127.0.0.1:11434",
                               "detail": "URLError: refused"})
        self.assertTrue(any("not running" in m for m, _ in win.logs))
        self.assertTrue(any("Start Server" in m for m, _ in win.logs))
        self.assertEqual(win.saved, 0)

    def test_llamacpp_not_found_error_with_command(self):
        win = _StubWindow()
        _apply(win, "llamacpp", {"success": True, "found": False,
                                 "detail": "no answer"})
        self.assertTrue(any("llama-server -m" in m for m, _ in win.logs))
        self.assertEqual(win.saved, 0)

    def test_ollama_up_no_models_warning(self):
        win = _StubWindow()
        _apply(win, "ollama", {"success": True, "found": True,
                               "base_url": "http://127.0.0.1:11434",
                               "models": [], "detail": "up"})
        self.assertTrue(any("ollama pull" in m for m, _ in win.logs))
        self.assertEqual(win.saved, 0)

    def test_llamacpp_up_no_model_warning(self):
        win = _StubWindow()
        _apply(win, "llamacpp", {"success": True, "found": True,
                                 "base_url": "http://127.0.0.1:8080",
                                 "models": [], "detail": "up",
                                 "ready": False})
        self.assertTrue(any(lvl == "warning" for _m, lvl in win.logs))
        self.assertEqual(win.saved, 0)

    def test_ollama_single_model_sets_and_saves(self):
        win = _StubWindow({"llm_provider": "llamacpp",
                           "ollama": {"base_url": "http://old:1",
                                      "model": "old"}})
        _apply(win, "ollama", {"success": True, "found": True,
                               "base_url": "http://127.0.0.1:11434",
                               "models": ["llama3:latest"],
                               "detail": "up · 1 model(s)"})
        self.assertEqual(win.config["llm_provider"], "ollama")
        self.assertEqual(win.config["ollama"]["base_url"],
                         "http://127.0.0.1:11434")
        self.assertEqual(win.config["ollama"]["model"], "llama3:latest")
        self.assertEqual(win.saved, 1)
        self.assertEqual(win.dialog_calls, [])  # single model → no menu
        # live widgets updated for the settings page
        self.assertEqual(win.ollama_url.text(), "http://127.0.0.1:11434")
        self.assertEqual(win.ollama_model.items, ["llama3:latest"])
        self.assertEqual(win.ollama_model.current, "llama3:latest")
        self.assertTrue(any("SET to Ollama" in m for m, _ in win.logs))

    def test_ollama_multi_model_menu_picks_choice(self):
        win = _StubWindow({"llm_provider": "cloud",
                           "ollama": {"base_url": "http://x:1",
                                      "model": "qwen2.5:7b"}},
                          dialog_choice="llama3:latest")
        _apply(win, "ollama", {"success": True, "found": True,
                               "base_url": "http://127.0.0.1:11434",
                               "models": ["llama3:latest", "qwen2.5:7b"],
                               "detail": "up · 2"})
        # the menu was offered with every model + the current preselect
        self.assertEqual(len(win.dialog_calls), 1)
        prov, _base, models, current = win.dialog_calls[0]
        self.assertEqual(prov, "ollama")
        self.assertEqual(models, ["llama3:latest", "qwen2.5:7b"])
        self.assertEqual(current, "qwen2.5:7b")  # configured still installed
        self.assertEqual(win.config["ollama"]["model"], "llama3:latest")
        self.assertEqual(win.saved, 1)

    def test_multi_model_cancel_changes_nothing(self):
        win = _StubWindow({"llm_provider": "cloud"}, dialog_choice="")
        _apply(win, "ollama", {"success": True, "found": True,
                               "base_url": "http://127.0.0.1:11434",
                               "models": ["a", "b"], "detail": "up"})
        self.assertEqual(win.config["llm_provider"], "cloud")  # untouched
        self.assertEqual(win.saved, 0)
        self.assertTrue(any("NOT changed" in m for m, _ in win.logs))

    def test_llamacpp_sets_v1_url_model_and_saves(self):
        win = _StubWindow({"llm_provider": "ollama",
                           "llamacpp_api_url": "http://old:1/v1",
                           "llamacpp_model": ""})
        _apply(win, "llamacpp", {"success": True, "found": True,
                                 "base_url": "http://127.0.0.1:8080",
                                 "models": ["qwen2.5-7b-q4"],
                                 "props_model": "qwen2.5-7b-q4",
                                 "detail": "up", "via": "process",
                                 "ready": True})
        self.assertEqual(win.config["llm_provider"], "llamacpp")
        self.assertEqual(win.config["llamacpp_api_url"],
                         "http://127.0.0.1:8080/v1")
        self.assertEqual(win.config["llamacpp_model"], "qwen2.5-7b-q4")
        self.assertEqual(win.llamacpp_api_url.text(),
                         "http://127.0.0.1:8080/v1")
        # the fill went through the SAME helper the Detect button uses
        self.assertEqual(win.fill_calls,
                         [(["qwen2.5-7b-q4"], "qwen2.5-7b-q4",
                           "qwen2.5-7b-q4")])
        self.assertEqual(win.saved, 1)
        self.assertTrue(any("via the running llama-server process"
                            in m for m, _ in win.logs))
        self.assertTrue(any("SET to llama.cpp" in m for m, _ in win.logs))

    def test_llamacpp_multi_model_menu(self):
        win = _StubWindow({"llm_provider": "ollama",
                           "llamacpp_model": "model-b.gguf"},
                          dialog_choice="model-a.gguf")
        _apply(win, "llamacpp", {"success": True, "found": True,
                                 "base_url": "http://127.0.0.1:8080",
                                 "models": ["model-a.gguf", "model-b.gguf"],
                                 "props_model": None, "detail": "up",
                                 "via": "scan", "ready": True})
        self.assertEqual(win.dialog_calls[0][2],
                         ["model-a.gguf", "model-b.gguf"])
        self.assertEqual(win.dialog_calls[0][3], "model-b.gguf")
        self.assertEqual(win.config["llamacpp_model"], "model-a.gguf")
        self.assertEqual(win.saved, 1)

    def test_llamacpp_props_only_model_counts(self):
        # /v1/models hidden but /props names the model → still settable
        win = _StubWindow({"llm_provider": "ollama"})
        _apply(win, "llamacpp", {"success": True, "found": True,
                                 "base_url": "http://127.0.0.1:8080",
                                 "models": ["llama-3-8b.Q4_K_M.gguf"],
                                 "props_model": "llama-3-8b.Q4_K_M.gguf",
                                 "detail": "up", "ready": True})
        self.assertEqual(win.config["llamacpp_model"],
                         "llama-3-8b.Q4_K_M.gguf")
        self.assertEqual(win.saved, 1)


# ---------------------------------------------------------------------------
# GUI wiring — the two buttons, the runner guards, the model dialog
# ---------------------------------------------------------------------------

class TestGuiWiring(unittest.TestCase):

    def test_main_screen_buttons_exist(self):
        # v0.23.0 — owner request: "Remove detect set ollama and detect set
        # llama.cpp from the main view of app (the settings is enough)".
        # The MAIN VIEW no longer constructs either button (the Settings
        # quick row keeps the plain strings, but only as LOCAL widgets —
        # the main-view row's self.detect_set_* attributes are gone, and
        # its llm_row layout with them). The handlers stay.
        import gitcurator.gui.app as gui_app
        src = inspect.getsource(gui_app.MainWindow.initUI)
        self.assertNotIn('self.detect_set_ollama_btn', src)
        self.assertNotIn('self.detect_set_llamacpp_btn', src)
        self.assertNotIn('llm_row', src)  # the removed main-view row layout
        # the v0.23.0 removal note is right there where the row used to be
        self.assertIn('the LLM quick-switch row', src)

    def test_settings_llm_page_quick_switch_row(self):
        import gitcurator.gui.app as gui_app
        src = inspect.getsource(gui_app.MainWindow.initUI)
        # v0.30.0 (audit): emoji purged from the chrome — the quick row is
        # introduced by its muted caption now, still wired once, still
        # inside the local engine card (local_layout).
        self.assertIn("Quick switch", src)
        # v0.23.0 — the quick row lives ONLY in Settings → LLM (the
        # main-view copies are gone), so initUI wires each handler once.
        self.assertEqual(src.count("quick_detect_set_ollama"), 1)
        self.assertEqual(src.count("quick_detect_set_llamacpp"), 1)
        # …and the quick row sits INSIDE the local engine card so the
        # buttons appear when "Locally hosted LLM model" is selected.
        self.assertIn('local_layout.addLayout(quick_row)', src)
        self.assertIn('"Locally hosted LLM model"', src)

    def test_handlers_and_runner_exist(self):
        import gitcurator.gui.app as gui_app
        for name in ("quick_detect_set_ollama", "quick_detect_set_llamacpp",
                     "_run_quick_detect", "_quick_model_dialog",
                     "_apply_quick_detect"):
            self.assertTrue(callable(getattr(gui_app.MainWindow, name)),
                            name)

    def test_runner_guards(self):
        import gitcurator.gui.app as gui_app
        src = inspect.getsource(gui_app.MainWindow._run_quick_detect)
        # a running batch blocks the switch (config stays consistent)
        self.assertIn("isFinished", src)
        # a second click while one detect runs is refused
        self.assertIn("_quick_detect_running", src)
        # the probe runs in a TestWorker — the GUI thread never blocks
        self.assertIn("TestWorker", src)
        # the apply happens on the GUI thread after the worker finishes
        self.assertIn("_apply_quick_detect", src)

    def test_runner_snapshots_live_widgets(self):
        import gitcurator.gui.app as gui_app
        src = inspect.getsource(gui_app.MainWindow._run_quick_detect)
        self.assertIn("ollama_url", src)
        self.assertIn("llamacpp_api_url", src)

    def test_model_dialog_source_contract(self):
        import gitcurator.gui.app as gui_app
        src = inspect.getsource(gui_app.MainWindow._quick_model_dialog)
        # themed dropdown pre-set to the configured model + explicit buttons
        self.assertIn("QComboBox", src)
        self.assertIn("setCurrentText(cur)", src)
        self.assertIn("Use this model", src)
        # cancel returns '' — the caller treats it as "change nothing"
        self.assertIn("return ''", src)

    def test_apply_source_saves_and_fills(self):
        import gitcurator.gui.app as gui_app
        src = inspect.getsource(gui_app.MainWindow._apply_quick_detect)
        self.assertIn("self.save_config()", src)
        self.assertIn("_fill_llamacpp_models", src)
        self.assertIn("llamacpp_api_url", src)
        self.assertIn("'/v1'", src)


# ---------------------------------------------------------------------------
# CLI — --detect-llm {ollama,llamacpp}
# ---------------------------------------------------------------------------

class TestCli(unittest.TestCase):

    def _cli(self, argv):
        from gitcurator import cli
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.cli_main(argv)
        return rc, buf.getvalue()

    def _write_cfg(self, td, cfg):
        cfg_path = os.path.join(td, "config.json")
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        return cfg_path

    def _read_cfg(self, path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_flag_parses_and_help_lists_it(self):
        from gitcurator import cli
        for value in ("ollama", "llamacpp"):
            args = cli.build_parser().parse_args(["--detect-llm", value])
            self.assertEqual(args.detect_llm, value)
        h = io.StringIO()
        with contextlib.redirect_stdout(h):
            cli.build_parser().print_help()
        self.assertIn("--detect-llm", h.getvalue())
        # the nothing-selected help lists it too
        rc, out = self._cli(["--no-color"])
        self.assertEqual(rc, 0)
        self.assertIn("--detect-llm", out)

    def test_invalid_choice_rejected(self):
        from gitcurator import cli
        with self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["--detect-llm", "cloud"])

    def test_missing_config_rc1(self):
        rc, out = self._cli(["--detect-llm", "ollama", "--config",
                             "/nonexistent/config.json", "--no-color"])
        self.assertEqual(rc, 1)
        self.assertIn("No config", out)

    def test_ollama_single_model_sets_and_saves(self):
        srv, url = _start_json_server({
            "/api/tags": (200, {"models": [{"name": "llama3:latest"}]})})
        self.addCleanup(_stop_json_server, srv)
        with tempfile.TemporaryDirectory() as td:
            cfg_path = self._write_cfg(td, {
                "llm_provider": "llamacpp",
                "ollama": {"base_url": url, "model": "old"},
                "llamacpp_api_url": "http://127.0.0.1:8080/v1"})
            rc, out = self._cli(["--detect-llm", "ollama", "--config",
                                 cfg_path, "--no-color"])
            self.assertEqual(rc, 0)
            self.assertIn("the only one installed", out)
            self.assertIn("SET to Ollama", out)
            cfg = self._read_cfg(cfg_path)
            self.assertEqual(cfg["llm_provider"], "ollama")
            self.assertEqual(cfg["ollama"]["base_url"], url)
            self.assertEqual(cfg["ollama"]["model"], "llama3:latest")
            # the OTHER provider's keys survive the MERGE save
            self.assertEqual(cfg["llamacpp_api_url"],
                             "http://127.0.0.1:8080/v1")

    def test_ollama_multi_model_menu_pick(self):
        srv, url = _start_json_server({
            "/api/tags": (200, {"models": [{"name": "llama3:latest"},
                                           {"name": "qwen2.5:7b"},
                                           {"name": "gemma2:2b"}]})})
        self.addCleanup(_stop_json_server, srv)
        with tempfile.TemporaryDirectory() as td:
            cfg_path = self._write_cfg(td, {
                "llm_provider": "llamacpp",
                "ollama": {"base_url": url, "model": "gemma2:2b"}})
            with mock.patch("builtins.input", return_value="2"):
                rc, out = self._cli(["--detect-llm", "ollama", "--config",
                                     cfg_path, "--no-color"])
            self.assertEqual(rc, 0)
            self.assertIn("3 models detected", out)
            self.assertIn("← current", out)  # gemma2:2b marked
            cfg = self._read_cfg(cfg_path)
            self.assertEqual(cfg["ollama"]["model"], "qwen2.5:7b")

    def test_ollama_multi_model_invalid_pick_aborts(self):
        srv, url = _start_json_server({
            "/api/tags": (200, {"models": [{"name": "a"}, {"name": "b"}]})})
        self.addCleanup(_stop_json_server, srv)
        with tempfile.TemporaryDirectory() as td:
            cfg_path = self._write_cfg(td, {
                "llm_provider": "cloud",
                "ollama": {"base_url": url, "model": "a"}})
            with mock.patch("builtins.input", return_value="99"):
                rc, out = self._cli(["--detect-llm", "ollama", "--config",
                                     cfg_path, "--no-color"])
            self.assertEqual(rc, 1)
            self.assertIn("not a valid choice", out)
            self.assertIn("nothing changed", out)
            # config file untouched
            self.assertEqual(self._read_cfg(cfg_path)["llm_provider"],
                             "cloud")

    def test_ollama_multi_model_enter_keeps_current(self):
        srv, url = _start_json_server({
            "/api/tags": (200, {"models": [{"name": "a"}, {"name": "b"}]})})
        self.addCleanup(_stop_json_server, srv)
        with tempfile.TemporaryDirectory() as td:
            cfg_path = self._write_cfg(td, {
                "llm_provider": "cloud",
                "ollama": {"base_url": url, "model": "b"}})
            with mock.patch("builtins.input", return_value=""):
                rc, _out = self._cli(["--detect-llm", "ollama", "--config",
                                      cfg_path, "--no-color"])
            self.assertEqual(rc, 0)
            self.assertEqual(self._read_cfg(cfg_path)["ollama"]["model"],
                             "b")

    def test_ollama_yes_flag_skips_menu(self):
        srv, url = _start_json_server({
            "/api/tags": (200, {"models": [{"name": "a"}, {"name": "b"}]})})
        self.addCleanup(_stop_json_server, srv)
        with tempfile.TemporaryDirectory() as td:
            cfg_path = self._write_cfg(td, {
                "llm_provider": "cloud",
                "ollama": {"base_url": url, "model": "b"}})
            with mock.patch("builtins.input", side_effect=AssertionError(
                    "menu must not open with --yes")):
                rc, out = self._cli(["--detect-llm", "ollama", "--yes",
                                     "--config", cfg_path, "--no-color"])
            self.assertEqual(rc, 0)
            self.assertIn("--yes", out)
            self.assertEqual(self._read_cfg(cfg_path)["ollama"]["model"],
                             "b")  # current wins over models[0]

    def test_ollama_dead_server_rc1(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_path = self._write_cfg(td, {
                "llm_provider": "cloud",
                "ollama": {"base_url": _dead_url(), "model": "a"}})
            rc, out = self._cli(["--detect-llm", "ollama", "--config",
                                 cfg_path, "--no-color"])
        self.assertEqual(rc, 1)
        self.assertIn("not running", out)
        self.assertIn("ollama serve", out)

    def test_ollama_no_models_rc1(self):
        srv, url = _start_json_server({
            "/api/tags": (200, {"models": []})})
        self.addCleanup(_stop_json_server, srv)
        with tempfile.TemporaryDirectory() as td:
            cfg_path = self._write_cfg(td, {
                "ollama": {"base_url": url, "model": "a"}})
            rc, out = self._cli(["--detect-llm", "ollama", "--config",
                                 cfg_path, "--no-color"])
        self.assertEqual(rc, 1)
        self.assertIn("ollama pull", out)

    def test_llamacpp_sets_keys_and_saves(self):
        probe = {"found": True, "ready": True,
                 "base_url": "http://127.0.0.1:8080",
                 "models": ["qwen2.5-7b-q4"], "model": "qwen2.5-7b-q4",
                 "props_model": "qwen2.5-7b-q4", "detail": "up",
                 "via": "process"}
        with tempfile.TemporaryDirectory() as td:
            cfg_path = self._write_cfg(td, {
                "llm_provider": "ollama",
                "ollama": {"base_url": "http://127.0.0.1:11434",
                           "model": "llama3"}})
            with mock.patch.object(_llm, "probe_llamacpp",
                                   return_value=probe), \
                    mock.patch.object(_llm, "detect_llamacpp") as d:
                rc, out = self._cli(["--detect-llm", "llamacpp", "--config",
                                     cfg_path, "--no-color"])
                d.assert_not_called()
            self.assertEqual(rc, 0)
            self.assertIn("SET to llama.cpp", out)
            cfg = self._read_cfg(cfg_path)
            self.assertEqual(cfg["llm_provider"], "llamacpp")
            self.assertEqual(cfg["llamacpp_api_url"],
                             "http://127.0.0.1:8080/v1")
            self.assertEqual(cfg["llamacpp_model"], "qwen2.5-7b-q4")
            # the Ollama keys survive the MERGE save
            self.assertEqual(cfg["ollama"]["model"], "llama3")

    def test_llamacpp_not_found_rc1_with_command(self):
        miss = {"found": False, "models": [], "model": None,
                "props_model": None, "detail": "no answer",
                "base_url": "http://127.0.0.1:8080"}
        with tempfile.TemporaryDirectory() as td:
            cfg_path = self._write_cfg(td, {"llm_provider": "ollama"})
            with mock.patch.object(_llm, "probe_llamacpp",
                                   return_value=miss), \
                    mock.patch.object(_llm, "detect_llamacpp",
                                      return_value=None):
                rc, out = self._cli(["--detect-llm", "llamacpp", "--config",
                                     cfg_path, "--no-color"])
        self.assertEqual(rc, 1)
        self.assertIn("No llama.cpp server found", out)
        self.assertIn("llama-server -m", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
