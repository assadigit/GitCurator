"""GitCurator — Telegram → Ollama → Obsidian GitHub repo curator.

v32 modular package layout:

    core          pure-stdlib testable core (links, storage,
                  note_builder, llm_client)
    integrations  telegram fetching, vaultseal, goodrepos,
                  error reporting
    cloud         cloudflare + gdrive integrations
    gui           the PyQt6 application (app.py: MainWindow,
                  pipeline, headless CLI)
    tools         developer utilities

Entry points (unchanged): ``python main.py`` (GUI) and
``python main.py --headless …`` — both land in
:func:`gitcurator.gui.app.main`.

Design discipline (v30 lineage): the four core modules plus every
integration stay pure-stdlib so the CI gate (py_compile + unittest)
never needs pip. GUI-only code lives in gitcurator/gui/app.py.
"""

__version__ = "32.0"
