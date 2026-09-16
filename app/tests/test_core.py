#!/usr/bin/env python3
"""
test_core.py — first unit tests for the extracted testable core.

v30 — Fix (Extract the testable core): links.py / storage.py /
note_builder.py / llm_client.py were carved out of the 8.8k-line main.py
PRECISELY so they can be tested without PyQt6 / Ollama / GitHub. This suite
locks down:

  - links:      the ONE GitHub regex (www + dots + paths), normalization,
                dedup — regression protection against re-drift
  - storage:    atomic writes, path-traversal-proof filenames, collision
                handling, and the config MERGE that replaced the
                whitelist-rebuild that destroyed cloudflare_*/gdrive_* keys
  - note_builder: YAML-injection hardening of every frontmatter value
  - llm_client: JSON extraction from messy LLM output + the timeout wrapper

Run:  python -m unittest tests.test_core -v
   or: python tests/test_core.py
"""

import os
import sys
import tempfile
import time
import unittest

# Make the app dir importable no matter where we run from.
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

import links
import storage
import note_builder
import llm_client


# ---------------------------------------------------------------------------
# links
# ---------------------------------------------------------------------------

class TestLinks(unittest.TestCase):

    def test_extract_basic(self):
        text = "see https://github.com/torvalds/linux please"
        self.assertEqual(links.extract_github_urls(text), ["https://github.com/torvalds/linux"])

    def test_extract_www_and_dots(self):
        # The OLD main.py regex (no-www, no-dots) missed BOTH of these.
        # (Scheme is required — same as every historical code path.)
        text = "https://www.github.com/john.doe/my.project and https://github.com/a.b/c.d"
        got = links.extract_github_urls(text)
        self.assertEqual(sorted(got), sorted(["https://github.com/john.doe/my.project",
                                              "https://github.com/a.b/c.d"]))

    def test_extract_with_paths_and_query(self):
        text = "https://github.com/owner/repo/issues/42?from=search"
        self.assertEqual(links.extract_github_urls(text),
                         ["https://github.com/owner/repo"])

    def test_extract_dedupes_and_preserves_order(self):
        text = ("https://github.com/a/b then https://github.com/c/d "
                "then https://github.com/a/b again")
        self.assertEqual(links.extract_github_urls(text),
                         ["https://github.com/a/b", "https://github.com/c/d"])

    def test_extract_ignores_gists_and_non_repos(self):
        self.assertEqual(links.extract_github_urls("https://gist.github.com/x/1"), [])

    def test_split_links(self):
        text = ("https://github.com/o/r and https://x.com/post?s=20 and "
                "https://www.github.com/o2/r2/tree/main read https://reddit.com/x.")
        gh, non_gh, raw = links.split_links(text)
        self.assertEqual(sorted(gh), sorted(["https://github.com/o/r",
                                             "https://github.com/o2/r2"]))
        # non-GitHub links keep their raw form (query included — normalization
        # happens at dedup time); trailing period stripped
        self.assertIn("https://x.com/post?s=20", non_gh)
        self.assertIn("https://reddit.com/x", non_gh)
        self.assertGreaterEqual(raw, 3)

    def test_is_github_url(self):
        self.assertTrue(links.is_github_url("https://github.com/a/b"))
        self.assertTrue(links.is_github_url("https://www.github.com/a.b/c.d"))
        self.assertFalse(links.is_github_url("https://gitlab.com/a/b"))
        self.assertFalse(links.is_github_url(""))
        # traversal-ish garbage must not validate
        self.assertFalse(links.is_github_url("https://github.com/../etc/passwd"))

    def test_normalize_url(self):
        self.assertEqual(links.normalize_url("https://x.com/a?utm=1#frag"),
                         "https://x.com/a")
        self.assertEqual(links.normalize_url("https://twitter.com/a/"),
                         "https://x.com/a")
        # domain lowercased, path case PRESERVED (GitHub paths are case-sensitive)
        self.assertEqual(links.normalize_url("https://GitHub.COM/User/Repo"),
                         "https://github.com/User/Repo")
        self.assertEqual(links.normalize_url(""), "")

    def test_dedupe_urls(self):
        urls = ["https://github.com/a/b", "https://github.com/a/b/",
                "https://x.com/p", "https://x.com/p?q=1"]
        unique, dups = links.dedupe_urls(urls)
        self.assertEqual(unique, ["https://github.com/a/b", "https://x.com/p"])
        self.assertEqual(dups, 2)


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------

