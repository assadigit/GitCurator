#!/usr/bin/env python3
"""gitcurator.gui.main_window._deps — single site of the MainWindow imports.

Every ``gui/main_window/`` submodule star-imports this module, so the
guarded PyGithub / ollama imports (original messages + ``exit(1)``) and
the core/integration imports live in ONE place — the single-site pattern
``gui/_qt.py`` established for PyQt6 (v32.3), applied here too.
"""

import html as _html_module
import json
import os
import re
import subprocess
import sys
import sys as _sys  # historical version-stamp alias used by _load_fonts
from datetime import datetime
from typing import Dict, List, Optional
try:
    import github
    from github import Github, GithubException, Auth
except ImportError:
    print("PyGithub is not installed. Please run: pip install PyGithub")
    sys.exit(1)
try:
    import ollama
except ImportError:
    print("Ollama Python client is not installed. Please run: pip install ollama")
    sys.exit(1)
from gitcurator.constants import (
    APP_DIR, COLORS, CONFIG_FILE, CONFIG_EXAMPLE, CATEGORY_FOLDERS,
    CATEGORY_KEYS,
)
from gitcurator.core.links import normalize_url
from gitcurator.core import storage as _storage
from gitcurator.core import llm_client as _llm_client
from gitcurator.core.vault import VaultIndex, find_obsidian_vaults
from gitcurator.core.cache_db import CacheDB
from gitcurator.core.link_tracker import LinkTracker
from gitcurator.core.inbox import write_inbox_links_by_platform
from gitcurator.integrations import vaultseal as _vaultseal
from gitcurator.integrations import goodrepos as _goodrepos
from gitcurator.integrations.telegram_jobs import (
    _telegram_test_job, _telegram_preview_job, _telegram_single_job,
    _telegram_keyword_job, _bot_queue_job,
)
from gitcurator.gui.workers import ProcessingWorker, TestWorker
from gitcurator.utils.terminal import Fore

_APP_DIR = APP_DIR  # assets/fonts anchor (unchanged behavior)


__all__ = [
    # stdlib + aliases
    "json", "os", "re", "subprocess", "sys", "_sys", "_html_module",
    "datetime", "Dict", "List", "Optional",
    # guarded third-party
    "github", "Github", "GithubException", "Auth", "ollama",
    # app constants
    "APP_DIR", "COLORS", "CONFIG_FILE", "CONFIG_EXAMPLE",
    "CATEGORY_FOLDERS", "CATEGORY_KEYS", "_APP_DIR",
    # core + integrations
    "normalize_url", "_storage", "_llm_client", "VaultIndex",
    "find_obsidian_vaults", "CacheDB", "LinkTracker",
    "write_inbox_links_by_platform", "_vaultseal", "_goodrepos",
    "_telegram_test_job", "_telegram_preview_job", "_telegram_single_job",
    "_telegram_keyword_job", "_bot_queue_job",
    # GUI workers + terminal colors
    "ProcessingWorker", "TestWorker", "Fore",
]
