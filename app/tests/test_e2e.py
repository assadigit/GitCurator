#!/usr/bin/env python3
"""
test_e2e.py — simulated end-to-end pipeline test (roadmap P2 item, v30.1).

Runs the REAL core modules (links → llm_client → note_builder → storage)
against FAKE Ollama responses and FAKE GitHub metadata — no network, no
GUI, no GitHub API, no Telethon. This tests the seam between the pure
core and the worker loop: it replays exactly what ProcessingWorker.run()
does per URL (dedup → 3-attempt analyze → build note → collision-safe
atomic write), minus the PyQt signal machinery.

Covered end-to-end:
  - message → canonical links → dedup → ONE analysis + ONE note per repo
  - non-GitHub links routed to the inbox, never into the vault
  - hostile LLM output (YAML injection, hostile category) neutralized
  - noisy/multi-attempt LLM output (fenced JSON, retry ladder)
  - dead URL does not kill the batch; no partial notes on failure
  - same-name collisions get _v1 filenames
  - config save/load round-trip preserves cloudflare_*/gdrive_* keys
  - banner bytes land atomically and the note references them safely

Run:  python -m unittest tests.test_e2e -v
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

# Make the app dir importable no matter where we run from.
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from gitcurator.core import links
from gitcurator.core import llm_client
from gitcurator.core import note_builder
from gitcurator.core import storage


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeResponse:
    """Mimics the ollama-py response object shape (new API)."""

    def __init__(self, content):
        class _Msg:
            pass
        msg = _Msg()
        msg.content = content
        self.message = msg


class FakeOllama:
    """Scripted Ollama client — pops scripted responses in call order."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def chat(self, **kwargs):
        self.calls += 1
        if self.responses:
            resp = self.responses.pop(0)
        else:
            resp = "{'error': 'no more scripted responses'}"
        if isinstance(resp, Exception):
            raise resp
        return FakeResponse(resp)


def fake_repo(name="repo", owner="owner", org="Some Org", stars=1234,
              forks=42, commits=87, primary_language="Python"):
    """Fixture standing in for the PyGithub repo object metadata."""
    return {
        "name": name, "owner": owner, "org": org, "stars": stars,
        "forks": forks, "commits": commits,
        "primary_language": primary_language,
        "languages": [primary_language, "Shell"],
    }


def llm_json(category="AI & ML", tags=("cli", "tool"), summary=None):
    """The canonical analysis JSON the real prompts ask for (subset)."""
    return {
        "category": category,
        "tags": list(tags),
        "summary": summary or ("A genuinely useful repository that does "
                               "exactly what it promises." * 2),
        "short_summary": "Useful repo, well maintained.",
        "how_it_works": "It parses input, applies heuristics, writes output.",
        "core_value": "Saves hours of manual triage per week.",
        "features": ["fast", "scriptable", "well documented", "active"],
        "difference": "Smaller and faster than the alternatives.",
        "org": "Some Org",
        "org_reputation": 7,
        "credibility_score": 82,
        "confidence": 88,
    }


# ---------------------------------------------------------------------------
# Miniature worker loop — mirrors ProcessingWorker.run() per-URL body
# ---------------------------------------------------------------------------

def analyze_url(client, config, attempts=3):
    """Mirrors _llm_analyze: up to N attempts, robust JSON extraction,
    every call timeout-wrapped. Raises the last error when unrecoverable."""
    last_err = None
    for _ in range(attempts):
        try:
            raw = llm_client.chat_with_timeout(
                client, 30, model=config["ollama"]["model"], messages=[])
            return llm_client.extract_json(raw)
        except (ValueError, TimeoutError) as e:
            last_err = e
    raise last_err


