#!/usr/bin/env python3
"""
test_sealfix.py — v0.21.0: the seal truth + the full-accounting report.

The owner's 2026-09-30 02:17 batch log (v0.20.0, Windows) exposed four
things this suite pins:

1. The websites-vault seal push was REJECTED FOREVER
   (``! [rejected] HEAD -> main (fetch first)``) because the private
   mirror repo had been doubling as the cloud-gate mirror — the remote
   had 18 gate commits, the local had vault snapshots, no shared
   history, and ``_seal`` just did a plain push with no reconciliation.
   ``VaultSeal._push`` now reconciles: fetch → rebase (shared history)
   or merge --allow-unrelated-histories (fresh init / repurposed
   mirror) → push; anything that cannot reconcile cleanly lands on a
   ``seal-rescue/<ts>`` branch — main is NEVER force-pushed, local
   commits are never lost. All proven here against REAL local bare
   repos.

2. "GitHub processed: 0 / Non-GitHub recorded: 0 / ✅ ALL LINKS
   VERIFIED" while the batch actually touched 271 links (8 GitHub
   dedup, 16 through the Websites pipeline, 247 blocked) — the report
   hid every websites/blocked outcome. ``LinkTracker.verify`` now
   buckets all of them and RECONCILES the sum against the total.

3. The bot's own auth links
   (``github-to-obsidian-bot…workers.dev/auth/?token=<64 hex>``) were
   FETCHED and stored (live tokens in _review notes + _inbox tables).
   ``web_self_domains`` never fetches them; the purge deletes their
   queued retries/placeholders; ``scrub_url_token`` keeps secret query
   values out of every stored row (and rewrites pre-v0.21.0 tables).

4. Ten of sixteen web fetches died at TLS through the proxy
   (``[SSL: UNEXPECTED_EOF_WHILE_READING]`` — the exit IP blocked by
   those sites' CDNs). ``fetch_url`` now falls back to one DIRECT
   attempt on connection-class proxy failures; both reasons surface
   when both lines fail.

Hermetic: local bare git repos, temp dirs, 127.0.0.1 sockets and
monkeypatched recorders. No network beyond loopback (the one
non-loopback fallback test skips when the machine has no usable
address). No GUI shown.
"""

import inspect
import ipaddress
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gitcurator.core import dryrun
from gitcurator.core import links as L
from gitcurator.core import web_fetch as wf
from gitcurator.core import website_pipeline as wp
from gitcurator.integrations import vaultseal as vs_mod
from gitcurator.gui.app import LinkTracker, write_inbox_links_by_platform


# ---------------------------------------------------------------------------
# git helpers (REAL local repos — the same porcelain VaultSeal drives)
# ---------------------------------------------------------------------------

def _run_git(cwd, *args):
    return subprocess.run(["git", "-C", cwd, *args],
                          capture_output=True, text=True, timeout=60)


def _init_repo(path, initial_file=None, content="init\n"):
    """A fresh git repo with one commit on main."""
    os.makedirs(path, exist_ok=True)
    _run_git(path, "init", "-b", "main")
    _run_git(path, "config", "user.email", "t@example.com")
    _run_git(path, "config", "user.name", "T")
    if initial_file is not None:
        with open(os.path.join(path, initial_file), "w",
                  encoding="utf-8") as f:
            f.write(content)
        _run_git(path, "add", "-A", "--", ".")
        _run_git(path, "commit", "-m", "init", "--quiet")
    return path


def _commit(path, fname, content, msg="change"):
    with open(os.path.join(path, fname), "w", encoding="utf-8") as f:
        f.write(content)
    _run_git(path, "add", "-A", "--", ".")
    _run_git(path, "commit", "-m", msg, "--quiet")


def _bare(path):
    subprocess.run(["git", "init", "--bare", "-b", "main", path],
                   capture_output=True, text=True, timeout=60)
    return path


def _remote_head(bare):
    out = subprocess.run(["git", "-C", bare, "rev-parse", "--short=7",
                          "main"], capture_output=True, text=True,
                         timeout=60)
    return out.stdout.strip()


def _remote_tree(bare):
    out = subprocess.run(["git", "-C", bare, "ls-tree", "-r", "--name-only",
                          "main"], capture_output=True, text=True,
                         timeout=60)
    return [ln for ln in out.stdout.splitlines() if ln.strip()]


def _remote_branches(bare):
    out = subprocess.run(["git", "-C", bare, "branch", "--format",
                          "%(refname:short)"], capture_output=True,
                         text=True, timeout=60)
    return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]


