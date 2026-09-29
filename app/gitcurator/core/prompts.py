#!/usr/bin/env python3
"""
prompts.py — template loader for the Phase 2 website prompts (and any
future prompt set that follows the same contract).

SPEC Appendix A: prompts live as files in ``app/prompts/``; ``{{SLOT}}``
marks a value the app fills, and the loader MUST REFUSE TO RUN when any
slot is left unfilled — a half-filled prompt quietly sent to a model is
how a taxonomy name or a URL goes missing in production.

Three call styles:

    load_prompt_file('w01_category.txt', URL=..., TITLE=...)   # by filename
    fill_template(open(...).read(), URL=...)                   # raw text
    load_prompt('w01_category', URL=...)                       # by stem

Pure stdlib, no PyQt.
"""

import os
import re
from typing import Dict

# The prompt folder ships with the app (.../app/prompts — constants.APP_DIR
# is .../app). Kept local (not in constants) because only this module and
# the website pipeline need it.
from gitcurator.constants import APP_DIR

PROMPTS_DIR = os.path.join(APP_DIR, "prompts")

# {{THIS_STYLE}} slot — upper-case letters, digits and underscores.
_SLOT_RE = re.compile(r'\{\{([A-Z][A-Z0-9_]*)\}\}')


class PromptError(Exception):
    """A prompt template is missing or was left partially unfilled."""


def slots_in(template: str):
    """The ordered, de-duplicated slot names a template declares."""
    seen = []
    for name in _SLOT_RE.findall(template or ''):
        if name not in seen:
            seen.append(name)
    return seen


def fill_template(template: str, **values: str) -> str:
    """Fill every ``{{SLOT}}`` in ``template``.

    Raises PromptError when:
      - a slot has no value (or an empty value) — the caller must decide
        what to pass ("unknown" is a legitimate value; "" is not), and
      - a value itself contains a ``{{SLOT}}`` marker (a model answer or
        page text could otherwise smuggle a new slot past validation).
    """
    if template is None:
        raise PromptError("no template given")

    missing = [name for name in slots_in(template)
               if name not in values or values[name] in (None, "")]
    if missing:
        raise PromptError(
            f"prompt slot(s) left unfilled: {', '.join(missing)}")

    for name, value in values.items():
        if value is None:
            raise PromptError(f"prompt slot {name} got None")
        text = str(value)
        if _SLOT_RE.search(text):
            raise PromptError(
                f"value for slot {name} contains a {{{{SLOT}}}} marker "
                f"— refusing to fill (untrusted input)")
        template = template.replace("{{" + name + "}}", text)

    leftovers = _SLOT_RE.findall(template)
    if leftovers:
        raise PromptError(
            f"prompt slot(s) left unfilled: {', '.join(leftovers)}")
    return template


def load_prompt_file(filename: str, **values: str) -> str:
    """Read ``app/prompts/<filename>`` (explicit UTF-8) and fill it."""
    if not filename or os.path.basename(filename) != filename:
        raise PromptError(f"bad prompt filename: {filename!r}")
    path = os.path.join(PROMPTS_DIR, filename)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            template = f.read()
    except OSError as exc:
        raise PromptError(f"cannot read prompt file {path!r}: {exc}")
    return fill_template(template, **values)


def load_prompt(stem: str, **values: str) -> str:
    """``load_prompt('w01_category', ...)`` -> app/prompts/w01_category.txt"""
    return load_prompt_file(stem + ".txt", **values)
