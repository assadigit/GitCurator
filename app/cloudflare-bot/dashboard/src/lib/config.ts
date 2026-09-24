/**
 * lib/config.ts — Dashboard configuration
 *
 * The Worker URL is set at build time via NEXT_PUBLIC_WORKER_URL.
 * In production, this is the Cloudflare Worker URL.
 */

export const WORKER_URL = process.env.NEXT_PUBLIC_WORKER_URL || 'http://localhost:8787';

export const POLL_INTERVAL = 30000; // 30 seconds

export const AUTH_COOKIE_NAME = 'curator_session';
