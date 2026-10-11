#!/usr/bin/env python3
"""
test_importbatch.py — v0.29.0: faithful batch imports.

The owner's fidelity test (2026-10-01): "give the app a .txt or .md
file with one website address each, and see whether it does faithfully
process them all or not. … it must be robust and accurate and do not
miss anything."

The v0.28.0-and-earlier intake treated each raw line AS the URL, so a
realistic Markdown file lost most of its addresses:

  * Markdown links / bullets / trailing text  -> garbage URLs that
    always failed their fetch (junk _review notes + retry queue)
  * http://github.com/… and www.github.com/…  -> misrouted to the
    Websites pipeline where THE LAW bans github.com -> repo link LOST
  * a scheme-less twitter.com line             -> bypassed THE LAW's
    domain check entirely (netloc of '' is never blocked) -> banned
    note in the vault
  * "…/owner/repo — best repo ever"            -> repo parsed as
    "repo — best repo ever" -> bogus 404 + _missing note
  * a UTF-8 BOM glued itself onto the first URL and corrupted it

Covered here:
  1. links.parse_import_text — the one-address-per-line grammar
     (every decoration form, routing, dedupe, unparseable reporting)
  2. links.read_import_file — BOM / latin-1 / CRLF tolerance
  3. links.domain_of — scheme-tolerant (THE LAW cannot be bypassed by
     dropping the scheme)
  4. the pipeline gate on a scheme-less banned link (leak regression)
  5. ProcessingWorker._fetch_from_import — the batch intake on a
     realistic file (counts, manifest counters, unparsed log lines)
  6. the GitHub loop's owner/repo parse (corruption regression)

All against LOCAL temp dirs / stubs — no network, no GUI shown.
"""

import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gitcurator.core import links as L
from gitcurator.core import website_pipeline as wp


# ---------------------------------------------------------------------------
# 1. The parser — one address per line, whatever decoration it carries
# ---------------------------------------------------------------------------

