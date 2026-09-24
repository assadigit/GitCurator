# GitCurator

> Telegram → Ollama → Obsidian. An automation that watches your Telegram
> Saved Messages for GitHub repositories, curates them with a **local LLM**,
> and writes clean, structured notes into your **Obsidian vault**.

**Version:** `0.09.2` — launcher regression fixed: the `.bat` double-click wall of "'tlocal' / 'Double-click' is not recognized" errors (see [CHANGELOG.md](CHANGELOG.md) · [VERSION](VERSION))
**Status:** redesigned PyQt6 GUI (v0.03 two-stage SYNC: fetch undone bot items → PROCESS → run; v0.05 auto-starts `ollama serve` when the server is down; v0.06 reliability fixes; **v0.07 main screen rebuilt per design review** — one unified SVG icon set, one-accent palette, labeled `PROCESSED x / y` counter, active-state log filter tabs; **v0.07.1 hotfix** — official Lucide icons bundled verbatim, proxy pre-flight, DC-rotating connect retries, token whitespace healing; **v0.08** — every icon re-fit to its area (they were rendering 2× too big and clipped — the "partial sun"), window re-proportioned 1000×375 → **900×600 (exact 6:4)**, **404 QUARANTINE** (dead links confirmed after 3 consecutive 404s across sessions are silently skipped in every input path), and a **visualized CLI companion** — `cli.py` + `Start-GitCurator-CLI.bat`, colors/spinners/live progress, zero flags needed) on the modular `gitcurator` package · 101/101 automated tests green · v0.04 ships the owner's real credentials pre-filled in `app/config.json` + `installer.config.json` (private repo, owner's explicit request — the v0.0.10 credential-free guarantee is lifted for this release line).

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


## The CLI companion (v0.09 — one click, fully visualized, zero extra deps)

Prefer a terminal? Keep `config.json` in the project folder (the SAME
file the GUI uses) and double-click:

```
Start-GitCurator-CLI.bat      (Windows — same engine as GitCurator-CLI.bat)
GitCurator-CLI-Setup.bat      (Windows — first-run credential wizard)
./gitcurator-cli.sh           (Linux/macOS)
```

v0.09 merges the two lineages' CLIs into ONE engine (`main.py --cli` →
`gitcurator/cli.py` — plain ANSI, no `rich` dependency). It carries the
v0.07.2 **model picker**: pre-flight checks run BEFORE any work, and when
the configured Ollama model isn't pulled you get an interactive numbered
menu (smart recommendation first — same family/size as the configured
model, embedding models flagged and never recommended); scheduled/piped
runs auto-pick the closest stand-in so a missing model can never fail or
degrade a batch. The choice is saved to `config.json`.

That's the whole interface. The CLI then runs the complete pipeline
automatically, in color, with loading animations:

```
  ╔════════════════════════════════════════╗
  ║   GitCurator — Telegram → GitHub →     ║   ← ASCII banner + version
  ║            Ollama → Obsidian           ║
  ╚════════════════════════════════════════╝
  ── run configuration ────────────────────   ← masked secrets, vault, proxy
  ── pre-flight checks ────────────────────   ← spinner per check
    ✓ GitHub token   authenticated as you · 4996 API calls left
    ✓ Proxy          127.0.0.1:10808 reachable (socks5, 3 ms)
    ✓ LLM provider   Ollama up · 3 model(s)
  ── fetching the bot queue ───────────────   ← live worker log + spinner
     Bot queue: 42 total · 18 in vault · 7 dead · 12 to process
  ── processing 12 repo(s) ────────────────   ← live bar: repo · ██ 7/12 · 0:42
  ── VaultSeal / Good Repos ───────────────   ← post-run, same as the GUI
  run summary: processed / warnings / retry queue / quarantined / elapsed
```

