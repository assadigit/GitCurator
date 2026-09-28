#!/usr/bin/env python3
"""
web_extract.py — turn fetched HTML into title / description / main text
(Phase 2, SPEC §4.3 step 4).

Standard library only (``html.parser`` — SPEC: "standard library first").
The extractor is deliberately conservative: it produces TEXT, never
interpretations. The two "partial page" heuristics the SPEC names —

    JavaScript-only shell — a page whose body renders client-side; the
        fetched HTML has (almost) no words in it
    paywall stub — a page whose text is a subscription wall rather than
        the article

are reported as flags; the PIPEPIPE's caller (website_pipeline) turns
them into ``fetch_status: partial``. Extraction never raises on bad
input — a broken page is a partial result, not a crash.

Pure stdlib, no PyQt.
"""

import re
from html.parser import HTMLParser
from typing import Optional

# How much body text to keep for note-building / LLM excerpts.
MAX_TEXT_CHARS = 20_000

# A body with fewer than this many alphanumeric characters AND at least one
# <script> is assumed to render client-side (a JS-only shell).
_JS_SHELL_MIN_TEXT = 120

# Paywall phrases (lower-cased substring match on the first part of the text).
_PAYWALL_MARKERS = (
    'subscribe to continue',
    'subscription required',
    'subscribe to read',
    'to continue reading',
    'create a free account to continue',
    'sign in to continue reading',
    'this article is for subscribers',
    'premium subscribers only',
    'unlock this article',
    'reader mode is not available',  # some JS shells say this; treat as wall
)

# Tags whose entire content is invisible to a reader.
_INVISIBLE_TAGS = {'script', 'style', 'noscript', 'template', 'svg', 'head'}

# Block-level tags that force a line break in the extracted text.
_BLOCK_TAGS = {
    'p', 'div', 'br', 'li', 'ul', 'ol', 'tr', 'table', 'section', 'article',
    'header', 'footer', 'nav', 'main', 'aside', 'h1', 'h2', 'h3', 'h4',
    'h5', 'h6', 'blockquote', 'pre', 'figure', 'figcaption', 'hr',
}


class Extracted:
    """What the extractor found on a page."""

    __slots__ = ('title', 'meta_description', 'text', 'is_js_shell',
                 'is_paywall', 'charset_used')

    def __init__(self, title='', meta_description='', text='',
                 is_js_shell=False, is_paywall=False, charset_used=''):
        self.title = (title or '').strip()
        self.meta_description = (meta_description or '').strip()
        self.text = (text or '').strip()
        self.is_js_shell = is_js_shell
        self.is_paywall = is_paywall
        self.charset_used = charset_used

    @property
    def has_content(self) -> bool:
        """True when there is enough signal (title/description/text) to
        classify and analyze the page."""
        return bool(self.title or self.meta_description
                    or len(self.text) >= 80)

    def __repr__(self):
        return (f"<Extracted title={self.title!r:.40} desc_len="
                f"{len(self.meta_description)} text_len={len(self.text)} "
                f"js_shell={self.is_js_shell} paywall={self.is_paywall}>")


class _PageParser(HTMLParser):
    """Collects title, meta description and visible text in one pass."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ''
        self.meta_description = ''
        self.detected_charset = ''
        self._in_title = False
        self._invisible_depth = 0
        self._script_depth = 0
        self._chunks = []
        self._skip_nl = True

    # -- tag events --------------------------------------------------------

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _INVISIBLE_TAGS:
            self._invisible_depth += 1
            if tag == 'script':
                self._script_depth += 1
            return
        if tag == 'title':
            self._in_title = True
        if tag == 'meta':
            d = dict(attrs)
            name = (d.get('name') or '').lower()
            prop = (d.get('property') or '').lower()
            content = d.get('content') or ''
            if name == 'description' and content and not self.meta_description:
                self.meta_description = content
            elif prop == 'og:description' and content \
                    and not self.meta_description:
                self.meta_description = content
            elif (d.get('charset') or '') and not self.detected_charset:
                self.detected_charset = d.get('charset')
            elif (d.get('http-equiv') or '').lower() == 'content-type' \
                    and content and not self.detected_charset:
                m = re.search(r'charset=([^\s;]+)', content, re.IGNORECASE)
                if m:
                    self.detected_charset = m.group(1).strip('"\'')
        if tag in _BLOCK_TAGS:
            self._chunks.append('\n')

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == 'title':
            self._in_title = False
        if tag in _INVISIBLE_TAGS:
            if self._invisible_depth > 0:
                self._invisible_depth -= 1
            if tag == 'script' and self._script_depth > 0:
                self._script_depth -= 1
        if tag in _BLOCK_TAGS:
            self._chunks.append('\n')

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self._invisible_depth > 0:
            return
        if data and data.strip('\n\t '):
            self._chunks.append(data)

    # -- result -------------------------------------------------------------

    @property
    def text(self) -> str:
        raw = ''.join(self._chunks)
        # Collapse runs of whitespace but KEEP paragraph breaks.
        raw = re.sub(r'[ \t]+', ' ', raw)
        raw = re.sub(r'\n\s*\n+', '\n', raw)
        return raw.strip()


def extract(html: str) -> Extracted:
    """Extract title / meta description / visible text from HTML text.

    Never raises: unparseable input returns an Extracted with whatever was
    found (possibly nothing) — the pipeline turns that into a partial.
    """
    if not html or not html.strip():
        return Extracted(is_js_shell=True)
    parser = _PageParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass  # keep whatever was collected before the parse error

    title = ' '.join(parser.title.split())
    text = parser.text[:MAX_TEXT_CHARS]
    word_chars = len(re.findall(r'[A-Za-z0-9]', text))

    # JS-only shell: the page HAS scripts but the served HTML has almost
    # no readable words. (A truly empty static page is also "no content".)
    has_script_tag = bool(re.search(r'<script\b', html, re.IGNORECASE))
    is_js_shell = word_chars < _JS_SHELL_MIN_TEXT and has_script_tag

    low_text = text[:4000].lower()
    is_paywall = any(m in low_text for m in _PAYWALL_MARKERS)

    return Extracted(title=title,
                     meta_description=parser.meta_description,
                     text=text,
                     is_js_shell=is_js_shell,
                     is_paywall=is_paywall,
                     charset_used=parser.detected_charset)


def extract_from_bytes(body: bytes, header_charset: str = '') -> Extracted:
    """Decode ``body`` (header charset, then the page's own <meta charset>,
    then UTF-8-with-replacement) and extract. Non-UTF-8 pages keep their
    accented characters instead of turning into '�' walls."""
    if not body:
        return Extracted(is_js_shell=True)

    def _try(cs: str) -> Optional[str]:
        if not cs:
            return None
        try:
            return body.decode(cs)
        except (LookupError, UnicodeDecodeError):
            return None

    html = _try(header_charset)
    if html is None:
        # Sniff <meta charset> from the raw bytes (ASCII-compatible prefixes
        # work for utf-8/windows-1252/iso-8859-* alike).
        head = body[:4096].decode('ascii', errors='ignore').lower()
        m = re.search(r'charset=["\']?([a-z0-9_\-]+)', head)
        if m:
            html = _try(m.group(1))
    if html is None:
        html = body.decode('utf-8', errors='replace')

    result = extract(html)
    if not result.charset_used:
        result.charset_used = header_charset or 'utf-8'
    return result
