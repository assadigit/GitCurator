// banish-gate.test.js — v0.60.0 THE CONFIRMATION GATE: the desktop's
// propose -> poll -> result round-trip, and the owner's two buttons
// (banish_yes / banish_no) over the webhook's callback_query.
// The owner's ask (verbatim): "it must show 'X number of notes should
// be deleted' do you confirm? … I confirm deletion, then they will get
// deleted from both vaults and never be fetched again."
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash, createHmac } from 'node:crypto';
import worker from '../src/index.js';
import { handleWebhook } from '../src/webhook.js';
import { makeEnv, FakeRequest } from '../test-helpers/fake-db.mjs';

function sha256(s) { return createHash('sha256').update(s).digest('hex'); }

// Zero-network rule: Telegram API calls answered with canned results;
// every call is CAPTURED so the message text and buttons are asserted.
const sent = [];
const realFetch = globalThis.fetch;
globalThis.fetch = async (url, opts) => {
  if (String(url).includes('api.telegram.org')) {
    const call = { url: String(url), body: JSON.parse(opts?.body || '{}') };
    sent.push(call);
    if (String(url).includes('/answerCallbackQuery')) {
      return { ok: true, json: async () => ({ ok: true, result: true }) };
    }
    return { ok: true, json: async () => ({ ok: true, result: { message_id: 77 } }) };
  }
  return realFetch(url, opts);
};

function signedRequest({ url, method = 'GET', body = '{}', secret = 'raw-secret-64-hex' }) {
  const u = new URL(url);
  const ts = String(Date.now());
  const pathWithQuery = u.pathname + u.search;
  const message = `${method}\n${pathWithQuery}\n${ts}\n${sha256(body)}`;
  const sig = createHmac('sha256', sha256(secret)).update(message).digest('hex');
  return new FakeRequest(url, {
    method,
    headers: {
      'Content-Type': 'application/json',
      'X-Auth-Install': 'install-1',
      'X-Auth-Timestamp': ts,
      'X-Auth-Signature': sig
    },
    body
  });
}

async function pairedEnv() {
  const env = makeEnv();
  env.BOT_TOKEN = 'test-token';
  env.DB.installs.set('install-1', {
    install_id: 'install-1',
    shared_secret_hash: sha256('raw-secret-64-hex'),
    last_seen_at: new Date().toISOString()
  });
  return env;
}

const ITEMS = [
  { url: 'https://example.com/one', title: 'Example One',
    marker: '🗑️', door: 'note tag' },
  { url: 'https://example.com/two', title: 'Example Two',
    marker: 'auto_delete', door: 'master-table gesture' }
];

async function propose(env, count = 2, items = ITEMS) {
  const body = JSON.stringify({ count, items, timeout_s: 60 });
  const res = await worker.fetch(signedRequest({
    url: 'https://bot.example/api/banish/propose', method: 'POST', body
  }), env, {});
  return { res, data: await res.json() };
}

test('propose: the ask carries the count, the list, and the two buttons', async () => {
  const env = await pairedEnv();
  sent.length = 0;
  const { res, data } = await propose(env);
  assert.equal(res.status, 200);
  assert.equal(data.success, true);
  assert.ok(data.id, 'the id the desktop polls');
  // ONE Telegram message to the allowed chat, with the ask:
  const send = sent.filter(c => c.url.includes('/sendMessage'));
  assert.equal(send.length, 1);
  assert.equal(send[0].body.chat_id, 12345);
  const text = send[0].body.text;
  assert.match(text, /Deletion review — 2 notes marked for deletion/);
  assert.match(text, /Example One/);
  assert.match(text, /https:\/\/example\.com\/one/);
  assert.match(text, /auto_delete/);
  assert.match(text, /never get fetched again/);
  // the two buttons address THIS ask's id:
  const kb = JSON.parse(send[0].body.reply_markup).inline_keyboard[0];
  assert.equal(kb.length, 2);
  assert.match(kb[0].text, /Delete all 2/);
  assert.equal(kb[0].callback_data, `banish_yes:${data.id}`);
  assert.match(kb[1].text, /Keep all 2/);
  assert.equal(kb[1].callback_data, `banish_no:${data.id}`);
  // the ask rests in the state table, pending:
  const row = JSON.parse(env.DB.state.get(`banish_confirm:${data.id}`));
  assert.equal(row.status, 'pending');
  assert.equal(row.count, 2);
  assert.equal(row.chat_id, 12345);
  assert.equal(env.DB.state.get('banish_active_confirm'), data.id);
});

test('propose: unsigned asks are rejected (the HMAC law)', async () => {
  const env = await pairedEnv();
  const res = await worker.fetch(new FakeRequest(
    'https://bot.example/api/banish/propose', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ count: 1, items: ITEMS })
    }), env, {});
  assert.equal(res.status, 401);
});

