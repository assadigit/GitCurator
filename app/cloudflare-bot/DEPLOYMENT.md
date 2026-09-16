# Deployment Guide — From Scratch

Complete step-by-step guide to deploy the Cloudflare Worker + Dashboard.
Estimated time: 30-45 minutes.

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
- Format: `REMOVED-BOT-TOKEN`

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
Paste your bot token: `REMOVED-BOT-TOKEN`

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
curl -s "https://api.telegram.org/botREMOVED-BOT-TOKEN/setWebhook?url=https://github-curator-bot.my-subdomain.workers.dev/webhook"
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
