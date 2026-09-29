#!/usr/bin/env python3
"""
test_connection.py — v0.17.0: the unified Test Connection prober.

Owner request 2026-09-29: "There is a button in the main GUI (we need same
thing in cli as well), which is called Test Connection. It must test connect
this and show in logs: 1. Vaults are found. and ready to be input to.
2. Telegram is connected (bot) and account login. 3. LLM whether via API,
ollama, llama.cpp 4. github repo's are ready (token working). THIS WAY USER
IS ENSURED THAT EVERYTHING IS UP AND READY."

Covers, all against LOCAL stdlib http.servers / temp dirs / canned configs —
no network beyond 127.0.0.1, no Telegram, no GUI shown:
  * vault_writable — the repo's FIRST writability probe (write+delete a
    temp file; never leaves a file behind; reports the exception class)
  * check_vault / check_vaults — the github/websites/manual matrix: found +
    writable, missing on disk, will-be-created, unwritable (chmod), not
    set, pipeline on/off variants
  * check_ollama — /api/tags reachable + models + configured-model check;
    no models; dead address
  * check_cloud — no URL; /v1/models listed / NOT listed / hidden list;
    dead endpoint (through the REAL preflight_openai)
  * check_llamacpp — a fake llama-server (/props + /health + /v1/models):
    up + model; still-loading; nothing running (scan monkeypatched away)
  * check_github — against a fake api.github.com: valid token + private /
    PUBLIC (should be private!) / 404 (will be created by the seal) repos;
    401 rejected; unreachable; no token; vault seal disabled
  * check_telegram_local — credentials / session file / bot / proxy rows;
    proxy reachable (live socket listener) vs dead port vs disabled
  * telegram_live_result — bot-queue and account-preview success shapes,
    the session-not-found mapping, generic failures
  * summarize — the verdict math (ready/total, error > warn > ok)
  * run_local_checks — 4 sections in order, callbacks, hostile config
    never raises
  * the GUI battery job (_connection_battery_job) + wiring (button text,
    menu entry, lock owner, the live leg methods)
  * the CLI: --test-connection parses + dispatches; missing config rc=1;
    a full run against a fake Ollama server with the live-Telegram probe
    mocked — exit code reflects the verdict level

Headless-safe: QT_QPA_PLATFORM=offscreen, no GUI is ever shown.
"""

import contextlib
import inspect
import io
import json
import os
import shutil
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gitcurator.core import connection_check as cc
from gitcurator.core import llm_client as _llm


# ---------------------------------------------------------------------------
# Fakes: one JSON-route server (ollama / cloud / github / llama.cpp shapes)
# ---------------------------------------------------------------------------

class _FakeJSONHandler(BaseHTTPRequestHandler):
    """GET routes from ``server.routes`` — ``{path: (code, payload)}``;
    records every request path in ``server.requests``."""

    def do_GET(self):
        srv = self.server
        path = self.path.split("?")[0]
        srv.requests.append(path)
        route = getattr(srv, "routes", {}).get(path)
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
    srv.requests = []
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


_LLMACPP_PROPS = {
    "model_path": "models/qwen2.5-3b-instruct-q4_k_m.gguf",
    "model_alias": "qwen2.5-3b",
    "total_slots": 1,
    "default_generation_settings": {"temperature": 0.8, "n_predict": -1},
}


# ---------------------------------------------------------------------------
# 1) Vaults
# ---------------------------------------------------------------------------

class TestVaultWritable(unittest.TestCase):

    def test_writable_dir_ok_and_leaves_no_file(self):
        with tempfile.TemporaryDirectory() as td:
            ok, why = cc.vault_writable(td)
            self.assertTrue(ok, why)
            leftovers = [n for n in os.listdir(td)
                         if n.startswith(".gc-write-test-")]
            self.assertEqual(leftovers, [])

    def test_nonexistent_dir_fails_with_reason(self):
        ok, why = cc.vault_writable("/nonexistent/no/such/dir")
        self.assertFalse(ok)
        self.assertTrue(why, "the failure detail must carry the reason")


