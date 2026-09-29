#!/usr/bin/env python3
"""
website_pipeline.py — the Websites pipeline (Phase 2, SPEC §4.3 + §4.6).

Per link:
    1. canonicalize   — core/links.normalize_website_url
    2. dedupe         — the websites VaultIndex (in-memory, passed in) +
                        the ``websites_processed`` table + the dismissed
                        list + a ``_review`` note with fetch_status failed
                        is RETRIED (upgraded), not skipped
    3. fetch          — core/web_fetch (timeout, size cap, rate limit, UA)
    4. extract        — core/web_extract (title, description, main text)
    5. classify       — two passes with the taxonomy (category, then
                        subcategory); every answer validated against the
                        parsed names, 2 retries, then ``_review``; low
                        confidence also goes to ``_review``
    6. analyze        — w03 prompt; rule: omit rather than guess
    7. build & write  — atomic write into
                        <website_vault>/<Category>/<Subcategory?>/<Name>.md
    8. (seal happens in the batch finish path, not here)

Failure handling (SPEC §4.3): a link that cannot be fetched still gets a
minimal note in ``_review`` with ``fetch_status: failed`` and is retried
automatically up to 3 times over several days (state in cache.db). Nothing
is silently dropped.

Dry-run: every vault write goes through storage.atomic_write_text /
dryrun.makedirs (already gated by core/dryrun), and the caller passes a
state DB pointed at the SHADOW cache — so a dry-run records nothing.

No PyQt. The GUI/CLI worker injects ``llm_call`` (a plain
messages -> str callable) and ``vault_index_has`` (dedupe probe).
"""

import os
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

from gitcurator.constants import (
    APP_DIR, MANAGED_BY_GITCURATOR, NOTE_SCHEMA_VERSION, OWNERSHIP_BANNER,
)
from gitcurator.core import dryrun as _dryrun
from gitcurator.core import prompts as _prompts
from gitcurator.core import web_extract as _web_extract
from gitcurator.core import web_fetch as _web_fetch
from gitcurator.core.links import (
    is_gist_url, normalize_website_url,
)
from gitcurator.core.note_builder import (
    sanitize_body_text, sanitize_seq_item, sanitize_short_summary,
    sanitize_tags, yaml_scalar,
)
from gitcurator.core.storage import atomic_write_text, safe_filename, unique_path
from gitcurator.core.taxonomy import Taxonomy, load_taxonomy_from_config

# Bump when the website prompt set changes shape (SPEC Appendix A).
WEBSITE_PROMPT_VERSION = "web-v1"

# ===========================================================================
# CONFIGURATION (safe to edit)
# ===========================================================================
REVIEW_FOLDER = "_review"
FETCH_TIMEOUT_S = 20
FETCH_MAX_BYTES = 2_000_000
DOMAIN_DELAY_S = 2.0
MAX_FETCH_RETRIES = 3            # SPEC: retried automatically up to 3 times
RETRY_BACKOFF_DAYS = 2           # "over several days"
CLASSIFY_RETRIES = 2             # SPEC §4.6: retry up to 2 times, then _review
LLM_EXCERPT_CHARS = 4000         # page text given to the model
MIN_TEXT_FOR_ANALYSIS = 80       # below this, the model gets title+desc only
LOW_CONFIDENCE = "low"           # confidence that routes to _review