Useful flags: `--init` (credential wizard) · `--auto` (SYNC → process →
seal → publish) · `--status` (config + cache + quarantine summary) ·
`--retry-failed` · `--mark-read` · `--import-file urls.txt` ·
`--from-id/--to-id`, `--offset-start/--count`, `--single-id` ·
`--list-dead` / `--reset-dead` (quarantine management, ported from the
v0.08 companion) · `--strikes N` (quarantine threshold for this run) ·
`--vault` / `--config` overrides.

The CLI supports Ctrl+C as a graceful stop and delivers the worker's
signals to the renderer through a main-thread bridge so the live
display stays thread-safe.

## The 404 quarantine (v0.09 — dead links stop wasting runs, tunably)

A GitHub link that returns **404 N times in a row — counted across
sessions** — is confirmed dead and silently skipped from every input
path (bot queue, Telethon channel fetch, import files, retries) before
any GitHub API call. Attempt logging stays quiet (`🗑️ 404, attempt 1/N`);
reaching the threshold logs `QUARANTINED` and writes its single permanent
row into `_inbox/notfound-links/notfound_links.md`. Batch summaries
report the skipped count in one aggregate line — no more per-URL 404 spam
from the same six dead repos every run.

**v0.09 unifications (from the merged lineage):**

- **The threshold is configurable again** — `notfound_strike_threshold`
  in `config.json` (default 3, min 2), set from the GUI
  (*Settings → Dashboard → "Deleted Repos — 404 Quarantine"*) or the CLI
  (`--strikes N`). v0.08 had it hardcoded to 3.
- **Consecutive semantics restored** — a SUCCESSFUL fetch resets the
  counter (v0.08 counted attempts forever, so stale strikes from months
  ago could quarantine a live repo after one more transient miss).
- **v0.07 caches migrate automatically** — the old `notfound_strikes`
  table moves into `decommissioned_repos.fail_count` on first open
  (higher count wins when a URL exists in both).
- **Two viewers** — Settings → Dashboard shows in-progress attempts AND
  confirmed rows (⛔) with the threshold spinbox; `More ▸ 🚫 View 404
  Quarantine` lists confirmed-dead with one-click reset. CLI:
  `--list-dead` / `--reset-dead`.

False positives recover: a repo that went PRIVATE reads as 404 to an
unauthorized token, so a reset gives every link a fresh set of attempts.

## Reliability (v0.06 — the "another operation is already running" fix)

The v0.05 forever-bug had two independent root causes, both fixed and both
covered by automated reproduction tests:

- **Stuck Telegram lock** — a hung telethon subprocess (dead proxy, session
  contention) could block the fetch worker forever, so the single-operation
  lock never released and every Telegram button logged
  `⏳ Another Telegram operation is already running`. The new
  `integrations/subprocess_runner.py` kills stalled children (idle timeout +
  30-min hard cap, interactive-auth grace), `gui/telegram_lock.py` tracks
  lock OWNERS (the busy message now names the holder and its age), 15
  early-return paths release correctly, two signal-ordering self-deadlocks
  are deferred, every worker always emits its finished signal, and a
  watchdog force-releases anything held implausibly long.
- **Zombie process → "Another instance is already running"** — modal dialogs
  fired by the 2-second startup timer outlived the main window;
  `app.exec()` never returned, `app.lock` was never cleaned up, and the next
  launch refused to start. Shutdown is now guarded (`_closing` flag + modal
  guards + explicit `QApplication.quit()`), so the process always dies and
  the lock file always gets removed.

Same release: set-backed link dedup (O(n²) → O(n)), SQLite WAL + hot-column
indexes, vault filtering moved off the GUI thread, no more per-link manifest
rewrites, and telethon imports deferred out of the GUI process.

## UI standards (v0.0.6 + v0.0.7 pastel, v0.0.8 toggle, v0.07 main screen)

The desktop GUI follows a small, explicit set of visual rules:

- **Fixed 1000×375 main window** — never resizes; the log panel is the one
  growable region, every Settings page scrolls independently at its natural
  height.
- **One growable region per screen** — the results/list/log panel absorbs the
  leftover vertical space; forms and buttons stay content-sized.
