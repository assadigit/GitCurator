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
from gitcurator.core import links as _links
from gitcurator.core.links import (
    is_gist_url, normalize_website_url,
)
from gitcurator.core.note_builder import (
    sanitize_body_text, sanitize_seq_item, sanitize_short_summary,
    sanitize_tags, yaml_scalar,
)
from gitcurator.core.storage import atomic_write_text, safe_filename, unique_path
from gitcurator.core.taxonomy import Taxonomy, load_taxonomy_from_config
# v0.25.0 — the state ledger moved to core/website_state.py
# (re-exported here so import paths and test patch targets are unchanged).
from gitcurator.core.website_state import (  # noqa: F401
    MAX_FETCH_RETRIES, RETRY_BACKOFF_DAYS, WebsiteStateDB,
)

# Bump when the website prompt set changes shape (SPEC Appendix A).
# web-v2 (v0.27.0): decision-oriented body — "What it does" replaces the
# marketing-prone "Core offerings"/"Standout feature" pair, unknown values
# are omitted instead of printed as "unknown", and two new sections carry
# the practical facts (price terms, license, framework, install) and honest
# caveats. "Best used for" keeps its exact heading (recall/linking parses
# it) and "Similar tools" stays the closing section (the recall regex
# needs a heading after "Best used for").
WEBSITE_PROMPT_VERSION = "web-v2"

# ===========================================================================
# CONFIGURATION (safe to edit)
# ===========================================================================
REVIEW_FOLDER = "_review"
FETCH_TIMEOUT_S = 20
FETCH_MAX_BYTES = 2_000_000
DOMAIN_DELAY_S = 2.0
CLASSIFY_RETRIES = 2             # SPEC §4.6: retry up to 2 times, then _review
LLM_EXCERPT_CHARS = 4000         # page text given to the model
MIN_TEXT_FOR_ANALYSIS = 80       # below this, the model gets title+desc only
LOW_CONFIDENCE = "low"           # confidence that routes to _review




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
    # web-v2: "what_it_does" replaces "core_offerings" (old-shape dicts —
    # the offline golden runner, any in-flight analyses — still render).
    does = analysis.get('what_it_does') or analysis.get('core_offerings') or []
    if isinstance(does, str):
        does = [does]
    does_lines = [sanitize_body_text(o, max_len=200) for o in does]
    does_lines = [o for o in does_lines if o.strip()]
    best_used_for = sanitize_body_text(analysis.get('best_used_for') or '',
                                       max_len=400)
    pricing = str(analysis.get('pricing') or 'unknown').strip().lower()
    if pricing not in ('free', 'freemium', 'paid', 'unknown'):
        pricing = 'unknown'
    pricing_detail = sanitize_body_text(analysis.get('pricing_detail') or '',
                                        max_len=200)
    login_required = str(analysis.get('login_required') or 'unknown').strip().lower()
    if login_required not in ('yes', 'no', 'unknown'):
        login_required = 'unknown'
    practical = analysis.get('practical_details') or []
    if isinstance(practical, str):
        practical = [practical]
    practical_lines = [sanitize_body_text(p, max_len=200) for p in practical]
    practical_lines = [p for p in practical_lines if p.strip()][:4]
    watch = analysis.get('watch_out') or []
    if isinstance(watch, str):
        watch = [watch]
    watch_lines = [sanitize_body_text(w, max_len=200) for w in watch]
    watch_lines = [w for w in watch_lines if w.strip()][:2]
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

    # --- body (web-v2: only what is known; no "unknown" filler lines) ---
    # The recall/linking layer regex-parses "## Best used for" and needs a
    # heading after it, so "Best used for" always renders (with the
    # not-captured fallback line) and "Similar tools" always closes.
    best_used_line = best_used_for or \
        "Use when you need to… (not captured — see the source link)."
    similar_md = ", ".join(similar) if similar else "—"

    detail_lines = []
    if pricing_detail:
        if (pricing in ('free', 'freemium', 'paid')
                and not pricing_detail.lower().startswith(pricing)):
            detail_lines.append(f"Pricing: {pricing} — {pricing_detail}")
        else:
            detail_lines.append(f"Pricing: {pricing_detail}")
    elif pricing in ('free', 'freemium', 'paid'):
        detail_lines.append(f"Pricing: {pricing}")
    if login_required in ('yes', 'no'):
        detail_lines.append(
            "Sign-up required: yes" if login_required == 'yes'
            else "Sign-up required: no")
    detail_lines.extend(practical_lines)

    sections = []
    if does_lines:
        sections.append("## What it does\n" +
                        "\n".join(f"- {o}" for o in does_lines))
    sections.append("## Best used for\n" + best_used_line)
    if detail_lines:
        sections.append("## Practical details\n" +
                        "\n".join(f"- {d}" for d in detail_lines))
    if watch_lines:
        sections.append("## Watch out\n" +
                        "\n".join(f"- {w}" for w in watch_lines))
    sections.append("## Similar tools\n" + similar_md)
    body_md = "\n\n".join(sections)

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

