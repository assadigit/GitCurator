// ========================================
// queue-consumer.js — Queue consumer
// ========================================
// Reads messages from the ingest queue, processes URLs,
// writes to D1, and sends auto-replies.
// 7-day retry buffer = no link left behind.

import {
  normalizeUrl, isGitHubUrl, parseGitHubUrl, formatStars,
  truncate, now, uuid
} from './utils.js';
import { sendMessage, inlineKeyboard } from './telegram.js';
import {
  ledgerGetByUrl, ledgerInsert, ledgerUpdateForward, ledgerUpdateBotReply,
  ledgerUpdateGithubMetadata,
  mirrorGetByUrl,
  decommissionIsDecommissioned, decommissionInsert,
  deadLetterIsDead, deadLetterInsert,
  activityLog
} from './db.js';
import { cacheGetDedup, cacheSetDedup } from './kv.js';

export async function handleQueue(batch, env) {
  for (const message of batch.messages) {
    try {
      const data = JSON.parse(message.body);

      if (data.type === 'urls') {
        await processUrlsMessage(data, env);
      } else if (data.type === 'enrich') {
        await enrichGithubMetadata(data, env);
      }

      // Ack the message
      message.ack();
    } catch (err) {
      console.error('Queue consumer error:', err);
      // Retry — the message will be redelivered
      // After max_retries, it goes to the DLQ
      message.retry();
    }
  }
}

// ========================================
// Process a message containing URLs
// ========================================

async function processUrlsMessage(data, env) {
  const { urls, chat_id, user_id, message_id, received_at } = data;

  // Deduplicate URLs within the same message
  const uniqueUrls = [...new Set(urls.map(u => normalizeUrl(u)))];
  const originalUrlsMap = {};
  for (const rawUrl of urls) {
    const normalized = normalizeUrl(rawUrl);
    if (!originalUrlsMap[normalized]) {
      originalUrlsMap[normalized] = rawUrl.replace(/[.,);]+$/, '');
    }
  }

  const results = {
    new: [],
    pending: [],
    inVault: [],
    decommissioned: [],
    deadLetter: [],
    nonGithub: []
  };

  // Process each URL
  for (const urlNorm of uniqueUrls) {
    const urlOriginal = originalUrlsMap[urlNorm];
    const github = isGitHubUrl(urlNorm);
    const githubInfo = github ? parseGitHubUrl(urlNorm) : null;

    // Check decommissioned
    const isDecommissioned = await decommissionIsDecommissioned(env.DB, urlNorm);
    if (isDecommissioned) {
      results.decommissioned.push({ url: urlNorm, original: urlOriginal });
      continue;
    }

    // Check dead letters
    const isDead = await deadLetterIsDead(env.DB, urlNorm);
    if (isDead) {
      results.deadLetter.push({ url: urlNorm, original: urlOriginal });
      continue;
    }

    // Non-GitHub URLs go to dead letters
    if (!github) {
      await deadLetterInsert(env.DB, {
        url_normalized: urlNorm,
        url_original: urlOriginal,
        reason: 'not_github',
        first_attempted_at: received_at,
        last_attempted_at: now(),
        telegram_message_id: message_id
      });
      results.nonGithub.push({ url: urlNorm, original: urlOriginal });
      continue;
    }

    // Check vault_mirror (already processed?)
    const mirrorEntry = await mirrorGetByUrl(env.DB, urlNorm);
    if (mirrorEntry && mirrorEntry.status === 'in_vault') {
      results.inVault.push({
        url: urlNorm,
        original: urlOriginal,
        path: mirrorEntry.vault_path,
        category: mirrorEntry.category,
        title: mirrorEntry.title
      });
      continue;
    }

    // Check ever_seen_ledger (already pending?)
    const ledgerEntry = await ledgerGetByUrl(env.DB, urlNorm);
    if (ledgerEntry) {
      // Already seen — update forward count
      await ledgerUpdateForward(env.DB, urlNorm, message_id);
      if (mirrorEntry && mirrorEntry.status === 'pending') {
        results.pending.push({ url: urlNorm, original: urlOriginal });
      } else if (!mirrorEntry) {
        results.pending.push({ url: urlNorm, original: urlOriginal });
      }
      continue;
    }

    // New URL — insert into ledger
    await ledgerInsert(env.DB, {
      url_normalized: urlNorm,
      url_original: urlOriginal,
      url_type: 'github',
      github_owner: githubInfo?.owner || null,
      github_repo: githubInfo?.repo || null,
      first_seen_at: received_at,
      last_seen_at: now(),
      telegram_message_id: message_id,
      telegram_chat_id: chat_id
    });

    // Get the ledger entry to retrieve its ID (for callback_data)
    const newLedgerEntry = await ledgerGetByUrl(env.DB, urlNorm);

    // Update KV cache
    await cacheSetDedup(env.CACHE, urlNorm, { status: 'pending' });

    // Log activity
    await activityLog(env.DB, 'received', urlNorm, `Received: ${githubInfo.owner}/${githubInfo.repo}`);

    results.new.push({
      url: urlNorm,
      original: urlOriginal,
      owner: githubInfo.owner,
      repo: githubInfo.repo,
      ledgerId: newLedgerEntry?.id || null
    });

    // Enqueue GitHub enrichment (Layer 3 — will add metadata fetch)
    // For now, we just send the basic reply
  }

  // Send reply
  await sendReply(env, chat_id, results, message_id);

  // Enrich new URLs with GitHub metadata (Layer 3)
  for (const item of results.new) {
    try {
      await env.INGEST_QUEUE.send(JSON.stringify({
        type: 'enrich',
        url_normalized: item.url,
        chat_id: chat_id,
        owner: item.owner,
        repo: item.repo,
        bot_reply_message_id: null // Will be updated after reply is sent
      }));
    } catch (err) {
      console.error('Enrich queue error:', err);
    }
  }
}