- **One icon system (v0.07.1)** — every glyph is the REAL Lucide icon
  pack (v0.544.0, ISC license) bundled verbatim in `gitcurator/gui/icons.py`
  — official geometry, not redraws — tinted per theme at render time (no
  emoji, no pixel-art, no mixed rendering styles).
- **One accent + semantic states (v0.07)** — lavender is the only
  interactive-chrome accent (hero CTA fill, filter-tab active state,
  progress chunk, links); green/amber/red appear ONLY as success/warning/
  error states (proxy health, log levels, the STOP kill-switch).
- **Status always labeled and truthful (v0.07)** — the `PROCESSED x / y`
  counter shows live batch counts, fetched-queue counts, or the last
  manifest's real totals — never a blank placeholder; the proxy dot reads
  `Connected / Idle / Error` with a matching semantic text color.
- **Always-visible light/dark toggle (v0.0.8)** — a compact moon/sun icon
  button in the top bar flips the full cream/plum pastel theme and persists
  the choice; the tooltip always names the current mode (never color alone).
- **Button variants** — filled lavender hero (main screen), filled pastel-mint
  primary (Settings, exactly one per page), outlined violet secondary, red
  filled danger (`#D63A24`, white text) for destructive actions only;
  everything infrequent (tests, verify, export, retry, recategorize) lives
  in the **More ⋯ menu**.
- **Pastel palette, AA contrast** — cream day (`#FBF8F2` + white sheets +
  warm-sand borders) / plum night (`#2B2639` sheets, recessed `#17131F` log
  well, lavender accents); every text pair ≥ 4.5:1 including placeholder
  text (`QPalette.PlaceholderText`); red only for errors; pending counts
  neutral.
- **Design tokens** — 4/8/16/24/32/48px spacing scale; type scale of four
  sizes (16 titles / 13 labels / 12 body / 12 mono); 2px focus outlines
  on every interactive element, both themes; accessible names on every
  icon-only control.

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
| `app/` | The Python application: PyQt6 GUI + visualized CLI (start with `app/README.md`) |
| `app/main.py` | Thin launcher — the real entry point is `gitcurator.gui.app.main` |
| `app/cli.py` + `Start-GitCurator-CLI.bat` | **Visualized CLI** (v0.08) — one-click, zero flags; real code in `gitcurator.cli_app` |
| `app/gitcurator/core/` | Pure-stdlib testable core — `links` · `storage` · `note_builder` · `llm_client` |
| `app/gitcurator/integrations/` | Telegram fetchers · `vaultseal` (private backup) · `goodrepos` (public directory) · `error_reporter` |
| `app/gitcurator/cloud/` | Cloudflare + Google Drive integrations (optional, graceful) |
| `app/gitcurator/gui/` | `app.py` — MainWindow, pipeline worker, headless CLI · `icons.py` — the Lucide pack |
| `app/gitcurator/tools/` | Developer utilities (diagnostics, import-surface docs) |
| `app/gitcurator/constants.py` | Shared design tokens + config defaults |
| `app/tests/` | 101 tests (34 unit + 11 e2e + 28 goodrepos + 19 reliability + 9 quarantine) — fake Ollama + fake GitHub, zero pip |
| `app/cloudflare-bot/` | Optional Cloudflare Worker deployment (canonical copy) |
| `dashboard/` | Next.js 16 verification console for the app (live tests, history, drift detection) |

## Quickstart — the app (Windows)

```bat
cd app
pip install -r requirements.txt
python main.py            :: GUI mode
python main.py --help     :: headless mode options
```

Requirements: Python 3.10+, a running [Ollama](https://ollama.com) server,
and (optionally) Telegram API credentials in `app/config.json`.
Run the test suite: `python -m unittest tests.test_core tests.test_e2e -v`

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
12 audited modules + the 73-case suite (no pip installs needed; the
testable core is pure stdlib). The dashboard's Releases tab shows the
live status when a read-only `GITCURATOR_GH_TOKEN` is configured.

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
