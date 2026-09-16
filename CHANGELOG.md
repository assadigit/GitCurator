# Changelog

All notable changes to GitCurator are documented here.
Versioning: [SemVer](https://semver.org/) — `MAJOR.MINOR.PATCH`, tagged `vMAJOR.MINOR.PATCH`.

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
