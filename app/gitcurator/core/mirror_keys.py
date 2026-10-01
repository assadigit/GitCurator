"""The shared ``mirror_of`` frontmatter key and its parsers.

Extracted from mirror.py (v0.26.0 — SWOT hardening) so the linking layer
can read ``mirror_of`` lines at import time without the lazy
mirror <-> linking import cycle: mirror imports linking for the Phase 6
``preserve_related_block`` carry-over hook, and linking used to import
these two helpers lazily *inside a function* to dodge the resulting
cycle. Both modules now depend on this leaf module only — the package
dependency graph is one-way again. The names are kept exactly as they
were in mirror.py (byte-for-byte move rule; mirror.py re-exports them
so every historical import path still works).
"""

import re

#: Front-matter key that marks a file as a machine-written mirror copy.
#: Only files carrying this marker are ever updated or deleted (and only
#: under Library/). SPEC §6 Phase 5.
MIRROR_KEY = "mirror_of"

_MIRROR_RE = re.compile(r'^' + re.escape(MIRROR_KEY) + r':\s*(.+)$',
                        re.MULTILINE)


def _unquote(value: str) -> str:
    return value.strip().strip('"\'')
