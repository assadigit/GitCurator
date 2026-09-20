"""gitcurator.utils — small cross-cutting utilities.

Currently: logging_setup (rotating console/file logs) and terminal
(guarded colorama import). Extracted from gui/app.py in the v32.3
modularization. Pure stdlib; safe to import from the headless CLI,
the GUI and the subprocess workers alike.
"""
