# GitCurator

> Telegram → Ollama → Obsidian. An automation that watches your Telegram
> Saved Messages for GitHub repositories, curates them with a **local LLM**,
> and writes clean, structured notes into your **Obsidian vault**.

**Version:** `0.0.10` (see [CHANGELOG.md](CHANGELOG.md) · [VERSION](VERSION))
**Status:** v32.4 modular layout — the 9,770-line `gui/app.py` monolith
became a 164-line back-compat facade over focused modules, and the two
remaining giants followed: `gui/main_window.py` (6,130 lines) and
`gui/workers.py` (2,138 lines) are now single-concern mixin packages
(`gui/main_window/` with 13 mixins, `gui/workers/` with the pipeline
phases) · 89/89 automated tests green · headless CLI works from any
launch directory (and via `python -m gitcurator`) · credential-free git
tree (v0.0.10). Also see [docs/SWOT_ANALYSIS.md](docs/SWOT_ANALYSIS.md).

---

## How it works

```
┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│   Telegram   │   │  Link & repo  │   │     Local    │   │   Obsidian   │
│    Saved     ├──▶│  extraction   ├──▶│    Ollama    ├──▶│    vault     │
│  Messages    │   │  (links.py)   │   │  (llm_client)│   │  (notes.md)  │
└──────────────┘   └──────────────┘   └──────────────┘   └──────────────┘
       ▲                  │                  │                  │
       │           Telethon fetcher    timeout-wrapped     sanitized frontmatter
       │           / bot queue         chat calls         + atomic writes
   PyQt6 GUI ── or ── headless CLI (run_headless)
```

1. **Fetch** — new messages arrive from Telegram (Telethon subprocess reading
   Saved Messages, or the bot-chat queue via `@githubfetcherbot`).
2. **Extract** — GitHub URLs are normalized and deduplicated by the single
   `links.py` module (one pattern, four former drifting copies removed).
3. **Curate** — a local Ollama model categorizes, summarizes and cross-checks
   each repository (all calls timeout-wrapped; failures degrade gracefully).
4. **Write** — sanitized, structured Obsidian notes (frontmatter + banners)
   land in your vault via atomic tempfile + `os.replace` writes.
5. **Seal** — the whole vault is committed and pushed to a **private GitHub
   repository** after every run (`vaultseal.py`) — Obsidian's free tier has no
   sync, VaultSeal is the safety net.
6. **Publish** — the curated notes become an emoji-rich **public README
   directory** (`goodrepos.py`) organized like *AI → Skills → …*, with the
   notes mirrored into category folders of the public `good-repos` repo.

## UI standards (v0.0.6 + v0.0.7 pastel, v0.0.8 toggle)

The desktop GUI follows a small, explicit set of visual rules:

- **Fixed 1000×750 window** — never resizes between tabs; every tab scrolls
  independently and starts at the same top position at its natural height.
- **One growable region per tab** — the results/list/log panel absorbs the
  leftover vertical space; forms and buttons stay content-sized.
- **Always-visible light/dark toggle (v0.0.8)** — a compact 🌙/☀️ icon button
  in the action row flips the full cream/plum pastel theme and persists the
  choice; the tooltip always names the current mode (never color alone).
- **Three button variants** — filled pastel-mint primary (exactly one per
  tab, deep-forest text), outlined violet secondary, filled pastel-rose
  danger; everything infrequent (tests, verify, export, retry,
  recategorize) lives in the **More ⋯ menu**.
- **Pastel palette, AA contrast** — cream day (`#FBF8F2` + white sheets +
  warm-sand borders) / plum night (`#2B2639` sheets + lavender accents);
  mint `#B9E3C9`+`#17402B`, violet `#5F54B4`, rose `#F6C6CD`+`#5E1120` —
  every text pair ≥ 4.5:1; red only for errors; pending counts neutral.
- **Status always labeled** — the proxy dot reads `Connected / Idle / Error`;
  the progress bar appears only while a batch runs
  (`Processing X of Y — owner/repo`), and batches over 10 items confirm
  their exact count first.
