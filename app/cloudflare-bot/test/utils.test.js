// utils.test.js — URL identity, routing and policy helpers.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  normalizeUrl, normalizeWebsiteUrl, normalizeUrlTyped, isGitHubUrl,
  parseGitHubUrl, scrubUrlToken, domainMatches, domainsFromEnv, extractUrls,
  mapGithubIoUrl,
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
  assert.deepEqual(DEFAULT_BLOCKED_DOMAINS, ['x.com', 'twitter.com', 't.co']);
  assert.ok(domainMatches('https://x.com/anything', DEFAULT_BLOCKED_DOMAINS));
  assert.ok(domainMatches('https://evil.x.com/path', ['x.com'])); // subdomain
  assert.ok(!domainMatches('https://x.com.example.org/', ['x.com'])); // suffix trap
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
