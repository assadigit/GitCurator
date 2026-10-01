// ========================================
// index.js — Main Cloudflare Worker entry
// ========================================
// Exports: fetch (HTTP), queue (consumer), scheduled (cron)

import { handleWebhook } from './webhook.js';
import { handleQueue, handleDeadLetterQueue } from './queue-consumer.js';
import { handleApi } from './api.js';
import { handleScheduled } from './cron.js';
import { handleCors, jsonResponse } from './utils.js';
import { stateGet, stateSet } from './db.js';
import { getDashboardHtml } from './dashboard-html.js';
import { WORKER_VERSION } from './version.js';

// ========================================
// Main Worker
// ========================================

export default {
  // ========================================
  // HTTP fetch handler (webhook + API + dashboard)
  // ========================================
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // CORS preflight
    if (request.method === 'OPTIONS') {
      return handleCors();
    }

    // ========================================
    // Telegram webhook
    // ========================================
    if (url.pathname === '/webhook' && request.method === 'POST') {
      // v30 — Fix (webhook impersonation): when WEBHOOK_SECRET is set (via
      // `npx wrangler secret put WEBHOOK_SECRET` + registering the webhook
      // with setWebhook?...&secret_token=...), Telegram echoes it in the
      // X-Telegram-Bot-Api-Secret-Token header. Reject anything else —
      // previously ANYONE who discovered the workers.dev URL could POST
      // forged updates and act as the allowlisted user.
      // Backward compatible: without WEBHOOK_SECRET the check is skipped.
      if (env.WEBHOOK_SECRET) {
        const provided = request.headers.get('X-Telegram-Bot-Api-Secret-Token');
        if (provided !== env.WEBHOOK_SECRET) {
          return new Response('Forbidden', { status: 403 });
        }
      }
      return handleWebhook(request, env);
    }

    // ========================================
    // Health check
    // ========================================
    if (url.pathname === '/health' || url.pathname === '/') {
      const lastWebhook = await stateGet(env.DB, 'last_webhook_at');
      const cutoverComplete = await stateGet(env.DB, 'cutover_complete');

      return jsonResponse({
        status: 'ok',
        service: 'github-curator-bot',
        version: WORKER_VERSION,
        last_webhook_at: lastWebhook,
        cutover_complete: cutoverComplete === '1',
        timestamp: new Date().toISOString()
      });
    }

    // ========================================
    // Dashboard (HTML — served directly, no build step)
    // ========================================
    if (url.pathname === '/dashboard' || url.pathname === '/dashboard/') {
      return getDashboardHtml(env);
    }

    // /auth redirects to /dashboard (for old magic links)
    if (url.pathname === '/auth' || url.pathname === '/auth/') {
      const token = url.searchParams.get('token');
      const redirectUrl = token ? `/dashboard?token=${encodeURIComponent(token)}` : '/dashboard';
      return Response.redirect(new URL(redirectUrl, request.url).toString(), 302);
    }

    // ========================================
    // API endpoints (desktop + dashboard)
    // ========================================
    if (url.pathname.startsWith('/api/')) {
      return handleApi(request, env, url);
    }

    // ========================================
    // 404
    // ========================================
    return jsonResponse({
      error: 'Not found',
      path: url.pathname,
      hint: 'Available: /webhook, /health, /dashboard, /api/*'
    }, 404);
  },

  // ========================================
  // Queue consumers (7-day retry buffer + the DLQ drain)
  // ========================================
  async queue(batch, env) {
    // v0.25.0 — no link left behind, the last leg: messages that exhausted
    // their retries land in curator-ingest-dlq. That queue now HAS a
    // consumer (see wrangler.toml) which records every URL it still can
    // into the dead_letters table (D1, permanent) and acks — it never
    // reprocesses, so a poison message can never loop forever.
    if (batch.queue === 'curator-ingest-dlq') {
      return handleDeadLetterQueue(batch, env);
    }
    return handleQueue(batch, env);
  },

  // ========================================
  // Cron triggers (scheduled jobs)
  // ========================================
  async scheduled(event, env, ctx) {
    ctx.waitUntil(handleScheduled(event, env));
  }
};
