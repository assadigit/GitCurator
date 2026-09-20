#!/usr/bin/env python3
"""gitcurator.gui.workers — the QThread pipeline workers.

Public surface (unchanged since the v32.3 extraction from the
``gui/app.py`` monolith):

* :class:`ProcessingWorker` — the curation pipeline (now assembled from
  single-concern mixins in this package; method bodies are verbatim moves)
* :class:`TestWorker` — one blocking callable on a background thread

Importing this package triggers the guarded dependency imports exactly
once (see ``_deps.py`` and ``gitcurator/gui/_qt.py``).
"""

from gitcurator.gui.workers.processing import ProcessingWorker
from gitcurator.gui.workers.test_worker import TestWorker

__all__ = ["ProcessingWorker", "TestWorker"]
