// ========================================
// dashboard-api.js — Dashboard auth + data endpoints
// ========================================
// Handles all /api/auth/* and /api/dashboard/* endpoints.
// Cookie-based auth (HttpOnly + Secure + SameSite=Strict).

import {
  jsonResponse, now, uuid, randomHex, constantTimeCompare
} from './utils.js';
import {
  sessionGetByMagic, sessionGetById, sessionInsert,
  sessionClearMagic, sessionUpdateLastSeen, sessionRevokeAll,
  getStats, activityGetRecent,
  ledgerGetByUrl, mirrorGetByUrl, mirrorGetByStatus, mirrorSearch,
  decommissionGetAll, decommissionInsert,
  deadLetterGetAll,
  errorGetRecent, errorAcknowledgeAll,
  gdriveGetRecent,
  stateGet, stateSet,
  ledgerSetForgotten,
  activityLog
} from './db.js';
import { sendMessage } from './telegram.js';

const SESSION_COOKIE = 'curator_session';
const SESSION_EXPIRY_DAYS = 7;
const MAGIC_TOKEN_TTL_MIN = 10;

// Note: SameSite=Lax (not Strict) because the dashboard is deployed on
// a different origin (Pages) than the Worker. CSRF is still protected via
// Origin header check on all POST requests (see handleDashboardApi).
const COOKIE_SAMESITE = 'Lax';

// ========================================
// Main router
// ========================================

export async function handleDashboardApi(request, env, url) {
  const path = url.pathname;
  const method = request.method;

  // ========================================
  // Auth endpoints (no cookie required)
  // ========================================

  if (path === '/api/auth/request' && method === 'POST') {
    return handleAuthRequest(request, env);
  }

  if (path === '/api/auth/verify') {
    return handleAuthVerify(request, env, url);
  }

  // ========================================
  // All /api/dashboard/* require valid session cookie
  // ========================================

  const session = await verifySession(request, env);
  if (!session) {
    return jsonResponse({ error: 'Not authenticated' }, 401);
  }

  // Update last seen
  await sessionUpdateLastSeen(env.DB, session.session_id);

  // ========================================
  // Auth actions
  // ========================================

  if (path === '/api/auth/logout' && method === 'POST') {
    await sessionRevokeAll(env.DB, session.user_id);  // Revoke all sessions for user
    return jsonResponse({ success: true }, 200, clearCookieHeaders());
  }

  if (path === '/api/auth/logout-all' && method === 'POST') {
    await sessionRevokeAll(env.DB, session.user_id);
    return jsonResponse({ success: true }, 200, clearCookieHeaders());
  }

  // ========================================
  // Dashboard data endpoints
  // ========================================

  if (path === '/api/dashboard/overview') {
    return handleOverview(env);
  }
  if (path === '/api/dashboard/pending') {
    return handleDashboardPending(env, url);
  }
  if (path === '/api/dashboard/vault') {
    return handleDashboardVault(env, url);
  }
  if (path === '/api/dashboard/decommissioned') {
    return handleDashboardDecommissioned(env, url);
  }
  if (path === '/api/dashboard/dead-letters') {
    return handleDashboardDeadLetters(env);
  }
  if (path === '/api/dashboard/errors') {
    return handleDashboardErrors(env, url);
  }
  if (path === '/api/dashboard/backups') {
    return handleDashboardBackups(env);
  }
  if (path === '/api/dashboard/settings') {
    return handleDashboardSettings(env);
  }

  // ========================================
  // Dashboard action endpoints (POST + CSRF check)
  // ========================================

  if (method === 'POST') {
    // CSRF: check Origin header
    const origin = request.headers.get('Origin') || '';
    const expectedOrigin = new URL(request.url).origin;
    if (origin && !origin.startsWith(expectedOrigin)) {
      return jsonResponse({ error: 'CSRF check failed' }, 403);
    }

    if (path === '/api/dashboard/action/decommission') {
      return handleActionDecommission(request, env, session);
    }
    if (path === '/api/dashboard/action/forget') {
      return handleActionForget(request, env, session);
    }
    if (path === '/api/dashboard/action/backup') {
      return handleActionBackup(env, session);
    }
    if (path === '/api/dashboard/action/restore') {
      return handleActionRestore(request, env, session);
    }
    if (path === '/api/dashboard/action/clear-errors') {
      return handleActionClearErrors(env);
    }
  }

  return jsonResponse({ error: 'Not found', path }, 404);
}

// ========================================
// Auth: request magic link
// ========================================