def process_message(message, repos, client, config, vault_dir):
    """Mirrors run(): split links → per-URL analyze → build note → write."""
    github_urls, inbox, raw_count = links.split_links(message)
    notes, failed = [], []
    for url in github_urls:
        if not links.is_github_url(url):
            failed.append(url)
            continue
        meta = repos.get(url)
        if meta is None:
            failed.append(url)
            continue
        try:
            data = analyze_url(client, config)
        except (ValueError, TimeoutError):
            failed.append(url)
            continue
        category = data.get("category", "Uncategorized")
        note = note_builder.build_note(
            url=url,
            repo_name=meta["name"], owner=meta["owner"],
            org_name=data.get("org") or meta["org"],
            stars=meta["stars"], forks=meta["forks"],
            commit_count=meta["commits"],
            cred_score=data.get("credibility_score", 50),
            org_rep=data.get("org_reputation", 5),
            summary=data.get("summary", ""),
            tags=data.get("tags", []),
            category_key=category,
            confidence=data.get("confidence", 50),
            how_it_works=data.get("how_it_works", ""),
            core_value=data.get("core_value", ""),
            features=data.get("features", []),
            difference=data.get("difference", ""),
            primary_language=meta.get("primary_language", ""),
            languages=meta.get("languages", []),
            short_summary=data.get("short_summary", ""),
        )
        category_dir = os.path.join(
            vault_dir, storage.safe_filename(category.replace('/', '_')))
        filename = storage.build_note_filename(meta["name"], category,
                                               data.get("tags", []))
        path = storage.unique_path(os.path.join(category_dir, filename))
        storage.atomic_write_text(path, note)
        notes.append(path)
    return {"notes": notes, "inbox": inbox, "failed": failed,
            "raw_count": raw_count}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEndToEndPipeline(unittest.TestCase):

    def setUp(self):
        self.vault = tempfile.mkdtemp(prefix='curator-e2e-')
        self.config = {"ollama": {"base_url": "http://localhost:11434",
                                  "model": "llama3"}}

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)

    def _repos(self, **overrides):
        base = {"https://github.com/owner/repo": fake_repo(**overrides)}
        return base

    # -- happy path ---------------------------------------------------------

    def test_happy_path_note_written_with_safe_frontmatter(self):
        client = FakeOllama([json.dumps(llm_json())])
        result = process_message(
            "check https://github.com/owner/repo thanks",
            self._repos(), client, self.config, self.vault)

        self.assertEqual(result["failed"], [])
        self.assertEqual(len(result["notes"]), 1)
        with open(result["notes"][0], encoding='utf-8') as f:
            content = f.read()
        # frontmatter present and well-formed
        self.assertTrue(content.startswith('---\n'))
        self.assertIn('source: "https://github.com/owner/repo"', content)
        self.assertIn('category: "AI & ML"', content)
        self.assertIn('# repo', content)
        self.assertIn('## What is it?', content)
        # note lives under the sanitized category folder
        expected_dir = os.path.join(self.vault, 'AI_ML')
        self.assertTrue(result["notes"][0].startswith(expected_dir + os.sep))
        # one analysis call for one (deduped) link
        self.assertEqual(client.calls, 1)

    def test_duplicate_links_in_one_message_produce_one_note(self):
        # www, trailing slash, query param, and path variants — all the
        # SAME repo. The old regex drift made some of these parse as
        # different links (phantom re-processing).
        msg = ("https://github.com/o/r plus https://www.github.com/o/r/ "
               "and https://github.com/o/r?tab=readme "
               "and https://github.com/o/r/tree/main")
        repos = {"https://github.com/o/r": fake_repo()}
        client = FakeOllama([json.dumps(llm_json())])
        result = process_message(msg, repos, client, self.config, self.vault)

        self.assertEqual(len(result["notes"]), 1)
        self.assertEqual(client.calls, 1)   # dedup happens BEFORE analysis
        self.assertGreaterEqual(result["raw_count"], 4)

    def test_non_github_links_go_to_inbox_not_vault(self):
        msg = ("https://github.com/o/r and https://x.com/post?s=20 "
               "and https://example.com/article")
        repos = {"https://github.com/o/r": fake_repo()}
        client = FakeOllama([json.dumps(llm_json())])
        result = process_message(msg, repos, client, self.config, self.vault)

        self.assertEqual(len(result["notes"]), 1)
        self.assertEqual(len(result["inbox"]), 2)
        self.assertTrue(any('x.com' in l for l in result["inbox"]))
        self.assertTrue(any('example.com' in l for l in result["inbox"]))
        # only ONE directory (the single GitHub category) in the vault —
        # inbox links never touch the vault filesystem
        entries = os.listdir(self.vault)
        self.assertEqual(entries, ['AI_ML'])

    # -- hostile input ------------------------------------------------------

    def test_malicious_llm_output_cannot_inject_yaml(self):
        evil = llm_json(
            tags=["pwned, x]  # injected", "{{jinja}}", "ok-tag"],
        )
        evil["org"] = 'Org" ] # pwn'
        # real models frequently wrap JSON in markdown fences + prose
        scripted = "Sure! Analysis:\n```json\n" + json.dumps(evil) + "\n```\nDone."
        client = FakeOllama([scripted])
        result = process_message(
            "https://github.com/owner/repo",
            self._repos(), client, self.config, self.vault)

        self.assertEqual(len(result["notes"]), 1)
        with open(result["notes"][0], encoding='utf-8') as f:
            content = f.read()
        fm = content.split('---')[1]
        tags_line = [l for l in fm.splitlines() if l.startswith('tags:')][0]
        inner = tags_line[len('tags: ['):]
        if inner.endswith(']'):
            inner = inner[:-1]
        items = [i.strip() for i in inner.split(',')] if inner.strip() else []
        self.assertTrue(items, 'expected sanitized tags to survive')
        for item in items:
            for ch in ',[]{}#:\'"':
                self.assertNotIn(ch, item, f'unsafe char {ch!r} in {item!r}')
        self.assertIn('ok-tag', items)
        org_line = [l for l in fm.splitlines() if l.startswith('org:')][0]
        self.assertTrue(org_line.startswith('org: "'))

    def test_hostile_category_cannot_escape_vault(self):
        client = FakeOllama([json.dumps(llm_json(category="../../Evil"))])
        result = process_message(
            "https://github.com/owner/repo",
            self._repos(), client, self.config, self.vault)

        self.assertEqual(len(result["notes"]), 1)
        note_path = os.path.abspath(result["notes"][0])
        vault_real = os.path.realpath(self.vault)
        # the note must still live INSIDE the vault
        self.assertTrue(os.path.realpath(note_path).startswith(vault_real + os.sep))
        # no traversal fragment in the path
        self.assertNotIn('..', note_path)

    # -- flaky / failing LLM ------------------------------------------------

    def test_noisy_llm_output_still_parses(self):
        scripted = ("Here is the JSON you requested:\n"
                    "```json\n" + json.dumps(llm_json()) + "\n```\n"
                    "Hope that helps! Let me know if you need more.")
        client = FakeOllama([scripted])
        result = process_message(
            "https://github.com/owner/repo",
            self._repos(), client, self.config, self.vault)
        self.assertEqual(len(result["notes"]), 1)
        self.assertEqual(client.calls, 1)

    def test_retry_ladder_recovers_on_third_attempt(self):
        client = FakeOllama([
            "total garbage, no json at all",
            "{ still broken json",
            json.dumps(llm_json()),
        ])
        result = process_message(
            "https://github.com/owner/repo",
            self._repos(), client, self.config, self.vault)
        self.assertEqual(len(result["notes"]), 1)
        self.assertEqual(client.calls, 3)

    def test_failed_url_does_not_kill_batch_and_leaves_no_partial_note(self):
        repos = {
            "https://github.com/o/dead": fake_repo(name="dead"),
            "https://github.com/o/alive": fake_repo(name="alive"),
        }
        client = FakeOllama([
            "garbage 1", "garbage 2", "garbage 3",   # dead repo: 3 fails
            json.dumps(llm_json()),                    # alive repo: success
        ])
        result = process_message(
            "https://github.com/o/dead and https://github.com/o/alive",
            repos, client, self.config, self.vault)

        self.assertEqual(result["failed"], ["https://github.com/o/dead"])
        self.assertEqual(len(result["notes"]), 1)
        self.assertIn('alive', result["notes"][0])
        # no partial/empty note for the failed repo anywhere in the vault
        for root, _dirs, files in os.walk(self.vault):
            for fname in files:
                with open(os.path.join(root, fname), encoding='utf-8') as f:
                    self.assertNotIn('dead', f.read().split('---')[-1])

    # -- collisions & files -------------------------------------------------

    def test_same_name_collision_gets_versioned_filename(self):
        repos = {
            "https://github.com/a/tool": fake_repo(name="tool", owner="a"),
            "https://github.com/b/tool": fake_repo(name="tool", owner="b"),
        }
        client = FakeOllama([json.dumps(llm_json()),
                             json.dumps(llm_json())])
        result = process_message(
            "https://github.com/a/tool and https://github.com/b/tool",
            repos, client, self.config, self.vault)

        self.assertEqual(len(result["notes"]), 2)
        first, second = result["notes"]
        self.assertNotEqual(first, second)
        self.assertTrue(second.endswith('_v1.md'))
        # both notes exist and reference their OWN repo
        with open(first, encoding='utf-8') as f:
            self.assertIn('github.com/a/tool', f.read())
        with open(second, encoding='utf-8') as f:
            self.assertIn('github.com/b/tool', f.read())

    def test_banner_atomic_write_and_note_reference(self):
        banner_dir = os.path.join(self.vault, 'attachments', 'banners')
        banner_bytes = b'\x89PNG\r\n\x1a\n' + bytes(range(256)) * 8
        storage.atomic_write_bytes(
            os.path.join(banner_dir, 'repo_banner.png'), banner_bytes)

        note = note_builder.build_note(
            url="https://github.com/owner/repo",
            repo_name="repo", owner="owner", org_name="Some Org",
            stars=10, forks=2, commit_count=5, cred_score=80, org_rep=7,
            summary="A" * 80, tags=["cli"], category_key="AI & ML",
            confidence=90, how_it_works="how", core_value="val",
            features=["f1", "f2", "f3"], difference="diff",
            banner_path="repo_banner.png", primary_language="Python",
        )
        self.assertIn('cover: attachments/banners/repo_banner.png', note)
        self.assertIn('![banner](attachments/banners/repo_banner.png)', note)
        with open(os.path.join(banner_dir, 'repo_banner.png'), 'rb') as f:
            self.assertEqual(f.read(), banner_bytes)  # byte-exact, untruncated
        # no temp leftovers from the atomic write
        leftovers = [f for f in os.listdir(banner_dir)
                     if f.startswith('.tmp_')]
        self.assertEqual(leftovers, [])

    def test_config_round_trip_preserves_cloud_and_user_keys(self):
        existing = {
            "ollama": {"base_url": "http://localhost:11434",
                       "model": "old-model", "llm_timeout_s": 300},
            "timeout_per_repo": 120,
            "cloudflare_install_id": "uuid-123",
            "cloudflare_shared_secret": "abc",
            "gdrive_folder_id": "xyz",
            "user_added_key": 42,
        }
        updates = {"ollama": {"model": "new-model"}}
        merged = storage.merge_config(existing, updates)
        path = os.path.join(self.vault, 'config.json')
        storage.write_config_file(path, merged)

        with open(path, encoding='utf-8') as f:
            loaded = json.load(f)

        self.assertEqual(loaded["ollama"]["model"], "new-model")
        self.assertEqual(loaded["ollama"]["llm_timeout_s"], 300)
        self.assertEqual(loaded["timeout_per_repo"], 120)
        self.assertEqual(loaded["cloudflare_install_id"], "uuid-123")
        self.assertEqual(loaded["cloudflare_shared_secret"], "abc")
        self.assertEqual(loaded["gdrive_folder_id"], "xyz")
        self.assertEqual(loaded["user_added_key"], 42)


if __name__ == '__main__':
    unittest.main(verbosity=2)
