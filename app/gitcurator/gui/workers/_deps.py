#!/usr/bin/env python3
"""gitcurator.gui.workers._deps — single site of the worker dependency imports.

Every ``gui/workers/`` submodule star-imports this module, so the guarded
PyGithub / ollama imports (with their original messages + ``exit(1)``) live
in exactly ONE place — the single-site pattern ``gui/_qt.py`` established
for PyQt6 in the v32.3 modularization, applied to the pipeline deps.

Importing ANY worker submodule therefore prints the same dependency error
the monolith printed, before any worker code runs.
"""

import json
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta
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
    CATEGORY_FOLDERS, CATEGORY_KEYS, DEFAULT_SYSTEM_PROMPT, resolve_app_path,
)
from gitcurator.core.links import clean_url
from gitcurator.core import storage as _storage
from gitcurator.core import note_builder as _note_builder
from gitcurator.core import llm_client as _llm_client
from gitcurator.core.vault import VaultIndex, _safe_moc_name
from gitcurator.core.cache_db import CacheDB
from gitcurator.core.link_tracker import LinkTracker
from gitcurator.core.inbox import (
    PLATFORM_INFO, classify_platform, write_inbox_links_by_platform,
)
from gitcurator.integrations.telegram_jobs import _run_telegram_worker


__all__ = [
    # stdlib shared by the worker modules
    "json", "logging", "os", "re", "sys", "threading", "time",
    "datetime", "timedelta",
    # guarded third-party
    "github", "Github", "GithubException", "Auth", "ollama",
    # app constants + core
    "CATEGORY_FOLDERS", "CATEGORY_KEYS", "DEFAULT_SYSTEM_PROMPT",
    "resolve_app_path", "clean_url", "_storage", "_note_builder",
    "_llm_client", "VaultIndex", "_safe_moc_name", "CacheDB",
    "LinkTracker", "PLATFORM_INFO", "classify_platform",
    "write_inbox_links_by_platform", "_run_telegram_worker",
]