// ========================================
// Send auto-reply based on processing results
// ========================================

async function sendReply(env, chatId, results, originalMessageId) {
  const total = results.new.length + results.pending.length +
                results.inVault.length + results.decommissioned.length +
                results.deadLetter.length + results.nonGithub.length;

  // Single URL — detailed reply
  if (total === 1) {
    if (results.new.length === 1) {
      const item = results.new[0];
      const replyText =
        `📋 <b>Received</b>: ${item.owner}/${item.repo}\n` +
        `🔗 ${item.url}\n` +
        `Status: <b>Pending processing</b>`;

      // Use ledger ID in callback_data (Telegram limit is 64 bytes)
      // Full URL would exceed the limit for long repo names
      const callbackData = item.ledgerId
        ? `decomm:${item.ledgerId}`
        : `decomm:${item.url}`.substring(0, 64);

      const reply = await sendMessage(env, chatId, replyText, {
        reply_markup: inlineKeyboard([[
          { text: '🗑️ Mark Decommission', callback_data: callbackData }
        ]])
      });

      if (reply) {
        await ledgerUpdateBotReply(env.DB, item.url, reply.message_id);
      }
    } else if (results.pending.length === 1) {
      const item = results.pending[0];
      await sendMessage(env, chatId,
        `⏳ <b>Already pending</b>: ${item.url}\n` +
        `(Already forwarded — waiting for desktop to process)`
      );
    } else if (results.inVault.length === 1) {
      const item = results.inVault[0];
      await sendMessage(env, chatId,
        `📚 <b>Already in vault</b>: ${item.url}\n` +
        `📂 ${item.path}\n` +
        (item.category ? `🏷️ ${item.category}\n` : '') +
        (item.title ? `📝 ${item.title}` : '')
      );
    } else if (results.decommissioned.length === 1) {
      const item = results.decommissioned[0];
      await sendMessage(env, chatId,
        `🗑️ <b>Already decommissioned</b>: ${item.url}\n` +
        `(This repo is marked as dead — 404 or manually removed)`
      );
    } else if (results.nonGithub.length === 1) {
      const item = results.nonGithub[0];
      await sendMessage(env, chatId,
        `⚠️ <b>Non-GitHub URL — skipped</b>: ${item.url}\n` +
        `(Only GitHub links are tracked)`
      );
    }
    return;
  }

  // Multiple URLs — summary reply
  const parts = [`📋 <b>Received ${total} links:</b>\n`];

  if (results.new.length > 0) {
    parts.push(`\n✅ <b>New (pending): ${results.new.length}</b>`);
    const shown = results.new.slice(0, 5);
    for (const item of shown) {
      parts.push(`  • ${item.owner}/${item.repo}`);
    }
    if (results.new.length > 5) {
      parts.push(`  ... and ${results.new.length - 5} more`);
    }
  }

  if (results.inVault.length > 0) {
    parts.push(`\n📚 <b>Already in vault:</b> ${results.inVault.length}`);
    const shown = results.inVault.slice(0, 3);
    for (const item of shown) {
      parts.push(`  • ${item.url} → ${item.path}`);
    }
    if (results.inVault.length > 3) {
      parts.push(`  ... and ${results.inVault.length - 3} more`);
    }
  }

  if (results.decommissioned.length > 0) {
    parts.push(`\n🗑️ <b>Already decommissioned:</b> ${results.decommissioned.length}`);
  }

  if (results.nonGithub.length > 0) {
    parts.push(`\n⚠️ <b>Non-GitHub:</b> ${results.nonGithub.length}`);
  }

  if (results.pending.length > 0) {
    parts.push(`\n⏳ <b>Already pending:</b> ${results.pending.length}`);
  }

  if (results.new.length > 0) {
    parts.push(`\nProcessing ${results.new.length} new link(s)...`);
  }

  await sendMessage(env, chatId, parts.join('\n'));
}

