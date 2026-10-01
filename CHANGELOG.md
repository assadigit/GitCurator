## [0.24.1] — the "websites never sync" queue fix: pending now covers BOTH vaults — 2026-09-30

Owner report (the v0.24.0 Windows build log): "it doesn't sync and see new
sites, for example I add new sites (not github projects), the app is
supposed to detect them > process them > put them in the websites vault
but in logs it says: … All 621 GitHub repos already in vault! 0 pending. …
All caught up — nothing undone in the bot queue."

**Root cause** — the bot-queue "pending" classification was GITHUB-ONLY:

- `_bot_queue_job` classified the GitHub URLs against the GitHub vault and
  returned `non_github_urls` **raw** — never classified against anything.
- `check_bot_queue`/`process_bot_queue`/the hero SYNC→PROCESS flip all
  keyed off that GitHub-only pending list, so a caught-up GitHub vault
  (0 pending repos) meant "All caught up" **even with hundreds of
  unprocessed website links in the queue** — and the Websites pipeline
  never ran. The ProcessingWorker has supported websites-only batches
  since v0.11.0 (`_run_impl`: "not urls and not (_websites_pipeline_on
  and _website_links)"), but no caller ever started one from the queue
  flow: `process_bot_queue` returned early at "No repos in queue".

**The fix, layer by layer (all six call sites of the pending decision):**

1. `worker_jobs._bot_queue_job` — new `website_vault_path` +
   `websites_pipeline_on` params; the non-GitHub links are now classified
   against the **Websites vault** (its own `VaultIndex` keyed by
   `links.normalize_website_url`) + the `WebsiteStateDB` dedupe layers,
   replicating `WebsitePipeline._process_link_inner`'s skip rules exactly
   (blocked/self → `_inbox` rows; dismissed → never re-add; real note →
   done; failed-fetch `_review` placeholder → still pending, the
   upgrade/retry path). The result dict gains `pending_website_urls`,
   `websites_in_vault_count`, `websites_processed_count`,
   `websites_dismissed_count`, `websites_blocked_count`,
   `websites_self_count`, `websites_vault_index_count` + the
   `websites_pipeline_off` / `websites_no_vault` hint flags. A
   configured-but-missing vault path is a loud warning + all-pending
   (VaultIndex on a missing dir would silently index 0 notes).
2. `bot_queue.check_bot_queue` — passes the websites vault + switch into
   the job; `_on_finished` stores `_bot_queue_pending_websites` (with a
   GUI-side fallback for unclassified legacy results); the pending badge,
   the queue report and the log lines now show BOTH pipelines side by
   side ("N repo(s) + M website link(s) pending"). "All caught up" only
   prints when BOTH are done — and when the Websites pipeline is OFF (or
   no vault is set) with non-GitHub links waiting, an actionable hint
   says so instead of a silent caught-up.
3. `bot_queue.process_bot_queue` — starts a **websites-only batch** when
   the GitHub side is caught up (`urls=[]`, `non_github_urls=[…]` — the
   pipeline's own dedupe skips the already-done links); the >10-item
   confirm gate counts repos + websites.
4. `hero` — `_after_sync_fetch` flips SYNC→PROCESS on EITHER pipeline's
   pending count; `_begin_hero_processing` routes websites to the bot
   queue; `_sync_run_button`/`_set_hero_state` label "PROCESS (repos +
   websites)".
5. `processing_control` — `start_processing` (the PROCESS dispatcher)
   covers websites-only batches; `processing_finished`'s Phase 5 CLEAR
   gate now keys on the batch's OWN `_bot_source` provenance + manifest
   (a websites-only bot batch verifies and consumes the queue; an
   import/retry batch never does — stale GUI lists can no longer mark
   the bot queue read).
6. `cli.py --auto` — `fetch_bot_queue` passes the same classification
   params (plus blocked/self domains, matching the GUI), the queue
   summary line shows both pipelines, and "All caught up — nothing to
   process" only fires when both are done.

**Tests** — `tests/test_websitesqueuefix.py` (22 cases, suite 796 → 818):
the owner's exact scenario (all repos done + new sites → the sites ARE
pending), every dedupe bucket (in-vault/processed/dismissed/blocked/
self/failed-review-upgrade), www/utm/http equivalence against real vault
notes, the pipeline-off / no-vault / broken-path hint paths, legacy
old-signature callers, the mixin flow (websites-only batch starts; hero
flips; mixed counts; nothing-pending bails) — worker-side cases run
headless anywhere, plus an offscreen end-to-end replay on the REAL composed MainWindow (check_bot_queue → _on_finished renders the actual queue panel → hero flip → PROCESS starts the websites-only batch; only the Telethon subprocess and the batch launcher are stubbed). Full gate green:
67-module compile, 818 tests, offline golden run 30/30.

### Delivery

Branch `fix/websites-queue-sync` merged into main `--no-ff` (`826b278`,
tree identical to the branch tip `cc13ee2`; branch kept for diffing).
**CI green on main** — run 36784587162 (push event): the same 67-module
compile + 818-test + websites-pipeline gate the session ran locally
before pushing. (The fix branch itself carries no CI run: ci.yml
triggers on main pushes / `v*` tags / PRs to main only, and the branch
went up without a PR — the merge's green run covers the exact tree that
shipped.) **No tag or release yet** — the owner tests from release
zips, so `v0.24.1` + the Windows zip (tag → CI on the tag →
`build_zip.py` → attach) is one command away, the owner's call; until
then, `main` at `826b278` is the deliverable.

## [0.24.0] — The app.py split: 13,815 lines become 28 focused modules, zero behavior change — 2026-09-30

A pure structural refactor of `gitcurator/gui/app.py` (branch
`refactor/gui-app-split`, baseline tag `baseline-before-split`). Nothing
was rewritten: every function and method was moved **verbatim**, and each
of the 29 commits passed the full gate — 67-module compile, fresh-process
import of every module, the 722-attribute import-surface check, an
AST-fingerprint check proving every baseline function appears exactly
once unedited, the 796-case suite (772 project tests + 24 new
characterization tests, `tests/test_refactor_surface.py`), the offline
golden run, and an offscreen smoke of the real composed MainWindow.

### The new layout