- **Design tokens** — 4/8/16/24/32/48px spacing scale; type scale of four
  sizes (16 titles / 13 labels / 12 body / 12 mono); 2px focus outlines
  on every interactive element, both themes.

## Resilience (v0.0.8)

Real-world Windows runs surfaced three failure modes — all fixed:

- **401 bad-credentials fallback** — an expired/rotated GitHub token used
  to fail every repo of a batch with raw `401` JSON. The first 401 now
  logs ONE actionable error, drops the token for the rest of the batch
  (anonymous access, 60 req/h), retries the current repo, and continues.
- **`🔑 Test GitHub Token`** (Credentials tab) — validates the token as
  typed *before* Save via `GET /user`: shows the account login on success,
  an actionable 401/403 message on failure.
- **Windows-safe MOC filenames** — LLM categories with quotes or `>`
  separators (`"Agents_Skills"`, `AI > Skills`) no longer crash master-index
  generation with `[Errno 22]`; one canonical sanitizer keeps the
  `_moc/` files and their `[[_moc/…]]` wiki-links in sync.

## Usability (v0.0.9)

The Backup tab fits the fixed window again, and theme switches keep every
status readable:

- **Scrollable, compacted Backup tab** — the four sections (Vault Backup,
  VaultSeal, Good Repos, Dashboard) sit in a vertical-only scroll area with
  compacted rows (status dots share the action rows, settings checkboxes
  side-by-side), so every control is reachable inside the fixed 1000×750
  window.
- **Theme-synced status dots** — toggling light/dark re-runs all three
  Backup status refreshers (no more light-theme deep-butter stranded on
  plum), and the dark scrollbar handle is readable on plum (WCAG 1.4.11).

## Security & Deploy (v0.0.10)

The git tree is now credential-free, and updating the Cloudflare worker to
the latest version is a two-minute command:

- **Scrubbed tree** — `session.session` files untracked + gitignored,
  `config.json` / `installer.config.json` are clean templates, and the docs
  use `YOUR_BOT_TOKEN` placeholders. Git history predating v0.0.10 still
  holds the old blobs — rewrite history (`git filter-repo`) before ever
  making the repository public.
- **`deploy-latest.ps1` / `deploy-latest.sh`** (in `app/cloudflare-bot/`) —
  wrangler auth check → idempotent D1 schema → `wrangler deploy` → live
  health check, with an optional `--with-secret` flow for the v30
  `WEBHOOK_SECRET` webhook anti-impersonation hardening. Secrets, data, D1,
  KV, Queues and R2 all persist across deploys.
- **Old release zips removed** — v0.01–v0.09 zips all carried the Telethon
  session (v0.01–v0.07 in the working tree; v0.08/v0.09 hidden inside the
  bundled `.git/objects` store) and were deleted from the download folders.
  The v0.0.10 zip is built via `git archive` — tracked tree only, no `.git`,
  no sessions, no caches.

## VaultSeal — automatic vault backup (v0.0.5)

Obsidian's free mode has no sync and no off-site backup. VaultSeal closes
that gap: after **every** curation run — 1 new project or 100 — the whole
vault is committed and pushed to a private GitHub repository. Restore is
plain `git clone`: the full vault at any point in its history, and Obsidian
opens the clone directly.

- **Zero-config** — on by default (`vaultseal.enabled`), reuses your
  `github_token`, derives the repo name from the vault folder, and creates
  the private repo on the first seal if it doesn't exist.
- **Hygiene** — machine-specific state (`workspace.json`, `.trash/`, OS
  noise) is excluded via a managed `.gitignore` block, merged idempotently.
