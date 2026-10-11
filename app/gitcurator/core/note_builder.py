#!/usr/bin/env python3
"""
note_builder.py — build the Obsidian note (frontmatter + body) from LLM output.

v30 — Fix (Sanitize LLM output into frontmatter, W12/T4): the LLM (and the
README text fed to it) is EXTERNAL, untrusted input. Before this module,
LLM-generated ``tags`` were interpolated straight into the YAML frontmatter
(main.py:3034) with zero sanitization:

    tags: [{', '.join(tags)}]

A model returning ``pwned, x]  # injected`` (or a README containing
frontmatter-looking text) could inject arbitrary YAML properties, break
Obsidian's parser, or smuggle links/aliases into every note. Same for
``aliases`` (repo_name/owner) and ``org`` (LLM/GitHub-controlled strings).

This module treats EVERY value that lands in YAML as hostile until
sanitized:
    - flow-sequence items (tags, aliases, languages): restricted charset,
      whitespace collapsed, length-capped, deduped
    - plain scalars (org, url, category): double-quoted YAML strings with
      proper escaping

Also treats README content as DATA, never instructions (the caller wraps
it in explicit untrusted-content delimiters — see _llm_analyze).

Pure stdlib — importable without PyQt for unit testing.
"""

import re
from datetime import datetime
from typing import List, Optional

from gitcurator.constants import (
    GITHUB_PROMPT_VERSION, MANAGED_BY_GITCURATOR, NOTE_SCHEMA_VERSION,
    OWNERSHIP_BANNER,
)

# ---------------------------------------------------------------------------
# Sanitizers
# ---------------------------------------------------------------------------

# Characters allowed inside a YAML flow-sequence item (tags / aliases /
# languages). Everything else is stripped. This is deliberately tight:
# no brackets, braces, commas, colons, quotes, hashes, backslashes, or
# control characters — nothing that can escape the sequence or start a
# comment.
_SEQ_ITEM_OK = re.compile(r'[^A-Za-z0-9 \-_.+/()]')
_MAX_SEQ_ITEM = 60       # per tag/alias/language
_MAX_TAGS = 12
_MAX_ALIASES = 4
_MAX_LANGUAGES = 8
_MAX_SCALAR = 120        # org / category / other scalars
_MAX_SUMMARY = 2000      # TL;DR one-liner


def sanitize_seq_item(value, max_len: int = _MAX_SEQ_ITEM) -> str:
    """Sanitize one item destined for a YAML flow sequence.

    Strips YAML-significant characters, control chars and newlines,
    collapses whitespace, caps length. Returns '' when nothing safe is left.
    """
    if value is None:
        return ''
    text = str(value)
    text = _SEQ_ITEM_OK.sub(' ', text)
    text = re.sub(r'\s+', ' ', text).strip().strip('-').strip()
    return text[:max_len].strip()


def sanitize_tags(tags, max_items: int = _MAX_TAGS) -> List[str]:
    """Sanitize a tag list (LLM output + GitHub topics merged upstream).

    Dedupes (case-insensitive), drops empties, caps the item count.
    """
    if not tags:
        return []
    if isinstance(tags, str):
        tags = [tags]
    seen = set()
    result = []
    for t in tags:
        clean = sanitize_seq_item(t)
        if not clean:
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(clean)
        if len(result) >= max_items:
            break
    return result


def sanitize_aliases(*names) -> List[str]:
    """Sanitize alias entries (repo short name, owner/repo, ...)."""
    seen = set()
    result = []
    for n in names:
        clean = sanitize_seq_item(n, max_len=80)
        if not clean or clean.lower() in seen:
            continue
        seen.add(clean.lower())
        result.append(clean)
        if len(result) >= _MAX_ALIASES:
            break
    return result


def sanitize_languages(languages) -> List[str]:
    """Sanitize the languages list for the YAML flow sequence."""
    if not languages:
        return []
    out = []
    for lang in languages:
        clean = sanitize_seq_item(lang, max_len=40)
        if clean and clean not in out:
            out.append(clean)
        if len(out) >= _MAX_LANGUAGES:
            break
    return out


def yaml_scalar(value, max_len: int = _MAX_SCALAR) -> str:
    """Render a value as a SAFE, always-double-quoted YAML scalar.

    Double-quoting with backslash escaping is bulletproof against
    injection: no unquoted special can leak through, and embedded
    quotes/backslashes/newlines are escaped per the YAML spec.
    """
    if value is None:
        return '""'
    text = str(value).replace('\r', ' ').replace('\n', ' ')
    text = re.sub(r'\s+', ' ', text).strip()
    text = text[:max_len]
    # Escape backslashes first, then double quotes.
    text = text.replace('\\', '\\\\').replace('"', '\\"')
    return f'"{text}"'


