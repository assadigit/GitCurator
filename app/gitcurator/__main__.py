#!/usr/bin/env python3
"""gitcurator.__main__ — the ``python -m gitcurator`` entry point (PEP 338).

Before this file existed, ``python -m gitcurator`` failed with
"No module named gitcurator.__main__" while ``python -m gitcurator.cli``,
``python main.py`` and ``python app/main.py`` all worked — an
inconsistent surface for the same application. This shim closes the gap:

    python -m gitcurator --help        (same as `python main.py --help`)
    python -m gitcurator --headless …  (same as `python main.py --headless …`)

It delegates to :func:`gitcurator.cli.main` — the same stdlib-only,
argparse-first entry the launcher shim uses, so ``--help`` still answers
BEFORE any PyQt6 import (see the v32.3 CLI hardening notes).
"""

from gitcurator.cli import main

if __name__ == "__main__":
    main()