class TestStorage(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='curator-test-')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_atomic_write_text(self):
        path = os.path.join(self.tmpdir, 'note.md')
        storage.atomic_write_text(path, 'hello')
        with open(path, encoding='utf-8') as f:
            self.assertEqual(f.read(), 'hello')
        # Overwrite is atomic and clean
        storage.atomic_write_text(path, 'second version' * 100)
        with open(path, encoding='utf-8') as f:
            self.assertEqual(f.read(), 'second version' * 100)
        # No temp files left behind
        leftovers = [f for f in os.listdir(self.tmpdir) if f.startswith('.tmp_')]
        self.assertEqual(leftovers, [])

    def test_atomic_write_bytes(self):
        path = os.path.join(self.tmpdir, 'banner.png')
        storage.atomic_write_bytes(path, b'\x89PNG\r\n\x1a\n' + b'x' * 2000)
        with open(path, 'rb') as f:
            self.assertEqual(f.read(4), b'\x89PNG')

    def test_safe_filename_defeats_traversal(self):
        # No path separators and no '..' can survive — traversal impossible.
        out = storage.safe_filename('../../etc/passwd')
        self.assertNotIn('/', out)
        self.assertNotIn('\\', out)
        self.assertNotIn('..', out)
        self.assertNotIn('..', storage.safe_filename('a/b\\c..d'))
        self.assertEqual(storage.safe_filename('my.repo-name_2'), 'my_repo-name_2')
        # (dots -> underscore matches the ORIGINAL main.py filename behavior
        #  that existing notes / undo lists were built on)

    def test_unique_path(self):
        path = os.path.join(self.tmpdir, 'note.md')
        with open(path, 'w') as f:
            f.write('first')
        second = storage.unique_path(path)
        self.assertTrue(second.endswith('_v1.md'))
        with open(second, 'w') as f:
            f.write('second')
        third = storage.unique_path(path)
        self.assertTrue(third.endswith('_v2.md'))

    def test_build_note_filename(self):
        name = storage.build_note_filename('my.repo', 'AI & ML', ['llm', 'x'])
        self.assertEqual(name, 'my_repo_AI_ML_llm.md')
        self.assertTrue(storage.build_note_filename('r', 'c', []).endswith('_misc.md'))

    def test_merge_config_preserves_unknown_keys(self):
        # THE regression test for the save_config key-wipe: cloudflare_* and
        # gdrive_* keys MUST survive a save.
        existing = {
            "ollama": {"base_url": "http://localhost:11434", "model": "old",
                       "custom_key": "keep-me"},
            "timeout_per_repo": 120,
            "max_retries": 7,
            "delay_between_api_calls": 2.5,
            "cloudflare_install_id": "uuid-123",
            "cloudflare_shared_secret": "abc",
            "gdrive_folder_id": "xyz",
            "user_added_key": 42,
        }
        updates = {
            "ollama": {"model": "new-model"},
            "proxy": {"host": "127.0.0.1"},
        }
        merged = storage.merge_config(existing, updates)

        self.assertEqual(merged["ollama"]["model"], "new-model")      # updated
        self.assertEqual(merged["ollama"]["base_url"], "http://localhost:11434")  # kept
        self.assertEqual(merged["ollama"]["custom_key"], "keep-me")   # nested keep
        self.assertEqual(merged["timeout_per_repo"], 120)            # NOT stomped
        self.assertEqual(merged["max_retries"], 7)                    # NOT stomped
        self.assertEqual(merged["delay_between_api_calls"], 2.5)     # NOT stomped
        self.assertEqual(merged["cloudflare_install_id"], "uuid-123") # preserved
        self.assertEqual(merged["cloudflare_shared_secret"], "abc")   # preserved
        self.assertEqual(merged["gdrive_folder_id"], "xyz")           # preserved
        self.assertEqual(merged["user_added_key"], 42)                # preserved
        self.assertEqual(merged["proxy"]["host"], "127.0.0.1")

    def test_merge_config_defaults_only_when_absent(self):
        merged = storage.merge_config({}, {})
        self.assertEqual(merged["timeout_per_repo"], 60)
        self.assertEqual(merged["max_retries"], 3)
        self.assertEqual(merged["delay_between_api_calls"], 0.5)