def _seal_for(vault_dir):
    return vs_mod.VaultSeal(vault_path=vault_dir, token="tok",
                            repo_name="mirror", auto_push=True)


# ---------------------------------------------------------------------------
# 1. VaultSeal push reconciliation
# ---------------------------------------------------------------------------

class TestSealPushReconcile(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sealpush-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fast_forward_to_empty_remote(self):
        bare = _bare(os.path.join(self.tmp, "mirror.git"))
        vault = _init_repo(os.path.join(self.tmp, "vault"),
                           initial_file="note.md", content="x" * 80)
        ok, err = _seal_for(vault)._push(bare)
        self.assertTrue(ok, err)
        self.assertIn("note.md", _remote_tree(bare))

    def test_push_twice_is_up_to_date(self):
        bare = _bare(os.path.join(self.tmp, "mirror.git"))
        vault = _init_repo(os.path.join(self.tmp, "vault"),
                           initial_file="note.md", content="x" * 80)
        vs = _seal_for(vault)
        self.assertTrue(vs._push(bare)[0])
        ok, err = vs._push(bare)
        self.assertTrue(ok, err)

    def test_diverged_shared_history_rebases_onto_remote(self):
        # The exact v0.20.0 websites-mirror shape, minus the unrelated
        # part: both sides share a base, the remote gained a commit we
        # lack (a web-UI edit / second machine), we gained a seal commit.
        bare = _bare(os.path.join(self.tmp, "mirror.git"))
        base = _init_repo(os.path.join(self.tmp, "base"),
                          initial_file="base.txt")
        _run_git(base, "push", bare, "HEAD:refs/heads/main")
        vault = os.path.join(self.tmp, "vault")
        _run_git(base, "clone", bare, vault)
        _run_git(vault, "config", "user.email", "t@example.com")
        _run_git(vault, "config", "user.name", "T")
        other = os.path.join(self.tmp, "other")
        _run_git(base, "clone", bare, other)
        _run_git(other, "config", "user.email", "t@example.com")
        _run_git(other, "config", "user.name", "T")
        _commit(other, "web-edit.txt", "edited on github\n", "web edit")
        _run_git(other, "push", bare, "HEAD:refs/heads/main")
        _commit(vault, "seal-note.md", "y" * 80, "seal")

        ok, err = _seal_for(vault)._push(bare)
        self.assertTrue(ok, err)
        tree = _remote_tree(bare)
        self.assertIn("web-edit.txt", tree)   # remote work kept
        self.assertIn("seal-note.md", tree)   # our seal landed too

    def test_unrelated_histories_merge_keeps_both(self):
        # Fresh ``git init`` in the vault vs a remote that was used for
        # something else (the gate mirror). No shared base → merge with
        # --allow-unrelated-histories; nothing discarded.
        bare = _bare(os.path.join(self.tmp, "mirror.git"))
        gate = _init_repo(os.path.join(self.tmp, "gate"),
                          initial_file="gate-runner.yml", content="ci: yes\n")
        _run_git(gate, "push", bare, "HEAD:refs/heads/main")
        vault = _init_repo(os.path.join(self.tmp, "vault"),
                           initial_file="websites-note.md", content="z" * 80)

        ok, err = _seal_for(vault)._push(bare)
        self.assertTrue(ok, err)
        tree = _remote_tree(bare)
        self.assertIn("gate-runner.yml", tree)     # remote history intact
        self.assertIn("websites-note.md", tree)    # vault snapshot added

    def test_unrelated_conflict_lands_on_rescue_branch(self):
        bare = _bare(os.path.join(self.tmp, "mirror.git"))
        gate = _init_repo(os.path.join(self.tmp, "gate"),
                          initial_file="README.md", content="remote\n")
        _run_git(gate, "push", bare, "HEAD:refs/heads/main")
        remote_before = _remote_head(bare)
        vault = _init_repo(os.path.join(self.tmp, "vault"),
                           initial_file="README.md", content="vault\n")

        ok, err = _seal_for(vault)._push(bare)
        self.assertFalse(ok)
        self.assertIn("seal-rescue/", err)
        # main untouched; the seal preserved on its own branch
        self.assertEqual(_remote_head(bare), remote_before)
        rescue = [b for b in _remote_branches(bare)
                  if b.startswith("seal-rescue/")]
        self.assertEqual(len(rescue), 1, _remote_branches(bare))

    def test_rebase_conflict_lands_on_rescue_branch(self):
        bare = _bare(os.path.join(self.tmp, "mirror.git"))
        base = _init_repo(os.path.join(self.tmp, "base"),
                          initial_file="f.txt", content="base\n")
        _run_git(base, "push", bare, "HEAD:refs/heads/main")
        vault = os.path.join(self.tmp, "vault")
        _run_git(base, "clone", bare, vault)
        _run_git(vault, "config", "user.email", "t@example.com")
        _run_git(vault, "config", "user.name", "T")
        other = os.path.join(self.tmp, "other")
        _run_git(base, "clone", bare, other)
        _run_git(other, "config", "user.email", "t@example.com")
        _run_git(other, "config", "user.name", "T")
        _commit(other, "f.txt", "remote version\n", "remote edit")
        _run_git(other, "push", bare, "HEAD:refs/heads/main")
        _commit(vault, "f.txt", "vault version\n", "seal edit")

        ok, err = _seal_for(vault)._push(bare)
        self.assertFalse(ok)
        self.assertIn("seal-rescue/", err)
        rescue = [b for b in _remote_branches(bare)
                  if b.startswith("seal-rescue/")]
        self.assertEqual(len(rescue), 1)

    def test_non_rejection_error_is_passed_through(self):
        # Auth/network failures ("repository not found") must NOT trigger
        # reconciliation or a rescue push.
        vault = _init_repo(os.path.join(self.tmp, "vault"),
                           initial_file="note.md", content="x" * 80)
        nowhere = os.path.join(self.tmp, "no-such-repo.git")
        ok, err = _seal_for(vault)._push(nowhere)
        self.assertFalse(ok)
        self.assertNotIn("seal-rescue", err)

    def test_fetch_head_token_is_scrubbed(self):
        vault = _init_repo(os.path.join(self.tmp, "vault"),
                           initial_file="note.md", content="x" * 80)
        fh = os.path.join(vault, ".git", "FETCH_HEAD")
        with open(fh, "w", encoding="utf-8") as f:
            f.write("abc123\t\tbranch 'main' of "
                    "https://x-access-token:SECRETTOKEN@github.com/o/r.git\n")
        vs = vs_mod.VaultSeal(vault_path=vault, token="SECRETTOKEN",
                              repo_name="mirror", auto_push=True)
        vs._sanitize_fetch_head()
        with open(fh, "r", encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("SECRETTOKEN", text)
        self.assertIn("***", text)

    def test_seal_result_wires_push_and_rescue(self):
        # seal() plumbing: a failing _push becomes the SealResult error
        # (commit kept); a reconciled push marks pushed + refreshes sha.
        vault = _init_repo(os.path.join(self.tmp, "vault"),
                           initial_file="note.md", content="x" * 80)
        with open(os.path.join(vault, "dirty.md"), "w",
                  encoding="utf-8") as f:
            f.write("uncommitted change\n")          # something to seal
        vs = vs_mod.VaultSeal(vault_path=vault, token="tok",
                              repo_name="mirror", auto_push=True)
        vs._repo_full = "o/mirror"
        vs._remote_url = "https://github.com/o/mirror"
        with mock.patch.object(vs, "ensure_repo",
                               return_value=(True, None)), \
             mock.patch.object(vs, "_push",
                               return_value=(True, "")):
            res = vs.seal({"processed": 1, "total": 1})
        self.assertTrue(res.ok, res.error)
        self.assertTrue(res.pushed)

        with open(os.path.join(vault, "note2.md"), "w",
                  encoding="utf-8") as f:
            f.write("y" * 80)
        with mock.patch.object(vs, "ensure_repo",
                               return_value=(True, None)), \
             mock.patch.object(vs, "_push",
                               return_value=(False,
                                             "mirror main rejected … "
                                             "'seal-rescue/2099'")):
            res2 = vs.seal({})
        self.assertFalse(res2.ok)
        self.assertTrue(res2.sealed)          # the local commit is kept
        self.assertIn("seal-rescue", res2.error)
        self.assertIn("commit kept", res2.describe())


# ---------------------------------------------------------------------------
# 2. Self domains + token scrubbing (core/links)
# ---------------------------------------------------------------------------

class TestSelfDomains(unittest.TestCase):

    def test_config_missing_key_gives_default(self):
        self.assertEqual(L.self_domains_from_config({}),
                         list(L.DEFAULT_SELF_DOMAINS))
        self.assertIn("workers.dev",
                      L.self_domains_from_config(None)[0])

    def test_config_opt_out_and_custom(self):
        self.assertEqual(L.self_domains_from_config(
            {"web_self_domains": []}), [])
        self.assertEqual(L.self_domains_from_config(
            {"web_self_domains": ""}), [])
        self.assertEqual(L.self_domains_from_config(
            {"web_self_domains": " A.example , b.example "}),
            ["a.example", "b.example"])
        self.assertEqual(L.self_domains_from_config(
            {"web_self_domains": 42}), list(L.DEFAULT_SELF_DOMAINS))

    def test_domain_is_self_matches_host_and_subdomains(self):
        doms = list(L.DEFAULT_SELF_DOMAINS)
        self.assertTrue(L.domain_is_self(
            "https://github-to-obsidian-bot.aliassadi-plus.workers.dev"
            "/auth/?token=abc", doms))
        self.assertTrue(L.domain_is_self(
            "https://api.github-to-obsidian-bot.aliassadi-plus.workers.dev/"
            "x", doms))                       # subdomain
        self.assertFalse(L.domain_is_self(
            "https://evil.example/github-to-obsidian-bot.aliassadi-plus"
            ".workers.dev", doms))            # suffix scam
        self.assertFalse(L.domain_is_self("https://gist.github.com/x", doms))
        self.assertFalse(L.domain_is_self("https://x.com/1", []))

    def test_scrub_url_token(self):
        hex64 = "e8d16400ecb1361ca7b7209a1ab7d485e50451449e6c469ff8e45fdc7a014cb1"
        url = f"https://bot.example/auth/?token={hex64}&next=keep"
        out = L.scrub_url_token(url)
        self.assertNotIn(hex64, out)
        self.assertIn("token=…", out)
        self.assertIn("next=keep", out)       # other params untouched
        # short values are not secret-shaped → untouched
        self.assertEqual(L.scrub_url_token("https://x.example/?token=abc"),
                         "https://x.example/?token=abc")
        self.assertEqual(L.scrub_url_token("https://x.example plain"),
                         "https://x.example plain")

    def test_scrub_urls_in_text(self):
        hex1 = "a" * 64
        hex2 = "b" * 40
        text = (f"| - | 2026-09-30 | https://bot.example/auth/?token={hex1} "
                f"| bot.example | Bot |\n"
                f"see https://y.example/login?secret={hex2} please\n"
                f"plain line without urls\n")
        out = L.scrub_urls_in_text(text)
        self.assertNotIn(hex1, out)
        self.assertNotIn(hex2, out)
        self.assertIn("token=…", out)
        self.assertIn("secret=…", out)
        self.assertIn("plain line without urls", out)


# ---------------------------------------------------------------------------
# 3. Pipeline: self-domain guard + purge
# ---------------------------------------------------------------------------

class TestPipelineSelfGuard(unittest.TestCase):

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix="selfpipe-")
        self.db = wp.WebsiteStateDB(
            db_path=os.path.join(self.tmp, "cache.db"))
        self.logs = []

    def tearDown(self):
        dryrun.disable()
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def log(self, msg, level="info"):
        self.logs.append((level, msg))

    def all_logs(self):
        return "\n".join(m for _, m in self.logs)

    def _make(self, config, fetch_fn="never"):
        def _never_fetch(url, **kw):
            raise AssertionError("self link must never be fetched: %s" % url)
        return wp.WebsitePipeline(
            config=config, llm_call=lambda *a, **k: "{}",
            vault_index_has=lambda u: False, state=self.db,
            fetch_fn=(_never_fetch if fetch_fn == "never" else fetch_fn),
            log=self.log)

    def test_self_link_skips_without_fetch_note_or_retry(self):
        pipe = self._make({"website_vault_path": os.path.join(self.tmp, "w"),
                           "web_domain_delay_s": 0,
                           "web_self_domains": ["bot.example"]})
        url = "https://bot.example/auth/?token=" + "c" * 64
        r = pipe.process_link(url)
        self.assertEqual(r["outcome"], "skipped")
        self.assertIn("self domain", r["error"])
        self.assertFalse(r.get("note_path"))
        self.assertIsNone(self.db.retry_row(
            "https://bot.example/auth/?token=" + "c" * 64))
        self.assertEqual(pipe.counters["skipped"], 1)
        self.assertIn("self domain", self.all_logs())
        # the log line never carries the live token
        self.assertNotIn("c" * 64, self.all_logs())

    def test_default_self_domains_when_key_missing(self):
        pipe = self._make({"website_vault_path": os.path.join(self.tmp, "w"),
                           "web_domain_delay_s": 0})
        self.assertEqual(pipe.self_domains, list(L.DEFAULT_SELF_DOMAINS))

    def test_production_constructor_purges_self_tail(self):
        # A self link queued by a pre-v0.21.0 run (retry row + failed
        # _review placeholder with the live token inside) is purged at
        # construction, production path only (fetch_fn None).
        auth = ("https://github-to-obsidian-bot.aliassadi-plus.workers.dev"
                "/auth/?token=" + "d" * 64)
        self.db.enqueue_retry(auth, "SSL EOF")
        review = os.path.join(self.tmp, "_review", "auth.md")
        os.makedirs(os.path.dirname(review), exist_ok=True)
        with open(review, "w", encoding="utf-8") as f:
            f.write("---\nsource: %s\nfetch_status: \"failed\"\n---\n" % auth)
        self.db.mark_processed(auth, review, "", "", "failed")

        wp.WebsitePipeline(
            config={"website_vault_path": os.path.join(self.tmp, "w"),
                    "web_domain_delay_s": 0,
                    "web_self_domains": list(L.DEFAULT_SELF_DOMAINS)},
            llm_call=lambda *a, **k: "{}",
            vault_index_has=lambda u: False, state=self.db,
            log=self.log)
        self.assertIsNone(self.db.retry_row(auth))
        self.assertFalse(os.path.exists(review))   # placeholder deleted
        self.assertTrue(self.db.is_dismissed(auth))
        self.assertIn("Self domains", self.all_logs())


# ---------------------------------------------------------------------------
# 4. web_fetch: the direct fallback for proxied connection failures
# ---------------------------------------------------------------------------

class _DeadProxy:
    """Accepts the SOCKS connection, then kills it — the proxy-path
    failure that must trigger the direct fallback."""

    def __init__(self):
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(4)
        self.port = self._srv.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
                conn.close()          # rude: handshake can never finish
            except OSError:
                return

    def close(self):
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<html><head><title>Fell Back</title></head><body>ok</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class TestDirectFallback(unittest.TestCase):

    PROXY = {"type": "socks5", "host": "127.0.0.1", "port": 1,
             "username": "", "password": ""}

    def test_fallback_succeeds_after_proxy_connection_failure(self):
        calls = []

        def _fake(url, timeout_s, max_bytes, user_agent, proxy, started):
            calls.append(proxy)
            if proxy is not None:
                return wf.FetchResult(url, status="failed",
                                      reason="connection: SSL EOF")
            return wf.FetchResult(url, final_url=url, status="full",
                                  http_status=200, body=b"x", text="x")

        with mock.patch.object(wf, "_fetch_once", side_effect=_fake):
            res = wf.fetch_url("https://gist.github.com/x", timeout_s=2,
                               proxy=dict(self.PROXY))
        self.assertTrue(res.ok, res.reason)
        self.assertEqual(res.status, "full")
        self.assertIn("direct fallback", res.reason)
        self.assertIn("SSL EOF", res.reason)
        self.assertEqual(calls, [self.PROXY, None])   # proxy then direct

    def test_both_fail_reports_both_reasons(self):
        def _fake(url, timeout_s, max_bytes, user_agent, proxy, started):
            reason = ("proxy died" if proxy is not None
                      else "connection refused locally")
            return wf.FetchResult(url, status="failed", reason=reason)

        with mock.patch.object(wf, "_fetch_once", side_effect=_fake):
            res = wf.fetch_url("https://hf.example/model", timeout_s=2,
                               proxy=dict(self.PROXY))
        self.assertFalse(res.ok)
        self.assertIn("proxy:", res.reason)
        self.assertIn("direct:", res.reason)

    def test_http_error_gets_no_fallback(self):
        calls = []

        def _fake(url, timeout_s, max_bytes, user_agent, proxy, started):
            calls.append(proxy)
            return wf.FetchResult(url, status="failed", reason="HTTP 404",
                                  http_status=404)

        with mock.patch.object(wf, "_fetch_once", side_effect=_fake):
            res = wf.fetch_url("https://x.example/gone", timeout_s=2,
                               proxy=dict(self.PROXY))
        self.assertFalse(res.ok)
        # v0.46.0 — the site ANSWERED (no route fallback), but the dead
        # page climbs its rescue ladder first: original + two variants +
        # the Wayback probe, all on the PRIMARY route, then the verdict.
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(c == self.PROXY for c in calls),
                        f"unexpected route: {calls}")

    def test_opt_out_and_no_proxy_and_loopback(self):
        calls = []

        def _fake(url, timeout_s, max_bytes, user_agent, proxy, started):
            calls.append(proxy)
            return wf.FetchResult(url, status="failed",
                                  reason="connection: boom")

        with mock.patch.object(wf, "_fetch_once", side_effect=_fake):
            wf.fetch_url("https://x.example/a", timeout_s=2,
                         proxy=dict(self.PROXY), direct_fallback=False)
            wf.fetch_url("https://x.example/b", timeout_s=2, proxy=None)
            wf.fetch_url("http://127.0.0.1:9/c", timeout_s=2,
                         proxy=dict(self.PROXY))
        self.assertEqual(calls, [self.PROXY, None, self.PROXY])

    def test_real_dead_proxy_falls_back_to_real_server(self):
        # End-to-end on a real socket pair: a proxy that kills the
        # handshake + a real HTTP server on a NON-loopback local address
        # (loopback would bypass the proxy by rule). Skips when the
        # machine has no usable non-loopback address.
        try:
            ip = socket.gethostbyname(socket.gethostname())
            if ipaddress.ip_address(ip).is_loopback:
                raise ValueError()
        except (socket.gaierror, ValueError):
            self.skipTest("no non-loopback local address")
        dead = _DeadProxy()
        httpd = HTTPServer((ip, 0), _Handler)
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.handle_request, daemon=True)
        t.start()
        try:
            res = wf.fetch_url(f"http://{ip}:{port}/page", timeout_s=5,
                               proxy={"type": "socks5", "host": "127.0.0.1",
                                      "port": dead.port, "username": "",
                                      "password": ""})
        finally:
            dead.close()
            t.join(timeout=5)
            httpd.server_close()
        self.assertTrue(res.ok, res.reason)
        self.assertEqual(res.status, "full")
        self.assertIn("direct fallback", res.reason)
        self.assertIn(b"Fell Back", res.body)


