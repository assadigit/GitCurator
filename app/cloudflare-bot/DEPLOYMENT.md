# Deployment Guide — From Scratch

Complete step-by-step guide to deploy the Cloudflare Worker + Dashboard.
Estimated time: 30-45 minutes.

---

## 📦 v0.52.0 deploy record — re-deploy of the unchanged 0.30.0 worker (DEPLOYED ✅)

> **Deployed to production on 2026-10-09** (worker version ID
> `9639f0fb-b5c4-4640-a801-ce14d78cdda5`, URL
> `https://github-to-obsidian-bot.aliassadi-plus.workers.dev`).
> v0.52.0 is a DESKTOP-ONLY release (the icon column speaks; the hand
> is Chrome's own: the master table's verdict emojis are read from the
> # cell OR the Status cell — a four-column table with 🖐/💀 in the
> first cell is no longer invisible — and a 🖐/✋ hand row is now
> scraped by the app itself in the owner's real Chrome, automatically
> at end-of-run and in the caught-up sync, no Ctrl+S demanded);
> the Worker's code is unchanged since v0.30.0 (it never touches the
> vault), so the deploy is the release ritual's health-and-parity
> check, not a code change.
>
> **Deploy ritual** (~2 minutes): `wrangler whoami` → the owner's
> account resolved with the `CLOUDFLARE_API_TOKEN` secret ✓ →
> `npx wrangler deploy` → version `9639f0fb…`, both queue consumers +
> producers attached (curator-ingest, curator-ingest-dlq), D1 (DB:
> curator-bot) and KV (CACHE: 4a0ad411…) bindings carried over
> untouched. The 0.30.0 worker suite (node --test, 50/50) was green
> on this tree at the v0.48/v0.51 deploys — the code is byte-identical
> (nothing under `src/` changed since).
>
> **Verified live after the deploy**: `/health` →
> `{"status":"ok","service":"github-curator-bot","version":"0.30.0",...}`
> ✓ · root → `200` ✓ · both queue consumers attached ✓ · secrets
> persisted (BOT_TOKEN, GITHUB_PAT, ALLOWED_USER_IDS, HMAC_SECRET —
> a deploy never touches them) ✓. Desktop release v0.52.0
> (GitCurator-v0.52.0-windows.zip, 129 files, 1,018,394 bytes, sha256
> `e320f4d0…c5624a9e`, download round-trip byte-identical) + CI green
> on main AND the v0.52.0 tag (runs 37682137978 / 37682137742 —
> 1437 tests, 85-module compile gate, offline golden 30/30).

---

## 📦 v0.51.0 deploy record — re-deploy of the unchanged 0.30.0 worker (DEPLOYED ✅)

> **Deployed to production on 2026-10-09** (worker version ID
> `20a2a662-af9c-44be-b7a4-7be2b82b25fb`, URL
> `https://github-to-obsidian-bot.aliassadi-plus.workers.dev`).
> v0.51.0 is a DESKTOP-ONLY release (the caught-up check reads the
> table: before "everything is up to date" is said, the master table's
> " - " rows — valid links whose fetches failed, no verdict emoji —
> are found and fetched again through the full pipeline, burned-out
> retry counters reborn one row at a time, succeeded rows stamped
> '📁 stored' so the table never lies "waiting" about stored links);
> the Worker's code is unchanged since v0.30.0 (it never touches the
> vault), so the deploy is the release ritual's health-and-parity
> re-verification: health `ok` with version 0.30.0, root 200, both
> queue consumers attached (curator-ingest + curator-ingest-dlq),
> secrets persisted. The deploy ran from the v0.51.0 release tree
> (`3ea3205`) right after the push and BEFORE the desktop tag's zip
> went out.

---

## 📦 v0.50.0 deploy record — re-deploy of the unchanged 0.30.0 worker (DEPLOYED ✅)

> **Deployed to production on 2026-10-09** (worker version ID
> `5ad435f7-9c3e-40e3-8f08-f4e803e8b335`, URL
> `https://github-to-obsidian-bot.aliassadi-plus.workers.dev`).
> v0.50.0 is a DESKTOP-ONLY release (the fifth door: when a run ends
> with links that generated no content, the end-of-run modal offers
> the retry — one yes and the app itself opens the owner's own
> Google Chrome in a fresh throwaway session, starts one tab per
> failed link, and takes the content from the live DOM over a
> raw-socket DevTools WebSocket — pure stdlib, no new dependency);
> the Worker's code is unchanged since v0.30.0 (it never fetches
> websites), so the deploy is the release ritual's
> health-and-parity re-verification: health `ok` with version
> 0.30.0, root 200, both queue consumers attached (curator-ingest +
> curator-ingest-dlq), secrets persisted. The deploy ran from the
> v0.50.0 release tree (`563e98c`) right after the push and BEFORE
> the desktop tag's zip went out.

---

## ⚡ Quick path — update an EXISTING deployment (2 minutes)

Already deployed before? Your D1 database, KV namespace, Queues, R2 bucket
and **all secrets persist across deploys** — updating to the latest version
only ships the new worker code:

```bash
cd cloudflare-bot
npm install                 # one-time, installs wrangler locally
npx wrangler login          # one-time, opens your browser
bash deploy-latest.sh       # schema (idempotent) + deploy + health check
```

On Windows, run `.\deploy-latest.ps1` instead (same steps, PowerShell).
Flags: `--with-secret` / `-WithSecret` also sets the **WEBHOOK_SECRET**
anti-impersonation hardening (v30+); `--skip-schema` / `-SkipSchema`
skips the (safe, `IF NOT EXISTS`) schema re-apply.

Prefer manual? The script is just these three commands:

```bash
npx wrangler d1 execute curator-bot --remote --file=schema.sql   # idempotent
npx wrangler deploy
npx wrangler tail                                                 # watch it live
```

