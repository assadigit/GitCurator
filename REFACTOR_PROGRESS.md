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

## Phase 3 — extractions (DONE, 2026-09-30)

Every row = one commit, gated by: compile (all files) + fresh-process
import of every module + SURFACE (722/722) + FINGERPRINT (365 functions
verbatim exactly once) + full 796-case suite + offline golden (milestones).
"tests re-aimed" = approved patch-target updates (expectations unchanged).

| # | Commit | Module | ln | notes |
|---|---|---|---|---|
| 0 | 975729a | docs + tests.test_refactor_surface (24 cases) | — | suite 772 → 796 |
| 1 | 9b716eb | gui/link_helpers.py | 62 | extract/clean/normalize_url |
| 2 | c8dddf0 | gui/dead_links.py | 70 | DEAD_LINK_THRESHOLD + dead_link_threshold |
| 3 | 7e13362 | gui/telegram_lazy.py | 63 | the mutually-recursive lazy shims travel together |
| 4 | 64b4d14 | gui/platform_intake.py | 254 | PLATFORM_INFO + inbox routing |
| 5 | 07bb05c | gui/vault_index.py | 213 | VaultIndex + find_obsidian_vaults + _safe_moc_name |
| 6 | ecdafde | gui/cache_db.py | 508 | CacheDB |
| 7 | 6cd0a43 | gui/link_tracker.py | 449 | LinkTracker |
| 8 | 41abe47 | gui/log_bridge.py | 84 | _GuiLogHandler + install/remove |
| 9 | d8921de | gui/worker_jobs.py | 278 | tests re-aimed: intakefix `_run_telegram_worker` |
| 10 | 99aa7b2 | gui/processing_worker.py | 3386 | tests re-aimed: phase1/2 `Github` (was silently hitting the real API) |
| 11 | c676a2f | gui/dialogs.py | 290 | SettingsDialog + ConnectionTestDialog |
| 12 | 89b7d8e | gui/headless.py | 263 | run_headless + _is_process_running |
| 13 | 63fb00d | main_window/theme.py (ThemeMixin) | 917 | 19 methods |
| 14 | dcbfcf2 | main_window/ui.py (UiMixin) | 1494 | 8 methods; tests re-aimed: llamacpp/phase4/phase5 source scans |
| 15 | b2cd49c | main_window/hero.py (HeroMixin) | 236 | 6 methods |
| 16 | 4d29919 | main_window/connection_tests.py (ConnectionTestsMixin) | 566 | 11 methods |
| 17 | f583acf | main_window/llamacpp.py (LlamaCppMixin) | 591 | 12 methods; tests re-aimed: llamacpp `threading` recorder |
| 18 | 966c995 | main_window/phase6.py (Phase6Mixin) | 121 | 3 methods |
| 19 | 84d08b2 | main_window/test_connection_modal.py (TestConnectionModalMixin) | 254 | 4 methods |
| 20 | d1c1ced | main_window/vaults_config.py (VaultConfigMixin) | 379 | 12 methods; tests re-aimed: v0230 + intakefix `CONFIG_FILE` |
| 21 | 4217147 | main_window/input_proxy.py (InputProxyMixin) | 166 | 5 methods |
| 22 | cf61e46 | main_window/telegram_ui.py (TelegramUiMixin) | 242 | 5 methods |
| 23 | 5d74c00 | main_window/processing_control.py (ProcessingControlMixin) | 718 | 12 methods |
| 24 | 9cc747f | main_window/dashboard.py (DashboardMixin) | 714 | 10 methods |
| 25 | 07ca7f2 | main_window/bot_queue.py (BotQueueMixin) | 1213 | 13 methods |
| 26 | 112b005 | main_window/backup_seal.py (BackupSealMixin) | 851 | 20 methods |
| 27 | 7b85477 | main_window/lifecycle.py (LifecycleMixin) | 273 | 3 methods (closeEvent etc.) |
| 28 | 9eacd26 | main_window/window.py (MainWindow shell) | 233 | mixin composition + `__init__` + `_open_settings` + class signal |

## Phase 4 — closeout (DONE)

- app.py: **13,815 → 473 lines** (facade). 28 new modules; largest
  processing_worker.py 3,386; main_window/ui.py 1,494; bot_queue.py 1,213.
- Final verification on the exact final tree: FINGERPRINT PASS (365
  functions verbatim exactly once), SURFACE PASS (722/722), IMPORT PASS
  (76 modules), GATE PASS (**796/796** + offline golden **30/30**),
  offscreen smoke of the real composed MainWindow (construction, theming,
  logging, progress, hero state): **ALL SMOKE CHECKS PASSED**.
- ci.yml: compile list 38 → 67 modules; suite line gains
  tests.test_refactor_surface (796); comments updated.
- VERSION 0.23.0 → 0.24.0; STATUS.md phase row + version line;
  CHANGELOG 0.24.0 entry. Released as v0.24.0 per owner instruction
  ("Push and commit, then release new version so i can test.").
- Known harmless wrinkle: `gitcurator/tools/smoke_detectset.py` still
  patches `gui_app.CONFIG_FILE` (dev tool, not part of the test gate) —
  recorded in REFACTOR_NOTICED_ISSUES.md.