- `gui/app.py` (~470 ln) — the facade: the original import header (PyQt
  star imports and all — the module's 722 public attributes are unchanged),
  re-exports of every moved name, `setup_logging`, `main()`.
- Leaves: `link_helpers` · `dead_links` · `telegram_lazy` ·
  `platform_intake`.
- Data layer: `vault_index` · `cache_db` · `link_tracker` · `log_bridge`.
- Workers/UI: `worker_jobs` · `processing_worker` (the 3,386-ln
  QThread worker) · `dialogs` · `headless`.
- `gui/main_window/`: `window.py` (the composed `MainWindow` — bases list
  the 15 mixins before `QMainWindow` so Qt virtual overrides stay
  effective) plus domain mixins: `theme` · `ui` · `hero` ·
  `connection_tests` · `llamacpp` · `phase6` · `test_connection_modal` ·
  `vaults_config` · `input_proxy` · `telegram_ui` · `processing_control`
  · `dashboard` · `bot_queue` · `backup_seal` · `lifecycle`.

### The only deliberate test changes

Five tests monkeypatched `gitcurator.gui.app.<name>` or scanned app.py
source; the split re-aims them at the modules that now own the code
(same expectations, new target): `test_intakefix`
(`_run_telegram_worker`, `CONFIG_FILE`), `test_phase1`/`test_phase2`
(`Github` — without the re-aim the worker silently hit the real GitHub
API), `test_llamacpp` (the `threading` recorder + label scans),
`test_phase4`/`test_phase5` (source scans now read the concatenated GUI
sources), `test_v0230` (`CONFIG_FILE`). Full record:
`REFACTOR_PLAN.md`, `REFACTOR_PROGRESS.md`, `REFACTOR_NOTICED_ISSUES.md`
(repo root).

### The release

Tagged `v0.24.0` and released with the Windows test zip
`GitCurator-v0.24.0-windows.zip` — 112 files / 721,543 bytes, sha256
`c10bfe3b…292245e96686941` (deterministic build from the tag; download
round-trip byte-identical; every `.bat` pure ASCII + CRLF). CI green on
both `main` and the tag. Because this is a pure refactor, the owner's
test is *sameness*: unzip → `1-INSTALL.bat` → `GitCurator.bat` →
everything should look and behave exactly like v0.23.0.

## [0.23.0] — The five-request desktop overhaul: the Test Connection modal, the two-radio LLM tab with Claude, the split context budget, the wizard light-mode fix, Import txt file — 2026-09-30

The owner's five requests, verbatim scope: (1) remove the Detect & Set
Ollama / llama.cpp buttons from the main view ("the settings is
enough"); (2) Test Connection opens a modal with a loading state, every
connected subsystem gets an emoji check, a close button and a Start
Syncing button that "turns to green after everything is connected —
before that it's turned off"; (3) the About Me Wizard "only opens in
dark mode — fix it"; (4) the LLM part gets two radios — Locally hosted
LLM model (→ the detect buttons) / Cloud API model (→ API URL, key,
model — both Claude and OpenAI-compatible), the parenthetical leaves the
endpoint label, and the context window splits into model max context +
output max tokens ("my model is 160k total, 32k of which is output");
(5) Input loses ID range + single msg + markers and gains "Import txt
file (which works both .md and .txt… instead of fetching from
telegram)".

### 1) The main view drops the LLM quick-switch row

The 🧠 Detect & Set Ollama / 🦙 Detect & Set llama.cpp buttons (v0.18.0)
are gone from the main screen; the row they lived in is deleted. Both
buttons — and the whole detect → model-menu → set-and-save fast lane —
live on unchanged in Settings → 🧠 LLM.

### 2) Test Connection is now a modal (ConnectionTestDialog)

Clicking Test Connection opens a modal with four subsystem rows — 📁
Vaults, 🧠 LLM, 🐙 GitHub, ✈️ Telegram. Each row starts ⏳ Waiting…,
spins (braille animation) while its section runs, and settles on
✅ Connected / ⚠️ Connected (warnings) / ❌ Not connected with the check
lines under it. The Telegram row keeps spinning through the LIVE leg
(bot-queue read or account-login probe) exactly like the log battery
always did. Bottom row: ✕ Close (always enabled) and 🚀 Start Syncing —
DISABLED until every row reports ok, then it flips to the filled
pastel-mint "go" style and starts the main view's SYNC flow. The
battery worker gained structured `section_signal`/`result_signal`
streams (the same results the log prints); `_connection_battery_job`
grew optional `on_section`/`on_result` callbacks. A closed dialog never
crashes a late result (every handler re-checks `isVisible()`).

### 3) The About Me Wizard light-mode fix — and every other dialog's

Root cause (reproduced offscreen): top-level dialogs do NOT inherit the
window palette — they keep the OS system palette — while the propagated
`QWidget { color: … }` rule DOES reach them. App-light + OS-dark =
dark-plum text painted on the system-dark window: an invisible dialog
("only opens in dark mode"). Both themes now pin EVERY dialog's surface
(`QDialog { background-color: … }` — cream light / plum dark), the
wizard's hardcoded `#423A52` intro color is gone (the themed rules color
it), and its buttons ride the tracked design-system styles so a theme
flip restyles them.

### 4) The LLM tab: two radios, Claude, and the split context budget

- **Two radios** — 🖥️ Locally hosted LLM model / ☁️ Cloud API model (the
  owner's exact two options). Local reveals the engine choice (🧠 Ollama
  / 🦙 llama.cpp radios) with the two Detect & Set buttons; Cloud reveals
  API URL / API key / Model. The stored `llm_provider` keeps its three
  values ('ollama' | 'llamacpp' | 'cloud') — old configs load unchanged.
- **Claude is first-class** — `llm_client` gained the Anthropic Messages
  API: `anthropic_chat` (x-api-key + anthropic-version headers, the
  system message as the top-level `system` parameter, REQUIRED
  `max_tokens`, text-block content joined, same wall-clock timeout +
  CloudLLM* error contract as the OpenAI path) and
  `anthropic_list_models`. `cloud_chat` is the single router the worker
  calls: the URL decides the wire format — api.anthropic.com → Claude,
  everything else → OpenAI-compatible (llama.cpp / vLLM / LM Studio /
  OpenAI / OpenRouter / Together…). `CLOUD_PROVIDER_LABEL` lost its
  parenthetical ("OpenAI-compatible endpoint"); the pre-flight, the
  Test Cloud API button and the batch banner all speak both formats.
- **The split context budget** — the single "Context window (tokens)"
  field became **Model max context window (tokens)** (`llm_num_ctx`,
  unchanged semantics: Ollama num_ctx / the over-budget warning) +
  **Output max tokens** (`llm_max_output_tokens`, new, default 0 =
  server's choice): Ollama sends `options.num_predict`, OpenAI-compatible
  sends `max_tokens`, Claude sends your value with a 4096 fallback. The
  owner's example — 160k total, 32k output — is the fields' tooltip.

### 5) Input: Import txt file (.txt AND .md) — the other modes removed

The Input tab's ID Range, Markers and Single Msg modes are gone, with
their code: `generate_marker_hash` / `copy_marker_hash` /
`find_by_marker` / `find_keyword_ids` / `preview_messages` /
`_show_preview_modal`, the `_telegram_single_job` /
  `_telegram_keyword_job` / `_telegram_preview_job` subprocess wrappers,
`update_mode`, the More ▸ Preview Messages action, and every
single/keyword/range branch of `start_processing`. What remains is the
owner's replacement: **Import txt file** — pick a `.txt` OR `.md` file
(one URL per line, `#` comments skipped); PROCESS runs it through the
same intake as any batch (GitHub repos → the GitHub pipeline, every
other website → the Websites pipeline). PROCESS's dispatcher is now:
fetched bot queue → import file → the helpful "Nothing to Process"
message. The CLI/headless `--from-id/--to-id/--offset-start/--count/
--single-id` flags keep working (a separate documented surface;
ProcessingWorker keeps the two telegram modes they drive).

### Tests

`tests/test_v0230.py` (30 cases): the Claude wire format against fake
servers (headers, system extraction, max_tokens fallback + override,
text-block join, error objects, /v1/models), the cloud_chat URL router
(mocked — no network), max_tokens/num_predict emission, the worker's
budget pass-through, the modal's row lifecycle + gating, themed dialog
backgrounds + the wizard in light mode, the import-only Input tab (.md
path end-to-end), and the two-radio LLM tab (visibility + persistence).
Updated: test_detectset (the buttons now live in Settings only), the
label constants, the recorder fakes (`max_output_tokens`), and the
smoke tool. **Suite: 772** (was 742) — all green; golden offline 30/30.

## [0.22.0] — The bot accepts every link: websites into the ledger, the x-family policy, the not_github amnesty — 2026-09-30

The owner sent `https://reverseui.com/` to @githubfetcherbot and got back:

> ⚠️ Non-GitHub URL — skipped: https://reverseui.com
> (Only GitHub links are tracked)

— but the Websites vault has existed since v0.11.0, and the owner's
v0.20.0 request #3 was "every website (which be pasted in the same
robot) must be processed and stored in websites vault".

### Root cause

The Cloudflare Worker is a v0.01 design (2026-09-16) that predates the
Websites pipeline by two weeks. Its queue consumer dead-lettered every
non-GitHub URL (`reason='not_github'`) and replied "Only GitHub links
are tracked". SPEC §2 Non-negotiable #9 ("Do not touch:
`app/cloudflare-bot/`") then shielded it from every Websites-pipeline
phase (v0.11.0 → v0.21.0): the desktop learned to process websites, the
bot never did. 43 wrongly-rejected links sat in `dead_letters`; on top,
`/api/pending` (api.js), `/pending` + `/status` (commands.js) and the
dashboard pending API all filtered `url_type='github'` — while the D1
schema had supported `non_github` ledger rows the whole time (schema.sql
line 14): nothing ever wrote them from the queue.

(The desktop was never actually blocked: the Telethon bot-queue path
reads the chat directly and extracts `non_github_urls` itself — the
bot's reply and its D1 records were the lie.)

### The fix (app/cloudflare-bot/ — the owner lifted SPEC §2#9 for this change, 2026-09-30)

- **`queue-consumer.js`** — non-GitHub URLs are now ledger'd
  (`url_type='non_github'`, owner/repo NULL) exactly like GitHub repos:
  mirror → ledger → insert → KV → activity log, and the bot replies
  **"🌐 Received — website: … Status: Pending processing (Websites
  vault)"** with the same Mark-Decommission button. Enrichment stays
  GitHub-only.
- **Website identity, desktop parity** — `normalizeUrlTyped` (utils.js):
  GitHub URLs keep the frozen `normalizeUrl` semantics (SPEC §4.3.1),
  every other URL gets `normalizeWebsiteUrl`, a JS port of
  `links.normalize_website_url` (https, `www.` dropped, tracking params
  dropped, meaningful params KEPT — a YouTube `?v=` IS the page; the
  bare root slash is kept only when the original had it, Python
  `urlparse` parity). Verified golden-value-equal against the desktop
  function on 10 cases.
- **Blocked domains** (v0.20.0 parity) — x.com/twitter.com/t.co (env
  `BLOCKED_DOMAINS`, comma-separated; missing = the desktop default, `""`
  opts out) are dead-lettered with their own reason `blocked_domain` and
  get **"🚫 Blocked domain — never fetched"** — the desktop never
  fetches them either.
- **Self domains** (v0.21.0 parity) — the bot's own workers.dev links
  (`…/auth/?token=…`) are never stored at all; the reply shows the
  query-stripped URL so a live token never echoes.
- **Token scrubbing** (v0.21.0 parity) — `scrubUrlToken` (port of
  `scrub_url_token`) masks secret-named query values in every stored
  `url_original`.
- **Surfaces un-GitHub'd** — `/api/pending` (api.js) and the dashboard
  pending API (dashboard-api.js) serve websites too; the dashboard table
  renders 🌐 rows with direct links (was `null/null`); `/pending`
  renders website URLs; `/status` accepts any URL (was "❌ Not a valid
  GitHub URL"; websites get a 🔗 Open website button); `/start`, `/help`
  and the no-URL prompt now say "links — GitHub repos or any website".
- **`migrate-not-github.sql`** — the one-time amnesty: all 43
  `not_github` dead letters moved into the ledger as `non_github` rows
  (first_seen preserved from the original attempt), then deleted from
  `dead_letters`. Idempotent (INSERT OR IGNORE + reason-scoped DELETE).
- Worker self-report `1.0.0` → `0.22.0` in `/health` + package.json
  (never bumped since v0.01).

### Deployed + verified live (2026-09-30)

- `wrangler d1 execute curator-bot --remote --file=schema.sql` (safe,
  IF NOT EXISTS) → `--file=migrate-not-github.sql` (43 rows moved,
  dead_letters now empty: 340 github + 43 non_github in the ledger) →
  `wrangler deploy` (version `43c6005d-1642-45d0-9e5a-8d268359ae95`).
- End-to-end through REAL webhook updates: `https://reverseui.com/` →
  ledger row #384 (`non_github`) + the 🌐 reply
  (`bot_reply_message_id` 1462) + activity log "Received website";
  `/status reverseui.com` → the migrated 03:36 row (was: "Not a valid
  GitHub URL"); re-sent `github.com/imputnet/cobalt` → forward_count 2,
  the ⏳ Already-pending path (GitHub regression green); `x.com/…` +
  `t.co/…` links → `dead_letters` `blocked_domain` rows (🚫 replies).
  Queue consumer logs clean (`wrangler tail`: Queue curator-ingest Ok).
- The desktop side needed nothing: with `pipelines.websites` ON and
  `website_vault_path` set (the owner's configuration since v0.20.0),
  the next "Check Queue"/PROCESS run fetches these links into the
  Websites vault exactly as SPEC §4.2-4.3 prescribes.

## [0.21.0] — The seal truth: mirror reconciliation, self domains, full-accounting report, direct fallback — 2026-09-30

The owner's v0.20.0 batch log (2026-09-30 02:17) ended with
`⚠️ Websites vault seal: failed — push failed … ! [rejected] HEAD -> main
(fetch first)` and a verification report that said "GitHub processed: 0 /
Non-GitHub recorded: 0 / ✅ ALL LINKS VERIFIED" for a batch that touched
271 links. The audit found four root causes — all four fixed.

### 1 — VaultSeal push reconciliation (the rejected-seal fix)

**Root cause:** the private `my-awesome-websites-directory` repo had been
doubling as the public cloud-gate mirror (the Actions-billing workaround
from 2026-09-29) — 18 gate commits on its `main`, zero vault commits, no
shared history with the vault's local repo, and `_seal` did a plain push
with no reconciliation: rejected forever, every run.

- **`VaultSeal._push`** now reconciles instead of giving up: plain push →
  on a non-fast-forward rejection, `fetch` the remote `main` → if the
  remote is already inside HEAD, retry; if the histories share a base,
  **rebase** our seal commit(s) onto the remote tip and push; if the
  histories are unrelated (fresh `git init` after a vault move, or a
  mirror that was repurposed), **merge --allow-unrelated-histories** and
  push. Nothing is discarded in either path.
- **Irreconcilable = rescue, never force:** when a rebase/merge conflicts,
  the seal lands on a `seal-rescue/<timestamp>` branch (local commit
  kept, remote `main` untouched) and the error says exactly where it is
  and how to reconcile. **`main` is never force-pushed** — a backup tool
  must never discard remote history it cannot see.
- The token used for the reconciliation fetch never persists
  (`.git/FETCH_HEAD` is scrubbed after every fetch — the token hygiene
  rule held everywhere else and now holds here too).
- Proven against real local bare repos: fast-forward, up-to-date,
  diverged-shared (rebase), unrelated (merge), unrelated-conflict and
  rebase-conflict (rescue), non-rejection passthrough.

### 2 — Self domains + token scrubbing (the bot's own auth links)

The log showed the pipeline FETCHING
`github-to-obsidian-bot…workers.dev/auth/?token=<64 hex>` — the owner's
own bot OAuth handoff links, complete with live tokens, fetched and
stored in `_review` notes and `_inbox` rows.

- **`web_self_domains`** (default: the bot's workers.dev host; Settings →
  📁 Vault → "Self domains") — the exact never-fetch treatment blocked
  domains get: process_link guard, intake filter, 🔒 queue bucket, and a
  purge that deletes the queued retries + failed `_review` placeholders
  (the tokens go with them). Never fetched, never noted.
- **`scrub_url_token` / `scrub_urls_in_text`**: secret-named query
  parameters (`token`, `secret`, `api_key`, `sig`, …) never appear in a
  stored `_inbox` row again — and the NEXT write rewrites tables that
  still carry pre-v0.21.0 live tokens (logged). Short non-secret values
  are untouched; dedupe/retry identity (the local state DB) keeps the
  full URL, so retries still work.

### 3 — The verification report's full accounting

"GitHub processed: 0 / Non-GitHub recorded: 0 / ALL LINKS VERIFIED" hid
16 websites-pipeline outcomes and 247 blocked links — every link was
actually accounted for, but the REPORT couldn't say so.

- `LinkTracker.verify` now buckets everything: **Websites notes / in
  _review (retry scheduled) / skipped (dedup)**, **Blocked/self domains
  (recorded in _inbox)**, **Non-GitHub pending** (pipeline off / no
  vault — visible, not a failure), and **GitHub pending** (unfinished
  work — now fails verification, matching `get_all_clear`).
- New **🧮 Accounting** line: `accounted/total` must reconcile or the
  verdict says so loudly. "ALL LINKS VERIFIED" now requires BOTH no
  failures and a clean reconciliation.
- New `mark_blocked` terminal status (blocked links no longer hide
  inside "skipped"); `_inbox` lookups search the Websites vault's tables
  too (where they live since v0.20.0). All three report surfaces updated
  (batch log, final report file, Verify Vault dashboard).

### 4 — Web-fetch direct fallback (the SSL-EOF class)

Ten of sixteen fetches died with `[SSL: UNEXPECTED_EOF_WHILE_READING]`
THROUGH the proxy (gist.github.com, huggingface.co, anthropic.com… — the
exit IP is blocked by those CDNs) while the local line reaches some of
them fine.

- `fetch_url(proxy=...)` now retries once **DIRECT** when the proxied
  attempt fails without ever getting an HTTP answer (TLS reset, timeout,
  dead proxy). HTTP errors (4xx/5xx) mean the site ANSWERED — no
  fallback. Loopback is never proxied and never falls back. A successful
  fallback says so in the result reason; a double failure carries BOTH
  reasons ("proxy: … | direct: …") so the `_review` note tells the owner
  exactly what to fix.

### Infra (agent-side, same release)

- The cloud gate moved to its own public repo **`gitcurator-gate`**
  (workflow + `GC_PAT` secret + repository_dispatch trigger; first
  baseline run green). The gate mirror no longer collides with a vault
  mirror — that collision was root cause #1.
- `my-awesome-websites-directory` reset as the websites-vault mirror
  (private, empty): the owner's next websites seal pushes cleanly (the
  "local commit kept" commits land on an empty remote). Both directory
  repos are private again — the "public until Oct 1" window closed early
  because the gate no longer needs them.
- Suite **742** (38 new tests in `tests/test_sealfix.py`); CI 38 modules.

## [0.20.0] — The intake truth: blocked domains, missing-repo notes, vault separation — 2026-09-29

Owner requests (three, after the first v0.19-era batch log):

1. "the X domains are already addressed, So i dont want to bring them
   again for websites vault, we need to find a robust method to prevent
   this."
2. "There are 8-9 github addresses that are 404. but system always count
   them as remaining to be processed, we need to find a solution for
   missing githubs as well. for example create a note in vault for
   missing github, and model reads them before again trying to process
   them or something faster."
3. "every website (which be pasted in the same robot) must be processed
   and stored in websites vault, the github vault only manages its
   domains."

### 1 — Blocked domains (the X fix, robust by construction)

- **`web_blocked_domains`** (default `x.com, twitter.com, t.co`) —
  Settings → 📁 Vault → "Blocked domains" (comma-separated; empty =
  allow all). MISSING key = the default list, so the owner's existing
  config.json gets the fix with no Settings visit.
- **Never fetched, never noted, never retried**: a guard at the top of
  `WebsitePipeline.process_link` (whatever path brought the link in)
  plus an intake filter in the websites phase (the "N link(s)" count is
  honest) plus the bot-queue classification (blocked links get their own
  🚫 bucket in the queue display — never "pending").
- **The queued tail is PURGED**: at pipeline construction, blocked-domain
  rows leave the retry queue, failed `_review` placeholder notes are
  DELETED (dry-run-aware; real notes are never touched), and every purged
  URL is marked dismissed — it can never re-enter. One log line reports
  the purge; idempotent.
- The X links keep their existing record: rows in the `_inbox` platform
  table ("already addressed") — which now lives in the WEBSITES vault
  (see 3).

### 2 — Missing-repo notes (the 404 fix)

- On a GitHub 404 the pipeline now writes
  `<github vault>/_missing/<owner>_<repo>.md` immediately — its
  `source:` frontmatter line IS the VaultIndex dedupe key, so the repo
  stops counting as pending in every queue view ("the model reads them
  before again trying" — one dict lookup, no API call, no LLM) — and
  confirms the 404 quarantine on the spot (a 404 from
  `/repos/{owner}/{repo}` is definitive: deleted or private).
- **Backfill for the legacy tail**: at batch start, repos that struck out
  in earlier versions (1-2 strikes, never confirmed, no note) get their
  note NOW (`🕳️ N missing-repo note(s) written (past 404s)`) — the
  owner's 8-9 links stop counting as remaining on the next run.
- **Re-check path** (documented inside every note): delete the note +
  reset the URL in More ▸ View 404 Quarantine → the repo is processed
  like new. `note_state` and the Library mirror both skip the `_missing`
  folder (deleting a placeholder must never be read as "dismiss the
  repo"; placeholders are not library notes).
- New `CacheDB.confirm_dead` (immediate confirmation, never lowers a
  higher count) and `CacheDB.get_unconfirmed_404s` (the backfill set).

### 3 — Vault separation ("the github vault only manages its domains")

- The per-platform `_inbox` tables (x_twitter_links.md, youtube_links.md,
  …) now land in the **WEBSITES vault** when one is configured
  (`_inbox_table_vault`); the GitHub vault keeps them only as the
  fallback when no Websites vault is set, so links are never lost.
- Every non-GitHub website (except blocked domains) is processed + stored
  in the Websites vault exactly as before — now with a clean record
  trail: table row (the record) + note (the artifact).

### Files

- `core/links.py`: `DEFAULT_BLOCKED_DOMAINS`, `blocked_domains_from_config`
  (list or comma-string, never raises, missing key = default),
  `domain_is_blocked` (exact + subdomain, port-stripped, anchored on a
  literal dot — `x.com.evil.tld` never matches).
- `core/website_pipeline.py`: `purge_blocked_domains` (one lock cycle;
  only `_review` placeholders), `_enforce_blocked_domains` (production
  path only — an injected fetch_fn stays hermetic), the `process_link`
  guard.
- `core/note_builder.py`: `build_missing_repo_note` (source: = the dedupe
  key; deliberately NOT a managed content note).
- `core/note_state.py` + `core/mirror.py`: `_missing` added to the skip
  lists (with the divergence from VaultIndex documented — VaultIndex
  DOES index `_missing`: that is the whole point).
- GUI: `CacheDB.confirm_dead` / `get_unconfirmed_404s`;
  `ProcessingWorker._record_missing_repo` + `_backfill_missing_notes` +
  the rewritten 404 branches + the intake filter; `_bot_queue_job`
  blocked bucket (+ the queue display line and the legacy GUI-side
  filter); the Settings row; `_inbox_table_vault` routing at all three
  `write_inbox_links_by_platform` call sites; `CONFIG_EXAMPLE` defaults.
- Tests: `tests/test_intakefix.py` (49) — the config contract and
  matcher matrix, the purge (real notes never touched, dry-run keeps
  files, idempotent), the pipeline guard (never-fetched proof), the
  missing note (dedupe via VaultIndex, delete = re-check, note_state /
  mirror skip), CacheDB methods, the worker halves (idempotent, .git
  suffix, non-github rows), the bot-queue bucket (blocked checked before
  in-vault), the table routing, GUI wiring + a full offscreen round
  trip. v0.19/v0.13 fixtures that used x.com URLs moved to neutral
  domains (rearm + correction-slot tests). Suite **704**; offline golden
  30/30 (the runner opts out of the block policy — its t.co fixture
  measures classification, not policy).

## [0.19.0] — Web fetches through your proxy — 2026-09-29

Owner's first v0.18.0 batch log: every `x.com` / `t.co` / `youtu.be`
link failed with `[WinError 10061] No connection could be made because
the target machine actively refused it` — while `gooseworks.ai` fetched
full. The blocked-web signature: on the owner's line those domains
resolve to a poisoned local DNS record that REFUSES the connection, and
the Websites pipeline fetched DIRECT — the app's SOCKS5 proxy (v2rayN
`127.0.0.1:10808`, used for Telegram since v0.07.1) never carried web
traffic.

### The Websites pipeline rides the proxy

- **One setting, already on**: the proxy block grows `use_for_web`
  (Settings → 🌐 Proxy → "Use this proxy for web fetches too (Websites
  pipeline — x.com / YouTube need it)"). Default ON — the owner's
  existing `config.json` gets the fix without touching Settings; untick
  to fetch direct again.
- **DNS at the proxy (the actual fix)**: SOCKS fetches resolve the
  target hostname AT THE PROXY EXIT (`rdns=True`) — the poisoned local
  resolver is never consulted. PySocks (a hard Telethon dependency, so
  always present; now listed in requirements.txt explicitly) provides
  the socket; `http://`-type proxies ride urllib's native
  `ProxyHandler` (absolute-URI requests — same proxy-side DNS).
- **Pre-flight, once per batch**: the pipeline TCP-probes the proxy
  before link #1. Up → `🌐 Web fetches via SOCKS5 127.0.0.1:10808
  (Settings → 🌐 Proxy)`. Down → one loud warning ("fetching DIRECT —
  x.com / YouTube links will keep failing until the proxy client is
  up") and the batch continues direct, exactly like today.
- **Loopback is never proxied** (the v0.15.1 rule): Ollama / llama.cpp
  traffic on 127.0.0.1 goes direct even with the proxy on.
- **🔁 The stuck links un-stick**: the owner's 263 failed links were at
  retry attempt 1/3 — two more failed batches and the retry cap would
  freeze them forever. When the proxy turns ACTIVE for the first time
  (or changes host/port/type), the whole retry queue is re-armed
  (attempts → 0, due NOW) once per proxy epoch — the next batch retries
  everything through the tunnel immediately.
- **Politeness unchanged**: same User-Agent, per-domain rate limit,
  20 s timeout, 2 MB cap, redirect cap, cert verification (a SOCKS
  fetch only swaps the raw socket — SNI and certificates intact).

### Files

- `core/web_fetch.py`: `proxy_from_config` + `web_proxy_preflight` +
  `proxy_label`, the PySocks connection classes
  (`_SocksHTTP(S)Connection` — rebound after `super().__init__`, the
  http-client instance-attribute trap), `_SocksHandler`,
  `_build_opener(force_direct=)`, `fetch_url(proxy=)`; every proxy
  failure is a normal failed `FetchResult` whose reason names the
  proxy.
- `core/website_pipeline.py`: constructor wiring (pre-flight + proxied
  fetcher + the log lines; NEVER applied to an injected `fetch_fn` —
  the golden run stays offline), `WebsiteStateDB.get_meta/set_meta` +
  the `website_state_meta` table + `rearm_retries`, and
  `_maybe_rearm_retries` (once per proxy epoch, failure-tolerant).
- GUI: the `proxy_use_for_web` checkbox (Settings → 🌐 Proxy), carried
  by `save_config` and `_get_proxy_dict`; `CONFIG_EXAMPLE` defaults in
  both constants modules; `PySocks>=1.7.1` in requirements.txt.
- Tests: `tests/test_webproxy.py` (44) — the config contract (missing
  key = ON), a REAL fake SOCKS5 server proving the hostname reaches the
  proxy (rdns) and the plain-HTTP loopback bypass, a fake HTTP proxy
  proving absolute-URI requests, pre-flight alive/dead/PySocks-missing,
  opener wiring, pipeline integration (proxied fetcher / direct
  fallback / opt-out / injected-fn verbatim), the re-arm epoch rules,
  GUI source assertions, defaults + packaging. Suite 655 (44 new);
  offline golden 30/30, 0 invalid.

## [0.18.0] — Detect & Set: the LLM quick-switch — 2026-09-29

Owner request: "I want these two buttons: Detect and Set ollama, Detect
and Set llama.cpp. In my system I have both llama.cpp and ollama,
sometimes I use llama.cpp model, sometimes ollama, so I want two buttons
that detect, and set the model for me. If there were multiple models for
each, a menu like the current one should help user to select their
desired model. The point is that there is still not dedicated way to
connect fast to llama.cpp."

### The fast lane between the two local engines

- **Two buttons on the MAIN screen** (a compact `LLM:` row right under
  the SYNC/Test Connection hero row): **🧠 Detect & Set Ollama** and
  **🦙 Detect & Set llama.cpp** — one click each way between the engines
  the owner runs side by side. The same two buttons also live in
  Settings → 🧠 LLM as a `⚡ Quick switch:` row, right under the provider
  radios. No radio-clicking, no Detect, no model typing, no Save — the
  button does all four.
- **What a click does**: probe the engine in a background worker (the
  GUI never blocks) → when the engine advertises SEVERAL models, a
  compact themed menu (a dropdown like the current model field, the
  configured model pre-selected when still installed) lets you pick the
  one to use — the only model is set directly → switch the provider
  radio + fill the URL + model in every live Settings widget →
  MERGE-save. The log tells the whole story, ending
  `✅ LLM provider SET to … — saved. The next batch uses it
  immediately.`
- **Ollama leg** (`llm_client.detect_ollama`, the Ollama twin of
  `probe_llamacpp`): ONE raw-HTTP `/api/tags` probe (no ollama SDK —
  thread-safe and testable against a stdlib http.server), loopback
  bypasses the proxy per the v0.15.1 rule, never raises; the model list
  is the server's own (name/model keys, deduped, server order kept).
- **llama.cpp leg**: the FULL catch — the configured URL first, then the
  RUNNING llama-server PROCESS's listening ports (any `--port`, the
  Task-Manager guarantee), then the common-port scan; models from
  `/v1/models` with the `/props` alias merged in; `via: process` is
  named in the log line.
- **Failure paths are one clear remedy each**: engine down → the exact
  command to start it (llama-server line for llama.cpp; Start Server /
  `ollama serve` for Ollama), up-but-empty → `ollama pull` / `-m
  <model>.gguf` (+ "still LOADING…" when `/health` says so), menu
  cancelled → "the LLM provider was NOT changed". Nothing is ever
  half-set: no save happens without a chosen model.
- **Guards**: a running batch refuses the switch (the config a batch
  reads stays consistent); a second click while a detect runs is
  refused; the live LLM widgets are snapshotted so a URL typed but not
  yet Saved is probed, not the stale one.
- **CLI twin**: `--cli --detect-llm ollama` / `--cli --detect-llm
  llamacpp` — the same probe behind a spinner, a numbered model menu
  when several are installed (`Enter` keeps the current model, `--yes`
  skips the menu for scripts), the same keys written through the same
  MERGE save. Exit 0 = set + saved; 1 = not found / no model / aborted.
- Tests: `tests/test_detectset.py` (47) — `detect_ollama` against fake
  `/api/tags` servers (models/dedup/normalization/hostile inputs), the
  background job (configured-hit, process-fallback, crash-guard), the
  SET half on a stubbed window (every branch incl. menu pick/cancel),
  GUI wiring (both button rows, the dialog contract, the runner guards),
  and the CLI end-to-end (parse/dispatch/help, fake servers, patched
  input, `--yes`, dead engines, MERGE-survival of the other provider's
  keys). Plus `gitcurator/tools/smoke_detectset.py` — the offscreen
  end-to-end click-through (real MainWindow + TestWorker threading +
  queued signals + widget updates against local fakes). Suite 611 (47
  new); CI 38 modules.

## [0.17.0] — Test Connection — 2026-09-29

Owner request: "There is a button in the main GUI (we need same thing in
cli as well), which is called Test Connection. It must test connect this
and show in logs: 1. Vaults are found. and ready to be input to.
2. Telegram is connected (bot) and account login. 3. LLM whether via
API, ollama, llama.cpp. 4. github repo's are ready (token working).
THIS WAY USER IS ENSURED THAT EVERYTHING IS UP AND READY."

### The four-subsystem prober (`core/connection_check.py`)

- One button — the GUI's **Test Connection** (was "Test Connectivity",
  in the hero row next to SYNC) — checks, in order, and logs one verdict
  line per result, then a final `🏁` verdict:
  **[1/4] Vaults** (found + **writable** — the repo's FIRST writability
  probe: a temp-file write+delete in each vault; github vault must
  exist, websites vault may be created, manual vault is the owner's),
  **[2/4] LLM** (the ACTIVE provider — Ollama `/api/tags` with the
  configured-model check, cloud API through the real `preflight_openai`,
  llama.cpp through the v0.15.1 probe + port scan + model resolution),
  **[3/4] GitHub** (stdlib GET `/user` → token valid/rejected/unreachable,
  then the backup repos: ready / PUBLIC-should-be-private warn /
  not-on-GitHub-yet-info — VaultSeal creates it on the next seal),
  **[4/4] Telegram** (credentials, session file = login state,
  bot username, proxy TCP reachability) + a **LIVE** connection test.
- Every check never raises and survives hostile configs; loopback HTTP
  bypasses the system proxy (the v0.15.1 rule), api.github.com honors it.
- The live Telegram leg: the BOT QUEUE fetch when a bot is configured
  (one Telethon subprocess proves BOTH the account login and the bot
  chat — the queue is read through the user's own session), else the
  Saved-Messages preview (account login only). Runs AFTER the battery —
  session.session is single-user, so the legs are serialized like every
  other Telegram button. Interactive login still works (the code dialog
  is wired), so a first-run user can log in during the test.
- GUI flow: the battery (vaults + LLM + GitHub + Telegram-local) runs in
  one background TestWorker — the GUI stays usable — with a snapshot of
  the saved config overlaid with the live credential widgets (unsaved
  edits get tested too); the live leg follows under the same
  `connection_check` Telegram lock; a crashed battery surfaces as an
  error (never "ALL SYSTEMS READY" from an empty result — GUI-smoke
  caught exactly that).
- CLI twin: `--cli --test-connection` — the same four sections with the
  house `_check_line` formatting, a spinner during the live probe, exit
  code 0 when no error-level issue was found (warnings don't fail), 1
  otherwise.

### Details

- New module `gitcurator/core/connection_check.py` (pure stdlib +
  llm_client, no PyQt): `check_vaults` / `check_llm` / `check_github` /
  `check_telegram_local` / `telegram_live_result` / `run_local_checks` /
  `summarize` — result dicts `{name, level ok|warn|error|info, detail}`,
  level maps for the GUI and CLI vocabularies.
- GUI: `test_all` rewritten (the old sequential telegram→proxy chain and
  its five dead helpers removed); `_connection_battery_job` +
  `_cc_telegram_leg` + `_cc_finish`; button/tooltip/menu relabeled
  ("🔌 Test Connection (all systems)"); the old per-test More-menu
  entries (Ollama / Cloud API / llama.cpp / Proxy / Telegram & GitHub)
  remain for the deep dives.
- CLI: `cmd_test_connection` + `_connection_live_telegram` (the
  `fetch_bot_queue` thread+spinner pattern, minus vault filtering);
  `--test-connection` in the parser, the dispatch (before batch modes)
  and the "Nothing to do" help list.
- Tests: `tests/test_connection.py` — 60 tests: the vault matrix incl.
  chmod-readonly + will-be-created + parent-missing, fake Ollama /
  cloud / llama.cpp / GitHub-API servers (one stdlib JSON-route handler),
  the telegram local/live tables, the verdict math, the battery +
  wiring (button text, menu entry, lock owner, the crash fallback), the
  CLI (flag parse, missing config rc=1, full dead-services run rc=1,
  fake-Ollama + mocked-live run rc=0). Suite **564**; CI **37 modules**.


## [0.16.0] — Phase 6: Linking — 2026-09-29

The owner's go: "the app runs, you can continue for next phase." SPEC
§4.8 / §6 Phase 6 — the last phase, built exactly in its four steps:

### 1. Recall hooks (`core/recall.py` + `prompts/r01_recall.txt` + `tools/add_recall_hooks.py`)

- Every existing GitHub note gets ONE delimited, app-managed block
  (`<!-- gitcurator:recall:start --> … end -->` — the markers
  `note_state` has owned since Phase 1) with a NEUTRAL "Best used for"
  sentence. Website notes already carry the field in their body.
- The prompt does NOT use `about_me.md` (only the GitHub repo prompts
  personalize). The sentence must start "Use when you need to" and name
  a problem; anything else (marketingese, a bare prefix, multi-line)
  falls back to the explicit "not captured" placeholder — omit rather
  than guess.
- Fingerprint-invisible: `note_state.compute_fingerprint` strips the
  block, so an app-added hook NEVER reads as a human edit (tested).
  Idempotent, replace-in-place, CRLF-preserving.
- The tool is DRY-RUN BY DEFAULT with a diff report under
  `reports/recall/`; `--sample 20` implements the SPEC's "the owner
  approves 20 before any bulk run", `--apply` writes.

### 2. Embeddings (`core/embeddings.py`)

- "Embed only the recall field, the one-line description and the tags.
  Never full text" — `embed_text_for` builds exactly that.
- Providers: local Ollama (`/api/embed`, with the legacy
  `/api/embeddings` fallback auto-detected for older servers) and ANY
  OpenAI-compatible `/v1/embeddings` endpoint — llama-server with
  `--embeddings`, LM Studio, vLLM, cloud. Every loopback call bypasses
  the system proxy (the v0.15.1 rule).
- Vectors live in SQLite (own `embeddings` table in cache.db, the
  NoteStateDB pattern), keyed by (vault, source URL, model) with a
  text-hash for stale-only refresh. Pure-Python cosine — no numpy.

### 3. Candidates + LLM confirmation (`core/linking.py` + `prompts/l01_confirm.txt`)

- Cosine neighbors across BOTH machine vaults ("across domains and
  within a domain"), `--top-k` per note above a cosine floor.
- One LLM call per candidate pair: related? yes/no + a short reason
  (neutral prompt, no `about_me.md`). A NO is recorded as rejected — it
  is never asked again.

### 4. The link store + the two surfaces

- `link_suggestions` table in cache.db: pending / approved / rejected.
  A pair is suggested AT MOST ONCE (rejected pairs never reappear —
  SPEC acceptance, tested end-to-end). Cap: 7 approved links per note
  (strongest first).
- **The Suggestions note** (`<manual>/Library/Suggestions.md`): every
  pending pair as an Obsidian checkbox with its two source URLs on the
  line below. Tick to approve, strike the line through to reject —
  `build_links.py --collect` reads the ticks back, and only lines
  carrying the URL pair are ever parsed (free text cannot be misread).
- **Related (auto) blocks**: written ONLY into the `Library/` MIRROR
  copies (found by their `mirror_of` marker) — never the machine vaults,
  never unmarked files, never outside `Library/` (all tested). The
  Phase-5 mirror itself now carries approved blocks across re-syncs
  (`preserve_related_block` in `_plan_tree` — the rebuild no longer
  wipes them; tested with a full re-apply).
- Silent otherwise: no Telegram messages about links.

### Also

- GUI: More menu → "🪝 Recall hooks (dry-run)" and "🔗 Build link
  suggestions" (the SAFE defaults, streamed to the log; the
  apply/collect steps stay on the command line where the owner-approval
  flow lives).
- Config: `embedding_model` (both constants modules; empty = provider
  default — `nomic-embed-text` on Ollama, the served model on
  llama.cpp).
- Suite: **504** (45 new in `tests/test_phase6.py`: block ops +
  fingerprint invisibility, sanitizers, field extraction, prompts
  filled + neutral, plan/run dry-run/apply/unchanged, cosine + embed
  text, fake `/v1/embeddings` + `/api/embed` + legacy fallback servers,
  provider routing, store roundtrip + stale refresh, candidate pairs,
  confirm parsing, the link store lifecycle + never-resuggest + cap,
  the Suggestions note roundtrip (tick/strike/free-text), Related
  blocks mirror-only + removal, the mirror carry-over, and both tools
  end-to-end over synthetic vaults). CI: **36 modules** compiled +
  offline golden 30/30.
- Test-caught en route: `ollama_embed` treated an HTTP 404 on
  `/api/embed` as fatal instead of falling back to the legacy route;
  `recall_fields_for` broke its own uniform contract for website notes
  (`recall` vs `best_used_for`); a bare "Use when you need to" prefix
  (a refusal in disguise) passed the sanitizer.

## [0.15.1] — the automatic llama.cpp catch — 2026-09-29

Owner report after testing the v0.15.0 zip: "it still doesnt auto-detect
llama cpp, the service is running on task manager, the app must
automatically catch that!" (+ "the app runs, you can continue for next
phase"). The app ran fine; the detection never fired on its own. Four
root causes, four fixes, all covered by 30 new tests (459 total):

1. **Detection was never automatic** — it lived behind the 🔍 Detect
   button and the batch pre-flight. Now ~1.5s after launch a daemon
   thread probes for llama-server and the result is applied on the GUI
   thread through a queued signal: when the current provider is
   unusable (Ollama not running — the default — or a keyless cloud
   endpoint), the app SWITCHES to llama.cpp itself, fills URL + model
   and saves; a working provider is never overridden (one hint line).
   `llamacpp_autodetect_decision()` is a pure, Qt-free function; the
   GUI applies it (`_startup_llamacpp_autodetect` /
   `_apply_llamacpp_autodetect`).
2. **System proxy swallowed the loopback probes** — urllib's
   `urlopen` consults env/registry proxies, and the owner's machine
   runs a proxy/VPN client (GitCurator itself ships a proxy monitor for
   Telegram). Every llama.cpp probe now bypasses proxies entirely
   (`ProxyHandler({})`), and `openai_chat`/`openai_list_models` do the
   same for LOOPBACK endpoints only (cloud endpoints keep proxy
   support — they may need it).
3. **Non-default ports were invisible** — the scan guessed 5 ports.
   Now the app reads llama-server's ACTUAL listening ports from the OS
   process table first: `tasklist` + `netstat` on Windows, `ss`/`netstat`
   on POSIX (`llamacpp_process_ports()` — never raises, 5s subprocess
   cap, no console-window flash under the packaged GUI). Any `--port`
   is caught; the common-port list grew to 11 as the fallback.
4. **/props-less builds were skipped** — when `/props` 404s (older
   llama.cpp releases/forks), the server is now positively identified
   through `/v1/models` fingerprints: the `Server: llama.cpp` response
   header, `"owned_by": "llama.cpp"` entries, `.gguf`-suffixed ids.
   vLLM / LM Studio / plain OpenAI proxies match none of these — still
   never misreported.

Also: the CLI `--status` / preflight / wizard, the GUI Detect button
and the worker preflight all ride the new discovery automatically;
`detect_llamacpp()` results now carry `via` ('process' | 'scan') and
probes carry `identified_by` ('props' | 'models:<fingerprint>').

## [0.15.0] — llama.cpp engine detection — 2026-09-29

The owner asked for it directly: "the app must have llama.cpp engine
detection. for example it currently sees ollama, or custom api, but it
must detect llama.cpp service and it's model detected automatically."
The app saw two providers (🧠 Local Ollama, ☁️ OpenAI-compatible
endpoint); a local **llama.cpp `llama-server`** is now the third — and
it is DETECTED like Ollama, not hand-configured like the cloud option.

### What you get

1. **Positive server identification** (`core/llm_client.py`): llama.cpp
    is identified through `/props` — a llama.cpp-ONLY route whose JSON
    shape (model_path / model_alias / default_generation_settings /
    total_slots) no other server produces. A vLLM/LM Studio/dev server
    answering on the scanned ports is skipped, never misreported.
    `/health` reports ready vs still-loading (503 = loading, handled).
2. **Automatic model detection**: the model id comes from `/v1/models`
    (the `/props` alias/basename is the fallback for builds that hide
    the route). An empty `llamacpp_model` in config.json means "ask the
    server" — the GUI Detect button, every batch pre-flight, the CLI
    wizard/status/preflight, the backfill tool and the golden runner
    all auto-fill AND persist what the server actually serves.
3. **GUI** (Settings → 🧠 LLM): a third radio 🦙 llama.cpp (local) with
    its own group — Server URL (llama-server default
    http://127.0.0.1:8080/v1), optional API key (--api-key only), a
    model combo, **🔍 Detect** (port scan 8080→8081→8082→8083→8000,
    positive /props ID, fills URL + model, /health state) and
    **🔄 Refresh**; "🦙 Test llama.cpp" joins the More menu; the
    LLM-failure dialog lists the llama-server's own models.
4. **Batch pre-flight** (worker): probe the configured URL → when dead,
    scan the common ports once and SWITCH to what is found (saved to
    Settings, like the Ollama single-model auto-switch) → auto-fill the
    model → a definitively absent server aborts the batch with the
    exact `llama-server -m <model>.gguf --port 8080` command (same
    policy as "Ollama not available" — no 100 per-link failures).
5. **Chat rides the hardened OpenAI-compatible path**: llama-server
    speaks the protocol natively, so the wall-clock timeout, JSON mode
    with memoized fallback and the `llm_num_ctx` over-budget warning
    all apply unchanged. `normalize_llamacpp_api_url` accepts every
    user spelling (`127.0.0.1:8080`, `http://host:8080/`, full `/v1`).
6. **CLI + tools**: `--cli --init` choice 3 (the wizard probes the
    server and offers its model as the default), `--cli --status`
    llama.cpp row + live detection line, the run card, the batch
    pre-flight (auto-detect + persist + clear abort),
    `backfill_websites.py --provider llamacpp`, and
    `run_golden_websites.py --live --backend llamacpp`.
7. **Config**: `llamacpp_api_url` / `llamacpp_api_key` /
    `llamacpp_model` (empty = auto) + `llm_provider: "llamacpp"` —
    old configs load unchanged (tested).
8. **Test-caught en route** (fixed): the phase-4 router-test restore
    assigned the unwrapped `_call_cloud_llm` back as a PLAIN function —
    every later `self._call_cloud_llm(...)` in the same process would
    have passed `self` as `api_url` (latent since v0.13.0, surfaced by
    the new end-to-end router test); fake-server state was written to
    the handler CLASS instead of the per-server dict; and a 503
    `/health` answer raised HTTPError instead of reporting "loading".
9. Tests: `tests/test_llamacpp.py` (49) against a fake llama-server
    (stdlib http.server serving /props, /health, /v1/models,
    /v1/chat/completions) — suite **429**.


## [0.14.1] — the first Windows zip — 2026-09-29

The owner asked for "the first .zip version to test locally". This is
it: a deterministic, one-command build of everything a Windows machine
needs, with double-click launchers and a 3-step quickstart.

### What you get

1. **`tools/build_zip.py`** — builds
    `app/reports/dist/GitCurator-v0.14.1-windows.zip` from
    git-**tracked** files only, so local junk can never ship; a
    tracked `config.json` / `cache.db` / session file refuses the
    build outright (defense in depth). Dev-only trees are excluded
    (`tests/`, `_attic/`, `cloudflare-bot/`, `list of changes.txt`),
    `VERSION` is stamped into the zip, and the build is
    **byte-identical across rebuilds** (fixed entry order +
    timestamps) — a released zip is always reproducible from its tag.
2. **Six double-click launchers**, every one enforced pure-ASCII +
    CRLF by the build (the v0.09.1 codepage-reparse bug class can
    never ship again):
    - `1-INSTALL.bat` — run once after unzipping: creates the app's
      private `.venv` and installs the requirements (finds Python
      3.10+ via `python` then `py -3`; the Microsoft Store stub is
      filtered out by the version check).
    - `GitCurator.bat` — the desktop app.
    - `GitCurator-DRY-RUN.bat` — the fully-automatic run with every
      write only **reported**: the safe first run.
    - The three existing CLI bats now prefer the private `.venv`
      (system Python still works).
3. **`WINDOWS-QUICKSTART.md`** — the 3-step guide (install → start
    → dry-run), a what-each-file-is table, the owner's vault paths,
    and the update-later recipe (copy `config.json` over).
4. **Verified end-to-end in the build sandbox**: unzip to a fresh
    folder → fresh venv → `pip install -r requirements.txt` →
    `main.py --cli --status` runs clean (full vault map + cache
    sections), the GUI module imports, every `.bat` is ASCII + CRLF,
    the zip is 73 files / 585 KB.
5. Tests: 13 new (`tests/test_packaging.py` — policy tables, the
    ASCII/CRLF enforcement, determinism by double-build sha256, the
    real build via subprocess, first-run files present, secrets and
    excluded trees absent, zip validity + size). Suite: **380**.
    CI: 31 compiled modules.

## [0.14.0] — Phase 5: the Manual Notes Library mirror — 2026-09-29

Phase 5 of the phased build (SPEC.md §6): your ideas vault gets a
read-only mirror of both libraries, and nothing else in it is ever
touched.

### What you get

1. **`core/mirror.py` + `tools/mirror_manual.py`** — one-way sync from
   the GitHub and Websites vaults into
   `<manual_vault>/Library/GitHub Projects/…` and
   `<manual_vault>/Library/Websites/…`, so you can write
   `[[a library note]]` in an idea and see the backlink in Obsidian.
2. **Dry-run by default.** Running the tool prints and reports the
   plan and writes nothing; `--apply` performs the sync. Every safety
   check re-runs before a real run.
3. **Only `Library/` is ever touched — proven.** 47 new tests on
   temporary folders assert nothing outside `Library/` is ever
   created, changed or deleted; only files carrying the new
   `mirror_of` front-matter marker are ever updated or deleted (a
   marker lookalike without the marker survives); any other file in
   the way of a mirror copy is a reported conflict, never overwritten.
   Target paths are built from validated components (no `..`, no
   absolute paths, no drive letters) and re-verified by realpath
   containment before every write and delete.
4. **Moves propagate** (matched by `source`, per SPEC §4.4): a note
   you moved between category folders re-mirrors at the new location
   and the stale copy is removed; deletions remove the orphaned mirror
   copy. The sync is **idempotent** — a second run performs zero
   writes (byte-identical).
5. **Refusals**: the mirror refuses to run when `manual_vault_path`
   is unset or overlaps either machine vault in EITHER direction
   (manual inside machine, machine inside manual), or when `Library/`
   exists but is a file, or (on `--apply`) when the manual vault
   folder does not exist yet — the owner creates their own vault.
6. **Mirror notes** carry `mirror_of` plus a read-only
   `> [!warning]` banner as the first body line. GitHub notes' banner
   image references (`attachments/banners/…`) are dropped from mirror
   copies — they could never resolve inside the manual vault (the
   image would have to live outside `Library/`, which is forbidden).
7. **Wiring**: `--cli --status` gains a "Library mirror" row; the
   GUI 📁 Vault page's Manual Notes group now says what the app
   actually writes and points at the tool. `config.example.json`
   unchanged (`manual_vault_path` has existed since v0.10.0).

### Owner review (SPEC)

Run it against a **copy** of Manual Notes:
`python gitcurator/tools/mirror_manual.py --manual-vault "<copy>"`,
then with `--apply`, then write `[[a library note]]` in an idea and
check that Obsidian shows the backlink.

### Tests

`tests/test_phase5.py` (47): note construction (marker, banner,
banner-ref stripping, no-front-matter wrap), refusals (all overlap
directions, unset paths, apply-needs-existing-folder),
`_safe_join` traversal battery, the outside-Library guarantee,
dry-run-writes-nothing, idempotency, source-edit updates, move
propagation (incl. a two-note swap), orphan deletion, duplicate
sources/markers, conflict handling, empty-dir cleanup, the tool
(config-driven paths, exit codes 1/2, report-inside-vault refusal,
report content), the CLI status row, the GUI wiring. CI: 30 compiled
modules, 367 tests + the offline golden run.

## [0.13.0] — Phase 4: LLM backends — 2026-09-29

Phase 4 of the phased build (SPEC.md §6): every OpenAI-compatible
endpoint is now a first-class backend, and the LLM client is honest
about timeouts, JSON mode and context.

### What you get

1. **The relabel**: "Cloud API" is now
   **"OpenAI-compatible endpoint (llama.cpp, vLLM, LM Studio, cloud)"**
   — GUI radio + settings group, CLI wizard, README, status lines. The
   config value stays `cloud`; old configs load unchanged.
2. **The same timeout wrapper for both providers** (`core/llm_client.py`):
   every endpoint call goes through the wall-clock `call_with_timeout`
   that already protected Ollama (configurable `llm_timeout_s`) — a hung
   llama.cpp server can no longer freeze a batch.
3. **JSON mode with a clean fallback**: `response_format: json_object`
   is sent on classification/analysis calls; a server that answers 400
   to the parameter gets exactly one retry without it, and the rejection
   is memoized per endpoint (one doomed attempt per process, never one
   per call). Clear errors (`CloudLLMError` family) on malformed bodies,
   error objects and empty-choices responses.
4. **The context window is explicit** (`llm_num_ctx`, default 8192, new
   Settings field): sent as `options.num_ctx` on EVERY Ollama call —
   Ollama's own small default used to truncate long prompts from the
   front **silently**; for OpenAI-compatible endpoints it powers an
   over-budget warning (their window is fixed at launch: llama.cpp
   `-c`, vLLM `--max-model-len`). Nothing is truncated silently on
   either backend — an over-budget prompt is logged before the call.
5. **/v1/models pre-flight**: every cloud batch (and the Test Connection
   button) first asks the endpoint for its model list; a configured
   model missing from it — including the optional per-task overrides —
   is a warning, never a block (servers that hide /models are fine).
6. **Per-task model overrides** (optional, `config.json`):
   `"models": {"classify": "", "analyze": ""}` — e.g. a bigger-context
   model for the w01/w02 classification passes and a fast one for note
   writing. Empty (default) = the single configured model, exactly as
   before. Applies to both providers, the GitHub analyze pass and the
   websites pipeline; the backfill + golden runner honor it too.
7. **Past corrections as classifier examples** (the deferred Phase-3
   few-shot item): the w01 category prompt carries a PAST_CORRECTIONS
   block built from the corrections log — this URL's own history first,
   then your three most recent moves in the Websites vault — so the
   model follows your filing taste instead of re-guessing.
8. **The golden set runs on both backends**:
   `tools/run_golden_websites.py --live --backend openai|ollama`
   (model pre-flight, `--num-ctx`); comparison report in
   `docs/reports/golden-backends-report.md`.

### Compatibility

- Ollama users: identical behavior plus an explicit `num_ctx=8192` on
  every call (set `llm_num_ctx: 0` to leave the window to the server).
- Cloud users: same URL/key/model keys; calls now carry
  `response_format` (rejected servers fall back automatically) and a
  configurable timeout instead of the old hardcoded 120s.
- Old configs load unchanged; both new keys are optional with safe
  defaults.

### Tests

- 45 new (`tests/test_phase4.py`): a stdlib fake OpenAI server (success,
  response_format rejection + memoized fallback, wall-clock timeout,
  malformed JSON, error objects, connection refused, /v1/models
  pre-flight), ollama_chat (num_ctx on every call, over-budget warning,
  both response shapes), the REAL ollama client library against a fake
  Ollama server (options.num_ctx verified on the wire), per-task model
  routing through the real ProcessingWorker routers, config
  compatibility, the relabel, the golden-runner backends, and the
  corrections-as-examples hook.
- Full gate: 28 compiled modules, 320 tests (was 275), offline golden
  30/30 with 0 invalid answers.

## [0.12.0] — Phase 3: moves are corrections + the backfill — 2026-09-29

Phase 3 of the phased build (SPEC.md §4.4 + §6): **your folder moves are
corrections, never damage** — and your existing bookmarks can finally be
loaded into the Websites vault.

### What you get

1. **The §4.4 state machine runs at the start of every batch, for both
   vaults.** Move a note to another category folder and the app now
   ACCEPTS it: the note's `category:`/`subcategory:` lines and tags are
   updated to match the new folder (targeted line edit, atomic write),
   a `category_locked: true` line is added, the move is logged in the
   new corrections log, and the note is never moved back. Moving a note
   OUT of `_review/` counts as a correction the same way.
2. **Edits are flagged, never touched.** A note you edited by hand is
   listed in the run report and skipped. Duplicates are flagged, neither
   copy touched. Notes without a `source:` line are ignored and listed.
   Moves into a folder the taxonomy doesn't define are kept exactly where
   you put them and reported — the app never invents a category.
3. **Deletes are permanent.** Delete a note and its URL goes on the
   dismissed list — it is never re-added, even if the link comes back
   from the bot. It's listed in the run report in case it was accidental.
4. **The classifier skips locked notes.** A note you placed yourself is
   locked: the model's category is ignored on every re-processing
   (retry upgrades included) and your folder wins. The bulk
   "Recategorize Notes" dialog skips locked notes too.
5. **`--cli --status` shows the record**: baseline counts, corrections
   applied, dismissed URLs — per vault.
6. **`tools/backfill_websites.py`** — load your existing bookmarks
   (`unique_links.csv`, ~930 links: 777 websites + GitHub links excluded
   by the same routing the app uses). Resumable (a per-URL checkpoint in
   `cache.db` — interrupted runs pick up exactly where they stopped),
   polite (a fixed pause between links plus the pipeline's per-domain
   pause), small batches (`--limit 30` new links per run), dry-run first
   (`--dry-run` writes nothing anywhere), progress lines + a Markdown
   batch report (`--report`). Works with your local Ollama or any
   OpenAI-compatible endpoint (`--api-url/--api-key/--model`; Cloudflare
   Workers AI's `/ai/v1` endpoint included).
7. **The run report gains a "Note State" section**: moves accepted
   (with "N notes moved from X to Y" lines), edits, deletes, duplicates,
   unmanaged files, unmapped folders, unknown notes — per vault, plus
   baseline notices and dismissed-URL counts.
8. **Websites-vault folder awareness fixed**: website notes live under
   taxonomy NAMES (`Design/UI-UX & Product Design`), which the GitHub
   category map cannot parse — the state machine now validates website
   folders against the taxonomy (nested legal folders are never
   "unmapped").

### Compatibility

- Websites pipeline OFF (the default) → the run-start state machine runs
  for the GitHub vault only, and nothing else changes vs v0.11.0.
- Old configs load unchanged; no new required keys.
- The first run after v0.12.0 records a silent baseline (nothing flagged
  on your 600+ existing notes) — then corrections begin.

### Tests

- 36 new (`tests/test_phase3.py`): front-matter surgery, corrections +
  dismissed tables, the full §4.4 table on temp vaults (moves, edits,
  deletes, duplicates, unmapped, idempotency, dry-run), the taxonomy
  resolver, locked-skip through the real pipeline, goodrepos/_moc
  agreement after moves, and the backfill tool (routing, resume,
  interrupt, limit, dry-run).
- Full gate: **28 modules compile, 275/275 tests green, offline golden
  run 30/30, 0 invalid.**

### Known limits

- The corrections log is a record (report + `--status`); a browsing UI
  for it comes with the dashboard work later in the build.
- The optional "most similar past corrections as classifier examples"
  item from the SPEC is deferred to Phase 4's backend work (needs the
  models-classify override first).

---

> 404 quarantine, its own rich-based CLI) while the sandbox lineage shipped
> **0.07.2 / 0.07.3** (CLI model picker + pre-flight, GUI strike manager).
> Both branches are preserved below, as-is. **v0.09 is the merge**: one
> tree, one CLI, one dead-link system, going forward.

## [0.11.0] — Phase 2: the Websites pipeline — 2026-09-29

Phase 2 of the phased build (SPEC.md): **the website pipeline**. Non-GitHub
links no longer dead-end in `_inbox/` review tables — when you flip the
Websites switch ON (Settings → 📁 Vault), every non-GitHub link from the bot
queue, imports and Telegram fetches is fetched politely, read, classified
into your category taxonomy, analyzed, and written as a real note into the
Websites vault. With the switch OFF (the default), nothing changes — links
still go to `_inbox/` exactly as before (verified by test).

### What you get

1. **A real second pipeline (SPEC §4.3).** Per link: canonicalize the URL
   (tracking parameters like `utm_*`/`fbclid`/`gclid`/`ref` are ignored;
   meaningful ones like a YouTube `?v=` are kept), dedupe against the
   Websites vault index + `cache.db` + the dismissed list, fetch politely
   (timeout, 2 MB cap, per-domain rate limit, a clear User-Agent), extract
   title / description / main text, classify in two passes, analyze, then
   write the note atomically to `<Websites vault>/<Category>/<Subcategory>/
   <Name>.md`. GitHub Pages links (`owner.github.io/repo`) now route to the
   GitHub pipeline as their repo; gists go to the Websites pipeline with a
   `#snippet` tag.
2. **Your taxonomy file is the source of truth (SPEC §4.6).**
   `app/taxonomy/website-library-categories.md` is parsed — 14 categories,
   16 subcategories, the judgment-call rules passed verbatim to the
   classifier. Every model answer must exactly match a parsed name;
   invalid answers are retried twice with a corrective nudge, then the
   note lands in `_review/` instead of a wrong folder. Low-confidence
   answers also go to `_review`. Nothing is ever filed under a name you
   didn't write.
3. **Notes in the SPEC §4.5 shape**: Name, one-line description (≤25
   words), 3–6 core offerings, standout feature, **Best used for** ("Use
   when you need to…"), pricing (`free|freemium|paid|unknown`), login
   required, similar tools (only when confident), source link — plus the
   Phase 1 ownership stamps and `fetch_status: full|partial|failed`.
   `partial` covers JavaScript-only shells, paywall stubs, PDFs and
   size-capped pages (the model then works from title + description only).
4. **Nothing is silently dropped (SPEC §4.3 failure handling).** A link
   that cannot be fetched still gets a minimal note in `_review/` with
   `fetch_status: failed`, and is retried automatically up to 3 times over
   several days (state in `cache.db`). When a retry finally succeeds, the
   `_review` placeholder is upgraded to a full note — and the placeholder
   is only removed when it is still byte-identical to what the app wrote;
   if you edited it by hand, it is kept and flagged instead.
5. **The golden set (SPEC §6).** Your 30 bookmark candidates are finalized
   as `app/tests/golden/websites.json` with proposed expected categories
   (the one unresolved t.co short link resolves to phosphoricons.com —
   verified). `gitcurator/tools/run_golden_websites.py` has two modes:
   `--offline` (fake LLM, canned pages — runs in CI on every push, zero
   network) and `--live` (real fetches + your configured LLM), both
   writing a side-by-side expected-vs-actual Markdown report. The live
   report for this release: `docs/reports/golden-websites-report.md` —
   0 invalid category names (the pipeline's validation held).
6. **Wiring**: the Websites pipeline runs after the GitHub loop in every
   batch (GUI and CLI/headless alike), the run report and summary log
   gained a Websites section, `--cli --status` shows websites counters
   (processed / retry queue / dismissed), and Stop works mid-phase. New
   optional config keys with safe defaults: `web_fetch_timeout_s`,
   `web_fetch_max_bytes`, `web_domain_delay_s`.

### Diagnosis / notes
- GitHub pipeline OFF + Websites ON now works: the batch still fetches,
  GitHub links are logged and skipped, websites process normally. Both
  OFF = the old early return.
- The Cloudflare API tokens on file carry no Workers AI permission
  (verified — both fail the AI endpoints while being valid tokens), so
  the live golden run used a temporary OpenAI-compatible endpoint in the
  build sandbox. Phase 4 runs the golden set on your real backends
  (Ollama / llama.cpp) per the spec.
- GitHub Actions on github.com has been unable to start runners since
  2026-09-24 ("recent account payments have failed or your spending
  limit needs to be increased" — Settings → Billing & plans). The local
  gate mirrors CI exactly (same compile list, same test command) and is
  fully green; the workflow itself triggers correctly.
- Dedupe identity for websites is the canonical URL *with meaningful
   query parameters* — a separate normalizer from GitHub's, which is
   untouched (SPEC §4.3.1).

### Verification
- 71 new tests (`tests/test_phase2.py`): taxonomy parsed against the
  REAL file (counts, tricky names, emoji/italic/em-dash stripping, the
  (no subcategories) marker, judgment rules, definitions, tag hints,
  safe folder paths); routing (github.io mapping, gist flag,
  canonicalization); fetch on a local HTTP server (redirect, 404,
  timeout, size cap, PDF, windows-1252, redirect loop, rate limiter);
  extraction on saved fixtures (article, landing, JS shell, paywall,
  non-UTF-8, huge); prompt-slot discipline (unfilled / empty / smuggled
  markers refused); state DB (backoff, cap, resolve, dismiss); the
  pipeline end-to-end with a fake LLM (note format, 4-layer dedupe,
  _review + retry + upgrade + hand-edit protection, classification
  validation + corrective retries, hostile-output sanitization, dry-run
  writes nothing, Stop); REAL ProcessingWorker batches (websites ON in
  a full batch, OFF keeps writing `_inbox/` unchanged, github-off +
  websites-on, both-off); golden set integrity + the offline runner.
- Full suite: **239 tests, all passing** (168 existing + 71 new). CI now
  compiles 27 modules, runs eight test modules and the offline golden
  run.
- Live golden run: 30 links, real fetches, real model — report committed
  at `docs/reports/golden-websites-report.md`.

## [0.10.0] — Phase 1: the app learns there is more than one vault — 2026-09-29

Phase 1 of the phased build (SPEC.md): **vault settings and ownership**.
The GitHub pipeline behaves exactly as before (verified by test); what's
new is the app's *awareness* of the vaults to come, and an ownership stamp
on new notes so future phases can always tell machine writing from yours.

### What you get

1. **The 📁 Vault settings page grew a vault map.** Under the existing
   GitHub vault picker you now find: a **Websites vault** path with a
   live status under it — *not set / will be created / found* — plus the
   private repo that will back it up; a **Manual Notes vault** path (for
   Phase 5, optional today) with its own live status; and two **pipeline
   switches**: GitHub (ON by default, today's behavior exactly) and
   Websites (**OFF** — the Phase 2 pipeline it enables doesn't exist yet,
   so the switch is wired but has nothing to drive).
2. **`--cli --status` shows the same vault map** (plus the taxonomy file
   status and a new *Note state* section — see #4) so you can check the
   setup from the CLI: websites vault, manual vault, websites repo,
   pipelines, taxonomy.
3. **New notes carry ownership stamps** (SPEC §4.1/§4.5): frontmatter
   `managed_by: gitcurator`, `schema_version`, `prompt_version`, and a
   one-line banner at the top of the body — `> [!info] Managed by
   GitCurator — machine-written note.` And the three human placeholder
   sections (*My Ideas & Notes*, *Social Signal (Manual)*, *Journal*) are
   **no longer written into new GitHub notes**: that writing now lives in
   your future Manual Notes vault, so the placeholders had nothing to
   hold. **Existing notes are untouched** — never rewritten, never
   migrated.
4. **The "moves are corrections" record (SPEC §4.4) now exists.** A new
   `note_state` table in `cache.db` keeps, per note: its vault, source
   URL, path, a content fingerprint, its folder-category, and a locked
   flag. The **first real batch you run records the baseline silently**
   (nothing acts on it yet — Phase 3 turns on the comparison that
   accepts your folder moves as corrections and flags hand edits). The
   fingerprint deliberately ignores a delimited "recall" block the app
   may add in Phase 6, so the app's own future additions can never look
   like your edits. The Phase 0 scan tool still detects your writing in
   the three legacy sections of *old* notes — exactly as before.
5. **A second VaultSeal for the Websites vault** — the backup mechanism
   the GitHub vault already has, parameterized for the new vault and its
   own private repo. It only ever runs when the Websites pipeline is ON
   (it is OFF), so today it is dormant, tested machinery.
6. **New optional config keys** (all safe defaults, old `config.json`
   files load unchanged — tested): `website_vault_path`,
   `manual_vault_path`, `website_repo_name`, `taxonomy_path`,
   `pipelines: {github: true, websites: false}`. `vault_path` keeps
   meaning "the GitHub Projects vault". `config.example.json` documents
   them, pre-filled with your two new backup repos
   (`my-awesome-github-directory`, `my-awesome-websites-directory`).

### Diagnosis / notes
- Baseline recording is skipped in a dry-run (a rehearsal must not write
  bookkeeping either) — same principle as Phase 0's shadow cache.
- The GitHub pipeline switch is checked **before anything is fetched**:
  flipping it OFF consumes no queue, marks nothing, writes nothing.
- `scan_vault_edits.py` needed no logic change: on new notes the three
  legacy sections read as "missing" (a handled state since Phase 0); its
  placeholder strings are now explicitly marked as the pre-v0.10.0
  legacy format.
- The record-on-write hook fingerprints the note **from the in-memory
  content** (no re-read) right after the atomic write succeeds.

### Verification
- 38 new tests (`tests/test_phase1.py`): fingerprint stability + the
  recall-block invisibility rule; folder→category mapping (including the
  `<Category>/<Subcategory>` layout Phase 2 uses); baseline one-time
  recording, unmanaged-notes exclusion, two-vault independence; every row
  of the §4.4 table detected (moved / edited / deleted / duplicates /
  unmanaged / unmapped / unknown); note format (stamps in, banner in,
  legacy sections out, existing sections intact); old-config fallbacks
  and the pipelines merge; VaultIndex on two synthetic vaults; the
  websites seal's three skip paths + real seal; the CLI vault map; and a
  REAL ProcessingWorker batch on a synthetic vault recording the
  baseline (with a dry-run of the same batch recording nothing).
- Full suite: **168 tests, all passing** (130 existing + 38 new). CI now
  compiles 21 modules and runs the seven test modules.

## [0.09.5] — Phase 0 groundwork: rehearse before you touch the vault — 2026-09-29

The phased build (SPEC.md) starts here. Phase 0 adds **tools and safety
nets only — no behavior change to the app**. Everything below runs
against a vault you point it at; nothing was run against a real vault.

### What you get

1. **`--dry-run` on the CLI** — add it to any batch command
   (`python main.py --cli --auto --dry-run`, `--import-file`, ranges,
   `--retry-failed`) and the batch runs for real — fetches, analyzes,
   builds every note in memory — but **writes nothing**: no notes, no
   banners, no inbox tables, no master index/MOCs, no reports, no
   manifest. It also skips the three post-batch side effects: the
   VaultSeal backup push, the Good Repos publish, and marking the bot
   queue read / advancing `last_processed_msg_id`. At the end it prints
   how many vault operations were withheld and saves a readable report
   of every one (with a content preview) to `app/reports/dry-runs/`.
   Even the anti-repeat state is safe: the batch reads the real
   `cache.db` through a throwaway copy, so a dry-run can never mark
   links "processed" and make the next real run skip them.
2. **`gitcurator/tools/scan_vault_edits.py`** — a strictly read-only
   scan of any vault folder: how many notes per category (and in
   `_review`, and in folders that don't match any known category), which
   notes are missing their `source:` line (invisible to the anti-duplicate
   index), which notes share the same `source:` (duplicates), and which
   notes contain anything you wrote yourself in *My Ideas & Notes*,
   *Social Signal (Manual)* or *Journal* beyond the template placeholders
   — the content the app must never overwrite. Writes a Markdown report
   **outside** the vault (`app/reports/scan/`); refuses to write inside
   the vault it scanned.
3. **`gitcurator/tools/snapshot_vault.py`** — zips the entire vault
   (notes, `_moc`, `_inbox`, `attachments`, even `.obsidian`) into one
   timestamped `.zip` outside the vault (`app/reports/snapshots/`).
   Restore is a plain "extract here". Run this before any risky
   operation on a real vault.
4. **`gitcurator/tools/pick_golden_links.py`** — reads your
   `unique_links.csv` (columns detected automatically; tolerates
   semicolons, tabs, BOM, headerless files), excludes GitHub repo links
   (they belong to the GitHub pipeline), and picks **30 diverse
   candidates** — spread across as many domains as possible, always the
   same pick for the same file — into `app/tests/golden/websites_candidates.json`
   as the candidate golden set for the future Websites pipeline. You
   approve the final list in Phase 2.

### Diagnosis / notes
- The dry-run switch lives in the new `core/dryrun.py` (pure stdlib, no
  PyQt). `core/storage.py`'s atomic writers consult it, which
  automatically covers every canonical vault write today and in future
  phases; the handful of legacy raw writes inside the batch (master
  index, MOCs, per-run reports, the 404 log, banner failure markers,
  inbox tables, the link manifest, folder creation, banner moves) were
  each routed through behavior-identical helpers — same bytes on disk
  when dry-run is off.
- The per-platform inbox tables are now written through the shared
  atomic writer (`storage.atomic_write_text`) instead of a duplicated
  inline copy — identical content, plus fsync durability.
- An end-to-end rehearsal of the dry-run (fake LLM, stubbed GitHub API,
  synthetic vault) caught a real deadlock in the fresh-install case (no
  `cache.db` yet): the shadow-cache copy ran while holding the log lock.
  Fixed, with a regression test that fails instead of hanging.
- Cosmetic, known: during a dry-run, a few log lines still say e.g.
  "Final report saved: …". Read them as "would save" — the authoritative
  truth is the closing "DRY-RUN COMPLETE" line and the dry-run report.

### Verification
- 24 new tests (`tests/test_phase0.py`) over synthetic vaults in temp
  folders: the dry-run module and storage gate; the real worker write
  slice (note + inbox + manifest) performing normally when off and
  touching nothing when on; the scan tool's findings and its
  read-only guarantee (hash of every file before/after); the snapshot
  zip's completeness (CRC check, unicode names, empty folders) and its
  refusals; the golden picker's diversity, determinism and column
  variants; the CLI flag plumbing.
- Full suite: **130 tests, all passing** (106 existing + 24 new). CI
  now compiles 20 modules and runs the six test modules.
- End-to-end dry-run rehearsal on a synthetic vault: 24 vault
  operations logged, vault byte-for-byte identical afterwards
  (verified by hashing every file before and after), no `cache.db`
  created, seal/publish/mark-read skipped, report written outside the
  vault.

## [0.09.4] — the bot queue can no longer look fully-unprocessed — 2026-09-22

Owner report: "it finds all 618 links in the bot to be processed, but in
reality 99.5% of them are already processed — the app thinks none of them
is processed. We had a special mechanism to make sure the app knows what
is already done (so it doesn't repeat, or duplicate by mistake)."

### Diagnosis
The GUI's anti-repeat design is **four layers**: (1) the vault index —
`VaultIndex`, rebuilt from every note's `source:` frontmatter, is the
ground truth for "already done" and filters the queue check AND every URL
during processing; (2) `cache.db` (`processed_repos` + the 404 quarantine
+ the retry queue); (3) `last_processed_msg_id` — "Process New" fetches
only bot messages newer than the last **fully-verified** batch; (4) Phase
5 CLEAR — the bot messages are only marked read when every link verified.
The CLI `--auto` flow only ever used layers 1–2 — and both could silently
fail, with no diagnostic, in ways the GUI was immune to:

- **`cache.db` was CWD-relative** (`CacheDB(db_path="cache.db")`). The GUI
  and every documented launcher run with cwd = the app folder, so it always
  opened `<app>/cache.db` — but a CLI run started from ANY other directory
  (scheduled task, `python C:\…\app\main.py --cli --auto` from a project
  folder) silently created a **parallel empty cache** in that cwd:
  processed repos, quarantine and retry queue all read 0. Split state —
  the app "thinks none of the links is processed".
- **The vault filter degrades silently**: when `vault_path` in
  config.json doesn't match the vault the notes actually live in (the GUI
  reads its live vault dropdown; the CLI only has the file — and any
  past drift, a typo in `--init`, or a moved vault is invisible), the
  classification indexes 0 notes → every queue link comes back "pending"
  → a full re-processing run into the wrong vault, duplicating ~all notes.
- **No min_id, no CLEAR**: `--auto` always re-fetched the entire bot
  history and never advanced `last_processed_msg_id` or marked the queue
  read, so every run carried the full 618-link history through the only
  two (silently fallible) dedup layers.

### Fixed
- **`cache.db` is anchored to the app folder** (`CacheDB` default path).
  One cache per installation, shared by the GUI and the CLI regardless of
  the working directory. Behavior is byte-identical for every documented
  launch path (cwd == app folder); explicit `db_path` arguments (tests)
  are untouched. This also fixes `--status` / `--list-dead` /
  `--reset-dead` / `--retry-failed`, which were equally cwd-sensitive.
- **`--auto` fetches like the GUI's "Process New"**: it passes
  `min_id=last_processed_msg_id` (printed as "Skipping bot messages up to
  ID N"), so previously-verified messages are never re-fetched at all.
  0 (first run) keeps the full-history + vault-classification behavior.
- **Phase 5 CLEAR, CLI edition**: after a successful bot-queue batch the
  LinkTracker's verdict decides — **all clear** → the bot messages are
  marked read (same engine as the GUI's auto-mark) and
  `last_processed_msg_id` is advanced and persisted to config.json;
  **anything unverified** → an explicit `⏸️ N link(s) not verified — the
  bot queue stays UNREAD and last_processed_msg_id is NOT advanced; the
  next --auto re-fetches and retries them`. Nothing is lost, nothing
  repeats — the GUI's exact semantics.
- **Dedup visibility + wrong-vault guard**: the queue summary now also
  prints the ground-truth counts ("Dedup ground truth — vault index: N
  note(s) · cache: M processed repo(s)"). On the wrong-vault signature
  (pending links, **zero** in-vault, **zero** notes indexed, but the cache
  knows processed repos) the CLI stops before processing and says so:
  which vault indexed 0 notes, what the cache knows, why processing now
  would duplicate, and the three ways to fix it (`--init`, a one-off
  `--vault` run, or editing config.json). `--yes` mode never re-processes
  on this signature; interactive mode asks `[y/N]` (default No). Fresh
  installs (empty cache + empty vault) are unaffected.

### Verification
- Reproduction harness (stubbed fetch worker, real VaultIndex/CacheDB):
  correct vault → 4 in-vault / 3 pending across quoted, unquoted,
  trailing-slash and `www.` note formats; empty-wrong vault → the exact
  owner symptom (all pending) — now caught by the guard: diagnostics,
  exit 1, batch never started.
- Phase 5 CLEAR harness (fake worker in the real Qt loop): all-clear →
  exit 0, `last_processed_msg_id` 100→777 persisted, queue marked read;
  one failed link → nothing advanced, nothing marked, retry message.
- min_id wiring: `last_processed_msg_id: 42` in config → the fetch
  receives `min_id=42`.
- Cache anchor: `CacheDB()` instantiated from a foreign cwd opens
  `<app>/cache.db`; no stray cache.db in that cwd.
- Full suite: 106/106 green (gc-venv, offscreen Qt). CLI smoke matrix:
  `--help`, `--status` / `--list-dead` from a foreign cwd, banner v0.09.4,
  PYTHONIOENCODING=cp1252 simulation — no tracebacks, correct exit codes.

## [0.09.3] — CLI `--login`: configs + Telegram verification code in the terminal — 2026-09-21

Owner request: "Update the CLI version, so the user can also enter configs
there, like telegram api id etc. and the telegram verification number —
currently it's only available on GUI."

### Added
- **`--login` command** (`python main.py --cli --login`) — the interactive
  Telegram login, end-to-end in the terminal: connects with the saved
  credentials (through the configured proxy, DC-rotation retries), and
  when the session is missing/expired the worker sends the verification
  code and the CLI prompts for it (`📩 Enter the Telegram login code:`),
  including the 2FA password when the account has one. On success the
  session file is saved — **the same `session.session` the GUI uses**, so
  every future run (CLI or GUI, `--auto` included) skips the login.
  Reuses the exact same subprocess engine as the GUI's Test Connectivity
  button (`_telegram_test_job` via `_gui_symbol`) — no new worker code,
  one code path. Failure output is actionable: rate-limited → wait and
  rerun; session/auth errors → "rerun `--login` once your proxy node
  works"; otherwise → proxy checklist.
- **`--init` now finishes the setup**: after saving config.json the wizard
  offers "Log in to Telegram now (enter the verification code)? (Y/n)" —
  `GitCurator-CLI-Setup.bat` becomes a complete first-run experience
  (credentials → code → ready). Non-interactive stdin (pipes/CI) skips
  the offer automatically; a failed login never fails the wizard itself.
- **`--auto` fetch failures now name the remedy**: when the error smells
  like session/auth ("code", "auth", "session", "password", "login"),
  the CLI prints "Tip: run `python main.py --cli --login` to
  (re-)authenticate with a fresh verification code."

### Changed
- Banner/version stamps: `v0.09.3` (cli.py banner, VERSION, README).

### Verified
- Flow simulation with a stubbed engine (success path: code typed via
  stdin → "session saved" card, exit 0; failure path: actionable retry
  advice, exit 1); no-config and missing-credentials paths exit 1 with
  the right guidance; `--help` and the no-args help list show `--login`;
  106/106 tests green after the change.

## [0.09.2] — launcher regression fixed (the "'tlocal' is not recognized" wall) — 2026-09-21

Owner report: double-clicking `Start-GitCurator-CLI.bat` printed a wall of
`'GitCurator' / 'Double-click' / '1.' / 'pre-flight' / 'Kept' / 'M' /
'tlocal' / 'tle' / '/d' is not recognized as an internal or external
command` errors **before the app started**. The app itself then ran fine.

### Root cause
- v0.09.1's belt-and-suspenders fix #2 — `chcp 65001` **inside** the
  `.bat` launchers — backfired. `cmd.exe` re-parses a batch file after a
  mid-file codepage switch, and it recomputes its file position under the
  NEW codepage: the em-dash (`—`) characters v0.09.1 also added to the REM
  comments are **3 bytes in UTF-8 but 1 byte in ANSI**, so the parser's
  byte offset shifted, cmd jumped back into the REM header block, and
  executed line fragments as commands (entering `setlocal` at +2 bytes →
  `'tlocal'`, `title` at +2 → `'tle'`, `cd /d` at +3 → `'/d'`, REM lines
  at +2/+5 → `'M'`/`'GitCurator'`/`'Double-click'`/…). It then re-synced
  at the `chcp` line and the run continued — hence "errors, then the app
  worked anyway".
- The `chcp` was **redundant from day one**: fix #1 (the in-app
  `_harden_stdio()` in `gitcurator/cli.py`: `SetConsoleOutputCP(65001)` +
  `SetConsoleCP(65001)` + UTF-8/replace std streams, at import time)
  already handles every console — and is the layer that made the owner's
  banner render perfectly in the very same run that showed the garbage.

### Fixed
- All three `.bat` launchers (`Start-GitCurator-CLI.bat`,
  `GitCurator-CLI.bat`, `GitCurator-CLI-Setup.bat`):
  - `chcp 65001` **removed** — a batch file never switches the codepage
    mid-run, so the cmd re-parse bug cannot trigger; console encoding is
    exclusively the app's job.
  - Content is now **pure ASCII** (em-dashes → `-`): a byte-identical
    parse under cp437/cp850/cp1252/65001, immune to any codepage.
  - Line endings normalized to **CRLF** (canonical Windows batch format).
- No Python changes — v0.09.1's in-app encoding fix is untouched and
  remains the single source of truth for console encoding.

### Verified
- Byte-level: all three `.bat` files are pure ASCII with CRLF terminators
  (`file` → "DOS batch file, ASCII text, with CRLF line terminators").
- Full test suite re-run (106/106 green) + CLI command matrix from three
  working directories; banner and output unchanged.
- Note for the run log that motivated this: the same console session also
  showed `Fetch failed: IncompleteReadError … Telegram closed the
  connection through your proxy (exit node blocked / DPI). Try a different
  v2ray node.` — that part is **environmental** (proxy exit node blocked
  + the Telegram session needing re-login), not a code defect: the app's
  pre-flight, proxy check, DC-rotation connect retry, and the actionable
  error all behaved exactly as designed. After switching to a working
  v2ray node, re-running the launcher will prompt for the Telegram login
  code (the CLI's interactive login path) and resume.

## [0.09.1] — CLI-not-running root-cause fix · UI polish — 2026-09-20

Two jobs, nothing else: **make the CLI actually run** and **polish the UI**.
No restructuring, no layout changes, no behavior changes.

### Fixed — the CLI was not running on Windows
- **Root cause found and reproduced**: on a Windows console with a legacy
  codepage (cp437 / cp850 / cp1252 — anything but 65001, still the default
  on many systems), the CLI's very first `print()` — the banner's
  block-drawing glyphs — raised
  `UnicodeEncodeError: 'charmap' codec can't encode characters` and killed
  the process **before any command could run**. Every `main.py --cli …`
  invocation and both double-click `.bat` launchers therefore appeared
  simply "not to run" (window with a traceback, or flash-and-close).
  **Fix (two layers):**
  1. `gitcurator/cli.py` now reconfigures stdout/stderr to UTF-8 with
     `errors="replace"` at import time (covers `--help` too, before
     argparse prints anything) and switches the Windows console codepage
     to 65001 via `kernel32.SetConsoleOutputCP` — the glyphs render, not
     just encode. No-op on already-UTF-8 streams (Windows Terminal,
     modern Linux, pipes).
  2. All three `.bat` launchers (`Start-GitCurator-CLI.bat`,
     `GitCurator-CLI.bat`, `GitCurator-CLI-Setup.bat`) now run
     `chcp 65001` before launching Python.
- **CLI no longer dies mid-command when GUI packages are missing**: the
  lazy imports of the pipeline engine (`gitcurator.gui.app`) hit that
  module's dependency guards, which call `sys.exit(1)` — a
  *BaseException* invisible to plain `except Exception` — so
  `--status` / `--list-dead` / `--reset-dead` / `--auto` could TERMINATE
  the whole process mid-command on a machine without the full GUI stack
  (despite the "no extra packages needed" launcher promise). A new
  `_gui_symbol()` helper converts the guard's exit (and any unguarded Qt
  ImportError, e.g. from `gui/icons.py`) into a clean, actionable
  `ImportError` — commands now print "pip install -r requirements.txt"
  and exit non-zero instead of a traceback or a silent kill.
- **CLI output noise removed**: importing the pipeline engine printed a
  2 KB one-line `[main] LOADED version …` blob to stderr in the middle of
  `--status` output (between banner and stats). The stamp now only prints
  for GUI loads, never in `--cli` mode.
- Verified: full command matrix (`--help`, `--status`, `--list-dead`,
  `--reset-dead`, no-args help, `--auto` paths) under simulated
  cp1252 / cp850 / latin1 consoles, from three different working
  directories, with and without PyQt6 present — correct output, correct
  exit codes, zero tracebacks. 106/106 automated tests green.

### UI polish (visual-only: tokens + stylesheets, zero layout changes)
- **The SYNC hero button finally pops** — its lavender fill was so pale
  (#C4BCF5) it read muted/disabled against the cream background (design
  review: "looks disabled"). Deepened to #B3A7F2 in both themes, and
  hover now DARKENS the fill (a real press affordance — the old
  lighter-hover felt inert).
- **Saturated status colors in dark mode** — the pastel log colors read
  muddy on plum ("warning yellow and error red lack saturation"). Now:
  mint #7CE2A9, butter #FFD37E, rose #FF9AAB, lifted info grey — all
  ≥7.5:1 on the plum panels. Light-mode warning deepened to #75510A.
- **Progress bar is visible now** — the track was nearly invisible on
  both themes. Light: warmer, darker track (#E3DACA); dark: recessed
  "well" track (#17131F, matching the log well figure-ground).
- **Log toolbar row aligned** — filter buttons (All/Errors/Warnings/
  Success) were ~25 px next to a 31 px search box. Filters are taller,
  the search box got height-harmonizing QSS (and the missing
  `setObjectName("log_search")` that the QSS needed), and focus states
  keep constant height.
- **Test Connectivity no longer a ghost in dark mode** — its fill was
  the sheet color itself (invisible body, outline only). Now the raised
  panel tone (#352F4A), balancing the saturated SYNC button.
- **Dim text lifted** — "PROCESSED" caption and breadcrumb brighter and
  11 px (was 10 px); header spacing 8 → 10 px.
- Verified with rendered offscreen screenshots in both themes, before/
  after design review: primary-action hierarchy, log scannability and
  toolbar alignment all confirmed improved; no regressions.

## [0.09] — Lineage Merge — one tree again — 2026-09-18

The owner asked for a single proper merged lineage instead of two parallel
fix-zips. This is it: **their v0.08 GUI work + our v0.07.2/3 pipeline
fixes, unified** — and one deliberate design reconciliation.

### Kept from v0.08 (the owner's local lineage)
- **Official Lucide SVG icons** everywhere (`gui/icons.py` — verbatim
  lucide-static v0.544.0 geometry, QSvgRenderer at 2× HiDPI, theme-tinted
  at render time; graceful degradation to text if QtSvg is missing).
  The icon-button clipping problem is solved by vector icons (34×30),
  superseding the v0.07 38×36 emoji workaround.
- **Main-screen redesign** — hero lavender CTA row (SYNC grows, Test
  Connectivity beside it), PROCESSED pipeline strip with caption, recessed
  log well as the stretch-growable region, status dots as tinted SVGs.
- **404 quarantine machinery** — batch-start dead-set pre-filter (dead
  links never reach the GitHub API), attempt counting in
  `decommissioned_repos.fail_count`, one aggregate summary line instead
  of per-URL spam, `notfound_links.md` written ONCE at confirmation,
  `More ▸ View 404 Quarantine` viewer + reset.
- **Telegram worker v3.4** — proxy pre-flight, own connect-retry policy
  with DC rotation (only for unauthorized sessions), session-copy tip,
  actionable network errors.
- **Config credential healing** — hand-edited trailing whitespace in
  tokens can no longer poison PyGithub headers.

### Kept from v0.07.2/0.07.3 (the sandbox lineage)
- **The 3-layer model picker** (owner report: *"it didn't let me choose a
  new model … Model wasn't found = failed cli"*): pre-flight numbered menu
  BEFORE any work, warmup stand-in resolution before the first repo,
  mid-batch recovery through `_wait_for_llm_decision(err, client, model)`
  + `model_prompt_callback`; `_pick_best_model` heuristic (embedders
  excluded → same family → same size → biggest); every choice persists to
  `config.json`. A missing Ollama model can no longer fail or degrade a
  batch in any mode.
- **Our zero-dependency visual CLI** (`main.py --cli` → `gitcurator/cli.py`,
  plain ANSI) — `--init`, `--auto`, `--status`, `--retry-failed`,
  `--mark-read`, all batch modes, `--strikes N`.
- **GUI threshold manager** (Settings → Dashboard) — rebadged as the
  quarantine manager below.

### Unified (the reconciliation)
- **ONE CLI** — the v0.08 companion (`app/cli.py` + `cli_app.py`, rich-
  based, with the green-✓-on-missing-model pre-flight that caused the
  owner's report) is retired; our engine absorbs its good ideas:
  `--list-dead` / `--reset-dead` are ported. `Start-GitCurator-CLI.bat`
  keeps its name (muscle memory) and now drives `main.py --cli --auto`.
  The `rich` dependency is gone.
- **ONE dead-link system** — the v0.08 quarantine keeps its machinery but:
  - the threshold is **configurable again** (`notfound_strike_threshold`,
    default 3, min 2 — GUI spinbox, `--strikes N`, config.json; v0.08
    hardcoded 3);
  - **consecutive semantics restored** — a successful fetch calls
    `reset_dead_links(url)` (v0.08 never reset, so stale attempts could
    quarantine a live repo after one transient miss);
  - **v0.07 caches auto-migrate** — `notfound_strikes` rows move into
    `fail_count` on first open (higher count wins), the old table is
    dropped;
  - `get_quarantine_stats()` added for viewers that show in-progress
    attempts AND confirmed rows.
- **Both quarantine viewers kept** on the one system: Settings →
  Dashboard manager (attempts + ⛔ + spinbox + reset) and the More-menu
  viewer (confirmed + reset).

### Tests & verification
- `tests/test_quarantine.py` extended 9 → 14 (configurable threshold,
  threshold-parameter steering, success-reset consecutive semantics,
  v0.07 strike-table migration incl. max-wins, stats in-progress rows).
- `tests/smoke_v007.py` rewritten for the merged design: 40 checks
  (900×600, 34×30 SVG icon buttons, icon pack renders, theme round-trip
  via tooltip, log-group stretch, quarantine API + manager + More-menu
  viewer, model-picker presence + heuristic).
- Full suite: **106/106 unit tests**, 40/40 smoke, 4/4 runner self-tests,
  16 modules compile; offline model-picker harness scenarios A/C/D re-run
  **PASS** against the merged worker; CLI `--status`/`--list-dead`/
  `--reset-dead` verified; GUI spinbox→config.json persistence +
  merge-safety re-verified; strike→quarantine migration verified e2e.
- CI: compile gate 16 modules (adds `gui/icons.py`), suite swaps
  `test_strikes` for `test_quarantine`.

## [0.08] — Icon Fix · 6:4 Window · 404 Quarantine · Visualized CLI — 2026-09-17

Four owner requests, all delivered. The headliners: every icon was
silently rendering 2× too big and clipped (the "partial sun"), the window
was an ultra-wide strip, dead repos kept being re-404'd every session,
and the app gained a fully visualized one-click CLI companion.

### Fixed — icons were big for their area ("only parts of the sun visible")
- **Root cause**: `QSvgRenderer.render(painter)` without a target rect
  paints the SVG at its NATURAL 24×24-unit size inside the painter's
  LOGICAL coordinate system. The icons render onto a DPR-2 pixmap whose
  logical area is the icon size (16 px) — so every glyph painted at
  24/16 = 1.5× (and 2× at the 12 px proxy dot) of its area and was
  clipped at the right and bottom edges. Pixel-probe before the fix:
  ink bbox `(2,2)-(31,31)` on a 32-device-px canvas touching both edges;
  after: `(1,1)-(30,30)`, no edge contact, all 12 glyphs verified.
- `icons.pixmap()` now passes an explicit `QRectF(0, 0, size, size)` so
  the viewBox maps exactly onto the icon area — the sun, moon, gear,
  layers, loader, search, trash, dots: every glyph is complete at last.

### Changed — window re-proportioned 1000×375 → 900×600 (exact 6:4)
- Owner: "the app is unnecessarily long — too much width, low height; I
  prefer a ratio like 6×4". The v33 1000×375 window was a 2.67:1
  ultra-wide strip; the new 900×600 is an exact 3:2. The extra 225 px of
  height goes to the log panel (the main view's growable region) — long
  batches show far more history without scrolling.

### Added — 404 QUARANTINE (dead links ignored after 3 tries, across sessions)
- Owner: "6-8 GitHub repos are deleted and became 404; the app repeats to
  find and 404 them again and add to the logs. After 2-3 tries across
  different sessions, ignore those links."
- **Why it kept happening**: the decommissioned-repos table existed, but
  `is_decommissioned()` was consulted ONLY on the bot-queue path. The
  Telethon channel fetch (keyword/marker/single-ID) and import-file paths
  funneled straight into `get_repo()` → 404 → log → re-decommission →
  repeat, every single session, forever.
- `decommissioned_repos` gains a `fail_count` column (with a migration:
  pre-v0.08 rows were 404'd in earlier sessions, so they start CONFIRMED
  dead — the fix takes effect on the owner's cache immediately).
- `record_404()` counts consecutive failures in cache.db — the count
  survives restarts by construction. Attempts 1-2 log normally ("attempt
  1/3"); at the threshold (3) the link is CONFIRMED dead, the log line
  says QUARANTINED, and `_inbox/notfound-links/notfound_links.md` gets
  its single permanent row (previously one duplicate row per run,
  forever).
- **The ProcessingWorker now skips confirmed-dead links BEFORE any
  GitHub API call** — this covers every input path at once (bot queue,
  channel fetch, imports, retries). Skipped links are counted silently
  and reported in ONE aggregate line in the batch summary instead of
  per-URL spam.
- Queue/verify filters use the confirmed-dead set only — unconfirmed
  entries (1-2 attempts) still surface as pending so they receive their
  remaining attempts.
- **Management**: GUI `More ▸ 🚫 View 404 Quarantine` lists every
  confirmed-dead link with attempts + date and offers a one-click reset
  (for false positives — a repo that went PRIVATE reads as 404 to an
  unauthorized token). CLI: `--list-dead` / `--reset-dead`.

### Added — VISUALIZED CLI companion (`cli.py` + `Start-GitCurator-CLI.bat`)
- Owner: "I want a CLI version — store the credentials in the project
  folder, start it via a .bat for easy clicking, and it automatically
  does the rest as long as credentials are okay. I want a visualized CLI
  — different colors, loading animations."
- **One-click launch**: `Start-GitCurator-CLI.bat` (double-click on
  Windows; `cli.sh` on Linux/macOS) finds Python, one-time-installs
  `rich`, and runs the pipeline. Credentials come from the SAME
  `config.json` the GUI uses.
- **The default flow needs zero flags**: colored ASCII banner → config
  summary panel (masked secrets) → spinner-driven pre-flight checks
  (GitHub token + rate limit, proxy socket, LLM provider) → bot-queue
  fetch through the same hardened subprocess worker (proxy pre-flight,
  DC rotation) → queue stats table → rich live progress bar with the
  current repo, M/N, elapsed → level-colored log lines → post-run
  (mark-read, VaultSeal, Good Repos) → summary table (processed /
  warnings / retry queue / quarantined total / elapsed).
- Shares the pipeline code with the GUI verbatim (ProcessingWorker,
  CacheDB, quarantine) — identical behavior by construction, including
  headless-mode safety (LLM failure → fallback note, Ctrl+C → graceful
  stop, single-instance lock so it refuses to run beside the GUI).
- Flags: `--check`, `--dry-run`, `--import-file F`, `--list-dead`,
  `--reset-dead`, `--skip-checks`, `--no-seal`, `--quiet`, `--config`,
  `--vault`, `--version`.
- Signal bridging: the worker's cross-thread Qt signals are delivered to
  the rich renderer through a main-thread QObject bridge (rich's Live
  display is not thread-safe — plain callables would have run the
  callbacks on the worker thread, exactly like `run_headless` could get
  away with only because `print()` is thread-safe).

### Verification
- 101/101 tests (92 existing + 9 new quarantine regression tests:
  schema, migration, cross-session counting, confirmed-only filtering,
  reset, legacy shape, threshold).
- End-to-end CLI run: 4 consecutive sessions against a real 404 repo —
  attempt 1/3 → 2/3 → 3/3 QUARANTINED → silently skipped with zero log
  spam and zero GitHub API calls; `notfound_links.md` gained exactly one
  row; summary table rendered each time.
- Offscreen smoke: 33/33 UI checks; window exactly 900×600; VLM visual
  review of light + dark screenshots: sun/moon/gear all fully rendered,
  layout balanced, no defects.
- `python -m py_compile` on all 17 audited modules (CI gate updated,
  which also finally fixes the `branches: ain]` trigger typo at its
  source — the v0.06 fix had not persisted).


## [0.07.3] — GUI 404-Strike Manager — 2026-09-18

Closes the v0.07 P1 follow-up: the deleted-repo threshold existed in
`config.json` / CLI (`--strikes N`) only — now it's a first-class citizen
of the GUI.

### Settings → Dashboard → "Deleted Repos — 404 Strike Counter"
- **Threshold spinbox** (2–10, default 3) — every change is saved to
  `config.json` immediately through the merge-safe `save_config` (unknown
  keys survive; `ProcessingWorker` reads the value per batch, so the next
  run picks it up without a restart). The initial `setValue` is
  signal-blocked so launching the app never rewrites `config.json`.
- **Live strike table** — every URL currently carrying 404 strikes
  (highest first, last-seen timestamp, ⛔ flag once the threshold is
  reached), capped at 30 rows with a pointer to `--status`. Refreshed by
  its own 🔄 button and rides along with **Refresh Dashboard**.
- **♻️ Reset Counters** — confirmation, then clears every strike row so
  restored/repos-that-came-back get re-checked on the next run instead of
  being auto-ignored.
- `refresh_strike_view` is best-effort: missing vault / cache.db shows a
  friendly empty-state message, never a dialog or crash.

### Tests & docs
- `tests/smoke_v007.py` extended 15 → **24 checks** (spin exists, range
  2–10, initial value matches config, group/text widgets, all three new
  methods, render-without-vault path).
- E2E verified offscreen: spin→`config.json` persistence, merge safety
  (probe key survives), strike table lists a threshold-reached URL with
  the ⛔ flag.
- `VERSION` → 0.07.3, `__VERSION__` stamp, README (GUI strike-manager
  section + changelog pointer).

## [0.07.2] — CLI Model Picker + Pre-flight Checks — 2026-09-18

Owner report: *"See it didn't let me choose a new model … Model wasn't found
= failed cli."* — with several Ollama models installed but the configured one
not pulled, the CLI logged *"The first LLM-failure dialog lets you pick one"*,
but that dialog only exists in the GUI: headless mode silently skipped every
repo's LLM analysis, so the whole batch degraded to placeholder notes.

### 1. Pre-flight checks (`gitcurator/cli.py`, all batch modes)
- **Run-configuration card** — boxed vault / bot-queue / proxy / LLM /
  masked-token snapshot before `--auto` runs (matches the GUI's settings
  summary).
- **GitHub token** — validated live (`authenticated as <login>`); a rejected
  token (401) aborts with a fix hint (interactive runs may override); no
  token warns about the 60 req/h unauthenticated limit.
- **Proxy** — TCP reachability + latency of the configured proxy (the Telethon
  fetch depends on it); unreachable → actionable warning.
- **Ollama + model** — the v0.08-lineage pre-flight showed a green ✓ while
  noting "(configured '…' not pulled)" and then failed the whole batch. Now:
  server down → warning (the pipeline's auto-start still gets its chance),
  no models → abort with `ollama pull` hint, **configured model missing →
  interactive numbered model menu BEFORE any fetching happens**:
  - smart recommendation first (same family + parameter size as the
    configured model, embedding models like `nomic-embed-text` flagged
    *"(embedding — cannot analyze)"* and never recommended),
  - pick by number or unique name substring; `Enter` takes the
    recommendation; `q` aborts with the `ollama pull` hint,
  - the choice is **saved to config.json** (GUI + CLI share it),
  - non-interactive runs (piped stdin / scheduled) auto-pick the
    recommendation and log the switch — a missing model can no longer
    fail a whole scheduled batch.

### 2. Pipeline headless model resolution (`gui/app.py`)
- **`model_prompt_callback`** — the CLI registers a console-menu callback on
  `ProcessingWorker`; the warmup-failure path (configured model missing,
  several installed) now asks the host instead of logging a promise no
  headless run could keep. The choice is applied to the whole batch
  (`_apply_model_choice`), persisted, and the stand-in is **re-warmed to
  verify it actually works** before the batch starts.
- **Auto-pick fallback** — headless without a console (or after declining):
  `_pick_best_model` chooses the closest stand-in (embedding models
  excluded → same family → same size → biggest) and logs the switch —
  never again does the batch continue with a known-missing model.
- **Mid-batch recovery** — `_wait_for_llm_decision` gained `err / client /
  model` context: a model-not-found failure mid-run (model deleted while
  the batch was running) triggers the same console menu / auto-pick via the
  existing retry plumbing; connection errors still skip with the
  "start the server" hint; after one decline the menu never nags again.
- GUI behavior is unchanged (the first LLM-failure dialog still offers the
  list and persists the choice batch-wide).

### 3. Fixes & polish
- **`--status`** now reports whether the configured model is actually
  installed (`N model(s) installed · 'X' ready` / warning + fix hint) and
  still degrades silently when the server is down.
- **`__config_path__` leak** — CLI model-switch saves wrote the private key
  into `config.json`; all saves now filter `__…__` keys.
- **Direct-launch `sys.path`** — `python gitcurator/cli.py …` computed the
  app root one directory too high (worked only via `main.py`); fixed.
- Verification: 110/110 unit tests, 15/15 GUI smoke checks, and a 4-scenario
  offline e2e (mock Ollama reproducing the owner's exact model list + fake
  PyGithub): non-interactive auto-pick, interactive menu pick honored,
  warmup-callback switch with re-warm verification, mid-batch
  decision matrix (choice / decline→auto-pick / decline-once / connection
  error). All batches completed 2/2 repos with the switched model and
  exit code 0.

## [0.07.1] — Connectivity Hotfix + Official Lucide Icons — 2026-09-18

The first v0.07 build failed to connect on the owner's machine for three
separate reasons (two app bugs, one environmental) — all three are fixed,
and the hand-drawn "Lucide-style" icons are replaced by the real thing.

### Connectivity — what the failure log actually said
- **Proxy disabled on the first queue check** (`build_proxy received:
  {'enabled': False}`): the Proxy checkbox state is the source of truth
  and had been toggled off on the machine — the queue check then spawned
  the worker SILENTLY on a direct connection (blocked in Iran) and burned
  four cryptic `ConnectionRefusedError [WinError 1225]` retries.
  `check_bot_queue` now logs a loud warning before the spawn, and the
  worker prints a proxy-disabled banner and pre-flights the proxy port
  (2s socket test) with an actionable error when v2rayN is down. The app
  NEVER silently falls back to a direct connection — in censored regions
  that both fails and leaks Telegram usage to the ISP.
- **GitHub header crash** (`Invalid … character(s) in header value:
  'token ghp_…\n'`): a token pasted with a trailing newline. The direct
  token test stripped its input, the combined Telegram+GitHub test did
  not — same field, six seconds apart, one passed and one crashed. Every
  token read strips now, `load_config` heals already-poisoned files, and
  `save_config` strips at the source so the newline can never persist.
- **`IncompleteReadError` was never retried** (`Server closed the
  connection: 0 bytes read on a total of 8 expected bytes`): Telethon's
  internal `connection_retries` only retries `ConnectionError` subclasses,
  so the classic DPI / exit-node reset aborted instantly with zero
  retries. The worker now owns the retry policy: 3 attempts, 3s/6s sleeps,
  clear per-attempt logs, alternate Telegram DCs on attempts 2-3 (fresh
  sessions only — auth keys are DC-bound), and a plain-language hint that
  a reset usually means the v2ray EXIT NODE is blocked (the remaining
  `IncompleteReadError` the owner saw at 02:18-02:19 was exactly this:
  their proxy exit node was being reset by Telegram/DPI — app-side
  retries now make it robust, changing the node fixes it outright).
- **Session guidance**: a fresh extract has no `session.session`, so
  every Telethon flow asked for a login code. The worker now prints a
  tip to copy `session.session` from the previous install instead of
  re-authenticating.
- Worker version bumped to 3.4 (visible in the log's first line —
  confirms the fixed worker is the one running).

### Icons — official Lucide pack, verbatim
- The v0.07 draft shipped hand-redrawn "Lucide-style" paths; the owner
  rejected self-drawn icons. `gitcurator/gui/icons.py` now embeds the
  REAL `lucide-static` v0.544.0 SVGs (ISC license, full text in the
  module) — layers, settings, moon/sun, refresh-cw, square (solid stop),
  play (solid), loader-circle, activity, search, trash-2, circle (status
  dot). Same tint-at-render machinery (QSvgRenderer, 2× HiDPI), same
  public API, graceful QtSvg-less fallback.

### Upgrade notes
- If Telegram still resets the connection with the proxy ON, the exit
  node is blocked — switch to a different v2ray/xray node (the app now
  says so in the log).
- Copy `session.session` from your previous app folder to skip the login
  code (the worker prints this tip too).

## [0.07] — Main-Screen Design Release — 2026-09-18 *(local lineage)*

## [0.07] — Visual CLI + 404 Strike Policy + 6:4 Window — 2026-09-18 *(sandbox lineage — same zip, other half of the story)*

Owner requests: *sun icon clipped in the theme toggle* · *window aspect
ratio 6:4 or 5:7* · *deleted repos: persist the 404 failure count locally
and auto-ignore after 2–3 misses* · *a visual CLI version with colored
output, spinners/progress bars, locally stored credentials, a double-click
.bat launcher, fully automatic*.

### 1. Visual CLI (`gitcurator/cli.py`, ~700 lines, zero new dependencies)
- **Colored output** — per-level colors + icons (`•` info, `✔` success,
  `▲` warning, `✖` error), dimmed timestamps, ASCII banner. Honors
  `NO_COLOR`, `--no-color`, and auto-disables on non-TTY pipes;
  colorama (already in requirements.txt) enables ANSI on legacy Windows
  consoles.
- **Spinner + progress bar** — a braille spinner (`⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏`)
  owns the terminal's bottom line; during batches it renders
  `⠹ Processing batch  ██████████░░░░░░░ 12/40 (30%) owner/repo`.
  Log lines print above it (clear → print → redraw), driven by a 100 ms
  QTimer so Ctrl+C stays responsive.
- **`--auto` — fully automatic run**: bot-queue SYNC (fetch UNDONE,
  vault-filtered) → PROCESS (same ProcessingWorker pipeline as the GUI)
  → VaultSeal git commit → Good Repos publish. Interactive Telegram
  login codes are prompted in-terminal; `>10`-repo confirmation is
  skippable with `--yes`.
- **`--init` — first-run wizard**: asks for Telegram/GitHub/LLM/proxy
  settings and saves them to the SAME `config.json` the GUI uses
  (credentials stay local; secrets are masked in every status view).
- **`--status`** — masked config summary + cache.db stats: processed,
  decommissioned, retry-queue size, and every URL currently on a 404
  strike with its count and last-seen time.
- **Manual modes** — `--import-file`, `--from-id/--to-id`,
  `--offset-start/--count`, `--single-id`, `--retry-failed` (reprocesses
  the whole retry queue), `--mark-read` (clears the bot's unread queue).
  All batch modes share the visual renderer and return proper exit
  codes (a failed batch exits 1 — the .bat shows the setup hint).
- **`--strikes N`** — per-run override of the 404 threshold.
- **Entry points** — `python main.py --cli …` (shim dispatches before the
  GUI import so `--status` never loads the widget stack),
  `python gitcurator/cli.py …`, `python -m gitcurator.cli …`.

### 2. Double-click launchers (Windows + Unix)
- `GitCurator-CLI.bat` — one double-click → the fully automatic run
  (`--cli --auto --yes`), with Python detection and exit-code hints.
- `GitCurator-CLI-Setup.bat` — one-time first-run wizard (`--cli --init`).
- `gitcurator-cli.sh` — Unix launcher (defaults to `--auto --yes`;
  forwards any flags).

### 3. 404 strike policy (deleted repos, persisted)
- New `notfound_strikes` table in `cache.db` (`url`, `strikes`,
  `first_seen`, `last_seen`); the counter is **persisted across
  sessions** — each batch run that sees a 404 for a URL increments it.
- A URL is only **decommissioned (auto-ignored)** after
  `notfound_strike_threshold` consecutive 404s (config key, default 3,
  clamped to ≥2). Sub-threshold misses are marked failed so the URL
  returns with the next SYNC / Retry-Failed run and gets another chance
  (protects against transient GitHub hiccups and false 404s).
- A successful fetch **resets** the counter (repo restored / rename
  reverted). Applies to both 404 paths (direct and post-rate-limit-wait).
- Decommission reasons now record the strike count
  (`404 Not Found (after 3 strikes)`), and the permanent
  `_inbox/notfound-links/notfound_links.md` record includes it too.

### 4. GUI fixes
- **Sun-icon clipping fixed** — the theme-toggle and settings icon
  buttons were 32×30 while the icon-button design spec (and the emoji
  glyph metrics at 16 px, which render ~20 px tall with padding on
  Windows display scaling) call for a 38×36 target. Both buttons are now
  38×36 — `☀️`/`🌙`/`⚙️` never clip at 100–150% DPI.
- **6:4 aspect-ratio window** — the main window is now 900×600 (was the
  ultra-compact 1000×375; 5:7 portrait was rejected because the toolbar
  and progress row are landscape-oriented). The +225 px of height flows
  entirely into the always-visible Progress Logs panel (its minimum grew
  64 → 180 px, ~4× the visible log lines); hero CTAs and the progress
  row keep their compact sizes.

### 5. Tests & CI
- New `tests/test_strikes.py` — 18 tests: strike persistence across DB
  reopens (the counter's whole point), URL normalization, clear-on-
  success, stats listing, `_handle_404` sub-threshold vs decommission
  paths, threshold clamping (min 2 / default 3 / junk → 3), notfound
  record contents, CLI parser/masking/bar/no-color behavior.
- Full suite: **110/110 green** (92 previous + 18 new; PyQt6-dependent
  suites still auto-skip cleanly without Qt).
- CI compile gate now includes `gitcurator/cli.py`; the suite runs
  `tests.test_strikes` too.
- CLI verified end-to-end headlessly: `--help`, no-arg friendly banner,
  `--status` (with live strike rows), and a full `--import-file` batch
  (spinner + progress rendering, inbox notes for non-GitHub links,
  manifest, VaultSeal git commit `5108f88`, Good Repos skip, exit code
  propagation).


A full design review of the main/status screen graded it **C overall**
(D+ design-system consistency): four icon languages on one screen, a log
panel indistinguishable from the background, a status counter that said
`- / -` while the real numbers sat in a log line, no active state on the
filter tabs, and a hue soup (pink STOP, mint CTA, green dot, blue text,
lavender everything else). v0.07 fixes every named finding.

### One icon system (was: pixel-art + emoji + outline + solid mixed)
- New `gitcurator/gui/icons.py` — a single flat-outline, Lucide-style
  SVG set (layers, settings, moon/sun, refresh, stop, play, loader,
  activity, search, trash, dot), tinted per theme at render time via
  `QSvgRenderer` (rendered at 2× for HiDPI). No new dependency — QtSvg
  ships with PyQt6, and the helpers degrade to text-only buttons if it
  is ever unavailable.
- Every main-screen glyph replaced: logo tile, settings, theme toggle,
  SYNC/PROCESS/FETCHING/STOP hero states, Test Connectivity, proxy
  status dot, log search (leading action), clear-log button.

### Palette collapsed to one accent + semantic states
- SYNC/PROCESS hero fill is now the **lavender anchor** (`#C4BCF5` +
  deep-plum text, 9.0:1) — the app's single interactive-chrome accent.
  The old mint CTA made the primary button read as a second, unrelated
  hue; mint/green is now reserved for success states only.
- STOP is a decisive red kill-switch (`#D63A24`, white text 4.7:1 AA;
  hover `#C43320` 5.5:1). It only renders while a batch runs, so the
  screen's loudest element is also its most urgent one.
- Progress-bar chunk is the accent (was mint — a success-state color
  doing chrome work). Semantic dot/label colors for proxy health.

### The status readout says something real
- The `– / –` counter is now labeled **PROCESSED x / y** and is
  truthful at all times: live counts during a batch, `0 / n` for a
  fetched queue, and the last batch manifest's real totals while idle
  (`_refresh_pipeline_counter()`, wired into init, vault change, SYNC
  fetch, batch finish and progress-bar reset). Tooltips explain the
  numbers. Nielsen "Visibility of System Status" restored.

### Figure-ground for the log panel
- Dark theme: the log well drops to a recessed `#17131F` (window
  `#221E2E` → card `#2B2639` → well `#17131F` is now a visible depth
  hierarchy; was `#241F31` ≈ 1.09:1 against the card). Log timestamps
  re-tinted per theme (were fixed `#666`, ~2.4:1 on the new well).

### Filter tabs with a legible active state
- All/Errors/Warnings/Success became a segmented control: checked tab
  gets a filled accent background (lavender/plum text dark, violet/white
  light), hover states, focus rings. Was: four identical gray buttons
  with zero state differentiation.

### Whitespace redistributed, hierarchy corrected
- SYNC and Test Connectivity share ONE horizontal row (SYNC grows) —
  the stacked layout's wasted vertical space now belongs to the log
  panel (min-height 72), so the empty state no longer reads as a void.
- One 10px spacing rhythm between the four bands (top bar / CTA card /
  pipeline strip / log) instead of uneven 8px gaps.
- The counter, bar and proxy health sit in ONE connected strip — the
  review noted the counter and "Connected" status were "two halves of
  the same story at opposite screen edges."

### Accessibility
- Placeholder text lifted to AA: `#A6A2AC` on plum (5.8:1, was 4.30:1)
  and `#7A7288` on white (4.6:1) via the `QPalette.PlaceholderText`
  role (guarded for Qt < 6.5).
- Accessible names on every icon-only control (settings, theme toggle,
  clear log, proxy dot + label, search box).
- Pointing-hand cursors on the filter tabs and clear button.

### Verified
- 92/92 tests green (incl. the 3 GUI worker-guarantee tests), 33/33
  offscreen smoke checks of the new screen, and an independent vision
  review of rendered light + dark screenshots: 14/14 checklist PASS.

## [0.06] — Reliability & Performance Release — 2026-09-17

Owner-reported symptom: *"it's buggy and doesn't work — it just says
another project is running, but does nothing"* with the log showing
`⏳ Another Telegram operation is already running` on every button from
19:49 to 19:52 with no operation ever finishing.

### Root causes (both reproduced, both fixed)

**1. The Telegram busy-lock could stick at "held" forever.**
- The subprocess runner read the telethon child's stderr with an
  UNBOUNDED blocking loop; its `proc.wait(timeout=300)` only ran after
  stderr closed, i.e. never for a stalled child (dead SOCKS proxy,
  session-file contention). The startup auto bot-check acquired the
  lock, hung there forever, and every subsequent Telegram operation was
  denied — the exact log the owner captured.
- 15 early-return paths (missing credentials / empty fields) exited
  AFTER acquiring the lock without releasing it.
- Two signal-ordering self-deadlocks guaranteed denials: the keyword-mode
  finisher started the batch while its own worker still held the lock
  (100% reproducible), and Verify All's finisher called
  `clear_bot_queue()` the same way.
- `ProcessingWorker.run()` had no top-level try/except: any crash in the
  ~850-line pipeline killed the thread silently — no `finished_signal`,
  no `processing_finished`, lock stuck for telegram batches.
- `processing_finished` released the lock unconditionally, even for
  direct/import batches that never acquired it (could free a live
  worker's lock → two telethon children on one session).
- closeEvent never killed telethon children; an orphan held
  `session.session`'s SQLite, making the NEXT launch's auto-check stall.

**2. A zombie process made the NEXT launch print "Another instance is
already running" and exit.** The startup auto-check opens modal dialogs
2s after launch; if the main window was closed in those 2s (or a hidden
parented dialog outlived it), Qt never emitted `lastWindowClosed`,
`app.exec()` never returned, the `finally` in `main()` never deleted
`app.lock` — the lingering process then blocked every future launch.

### Fixes
- **New module `gitcurator/gui/telegram_lock.py`** — `TelegramLockManager`
  with owner tracking, age reporting, owner-scoped release, force-release
  and a context manager. The busy message now names the holder and its
  age instead of a dead-end "please wait".
- **New module `gitcurator/integrations/subprocess_runner.py`** — a
  runner with a REAL timeout: idle-based kill (no child output for 180s
  default; progress lines keep healthy fetches alive), a 30-min hard
  cap, generous auth-grace while the user types a login code / 2FA
  password, and a process registry so closeEvent kills every orphan.
  Ships a 4-scenario self-test (`python -m
  gitcurator.integrations.subprocess_runner`).
- All 15 leak paths release on early return; every acquire site names
  its owner; `_keep_worker` releases only if that worker still holds the
  lock; the two self-deadlock finishers defer one event-loop tick
  (`QTimer.singleShot(0, …)`); `TestWorker.run` catches BaseException
  and always emits `finished_signal`; `ProcessingWorker.run` is wrapped
  so the batch ALWAYS emits `finished_signal` exactly once.
- **Watchdog** — a 60s QTimer force-releases any lock held > 35 min
  (longer than the runner's hard cap) with a loud log line.
- **Zombie fix** — `_closing` flag + guards in every modal helper and
  the startup auto-check (dialogs are logged, not shown, during
  shutdown); closeEvent stops the timers and ends with an explicit
  `QApplication.quit()` so `app.exec()` always returns and `app.lock` is
  always removed.
- `telegram_fetch_worker.py`: Telethon `timeout=45`, bounded `connect()`
  (90s), retry caps, and a progress heartbeat every 500 fetched messages
  so large healthy fetches are never mistaken for stalls.
- Startup lock-reset removed (fresh window = fresh lock; the old blind
  reset could wipe a legitimately-acquired lock from the first 2s).

### Performance
- O(n²) link dedup → set-backed in all three extraction blocks
  (telegram_fetch_worker ×2, message-range fetch; a 5,000-link bot chat
  went from ~25M list comparisons to hash lookups).
- SQLite: WAL journal + `synchronous=NORMAL` + indexes on the hot
  lookup columns (`processed_repos.url/.note_path`, `failed_repos.url`);
  verify-all now opens ONE CacheDB connection instead of two.
- `check_bot_queue` vault filtering (VaultIndex rebuild — a full walk of
  every note — plus decommissioned classification) moved from the GUI
  thread into the background worker; the GUI used to freeze 0.5–5s on
  every queue check including the startup auto-check.
- `LinkTracker.mark_processing` no longer rewrites the entire manifest
  JSON per link (~1,000 full-manifest writes per 500-link batch → 0;
  crash recovery unaffected — reconciliation already retries "pending").
- Telethon import deferred to the headless `--single-id` path
  (~0.5–1s faster GUI cold start; the asyncio stack no longer loads in
  the GUI process).

### Modularity & hygiene
- New testable modules (telegram_lock, subprocess_runner) with the bug
  context documented at the top of each.
- Dead files moved to `app/_attic/` (4 screenshot scripts, superseded
  smoke script, 3 byte-identical prompt duplicates; `app/prompts/`
  keeps the canonical copies).
- `.gitignore` now excludes the live credential stores (config.json,
  both installer.config.json) — an accidental `git add .` can no longer
  commit five live tokens. New `app/config.example.json` template with
  every secret masked.

### Verification
- 73/73 existing unit + e2e + goodrepos tests green.
- Subprocess runner self-test: healthy worker / hung-worker kill /
  interactive-auth grace / hard cap — all pass.
- Offscreen GUI smoke: module import, MainWindow construction, lock
  semantics (double-acquire denial, wrong-owner release protection,
  watchdog force-release), TestWorker SystemExit → finished_signal,
  clean event-loop exit.
- **End-to-end forever-bug repro** through the real
  `MainWindow.check_bot_queue()` path: (A) fast-fail child releases the
  lock and a second Telegram operation is admitted; (B) a HUNG child
  (simulated dead proxy, 600s sleep) is killed after 6.1s by the idle
  timeout and the lock auto-releases; (C) `app.exec()` returns after
  close — no zombie, no stale app.lock.

### Standing security note (unchanged P0)
Rotate every credential that has appeared in chat/logs (Telegram bot
token + api_id/api_hash, Cloudflare tokens, GitHub PAT) and update them
in the app and on the worker. Git history predating v0.0.10 still holds
old blobs.

## [0.05] — Ollama Auto-Start Release — 2026-09-17

Owner-reported fix: "a problem that prevents processing… tried with
different LLMs, but it fails to actually digest and process the new
link." The log showed the classic wall:
`HTTPConnectionPool(localhost:11434) … Connection refused` → retries →
`❌ LLM failed for <repo>` five minutes later.

### Root cause
**The Ollama server was not running** (connection refused = nothing is
listening on `localhost:11434`). Switching models can never fix a dead
server — every model lives on the same server — yet the old failure UI
presented a model-picker, sending the user down exactly that dead end.

### What v0.05 does about it
- **Auto-start at batch start** — the pre-flight check in `run()` now
  spawns `ollama serve` detached (same flags as the 🚀 Start Server
  button: survives the app on Windows/Unix), polls until the server
  answers (16 probes × 1.5s), and continues the batch seamlessly.
- **Clear failure trail** — if Ollama isn't installed / never comes up,
  the log says exactly that (install URL, the Start Server button, the
  `ollama serve` command) instead of a raw traceback.
- **Per-repo diagnosis** — mid-batch LLM failures are now inspected: a
  connection-refused family error (incl. the urllib3/requests wording
  from the owner's log) logs "💡 That is a CONNECTION error… choosing a
  different model will NOT fix it" BEFORE the model-picker dialog.
- **Dialog warning** — the "LLM Analysis Failed" dialog now shows a
  highlighted warning box when the server is unreachable, instead of a
  silent empty model dropdown.
- New helpers: `ProcessingWorker._autostart_ollama()` (thread-safe,
  GUI only via the log signal) and `_looks_like_connection_error()`
  (string+exception-type detection; `TimeoutError` explicitly excluded —
  it is an `OSError` subclass in Python 3 and must NOT read as "down").

### Verification
- 85/85 offscreen GUI smoke checks (+7 new: connection-error detection
  incl. the owner's exact urllib3 string, timeout non-detection, and the
  auto-start not-installed branch).
- 73/73 backend tests green.

## [0.04] — Credentials Release — config pre-filled — 2026-09-17

Owner-reported fix: "I still get the same error" — the v0.03 zip shipped a
credential-free `config.json` (v0.0.10's sanitization), so on a fresh unzip
SYNC bailed with **"❌ Telegram credentials required."** (`api_id` 0,
`api_hash`/`phone` empty). Per the owner's explicit request this release
**pre-fills the credentials into the app config**.

### What's pre-filled now
- `app/config.json`:
  - `telegram_api_id` / `telegram_api_hash` — the owner's Telegram API pair.
  - `bot_token` (@githubfetcherbot) + `bot_username`.
  - `github_token` — the owner's PAT (repo fetching + VaultSeal/GoodRepos pushes).
  - `cloudflare_worker_url` — the LIVE worker
    (`https://github-to-obsidian-bot.aliassadi-plus.workers.dev`, verified
    via `/health` before shipping).
  - Reserved keys (kept alive by the v30 merge-save, no consumer yet):
    `cloudflare_api_email`, `cloudflare_api_token`, `cloudflare_account_id`,
    `cloudflare_workers_ai_token`, `telegram_user_id`.
- `app/installer.config.json` + `app/cloudflare-bot/installer.config.json`:
  `bot_token`, `github_pat`, `allowed_user_ids` (= the sole bot-chat user
  92788333, recovered from the worker's D1 ledger), and a freshly generated
  random `hmac_secret` — `node install.js` now validates out of the box.

### The one field still empty
- `telegram_phone` — the owner's own phone number was not in the provided
  credential list. Every Telegram fetch (SYNC included) needs it once:
  **Settings → Credentials → Phone** (`+98…` international format). The app
  saves it on first SYNC and never asks again (the Telethon session survives
  restarts).

### Note
- The Cloudflare Workers AI token is stored but not yet consumed by any
  code path — it's parked as a reserved key for future use.
- Zip note: this release intentionally ships real credentials inside
  `config.json`/`installer.config.json` (private repo + owner's explicit
  request). The v0.0.10 "credential-free tree" guarantee is lifted for
  this release line.

## [0.03] — GUI Redesign Release — two-stage SYNC — 2026-09-17

Owner-reported fix: clicking SYNC used to raise the "Nothing to Process"
error box. SYNC now runs the fetch itself, per the requested flow:
**click SYNC → it fetches all undone items in the Telegram bot → the button
turns into PROCESS → click PROCESS → it starts.**

### The new hero-button flow (GUI-only, zero pipeline changes)
- **SYNC** — fetches every undone item from the Telegram bot (the bot-queue
  check: already-in-vault and decommissioned repos are skipped), logging the
  queue summary; the button reads `⏳ FETCHING…` while it runs.
- **PROCESS (N)** — after the fetch, the button shows the pending count and
  the progress bar reads `N ready to process`; clicking starts the batch
  through the existing confirm-gated path.
- **STOP** — while the batch runs (unchanged mirror behavior), then back to
  SYNC when it finishes.
- Nothing fetched (0 pending) with an Input mode selected → PROCESS runs
  that mode (Markers/Single/Range/Import keep their launcher); 0 pending and
  no mode → "✅ All caught up".
- Bot not configured → SYNC falls back to the legacy input-mode path, or
  shows a friendly setup hint instead of the old error.
- `check_bot_queue()` gained an optional `on_done(name, result)` completion
  callback + `bool` return for early bails (backward compatible).

### Fixes
- **Progress-bar text never rendered while idle** — a fresh QProgressBar
  holds `value = -1` (unset, out of range), so the QSS-styled bar drew no
  text until the first batch ran; the idle "Ready" (and the new "N ready to
  process") label was invisible. Pinned to `setValue(0)` at construction.
- "Nothing to Process" / SYNC tooltips re-worded for the new flow.

### Verification
- 78/78 offscreen GUI smoke checks (20 new for the two-stage state machine:
  fetching render, PROCESS (N), count refresh, 0-pending/error fallbacks,
  PROCESS routing, busy-lock bail, full SYNC→PROCESS→STOP→SYNC cycle).
- 73/73 backend tests green. `VERSION` → `0.03`; `gui/app.py` stamp → `0.03`.

## [0.02] — GUI Redesign Release — compact pass — 2026-09-17

New owner-requested release-zip line for the GUI redesign deliveries
(`gitcurator-v0.02.zip`; numbering v0.01, v0.02, … — each new delivery
bumps the version). Not related to the deleted early session zips that
happened to use v0.01–v0.09 names. Built with `git archive` from the
`gui-redesign-v33` branch: tracked tree only — no `.git`, no runtime
caches, no sessions.

### GUI redesign (GUI-only, zero business-logic changes)
- **v0.01 — wireframe redesign (first delivery):** minimal main view —
  logo + SYNC CTA + Test Connectivity + progress + logs — with every one
  of the 9 tabs moved into a Settings window (sidebar master-detail).
- **v0.02 — compact pass (this release):**
  - Main window halved to 1000×375; log panel height halved.
  - Settings > Vault: the giant gap is gone — pages are top-aligned at
    natural height inside the scroll area.
  - Settings > LLM: everything fits the viewport — combo min-width caps,
    🔄 Refresh + 🚀 Start Server share the Model row.
  - Verification: 73/73 backend tests + 60/60 offscreen GUI checks green.
- `VERSION` → `0.02`; `gui/app.py` version stamp → `0.02`.

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
