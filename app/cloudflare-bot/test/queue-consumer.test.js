// queue-consumer.test.js — link intake, dedup, policies, and the DLQ drain.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { handleQueue, handleDeadLetterQueue } from '../src/queue-consumer.js';
import { makeEnv, message } from '../test-helpers/fake-db.mjs';

// Zero-network rule (same as the Python suite): Telegram API calls are
// intercepted and answered with a canned successful sendMessage result.
const realFetch = globalThis.fetch;
globalThis.fetch = async (url, opts) => {
  if (String(url).includes('api.telegram.org')) {
    return { ok: true, json: async () => ({ ok: true, result: { message_id: 1 } }) };
  }
  return realFetch(url, opts);
};

test('intake: a new GitHub link lands in the ledger as github with owner/repo', async () => {
  const env = makeEnv();
  const msg = message({
    type: 'urls', urls: ['https://github.com/owner/repo'],
    chat_id: 1, user_id: 12345, message_id: 10, received_at: '2026-10-01T00:00:00Z'
  });
  await handleQueue({ messages: [msg], queue: 'curator-ingest' }, env);
  assert.ok(msg.acked);
  const row = env.DB.ledger.get('https://github.com/owner/repo');
  assert.ok(row, 'ledger row created');
  assert.equal(row.url_type, 'github');
  assert.equal(row.github_owner, 'owner');
  assert.equal(row.github_repo, 'repo');
});

test('intake: a website link lands in the ledger as non_github (v0.22.0 contract)', async () => {
  const env = makeEnv();
  await handleQueue({ messages: [message({
    type: 'urls', urls: ['https://example.com/tool'],
    chat_id: 1, user_id: 12345, message_id: 11, received_at: '2026-10-01T00:00:00Z'
  })], queue: 'curator-ingest' }, env);
  const row = env.DB.ledger.get('https://example.com/tool');
  assert.ok(row);
  assert.equal(row.url_type, 'non_github');
});

test('dedup: sending the same link again never creates a second ledger row', async () => {
  const env = makeEnv();
  const mk = () => message({
    type: 'urls', urls: ['https://github.com/o/r'],
    chat_id: 1, user_id: 12345, message_id: 12, received_at: '2026-10-01T00:00:00Z'
  });
  await handleQueue({ messages: [mk()], queue: 'curator-ingest' }, env);
  await handleQueue({ messages: [mk()], queue: 'curator-ingest' }, env);
  assert.equal(env.DB.ledger.size, 1);
  assert.equal(env.DB.ledger.get('https://github.com/o/r').forward_count, 2,
    'second forward bumps the counter instead of duplicating');
});

test('dedup: two different links in one message are both recorded once', async () => {
  const env = makeEnv();
  await handleQueue({ messages: [message({
    type: 'urls', urls: ['https://github.com/a/b', 'https://github.com/a/b', 'https://example.com/x'],
    chat_id: 1, user_id: 12345, message_id: 13, received_at: '2026-10-01T00:00:00Z'
  })], queue: 'curator-ingest' }, env);
  assert.equal(env.DB.ledger.size, 2, 'in-message duplicates collapse');
});

test('policy: blocked domains are dead-lettered, never ledger-pending', async () => {
  const env = makeEnv();
  await handleQueue({ messages: [message({
    type: 'urls', urls: ['https://x.com/post/123'],
    chat_id: 1, user_id: 12345, message_id: 14, received_at: '2026-10-01T00:00:00Z'
  })], queue: 'curator-ingest' }, env);
  assert.equal(env.DB.ledger.size, 0, 'x-family never enters the ledger');
  const dead = env.DB.deadLetters.get('https://x.com/post/123');
  assert.ok(dead, 'recorded as a dead letter');
  assert.equal(dead.reason, 'blocked_domain');
});