test('status: the desktop polls pending, then confirmed after the button', async () => {
  const env = await pairedEnv();
  const { data } = await propose(env);
  const poll = async () => {
    const res = await worker.fetch(signedRequest({
      url: `https://bot.example/api/banish/status?id=${data.id}`
    }), env, {});
    return { res, d: await res.json() };
  };
  let { res, d } = await poll();
  assert.equal(res.status, 200);
  assert.equal(d.status, 'pending');
  assert.equal(d.count, 2);
  // the owner presses 🗑️ Delete (the webhook's callback_query):
  sent.length = 0;
  await handleWebhook(new FakeRequest('https://bot.example/webhook', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ callback_query: {
      id: 'cbq-1', from: { id: 12345 },
      message: { chat: { id: 12345 }, message_id: 77 },
      data: `banish_yes:${data.id}`
    } })
  }), env);
  // the message was edited to the confirmed story:
  const edits = sent.filter(c => c.url.includes('/editMessageText'));
  assert.equal(edits.length, 1);
  assert.match(edits[0].body.text, /Confirmed/);
  assert.match(edits[0].body.text, /removing 2 note\(s\)/);
  // and the poll now answers confirmed:
  ({ res, d } = await poll());
  assert.equal(d.status, 'confirmed');
});

test('the Keep button declines; a second press answers "already answered"', async () => {
  const env = await pairedEnv();
  const { data } = await propose(env);
  const press = () => handleWebhook(new FakeRequest(
    'https://bot.example/webhook', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ callback_query: {
        id: 'cbq-2', from: { id: 12345 },
        message: { chat: { id: 12345 }, message_id: 77 },
        data: `banish_no:${data.id}`
      } })
    }), env);
  sent.length = 0;
  await press();
  const row = JSON.parse(env.DB.state.get(`banish_confirm:${data.id}`));
  assert.equal(row.status, 'declined');
  const edits = sent.filter(c => c.url.includes('/editMessageText'));
  assert.match(edits[0].body.text, /Kept/);
  assert.match(edits[0].body.text, /nothing deleted/);
  // the second press is a no-op (idempotent answer):
  sent.length = 0;
  await press();
  const answers = sent.filter(c => c.url.includes('/answerCallbackQuery'));
  assert.equal(answers.length, 1);
  assert.match(answers[0].body.text, /Already answered/);
});

test('result: the confirmed run closes with the owner\'s example line', async () => {
  const env = await pairedEnv();
  const { data } = await propose(env);
  sent.length = 0;
  const res = await worker.fetch(signedRequest({
    url: 'https://bot.example/api/banish/result', method: 'POST',
    body: JSON.stringify({ id: data.id, outcome: 'deleted', deleted: 3 })
  }), env, {});
  assert.equal(res.status, 200);
  const d = await res.json();
  assert.equal(d.status, 'done');
  const edits = sent.filter(c => c.url.includes('/editMessageText'));
  assert.equal(edits.length, 1);
  // "10 Websites Removed and will never fetch again because you …":
  assert.match(edits[0].body.text, /3 websites removed/);
  assert.match(edits[0].body.text, /never to be fetched again/);
  assert.match(edits[0].body.text, /You confirmed the deletion/);
});

test('result: the desktop\'s timeout edits the no-answer story', async () => {
  const env = await pairedEnv();
  const { data } = await propose(env);
  sent.length = 0;
  const res = await worker.fetch(signedRequest({
    url: 'https://bot.example/api/banish/result', method: 'POST',
    body: JSON.stringify({ id: data.id, outcome: 'timeout' })
  }), env, {});
  assert.equal(res.status, 200);
  const row = JSON.parse(env.DB.state.get(`banish_confirm:${data.id}`));
  assert.equal(row.status, 'timeout');
  const edits = sent.filter(c => c.url.includes('/editMessageText'));
  assert.match(edits[0].body.text, /No answer in time/);
  assert.match(edits[0].body.text, /nothing deleted/);
});

test('a newer ask supersedes the pending one (one live question at a time)', async () => {
  const env = await pairedEnv();
  const first = await propose(env);
  sent.length = 0;
  const second = await propose(env, 1, [ITEMS[0]]);
  assert.notEqual(second.data.id, first.data.id);
  const row = JSON.parse(env.DB.state.get(`banish_confirm:${first.data.id}`));
  assert.equal(row.status, 'superseded');
  assert.equal(env.DB.state.get('banish_active_confirm'), second.data.id);
  const edits = sent.filter(c => c.url.includes('/editMessageText'));
  assert.match(edits[0].body.text, /Superseded/);
  // the superseded buttons answer honestly:
  sent.length = 0;
  await handleWebhook(new FakeRequest('https://bot.example/webhook', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ callback_query: {
      id: 'cbq-3', from: { id: 12345 },
      message: { chat: { id: 12345 }, message_id: 77 },
      data: `banish_yes:${first.data.id}`
    } })
  }), env);
  const answers = sent.filter(c => c.url.includes('/answerCallbackQuery'));
  assert.match(answers[0].body.text, /Already answered/);
});

test('status: an unknown id is a 404, a missing id a 400', async () => {
  const env = await pairedEnv();
  const missing = await worker.fetch(signedRequest({
    url: 'https://bot.example/api/banish/status?id=nope'
  }), env, {});
  assert.equal(missing.status, 404);
  const noId = await worker.fetch(signedRequest({
    url: 'https://bot.example/api/banish/status'
  }), env, {});
  assert.equal(noId.status, 400);
});

test('the ask\'s HTML is escaped (a title with <b> stays inert)', async () => {
  const env = await pairedEnv();
  sent.length = 0;
  await propose(env, 1, [{
    url: 'https://example.com/x', title: 'Cunning <b>title</b> & Co',
    marker: 'delete', door: 'note tag'
  }]);
  const text = sent.find(c => c.url.includes('/sendMessage')).body.text;
  assert.ok(!text.includes('<b>title</b>'), 'raw <b> never rides the title');
  assert.ok(text.includes('&lt;b&gt;title&lt;/b&gt; &amp; Co'));
});
