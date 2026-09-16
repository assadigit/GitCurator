# GitCurator

> Telegram → Ollama → Obsidian. An automation that watches your Telegram
> Saved Messages for GitHub repositories, curates them with a **local LLM**,
> and writes clean, structured notes into your **Obsidian vault**.

**Version:** `0.0.7` (see [CHANGELOG.md](CHANGELOG.md) · [VERSION](VERSION))
**Status:** modular `gitcurator` package + public Good Repos directory + pastel UI · 73/73 automated tests green.

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

## UI standards (v0.0.6 + v0.0.7 pastel)

The desktop GUI follows a small, explicit set of visual rules:

- **Fixed 1000×750 window** — never resizes between tabs; every tab scrolls
  independently and starts at the same top position at its natural height.
- **One growable region per tab** — the results/list/log panel absorbs the
  leftover vertical space; forms and buttons stay content-sized.
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
| `app/main.py` | Thin launcher — the real entry point is `gitcurator.gui.app.main` |
| `app/gitcurator/core/` | Pure-stdlib testable core — `links` · `storage` · `note_builder` · `llm_client` |
| `app/gitcurator/integrations/` | Telegram fetchers · `vaultseal` (private backup) · `goodrepos` (public directory) · `error_reporter` |
| `app/gitcurator/cloud/` | Cloudflare + Google Drive integrations (optional, graceful) |
| `app/gitcurator/gui/` | `app.py` — MainWindow, pipeline worker, headless CLI |
| `app/gitcurator/tools/` | Developer utilities (diagnostics, import-surface docs) |
| `app/gitcurator/constants.py` | Shared design tokens + config defaults |
| `app/tests/` | 73 tests (34 unit + 11 e2e + 28 goodrepos) — fake Ollama + fake GitHub, zero pip |
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

This repository ships **private** because it contains live credentials:

- `app/config.json` — Telegram bot token, api_id/api_hash, phone
- `app/session.session` (+ `.bak`) — Telethon auth sessions (full account access)
- `app/installer.config.json`, `app/cloudflare-bot/` — bot token + Cloudflare account references

**Do not make this repository public without rotating every credential above.**
The dashboard's Go-Live tab tracks this rotation checklist (3 × P0 items).

## License

Private project — all rights reserved.