class TestParseImportText(unittest.TestCase):

    def test_plain_addresses(self):
        r = L.parse_import_text(
            "https://example.com/one\n"
            "http://example.com/two\n"
            "example.com/three\n"
            "www.example.com/four\n")
        self.assertEqual(r['github_urls'], [])
        self.assertEqual(r['website_urls'], [
            'https://example.com/one',
            'http://example.com/two',
            'https://example.com/three',
            'https://www.example.com/four'])
        self.assertEqual(r['unparsed'], [])
        self.assertEqual(r['duplicates'], 0)
        self.assertEqual(r['raw_count'], 4)

    def test_markdown_bullets_and_trailing_text(self):
        r = L.parse_import_text(
            "- [Coolors](https://coolors.co)\n"
            "- https://palette.ninja\n"
            "- https://www.color-hex.com/color-palettes — community palettes\n"
            "[Adobe Color](https://color.adobe.com) — the official wheel\n"
            "* https://example.com\n")
        self.assertEqual(r['website_urls'], [
            'https://coolors.co',
            'https://palette.ninja',
            'https://www.color-hex.com/color-palettes',
            'https://color.adobe.com',
            'https://example.com'])
        self.assertEqual(r['unparsed'], [])

    def test_two_addresses_on_one_line_both_captured(self):
        r = L.parse_import_text(
            "see https://a.example.com and https://b.example.com — both")
        self.assertEqual(r['website_urls'],
                         ['https://a.example.com', 'https://b.example.com'])

    def test_github_variants_all_canonical(self):
        r = L.parse_import_text(
            "https://github.com/fastapi/fastapi\n"
            "github.com/psf/requests\n"                # bare
            "http://github.com/pydantic/pydantic\n"    # http
            "www.github.com/encode/httpx\n"            # www, bare
            "- [Typer](https://github.com/tiangolo/typer) — CLI builder\n"
            "https://github.com/encode/starlette extra path /blob/main/README.md\n"
            "https://github.com/o/r/issues/123\n")     # deep path
        self.assertEqual(r['github_urls'], [
            'https://github.com/fastapi/fastapi',
            'https://github.com/psf/requests',
            'https://github.com/pydantic/pydantic',
            'https://github.com/encode/httpx',
            'https://github.com/tiangolo/typer',
            'https://github.com/encode/starlette',
            'https://github.com/o/r'])
        self.assertEqual(r['website_urls'], [])

    def test_github_io_maps_to_repo_and_dedupes(self):
        r = L.parse_import_text(
            "https://tiangolo.github.io/typer/\n"
            "https://github.com/tiangolo/typer\n")
        # Both forms are the same repo: one canonical entry.
        self.assertEqual(r['github_urls'], ['https://github.com/tiangolo/typer'])
        self.assertEqual(r['duplicates'], 1)

    def test_bare_github_io_site_is_a_website(self):
        # A bare <owner>.github.io with no repo path is a real site (SPEC
        # §4.2) — routed to the Websites pipeline (where THE LAW's gate
        # refuses it; that is the documented v0.28.0 behavior).
        r = L.parse_import_text("https://tiangolo.github.io/\n")
        self.assertEqual(r['github_urls'], [])
        self.assertEqual(r['website_urls'], ['https://tiangolo.github.io/'])

    def test_duplicates_counted_across_both_kinds(self):
        # NOTE: https://coolors.co and https://www.coolors.co/ are ONE
        # identity under v0.64.0's canonical form (THE ONE SPELLING —
        # the root slash and the www both fold), so the pair no longer
        # demonstrates a duplicate; a DIFFERENT page of the same site
        # does. The parser stays consistent with the vault's own
        # dedupe model — it does not invent a second one.
        r = L.parse_import_text(
            "https://coolors.co\n"
            "- [Coolors](https://coolors.co)\n"        # same site
            "https://coolors.co/palettes\n"             # different page
            "https://github.com/o/r\n"
            "https://github.com/o/r\n")
        self.assertEqual(r['website_urls'],
                         ['https://coolors.co',
                          'https://coolors.co/palettes'])
        self.assertEqual(r['github_urls'], ['https://github.com/o/r'])
        self.assertEqual(r['duplicates'], 2)
        self.assertEqual(r['raw_count'], 5)

    def test_unparseable_lines_reported_not_dropped(self):
        r = L.parse_import_text(
            "https://example.com\n"
            "this line is not a url at all\n"
            "just-text\n"
            "- a bullet with no address\n")
        self.assertEqual(r['website_urls'], ['https://example.com'])
        self.assertEqual(r['unparsed'], [
            'this line is not a url at all',
            'just-text',
            '- a bullet with no address'])

    def test_comments_blanks_bom_heading_skipped(self):
        text = (
            "﻿# Batch import test\n"       # BOM + comment heading
            "\n"
            "## Color palette makers\n"     # markdown section heading
            "# https://commented-out.com\n" # a URL in a comment stays out
            "https://example.com\n")
        r = L.parse_import_text(text)
        self.assertEqual(r['website_urls'], ['https://example.com'])
        self.assertEqual(r['unparsed'], [])
        self.assertEqual(r['skipped_comment_lines'], 4)

    def test_crlf_and_trailing_whitespace(self):
        r = L.parse_import_text(
            "https://example.com/one\r\n"
            "   https://example.com/two  \r\n"
            "https://example.com/three.\r\n")   # sentence dot stripped
        self.assertEqual(r['website_urls'], [
            'https://example.com/one',
            'https://example.com/two',
            'https://example.com/three'])

    def test_ipv4_with_port_is_a_bare_address(self):
        r = L.parse_import_text("127.0.0.1:8901/site/palette\n")
        self.assertEqual(r['website_urls'], ['https://127.0.0.1:8901/site/palette'])
        self.assertEqual(r['unparsed'], [])

    def test_empty_and_comment_only_files(self):
        for text in ('', '\n\n', '# only comments\n# nothing else\n'):
            r = L.parse_import_text(text)
            self.assertEqual(r['github_urls'], [])
            self.assertEqual(r['website_urls'], [])
            self.assertEqual(r['unparsed'], [])
            self.assertEqual(r['raw_count'], 0)

    def test_law_domains_route_to_websites_where_the_gate_refuses(self):
        # The parser itself does NOT filter banned domains (the pipeline
        # gate is the single enforcement point) — but every link it
        # emits carries a scheme, so the gate can see the host.
        r = L.parse_import_text(
            "https://x.com/elixir_status/123\n"
            "twitter.com/PyBlog/status/456\n"      # bare — used to leak
            "https://huggingface.co/some-model\n"
            "https://www.instagram.com/p/ABC123\n"
            "https://www.linkedin.com/in/someone\n")
        self.assertEqual(r['github_urls'], [])
        self.assertEqual(r['website_urls'], [
            'https://x.com/elixir_status/123',
            'https://twitter.com/PyBlog/status/456',
            'https://huggingface.co/some-model',
            'https://www.instagram.com/p/ABC123',
            'https://www.linkedin.com/in/someone'])

    def test_order_preserved(self):
        r = L.parse_import_text(
            "https://b.example.com\n"
            "https://github.com/o/r\n"
            "https://a.example.com\n")
        self.assertEqual(r['website_urls'],
                         ['https://b.example.com', 'https://a.example.com'])
        self.assertEqual(r['github_urls'], ['https://github.com/o/r'])


