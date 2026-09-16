#!/usr/bin/env bash
# ============================================================================
# GitCurator — deploy the LATEST worker version to Cloudflare (bash twin of
# deploy-latest.ps1). Two-minute update path for an EXISTING deployment.
#
# Usage (from the cloudflare-bot folder):
#   bash deploy-latest.sh [--with-secret] [--skip-schema] [--health-url URL]
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")"

WITH_SECRET=0; SKIP_SCHEMA=0; HEALTH_URL=""
while [ $# -gt 0 ]; do
    case "$1" in
        --with-secret) WITH_SECRET=1 ;;
        --skip-schema) SKIP_SCHEMA=1 ;;
        --health-url)  HEALTH_URL="$2"; shift ;;
        *) echo "unknown option: $1"; exit 1 ;;
    esac
    shift
done

echo ""
echo "GitCurator -> Cloudflare deploy (latest version)"
echo "================================================="

command -v node >/dev/null 2>&1 || { echo "[FAIL] Node.js not found — install Node 18+ first."; exit 1; }

if [ ! -e node_modules/.bin/wrangler ]; then
    echo "[1/4] Installing wrangler (one-time)..."
    npm install
else
    echo "[1/4] wrangler present."
fi

echo "[2/4] Checking Cloudflare login..."
if ! npx wrangler whoami >/dev/null 2>&1; then
    echo "      Not logged in. Run:  npx wrangler login   (opens your browser), then re-run."
    exit 1
fi

if [ "$SKIP_SCHEMA" = "1" ]; then
    echo "[3/4] Skipping schema (--skip-schema)."
else
    echo "[3/4] Applying schema.sql (safe — IF NOT EXISTS, data untouched)..."
    npx wrangler d1 execute curator-bot --remote --file=schema.sql
fi

echo "[4/4] Deploying github-to-obsidian-bot ..."
DEPLOY_LOG="$(mktemp)"
npx wrangler deploy 2>&1 | tee "$DEPLOY_LOG"

if [ "$WITH_SECRET" = "1" ]; then
    echo ""
    echo "Setting WEBHOOK_SECRET (the v30 anti-impersonation hardening)."
    npx wrangler secret put WEBHOOK_SECRET || echo "[WARN] secret not set — backward-compatible mode stays on."
    echo "IMPORTANT — register the SAME secret with Telegram (one command):"
    echo '  curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook?url=https://<YOUR_WORKER_URL>/webhook&secret_token=<SAME_SECRET>"'
    echo "Until you run it, Telegram will NOT send the header and /webhook returns 403."
fi

URL="${HEALTH_URL:-$(grep -Eo 'https://[a-z0-9-]+\.[a-z0-9-]+\.workers\.dev' "$DEPLOY_LOG" | head -1)}"
rm -f "$DEPLOY_LOG"
if [ -n "$URL" ]; then
    echo ""
    echo "Health check: $URL"
    curl -fsS --max-time 20 "$URL" && echo "" || echo "  [WARN] health endpoint did not answer yet — retry in a minute."
fi

echo ""
echo "Done. Reminders:"
echo " - All secrets and data persisted across this deploy."
echo " - Rotate the Telegram bot token (it was exposed in chat) — then:"
echo "     npx wrangler secret put BOT_TOKEN"
echo " - Live tail while testing:  npx wrangler tail"
