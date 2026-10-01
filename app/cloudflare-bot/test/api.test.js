// api.test.js — the desktop-facing contract: HMAC (incl. the query-string
// regression), /api/pending shape, /api/verify, /health version.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash, createHmac } from 'node:crypto';
import worker from '../src/index.js';
import { WORKER_VERSION } from '../src/version.js';
import { makeEnv, FakeRequest } from './helpers/fake-db.mjs';

function sha256(s) { return createHash('sha256').update(s).digest('hex'); }

// Exactly how the desktop signs (cloudflare_sync._sign_request): key =
// sha256-hex of the raw shared secret; message = method\npath\nts\nsha256(body).
function signedRequest({ url, method = 'GET', body = '{}', secret = 'raw-secret-64-hex' }) {
  const u = new URL(url);
  const ts = String(Date.now());
  const pathWithQuery = u.pathname + u.search;   // <-- the desktop signs THIS
  const message = `${method}\n${pathWithQuery}\n${ts}\n${sha256(body)}`;
  const keyHex = sha256(secret);
  const sig = createHmac('sha256', keyHex).update(message).digest('hex');
  // FakeRequest: the desktop's urllib sends a body ('{}') on GET too —
  // undici's Request forbids that, so we duck-type it.
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
  env.DB.installs.set('install-1', {
    install_id: 'install-1',
    shared_secret_hash: sha256('raw-secret-64-hex'),
    last_seen_at: new Date().toISOString()
  });
  return env;
}

test('HMAC: an endpoint WITH a query string verifies (the v0.25.0 fix)', async () => {
  const env = await pairedEnv();
  env.DB.decommission.push({
    url_normalized: 'https://github.com/o/r', source: 'bot', reason: '404',
    details: '', decommissioned_at: '2026-10-01T00:00:00Z', desktop_sync_id: null
  });
  const req = signedRequest({
    url: 'https://bot.example/api/decommissions?since=1970-01-01T00:00:00Z'
  });
  const res = await worker.fetch(req, env, {});
  assert.equal(res.status, 200, `expected 200, got ${res.status}`);
  const data = await res.json();
  assert.equal(data.success, true);
  assert.equal(data.decommissions.length, 1);
});

test('HMAC: a tampered signature is rejected with 401', async () => {
  const env = await pairedEnv();
  const req = signedRequest({ url: 'https://bot.example/api/pending' });
  req.headers.set('X-Auth-Signature', '0'.repeat(64));
  const res = await worker.fetch(req, env, {});
  assert.equal(res.status, 401);
});
test('HMAC: a stale timestamp is rejected (5-minute window)', async () => {
  const env = await pairedEnv();
  const u = 'https://bot.example/api/pending';
  const ts = String(Date.now() - 10 * 60 * 1000);
  const message = `GET\n/api/pending\n${ts}\n${sha256('{}')}`;
  const sig = createHmac('sha256', sha256('raw-secret-64-hex')).update(message).digest('hex');
  const res = await worker.fetch(new FakeRequest(u, {
    headers: { 'X-Auth-Install': 'install-1', 'X-Auth-Timestamp': ts, 'X-Auth-Signature': sig },
    body: '{}'
  }), env, {});
  assert.equal(res.status, 401);
});

test('/api/pending returns the ledger shape the desktop consumes', async () => {
  const env = await pairedEnv();
  await env.DB.prepare(`INSERT INTO ever_seen_ledger`).bind(
    'https://github.com/o/r', 'https://github.com/o/r', 'github', 'o', 'r',
    '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z', 1, 10, 1, null
  ).run();
  await env.DB.prepare(`INSERT INTO ever_seen_ledger`).bind(
    'https://example.com/tool', 'https://example.com/tool', 'non_github', null, null,
    '2026-10-01T00:00:01Z', '2026-10-01T00:00:01Z', 1, 11, 1, null
  ).run();
  const res = await worker.fetch(
    signedRequest({ url: 'https://bot.example/api/pending' }), env, {});
  assert.equal(res.status, 200);
  const data = await res.json();
  assert.equal(data.success, true);
  assert.equal(data.count, 2);
  const gh = data.pending.find(p => p.url_type === 'github');
  const web = data.pending.find(p => p.url_type === 'non_github');
  assert.ok(gh && web, 'both pipelines are pending-visible');
  assert.equal(gh.github_owner, 'o');
  assert.equal(gh.github_repo, 'r');
  assert.equal(typeof gh.ledger_id, 'number');
  assert.equal(typeof gh.first_seen_at, 'string');
  assert.equal(typeof gh.forward_count, 'number');
  assert.equal(web.github_metadata, null);
});