# ---------------------------------------------------------------------------
# 2. The file reader — encodings never crash the batch
# ---------------------------------------------------------------------------

class TestReadImportFile(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='gc-import-rd-')
        self.path = os.path.join(self.tmp, 'links.md')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_utf8_bom_stripped(self):
        with open(self.path, 'wb') as f:
            f.write(b'\xef\xbb\xbfhttps://example.com\n')
        text = L.read_import_file(self.path)
        self.assertFalse(text.startswith('\ufeff'))
        r = L.parse_import_text(text)
        self.assertEqual(r['website_urls'], ['https://example.com'])

    def test_latin1_fallback(self):
        with open(self.path, 'wb') as f:
            f.write('https://example.com/caf\xe9\r\n'.encode('latin-1'))
        text = L.read_import_file(self.path)     # never raises
        r = L.parse_import_text(text)
        self.assertEqual(len(r['website_urls']), 1)
        self.assertTrue(r['website_urls'][0].startswith(
            'https://example.com/caf'))

    def test_crlf_file(self):
        with open(self.path, 'wb') as f:
            f.write(b'# heading\r\nhttps://example.com\r\n')
        r = L.parse_import_text(L.read_import_file(self.path))
        self.assertEqual(r['website_urls'], ['https://example.com'])


# ---------------------------------------------------------------------------
# 3. THE LAW cannot be bypassed by dropping the scheme
# ---------------------------------------------------------------------------

class TestSchemeTolerantDomainLaw(unittest.TestCase):

    def test_domain_of_scheme_less(self):
        self.assertEqual(L.domain_of('twitter.com/PyBlog/status/456'),
                         'twitter.com')
        # netloc is returned as-is (www kept) — the blocked-domain check
        # is the one that resolves subdomains.
        self.assertEqual(L.domain_of('www.github.com/o/r'),
                         'www.github.com')
        self.assertEqual(L.domain_of('x.com'), 'x.com')
        self.assertEqual(L.domain_of(''), '')
        self.assertEqual(L.domain_of('not a url at all'),
                         'not a url at all')  # a host-shaped blob — still
        # …and that blob is NOT on any ban list:
        self.assertFalse(L.domain_is_blocked(
            'not a url at all', list(L.LAW_BLOCKED_DOMAINS)))

    def test_domain_is_blocked_scheme_less_law_domains(self):
        for url in ('x.com/i/status/1', 'twitter.com/a/b', 't.co/xyz',
                    'huggingface.co/m', 'hf.co/m', 'instagram.com/p/x',
                    'facebook.com/x', 'linkedin.com/in/x',
                    'gist.github.com/u/1', 'www.github.com/o/r'):
            self.assertTrue(
                L.domain_is_blocked(url, list(L.LAW_BLOCKED_DOMAINS)),
                f'{url} must be blocked even without a scheme')

    def test_pipeline_gate_refuses_scheme_less_banned_link(self):
        """THE exact leak regression: a bare ``twitter.com/…`` import line
        (pre-v0.29.0) fetched, failed and left a banned placeholder note
        in the vault. The gate must refuse it before any fetch."""
        tmp = tempfile.mkdtemp(prefix='gc-import-law-')
        try:
            vault = os.path.join(tmp, 'websites')
            os.makedirs(os.path.join(vault, '_review'), exist_ok=True)
            db = wp.WebsiteStateDB(db_path=os.path.join(tmp, 'cache.db'))
            try:
                def _never_fetch(url, **kw):
                    raise AssertionError(
                        f'banned link must never be fetched: {url}')

                pipe = wp.WebsitePipeline(
                    config={'website_vault_path': vault,
                            'web_domain_delay_s': 0},
                    llm_call=lambda *a, **k: '{}',
                    vault_index_has=lambda u: False, state=db,
                    fetch_fn=_never_fetch, log=lambda *a, **k: None)
                result = pipe.process_link('twitter.com/PyBlog/status/456')
                self.assertEqual(result['outcome'], 'skipped')
                self.assertIn('blocked domain', result['error'])
                # …and nothing was written into the vault
                self.assertEqual(
                    os.listdir(os.path.join(vault, '_review')), [])
            finally:
                db.close()
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 4. The batch intake — ProcessingWorker._fetch_from_import
# ---------------------------------------------------------------------------

