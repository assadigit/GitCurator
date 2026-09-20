#!/usr/bin/env python3
"""gitcurator.utils.logging_setup — rotating console/file logging.

Moved verbatim from ``gitcurator/gui/app.py`` (v32.3 modularization).
v32.3 fix: ``log_dir`` defaults to ``<APP_DIR>/logs`` (was CWD-relative,
so logs scattered wherever the process happened to start).
"""

import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler

from gitcurator.constants import APP_DIR

__all__ = ["setup_logging"]



# ============================================================================
# Logging Setup
# ============================================================================

def setup_logging(log_level="INFO", log_dir=None):
    # v32.3 fix: "logs" was CWD-relative; default now anchors to APP_DIR
    # (same directory the documented app/-cwd behavior always used).
    if log_dir is None:
        log_dir = os.path.join(APP_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file_md = os.path.join(log_dir, f"processing_{timestamp}.md")
    log_file_txt = os.path.join(log_dir, f"processing_{timestamp}.txt")

    logger = logging.getLogger()
    logger.setLevel(getattr(logging, log_level.upper()))

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG)
    logger.addHandler(console)

    file_md = RotatingFileHandler(log_file_md, maxBytes=5*1024*1024, backupCount=5)
    file_md.setLevel(logging.INFO)
    logger.addHandler(file_md)

    file_txt = RotatingFileHandler(log_file_txt, maxBytes=5*1024*1024, backupCount=5)
    file_txt.setLevel(logging.DEBUG)
    logger.addHandler(file_txt)

    return logger
