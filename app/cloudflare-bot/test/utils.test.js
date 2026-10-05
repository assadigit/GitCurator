// utils.test.js — URL identity, routing and policy helpers.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  normalizeUrl, normalizeWebsiteUrl, normalizeUrlTyped, isGitHubUrl,
  parseGitHubUrl, scrubUrlToken, domainMatches, domainsFromEnv, extractUrls,
  mapGithubIoUrl, blockedDomainsFromEnv,
  DEFAULT_BLOCKED_DOMAINS, DEFAULT_SELF_DOMAINS
} from '../src/utils.js';

test('normalizeUrl strips tracking noise and lowercases the domain', () => {
  assert.equal(normalizeUrl('https://GitHub.com/Foo/Bar/?utm_x=1#readme'),
    'https://github.com/Foo/Bar');
  assert.equal(normalizeUrl('https://twitter.com/a/status/1'),
    'https://x.com/a/status/1');
});

test('normalizeWebsiteUrl keeps meaningful query params and drops tracking', () => {
  assert.equal(
    normalizeWebsiteUrl('https://www.youtube.com/watch?v=abc&utm_source=x'),
    'https://youtube.com/watch?v=abc');
  assert.equal(normalizeWebsiteUrl('https://example.com/a/'), 'https://example.com/a');
  assert.equal(normalizeWebsiteUrl('https://example.com'), 'https://example.com');
});

test('normalizeUrlTyped: GitHub keeps frozen semantics, websites keep identity', () => {
  assert.equal(normalizeUrlTyped('https://github.com/a/b?tab=readme'),
    'https://github.com/a/b');
  assert.equal(normalizeUrlTyped('https://youtube.com/watch?v=abc'),
    'https://youtube.com/watch?v=abc');
});

test('routing: github.com repos are github; gists and everything else are not', () => {
  assert.ok(isGitHubUrl('https://github.com/owner/repo'));
  assert.ok(!isGitHubUrl('https://gist.github.com/abc123'));
  assert.ok(!isGitHubUrl('https://example.com/x'));
  assert.deepEqual(parseGitHubUrl('https://github.com/o/r'),
    { owner: 'o', repo: 'r', apiUrl: 'https://api.github.com/repos/o/r' });
});

test('blocked-domain policy matches the desktop default (x-family)', () => {
  assert.ok(domainMatches('https://x.com/anything', DEFAULT_BLOCKED_DOMAINS));
  assert.ok(domainMatches('https://evil.x.com/path', ['x.com'])); // subdomain
  assert.ok(!domainMatches('https://x.com.example.org/', ['x.com'])); // suffix trap
});

// ── v0.28.0 — THE LAW ───────────────────────────────────────────────

test('THE LAW: every x/github-group/hf/ig/fb/li domain is in the default ban list', () => {
  const law = ['x.com', 'twitter.com', 't.co',
    'github.com', 'gist.github.com', 'github.io', 'githubusercontent.com',
    'huggingface.co', 'hf.co',
    'instagram.com', 'instagr.am',
    'facebook.com', 'fb.com', 'fb.me', 'fb.watch',
    'linkedin.com', 'lnkd.in'];
  assert.deepEqual(DEFAULT_BLOCKED_DOMAINS, law);
  for (const d of law) {
    assert.ok(domainMatches(`https://${d}/x`, DEFAULT_BLOCKED_DOMAINS), d);
    assert.ok(domainMatches(`https://www.${d}/x`, DEFAULT_BLOCKED_DOMAINS), d);
  }
});

test('THE LAW: repo links still flow (typing happens before the block check)', () => {
  // The queue-consumer checks `!github && domainMatches(...)`, so a
  // github.com REPO url is never blocked at collection — the GitHub
  // pipeline keeps working. Only non-repo github.com paths would fall
  // through to the law.
  assert.ok(isGitHubUrl('https://github.com/owner/repo'));
  assert.ok(!isGitHubUrl('https://github.com/features'));
  assert.ok(!isGitHubUrl('https://gist.github.com/abc'));
  assert.ok(domainMatches('https://gist.github.com/abc', DEFAULT_BLOCKED_DOMAINS));
  assert.ok(domainMatches('https://owner.github.io/', DEFAULT_BLOCKED_DOMAINS));
  assert.ok(domainMatches('https://raw.githubusercontent.com/o/r/main/f',
    DEFAULT_BLOCKED_DOMAINS));
  assert.ok(!domainMatches('https://github.com/owner/repo', []));
});

test('blockedDomainsFromEnv: the law is the floor, env can only add', () => {
  const law = DEFAULT_BLOCKED_DOMAINS;
  assert.deepEqual(blockedDomainsFromEnv(undefined), law);
  assert.deepEqual(blockedDomainsFromEnv(''), law); // no more opt-out
  assert.deepEqual(blockedDomainsFromEnv('x.com,reddit.com'),
    [...law, 'reddit.com']);
  assert.ok(blockedDomainsFromEnv('reddit.com').includes('linkedin.com'));
  // removal attempts are ignored — the law cannot be edited away
  assert.ok(blockedDomainsFromEnv('linkedin.com').includes('x.com'));
});

