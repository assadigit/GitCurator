# GitCurator

> Telegram → Ollama → Obsidian. An automation that watches your Telegram
> Saved Messages for GitHub repositories, curates them with a **local LLM**,
> and writes clean, structured notes into your **Obsidian vault**.

**Version:** `0.0.3` (see [CHANGELOG.md](CHANGELOG.md) · [VERSION](VERSION))
**Status:** v30.x hardening sprint complete — 45/45 automated tests green.

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

## Repository layout

| Path | What it is |
|---|---|
| `app/` | The Python application: PyQt6 GUI + headless CLI (start with `app/README.md`) |
| `app/main.py` | Orchestrator, GUI, worker threads, headless runner |
| `app/links.py` · `storage.py` · `note_builder.py` · `llm_client.py` | Pure-stdlib testable core |
| `app/tests/` | 34 unit tests + 11 end-to-end tests (fake Ollama + fake GitHub) |
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
backups, and the last 10 GitHub Actions runs).

## Versioning

- Semantic versioning `MAJOR.MINOR.PATCH`, git tags `vMAJOR.MINOR.PATCH`.
- **0.0.4** — Provenance & CI History: every verification run records its
  origin (manual / startup / scheduled — badges + stats in the History tab,
  source column in the CSV export); the Releases tab gains a CI run-history
  strip (last 10 Actions runs, each chip linking to its run) plus a
  version-history timeline rail and card-hover polish.
- **0.0.3** — CI Pipeline & Actions Status: GitHub Actions runs the
  45-test gate on every push/tag/PR; the Releases tab shows live CI status.
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
9 audited modules + the 45-case suite (no pip installs needed; the
testable core is pure stdlib). The dashboard's Releases tab shows the
live status when a read-only `GITCURATOR_GH_TOKEN` is configured.

## Security — read before deploying

This repository ships **private** because it contains live credentials:

- `app/config.json` — Telegram bot token, api_id/api_hash, phone
- `app/session.session` (+ `.bak`) — Telethon auth sessions (full account access)
- `app/installer.config.json`, `app/cloudflare-bot/` — bot token + Cloudflare account references

**Do not make this repository public without rotating every credential above.**
The dashboard's Go-Live tab tracks this rotation checklist (3 × P0 items).

## License

Private project — all rights reserved.