class TestVaultChecks(unittest.TestCase):

    def test_github_vault_not_set_is_error(self):
        r = cc.check_vault("", "github", "GitHub vault")
        self.assertEqual(r["level"], cc.LEVEL_ERROR)
        self.assertIn("not set", r["detail"])

    def test_github_vault_missing_on_disk_is_error(self):
        r = cc.check_vault("/nonexistent/vault", "github", "GitHub vault")
        self.assertEqual(r["level"], cc.LEVEL_ERROR)
        self.assertIn("missing on disk", r["detail"])

    def test_github_vault_found_writable_is_ok(self):
        with tempfile.TemporaryDirectory() as td:
            os.mkdir(os.path.join(td, ".obsidian"))
            r = cc.check_vault(td, "github", "GitHub vault")
            self.assertEqual(r["level"], cc.LEVEL_OK)
            self.assertIn("writable", r["detail"])
            self.assertIn("ready to receive notes", r["detail"])
            self.assertIn("Obsidian vault", r["detail"])

    def test_github_vault_readonly_is_error(self):
        td = tempfile.mkdtemp()
        os.chmod(td, 0o555)
        try:
            r = cc.check_vault(td, "github", "GitHub vault")
        finally:
            os.chmod(td, 0o755)
            shutil.rmtree(td)
        self.assertEqual(r["level"], cc.LEVEL_ERROR)
        self.assertIn("NOT writable", r["detail"])

    def test_websites_vault_will_be_created(self):
        with tempfile.TemporaryDirectory() as td:
            future = os.path.join(td, "websites-vault")
            r = cc.check_vault(future, "websites", "Websites vault")
            self.assertEqual(r["level"], cc.LEVEL_INFO)
            self.assertIn("created by the pipeline", r["detail"])

    def test_websites_vault_parent_missing_is_error(self):
        r = cc.check_vault("/nonexistent/parent/websites", "websites",
                           "Websites vault")
        self.assertEqual(r["level"], cc.LEVEL_ERROR)

    def test_manual_vault_not_found_is_warn_never_created(self):
        with tempfile.TemporaryDirectory() as td:
            r = cc.check_vault(os.path.join(td, "manual"), "manual",
                               "Manual vault")
            self.assertEqual(r["level"], cc.LEVEL_WARN)
            self.assertIn("never creates it", r["detail"])

    def test_manual_vault_not_set_is_info_optional(self):
        r = cc.check_vault("", "manual", "Manual vault")
        self.assertEqual(r["level"], cc.LEVEL_INFO)


class TestCheckVaultsMatrix(unittest.TestCase):

    def test_pipeline_off_not_set_is_info(self):
        rows = cc.check_vaults({"pipelines": {"websites": False}})
        names = [(r["name"], r["level"]) for r in rows]
        self.assertEqual(names[0], ("GitHub vault", cc.LEVEL_ERROR))
        self.assertEqual(names[1], ("Websites vault", cc.LEVEL_INFO))
        self.assertEqual(names[2], ("Manual vault", cc.LEVEL_INFO))

    def test_pipeline_on_not_set_is_warn(self):
        rows = cc.check_vaults({"pipelines": {"websites": True}})
        self.assertEqual(rows[1]["name"], "Websites vault")
        self.assertEqual(rows[1]["level"], cc.LEVEL_WARN)

    def test_pipeline_off_but_path_set_is_info(self):
        rows = cc.check_vaults({"pipelines": {"websites": False},
                                "website_vault_path": "/some/path"})
        self.assertEqual(rows[1]["level"], cc.LEVEL_INFO)
        self.assertIn("pipeline is OFF", rows[1]["detail"])


# ---------------------------------------------------------------------------
# 2) LLM
# ---------------------------------------------------------------------------

class TestCheckOllama(unittest.TestCase):

    def test_up_with_configured_model(self):
        srv, url = _start_json_server({
            "/api/tags": (200, {"models": [{"name": "llama3:latest"},
                                           {"name": "qwen:7b"}]})})
        self.addCleanup(_stop_json_server, srv)
        r = cc.check_ollama(url, "qwen:7b")
        self.assertEqual(r["level"], cc.LEVEL_OK)
        self.assertIn("2 model(s)", r["detail"])
        self.assertIn("'qwen:7b' ready", r["detail"])

    def test_configured_model_not_pulled_is_warn(self):
        srv, url = _start_json_server({
            "/api/tags": (200, {"models": [{"name": "llama3:latest"}]})})
        self.addCleanup(_stop_json_server, srv)
        r = cc.check_ollama(url, "mistral")
        self.assertEqual(r["level"], cc.LEVEL_WARN)
        self.assertIn("NOT pulled", r["detail"])

    def test_no_models_is_error(self):
        srv, url = _start_json_server({"/api/tags": (200, {"models": []})})
        self.addCleanup(_stop_json_server, srv)
        r = cc.check_ollama(url)
        self.assertEqual(r["level"], cc.LEVEL_ERROR)
        self.assertIn("NO models", r["detail"])

    def test_dead_address_is_error(self):
        r = cc.check_ollama(_dead_url())
        self.assertEqual(r["level"], cc.LEVEL_ERROR)
        self.assertIn("not reachable", r["detail"])