class TestWorkerImportIntake(unittest.TestCase):
    """The realistic one-address-per-line Markdown file from the owner's
    test, end to end through the intake (no network, no LLM — only the
    parsing/routing layer is exercised)."""

    REALISTIC_MD = (
        "\ufeff# Batch import test — color tools, dev tools, misc\n"
        "\n"
        "## Color palette makers\n"
        "- [Coolors](https://coolors.co)\n"
        "- https://palette.ninja\n"
        "- https://www.color-hex.com/color-palettes — community palettes\n"
        "- [Adobe Color](https://color.adobe.com) — the official color wheel\n"
        "http://colorkit.co/palette-generator/\n"
        "coolors.co/palettes/af6f9f5\n"
        "www.paletton.com\n"
        "- [Realtime Colors](https://realtimecolors.com) — real layouts\n"
        "https://huemint.com/generator/\n"
        "\n"
        "## Dev tools\n"
        "https://github.com/fastapi/fastapi\n"
        "github.com/psf/requests\n"
        "http://github.com/pydantic/pydantic\n"
        "www.github.com/encode/httpx\n"
        "- [Typer](https://github.com/tiangolo/typer) — CLI builder\n"
        "https://tiangolo.github.io/typer/\n"
        "https://github.com/encode/starlette extra path /blob/main/README.md\n"
        "\n"
        "## Reads & references\n"
        "https://example.com/some-article\n"
        "- https://developer.mozilla.org/en-US/docs/Web/CSS\n"
        "https://en.wikipedia.org/wiki/Color_theory\n"
        "[DuckDuckGo](https://duckduckgo.com)\n"
        "\n"
        "## Banned by THE LAW (must be refused, never noted)\n"
        "https://x.com/elixir_status/123\n"
        "twitter.com/PyBlog/status/456\n"
        "https://huggingface.co/some-model\n"
        "https://www.instagram.com/p/ABC123\n"
        "\n"
        "## Duplicates (counted, not double-processed)\n"
        "https://coolors.co\n"
        "https://github.com/fastapi/fastapi\n"
        "\n"
        "## Broken lines (reported, never silently dropped)\n"
        "this line is not a url at all\n"
        "just-text\n")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='gc-import-w-')
        self.md = os.path.join(self.tmp, 'links.md')
        with open(self.md, 'w', encoding='utf-8') as f:
            f.write(self.REALISTIC_MD)

        from gitcurator.gui.app import ProcessingWorker
        self.ProcessingWorker = ProcessingWorker
        self.logs = []

        logs = self.logs

        class _Log:
            def emit(self, msg, lvl=None, **k):
                logs.append((lvl, str(msg)))

        log = _Log()
        w = ProcessingWorker.__new__(ProcessingWorker)
        w.log_message = log
        w._create_inbox_notes = lambda urls, source=None: None
        w.import_file = self.md
        self.worker = w

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _messages(self):
        return '\n'.join(m for _lvl, m in self.logs)

    def test_realistic_md_every_address_captured(self):
        github_urls = self.worker._fetch_from_import()
        # 6 canonical GitHub URLs — every variant form, nothing lost
        self.assertEqual(github_urls, [
            'https://github.com/fastapi/fastapi',
            'https://github.com/psf/requests',
            'https://github.com/pydantic/pydantic',
            'https://github.com/encode/httpx',
            'https://github.com/tiangolo/typer',   # markdown link + github.io
            'https://github.com/encode/starlette', # trailing text stripped
        ])
        websites = self.worker._non_github_urls
        # 17 websites — every one scheme-ful (fetchable + law-checkable)
        self.assertEqual(len(websites), 17)
        for u in websites:
            self.assertTrue(u.startswith(('http://', 'https://')),
                            f'{u!r} must carry a scheme')
        self.assertIn('https://coolors.co', websites)            # from [Coolors](…)
        self.assertIn('https://www.paletton.com', websites)      # bare www
        self.assertIn('https://coolors.co/palettes/af6f9f5', websites)
        self.assertIn('https://color.adobe.com', websites)
        self.assertIn('https://twitter.com/PyBlog/status/456', websites)
        # The BOM'd heading is a comment now, NOT a garbage URL
        self.assertNotIn('\ufeff', repr(websites))

    def test_realistic_md_accounting_reported(self):
        self.worker._fetch_from_import()
        msg = self._messages()
        # Duplicates: coolors (repeat), fastapi (repeat), typer
        # (the github.io line merges into the markdown-link repo)
        self.assertIn('3 duplicate', msg)
        self.assertEqual(getattr(self.worker, '_intake_duplicates'), 3)
        self.assertEqual(getattr(self.worker, '_raw_url_count'), 26)
        # The unparseable lines are listed by content
        self.assertIn('no recognizable address', msg)
        self.assertIn('this line is not a url at all', msg)
        self.assertIn('just-text', msg)
        # The headline count
        self.assertIn('6 GitHub URLs + 17 website URLs', msg)

    def test_missing_file_reports_error(self):
        self.worker.import_file = os.path.join(self.tmp, 'nope.md')
        self.assertEqual(self.worker._fetch_from_import(), [])
        self.assertIn('not found', self._messages())

    def test_empty_file_says_so(self):
        empty = os.path.join(self.tmp, 'empty.txt')
        with open(empty, 'w', encoding='utf-8') as f:
            f.write('# only a comment\n\n')
        self.worker.import_file = empty
        self.assertEqual(self.worker._fetch_from_import(), [])
        # (PyQt6 raises on getattr-with-default for unset attributes —
        # reach into the instance dict instead.)
        self.assertEqual(self.worker.__dict__.get('_non_github_urls', []), [])
        self.assertIn('no addresses', self._messages())

    def test_old_contract_still_holds(self):
        """The v0.23.0 test's expectations, verbatim: '#' comment rule,
        blank lines, plain split routing."""
        plain = os.path.join(self.tmp, 'plain.txt')
        with open(plain, 'w', encoding='utf-8') as f:
            f.write("# heading (the '# comment' rule still applies)\n"
                    "https://github.com/o/r\n"
                    "\n"
                    "https://example.com/site\n")
        self.worker.import_file = plain
        github_urls = self.worker._fetch_from_import()
        self.assertEqual(github_urls, ["https://github.com/o/r"])
        self.assertEqual(self.worker._non_github_urls,
                         ["https://example.com/site"])


