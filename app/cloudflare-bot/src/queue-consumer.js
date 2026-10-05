// ========================================
// queue-consumer.js — Queue consumer
// ========================================
// Reads messages from the ingest queue, processes URLs,
// writes to D1, and sends auto-replies.
// 7-day retry buffer = no link left behind.

import {
  normalizeUrl, normalizeUrlTyped, isGitHubUrl, parseGitHubUrl, formatStars,
  domainMatches, domainsFromEnv, scrubUrlToken, extractUrls,
  mapGithubIoUrl, blockedDomainsFromEnv,
  DEFAULT_BLOCKED_DOMAINS, DEFAULT_SELF_DOMAINS,
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
// DLQ drain — the "no link left behind" last leg (v0.25.0)
// ========================================
// Messages that exhausted their retries on curator-ingest land here.
// The job is NOT to retry them (a poison message must never loop) — it
// is to record every URL the message still carries into the permanent
// dead_letters table (D1), best-effort notify the user, and ack.

export async function handleDeadLetterQueue(batch, env) {
  for (const message of batch.messages) {
    try {
      const data = JSON.parse(message.body);

      if (data.type === 'enrich') {
        // v0.26.0 — the enrich-message DLQ gap: an enrich message that
        // exhausted its retries used to be parsed as type 'urls', find
        // no URLs and be acked SILENTLY. The link itself is already in
        // the ledger (layer 1) — only the metadata reply edit was lost,
        // and the receipt reply stays honestly "Pending processing".
        // Record the failure in the activity log so it is never
        // silently swallowed, then ack (never retry).
        try {
          await activityLog(env.DB, 'dead_letter', data.url_normalized || null,
            'GitHub enrichment failed after retries — link stays ' +
            'ledgered; the metadata reply was not sent');
        } catch (e) {
          console.error('DLQ enrich activityLog error:', e);
        }
      }

      const urls = (data.type === 'urls' && Array.isArray(data.urls)) ? data.urls : [];

      for (const rawUrl of urls) {
        // Same canonical identity as the intake path (v0.26.0 github.io
        // parity) so a dead-lettered pages URL matches its ledger row.
        const urlNorm = normalizeUrlTyped(mapGithubIoUrl(rawUrl) || rawUrl);
        const urlOriginal = (rawUrl || '').replace(/[.,);]+$/, '');
        try {
          await deadLetterInsert(env.DB, {
            url_normalized: urlNorm,
            url_original: scrubUrlToken(urlOriginal),
            reason: 'dlq_exhausted',
            first_attempted_at: data.received_at || now(),
            last_attempted_at: now(),
            telegram_message_id: data.message_id || null
          });
        } catch (e) {
          console.error('DLQ deadLetterInsert error:', e);
        }
      }

      // Best-effort notice to the user (the link never got its
      // 'Received' reply — the consumer kept failing). Never fatal.
      if (urls.length > 0 && data.chat_id && env.BOT_TOKEN) {
        try {
          await sendMessage(env, data.chat_id,
            `💀 <b>Recording problem</b> — I could not fully process ${urls.length === 1 ? 'this link' : `these ${urls.length} links`} after several tries.\n` +
            `They are parked in the bot's dead letters (see the dashboard — nothing is lost).`);
        } catch (e) {
          console.error('DLQ notify error:', e);
        }
      }

      try {
        await activityLog(env.DB, 'dead_letter', null,
          `DLQ drained: ${urls.length} link(s) recorded (reason dlq_exhausted)`);
      } catch (e) {
        console.error('DLQ activityLog error:', e);
      }
    } catch (err) {
      // Unparseable body — nothing recordable; ack so it can never loop.
      console.error('DLQ consumer (unparseable body, acking):', err);
    }
    // ALWAYS ack on the DLQ — there is no queue behind this queue.
    if (typeof message.ack === 'function') message.ack();
  }
}

// ========================================
// Process a message containing URLs
// ========================================