class TestCheckCloud(unittest.TestCase):

    def test_no_url_is_error(self):
        r = cc.check_cloud({})
        self.assertEqual(r["level"], cc.LEVEL_ERROR)
        self.assertIn("no API URL", r["detail"])

    def test_model_listed_is_ok(self):
        srv, url = _start_json_server({
            "/v1/models": (200, {"object": "list",
                                 "data": [{"id": "zai-glm"}]})})
        self.addCleanup(_stop_json_server, srv)
        r = cc.check_cloud({"cloud_api_url": url + "/v1",
                            "cloud_api_key": "", "cloud_model": "zai-glm"})
        self.assertEqual(r["level"], cc.LEVEL_OK)
        self.assertIn("'zai-glm' listed", r["detail"])

    def test_model_not_listed_is_warn(self):
        srv, url = _start_json_server({
            "/v1/models": (200, {"object": "list",
                                 "data": [{"id": "other"}]})})
        self.addCleanup(_stop_json_server, srv)
        r = cc.check_cloud({"cloud_api_url": url + "/v1",
                            "cloud_api_key": "", "cloud_model": "zai-glm"})
        self.assertEqual(r["level"], cc.LEVEL_WARN)
        self.assertIn("NOT in the list", r["detail"])

    def test_dead_endpoint_is_error(self):
        r = cc.check_cloud({"cloud_api_url": _dead_url() + "/v1",
                            "cloud_api_key": "", "cloud_model": "m"})
        self.assertEqual(r["level"], cc.LEVEL_ERROR)


class TestCheckLlamacpp(unittest.TestCase):

    def _fake(self, health=(200, {"status": "ok"})):
        return _start_json_server({
            "/props": (200, dict(_LLMACPP_PROPS)),
            "/health": health,
            "/v1/models": (200, {"object": "list",
                                 "data": [{"id": "qwen2.5-3b",
                                           "object": "model",
                                           "owned_by": "llama.cpp"}]}),
        })

    def test_up_with_model_is_ok(self):
        srv, url = self._fake()
        self.addCleanup(_stop_json_server, srv)
        r = cc.check_llamacpp({"llamacpp_api_url": url})
        self.assertEqual(r["level"], cc.LEVEL_OK)
        self.assertIn("model 'qwen2.5-3b'", r["detail"])

    def test_still_loading_noted(self):
        srv, url = self._fake(health=(503, {"error": "loading model"}))
        self.addCleanup(_stop_json_server, srv)
        r = cc.check_llamacpp({"llamacpp_api_url": url})
        self.assertEqual(r["level"], cc.LEVEL_OK)
        self.assertIn("still loading", r["detail"])

    def test_nothing_running_is_error_with_command_hint(self):
        # the port scan is llm_client's business (79 tests there); here we
        # pin it to "nothing" so the check reports the clear error.
        with mock.patch.object(_llm, "detect_llamacpp",
                               lambda *a, **k: None):
            r = cc.check_llamacpp(
                {"llamacpp_api_url": _dead_url() + "/v1"})
        self.assertEqual(r["level"], cc.LEVEL_ERROR)
        self.assertIn("llama-server -m", r["detail"])

    def test_check_llm_hint_line_when_provider_down(self):
        with mock.patch.object(_llm, "detect_llamacpp",
                               lambda *a, **k: None):
            out = cc.check_llm({"llm_provider": "llamacpp",
                                "llamacpp_api_url": _dead_url() + "/v1"})
        self.assertEqual(len(out), 2)  # the error + the alternatives hint
        self.assertEqual(out[1]["name"], "Hint")
        self.assertEqual(out[1]["level"], cc.LEVEL_INFO)


# ---------------------------------------------------------------------------
# 3) GitHub
# ---------------------------------------------------------------------------

