// ========================================
// commands.js — Bot command handlers
// ========================================
// All Telegram bot commands with rich formatting.

import {
  normalizeUrl, isGitHubUrl, parseGitHubUrl, formatStars,
  truncate, now, toIranTime, timeAgo, uuid
} from './utils.js';
import { sendMessage, editMessage, inlineKeyboard, isUserAllowed } from './telegram.js';
import {
  getStats, ledgerGetByUrl, mirrorGetByUrl, mirrorGetByStatus,
  mirrorSearch, mirrorCount,
  decommissionGetAll, decommissionInsert, decommissionIsDecommissioned,
  deadLetterGetAll, deadLetterInsert,
  errorGetRecent, errorAcknowledgeAll, errorGetUnackCount,
  gdriveGetRecent, activityGetRecent,
  stateGet, stateSet,
  ledgerSetForgotten
} from './db.js';

// ========================================
// Main command router
// ========================================

export async function handleCommand(text, chatId, userId, message, env) {
  const parts = text.split(/\s+/);
  const cmd = parts[0].toLowerCase().split('@')[0]; // Remove @botname suffix
  const args = parts.slice(1).join(' ').trim();

  switch (cmd) {
    case '/start':
      await cmdStart(env, chatId);
      break;

    case '/help':
      await cmdHelp(env, chatId);
      break;

    case '/stats':
      await cmdStats(env, chatId);
      break;

    case '/pending':
      await cmdPending(env, chatId);
      break;

    case '/status':
      await cmdStatus(env, chatId, args);
      break;

    case '/search':
      await cmdSearch(env, chatId, args);
      break;

    case '/dead':
      await cmdDead(env, chatId);
      break;

    case '/notfound':
      await cmdNotFound(env, chatId);
      break;

    case '/errors':
      if (args.toLowerCase() === 'clear') {
        await cmdErrorsClear(env, chatId);
      } else {
        await cmdErrors(env, chatId);
      }
      break;

    case '/forget':
      await cmdForget(env, chatId, args);
      break;

    case '/backup':
      await cmdBackup(env, chatId);
      break;

    case '/restore':
      await cmdRestore(env, chatId);
      break;

    case '/sync':
      await cmdSync(env, chatId);
      break;

    case '/pair':
      await cmdPair(env, chatId, userId);
      break;

    case '/dashboard':
      await cmdDashboard(env, chatId, userId);
      break;

    case '/logout':
      await cmdLogout(env, chatId, userId);
      break;

    default:
      await sendMessage(env, chatId,
        `❓ Unknown command: <code>${cmd}</code>\n\nType /help for available commands.`
      );
  }
}

// ========================================
// /start
// ========================================

async function cmdStart(env, chatId) {
  await sendMessage(env, chatId,
    "🤖 <b>GitHub Project Curator Bot</b>\n\n" +
    "Forward me GitHub links and I'll track them for processing!\n\n" +
    "The desktop app will pick them up automatically and create Obsidian notes.\n\n" +
    "<b>Quick Start:</b>\n" +
    "  1. Forward a GitHub link to me\n" +
    "  2. I'll reply with status (pending/in-vault/decommissioned)\n" +
    "  3. Desktop app processes it and writes an Obsidian note\n" +
    "  4. My reply updates to show the vault path\n\n" +
    "Type /help for all commands."
  );
}

// ========================================
// /help
// ========================================

async function cmdHelp(env, chatId) {
  await sendMessage(env, chatId,
    "🤖 <b>Bot Commands</b>\n\n" +

    "<b>📊 Status &amp; Info</b>\n" +
    "  /stats — Link statistics\n" +
    "  /status &lt;url&gt; — Check a link's status\n" +
    "  /search &lt;query&gt; — Search your vault\n\n" +

    "<b>📋 Lists</b>\n" +
    "  /pending — Pending links\n" +
    "  /dead — Decommissioned repos\n" +
    "  /notfound — Dead letters (unprocessable)\n\n" +

    "<b>⚠️ Errors</b>\n" +
    "  /errors — Recent desktop errors\n" +
    "  /errors clear — Acknowledge all errors\n\n" +

    "<b>💾 Backup</b>\n" +
    "  /backup — Manual GDrive backup\n" +
    "  /restore — List available backups\n\n" +

    "<b>🔧 System</b>\n" +
    "  /sync — Force desktop sync (if online)\n" +
    "  /pair — Pair desktop app\n" +
    "  /forget &lt;url&gt; — Remove from ledger\n" +
    "  /dashboard — Get dashboard login link\n" +
    "  /logout — Revoke dashboard sessions\n\n" +

    "<b>💡 Just forward GitHub links to me — I'll handle the rest!</b>"
  );
}

