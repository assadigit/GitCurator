# GitHub-to-Obsidian v0.13.0 (Phased Build — Websites Pipeline + Corrections + LLM Backends)

## Quick Start

### Desktop App
```cmd
pip install -r requirements.txt
python main.py
```

### Visualized CLI (v0.09 — one click, colors + animations, zero extra deps)
```cmd
Start-GitCurator-CLI.bat     (double-click it — same engine as GitCurator-CLI.bat)
python main.py --cli --auto --yes    (fully automatic run)
python main.py --cli --auto --yes --dry-run   (SAME run, but writes NOTHING — rehearsal)
python main.py --cli --init          (first-run credential wizard)
python main.py --cli --status        (vault map + config + cache + note-state summary)
python main.py --cli --list-dead     (show the 404 quarantine)
```
The CLI reads `config.json` from this folder — the SAME file the GUI
uses — and runs pre-flight checks (GitHub token, proxy, Ollama **model**)
→ bot queue → processing → Obsidian notes → seal, automatically. A
configured Ollama model that isn't pulled opens an interactive numbered
picker (or auto-picks in unattended runs) — a missing model can never
fail the batch.

### Headless (no GUI, plain output — the original interface)
```cmd
python main.py --headless --import-file urls.txt --vault "C:\path\to\vault"
python main.py --headless --from-id 123 --to-id 456 --vault "C:\path\to\vault"
```

### Unit tests (no GUI / no Ollama / no Telegram needed)
```cmd
python -m unittest tests.test_core -v
```

### Phase 0 safety tools (v0.09.5 — run from the app/ folder)
```cmd
python gitcurator/tools/scan_vault_edits.py "C:\path\to\vault"   (read-only vault scan → report)
python gitcurator/tools/snapshot_vault.py "C:\path\to\vault"      (zip backup, timestamped)
python gitcurator/tools/pick_golden_links.py "C:\path\to\unique_links.csv"  (golden-set candidates)
```
All three refuse to write anything inside the vault they are pointed at.
Reports and snapshots land in `app/reports/` (scan/, snapshots/, dry-runs/).
The scan tells you: notes per category, notes missing `source:`, duplicate
`source:` URLs, and which notes contain your own writing in *My Ideas &
Notes* / *Social Signal (Manual)* / *Journal* (the app must never overwrite
those). Snapshot before any risky operation on a real vault; restore by
unzipping over the vault folder.

### Vault settings & pipelines (v0.10.0 / v0.11.0)

The app now knows **three vaults** (Settings → 📁 Vault in the GUI, or
`--cli --status`):

| Vault | Who writes it | Backup | Status today |
|---|---|---|---|
| GitHub Projects (`vault_path`) | the app only | its private repo (VaultSeal) | working — unchanged |
| Websites (`website_vault_path`) | the app only | `my-awesome-websites-directory` | **pipeline works** — switch ON when ready |
| Manual Notes (`manual_vault_path`) | **you only** | your own repo | optional until Phase 5 |