test('policy: self domains (the bot own links) are stored nowhere', async () => {
  const env = makeEnv();
  await handleQueue({ messages: [message({
    type: 'urls', urls: ['https://github-to-obsidian-bot.aliassadi-plus.workers.dev/auth/?token=abcdefgh12345678'],
    chat_id: 1, user_id: 12345, message_id: 15, received_at: '2026-10-01T00:00:00Z'
  })], queue: 'curator-ingest' }, env);
  assert.equal(env.DB.ledger.size, 0);
  assert.equal(env.DB.deadLetters.size, 0, 'never stored, token never echoed');
});

test('policy: decommissioned links are never re-added to pending', async () => {
  const env = makeEnv();
  env.DB.decommission.push({ url_normalized: 'https://github.com/o/dead', reason: '404' });
  await handleQueue({ messages: [message({
    type: 'urls', urls: ['https://github.com/o/dead'],
    chat_id: 1, user_id: 12345, message_id: 16, received_at: '2026-10-01T00:00:00Z'
  })], queue: 'curator-ingest' }, env);
  assert.equal(env.DB.ledger.size, 0, 'decommissioned link not re-ledgered');
});

test('secrets: secret query values are scrubbed from the stored original', async () => {
  const env = makeEnv();
  await handleQueue({ messages: [message({
    type: 'urls', urls: ['https://example.com/app?token=Zx123456789012345'],
    chat_id: 1, user_id: 12345, message_id: 17, received_at: '2026-10-01T00:00:00Z'
  })], queue: 'curator-ingest' }, env);
  const row = [...env.DB.ledger.values()][0];
  assert.ok(row.url_original.includes('token=…'), `scrubbed, got: ${row.url_original}`);
});

test('failure handling: a crashing consumer message retries (never lost, never acked)', async () => {
  const env = makeEnv();
  const badDb = {
    prepare() {
      return {
        bind() { return this; },
        first: async () => { throw new Error('D1 hiccup'); },
        all: async () => { throw new Error('D1 hiccup'); },
        run: async () => { throw new Error('D1 hiccup'); }
      };
    }
  };
  env.DB = badDb;
  const msg = message({
    type: 'urls', urls: ['https://github.com/o/r'],
    chat_id: 1, user_id: 12345, message_id: 18, received_at: '2026-10-01T00:00:00Z'
  });
  await handleQueue({ messages: [msg], queue: 'curator-ingest' }, env);
  assert.ok(msg.retried, 'message.retry() so the queue redelivers -> DLQ');
  assert.ok(!msg.acked);
});

// ---------------------------------------------------------------------------
// The DLQ drain (v0.25.0 — "no link left behind", the last leg)
// ---------------------------------------------------------------------------

test('DLQ: exhausted-retry links are recorded into dead_letters and acked', async () => {
  const env = makeEnv();
  const msg = message({
    type: 'urls', urls: ['https://github.com/o/stuck', 'https://example.com/also-stuck'],
    chat_id: 1, user_id: 12345, message_id: 19, received_at: '2026-10-01T00:00:00Z'
  });
  await handleDeadLetterQueue({ messages: [msg], queue: 'curator-ingest-dlq' }, env);
  assert.ok(msg.acked, 'DLQ always acks — a poison message can never loop');
  const a = env.DB.deadLetters.get('https://github.com/o/stuck');
  const b = env.DB.deadLetters.get('https://example.com/also-stuck');
  assert.ok(a && b, 'both links recorded');
  assert.equal(a.reason, 'dlq_exhausted');
  assert.equal(b.reason, 'dlq_exhausted');
});

test('DLQ: a link already dead-lettered bumps attempts instead of duplicating', async () => {
  const env = makeEnv();
  const mk = () => message({
    type: 'urls', urls: ['https://github.com/o/stuck'],
    chat_id: 1, user_id: 12345, message_id: 20, received_at: '2026-10-01T00:00:00Z'
  });
  await handleDeadLetterQueue({ messages: [mk()], queue: 'curator-ingest-dlq' }, env);
  await handleDeadLetterQueue({ messages: [mk()], queue: 'curator-ingest-dlq' }, env);
  assert.equal(env.DB.deadLetters.size, 1);
  assert.equal(env.DB.deadLetters.get('https://github.com/o/stuck').attempt_count, 2);
});