# ---------------------------------------------------------------------------
# 5. The GitHub loop's owner/repo parse — corruption regression
# ---------------------------------------------------------------------------

class TestGithubLoopParse(unittest.TestCase):
    """v0.29.0: the loop canonicalizes through extract_github_urls before
    the owner/repo split, so decorated lines can no longer produce a
    repo named "repo — best repo ever" (a bogus 404 + _missing note)."""

    def _loop_parse(self, raw_url):
        """The exact new code shape from the loop (clean_url →
        canonicalize → parts split)."""
        url = L.clean_url(raw_url)
        canon = L.extract_github_urls(url)
        if canon:
            url = canon[0]
        if not url.startswith("https://github.com/"):
            return None
        parts = url.replace("https://github.com/", "").split("/")
        if len(parts) < 2:
            return None
        return parts[0], parts[1]

    def test_decorated_line_parses_clean_owner_repo(self):
        self.assertEqual(
            self._loop_parse(
                'https://github.com/encode/starlette extra path '
                '/blob/main/README.md'),
            ('encode', 'starlette'))

    def test_http_and_www_variants_canonicalized(self):
        self.assertEqual(self._loop_parse('http://github.com/o/r'), ('o', 'r'))
        self.assertEqual(self._loop_parse('www.github.com/o/r'), ('o', 'r'))
        self.assertEqual(self._loop_parse('github.com/o/r'), ('o', 'r'))

    def test_plain_and_deep_paths_unchanged(self):
        self.assertEqual(
            self._loop_parse('https://github.com/o/r'), ('o', 'r'))
        self.assertEqual(
            self._loop_parse('https://github.com/o/r/issues/123'), ('o', 'r'))

    def test_non_github_still_skipped(self):
        self.assertIsNone(self._loop_parse('https://example.com'))
        self.assertIsNone(self._loop_parse('not a url at all'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
