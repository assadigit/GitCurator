// ========================================
// api.js — API endpoint router
// ========================================
// Desktop-facing + dashboard-facing API endpoints.
// HMAC-authenticated for desktop, cookie-auth for dashboard.

import { jsonResponse, now, uuid, hmacSha256, sha256, constantTimeCompare } from './utils.js';
import {
  stateGet, stateSet, getStats,
  ledgerGetByUrl, mirrorGetByUrl, mirrorGetByStatus, mirrorSearch, mirrorCount,
  decommissionGetSince, decommissionGetAll, decommissionInsert,
  deadLetterGetAll,
  errorGetRecent, errorGetUnackCount, errorAcknowledgeAll,
  gdriveGetRecent,
  activityGetRecent,
  installGet, installInsert, installUpdateLastSeen
} from './db.js';
import { cacheGetPairingCode, cacheSetPairingCode, cacheDeletePairingCode } from './kv.js';
import { sendMessage } from './telegram.js';
import { handleDashboardApi } from './dashboard-api.js';

// ========================================
// Main API router
// ========================================

export async function handleApi(request, env, url) {
  const path = url.pathname;
  const method = request.method;

  // ========================================
  // Public endpoints (no auth)
  // ========================================

  if (path === '/api/health') {
    return jsonResponse({ status: 'ok', timestamp: now() });
  }

  // ========================================
  // HMAC-authenticated desktop endpoints
  // ========================================

  const desktopEndpoints = [
    '/api/pending',
    '/api/verify',
    '/api/decommissions',
    '/api/vault_mirror/push',
    '/api/decommission',
    '/api/errors',
    '/api/gdrive/backup_status',
    '/api/pair',
    '/api/backfill'
  ];

  if (desktopEndpoints.some(ep => path.startsWith(ep))) {
    // Pair endpoint is special — uses pairing code, not HMAC
    if (path === '/api/pair' && method === 'POST') {
      return handlePair(request, env);
    }

    // All other desktop endpoints require HMAC
    const authResult = await verifyHmac(request, env);
    if (!authResult.ok) {
      return jsonResponse({ error: authResult.error }, 401);
    }

    // Route to handler
    if (path === '/api/pending' && method === 'GET') {
      return handleGetPending(request, env);
    }
    if (path === '/api/verify' && method === 'GET') {
      return handleVerify(request, env);
    }
    if (path === '/api/decommissions' && method === 'GET') {
      return handleGetDecommissions(request, env, url);
    }
    if (path === '/api/vault_mirror/push' && method === 'POST') {
      return handleVaultMirrorPush(request, env);
    }
    if (path === '/api/decommission' && method === 'POST') {
      return handleDecommission(request, env);
    }
    if (path === '/api/errors' && method === 'POST') {
      return handleErrorsPush(request, env);
    }
    if (path === '/api/gdrive/backup_status' && method === 'POST') {
      return handleGdriveBackupStatus(request, env);
    }
    if (path === '/api/backfill' && method === 'POST') {
      return handleBackfill(request, env);
    }

    return jsonResponse({ error: 'Method not allowed' }, 405);
  }

  // ========================================
  // Dashboard endpoints (cookie-auth) — Layer 13
  // ========================================

  if (path.startsWith('/api/dashboard/') || path.startsWith('/api/auth/')) {
    return handleDashboardApi(request, env, url);
  }

  return jsonResponse({ error: 'Not found', path }, 404);
}

// ========================================
// HMAC verification
// ========================================

async function verifyHmac(request, env) {
  const installId = request.headers.get('X-Auth-Install');
  const timestamp = request.headers.get('X-Auth-Timestamp');
  const signature = request.headers.get('X-Auth-Signature');

  if (!installId || !timestamp || !signature) {
    return { ok: false, error: 'Missing auth headers' };
  }

  // Check timestamp (5-min window)
  const nowMs = Date.now();
  const tsMs = parseInt(timestamp);
  if (isNaN(tsMs) || Math.abs(nowMs - tsMs) > 5 * 60 * 1000) {
    return { ok: false, error: 'Timestamp out of range' };
  }

  // Look up install
  const install = await installGet(env.DB, installId);
  if (!install) {
    return { ok: false, error: 'Unknown install' };
  }

  // Recompute signature
  const method = request.method;
  const path = new URL(request.url).pathname;
  const bodyText = await request.clone().text();
  const bodyHash = await sha256(bodyText);
  const message = `${method}\n${path}\n${timestamp}\n${bodyHash}`;
  const expectedSig = await hmacSha256(install.shared_secret_hash, message);

  // Constant-time compare
  if (!constantTimeCompare(signature, expectedSig)) {
    return { ok: false, error: 'Invalid signature' };
  }

  // Update last seen
  await installUpdateLastSeen(env.DB, installId);

  return { ok: true, installId };
}

// ========================================
// Pairing flow
// ========================================