class TestCheckGithub(unittest.TestCase):

    def _cfg(self, **kw):
        base = {"github_token": "tok",
                "vaultseal": {"enabled": True, "repo_name": "backup"},
                "pipelines": {"websites": False}}
        base.update(kw)
        return base

    def test_no_token_is_warn_single_row(self):
        rows = cc.check_github({"github_token": ""})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["level"], cc.LEVEL_WARN)
        self.assertIn("not set", rows[0]["detail"])

    def test_valid_token_private_repo_ready(self):
        srv, url = _start_json_server({
            "/user": (200, {"login": "testuser"}),
            "/repos/testuser/backup": (200, {"private": True}),
        })
        self.addCleanup(_stop_json_server, srv)
        rows = cc.check_github(self._cfg(), api_base=url)
        self.assertEqual([r["level"] for r in rows],
                         [cc.LEVEL_OK, cc.LEVEL_OK])
        self.assertIn("account testuser", rows[0]["detail"])
        self.assertIn("backup ready (private)", rows[1]["detail"])

    def test_public_repo_should_be_private_warn(self):
        srv, url = _start_json_server({
            "/user": (200, {"login": "testuser"}),
            "/repos/testuser/backup": (200, {"private": False}),
        })
        self.addCleanup(_stop_json_server, srv)
        rows = cc.check_github(self._cfg(), api_base=url)
        self.assertEqual(rows[1]["level"], cc.LEVEL_WARN)
        self.assertIn("PUBLIC", rows[1]["detail"])

    def test_missing_repo_created_by_next_seal(self):
        srv, url = _start_json_server({
            "/user": (200, {"login": "testuser"}),
            "/repos/testuser/backup": (404, {"message": "Not Found"}),
        })
        self.addCleanup(_stop_json_server, srv)
        rows = cc.check_github(self._cfg(), api_base=url)
        self.assertEqual(rows[1]["level"], cc.LEVEL_INFO)
        self.assertIn("created automatically", rows[1]["detail"])

    def test_rejected_token_401(self):
        srv, url = _start_json_server({
            "/user": (401, {"message": "Bad credentials"}),
        })
        self.addCleanup(_stop_json_server, srv)
        rows = cc.check_github(self._cfg(), api_base=url)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["level"], cc.LEVEL_ERROR)
        self.assertIn("REJECTED", rows[0]["detail"])

    def test_unreachable(self):
        rows = cc.check_github(self._cfg(), api_base=_dead_url())
        self.assertEqual(rows[0]["level"], cc.LEVEL_ERROR)
        self.assertIn("unreachable", rows[0]["detail"])

    def test_seal_disabled_no_repo_check(self):
        srv, url = _start_json_server({
            "/user": (200, {"login": "testuser"}),
        })
        self.addCleanup(_stop_json_server, srv)
        rows = cc.check_github(
            self._cfg(vaultseal={"enabled": False, "repo_name": "backup"}),
            api_base=url)
        self.assertEqual([r["name"] for r in rows],
                         ["GitHub token", "Vault seal"])
        self.assertEqual(rows[1]["level"], cc.LEVEL_INFO)
        self.assertEqual(srv.requests, ["/user"])  # no repo call happened

    def test_websites_repo_checked_when_pipeline_on(self):
        srv, url = _start_json_server({
            "/user": (200, {"login": "testuser"}),
            "/repos/testuser/backup": (200, {"private": True}),
            "/repos/testuser/websites-dir": (200, {"private": True}),
        })
        self.addCleanup(_stop_json_server, srv)
        rows = cc.check_github(
            self._cfg(pipelines={"websites": True},
                      website_repo_name="websites-dir"),
            api_base=url)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[2]["name"], "Websites repo")
        self.assertEqual(rows[2]["level"], cc.LEVEL_OK)


# ---------------------------------------------------------------------------
# 4) Telegram
# ---------------------------------------------------------------------------