- Every picker shows a live status: *not set / will be created / found*.
- Pipeline switches: **GitHub ON** (today's behavior exactly),
  **Websites OFF by default** (v0.11.0 — flip it ON after setting the
  Websites vault path; OFF keeps the old `_inbox/` behavior byte-for-byte).
- **New notes** carry ownership stamps — frontmatter `managed_by`,
  `schema_version`, `prompt_version` + a one-line *Managed by GitCurator*
  banner — and no longer include the three empty human placeholder
  sections (that writing moves to your Manual Notes vault in Phase 5).
  **Existing notes are never rewritten.**
- The first real batch you run records a silent **baseline** of the
  GitHub vault (a per-note record in `cache.db`). Phase 3 will use it to
  treat your folder moves as corrections instead of damage; a dry-run
  records nothing.

### The Websites pipeline (v0.11.0)

With the Websites switch ON, every non-GitHub link (bot queue, imports,
Telegram) is processed instead of being tabled in `_inbox/`:

1. the URL is canonicalized (tracking params ignored, meaningful ones
   kept) and checked against the vault + `cache.db` + the dismissed list;
2. the page is fetched politely (timeout, size cap, per-domain pause,
   identifiable User-Agent);
3. title / description / main text are extracted — JavaScript-only
   shells, paywalls, PDFs and truncated pages are marked `fetch_status:
   partial` and classified from title + description;
4. the model files the site into your taxonomy
   (`app/taxonomy/website-library-categories.md` — 14 categories, 16
   subcategories, your judgment rules) in two passes (category, then
   subcategory); every answer must exactly match a name from the file or
   it is retried, then routed to `_review/` — nothing is ever filed under
   a name you didn't write; low-confidence answers also go to `_review/`;
5. the note is written atomically into
   `<Websites vault>/<Category>/<Subcategory>/<Name>.md` with the full
   §4.5 body (TL;DR, core offerings, standout feature, **Best used
   for**, pricing, login, similar tools) and ownership stamps;
6. unfetchable links still get a minimal `_review/` note and are retried
   automatically up to 3 times over several days; a successful retry
   upgrades the placeholder to a real note (hand-edited placeholders are
   never touched).

GitHub Pages links (`owner.github.io/repo`) now go to the GitHub
pipeline as their repo; gists are websites with a `#snippet` tag.
The run report, summary log and `--cli --status` all gained a Websites
section. Optional config knobs (safe defaults): `web_fetch_timeout_s`,
`web_fetch_max_bytes`, `web_domain_delay_s`.

```cmd
python gitcurator/tools/run_golden_websites.py --offline   (CI mode — no network)
python gitcurator/tools/run_golden_websites.py --live       (your configured LLM)
```
The golden set lives in `tests/golden/websites.json` (30 links from your
bookmarks, with proposed expected categories — edit them as you see fit
and re-run).

### Moves are corrections + the backfill (v0.12.0)

**Your folder moves are corrections, never damage (SPEC §4.4).** At the
start of every batch — for both vaults — the app compares each vault
with its persistent record in `cache.db` and acts:

- **moved** → accepted: the note's `category:`/`subcategory:` lines and
  tags are updated to the folder you chose, `category_locked: true` is
  added, the correction is logged (append-only `corrections_log`), and
  the note never moves back. Moving out of `_review/` counts too.
- **edited by hand** → flagged in the run report, file untouched.
- **deleted** → the URL is dismissed (never re-added; listed in the
  report in case it was accidental).
- **duplicates** → flagged, neither copy touched. **Unmanaged** files
  (no `source:`) → ignored and listed. **Unmapped folders** → kept
  exactly as placed, reported — no category is ever invented.

The first run after v0.11.0/v0.12.0 records a **silent baseline** (your
600+ existing notes are never flagged); a dry-run detects and logs
without recording. The classifier never overrides a locked note
(retry upgrades included), and the Recategorize dialog skips locked
notes. The run report gains a "Note State" section; `--cli --status`
shows baseline / corrections / dismissed counts per vault.

**The backfill** loads your existing bookmarks into the Websites vault,
safely:

```cmd
python gitcurator/tools/backfill_websites.py ../unique_links.csv --dry-run
python gitcurator/tools/backfill_websites.py ../unique_links.csv --limit 30
```

GitHub links are excluded by the same routing the app uses; the tool is
resumable (per-URL checkpoint in `cache.db` — interrupt it any time,
re-run and it continues exactly where it stopped), polite (fixed pause
+ per-domain pause), works with local Ollama or any OpenAI-compatible
endpoint (`--api-url/--api-key/--model`), and can write a Markdown
batch report (`--report`).

### LLM backends (v0.13.0)

The option previously called "Cloud API" is now labeled what it always
was under the hood: an **OpenAI-compatible endpoint (llama.cpp, vLLM,
LM Studio, cloud)** — Settings → 🧠 LLM in the GUI, or choice 2 in
`--cli --init`. The config value stays `cloud`; old configs load
unchanged.

- **Same hardened client for both providers** (`core/llm_client.py`):
  the wall-clock timeout (`llm_timeout_s`) that already protected
  Ollama now also protects every endpoint call — a hung llama.cpp
  server can no longer freeze a batch.
- **JSON mode with a clean fallback**: `response_format: json_object`
  is sent on classification/analysis calls; a server that rejects the
  parameter gets one retry without it and the rejection is remembered
  (one doomed attempt per run, never one per call).
- **The context window is explicit** (`llm_num_ctx`, default 8192 —
  Settings → 🧠 LLM → *Context window*): sent as `num_ctx` with every
  Ollama call (Ollama's own default is small and truncates long prompts
  from the front **silently** — that can no longer happen); for
  OpenAI-compatible endpoints it powers an over-budget warning (the
  window itself is fixed at server launch: llama.cpp `-c`, vLLM
  `--max-model-len`). Nothing is ever truncated silently on either
  backend.
- **/v1/models pre-flight**: every cloud batch and the Test Connection
  button first ask the endpoint for its model list; a configured model
  missing from it is a *warning*, never a block (servers that hide
  /models — single-model llama.cpp builds, some proxies — are fine).
- **Per-task model overrides** (optional, `config.json` only):
  `"models": {"classify": "", "analyze": ""}` — e.g. a bigger-context
  model for the classification passes and a fast one for note writing.
  Empty (the default) = the single configured model, exactly as before.
- **Your past corrections are now classifier examples** (the deferred
  Phase-3 few-shot item): when you have moved website notes by hand,
  the category prompt carries those corrections, so the model follows
  your filing taste instead of re-guessing.

The golden set runs on **both** backends for comparison:

```cmd
python gitcurator/tools/run_golden_websites.py --live --backend ollama --model llama3
python gitcurator/tools/run_golden_websites.py --live --backend openai --api-url http://localhost:8080/v1 --model my-model
```
Both run the same 30 links through the same pipeline; reports land in
`app/reports/golden/` (…`live-ollama.md` / …`live-openai.md`) for a
side-by-side check.

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