# ---------------------------------------------------------------------------
# note_builder (YAML injection hardening)
# ---------------------------------------------------------------------------

class TestNoteBuilder(unittest.TestCase):

    def test_sanitize_tags_blocks_yaml_injection(self):
        evil = ["pwned, x]  # injected", "{{pipeline}}", "a:b", "'quoted'",
                'd"q', "ok-tag", "ok tag 2", "  spaced  ", "-leading-dash"]
        out = note_builder.sanitize_tags(evil)
        for t in out:
            # No character that can escape a YAML flow sequence or start a comment
            for ch in ',[]{}#:\'"':
                self.assertNotIn(ch, t)
        self.assertIn("ok-tag", out)
        self.assertIn("ok tag 2", out)

    def test_sanitize_tags_dedup_case_insensitive_and_cap(self):
        out = note_builder.sanitize_tags(["Tag", "tag", "TAG", "b"] * 20)
        self.assertEqual(out, ["Tag", "b"])

    def test_sanitize_aliases(self):
        out = note_builder.sanitize_aliases("repo.name", "owner/repo.name", "repo.name")
        self.assertEqual(out, ["repo.name", "owner/repo.name"])  # dedup'd

    def test_yaml_scalar_quotes_everything(self):
        self.assertEqual(note_builder.yaml_scalar("simple"), '"simple"')
        out = note_builder.yaml_scalar('injected" ] # comment')
        self.assertTrue(out.startswith('"'))
        # embedded quote escaped
        self.assertIn('\\"', out)

    def test_build_note_frontmatter_is_injection_safe(self):
        note = note_builder.build_note(
            url="https://github.com/a/b?x=1",
            repo_name="Evil] Repo", owner="ow ner", org_name='Org: "#pwn"',
            stars=10, forks=2, commit_count=5, cred_score=55, org_rep=3,
            summary="A" * 80, tags=["ok", "bad,tag] #pwn", "{{x}}"],
            category_key="AI & ML", confidence=80,
            how_it_works="how", core_value="val",
            features=["f1", "f2", "f3"], difference="diff",
            banner_path=None, primary_language="Python",
            languages=["Python", "C++"], short_summary="short",
            latest_release_date="2024-01-01",
        )
        fm = note.split('---')[1]
        # tags line: split the flow sequence into items and verify EACH item
        # contains no character that can escape the sequence or start a comment
        tags_line = [l for l in fm.splitlines() if l.startswith('tags:')][0]
        inner = tags_line[len('tags: ['):-1] if tags_line.endswith(']') else tags_line[len('tags: ['):]
        items = [i.strip() for i in inner.split(',')] if inner.strip() else []
        self.assertTrue(items, 'expected at least one tag')
        for item in items:
            for ch in '[]{}#:\'"':
                self.assertNotIn(ch, item, f'unsafe char {ch!r} in tag {item!r}')
        # org is a quoted scalar
        org_line = [l for l in fm.splitlines() if l.startswith('org:')][0]
        self.assertTrue(org_line.startswith('org: "'))
        # source URL quoted
        src_line = [l for l in fm.splitlines() if l.startswith('source:')][0]
        self.assertTrue(src_line.startswith('source: "'))
        # body sanity (']' in a markdown H1 is harmless — only YAML is hardened)
        self.assertIn('# Evil] Repo', note)
        self.assertIn('## What is it?', note)

    def test_body_text_strips_control_chars(self):
        # \r is converted to \n (Windows line endings), other control chars
        # (\x00, \x07) are stripped.
        out = note_builder.sanitize_body_text("a\x00b\x07c\rd")
        self.assertEqual(out, "abc\nd")

    def test_short_summary_collapses_to_one_line(self):
        out = note_builder.sanitize_short_summary("line1\nline2\r\nline3  ")
        self.assertEqual(out, "line1 line2 line3")

    def test_rep_and_score_helpers(self):
        self.assertEqual(note_builder.rep_to_str(9), "High (Major tech company)")
        self.assertEqual(note_builder.score_to_rating(95), "Excellent")
        self.assertEqual(note_builder.score_to_rating(10), "Low")


