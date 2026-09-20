# GitHub-to-Obsidian — GitCurator app (v32.4 modular layout)

> **v32.4 update** — the modularization is complete: besides the
> 9,770-line monolith becoming a 164-line facade, the two remaining
> giants were split the same way (script-driven, every method moved
> byte-identically — md5-verified). The layout:
>
> | Module | Responsibility |
> |---|---|
> | `gitcurator/cli.py` | headless CLI (`run_headless`) + `main()` dispatch, app.lock, QApplication bootstrap |
> | `gitcurator/__main__.py` | `python -m gitcurator` entry (PEP 338) |
> | `gitcurator/gui/main_window/` | MainWindow assembled in `window.py` from 13 mixins (`styles`, `ui_setup`, `settings_tests`, `search`, `config_ui`, `processing_ctl`, `log_panel`, `vault_ops`, `bot_queue`, `bot_links`, `dialogs`, `backup_tab`, `publish_services`) |
> | `gitcurator/gui/workers/` | `processing.py` (signals + `__init__`), phase mixins (`_auth`, `fetch`, `llm`, `assets`, `scoring`, `notes`, `pipeline`), `test_worker.py`, `_deps.py` (guarded deps) |
> | `gitcurator/gui/_qt.py` · `log_handler.py` · `app.py` | guarded PyQt6 import · logging→signal bridge · back-compat facade |
> | `gitcurator/core/` | `links` · `storage` · `note_builder` · `llm_client` · `vault` · `cache_db` · `link_tracker` · `inbox` (pure stdlib, testable) |
> | `gitcurator/utils/` | `logging_setup` · `terminal` |
> | `gitcurator/integrations/telegram_jobs.py` | subprocess Telegram fetch jobs |
>
> **CLI hardening (v32.3, kept):** the headless CLI works from any launch
> directory (all runtime paths are anchored to this folder via
> `constants.resolve_app_path`), failed runs exit `1`, `--help` is
> answered by argparse before any PyQt6 import, and requirements.txt now
> declares PySocks. **v32.4:** `python -m gitcurator` works, and the
> dependency guards tell the truth (a broken native Qt install is no
> longer reported as "PyQt6 is not installed"; the no-colorama fallback
> honors its `__all__` contract). The sections below are the historical
> v30 notes — still accurate for behavior, outdated only where they
> describe file layout; see the root `README.md` + `CHANGELOG.md` for the
current state.

## Quick Start

### Desktop App
```cmd
pip install -r requirements.txt
python main.py
```

### Headless (no GUI) — works from any directory since v32.3
```cmd
python main.py --headless --import-file urls.txt --vault "C:\path\to\vault"
python main.py --headless --from-id 123 --to-id 456 --vault "C:\path\to\vault"
python main.py --help
```

### Unit tests (no GUI / no Ollama / no Telegram needed)
```cmd
python -m unittest tests.test_core tests.test_e2e tests.test_goodrepos tests.test_cli -v
```

### Cloudflare Worker (deploy from cloudflare-bot/ folder) — OPTIONAL, see status below
```cmd
cd cloudflare-bot
npm install
npx wrangler login
```
Edit `installer.config.json` with your credentials, then:
```cmd
node install.js
```

### Dashboard (deploy from cloudflare-bot/dashboard/ folder)
```cmd
cd cloudflare-bot\dashboard
npm install
node deploy-dashboard.js
```

## Version: 30.0 — What changed (audit-driven fixes)

### New core modules (extracted from main.py — pure stdlib, unit-testable)
| Module | Responsibility |
|---|---|
| `links.py` | THE single GitHub/non-GitHub URL regex + normalization + dedup (replaces 4+ divergent copies) |
| `storage.py` | Atomic writes (tempfile + `os.replace`), traversal-proof filenames, collision handling, config merge |
| `note_builder.py` | Note/frontmatter builder with YAML-injection sanitization of tags/aliases/org/url/languages |
| `llm_client.py` | Timeout-wrapped Ollama `chat`/`list` (both old & new ollama-py shapes) + robust JSON extraction |
| `tests/test_core.py` | 34 unit tests locking the above down |