After deploying, verify: `curl https://<your-worker>.workers.dev/`
should answer `{"status":"ok", ...}`.

First time here instead? Follow the from-scratch guide below.

---

## 📦 v0.48.0 deploy record — re-deploy of the unchanged 0.30.0 worker (DEPLOYED ✅)

> **Deployed to production on 2026-10-09** (worker version ID
> `aba752b4-a7a2-4947-abe5-507efb4c25f9`, URL
> `https://github-to-obsidian-bot.aliassadi-plus.workers.dev`).
> v0.48.0 is a DESKTOP-ONLY release (the fourth door: walled links
> can be opened in the owner's REAL Chrome and the saved pages are
> consumed as real fetches by the next batch — the strictest bot
> defenses answer exactly what the three machine doors are not: a
> real Chrome driven by the owner's own hand); the Worker's code is
> unchanged since v0.30.0 (it never fetches websites), so the deploy
> is the release ritual's health-and-parity re-verification: schema
> re-applied idempotently (10 tables, no writes), health `ok` with
> version 0.30.0, root 200, both queue consumers attached
> (curator-ingest + curator-ingest-dlq), secrets persisted. The
> deploy ran from the v0.48.0 release tree (`0fba81c`) right after
> the push and BEFORE the desktop tag's zip went out.

---

## 📦 v0.40.0 deploy record — the 0.30.0 worker, cutover retired (DEPLOYED ✅)

> **Deployed to production on 2026-10-06** (worker version ID
> `6d75921e-6e41-4c46-b739-9a409b67a6fe`, URL
> `https://github-to-obsidian-bot.aliassadi-plus.workers.dev`).
> The deploy ran from the v0.40.0 release tree (`6add0f6`, CI green —
> 83 compiles · 1030 tests · golden 30/30 · worker 50/50) minutes
> after the push, BEFORE the desktop tag went out, so a freshly
> downloaded v0.40.0 app finds the live worker already at 0.30.0
> (the desktop's EXPECTED_WORKER_VERSION moved in lockstep).
>
> **What ships in 0.30.0 — the cutover_complete flag is RETIRED.**
> Seeded as '0' by the v0.01 schema and NEVER set to '1' by anything
> (the intended desktop setter lives only in an example snippet;
> the pre-deploy D1 backup proves it: the row still read
> `cutover_complete='0'`, untouched since 2026-07-07), while the
> desktop still fetches its links via Telethon — the "cutover" was
> an abandoned V0.01 plan whose dashboard tile said "Cutover:
> Pending" forever. Removed from `/health`, from `/api/pending`,
> from the dashboard Settings card, from the state-key list, and
> from the schema seed; two worker tests (50-case suite) guard the
> retirement so the flag cannot quietly reappear in an API response.
> The D1 row itself (if present on an existing deployment) is simply
> ignored — the change is purely subtractive and idempotent.
>
> **Deploy ritual** (as executed, ~3 minutes): D1 export backup FIRST
> → `/home/z/backups/d1-backup-20261006-pre-030-deploy.sql`
> (2,293 INSERTs, 1,128,765 bytes; ledger 1,158 · activity_log 1,113 ·
> dead_letters 9 · sync_state 9 · dashboard_sessions 2), then the
> idempotent `schema.sql` re-apply (10 tables, no-op), then
> `wrangler deploy` → version `6d75921e…`, both queue consumers +
> producers attached (curator-ingest, curator-ingest-dlq). Pre-flight:
> the 50/50 worker suite (node --test) on the release tree.
>
> **Verified live after the deploy**: `/health` →
> `{"status":"ok","version":"0.30.0",...}` with `cutover_complete`
> ABSENT from the payload (one edge-propagation beat, ~20 s) ✓ ·
> Telegram `getWebhookInfo` → url = the worker's `/webhook`,
> `pending_update_count` 0 ✓ · all 4 secrets persisted (BOT_TOKEN,
> GITHUB_PAT, ALLOWED_USER_IDS, HMAC_SECRET) ✓ · D1 parity after the
> deploy IDENTICAL to the pre-deploy backup (ledger 1,158 ·
> activity 1,113 · dead 9 · state 9 · sessions 2 — the untouched
> `cutover_complete` row still sits in sync_state, ignored) ✓.

---

## 📦 v0.37.0 deploy record — the 0.29.0 worker, batch pastes (DEPLOYED ✅)

> **Deployed to production on 2026-10-05** (worker version ID
> `09079fd8-3063-4e81-81e9-b58f4363bf03`, URL
> `https://github-to-obsidian-bot.aliassadi-plus.workers.dev`).
> The deploy ran from the v0.39.0 tree (`0c5c6dd`, CI green) after the
> owner re-supplied the Cloudflare API token — the same 0.29.0 bundle
> that was prepared and dry-run-verified at v0.37.0.
>
> **What ships in 0.29.0** (a REAL worker change this time):
> - `extractUrls` captures a line that is exactly ONE bare address
>   (`coolors.co`, `www.site.org/path`, `127.0.0.1:8901/site`) and
>   prepends `https://` — a pasted batch of 50 websites without
>   schemes used to extract to NOTHING.
> - The multi-link reply leads with **"N new links received"**
>   (the owner's exact ask: "say 50 new links received, despite being
>   only 1 message"); a partially-new batch says "Received N links —
>   M new".
> - The no-link hint says you can paste as many links as you like in
>   one message.
>
> **Deploy ritual** (as executed, 2 minutes): D1 export backup FIRST →
> `/home/z/backups/d1-backup-20261005-pre-029-deploy.sql` (2,293 INSERTs,
> 1,128,765 bytes; ledger 1,158 · activity_log 1,113 · dead_letters 9 ·
> vault_mirror 0 · sync_state 9 · dashboard_sessions 2), then the
> idempotent `schema.sql` re-apply (10 tables, no-op), then
> `wrangler deploy`. Pre-flight: worker suite 48/48 + `--dry-run` PASS.
>
> **Verified live after the deploy**: `/health` →
> `{"status":"ok","version":"0.29.0",...}` ✓ · Telegram
> `getWebhookInfo` → url = the worker's `/webhook`, `pending_update_count`
> 0, no `last_error` ✓ · producers + consumers registered for BOTH
> `curator-ingest` and `curator-ingest-dlq` ✓ · all 4 secrets persisted
> (BOT_TOKEN, GITHUB_PAT, ALLOWED_USER_IDS, HMAC_SECRET) ✓ · D1 parity
> after the deploy IDENTICAL to the pre-deploy backup (ledger 1,158 ·
> activity 1,113 · dead 9 · mirror 0) ✓.
>
> The DESKTOP app has expected Worker 0.29.0 since v0.37.0 — its
> Test Connection → Bot Worker older-bot warning is now resolved
> (bot and expectation in lockstep at 0.29.0).

---

## 📦 v0.36.0 deploy record — re-deploy of the unchanged 0.28.0 worker (DEPLOYED ✅)

> **Deployed to production on 2026-10-05** (worker version ID
> `a6511ec7-965f-4471-8a83-dc5114b9d92a`, URL
> `https://github-to-obsidian-bot.aliassadi-plus.workers.dev`).
> v0.36.0 is a DESKTOP-ONLY release (the mirror resync: a warned
> Settings → Backup → "Resync Mirror" button + a `--resync` CLI twin
> that make the GitHub backup repos follow the vaults exactly —
> deletions reach the mirror, no union-merge resurrection, main never
> force-pushed); the Worker's code is unchanged since v0.28.0, so the
> deploy is the release ritual's health-and-parity re-verification,
> not a code change.
>
> - Pre-deploy D1 backup: `d1-backup-20261005-pre-v036-deploy.sql`
>   (744 INSERTs — 386 ever_seen_ledger · 343 activity_log ·
>   9 sync_state · 2 dashboard_sessions · 2 dead_letters ·
>   2 sqlite_sequence).
> - `npx wrangler deploy` → version `a6511ec7…`, both queue consumers
>   + producers attached (curator-ingest, curator-ingest-dlq).
> - `/health` → `{"status":"ok","version":"0.28.0"}` ✅
> - Secrets: 4/4 (ALLOWED_USER_IDS, BOT_TOKEN, GITHUB_PAT,
>   HMAC_SECRET) ✅
> - D1 parity vs the backup: every table count identical
>   (386/343/9/2/2, all others 0) ✅

---

## 📦 v0.35.0 deploy record — re-deploy of the unchanged 0.28.0 worker (DEPLOYED ✅)

> **Deployed to production on 2026-10-05** (worker version ID
> `d9fa8a30-8dae-42ee-ae25-5e150f404bed`, URL
> `https://github-to-obsidian-bot.aliassadi-plus.workers.dev`).
> v0.35.0 is a DESKTOP-ONLY release (the domain law's second reading:
> YouTube, Google share/drive/docs and the social-media majors are
> banned outright — banned links are never fetched, never noted and
> never collected in the _inbox review-queue tables; the tables are
> pruned of legacy banned/stored rows; the banned platforms' tables
> are quarantined; both pipelines' success logs now show the
> destination "[Vault Name] Item X processed and stored → path"); the
> Worker's code is unchanged since v0.28.0, so the deploy is the
> release ritual's health-and-parity re-verification, not a code
> change.
>
> - Pre-deploy D1 backup: `d1-backup-20261005-pre-v035-deploy.sql`
>   (744 INSERTs — 386 ever_seen_ledger · 343 activity_log ·
>   9 sync_state · 2 dashboard_sessions · 2 dead_letters ·
>   2 sqlite_sequence).
> - `npx wrangler deploy` → version `d9fa8a30…`, both queue consumers
>   + producers attached (curator-ingest, curator-ingest-dlq).
> - `/health` → `{"status":"ok","version":"0.28.0"}` ✅
> - Secrets: 4/4 (ALLOWED_USER_IDS, BOT_TOKEN, GITHUB_PAT,
>   HMAC_SECRET) ✅
> - D1 parity vs the backup: every table count identical
>   (386/343/9/2/2, all others 0) ✅

---

## 📦 v0.34.0 deploy record — re-deploy of the unchanged 0.28.0 worker (DEPLOYED ✅)

> **Deployed to production on 2026-10-03** (worker version ID
> `dc5345b5-cb54-4ba0-b682-a413a75190d5`, URL
> `https://github-to-obsidian-bot.aliassadi-plus.workers.dev`).
> v0.34.0 is a DESKTOP-ONLY release (the follow-up review — the
> segmented green/amber result bar, the balanced two-thirds CTA band,
> real proxy words "off/connected/unreachable" with the launch-focus
> fix, the greyed-out empty-log toolbar with the list glyph, and the
> Last-sync line); the Worker's code is unchanged since v0.28.0, so
> this deploy re-ships the exact same 0.28.0 bundle per the house
> pattern. Verified after the deploy: `/health` answers
> `"version":"0.28.0"` with `"status":"ok"`, both queue consumers
> registered (ingest **and** DLQ, plus both producers), all four
> secrets persisted (BOT_TOKEN, GITHUB_PAT, ALLOWED_USER_IDS,
> HMAC_SECRET), D1 row counts identical before/after (385 ledger /
> 2 dead / 342 activity / 0 mirror). The D1 export taken just before
> the deploy:
> `/home/z/backups/d1-backup-20261003-pre-v034-deploy.sql` (742
> INSERTs — byte-count identical to the pre-v0.33 backup;
> belt-and-braces).

## 📦 v0.33.0 deploy record — re-deploy of the unchanged 0.28.0 worker (DEPLOYED ✅)

> **Deployed to production on 2026-10-03** (worker version ID
> `502abe39-1e8e-4e77-bf0a-c086a4f8a104`, URL
> `https://github-to-obsidian-bot.aliassadi-plus.workers.dev`).
> v0.33.0 is a DESKTOP-ONLY release (the idle dedup — the header's
> proxy health label drops the state word at rest, reading
> "Proxy —" instead of "Proxy: Idle", so "Idle" is said once; plus
> the WCAG 2.5.3 Label-in-Name cleanup on the same label); the
> Worker's code is unchanged since v0.28.0, so this deploy re-ships
> the exact same 0.28.0 bundle per the house pattern. Verified after
> the deploy: `/health` answers `"version":"0.28.0"` with
> `"status":"ok"`, both queue consumers registered (ingest **and**
> DLQ, plus both producers), all four secrets persisted (BOT_TOKEN,
> GITHUB_PAT, ALLOWED_USER_IDS, HMAC_SECRET), D1 row counts identical
> before/after (385 ledger / 2 dead / 342 activity / 0 mirror). The
> D1 export taken just before the deploy:
> `/home/z/backups/d1-backup-20261003-pre-v033-deploy.sql` (742
> INSERTs — byte-count identical to the pre-v0.32 backup; belt-and-braces).

## 📦 v0.32.0 deploy record — re-deploy of the unchanged 0.28.0 worker (DEPLOYED ✅)

> **Deployed to production on 2026-10-03** (worker version ID
> `867f5b05-0220-4611-9454-98bd20ee4458`, URL
> `https://github-to-obsidian-bot.aliassadi-plus.workers.dev`).
> v0.32.0 is a DESKTOP-ONLY release (the five-change pass — shorter
> window with a remembered size, the SYNC⇄Stop single-button toggle,
> the flat secondary log panel, and the live-count retry banner); the
> Worker's code is unchanged since v0.28.0, so this deploy re-ships
> the exact same 0.28.0 bundle per the house pattern. Verified after
> the deploy: `/health` answers `"version":"0.28.0"` with
> `"status":"ok"`, both queue consumers registered (ingest **and**
> DLQ, plus both producers), all four secrets persisted (BOT_TOKEN,
> GITHUB_PAT, ALLOWED_USER_IDS, HMAC_SECRET), D1 row counts identical
> before/after (385 ledger / 2 dead / 342 activity / 0 mirror). The
> D1 export taken just before the deploy:
> `/home/z/backups/d1-backup-20261003-pre-v032-deploy.sql` (742
> INSERTs — byte-count identical to the pre-v0.31 backup; belt-and-braces).

## 📦 v0.31.0 deploy record — re-deploy of the unchanged 0.28.0 worker (DEPLOYED ✅)

> **Deployed to production on 2026-10-02** (worker version ID
> `a66084bf-3185-4e3a-b6c7-49569962dfb8`, URL
> `https://github-to-obsidian-bot.aliassadi-plus.workers.dev`).
> v0.31.0 is a DESKTOP-ONLY release (the main-window balance pass — one
> status card, shape-coded state indicator, resizable window, the log
> card's row grammar + empty state); the Worker's code is unchanged
> since v0.28.0, so this deploy re-ships the exact same 0.28.0 bundle
> at the owner's request. Verified after the deploy: `/health` answers
> `"version":"0.28.0"` with `"status":"ok"`, both queue consumers
> registered (ingest **and** DLQ, plus both producers), all four secrets
> persisted (BOT_TOKEN, GITHUB_PAT, ALLOWED_USER_IDS, HMAC_SECRET), D1
> row counts identical before/after (385 ledger / 2 dead / 342 activity /
> 0 mirror). The D1 export taken just before the deploy:
> `/home/z/backups/d1-backup-20261002-pre-v031-deploy.sql` (belt-and-braces).

## 📦 v0.30.0 deploy record — re-deploy of the unchanged 0.28.0 worker (DEPLOYED ✅)

> **Deployed to production on 2026-10-02** (worker version ID
> `3310c86a-a084-4935-a458-78c2d181c669`, URL
> `https://github-to-obsidian-bot.aliassadi-plus.workers.dev`).
> v0.30.0 is a DESKTOP-ONLY release (the Settings-UI audit — theme kit,
> flat cards, sidebar icons); the Worker's code is unchanged since
> v0.28.0, so this deploy re-ships the exact same 0.28.0 bundle at the
> owner's request. Verified after the deploy: `/health` answers
> `"version":"0.28.0"` with `"status":"ok"`, both queue consumers
> registered (ingest **and** DLQ, plus both producers), all four secrets
> persisted (BOT_TOKEN, GITHUB_PAT, ALLOWED_USER_IDS, HMAC_SECRET), D1
> row counts identical before/after (385 ledger / 2 dead / 342 activity /
> 0 mirror). The D1 export taken just before the deploy:
> `/home/z/backups/d1-backup-20261002-pre-v030-deploy.sql` (belt-and-braces).

## 📦 v0.28.0 update pack — THE LAW at collection time (DEPLOYED ✅)

> **Deployed to production on 2026-10-01** (worker version ID
> `5ac70507-4d33-4eba-9c47-b9584c632456`, URL
> `https://github-to-obsidian-bot.aliassadi-plus.workers.dev`).
> Verified after the deploy: `/health` answers `"version":"0.28.0"`,
> both queue consumers registered (ingest **and** DLQ), all four secrets
> persisted (BOT_TOKEN, GITHUB_PAT, ALLOWED_USER_IDS, HMAC_SECRET), D1
> row counts stable (385 ledger / 2 dead / 342 activity / 0 mirror).
> The D1 export taken just before the deploy:
> `/home/z/backups/d1-backup-20261001-pre-v028.sql` (belt-and-braces).

The owner's law (2026-10-01): "EVERY X And GITHUB domain (all of its
group) must be banned from showing on websites directory" — x/Twitter,
the GitHub group, HuggingFace, Instagram, Facebook, LinkedIn. The
Worker now enforces it at COLLECTION time (tested 40/40 with
`npm test`; `wrangler deploy --dry-run` PASS first):

1. **`DEFAULT_BLOCKED_DOMAINS` = the full 17-domain law** (same list as
   the desktop's `links.LAW_BLOCKED_DOMAINS`). A NON-GitHub link on one
   of them is dead-lettered (`reason: blocked_domain`, 🚫 reply) and
   never reaches the desktop as a pending website.
2. **GitHub REPO links still flow** — they are typed `github` before
   the block check ever runs; only non-repo github.com paths (e.g.
   `/features`) land in the law's net.
3. **`BLOCKED_DOMAINS` env var is ADD-ONLY now** (`blockedDomainsFromEnv`
   in `src/utils.js`): unset or empty = just the law; the var can only
   append more domains. The law cannot be configured away.
4. **Bare `owner.github.io` sites are dead-lettered** (GitHub group) —
   the old "a bare Pages site is a real website" rule is superseded by
   the law. `owner.github.io/<repo>` still maps to the repo (GitHub
   pipeline), unchanged.

## 📦 v0.26.0 update pack — the bot must match the app (DEPLOYED ✅)

> **Deployed to production on 2026-10-01** (worker version ID
> `5865d691-8709-4a52-82f7-1eacdc9d7d5d`, URL
> `https://github-to-obsidian-bot.aliassadi-plus.workers.dev`).
> Verified after the deploy: `/health` answers `"version":"0.26.0"`,
> both queue consumers registered (ingest **and** DLQ), Telegram webhook
> healthy (0 pending, no errors), all four secrets persisted, D1 row
> counts identical before/after (384 ledger / 2 dead / 341 activity).
> The D1 export taken just before the deploy is the belt-and-braces copy.

This release changes the Worker (tested 37/37 with Node's built-in test
runner — `npm test` — and validated with `wrangler deploy --dry-run` plus a
local `wrangler dev` run: a forwarded GitHub link, a website link and an
x.com link were recorded exactly as the app expects: ledger / ledger /
dead-letter). What's new on the Worker side:

1. **The dead-letter queue finally has a consumer** — a link that fails
   processing 5 times used to sit in a queue forever, invisible to D1;
   now it is recorded into the permanent `dead_letters` table (reason
   `dlq_exhausted`), you get a 💀 note in Telegram, and nothing loops.
2. **HMAC fix** — `/api/decommissions?since=…` could never verify before
   (the worker stripped the query string when checking the signature);
   it now signs exactly what the desktop signs.
3. **Version reporting** — `/health` reports `0.26.0`, and the desktop
4. **v0.26.0 contract fixes** — GitHub Pages links
   (`owner.github.io/repo`) now get the same canonical identity on both
   sides; a transiently-failed link (dlq_exhausted) stays visible in
   /pending and can be re-sent (only the blocked-domain policy hides
   links now); failed enrichments are audited in the activity log.
   app's Test Connection compares it with the version it expects and
   tells you when the deployed bot is older than the app.

### Step-by-step (about 5 minutes, nothing is destroyed)

Run everything from the `cloudflare-bot` folder. On Windows use
PowerShell; the `bash` lines have PowerShell twins built into
`deploy-latest.ps1` — you can simply run `.\deploy-latest.ps1` and skip
to step 4.

1. **Save a copy of the database first** (a plain-language safety net —
   this downloads a snapshot of every link the bot has ever recorded to
   your machine; nothing is changed or deleted):
   ```bash
   npx wrangler d1 export curator-bot --remote --output=d1-backup-$(date +%Y%m%d).sql
   ```
   Windows PowerShell:
   ```powershell
   npx wrangler d1 export curator-bot --remote --output="d1-backup-$(Get-Date -Format yyyyMMdd).sql"
   ```
2. **Check you are logged in** (opens your browser if not):
   ```bash
   npx wrangler login
   ```
3. **Apply the schema and deploy** (the schema is `IF NOT EXISTS` — safe
   to run twice, existing data untouched; v0.25.0's only schema change
   was a comment; v0.26.0 needs no schema change at all):
   ```bash
   npx wrangler d1 execute curator-bot --remote --file=schema.sql
   npx wrangler deploy
   ```
   (Or just: `bash deploy-latest.sh` — it does both plus a health check.)
4. **Check it worked** — all three must pass:
   - `curl https://github-to-obsidian-bot.aliassadi-plus.workers.dev/health`
     answers with `"version":"0.26.0"`.
   - In the desktop app: **Test Connection** → the Telegram section shows
     `✅ Bot Worker — v0.26.0 — matches this app`. (Needs the Worker URL
     in Settings; it is already in your config.example.)
   - Send **one test link** to @githubfetcherbot from Telegram — e.g.
     `https://github.com/torvalds/linux` — and watch for the usual
     ⭐ reply; it should appear in the bot queue on your next SYNC.
5. **Watch it live while testing** (optional): `npx wrangler tail`.

### Rollback (if anything looks wrong)

```bash
git log --oneline -5                      # find the previous release commit
git checkout <previous-release-commit> -- src/ wrangler.toml schema.sql
npx wrangler deploy                       # redeploy the old code
```
Data is never touched by a deploy (the schema is additive-only), so a
rollback only means redeploying the previous worker code. The D1 export
from step 1 is the belt-and-braces copy.


---

## Prerequisites

### 1. Software
- **Node.js 18+** — Download from https://nodejs.org/
- **A terminal** — Command Prompt, PowerShell, or Git Bash on Windows

Verify Node.js is installed:
```bash
node --version
# Should show v18.x.x or higher

npm --version
# Should show 9.x.x or higher
```

### 2. Accounts & Tokens

You need these ready BEFORE starting:

#### a) Cloudflare Account (free)
- Go to https://dash.cloudflare.com/sign-up
- Create a free account
- Verify your email

#### b) Telegram Bot Token (you already have this)
- Your existing `@githubfetcherbot` token
- Format: `YOUR_BOT_TOKEN`

#### c) Your Telegram User ID
- Open Telegram, search for `@userinfobot`
- Send any message to it
- It replies with your numeric user ID (e.g., `123456789`)
- **Write this down** — you'll need it

#### d) GitHub Personal Access Token (PAT)
- Go to https://github.com/settings/tokens
- Click "Generate new token (classic)"
- Name: "Curator Bot"
- Expiration: 90 days (or longer)
- Scopes: Check **`public_repo`** only
- Click "Generate token"
- **Copy the token immediately** (starts with `ghp_`)
- **Write this down** — you'll need it

---

## Part 1: Deploy the Cloudflare Worker

### Step 1: Copy files to your computer

Copy the entire `cloudflare/` folder to your computer, e.g., to `C:\curator-bot\cloudflare\` (Windows) or `~/curator-bot/cloudflare/` (Mac/Linux).

The folder should contain:
```
cloudflare/
├── wrangler.toml
├── package.json
├── schema.sql
├── README.md
├── src/
│   └── (11 .js files)
└── dashboard/
    └── (Next.js project)
```

### Step 2: Open terminal in the cloudflare folder

```bash
cd C:\curator-bot\cloudflare
# or on Mac/Linux:
cd ~/curator-bot/cloudflare
```

### Step 3: Install Wrangler (Cloudflare CLI)

```bash
npm install
```

This installs `wrangler` locally (defined in package.json).

Verify:
```bash
npx wrangler --version
# Should show 3.x.x or higher
```

### Step 4: Login to Cloudflare

```bash
npx wrangler login
```

This opens your browser. Click "Allow" to authorize Wrangler.

Verify you're logged in:
```bash
npx wrangler whoami
```

You should see your email and account ID.

### Step 5: Create D1 Database

```bash
npx wrangler d1 create curator-bot
```

**Expected output:**
```
✅ Successfully created DB 'curator-bot'
Created your new D1 database.

[[d1_databases]]
binding = "DB"
database_name = "curator-bot"
database_id = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"  ← COPY THIS
```

**Copy the `database_id` value** — you'll paste it into wrangler.toml.

### Step 6: Create KV Namespace

```bash
npx wrangler kv namespace create CACHE
```

**Expected output:**
```
 ⛅️ wrangler
[id = xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx]  ← COPY THIS
```

**Copy the `id` value.**

### Step 7: Create Queues

```bash
npx wrangler queues create curator-ingest
npx wrangler queues create curator-ingest-dlq
```

**Expected output (for each):**
```
Creating queue curator-ingest.
Created queue curator-ingest.
```

### Step 8: Create R2 Bucket

```bash
npx wrangler r2 bucket create curator-backups
```

**Expected output:**
```
Creating bucket curator-backups.
Created bucket curator-backups.
```

### Step 9: Update wrangler.toml

Open `wrangler.toml` in a text editor. Replace these two values:

```toml
[[d1_databases]]
binding = "DB"
database_name = "curator-bot"
database_id = "PASTE_YOUR_D1_DATABASE_ID_HERE"  ← Replace this

[[kv_namespaces]]
binding = "CACHE"
id = "PASTE_YOUR_KV_NAMESPACE_ID_HERE"  ← Replace this
```

Save the file.

### Step 10: Apply D1 Schema (create tables)

```bash
npx wrangler d1 execute curator-bot --remote --file=schema.sql
```

**Expected output:**
```
✅ Executed 25 commands in XXms
```

Verify tables were created:
```bash
npx wrangler d1 execute curator-bot --remote --command="SELECT name FROM sqlite_master WHERE type='table'"
```

**Expected output:**
```
┌──────────────────────┐
│ name                 │
├──────────────────────┤
│ ever_seen_ledger     │
│ vault_mirror         │
│ decommission_events  │
│ dead_letters         │
│ desktop_errors       │
│ sync_state           │
│ gdrive_snapshots     │
│ dashboard_sessions   │
│ desktop_installs     │
│ activity_log         │
│ sqlite_sequence      │
│ _cf_KV               │
└──────────────────────┘
```

You should see 10 tables (plus `sqlite_sequence` and `_cf_KV` which are internal).

### Step 11: Set Secrets

Run each command. It prompts you to paste a value:

#### a) Telegram Bot Token
```bash
npx wrangler secret put BOT_TOKEN
```
Paste your bot token: `YOUR_BOT_TOKEN`

#### b) GitHub PAT
```bash
npx wrangler secret put GITHUB_PAT
```
Paste your GitHub PAT: `ghp_xxxxxxxxxxxxxxxxxxxx`

#### c) Allowed User IDs
```bash
npx wrangler secret put ALLOWED_USER_IDS
```
Paste your Telegram user ID: `123456789` (just the number, no quotes)

#### d) HMAC Secret (generate a random string)
```bash
npx wrangler secret put HMAC_SECRET
```
Paste any random 32+ character string, e.g.: `curator-hmac-secret-2025-change-this-to-random`

**Verify secrets are set:**
```bash
npx wrangler secret list
```

Should show: `BOT_TOKEN`, `GITHUB_PAT`, `ALLOWED_USER_IDS`, `HMAC_SECRET`

### Step 12: Deploy the Worker

```bash
npx wrangler deploy
```

**Expected output:**
```
 ⛅️ wrangler
 Uploaded github-curator-bot (XX sec)
 Deployed github-curator-bot triggers (X sec)
   https://github-curator-bot.YOUR-SUBDOMAIN.workers.dev  ← COPY THIS URL
 Current Version ID: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

**Copy your Worker URL** — you'll need it for the webhook and dashboard.

### Step 13: Test the Worker

Open your Worker URL in a browser:
```
https://github-curator-bot.YOUR-SUBDOMAIN.workers.dev/health
```

**Expected JSON response:**
```json
{
  "status": "ok",
  "service": "github-curator-bot",
  "version": "1.0.0",
  "last_webhook_at": "",
  "cutover_complete": false,
  "timestamp": "2025-07-XX..."
}
```

### Step 14: Set Telegram Webhook

Replace `YOUR_BOT_TOKEN` and `YOUR_WORKER_URL`:

```bash
curl -s "https://api.telegram.org/botYOUR_BOT_TOKEN/setWebhook?url=YOUR_WORKER_URL/webhook"
```

**Example:**
```bash
curl -s "https://api.telegram.org/botYOUR_BOT_TOKEN/setWebhook?url=https://github-curator-bot.my-subdomain.workers.dev/webhook"
```

**Expected response:**
```json
{
  "ok": true,
  "result": true,
  "description": "Webhook was set"
}
```

Verify webhook is set:
```bash
curl -s "https://api.telegram.org/botYOUR_BOT_TOKEN/getWebhookInfo" | python -m json.tool
```

Check that `url` field shows your Worker URL and `last_error_message` is null.

### Step 15: Test the Bot in Telegram

1. Open Telegram
2. Find your bot (`@githubfetcherbot`)
3. Send `/start`
4. You should get the welcome message

5. Send a GitHub link, e.g.:
   ```
   https://github.com/vercel/next.js
   ```

6. You should get a reply like:
   ```
   📋 Received: vercel/next.js
   ⭐ 120k — The React Framework
   💻 JavaScript
   Status: Pending processing
   [🗑️ Mark Decommission]
   ```

7. Send `/stats` — you should see statistics

**If the bot doesn't reply:**
- Check `npx wrangler tail` for error logs (real-time)
- Verify `ALLOWED_USER_IDS` matches your Telegram user ID
- Check webhook info (Step 14)

---

## Part 2: Deploy the Dashboard

### Step 1: Install dashboard dependencies

```bash
cd dashboard
npm install
```

This installs Next.js, React, TanStack Query, Tailwind CSS, etc.

### Step 2: Set the Worker URL environment variable

Create a file named `.env.local` in the `dashboard/` folder:

```bash
# Windows (Command Prompt):
echo NEXT_PUBLIC_WORKER_URL=https://github-curator-bot.YOUR-SUBDOMAIN.workers.dev > .env.local

# Mac/Linux:
echo "NEXT_PUBLIC_WORKER_URL=https://github-curator-bot.YOUR-SUBDOMAIN.workers.dev" > .env.local
```

Replace `YOUR-SUBDOMAIN` with your actual subdomain from Part 1, Step 12.

### Step 3: Build the dashboard (static export)

```bash
npm run build
```

**Expected output:**
```
  ▲ Next.js 16.0.0
  Creating an optimized production build ...

 ✓ Compiled successfully
 ✓ Collecting page data
 ✓ Generating static pages (12/12)

Finalizing page optimization ...

Route (app)                              Size     First Load JS
┌ ○ /                                    142 B          89.4 kB
├ ○ /_not-found                          871 B            89 kB
├ ○ /auth                                1.35 kB       90.1 kB
├ ○ /backups                             3.21 kB       93.4 kB
├ ○ /dead-letters                        2.04 kB       89.3 kB
├ ○ /decommissioned                      2.15 kB       89.4 kB
├ ○ /errors                              2.38 kB       89.7 kB
├ ○ /login                               2.65 kB       90.1 kB
├ ○ /overview                            4.76 kB       95.7 kB
├ ○ /pending                             2.77 kB       90.3 kB
├ ○ /settings                            3.85 kB       94.1 kB
└ ○ /vault                               2.62 kB       90.2 kB
+ First Load JS shared by all             87.8 kB
├ chunks/main-xxxxx.js                  87.8 kB
└ other shared chunks (total)

○  (Static)  prerendered as static content
```

This creates an `out/` folder with all the static HTML/CSS/JS files.

### Step 4: Deploy to Cloudflare Pages

#### Option A: Using Wrangler CLI (recommended)

```bash
npx wrangler pages deploy out --project-name=curator-dashboard
```

**First run** — it will ask:
```
✨ Successfully created the 'curator-dashboard' project. It will be available at the following URL once the deployment is complete:
https://curator-dashboard-xxxx.pages.dev  ← COPY THIS
```

**Expected output:**
```
🌎  Deploying...
✨ Success! Uploaded 0 files
✨ Deployment complete! Take a peek over at https://xxxxxxxx.curator-dashboard-xxxx.pages.dev
```

**Copy your dashboard URL** — this is where you'll access the dashboard.

#### Option B: Using Cloudflare Dashboard UI

If the CLI doesn't work:

1. Go to https://dash.cloudflare.com/
2. Click "Workers & Pages" in the left sidebar
3. Click "Create application" → "Pages" → "Upload assets"
4. Project name: `curator-dashboard`
5. Click "Create project"
6. Drag and drop the `out/` folder contents
7. Click "Deploy"

### Step 5: Test the Dashboard

1. Open your dashboard URL in a browser:
   ```
   https://curator-dashboard-xxxx.pages.dev
   ```

2. You'll be redirected to `/login`

3. Enter your Telegram User ID and click "Send Magic Link"

4. Check your Telegram — the bot DMs you:
   ```
   🔐 Dashboard Login

   Click below to log in to the dashboard (link expires in 10 minutes):

   🔓 Open Dashboard
   ```

5. Click the link — you're redirected to `/overview`

6. You should see:
   - Stats cards (Pending, In Vault, Decommissioned, Dead Letters)
   - System status (Desktop, GDrive, Last Sync)
   - Recent activity feed

7. Navigate through all pages:
   - `/pending` — links you forwarded to the bot
   - `/vault` — searchable (empty until desktop app syncs)
   - `/decommissioned` — dead repos
   - `/dead-letters` — invalid links
   - `/errors` — desktop error log
   - `/backups` — GDrive backups (empty until desktop syncs)
   - `/settings` — system config + logout

---

## Part 3: Verify Everything Works

### End-to-End Test

1. **Forward a GitHub link to the bot** in Telegram
   - Bot replies with stars + description ✅

2. **Check the dashboard** `/pending` page
   - Your link should appear (refresh takes ~30s) ✅

3. **Run `/stats` in Telegram**
   - Should show: `Pending: 1, Total Seen: 1` ✅

4. **Check the Worker health**:
   ```bash
   curl https://github-curator-bot.YOUR-SUBDOMAIN.workers.dev/health
   ```
   - `last_webhook_at` should be recent ✅

5. **Check D1 data**:
   ```bash
   npx wrangler d1 execute curator-bot --remote --command="SELECT COUNT(*) FROM ever_seen_ledger"
   ```
   - Should show count >= 1 ✅

### View Real-Time Logs

To debug issues, watch Worker logs in real-time:
```bash
npx wrangler tail
```

Keep this running while you test. Any errors will appear here.

---

## Troubleshooting

### Bot doesn't reply to messages

1. **Check webhook is set:**
   ```bash
   curl -s "https://api.telegram.org/botYOUR_BOT_TOKEN/getWebhookInfo" | python -m json.tool
   ```
   Look for `last_error_message` field.

2. **Check ALLOWED_USER_IDS:**
   - Your Telegram user ID must be in this secret
   - Get your ID from `@userinfobot`
   - Re-set: `npx wrangler secret put ALLOWED_USER_IDS`

3. **Check Worker logs:**
   ```bash
   npx wrangler tail
   ```
   Forward a link and watch for errors.

4. **Redeploy after code changes:**
   ```bash
   npx wrangler deploy
   ```

### Dashboard shows "Not authenticated" or redirect loop

1. **Check Worker URL in `.env.local`:**
   - Must match exactly (including `https://`)
   - No trailing slash
   - Rebuild: `npm run build`

2. **Check browser console** (F12) for CORS errors

3. **Verify magic link flow:**
   - Click the link in Telegram DM
   - Should redirect to `/auth/?token=...` then `/overview/`

### Dashboard data is empty

This is expected until you:
1. **Run the desktop app** (which polls the Worker)
2. **Process some links** (which pushes vault_mirror)
3. **Wait 5 minutes** (poll interval)

The dashboard is a mirror — it only shows what the desktop has synced.

### D1 query fails

```bash
# Check tables exist
npx wrangler d1 execute curator-bot --remote --command=".tables"

# Re-apply schema if needed
npx wrangler d1 execute curator-bot --remote --file=schema.sql
```

### Queue messages not processing

```bash
# Check queue status
npx wrangler queues list
npx wrangler queues describe curator-ingest
```

If messages are stuck, check `npx wrangler tail` for consumer errors.

### GitHub enrichment not working (no stars/description)

1. **Check GITHUB_PAT is set:**
   ```bash
   npx wrangler secret list
   ```

2. **Check PAT is valid:**
   ```bash
   curl -H "Authorization: token ghp_YOUR_PAT" https://api.github.com/repos/vercel/next.js
   ```
   Should return JSON with repo data.

3. **Check PAT scope:**
   - Must have `public_repo` scope
   - Generate new token if needed

---

## Post-Deployment

### Update Worker Code

After any code change:
```bash
npx wrangler deploy
```

### Update Dashboard

After any dashboard change:
```bash
cd dashboard
npm run build
npx wrangler pages deploy out --project-name=curator-dashboard
```

### Monitor

- **Worker logs:** `npx wrangler tail`
- **D1 data:** `npx wrangler d1 execute curator-bot --remote --command="SELECT COUNT(*) FROM ever_seen_ledger"`
- **Queue status:** `npx wrangler queues describe curator-ingest`
- **R2 exports:** `npx wrangler r2 object list curator-backups`
- **Cloudflare dashboard:** https://dash.cloudflare.com/ → Workers & Pages

### Costs

Everything is on the free tier:
- **Workers:** 100k req/day (you'll use ~200/day)
- **D1:** 5M rows read/day, 100k written/day (you'll use <500)
- **KV:** 100k reads/day, 1k writes/day (you'll use ~200)
- **Queues:** 100k ops/day (you'll use ~100)
- **R2:** 10GB storage (you'll use <100MB)
- **Pages:** 500 builds/month, unlimited requests

**Total cost: $0/month** at your projected volume (<100 links/day).

---

## Summary of URLs

After deployment, you'll have:

| Service | URL |
|---------|-----|
| Worker API | `https://github-curator-bot.YOUR-SUBDOMAIN.workers.dev` |
| Worker health | `https://github-curator-bot.YOUR-SUBDOMAIN.workers.dev/health` |
| Telegram webhook | `https://github-curator-bot.YOUR-SUBDOMAIN.workers.dev/webhook` |
| Dashboard | `https://curator-dashboard-xxxx.pages.dev` |

**Write these down.** You'll need the Worker URL for:
- Desktop app config (`cloudflare_worker_url`)
- Dashboard `.env.local` (`NEXT_PUBLIC_WORKER_URL`)
- Telegram webhook setup

---

## Next Step

Once Worker + Dashboard are deployed and tested, the final step is **integrating the desktop app** (main.py) with the Python modules. That's the `integration_snippet.py` work — I can do that next, or you can deploy first and verify everything works.
