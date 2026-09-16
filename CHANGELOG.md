# Changelog

All notable changes to GitCurator are documented here.
Versioning: [SemVer](https://semver.org/) — `MAJOR.MINOR.PATCH`, tagged `vMAJOR.MINOR.PATCH`.

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