test('/api/pending hides in-vault and dead-lettered links', async () => {
  const env = await pairedEnv();
  await env.DB.prepare(`INSERT INTO ever_seen_ledger`).bind(
    'https://github.com/o/in-vault', 'x', 'github', 'o', 'in-vault',
    '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z', 1, 1, 1, null
  ).run();
  await env.DB.prepare(`INSERT INTO vault_mirror`).bind(
    'https://github.com/o/in-vault', 'A/x.md', 'A', 'T', 'in_vault',
    '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z', 's1'
  ).run();
  await env.DB.prepare(`INSERT INTO ever_seen_ledger`).bind(
    'https://github.com/o/dead', 'x', 'github', 'o', 'dead',
    '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z', 1, 2, 1, null
  ).run();
  await env.DB.prepare(`INSERT INTO dead_letters`).bind(
    'https://github.com/o/dead', 'x', 'blocked_domain',
    '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z', 1
  ).run();
  const res = await worker.fetch(
    signedRequest({ url: 'https://bot.example/api/pending' }), env, {});
  const data = await res.json();
  assert.equal(data.count, 0);
});

test('/api/verify reports the reconciliation stats', async () => {
  const env = await pairedEnv();
  await env.DB.prepare(`INSERT INTO ever_seen_ledger`).bind(
    'https://github.com/o/r', 'x', 'github', 'o', 'r',
    '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z', 1, 1, 1, null
  ).run();
  await env.DB.prepare(`INSERT INTO ever_seen_ledger`).bind(
    'https://example.com/t', 'x', 'non_github', null, null,
    '2026-10-01T00:00:01Z', '2026-10-01T00:00:01Z', 1, 2, 1, null
  ).run();
  // a mirror row still 'pending' counts as pending; an untouched ledger
  // row is simply not in the vault mirror yet (inVault/pending stay 0/1).
  await env.DB.prepare(`INSERT INTO vault_mirror`).bind(
    'https://example.com/t', null, null, null, 'pending',
    null, '2026-10-01T00:00:02Z', 's1'
  ).run();
  const res = await worker.fetch(
    signedRequest({ url: 'https://bot.example/api/verify' }), env, {});
  const data = await res.json();
  assert.equal(data.success, true);
  assert.equal(data.stats.total_ledger, 2);
  assert.equal(data.stats.pending, 1);
  assert.equal(data.stats.non_github, 1);
  assert.equal(typeof data.ledger_hash, 'string');
});

test('/health reports the worker version (staleness check)', async () => {
  const env = makeEnv();
  const res = await worker.fetch(new FakeRequest("https://bot.example/health", { body: null }), env, {});
  assert.equal(res.status, 200);
  const data = await res.json();
  assert.equal(data.status, 'ok');
  assert.equal(data.version, WORKER_VERSION);
});

test('/api/health (public) is alive', async () => {
  const env = makeEnv();
  const res = await worker.fetch(new FakeRequest("https://bot.example/api/health", { body: null }), env, {});
  const data = await res.json();
  assert.equal(data.status, 'ok');
});

test('unknown paths 404 with the endpoint hint', async () => {
  const env = makeEnv();
  const res = await worker.fetch(new FakeRequest("https://bot.example/nope", { body: null }), env, {});
  assert.equal(res.status, 404);
});
