#!/usr/bin/env python3
"""
taxonomy.py — parse and validate the Websites category taxonomy (Phase 2).

The owner's ``website-library-categories.md`` (app/taxonomy/, overridable via
``taxonomy_path`` in config) is the SOURCE OF TRUTH for the Websites vault's
folder structure and the classifier's allowed answers (SPEC §4.6). It is not
documentation — this module turns it into data:

    ### <Category>  — heading, emoji + trailing italic notes stripped
    - **Subcat** — bullet, bold markers + everything after the em dash stripped
    Example tags: `#a` `#b` — tag hints per category
    *(no subcategories)* — marker, the rest of the line is a definition
    prose paragraph under a heading — one-line definition of the category
    "Judgment-call rules" section — passed VERBATIM to the classifier
    "How this works" / "Not yet covered" — ignored entirely

Everything here is pure stdlib, no PyQt, and untrusted-input safe: model
answers are validated against parsed names before they can touch a path.

Parsing contract (SPEC §4.6, "be tolerant; the file is written for humans"):
- a category name is the heading text with the emoji and any trailing
  ``*(...)*`` italic note removed;
- a subcategory is a top-level bullet with bold markers stripped and
  everything from the em dash ``—`` onward removed;
- ``(no subcategories)`` and ``(empty for now …)`` notes never remove real
  subcategory bullets listed under the same heading;
- ``Example tags:`` lines are tag hints for the prompt, never folders;
- an unparseable file raises TaxonomyError — the pipeline refuses to run
  rather than guessing (SPEC stop-condition: "the taxonomy file cannot be
  parsed unambiguously").
"""

import os
import re
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TaxonomyError(Exception):
    """The taxonomy file could not be parsed unambiguously."""


# ---------------------------------------------------------------------------
# Line-level helpers
# ---------------------------------------------------------------------------

# Emoji / non-latin symbol prefix on a category heading ("### Design 🌐").
_EMOJI_RE = re.compile(
    '[\U0001F000-\U0001FAFF\u2600-\u27BF\u2190-\u21FF\u2B00-\u2BFF'
    '\uFE0F\u200D]')

# Trailing italic note: "*(...)*" possibly with a leading space.
_TRAILING_ITALIC_RE = re.compile(r'\s*\*\(.*\)\*\s*$')

# A bullet line at the top level ("- **Assets & Resources** — desc").
_BULLET_RE = re.compile(r'^\s*-\s+(.*)$')

# "Example tags:" hint line.
_EXAMPLE_TAGS_RE = re.compile(r'^\s*Example tags:\s*(.*)$', re.IGNORECASE)

# The "(no subcategories)" marker inside a note line.
_NO_SUBCATEGORIES_RE = re.compile(r'\(\s*no subcategories\s*\)', re.IGNORECASE)

# Unicode em dash and friends that separate a name from its description.
_DASH_SPLIT_RE = re.compile(r'\s*[—–]\s*')


def _strip_category_heading(text: str) -> str:
    """'Design 🌐 *(Assets & Resources … are public-directory candidates)*'
    -> 'Design'. Emoji first (it sits before the italic note)."""
    text = _TRAILING_ITALIC_RE.sub('', text or '')
    text = _EMOJI_RE.sub('', text)
    return text.strip()


def _strip_subcategory(text: str) -> str:
    """'**Assets & Resources** — downloadable/usable design assets: …'
    -> 'Assets & Resources' (bold stripped, em-dash tail dropped)."""
    text = (text or '').strip()
    text = text.replace('**', '')
    text = _DASH_SPLIT_RE.split(text, maxsplit=1)[0]
    return text.strip()


def _strip_tags_line(text: str) -> List[str]:
    """'`#icons` `#mockup` `#free`' -> ['icons', 'mockup', 'free']."""
    return [t.lstrip('#').strip() for t in re.findall(r'#([A-Za-z0-9\-_]+)',
                                                      text or '')]


# ---------------------------------------------------------------------------
# The taxonomy model
# ---------------------------------------------------------------------------