test('domainsFromEnv: unset -> defaults, empty -> opt-out, values parsed', () => {
  assert.deepEqual(domainsFromEnv(undefined, ['a.com']), ['a.com']);
  assert.deepEqual(domainsFromEnv('', ['a.com']), []);
  assert.deepEqual(domainsFromEnv(' A.com , b.com ', []), ['a.com', 'b.com']);
});

test('scrubUrlToken masks secret query values in stored originals', () => {
  assert.equal(
    scrubUrlToken('https://w/auth/?token=abcdefgh12345678'),
    'https://w/auth/?token=…');
  assert.equal(scrubUrlToken('https://w/p?x=1'), 'https://w/p?x=1');
});

test('extractUrls pulls every URL out of a message and trims punctuation', () => {
  assert.deepEqual(
    extractUrls('see https://github.com/a/b, and https://example.com/x.'),
    ['https://github.com/a/b', 'https://example.com/x']);
});

// ── v0.29.0 — batch pastes: bare addresses, one per line ────────────

test('extractUrls: a batch paste of bare domains (one per line) is fully captured', () => {
  const text = [
    'coolors.co',
    'www.paletton.com/',
    'example.com/tool?feature=x',
    'github.com/owner/repo',
    '127.0.0.1:8901/site',
    'sub.domain.org/path/to/page'
  ].join('\n');
  assert.deepEqual(extractUrls(text), [
    'https://coolors.co',
    'https://www.paletton.com/',
    'https://example.com/tool?feature=x',
    'https://github.com/owner/repo',
    'https://127.0.0.1:8901/site',
    'https://sub.domain.org/path/to/page'
  ]);
});

test('extractUrls: mixed batch — schemed and bare lines together', () => {
  const text = 'https://example.com/one\nexample.com/two\nplain prose line here';
  assert.deepEqual(extractUrls(text),
    ['https://example.com/one', 'https://example.com/two']);
});

test('extractUrls: prose never yields false bare links (whole-line rule)', () => {
  assert.deepEqual(extractUrls('check out example.com please'), []);
  assert.deepEqual(extractUrls('not a url at all'), []);
  assert.deepEqual(extractUrls('e.g. this and that'), []);
});

test('extractUrls: bare lines with trailing punctuation are trimmed; schemed lines not duplicated', () => {
  assert.deepEqual(extractUrls('example.com.'), ['https://example.com']);
  assert.deepEqual(extractUrls('example.com,'), ['https://example.com']);
  // a schemed line is captured once by the global pattern, never again
  assert.deepEqual(extractUrls('https://example.com/x'), ['https://example.com/x']);
});

test('extractUrls: 50-line website batch (the owner\'s exact scenario)', () => {
  const lines = [];
  for (let i = 0; i < 50; i++) lines.push(`site${i}.example.com/page`);
  const out = extractUrls(lines.join('\n'));
  assert.equal(out.length, 50);
  assert.equal(out[0], 'https://site0.example.com/page');
  assert.equal(out[49], 'https://site49.example.com/page');
});

test('self-domain default is the bot\'s own worker host', () => {
  assert.equal(DEFAULT_SELF_DOMAINS[0], 'github-to-obsidian-bot.aliassadi-plus.workers.dev');
});

// ── v0.26.0 ──────────────────────────────────────────────────────────

test('mapGithubIoUrl: pages URLs map to their canonical repo (desktop links.py parity)', () => {
  assert.equal(mapGithubIoUrl('https://owner.github.io/repo'),
    'https://github.com/owner/repo');
  assert.equal(mapGithubIoUrl('https://owner.github.io/repo/anything?utm=x'),
    'https://github.com/owner/repo');
  assert.equal(mapGithubIoUrl('  https://owner.github.io/repo  '),
    'https://github.com/owner/repo');
  // a bare owner.github.io site is a real website — no repo path
  assert.equal(mapGithubIoUrl('https://owner.github.io'), '');
  assert.equal(mapGithubIoUrl('https://owner.github.io/'), '');
  // non-pages URLs pass through untouched (returned as '')
  assert.equal(mapGithubIoUrl('https://github.com/o/r'), '');
  assert.equal(mapGithubIoUrl('https://example.com/x'), '');
  assert.equal(mapGithubIoUrl(''), '');
  // invalid GitHub names (must start/end alphanumeric — traversal guard)
  assert.equal(mapGithubIoUrl('https://..github.io/repo'), '');
  assert.equal(mapGithubIoUrl('https://-bad.github.io/repo'), '');
  assert.equal(mapGithubIoUrl('https://owner.github.io/bad-'), '');
});