test('DLQ: an unparseable body is acked and never crashes the consumer', async () => {
  const env = makeEnv();
  const msg = message('this is not json {{{');
  await handleDeadLetterQueue({ messages: [msg], queue: 'curator-ingest-dlq' }, env);
  assert.ok(msg.acked);
  assert.equal(env.DB.deadLetters.size, 0);
});

test('DLQ: the activity log records the drain', async () => {
  const env = makeEnv();
  await handleDeadLetterQueue({ messages: [message({
    type: 'urls', urls: ['https://example.com/x'],
    chat_id: 1, user_id: 12345, message_id: 21, received_at: '2026-10-01T00:00:00Z'
  })], queue: 'curator-ingest-dlq' }, env);
  assert.ok(env.DB.activity.some(a => a.event_type === 'dead_letter'));
});

// ── v0.26.0 ──────────────────────────────────────────────────────────

test('intake: owner.github.io/<repo> maps to the canonical GitHub URL (parity with the desktop)', async () => {
  const env = makeEnv();
  await handleQueue({ messages: [message({
    type: 'urls', urls: ['https://owner.github.io/repo/?utm_x=1'],
    chat_id: 1, user_id: 12345, message_id: 20, received_at: '2026-10-01T00:00:00Z'
  })], queue: 'curator-ingest' }, env);
  const row = env.DB.ledger.get('https://github.com/owner/repo');
  assert.ok(row, 'ledger row lives under the canonical repo URL');
  assert.equal(row.url_type, 'github');
  assert.equal(row.github_owner, 'owner');
  assert.equal(row.github_repo, 'repo');
  assert.equal(env.DB.ledger.size, 1);
});

test('intake: a bare owner.github.io site stays a website', async () => {
  const env = makeEnv();
  await handleQueue({ messages: [message({
    type: 'urls', urls: ['https://owner.github.io/'],
    chat_id: 1, user_id: 12345, message_id: 21, received_at: '2026-10-01T00:00:00Z'
  })], queue: 'curator-ingest' }, env);
  const row = env.DB.ledger.get('https://owner.github.io/');
  assert.ok(row);
  assert.equal(row.url_type, 'non_github');
});

test('intake: the pages form and the repo form of one link dedupe to a single row', async () => {
  const env = makeEnv();
  await handleQueue({ messages: [message({
    type: 'urls', urls: ['https://github.com/o/r'],
    chat_id: 1, user_id: 12345, message_id: 22, received_at: '2026-10-01T00:00:00Z'
  })], queue: 'curator-ingest' }, env);
  await handleQueue({ messages: [message({
    type: 'urls', urls: ['https://o.github.io/r/'],
    chat_id: 1, user_id: 12345, message_id: 23, received_at: '2026-10-01T00:00:00Z'
  })], queue: 'curator-ingest' }, env);
  assert.equal(env.DB.ledger.size, 1, 'one identity, not two');
  assert.equal(env.DB.ledger.get('https://github.com/o/r').forward_count, 2);
});

test('DLQ: a failed enrich message is audited in the activity log, never silently acked', async () => {
  const env = makeEnv();
  const msg = message({
    type: 'enrich', url_normalized: 'https://github.com/o/r',
    chat_id: 1, owner: 'o', repo: 'r'
  });
  await handleDeadLetterQueue({ messages: [msg], queue: 'curator-ingest-dlq' }, env);
  assert.ok(msg.acked, 'DLQ always acks — no loop behind this queue');
  assert.equal(env.DB.deadLetters.size, 0,
    'the link is NOT dead-lettered — it is already ledgered');
  const entry = env.DB.activity.find(a =>
    a.event_type === 'dead_letter' && /enrichment/i.test(a.message));
  assert.ok(entry, 'the enrichment failure is recorded for the audit trail');
  assert.equal(entry.url, 'https://github.com/o/r');
});
