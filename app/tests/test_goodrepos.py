#!/usr/bin/env python3
"""
test_goodrepos.py — unit tests for GoodRepos (goodrepos.py).

GoodRepos publishes the curated vault as a PUBLIC "good-repos" GitHub
directory: a generated emoji README + the notes mirrored into category
folders. Like VaultSeal, the module is pure stdlib and NEVER raises — so the
whole suite runs headlessly, with NO network and NO git remotes (every
publisher below is built with auto_push=False / token=""). The local git
machinery (init/commit/skip-when-unchanged) is exercised for real in a
throwaway mkdtemp staging dir.

Locks down:
  - vault scan (notes, ignored dirs, root extras), staging tree layout
  - README structure: title, stats line, contents anchors, entry lines
  - GitHub anchor math (emoji -> leading hyphen, counts -> trailing digits)
  - sorting (stars desc), category order, per-category counts
  - TL;DR extraction + "What is it?" fallback + graceful absence
  - owner/repo display-name resolution (url -> aliases -> filename)
  - idempotency (second publish on the same instance skips)
  - guards (disabled / missing vault) and the config bridge
  - frontmatter edge cases, star formatting, emoji fallback

Run:  python -m unittest tests.test_goodrepos -v
   or: python tests/test_goodrepos.py
"""

import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Make the app dir importable no matter where we run from.
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from gitcurator.integrations import goodrepos

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

NOTE_TMPL = """---
source: "{source}"
aliases:
  - {alias}
  - {alias}/{repo}
tags: [{tags}]
category: "{category}"
stars: {stars}
org: ""
primary_language: {lang}
languages: [{lang}]
credibility_score: 78/100
date_processed: 2026-09-16
---

# {repo}

{tldr_line}

**`{alias}/{repo}`** · ⭐ {stars} · 🔧 {lang}

## What is it?
{what}
"""


def note_text(source, repo, alias, category, stars, lang, tldr,
              tags="alpha, beta", what="Fallback description sentence."):
    tldr_line = f"> **TL;DR:** {tldr}" if tldr is not None else ""
    return NOTE_TMPL.format(
        source=source, repo=repo, alias=alias, category=category,
        stars=stars, lang=lang, tldr_line=tldr_line, tags=tags, what=what,
    )


def simple_note(name, stars=100, tldr="Does one thing well.", **kw):
    """A note for owner ``name``/``name``-style repos with sane defaults."""
    kw.setdefault("alias", name)
    kw.setdefault("category", "Agents")
    kw.setdefault("lang", "Python")
    kw.setdefault("tags", "alpha, beta")
    kw.setdefault("what", "Fallback description sentence.")
    return note_text(
        source=f"https://github.com/{name}/{name}",
        repo=name, stars=stars, tldr=tldr, **kw
    )


