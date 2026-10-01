# GitCurator

> **Your personal AI librarian.** Forward any link to a Telegram bot — GitHub
> repositories, tools, articles, any website — and GitCurator's LLM curates
> it into a clean, structured, cross-linked Obsidian library: categorized,
> summarized, deduplicated, backed up to private GitHub mirrors, and
> (optionally) published as a public directory.

**Version:** `0.25.0` · **Suite:** 826 automated tests + a 30-link golden set ·
**Releases:** every version since v0.14.1 ships a deterministic Windows zip ·
**Status:** all six SPEC phases done and merged — session history
archived in [docs/history/](docs/history/) · [CHANGELOG.md](CHANGELOG.md) · [app/README.md](app/README.md)

> Public repository (since 2026-09-30, after a full history scrub — see
> [Security](#security)).

---

## Executive summary — what this app is

GitCurator is a **personal "second brain" library builder**. It solves a
problem every heavy internet reader has: you save dozens of links in Telegram
"Saved Messages" (or send them to a bot), they pile up in an unsorted mess,
and you never look at them again.

GitCurator turns that pile into a real library, automatically:

1. **You forward links to the Telegram bot** [@githubfetcherbot](https://t.me/githubfetcherbot)
   (from your phone, desktop, anywhere). The bot — a Cloudflare Worker —
   replies instantly, records every link in a permanent ledger (D1 database),
   and never loses one.
2. **Your desktop app fetches the queue** (one **SYNC** button — or a fully
   automatic CLI run), and routes each link: GitHub repositories go to the
   GitHub pipeline, every other website to the Websites pipeline.
3. **An LLM curates each link** — locally with **Ollama** or **llama.cpp**
   (your models, your machine, nothing leaves it), or through a cloud API
   (**Anthropic Claude** or any **OpenAI-compatible** endpoint). It fetches
   the repo/site, categorizes it into *your* taxonomy, and writes a rich
   Obsidian note: TL;DR, what it is, standout features, best-used-for,
   pricing, tags.
4. **Notes land in three separate Obsidian vaults** — a GitHub Projects
   vault, a Websites vault, and your own Manual Notes vault the app never
   touches except a read-only `Library/` mirror.
5. **After every run, everything is sealed** — both machine vaults are
   committed and pushed to private GitHub repositories (VaultSeal), and the
   curated collection can be published as an emoji-rich public directory
   (Good Repos).

The design principles, in one breath: **local-first, private by default, no
link left behind, your moves are corrections (never damage), existing notes
are never rewritten, and every risky operation has a dry-run.**

| Component | Stack |
|---|---|
| Desktop app | Python 3.10+ / PyQt6 GUI + a visualized CLI (Windows-first, `.bat` launchers) |
| Bot backend | Cloudflare Worker + D1 + KV + Queues (`app/cloudflare-bot/`) |
| LLM backends | Ollama · llama.cpp · Anthropic Claude · any OpenAI-compatible endpoint |
| Storage | Obsidian vaults (Markdown) + `cache.db` (SQLite) |
| Backup | private GitHub mirrors per vault (VaultSeal) + optional public directory (Good Repos) |
| Tests | 826 automated cases, zero network needed; 77 modules compiled in CI |

## How it works

```
        you, on any device
              │   forward any link (GitHub repo or website)
              ▼
   Telegram ── @githubfetcherbot
              │   webhook — instant reply (✓ Received / ⭐ repo info)
              ▼
   Cloudflare Worker  (app/cloudflare-bot)
     D1 ledger · dedup · blocked/self-domain policy──▶  its web dashboard:
              │   bot queue (no link is ever lost)        stats · pending ·
              ▼                                          dead letters · activity
   Desktop app — SYNC  (PyQt6 GUI · visualized CLI · headless)
     ├─ GitHub pipeline   → GitHub API   → notes + banner images
     └─ Websites pipeline → polite fetch → taxonomy filing
              │
              │   curated by YOUR LLM — Ollama · llama.cpp · Claude · any
              │   OpenAI-compatible endpoint
              ▼
   Obsidian vaults
     ├─ GitHub Projects vault   (machine-written)
     ├─ Websites vault          (machine-written)
     └─ Manual Notes vault      (yours; read-only Library/ mirror)
              ▼
   after every run:  VaultSeal (private GitHub mirrors)
                      + Good Repos (the public directory, optional)
```

1. **Send** — forward any link to the bot. GitHub repos get a reply with
   stars and description; websites get a "🌐 Received" reply; blocked
   domains (x.com / twitter.com / t.co by default) get an honest 🚫.
2. **Ledger** — the Worker normalizes the URL (tracking parameters dropped,
   meaningful ones kept — a YouTube `?v=` **is** the page), dedupes against
   the permanent D1 ledger, and queues it. Secret query values are scrubbed
   from everything it stores.
3. **SYNC** — the desktop app fetches only undone bot items, then processes.
   You can also import a `.txt`/`.md` file of URLs instead of Telegram.
4. **Curate** — the LLM categorizes into *your* taxonomy (a file you own and
   edit; every answer must match a category you wrote, or it goes to
   `_review/` — nothing is ever filed under an invented name), then writes
   the structured note atomically.
5. **Learn from you** — when you move a note to a different folder, that
   move is recorded as a *correction*: the note's categories update, the
   category is locked, and your past corrections become few-shot examples
   for the classifier. The app never moves it back.
6. **Seal** — both machine vaults commit + push to their private GitHub
   mirrors with reconciliation (rebase/merge on divergence, a rescue branch
   if ever irreconcilable — main is never force-pushed). Optionally, the
   curated notes are also published to the `good-repos` public directory.

## The three vaults

| Vault | Config key | Who writes it | Backup |
|---|---|---|---|
| **GitHub Projects** | `vault_path` | the app only | private `my-awesome-github-directory` |
| **Websites** | `website_vault_path` | the app only | private `my-awesome-websites-directory` |
| **Manual Notes** | `manual_vault_path` | **you only** | your own repo |

The app keeps a **read-only `Library/` mirror** of both machine libraries
inside your Manual Notes vault, so you can link `[[a repo note]]` from your
own ideas and see backlinks — while the app is forbidden (and tested) to
touch one file outside `Library/`.

Ownership is explicit: new notes carry `managed_by` / `schema_version`
stamps; unmanaged files are never touched; **existing notes are never
rewritten**; your hand-edited placeholders survive automatic retries.

## Feature tour

### Intake — the bot accepts every link (v0.22.0, live)
- Non-GitHub links are no longer rejected — they are ledger'd and flow to
  the Websites vault on your next SYNC.
- **No link left behind** — a permanent D1 ledger (`ever_seen_ledger`) plus
  the desktop manifest; every link's state is knowable at any time
  (`/pending`, `/status`, the web dashboard).
- **Blocked domains** (default `x.com, twitter.com, t.co`) are recorded in
  the platform `_inbox` tables but never fetched, never noted, purged from
  retry queues.
- **Self domains** (the bot's own auth links) are never stored at all;
  secret query values are scrubbed from every stored original.
- **Decommission** — mark a repo dead from Telegram; it is never re-added.

### The desktop app (v0.23.0)
- **Test Connection modal** — four subsystem rows (📁 Vaults · 🧠 LLM · 🐙
  GitHub · ✈️ Telegram, with a **live** Telegram leg) spin → settle on
  ✅/⚠️/❌ with the check lines; **🚀 Start Syncing stays disabled until
  everything is connected**, then turns green and starts the SYNC flow.
- **Two-radio LLM tab** — 🖥️ Locally hosted (Ollama / llama.cpp + their
  Detect & Set buttons) / ☁️ Cloud API (URL + key + model — **Anthropic
  Claude** or any OpenAI-compatible endpoint; an `api.anthropic.com` URL
  automatically speaks the Messages API).
- **Split context budget** — *Model max context window* (`llm_num_ctx`)
  plus *Output max tokens* (`llm_max_output_tokens`) — e.g. 160k total /
  32k output. Nothing is ever truncated silently on any backend.
- **Input = the bot queue + Import txt file** (`.txt` or `.md`, one URL per
  line) — the old ID-range/markers/single-message modes are gone.
- **Every dialog themed** — cream light / plum night, AA contrast, one
  Lucide icon pack, always-visible theme toggle.

### The pipelines
- **GitHub** — repo metadata + README via the GitHub API, LLM analysis
  (category · summary · standout features · best-used-for), sanitized
  frontmatter, banner images, atomic writes. A 401 token failure degrades
  gracefully to anonymous access instead of failing the batch.
- **Websites** — polite fetch (timeout, size cap, per-domain pause,
  identifiable User-Agent, optional proxy with DNS resolved at the exit),
  HTML/og extraction with JS-shell and paywall heuristics, **two-pass
  classification into your taxonomy with exact-name validation**, `_review/`
  for low-confidence or unfetchable links, multi-day retry queue (max 3,
  hand-edited placeholders are never touched on upgrade).
- **Moves are corrections** (SPEC §4.4) — vault-vs-`cache.db` reconciliation
  at every run start: moved → accept + lock + log; hand-edited → flag only;
  deleted → dismiss (never re-added); duplicates → flag; unmapped folders →
  kept exactly as placed. First run after upgrade records a silent baseline.
- **404 quarantine** — a repo 404ing N times in a row (configurable,
  default 3, a success resets the counter) is confirmed dead and skipped
  everywhere, with a placeholder note in `_missing/` so it stops counting
  as pending (delete the note to re-check).

### LLM backends
- **Ollama** — auto-detected, auto-started when the server is down; the
  model picker recommends a same-family stand-in when the configured model
  isn't pulled.
- **llama.cpp** — *caught automatically* ~1.5s after launch: the running
  `llama-server` **process's actual listening ports** (any `--port`), then
  common ports; the loaded model is auto-filled. Loopback traffic never
  rides the proxy.
- **Cloud** — Anthropic Claude (Messages API, required `max_tokens`) or any
  OpenAI-compatible endpoint (llama.cpp server, vLLM, LM Studio, proxies);
  `/v1/models` pre-flight (warn, never block), JSON mode with clean
  fallback, wall-clock timeouts on every call.
- **Per-task model overrides** (`models.classify` / `models.analyze`) and
  **your past corrections as few-shot examples** for the classifier.

### The Linking layer (Phase 6)
- **Recall hooks** — every note gets a neutral "Use when you need to…"
  sentence (invisible to fingerprinting, dry-run first).
- **Local embeddings** — only the recall field + description + tags are
  embedded (never full text); Ollama or any OpenAI-compatible
  `/v1/embeddings`.
- **Suggested links wait for you** — cosine candidates + one LLM yes/no per
  pair land in `<manual vault>/Library/Suggestions.md` as checkboxes;
  tick to approve / strike to reject, then `--collect` writes the approved
  *Related (auto)* blocks into the `Library/` mirror copies only. A "no"
  is remembered forever.

### Resilience (hardened over 23 versions)
- **Proxy everywhere it's needed** — SOCKS5/HTTP for Telegram *and* web
  fetches (DNS at the proxy exit — the fix for blocked-web connections),
  per-batch pre-flight with DIRECT fallback, loopback never proxied, retry
  queue re-armed when the proxy epoch changes.
- **VaultSeal reconciliation** — plain push → fetch → rebase / merge
  unrelated histories / rescue branch; the token never touches
  `.git/config`, is never logged.
- **Atomic writes, thread-safe SQLite (WAL), stuck-subprocess killing,
  graceful Ctrl+C, headless-safe degradation** for every modal condition
  (LLM failure, disk full, auth code).
- **Dry-run everywhere** — the same full pipeline, zero writes; plus
  read-only safety tools (`scan_vault_edits.py`, `snapshot_vault.py`).

## Install & quickstart

### Windows (the release zip — the owner's path)
Download `GitCurator-vX.Y.Z-windows.zip` from
[Releases](https://github.com/assadigit/GitCurator/releases), unzip, then:

```bat
1-INSTALL.bat        :: once — creates the app's private .venv + installs requirements
GitCurator.bat       :: the desktop app
GitCurator-DRY-RUN.bat  :: a full rehearsal that writes NOTHING
```

`WINDOWS-QUICKSTART.md` (inside the zip / [app/](app/WINDOWS-QUICKSTART.md))
is the 3-step guide with the vault paths. The zip is deterministic —
byte-identical when rebuilt from the same tag.

### From a checkout
```bash
cd app
pip install -r requirements.txt
python main.py                        # GUI
python -m unittest tests.test_core -v  # quick unit-test smoke (no network)
```
Requirements: Python 3.10+, PyQt6, Telethon, PyGithub, PySocks (see
`app/requirements.txt`), plus any one LLM backend. The full suite is the
exact module list in [`.github/workflows/ci.yml`](.github/workflows/ci.yml)
(run from `app/`; the GUI tests run offscreen via `QT_QPA_PLATFORM=offscreen`).

### The visualized CLI (one click, zero flags)
```bat
Start-GitCurator-CLI.bat        (double-click)
python main.py --cli --auto --yes          :: full automatic run
python main.py --cli --auto --yes --dry-run :: the SAME run, writes nothing
python main.py --cli --init    :: first-run credential wizard
python main.py --cli --status  :: vault map + config + cache + quarantine
python main.py --cli --test-connection     :: the four-subsystem battery
python main.py --cli --list-dead / --reset-dead   :: 404 quarantine
```

### The Cloudflare bot (optional deploy, already live for the owner)
```bash
cd app/cloudflare-bot
npm install && npx wrangler login
node install.cjs                 # or: bash deploy-latest.sh (idempotent)
```
Deployed at `github-to-obsidian-bot.aliassadi-plus.workers.dev`
(currently **v0.22.0** — v0.25.0 is prepared, see
`app/cloudflare-bot/DEPLOYMENT.md` for the 5-minute update pack; Test
Connection warns when the deployed bot is older than the app expects).
Its web dashboard shows stats, pending links, dead letters and the
activity log.

## Repository layout

| Path | What it is |
|---|---|
| `app/` | **The application** — start with [app/README.md](app/README.md) |
| `app/gitcurator/core/` | Pure-stdlib testable core — links · storage · note_builder · llm_client · website_pipeline · taxonomy · web_fetch/extract · note_state (moves-as-corrections) · mirror · linking · embeddings · dryrun |
| `app/gitcurator/integrations/` | Telethon fetchers · `vaultseal` (private backup) · `goodrepos` (public directory) · subprocess_runner · backfill_manager |
| `app/gitcurator/gui/` | `app.py` (the facade) · `main_window/` (window + mixins) · `processing_worker.py` + `gui/worker/` (the batch worker) · `icons.py` · themed dialogs |
| `app/gitcurator/tools/` | Golden runners · backfill · mirror · link-builder · safety scanners · `build_zip.py` |
| `app/taxonomy/` | The Websites category file — **yours to edit**; the classifier may only use names from it |
| `app/prompts/` | The pipeline prompts (w01 category · w02 subcategory · w03 analyze) |
| `app/tests/` | 826 tests + `golden/websites.json` (the 30-link golden set) |
| `app/cloudflare-bot/` | The Telegram bot Worker — canonical deploy copy (D1 schema, migrations, deploy scripts) |
| `app/cloudflare-bot/dashboard/` | The bot's web dashboard (stats · pending · dead letters · activity) |
| `docs/history/` | The archived session history — STATUS, REFACTOR notes, phase reports, trials, kickoff |
| `SPEC.md` | The agent briefing — mission, the six phases, the non-negotiables |
| `CHANGELOG.md` | Every version, plain-language, owner-readable |
| `SWOT-ANALYSIS.md` | The current engineering risk picture (SWE-focused, v0.26.0) |

## Configuration

Everything lives in `app/config.json` (template committed; your real file is
untracked) and is edited from the GUI's Settings pages — 📁 Vault · 🧠 LLM ·
✈️ Telegram · 🌐 Proxy · 🐙 GitHub · 💾 Backup. The keys you'll actually
touch:

| Key | Meaning |
|---|---|
| `vault_path` / `website_vault_path` / `manual_vault_path` | the three vaults |
| `pipelines.github` / `pipelines.websites` | per-vault pipeline switches |
| `llm_provider` | `ollama` · `llamacpp` · `cloud` |
| `llm_num_ctx` / `llm_max_output_tokens` | the split context budget |
| `web_blocked_domains` | never-fetched domains (default the x-family) |
| `notfound_strike_threshold` | 404 quarantine threshold (default 3) |
| `proxy.*` | SOCKS5/HTTP proxy — Telegram + web fetches |

## Testing, CI & verification

- **826 automated tests**, zero network at test time — the core is pure
  stdlib and the outside world is faked (fake Ollama, fake GitHub, a real
  fake SOCKS5 server, offscreen Qt). Exact command in
  [`.github/workflows/ci.yml`](.github/workflows/ci.yml) (run from `app/`).
- **The golden set** — 30 real links run through the real pipeline offline
  in CI (`run_golden_websites.py --offline`), and `--live` against any
  backend for side-by-side comparison reports.
- **CI** (`.github/workflows/ci.yml`) — requirements + Qt system libs,
  77 modules compiled, the full 826 suite, the Worker's 31 Node tests and the offline golden run on
  every push/tag/PR. The repository is **public since 2026-09-30** (after a
  full history scrub), so Actions minutes are free; before that, a billing
  block on private-repo Actions was worked around with the
  [gitcurator-gate](https://github.com/assadigit/gitcurator-gate) mirror
  (latest mirror run: 772/772 + golden 30/30 on the v0.23.0 code).
- The old Next.js verification console (`dashboard/`, v0.26.0) was removed —
  CI runs the identical gate on every push, so the console duplicated it;
  it remains recoverable from git history.

## Versioning & releases

- Semantic versioning, git tags `vX.Y.Z`; the working tree on `main` is
  always the latest version.
- Every release v0.14.1 → v0.23.0 ships `GitCurator-vX.Y.Z-windows.zip`,
  built by `tools/build_zip.py` deterministically from the tag (fixed
  timestamps, tracked files only, secrets banned — byte-identical
  rebuilds). v0.23.0: 83 files, sha256
  `89e78290a4b9f45bf89f323bc82ecb92503b706b47a9ec6a4784a38f268bd04d`.
- `CHANGELOG.md` holds the plain-language history; the old `STATUS.md`
  phase table and session log are archived in `docs/history/`.

## Security

- **History scrubbed before going public (2026-09-30).** The repository
  was private through v0.23.0 and its early history (pre-v0.0.10) contained
  real credentials. Before the public flip the entire history was rewritten
  with `git filter-repo`: Telethon session files and the live
  `config.json` / `installer.config.json` removed by path; the live bot
  token, Telegram api_hash, GitHub PATs, phone number and Cloudflare token
  prefixes replaced; commit author emails rewritten to the GitHub noreply
  form. The v0.23.0 zip rebuilt from the scrubbed tag is **byte-identical**
  to the published asset (sha256 `89e78290…d04d`) — no release content
  changed.
- Since v0.0.10 the tree ships credential-free: config templates, session
  files gitignored, `YOUR_BOT_TOKEN` placeholders in docs, and the local
  wrangler account cache is untracked + gitignored.
- **Credential rotation is still recommended** — every secret was exposed
  in chat during development. Rotate the Telegram bot token / API
  credentials, the Cloudflare tokens, and the GitHub PAT when convenient;
  then update the app (Settings → Credentials) and the Worker
  (`npx wrangler secret put BOT_TOKEN`).
- The app's token hygiene: tokens are used for API calls and one-time push
  URLs only — never written to `.git/config`, never persisted, never
  logged; secret query values are scrubbed from every stored URL.

## Further reading

| Doc | For |
|---|---|
| [app/README.md](app/README.md) | the full per-feature manual (every version's section) |
| [app/WINDOWS-QUICKSTART.md](app/WINDOWS-QUICKSTART.md) | the 3-step Windows guide |
| [app/cloudflare-bot/README.md](app/cloudflare-bot/README.md) | the bot Worker: deploy, env vars, dashboard |
| [SPEC.md](SPEC.md) | the mission, the six phases, the non-negotiables |
| [SWOT-ANALYSIS.md](SWOT-ANALYSIS.md) | the current engineering risk picture |
| [CHANGELOG.md](CHANGELOG.md) | every version in plain language |
| [docs/history/](docs/history/) | the archived session history (STATUS, REFACTOR notes, phase reports, trials) |

## License

Private personal project — all rights reserved. Not for distribution.