// ========================================
// /stats
// ========================================

async function cmdStats(env, chatId) {
  const stats = await getStats(env.DB);

  const desktopOnline = stats.lastPoll
    ? (Date.now() - new Date(stats.lastPoll).getTime() < 10 * 60 * 1000)
    : false;

  const gdriveOk = await stateGet(env.DB, 'gdrive_auth_status') === 'ok';

  await sendMessage(env, chatId,
    `📊 <b>Curator Stats</b>\n\n` +
    `📋 Pending:        <b>${stats.pending}</b>\n` +
    `✅ In Vault:       <b>${stats.inVault}</b>\n` +
    `🗑️ Decommissioned:  <b>${stats.decommissioned}</b>\n` +
    `💀 Dead Letters:    <b>${stats.deadLetters}</b>\n` +
    `🔗 Non-GitHub:     <b>${stats.byType.non_github || 0}</b>\n` +
    `━━━━━━━━━━━━━━━━━━\n` +
    `📝 Total Seen:     <b>${stats.total}</b>\n\n` +

    `<b>System:</b>\n` +
    `  🤖 Desktop: ${desktopOnline ? '● Online' : '○ Offline'}${stats.lastPoll ? ` (${timeAgo(stats.lastPoll)})` : ''}\n` +
    `  💾 GDrive:  ${gdriveOk ? '● Connected' : '○ Disconnected'}\n` +
    `  🔄 Last Sync: ${stats.lastSync ? timeAgo(stats.lastSync) : 'never'}\n` +
    `  💾 Last Backup: ${stats.lastBackup ? timeAgo(stats.lastBackup) : 'never'}\n` +
    `  📡 Last Webhook: ${stats.lastWebhook ? timeAgo(stats.lastWebhook) : 'never'}`
  );
}

// ========================================
// /pending
// ========================================

async function cmdPending(env, chatId) {
  // Get pending links (in ledger but not in vault_mirror with in_vault status)
  const result = await env.DB.prepare(`
    SELECT l.* FROM ever_seen_ledger l
    LEFT JOIN vault_mirror v ON l.url_normalized = v.url_normalized
    WHERE l.forgotten = 0
      AND l.url_type = 'github'
      AND (v.status IS NULL OR v.status = 'pending')
      AND l.url_normalized NOT IN (SELECT url_normalized FROM decommission_events)
      AND l.url_normalized NOT IN (SELECT url_normalized FROM dead_letters WHERE resolved = 0)
    ORDER BY l.first_seen_at DESC
    LIMIT 20
  `).all();

  if (result.results.length === 0) {
    await sendMessage(env, chatId,
      "✅ <b>No pending links!</b>\n\n" +
      "All received links have been processed."
    );
    return;
  }

  const lines = [`📋 <b>Pending Links</b> (${result.results.length}${result.results.length === 20 ? '+' : ''})\n`];

  for (let i = 0; i < result.results.length; i++) {
    const row = result.results[i];
    const starsStr = row.github_stars ? `⭐ ${formatStars(row.github_stars)}` : '';
    const desc = row.github_description ? ` — ${truncate(row.github_description, 60)}` : '';
    lines.push(`${i + 1}. <b>${row.github_owner}/${row.github_repo}</b>`);
    if (starsStr || desc) {
      lines.push(`   ${starsStr}${desc}`);
    }
    lines.push(`   📅 ${timeAgo(row.first_seen_at)}`);
  }

  await sendMessage(env, chatId, lines.join('\n'));
}

// ========================================
// /status <url>
// ========================================

