# GitHub-to-Obsidian v0.26.0 (hygiene, modularization & SWOT hardening — 837-test suite)

Latest user-visible change: Test Connection can now tell you when the
deployed Telegram bot is older than the app expects (one extra line in
the Telegram section, only when a Cloudflare Worker URL is configured).
The rest of this pass is internal: the big modules were split by
responsibility with zero behavior change. Previous feature release:
v0.23.0 (Five-Request Desktop Overhaul: Test Connection modal ·
two-radio LLM tab with Claude · split context budget · themed dialogs ·
Import txt file).

## Quick Start

### Desktop App

**From the zip (first run on Windows):** unzip anywhere, double-click
`1-INSTALL.bat` (once — it creates the app's private `.venv` and
installs the requirements), then double-click `GitCurator.bat`.
The 3-step guide with your vault paths is `WINDOWS-QUICKSTART.md`
next to this file. A safe rehearsal that writes NOTHING:
`GitCurator-DRY-RUN.bat`.

From a checkout:
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
python gitcurator/tools/pick_golden_links.py "C:\path\to\bookmarks.csv"  (golden-set candidates)
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
python gitcurator/tools/backfill_websites.py "C:\path\to\bookmarks.csv" --dry-run
python gitcurator/tools/backfill_websites.py "C:\path\to\bookmarks.csv" --limit 30
```

(The path is any bookmarks CSV — the owner's original `unique_links.csv`
is no longer tracked in the repo since v0.26.0; pass wherever your copy
lives.)

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

### llama.cpp engine detection (v0.15.0) + the automatic catch (v0.15.1)

The app always saw two LLM providers — 🧠 Local Ollama and the custom
☁️ OpenAI-compatible endpoint. A local **llama.cpp server
(`llama-server`)** is now the third, and it is **DETECTED like Ollama**
instead of hand-configured like the cloud endpoint — and since v0.15.1
the catch is AUTOMATIC (owner report: "the service is running on task
manager, the app must automatically catch that!"):

- **Caught at launch, no clicks**: ~1.5s after the app starts, a
  background probe finds llama-server (configured URL → the RUNNING
  PROCESS's listening ports → the common ports). When the current
  provider is unusable (Ollama not running / no cloud API key), the app
  switches to llama.cpp itself, fills URL + model and saves — the log
  says exactly what happened. A working provider is never overridden;
  you get a one-line hint instead.
- **Process-based discovery — the Task-Manager guarantee**: on Windows
  the app reads llama-server's ACTUAL listening ports from `tasklist` +
  `netstat` (POSIX: `ss`/`netstat`), so ANY `--port` is caught — even a
  port no guess list would ever contain.
- **Proxy-safe loopback**: every local probe/list/chat bypasses the
  system & env proxy entirely — a VPN/proxy client that doesn't bypass
  127.0.0.1 can no longer hide a running llama-server (the reported
  v0.15.0 failure on a proxy-running machine).
- **Old builds too**: when `/props` 404s (older llama.cpp
  releases/forks), the server is still positively identified through
  `/v1/models` fingerprints (`Server: llama.cpp` header,
  `"owned_by": "llama.cpp"` entries, `.gguf` model ids). vLLM / LM
  Studio / plain OpenAI proxies match none of these — still never
  misreported.
- Settings → 🧠 LLM → radio **🦙 llama.cpp (local)** → **🔍 Detect**
  does the same on demand (the running process's ports first, then the
  common llama-server ports — 8080 first) and fills the URL + model.
- **The model is detected automatically**: the loaded model's id comes
  from `/v1/models` (the `/props` alias/basename is the fallback), the
  model field is filled for you, and an empty `llamacpp_model` in
  `config.json` means "ask the server". `/health` is reported too
  (⏳ still-loading servers say so).
- Every batch pre-flight does the same: probe the configured URL, then
  the process ports + the common-port scan when it is dead (switching +
  saving what it finds), auto-fill the
  model, and abort with the exact `llama-server -m <model>.gguf
  --port 8080` command when nothing is detected — 100 per-link failures
  against a known-dead server help nobody.
- Chat rides the SAME hardened OpenAI-compatible path (wall-clock
timeout, JSON mode with fallback, the over-budget `llm_num_ctx`
warning) — llama-server speaks the protocol natively.
- Also wired: `--cli --init` (choice 3), `--cli --status`, the run
  card, the batch pre-flight, the backfill tool
  (`--provider llamacpp`) and the golden runner
  (`--live --backend llamacpp`). `llamacpp_api_key` stays empty unless
  you started llama-server with `--api-key`.

```cmd
python gitcurator/tools/run_golden_websites.py --live --backend llamacpp
```

### The intake truth (v0.20.0 — blocked domains, missing repos, vault separation)

Three owner-directed fixes after the first proxied batch:

- **🚫 Blocked domains** — Settings → 📁 Vault → "Blocked domains"
  (default `x.com, twitter.com, t.co`). Links on these domains are
  recorded as rows in the `_inbox` platform tables ONLY — never fetched,
  never turned into notes, never retried; previously-queued ones are
  purged and dismissed automatically; the bot-queue view counts them in
  their own 🚫 bucket instead of "pending". Empty field = allow all.
- **🕳️ Missing-repo notes** — a 404 GitHub repo now gets a placeholder
  note in `<github vault>/_missing/` immediately (plus a batch-start
  backfill for repos that struck out in earlier versions), so those
  links STOP counting as "remaining to be processed" and never burn
  another API call. To re-check a repo: delete its note + reset the URL
  in More ▸ View 404 Quarantine.
- **Vault separation** — "the github vault only manages its domains":
  the per-platform `_inbox` tables (x/twitter, youtube, reddit, …) now
  land in the **Websites vault** when one is set; every other non-GitHub
  website is processed into the Websites vault as before.

### Web fetches through your proxy (v0.19.0)

The owner's first v0.18.0 batch exposed the blocked-web pattern: every
`x.com` / `t.co` / `youtu.be` link failed with `[WinError 10061]`
(connection actively refused — poisoned local DNS) while ordinary sites
fetched fine, because the Websites pipeline fetched DIRECT and never
rode the app's proxy. v0.19.0 routes it:

- **Settings → 🌐 Proxy** grows "Use this proxy for web fetches too
  (Websites pipeline — x.com / YouTube need it)" — **on by default**, so
  an existing config gets the fix with no Settings visit.
- Every website fetch of a batch rides the proxy with **DNS resolved at
  the proxy exit** (SOCKS `rdns=True`; HTTP-type proxies via absolute-URI
  requests — same effect). The poisoned local resolver is never
  consulted for the target host.
- A **pre-flight** TCP probe runs once per batch: proxy up →
  `🌐 Web fetches via SOCKS5 127.0.0.1:10808 (Settings → 🌐 Proxy)`;
  proxy down → one loud warning and the batch continues DIRECT (never
  blocked, never crashed).
- **Loopback is never proxied** (v0.15.1 rule): Ollama / llama.cpp
  traffic stays direct.
- **Stuck links un-stick**: when the proxy turns active for the first
  time (or changes), the whole fetch-retry queue is re-armed — attempts
  reset, due NOW — so links that failed while fetching direct get an
  immediate fresh retry through the tunnel (`🔁 Web proxy active —
  re-armed N queued retry(ies)` in the log).
- Politeness unchanged: same User-Agent, per-domain rate limit, size
  cap, redirect cap, certificate verification.

### Detect & Set — the LLM quick-switch (v0.18.0)

Two buttons — **🧠 Detect & Set Ollama** and **🦙 Detect & Set
llama.cpp** — in a compact `LLM:` row on the main screen right under
the SYNC / Test Connection hero row (and again as a `⚡ Quick switch:`
row in Settings → 🧠 LLM). The owner runs BOTH local engines and
switches between them ("sometimes I use llama.cpp model, sometimes
ollama") — one click per engine now does the whole switch:

- **Detect**: probe the engine in a background worker (the GUI never
  blocks). Ollama: one `/api/tags` probe at the configured URL
  (`llm_client.detect_ollama`, raw HTTP, proxy-safe loopback). llama.cpp:
  the FULL catch — configured URL first, then the RUNNING llama-server
  PROCESS's listening ports (any `--port`), then the common-port scan.
- **Pick the model**: the engine's ONLY model is set directly; when
  SEVERAL models are installed, a compact menu (a dropdown like the
  current model field, the configured model pre-selected when still
  installed) lets you pick the one to use.
- **Set + save**: switch the provider radio, fill the URL + model in
  every live Settings widget, MERGE-save. The log ends with
  `✅ LLM provider SET to … — saved. The next batch uses it immediately.`
  A cancelled menu changes nothing ("the LLM provider was NOT changed").
- Every failure is one clear remedy: engine down → the exact start
  command (`llama-server -m <model>.gguf --port 8080` / Start Server),
  up-but-empty → `ollama pull <model>` / `-m <model>.gguf`, a loading
  model says so ("still LOADING…"). A running batch refuses the switch
  so the config it reads stays consistent; a URL typed but not yet
  Saved is still probed (the live widgets are snapshotted).
- **CLI twin**: `--cli --detect-llm ollama` / `--cli --detect-llm
  llamacpp` — the same probe behind a spinner, a numbered model menu
  when several are installed (Enter keeps the current model, `--yes`
  skips the menu), the same keys through the same MERGE save. Exit 0 =
  set + saved.

### Test Connection (v0.17.0 — everything-up-and-ready, in the log)

v0.25.0 adds one optional line: when a Cloudflare Worker URL is
configured (Settings → Backup / config `cloudflare_worker_url`), the
Telegram section also compares the **deployed bot's version** (its
`/health` endpoint) with the version this app expects —
`✅ Bot Worker — v0.25.0 — matches this app`, or a ⚠️ telling you to
redeploy (`cd app/cloudflare-bot && bash deploy-latest.sh`). No Worker
URL configured → the battery looks exactly as before (four sections).

One button — **Test Connection**, in the hero row next to SYNC — checks
the four subsystems a batch needs and logs one verdict line per result,
then a final `🏁` verdict (the CLI twin is `--cli --test-connection`):

```text
📋 [1/4] Vaults      ✅ GitHub vault — found · writable — ready to receive notes
                     ℹ️ Websites vault — not on disk yet — created by the pipeline on its first run
