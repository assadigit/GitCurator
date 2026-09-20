#!/usr/bin/env python3
"""gitcurator.utils.logging_setup — rotating console/file logging.

Moved verbatim from ``gitcurator/gui/app.py`` (v32.3 modularization).
Behavioral note kept for fidelity: ``log_dir`` defaults to ``"logs"``
resolved against the process CWD, exactly as in the monolith — launch
from ``app/`` (the documented usage) so logs land in ``app/logs/``.
"""

import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler

__all__ = ["setup_logging"]



# ============================================================================
# Logging Setup
# ============================================================================

def setup_logging(log_level="INFO", log_dir="logs"):
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