async function cmdStatus(env, chatId, args) {
  if (!args) {
    await sendMessage(env, chatId, "Usage: <code>/status &lt;url&gt;</code>\n\nExample: <code>/status github.com/vercel/next.js</code>");
    return;
  }

  const urlNorm = normalizeUrl(args.startsWith('http') ? args : `https://${args}`);
  const github = parseGitHubUrl(urlNorm);

  if (!github) {
    await sendMessage(env, chatId, `❌ Not a valid GitHub URL: <code>${args}</code>`);
    return;
  }

  const ledger = await ledgerGetByUrl(env.DB, urlNorm);
  if (!ledger) {
    await sendMessage(env, chatId,
      `❌ <b>Not found</b>: ${github.owner}/${github.repo}\n\n` +
      `This URL has never been forwarded to the bot.`
    );
    return;
  }

  const mirror = await mirrorGetByUrl(env.DB, urlNorm);
  const isDecomm = await decommissionIsDecommissioned(env.DB, urlNorm);

  let statusLine;
  if (isDecomm) {
    statusLine = '🗑️ Decommissioned';
  } else if (mirror?.status === 'in_vault') {
    statusLine = `✅ In Vault — <code>${mirror.vault_path}</code>`;
  } else {
    statusLine = '📋 Pending';
  }

  const parts = [
    `🔍 <b>Status</b>: ${github.owner}/${github.repo}\n`,
    `📝 Ledger: #${ledger.id} — first seen ${timeAgo(ledger.first_seen_at)}`,
    `📋 Status: ${statusLine}`
  ];

  if (ledger.github_stars) {
    parts.push(`⭐ Stars: ${formatStars(ledger.github_stars)}`);
  }
  if (ledger.github_description) {
    parts.push(`📝 ${truncate(ledger.github_description, 100)}`);
  }
  if (ledger.github_language) {
    parts.push(`💻 ${ledger.github_language}`);
  }
  if (mirror?.category) {
    parts.push(`🏷️ ${mirror.category}`);
  }
  if (mirror?.title) {
    parts.push(`📄 ${mirror.title}`);
  }

  parts.push(`\n🔄 Forwarded ${ledger.forward_count} time(s)`);

  const buttons = [];
  if (!isDecomm) {
    buttons.push([{ text: '🗑️ Mark Decommission', callback_data: `decomm:${urlNorm}` }]);
  }
  buttons.push([{ text: '🐙 Open in GitHub', url: `https://github.com/${github.owner}/${github.repo}` }]);

  await sendMessage(env, chatId, parts.join('\n'), {
    reply_markup: inlineKeyboard(buttons)
  });
}

// ========================================
// /search <query>
// ========================================

async function cmdSearch(env, chatId, args) {
  if (!args) {
    await sendMessage(env, chatId, "Usage: <code>/search &lt;query&gt;</code>\n\nExample: <code>/search ai/ml</code>");
    return;
  }

  const result = await mirrorSearch(env.DB, args, null, 10, 0);

  if (result.results.length === 0) {
    await sendMessage(env, chatId, `🔍 No results for "<b>${args}</b>"`);
    return;
  }

  const lines = [`🔍 <b>Search</b>: "${args}"\n`];
  lines.push(`Found <b>${result.results.length}</b> result(s):\n`);

  for (let i = 0; i < result.results.length; i++) {
    const row = result.results[i];
    const github = parseGitHubUrl(row.url_normalized);
    lines.push(`${i + 1}. <b>${github?.owner}/${github?.repo}</b>`);
    if (row.title) lines.push(`   📄 ${row.title}`);
    if (row.category) lines.push(`   🏷️ ${row.category}`);
    lines.push(`   📂 <code>${row.vault_path}</code>`);
  }

  await sendMessage(env, chatId, lines.join('\n'));
}

// ========================================
// /dead — decommissioned repos
// ========================================

async function cmdDead(env, chatId) {
  const result = await decommissionGetAll(env.DB, 20, 0);

  if (result.results.length === 0) {
    await sendMessage(env, chatId, "✅ No decommissioned repos.");
    return;
  }

  const lines = [`🗑️ <b>Decommissioned Repos</b> (${result.results.length}${result.results.length === 20 ? '+' : ''})\n`];

  for (let i = 0; i < result.results.length; i++) {
    const row = result.results[i];
    const github = parseGitHubUrl(row.url_normalized);
    const repoName = github ? `${github.owner}/${github.repo}` : row.url_normalized;
    lines.push(`${i + 1}. <b>${repoName}</b>`);
    lines.push(`   Reason: ${row.reason} — ${timeAgo(row.decommissioned_at)}`);
    if (row.source === 'bot') lines.push(`   🤖 Detected by bot`);
    else lines.push(`   💻 Marked by desktop`);
  }

  await sendMessage(env, chatId, lines.join('\n'));
}

// ========================================
// /notfound — dead letters
// ========================================