{body_md}

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
        # v0.35.0 — the display name of the Websites vault for the
        # per-item destination logs ("[Vault Name] Item X processed and
        # stored" — the owner asked the log to show where each link goes).
        self._vault_name = os.path.basename(self.vault_path) \
            if self.vault_path else 'Websites vault'
        self.taxonomy_path = resolve_taxonomy_path(self.config)
        # v0.20.0 — blocked domains (the X fix): these links are already
        # addressed as rows in the _inbox platform tables; the pipeline
        # must never fetch, note, or retry them.
        self.blocked_domains = _links.blocked_domains_from_config(self.config)
        # v0.21.0 — self domains (the app's own bot): the bot's auth links
        # (…/auth/?token=<hex>) land in the same Telegram chat the curator
        # reads; fetching them would hit the owner's own OAuth flow and
        # store live tokens in _review notes. Same never-fetch treatment.
        self.self_domains = _links.self_domains_from_config(self.config)
        if (self.blocked_domains or self.self_domains) and fetch_fn is None:
            # Production only (an injected fetch_fn = the hermetic golden
            # run / tests). Purge queued retries + _review placeholders
            # ONCE so previously-queued blocked links stop coming back.
            self._enforce_blocked_domains()
            # v0.28.0 — THE LAW, disk edition: any app-owned note whose
            # source is on a banned domain (the legacy x_com_i_status_*
            # _review pile, notes written by an older app, a since-banned
            # domain) leaves the library for .trash/banned-domains. Runs
            # on every batch; idempotent (the first run cleans history,
            # later runs are no-ops).
            if self.vault_path and os.path.isdir(self.vault_path):
                try:
                    from gitcurator.core import \
                        website_directory as _webdir
                    _webdir.sweep_banned_notes(
                        self.vault_path, self.blocked_domains,
                        log=self.log)
                except Exception as e:
                    self.log(f"⚠️ Banned-domain sweep skipped: {e}",
                             "warning")
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

    def _enforce_blocked_domains(self) -> None:
        """v0.20.0 — purge the state DB of blocked-domain links (retry
        queue rows + failed _review placeholder records, both marked
        dismissed) and delete the placeholder FILES. v0.21.0 — the same
        purge now covers SELF domains (the app's own bot auth links that
        were queued before this release, complete with their live tokens).
        Idempotent; every failure is tolerated (bookkeeping never kills a
        batch). File deletions are dry-run-aware."""
        try:
            report = self.state.purge_blocked_domains(
                lambda u: _links.domain_is_blocked(u, self.blocked_domains))
            deleted_files = 0
            for _url, path in report.get('placeholders', []):
                try:
                    if os.path.exists(path):
                        _dryrun.remove(path)
                        deleted_files += 1
                except Exception:
                    pass
            if report['retries'] or report['placeholders']:
                self.log(
                    f"🚫 Blocked domains ({', '.join(self.blocked_domains)}): "
                    f"purged {report['retries']} queued retry(ies) + "
                    f"{len(report['placeholders'])} _review placeholder(s) "
                    f"({deleted_files} file(s) deleted) — banned links "
                    f"are never collected (no note, no _review, no _inbox "
                    f"row)",
                    "info")
        except Exception as e:
            self.log(f"⚠️ Blocked-domain purge skipped: {e}", "warning")
        if not self.self_domains:
            return
        try:
            report = self.state.purge_blocked_domains(
                lambda u: _links.domain_is_self(u, self.self_domains))
            deleted_files = 0
            for _url, path in report.get('placeholders', []):
                try:
                    if os.path.exists(path):
                        _dryrun.remove(path)
                        deleted_files += 1
                except Exception:
                    pass
            if report['retries'] or report['placeholders']:
                self.log(
                    f"🔒 Self domains ({', '.join(self.self_domains)} — the "
                    f"app's own bot): purged {report['retries']} queued "
                    f"retry(ies) + {len(report['placeholders'])} _review "
                    f"placeholder(s) ({deleted_files} file(s) deleted, tokens "
                    f"gone with them) — never fetched, the _inbox row is the "
                    f"record", "info")
        except Exception as e:
            self.log(f"⚠️ Self-domain purge skipped: {e}", "warning")

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

        # ---- 1b. never-fetch domains (v0.20.0 blocked + v0.21.0 self) ---
        # Never fetched, never noted, never retried — whatever path
        # brought the link here (batch, retry, direct, import). v0.35.0:
        # banned links are omitted ENTIRELY (no _inbox row either — the
        # manifest's blocked bucket is the count).
        if self.blocked_domains and _links.domain_is_blocked(
                url, self.blocked_domains):
            result['outcome'] = 'skipped'
            result['error'] = 'blocked domain — omitted (never collected)'
            self.counters['skipped'] += 1
            self.last_results.append(result)
            self.log(f"🚫 [{self._vault_name}] {url}: banned domain — "
                     "omitted (never fetched, never noted, never "
                     "collected)", "info")
            return result
        if self.self_domains and _links.domain_is_self(url, self.self_domains):
            result['outcome'] = 'skipped'
            result['error'] = ("self domain (the app's own bot) — recorded "
                               "in _inbox only")
            self.counters['skipped'] += 1
            self.last_results.append(result)
            self.log(f"🔒 {_links.scrub_url_token(url)}: self domain (the "
                     f"app's own bot) — never fetched, the _inbox row is "
                     f"the record", "info")
            return result

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
            self.log(f"📥 [{self._vault_name}] {url}: fetch failed "
                     f"({fetch.reason}) — minimal note in _review, retry "
                     "scheduled", "warning")
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
                self.log(f"🗂️ [{self._vault_name}] {url}: {reason} — filed "
                         "under _review", "warning")
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
        # v0.35.0 — the owner asked the log to SHOW where each link goes:
        # "[Vault Name] Item X processed and stored". The vault name is
        # the configured Websites vault's folder; the arrow is the note's
        # path INSIDE the vault (category folders included).
        _rel = os.path.relpath(path, self.vault_path).replace(os.sep, '/') \
            if self.vault_path else path
        self.log(
            f"✅ [{self._vault_name}] {name} processed and stored "
            f"→ {_rel}"
            + (f" ({fetch_status})" if fetch_status != 'full' else ''),
            "success")
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