// ========================================
// GitHub metadata enrichment (Layer 3)
// ========================================

async function enrichGithubMetadata(data, env) {
  const { url_normalized, chat_id, owner, repo } = data;

  try {
    const response = await fetch(`https://api.github.com/repos/${owner}/${repo}`, {
      headers: {
        'Authorization': `token ${env.GITHUB_PAT}`,
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': 'github-curator-bot'
      }
    });

    if (response.status === 404) {
      // Repo is dead — decommission it
      await decommissionInsert(env.DB, url_normalized, 'bot', '404', 'HTTP 404 during enrichment', null);
      await cacheSetDedup(env.CACHE, url_normalized, { status: 'decommissioned' });
      await activityLog(env.DB, 'decommissioned', url_normalized, `Decommissioned (404): ${owner}/${repo}`);

      // Edit the bot reply if we have it
      const ledger = await ledgerGetByUrl(env.DB, url_normalized);
      if (ledger?.bot_reply_message_id) {
        const { editMessage } = await import('./telegram.js');
        await editMessage(env, chat_id, ledger.bot_reply_message_id,
          `🗑️ <b>Decommissioned (404)</b>: ${owner}/${repo}\n` +
          `Repo not found on GitHub.`
        );
      }
      return;
    }

    if (response.status === 301) {
      // Redirect — repo was renamed
      // Layer 11 will handle this properly
      // For now, just log it
      console.log(`GitHub redirect for ${owner}/${repo}`);
      return;
    }

    if (!response.ok) {
      console.error(`GitHub API error: ${response.status}`);
      return;
    }

    const repoData = await response.json();

    // Fetch README excerpt (first 500 chars)
    let readmeExcerpt = null;
    try {
      const readmeResponse = await fetch(
        `https://api.github.com/repos/${owner}/${repo}/readme`,
        {
          headers: {
            'Authorization': `token ${env.GITHUB_PAT}`,
            'Accept': 'application/vnd.github.v3.raw',
            'User-Agent': 'github-curator-bot'
          }
        }
      );
      if (readmeResponse.ok) {
        const readmeText = await readmeResponse.text();
        readmeExcerpt = truncate(readmeText, 500);
      }
    } catch (e) {
      // README fetch is optional
    }

    // Update ledger with GitHub metadata
    await ledgerUpdateGithubMetadata(env.DB, url_normalized, {
      stars: repoData.stargazers_count,
      description: repoData.description,
      language: repoData.language,
      topics: repoData.topics,
      readme_excerpt: readmeExcerpt
    });

    // Edit bot reply with enriched info
    const ledger = await ledgerGetByUrl(env.DB, url_normalized);
    if (ledger?.bot_reply_message_id) {
      const { editMessage } = await import('./telegram.js');
      const starsStr = formatStars(repoData.stargazers_count);
      const desc = repoData.description ? truncate(repoData.description, 100) : 'No description';

      // Use ledger ID in callback_data (Telegram limit is 64 bytes)
      const callbackData = ledger.id
        ? `decomm:${ledger.id}`
        : `decomm:${url_normalized}`.substring(0, 64);

      await editMessage(env, chat_id, ledger.bot_reply_message_id,
        `📋 <b>Received</b>: ${owner}/${repo}\n` +
        `⭐ ${starsStr} — ${desc}\n` +
        (repoData.language ? `💻 ${repoData.language}\n` : '') +
        `Status: <b>Pending processing</b>`,
        {
          reply_markup: inlineKeyboard([[
            { text: '🗑️ Mark Decommission', callback_data: callbackData }
          ]])
        }
      );
    }
  } catch (err) {
    console.error('GitHub enrichment error:', err);
    // Enrichment failure is non-fatal — the URL is still in the ledger
  }
}