async function cmdNotFound(env, chatId) {
  const result = await deadLetterGetAll(env.DB, 20, 0);

  if (result.results.length === 0) {
    await sendMessage(env, chatId, "✅ No dead letters.");
    return;
  }

  const lines = [`💀 <b>Dead Letters</b> (${result.results.length}${result.results.length === 20 ? '+' : ''})\n`];

  for (let i = 0; i < result.results.length; i++) {
    const row = result.results[i];
    lines.push(`${i + 1}. <code>${truncate(row.url_original, 60)}</code>`);
    lines.push(`   Reason: ${row.reason} — ${timeAgo(row.first_attempted_at)}`);
  }

  await sendMessage(env, chatId, lines.join('\n'));
}

// ========================================
// /errors
// ========================================

async function cmdErrors(env, chatId) {
  const result = await errorGetRecent(env.DB, 20, null);
  const unackCount = await errorGetUnackCount(env.DB);

  if (result.results.length === 0) {
    await sendMessage(env, chatId, "✅ No desktop errors logged.");
    return;
  }

  const lines = [`⚠️ <b>Desktop Errors</b> (last ${result.results.length}, ${unackCount} unacknowledged)\n`];

  const severityEmoji = {
    CRITICAL: '🔴',
    WARNING: '🟠',
    INFO: '🔵',
    DEBUG: '⚪'
  };

  for (const row of result.results.slice(0, 15)) {
    const emoji = severityEmoji[row.severity] || '❓';
    lines.push(`${emoji} [${row.severity}] ${timeAgo(row.occurred_at)} — <b>${row.error_code}</b>`);
    lines.push(`   ${truncate(row.message, 80)}`);
  }

  const buttons = [[{ text: '✅ Clear All', callback_data: 'errors_clear' }]];
  await sendMessage(env, chatId, lines.join('\n'), {
    reply_markup: inlineKeyboard(buttons)
  });
}

async function cmdErrorsClear(env, chatId) {
  await errorAcknowledgeAll(env.DB);
  await sendMessage(env, chatId, "✅ All errors acknowledged.");
}

// ========================================
// /forget <url>
// ========================================

async function cmdForget(env, chatId, args) {
  if (!args) {
    await sendMessage(env, chatId, "Usage: <code>/forget &lt;url&gt;</code>\n\nExample: <code>/forget github.com/user/repo</code>");
    return;
  }

  const urlNorm = normalizeUrl(args.startsWith('http') ? args : `https://${args}`);
  const ledger = await ledgerGetByUrl(env.DB, urlNorm);

  if (!ledger) {
    await sendMessage(env, chatId, `❌ Not found in ledger: <code>${args}</code>`);
    return;
  }

  const github = parseGitHubUrl(urlNorm);
  const repoName = github ? `${github.owner}/${github.repo}` : urlNorm;

  await sendMessage(env, chatId,
    `⚠️ <b>Confirm Forget</b>\n\n` +
    `Are you sure you want to remove this from the permanent ledger?\n\n` +
    `URL: <code>${repoName}</code>\n` +
    `Ledger ID: #${ledger.id}\n` +
    `First seen: ${timeAgo(ledger.first_seen_at)}\n` +
    `Forward count: ${ledger.forward_count}\n\n` +
    `<b>Note:</b> The vault note will NOT be deleted.\n` +
    `This only removes the URL from the bot's tracking.`,
    {
      reply_markup: inlineKeyboard([
        [{ text: '✅ Confirm Forget', callback_data: `forget_confirm:${urlNorm}` }],
        [{ text: '❌ Cancel', callback_data: 'forget_cancel' }]
      ])
    }
  );
}

// ========================================
// /backup — manual GDrive backup
// ========================================

async function cmdBackup(env, chatId) {
  // Signal desktop to trigger a manual backup
  await stateSet(env.DB, 'pending_backup_request', now());

  await sendMessage(env, chatId,
    "💾 <b>Manual backup requested</b>\n\n" +
    "The desktop app will upload a backup on its next sync cycle (within 5 minutes).\n\n" +
    "You'll receive a confirmation when it's done."
  );
}

// ========================================
// /restore — list backups
// ========================================