def sanitize_body_text(value, max_len: Optional[int] = None) -> str:
    """Sanitize a multi-line markdown BODY field (summary, how_it_works...).

    Body text is plain markdown — the only hard rule is that it must not
    be able to re-open YAML frontmatter. In a note the frontmatter is at
    the very top of the file, so body text cannot affect parsing; we still
    normalize control characters and cap runaway lengths.
    """
    if value is None:
        return ''
    text = str(value).replace('\r\n', '\n').replace('\r', '\n')
    # Strip control chars except \n and \t
    text = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', text)
    text = text.strip()
    if max_len:
        text = text[:max_len]
    return text


def sanitize_short_summary(value) -> str:
    """One-liner TL;DR — collapse to a single line, cap length."""
    if value is None:
        return ''
    text = str(value).replace('\r', ' ').replace('\n', ' ')
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:_MAX_SUMMARY]


# ---------------------------------------------------------------------------
# Reputation / rating helpers (moved verbatim from ProcessingWorker)
# ---------------------------------------------------------------------------

def rep_to_str(rep) -> str:
    if rep >= 8:
        return "High (Major tech company)"
    elif rep >= 6:
        return "Medium (Well-known organization)"
    elif rep >= 4:
        return "Low (Individual or unknown)"
    else:
        return "Unknown"


def score_to_rating(score) -> str:
    if score >= 90:
        return "Excellent"
    elif score >= 75:
        return "Good"
    elif score >= 60:
        return "Average"
    else:
        return "Low"


# ---------------------------------------------------------------------------
# Note builder
# ---------------------------------------------------------------------------

def build_note(url, repo_name, owner, org_name, stars, forks, commit_count,
               cred_score, org_rep, summary, tags, category_key, confidence,
               how_it_works, core_value, features, difference, banner_path=None,
               primary_language="", languages=None, short_summary="",
               latest_release_date="",
               quality_issues=None, is_low_quality=False) -> str:
    """Build the full note content (frontmatter + markdown body).

    Mirrors the original ProcessingWorker._build_note, with every
    YAML-bound value routed through the sanitizers above.

    Callers already enforce: category_key is from the CATEGORY_KEYS
    whitelist, filename components via storage.safe_filename().
    """
    # ---- sanitize everything that touches YAML frontmatter ----
    tags = sanitize_tags(tags)
    aliases = sanitize_aliases(repo_name, f"{owner}/{repo_name}")
    languages = sanitize_languages(languages or [])
    org_yaml = yaml_scalar(org_name)
    url_yaml = yaml_scalar(url)
    category_yaml = yaml_scalar(category_key)
    lang_scalar = sanitize_seq_item(primary_language, max_len=40)
    release_scalar = sanitize_seq_item(latest_release_date, max_len=32)

    # ---- body fields ----
    summary = sanitize_body_text(summary)
    how_it_works = sanitize_body_text(how_it_works)
    core_value = sanitize_body_text(core_value)
    difference = sanitize_body_text(difference)
    short_summary = sanitize_short_summary(short_summary)
    repo_title = sanitize_short_summary(repo_name)[:120] or "Untitled"
    owner_label = sanitize_short_summary(owner)[:120]

    if isinstance(features, list):
        feats = [sanitize_body_text(f, max_len=300) for f in features if f]
        feats = [f for f in feats if f.strip()]
        features_list = "\n".join([f"- {f}" for f in feats]) or "- (none listed)"
    else:
        features_list = sanitize_body_text(features) or "(none listed)"

    if languages is None:
        languages = []

    # ---- quality issues (v22 Feature 5) ----
    if quality_issues is None:
        quality_issues = []
        if len(summary) < 50:
            quality_issues.append("summary too short")
        if isinstance(features, list) and len(features) < 3:
            quality_issues.append("fewer than 3 features")
        if confidence < 30:
            quality_issues.append("low confidence")
        if category_key == "Uncategorized":
            quality_issues.append("uncategorized")
        if not is_low_quality:
            is_low_quality = len(quality_issues) > 0
    quality_issues = [sanitize_seq_item(q, max_len=40) for q in quality_issues]
    quality_issues = [q for q in quality_issues if q]

    # ---- banner ----
    banner_cover_line = ""
    banner_section = ""
    if banner_path:
        # banner filenames are generated by storage.safe_filename() upstream —
        # accept only a strictly safe charset, else drop the reference.
        raw_name = str(banner_path).replace('\\', '/').split('/')[-1]
        if re.fullmatch(r'[A-Za-z0-9\-_.]+', raw_name):
            banner_cover_line = f"cover: attachments/banners/{raw_name}"
            banner_section = f"![banner](attachments/banners/{raw_name})\n\n"

    # ---- frontmatter lines ----
    aliases_yaml = "aliases:\n" + "".join(f"  - {a}\n" for a in aliases) if aliases else "aliases: []"
    tags_yaml = "tags: [" + ", ".join(tags) + "]" if tags else "tags: []"
    languages_yaml = "languages: [" + ", ".join(languages) + "]" if languages else ""
    lang_line = f"primary_language: {lang_scalar}" if lang_scalar else ""
    last_release_line = f"last_release: {release_scalar}" if release_scalar else ""

    quality_lines = ""
    if is_low_quality:
        issues_str = ", ".join(quality_issues)
        quality_lines = f"quality: low\nquality_issues: [{issues_str}]"

    short_summary_section = f"> **TL;DR:** {short_summary}\n\n" if short_summary else ""

    frontmatter = f"""---
source: {url_yaml}
{aliases_yaml}
{tags_yaml}
category: {category_yaml}
stars: {int(stars) if isinstance(stars, (int, float)) else 0}
org: {org_yaml}
{lang_line}
{languages_yaml}
credibility_score: {cred_score}/100
date_processed: {datetime.now().strftime("%Y-%m-%d")}
{last_release_line}
{banner_cover_line}
{quality_lines}
managed_by: {MANAGED_BY_GITCURATOR}
schema_version: {NOTE_SCHEMA_VERSION}
prompt_version: {GITHUB_PROMPT_VERSION}
---

{banner_section}{OWNERSHIP_BANNER}

# {repo_title}

{short_summary_section}**`{owner_label}/{repo_title}`** · ⭐ {stars:,} · 🔧 {lang_scalar or 'N/A'}

## What is it?
{summary}

## How does it work?
{how_it_works}

## Why is it important? (Core Value)
{core_value}

## Key Features & Technologies
{features_list}

## Difference from Others
{difference}

## 🏢 Organization & Credibility
- **Developer:** {org_name}
- **Reputation:** {rep_to_str(org_rep)}
- **Stars:** {stars:,}
- **Forks:** {forks}
- **Recent Activity:** {commit_count} commits in 3 months
- **Credibility Score:** {cred_score}/100 ({score_to_rating(cred_score)})
- **Languages:** {', '.join(languages) if languages else 'N/A'}
- **Last Release:** {latest_release_date or 'No releases'}
- **Quality:** {'⚠️ low (' + ', '.join(quality_issues) + ')' if is_low_quality else '✅ good'}

---
*Source: [GitHub]({url})*

*Not useful anymore? Tag this note 🗑️ or delete / auto_delete —
typed inline anywhere in the note (Obsidian's own tag syntax), added
in the tags property, or moved into the Trash folder — the next run
counts it, asks you to confirm on Telegram, and on your 🗑️ Delete it
leaves the library and this repo is never fetched or counted again
(v0.65.0).*
"""
    return frontmatter