# ---------------------------------------------------------------------------
# 5. LinkTracker.verify — full accounting
# ---------------------------------------------------------------------------

class TestVerifyAccounting(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="verify-")
        self.vault = os.path.join(self.tmp, "github-vault")
        self.websites = os.path.join(self.tmp, "websites-vault")
        os.makedirs(self.vault)
        os.makedirs(self.websites)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _tracker(self):
        t = LinkTracker(self.vault)
        return t

    def _note(self, rel, size=120):
        path = os.path.join(self.websites, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("x" * size)
        return path

    def _inbox_row(self, *urls):
        inbox = os.path.join(self.websites, "_inbox")
        os.makedirs(inbox, exist_ok=True)
        with open(os.path.join(inbox, "other_links.md"), "w",
                  encoding="utf-8") as f:
            f.write("# Other\n\n| - | date | url | host | src | status |\n")
            for u in urls:
                f.write(f"| - | 2026-09-30 | {u} | h | Bot | unreviewed |\n")

    def test_owner_batch_shape_accounts_every_link(self):
        # The 02:17 run, scaled down: 8 GitHub dedup-skips, 4 website
        # notes, 10 _review placeholders, 2 website dedup-skips, 47
        # blocked-domain links — 71 total, every one bucketed.
        t = self._tracker()
        t.manifest["links"] = []
        for i in range(8):
            t.manifest["links"].append(
                {"url": f"https://github.com/o/r{i}", "normalized":
                 f"https://github.com/o/r{i}", "type": "github",
                 "status": "skipped", "note_path": None, "error": None,
                 "processed_at": None})
        for i in range(4):
            url = f"https://site{i}.example/"
            t.manifest["links"].append(
                {"url": url, "normalized": url, "type": "non-github",
                 "status": "processed", "note_path":
                 self._note(f"Tools/site{i}/note{i}.md"), "error": None,
                 "processed_at": None})
        for i in range(10):
            url = f"https://fail{i}.example/"
            t.manifest["links"].append(
                {"url": url, "normalized": url, "type": "non-github",
                 "status": "processed",
                 "note_path": self._note(f"_review/fail{i}.md"),
                 "error": "fetch failed", "processed_at": None})
        for i in range(2):
            url = f"https://dup{i}.example/"
            t.manifest["links"].append(
                {"url": url, "normalized": url, "type": "non-github",
                 "status": "skipped", "note_path": None,
                 "error": "already in the websites vault",
                 "processed_at": None})
        blocked_urls = [f"https://x.com/i/status/{i}" for i in range(47)]
        for u in blocked_urls:
            t.manifest["links"].append(
                {"url": u, "normalized": u, "type": "non-github",
                 "status": "blocked", "note_path": None,
                 "error": "blocked domain", "processed_at": None})
        t.manifest["total_links"] = len(t.manifest["links"])
        self._inbox_row(*blocked_urls)

        rep = t.verify(
            extra_inbox_dirs=[os.path.join(self.websites, "_inbox")])
        self.assertEqual(rep["total"], 71)
        self.assertEqual(rep["github_skipped"], 8)
        self.assertEqual(rep["websites_processed"], 4)
        self.assertEqual(rep["websites_review"], 10)
        self.assertEqual(rep["websites_skipped"], 2)
        self.assertEqual(rep["blocked_recorded"], 47)
        self.assertEqual(rep["accounted"], 71)
        self.assertEqual(rep["unaccounted"], 0)
        self.assertTrue(rep["accounting_ok"])
        self.assertTrue(rep["verification_passed"])

    def test_github_pending_is_now_visible_and_fails(self):
        t = self._tracker()
        t.manifest["links"] = [
            {"url": "https://github.com/o/pending", "normalized":
             "https://github.com/o/pending", "type": "github",
             "status": "pending", "note_path": None, "error": None,
             "processed_at": None}]
        rep = t.verify()
        self.assertEqual(rep["github_pending"], 1)
        self.assertFalse(rep["verification_passed"])
        self.assertTrue(rep["accounting_ok"])      # still fully counted

    def test_non_github_pending_is_a_bucket_not_a_failure(self):
        t = self._tracker()
        t.manifest["links"] = [
            {"url": "https://x.example/", "normalized":
             "https://x.example/", "type": "non-github",
             "status": "pending", "note_path": None, "error": None,
             "processed_at": None}]
        rep = t.verify()
        self.assertEqual(rep["non_github_pending"], 1)
        self.assertTrue(rep["verification_passed"])
        self.assertTrue(rep["accounting_ok"])

    def test_blocked_needs_no_inbox_row_anymore(self):
        """v0.35.0 — the owner's omission rule: banned links are NEVER
        collected (no note, no _review, no _inbox row), so a blocked
        manifest entry counts as accounted BY ITSELF — the old contract
        (a missing _inbox row = data loss = failed verify) is retired.
        The tables may exist with other links' rows; ours is legitimately
        absent everywhere."""
        t = self._tracker()
        t.manifest["links"] = [
            {"url": "https://x.com/1", "normalized": "https://x.com/1",
             "type": "non-github", "status": "blocked", "note_path": None,
             "error": "blocked domain", "processed_at": None}]
        self._inbox_row("https://fresh.example/only-other-links-row")
        rep = t.verify(
            extra_inbox_dirs=[os.path.join(self.websites, "_inbox")])
        self.assertEqual(rep["blocked_recorded"], 1)
        self.assertEqual(rep["non_github_failed"], 0)
        self.assertTrue(rep["verification_passed"])
        self.assertTrue(rep["accounting_ok"])
        # …and with NO tables at all, exactly the same verdict (the
        # manifest entry itself is the record now).
        t2 = self._tracker()
        t2.manifest["links"] = [
            {"url": "https://youtu.be/v", "normalized": "https://youtu.be/v",
             "type": "non-github", "status": "blocked", "note_path": None,
             "error": "blocked domain", "processed_at": None}]
        rep2 = t2.verify()
        self.assertEqual(rep2["blocked_recorded"], 1)
        self.assertTrue(rep2["verification_passed"])

    def test_websites_note_missing_fails(self):
        t = self._tracker()
        t.manifest["links"] = [
            {"url": "https://gone.example/", "normalized":
             "https://gone.example/", "type": "non-github",
             "status": "processed",
             "note_path": os.path.join(self.websites, "no", "such.md"),
             "error": None, "processed_at": None}]
        rep = t.verify()
        self.assertEqual(rep["websites_processed"], 0)
        self.assertEqual(rep["non_github_failed"], 1)
        self.assertFalse(rep["verification_passed"])

    def test_blocked_row_counts_even_with_legacy_row_present(self):
        # The v0.20.0 vault separation: tables live in the WEBSITES
        # vault. v0.35.0 — a blocked link counts whether or not a legacy
        # row happens to survive in a table (the manifest is the record).
        t = self._tracker()
        t.manifest["links"] = [
            {"url": "https://t.co/abc", "normalized": "https://t.co/abc",
             "type": "non-github", "status": "blocked", "note_path": None,
             "error": "blocked domain", "processed_at": None}]
        self._inbox_row("https://t.co/abc")
        rep = t.verify(
            extra_inbox_dirs=[os.path.join(self.websites, "_inbox")])
        self.assertEqual(rep["blocked_recorded"], 1)
        self.assertTrue(rep["verification_passed"])


# ---------------------------------------------------------------------------
# 6. The _inbox writer scrubs tokens (new rows AND pre-v0.21.0 files)
# ---------------------------------------------------------------------------

class TestInboxTokenScrub(unittest.TestCase):

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix="inbox-")

    def tearDown(self):
        dryrun.disable()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_new_rows_never_store_secret_values(self):
        hex64 = "e" * 64
        url = f"https://bot.example/auth/?token={hex64}"
        write_inbox_links_by_platform(self.tmp, [url], source="Bot")
        table = os.path.join(self.tmp, "_inbox", "other_links.md")
        with open(table, "r", encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn(hex64, text)
        self.assertIn("token=…", text)

    def test_existing_live_token_rows_get_scrubbed_on_next_write(self):
        hex64 = "f" * 64
        table = os.path.join(self.tmp, "_inbox", "other_links.md")
        os.makedirs(os.path.dirname(table), exist_ok=True)
        with open(table, "w", encoding="utf-8") as f:
            f.write("# 🔗 Other — Review Queue\n\n"
                    "| - | 2026-09-29 | "
                    f"https://bot.example/auth/?token={hex64} "
                    "| bot.example | Bot | unreviewed | |\n")
        logs = []
        write_inbox_links_by_platform(
            self.tmp, ["https://fresh.example/"], source="Bot",
            log_callback=lambda m, l: logs.append(m))
        with open(table, "r", encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn(hex64, text)
        self.assertIn("token=…", text)
        self.assertIn("https://fresh.example/", text)   # new row too
        self.assertTrue(any("scrubbed" in m for m in logs), logs)


# ---------------------------------------------------------------------------
# 7. Wiring (source assertions — the intakefix pattern) + defaults
# ---------------------------------------------------------------------------

class TestWiring(unittest.TestCase):

    def test_website_phase_intake_covers_self_domains(self):
        import gitcurator.gui.app as ga
        src = inspect.getsource(ga.ProcessingWorker._run_website_phase)
        self.assertIn("self_domains_from_config", src)
        self.assertIn("mark_blocked", src)

    def test_worker_verify_passes_extra_inbox_dirs(self):
        import gitcurator.gui.app as ga
        src = inspect.getsource(ga.ProcessingWorker._run_impl)
        self.assertIn("extra_inbox_dirs", src)
        src2 = inspect.getsource(ga.MainWindow.verify_vault)
        self.assertIn("extra_inbox_dirs", src2)

    def test_bot_check_passes_self_domains(self):
        import gitcurator.gui.app as ga
        src = inspect.getsource(ga.MainWindow.check_bot_queue)
        self.assertIn("self_domains=", src)
        self.assertIn("Self domains", src)

    def test_settings_and_save_config_carry_the_key(self):
        import gitcurator.gui.app as ga
        src = inspect.getsource(ga.MainWindow.initUI)
        self.assertIn("web_self_input", src)
        src2 = inspect.getsource(ga.MainWindow.save_config)
        self.assertIn("web_self_domains", src2)

    def test_linktracker_has_mark_blocked(self):
        import gitcurator.gui.app as ga
        self.assertTrue(hasattr(ga.LinkTracker, "mark_blocked"))

    def test_verify_buckets_in_report_writers(self):
        import gitcurator.gui.app as ga
        src = inspect.getsource(ga.LinkTracker.verify)
        for needle in ("websites_processed", "websites_review",
                       "blocked_recorded", "non_github_pending",
                       "accounting_ok", "extra_inbox_dirs"):
            self.assertIn(needle, src)
        src2 = inspect.getsource(ga.ProcessingWorker._generate_final_report)
        self.assertIn("Websites notes", src2)

    def test_constants_defaults_both_modules(self):
        from gitcurator import constants as c1
        from gitcurator.gui import constants as c2
        expected = ["github-to-obsidian-bot.aliassadi-plus.workers.dev"]
        for mod in (c1, c2):
            self.assertEqual(mod.CONFIG_EXAMPLE["web_self_domains"],
                             expected, mod.__name__)

    def test_pipeline_guard_source_has_self_domains(self):
        src = inspect.getsource(wp.WebsitePipeline._process_link_inner)
        self.assertIn("domain_is_self", src)
        self.assertIn("scrub_url_token", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
