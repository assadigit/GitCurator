// ========================================
// db.js — D1 database helpers
// ========================================

import { now } from './utils.js';

// ========================================
// ever_seen_ledger
// ========================================

export async function ledgerGetByUrl(db, urlNormalized) {
  const result = await db.prepare(
    'SELECT * FROM ever_seen_ledger WHERE url_normalized = ?'
  ).bind(urlNormalized).first();
  return result;
}

export async function ledgerInsert(db, entry) {
  await db.prepare(`
    INSERT INTO ever_seen_ledger
      (url_normalized, url_original, url_type, github_owner, github_repo,
       first_seen_at, last_seen_at, forward_count,
       telegram_message_id, telegram_chat_id, bot_reply_message_id)
    VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
  `).bind(
    entry.url_normalized,
    entry.url_original,
    entry.url_type,
    entry.github_owner || null,
    entry.github_repo || null,
    entry.first_seen_at,
    entry.last_seen_at,
    entry.telegram_message_id || null,
    entry.telegram_chat_id || null,
    entry.bot_reply_message_id || null
  ).run();
}

export async function ledgerUpdateForward(db, urlNormalized, messageId) {
  await db.prepare(`
    UPDATE ever_seen_ledger
    SET forward_count = forward_count + 1,
        last_seen_at = ?,
        telegram_message_id = COALESCE(?, telegram_message_id)
    WHERE url_normalized = ?
  `).bind(now(), messageId, urlNormalized).run();
}

export async function ledgerUpdateGithubMetadata(db, urlNormalized, metadata) {
  await db.prepare(`
    UPDATE ever_seen_ledger
    SET github_stars = ?,
        github_description = ?,
        github_language = ?,
        github_topics = ?,
        github_readme_excerpt = ?,
        github_fetched_at = ?
    WHERE url_normalized = ?
  `).bind(
    metadata.stars || null,
    metadata.description || null,
    metadata.language || null,
    metadata.topics ? JSON.stringify(metadata.topics) : null,
    metadata.readme_excerpt || null,
    now(),
    urlNormalized
  ).run();
}

export async function ledgerUpdateBotReply(db, urlNormalized, replyMessageId) {
  await db.prepare(`
    UPDATE ever_seen_ledger SET bot_reply_message_id = ? WHERE url_normalized = ?
  `).bind(replyMessageId, urlNormalized).run();
}

export async function ledgerSetForgotten(db, urlNormalized, notes) {
  await db.prepare(`
    UPDATE ever_seen_ledger SET forgotten = 1, notes = ? WHERE url_normalized = ?
  `).bind(notes || 'Forgotten by user request', urlNormalized).run();
}

// ========================================
// vault_mirror
// ========================================

export async function mirrorUpsert(db, entry, syncId) {
  await db.prepare(`
    INSERT INTO vault_mirror
      (url_normalized, vault_path, category, title, status,
       first_processed_at, last_updated_at, desktop_sync_id)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(url_normalized) DO UPDATE SET
      vault_path = excluded.vault_path,
      category = excluded.category,
      title = excluded.title,
      status = excluded.status,
      first_processed_at = COALESCE(excluded.first_processed_at, vault_mirror.first_processed_at),
      last_updated_at = excluded.last_updated_at,
      desktop_sync_id = excluded.desktop_sync_id
  `).bind(
    entry.url_normalized,
    entry.vault_path || null,
    entry.category || null,
    entry.title || null,
    entry.status,
    entry.first_processed_at || null,
    entry.last_updated_at,
    syncId
  ).run();
}

export async function mirrorGetByUrl(db, urlNormalized) {
  return db.prepare(
    'SELECT * FROM vault_mirror WHERE url_normalized = ?'
  ).bind(urlNormalized).first();
}

export async function mirrorGetByStatus(db, status, limit = 100, offset = 0) {
  return db.prepare(
    'SELECT * FROM vault_mirror WHERE status = ? ORDER BY last_updated_at DESC LIMIT ? OFFSET ?'
  ).bind(status, limit, offset).all();
}

export async function mirrorSearch(db, query, category = null, limit = 50, offset = 0) {
  let sql = `
    SELECT * FROM vault_mirror
    WHERE status = 'in_vault'
      AND (url_normalized LIKE ? OR title LIKE ? OR vault_path LIKE ? OR category LIKE ?)
  `;
  const params = [`%${query}%`, `%${query}%`, `%${query}%`, `%${query}%`];
  if (category) {
    sql += ' AND category = ?';
    params.push(category);
  }
  sql += ' ORDER BY last_updated_at DESC LIMIT ? OFFSET ?';
  params.push(limit, offset);
  return db.prepare(sql).bind(...params).all();
}

export async function mirrorCount(db) {
  const result = await db.prepare(
    'SELECT status, COUNT(*) as count FROM vault_mirror GROUP BY status'
  ).all();
  const counts = {};
  for (const row of result.results) {
    counts[row.status] = row.count;
  }
  return counts;
}

// ========================================
// decommission_events
// ========================================

