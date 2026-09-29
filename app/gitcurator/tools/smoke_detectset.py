#!/usr/bin/env python3
"""v0.18.0 GUI smoke — the Detect & Set fast lane, clicked end-to-end
offscreen against local fake servers. Exercises what unit tests cannot:
real MainWindow + TestWorker threading + queued signals + widget updates.

Run: QT_QPA_PLATFORM=offscreen python gitcurator/tools/smoke_detectset.py
"""
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", ".."))


class _FakeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        route = getattr(self.server, "routes", {}).get(
            self.path.split("?")[0])
        if route is None:
            self._reply(404, {"error": "not found"})
            return
        self._reply(200, route)

    def _reply(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a):
        pass


def _serve(routes):
    srv = HTTPServer(("127.0.0.1", 0), _FakeHandler)
    srv.routes = routes
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


def main():
    from PyQt6.QtWidgets import QApplication
    import gitcurator.gui.app as gui_app

    app = QApplication(sys.argv)

    # ---- fake engines ----
    ollama_srv, ollama_url = _serve({
        "/api/tags": {"models": [{"name": "llama3.1:8b"},
                                 {"name": "qwen2.5:7b"}]}})
    lcp_srv, lcp_url = _serve({
        "/props": {"model_alias": "Qwen2.5-7B-Q4_K_M",
                   "model_path": "models/qwen2.5-7b-q4_k_m.gguf",
                   "default_generation_settings": {}},
        "/health": {"status": "ok"},
        "/v1/models": {"data": [{"id": "Qwen2.5-7B-Q4_K_M",
                                 "owned_by": "llama.cpp"}]},
    })

    # Never touch the real config.json: swap the module constant BEFORE
    # MainWindow.__init__ loads it, and neuter save_config after.
    smoke_dir = tempfile.mkdtemp(prefix="gk-smoke-")
    smoke_cfg = os.path.join(smoke_dir, "config.json")
    with open(smoke_cfg, "w", encoding="utf-8") as f:
        json.dump({"llm_provider": "cloud", "cloud_api_url": "",
                   "cloud_api_key": "", "cloud_model": "",
                   "ollama": {"base_url": "http://127.0.0.1:11434",
                              "model": "old-model"},
                   "llamacpp_api_url": "http://127.0.0.1:8080/v1",
                   "llamacpp_model": ""}, f)
    gui_app.CONFIG_FILE = smoke_cfg

    win = gui_app.MainWindow()
    logs = []
    win.log_message = lambda msg, level="info": logs.append((str(msg),
                                                             str(level)))
    saved = {"n": 0}
    win.save_config = lambda: saved.__setitem__("n", saved["n"] + 1)

    # the buttons exist on the main screen
    assert win.detect_set_ollama_btn.text() == "🧠 Detect & Set Ollama", \
        win.detect_set_ollama_btn.text()
    assert win.detect_set_llamacpp_btn.text() == "🦙 Detect & Set llama.cpp"
    print("OK buttons on main screen:",
          win.detect_set_ollama_btn.text(), "+",
          win.detect_set_llamacpp_btn.text())

    # point the LIVE widgets at the fakes (the snapshot must honor them)
    win.ollama_url.setText(ollama_url)
    win.llamacpp_api_url.setText(lcp_url + "/v1")

    # the model menu: auto-pick the FIRST model (records the call)
    dialogs = []

    def _auto_dialog(provider, base_url, models, current=""):
        dialogs.append((provider, base_url, list(models), current))
        return models[0]

    win._quick_model_dialog = _auto_dialog

    def wait_done():
        deadline = time.time() + 20
        while time.time() < deadline:
            app.processEvents()
            if win._active_test_workers and all(
                    w.isFinished() for w in win._active_test_workers):
                for _ in range(25):
                    app.processEvents()  # drain queued signals
                    time.sleep(0.02)
                return True
            time.sleep(0.02)
        return False

    # ---- click 1: Detect & Set Ollama (multi-model → menu) ----
    win.quick_detect_set_ollama()
    assert wait_done(), "ollama detect worker never finished"
    assert any("SET to Ollama" in m for m, _ in logs), logs
    assert win.config["llm_provider"] == "ollama"
    assert win.config["ollama"]["base_url"] == ollama_url
    assert win.config["ollama"]["model"] == "llama3.1:8b"
    assert win.ollama_model.currentText() == "llama3.1:8b"
    assert win.llm_provider_ollama.isChecked()
    assert len(dialogs) == 1 and dialogs[0][0] == "ollama"
    assert dialogs[0][2] == ["llama3.1:8b", "qwen2.5:7b"]
    assert saved["n"] == 1
    print("OK Detect & Set Ollama: menu offered, provider+model+URL set, "
          "saved")

    # ---- click 2: Detect & Set llama.cpp (configured URL → single model) --
    logs.clear()
    win.quick_detect_set_llamacpp()
    assert wait_done(), "llamacpp detect worker never finished"
    assert any("SET to llama.cpp" in m for m, _ in logs), logs
    assert win.config["llm_provider"] == "llamacpp"
    assert win.config["llamacpp_api_url"] == lcp_url + "/v1"
    assert win.config["llamacpp_model"] == "Qwen2.5-7B-Q4_K_M"
    assert win.llamacpp_model.currentText() == "Qwen2.5-7B-Q4_K_M"
    assert win.llm_provider_llamacpp.isChecked()
    assert len(dialogs) == 1  # single model → NO second menu
    assert saved["n"] == 2
    print("OK Detect & Set llama.cpp: configured-URL catch, model set "
          "from /props+/v1/models, saved")

    # ---- click 3: back to Ollama — the BOTH-ENGINES switching story ----
    logs.clear()
    win.quick_detect_set_ollama()
    assert wait_done(), "second ollama worker never finished"
    assert win.config["llm_provider"] == "ollama"
    assert saved["n"] == 3
    print("OK switch back to Ollama — the both-engines round trip works")

    # ---- guard: a running batch refuses the switch ----
    logs.clear()

    class _FakeRunning:
        def isFinished(self):
            return False
    win.worker = _FakeRunning()   # simulate a running BATCH
    win.quick_detect_set_llamacpp()
    assert any("batch is running" in m for m, _ in logs), logs
    win.worker = None
    print("OK batch-running guard refuses the switch")

    # ---- dead engine: clear error + remedy, nothing changed ----
    logs.clear()
    win.ollama_url.setText("http://127.0.0.1:1")  # dead port
    win.quick_detect_set_ollama()
    assert wait_done(), "dead-ollama worker never finished"
    assert any("not running" in m for m, _ in logs), logs
    assert any("Start Server" in m for m, _ in logs)
    assert win.config["llm_provider"] == "ollama"  # unchanged by failure
    assert saved["n"] == 3
    print("OK dead engine: clear error + remedy, config untouched")

    print("\nALL SMOKE CHECKS PASSED")
    ollama_srv.shutdown(); ollama_srv.server_close()
    lcp_srv.shutdown(); lcp_srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