# ===========================================================================
# v0.20.0 — Missing-repo placeholder notes (the 404 fix)
# ===========================================================================

def build_missing_repo_note(url: str, owner: str, repo: str,
                            strikes: int = 1) -> str:
    """The ``_missing/`` placeholder note for a 404 GitHub repo.

    The owner's ask: "create a note in vault for missing github, and
    model reads them before again trying to process them" — the
    ``source:`` frontmatter key below IS the VaultIndex dedupe key, so
    with this note in the vault the repo stops counting as pending in
    every queue view (the vault is the ground truth) and never reaches
    the GitHub API again ("or something faster" — no LLM, no API call,
    one dict lookup).

    Deliberately NOT a managed content note: no OWNERSHIP_BANNER and no
    ``category:`` — note_state skips the ``_missing`` folder entirely
    (deleting this note must NEVER be read as "dismiss the repo"; it is
    the re-check trigger), and the Library mirror never copies it.
    """
    today = datetime.now().strftime('%Y-%m-%d')
    clean_repo = str(repo or '').strip()
    if clean_repo.endswith('.git'):
        clean_repo = clean_repo[:-len('.git')]
    return f"""---
source: {url}
repo: {owner}/{clean_repo}
status: missing
reason: 404 Not Found (deleted or private)
strikes: {strikes}
date_recorded: {today}
placeholder: missing-repo
---

# {owner}/{clean_repo} — missing (404)

The repository **{url}** returned **404 Not Found** — it was deleted or
made private. GitCurator recorded this placeholder so the link:

- no longer counts as *remaining to be processed* in any queue view,
- is never fetched from the GitHub API again (404 quarantine confirmed).

**To re-check this repo later** (restored / renamed / made public):

1. Delete this note (the file you are reading).
2. More ▸ View 404 Quarantine → reset this URL
   (or the CLI: `python main.py --cli --reset-dead "{url}"`).
3. Run the next batch — the repo is processed like new.

*Recorded {today} after {strikes} confirmed 404(s). The x/y links of this
repo, if any, live on in the bot chat; nothing was lost.*
"""