async function handlePair(request, env) {
  const body = await request.json();
  const { code } = body;

  if (!code) {
    return jsonResponse({ error: 'Missing pairing code' }, 400);
  }

  // Look up pairing code in KV
  const pairingData = await cacheGetPairingCode(env.CACHE, code);
  if (!pairingData) {
    return jsonResponse({ error: 'Invalid or expired pairing code' }, 401);
  }

  // Generate install_id and shared_secret
  const installId = uuid();
  const sharedSecret = uuid() + uuid(); // 64-char hex
  const secretHash = await sha256(sharedSecret);

  // Store install
  await installInsert(env.DB, installId, secretHash);

  // Delete pairing code (single-use)
  await cacheDeletePairingCode(env.CACHE, code);

  return jsonResponse({
    success: true,
    install_id: installId,
    shared_secret: sharedSecret
  });
}

// ========================================
// GET /api/pending — return pending links
// ========================================

async function handleGetPending(request, env) {
  // Update desktop last poll
  await stateSet(env.DB, 'desktop_last_poll', now());

  // Get pending links from ledger that don't have a vault_mirror entry
  // or have status=pending in vault_mirror
  const result = await env.DB.prepare(`
    SELECT l.* FROM ever_seen_ledger l
    LEFT JOIN vault_mirror v ON l.url_normalized = v.url_normalized
    WHERE l.forgotten = 0
      AND l.url_type = 'github'
      AND (v.status IS NULL OR v.status = 'pending')
      AND l.url_normalized NOT IN (SELECT url_normalized FROM decommission_events)
      AND l.url_normalized NOT IN (SELECT url_normalized FROM dead_letters WHERE resolved = 0)
    ORDER BY l.first_seen_at ASC
    LIMIT 100
  `).all();

  const pending = result.results.map(row => ({
    url_normalized: row.url_normalized,
    url_original: row.url_original,
    github_owner: row.github_owner,
    github_repo: row.github_repo,
    github_metadata: row.github_stars ? {
      stars: row.github_stars,
      description: row.github_description,
      language: row.github_language,
      topics: row.github_topics ? JSON.parse(row.github_topics) : [],
      readme_excerpt: row.github_readme_excerpt
    } : null,
    first_seen_at: row.first_seen_at,
    forward_count: row.forward_count,
    ledger_id: row.id
  }));

  const cutoverComplete = await stateGet(env.DB, 'cutover_complete');

  return jsonResponse({
    success: true,
    pending,
    count: pending.length,
    cutover_complete: cutoverComplete === '1'
  });
}

// ========================================
// GET /api/verify — full reconciliation
// ========================================

async function handleVerify(request, env) {
  const stats = await getStats(env.DB);

  const ledgerHash = await env.DB.prepare(
    'SELECT GROUP_CONCAT(url_normalized, "|") as concat FROM ever_seen_ledger WHERE forgotten = 0'
  ).first();

  const mirrorHash = await env.DB.prepare(
    'SELECT GROUP_CONCAT(url_normalized, "|") as concat FROM vault_mirror'
  ).first();

  const ledgerSha = ledgerHash?.concat ? await sha256(ledgerHash.concat) : 'empty';
  const mirrorSha = mirrorHash?.concat ? await sha256(mirrorHash.concat) : 'empty';

  return jsonResponse({
    success: true,
    stats: {
      total_ledger: stats.total,
      in_vault: stats.inVault,
      pending: stats.pending,
      decommissioned: stats.decommissioned,
      dead_letters: stats.deadLetters,
      non_github: stats.byType.non_github || 0
    },
    ledger_hash: ledgerSha,
    vault_mirror_hash: mirrorSha
  });
}

// ========================================
// GET /api/decommissions?since=
// ========================================

async function handleGetDecommissions(request, env, url) {
  const since = url.searchParams.get('since') || '1970-01-01T00:00:00Z';
  const result = await decommissionGetSince(env.DB, since, 200);

  return jsonResponse({
    success: true,
    decommissions: result.results,
    count: result.results.length
  });
}

// ========================================
// POST /api/vault_mirror/push — idempotent batch push
// ========================================