class GoodReposBase(unittest.TestCase):
    """Tiny in-memory vault + publisher factory (no network, no remotes)."""

    def setUp(self):
        self.vault = Path(tempfile.mkdtemp(prefix="grtest-vault-"))
        self.addCleanup(shutil.rmtree, self.vault, ignore_errors=True)

    def write(self, rel, text):
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def publisher(self, **kw):
        kw.setdefault("auto_push", False)  # NEVER push in tests
        pub = goodrepos.GoodRepos(vault_path=str(self.vault), **kw)
        self.addCleanup(self._drop_staging, pub)
        return pub

    @staticmethod
    def _drop_staging(pub):
        staging = getattr(pub, "_staging", None)
        if staging:
            shutil.rmtree(staging, ignore_errors=True)
            try:
                goodrepos._STAGING_CLEANUP.remove(staging)
            except ValueError:
                pass

    def default_vault(self):
        """3 curated notes across 2 categories + the two root extras."""
        self.write("AI-Domain/Agents/aaa_Agents_x.md",
                   simple_note("aaa", stars=100, tldr="First agent summary."))
        self.write("AI-Domain/Agents/bbb_Agents_y.md",
                   simple_note("bbb", stars=900, tldr="Second agent summary."))
        self.write("Tools/Automation/ccc_Automation_z.md",
                   simple_note("ccc", stars=42, tldr="Automation summary.",
                               category="Automation"))
        self.write("_index.md", "# Index\n")
        self.write("links_manifest.json", json.dumps(
            {"generated": "2026-09-16",
             "links": [{"url": "https://github.com/aaa/aaa", "status": "processed"}]}))

    def readme(self, result):
        return (Path(result.staging_dir) / "README.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1-2. Scan, publish, staging tree, README structure
# ---------------------------------------------------------------------------

class TestPublish(GoodReposBase):

    def test_publish_basic_tree(self):
        self.default_vault()
        result = self.publisher().publish()
        self.assertTrue(result.ok, result.describe())
        self.assertTrue(result.published)
        self.assertEqual(result.entries, 3)
        self.assertEqual(result.categories, 2)
        self.assertFalse(result.pushed)  # no token -> local commit only
        self.assertIsNotNone(result.commit_sha)

        staging = Path(result.staging_dir)
        # notes mirrored at their vault-relative paths
        for rel in ("AI-Domain/Agents/aaa_Agents_x.md",
                    "AI-Domain/Agents/bbb_Agents_y.md",
                    "Tools/Automation/ccc_Automation_z.md",
                    "_index.md", "links_manifest.json", "README.md"):
            self.assertTrue((staging / rel).is_file(), f"missing {rel}")

    def test_readme_structure(self):
        self.default_vault()
        readme = self.readme(self.publisher().publish())
        self.assertIn("# 🗂️ Good Repos", readme)
        self.assertRegex(readme, r"📊 \*\*3 repos\*\* · 🗂️ \*\*2 categories\*\* · 🕰️ Updated \d{4}-\d{2}-\d{2}")
        self.assertIn("## 📇 Contents", readme)
        self.assertIn("[GitCurator](https://github.com/assadigit/GitCurator)", readme)
        self.assertIn("[aaa/aaa](https://github.com/aaa/aaa)", readme)
        self.assertIn("🧠", readme)   # Agents emoji
        self.assertIn("🛠️", readme)  # Tools emoji
        self.assertIn("regenerated automatically after every GitCurator curation run",
                      readme)

    def test_entry_line_format(self):
        self.default_vault()
        readme = self.readme(self.publisher().publish())
        # - 📦 [owner/repo](url) — TL;DR · ⭐ n · 🔧 Lang · `tag` `tag`
        self.assertRegex(
            readme,
            r"- 📦 \[bbb/bbb\]\(https://github\.com/bbb/bbb\) — Second agent summary\. "
            r"· ⭐ 900 · 🔧 Python · `alpha` `beta`")

    def test_status_scan_only(self):
        self.default_vault()
        info = self.publisher().status()
        self.assertEqual(info["entries"], 3)
        self.assertEqual(info["categories"], 2)
        self.assertEqual(info["tree"], {"AI-Domain/Agents": 2, "Tools/Automation": 1})
        self.assertEqual(info["repo_name"], "good-repos")
        self.assertTrue(info["exists"])

    def test_dry_run_builds_tree_without_git(self):
        self.default_vault()
        result = self.publisher().publish(dry_run=True)
        self.addCleanup(shutil.rmtree, result.staging_dir, ignore_errors=True)
        self.assertTrue(result.ok, result.describe())
        self.assertTrue(result.published)
        self.assertTrue(result.dry_run)
        self.assertFalse(result.pushed)
        self.assertIsNone(result.commit_sha)
        staging = Path(result.staging_dir)
        self.assertFalse((staging / ".git").exists())  # no git at all
        self.assertTrue((staging / "README.md").is_file())
        self.assertTrue((staging / "AI-Domain/Agents/aaa_Agents_x.md").is_file())


# ---------------------------------------------------------------------------
# 3. Anchor correctness (GitHub math)
# ---------------------------------------------------------------------------

class TestAnchors(GoodReposBase):

    def test_anchor_math_spec_examples(self):
        # verified against GitHub's algorithm: lowercase, drop emoji/
        # punctuation, spaces->hyphens (emoji leaves a leading hyphen)
        self.assertEqual(goodrepos.github_anchor("🤖 AI Domain"), "-ai-domain")
        self.assertEqual(goodrepos.github_anchor("⚙️ Automation"), "-automation")
        self.assertEqual(goodrepos.github_anchor("🧠 Agents (2)"), "-agents-2")
        self.assertEqual(goodrepos.github_anchor("📦 Uncategorized"), "-uncategorized")
        self.assertEqual(goodrepos.github_anchor("🧰 LLM Tools (1)"), "-llm-tools-1")
        self.assertEqual(goodrepos.github_anchor("🌐 Web Frameworks (3)"),
                         "-web-frameworks-3")

    def test_contents_links_match_headings(self):
        self.default_vault()
        readme = self.readme(self.publisher().publish())

        heading_anchors = set()
        for line in readme.splitlines():
            if line.startswith("## ") or line.startswith("### "):
                # independent reimplementation of GitHub's anchor algorithm
                text = line.lstrip("#").strip().lower()
                kept = "".join(
                    ch for ch in text if ch.isalnum() or ch in (" ", "-"))
                heading_anchors.add(kept.replace(" ", "-"))

        targets = set(re.findall(r"\]\(#([^)]+)\)", readme))
        self.assertTrue(targets)
        # every link target must be the anchor of a real heading …
        self.assertLessEqual(targets, heading_anchors)
        # … and every section heading is linked (except Contents itself)
        self.assertEqual(heading_anchors - targets, {"-contents"})

    def test_specific_anchors_present(self):
        self.default_vault()
        readme = self.readme(self.publisher().publish())
        self.assertIn("(#-ai-domain)", readme)
        self.assertIn("(#-agents-2)", readme)
        self.assertIn("(#-automation-1)", readme)
        self.assertIn("(#-tools)", readme)


# ---------------------------------------------------------------------------
# 4. Sorting, ordering, counts
# ---------------------------------------------------------------------------

class TestOrdering(GoodReposBase):

    def test_sorted_by_stars_desc_and_categories_alpha(self):
        self.default_vault()
        readme = self.readme(self.publisher().publish())

        # entries sorted by stars desc within their category
        aaa_pos = readme.index("[aaa/aaa]")
        bbb_pos = readme.index("[bbb/bbb]")
        self.assertLess(bbb_pos, aaa_pos)  # 900 stars before 100

        # category sections alphabetical: AI-Domain before Tools
        self.assertLess(readme.index("## 🤖 AI Domain"),
                        readme.index("## 🛠️ Tools"))
        # contents lists AI Domain before Tools too
        contents = readme.split("## 📇 Contents", 1)[1].split("\n\n## ", 1)[0]
        self.assertLess(contents.index("AI Domain"), contents.index("Tools"))

    def test_per_category_counts(self):
        self.default_vault()
        readme = self.readme(self.publisher().publish())
        self.assertIn("### 🧠 Agents (2)", readme)
        self.assertIn("### ⚙️ Automation (1)", readme)
        self.assertIn("📊 **3 repos** · 🗂️ **2 categories**", readme)

    def test_subs_alphabetical_despite_emoji(self):
        # emoji code points (✨ U+2728 sorts before 🧠 U+1F9E0) must NOT
        # decide the order — the DISPLAY name does (demo-vault regression)
        for sub in ("Skills", "MCP", "Agents", "LLM-Tools"):
            self.write(f"AI-Domain/{sub}/{sub.lower()}_note.md",
                       simple_note(sub.lower(), category=sub))
        readme = self.readme(self.publisher().publish())
        order = [readme.index(f"### {goodrepos.emoji_for(sub)} ")
                 for sub in ("Agents", "LLM-Tools", "MCP", "Skills")]
        self.assertEqual(order, sorted(order))
        contents = readme.split("## 📇 Contents", 1)[1].split("\n\n## ", 1)[0]
        c_order = [contents.index(n) for n in
                   ("Agents", "LLM Tools", "MCP", "Skills")]
        self.assertEqual(c_order, sorted(c_order))


# ---------------------------------------------------------------------------
# 5-6. TL;DR extraction, display-name resolution
# ---------------------------------------------------------------------------

class TestNoteParsing(GoodReposBase):

    def test_tldr_extracted_and_fallback(self):
        self.write("AI-Domain/Agents/aaa_Agents_x.md",
                   simple_note("aaa", tldr="Primary summary."))
        self.write("AI-Domain/Agents/bbb_Agents_y.md",
                   simple_note("bbb", tldr=None, what="Fallback description sentence."))
        self.write("AI-Domain/Agents/ccc_Agents_z.md",
                   note_text("https://github.com/ccc/ccc", "ccc", "ccc", "Agents",
                             5, "Go", None, what="", tags="solo"))
        readme = self.readme(self.publisher().publish())

        self.assertIn("— Primary summary.", readme)
        self.assertIn("— Fallback description sentence.", readme)
        # no TL;DR anywhere -> line renders without the "—" tail piece
        ccc_line = next(ln for ln in readme.splitlines() if "[ccc/ccc]" in ln)
        self.assertNotIn(" — ", ccc_line)
        self.assertRegex(ccc_line, r"- 📦 \[ccc/ccc\]\(https://github\.com/ccc/ccc\) · ⭐ 5 · 🔧 Go · `solo`")

    def test_display_name_url_aliases_filename(self):
        # 1) parsed from the source url (with trailing .git + path noise)
        self.write("AI-Domain/Agents/aaa_Agents_x.md",
                   simple_note("aaa").replace(
                       "https://github.com/aaa/aaa",
                       "https://github.com/aaa/aaa.git/tree/main"))
        # 2) no source -> alias containing "/" wins
        self.write("AI-Domain/Agents/bbb_Agents_y.md",
                   simple_note("bbb").replace('source: "https://github.com/bbb/bbb"',
                                              'source: ""'))
        # 3) no source, bare alias only
        self.write("AI-Domain/Agents/ccc_Agents_z.md",
                   note_text("", "ccc", "ccc", "Agents", 7, "Go", "C summary.",
                             tags="t").replace("  - ccc\n  - ccc/ccc", "  - ccc"))
        # 4) nothing at all -> filename stem before the first "_"
        self.write("AI-Domain/Agents/ddd_Agents_w.md",
                   "# ddd\n\nno frontmatter at all, just a body\n")

        readme = self.readme(self.publisher().publish())
        self.assertIn("[aaa/aaa](https://github.com/aaa/aaa)", readme)
        self.assertIn("- 📦 bbb/bbb —", readme)  # alias "bbb/bbb" rescued the name
        self.assertIn("- 📦 ccc —", readme)      # bare alias
        self.assertIn("- 📦 ddd", readme)       # filename fallback "ddd"

    def test_frontmatter_edge_cases(self):
        weird = """---
source: "https://github.com/xxx/xxx"
aliases: []
tags: not-a-list, malformed]
category: ""
stars: "14k"
org: ""
primary_language: ""
languages: []
credibility_score: 78/100
date_processed: 2026-09-16
---

# xxx

> **TL;DR:** Weird frontmatter.

**`xxx/xxx`** · ⭐ ? · 🔧 ?
"""
        self.write("Uncategorized/xxx_Uncategorized_y.md", weird)
        self.write("Uncategorized/yyy_Uncategorized_z.md",
                   "# yyy\n\n> **TL;DR:** No frontmatter at all.\n")
        result = self.publisher().publish()
        self.assertEqual(result.entries, 2)
        self.assertEqual(result.categories, 1)
        readme = self.readme(result)
        self.assertIn("## 📦 Uncategorized", readme)
        # "14k" stars -> unparsable -> ⭐ piece skipped, no crash
        xxx_line = next(ln for ln in readme.splitlines() if "[xxx/xxx]" in ln)
        self.assertNotIn("⭐", xxx_line)
        self.assertNotIn("🔧", xxx_line)
        # malformed tag list degrades into individual tags
        self.assertIn("`not-a-list`", readme)
        self.assertIn("`malformed`", readme)
        # note without frontmatter still publishes (filename fallback)
        self.assertIn("- 📦 yyy", readme)

    def test_stars_string_value(self):
        self.write("AI-Domain/Agents/aaa_Agents_x.md",
                   simple_note("aaa", stars="1234"))
        readme = self.readme(self.publisher().publish())
        self.assertIn("⭐ 1,234", readme)


# ---------------------------------------------------------------------------
# 7-8. Idempotency + guards
# ---------------------------------------------------------------------------

class TestLifecycle(GoodReposBase):

    def test_publish_twice_skips_when_unchanged(self):
        self.default_vault()
        pub = self.publisher()
        first = pub.publish()
        self.assertTrue(first.published)
        second = pub.publish()
        self.assertFalse(second.published)
        self.assertTrue(second.skipped_reason)
        self.assertIn("unchanged", second.skipped_reason)
        self.assertTrue(second.ok)  # a skip is a success
        self.assertFalse(second.pushed)

    def test_republish_after_change_commits_again(self):
        self.default_vault()
        pub = self.publisher()
        self.assertTrue(pub.publish().published)
        self.write("AI-Domain/Agents/ddd_Agents_v.md",
                   simple_note("ddd", stars=1))
        third = pub.publish()
        self.assertTrue(third.published)
        self.assertEqual(third.entries, 4)

    def test_disabled_short_circuits(self):
        self.default_vault()
        result = self.publisher(enabled=False).publish()
        self.assertFalse(result.published)
        self.assertTrue(result.skipped_reason)
        self.assertIn("disabled", result.skipped_reason)
        self.assertTrue(result.ok)

    def test_missing_vault_is_an_error(self):
        pub = goodrepos.GoodRepos(vault_path=str(self.vault / "does-not-exist"),
                                  auto_push=False)
        result = pub.publish()
        self.assertFalse(result.ok)
        self.assertFalse(result.published)
        self.assertTrue(result.error)
        self.assertIn("vault directory not found", result.error)

    def test_empty_vault_skips(self):
        result = self.publisher().publish()
        self.assertFalse(result.published)
        self.assertEqual(result.skipped_reason, "vault contains no curated notes")
        self.assertTrue(result.ok)


# ---------------------------------------------------------------------------
# 9-10. Config bridge
# ---------------------------------------------------------------------------

class TestConfigBridge(GoodReposBase):

    def test_publish_from_config_bridges_keys(self):
        self.default_vault()
        config = {
            "vault_path": str(self.vault),
            "github_token": "",
            "goodrepos": {"enabled": True, "repo_name": "my-good-dir",
                          "auto_push": True},
        }
        result = goodrepos.publish_from_config(config)
        self.assertTrue(result.ok, result.describe())
        self.assertTrue(result.published)
        self.assertEqual(result.entries, 3)
        self.assertEqual(result.repo_name, "my-good-dir")
        self.assertFalse(result.pushed)  # no token -> auto_push degrades

    def test_publish_from_config_disabled_short_circuits(self):
        # enabled=False returns BEFORE any disk access: even a bogus
        # vault_path never turns into an error.
        config = {
            "vault_path": str(self.vault / "nope"),
            "github_token": "irrelevant-but-empty",
            "goodrepos": {"enabled": False},
        }
        result = goodrepos.publish_from_config(config)
        self.assertFalse(result.published)
        self.assertIn("disabled", result.skipped_reason)
        self.assertTrue(result.ok)


# ---------------------------------------------------------------------------
# 11. Root extras copied, machine state excluded
# ---------------------------------------------------------------------------

class TestVaultHygiene(GoodReposBase):

    def test_extras_copied_and_noise_excluded(self):
        self.write("AI-Domain/Agents/aaa_Agents_x.md", simple_note("aaa"))
        self.write("_index.md", "# Index\n")
        self.write("links_manifest.json", '{"generated": "2026-09-16", "links": []}')
        self.write("README.md", "# the vault's own README (must NOT win)\n")
        self.write(".obsidian/app.json", "{}")
        self.write(".obsidian/workspace.json", "{}")
        self.write("_inbox/stray.md", simple_note("stray"))
        self.write("_review/pending.md", simple_note("pending"))
        self.write("_moc/map.md", simple_note("map"))
        self.write(".trash/deleted.md", simple_note("deleted"))
        self.write("__pycache__/junk.md", simple_note("junk"))
        self.write("AI-Domain/Agents/notes.txt", "not a note")

        result = self.publisher().publish()
        self.assertEqual(result.entries, 1)
        staging = Path(result.staging_dir)
        self.assertTrue((staging / "_index.md").is_file())
        self.assertTrue((staging / "links_manifest.json").is_file())
        for excluded in (".obsidian", "_inbox", "_review", "_moc", ".trash",
                         "__pycache__", "AI-Domain/Agents/notes.txt"):
            self.assertFalse((staging / excluded).exists(),
                             f"{excluded} leaked into the directory")
        readme = self.readme(result)
        self.assertNotIn("stray", readme)
        self.assertNotIn("vault's own README", readme)
        # the generated README is the directory's README
        self.assertIn("# 🗂️ Good Repos", readme)

    def test_root_notes_are_not_categories(self):
        # .md files directly in the vault root are not curated entries
        self.write("loose.md", simple_note("loose"))
        self.write("AI-Domain/Agents/aaa_Agents_x.md", simple_note("aaa"))
        result = self.publisher().publish()
        self.assertEqual(result.entries, 1)
        readme = self.readme(result)
        self.assertNotIn("loose", readme)


# ---------------------------------------------------------------------------
# 12. Star formatting + emoji map
# ---------------------------------------------------------------------------

class TestHelpers(unittest.TestCase):

    def test_format_stars(self):
        cases = {
            0: "0",
            999: "999",
            1234: "1,234",
            9_999: "9,999",
            10_000: "10k",
            14_023: "14k",
            42_987: "43k",
            86_750: "86.8k",
            140_232: "140.2k",
        }
        for value, expected in cases.items():
            self.assertEqual(goodrepos.format_stars(value), expected, value)
        self.assertEqual(goodrepos.format_stars(None), "")
        self.assertEqual(goodrepos.format_stars("14k"), "")

    def test_emoji_map_and_fallback(self):
        self.assertEqual(goodrepos.emoji_for("AI-Domain"), "🤖")
        self.assertEqual(goodrepos.emoji_for("LLM-Tools"), "🧰")
        self.assertEqual(goodrepos.emoji_for("Uncategorized"), "📦")
        self.assertEqual(goodrepos.emoji_for("No-Such-Category"), "📁")
        # deepest component wins for nested category paths
        self.assertEqual(goodrepos.emoji_for("AI-Domain", "Agents", "Frameworks"),
                         "🏗️")
        self.assertIn("AI-Domain", goodrepos.CATEGORY_EMOJI)

    def test_display_name(self):
        self.assertEqual(goodrepos.display_name("AI-Domain"), "AI Domain")
        self.assertEqual(goodrepos.display_name("Web-Frameworks"), "Web Frameworks")

    def test_result_dict_contract(self):
        result = goodrepos.PublishResult(published=True, entries=3, categories=2)
        self.assertTrue(result.ok)
        d = result.to_dict()
        for key in ("published", "skipped_reason", "error", "commit_sha",
                    "files_changed", "pushed", "repo_name", "repo_url",
                    "entries", "categories", "duration_ms", "ok"):
            self.assertIn(key, d)
        self.assertIn("3 repos across 2 categories", result.describe())
        skipped = goodrepos.PublishResult(skipped_reason="nothing to do")
        self.assertTrue(skipped.ok)
        self.assertEqual(skipped.describe(), "skipped — nothing to do")
        failed = goodrepos.PublishResult(error="boom")
        self.assertFalse(failed.ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