export async function decommissionInsert(db, urlNormalized, source, reason, details, syncId) {
  await db.prepare(`
    INSERT INTO decommission_events
      (url_normalized, source, reason, decommissioned_at, details, sync_id)
    VALUES (?, ?, ?, ?, ?, ?)
  `).bind(urlNormalized, source, reason, now(), details || null, syncId || null).run();
}

export async function decommissionIsDecommissioned(db, urlNormalized) {
  const result = await db.prepare(
    'SELECT 1 FROM decommission_events WHERE url_normalized = ? LIMIT 1'
  ).bind(urlNormalized).first();
  return !!result;
}

export async function decommissionGetSince(db, sinceTimestamp, limit = 100) {
  return db.prepare(`
    SELECT * FROM decommission_events
    WHERE decommissioned_at > ? AND source = 'bot'
    ORDER BY decommissioned_at DESC
    LIMIT ?
  `).bind(sinceTimestamp, limit).all();
}

export async function decommissionGetAll(db, limit = 50, offset = 0) {
  return db.prepare(`
    SELECT * FROM decommission_events
    ORDER BY decommissioned_at DESC
    LIMIT ? OFFSET ?
  `).bind(limit, offset).all();
}

// ========================================
// dead_letters
// ========================================

export async function deadLetterInsert(db, entry) {
  await db.prepare(`
    INSERT INTO dead_letters
      (url_normalized, url_original, reason, first_attempted_at, last_attempted_at, attempt_count, telegram_message_id)
    VALUES (?, ?, ?, ?, ?, 1, ?)
    ON CONFLICT(url_normalized) DO UPDATE SET
      last_attempted_at = excluded.last_attempted_at,
      attempt_count = attempt_count + 1
  `).bind(
    entry.url_normalized,
    entry.url_original,
    entry.reason,
    entry.first_attempted_at,
    entry.last_attempted_at,
    entry.telegram_message_id || null
  ).run();
}

export async function deadLetterIsDead(db, urlNormalized) {
  const result = await db.prepare(
    'SELECT 1 FROM dead_letters WHERE url_normalized = ? AND resolved = 0 LIMIT 1'
  ).bind(urlNormalized).first();
  return !!result;
}

export async function deadLetterGetAll(db, limit = 50, offset = 0) {
  return db.prepare(
    'SELECT * FROM dead_letters WHERE resolved = 0 ORDER BY last_attempted_at DESC LIMIT ? OFFSET ?'
  ).bind(limit, offset).all();
}

// ========================================
// desktop_errors
// ========================================

export async function errorInsert(db, error) {
  const result = await db.prepare(`
    INSERT INTO desktop_errors
      (severity, error_code, message, details, occurred_at, desktop_sync_id)
    VALUES (?, ?, ?, ?, ?, ?)
  `).bind(
    error.severity,
    error.error_code,
    error.message,
    error.details || null,
    error.occurred_at,
    error.desktop_sync_id || null
  ).run();
  return result.meta.last_row_id;
}

export async function errorGetRecent(db, limit = 20, severity = null) {
  let sql = 'SELECT * FROM desktop_errors';
  const params = [];
  if (severity) {
    sql += ' WHERE severity = ?';
    params.push(severity);
  }
  sql += ' ORDER BY occurred_at DESC LIMIT ?';
  params.push(limit);
  return db.prepare(sql).bind(...params).all();
}

export async function errorGetUnackCount(db) {
  const result = await db.prepare(
    'SELECT COUNT(*) as count FROM desktop_errors WHERE acknowledged = 0'
  ).first();
  return result.count;
}

export async function errorAcknowledgeAll(db) {
  await db.prepare('UPDATE desktop_errors SET acknowledged = 1').run();
}

export async function errorGetLastDm(db, errorCode, withinMinutes = 60) {
  const since = new Date(Date.now() - withinMinutes * 60 * 1000).toISOString();
  return db.prepare(`
    SELECT * FROM desktop_errors
    WHERE error_code = ? AND dm_sent = 1 AND occurred_at > ?
    ORDER BY occurred_at DESC LIMIT 1
  `).bind(errorCode, since).first();
}

export async function errorUpdateDmSent(db, errorId, dmMessageId) {
  await db.prepare(`
    UPDATE desktop_errors SET dm_sent = 1, dm_message_id = ? WHERE id = ?
  `).bind(dmMessageId, errorId).run();
}

// ========================================
// sync_state
// ========================================

export async function stateGet(db, key) {
  const result = await db.prepare(
    'SELECT value FROM sync_state WHERE key = ?'
  ).bind(key).first();
  return result ? result.value : null;
}

export async function stateSet(db, key, value) {
  await db.prepare(`
    INSERT INTO sync_state (key, value, updated_at)
    VALUES (?, ?, ?)
    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
  `).bind(key, value, now()).run();
}

// ========================================
// gdrive_snapshots
// ========================================