async function cmdRestore(env, chatId) {
  const result = await gdriveGetRecent(env.DB, 10);

  if (result.results.length === 0) {
    await sendMessage(env, chatId,
      "💾 <b>No backups available</b>\n\n" +
      "Backups will appear here after the desktop app processes its first batch."
    );
    return;
  }

  const lines = [`💾 <b>Available Backups</b> (${result.results.length})\n`];

  for (let i = 0; i < result.results.length; i++) {
    const row = result.results[i];
    const sizeStr = formatBytes(row.file_size_bytes);
    const dateStr = toIranTime(row.created_at);
    lines.push(`${i + 1}. <b>${row.file_name}</b>`);
    lines.push(`   📦 ${sizeStr} — ${row.repo_count} repos — ${dateStr}`);
    if (row.status !== 'uploaded') {
      lines.push(`   ⚠️ Status: ${row.status}`);
    }
  }

  lines.push(`\n💡 Use the desktop app to restore from any backup.`);

  await sendMessage(env, chatId, lines.join('\n'));
}

// ========================================
// /sync — force desktop sync
// ========================================

async function cmdSync(env, chatId) {
  await stateSet(env.DB, 'pending_sync_request', now());

  const lastPoll = await stateGet(env.DB, 'desktop_last_poll');
  const desktopOnline = lastPoll
    ? (Date.now() - new Date(lastPoll).getTime() < 10 * 60 * 1000)
    : false;

  if (desktopOnline) {
    await sendMessage(env, chatId,
      "🔄 <b>Sync requested</b>\n\n" +
      "Desktop is online — it will sync on its next poll cycle (within 5 minutes)."
    );
  } else {
    await sendMessage(env, chatId,
      "🔄 <b>Sync requested</b>\n\n" +
      "⚠️ Desktop appears to be offline.\n" +
      "The sync will happen when the desktop app starts up."
    );
  }
}

// ========================================
// /pair — generate pairing code
// ========================================

async function cmdPair(env, chatId, userId) {
  const { cacheSetPairingCode } = await import('./kv.js');
  const code = uuid().substring(0, 8).toUpperCase();

  await cacheSetPairingCode(env.CACHE, code, {
    user_id: userId,
    chat_id: chatId,
    created_at: now()
  });

  await sendMessage(env, chatId,
    `🔧 <b>Desktop Pairing Code</b>\n\n` +
    `Your pairing code:\n\n` +
    `<code>${code}</code>\n\n` +
    `⚠️ This code expires in <b>10 minutes</b>.\n` +
    `Use it in the desktop app: Settings → Cloudflare → Enter Pairing Code.\n\n` +
    `After pairing, the desktop app can sync with this bot automatically.`
  );
}

// ========================================
// /dashboard — get magic link
// ========================================

async function cmdDashboard(env, chatId, userId) {
  // Generate magic link for dashboard login
  const { sessionInsert } = await import('./db.js');
  const magicToken = uuid() + uuid();

  await sessionInsert(env.DB, {
    session_id: uuid(),
    user_id: userId,
    magic_token: magicToken,
    created_at: now(),
    expires_at: new Date(Date.now() + 10 * 60 * 1000).toISOString(), // 10 min
    last_seen_at: now(),
    user_agent: 'telegram-magic-link',
    ip_address: null
  });

  // Build dashboard URL — magic link goes to /dashboard?token=...
  // The HTML dashboard checks for ?token= on load and verifies it
  const workerUrl = env.WORKER_URL || `https://github-to-obsidian-bot.${(env.CLOUDFLARE_ACCOUNT_ID || 'your-subdomain')}.workers.dev`;

  await sendMessage(env, chatId,
    `🔐 <b>Dashboard Login</b>\n\n` +
    `Click below to open the dashboard (link expires in 10 minutes):\n\n` +
    `<a href="${workerUrl}/dashboard?token=${magicToken}">🔓 Open Dashboard</a>\n\n` +
    `This link is single-use. After login, you'll stay logged in for 7 days.`
  );
}

// ========================================
// /logout — revoke all dashboard sessions
// ========================================

async function cmdLogout(env, chatId, userId) {
  const { sessionRevokeAll } = await import('./db.js');
  await sessionRevokeAll(env.DB, userId);

  await sendMessage(env, chatId,
    "✅ <b>All dashboard sessions revoked.</b>\n\n" +
    "You'll need to run /dashboard again to log back in."
  );
}

// ========================================
// Helper: format bytes
// ========================================

function formatBytes(bytes) {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let size = bytes;
  let unitIdx = 0;
  while (size >= 1024 && unitIdx < units.length - 1) {
    size /= 1024;
    unitIdx++;
  }
  return `${size.toFixed(1)} ${units[unitIdx]}`;
}
