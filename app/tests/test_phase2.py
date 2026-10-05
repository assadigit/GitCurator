#!/usr/bin/env python3
"""
test_phase2.py — Phase 2 (v0.11.0): the Websites pipeline.

Covers (SPEC §6 Phase 2 + acceptance):
  * taxonomy parser against the REAL file (copied to tests/fixtures/)
  * link routing: github.io mapping, gist detection, website URL
    canonicalization (normalize_url for GitHub untouched)
  * web_fetch on a LOCAL http.server: full / redirect / 404 / timeout /
    size cap / PDF / non-UTF-8 / per-domain rate limiting
  * web_extract on the saved HTML fixtures: article, marketing landing,
    JS-only shell, paywall stub, windows-1252 bytes, huge page
  * prompt loader: refuses unfilled {{SLOT}}s and slot-marker smuggling
  * WebsiteStateDB: processed rows, retry queue with backoff + cap,
    dismissed list, resolve_retry
  * WebsitePipeline with fake fetch + fake LLM: happy path note format
    (SPEC §4.5), dedupe, _review behavior, retry/upgrade, classification
    validation + corrective retries, sanitization of hostile model output,
    dry-run writes nothing, Stop responsiveness
  * the real ProcessingWorker with websites ON (GUI/CLI hook proof):
    websites-off still writes _inbox; github-off + websites-on processes
    websites only; both-off early return
  * the golden set file only contains valid taxonomy names; the offline
    golden runner produces a report with 0 invalid answers

Headless-safe: QT_QPA_PLATFORM=offscreen, no GUI is ever shown.
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
import gitcurator.gui.processing_worker as gui_pw  # refactor/gui-app-split: ProcessingWorker consumes Github from here
from gitcurator.core import dryrun
from gitcurator.core import prompts as _prompts
from gitcurator.core import web_extract as _web_extract
from gitcurator.core import web_fetch as _web_fetch
from gitcurator.core import website_pipeline as wp
from gitcurator.core import note_state
from gitcurator.core.links import (
    is_gist_url, map_github_io_url, normalize_url, normalize_website_url,
    split_links,
)
from gitcurator.core.taxonomy import (
    TaxonomyError, load_taxonomy, parse_taxonomy,
)

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, 'fixtures')
WEB_FIXTURES = os.path.join(FIXTURES, 'web')
TAXONOMY_FIXTURE = os.path.join(FIXTURES, 'website-library-categories.md')


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    return path


# ---------------------------------------------------------------------------
# Local test HTTP server (one per class that needs it)
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/slow':
            time.sleep(8)
            self.send_response(200)
            self.end_headers()
            return
        if path == '/redirect':
            self.send_response(301)
            self.send_header('Location', '/article')
            self.end_headers()
            return
        if path == '/redirect-loop':
            self.send_response(302)
            self.send_header('Location', '/redirect-loop')
            self.end_headers()
            return
        if path == '/pdf':
            body = b'%PDF-1.4 fake pdf bytes for the fixture'
            self.send_response(200)
            self.send_header('Content-Type', 'application/pdf')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == '/latin1':
            body = ('<html><head><title>Caf\u00e9 Dise\u00f1o</title></head>'
                    '<body><p>P\u00e1gina sobre dise\u00f1o gr\u00e1fico y '
                    'tipograf\u00eda editorial para el proyecto.</p>'
                    '</body></html>').encode('windows-1252')
            self.send_response(200)
            self.send_header('Content-Type',
                             'text/html; charset=windows-1252')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == '/huge':
            chunk = ('<p>' + 'word ' * 40 + '</p>\n') * 2000   # ~400 KB
            body = ('<html><head><title>Huge</title></head><body>'
                    + chunk + '</body></html>').encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(body)
            return
        fname = {'/article': 'article.html', '/landing': 'landing.html',
                 '/js': 'js_shell.html', '/paywall': 'paywall.html'}.get(path)
        if fname and os.path.exists(os.path.join(WEB_FIXTURES, fname)):
            with open(os.path.join(WEB_FIXTURES, fname), 'rb') as f:
                body = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *a):
        pass


def _start_server():
    srv = HTTPServer(('127.0.0.1', 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


# ---------------------------------------------------------------------------
# Taxonomy (against the REAL file, copied into fixtures/)
# ---------------------------------------------------------------------------

class TestTaxonomyRealFile(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tax = load_taxonomy(TAXONOMY_FIXTURE)

    def test_counts(self):
        self.assertEqual(len(self.tax.categories), 14)
        self.assertEqual(sum(len(c.subcategories)
                             for c in self.tax.categories), 16)

    def test_tricky_names_parse_exactly(self):
        for name in ("Design", "AI Tools for Web & App Development",
                     "AI Tools (General)", "Developer Tools",
                     "Free Utilities & Everyday Tools",
                     "Knowledge, Research & Reference", "Download Resources",
                     "Music & Audio Discovery", "Security & Privacy Tools",
                     "Crypto & Markets", "English Learning & Career Prep",
                     "Geography & Urban Planning", "Living Abroad",
                     "Random / Curiosities"):
            self.assertTrue(self.tax.is_category(name), name)

    def test_emoji_and_italic_notes_stripped(self):
        # "### Design 🌐 *(Assets & Resources and …)*" -> "Design"
        cat = self.tax.category("Design")
        self.assertIsNotNone(cat)
        self.assertEqual(cat.name, "Design")

    def test_subcategory_sets(self):
        self.assertEqual(self.tax.subcategories_of("Design"),
                         ["Assets & Resources", "UI/UX & Product Design",
                          "Print & Editorial Design", "Branding & Identity"])
        # "(empty for now)" note does NOT remove listed subcategories.
        self.assertEqual(self.tax.subcategories_of(
            "Geography & Urban Planning"),
            ["Urban Planning Courses & Learning", "Maps, GIS & Data Tools",
             "Case Studies & Reference"])
        self.assertEqual(self.tax.subcategories_of("Developer Tools"), [])
        self.assertEqual(self.tax.subcategories_of("Living Abroad"),
                         ["Australia"])
        self.assertEqual(self.tax.subcategories_of("Download Resources"),
                         ["Public Archives & Libraries",
                          "Torrent & Shadow Libraries"])

    def test_validation_is_exact_match(self):
        self.assertFalse(self.tax.is_category("design"))       # case matters
        self.assertFalse(self.tax.is_category("Not A Category"))
        # Whitespace around a model answer is tolerated (a trailing space
        # is not a real mismatch; anything else is rejected).
        self.assertTrue(self.tax.is_category(" Design "))
        self.assertFalse(self.tax.is_subcategory_of("Design", "Icons"))
        self.assertTrue(self.tax.is_subcategory_of(
            "Design", "UI/UX & Product Design"))

    def test_judgment_rules_extracted(self):
        rules = self.tax.judgment_rules
        for fragment in ("background removal", "Coursera", "Shadow libraries",
                         "AI Tools for Web & App Development"):
            self.assertIn(fragment, rules)

    def test_definitions_come_from_prose(self):
        self.assertIn("Country-specific relocation",
                      self.tax.category("Living Abroad").definition)
        self.assertIn("learning platforms",
                      self.tax.category("Knowledge, Research & Reference")
                      .definition)
        # The italic heading notes are NOT definitions.
        self.assertNotIn("public-directory candidates",
                         self.tax.category("Design").definition)

    def test_tag_hints(self):
        hints = self.tax.category("Design").tag_hints
        self.assertIn("icons", hints)
        self.assertIn("ai-powered", hints)
        self.assertEqual(self.tax.tag_hints_of("Nope"), "")

    def test_prompt_payloads(self):
        one = self.tax.category_one_liner()
        # Design has no prose definition -> name only.
        self.assertIn("- Design\n", one)
        # Knowledge's definition survives its leading (no subcategories)
        # marker, with the stray emphasis characters cleaned.
        self.assertIn("- Knowledge, Research & Reference — also the home",
                      one)
        self.assertNotIn('**', one)
        sub = self.tax.subcategory_one_liner("Developer Tools")
        self.assertIn("no subcategories", sub)
        sub2 = self.tax.subcategory_one_liner("Design")
        self.assertIn("- Assets & Resources — ", sub2)

    def test_folder_relpaths_are_safe_filenames(self):
        self.assertEqual(self.tax.folder_relpath("Design", "Assets & Resources"),
                         "Design" + os.sep + "Assets_Resources")
        self.assertEqual(self.tax.folder_relpath("AI Tools (General)"),
                         "AI_Tools_General_")
        with self.assertRaises(TaxonomyError):
            self.tax.folder_relpath("Nope")
        with self.assertRaises(TaxonomyError):
            self.tax.folder_relpath("Design", "Not A Subcategory")

    def test_unparseable_file_raises(self):
        with self.assertRaises(TaxonomyError):
            parse_taxonomy("# just a title\nno categories here")
        with self.assertRaises(TaxonomyError):
            parse_taxonomy("")

    def test_ignored_sections(self):
        # "How this works" / "Not yet covered" must not appear as rules.
        self.assertNotIn("mirrored roots", self.tax.judgment_rules)


# ---------------------------------------------------------------------------
# Link routing
# ---------------------------------------------------------------------------

class TestLinkRouting(unittest.TestCase):

    def test_github_io_maps_to_repo(self):
        self.assertEqual(map_github_io_url(
            "https://owner.github.io/repo/whatever"),
            "https://github.com/owner/repo")
        self.assertEqual(map_github_io_url(
            "http://owner.github.io/repo"), "https://github.com/owner/repo")

    def test_bare_github_io_is_a_website(self):
        self.assertEqual(map_github_io_url("https://owner.github.io/"), "")
        self.assertEqual(map_github_io_url("https://owner.github.io"), "")

    def test_gist_detection(self):
        self.assertTrue(is_gist_url("https://gist.github.com/o/abc123"))
        self.assertFalse(is_gist_url("https://github.com/o/repo"))
        self.assertFalse(is_gist_url("https://example.com"))

    def test_split_links_routing(self):
        text = ("check https://github.com/o/repo and "
                "https://owner.github.io/site plus "
                "https://gist.github.com/o/abc and https://example.com/page "
                "and https://github.com/o/repo again")
        gh, non_gh, raw = split_links(text)
        self.assertEqual(gh, ["https://github.com/o/repo",
                              "https://github.com/owner/site"])
        self.assertEqual(non_gh, ["https://gist.github.com/o/abc",
                                  "https://example.com/page"])
        self.assertEqual(raw, 5)

    def test_normalize_website_url(self):
        self.assertEqual(
            normalize_website_url("http://WWW.Example.com/path/?utm_source=x"),
            "https://example.com/path")
        self.assertEqual(
            normalize_website_url(
                "https://www.youtube.com/watch?v=abc&fbclid=xyz#t=1"),
            "https://youtube.com/watch?v=abc")
        self.assertEqual(
            normalize_website_url("https://example.com/a/"),
            "https://example.com/a")
        self.assertEqual(normalize_website_url("https://example.com"),
                         "https://example.com")

    def test_github_normalize_url_unchanged(self):
        # SPEC 4.3.1: normalize_url's GitHub behavior is frozen.
        self.assertEqual(normalize_url("https://github.com/O/R?tab=readme"),
                         "https://github.com/O/R")


# ---------------------------------------------------------------------------
# web_fetch (local server)
# ---------------------------------------------------------------------------

class TestWebFetch(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.srv, cls.base = _start_server()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def _url(self, path):
        return self.base + path

    def test_full_fetch(self):
        r = _web_fetch.fetch_url(self._url('/article'), timeout_s=10)
        self.assertEqual(r.status, 'full')
        self.assertTrue(r.ok)
        self.assertEqual(r.http_status, 200)
        self.assertIn('Practical Typography Guide', r.text)

    def test_follows_redirect(self):
        r = _web_fetch.fetch_url(self._url('/redirect'), timeout_s=10)
        self.assertEqual(r.status, 'full')
        self.assertNotEqual(r.url, r.final_url)
        self.assertIn('/article', r.final_url)

    def test_404_is_failed_not_exception(self):
        r = _web_fetch.fetch_url(self._url('/nope'), timeout_s=10)
        self.assertEqual(r.status, 'failed')
        self.assertFalse(r.ok)
        self.assertIn('404', r.reason)

    def test_timeout_is_failed(self):
        t0 = time.monotonic()
        r = _web_fetch.fetch_url(self._url('/slow'), timeout_s=1)
        elapsed = time.monotonic() - t0
        self.assertEqual(r.status, 'failed')
        self.assertEqual(r.reason, 'timeout')
        self.assertLess(elapsed, 5)

    def test_size_cap_truncates_to_partial(self):
        r = _web_fetch.fetch_url(self._url('/huge'), timeout_s=20,
                                 max_bytes=50_000)
        self.assertEqual(r.status, 'partial')
        self.assertIn('size cap', r.reason)
        self.assertLessEqual(len(r.body), 50_000)

    def test_pdf_is_partial(self):
        r = _web_fetch.fetch_url(self._url('/pdf'), timeout_s=10)
        self.assertEqual(r.status, 'partial')
        self.assertEqual(r.reason, 'pdf')
        self.assertEqual(r.body, b'')

    def test_non_utf8_charset_decodes(self):
        r = _web_fetch.fetch_url(self._url('/latin1'), timeout_s=10)
        self.assertEqual(r.status, 'full')
        self.assertIn('Diseño', r.text)

    def test_redirect_loop_is_failed(self):
        r = _web_fetch.fetch_url(self._url('/redirect-loop'), timeout_s=10)
        self.assertEqual(r.status, 'failed')

    def test_bad_scheme_is_failed(self):
        r = _web_fetch.fetch_url('ftp://example.com/x', timeout_s=5)
        self.assertEqual(r.status, 'failed')

    def test_rate_limiter_enforces_delay(self):
        limiter = _web_fetch.DomainRateLimiter(delay_s=0.4)
        limiter.wait('example.com')
        t0 = time.monotonic()
        limiter.wait('example.com')      # second hit must wait
        self.assertGreaterEqual(time.monotonic() - t0, 0.35)
        t0 = time.monotonic()
        limiter.wait('other.com')        # different domain: no wait
        self.assertLess(time.monotonic() - t0, 0.2)


# ---------------------------------------------------------------------------
# web_extract (saved fixtures)
# ---------------------------------------------------------------------------

class TestWebExtract(unittest.TestCase):

    @staticmethod
    def _page(name):
        with open(os.path.join(WEB_FIXTURES, name), 'rb') as f:
            return _web_extract.extract_from_bytes(f.read())

    def test_article(self):
        p = self._page('article.html')
        self.assertEqual(p.title, 'The Practical Typography Guide')
        self.assertIn('typography', p.meta_description)
        self.assertIn('twelve-column grid', p.text)
        self.assertFalse(p.is_js_shell)
        self.assertFalse(p.is_paywall)
        self.assertTrue(p.has_content)

    def test_landing_page(self):
        p = self._page('landing.html')
        self.assertEqual(p.title, 'PaletteForge — color palettes that ship')
        self.assertIn('40,000', p.meta_description)
        self.assertIn('WCAG-checked palettes', p.text)
        self.assertFalse(p.is_js_shell)

    def test_js_shell(self):
        p = self._page('js_shell.html')
        self.assertTrue(p.is_js_shell)
        # Title + meta description exist -> there IS content to classify
        # from (the model gets title/description only; fetch_status will
        # be 'partial').
        self.assertTrue(p.has_content)

    def test_paywall(self):
        p = self._page('paywall.html')
        self.assertTrue(p.is_paywall)
        self.assertIn('supply-chain', p.title.lower())
        self.assertTrue(p.has_content)   # real title + description exist

    def test_latin1_bytes(self):
        body = open(os.path.join(WEB_FIXTURES, 'latin1.html'), 'rb').read()
        p = _web_extract.extract_from_bytes(body, header_charset='')
        self.assertIn('Diseño', p.title)
        self.assertIn('tipográfica', p.text)

    def test_empty_html(self):
        p = _web_extract.extract('')
        self.assertTrue(p.is_js_shell)
        self.assertFalse(p.has_content)

    def test_huge_text_is_capped(self):
        huge = ('<html><body>' + '<p>word ' * 200_000 + '</p></body></html>')
        p = _web_extract.extract(huge)
        self.assertLessEqual(len(p.text), _web_extract.MAX_TEXT_CHARS)


# ---------------------------------------------------------------------------
# Prompt loader
# ---------------------------------------------------------------------------

class TestPrompts(unittest.TestCase):

    def test_refuses_unfilled_slot(self):
        with self.assertRaises(_prompts.PromptError):
            _prompts.fill_template('Hello {{NAME}} and {{OTHER}}', NAME='x')

    def test_refuses_empty_value(self):
        with self.assertRaises(_prompts.PromptError):
            _prompts.fill_template('{{A}}', A='')

    def test_refuses_slot_marker_smuggling(self):
        with self.assertRaises(_prompts.PromptError):
            _prompts.fill_template('{{A}}', A='{{B}}')

    def test_prompt_files_fill_completely(self):
        for stem, slots in (
                ('w01_category',
                 dict(CATEGORY_NAMES_WITH_ONE_LINE_DEFINITIONS='- A\n- B',
                      JUDGMENT_RULES='rules', PAST_CORRECTIONS='(none)',
                      URL='https://x', TITLE='T',
                      META_DESCRIPTION='D', TEXT_EXCERPT='E')),
                ('w02_subcategory',
                 dict(CATEGORY='A', SUBCATEGORIES_WITH_DEFINITIONS='- s1',
                      URL='https://x', TITLE='T', META_DESCRIPTION='D',
                      TEXT_EXCERPT='E')),
                ('w03_analyze',
                 dict(TAG_HINTS='#a #b', URL='https://x', TITLE='T',
                      META_DESCRIPTION='D', TEXT_EXCERPT='E'))):
            out = _prompts.load_prompt(stem, **slots)
            self.assertNotIn('{{', out)
            self.assertIn('https://x', out)

    def test_missing_file(self):
        with self.assertRaises(_prompts.PromptError):
            _prompts.load_prompt_file('nope.txt', A='a')

    def test_bad_filename_rejected(self):
        with self.assertRaises(_prompts.PromptError):
            _prompts.load_prompt_file('../secrets.txt', A='a')


# ---------------------------------------------------------------------------
# WebsiteStateDB
# ---------------------------------------------------------------------------

class TestWebsiteStateDB(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='p2state-')
        self.db = wp.WebsiteStateDB(
            db_path=os.path.join(self.tmp, 'cache.db'))

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_processed_roundtrip(self):
        self.assertFalse(self.db.is_processed('https://x.com'))
        self.db.mark_processed('https://x.com', '/n.md', 'Design',
                               'Assets & Resources', 'full')
        row = self.db.processed_row('https://x.com')
        self.assertEqual(row['category'], 'Design')
        self.assertEqual(row['fetch_status'], 'full')

    def test_retry_queue_counts_and_backoff(self):
        self.db.enqueue_retry('https://x.com', 'HTTP 404')
        first = self.db.retry_row('https://x.com')
        self.assertEqual(first['attempts'], 1)
        time.sleep(0.01)
        self.db.enqueue_retry('https://x.com', 'HTTP 404 again')
        second = self.db.retry_row('https://x.com')
        self.assertEqual(second['attempts'], 2)
        # first_failed_at survives the refresh
        self.assertEqual(first['first_failed_at'], second['first_failed_at'])

    def test_due_retries_respect_cap_and_time(self):
        from datetime import datetime, timedelta
        self.db.enqueue_retry('https://a.com', 'e')
        self.db.enqueue_retry('https://a.com', 'e')
        self.db.enqueue_retry('https://a.com', 'e')    # attempts = 3 = cap
        self.assertEqual(self.db.due_retries(), [])    # capped: never due
        # A fresh row whose backoff is in the future.
        self.db.enqueue_retry('https://b.com', 'e')
        self.assertEqual(self.db.due_retries(), [])    # next_attempt in days
        # Force one due by rewinding its clock.
        with self.db._lock:
            self.db.conn.execute(
                "UPDATE website_retry_queue SET next_attempt_at=? "
                "WHERE url='https://b.com'",
                ((datetime.now() - timedelta(days=1)).isoformat(
                    timespec='seconds'),))
            self.db.conn.commit()
        self.assertEqual(self.db.due_retries(), ['https://b.com'])

    def test_resolve_retry(self):
        self.db.enqueue_retry('https://x.com', 'e')
        self.db.resolve_retry('https://x.com')
        self.assertIsNone(self.db.retry_row('https://x.com'))

    def test_dismissed(self):
        self.assertFalse(self.db.is_dismissed('https://x.com'))
        self.db.dismiss('https://x.com')
        self.assertTrue(self.db.is_dismissed('https://x.com'))


# ---------------------------------------------------------------------------
# WebsitePipeline (fake fetch + fake LLM)
# ---------------------------------------------------------------------------

class _FakeFetch:
    """Canned fetch results per path."""

    def __init__(self, pages=None, fail_paths=()):
        self.pages = pages or {}
        self.fail_paths = set(fail_paths)
        self.calls = []

    def __call__(self, url, **kwargs):
        from urllib.parse import urlparse
        self.calls.append(url)
        path = urlparse(url).path or '/'
        if path in self.fail_paths:
            return _web_fetch.FetchResult(url=url, status='failed',
                                          reason='HTTP 404')
        html = self.pages.get(path)
        if html is None:
            html = ("<html><head><title>Test Site</title>"
                    "<meta name=\"description\" content=\"A test page."
                    "\"></head><body><p>Body text about design tools and "
                    "resources for building websites and applications, "
                    "long enough to classify confidently.</p></body></html>")
        return _web_fetch.FetchResult(
            url=url, final_url=url, status='full', http_status=200,
            content_type='text/html', charset='utf-8',
            body=html.encode('utf-8'), text=html)


class _FakeLLM:
    """Scriptable LLM: pops answers per prompt type; every answer can be
    overridden to test validation and retries."""

    def __init__(self, category='Design', category_conf='high',
                 subcategory='Assets & Resources', subcategory_conf='medium',
                 bad_category_answers=0, analysis=None):
        self.category = category
        self.category_conf = category_conf
        self.subcategory = subcategory
        self.subcategory_conf = subcategory_conf
        self.bad_left = bad_category_answers
        self.analysis = analysis or {
            'name': 'Test Site', 'one_line': 'A test page about design.',
            'core_offerings': ['One', 'Two', 'Three'],
            'standout_feature': 'It works offline',
            'best_used_for': 'Use when you need to test the pipeline.',
            'pricing': 'free', 'login_required': 'no',
            'similar_tools': [], 'tags': ['design', 'testing'],
            'confidence': 'high'}
        self.calls = []

    def __call__(self, messages, task=None):
        text = messages[0]['content']
        self.calls.append(text)
        self.tasks = getattr(self, 'tasks', [])
        self.tasks.append(task)
        if 'filing a website into a personal library' in text:
            if self.bad_left > 0:
                self.bad_left -= 1
                return json.dumps({'category': 'Not A Real Category',
                                   'confidence': 'high', 'reason': 'x'})
            return json.dumps({'category': self.category,
                               'confidence': self.category_conf,
                               'reason': 'testing'})
        if 'was filed under' in text:
            return json.dumps({'subcategory': self.subcategory,
                               'confidence': self.subcategory_conf})
        return json.dumps(self.analysis)


class _PipeCase(unittest.TestCase):
    """Shared plumbing: temp vault + state db + pipeline factory."""

    def setUp(self):
        dryrun.disable()
        dryrun.clear()
        self.tmp = tempfile.mkdtemp(prefix='p2pipe-')
        self.vault = os.path.join(self.tmp, 'websites')
        self.db = wp.WebsiteStateDB(
            db_path=os.path.join(self.tmp, 'cache.db'))
        self.in_vault = set()
        self.logs = []

    def tearDown(self):
        dryrun.disable()
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_pipeline(self, llm, fetch=None, config=None):
        cfg = {'website_vault_path': self.vault,
               'web_domain_delay_s': 0}
        cfg.update(config or {})
        return wp.WebsitePipeline(
            config=cfg, llm_call=llm,
            vault_index_has=lambda u: u in self.in_vault,
            state=self.db, fetch_fn=fetch or _FakeFetch(),
            log=lambda m, l='info': self.logs.append((l, m)))


class TestPipeline(_PipeCase):

    def test_happy_path_note_format(self):
        pipe = self.make_pipeline(_FakeLLM())
        r = pipe.process_link('https://example.com/tool?utm_source=x')
        self.assertEqual(r['outcome'], 'processed')
        self.assertEqual(r['category'], 'Design')
        self.assertEqual(r['subcategory'], 'Assets & Resources')
        self.assertEqual(r['canonical'], 'https://example.com/tool')
        # Folder: <vault>/<Category>/<Subcategory>/<name>.md
        rel = os.path.relpath(r['note_path'], self.vault)
        self.assertEqual(rel, os.path.join('Design', 'Assets_Resources',
                                           'Test_Site.md'))
        note = open(r['note_path'], encoding='utf-8').read()
        for key in ('source:', 'category:', 'subcategory:', 'fetch_status:',
                    'pricing:', 'login_required:', 'date_processed:',
                    'managed_by:', 'schema_version:', 'prompt_version:',
                    'aliases:', 'tags:'):
            self.assertIn(key, note, key)
        self.assertIn('fetch_status: "full"', note)
        self.assertIn(wp.OWNERSHIP_BANNER, note)
        self.assertIn('Use when you need to test the pipeline.', note)
        self.assertIn('design', note)
        # The state DB recorded it (dedupe layer).
        self.assertTrue(self.db.is_processed('https://example.com/tool'))

    def test_no_subcategory_lands_in_category_folder(self):
        pipe = self.make_pipeline(_FakeLLM(subcategory='none'))
        r = pipe.process_link('https://example.com/x')
        rel = os.path.relpath(r['note_path'], self.vault)
        self.assertEqual(rel, os.path.join('Design', 'Test_Site.md'))

    def test_dedupe_canonical_forms(self):
        pipe = self.make_pipeline(_FakeLLM())
        r1 = pipe.process_link('https://example.com/page?utm_campaign=a')
        self.assertEqual(r1['outcome'], 'processed')
        self.in_vault.add(r1['canonical'])
        r2 = pipe.process_link('http://www.example.com/page/?fbclid=zz')
        self.assertEqual(r2['outcome'], 'skipped')
        self.assertIn('already', r2['error'])
        # And nothing new was written.
        self.assertEqual(len(os.listdir(os.path.join(self.vault, 'Design'))),
                         1)

    def test_dismissed_never_reprocessed(self):
        self.db.dismiss('https://example.com/gone',
                        'owner deleted the note')
        pipe = self.make_pipeline(_FakeLLM())
        r = pipe.process_link('https://example.com/gone')
        self.assertEqual(r['outcome'], 'skipped')
        self.assertIn('dismissed', r['error'])
        self.assertFalse(os.path.exists(self.vault))

    # -- v0.37.0: the per-link on_progress hook (the frozen-bar fix) ------

    def test_run_emits_on_progress_per_link(self):
        pipe = self.make_pipeline(_FakeLLM())
        seen = []
        results = pipe.run(
            ['https://example.com/one', 'https://example.com/one?utm_source=x',
             'https://example.com/two'],
            on_progress=seen.append)
        # one callback per link handed over (the within-batch duplicate —
        # same canonical once utm drops — counts too: the caller's position
        # advances for it, like the GitHub loop)
        self.assertEqual(
            seen, ['https://example.com/one',
                   'https://example.com/one?utm_source=x',
                   'https://example.com/two'])
        self.assertEqual(len(results), 3)
        self.assertEqual(sum(1 for r in results
                             if r['outcome'] == 'processed'), 2)
        self.assertEqual(sum(1 for r in results
                             if r['error'] == 'duplicate within batch'), 1)

    def test_run_on_progress_exceptions_swallowed(self):
        pipe = self.make_pipeline(_FakeLLM())

        def boom(url):
            raise RuntimeError("callback crashed")

        results = pipe.run(['https://example.com/ok'],
                           on_progress=boom)
        self.assertEqual(results[0]['outcome'], 'processed')

    def test_run_due_retries_emits_on_progress(self):
        from datetime import datetime, timedelta
        self.db.enqueue_retry('https://example.com/broken', 'HTTP 404')
        # rewind its clock so the retry is due NOW (same pattern as
        # test_due_retries_respect_cap_and_time)
        with self.db._lock:
            self.db.conn.execute(
                "UPDATE website_retry_queue SET next_attempt_at=? "
                "WHERE url='https://example.com/broken'",
                ((datetime.now() - timedelta(days=1)).isoformat(
                    timespec='seconds'),))
            self.db.conn.commit()
        pipe = self.make_pipeline(
            _FakeLLM(), fetch=_FakeFetch(fail_paths={'/broken'}))
        seen = []
        pipe.run_due_retries(on_progress=seen.append)
        self.assertEqual(seen, ['https://example.com/broken'])

    def test_run_without_on_progress_unchanged(self):
        pipe = self.make_pipeline(_FakeLLM())
        results = pipe.run(['https://example.com/plain'])
        self.assertEqual(results[0]['outcome'], 'processed')

    def test_fetch_failure_writes_review_and_queues_retry(self):
        pipe = self.make_pipeline(
            _FakeLLM(), fetch=_FakeFetch(fail_paths={'/broken'}))
        r = pipe.process_link('https://example.com/broken')
        self.assertEqual(r['outcome'], 'review')
        self.assertEqual(r['fetch_status'], 'failed')
        note = open(r['note_path'], encoding='utf-8').read()
        self.assertIn('fetch_status: "failed"', note)
        self.assertIn('_review', r['note_path'])
        retry = self.db.retry_row('https://example.com/broken')
        self.assertIsNotNone(retry)
        self.assertEqual(retry['attempts'], 1)
        # The _review note itself is in the vault (never silently dropped).
        self.in_vault.add(r['canonical'])

    def test_retry_exhaustion_keeps_review_note(self):
        # Simulate a link that already failed MAX times.
        url = 'https://example.com/broken'
        for _ in range(wp.MAX_FETCH_RETRIES):
            self.db.enqueue_retry(url, 'HTTP 404')
        self.in_vault.add(url)
        self.db.mark_processed(url, os.path.join(
            self.vault, '_review', 'x.md'), '', '', 'failed')
        pipe = self.make_pipeline(
            _FakeLLM(), fetch=_FakeFetch(fail_paths={'/broken'}))
        r = pipe.process_link(url)
        self.assertEqual(r['outcome'], 'skipped')
        self.assertIn('no more retries', r['error'])

    def test_failed_review_note_is_upgraded(self):
        url = 'https://example.com/revived'
        # A prior failed _review placeholder exists on disk, recorded in
        # note_state — exactly as a real earlier run would have left it.
        ns = note_state.NoteStateDB(db_path=os.path.join(self.tmp,
                                                         'notes.db'))
        old_path = os.path.join(self.vault, '_review', 'revived.md')
        _write(old_path, wp.build_review_note(url, 'failed', 'HTTP 404'))
        ns.record_note(note_state.VAULT_WEBSITES, url, old_path,
                       normalizer=normalize_website_url)
        self.db.enqueue_retry(url, 'HTTP 404')
        self.db.mark_processed(url, old_path, '', '', 'failed')
        self.in_vault.add(url)
        with self.db._lock:
            self.db.conn.execute(
                "UPDATE website_retry_queue SET next_attempt_at='2000-01-01"
                "T00:00:00' WHERE url=?", (url,))
            self.db.conn.commit()
        pipe = self.make_pipeline(_FakeLLM())
        pipe.note_state_db = ns
        try:
            r = pipe.process_link(url)
            self.assertEqual(r['outcome'], 'processed')
            self.assertEqual(pipe.counters['upgraded'], 1)
            # The retry queue entry is resolved.
            self.assertIsNone(self.db.retry_row(url))
            # The old app-owned placeholder was removed (no duplicate).
            self.assertFalse(os.path.exists(old_path))
        finally:
            ns.close()

    def test_hand_edited_placeholder_is_never_removed(self):
        url = 'https://example.com/kept'
        ns = note_state.NoteStateDB(db_path=os.path.join(self.tmp,
                                                         'notes2.db'))
        old_path = os.path.join(self.vault, '_review', 'kept.md')
        _write(old_path, wp.build_review_note(url, 'failed', 'HTTP 404'))
        ns.record_note(note_state.VAULT_WEBSITES, url, old_path,
                       normalizer=normalize_website_url)
        # The owner edits the placeholder by hand.
        _write(old_path, open(old_path, encoding='utf-8').read()
               + '\nMy own note about this one.\n')
        self.db.enqueue_retry(url, 'HTTP 404')
        self.db.mark_processed(url, old_path, '', '', 'failed')
        self.in_vault.add(url)
        with self.db._lock:
            self.db.conn.execute(
                "UPDATE website_retry_queue SET next_attempt_at='2000-01-01"
                "T00:00:00' WHERE url=?", (url,))
            self.db.conn.commit()
        pipe = self.make_pipeline(_FakeLLM())
        pipe.note_state_db = ns
        try:
            r = pipe.process_link(url)
            self.assertEqual(r['outcome'], 'processed')
            # The hand-edited placeholder SURVIVES (never deleted).
            self.assertTrue(os.path.exists(old_path))
            self.assertTrue(any('hand-edited' in m for _l, m in self.logs))
        finally:
            ns.close()

    def test_invalid_category_answer_retried_then_review(self):
        # All three answers invalid -> corrective retries -> _review.
        llm = _FakeLLM(bad_category_answers=5)
        pipe = self.make_pipeline(llm)
        r = pipe.process_link('https://example.com/hard')
        self.assertEqual(r['outcome'], 'review')
        self.assertIn('no valid category', r['error'])
        # 1 initial + 2 corrective = 3 classification calls.
        classify_calls = [c for c in llm.calls
                          if 'filing a website' in c]
        self.assertEqual(len(classify_calls), 1 + wp.CLASSIFY_RETRIES)

    def test_invalid_then_valid_category(self):
        llm = _FakeLLM(bad_category_answers=1)
        pipe = self.make_pipeline(llm)
        r = pipe.process_link('https://example.com/ok')
        self.assertEqual(r['outcome'], 'processed')
        self.assertEqual(r['category'], 'Design')

    def test_low_confidence_goes_to_review(self):
        pipe = self.make_pipeline(_FakeLLM(category_conf='low'))
        r = pipe.process_link('https://example.com/unsure')
        self.assertEqual(r['outcome'], 'review')
        self.assertIn('confidence was low', r['error'])
        self.assertIn('_review', r['note_path'])

    def test_low_confidence_subcategory_dropped(self):
        pipe = self.make_pipeline(_FakeLLM(subcategory_conf='low'))
        r = pipe.process_link('https://example.com/sub')
        self.assertEqual(r['outcome'], 'processed')
        self.assertEqual(r['subcategory'], '')
        rel = os.path.relpath(r['note_path'], self.vault)
        self.assertEqual(rel, os.path.join('Design', 'Test_Site.md'))

    def test_analysis_failure_writes_review(self):
        def bad_analysis(messages, task=None):
            text = messages[0]['content']
            if 'filing a website' in text:
                return json.dumps({'category': 'Design',
                                   'confidence': 'high', 'reason': 'x'})
            if 'was filed under' in text:
                return json.dumps({'subcategory': 'none',
                                   'confidence': 'high'})
            return 'this is not json at all'
        pipe = self.make_pipeline(bad_analysis)
        r = pipe.process_link('https://example.com/noanalysis')
        self.assertEqual(r['outcome'], 'review')
        self.assertIn('analysis failed', r['error'].lower())

    def test_llm_outage_still_writes_review_note(self):
        """A hard LLM outage (exception, not a bad answer) mid-classification
        must not silently drop the link (SPEC 4.3) — a _review note is
        written by the catch-all."""
        def exploding_llm(messages, task=None):
            raise RuntimeError("LLM connection refused")
        pipe = self.make_pipeline(exploding_llm)
        r = pipe.process_link('https://example.com/outage')
        self.assertEqual(r['outcome'], 'review')
        self.assertIn('LLM connection refused', r['error'])
        self.assertTrue(os.path.exists(r['note_path']))
        note = open(r['note_path'], encoding='utf-8').read()
        self.assertIn('fetch_status: "failed"', note)
        self.assertIn('Pipeline error: RuntimeError', note)
        self.assertIn('LLM connection refused', note)

    def test_gist_is_law_blocked_not_noted(self):
        """v0.28.0 — THE LAW settles the old SPEC §4.2 judgment call: the
        GitHub group (gists included) is banned from the Websites vault.
        A gist link is refused — never fetched, never noted (the _inbox
        row / D1 ledger row is the record). The old behavior (a note
        with the #snippet tag) is gone by owner decree."""
        pipe = self.make_pipeline(_FakeLLM())
        r = pipe.process_link('https://gist.github.com/o/abc123')
        self.assertEqual(r['outcome'], 'skipped')
        self.assertIn('blocked', r['error'])
        self.assertFalse(r.get('note_path'))

    def test_hostile_llm_output_is_sanitized(self):
        hostile = {
            'name': 'Evil"]\nfrontmatter: injected\n---',
            'one_line': 'x' * 500,
            'core_offerings': ['a"] # comment', 'b\nnewline'],
            'standout_feature': '"><script>alert(1)</script>',
            'best_used_for': 'Use when you need to test injection.',
            'pricing': 'FREE!! ; drop table',
            'login_required': 'maybe',
            'similar_tools': ['x]  # injected', 'ok-tool'],
            'tags': ['pwned, x]  # injected', 'ok-tag'],
            'confidence': 'high'}
        pipe = self.make_pipeline(_FakeLLM(analysis=hostile))
        r = pipe.process_link('https://example.com/evil?x=<script>')
        self.assertEqual(r['outcome'], 'processed')
        note = open(r['note_path'], encoding='utf-8').read()
        # A clean note has exactly two '---' separators inside the body
        # flow: the frontmatter close and the Source separator.
        self.assertEqual(note.count('\n---\n'), 2)
        self.assertIn('pricing: "unknown"', note)      # invalid -> unknown
        self.assertIn('login_required: "unknown"', note)
        # The security property: nothing hostile broke the FRONTMATTER.
        # The sanitizer reduces the tag to inert words ("pwned x injected"
        # is fine); what must NEVER appear: a '#' (YAML comment), a ']'
        # outside the tags flow-sequence, or a smuggled new key.
        first = note.index('---')
        second = note.index('---', first + 3)
        fm = note[first:second]
        self.assertNotIn('#', fm)
        self.assertNotIn('frontmatter:', fm)
        self.assertNotIn('alert(1)', fm)
        import re as _re
        m = _re.search(r'^tags: \[(.*)\]$', fm, _re.MULTILINE)
        self.assertIsNotNone(m)
        self.assertRegex(m.group(1), r'^[A-Za-z0-9_ ,\-]*$')
        # And the filename derived from the hostile name is safe.
        fname = os.path.basename(r['note_path'])
        self.assertRegex(fname, r'^[A-Za-z0-9\-_]+\.md$')

    def test_dry_run_writes_nothing(self):
        dryrun.enable()
        try:
            pipe = self.make_pipeline(_FakeLLM())
            r = pipe.process_link('https://example.com/dry')
            self.assertEqual(r['outcome'], 'processed')
            self.assertFalse(os.path.exists(self.vault))
            self.assertGreaterEqual(dryrun.entry_count(), 2)  # makedirs+write
        finally:
            dryrun.disable()

    def test_should_continue_stops_batch(self):
        pipe = self.make_pipeline(_FakeLLM())
        results = pipe.run(['https://a.com/1', 'https://b.com/2'],
                          should_continue=lambda: False)
        self.assertEqual(results, [])
        self.assertFalse(os.path.exists(self.vault))

    def test_batch_dedupes_within_run(self):
        pipe = self.make_pipeline(_FakeLLM())
        results = pipe.run(['https://example.com/x?utm=1',
                            'https://example.com/x?utm=2'])
        self.assertEqual(len(results), 2)
        self.assertEqual(results[1]['outcome'], 'skipped')


# ---------------------------------------------------------------------------
# The real ProcessingWorker hook (GUI/CLI proof)
# ---------------------------------------------------------------------------

class _Sig:
    def __init__(self, name, sink):
        self.name = name
        self.sink = sink

    def emit(self, *a):
        self.sink.append((self.name, a))

    def connect(self, *a):
        pass


class TestWorkerWebsites(unittest.TestCase):
    """Runs the REAL ProcessingWorker._run_impl — proving the thin hooks
    fire in a real batch (and stay dormant when the pipeline is OFF).

    Every stateful dependency is patched onto a temp dir: Github fails
    fast (this sandbox blocks api.github.com), CacheDB / NoteStateDB /
    WebsiteStateDB are redirected, and the cloud LLM is faked — the same
    harness pattern as test_phase1's worker test."""

    def setUp(self):
        dryrun.disable()
        dryrun.clear()

    def _run_worker(self, tmp, cfg, urls, non_github):
        """Shared runner: builds the worker, patches everything, runs
        _run_impl, returns (events, web_vault_path)."""
        events = []
        server = HTTPServer(('127.0.0.1', 0), _Handler)
        threading.Thread(target=server.serve_forever,
                         daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        non_github = [base + u if u.startswith('/') else u
                      for u in non_github]

        worker = gui_app.ProcessingWorker(
            config=cfg, mode='direct', urls=urls,
            headless=True, dry_run=False)
        worker._non_github_urls = list(non_github)
        worker.log_message = _Sig('log', events)
        worker.finished_signal = _Sig('finished', events)
        worker.progress_updated = _Sig('progress', events)
        worker.status_updated = _Sig('status', events)

        class _FastFailGithub:
            def __init__(self, *a, **k):
                pass

            def get_repo(self, *a, **k):
                raise RuntimeError("network blocked in test")

        db_path = os.path.join(tmp, 'cache.db')
        orig_github = gui_pw.Github
        orig_ns_init = note_state.NoteStateDB.__init__
        orig_ws_init = wp.WebsiteStateDB.__init__
        orig_cache_init = gui_app.CacheDB.__init__
        orig_cloud = gui_app.ProcessingWorker._call_cloud_llm

        def _redirect(orig):
            def patched(self, db_path_arg="cache.db"):
                orig(self, db_path=db_path if db_path_arg == "cache.db"
                     else db_path_arg)
            return patched

        fake = _FakeLLM()

        def _fake_cloud(api_url, api_key, model, messages,
                        json_mode=False, timeout_s=300, num_ctx=None,
                        on_warn=None, verify_tls=False):
            return fake(messages)

        gui_pw.Github = _FastFailGithub
        note_state.NoteStateDB.__init__ = _redirect(orig_ns_init)
        wp.WebsiteStateDB.__init__ = _redirect(orig_ws_init)
        gui_app.CacheDB.__init__ = _redirect(orig_cache_init)
        gui_app.ProcessingWorker._call_cloud_llm = staticmethod(_fake_cloud)
        try:
            worker._run_impl()
        finally:
            gui_pw.Github = orig_github
            note_state.NoteStateDB.__init__ = orig_ns_init
            wp.WebsiteStateDB.__init__ = orig_ws_init
            gui_app.CacheDB.__init__ = orig_cache_init
            gui_app.ProcessingWorker._call_cloud_llm = orig_cloud
            server.shutdown()
            server.server_close()
        return events, base

    @staticmethod
    def _base_cfg(tmp, **overrides):
        cfg = {'vault_path': os.path.join(tmp, 'ghvault'),
               'website_vault_path': os.path.join(tmp, 'webvault'),
               'llm_provider': 'cloud',
               'cloud_api_url': 'http://127.0.0.1:9/v1',
               'cloud_model': 'test', 'github_token': '',
               'timeout_per_repo': 5, 'max_retries': 1,
               'delay_between_api_calls': 0,
               'web_domain_delay_s': 0, 'web_fetch_timeout_s': 10,
               'pipelines': {'github': True, 'websites': True}}
        os.makedirs(cfg['vault_path'], exist_ok=True)
        cfg.update(overrides)
        return cfg

    def test_full_batch_with_websites_on(self):
        """GitHub link fails fast (patched) + one website link succeeds ->
        the websites phase writes the note and the batch still finishes."""
        tmp = tempfile.mkdtemp(prefix='p2worker2-')
        try:
            cfg = self._base_cfg(tmp)
            events, _base = self._run_worker(
                tmp, cfg,
                urls=['https://github.com/o/not-a-real-repo'],
                non_github=['/article'])
            web_vault = cfg['website_vault_path']
            notes = [os.path.join(root, f)
                     for root, dirs, files in os.walk(web_vault)
                     for f in files if f.endswith('.md')]
            # v0.28.0 — the batch ALSO writes the Website Directory
            # (the consolidated categorized index) at the vault root.
            self.assertEqual(len(notes), 2, notes)
            site = [n for n in notes
                    if os.path.basename(n) !=
                    '000 📚 Website Directory.md'][0]
            note = open(site, encoding='utf-8').read()
            self.assertIn('category: "Design"', note)
            directory = os.path.join(web_vault,
                                     '000 📚 Website Directory.md')
            self.assertTrue(os.path.exists(directory))
            self.assertIn('Test Site', open(directory, encoding='utf-8').read())
            finished = [a[1] for _n, a in events if _n == 'finished']
            self.assertTrue(finished)
            self.assertIn('Websites: 1 processed', finished[-1])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_websites_off_writes_inbox(self):
        """Websites OFF (the default): non-GitHub links keep going to the
        _inbox dead end — the pre-Phase-2 behavior is unchanged."""
        tmp = tempfile.mkdtemp(prefix='p2worker3-')
        try:
            cfg = self._base_cfg(
                tmp, pipelines={'github': True, 'websites': False},
                website_vault_path='')
            # A GitHub link keeps the batch alive past the "no URLs"
            # early-return (it fails fast via the patched Github client),
            # so the manifest intake records the non-GitHub link.
            events, _base = self._run_worker(
                tmp, cfg, urls=['https://github.com/o/repo-x'],
                non_github=['https://example.com/some-page'])
            inbox = os.path.join(cfg['vault_path'], '_inbox')
            self.assertTrue(os.path.isdir(inbox), os.listdir(cfg['vault_path']))
            files = [f for f in os.listdir(inbox) if f.endswith('.md')]
            self.assertTrue(files, files)
            tbl = open(os.path.join(inbox, files[0]),
                       encoding='utf-8').read()
            self.assertIn('https://example.com/some-page', tbl)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_github_off_websites_on(self):
        """GitHub OFF + websites ON: the fetch still happens, GitHub links
        are skipped with a log line, websites still process."""
        tmp = tempfile.mkdtemp(prefix='p2worker4-')
        try:
            cfg = self._base_cfg(
                tmp, pipelines={'github': False, 'websites': True})
            events, _base = self._run_worker(
                tmp, cfg,
                urls=['https://github.com/o/some-repo'],
                non_github=['/article'])
            logs = ' '.join(str(a[0]) for _n, a in events if _n == 'log')
            self.assertIn('GitHub pipeline is OFF', logs)
            self.assertIn('Websites done: 1 processed', logs)
            notes = [os.path.join(root, f)
                     for root, dirs, files in
                     os.walk(cfg['website_vault_path'])
                     for f in files if f.endswith('.md')]
            # site note + the Website Directory (v0.28.0)
            self.assertEqual(len(notes), 2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_both_off_early_return(self):
        tmp = tempfile.mkdtemp(prefix='p2worker5-')
        try:
            cfg = self._base_cfg(
                tmp, pipelines={'github': False, 'websites': False})
            events, _base = self._run_worker(
                tmp, cfg, urls=['https://github.com/o/r'], non_github=[])
            finished = [a[1] for _n, a in events if _n == 'finished']
            self.assertTrue(any('Both pipelines are off' in m
                                for m in finished))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Golden set
# ---------------------------------------------------------------------------

class TestGoldenSet(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from gitcurator.core.taxonomy import load_taxonomy_from_config
        cls.tax = load_taxonomy_from_config({})
        with open(os.path.join(HERE, 'golden', 'websites.json'),
                  encoding='utf-8') as f:
            cls.golden = json.load(f)

    def test_every_expected_value_is_valid(self):
        entries = self.golden['entries']
        self.assertEqual(len(entries), 30)
        urls = set()
        for e in entries:
            self.assertTrue(e.get('url'))
            self.assertNotIn(e['url'], urls, 'duplicate golden URL')
            urls.add(e['url'])
            self.assertTrue(self.tax.is_category(e['expected_category']),
                            e)
            sub = e.get('expected_subcategory') or ''
            if sub:
                self.assertTrue(
                    self.tax.is_subcategory_of(e['expected_category'], sub),
                    e)

    def test_offline_runner(self):
        from gitcurator.tools import run_golden_websites as runner
        out = os.path.join(tempfile.mkdtemp(prefix='p2golden-'),
                           'report.md')
        try:
            rc = runner.main(['--offline', '--out', out])
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.exists(out))
            report = open(out, encoding='utf-8').read()
            self.assertIn('invalid taxonomy names', report)
            self.assertIn('30', report)
            self.assertNotIn('❌ INVALID CATEGORY', report)
        finally:
            shutil.rmtree(os.path.dirname(out), ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