export async function gdriveInsert(db, snapshot) {
  await db.prepare(`
    INSERT INTO gdrive_snapshots
      (backup_id, file_name, file_size_bytes, repo_count, trigger, created_at, gdrive_file_id, status, error_message)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).bind(
    snapshot.backup_id,
    snapshot.file_name,
    snapshot.file_size_bytes,
    snapshot.repo_count,
    snapshot.trigger,
    snapshot.created_at,
    snapshot.gdrive_file_id || null,
    snapshot.status,
    snapshot.error_message || null
  ).run();
}

export async function gdriveGetRecent(db, limit = 10) {
  return db.prepare(
    'SELECT * FROM gdrive_snapshots ORDER BY created_at DESC LIMIT ?'
  ).bind(limit).all();
}

// ========================================
// desktop_installs (HMAC pairing)
// ========================================

export async function installGet(db, installId) {
  return db.prepare(
    'SELECT * FROM desktop_installs WHERE install_id = ?'
  ).bind(installId).first();
}

export async function installInsert(db, installId, secretHash) {
  await db.prepare(`
    INSERT INTO desktop_installs (install_id, shared_secret_hash, paired_at, last_seen_at)
    VALUES (?, ?, ?, ?)
  `).bind(installId, secretHash, now(), now()).run();
}

export async function installUpdateLastSeen(db, installId) {
  await db.prepare(
    'UPDATE desktop_installs SET last_seen_at = ? WHERE install_id = ?'
  ).bind(now(), installId).run();
}

// ========================================
// activity_log
// ========================================

export async function activityLog(db, eventType, url, message) {
  await db.prepare(`
    INSERT INTO activity_log (event_type, url, message, occurred_at)
    VALUES (?, ?, ?, ?)
  `).bind(eventType, url || null, message, now()).run();
}

export async function activityGetRecent(db, limit = 50, hours = 24) {
  const since = new Date(Date.now() - hours * 60 * 60 * 1000).toISOString();
  return db.prepare(`
    SELECT * FROM activity_log WHERE occurred_at > ?
    ORDER BY occurred_at DESC LIMIT ?
  `).bind(since, limit).all();
}

// ========================================
// dashboard_sessions
// ========================================

export async function sessionGetByMagic(db, magicToken) {
  return db.prepare(`
    SELECT * FROM dashboard_sessions
    WHERE magic_token = ? AND magic_token IS NOT NULL
  `).bind(magicToken).first();
}

export async function sessionGetById(db, sessionId) {
  return db.prepare(`
    SELECT * FROM dashboard_sessions WHERE session_id = ?
  `).bind(sessionId).first();
}

export async function sessionInsert(db, session) {
  await db.prepare(`
    INSERT INTO dashboard_sessions
      (session_id, user_id, magic_token, created_at, expires_at, last_seen_at, user_agent, ip_address)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
  `).bind(
    session.session_id,
    session.user_id,
    session.magic_token || null,
    session.created_at,
    session.expires_at,
    session.last_seen_at,
    session.user_agent || null,
    session.ip_address || null
  ).run();
}

export async function sessionClearMagic(db, sessionId) {
  await db.prepare(
    'UPDATE dashboard_sessions SET magic_token = NULL WHERE session_id = ?'
  ).bind(sessionId).run();
}

export async function sessionUpdateLastSeen(db, sessionId) {
  await db.prepare(
    'UPDATE dashboard_sessions SET last_seen_at = ? WHERE session_id = ?'
  ).bind(now(), sessionId).run();
}

export async function sessionRevokeAll(db, userId) {
  await db.prepare(
    'DELETE FROM dashboard_sessions WHERE user_id = ?'
  ).bind(userId).run();
}

// ========================================
// Aggregate stats
// ========================================

export async function getStats(db) {
  const ledgerTotal = await db.prepare(
    'SELECT COUNT(*) as count FROM ever_seen_ledger WHERE forgotten = 0'
  ).first();

  const ledgerByType = await db.prepare(
    "SELECT url_type, COUNT(*) as count FROM ever_seen_ledger WHERE forgotten = 0 GROUP BY url_type"
  ).all();

  const mirrorCounts = await mirrorCount(db);

  const decommCount = await db.prepare(
    'SELECT COUNT(DISTINCT url_normalized) as count FROM decommission_events'
  ).first();

  const deadLetterCount = await db.prepare(
    'SELECT COUNT(*) as count FROM dead_letters WHERE resolved = 0'
  ).first();

  const lastBackup = await db.prepare(
    "SELECT value FROM sync_state WHERE key = 'gdrive_last_backup'"
  ).first();

  const lastSync = await db.prepare(
    "SELECT value FROM sync_state WHERE key = 'desktop_last_sync'"
  ).first();

  const lastPoll = await db.prepare(
    "SELECT value FROM sync_state WHERE key = 'desktop_last_poll'"
  ).first();

  const lastWebhook = await db.prepare(
    "SELECT value FROM sync_state WHERE key = 'last_webhook_at'"
  ).first();

  return {
    total: ledgerTotal.count,
    byType: Object.fromEntries(ledgerByType.results.map(r => [r.url_type, r.count])),
    pending: mirrorCounts.pending || 0,
    inVault: mirrorCounts.in_vault || 0,
    decommissioned: decommCount.count,
    deadLetters: deadLetterCount.count,
    lastBackup: lastBackup?.value || null,
    lastSync: lastSync?.value || null,
    lastPoll: lastPoll?.value || null,
    lastWebhook: lastWebhook?.value || null
  };
}