class Category:
    """One parsed category: name, one-line definition, subcategories,
    tag hints. Folder-safe names are derived on demand (safe_filename)."""

    def __init__(self, name: str, definition: str = "",
                 subcategories: Optional[List[str]] = None,
                 subcategory_definitions: Optional[Dict[str, str]] = None,
                 tag_hints: Optional[List[str]] = None):
        self.name = name
        self.definition = (definition or "").strip()
        self.subcategories = list(subcategories or [])
        self.subcategory_definitions = dict(subcategory_definitions or {})
        self.tag_hints = list(tag_hints or [])

    def __repr__(self):
        return (f"<Category {self.name!r} subcats={self.subcategories} "
                f"tags={len(self.tag_hints)}>")

    @property
    def one_line(self) -> str:
        """'Category — definition' for pass 1 of the classifier."""
        if self.definition:
            return f"{self.name} — {self.definition}"
        return self.name


class Taxonomy:
    """Parsed taxonomy: ordered categories + the verbatim judgment rules."""

    def __init__(self, categories: List[Category], judgment_rules: str,
                 source_path: str = ""):
        self.categories = categories
        self.judgment_rules = judgment_rules.strip()
        self.source_path = source_path
        self._by_name = {c.name: c for c in categories}
        if len(self._by_name) != len(categories):
            raise TaxonomyError("duplicate category name in taxonomy file")

    # -- queries used by the pipeline -------------------------------------

    @property
    def category_names(self) -> List[str]:
        return [c.name for c in self.categories]

    def category(self, name: str) -> Optional[Category]:
        """Exact-match lookup (model answers must match a parsed name)."""
        return self._by_name.get((name or "").strip())

    def is_category(self, name: str) -> bool:
        return (name or "").strip() in self._by_name

    def subcategories_of(self, category_name: str) -> List[str]:
        cat = self.category(category_name)
        return list(cat.subcategories) if cat else []

    def is_subcategory_of(self, category_name: str, subcategory: str) -> bool:
        sub = (subcategory or "").strip()
        return sub != "" and sub in self.subcategories_of(category_name)

    def category_one_liner(self) -> str:
        """Pass-1 payload: category names + one-line definitions."""
        return "\n".join(f"- {c.one_line}" for c in self.categories)

    def subcategory_one_liner(self, category_name: str) -> str:
        """Pass-2 payload: the chosen category's subcategories (or none)."""
        cat = self.category(category_name)
        if cat is None:
            return ""
        if not cat.subcategories:
            return "- none (this category has no subcategories)"
        lines = []
        for s in cat.subcategories:
            d = cat.subcategory_definitions.get(s, "")
            lines.append(f"- {s} — {d}" if d else f"- {s}")
        return "\n".join(lines)

    def tag_hints_of(self, category_name: str) -> str:
        cat = self.category(category_name)
        return " ".join(f"#{t}" for t in cat.tag_hints) if cat else ""

    # -- folders -----------------------------------------------------------

    def folder_relpath(self, category_name: str,
                       subcategory: Optional[str] = None) -> str:
        """Vault-relative folder for a (validated) category/subcategory.
        Names go through safe_filename (category names contain '&' and '/').
        An empty/None subcategory means the category folder itself."""
        from gitcurator.core.storage import safe_filename
        cat = self.category(category_name)
        if cat is None:
            raise TaxonomyError(
                f"unknown category {category_name!r} — refusing to build a path")
        folder = safe_filename(cat.name)
        if subcategory:
            if not self.is_subcategory_of(category_name, subcategory):
                raise TaxonomyError(
                    f"unknown subcategory {subcategory!r} for "
                    f"{category_name!r} — refusing to build a path")
            folder = os.path.join(folder, safe_filename(subcategory))
        return folder

    # -- summary (logs / reports) ------------------------------------------

    def summary(self) -> str:
        n_sub = sum(len(c.subcategories) for c in self.categories)
        return (f"{len(self.categories)} categories, {n_sub} subcategories "
                f"from {self.source_path or '(inline)'}")


# ---------------------------------------------------------------------------
# The parser
# ---------------------------------------------------------------------------

# Sections ignored completely (SPEC §4.6).
_IGNORED_SECTIONS = {"How this works", "Not yet covered"}
_RULES_SECTION = "Judgment-call rules"


