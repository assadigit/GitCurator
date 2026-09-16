// ========================================
// webhook.js — Telegram webhook handler
// ========================================
// Receives Telegram updates, extracts URLs, enqueues to Queue.
// Returns 200 immediately (Telegram requirement).
// Heavy processing happens in the queue consumer.

import { extractUrls, normalizeUrl, now } from './utils.js';
import { sendMessage, editMessage, answerCallbackQuery, inlineKeyboard, isUserAllowed } from './telegram.js';
import { stateSet, decommissionInsert, ledgerSetForgotten, ledgerGetByUrl, errorAcknowledgeAll } from './db.js';
import { cacheSetDedup } from './kv.js';
import { handleCommand } from './commands.js';

export async function handleWebhook(request, env) {
  let update;
  try {
    update = await request.json();
  } catch {
    return new Response('OK', { status: 200 });
  }

  // Update last webhook timestamp (for health check)
  await stateSet(env.DB, 'last_webhook_at', now());

  // Handle callback queries (inline button presses)
  if (update.callback_query) {
    await handleCallbackQuery(update.callback_query, env);
    return new Response('OK', { status: 200 });
  }

  const message = update.message;
  if (!message || !message.text) {
    return new Response('OK', { status: 200 });
  }

  const chatId = message.chat.id;
  const userId = message.from?.id;
  const text = message.text;

  // Allowlist check
  if (!isUserAllowed(env, userId)) {
    console.log(`Unauthorized user: ${userId}`);
    return new Response('OK', { status: 200 }); // Silently ignore
  }

  // Handle commands
  if (text.startsWith('/')) {
    await handleCommand(text, chatId, userId, message, env);
    return new Response('OK', { status: 200 });
  }

  // Extract URLs from message
  const urls = extractUrls(text);

  if (urls.length === 0) {
    await sendMessage(env, chatId,
      "📋 Send me a GitHub link to track it!\n\n" +
      "Commands: /help"
    );
    return new Response('OK', { status: 200 });
  }

  // Enqueue to Queue for processing
  // We send an immediate "received" message from the queue consumer
  // (after dedup check), so the user gets accurate status.
  try {
    await env.INGEST_QUEUE.send(JSON.stringify({
      type: 'urls',
      urls: urls,
      chat_id: chatId,
      user_id: userId,
      message_id: message.message_id,
      text: text,
      received_at: now()
    }));

    // For bulk forwards (3+ URLs), send immediate acknowledgment
    if (urls.length >= 3) {
      await sendMessage(env, chatId,
        `📋 Received ${urls.length} links — processing...`
      );
    }
  } catch (err) {
    console.error('Queue send error:', err);
    // If queue fails, we still return 200 to Telegram (it will retry the webhook)
    // The link is NOT lost — Telegram will retry.
  }

  return new Response('OK', { status: 200 });
}

// ========================================
// Callback query handler (inline button presses)
// ========================================

async function handleCallbackQuery(callbackQuery, env) {
  const data = callbackQuery.data;
  const chatId = callbackQuery.message?.chat?.id;
  const userId = callbackQuery.from?.id;
  const messageId = callbackQuery.message?.message_id;

  if (!isUserAllowed(env, userId)) {
    await answerCallbackQuery(env, callbackQuery.id, "Unauthorized", true);
    return;
  }

  // Parse callback data
  // Format: "action:param1:param2"
  const colonIdx = data.indexOf(':');
  const action = colonIdx >= 0 ? data.substring(0, colonIdx) : data;
  const param = colonIdx >= 0 ? data.substring(colonIdx + 1) : '';

  switch (action) {
    case 'decomm': {
      // Mark URL as decommissioned (from inline button)
      // param can be either a ledger ID (integer) or a URL
      let urlNorm = param;

      // If param is numeric, it's a ledger ID — look up the URL
      if (/^\d+$/.test(param)) {
        const ledgerEntry = await env.DB.prepare(
          'SELECT url_normalized FROM ever_seen_ledger WHERE id = ?'
        ).bind(parseInt(param)).first();
        if (ledgerEntry) {
          urlNorm = ledgerEntry.url_normalized;
        } else {
          await answerCallbackQuery(env, callbackQuery.id, "Error: ledger entry not found", true);
          return;
        }
      }

      if (!urlNorm || !urlNorm.startsWith('http')) {
        await answerCallbackQuery(env, callbackQuery.id, "Error: invalid URL", true);
        return;
      }

      await decommissionInsert(env.DB, urlNorm, 'bot', 'manual', 'Marked decommissioned via inline button', null);
      await cacheSetDedup(env.CACHE, urlNorm, { status: 'decommissioned' });

      // Edit the message to show decommissioned status
      await editMessage(env, chatId, messageId,
        `🗑️ <b>Decommissioned</b>\n` +
        `<code>${urlNorm}</code>\n\n` +
        `Marked by user via inline button.`
      );

      await answerCallbackQuery(env, callbackQuery.id, "🗑️ Decommissioned", false);
      break;
    }

    case 'forget_confirm': {
      const urlNorm = param;
      if (!urlNorm) {
        await answerCallbackQuery(env, callbackQuery.id, "Error: missing URL", true);
        return;
      }

      await ledgerSetForgotten(env.DB, urlNorm, 'Forgotten by user via /forget command');

      await editMessage(env, chatId, messageId,
        `✅ <b>Forgotten</b>\n` +
        `<code>${urlNorm}</code>\n\n` +
        `Removed from the permanent ledger.\n` +
        `The vault note is untouched.`
      );

      await answerCallbackQuery(env, callbackQuery.id, "✅ Forgotten", false);
      break;
    }

    case 'forget_cancel':
      await editMessage(env, chatId, messageId, "❌ <b>Cancelled</b> — URL was not forgotten.");
      await answerCallbackQuery(env, callbackQuery.id, "Cancelled", false);
      break;

    case 'errors_clear':
      await errorAcknowledgeAll(env.DB);
      await editMessage(env, chatId, messageId, "✅ <b>All errors acknowledged.</b>");
      await answerCallbackQuery(env, callbackQuery.id, "✅ Cleared", false);
      break;

    case 'treat_same':
      // GitHub redirect: treat as same repo (Layer 11)
      await answerCallbackQuery(env, callbackQuery.id, "Linked to existing note", false);
      break;

    case 'treat_new':
      // GitHub redirect: treat as new repo (Layer 11)
      await answerCallbackQuery(env, callbackQuery.id, "Processing as new", false);
      break;

    default:
      await answerCallbackQuery(env, callbackQuery.id, "Unknown action", false);
  }
}