async function processUrlsMessage(data, env) {
  const { urls, chat_id, user_id, message_id, received_at } = data;

  // v0.22.0 — the bot accepts EVERY link, not just GitHub. The desktop's
  // Websites pipeline (v0.11.0+) fetches non-GitHub links into the Websites
  // vault; the bot's job is to confirm receipt and track them in the ledger
  // (SPEC §4.2: "Telegram only confirms receipt"). Policy lists mirror the
  // desktop (blocked = never fetched; self = the bot's own hosts, never
  // stored because the query can carry a live token).
  // v0.28.0 — blockedDomainsFromEnv: THE LAW is the floor; BLOCKED_DOMAINS
  // env extras can only add to it (x/twitter, GitHub group, HuggingFace,
  // Instagram, Facebook, LinkedIn — same list as the desktop's law).
  const blockedDomains = blockedDomainsFromEnv(env.BLOCKED_DOMAINS);
  const selfDomains = domainsFromEnv(env.SELF_DOMAINS, DEFAULT_SELF_DOMAINS);

  // Deduplicate URLs within the same message. Identity is website-aware
  // (v0.22.0): GitHub URLs keep the frozen normalizeUrl semantics, every
  // other URL keeps meaningful query params (a YouTube ?v= IS the page).
  // v0.26.0 — GitHub Pages parity (the desktop's links.py §4.2 map):
  // owner.github.io/<repo> pages map to github.com/<owner>/<repo> BEFORE
  // typing/normalizing, so the ledger records the canonical repo URL with
  // url_type 'github' — the same identity the desktop routes and dedupes
  // on. A bare owner.github.io site is a real website and stays as-is.
  const canonicalOf = (u) => mapGithubIoUrl(u) || u;
  const uniqueUrls = [...new Set(urls.map(u => normalizeUrlTyped(canonicalOf(u))))];
  const originalUrlsMap = {};
  for (const rawUrl of urls) {
    const normalized = normalizeUrlTyped(canonicalOf(rawUrl));
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
    blocked: [],
    self: []
  };

  // Process each URL
  for (const urlNorm of uniqueUrls) {
    const urlOriginal = originalUrlsMap[urlNorm];
    // v0.26.0 — typing follows the CANONICAL form (a mapped pages URL
    // is its repo); urlOriginal stays what the user actually sent.
    const github = isGitHubUrl(normalizeUrl(canonicalOf(urlOriginal)));
    const githubInfo = github ? parseGitHubUrl(urlNorm) : null;
    const urlType = github ? 'github' : 'non_github';

    // Self domains (the bot's own worker) — never stored, never fetched.
    // The URL shown back is query-stripped so a live token never echoes.
    if (domainMatches(urlOriginal, selfDomains)) {
      results.self.push({ url: normalizeUrl(urlOriginal), original: urlOriginal });
      continue;
    }

    // Blocked domains (v0.28.0: THE LAW — x/twitter, GitHub group,
    // HuggingFace, Instagram, Facebook, LinkedIn) — recorded as dead
    // letters with their own reason; they are never fetched by the
    // desktop either. GitHub REPO links are already typed 'github' and
    // skip this check; only non-repo paths on the banned hosts land here.
    if (!github && domainMatches(urlOriginal, blockedDomains)) {
      await deadLetterInsert(env.DB, {
        url_normalized: urlNorm,
        url_original: scrubUrlToken(urlOriginal),
        reason: 'blocked_domain',
        first_attempted_at: received_at,
        last_attempted_at: now(),
        telegram_message_id: message_id
      });
      results.blocked.push({ url: urlNorm, original: urlOriginal });
      continue;
    }

    // Check decommissioned
    const isDecommissioned = await decommissionIsDecommissioned(env.DB, urlNorm);
    if (isDecommissioned) {
      results.decommissioned.push({ url: urlNorm, original: urlOriginal, isWebsite: !github });
      continue;
    }

    // Check dead letters (invalid format / persistent fetch failures /
    // blocked domains — the v0.01 'not_github' tail was migrated to the
    // ledger by migrate-not-github.sql at the v0.22.0 deploy)
    const isDead = await deadLetterIsDead(env.DB, urlNorm);
    if (isDead) {
      results.deadLetter.push({ url: urlNorm, original: urlOriginal, isWebsite: !github });
      continue;
    }

    // Check vault_mirror (already processed?)
    const mirrorEntry = await mirrorGetByUrl(env.DB, urlNorm);
    if (mirrorEntry && mirrorEntry.status === 'in_vault') {
      results.inVault.push({
        url: urlNorm,
        original: urlOriginal,
        isWebsite: !github,
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
        results.pending.push({ url: urlNorm, original: urlOriginal, isWebsite: !github });
      } else if (!mirrorEntry) {
        results.pending.push({ url: urlNorm, original: urlOriginal, isWebsite: !github });
      }
      continue;
    }

    // New URL — insert into ledger (GitHub repo OR website). Secret
    // query values are scrubbed from the stored original (v0.21.0 parity).
    await ledgerInsert(env.DB, {
      url_normalized: urlNorm,
      url_original: scrubUrlToken(urlOriginal),
      url_type: urlType,
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
    if (github) {
      await activityLog(env.DB, 'received', urlNorm, `Received: ${githubInfo.owner}/${githubInfo.repo}`);
    } else {
      await activityLog(env.DB, 'received', urlNorm, `Received website: ${urlNorm}`);
    }

    results.new.push({
      url: urlNorm,
      original: urlOriginal,
      isWebsite: !github,
      owner: githubInfo?.owner || null,
      repo: githubInfo?.repo || null,
      ledgerId: newLedgerEntry?.id || null
    });
  }

  // Send reply
  await sendReply(env, chat_id, results, message_id);

  // Enrich new GITHUB URLs with metadata (Layer 3) — websites are
  // classified by the desktop's Websites pipeline, not here.
  for (const item of results.new.filter(i => !i.isWebsite)) {
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
                results.deadLetter.length + results.blocked.length +
                results.self.length;

  // Single URL — detailed reply
  if (total === 1) {
    if (results.new.length === 1) {
      const item = results.new[0];
      if (item.isWebsite) {
        // v0.22.0 — websites are received and tracked too
        const callbackData = item.ledgerId
          ? `decomm:${item.ledgerId}`
          : `decomm:${item.url}`.substring(0, 64);
        const reply = await sendMessage(env, chatId,
          `🌐 <b>Received — website</b>: ${item.url}\n` +
          `Status: <b>Pending processing</b> (Websites vault)`,
          {
            reply_markup: inlineKeyboard([[
              { text: '🗑️ Mark Decommission', callback_data: callbackData }
            ]])
          }
        );
        if (reply) {
          await ledgerUpdateBotReply(env.DB, item.url, reply.message_id);
        }
        return;
      }
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
      if (item.isWebsite) {
        await sendMessage(env, chatId,
          `⏳ <b>Already pending</b> (website): ${item.url}\n` +
          `(Already forwarded — waiting for desktop to process)`
        );
      } else {
        await sendMessage(env, chatId,
          `⏳ <b>Already pending</b>: ${item.url}\n` +
          `(Already forwarded — waiting for desktop to process)`
        );
      }
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
    } else if (results.blocked.length === 1) {
      const item = results.blocked[0];
      await sendMessage(env, chatId,
        `🚫 <b>Blocked domain — never fetched</b>: ${item.url}\n` +
        `(x.com / twitter.com / t.co links are recorded but not processed, by policy)`
      );
    } else if (results.self.length === 1) {
      const item = results.self[0];
      await sendMessage(env, chatId,
        `🤖 <b>Own link — ignored</b>: ${item.url}\n` +
        `(The bot's own links are never processed)`
      );
    } else if (results.deadLetter.length === 1) {
      const item = results.deadLetter[0];
      await sendMessage(env, chatId,
        `💀 <b>Dead letter</b>: ${item.url}\n` +
        `(Previously marked unprocessable — see /notfound)`
      );
    }
    return;
  }

  // Multiple URLs — summary reply
  // v0.29.0 — batch pastes (the owner's "50 websites, one line each, in
  // ONE message"): the lead line says how many NEW links were received,
  // not just the message total — "50 new links received" when everything
  // is new; otherwise the split ("Received 60 links — 41 new").
  const lead = (results.new.length === total && total > 0)
    ? `📋 <b>${total} new link${total === 1 ? '' : 's'} received</b>\n`
    : `📋 <b>Received ${total} links — ${results.new.length} new</b>\n`;
  const parts = [lead];

  const newGithub = results.new.filter(i => !i.isWebsite);
  const newWebsites = results.new.filter(i => i.isWebsite);

  if (newGithub.length > 0) {
    parts.push(`\n✅ <b>New repos (pending): ${newGithub.length}</b>`);
    const shown = newGithub.slice(0, 5);
    for (const item of shown) {
      parts.push(`  • ${item.owner}/${item.repo}`);
    }
    if (newGithub.length > 5) {
      parts.push(`  ... and ${newGithub.length - 5} more`);
    }
  }

  if (newWebsites.length > 0) {
    parts.push(`\n🌐 <b>New websites (pending): ${newWebsites.length}</b>`);
    const shown = newWebsites.slice(0, 5);
    for (const item of shown) {
      parts.push(`  • ${truncate(item.url, 60)}`);
    }
    if (newWebsites.length > 5) {
      parts.push(`  ... and ${newWebsites.length - 5} more`);
    }
  }

  if (results.inVault.length > 0) {
    parts.push(`\n📚 <b>Already in vault:</b> ${results.inVault.length}`);
    const shown = results.inVault.slice(0, 3);
    for (const item of shown) {
      parts.push(`  • ${truncate(item.url, 50)} → ${item.path}`);
    }
    if (results.inVault.length > 3) {
      parts.push(`  ... and ${results.inVault.length - 3} more`);
    }
  }

  if (results.decommissioned.length > 0) {
    parts.push(`\n🗑️ <b>Already decommissioned:</b> ${results.decommissioned.length}`);
  }

  if (results.blocked.length > 0) {
    parts.push(`\n🚫 <b>Blocked domains (never fetched):</b> ${results.blocked.length}`);
  }

  if (results.self.length > 0) {
    parts.push(`\n🤖 <b>Own links (ignored):</b> ${results.self.length}`);
  }

  if (results.deadLetter.length > 0) {
    parts.push(`\n💀 <b>Dead letters:</b> ${results.deadLetter.length}`);
  }

  const pendingGithub = results.pending.filter(i => !i.isWebsite);
  const pendingWebsites = results.pending.filter(i => i.isWebsite);
  if (pendingGithub.length > 0) {
    parts.push(`\n⏳ <b>Already pending:</b> ${pendingGithub.length}`);
  }
  if (pendingWebsites.length > 0) {
    parts.push(`\n⏳ <b>Already pending (websites):</b> ${pendingWebsites.length}`);
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