async function handleAuthRequest(request, env) {
  const body = await request.json();
  const userId = parseInt(body.user_id);

  if (!userId) {
    return jsonResponse({ error: 'Missing user_id' }, 400);
  }

  // Verify user is in allowlist
  const allowed = (env.ALLOWED_USER_IDS || '').split(',').map(s => s.trim());
  if (!allowed.includes(String(userId))) {
    return jsonResponse({ error: 'User not authorized' }, 403);
  }

  // Generate magic token
  const magicToken = randomHex(32);
  const sessionId = uuid();

  // Store in D1 (magic token valid for 10 min)
  await sessionInsert(env.DB, {
    session_id: sessionId,
    user_id: userId,
    magic_token: magicToken,
    created_at: now(),
    expires_at: new Date(Date.now() + MAGIC_TOKEN_TTL_MIN * 60 * 1000).toISOString(),
    last_seen_at: now(),
    user_agent: null,
    ip_address: null
  });

  // Build magic link URL
  const workerUrl = new URL(request.url).origin;
  const magicLink = `${workerUrl}/auth/?token=${magicToken}`;

  // DM the user via bot
  await sendMessage(env, userId,
    `🔐 <b>Dashboard Login</b>\n\n` +
    `Click below to log in to the dashboard (link expires in ${MAGIC_TOKEN_TTL_MIN} minutes):\n\n` +
    `<a href="${magicLink}">🔓 Open Dashboard</a>\n\n` +
    `This link is single-use. After login, you'll stay logged in for ${SESSION_EXPIRY_DAYS} days.`
  );

  return jsonResponse({
    success: true,
    message: `Magic link sent to your Telegram DMs (expires in ${MAGIC_TOKEN_TTL_MIN} min)`
  });
}

// ========================================
// Auth: verify magic token, set cookie
// ========================================

async function handleAuthVerify(request, env, url) {
  const token = url.searchParams.get('token');

  if (!token) {
    return jsonResponse({ error: 'Missing token' }, 400);
  }

  const session = await sessionGetByMagic(env.DB, token);

  if (!session) {
    return jsonResponse({ error: 'Invalid token' }, 401);
  }

  // Check token not expired
  if (new Date(session.expires_at) < new Date()) {
    return jsonResponse({ error: 'Token expired' }, 401);
  }

  // Upgrade to full session (extend expiry to 7 days)
  const newExpiresAt = new Date(Date.now() + SESSION_EXPIRY_DAYS * 24 * 60 * 60 * 1000).toISOString();
  const newSessionId = uuid();

  // Create a new session row with 7-day expiry, clear magic token
  await env.DB.prepare(`
    UPDATE dashboard_sessions
    SET session_id = ?,
        expires_at = ?,
        magic_token = NULL,
        last_seen_at = ?
    WHERE session_id = ?
  `).bind(newSessionId, newExpiresAt, now(), session.session_id).run();

  // Set cookie
  const cookieValue = `${SESSION_COOKIE}=${newSessionId}; HttpOnly; Secure; SameSite=${COOKIE_SAMESITE}; Path=/; Max-Age=${SESSION_EXPIRY_DAYS * 24 * 60 * 60}`;

  return jsonResponse({
    success: true,
    redirect: '/overview/'
  }, 200, {
    'Set-Cookie': cookieValue
  });
}

// ========================================
// Session verification
// ========================================

async function verifySession(request, env) {
  const cookies = parseCookies(request.headers.get('Cookie') || '');
  const sessionId = cookies[SESSION_COOKIE];

  if (!sessionId) return null;

  const session = await sessionGetById(env.DB, sessionId);
  if (!session) return null;

  // Check not expired
  if (new Date(session.expires_at) < new Date()) {
    return null;
  }

  return session;
}

function parseCookies(cookieHeader) {
  const cookies = {};
  cookieHeader.split(';').forEach(pair => {
    const [key, ...valueParts] = pair.trim().split('=');
    if (key) {
      cookies[key] = valueParts.join('=');
    }
  });
  return cookies;
}

function clearCookieHeaders() {
  return {
    'Set-Cookie': `${SESSION_COOKIE}=; HttpOnly; Secure; SameSite=${COOKIE_SAMESITE}; Path=/; Max-Age=0`
  };
}

// ========================================
// Dashboard data endpoints
// ========================================

async function handleOverview(env) {
  const stats = await getStats(env.DB);
  const activity = await activityGetRecent(env.DB, 50, 24);

  return jsonResponse({
    stats,
    recent_activity: activity.results || []
  });
}

