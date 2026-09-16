// ========================================
// cron.js — Cron trigger handlers
// ========================================
// 3 cron triggers:
//   1. Hourly health check
//   2. Sunday 9 AM Iran — weekly report
//   3. Midnight Iran — daily summary
//
// Note: R2 export removed (R2 requires payment method).
// D1 is already durable (Cloudflare-managed with built-in redundancy).

import { now, toIranTime, timeAgo } from './utils.js';
import { getStats, stateGet, stateSet, activityGetRecent, errorGetRecent, errorGetUnackCount } from './db.js';
import { sendMessage } from './telegram.js';

export async function handleScheduled(event, env) {
  const cron = event.cron;

  console.log(`Cron triggered: ${cron}`);

  switch (cron) {
    case '0 * * * *':
      // Hourly health check
      await healthCheck(env);
      break;

    case '30 5 * * 0':
      // Sunday 9 AM Iran — weekly report
      await weeklyReport(env);
      break;

    case '30 20 * * *':
      // Midnight Iran — daily summary
      await dailySummary(env);
      break;

    default:
      console.log(`Unknown cron: ${cron}`);
  }
}

// ========================================
// 1. Hourly health check
// ========================================

async function healthCheck(env) {
  const lastWebhook = await stateGet(env.DB, 'last_webhook_at');
  const lastPoll = await stateGet(env.DB, 'desktop_last_poll');
  const stats = await getStats(env.DB);

  const nowMs = Date.now();

  // Check 1: If no webhook in 6h AND there are pending links → alert
  if (lastWebhook) {
    const webhookAge = nowMs - new Date(lastWebhook).getTime();
    if (webhookAge > 6 * 60 * 60 * 1000 && stats.pending > 0) {
      await sendAlert(env,
        `⚠️ <b>Health Check Alert</b>\n\n` +
        `No webhooks received in ${Math.floor(webhookAge / 3600000)}h.\n` +
        `Pending links: ${stats.pending}\n\n` +
        `The bot may not be receiving Telegram updates. Check:\n` +
        `• Telegram webhook is set: /api/health\n` +
        `• Cloudflare Worker is running\n` +
        `• Bot token is valid`
      );
    }
  }

  // Check 2: If desktop hasn't polled in 1h → alert
  if (lastPoll) {
    const pollAge = nowMs - new Date(lastPoll).getTime();
    if (pollAge > 1 * 60 * 60 * 1000) {
      await sendAlert(env,
        `⚠️ <b>Desktop Offline</b>\n\n` +
        `Desktop app hasn't polled in ${Math.floor(pollAge / 60000)} minutes.\n` +
        `Pending links are accumulating: ${stats.pending}\n\n` +
        `Start the desktop app to resume processing.`
      );
    }
  }

  // Check 3: GDrive auth status
  const gdriveStatus = await stateGet(env.DB, 'gdrive_auth_status');
  if (gdriveStatus === 'expired') {
    const lastAlert = await stateGet(env.DB, 'gdrive_alert_sent');
    const lastAlertAge = lastAlert ? nowMs - new Date(lastAlert).getTime() : Infinity;
    // Alert at most once per day
    if (lastAlertAge > 24 * 60 * 60 * 1000) {
      await sendAlert(env,
        `🔴 <b>Google Drive Auth Expired</b>\n\n` +
        `Backups are halted. Your vault is NOT being backed up.\n\n` +
        `To fix: Open desktop app → Settings → Google Drive → Re-authorize`
      );
      await stateSet(env.DB, 'gdrive_alert_sent', now());
    }
  }
}

// ========================================
// 2. Weekly report (Sunday 9 AM Iran)
// ========================================

