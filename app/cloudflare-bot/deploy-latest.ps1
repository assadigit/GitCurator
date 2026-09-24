# ============================================================================
# GitCurator — deploy the LATEST worker version to Cloudflare
# ----------------------------------------------------------------------------
# Two-minute update path for an EXISTING deployment (D1/KV/Queues/R2 already
# exist — see DEPLOYMENT.md for the from-scratch path).
#
# Usage (from the cloudflare-bot folder):
#   .\deploy-latest.ps1                 # schema (idempotent) + deploy + health
#   .\deploy-latest.ps1 -WithSecret     # also set the WEBHOOK_SECRET hardening
#   .\deploy-latest.ps1 -SkipSchema     # deploy + health only
#
# What persists across deploys (never re-entered): D1 database + data, KV
# namespace, Queues, R2 bucket, and ALL worker secrets (BOT_TOKEN, GITHUB_PAT,
# ALLOWED_USER_IDS, HMAC_SECRET, WEBHOOK_SECRET).
# ============================================================================
param(
    [switch]$WithSecret,
    [switch]$SkipSchema
)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$WorkerName = "github-to-obsidian-bot"

Write-Host ""
Write-Host "GitCurator -> Cloudflare deploy (latest version)" -ForegroundColor Cyan
Write-Host "=================================================" -ForegroundColor Cyan

# --- 1) Node.js -------------------------------------------------------------
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host "[FAIL] Node.js not found. Install Node 18+ from https://nodejs.org/ and re-run." -ForegroundColor Red
    exit 1
}

# --- 2) Dependencies --------------------------------------------------------
if (-not (Test-Path ".\node_modules\.bin\wrangler*")) {
    Write-Host "[1/4] Installing wrangler (one-time)..." -ForegroundColor Yellow
    npm install
    if ($LASTEXITCODE -ne 0) { Write-Host "[FAIL] npm install failed." -ForegroundColor Red; exit 1 }
} else {
    Write-Host "[1/4] wrangler present." -ForegroundColor Green
}

# --- 3) Cloudflare auth -----------------------------------------------------
Write-Host "[2/4] Checking Cloudflare login..." -ForegroundColor Yellow
$whoami = npx wrangler whoami 2>&1 | Out-String
if ($LASTEXITCODE -ne 0 -or $whoami -match "not authenticated|run wrangler login") {
    Write-Host "      Not logged in. Run:  npx wrangler login   (opens your browser), then re-run this script." -ForegroundColor Red
    exit 1
}
Write-Host ($whoami.Trim() -split "`n" | Select-Object -Last 1)

# --- 4) Schema (idempotent — CREATE TABLE/INDEX IF NOT EXISTS) -------------
if ($SkipSchema) {
    Write-Host "[3/4] Skipping schema (-SkipSchema)." -ForegroundColor Yellow
} else {
    Write-Host "[3/4] Applying schema.sql (safe — IF NOT EXISTS, existing data untouched)..." -ForegroundColor Yellow
    npx wrangler d1 execute curator-bot --remote --file=schema.sql
    if ($LASTEXITCODE -ne 0) { Write-Host "[FAIL] schema apply failed." -ForegroundColor Red; exit 1 }
}

# --- 5) Deploy ---------------------------------------------------------------
Write-Host "[4/4] Deploying $WorkerName ..." -ForegroundColor Yellow
$deployOutput = (npx wrangler deploy 2>&1 | Tee-Object -Variable out) | Out-String
if ($LASTEXITCODE -ne 0) { Write-Host $deployOutput; Write-Host "[FAIL] deploy failed." -ForegroundColor Red; exit 1 }
Write-Host $deployOutput

# --- 6) Optional hardening secret -------------------------------------------
if ($WithSecret) {
    Write-Host ""
    Write-Host "Setting WEBHOOK_SECRET (the v30 anti-impersonation hardening)." -ForegroundColor Cyan
    Write-Host "Pick a long random string (e.g. from: https://generate-secret.org/)" -ForegroundColor DarkGray
    npx wrangler secret put WEBHOOK_SECRET
    if ($LASTEXITCODE -ne 0) { Write-Host "[WARN] secret not set — worker stays in backward-compatible mode." -ForegroundColor Yellow }
    else {
        Write-Host "IMPORTANT — register the SAME secret with Telegram (one command):" -ForegroundColor Yellow
        Write-Host '  curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook?url=https://<YOUR_WORKER_URL>/webhook&secret_token=<SAME_SECRET>"' -ForegroundColor White
        Write-Host "Until you run it, Telegram will NOT send the header and /webhook returns 403." -ForegroundColor DarkGray
    }
}

# --- 7) Health check ---------------------------------------------------------
$url = $null
if ($deployOutput -match 'https://[a-z0-9-]+\.[a-z0-9-]+\.workers\.dev') { $url = $Matches[0] }
if ($url) {
    Write-Host ""
    Write-Host "Health check: $url" -ForegroundColor Cyan
    try {
        $health = Invoke-RestMethod -Uri $url -TimeoutSec 20
        Write-Host ("  service: {0}  version: {1}" -f $health.service, $health.version) -ForegroundColor Green
        Write-Host ("  last webhook: {0}" -f $health.last_webhook_at) -ForegroundColor Green
        Write-Host ("  cutover complete: {0}" -f $health.cutover_complete) -ForegroundColor Green
    } catch {
        Write-Host "  [WARN] health endpoint did not answer yet — try the URL in a minute." -ForegroundColor Yellow
    }
} else {
    Write-Host "Health check skipped (no workers.dev URL in deploy output)." -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "Done. Reminders:" -ForegroundColor Cyan
Write-Host " - All secrets and data persisted across this deploy." -ForegroundColor Gray
Write-Host " - Rotate the Telegram bot token (it was exposed in chat) — then:" -ForegroundColor Gray
Write-Host "     npx wrangler secret put BOT_TOKEN" -ForegroundColor Gray
Write-Host " - Live tail while testing:  npx wrangler tail" -ForegroundColor Gray