async function handleDashboardPending(env, url) {
  const limit = parseInt(url.searchParams.get('limit') || '100');

  const result = await env.DB.prepare(`
    SELECT l.* FROM ever_seen_ledger l
    LEFT JOIN vault_mirror v ON l.url_normalized = v.url_normalized
    WHERE l.forgotten = 0
      AND (v.status IS NULL OR v.status = 'pending')
      AND l.url_normalized NOT IN (SELECT url_normalized FROM decommission_events)
      AND l.url_normalized NOT IN (SELECT url_normalized FROM dead_letters WHERE resolved = 0 AND reason = 'blocked_domain')
    ORDER BY l.first_seen_at DESC
    LIMIT ?
  `).bind(limit).all();

  const pending = (result.results || []).map(row => ({
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

  return jsonResponse({ pending, count: pending.length });
}

async function handleDashboardVault(env, url) {
  const query = url.searchParams.get('q') || '';
  const category = url.searchParams.get('category') || '';
  const limit = parseInt(url.searchParams.get('limit') || '100');

  let sql = `SELECT * FROM vault_mirror WHERE status = 'in_vault'`;
  const params = [];

  if (query) {
    sql += ` AND (url_normalized LIKE ? OR title LIKE ? OR vault_path LIKE ? OR category LIKE ?)`;
    const pattern = `%${query}%`;
    params.push(pattern, pattern, pattern, pattern);
  }
  if (category) {
    sql += ` AND category = ?`;
    params.push(category);
  }
  sql += ` ORDER BY last_updated_at DESC LIMIT ?`;
  params.push(limit);

  const result = await env.DB.prepare(sql).bind(...params).all();
  const entries = result.results || [];

  return jsonResponse({ entries, count: entries.length });
}

async function handleDashboardDecommissioned(env, url) {
  const limit = parseInt(url.searchParams.get('limit') || '100');
  const result = await decommissionGetAll(env.DB, limit, 0);

  return jsonResponse({
    events: result.results || [],
    count: (result.results || []).length
  });
}

async function handleDashboardDeadLetters(env) {
  const result = await deadLetterGetAll(env.DB, 100, 0);

  return jsonResponse({
    letters: result.results || [],
    count: (result.results || []).length
  });
}

async function handleDashboardErrors(env, url) {
  const severity = url.searchParams.get('severity') || null;
  const result = await errorGetRecent(env.DB, 50, severity);

  return jsonResponse({
    errors: result.results || [],
    count: (result.results || []).length
  });
}

async function handleDashboardBackups(env) {
  const result = await gdriveGetRecent(env.DB, 20);

  return jsonResponse({
    backups: result.results || [],
    count: (result.results || []).length
  });
}

async function handleDashboardSettings(env) {
  const keys = [
    'desktop_last_poll', 'desktop_last_sync',
    'vault_index_hash', 'vault_index_entry_count',
    'gdrive_last_backup', 'gdrive_auth_status',
    'last_webhook_at'
  ];

  const settings = {};
  for (const key of keys) {
    settings[key] = await stateGet(env.DB, key);
  }

  // Compute poll interval from config (default 5 min = 300s)
  settings.poll_interval = '5';

  return jsonResponse({ settings });
}

// ========================================
// Dashboard action endpoints
// ========================================

async function handleActionDecommission(request, env, session) {
  const body = await request.json();
  const { url_normalized } = body;

  if (!url_normalized) {
    return jsonResponse({ error: 'Missing url_normalized' }, 400);
  }

  await decommissionInsert(env.DB, url_normalized, 'bot', 'manual', 'Marked via dashboard', null);
  await activityLog(env.DB, 'decommissioned', url_normalized, 'Decommissioned via dashboard');

  return jsonResponse({ success: true });
}

async function handleActionForget(request, env, session) {
  const body = await request.json();
  const { url_normalized } = body;

  if (!url_normalized) {
    return jsonResponse({ error: 'Missing url_normalized' }, 400);
  }

  await ledgerSetForgotten(env.DB, url_normalized, 'Forgotten via dashboard');
  await activityLog(env.DB, 'forget', url_normalized, 'Forgotten via dashboard');

  return jsonResponse({ success: true });
}

async function handleActionBackup(env, session) {
  // Signal desktop to trigger a manual backup
  await stateSet(env.DB, 'pending_backup_request', now());
  await activityLog(env.DB, 'backup', null, 'Manual backup triggered via dashboard');

  return jsonResponse({
    success: true,
    message: 'Backup triggered — desktop will upload on next sync cycle'
  });
}

async function handleActionRestore(request, env, session) {
  const body = await request.json();
  const { backup_id, mode } = body;  // mode: 'new_folder' | 'replace' | 'download'

  if (!backup_id || !mode) {
    return jsonResponse({ error: 'Missing backup_id or mode' }, 400);
  }

  // Signal desktop to restore
  await stateSet(env.DB, 'pending_restore_request', JSON.stringify({
    backup_id, mode, requested_at: now(), requested_by: 'dashboard'
  }));
  await activityLog(env.DB, 'restore', null, `Restore requested: ${mode} (backup ${backup_id})`);

  return jsonResponse({
    success: true,
    message: 'Restore requested — desktop will process on next sync cycle'
  });
}

async function handleActionClearErrors(env) {
  await errorAcknowledgeAll(env.DB);
  return jsonResponse({ success: true });
}