async function weeklyReport(env) {
  const stats = await getStats(env.DB);
  const errors = await errorGetRecent(env.DB, 100);
  const criticalCount = errors.results.filter(e => e.severity === 'CRITICAL').length;
  const warningCount = errors.results.filter(e => e.severity === 'WARNING').length;

  const message =
    `📊 <b>Weekly Report</b> — ${toIranTime(now())}\n\n` +
    `<b>Link Statistics:</b>\n` +
    `  📋 Pending:        ${stats.pending}\n` +
    `  ✅ In Vault:       ${stats.inVault}\n` +
    `  🗑️ Decommissioned:  ${stats.decommissioned}\n` +
    `  💀 Dead Letters:    ${stats.deadLetters}\n` +
    `  📝 Total Seen:     ${stats.total}\n\n` +
    `<b>Errors (last 7 days):</b>\n` +
    `  🔴 Critical: ${criticalCount}\n` +
    `  🟠 Warnings: ${warningCount}\n\n` +
    `<b>System:</b>\n` +
    `  💾 Last Backup: ${stats.lastBackup ? timeAgo(stats.lastBackup) : 'never'}\n` +
    `  🔄 Last Sync:   ${stats.lastSync ? timeAgo(stats.lastSync) : 'never'}\n` +
    `  🤖 Last Poll:   ${stats.lastPoll ? timeAgo(stats.lastPoll) : 'never'}\n\n` +
    (stats.pending > 0
      ? `⚠️ <b>${stats.pending} link(s) pending processing.</b> Start the desktop app to process them.`
      : `✅ All links processed!`);

  await sendAlert(env, message);
}

// ========================================
// 3. Daily summary (midnight Iran)
// ========================================

async function dailySummary(env) {
  const stats = await getStats(env.DB);
  const activity = await activityGetRecent(env.DB, 100, 24);
  const errors = await errorGetRecent(env.DB, 100);
  const unackErrors = await errorGetUnackCount(env.DB);

  // Count today's activity
  const todayActivity = activity.results;
  const processed = todayActivity.filter(a => a.event_type === 'processed').length;
  const received = todayActivity.filter(a => a.event_type === 'received').length;
  const decommissioned = todayActivity.filter(a => a.event_type === 'decommissioned').length;
  const backups = todayActivity.filter(a => a.event_type === 'backup').length;

  const todayErrors = errors.results.filter(e => {
    const eTime = new Date(e.occurred_at).getTime();
    return eTime > Date.now() - 24 * 60 * 60 * 1000;
  });
  const critical = todayErrors.filter(e => e.severity === 'CRITICAL').length;
  const warnings = todayErrors.filter(e => e.severity === 'WARNING').length;

  const message =
    `📅 <b>Daily Summary</b> — ${toIranTime(now()).split(' ')[0]}\n\n` +
    `<b>Today's Activity:</b>\n` +
    `  📋 Links received:    ${received}\n` +
    `  ✅ Repos processed:   ${processed}\n` +
    `  🗑️ Decommissioned:    ${decommissioned}\n` +
    `  💾 Backups uploaded:  ${backups}\n\n` +
    `<b>Errors:</b>\n` +
    `  🔴 Critical: ${critical}\n` +
    `  🟠 Warnings: ${warnings}\n` +
    `  📊 Unacknowledged: ${unackErrors}\n\n` +
    `<b>Current State:</b>\n` +
    `  📋 Pending:     ${stats.pending}\n` +
    `  ✅ In Vault:    ${stats.inVault}\n` +
    `  📝 Total:       ${stats.total}\n\n` +
    `💾 Last Backup: ${stats.lastBackup ? timeAgo(stats.lastBackup) : 'never'}\n` +
    `🤖 Desktop: ${stats.lastPoll ? (Date.now() - new Date(stats.lastPoll).getTime() < 10 * 60 * 1000 ? 'Online' : 'Offline') : 'Unknown'}`;

  await sendAlert(env, message);
}

// ========================================
// Helper: send alert to user
// ========================================

async function sendAlert(env, message) {
  const allowed = (env.ALLOWED_USER_IDS || '').split(',').map(s => s.trim()).filter(Boolean);
  for (const userId of allowed) {
    try {
      await sendMessage(env, parseInt(userId), message);
    } catch (err) {
      console.error(`Failed to send alert to ${userId}:`, err);
    }
  }
}