# ---------------------------------------------------------------------------
# llm_client
# ---------------------------------------------------------------------------

class TestLLMClient(unittest.TestCase):

    def test_extract_json_plain(self):
        self.assertEqual(llm_client.extract_json('{"a": 1}'), {"a": 1})

    def test_extract_json_fenced(self):
        text = 'Sure! Here is the JSON:\n```json\n{"a": 2}\n```\nDone.'
        self.assertEqual(llm_client.extract_json(text), {"a": 2})

    def test_extract_json_embedded_in_prose(self):
        text = 'The result is {"a": {"b": 3}} as requested.'
        self.assertEqual(llm_client.extract_json(text), {"a": {"b": 3}})

    def test_extract_json_invalid_raises(self):
        with self.assertRaises(ValueError):
            llm_client.extract_json("no json here at all")
        with self.assertRaises(ValueError):
            llm_client.extract_json("")

    def test_call_with_timeout_fast_fn(self):
        self.assertEqual(llm_client.call_with_timeout(lambda a, b=0: a + b, 5, 2, b=3), 5)

    def test_call_with_timeout_times_out(self):
        def slow():
            time.sleep(2.0)
            return "late"
        start = time.time()
        with self.assertRaises(TimeoutError):
            llm_client.call_with_timeout(slow, 0.2)
        self.assertLess(time.time() - start, 1.5)  # returned promptly

    def test_call_with_timeout_zero_is_direct(self):
        self.assertEqual(llm_client.call_with_timeout(lambda: 7, 0), 7)

    def test_response_content_shapes(self):
        class Msg:
            content = "obj-content"
        class Resp:
            message = Msg()
        self.assertEqual(llm_client.response_content(Resp()), "obj-content")
        self.assertEqual(llm_client.response_content({"message": {"content": "dict"}}), "dict")
        self.assertEqual(llm_client.response_content("plain"), "plain")
        self.assertEqual(llm_client.response_content(None), "")

    def test_list_models_shapes(self):
        class M:
            def __init__(self, name): self.model = name
        class Resp:
            models = [M("new-api"), M("second")]
        class NewClient:
            def list(self): return Resp()
        self.assertEqual(llm_client.list_models_with_timeout(NewClient(), 5),
                         ["new-api", "second"])

        class OldClient:
            def list(self): return {"models": [{"name": "old-api"},
                                               {"model": "old-api-2"}]}
        self.assertEqual(llm_client.list_models_with_timeout(OldClient(), 5),
                         ["old-api", "old-api-2"])

    def test_chat_with_timeout(self):
        class FakeChat:
            def chat(self, **kwargs):
                class R:
                    class message:
                        content = f"echo:{kwargs.get('model')}"
                return R()
        got = llm_client.chat_with_timeout(FakeChat(), 5, model="m1", messages=[])
        self.assertEqual(got, "echo:m1")


if __name__ == '__main__':
    unittest.main(verbosity=2)
