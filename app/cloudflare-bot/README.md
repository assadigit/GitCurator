# GitHub Project Curator Bot — Cloudflare Worker

Cloudflare-edge backend for the GitHub Project Curator desktop app. Receives forwarded GitHub links via Telegram webhook, stores in D1 (SQLite) via Queue (7-day retry buffer), auto-replies with status, and syncs bidirectionally with the desktop app.

> **Version staleness is detectable** (v0.25.0): `GET /health` reports the
> Worker's version (`src/version.js` is the single source — keep it in
> lockstep with the repo `VERSION` and
> `gitcurator/constants.py::EXPECTED_WORKER_VERSION` at release time). The
> desktop's Test Connection compares them and tells you when the deployed
> bot is older than the app expects.
>
> **Tests** (Node's built-in runner, no extra dependencies):
> `npm test` — 32 cases covering link intake, dedup, the blocked/self
> domain policy, the HMAC desktop contract (including the query-string
> case), the `/api/pending` shape, and the dead-letter-queue drain.


## Architecture

```
Telegram → Webhook → Worker → Queue → Consumer → D1
                                    ↓
                              GitHub enrichment (PAT)
                                    ↓
                              Auto-reply (with inline buttons)

Desktop → Poll /api/pending (every 5 min)
Desktop → Push /api/vault_mirror/push (after batch, HMAC-signed)
Desktop → Push /api/errors (CRITICAL/WARNING/INFO)
Desktop → Push /api/decommission (real-time 404s)

Cron → Hourly health check
Cron → Sunday 9 AM Iran: weekly report
Cron → Midnight Iran: daily summary
Cron → 3 AM Iran: R2 D1 export
```

## Setup (15 minutes)

### Prerequisites
- Cloudflare account (free tier is sufficient)
- Node.js 18+ and npm
- Telegram bot token (your existing `@githubfetcherbot`)
- GitHub Personal Access Token (PAT) with `public_repo` scope

### Step 1: Install Wrangler

```bash
cd /home/z/my-project/upload/fixes/cloudflare
npm install
npx wrangler login
```

### Step 2: Create Cloudflare resources

```bash
# Create D1 database
npx wrangler d1 create curator-bot
# Copy the database_id from output → paste into wrangler.toml

# Create KV namespace
npx wrangler kv namespace create CACHE
# Copy the id from output → paste into wrangler.toml

# Create Queues
npx wrangler queues create curator-ingest
npx wrangler queues create curator-ingest-dlq

# Create R2 bucket
npx wrangler r2 bucket create curator-backups
```

### Step 3: Update wrangler.toml

Edit `wrangler.toml` and replace:
- `YOUR_D1_DATABASE_ID` → from Step 2
- `YOUR_KV_NAMESPACE_ID` → from Step 2

### Step 4: Apply D1 schema

```bash
npx wrangler d1 execute curator-bot --remote --file=schema.sql
```

Verify:
```bash
npx wrangler d1 execute curator-bot --remote --command="SELECT name FROM sqlite_master WHERE type='table'"
```
Should show 10 tables.

### Step 5: Set secrets

```bash
# Telegram bot token
npx wrangler secret put BOT_TOKEN
# Paste: your bot token (e.g., 123456789:AAF...)

# GitHub PAT (public_repo scope)
npx wrangler secret put GITHUB_PAT
# Paste: ghp_xxxxxxxxxxxxxxxxxxxx

# Allowed Telegram user IDs (comma-separated)
npx wrangler secret put ALLOWED_USER_IDS
# Paste: 123456789 (your Telegram user ID — get from @userinfobot)

# HMAC secret (random string for pairing)
npx wrangler secret put HMAC_SECRET
# Paste: any random 32+ char string
```

### Step 6: Deploy

```bash
npx wrangler deploy
```

Note your Worker URL: `https://github-curator-bot.<your-subdomain>.workers.dev`

### Step 7: Set Telegram webhook

```bash
# Replace YOUR_WORKER_URL and YOUR_BOT_TOKEN
curl -s "https://api.telegram.org/botYOUR_BOT_TOKEN/setWebhook?url=YOUR_WORKER_URL/webhook"
```

Verify webhook is set:
```bash
curl -s "https://api.telegram.org/botYOUR_BOT_TOKEN/getWebhookInfo" | jq
```

### Step 8: Test

1. Send `/start` to your bot in Telegram → should get welcome message
2. Forward a GitHub link (e.g., `https://github.com/vercel/next.js`) → should get:
   ```
   📋 Received: vercel/next.js
   ⭐ 120k — The React Framework
   💻 JavaScript
   Status: Pending processing
   [🗑️ Mark Decommission]
   ```
3. Forward the same link again → should get "⏳ Already pending"
4. Visit `https://your-worker-url/health` → should return JSON with status

## File Structure

```
cloudflare/
├── README.md                # This file
├── package.json             # npm config
├── wrangler.toml            # Cloudflare config (D1, KV, Queue, R2, Cron)
├── schema.sql               # D1 schema (10 tables)
└── src/
    ├── index.js             # Main Worker entry (fetch + queue + scheduled)
    ├── webhook.js           # Telegram webhook handler
    ├── queue-consumer.js    # Queue consumer (D1 write + enrich + reply)
    ├── api.js               # Desktop API endpoints (HMAC-auth)
    ├── cron.js              # Cron trigger handlers
    ├── db.js                # D1 query helpers
    ├── kv.js                # KV cache helpers
    ├── telegram.js          # Telegram Bot API helpers
    └── utils.js             # URL normalization, crypto, formatting
```

## API Endpoints

### Public
- `GET /health` — Health check + status

### Telegram
- `POST /webhook` — Telegram webhook (receives updates)

### Desktop (HMAC-authenticated)
- `POST /api/pair` — Pair desktop install (uses pairing code)
- `GET /api/pending` — Fetch pending links
- `GET /api/verify` — Full reconciliation data
- `GET /api/decommissions?since=` — Pull bot-side decommissions
- `POST /api/vault_mirror/push` — Push vault state (idempotent via sync_id)
- `POST /api/decommission` — Real-time 404 from desktop
- `POST /api/errors` — Push desktop errors
- `POST /api/gdrive/backup_status` — Push backup metadata
- `POST /api/backfill` — Backfill chunk (idempotent)

### Dashboard (cookie-auth) — Layer 13
- `POST /api/auth/request` — Request magic link
- `GET /api/auth/verify` — Verify magic link, set cookie
- `POST /api/auth/logout` — Revoke session
- `GET /api/dashboard/*` — Dashboard data endpoints

## Bot Commands

| Command | Status | Description |
|---------|--------|-------------|
| `/start` | ✅ | Welcome + command list |
| `/help` | ✅ | Full command reference |
| `/pair` | 🚧 | Pair desktop app (Layer 4) |
| `/dashboard` | 🚧 | Get dashboard login (Layer 13) |
| `/stats` | 🚧 | Statistics (Layer 7) |
| `/status <url>` | 🚧 | Check link status (Layer 7) |
| `/search <query>` | 🚧 | Search vault (Layer 7) |
| `/pending` | 🚧 | List pending (Layer 7) |
| `/dead` | 🚧 | List decommissioned (Layer 7) |
| `/notfound` | 🚧 | List dead letters (Layer 7) |
| `/sync` | 🚧 | Force sync (Layer 7) |
| `/errors` | 🚧 | Show errors (Layer 7) |
| `/errors clear` | 🚧 | Acknowledge errors (Layer 7) |
| `/forget <url>` | 🚧 | Soft-delete from ledger (Layer 7) |
| `/backup` | 🚧 | Manual GDrive backup (Layer 10) |
| `/restore` | 🚧 | List backups (Layer 10) |
| `/logout` | 🚧 | Revoke dashboard sessions (Layer 13) |

✅ = implemented, 🚧 = planned for later layer

## D1 Tables

1. `ever_seen_ledger` — Immutable, every URL ever seen (safety net)
2. `vault_mirror` — Mutable, desktop-owned current vault state
3. `decommission_events` — Two-way decommission sync (union)
4. `dead_letters` — Unprocessable links (non-GitHub, invalid)
5. `desktop_errors` — Desktop error log
6. `sync_state` — Key-value sync state (idempotency, reconciliation)
7. `gdrive_snapshots` — Google Drive backup metadata
8. `dashboard_sessions` — Web dashboard auth sessions
9. `desktop_installs` — HMAC pairing registry
10. `activity_log` — Recent activity for dashboard

## Monitoring

- **Logs**: `npx wrangler tail` (real-time)
- **Health**: `GET /health` endpoint
- **Cron**: Cloudflare Dashboard → Workers → your-worker → Triggers
- **D1**: `npx wrangler d1 execute curator-bot --remote --command="SELECT COUNT(*) FROM ever_seen_ledger"`
- **R2**: `npx wrangler r2 object list curator-backups`

## Troubleshooting

### Webhook not receiving updates
```bash
curl -s "https://api.telegram.org/botYOUR_BOT_TOKEN/getWebhookInfo" | jq
```
Check `last_error_message` field.

### Bot not replying
1. Check `ALLOWED_USER_IDS` includes your Telegram user ID
2. Check `wrangler tail` for errors
3. Verify D1 schema is applied: `npx wrangler d1 execute curator-bot --remote --command=".tables"`

### Queue messages stuck
```bash
npx wrangler queues list
npx wrangler queues describe curator-ingest
```

### GitHub enrichment failing
- Verify `GITHUB_PAT` secret is set
- Verify PAT has `public_repo` scope
- Check rate limits in `wrangler tail`

## Build Layers

- **Layer 1**: ✅ Cloudflare infrastructure (D1, KV, Queue, R2, Cron)
- **Layer 2**: ✅ Webhook ingestion + Queue + D1 + basic auto-reply
- **Layer 3**: ✅ GitHub enrichment (stars, description, README)
- **Layer 4**: ✅ HMAC auth + desktop API endpoints
- **Layer 5**: ✅ vault_mirror push + decommission sync
- **Layer 6**: 🚧 Rich auto-replies + edit-on-status-change
- **Layer 7**: 🚧 All bot commands
- **Layer 8**: 🚧 Desktop error reporting (CRITICAL/WARNING/INFO)
- **Layer 9**: ✅ Cron triggers (health, weekly, daily, R2 export)
- **Layer 10**: 🚧 Google Drive backup
- **Layer 11**: 🚧 GitHub redirect handling
- **Layer 12**: 🚧 Cutover + backfill
- **Layer 13**: 🚧 Web dashboard (Cloudflare Pages)
- **Layer 14**: 🚧 Polish + end-to-end testing