def parse_taxonomy(text: str, source_path: str = "") -> Taxonomy:
    """Parse the taxonomy markdown into a Taxonomy. Raises TaxonomyError
    when the file has no categories at all (clearly not a taxonomy)."""
    categories: List[Category] = []
    rules_lines: List[str] = []
    section = None            # current ## section title (lower-cased)
    current: Optional[Category] = None
    saw_definition = False    # current category already has a prose line

    for raw_line in (text or "").splitlines():
        line = raw_line.rstrip()

        # --- structural lines ---------------------------------------------
        if not line.strip():
            saw_definition = saw_definition  # blank lines keep state
            continue

        h3 = re.match(r'^###\s+(.*)$', line)
        if h3:
            name = _strip_category_heading(h3.group(1))
            if not name:
                raise TaxonomyError(
                    f"category heading without a name: {line!r}")
            current = Category(name)
            categories.append(current)
            saw_definition = False
            continue

        h2 = re.match(r'^##\s+(.*)$', line)
        if h2:
            section = h2.group(1).strip().lower()
            current = None            # a ## ends the current category block
            continue

        if line.strip() == '---':
            continue

        if section and section.lower() in {s.lower()
                                           for s in _IGNORED_SECTIONS}:
            continue                  # ignored section, skip wholesale

        # --- inside the judgment-rules section ------------------------------
        if section and section.lower() == _RULES_SECTION.lower():
            m = _BULLET_RE.match(line)
            if m:
                rules_lines.append(m.group(1).strip())
            elif line.strip() and not line.startswith('*('):
                rules_lines.append(line.strip())
            continue

        # --- category body lines -------------------------------------------
        # An "## Categories"-level line is only meaningful when a category is
        # open; stray top-level lines elsewhere are ignored (tolerant parse).
        if current is None:
            continue

        tags_m = _EXAMPLE_TAGS_RE.match(line)
        if tags_m:
            current.tag_hints.extend(_strip_tags_line(tags_m.group(1)))
            continue

        bullet_m = _BULLET_RE.match(line)
        if bullet_m:
            body = bullet_m.group(1).strip()
            sub_name = _strip_subcategory(body)
            if not sub_name or sub_name.lower().startswith('(empty for now'):
                continue
            # A bullet repeats the "(no subcategories)" marker in some
            # categories — never a subcategory.
            if _NO_SUBCATEGORIES_RE.search(body):
                continue
            if sub_name not in current.subcategories:
                current.subcategories.append(sub_name)
                desc = _DASH_SPLIT_RE.split(body.replace('**', ''), maxsplit=1)
                if len(desc) == 2 and desc[1].strip():
                    current.subcategory_definitions[sub_name] = \
                        desc[1].strip()
            continue

        # Prose paragraph: first one is the category's definition. A leading
        # "*(no subcategories)*" marker is stripped off it.
        prose = line.strip()
        prose = _NO_SUBCATEGORIES_RE.sub('', prose).strip()
        prose = re.sub(r'^[—–\-]\s*', '', prose).strip()
        if prose and not prose.startswith('*(') and not saw_definition:
            current.definition = prose
            saw_definition = True
        elif prose and prose.startswith('*('):
            # "(empty for now …)" style note — use as definition when the
            # category has none yet.
            inner = re.sub(r'^\*\((.*)\)\*$', r'\1', prose).strip()
            if inner and not current.definition:
                current.definition = inner

    if not categories:
        raise TaxonomyError(
            "no '### <Category>' headings found — not a taxonomy file")

    return Taxonomy(categories, "\n".join(rules_lines), source_path)


def load_taxonomy(path: str) -> Taxonomy:
    """Read + parse the taxonomy file. Explicit UTF-8 (owner is on Windows;
    a BOM would otherwise corrupt the first heading)."""
    try:
        with open(path, 'r', encoding='utf-8-sig') as f:
            text = f.read()
    except OSError as exc:
        raise TaxonomyError(f"cannot read taxonomy file {path!r}: {exc}")
    return parse_taxonomy(text, source_path=path)


def load_taxonomy_from_config(config: Optional[dict] = None) -> Taxonomy:
    """The effective taxonomy: the config override, else the bundled file."""
    from gitcurator.constants import resolve_taxonomy_path
    return load_taxonomy(resolve_taxonomy_path(config))