### Fixed in this release
1. **LLM model persistence** — picking a new model in the LLM-failure dialog now applies to the current repo, the rest of the batch, the Settings combo, AND config.json ("Remember" checkbox, ON by default). Single-model auto-switch at warmup when the configured model is gone. Fixes the "modal on every link" frustration.
2. **Headless hang-bombs** — `llm_failed_signal` (10-min stall), `disk_full_signal` (INFINITE spin) and `code_requested` (5-min stall) all take non-blocking defaults in headless mode.
3. **`save_config()` data destruction** — merge-based now (unknown keys like `cloudflare_install_id`, `cloudflare_shared_secret`, `gdrive_*` survive); `timeout_per_repo` / `max_retries` / `delay_between_api_calls` are defaulted, never stomped; `self.config` is never rebound (worker sees live updates); write is atomic.
4. **Timeouts on every Ollama call** — `chat` (configurable `llm_timeout_s`, default 300s), `list` (15s), warmup (120s), settings test (120s). A hung Ollama can no longer freeze a batch forever.
5. **Atomic note + banner writes** — `os.replace` on a same-dir temp file; crashes can no longer leave truncated notes/pngs that the cache would treat as complete.
6. **Frontmatter sanitization** — LLM output (tags/aliases/org) and README content are treated as untrusted: YAML flow-sequence items are charset-restricted, scalars are escaped-quoted, README is wrapped in untrusted-data delimiters in the prompt.
7. **CacheDB hardening** — `busy_timeout=30000`, connect `timeout=30`, every statement under an `RLock` (cross-thread safe), idempotent `close()`, and the two early-return leak paths in `run()` fixed.
8. **Graceful shutdown** — `closeEvent` now unblocks every wait the worker could be parked on (LLM decision → "stop", auth code → "", disk-full flag → clear) BEFORE waiting, so quitting mid-batch can't leave the thread running.
9. **Link parsing standardized** — one implementation (`links.py`) used by main.py, telegram_fetch_worker.py, backfill_manager.py, telethon_fetcher.py.

### Cloudflare stack — decision & status
The desktop-side Cloudflare sync is currently **dormant** (`main.py` keeps `cf_manager = None`), but the stack is **fixed, not deleted**:
- **HMAC key mismatch FIXED** (`cloudflare_sync.py::_sign_request`): the Worker stores `sha256(shared_secret)` as the HMAC key; the desktop used to sign with the raw secret → every sync endpoint 401'd. The desktop now derives the same key.
- **Webhook impersonation FIXED** (`cloudflare-bot/src/index.js`): set `WEBHOOK_SECRET` (see `cloudflare-bot/wrangler.toml` for the 2-command setup) and forged Telegram updates are rejected with 403.
- **`save_config` key-wipe FIXED**: pairing keys survive saves now.
- **Stale root `worker.js` + `wrangler.toml` REMOVED** (quarantined as `.deleted-worker.js.v30` / `.deleted-wrangler.toml.v30` — delete those two files to drop them permanently). They were an older, divergent deploy target (placeholder KV id, different worker name) that risked deploying the wrong code from the repo root. `cloudflare-bot/` is the ONE canonical worker.

### ⚠️ Before deploying the worker again
Secrets were previously committed to this folder (bot tokens, api id/hash, Cloudflare account info, session files). Rotate every credential before exposing anything publicly, and never commit this folder to a public repo.

### Key Features
- Forward GitHub links to bot → auto-reply with stars + description
- Desktop app processes links → writes Obsidian notes
- Local folder backup (works with OneDrive/Dropbox/Google Drive sync)
- Web dashboard with stats, pending links, decommissioned repos, errors
- Dark/light mode toggle
- No link left behind (D1 permanent ledger + LinkTracker manifest)
- Headless CLI mode (non-blocking: LLM failures/disk-full/auth degrade gracefully)