class TestTelegramLocal(unittest.TestCase):

    def _cfg(self, **kw):
        base = {"telegram_api_id": "12345",
                "telegram_api_hash": "abcdef",
                "telegram_phone": "+989123456789",
                "bot_username": "mybot",
                "proxy": {"enabled": False},
                "__session__": None}
        base.update(kw)
        return base

    def test_full_local_rows_proxy_disabled(self):
        with tempfile.TemporaryDirectory() as td:
            sess = os.path.join(td, "session.session")
            open(sess, "w").close()
            rows = cc.check_telegram_local(self._cfg(), session_file=sess)
        self.assertEqual([r["name"] for r in rows],
                         ["Credentials", "Account session", "Bot", "Proxy"])
        self.assertEqual(rows[0]["level"], cc.LEVEL_OK)
        self.assertEqual(rows[1]["level"], cc.LEVEL_OK)
        self.assertEqual(rows[2]["level"], cc.LEVEL_OK)
        self.assertIn("@mybot", rows[2]["detail"])
        self.assertEqual(rows[3]["level"], cc.LEVEL_WARN)  # disabled

    def test_credentials_incomplete_is_error(self):
        rows = cc.check_telegram_local(
            {"telegram_api_id": "", "telegram_api_hash": "",
             "telegram_phone": "", "proxy": {"enabled": False}},
            session_file="/nonexistent/session.session")
        self.assertEqual(rows[0]["level"], cc.LEVEL_ERROR)
        self.assertIn("api id", rows[0]["detail"])

    def test_session_missing_is_warn(self):
        rows = cc.check_telegram_local(self._cfg(),
                                       session_file="/nonexistent/sess")
        self.assertEqual(rows[1]["level"], cc.LEVEL_WARN)
        self.assertIn("not logged in", rows[1]["detail"])

    def test_bot_missing_is_warn(self):
        rows = cc.check_telegram_local(self._cfg(bot_username=""),
                                       session_file="/nonexistent/sess")
        self.assertEqual(rows[2]["level"], cc.LEVEL_WARN)

    def test_proxy_reachable_is_ok(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        self.addCleanup(listener.close)
        port = listener.getsockname()[1]
        rows = cc.check_telegram_local(
            self._cfg(proxy={"enabled": True, "host": "127.0.0.1",
                             "port": port, "type": "socks5"}),
            session_file="/nonexistent/sess")
        self.assertEqual(rows[3]["level"], cc.LEVEL_OK)
        self.assertIn("reachable", rows[3]["detail"])

    def test_proxy_dead_is_error(self):
        rows = cc.check_telegram_local(
            self._cfg(proxy={"enabled": True, "host": "127.0.0.1",
                             "port": 1, "type": "socks5"}),
            session_file="/nonexistent/sess")
        self.assertEqual(rows[3]["level"], cc.LEVEL_ERROR)
        self.assertIn("UNREACHABLE", rows[3]["detail"])


class TestTelegramLiveResult(unittest.TestCase):

    def test_bot_mode_success_counts_links(self):
        r = cc.telegram_live_result(
            {"success": True, "urls": ["a", "b"], "max_message_id": 42},
            "bot")
        self.assertEqual(r["level"], cc.LEVEL_OK)
        self.assertIn("account login OK", r["detail"])
        self.assertIn("2 link(s) waiting", r["detail"])
        self.assertIn("id 42", r["detail"])

    def test_account_mode_success(self):
        r = cc.telegram_live_result(
            {"success": True, "preview": {"total_count": 7}}, "account")
        self.assertEqual(r["level"], cc.LEVEL_OK)
        self.assertIn("account login OK", r["detail"])
        self.assertIn("7 message(s)", r["detail"])

    def test_missing_session_maps_to_login_hint(self):
        r = cc.telegram_live_result(
            {"success": False,
             "error": "Session file not found: session.session. "
                      "Run 'python test.py' first"}, "bot")
        self.assertEqual(r["level"], cc.LEVEL_ERROR)
        self.assertIn("not logged in", r["detail"])

    def test_generic_failure_passes_detail(self):
        r = cc.telegram_live_result({"success": False,
                                     "error": "proxy dead"}, "account")
        self.assertEqual(r["level"], cc.LEVEL_ERROR)
        self.assertIn("proxy dead", r["detail"])

    def test_non_dict_result_never_raises(self):
        r = cc.telegram_live_result(None, "bot")
        self.assertEqual(r["level"], cc.LEVEL_ERROR)
        self.assertIn("unexpected", r["detail"])


# ---------------------------------------------------------------------------
# Battery + verdict
# ---------------------------------------------------------------------------

class TestRunLocalChecks(unittest.TestCase):

    def test_four_sections_in_order_with_callbacks(self):
        sections_seen, results_seen = [], []
        sections = cc.run_local_checks(
            {}, on_section=lambda t, i, n: sections_seen.append((t, i, n)),
            on_result=results_seen.append)
        self.assertEqual([t for t, _ in sections],
                         ["Vaults", "LLM", "GitHub", "Telegram"])
        self.assertEqual(sections_seen[0], ("Vaults", 1, 4))
        self.assertEqual(sections_seen[-1], ("Telegram", 4, 4))
        self.assertTrue(results_seen)
        for r in results_seen:
            self.assertIn(r["level"], ("ok", "warn", "error", "info"))

    def test_hostile_config_never_raises(self):
        sections = cc.run_local_checks({"pipelines": "junk",
                                        "ollama": "junk",
                                        "vaultseal": 3,
                                        "proxy": "junk"})
        self.assertEqual(len(sections), 4)

    def test_sections_are_mutable_for_the_live_leg(self):
        sections = cc.run_local_checks({})
        sections[-1][1].append({"name": "Live connection",
                                "level": cc.LEVEL_OK, "detail": "x"})
        self.assertEqual(sections[-1][1][-1]["name"], "Live connection")


class TestSummarize(unittest.TestCase):

    def _ok(self):
        return {"name": "x", "level": cc.LEVEL_OK, "detail": ""}

    def test_all_ok(self):
        s = cc.summarize([["A", [self._ok()]], ["B", [self._ok()]]])
        self.assertEqual(s["level"], cc.LEVEL_OK)
        self.assertEqual(s["ready"], 2)
        self.assertIn("ALL SYSTEMS READY", s["headline"])

    def test_warn_beats_ok(self):
        s = cc.summarize([["A", [self._ok()]],
                          ["B", [{"name": "x", "level": cc.LEVEL_WARN,
                                  "detail": ""}]]])
        self.assertEqual(s["level"], cc.LEVEL_WARN)
        self.assertEqual(s["ready"], 1)
        self.assertIn("1 warning(s)", s["headline"])

    def test_error_beats_warn(self):
        s = cc.summarize([
            ["A", [{"name": "x", "level": cc.LEVEL_WARN, "detail": ""}]],
            ["B", [{"name": "x", "level": cc.LEVEL_ERROR, "detail": ""}]]])
        self.assertEqual(s["level"], cc.LEVEL_ERROR)
        self.assertIn("1 error(s)", s["headline"])

    def test_empty_sections_safe(self):
        s = cc.summarize([])
        self.assertEqual(s["level"], cc.LEVEL_OK)


# ---------------------------------------------------------------------------
# GUI battery + wiring
# ---------------------------------------------------------------------------

class _FakeLogSignal:
    def __init__(self):
        self.lines = []

    def emit(self, msg, level):
        self.lines.append((str(msg), str(level)))


class TestGuiBattery(unittest.TestCase):

    def test_battery_streams_and_returns_sections(self):
        import gitcurator.gui.app as gui_app
        sig = _FakeLogSignal()
        with tempfile.TemporaryDirectory() as td:
            cfg = {"vault_path": td, "pipelines": {"websites": False}}
            out = gui_app._connection_battery_job(cfg, sig)
        self.assertTrue(out.get("success"))
        sections = out.get("sections") or []
        self.assertEqual([t for t, _ in sections],
                         ["Vaults", "LLM", "GitHub", "Telegram"])
        headers = [m for m, _lvl in sig.lines if m.startswith("📋 [")]
        self.assertEqual(len(headers), 4)
        rendered = [m for m, _lvl in sig.lines if m.startswith("   ")]
        self.assertTrue(rendered)
        # level mapping reaches the GUI vocabulary
        levels = {lvl for _m, lvl in sig.lines}
        self.assertTrue(levels <= {"info", "warning", "error", "success"})

    def test_battery_hostile_config_never_raises(self):
        import gitcurator.gui.app as gui_app
        sig = _FakeLogSignal()
        out = gui_app._connection_battery_job({"pipelines": None,
                                               "ollama": None}, sig)
        self.assertTrue(out.get("success"))


class TestGuiWiring(unittest.TestCase):

    def test_button_and_menu_wired_to_test_all(self):
        import gitcurator.gui.app as gui_app
        src = inspect.getsource(gui_app.MainWindow.test_all)
        self.assertIn('_acquire_telegram_lock("connection_check")', src)
        self.assertIn("_connection_battery_job", src)
        self.assertIn("_cc_telegram_leg", src)
        ui_src = inspect.getsource(gui_app.MainWindow.initUI)
        self.assertIn('QPushButton("Test Connection")', ui_src)
        self.assertIn("Test Connection (all systems)", ui_src)

    def test_live_leg_and_finish_methods_exist(self):
        import gitcurator.gui.app as gui_app
        self.assertTrue(callable(gui_app.MainWindow._cc_telegram_leg))
        self.assertTrue(callable(gui_app.MainWindow._cc_finish))
        leg_src = inspect.getsource(gui_app.MainWindow._cc_telegram_leg)
        self.assertIn("_bot_queue_job", leg_src)
        self.assertIn("_telegram_test_job", leg_src)
        self.assertIn('_keep_worker(worker, owner="connection_check")', leg_src)

    def test_battery_crash_never_reports_ready(self):
        # a crashed battery must surface as an error section, never an
        # empty-sections 'ALL SYSTEMS READY' verdict (GUI-smoke-caught)
        import gitcurator.gui.app as gui_app
        src = inspect.getsource(gui_app.MainWindow.test_all)
        self.assertIn("The battery itself crashed", src)
        self.assertIn('"level": "error"', src)
        self.assertIn('get("sections")', src)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestCli(unittest.TestCase):

    def _cli(self, argv):
        from gitcurator import cli
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.cli_main(argv)
        return rc, buf.getvalue()

    def test_flag_parses_and_help_lists_it(self):
        from gitcurator import cli
        args = cli.build_parser().parse_args(["--test-connection"])
        self.assertTrue(args.test_connection)
        h = io.StringIO()
        with contextlib.redirect_stdout(h):
            cli.build_parser().print_help()
        self.assertIn("--test-connection", h.getvalue())

    def test_missing_config_rc1(self):
        rc, out = self._cli(["--test-connection", "--config",
                             "/nonexistent/config.json", "--no-color"])
        self.assertEqual(rc, 1)
        self.assertIn("No config", out)

    def test_full_run_dead_services_rc1(self):
        # vault ok + ollama (dead port) error + no github token (warn) +
        # telegram creds incomplete (error, live skipped) → verdict error
        with tempfile.TemporaryDirectory() as td:
            cfg_path = os.path.join(td, "config.json")
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump({"vault_path": td,
                           "llm_provider": "ollama",
                           "ollama": {"base_url": _dead_url(),
                                      "model": "llama3"},
                           "github_token": "",
                           "proxy": {"enabled": False}}, f)
            rc, out = self._cli(["--test-connection", "--config", cfg_path,
                                 "--no-color"])
        self.assertEqual(rc, 1)
        self.assertIn("[1/4] Vaults", out)
        self.assertIn("[2/4] LLM", out)
        self.assertIn("[3/4] GitHub", out)
        self.assertIn("[4/4] Telegram", out)
        self.assertIn("VERDICT", out)
        self.assertIn("ready to receive notes", out)
        self.assertIn("skipped — credentials incomplete", out)

    def test_full_run_ok_with_fake_ollama_and_mocked_live(self):
        # vault ok + fake ollama up with the model + github token absent
        # (warn) + telegram local warns + live probe MOCKED ok → warn
        # verdict → exit 0 (warnings do not fail)
        from gitcurator import cli
        srv, url = _start_json_server({
            "/api/tags": (200, {"models": [{"name": "llama3:latest"}]})})
        self.addCleanup(_stop_json_server, srv)
        with tempfile.TemporaryDirectory() as td:
            cfg_path = os.path.join(td, "config.json")
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump({"vault_path": td,
                           "llm_provider": "ollama",
                           "ollama": {"base_url": url, "model": "llama3:latest"},
                           "github_token": "",
                           "telegram_api_id": "123",
                           "telegram_api_hash": "abc",
                           "telegram_phone": "+98912",
                           "bot_username": "mybot",
                           "proxy": {"enabled": False}}, f)
            with mock.patch.object(
                    cli, "_connection_live_telegram",
                    return_value={"success": True, "urls": ["u1"],
                                  "max_message_id": 9}):
                rc, out = self._cli(["--test-connection", "--config",
                                     cfg_path, "--no-color"])
        self.assertEqual(rc, 0)
        self.assertIn("'llama3:latest' ready", out)
        self.assertIn("bot queue readable (1 link(s) waiting", out)
        self.assertIn("warning(s)", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
