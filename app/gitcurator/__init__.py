"""GitCurator — Telegram → Ollama → Obsidian GitHub repo curator.

v32.4 modular package layout:

    core          pure-stdlib testable core (links, storage,
                  note_builder, llm_client, vault, cache_db,
                  link_tracker, inbox)
    integrations  telegram fetching, vaultseal, goodrepos,
                  error reporting
    cloud         cloudflare + gdrive integrations
    gui           the PyQt6 application: cli.py (argparse-first entry),
                  gui/app.py (back-compat facade),
                  gui/main_window/ (MainWindow assembled from
                  single-concern mixins), gui/workers/ (the pipeline
                  QThread assembled from phase mixins),
                  gui/_qt.py (guarded PyQt6 import site)
    tools         developer utilities

Entry points (all equivalent): ``python main.py``,
``python -m gitcurator`` and ``python -m gitcurator.cli`` — GUI with no
args, headless with ``--headless …``; ``--help`` is answered by argparse
BEFORE any PyQt6 import.

Design discipline (v30 lineage): the core modules plus every
integration stay pure-stdlib so the CI gate (py_compile + unittest)
never needs pip. GUI-only code lives under gitcurator/gui/.
"""

__version__ = "32.4"
