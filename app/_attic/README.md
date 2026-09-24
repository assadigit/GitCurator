# _attic — retired files (v0.06 tidy-up)

These files are NOT used by the application at runtime. They were moved
here from `app/` so the active tree contains only live code:

- `screenshot_v*.py` — one-off GUI screenshot scripts from past releases
  (hardcoded output paths from the dev machine).
- `tests_smoke_gui_v33.py` — superseded by the automated GUI smoke checks
  documented in the v0.06 CHANGELOG (offscreen MainWindow construction,
  lock-manager semantics, hung-worker recovery).
- `01_categorize.txt / 02_summarize.txt / 03_crosscheck.txt` — byte-identical
  duplicates of `app/prompts/*`; neither set is read by the code (the live
  prompt is `DEFAULT_SYSTEM_PROMPT` in `gitcurator/constants.py`). The
  canonical copies stay in `app/prompts/`.

Nothing imports from this folder. Delete it freely.