- **Never dangerous** — a vault nested inside another git repository
  bootstraps its own repo (a parent's files can never be sealed); an
  unchanged vault is a no-op; a failed push keeps the local commit.
- **Credential hygiene** — the token is used only for API calls and one-time
  push URLs; it is never written to `.git/config`, never persisted, never
  logged.
- **Runs for failed batches too** — notes written before a mid-run failure
  are exactly what you want backed up.

Manual seal / status: `python app/gitcurator/integrations/vaultseal.py
--vault <path> [--token $GITHUB_TOKEN]` · configure in the GUI's Backup tab
or `config.json` (`vaultseal` section). The dashboard's **Vault Seal** tab
shows live vault state and the seal history (`GET /api/vault-seal`).

## Good Repos — the public curated directory (v0.0.7)

VaultSeal keeps the vault private; **GoodRepos shares the curation with the
world**. After every run, the curated notes become a browsable, emoji-rich
README directory — organized like *AI → Skills → …* — with the full notes
mirrored into category folders of the **public**
[`good-repos`](https://github.com/assadigit/good-repos) repository. Everyone
can benefit from your curated collection of good GitHub repositories.

- **Emoji README, auto-maintained** — stats line, contents anchors, category
  tree with per-category counts, and one line per repo: link, TL;DR, stars,
  language, tags. Regenerated after every run.
- **History-preserving publishes** — the module fetches the remote history
  first (`fetch` + `reset --mixed`), so each publish is a real diff on top of
  the previous directory; an unchanged vault is a no-op.
- **Public by design** — creates the repo as public via the API if missing
  (warns if an existing repo is private, still publishes).
- **Same hygiene as VaultSeal** — pure stdlib, token never persisted, never
  logged; only curated notes + `_index.md` + `links_manifest.json` are
  copied (no config, no sessions).
- **Derived data** — the vault (and its private VaultSeal mirror) remains
  the source of truth; delete `good-repos` and the next run recreates it.

Manual publish / status: `python app/gitcurator/integrations/goodrepos.py
--vault <path> [--repo-name good-repos] [--token $GITHUB_TOKEN]` · configure
in the GUI's Backup tab or `config.json` (`goodrepos` section). The
dashboard's **Good Repos** tab shows the live directory scan and the publish
history (`GET /api/goodrepos`).

## Repository layout

| Path | What it is |
|---|---|
| `app/` | The Python application: PyQt6 GUI + headless CLI (start with `app/README.md`) |
| `app/main.py` | Thin launcher — the real entry point is `gitcurator.cli.main` (re-exported by the `gui/app.py` facade) |
| `app/gitcurator/cli.py` | Headless CLI (`run_headless`) + argv dispatch / app.lock / QApplication bootstrap (`main`) |
| `app/gitcurator/gui/` | `main_window/` package (MainWindow assembled in `window.py` from 13 mixins: styles · ui_setup · settings_tests · search · config_ui · processing_ctl · log_panel · vault_ops · bot_queue · bot_links · dialogs · backup_tab · publish_services) · `workers/` package (ProcessingWorker signals+init in `processing.py`, phase mixins: `_auth` · `fetch` · `llm` · `assets` · `scoring` · `notes` · `pipeline`; TestWorker) · `log_handler.py` · `_qt.py` (guarded PyQt6 import) · `app.py` (back-compat facade) |
| `app/gitcurator/core/` | Pure-stdlib testable core — `links` · `storage` · `note_builder` · `llm_client` · `vault` · `cache_db` · `link_tracker` · `inbox` |
| `app/gitcurator/utils/` | `logging_setup` · `terminal` (guarded colorama) |
| `app/gitcurator/integrations/` | `telegram_jobs` (subprocess fetch jobs) · Telegram fetchers · `vaultseal` (private backup) · `goodrepos` (public directory) · `error_reporter` |
| `app/gitcurator/cloud/` | Cloudflare + Google Drive integrations (optional, graceful — currently dormant) |
| `app/gitcurator/constants.py` | Shared design tokens, config defaults and the APP_DIR path anchoring (`resolve_app_path`) |
| `app/gitcurator/__main__.py` | `python -m gitcurator` entry (PEP 338) — delegates to `cli.main` |
| `app/tests/` | 89 tests (34 unit + 11 e2e + 28 goodrepos + 16 CLI smoke) — fake Ollama + fake GitHub, zero pip |
| `app/cloudflare-bot/` | Optional Cloudflare Worker deployment (canonical copy) |
| `dashboard/` | Next.js 16 verification console for the app (live tests, history, drift detection) |
| `docs/SWOT_ANALYSIS.md` | Evidence-based SWOT audit of v0.0.10 (Phase-2 deliverable) |

## Quickstart — the app (Windows)

```bat
cd app
pip install -r requirements.txt   (includes PySocks — required for the
                                   default socks5 proxy configuration)
python main.py            :: GUI mode
python main.py --help     :: headless mode options (works from any cwd,
                            even before the deps are installed)
```

The headless CLI works from **any** launch directory since v32.3 —
`--config` / `--import-file` / `cache.db` / `session.session` /
`system_prompt.txt` / `app.lock` are anchored to the app folder, and a
failed headless run exits `1` (not `0`), so schedulers can detect it.

Requirements: Python 3.10+, a running [Ollama](https://ollama.com) server,
and (optionally) Telegram API credentials in `app/config.json`.
Run the test suite: `python -m unittest tests.test_core tests.test_e2e tests.test_goodrepos tests.test_cli -v`

## Quickstart — the dashboard

```bash
cd dashboard
bun install            # or npm install
bun run db:push       # create the SQLite history database
bun run dev           # http://localhost:3000
```

The dashboard expects the Python app at `../app` (override with
`GITCURATOR_APP_DIR`). It runs the real test suite on demand
(`POST /api/verify`), persists every run — tagged by who triggered it
(manual button, server-startup pass or the 6-hour scheduler) — flags code
drift between runs, and reports live repository/release status
(`GET /api/releases` — last commit, tags, dirty files, CHANGELOG, zip
backups, and the last 10 GitHub Actions runs). The **Vault Seal** tab
(`GET /api/vault-seal`) shows the live git state of your vault and the
seal history; "Seal vault now" runs the real `app/gitcurator/integrations/vaultseal.py` (local
commit — pushing stays the app's job; the dashboard holds no GitHub token
by design). Optionally point it at a vault with `GITCURATOR_VAULT_DIR`.

## Versioning

- Semantic versioning `MAJOR.MINOR.PATCH`, git tags `vMAJOR.MINOR.PATCH`.
- **0.0.4** — Provenance & CI History: every verification run records its
  origin (manual / startup / scheduled — badges + stats in the History tab,
  source column in the CSV export); the Releases tab gains a CI run-history
  strip (last 10 Actions runs, each chip linking to its run) plus a
  version-history timeline rail and card-hover polish.
- **0.0.3** — CI Pipeline & Actions Status: GitHub Actions runs the
  73-test gate (12 py_compiles) on every push/tag/PR; the Releases tab shows live CI status.
- **0.0.2** — Repository & Release Console: `/api/releases` + Releases tab,
  header backup download, version alignment, mobile 2×4 tab grid.
- **0.0.1** — initial repository import. The internal build lineage
  (v30.0 → v30.4) maps to this snapshot; see `CHANGELOG.md`.
- Future releases: every meaningful change set ships as a new tag, and the
  working tree on `main` is always the latest version. Each release also
  produces a `GitCurator-vX.YY.zip` snapshot (source + .git history +
  runtime DBs) served by the dashboard's Releases tab.

## CI

Every push, tag and PR runs `.github/workflows/ci.yml` — py_compile of the
24 audited modules + the 87-case suite (no pip installs needed; the
testable core is pure stdlib and the CLI subprocess tests run on bare
Python). The dashboard's Releases tab shows the live status when a
read-only `GITCURATOR_GH_TOKEN` is configured.

## Security — read before deploying

Since v0.0.10 the git tree ships **without live credentials** (session files
untracked, configs are templates, docs use placeholders). Two things remain:

- **Git history** still contains pre-scrub blobs (`session.session`, real
  tokens) in commits before v0.0.10. The repository is private — keep it
  that way, or run `git filter-repo` + force-push BEFORE any visibility flip.
- **Credential rotation is still the standing P0** — every value was exposed
  in chat during development. Rotate the Telegram bot token / api credentials
  / sessions and the GitHub PAT, then update them in the app (Settings →
  Credentials → '🔑 Test GitHub Token') and on the worker
  (`npx wrangler secret put BOT_TOKEN`). The dashboard's Go-Live tab tracks
  this rotation checklist (3 × P0 items).

## License

Private project — all rights reserved.