📋 [2/4] LLM         ✅ llama.cpp — up @ http://127.0.0.1:8080 · model 'qwen2.5-3b'
📋 [3/4] GitHub      ✅ GitHub token — valid — account you (5000 req/h)
                     ✅ Vault repo — my-awesome-github-directory ready (private)
📋 [4/4] Telegram    ✅ Credentials · ✅ Account session · ✅ Bot @githubfetcherbot
                     ✅ Proxy 127.0.0.1:10808 reachable · ✅ Live connection — account
                     login OK · bot queue readable (12 link(s) waiting)
🏁 Test Connection — ALL SYSTEMS READY — vaults, Telegram, LLM and GitHub are up
```

- **Vaults** are checked for WRITABILITY too (a temp-file write+delete —
  the app must be able to write notes into them), not just existence.
- **LLM** tests the ACTIVE provider — cloud API, Ollama, or llama.cpp
  (with the v0.15.1 automatic probe + port scan).
- **GitHub** verifies the token AND the backup repos (a PUBLIC backup
  repo gets a warning; a missing repo is fine — the seal creates it).
- **Telegram** runs a LIVE connection test: with a bot configured, the
  bot-queue fetch through your own session proves both the account
  login and the bot chat in one shot. The login dialog is wired, so a
  first run can complete the account login during the test.
- The battery runs in the background (the GUI stays usable); the live
  Telegram leg follows it — session.session is single-user, so the legs
  are serialized like every other Telegram button. Warnings never fail
  the CLI exit code; errors do (rc 1).

### The Linking layer (v0.16.0 — Phase 6, the last phase)

Notes now link to each other, with every suggestion waiting for your
approval (SPEC §4.8):

- **Recall hooks**: every GitHub note gets one delimited
  fingerprint-invisible block with a NEUTRAL "Use when you need to…"
  sentence (prompt without `about_me.md` — website notes already carry
  the field). Dry-run first; the SPEC flow is approve-a-sample first::

      python gitcurator/tools/add_recall_hooks.py --sample 20        # dry-run
      python gitcurator/tools/add_recall_hooks.py --sample 20 --apply
      python gitcurator/tools/add_recall_hooks.py --apply            # the rest

- **Embeddings** (local, your provider): only the recall field + the
  one-line description + the tags are embedded — never full text.
  Ollama (`/api/embed`, legacy servers auto-detected) or any
  OpenAI-compatible `/v1/embeddings` (llama-server `--embeddings`,
  LM Studio, vLLM). Vectors live in SQLite; refreshes are stale-only.
  Optional `embedding_model` in config.json (Ollama default:
  `nomic-embed-text`).
- **Candidates + confirmation**: cosine neighbors across BOTH
  libraries; one LLM call per pair (yes/no + a short reason). A "no"
  is remembered forever.
- **The Suggestions note** — the suggest step's ONLY write::

      python gitcurator/tools/build_links.py

  lands `<manual vault>/Library/Suggestions.md` with every pending pair
  as an Obsidian checkbox. **Tick to approve, strike a line through to
  reject**, then::

      python gitcurator/tools/build_links.py --collect

  writes the approved **Related (auto)** blocks into the `Library/`
  mirror copies ONLY (never the machine vaults, never your own notes;
  the mirror carries the blocks across re-syncs). Cap: 7 links per
  note; rejected pairs never come back. Silent otherwise — no Telegram
  messages about links.
- GUI: More menu → **🪝 Recall hooks (dry-run)** and **🔗 Build link
  suggestions** run the safe defaults from the app.

```cmd
python gitcurator/tools/build_links.py
```

### The Manual Notes Library mirror (v0.14.0)

Your ideas live in your own **Manual Notes vault**; the app now keeps a
read-only mirror of both libraries inside it, so you can link to
library notes from your ideas (`[[Some repo]]`) and see the backlinks —
without the machine ever writing anywhere else in your vault:

```
<your Manual Notes vault>/Library/GitHub Projects/…   ← your GitHub vault
<your Manual Notes vault>/Library/Websites/…          ← your Websites vault
```

```cmd
python gitcurator/tools/mirror_manual.py
python gitcurator/tools/mirror_manual.py --apply
```

- **Dry-run by default** — it prints and reports the plan, writes
  nothing; `--apply` performs the sync (after re-verifying every
  safety check).
- **Only `Library/` is ever touched.** Not one file outside it is
  created, changed or deleted (tested); only mirror copies carrying
  the `mirror_of` marker are ever updated or deleted. Your own files —
  even inside `Library/` — are never touched; anything in the way of a
  mirror copy is reported as a conflict and left alone.
- **It follows your moves**: notes are matched by their `source`, so a
  note you moved between category folders re-mirrors at the new
  location; a note you deleted removes its mirror copy.
- **Idempotent**: a second run over unchanged vaults performs zero
  writes.
- **Refuses to run** if the manual vault overlaps either machine vault
  (either direction). The manual vault path comes from the GUI 📁 Vault
  page / `config.json` (`manual_vault_path`); `--manual-vault` points
  at a copy for a safe rehearsal.
- Mirror copies drop the GitHub notes' banner-image references
  (`attachments/banners/…` can never resolve inside your vault — the
  image would have to live outside `Library/`, which is forbidden);
  everything else is the note, verbatim, under a read-only banner.

Run it against a **copy** of your Manual Notes vault first (SPEC owner
review), then write `[[a library note]]` in an idea and check that
Obsidian shows the backlink.

### Cloudflare Worker (deploy from cloudflare-bot/ folder) — OPTIONAL, see status below
```cmd
cd cloudflare-bot
npm install
npx wrangler login
```
Edit `installer.config.json` with your credentials, then:
```cmd
node install.cjs
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
- **v0.23.0 — the five-request desktop overhaul (2026-09-30):** (1) the main view's 🧠 Detect & Set Ollama / 🦙 Detect & Set llama.cpp row is gone — both buttons live in Settings → 🧠 LLM; (2) **Test Connection is a modal** — four subsystem rows ( Vaults / LLM / GitHub / Telegram) spin then settle ✅/⚠️/❌ with the check lines, ✕ Close always available, and 🚀 Start Syncing unlocks (green) only when EVERY row is connected, then starts the SYNC flow; (3) **every dialog is themed** (`QDialog` backgrounds pinned in both themes — the About Me Wizard "only opens in dark mode" bug: top-level dialogs keep the OS system palette while the app's text colors propagate, so app-light + OS-dark painted an invisible dialog); (4) **the LLM tab is two radios** — 🖥️ Locally hosted LLM model (engine choice + the detect buttons) / ☁️ Cloud API model (API URL + key + model) — with **Anthropic Claude as a first-class cloud backend** (an api.anthropic.com URL automatically speaks the Messages API: x-api-key + anthropic-version + required max_tokens) and the **context budget split**: Model max context window + Output max tokens (e.g. 160k total / 32k output → `llm_num_ctx=160000`, `llm_max_output_tokens=32000`; Ollama num_predict, OpenAI-compatible max_tokens, Claude max_tokens); (5) **Input is Import txt file only** — a `.txt` OR `.md` file with one URL per line (the ID Range / Markers / Single Msg modes and their code are deleted; the CLI flags stay). Suite 772.
- **v0.22.0 — the bot accepts every link (LIVE, deployed 2026-09-30):** non-GitHub links are no longer dead-lettered "not_github" — they are ledger'd as `non_github` and get a "🌐 Received — website" reply (the desktop Websites pipeline processes them into the Websites vault). The x-family policy (x.com/twitter.com/t.co, env `BLOCKED_DOMAINS`) gets its own `blocked_domain` dead-letter reason + 🚫 replies; the bot's own links (env `SELF_DOMAINS`) are never stored; secret query values are scrubbed from stored originals; `/pending`, `/status`, `/api/pending` and the dashboard serve websites too; `migrate-not-github.sql` amnestied the 43 wrongly-rejected links. Redeploy after changes: `bash deploy-latest.sh` (schema is non-GitHub-capable — no migration needed beyond the one-time amnesty).
- **HMAC key mismatch FIXED** (`cloudflare_sync.py::_sign_request`): the Worker stores `sha256(shared_secret)` as the HMAC key; the desktop used to sign with the raw secret → every sync endpoint 401'd. The desktop now derives the same key.
- **Webhook impersonation FIXED** (`cloudflare-bot/src/index.js`): set `WEBHOOK_SECRET` (see `cloudflare-bot/wrangler.toml` for the 2-command setup) and forged Telegram updates are rejected with 403.
- **`save_config` key-wipe FIXED**: pairing keys survive saves now.
- **Stale root `worker.js` + `wrangler.toml` REMOVED** (quarantined as `.deleted-worker.js.v30` / `.deleted-wrangler.toml.v30` — delete those two files to drop them permanently). They were an older, divergent deploy target (placeholder KV id, different worker name) that risked deploying the wrong code from the repo root. `cloudflare-bot/` is the ONE canonical worker.

### ⚠️ Before deploying the worker again
Secrets were previously committed to this folder (bot tokens, api id/hash, Cloudflare account info, session files). Rotate every credential before exposing anything publicly, and never commit this folder to a public repo.

### Key Features
- Forward links to the bot — GitHub repos → the GitHub vault, any other website → the Websites vault — auto-reply with status (stars + description for repos)
- Desktop app processes links → writes Obsidian notes
- Local folder backup (works with OneDrive/Dropbox/Google Drive sync)
- Web dashboard with stats, pending links, decommissioned repos, errors
- Dark/light mode toggle
- No link left behind (D1 permanent ledger + LinkTracker manifest)
- Headless CLI mode (non-blocking: LLM failures/disk-full/auth degrade gracefully)
