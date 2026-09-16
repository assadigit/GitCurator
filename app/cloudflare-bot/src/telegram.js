// ========================================
// telegram.js — Telegram Bot API helpers
// ========================================

/**
 * Send a message via Telegram Bot API
 */
export async function sendMessage(env, chatId, text, options = {}) {
  const apiUrl = `https://api.telegram.org/bot${env.BOT_TOKEN}/sendMessage`;

  const body = {
    chat_id: chatId,
    text: text,
    parse_mode: options.parse_mode || 'HTML',
    disable_web_page_preview: options.disable_web_page_preview !== false,
    reply_markup: options.reply_markup || undefined
  };

  // Remove undefined fields
  Object.keys(body).forEach(k => body[k] === undefined && delete body[k]);

  const response = await fetch(apiUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });

  const data = await response.json();
  if (!data.ok) {
    console.error('Telegram sendMessage error:', data.description);
    return null;
  }
  return data.result;
}

/**
 * Edit an existing message (for edit-on-status-change)
 */
export async function editMessage(env, chatId, messageId, text, options = {}) {
  const apiUrl = `https://api.telegram.org/bot${env.BOT_TOKEN}/editMessageText`;

  const body = {
    chat_id: chatId,
    message_id: messageId,
    text: text,
    parse_mode: options.parse_mode || 'HTML',
    disable_web_page_preview: options.disable_web_page_preview !== false,
    reply_markup: options.reply_markup || undefined
  };

  Object.keys(body).forEach(k => body[k] === undefined && delete body[k]);

  const response = await fetch(apiUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });

  const data = await response.json();
  if (!data.ok) {
    // Message might be too old to edit, or content is the same
    console.error('Telegram editMessage error:', data.description);
    return null;
  }
  return data.result;
}

/**
 * Answer a callback query (inline button press)
 */
export async function answerCallbackQuery(env, callbackQueryId, text = null, showAlert = false) {
  const apiUrl = `https://api.telegram.org/bot${env.BOT_TOKEN}/answerCallbackQuery`;

  const body = {
    callback_query_id: callbackQueryId
  };
  if (text) body.text = text;
  if (showAlert) body.show_alert = showAlert;

  await fetch(apiUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
}

/**
 * Send a DM to the user (for error alerts)
 */
export async function sendDM(env, userId, text) {
  return sendMessage(env, userId, text, { disable_web_page_preview: true });
}

/**
 * Build an inline keyboard
 * @param {Array<Array<{text: string, callback_data?: string, url?: string}>>} buttons
 */
export function inlineKeyboard(buttons) {
  return JSON.stringify({ inline_keyboard: buttons });
}

/**
 * Check if user is in allowed list
 */
export function isUserAllowed(env, userId) {
  const allowed = (env.ALLOWED_USER_IDS || '').split(',').map(s => s.trim());
  return allowed.includes(String(userId));
}
