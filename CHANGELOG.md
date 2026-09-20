# Changelog

All notable changes to GitCurator are documented here.
Versioning: [SemVer](https://semver.org/) — `MAJOR.MINOR.PATCH`, tagged `vMAJOR.MINOR.PATCH`.

## [Unreleased] — v32.3 · Modularization & CLI Hardening (branch `refactor/modularization`)

A behavior-preserving refactor of the 9,770-line `gui/app.py` monolith
(51% of the Python codebase) into focused single-responsibility modules,
plus four CLI bug fixes with regression tests. Baseline before the work:
73/73 tests green; every commit on the branch was verified against that
baseline (tests + import smokes + offscreen GUI construction + headless
end-to-end runs byte-compared against pre-refactor output).

### Changed — codebase refactoring & modularization (no behavior changes)

- **`app/gitcurator/gui/app.py`: 9,770 → 164 lines.** It is now a thin
  back-compat facade that re-exports the public surface the monolith
  exposed (`gitcurator.gui.app.main` and friends keep working —
  `main.py`'s documented import is unchanged in spirit and re-anchored
  to `gitcurator.cli.main` in the CLI fix below).
- New focused modules (bodies moved verbatim, docstrings + explicit
  import headers added):
  - `gitcurator/cli.py` — `run_headless`, `_is_process_running`, `main`
  - `gitcurator/gui/main_window.py` — MainWindow (the PyQt6 GUI, ~6,130 lines)
  - `gitcurator/gui/workers.py` — ProcessingWorker + TestWorker
  - `gitcurator/gui/log_handler.py` — logging → Qt signal bridge
  - `gitcurator/gui/_qt.py` — the guarded PyQt6 import (single site)
  - `gitcurator/core/vault.py` — VaultIndex, find_obsidian_vaults, _safe_moc_name
  - `gitcurator/core/cache_db.py` — CacheDB (now pure-stdlib importable)
  - `gitcurator/core/link_tracker.py` — the v23 "No Link Left Behind" manifest
  - `gitcurator/core/inbox.py` — non-GitHub link routing (classify/PLATFORM_INFO/write)
  - `gitcurator/utils/` — logging_setup + terminal (guarded colorama)
  - `gitcurator/integrations/telegram_jobs.py` — subprocess Telegram fetch jobs
- The compile gate (CI + dashboard `/api/verify`) widened from 12 to 24
  modules; the test suite grew from 73 to 87 cases (see Added).

### Fixed — CLI investigation (root causes, fixes, verification)

- **Headless CLI failed outside `app/` (the primary reported failure).**
  Root cause: the v32 modularization anchored `CONFIG_FILE` to `APP_DIR`
  in `constants.py` ("correct file when launched from anywhere") but
  never updated `run_headless()`, whose `--config` default remained the
  CWD-relative string `'config.json'` — so
  `python app/main.py --headless …` from any other directory died with
  `Config file not found: config.json` (exit 1). Fix:
  `constants.resolve_app_path()` (cwd-first, `APP_DIR` fallback) +
  `--config` defaulting to the anchored `CONFIG_FILE`; the same
  resolution covers `--import-file`. Verified: from the repo root the
  pipeline now loads `…/app/config.json` and runs end-to-end with output
  byte-identical to an `app/`-cwd run; locked down by
  `tests/test_cli.py`.
- **Five sibling CWD-relative paths fixed by the same root-cause
  analysis:** `system_prompt.txt` (a custom prompt was silently ignored
  outside `app/`), `session.session` (backup copy missed — the fetch
  subprocess itself always ran with `cwd=APP_DIR`), `cache.db`
  (`CacheDB()` re-created the dedup cache per launch directory, so
  processed repos were "forgotten"), `app.lock` (instances launched from
  different directories never saw each other's lock), and the `logs/`
  default. All anchored to `APP_DIR` — identical behavior for the
  documented `app/` usage.
- **Headless runs exited 0 on failure.** `run_headless` returned
  `app.exec()` (0 after `quit()`), so a failed batch ("ERROR: Ollama not
  available") still exited 0 — schedulers/CI could not detect failure.
  Fix: `app.exit(0 if success else 1)`. Verified: failed run → exit 1.
- **`python main.py --help` launched the GUI.** The README has promised
  "headless mode options" since v0.0.7, but `main()` only routed
  `--headless`; on display-less machines `--help` died with a Qt
  platform-plugin error. Root cause: argument inspection happened after
  the whole PyQt6 import chain. Fix: `build_arg_parser()` hoisted to
  module level, `--help`/`-h`/`--headless` routed to argparse BEFORE any
  heavy import (`cli.py` is stdlib+constants-only at module level),
  `main.py` imports `gitcurator.cli.main` directly. Verified:
  `python main.py --help` prints usage + exit 0 on a bare Python install
  with no PyQt6; real runs keep the historical version-stamp on stderr.
- **`requirements.txt` missing PySocks.** `telethon_fetcher.py` imports
  `socks` at load time and the default `config.json` ships a socks5
  proxy enabled — fresh installs printed `No module named 'socks'` and
  the Telegram fetcher silently degraded to a stub. Fix:
  `PySocks>=1.7.1` declared. Verified in a clean venv.
- **Windows console hardening:** redirected stdout/stderr (ANSI code
  page, e.g. cp1252) are reconfigured to UTF-8 with `errors='replace'`
  at CLI entry, so the emoji-rich log lines can no longer raise
  `UnicodeEncodeError` when piped.
- **Latent unbound-name in the headless finish handler:** `vs_summary`
  is now built before the VaultSeal try-block (a seal exception used to
  turn the GoodRepos call into `NameError`).

### Added

- `tests/test_cli.py` — 14 CLI smoke tests (parser surface, path
  resolution, `main.py --help` subprocess behavior on bare Python, and a
  PyQt6-guarded end-to-end). Suite: 73 → 87 tests.
- `docs/SWOT_ANALYSIS.md` — evidence-based SWOT audit of v0.0.10.
- `gitcurator/constants.resolve_app_path()` — the one canonical
  relative-path resolver (cwd-first, APP_DIR fallback).

## [0.0.10] — Security Hygiene & Deploy Kit — 2026-09-17

Session wrap-up release: closes the Session-1 audit finding *live secrets
committed* in the git tree, and adds the two-minute Cloudflare update path.

### Security
- **Live credentials removed from the git tree** — `app/session.session` and
  `app/session.session.bak` (full Telethon account access) are untracked and
  gitignored; `app/config.json` and both `installer.config.json` files are
  clean templates again (empty credential fields, as their `_comment` headers
  intend); the real bot token that appeared in `DEPLOYMENT.md` examples is
  now `YOUR_BOT_TOKEN`; `gitcurator/gui/app.py`, `tools/test.py` and
  `tools/diagnose_code.py` use placeholder credentials. The repository is
  private and every value was already exposed in chat (credential rotation
  remains the standing P0 — see the dashboard's Go-Live tab), but the tree
  is now safe against a future visibility flip. Note: git **history** still
  contains the pre-scrub blobs — run `git filter-repo` before ever making
  this repository public.
- **Release zips v0.01–v0.09 deleted from download/ + public/** — v0.01–v0.07
  shipped `session.session` directly in the working tree; v0.08/v0.09
  excluded the file from the tree but bundled the whole `.git` folder, whose
  object store still carried the session blob (`44213fc2…`) plus every other
  historical secret — recoverable by anyone who unzipped them. Only the
  v0.0.10 zip is clean: it is built via `git archive` (tracked tree only, no
  `.git`, no runtime caches, no sessions).

### Added
- **`deploy-latest.ps1` / `deploy-latest.sh`** — the two-minute update path
  for an existing Cloudflare deployment: wrangler presence + auth check,
  idempotent D1 schema apply (`IF NOT EXISTS` — existing data untouched),
  `wrangler deploy`, a live health check against the deployed worker, and an
  optional `--with-secret` / `-WithSecret` flow that sets the v30
  `WEBHOOK_SECRET` anti-impersonation hardening and prints the matching
  Telegram `setWebhook` command. All secrets and data persist across
  deploys — nothing is re-entered.
- **DEPLOYMENT.md** gained a "Quick path — update an EXISTING deployment"
  section ahead of the 45-minute from-scratch guide.

### Verified
- 12/12 py_compile and 73/73 tests on the sanitized tree (repo mirror AND
  the live copy stay byte-identical); `git grep` sweep for every known
  secret value (bot token, api_id/hash, phone, user id, bot-id prefix)
  returns clean; `bash -n` on the deploy script; PowerShell twin reviewed
  line-by-line (no pwsh in the sandbox).

## [0.0.9] — Backup Tab Scrollout Fix Pack — 2026-09-17

Three usability fixes for the Backup tab (internal `v32.2`): its four
sections no longer clip inside the fixed window, the theme toggle now
re-colors all three status dots, and the dark scrollbar handle is
readable on plum.

### Fixed
- **Backup tab scrollout** — the Backup tab's four sections (Vault Backup,
  VaultSeal — GitHub Mirror, Good Repos — Public Directory, Dashboard) no
  longer clip inside the fixed 1000×750 window: the tab content is wrapped
  in a vertical-only `QScrollArea` (horizontal scrollbar always off) and
  compacted — status dots share the action rows, the two settings
  checkboxes sit side-by-side, info notes are single wrapped sentences.
  Visible-pane/natural-content ratio dropped from ~2.2 to 1.31; every
  control (Backup Now / Restore / toggles / Seal Now / Publish Now /
  Worker URL / Open Dashboard) is reachable by scrolling.
- **Theme toggle re-themes all three Backup status dots** — previously the
  Good Repos status kept light-theme deep-butter on the plum background
  (~2.5:1) after switching to dark; `toggle_theme()` and the startup
  dark-restore block now re-run all three refreshers; the backup dot also
  refreshes at construction; hardcoded `#AE2237` error reds route through
  theme-aware `_status_colors()`.
- **Dark scrollbar handle contrast raised** — `#3B344F` → `#7A7199`
  (~3.2:1 on plum, WCAG 1.4.11 non-text), hover `#8D84AD`.

### Verified
- 12/12 py_compile gate and 73/73 tests (34 core + 28 goodrepos + 11 e2e)
  on both the live copy and the repo mirror; offscreen GUI smoke
  (scroll-area config, four-section single column, 617px pane / 811px
  content, post-scroll visibility of the Dashboard section + Open
  Dashboard button, THEME-SWITCH assertion for all three dots); VLM QA
  of 4 screenshots (top/bottom × light/dark) with pixel forensics.

## [0.0.8] — Resilience Fix Pack: 401 Fallback, Windows-safe MOCs, Visible Theme Toggle

Four fixes driven by a real Windows run (internal `v32.1`): a rejected
GitHub token no longer kills a batch, quoted LLM categories no longer
crash the master index, the light/dark toggle is finally *findable*, and
token health is one click away.

### Bad-credentials fallback (the 401 wall)
- **Before**: an expired/rotated GitHub token failed *every* repo with a
  raw `401 {"message": "Bad credentials"}` blob, and the whole batch
  ended with "7 links need retry" + VaultSeal/GoodRepos both failing to
  resolve the account.
- **Now**: the first 401 logs ONE actionable error (token invalid,
  expired, or rotated + exactly where to fix it), silently drops the
  token for the rest of the batch (anonymous access, 60 req/h), retries
  the current repo, and keeps going.
- **VaultSeal / GoodRepos** messages now name the fix too:
  "could not resolve the GitHub account for the token (invalid or
  expired — update the GitHub Token in Settings → Credentials, then
  'Test GitHub Token')".

### MOC filename sanitizer (Windows `[Errno 22]` crash)
- The LLM occasionally returns categories with literal quotes or `>`
  separators (`"Agents_Skills"`, `AI > Skills`). The master-index
  generator built `_moc/"Agents_Skills".md` — quotes are ILLEGAL in
  Windows filenames, so index generation died with
  `[Errno 22] Invalid argument`.
- **`_safe_moc_name()`** — one canonical sanitizer (`/` `\` → `_`;
  strips `<>:"|?*` + control chars; collapses whitespace; no trailing
  dots/spaces) used by BOTH the file writes and the
  `[[_moc/…|View MOC]]` wiki-links so they always match.

### Always-visible light/dark toggle
- The theme toggle lived in *More → Settings* — undiscoverable, and with
  `dark_mode: true` saved the app read as "dark only". There is now a
  compact **🌙/☀️ icon button in the action row** (38×36, pastel violet
  on white / lavender on plum, tooltip names the current mode) that
  flips the full pastel cream/plum theme and persists the choice. The
  Settings-menu entry stays in sync.

### One-click GitHub token tester
- **Credentials → "🔑 Test GitHub Token"** validates the token *as
  typed* (before Save) via `GET /user`: shows the account login on
  success ("5000 requests/hour enabled"), or an actionable 401/403
  message on failure — the #1 field error, caught before a batch burns
  its repos on it.

### Fixes
- `gui/app.py` 401 branch, `_safe_moc_name` + 3 call sites, `icon` button
  variant, `theme_toggle_btn` + `_sync_theme_toggle_btn`, `test_github_token`
- `integrations/vaultseal.py`, `integrations/goodrepos.py` — enriched
  could-not-resolve messages
- Gates: 12/12 compiles + **73/73 tests** (live copy *and* repo mirror)
  + offscreen smoke (both themes, toggle geometry, sanitizer cases
  including the exact `"Agents_Skills"` crash).

## [0.0.7] — Modular Core & Good Repos: Public Directory, Pastel UI

Three user-facing asks, one release: the flat `app/` became a proper Python
package, every curation run now also publishes a **public** curated directory
(`good-repos`), and the whole GUI speaks **pastel** (internal `v32.0`).

### Modular package layout (breaking-free)
- **`app/gitcurator/`** — the seventeen flat modules moved into purposeful
  subpackages: `core/` (links · storage · note_builder · llm_client),
  `integrations/` (telegram fetchers · vaultseal · goodrepos ·
  error_reporter), `cloud/` (cloudflare + gdrive), `gui/` (the PyQt6
  application), `tools/` (developer utilities) + `constants.py` (shared
  design tokens + config defaults).
- **Thin `main.py` launcher** — every historical entry point keeps working
  unchanged: `python main.py` (GUI), `python main.py --headless …`, and
  `python -m unittest tests.test_core …` from the app dir.
- **Path hardening** — `config.json` is now anchored to the app root (was
  cwd-relative); the Telegram subprocess and `assets/fonts` resolve through
  the new package layout; fetcher scripts bootstrap `sys.path` themselves so
  direct execution still works.
- **CI** — the workflow compiles the 12 audited modules at their new paths
  and runs all three suites; also fixed a pre-existing YAML typo that had
  mangled `branches: [main]` into `branches: ain]`.

### GoodRepos — the public curated directory
- **After every run** (GUI or headless, right after VaultSeal), the curated
  notes become an emoji-rich README directory — organized like
  *AI → Skills → …* — with the full notes mirrored into category folders of
  the **public** `good-repos` GitHub repository. Everyone can browse and
  benefit from the curation.
- **README generator** — stats line (`N repos · M categories · Updated`),
  anchor-verified contents, per-category counts, one line per repo (link,
  TL;DR, ⭐ stars, 🔧 language, `tags`), GitHub-exact anchor math.
- **History-preserving publishes** — fetch + `reset --mixed` keeps the
  remote history; each publish is a real diff; unchanged content is a no-op
  ("directory unchanged since the last publish").
- **Config + UI** — `goodrepos` section (enabled / repo_name / auto_push),
  a Backup-tab group with status line and "Publish Now", theme-aware status
  colors; standalone CLI (`--vault --repo-name --token --no-push --json
  --status --dry-run`).
- **28 new tests** (`tests/test_goodrepos.py`) — scan, README structure,
  anchors, sorting, idempotency, frontmatter edge cases, config bridge.
  Total suite: **73 tests**.
- **Dashboard** — new **Good Repos** tab (live directory scan, category
  tree, publish timeline, publish-now/simulate via `GET/POST/DELETE
  /api/goodrepos`) + `GoodReposEvent` Prisma model; the compile gate grew
  to 12 files.

### Pastel theme (AA-verified)
- **Light: pastel cream** — `#FBF8F2` window, white sheets, warm-sand
  borders, plum text; mint filled primary (`#B9E3C9` + deep-forest `#17402B`
  text, 8.3:1), violet outlined secondary (`#5F54B4`, 6.2:1), pastel-rose
  danger (`#F6C6CD` + `#5E1120`, 8.8:1), butter warnings, lavender/mint
  info notes.
- **Dark: pastel night** — soft plum `#221E2E`/`#2B2639` surfaces, warm
  white text, lavender `#C4BCF5` accents (8.2:1), pastel mint progress
  chunk.
- **30 text pairs verified programmatically** (all ≥ 4.5:1) — pastel fills
  always carry a deep companion text color; `_btn_style` gained the `text`
  parameter; `_status_colors()` makes every status label theme-aware.
- **Verification** — window-composite histograms in both themes (light:
  white/cream/lavender/mint dominant; dark: plum dominant, zero white
  leakage), offscreen GUI smoke across all tabs, VLM review 9/10 light.

### Fixes
- **CI YAML typo** — `branches: ain]` → `branches: [main]` (pre-existing;
  pushes/tags had still triggered, PRs had not).
- Duplicate `CONFIG_FILE` definition in the extracted constants (the flat
  copy silently overrode the anchored one).
- Dialog header blue `#2196F3` replaced with the violet accent (no-blue
  rule); remaining zinc literals in dialogs migrated to the pastel family.

### Verification (this release)
- 73/73 tests (34 + 11 + 28) and 12/12 py_compiles — locally, from the
  repo mirror, and live through the dashboard gate (`POST /api/verify`).
- Real public repo created + published end-to-end from the demo vault
  (7 repos across 7 categories, commit `c958518`), all README entry links
  verified intact via the GitHub API; idempotent re-publish skips.
- Offscreen PyQt6 smoke: window, 9 tabs, theme toggle, GoodRepos group;
  pixel-sampled histograms in both themes; VLM screenshot QA.

## [0.0.6] — UI/UX Overhaul: Fixed Window, AA Contrast, Button Hierarchy

A full visual-standards pass over the PyQt6 GUI (internal `v31.1`).
No business logic, API calls, or file paths were changed — presentation
layer only.

### Window & Layout
- **Fixed 1000×750 window** — one size for every tab; the window never
  resizes when switching tabs (preserves spatial memory).
- **Every tab scrolls independently** — each tab's content is wrapped in a
  `QScrollArea` starting at the same top position at its natural height.
  No padded/fixed-height containers, no filler stretches.
- **Exactly one growable region per tab** — the results/list/log panel
  (Bot queue, Dashboard stats, Sources results, and the global log).
  Removed the old 150px height caps; controls stay content-sized.

### Color & Contrast (WCAG AA)
- Primary action fill `#10B981` → **`#047857`** (white text 7.4:1).
- Secondary/read action `#6366F1` → **`#4338CA`** (7.9:1).
- Destructive `#EF4444` → **`#B91C1C`** (6.5:1).
- Red removed from non-error elements — the pending-count badge is now
  neutral zinc (a pending count is routine, not an error).
- Log text colors are theme-aware (dark shades on light, light shades on
  dark) so every level stays readable in both themes.

### Button Hierarchy
- **Three variants only** — filled primary (max one per tab), outlined
  secondary, filled danger. Plus a quiet ghost for log-panel utilities.
- **Overflow "More" menu** (single) — tests, verification, export, retry,
  recategorize and preview moved out of the action bar (Hick's law).
- **Dark-mode toggle moved into a Settings submenu** — a display
  preference no longer sits beside batch-job triggers.

### System Status & Safety
- **Determinate progress bar, visible only while a batch runs** —
  labeled `Processing X of Y — owner/repo`; hidden at rest.
- **Large-batch confirmation** — any batch >10 items asks for exact-count
  confirmation first (bot queue, Process New, sources, retry, fetches).
- **Labeled proxy status** — the dot is now paired with a text label
  (`Connected` / `Idle` / `Error`) per WCAG 1.4.1.

### Design Tokens
- **Spacing scale 4/8/16/24/32/48px** applied across QSS and layouts.
- **Type scale of four sizes** — 16px section/dialog titles, 13px
  labels/buttons, 12px body, 12px mono for log/results.

### Accessibility
- **2px focus outlines (2px offset)** on every interactive element
  (buttons, inputs, combos, checks, radios) in both themes.

### Microcopy & Icons
- Routine **pending counts use ⏳**; ❌ is reserved for actual failures.
- **Sources tab now uses 📡** (Proxy keeps 🌐) — two destinations no
  longer share one icon.

### Notes
- Dark mode fixed for real: semi-transparent (rgba) widget fills were
  composited over a light base by Qt's QSS engine — replaced with solid
  theme-aware panels (`tab_sheet` / `info_header` / `info_note` rules).
- Outlined variants re-apply on theme switch via a tracked button list.

## [0.0.5] — VaultSeal: Automatic Vault Backup

Obsidian's free tier has no sync. GitCurator now closes that gap itself:
after every curation run — 1 new project or 100 — the whole vault is
committed and pushed to a **private GitHub repository**. Restore is plain
`git clone`: the full vault at any point in its history.

### Python application (`app/`)
- **NEW `vaultseal.py`** (pure stdlib, 547 lines) — the VaultSeal engine:
  - `VaultSeal.seal()` — git init (when needed) → managed `.gitignore`
    hygiene → `git add -A` → `seal:` commit → one-time-token-URL push.
    Never raises (a backup failure must never fail the run it protects);
    skip-when-unchanged; failed pushes keep the local commit.
  - **Nested-repo guard** — a vault inside another git repository (a
    dotfiles tree, a synced workspace…) bootstraps its OWN repo: git's
    `add -A` is repo-wide from subdirectories since git 2.0, so without
    the guard a parent's files would be sealed. Caught live during QA
    (the sandbox workspace itself) and fixed with a toplevel comparison;
    regression-tested.
  - **Hygiene** — `workspace.json`, `workspace-mobile.json`,
    `.obsidian/cache`, `.trash/`, `__pycache__/`, OS noise sealed OUT via
    an idempotently-merged managed `.gitignore` block.
  - **Credential hygiene** — the token lives only in memory: API calls +
    one-time push URLs. The remote stays token-less, `.git/config` never
    sees the token, and it is never logged.
  - Repo bootstrap over the GitHub API — resolves the login from the
    token, reuses an existing private repo or creates it (described as
    "VaultSeal — automatic Obsidian vault backup (GitCurator)").
- **Post-run hook wired into both surfaces**: the PyQt6 GUI
  (`processing_finished` → background `VaultSealWorker` QThread, mirrors
  the v29.4 BackupWorker pattern) and the headless CLI (`_on_finished`,
  before the exit print). Runs for FAILED batches too — notes written
  before a mid-run failure are exactly what you want backed up; an
  unchanged vault is a no-op.
- **Backup tab (GUI)** — new "🛡️ VaultSeal — GitHub Mirror" group: enable
  checkbox, push toggle, repo-name override ("auto" = derived from the
  vault folder), live status line (Ready / Local-only / Disabled), and a
  manual **Seal Now** button that exercises the exact post-run path.
- **Config** — new `vaultseal` section (`enabled` / `repo_name` /
  `auto_push`), merge-safe through the v30 `merge_config` machinery.
- Internal lineage bumped to **v31.0**; compile gate + CI now cover 10
  modules (`+vaultseal.py`). 45/45 tests still green (2.3s).

### Verification dashboard (`dashboard/`)
- **NEW GET/POST/DELETE `/api/vault-seal`** — live git state of the vault
  (own-repo detection, dirty count, notes/files/size, last commit, remote,
  hygiene check), the app's `vaultseal` config section (token never
  returned), the persisted seal history + stats; `seal-now` runs the REAL
  `app/vaultseal.py` (local commit — the dashboard holds no GitHub token
  by design), `simulate` records a plausible event, `record` persists
  results reported by the app.
- **NEW Prisma model `VaultSealEvent`** (sealedAt, status
  sealed/skipped/failed, commit sha/message, files, pushed, repo,
  provenance source app/demo/simulate, duration).
- **NEW Vault Seal tab (9th)** — protection/vault/last-seal stat cards
  (CountUp, hover lift), a "how a seal works" 4-step walkthrough with
  hygiene chips, and the seal history timeline with per-event badges
  (status, source, pushed), delete + clear, and the git-clone recovery
  note. Mobile tab grid now 3×3.
- Export JSON includes the VaultSeal snapshot; footer lists
  `/api/vault-seal`; version strings bumped to 0.0.5.

### Verified live
- A real demo vault was sealed to the private
  `github.com/assadigit/GitCurator-Vault`: initial 12-file seal,
  incremental 1-file fast-forward push, and a no-op skip — all confirmed
  over the GitHub API. The workspace-junk first attempt (pre-guard) was
  force-replaced with clean history; no credentials were ever in it.

## [0.0.4] — Provenance & CI History

Verification runs now carry their origin, and the CI pipeline gets a
visible track record in the dashboard.

### Verification provenance
- `VerificationRun` (Prisma) gains a `source` column (default
  `"manual"`): `"manual"` for button-triggered runs, `"startup"` for the
  scheduler's first pass 60s after boot, `"scheduled"` for the 6-hour
  intervals. Existing rows backfill to `"manual"`.
- `POST /api/verify` accepts an optional JSON body
  `{"source": "manual"|"startup"|"scheduled"}` — validated against a
  closed whitelist before it touches the database; no body (the normal
  dashboard button) still records `"manual"`. In-flight runs coalesce,
  so the source of whoever started the run is the one recorded.
- `src/instrumentation.ts` now identifies itself — the startup pass and
  the interval passes each send their own source.
- `GET /api/history` returns `source` per run plus a
  `stats.sources { manual, startup, scheduled }` split.
- History tab: provenance badge on every run row (manual = zinc cursor,
  startup = amber refresh, scheduled = teal clock, each with an
  explanatory tooltip), source-count chips under the Total-runs stat
  tile, and a `source` column in the CSV export.

### CI run history
- `GET /api/releases` returns `ciHistory` — the last 10 GitHub Actions
  runs (newest first) from the same token-gated Actions API call as the
  existing latest-run chip. No token → `null` → the card simply doesn't
  render; zero runs → `[]` → also hidden (the "not run yet" chip covers
  it).
- Releases tab: new "CI run history" card — a 5×2 (mobile) / 10-wide
  (desktop) grid of run-number chips, colored by conclusion (emerald
  success / amber pulse while running / rose failure), each linking to
  its run on GitHub, with a pass-rate summary and last-failure line.

### Styling & UX polish
- Version history renders as a timeline: connector rail + per-release
  dots on the left, the newest dot emerald with a soft halo.
- The current release card gets an emerald border, ring and gradient
  wash; "current" badge gains a pulsing dot.
- Repo status / Backups / Workflow / Release cards lift on hover
  (translate + border + shadow), matching the History stat tiles.
- Repo status tile icons are now color-coded (VERSION emerald, Last tag
  amber, Commits teal, Working tree status-colored); the hero "Release"
  badge is emerald-tinted.

## [0.0.3] — CI Pipeline & Actions Status

The repository gets its own gate: GitHub Actions now runs the exact
verification the dashboard performs — on every push, tag and pull request —
and the dashboard surfaces the live CI status.

### CI (`​.github/workflows/ci.yml`)
- Runs on push (main + v* tags), pull requests and manual dispatch.
- The gate mirrors POST /api/verify 1:1 — py_compile over the 9 audited
  modules, then the full 45-case suite (`tests.test_core` +
  `tests.test_e2e`) with verbose output.
- Zero pip installs: the testable core is pure stdlib by design, so the
  whole gate boots in seconds on ubuntu-latest / Python 3.12.

### Verification dashboard (`dashboard/`)
- `/api/releases` now also reports the newest GitHub Actions run
  (status, conclusion, run number, link) — read-only, best-effort, and
  only when a `GITCURATOR_GH_TOKEN` (or `GITHUB_TOKEN`) env var is set;
  private-repo API reads need a token. Without it the field is null and
  the UI shows "CI n/a" — never an error.
- Releases tab: CI status chip beside the repo title — passing (emerald) /
  running (amber pulse) / failing (rose) / not-run (zinc), linking
  straight to the run on GitHub with a status tooltip.
- `.env.example` documents the optional read-only token (recommend a
  fine-grained PAT with Actions:read only).
- Version bumped to 0.0.3 across report/layout/fallbacks.


## [0.0.2] — Repository & Release Console

The dashboard becomes release-aware: live git status of this repository, the
parsed changelog, and the downloadable zip backups — plus version alignment
(repo-level SemVer 0.0.x; the v30.x numbers live on as the internal build
lineage).

### Verification dashboard (`dashboard/`)
- **NEW GET /api/releases** — live repository status: last commit, commit
  count, last tag, dirty-file detection, VERSION, the CHANGELOG.md parsed
  into structured releases, and the local .zip backups (public/ + download/
  scan with served flags). Best-effort by construction — never 500s.
- **NEW Releases tab (8th)** — repository status card (private badge,
  Open-on-GitHub link, clean/dirty working-tree indicator with file chips),
  backups card with per-zip Download buttons, the "how the next version
  ships" workflow card, and the full version history rendered from
  CHANGELOG.md in a scrollable timeline.
- **Header Backup button** — one-click download of the newest served zip
  with toast + tooltip (name, size, contents).
- **Versioning aligned** — the dashboard version now equals the repo
  version (0.0.2); the internal v30.x lineage is shown as a hero badge;
  page title and metadata renamed to GitCurator.
- **Mobile tab grid** — the 8 tabs lay out as a 2×4 grid on phones
  (replacing horizontal scroll); "Risks & Roadmap" shortens to "Risks"
  below the sm breakpoint; trigger typography scales responsively.
- Export JSON now includes the releases/repo snapshot; footer lists
  /api/releases; eslint config ignores the staging repos/ tree.

### Notes
- First release produced by the complete round-trip: sandbox QA → staging
  mirror → VERSION/CHANGELOG bump → tag v0.0.2 → GitHub push →
  GitCurator-v0.02.zip backup (download/ + public/).


## [0.0.1] — Initial repository import (V0.01)

First versioned snapshot of GitCurator, published to GitHub as the canonical
online home for all future development. Internal build lineage: the code
below already carries the full v30.x hardening sprint (v30.0 → v30.4).

### Python application (`app/`)
- **Core pipeline** — Telegram Saved Messages → link extraction → GitHub
  repo metadata → local Ollama LLM analysis → structured Obsidian notes,
  with PyQt6 GUI and a headless CLI mode.
- **v30 hardening sprint (all fixes verified by 45 automated tests):**
  - Headless mode can no longer hang (LLM-failure / disk-full / auth-code
    signals all have receivers or non-blocking defaults).
  - `save_config()` rewritten as a merge (unknown keys preserved, atomic
    write) — the "model choice never persisted" bug is fixed end-to-end.
  - All Ollama calls are timeout-wrapped (`llm_timeout_s`, default 300 s).
  - Atomic note/banner writes via tempfile + `os.replace`.
  - URL parsing converged into a single `links.py` module (4 previous
    drifting copies removed).
  - LLM output sanitized before frontmatter (YAML injection neutralized);
    README content treated as untrusted input.
  - Cloudflare stack: HMAC signing fixed (sha256 of shared secret),
    webhook secret-token verification added, stale root-worker duplicates
    quarantined and deleted; `cloudflare-bot/` is canonical.
  - Testable core extracted: `links.py`, `storage.py`, `note_builder.py`,
    `llm_client.py` + 34 unit tests and 11 end-to-end tests (45/45 green).
  - CacheDB hardened: `busy_timeout`, connect timeout, cross-thread locking,
    idempotent close.

### Verification dashboard (`dashboard/`)
- Next.js 16 live verification console for the Python app:
  - `/api/verify` — on-demand compile gate (9 files) + full test run with
    per-case timing and code fingerprints (sha256 per file).
  - `/api/history` — SQLite-persisted run history (Prisma), drift detection
    ("working tree changed since last verification"), timing insights,
    regression/flaky detection, CSV export.
  - 7-tab UI: Overview, Fixes, Audit, Tests, Go-Live checklist, History,
    Insights — dark/light/system themes, fully responsive, WCAG-checked.
- Portable layout: expects the Python app at `../app` (override with
  `GITCURATOR_APP_DIR`).

### Notes
- Runtime caches (`app/cache.db`, `app/error_outbox.db`,
  `dashboard/db/custom.db`) are gitignored; zip backups include them.
