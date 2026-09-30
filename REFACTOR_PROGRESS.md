# REFACTOR_PROGRESS — live log for the app.py split

Branch `refactor/gui-app-split` · baseline tag `baseline-before-split`.
Plan: see REFACTOR_PLAN.md. Issues noticed along the way: REFACTOR_NOTICED_ISSUES.md.

## Phase 0 — safety net (DONE, 2026-09-30)

- Fresh clone @ 0d6746c (`main`), branch + baseline tag created; tree clean.
- Environment: `/home/z/gc-venv` (Python 3.12.14, app requirements),
  Qt offscreen libs via `apt-get download` + `dpkg -x`
  (libegl1 libgl1 libxkbcommon0 libdbus-1-3 libfontconfig1
  libglib2.0-0t64 → `/home/z/gc-qtlibs`), runner `/home/z/gc-run.sh`.
- Baseline gate: 772/772 tests OK **twice** (60.8s, 62.3s); offline golden
  30/30, 0 invalid categories; `import gitcurator.gui.app` 1.00s.
- `.refactor-scratch/` tooling built and **failure-proved**:
  fingerprint (mutA body-edit → FAIL), surface (mutB rename → FAIL),
  import (mutC syntax error → FAIL).
- Baseline facts: surface = 722 attrs; 365 functions fingerprinted;
  `gitcurator.tools.test` fails to import at baseline (Telethon env
  validation) — pre-existing, allowlisted.
- Characterization tests `app/tests/test_refactor_surface.py`: 24 cases,
  pass on unmodified code; full suite with them = **796/796 OK**.

## Phase 1 — ast analysis (DONE)

13815 lines · 22 top-level defs · 9 classes (MainWindow 8060 ln / 146
methods, ProcessingWorker 3245 ln, LinkTracker 405 ln, CacheDB 462 ln) ·
31 import statements · 17 other top-level statements · 6 module vars ·
**0 `global` declarations** · **0 dynamic references** (empty dynamic.txt).
Dependency graph acyclic and cleanly layered (see `.refactor-scratch/deps.json`).

## Phase 2 — plan (DONE)

REFACTOR_PLAN.md written. Owner approved defaults: mixins YES, patch-target
updates YES, CI edit YES, modules under gui/, docs untouched, per-module
commits. Owner then said: "Push and commit, then release new version so i
can test." — proceed through Phase 3/4 and release v0.24.0.

## Phase 3 — extractions

| # | Commit | Module | Gate |
|---|---|---|---|
| 0 | docs + characterization tests (796/796) | tests.test_refactor_surface joins CI | ✅ |