class WebsiteStateDB:
    """cache.db tables for the websites pipeline (same file as CacheDB and
    NoteStateDB — same APP_DIR anchoring rule, own connection + lock).

    Tables:
      websites_processed — one row per processed URL (dedupe layer 2)
      website_retry_queue — fetch failures awaiting automatic retry
      dismissed_urls — URLs whose note the owner deleted (never re-add;
        populated by Phase 3's delete detection, read here from day one)
    """

    def __init__(self, db_path: str = "cache.db"):
        if db_path == "cache.db":
            db_path = os.path.join(APP_DIR, "cache.db")
        self.db_path = db_path
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self.conn.execute("PRAGMA busy_timeout = 30000")
        with self._lock:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS websites_processed (
                    url TEXT PRIMARY KEY,
                    note_path TEXT,
                    category TEXT,
                    subcategory TEXT,
                    fetch_status TEXT,
                    processed_at TEXT
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS website_retry_queue (
                    url TEXT PRIMARY KEY,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    next_attempt_at TEXT,
                    first_failed_at TEXT,
                    last_failed_at TEXT
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS dismissed_urls (
                    url TEXT PRIMARY KEY,
                    reason TEXT,
                    dismissed_at TEXT
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS website_state_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            self.conn.commit()

    # -- writes -----------------------------------------------------------

    def mark_processed(self, url: str, note_path: str, category: str,
                       subcategory: str, fetch_status: str) -> None:
        """Record the dedupe row for a processed URL. Does NOT touch the
        retry queue — a fetch-failed link keeps its _review note AND its
        pending retries; resolve_retry() clears the queue only on full
        success."""
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO websites_processed "
                "(url, note_path, category, subcategory, fetch_status,"
                " processed_at) VALUES (?,?,?,?,?,?)",
                (url, note_path, category, subcategory, fetch_status,
                 datetime.now().isoformat(timespec='seconds')))
            self.conn.commit()

    def enqueue_retry(self, url: str, error: str) -> None:
        """Record/refresh a fetch failure. ``next_attempt_at`` backs off
        RETRY_BACKOFF_DAYS per attempt so retries spread over several days.
        The ORIGINAL first_failed_at survives refreshes."""
        now = datetime.now()
        now_iso = now.isoformat(timespec='seconds')
        with self._lock:
            row = self.conn.execute(
                "SELECT attempts, first_failed_at FROM website_retry_queue"
                " WHERE url=?", (url,)).fetchone()
            attempts = (row[0] + 1) if row else 1
            first_failed = (row[1] if row and row[1] else now_iso)
            next_at = (now + timedelta(days=RETRY_BACKOFF_DAYS * attempts)) \
                .isoformat(timespec='seconds')
            self.conn.execute(
                "INSERT OR REPLACE INTO website_retry_queue "
                "(url, attempts, last_error, next_attempt_at, first_failed_at,"
                " last_failed_at) VALUES (?,?,?,?,?,?)",
                (url, attempts, error[:500], next_at, first_failed, now_iso))
            self.conn.commit()

    def resolve_retry(self, url: str) -> None:
        """Drop a link from the retry queue — called when processing fully
        succeeded (note written with real content). Kept SEPARATE from
        mark_processed on purpose: a fetch-failed link writes a _review note
        AND stays queued for its automatic retries."""
        with self._lock:
            self.conn.execute("DELETE FROM website_retry_queue WHERE url=?",
                              (url,))
            self.conn.commit()

    def dismiss(self, url: str, reason: str = "note deleted by owner") -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO dismissed_urls (url, reason,"
                " dismissed_at) VALUES (?,?,?)",
                (url, reason[:200], datetime.now().isoformat(timespec='seconds')))
            self.conn.commit()

    # -- v0.19.0 proxy-epoch meta + retry re-arm --------------------------

    def get_meta(self, key: str) -> Optional[str]:
        """One row from website_state_meta (None when absent)."""
        with self._lock:
            row = self.conn.execute(
                "SELECT value FROM website_state_meta WHERE key=?",
                (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO website_state_meta (key, value)"
                " VALUES (?,?)", (key, str(value)))
            self.conn.commit()

    def rearm_retries(self) -> int:
        """v0.19.0 — reset the ENTIRE retry queue (attempts=0, due NOW).
        Called when the web proxy turns ACTIVE: the queued failures were
        almost certainly the blocked-web pattern (x.com/t.co/youtu.be
        connection-refused on a poisoned resolver), and with DNS now
        resolving at the proxy they deserve an immediate fresh set.
        Returns how many rows were re-armed."""
        now_iso = datetime.now().isoformat(timespec='seconds')
        with self._lock:
            cur = self.conn.execute(
                "UPDATE website_retry_queue"
                " SET attempts=0, next_attempt_at=?", (now_iso,))
            self.conn.commit()
            return cur.rowcount or 0

    # -- reads ------------------------------------------------------------

    def is_processed(self, url: str) -> bool:
        with self._lock:
            row = self.conn.execute(
                "SELECT 1 FROM websites_processed WHERE url=?", (url,)).fetchone()
        return row is not None

    def processed_row(self, url: str) -> Optional[Dict]:
        with self._lock:
            row = self.conn.execute(
                "SELECT url, note_path, category, subcategory, fetch_status,"
                " processed_at FROM websites_processed WHERE url=?", (url,)).fetchone()
        if not row:
            return None
        return {'url': row[0], 'note_path': row[1], 'category': row[2],
                'subcategory': row[3], 'fetch_status': row[4],
                'processed_at': row[5]}

    def is_dismissed(self, url: str) -> bool:
        with self._lock:
            row = self.conn.execute(
                "SELECT 1 FROM dismissed_urls WHERE url=?", (url,)).fetchone()
        return row is not None

    def retry_row(self, url: str) -> Optional[Dict]:
        with self._lock:
            row = self.conn.execute(
                "SELECT url, attempts, last_error, next_attempt_at,"
                " first_failed_at, last_failed_at FROM website_retry_queue"
                " WHERE url=?", (url,)).fetchone()
        if not row:
            return None
        return {'url': row[0], 'attempts': row[1], 'last_error': row[2],
                'next_attempt_at': row[3], 'first_failed_at': row[4],
                'last_failed_at': row[5]}

    def due_retries(self, now: Optional[datetime] = None) -> List[str]:
        """Retry-queue URLs whose backoff has elapsed (attempts < cap)."""
        now = now or datetime.now()
        with self._lock:
            rows = self.conn.execute(
                "SELECT url, attempts, next_attempt_at FROM website_retry_queue"
            ).fetchall()
        out = []
        for url, attempts, next_at in rows:
            if attempts >= MAX_FETCH_RETRIES:
                continue
            try:
                if datetime.fromisoformat(next_at) <= now:
                    out.append(url)
            except (TypeError, ValueError):
                out.append(url)
        return out

    def close(self) -> None:
        with self._lock:
            try:
                self.conn.close()
            except sqlite3.Error:
                pass


# ===========================================================================
# The website note builder
# ===========================================================================

def build_website_note(url: str, analysis: Dict, category: str,
                       subcategory: str, fetch_status: str,
                       tags: Optional[List[str]] = None) -> str:
    """Build a website note (frontmatter + body) from SANITIZED-safe inputs.

    Every value that reaches YAML goes through the note_builder sanitizers
    (non-negotiable #6: LLM output is untrusted input). ``category`` and
    ``subcategory`` are taxonomy-validated BEFORE this function is called;
    they are still re-sanitized here — defense in depth.
    """
    name = sanitize_short_summary(analysis.get('name') or '')[:120] \
        or "Untitled site"
    one_line = sanitize_short_summary(analysis.get('one_line') or '')
    offerings = analysis.get('core_offerings') or []
    if isinstance(offerings, str):
        offerings = [offerings]
    offering_lines = [sanitize_body_text(o, max_len=200) for o in offerings]
    offering_lines = [o for o in offering_lines if o.strip()]
    offerings_md = "\n".join(f"- {o}" for o in offering_lines[:6]) \
        or "- (none listed)"
    standout = sanitize_body_text(analysis.get('standout_feature') or '',
                                  max_len=500)
    best_used_for = sanitize_body_text(analysis.get('best_used_for') or '',
                                       max_len=400)
    pricing = str(analysis.get('pricing') or 'unknown').strip().lower()
    if pricing not in ('free', 'freemium', 'paid', 'unknown'):
        pricing = 'unknown'
    login_required = str(analysis.get('login_required') or 'unknown').strip().lower()
    if login_required not in ('yes', 'no', 'unknown'):
        login_required = 'unknown'
    similar = sanitize_tags(analysis.get('similar_tools') or [], max_items=5)
    note_tags = sanitize_tags(list(tags or []) + list(analysis.get('tags') or []),
                              max_items=8)
    if is_gist_url(url) and 'snippet' not in [t.lower() for t in note_tags]:
        note_tags.append('snippet')       # SPEC §4.2: gists get #snippet

    cat_yaml = yaml_scalar(category)
    sub_yaml = yaml_scalar(subcategory) if subcategory else '""'
    url_yaml = yaml_scalar(url)
    tags_yaml = "tags: [" + ", ".join(note_tags) + "]" if note_tags \
        else "tags: []"

    outstanding_md = standout or "(none identified)"
    best_used_line = best_used_for or \
        "Use when you need to… (not captured — see the source link)."
    similar_md = ", ".join(similar) if similar else "—"

    return f"""---
source: {url_yaml}
aliases: []
{tags_yaml}
category: {cat_yaml}
subcategory: {sub_yaml}
fetch_status: "{fetch_status}"
pricing: "{pricing}"
login_required: "{login_required}"
date_processed: {datetime.now().strftime("%Y-%m-%d")}
managed_by: "{MANAGED_BY_GITCURATOR}"
schema_version: "{NOTE_SCHEMA_VERSION}"
prompt_version: "{WEBSITE_PROMPT_VERSION}"
---

{OWNERSHIP_BANNER}

# {name}

> **TL;DR:** {one_line or '—'}

## Core offerings
{offerings_md}

## Standout feature
{outstanding_md}

## Best used for
{best_used_line}

## Pricing & sign-up
- Pricing: {pricing}
- Login required: {login_required}

## Similar tools
{similar_md}

---
*Source: [{url}]({url})*
"""


def build_review_note(url: str, fetch_status: str, reason: str,
                      title: str = '', tags: Optional[List[str]] = None) -> str:
    """Minimal _review note for links that could not be fully processed
    (SPEC §4.3: "a link that cannot be fetched still gets a minimal note
    in _review with fetch_status: failed")."""
    note_tags = sanitize_tags(list(tags or []), max_items=6)
    if is_gist_url(url) and 'snippet' not in [t.lower() for t in note_tags]:
        note_tags.append('snippet')
    tags_yaml = "tags: [" + ", ".join(note_tags) + "]" if note_tags \
        else "tags: []"
    title = sanitize_short_summary(title or '')[:120] or url
    return f"""---
source: {yaml_scalar(url)}
aliases: []
{tags_yaml}
category: ""
subcategory: ""
fetch_status: "{fetch_status}"
pricing: "unknown"
login_required: "unknown"
date_processed: {datetime.now().strftime("%Y-%m-%d")}
managed_by: "{MANAGED_BY_GITCURATOR}"
schema_version: "{NOTE_SCHEMA_VERSION}"
prompt_version: "{WEBSITE_PROMPT_VERSION}"
---

{OWNERSHIP_BANNER}

# {title}

> [!warning] Needs review — {fetch_status}
> {sanitize_body_text(reason or 'The page could not be processed fully.', max_len=400)}

This note is a placeholder created automatically. The link was recorded so
it is never lost; it will be retried automatically.

---
*Source: [{url}]({url})*
"""


# ===========================================================================
# The pipeline
# ===========================================================================

class WebsitePipeline:
    """Runs the per-link flow. One instance per batch.

    ``llm_call(messages, task=None) -> str`` is injected (the worker
    routes to Ollama or an OpenAI-compatible endpoint exactly like the
    GitHub pipeline; the golden runner injects a fake). ``task`` is
    'classify' for the w01/w02 passes and 'analyze' for w03 — the router
    uses it for the per-task model overrides (models.classify /
    models.analyze, v0.13.0 Phase 4). ``vault_index_has(url) -> bool |
    path`` probes the websites VaultIndex (the ground-truth dedupe
    layer).
    """

    def __init__(self, config: dict, llm_call: Callable,
                 vault_index_has: Callable,
                 state: WebsiteStateDB,
                 log: Optional[Callable] = None,
                 taxonomy: Optional[Taxonomy] = None,
                 note_state_db=None,
                 rate_limiter: Optional[_web_fetch.DomainRateLimiter] = None,
                 fetch_fn=None):
        from gitcurator.constants import resolve_taxonomy_path
        self.config = config or {}
        self.llm_call = llm_call
        self.vault_index_has = vault_index_has
        self.state = state
        self.log = log or (lambda *a, **k: None)
        self.note_state_db = note_state_db
        # Optional fetch injection (tests + the offline golden run stub
        # this so NO network is touched; production leaves it None).
        self.fetch_fn = fetch_fn or _web_fetch.fetch_url
        # v0.19.0 — Web fetches through the proxy (Settings → Proxy): the
        # blocked-web fix. x.com / t.co / youtu.be connections are REFUSED
        # on the owner's direct line (poisoned DNS) while the rest of the
        # web fetches fine — so when a proxy is configured AND reachable,
        # every fetch of this batch rides it (DNS at the proxy). NEVER
        # applied to an injected fetch_fn: the golden run must stay
        # offline and tests must stay hermetic.
        self.web_proxy = None
        if fetch_fn is None:
            self.web_proxy = _web_fetch.proxy_from_config(self.config)
            if self.web_proxy is not None:
                _ok, _why = _web_fetch.web_proxy_preflight(self.web_proxy)
                if _ok:
                    self.log(
                        f"🌐 Web fetches via "
                        f"{_web_fetch.proxy_label(self.web_proxy)} proxy "
                        f"(Settings → 🌐 Proxy)", "info")
                    _proxy = self.web_proxy

                    def _proxied_fetch(url, **kwargs):
                        kwargs.setdefault('proxy', _proxy)
                        return _web_fetch.fetch_url(url, **kwargs)

                    self.fetch_fn = _proxied_fetch
                    self._maybe_rearm_retries()
                else:
                    self.log(
                        f"⚠️ Web proxy "
                        f"{_web_fetch.proxy_label(self.web_proxy)} NOT "
                        f"reachable — fetching DIRECT. {_why} x.com / "
                        f"YouTube links will keep failing until the proxy "
                        f"client is up.", "warning")
                    self.web_proxy = None
        self.taxonomy = taxonomy or load_taxonomy_from_config(self.config)
        self.vault_path = (self.config.get('website_vault_path') or '').strip()
        self.taxonomy_path = resolve_taxonomy_path(self.config)
        # Fetch politeness knobs are config-overridable (tests use small
        # timeouts; the owner can raise them for slow connections).
        self.fetch_timeout_s = float(
            self.config.get('web_fetch_timeout_s', FETCH_TIMEOUT_S)
            or FETCH_TIMEOUT_S)
        self.fetch_max_bytes = int(
            self.config.get('web_fetch_max_bytes', FETCH_MAX_BYTES)
            or FETCH_MAX_BYTES)
        self.rate_limiter = rate_limiter or _web_fetch.DomainRateLimiter(
            float(self.config.get('web_domain_delay_s', DOMAIN_DELAY_S)
                  or DOMAIN_DELAY_S))
        # Per-batch counters for the run report.
        self.counters = {'processed': 0, 'review': 0, 'skipped': 0,
                         'retried': 0, 'failed': 0, 'upgraded': 0}
        self.last_results: List[Dict] = []

    # -- helpers -----------------------------------------------------------

    PROXY_EPOCH_KEY = 'web_proxy_epoch'

    def _maybe_rearm_retries(self) -> None:
        """v0.19.0 — when the ACTIVE web proxy differs from the last one
        this state DB saw (first proxy ever, or a changed host/port/type),
        reset the whole retry queue: those failures queued while fetching
        direct deserve an immediate retry through the tunnel instead of
        waiting out their backoff. Once per proxy epoch — a later batch
        with the SAME proxy never re-arms again (the retry cap keeps its
        meaning)."""
        try:
            epoch = _web_fetch.proxy_label(self.web_proxy)
            if self.state.get_meta(self.PROXY_EPOCH_KEY) == epoch:
                return
            count = self.state.rearm_retries()
            self.state.set_meta(self.PROXY_EPOCH_KEY, epoch)
            if count:
                self.log(
                    f"🔁 Web proxy active — re-armed {count} queued "
                    f"retry(ies) for an immediate retry through the proxy",
                    "info")
        except Exception as e:  # bookkeeping must never kill the batch
            self.log(f"⚠️ Retry re-arm skipped: {e}", "warning")

    def _llm_json(self, messages: List[Dict], task: str = None) -> Dict:
        """One LLM call + robust JSON extraction. ``task`` tags the pass
        ('classify' / 'analyze') so the router can apply the per-task
        model override. Raises ValueError on an empty/unparseable answer
        (callers retry, then fall back to _review)."""
        from gitcurator.core.llm_client import extract_json
        content = self.llm_call(messages, task=task)
        if not content or not str(content).strip():
            raise ValueError("model returned an empty response")
        return extract_json(str(content))

    def _correction_examples(self, url: str) -> str:
        """v0.13.0 — the deferred Phase-3 few-shot hook: past corrections
        as classifier examples (needs the Phase-4 models.classify override
        to be safe on context budget — now shipped). This URL's own
        correction history first (strongest signal: the owner already
        moved THIS site once), then up to three of the owner's most
        recent moves in the Websites vault. '(none)' when there is
        nothing (the common case on a fresh install)."""
        if self.note_state_db is None:
            return "(none)"
        from gitcurator.core import note_state as _note_state
        lines: List[str] = []
        try:
            own = self.note_state_db.corrections_for(
                _note_state.VAULT_WEBSITES, url)
        except Exception as e:
            self.log(f"⚠️ corrections lookup failed for {url}: {e}",
                     "warning")
            own = []
        for c in own[:3]:
            lines.append(
                f"- this exact website: the owner moved it "
                f"{c['from_category'] or '(none)'} -> "
                f"{c['to_category'] or '(none)'}")
        try:
            recent = self.note_state_db.recent_corrections(
                _note_state.VAULT_WEBSITES, limit=6)
        except Exception as e:
            self.log(f"⚠️ recent-corrections lookup failed: {e}", "warning")
            recent = []
        seen = set()
        for c in recent:
            if c['source_url'] == url:
                continue
            key = (c['from_category'], c['to_category'])
            if key in seen:
                continue
            seen.add(key)
            lines.append(
                f"- {c['source_url']}: the owner moved it "
                f"{c['from_category'] or '(none)'} -> "
                f"{c['to_category'] or '(none)'}")
            if len(seen) >= 3:
                break
        return "\n".join(lines) if lines else "(none)"

    def _classify_category(self, url: str, title: str, description: str,
                           text: str) -> tuple:
        """Pass 1 (SPEC §4.6): category names + one-line definitions + the
        judgment rules. Validates the answer; retries up to CLASSIFY_RETRIES
        with a corrective nudge; returns (category, confidence) or ('', '')."""
        prompt = _prompts.load_prompt(
            'w01_category',
            CATEGORY_NAMES_WITH_ONE_LINE_DEFINITIONS=self.taxonomy
            .category_one_liner(),
            JUDGMENT_RULES=self.taxonomy.judgment_rules or "(none)",
            PAST_CORRECTIONS=self._correction_examples(url),
            URL=url, TITLE=title or "(unknown)",
            META_DESCRIPTION=description or "(none)",
            TEXT_EXCERPT=text[:LLM_EXCERPT_CHARS] or "(no page text)")
        messages = [{"role": "user", "content": prompt}]
        last_bad = ""
        for attempt in range(1 + CLASSIFY_RETRIES):
            try:
                answer = self._llm_json(messages, task='classify')
            except ValueError as e:
                last_bad = f"unparseable answer: {e}"
                continue
            cat = str(answer.get('category') or '').strip()
            conf = str(answer.get('confidence') or '').strip().lower()
            if self.taxonomy.is_category(cat):
                return cat, conf or 'medium'
            last_bad = f"category {cat!r} is not in the taxonomy"
            # Corrective retry: tell the model exactly what it did wrong.
            messages = [{"role": "user", "content": prompt + (
                f"\n\nYour previous answer was rejected: {last_bad}. "
                "Answer again with the EXACT name of one category from the "
                "list, as JSON.")}]
        self.log(f"⚠️ {url}: category classification failed after "
                 f"{1 + CLASSIFY_RETRIES} attempts ({last_bad}) — filing "
                 "under _review", "warning")
        return '', ''

    def _classify_subcategory(self, url: str, category: str, title: str,
                              description: str, text: str) -> tuple:
        """Pass 2: only the chosen category's subcategories."""
        prompt = _prompts.load_prompt(
            'w02_subcategory',
            CATEGORY=category,
            SUBCATEGORIES_WITH_DEFINITIONS=self.taxonomy
            .subcategory_one_liner(category),
            URL=url, TITLE=title or "(unknown)",
            META_DESCRIPTION=description or "(none)",
            TEXT_EXCERPT=text[:LLM_EXCERPT_CHARS] or "(no page text)")
        messages = [{"role": "user", "content": prompt}]
        for attempt in range(1 + CLASSIFY_RETRIES):
            try:
                answer = self._llm_json(messages, task='classify')
            except ValueError:
                continue
            sub = str(answer.get('subcategory') or '').strip()
            conf = str(answer.get('confidence') or '').strip().lower()
            if sub.lower() == 'none' or sub == '':
                return '', conf or 'medium'
            if self.taxonomy.is_subcategory_of(category, sub):
                return sub, conf or 'medium'
            messages = [{"role": "user", "content": prompt + (
                f"\n\nYour previous answer {sub!r} is not one of the listed "
                "subcategories. Answer again with the EXACT name of one "
                "subcategory from the list, or \"none\".")}]
        return '', ''   # no subcategory — still file under the category

    def _analyze(self, url: str, title: str, description: str,
                 text: str, category: str) -> Dict:
        """w03: the note fields. Rule: omit rather than guess — enforced by
        the prompt itself; unparseable answers raise to the caller."""
        # SPEC Appendix A note: when the page text is missing or clearly
        # partial, the model gets only the title and description.
        if len(text or '') < MIN_TEXT_FOR_ANALYSIS:
            excerpt = "(no usable page text — classify from title and description)"
        else:
            excerpt = text[:LLM_EXCERPT_CHARS]
        prompt = _prompts.load_prompt(
            'w03_analyze',
            TAG_HINTS=self.taxonomy.tag_hints_of(category) or "(none)",
            URL=url, TITLE=title or "(unknown)",
            META_DESCRIPTION=description or "(none)",
            TEXT_EXCERPT=excerpt)
        answer = self._llm_json([{"role": "user", "content": prompt}],
                                task='analyze')
        if not isinstance(answer, dict):
            raise ValueError("analyze answer was not a JSON object")
        return answer

    # -- writing -----------------------------------------------------------

    def _note_path(self, category: str, subcategory: str, name: str) -> str:
        rel = self.taxonomy.folder_relpath(category, subcategory or None)
        fname = safe_filename(name) or "untitled-site"
        if not fname.lower().endswith('.md'):
            fname += '.md'
        path = os.path.join(self.vault_path, rel, fname)
        return unique_path(path)

    def _write_note(self, path: str, content: str) -> None:
        # _dryrun.makedirs records instead of creating while a dry-run is
        # active (and is a plain makedirs otherwise); atomic_write_text is
        # gated the same way — dry-run writes NOTHING, by construction.
        _dryrun.makedirs(os.path.dirname(path), exist_ok=True)
        atomic_write_text(path, content)

    def _record(self, url: str, path: str, content: str, category: str,
                subcategory: str, fetch_status: str) -> None:
        """Persist dedupe + note-state bookkeeping. In a dry-run the state
        DB is the shadow cache, so nothing real is recorded."""
        self.state.mark_processed(url, path, category, subcategory,
                                  fetch_status)
        if self.note_state_db is not None:
            try:
                from gitcurator.core import note_state as _note_state
                from gitcurator.core.links import normalize_website_url
                self.note_state_db.record_note(
                    _note_state.VAULT_WEBSITES, url, path, content=content,
                    category=category, subcategory=subcategory,
                    normalizer=normalize_website_url)
            except Exception:
                pass  # bookkeeping must never break a run

    def _app_owns_old_note(self, canonical: str, old_path: str) -> bool:
        """True when the file at ``old_path`` is EXACTLY the app-written
        note recorded in note_state (fingerprint unchanged — no human
        edit). Only then may the pipeline replace or remove it."""
        if self.note_state_db is None or not old_path:
            return False
        try:
            from gitcurator.core import note_state as _note_state
            row = self.note_state_db.row_for(
                _note_state.VAULT_WEBSITES, canonical)
            if not row or not row.get('fingerprint'):
                return False
            if os.path.normpath(row.get('path') or '') != \
                    os.path.normpath(old_path):
                return False
            with open(old_path, 'r', encoding='utf-8',
                      errors='replace') as f:
                content = f.read()
            return _note_state.compute_fingerprint(content) \
                == row['fingerprint']
        except Exception:
            return False

    def _cleanup_replaced_review_note(self, canonical: str,
                                      old_path: str) -> None:
        """After a successful upgrade, remove the old app-owned _review
        placeholder — otherwise two files would carry the same source
        (the exact duplicate situation SPEC §4.4 flags). A hand-edited
        placeholder is NEVER removed; Phase 3's duplicate detector will
        surface it instead."""
        if not old_path or not os.path.exists(old_path):
            return
        if self._app_owns_old_note(canonical, old_path):
            _dryrun.remove(old_path)
            self.log(f"♻️ upgraded note replaced its _review placeholder "
                     f"({os.path.basename(old_path)})", "info")
        else:
            self.log(f"⚠️ kept the old _review note {old_path!r} — it looks "
                     "hand-edited; both files now carry the same source",
                     "warning")

    # -- the per-link flow ---------------------------------------------------

    def process_link(self, url: str) -> Dict:
        """Run the full per-link pipeline. Returns a result dict (never
        raises — one failing link never stops the batch)."""
        result = {'url': url, 'canonical': '', 'outcome': 'failed',
                  'note_path': '', 'category': '', 'subcategory': '',
                  'fetch_status': '', 'error': ''}
        try:
            return self._process_link_inner(url, result)
        except Exception as e:
            result['error'] = f"{type(e).__name__}: {e}"
            self.log(f"❌ Website pipeline error for {url}: {result['error']}",
                     "error")
            # SPEC §4.3: nothing is silently dropped — an unexpected error
            # (e.g. an LLM outage mid-classification) still leaves a
            # _review note behind, UNLESS this link already got one (the
            # error may have happened after a successful write).
            if not result.get('note_path'):
                try:
                    canonical = result.get('canonical') \
                        or normalize_website_url(url)
                    note = build_review_note(
                        canonical, 'failed',
                        f"Pipeline error: {result['error']}")
                    path = self._review_path(canonical)
                    self._write_note(path, note)
                    self._record(canonical, path, note, '', '', 'failed')
                    result.update(outcome='review', note_path=path,
                                  canonical=canonical)
                    self.counters['review'] += 1
                except Exception:
                    pass  # the result dict still carries the error
            self.counters['failed'] += 1
            self.last_results.append(result)
            return result

    def _process_link_inner(self, url: str, result: Dict) -> Dict:
        canonical = normalize_website_url(url)
        result['canonical'] = canonical

        # ---- 2. dedupe (SPEC §4.3 step 2) --------------------------------
        if self.state.is_dismissed(canonical):
            result['outcome'] = 'skipped'
            result['error'] = 'dismissed (note was deleted)'
            self.counters['skipped'] += 1
            self.last_results.append(result)
            return result

        in_vault = self.vault_index_has(canonical)
        prior = self.state.processed_row(canonical)
        if in_vault and not (prior and prior.get('fetch_status') == 'failed'
                             and self._is_review_path(prior.get('note_path'))):
            # Already a real note (or a non-failed _review note): skip.
            result['outcome'] = 'skipped'
            result['error'] = 'already in the websites vault'
            self.counters['skipped'] += 1
            self.last_results.append(result)
            return result

        retry = self.state.retry_row(canonical)
        if retry and retry['attempts'] >= MAX_FETCH_RETRIES:
            # A failed-fetch _review note already exists and retries are
            # exhausted: leave it, report, never drop silently.
            result['outcome'] = 'skipped'
            result['error'] = (f"fetch failed {retry['attempts']} times — "
                               "kept in _review, no more retries")
            self.counters['skipped'] += 1
            self.last_results.append(result)
            return result

        upgraded = bool(in_vault and prior
                        and prior.get('fetch_status') == 'failed')

        # v0.12.0 — Phase 3 (locked-skip, SPEC §4.4/§6): a note the owner
        # moved by hand is LOCKED — its folder placement beats the
        # classifier. When a locked row exists (e.g. a _review placeholder
        # the owner moved into a category folder, later upgraded by a
        # successful retry), the model's category/subcategory are ignored
        # and the locked row's values are written instead.
        locked_row = None
        if self.note_state_db is not None:
            try:
                from gitcurator.core import note_state as _note_state
                _row = self.note_state_db.row_for(
                    _note_state.VAULT_WEBSITES, canonical)
                if _row is not None and _row.get('locked') \
                        and _row.get('category'):
                    locked_row = _row
            except Exception:
                locked_row = None

        # ---- 3. fetch ------------------------------------------------------
        fetch = self.fetch_fn(
            url, timeout_s=self.fetch_timeout_s,
            max_bytes=self.fetch_max_bytes,
            rate_limiter=self.rate_limiter)
        result['fetch_status'] = fetch.status

        if not fetch.ok:
            # ---- failure handling: minimal _review note + retry queue -----
            self.state.enqueue_retry(canonical, fetch.reason)
            note = build_review_note(canonical, 'failed',
                                     f"Fetch failed: {fetch.reason}")
            # Re-failure: overwrite the app-owned placeholder in place when
            # possible (same path, atomic write) instead of stacking
            # _v1/_v2 duplicates; a hand-edited placeholder is kept and a
            # fresh path is used.
            path = self._review_path(canonical, prior=prior)
            self._write_note(path, note)
            self._record(canonical, path, note, '', '', 'failed')
            result.update(outcome='review', note_path=path,
                          error=f"fetch failed: {fetch.reason}")
            self.counters['review' if not upgraded else 'upgraded'] += 1
            self.last_results.append(result)
            self.log(f"📥 {url}: fetch failed ({fetch.reason}) — minimal "
                     "note in _review, retry scheduled", "warning")
            return result

        # ---- 4. extract ----------------------------------------------------
        page = _web_extract.extract_from_bytes(fetch.body, fetch.charset)
        fetch_status = fetch.status           # full | partial (pdf/size)
        if fetch_status == 'full':
            if page.is_js_shell:
                fetch_status = 'partial'
            elif page.is_paywall:
                fetch_status = 'partial'
        title = page.title or ''
        description = page.meta_description or ''

        # ---- 5. classify ---------------------------------------------------
        if locked_row is not None:
            # The owner already placed this note — no model call, no
            # _review: their correction IS the classification.
            category = locked_row['category']
            subcategory = locked_row.get('subcategory') or ''
            self.log(f"🔒 {url}: locked note — owner's placement "
                     f"({category}"
                     + (f" / {subcategory}" if subcategory else "")
                     + ") kept, classifier skipped", "info")
        else:
            category, conf1 = self._classify_category(
                canonical, title, description, page.text)
            if not category or conf1 == LOW_CONFIDENCE:
                # Low confidence is NOT a fetch failure — no fetch retry. The
                # note waits in _review for the owner's move (a correction,
                # SPEC §4.4), and the run report lists it.
                reason = ("classification confidence was low"
                          if category else "no valid category after retries")
                note = build_review_note(
                    canonical, fetch_status, reason, title=title)
                path = self._review_path(canonical)
                self._write_note(path, note)
                self._record(canonical, path, note, '', '', fetch_status)
                result.update(outcome='review', note_path=path, error=reason,
                              fetch_status=fetch_status)
                self.counters['review'] += 1
                self.last_results.append(result)
                self.log(f"🗂️ {url}: {reason} — filed under _review", "warning")
                return result

            subcategory, conf2 = self._classify_subcategory(
                canonical, category, title, description, page.text)
            if conf2 == LOW_CONFIDENCE:
                subcategory = ''      # low-confidence subcategory: category only

        # ---- 6. analyze ----------------------------------------------------
        try:
            analysis = self._analyze(canonical, title, description,
                                     page.text, category)
        except Exception as e:
            # Analysis failed but classification succeeded: still write a
            # review note — the link must never be silently dropped.
            note = build_review_note(
                canonical, fetch_status,
                f"Analysis failed: {e}", title=title)
            path = self._review_path(canonical)
            self._write_note(path, note)
            self._record(canonical, path, note, category, subcategory,
                         fetch_status)
            result.update(outcome='review', note_path=path,
                          error=f"analysis failed: {e}",
                          category=category, subcategory=subcategory,
                          fetch_status=fetch_status)
            self.counters['review'] += 1
            self.last_results.append(result)
            return result

        # ---- 7. build & write ------------------------------------------------
        name = str(analysis.get('name') or title or canonical)
        note = build_website_note(canonical, analysis, category, subcategory,
                                  fetch_status)
        path = self._note_path(category, subcategory, name)
        self._write_note(path, note)
        # Upgrade cleanup BEFORE _record: the ownership proof compares the
        # note_state row's path with the OLD path — recording first would
        # make the app look like it no longer owns the placeholder.
        if upgraded and prior:
            self._cleanup_replaced_review_note(canonical,
                                               prior.get('note_path') or '')
        self._record(canonical, path, note, category, subcategory,
                     fetch_status)
        # Full success: any pending fetch-retry for this link is resolved.
        if retry:
            self.state.resolve_retry(canonical)

        result.update(outcome='processed', note_path=path, category=category,
                      subcategory=subcategory, fetch_status=fetch_status,
                      error='')
        self.counters['processed' if not upgraded else 'upgraded'] += 1
        self.last_results.append(result)
        self.log(f"✅ {url} → {category}"
                 + (f" / {subcategory}" if subcategory else "")
                 + f" ({fetch_status})", "success")
        return result

    def _review_path(self, canonical: str, prior: Optional[Dict] = None) -> str:
        """Path for a _review note. When ``prior`` points at an existing
        app-owned _review placeholder for the same URL, that SAME path is
        returned so the atomic write replaces it (no _v1/_v2 stacking)."""
        if prior and prior.get('note_path') \
                and self._is_review_path(prior['note_path']) \
                and os.path.exists(prior['note_path']) \
                and self._app_owns_old_note(canonical, prior['note_path']):
            return prior['note_path']
        from urllib.parse import urlparse
        host = urlparse(canonical).netloc or 'unknown'
        fname = safe_filename(host + urlparse(canonical).path)[:120] \
            or 'review'
        if not fname.lower().endswith('.md'):
            fname += '.md'
        return unique_path(os.path.join(self.vault_path, REVIEW_FOLDER, fname))

    @staticmethod
    def _is_review_path(path: str) -> bool:
        return bool(path) and (REVIEW_FOLDER in (path or '').replace('\\', '/'))

    # -- batch entry --------------------------------------------------------

    def run(self, urls: List[str], should_continue=None) -> List[Dict]:
        """Process a batch of non-GitHub links (deduped upstream by the
        caller; dedupe is re-checked per link here anyway).
        ``should_continue`` (optional) is polled between links so a GUI
        Stop button can end the phase cleanly."""
        results = []
        seen = set()
        for url in urls:
            if should_continue is not None and not should_continue():
                self.log("⏹️ Websites pipeline stopped by user — remaining "
                         "links stay queued for the next run", "warning")
                break
            canonical = normalize_website_url(url)
            if canonical in seen:
                # Within-batch duplicate: REPORT it (never silently drop —
                # the run report's counts must add up).
                results.append({
                    'url': url, 'canonical': canonical, 'outcome': 'skipped',
                    'note_path': '', 'category': '', 'subcategory': '',
                    'fetch_status': '', 'error': 'duplicate within batch'})
                continue
            seen.add(canonical)
            results.append(self.process_link(url))
        return results

    def run_due_retries(self, should_continue=None) -> List[Dict]:
        """Retry fetch-failed links whose backoff elapsed (SPEC §4.3:
        "retried automatically up to 3 times over several days"). Called by
        the batch BEFORE the fresh links."""
        due = self.state.due_retries()
        if not due:
            return []
        self.log(f"🔁 Retrying {len(due)} fetch-failed website link(s) "
                 "whose backoff elapsed", "info")
        results = []
        for u in due:
            if should_continue is not None and not should_continue():
                self.log("⏹️ Websites retry pass stopped by user", "warning")
                break
            results.append(self.process_link(u))
        self.counters['retried'] = len(due)
        return results
