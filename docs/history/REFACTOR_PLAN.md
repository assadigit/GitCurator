# REFACTOR_PLAN — splitting `app/gitcurator/gui/app.py`

**Branch:** `refactor/gui-app-split` · **Baseline tag:** `baseline-before-split`
**Mode:** pure structural refactor. Code is MOVED, never rewritten. Zero behavior
change. Every commit passes the full gate (see "The gate" below).

## 1. What we are doing and why

`gitcurator/gui/app.py` is a 13,815-line monolith: 22 top-level functions,
9 classes (MainWindow alone = 8,060 lines / 146 methods; ProcessingWorker =
3,245 lines), 6 module-level variables, zero `global` statements, zero
dynamic references (`globals()`/`getattr`/`eval` — verified by
`.refactor-scratch/dynamic.txt`, empty). This plan splits it into small,
cohesive modules while keeping `gitcurator.gui.app` as a facade so every
existing importer (`from gitcurator.gui.app import X`) keeps working.

## 2. Safety net (built and failure-proved before any move)

All tooling lives in `.refactor-scratch/` (git-ignored via `.git/info/exclude`).

| Check | What it proves | Proved it can fail |
|---|---|---|
| `fingerprint_check.py` | every baseline function/method/lambda appears EXACTLY ONCE in the current tree, AST-identical (hash of `ast.dump` without positions); classes verbatim-once except the allowlisted restructured `MainWindow` | mutA (body edit) → FAIL |
| `surface_check.py` | the 722 runtime attributes of `gitcurator.gui.app` (incl. PyQt star-import names) are all still exposed with the same kind | mutB (rename) → FAIL |
| `import_test.py` | every package module imports cleanly in a fresh process (catches cycles) | mutC (syntax error) → FAIL |
| full unittest suite | 772 baseline cases, twice green at baseline (60.8s / 62.3s) | n/a (project's own gate) |
| offline golden run | 30/30 processed, 0 invalid categories at baseline | n/a |
| characterization tests | `app/tests/test_refactor_surface.py` (committed) pin the public surface + pure-function behavior; must pass BEFORE and AFTER every move | n/a |

Baseline facts recorded: 772 tests OK ×2; golden 30/30; import time 1.00s;
`gitcurator.tools.test` already fails to import at baseline (Telethon env
validation — pre-existing, allowlisted in the import test).

## 3. Target layout (leaf-first; one module = one commit)

Direction of imports is strictly one-way: app.py → feature modules → leaf
modules. No module imports `gitcurator.gui.app`.

### Stage A — leaves
| Module | Moves (verbatim) |
|---|---|
| `gui/link_helpers.py` | `extract_github_urls`, `clean_url`, `normalize_url` |
| `gui/dead_links.py` | `DEAD_LINK_THRESHOLD`, `dead_link_threshold` |
| `gui/telegram_lazy.py` | `_import_telethon_fetcher`, `fetch_github_urls_sync`, `TelegramFetcherError` |
| `gui/platform_intake.py` | `PLATFORM_INFO`, `_inbox_table_vault`, `classify_platform`, `write_inbox_links_by_platform` |

### Stage B — data layer
| Module | Moves |
|---|---|
| `gui/vault_index.py` | `VaultIndex`, `find_obsidian_vaults`, `_safe_moc_name` |
| `gui/cache_db.py` | `CacheDB` |
| `gui/link_tracker.py` | `LinkTracker` |
| `gui/log_bridge.py` | `_GuiLogHandler`, `_install_gui_log_handler`, `_remove_gui_log_handler` |

### Stage C — workers, dialogs, headless
| Module | Moves |
|---|---|
| `gui/worker_jobs.py` | `_run_telegram_worker`, `_telegram_test_job`, `_bot_queue_job`, `_connection_battery_job`, `_quick_detect_job` (+ `import subprocess as _subprocess` in header) |
| `gui/processing_worker.py` | `ProcessingWorker`, `TestWorker` |
| `gui/dialogs.py` | `SettingsDialog`, `ConnectionTestDialog` |
| `gui/headless.py` | `run_headless`, `_is_process_running` |

### Stage D — MainWindow split (`gui/main_window/` package)
`MainWindow.__init__` (131 ln) and the class shell move LAST into
`gui/main_window/window.py`; every other method is peeled, one mixin module
per commit, into domain mixins. Mixins precede `QMainWindow` in the bases so
Qt virtual overrides (`closeEvent`) are never shadowed; no mixin defines
`__init__` and no peeled method calls `super().<method>` (verified: all
`super()` calls in the file are `super().__init__()` inside whole classes or
nested local classes), so MRO semantics are preserved.

| Mixin module / class | Methods peeled |
|---|---|
| `theme.py` `ThemeMixin` | `_load_fonts _accent _panel_bg _panel_bg_alt _status_colors _btn_kind_style _style_btn _refresh_button_styles _wrap_scroll _btn_style _animate_dialog apply_light_theme apply_dark_theme toggle_theme _sync_theme_toggle_btn _log_html_colors _refresh_main_icons _show_custom_message_box _show_custom_question` |
| `ui.py` `UiMixin` | `initUI _build_logo_lockup log_message _toggle_log_panel _set_log_filter _filter_log _clear_log _refresh_pipeline_counter` |
| `hero.py` `HeroMixin` | `_confirm_batch _sync_run_button _on_hero_clicked _after_sync_fetch _begin_hero_processing _set_hero_state` |
| `connection_tests.py` `ConnectionTestsMixin` | `test_telegram_github test_github_token test_proxy test_vault _get_ollama_model_names refresh_ollama_models start_ollama_server test_ollama _llm_num_ctx_value _llm_max_output_tokens_value test_cloud_llm` |
| `llamacpp.py` `LlamaCppMixin` | `_llamacpp_fields _fill_llamacpp_models _startup_llamacpp_autodetect _apply_llamacpp_autodetect detect_llamacpp_service refresh_llamacpp_models test_llamacpp quick_detect_set_ollama quick_detect_set_llamacpp _run_quick_detect _quick_model_dialog _apply_quick_detect` |
| `phase6.py` `Phase6Mixin` | `_run_phase6_tool run_recall_hooks_dryrun run_link_suggestions` |
| `test_connection_modal.py` `TestConnectionModalMixin` | `test_all _cc_dialog_sync_rows _cc_telegram_leg _cc_finish` |
| `vaults_config.py` `VaultConfigMixin` | `populate_vaults on_vault_changed browse_vault browse_website_vault browse_manual_vault _vault_path_status _refresh_vault_page_status _save_vault_page remove_vault load_config save_config load_ui_config` |
| `input_proxy.py` `InputProxyMixin` | `select_import_file show_input_help _get_proxy_dict _check_proxy_health _set_proxy_status_text` |
| `telegram_ui.py` `TelegramUiMixin` | `_acquire_telegram_lock _release_telegram_lock _tg_lock_watchdog _keep_worker _on_telegram_code_requested` |
| `processing_control.py` `ProcessingControlMixin` | `start_processing _start_worker_with_urls _start_worker stop_processing update_progress update_status _schedule_progress_hide _hide_progress_bar processing_finished _on_disk_full _on_llm_failed _on_model_changed` |
| `dashboard.py` `DashboardMixin` | `update_dashboard refresh_quarantine_view clear_all_quarantine _save_quarantine_threshold undo_last_batch verify_vault recategorize_notes view_dead_links _reset_dead_links_now retry_failed_repos` |
| `bot_queue.py` `BotQueueMixin` | `fetch_from_sources process_sources_urls check_bot_queue process_bot_queue process_new_bot_queue export_all_bot_links verify_all_bot_links _fuzzy_match_github_url _process_missing_urls _show_manual_resolve_dialog clear_bot_queue _mark_bot_messages_read _startup_auto_check` |
| `backup_seal.py` `BackupSealMixin` | `_create_backup_tab _backup_browse_folder _backup_save_config _backup_refresh_status _backup_now _backup_result _start_vault_seal _vault_seal_result _start_websites_seal _vaultseal_now _vaultseal_refresh_status _start_goodrepos_publish _goodrepos_result _goodrepos_now _goodrepos_refresh_status _backup_export_zip _export_result _backup_restore _open_dashboard_from_backup_tab _open_dashboard_browser` |
| `lifecycle.py` `LifecycleMixin` | `show_about_me_wizard closeEvent _unblock_worker_for_shutdown` |
| `window.py` (final) | the remaining `MainWindow` shell: bases composition + `__init__` |

### app.py afterwards (~370 ln)
Module docstring, `__VERSION__` + the `--cli` stamp guard, the full original
import header (stdlib + gitcurator + all try-blocks — the PyQt star imports
MUST stay so the 722-attribute surface is preserved), `_APP_DIR`, the facade
imports (explicit names from every new module), `setup_logging`, `main`, and
the `__name__ == "__main__"` guard. No import line is ever deleted from
app.py (surface preservation; `_subprocess` etc. stay even when unused).

## 4. Move mechanics (what "verbatim" means here)

- Each moved segment is the exact source lines of the def/class/assignment,
  including decorators and the contiguous comment block directly above it.
- New module headers are necessarily new glue: the original import header
  (stdlib + gitcurator imports), the third-party try-blocks the moved code
  actually references (PyQt star / github / ollama / dotenv / cloud blocks),
  `_APP_DIR = APP_DIR`, and imports of already-moved names from their new
  homes. The colorama block is reproduced WITHOUT `colorama.init()` — the
  init stays in app.py, exactly once per process, as at baseline.
- The move tool refuses to write a module whose moved code references a
  name not covered by (header imports ∪ facade imports ∪ star-imports ∪
  builtins) — a scope-aware free-name check before anything is written.
- app.py receives one facade import line per extraction, inserted right
  after the import header; moved names stay importable from
  `gitcurator.gui.app` unchanged.
- Peel commits rewrite only the `class MainWindow(...)` bases line
  (`class MainWindow(NewMixin, ...PreviousMixins..., QMainWindow):`).

## 5. The gate (after EVERY extraction commit)

1. `python -m py_compile` on app.py + every new module (syntax)
2. `import_test.py` — fresh-process import of all modules (cycles)
3. `surface_check.py` — 722 attrs, kinds equal (API preserved)
4. `fingerprint_check.py` — all functions verbatim exactly once
5. full suite: `python -m unittest <20 modules> tests.test_refactor_surface`
   (772 + characterization = 793 green)
6. offline golden run stays green (checked at milestones and at the end)

Failing the same problem ~3 times ⇒ `git revert` + ask for help.

## 6. Approved deviations (defaults, per owner)

1. **Mixins for MainWindow** — yes (the only way to split an 8k-line Qt class
   without rewriting method bodies).
2. **Patch-target updates in tests** — yes. Tests that monkeypatch
   `gitcurator.gui.app.<name>` (or read app.py source text) are updated to
   target the module that now owns the code: `gui_app.Github` →
   `processing_worker.Github` (test_phase1/2), `gui_app.threading` →
   `main_window/llamacpp.threading` (test_llamacpp), `gui_app.CONFIG_FILE` →
   the consuming module(s) (test_v0230), plus source-scan tests
   (test_llamacpp/phase4/phase5/packaging) pointed at the new module files.
   Test EXPECTATIONS never change — only where they aim.
3. **CI edit** — yes: `ci.yml` compile list grows 38 → ~64 modules; the
   unittest line gains `tests.test_refactor_surface`.
4. New modules live under `gitcurator/gui/` (+ `gui/main_window/`).
5. Project docs (README/SPEC/STATUS) are untouched by the code-move commits.
6. Commit format: `Move <what> from app.py into <module>` — one logical move
   per commit.

## 7. Out of scope

No renames, no signature changes, no "improvements", no dependency updates,
no formatting passes, no comment rewriting inside moved bodies. Bugs found
on the way are recorded in REFACTOR_NOTICED_ISSUES.md and left alone.

## 8. Order of execution

link_helpers → dead_links → telegram_lazy → platform_intake → vault_index →
cache_db → link_tracker → log_bridge → worker_jobs → processing_worker →
dialogs → headless → 15 mixins (theme, ui, hero, connection_tests, llamacpp,
phase6, test_connection_modal, vaults_config, input_proxy, telegram_ui,
processing_control, dashboard, bot_queue, backup_seal, lifecycle) →
window.py (MainWindow shell) → ci.yml/docs closeout.
