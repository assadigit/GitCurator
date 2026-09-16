/**
 * lib/api.ts — Worker API client
 *
 * All requests include the session cookie automatically (SameSite=Strict).
 * CSRF protection: SameSite=Strict + origin header check (handled by Worker).
 */

import { WORKER_URL, AUTH_COOKIE_NAME } from './config';

// ========================================
// Types
// ========================================

export interface Stats {
  total: number;
  byType: { github: number; non_github: number };
  pending: number;
  inVault: number;
  decommissioned: number;
  deadLetters: number;
  lastBackup: string | null;
  lastSync: string | null;
  lastPoll: string | null;
  lastWebhook: string | null;
}

export interface PendingLink {
  url_normalized: string;
  url_original: string;
  github_owner: string;
  github_repo: string;
  github_metadata: {
    stars: number;
    description: string;
    language: string;
    topics: string[];
    readme_excerpt: string;
  } | null;
  first_seen_at: string;
  forward_count: number;
  ledger_id: number;
}

export interface VaultEntry {
  url_normalized: string;
  vault_path: string;
  category: string;
  title: string;
  status: string;
  last_updated_at: string;
}

export interface DecommissionEvent {
  id: number;
  url_normalized: string;
  source: string;
  reason: string;
  decommissioned_at: string;
  details: string;
}

export interface DeadLetter {
  url_normalized: string;
  url_original: string;
  reason: string;
  first_attempted_at: string;
  last_attempted_at: string;
  attempt_count: number;
}

export interface DesktopError {
  id: number;
  severity: 'CRITICAL' | 'WARNING' | 'INFO' | 'DEBUG';
  error_code: string;
  message: string;
  details: string | null;
  occurred_at: string;
  acknowledged: number;
}

export interface GdriveBackup {
  id: number;
  backup_id: string;
  file_name: string;
  file_size_bytes: number;
  repo_count: number;
  trigger: string;
  created_at: string;
  status: string;
}

export interface ActivityEntry {
  id: number;
  event_type: string;
  url: string | null;
  message: string;
  occurred_at: string;
}

export interface OverviewData {
  stats: Stats;
  recent_activity: ActivityEntry[];
}

// ========================================
// API client
// ========================================

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const url = `${WORKER_URL}${path}`;
  const response = await fetch(url, {
    ...options,
    credentials: 'include',  // Include cookies
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  });

  if (response.status === 401) {
    // Not authenticated — redirect to login
    if (typeof window !== 'undefined') {
      window.location.href = '/login/';
    }
    throw new Error('Not authenticated');
  }

  if (!response.ok) {
    const error = await response.json().catch(() => ({ error: response.statusText }));
    throw new Error(error.error || `HTTP ${response.status}`);
  }

  return response.json();
}

// ========================================
// Auth endpoints
// ========================================

export async function requestMagicLink(userId: string): Promise<{ success: boolean; message: string }> {
  return apiFetch('/api/auth/request', {
    method: 'POST',
    body: JSON.stringify({ user_id: userId }),
  });
}

export async function verifyMagicToken(token: string): Promise<{ success: boolean }> {
  return apiFetch(`/api/auth/verify?token=${encodeURIComponent(token)}`);
}

export async function logout(): Promise<{ success: boolean }> {
  return apiFetch('/api/auth/logout', { method: 'POST' });
}

export async function logoutAll(): Promise<{ success: boolean }> {
  return apiFetch('/api/auth/logout-all', { method: 'POST' });
}

// ========================================
// Dashboard data endpoints
// ========================================

export async function getOverview(): Promise<OverviewData> {
  return apiFetch('/api/dashboard/overview');
}

export async function getPending(): Promise<{ pending: PendingLink[]; count: number }> {
  return apiFetch('/api/dashboard/pending');
}

export async function getVault(query?: string, category?: string): Promise<{ entries: VaultEntry[]; count: number }> {
  const params = new URLSearchParams();
  if (query) params.set('q', query);
  if (category) params.set('category', category);
  return apiFetch(`/api/dashboard/vault?${params}`);
}

export async function getDecommissioned(): Promise<{ events: DecommissionEvent[]; count: number }> {
  return apiFetch('/api/dashboard/decommissioned');
}

export async function getDeadLetters(): Promise<{ letters: DeadLetter[]; count: number }> {
  return apiFetch('/api/dashboard/dead-letters');
}

export async function getErrors(severity?: string): Promise<{ errors: DesktopError[]; count: number }> {
  const params = severity ? `?severity=${severity}` : '';
  return apiFetch(`/api/dashboard/errors${params}`);
}

export async function getBackups(): Promise<{ backups: GdriveBackup[]; count: number }> {
  return apiFetch('/api/dashboard/backups');
}

export async function getSettings(): Promise<{ settings: Record<string, unknown> }> {
  return apiFetch('/api/dashboard/settings');
}

// ========================================
// Dashboard action endpoints
// ========================================

export async function markDecommissioned(url: string): Promise<{ success: boolean }> {
  return apiFetch('/api/dashboard/action/decommission', {
    method: 'POST',
    body: JSON.stringify({ url_normalized: url }),
  });
}

export async function forgetUrl(url: string): Promise<{ success: boolean }> {
  return apiFetch('/api/dashboard/action/forget', {
    method: 'POST',
    body: JSON.stringify({ url_normalized: url }),
  });
}

export async function triggerBackup(): Promise<{ success: boolean }> {
  return apiFetch('/api/dashboard/action/backup', { method: 'POST' });
}

export async function triggerRestore(backupId: string, mode: string): Promise<{ success: boolean }> {
  return apiFetch('/api/dashboard/action/restore', {
    method: 'POST',
    body: JSON.stringify({ backup_id: backupId, mode }),
  });
}

export async function clearErrors(): Promise<{ success: boolean }> {
  return apiFetch('/api/dashboard/action/clear-errors', { method: 'POST' });
}
