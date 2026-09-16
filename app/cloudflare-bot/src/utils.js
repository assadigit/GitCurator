// ========================================
// utils.js — Shared utilities
// ========================================

/**
 * Normalize a URL for dedup purposes.
 * Matches desktop app logic exactly.
 */
export function normalizeUrl(rawUrl) {
  let u = rawUrl.trim().replace(/[.,);]+$/, '');

  // twitter.com → x.com
  u = u.replace('twitter.com', 'x.com');

  // Strip fragment
  if (u.includes('#')) u = u.split('#')[0];

  // Strip query string
  if (u.includes('?')) u = u.split('?')[0];

  // Strip trailing slashes (but keep protocol://)
  u = u.replace(/\/+$/, '');
  if (u.endsWith(':')) u += '//';

  // Lowercase the domain
  if (u.includes('://')) {
    const [scheme, rest] = u.split('://');
    if (rest.includes('/')) {
      const slashIdx = rest.indexOf('/');
      const domain = rest.substring(0, slashIdx).toLowerCase();
      const path = rest.substring(slashIdx);
      u = `${scheme}://${domain}${path}`;
    } else {
      u = `${scheme}://${rest.toLowerCase()}`;
    }
  }

  return u;
}

/**
 * Extract GitHub owner/repo from a URL.
 * Returns null if not a GitHub URL.
 */
export function parseGitHubUrl(url) {
  const match = url.match(/^https?:\/\/(?:www\.)?github\.com\/([a-zA-Z0-9\-_.]+)\/([a-zA-Z0-9\-_.]+)/);
  if (!match) return null;
  return {
    owner: match[1],
    repo: match[2],
    apiUrl: `https://api.github.com/repos/${match[1]}/${match[2]}`
  };
}

/**
 * Check if a URL is a GitHub repo URL.
 */
export function isGitHubUrl(url) {
  return /^https?:\/\/(?:www\.)?github\.com\/[a-zA-Z0-9\-_.]+\/[a-zA-Z0-9\-_.]+/.test(url);
}

/**
 * Extract all URLs from a text message.
 */
export function extractUrls(text) {
  const pattern = /https?:\/\/[^\s<>"')\]]+/g;
  const matches = text.match(pattern) || [];
  // Clean trailing punctuation
  return matches.map(u => u.replace(/[.,);:]+$/, ''));
}

/**
 * Format stars count (1234 → "1.2k")
 */
export function formatStars(count) {
  if (!count) return '0';
  if (count >= 1000000) return `${(count / 1000000).toFixed(1)}M`;
  if (count >= 1000) return `${(count / 1000).toFixed(1)}k`;
  return String(count);
}

/**
 * Get current UTC ISO timestamp
 */
export function now() {
  return new Date().toISOString();
}

/**
 * Generate a random hex string
 */
export function randomHex(bytes = 16) {
  const arr = new Uint8Array(bytes);
  crypto.getRandomValues(arr);
  return Array.from(arr).map(b => b.toString(16).padStart(2, '0')).join('');
}

/**
 * Generate a UUID v4
 */
export function uuid() {
  return crypto.randomUUID();
}

/**
 * HMAC-SHA256 using Web Crypto API
 */
export async function hmacSha256(secret, message) {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw',
    encoder.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  );
  const sig = await crypto.subtle.sign('HMAC', key, encoder.encode(message));
  return Array.from(new Uint8Array(sig)).map(b => b.toString(16).padStart(2, '0')).join('');
}

/**
 * SHA-256 hash
 */
export async function sha256(data) {
  const encoder = new TextEncoder();
  const hash = await crypto.subtle.digest('SHA-256', encoder.encode(data));
  return Array.from(new Uint8Array(hash)).map(b => b.toString(16).padStart(2, '0')).join('');
}

/**
 * Constant-time string comparison
 */
export function constantTimeCompare(a, b) {
  if (a.length !== b.length) return false;
  let result = 0;
  for (let i = 0; i < a.length; i++) {
    result |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return result === 0;
}

/**
 * Truncate text to maxLen, adding ellipsis
 */
export function truncate(text, maxLen = 500) {
  if (!text) return '';
  if (text.length <= maxLen) return text;
  return text.substring(0, maxLen - 3) + '...';
}

/**
 * JSON response helper
 */
export function jsonResponse(data, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      'Content-Type': 'application/json',
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-Auth-Install, X-Auth-Timestamp, X-Auth-Signature',
      ...extraHeaders
    }
  });
}

/**
 * Handle CORS preflight
 */
export function handleCors() {
  return new Response(null, {
    status: 204,
    headers: {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-Auth-Install, X-Auth-Timestamp, X-Auth-Signature',
      'Access-Control-Max-Age': '86400'
    }
  });
}

/**
 * Iran timezone conversion (UTC+3:30)
 */
export function toIranTime(isoUtc) {
  // Iran is UTC+3:30
  const date = new Date(isoUtc);
  const iranOffset = 3.5 * 60 * 60 * 1000; // 3.5 hours in ms
  const iranTime = new Date(date.getTime() + iranOffset);
  return iranTime.toISOString().replace('T', ' ').substring(0, 16) + ' IRDT';
}

/**
 * Time ago formatter
 */
export function timeAgo(isoUtc) {
  const now = Date.now();
  const then = new Date(isoUtc).getTime();
  const diff = now - then;
  const minutes = Math.floor(diff / 60000);
  const hours = Math.floor(diff / 3600000);
  const days = Math.floor(diff / 86400000);

  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  if (hours < 24) return `${hours}h ago`;
  if (days < 30) return `${days}d ago`;
  return new Date(isoUtc).toISOString().split('T')[0];
}
