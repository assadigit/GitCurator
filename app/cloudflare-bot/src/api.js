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
  activityGetRecent, activityLog,
  installGet, installInsert, installUpdateLastSeen
} from './db.js';
import { cacheGetPairingCode, cacheSetPairingCode, cacheDeletePairingCode } from './kv.js';
import { sendMessage, editMessage, inlineKeyboard } from './telegram.js';
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
    '/api/backfill',
    '/api/banish',
    '/api/scan'
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
    // v0.60.0 — THE CONFIRMATION GATE (desktop <-> owner over Telegram)
    if (path === '/api/banish/propose' && method === 'POST') {
      return handleBanishPropose(request, env);
    }
    if (path === '/api/banish/status' && method === 'GET') {
      return handleBanishStatus(request, env, url);
    }
    if (path === '/api/banish/result' && method === 'POST') {
      return handleBanishResult(request, env);
    }
    // v0.61.0 — THE VAULT SCAN's round-trip (the banish trio's twin:
    // the scan's plan — deletions + moves + new folders — asked over
    // the same Telegram gate; nothing moves until the owner answers)
    if (path === '/api/scan/propose' && method === 'POST') {
      return handleScanPropose(request, env);
    }
    if (path === '/api/scan/status' && method === 'GET') {
      return handleScanStatus(request, env, url);
    }
    if (path === '/api/scan/result' && method === 'POST') {
      return handleScanResult(request, env);
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

  // Recompute signature.
  // v0.25.0 — Fix (HMAC query-string mismatch): the desktop signs the path
  // WITH its query string (cloudflare_sync._make_request signs exactly the
  // `path` argument it was given, e.g. '/api/decommissions?since=…'), but
  // this check used to recompute over url.pathname only (query stripped) —
  // so GET /api/decommissions?since=… could never verify (always 401
  // 'Invalid signature'). Sign over pathname + search, like the client.
  // Endpoints without a query string are unaffected (search === '').
  const method = request.method;
  const u = new URL(request.url);
  const path = u.pathname + u.search;
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
  // or have status=pending in vault_mirror.
  // v0.22.0 — includes websites (url_type='non_github'); the desktop's
  // Websites pipeline processes those into the Websites vault.
  const result = await env.DB.prepare(`
    SELECT l.* FROM ever_seen_ledger l
    LEFT JOIN vault_mirror v ON l.url_normalized = v.url_normalized
    WHERE l.forgotten = 0
      AND (v.status IS NULL OR v.status = 'pending')
      AND l.url_normalized NOT IN (SELECT url_normalized FROM decommission_events)
      AND l.url_normalized NOT IN (SELECT url_normalized FROM dead_letters WHERE resolved = 0 AND reason = 'blocked_domain')
    ORDER BY l.first_seen_at ASC
    LIMIT 100
  `).all();

  const pending = result.results.map(row => ({
    url_normalized: row.url_normalized,
    url_original: row.url_original,
    url_type: row.url_type,
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

  return jsonResponse({
    success: true,
    pending,
    count: pending.length
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
// v0.60.0 — THE CONFIRMATION GATE endpoints
// ========================================
// The desktop's banish gate asks the owner over Telegram before any
// 🗑-marked note is removed (the owner's ask: "it must show 'X number
// of notes should be deleted' do you confirm?"). The ask lives in the
// state table under `banish_confirm:<id>`; the buttons answer over the
// webhook's callback_query (banish_yes / banish_no — webhook.js);
// the desktop polls /status and closes with /result.

const BANISH_CONFIRM_PREFIX = 'banish_confirm:';
const BANISH_ACTIVE_KEY = 'banish_active_confirm';

function _escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

async function _banishConfirmGet(db, id) {
  const raw = await stateGet(db, BANISH_CONFIRM_PREFIX + String(id));
  if (!raw) return null;
  try { return JSON.parse(raw); } catch { return null; }
}

async function _banishConfirmSet(db, id, obj) {
  await stateSet(db, BANISH_CONFIRM_PREFIX + String(id), JSON.stringify(obj));
}

async function handleBanishPropose(request, env) {
  const body = await request.json();
  const { count, items, timeout_s } = body || {};
  if (!count || !Array.isArray(items) || !items.length) {
    return jsonResponse({ error: 'Missing count or items' }, 400);
  }
  const allowed = (env.ALLOWED_USER_IDS || '').split(',')[0].trim();
  if (!allowed) {
    return jsonResponse({ error: 'No allowed chat configured' }, 400);
  }
  const chatId = parseInt(allowed, 10);

  // v0.60.0 — supersede the previous pending ask (one live question at
  // a time; a stale ask's message says so, its buttons answer 'expired')
  const prevId = await stateGet(env.DB, BANISH_ACTIVE_KEY);
  if (prevId) {
    const prev = await _banishConfirmGet(env.DB, prevId);
    if (prev && prev.status === 'pending') {
      prev.status = 'superseded';
      prev.resolved_at = now();
      await _banishConfirmSet(env.DB, prevId, prev);
      try {
        await editMessage(env, prev.chat_id, prev.message_id,
          `⌛ <b>Superseded</b> — a newer deletion review replaced this ask. ` +
          `Nothing was deleted from it.`);
      } catch (err) {
        console.error('banish supersede edit error:', err);
      }
    }
  }

  const n = parseInt(count, 10) || items.length;
  const shown = items.slice(0, 20).map((it, i) =>
    `${i + 1}. <b>${_escapeHtml(it.title || it.url)}</b>\n` +
    `   <code>${_escapeHtml(it.url)}</code>`);
  if (items.length > 20) {
    shown.push(`… +${items.length - 20} more`);
  }
  const text =
    `🗑️ <b>Deletion review — ${n} note${n === 1 ? '' : 's'} marked for deletion</b>\n\n` +
    `The vault scan found <b>${n}</b> note${n === 1 ? '' : 's'} carrying your ` +
    `delete mark (🗑️ / delete / auto_delete):\n\n` +
    shown.join('\n') +
    `\n\nConfirm, and they leave your vault and the GitHub backup — ` +
    `and never get fetched again.\n` +
    `Keep them, and the marks stay for the next run.`;
  const id = uuid();
  const keyboard = inlineKeyboard([[
    { text: `🗑️ Delete all ${n}`, callback_data: `banish_yes:${id}` },
    { text: `✋ Keep all ${n}`, callback_data: `banish_no:${id}` }
  ]]);
  const msg = await sendMessage(env, chatId, text, {
    reply_markup: keyboard, disable_web_page_preview: true });
  if (!msg) {
    return jsonResponse({ error: 'Telegram send failed' }, 502);
  }
  await _banishConfirmSet(env.DB, id, {
    status: 'pending',
    count: n,
    items: items.map(it => ({
      url: it.url, title: it.title, marker: it.marker, door: it.door })),
    timeout_s: timeout_s || 300,
    chat_id: chatId,
    message_id: msg.message_id,
    created_at: now()
  });
  await stateSet(env.DB, BANISH_ACTIVE_KEY, id);
  await activityLog(env.DB, 'banish_gate', null,
    `Deletion review proposed: ${n} note(s) awaiting the owner's confirmation`);

  return jsonResponse({ success: true, id, message_id: msg.message_id });
}

async function handleBanishStatus(request, env, url) {
  const id = url.searchParams.get('id');
  if (!id) {
    return jsonResponse({ error: 'Missing id' }, 400);
  }
  const obj = await _banishConfirmGet(env.DB, id);
  if (!obj) {
    return jsonResponse({ error: 'Unknown confirmation id' }, 404);
  }
  return jsonResponse({
    success: true, id,
    status: obj.status || 'pending',
    count: obj.count || 0
  });
}

async function handleBanishResult(request, env) {
  const body = await request.json();
  const { id, outcome, deleted } = body || {};
  if (!id || !outcome) {
    return jsonResponse({ error: 'Missing id or outcome' }, 400);
  }
  const obj = await _banishConfirmGet(env.DB, id);
  if (!obj) {
    return jsonResponse({ error: 'Unknown confirmation id' }, 404);
  }
  let text;
  if (outcome === 'deleted') {
    // the owner's example wording: "10 Websites Removed and will never
    // fetch again because you …"
    const n = parseInt(deleted, 10) || 0;
    text =
      `🗑️ <b>${n} website${n === 1 ? '' : 's'} removed — never to be ` +
      `fetched again.</b> You confirmed the deletion; the notes rest in ` +
      `.trash/banished (recoverable by hand), the URLs are blacklisted, ` +
      `and the record rows (♻️ undo) are in the master table.`;
    obj.status = 'done';
  } else if (outcome === 'timeout') {
    text =
      `⌛ <b>No answer in time</b> — nothing deleted. The ${obj.count} ` +
      `mark(s) stay; I'll ask again on the next run.`;
    obj.status = 'timeout';
  } else {
    text = `👌 <b>Kept</b> — nothing deleted. The marks stay; I'll ask again on the next run.`;
    obj.status = obj.status === 'pending' ? 'declined' : obj.status;
  }
  obj.resolved_at = obj.resolved_at || now();
  await _banishConfirmSet(env.DB, id, obj);
  try {
    await editMessage(env, obj.chat_id, obj.message_id, text);
  } catch (err) {
    console.error('banish result edit error:', err);
  }
  await activityLog(env.DB, 'banish_gate', null,
    `Deletion review closed (${outcome}): ${deleted || 0} removed`);

  return jsonResponse({ success: true, status: obj.status });
}

// ========================================
// v0.61.0 — THE VAULT SCAN's round-trip (the banish trio's twin)
//
// The desktop's scan asks the owner over Telegram before ANY note is
// deleted or moved: the ask carries the whole plan (the deletions the
// owner's own marks asked for + the LLM's filing proposal) and the two
// buttons (scan_yes / scan_no — webhook.js); the desktop polls
// /api/scan/status and closes with /api/scan/result. Nothing moves
// until the owner answers; the 300s default is a safe defer.
// ========================================

const SCAN_CONFIRM_PREFIX = 'scan_confirm:';
const SCAN_ACTIVE_KEY = 'scan_active_confirm';

async function _scanConfirmGet(db, id) {
  const raw = await stateGet(db, SCAN_CONFIRM_PREFIX + String(id));
  if (!raw) return null;
  try { return JSON.parse(raw); } catch { return null; }
}

async function _scanConfirmSet(db, id, obj) {
  await stateSet(db, SCAN_CONFIRM_PREFIX + String(id), JSON.stringify(obj));
}

async function handleScanPropose(request, env) {
  const body = await request.json();
  const { deletions, moves, new_folders, items, summary, timeout_s } = body || {};
  if (!Array.isArray(items) || !items.length) {
    return jsonResponse({ error: 'Missing items' }, 400);
  }
  const allowed = (env.ALLOWED_USER_IDS || '').split(',')[0].trim();
  if (!allowed) {
    return jsonResponse({ error: 'No allowed chat configured' }, 400);
  }
  const chatId = parseInt(allowed, 10);

  // one live scan ask at a time (the banish gate's supersede law)
  const prevId = await stateGet(env.DB, SCAN_ACTIVE_KEY);
  if (prevId) {
    const prev = await _scanConfirmGet(env.DB, prevId);
    if (prev && prev.status === 'pending') {
      prev.status = 'superseded';
      prev.resolved_at = now();
      await _scanConfirmSet(env.DB, prevId, prev);
      try {
        await editMessage(env, prev.chat_id, prev.message_id,
          `⌛ <b>Superseded</b> — a newer vault scan review replaced this ` +
          `ask. Nothing was applied from it.`);
      } catch (err) {
        console.error('scan supersede edit error:', err);
      }
    }
  }

  const nDel = parseInt(deletions, 10) || 0;
  const nMove = parseInt(moves, 10) || 0;
  const nFolder = parseInt(new_folders, 10) || 0;
  const shown = items.slice(0, 20).map((it, i) => {
    const kind = it.kind === 'move' ? '📦 move' : '🗑️ delete';
    return `${i + 1}. ${kind} — <b>${_escapeHtml(it.title || '')}</b>\n` +
      `   <code>${_escapeHtml(it.detail || '')}</code>` +
      (it.marker ? `\n   <i>${_escapeHtml(String(it.marker).slice(0, 120))}</i>` : '');
  });
  if (items.length > 20) {
    shown.push(`… +${items.length - 20} more`);
  }
  const parts = [];
  if (nDel) parts.push(`<b>${nDel}</b> note${nDel === 1 ? '' : 's'} marked for deletion`);
  if (nMove) parts.push(`<b>${nMove}</b> move suggestion${nMove === 1 ? '' : 's'}`);
  if (nFolder) parts.push(`<b>${nFolder}</b> new folder${nFolder === 1 ? '' : 's'}`);
  const text =
    `🗂️ <b>Vault scan review</b>\n\n` +
    `The scan read your vault and proposes: ${parts.join(' · ') || 'nothing'}.\n\n` +
    (summary ? `<i>${_escapeHtml(String(summary).slice(0, 300))}</i>\n\n` : '') +
    shown.join('\n') +
    `\n\nApply, and the deletions leave for <code>.trash/banished</code> ` +
    `(never fetched again) and the moves re-file the notes — nothing is ` +
    `rewritten, only moved.\n` +
    `Keep everything, and the vault stays byte-for-byte as it is.`;
  const id = uuid();
  const keyboard = inlineKeyboard([[
    { text: `🗂️ Apply plan`, callback_data: `scan_yes:${id}` },
    { text: `✋ Keep everything`, callback_data: `scan_no:${id}` }
  ]]);
  const msg = await sendMessage(env, chatId, text, {
    reply_markup: keyboard, disable_web_page_preview: true });
  if (!msg) {
    return jsonResponse({ error: 'Telegram send failed' }, 502);
  }
  await _scanConfirmSet(env.DB, id, {
    status: 'pending',
    deletions: nDel, moves: nMove, new_folders: nFolder,
    items: items.map(it => ({
      kind: it.kind, title: it.title, detail: it.detail, marker: it.marker })),
    summary: String(summary || '').slice(0, 400),
    timeout_s: timeout_s || 300,
    chat_id: chatId,
    message_id: msg.message_id,
    created_at: now()
  });
  await stateSet(env.DB, SCAN_ACTIVE_KEY, id);
  await activityLog(env.DB, 'scan_gate', null,
    `Vault scan review proposed: ${nDel} deletion(s), ${nMove} move(s), ` +
    `${nFolder} new folder(s) awaiting the owner's confirmation`);

  return jsonResponse({ success: true, id, message_id: msg.message_id });
}

async function handleScanStatus(request, env, url) {
  const id = url.searchParams.get('id');
  if (!id) {
    return jsonResponse({ error: 'Missing id' }, 400);
  }
  const obj = await _scanConfirmGet(env.DB, id);
  if (!obj) {
    return jsonResponse({ error: 'Unknown confirmation id' }, 404);
  }
  return jsonResponse({
    success: true, id,
    status: obj.status || 'pending',
    deletions: obj.deletions || 0, moves: obj.moves || 0,
    new_folders: obj.new_folders || 0
  });
}

async function handleScanResult(request, env) {
  const body = await request.json();
  const { id, outcome, applied, moved, folders } = body || {};
  if (!id || !outcome) {
    return jsonResponse({ error: 'Missing id or outcome' }, 400);
  }
  const obj = await _scanConfirmGet(env.DB, id);
  if (!obj) {
    return jsonResponse({ error: 'Unknown confirmation id' }, 404);
  }
  let text;
  if (outcome === 'applied') {
    const n = parseInt(applied, 10) || 0;
    const m = parseInt(moved, 10) || 0;
    const f = parseInt(folders, 10) || 0;
    text =
      `🗂️ <b>Plan applied</b> — ${n} note${n === 1 ? '' : 's'} removed ` +
      `(resting in .trash/banished, never to be fetched again), ` +
      `${m} note${m === 1 ? '' : 's'} re-filed, ${f} new folder${f === 1 ? '' : 's'} ` +
      `created. Nothing was rewritten — moves only.`;
    obj.status = 'done';
  } else if (outcome === 'timeout') {
    text =
      `⌛ <b>No answer in time</b> — nothing moved, nothing deleted. ` +
      `The vault stays exactly as it is; I'll ask again on the next scan.`;
    obj.status = 'timeout';
  } else {
    text = `👌 <b>Kept everything</b> — nothing moved, nothing deleted. The vault stays as it is.`;
    obj.status = obj.status === 'pending' ? 'declined' : obj.status;
  }
  obj.resolved_at = obj.resolved_at || now();
  await _scanConfirmSet(env.DB, id, obj);
  try {
    await editMessage(env, obj.chat_id, obj.message_id, text);
  } catch (err) {
    console.error('scan result edit error:', err);
  }
  await activityLog(env.DB, 'scan_gate', null,
    `Vault scan review closed (${outcome}): ${applied || 0} removed, ` +
    `${moved || 0} moved, ${folders || 0} folder(s)`);

  return jsonResponse({ success: true, status: obj.status });
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