async function handleVaultMirrorPush(request, env) {
  const body = await request.json();
  const { sync_id, entries, decommission_events } = body;

  if (!sync_id || !entries) {
    return jsonResponse({ error: 'Missing sync_id or entries' }, 400);
  }

  // Idempotency check: if this sync_id was already processed, return cached result
  const existingSync = await env.DB.prepare(
    'SELECT value FROM sync_state WHERE key = ?'
  ).bind(`sync_processed:${sync_id}`).first();

  if (existingSync) {
    return jsonResponse({
      success: true,
      synced_entries: 0,
      synced_decomm: 0,
      conflicts: [],
      vault_mirror_total: 0,
      idempotent: true
    });
  }

  // Process vault_mirror entries
  const { mirrorUpsert } = await import('./db.js');
  let syncedEntries = 0;
  for (const entry of entries) {
    await mirrorUpsert(env.DB, entry, sync_id);
    syncedEntries++;
  }

  // Process decommission events
  let syncedDecomm = 0;
  if (decommission_events) {
    for (const evt of decommission_events) {
      await decommissionInsert(env.DB, evt.url_normalized, 'desktop', evt.reason, evt.details, sync_id);
      syncedDecomm++;
    }
  }

  // Mark sync as processed
  await stateSet(env.DB, `sync_processed:${sync_id}`, JSON.stringify({
    entries: syncedEntries,
    decommissions: syncedDecomm,
    timestamp: now()
  }));

  // Update desktop last sync
  await stateSet(env.DB, 'desktop_last_sync', now());

  // Update vault index hash
  if (body.vault_index_hash) {
    await stateSet(env.DB, 'vault_index_hash', body.vault_index_hash);
    await stateSet(env.DB, 'vault_index_entry_count', String(entries.length));
  }

  const totalMirror = await env.DB.prepare(
    'SELECT COUNT(*) as count FROM vault_mirror'
  ).first();

  return jsonResponse({
    success: true,
    synced_entries: syncedEntries,
    synced_decomm: syncedDecomm,
    conflicts: [],
    vault_mirror_total: totalMirror.count
  });
}

// ========================================
// POST /api/decommission — real-time 404 from desktop
// ========================================

async function handleDecommission(request, env) {
  const body = await request.json();
  const { url_normalized, reason, details } = body;

  if (!url_normalized || !reason) {
    return jsonResponse({ error: 'Missing url_normalized or reason' }, 400);
  }

  await decommissionInsert(env.DB, url_normalized, 'desktop', reason, details, null);

  return jsonResponse({ success: true, decommissioned: true });
}

// ========================================
// POST /api/errors — desktop error reporting
// ========================================

async function handleErrorsPush(request, env) {
  const body = await request.json();
  const { errors } = body;

  if (!errors || !Array.isArray(errors)) {
    return jsonResponse({ error: 'Missing errors array' }, 400);
  }

  const { errorInsert } = await import('./db.js');
  let dmSent = 0;

  for (const error of errors) {
    const errorId = await errorInsert(env.DB, error);

    // CRITICAL errors → DM immediately
    if (error.severity === 'CRITICAL') {
      const allowed = (env.ALLOWED_USER_IDS || '').split(',')[0].trim();
      if (allowed) {
        await sendMessage(env, parseInt(allowed),
          `🔴 <b>CRITICAL</b>: ${error.error_code}\n\n${error.message}`
        );
        dmSent++;
      }
    }

    // Log activity
    const { activityLog } = await import('./db.js');
    await activityLog(env.DB, 'error', null, `${error.severity}: ${error.error_code} — ${error.message}`);
  }

  return jsonResponse({ success: true, received: errors.length, dm_sent: dmSent });
}

// ========================================
// POST /api/gdrive/backup_status
// ========================================

async function handleGdriveBackupStatus(request, env) {
  const body = await request.json();
  const { gdriveInsert } = await import('./db.js');

  await gdriveInsert(env.DB, body);
  await stateSet(env.DB, 'gdrive_last_backup', body.created_at);
  await stateSet(env.DB, 'gdrive_auth_status', 'ok');

  return jsonResponse({ success: true });
}

// ========================================
// POST /api/backfill — idempotent chunked backfill
// ========================================

async function handleBackfill(request, env) {
  const body = await request.json();
  const { entries, chunk_num, total_chunks } = body;

  if (!entries || !Array.isArray(entries)) {
    return jsonResponse({ error: 'Missing entries array' }, 400);
  }

  const { ledgerInsert, ledgerGetByUrl, ledgerUpdateForward } = await import('./db.js');
  let inserted = 0;
  let skipped = 0;

  for (const entry of entries) {
    const existing = await ledgerGetByUrl(env.DB, entry.url_normalized);
    if (existing) {
      // Idempotent — skip
      skipped++;
      continue;
    }

    await ledgerInsert(env.DB, {
      url_normalized: entry.url_normalized,
      url_original: entry.url_original,
      url_type: entry.url_type || 'github',
      github_owner: entry.github_owner || null,
      github_repo: entry.github_repo || null,
      first_seen_at: entry.first_seen_at || now(),
      last_seen_at: now(),
      telegram_message_id: null,
      telegram_chat_id: null
    });
    inserted++;
  }

  // Update backfill progress
  const progress = await stateGet(env.DB, 'backfill_progress');
  const progressObj = progress ? JSON.parse(progress) : { total: 0, pushed: 0, last_chunk: 0 };
  progressObj.pushed += inserted;
  progressObj.last_chunk = chunk_num;
  progressObj.total_chunks = total_chunks;
  await stateSet(env.DB, 'backfill_progress', JSON.stringify(progressObj));

  return jsonResponse({
    success: true,
    inserted,
    skipped,
    progress: progressObj
  });
}
