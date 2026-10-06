// ========================================
// dashboard-html.js — Serves the dashboard HTML directly from the Worker
// ========================================
// No build step. No Node.js. No Next.js. Just HTML.
// Served at /dashboard on the Worker URL.

export function getDashboardHtml(env) {
  return new Response(DASHBOARD_HTML, {
    headers: {
      'Content-Type': 'text/html; charset=utf-8',
      'Cache-Control': 'no-cache',
    }
  });
}

const DASHBOARD_HTML = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Curator Dashboard</title>
<style>
:root {
  --primary: #6366F1;
  --primary-dark: #4F46E5;
  --accent: #10B981;
  --danger: #EF4444;
  --warning: #F59E0B;
  --info: #3B82F6;
  --bg: #FAFAFA;
  --surface: #FFFFFF;
  --surface-2: #F4F4F5;
  --fg: #18181B;
  --muted: #71717A;
  --muted-2: #A1A1AA;
  --border: #E4E4E7;
  --border-strong: #D4D4D8;
  --sidebar-bg: #FFFFFF;
  --sidebar-fg: #18181B;
  --sidebar-muted: #71717A;
  --sidebar-border: #E4E4E7;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #09090B;
    --surface: #18181B;
    --surface-2: #27272A;
    --fg: #FAFAFA;
    --muted: #A1A1AA;
    --muted-2: #71717A;
    --border: #27272A;
    --border-strong: #3F3F46;
    --sidebar-bg: #0F0F12;
    --sidebar-fg: #FAFAFA;
    --sidebar-muted: #71717A;
    --sidebar-border: #27272A;
  }
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: var(--bg);
  color: var(--fg);
  -webkit-font-smoothing: antialiased;
  letter-spacing: -0.01em;
}
.layout { display: flex; min-height: 100vh; }

/* Sidebar */
.sidebar {
  width: 240px;
  background: var(--sidebar-bg);
  border-right: 1px solid var(--sidebar-border);
  display: flex;
  flex-direction: column;
  position: fixed;
  height: 100vh;
  left: 0;
  top: 0;
  z-index: 100;
  transition: width 0.2s ease;
}
.sidebar.collapsed { width: 64px; }
.sidebar-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 20px 16px;
  border-bottom: 1px solid var(--sidebar-border);
}
.logo {
  width: 36px;
  height: 36px;
  background: var(--primary);
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.logo svg { width: 20px; height: 20px; color: white; }
.sidebar-header h1 { font-size: 14px; font-weight: 600; color: var(--sidebar-fg); }
.sidebar-header p { font-size: 12px; color: var(--sidebar-muted); }
.sidebar.collapsed .sidebar-header h1,
.sidebar.collapsed .sidebar-header p { display: none; }

.nav { flex: 1; padding: 12px 8px; overflow-y: auto; }
.nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 12px;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
  color: var(--sidebar-muted);
  cursor: pointer;
  text-decoration: none;
  transition: all 0.15s ease;
  margin-bottom: 2px;
  border: none;
  background: none;
  width: 100%;
  text-align: left;
}
.nav-item:hover { background: var(--surface-2); color: var(--sidebar-fg); }
.nav-item.active { background: var(--primary); color: white; }
.nav-item svg { width: 16px; height: 16px; flex-shrink: 0; }
.sidebar.collapsed .nav-item span { display: none; }

.sidebar-footer {
  padding: 8px;
  border-top: 1px solid var(--sidebar-border);
}
.sidebar-footer .nav-item { font-size: 12px; }
.sidebar-version {
  padding: 8px 12px;
  font-size: 10px;
  color: var(--sidebar-muted);
}
.sidebar.collapsed .sidebar-version,
.sidebar.collapsed .sidebar-footer .nav-item span { display: none; }

/* Main */
.main {
  flex: 1;
  margin-left: 240px;
  padding: 32px;
  transition: margin-left 0.2s ease;
}
.sidebar.collapsed ~ .main { margin-left: 64px; }

.page-header { margin-bottom: 24px; }
.page-header h1 { font-size: 24px; font-weight: 700; letter-spacing: -0.03em; }
.page-header p { font-size: 14px; color: var(--muted); margin-top: 4px; }

/* Cards */
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
}
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.card-title { font-size: 14px; font-weight: 600; }

/* Stat cards */
.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}
.stat-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 20px;
  position: relative;
}
.stat-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 8px;
}
.stat-value { font-size: 32px; font-weight: 700; letter-spacing: -0.03em; line-height: 1; }
.stat-icon {
  position: absolute;
  top: 20px;
  right: 20px;
  width: 36px;
  height: 36px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.stat-icon svg { width: 16px; height: 16px; }

/* Two columns */
.two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
@media (max-width: 768px) { .two-col { grid-template-columns: 1fr; } }

/* System status */
.status-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 0;
}
.status-row:not(:last-child) { border-bottom: 1px solid var(--border); }
.status-left { display: flex; align-items: center; gap: 12px; }
.status-left svg { width: 16px; height: 16px; }
.status-right { display: flex; align-items: center; gap: 8px; }
.dot { width: 8px; height: 8px; border-radius: 50%; }
.dot-on { background: var(--accent); }
.dot-off { background: var(--muted-2); }

/* Activity */
.activity-item {
  display: flex;
  gap: 10px;
  padding: 10px 0;
  border-bottom: 1px solid var(--border);
}
.activity-item:last-child { border-bottom: none; }
.activity-time {
  font-size: 11px;
  color: var(--muted-2);
  font-family: 'SF Mono', Consolas, monospace;
  white-space: nowrap;
  margin-top: 1px;
}
.activity-text { font-size: 13px; flex: 1; line-height: 1.5; }
.activity-list { max-height: 300px; overflow-y: auto; }

/* Table */
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th {
  text-align: left;
  padding: 10px 16px;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--muted);
  background: var(--surface-2);
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
}
td {
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
  font-size: 13px;
}
tbody tr:hover { background: var(--surface-2); }
tbody tr:last-child td { border-bottom: none; }

/* Badges */
.badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 500;
}
.badge-success { background: rgba(16,185,129,0.12); color: #059669; }
.badge-warning { background: rgba(245,158,11,0.12); color: #D97706; }
.badge-danger { background: rgba(239,68,68,0.12); color: #DC2626; }
.badge-info { background: rgba(59,130,246,0.12); color: #2563EB; }
.badge-neutral { background: var(--surface-2); color: var(--muted); }

/* Buttons */
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 8px 14px;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  border: 1px solid transparent;
  white-space: nowrap;
  transition: all 0.15s ease;
  font-family: inherit;
}
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-primary { background: var(--primary); color: white; }
.btn-primary:hover:not(:disabled) { background: var(--primary-dark); }
.btn-accent { background: var(--accent); color: white; }
.btn-outline { background: transparent; border-color: var(--border-strong); color: var(--fg); }
.btn-outline:hover:not(:disabled) { background: var(--surface-2); }
.btn-ghost { background: transparent; color: var(--muted); padding: 6px; }
.btn-ghost:hover { background: var(--surface-2); color: var(--fg); }
.btn-danger { background: var(--danger); color: white; }
.btn-icon { padding: 6px; width: 32px; height: 32px; }
.btn-sm { padding: 6px 10px; font-size: 12px; }

/* Input */
input, select {
  padding: 10px 12px;
  border-radius: 8px;
  border: 1px solid var(--border-strong);
  background: var(--surface);
  color: var(--fg);
  font-size: 14px;
  font-family: inherit;
  outline: none;
  transition: border-color 0.15s, box-shadow 0.15s;
}
input:focus, select:focus {
  border-color: var(--primary);
  box-shadow: 0 0 0 3px rgba(99,102,241,0.12);
}
input::placeholder { color: var(--muted-2); }

/* Search */
.search-bar {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
  align-items: center;
}
.search-bar input { flex: 1; }

/* Empty state */
.empty {
  text-align: center;
  padding: 48px 24px;
  color: var(--muted);
}
.empty svg { width: 48px; height: 48px; margin: 0 auto 12px; color: var(--muted-2); }

/* Login */
.login-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
.login-box { width: 100%; max-width: 360px; }
.login-logo {
  display: flex;
  flex-direction: column;
  align-items: center;
  margin-bottom: 32px;
}
.login-logo .logo {
  width: 56px;
  height: 56px;
  border-radius: 16px;
  box-shadow: 0 8px 24px -4px rgba(99,102,241,0.4);
  margin-bottom: 16px;
}
.login-logo .logo svg { width: 28px; height: 28px; }
.login-logo h1 { font-size: 20px; font-weight: 700; }
.login-logo p { font-size: 14px; color: var(--muted); margin-top: 4px; }
.login-form { display: flex; flex-direction: column; gap: 16px; }
.login-form label { font-size: 12px; font-weight: 500; display: block; margin-bottom: 8px; }
.login-help { font-size: 12px; color: var(--primary); margin-top: 8px; text-decoration: none; }
.login-help:hover { text-decoration: underline; }
.login-footer {
  text-align: center;
  font-size: 12px;
  color: var(--muted);
  margin-top: 24px;
  line-height: 1.6;
}
.alert {
  padding: 12px;
  border-radius: 8px;
  font-size: 14px;
  border: 1px solid;
}
.alert-error { background: rgba(239,68,68,0.08); color: #DC2626; border-color: rgba(239,68,68,0.2); }
.alert-success { background: rgba(16,185,129,0.08); color: #059669; border-color: rgba(16,185,129,0.2); }

/* Error cards */
.error-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px 20px;
  margin-bottom: 8px;
}
.error-header { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.error-msg { font-size: 13px; margin-top: 4px; }
.error-details {
  margin-top: 8px;
  padding: 12px;
  background: var(--surface-2);
  border-radius: 8px;
  font-family: 'SF Mono', Consolas, monospace;
  font-size: 12px;
  white-space: pre-wrap;
  overflow-x: auto;
}
.mono { font-family: 'SF Mono', Consolas, monospace; font-size: 12px; }
.link { color: var(--primary); text-decoration: none; font-weight: 500; }
.link:hover { text-decoration: underline; }
.spinner {
  width: 24px;
  height: 24px;
  border: 2px solid var(--border-strong);
  border-top-color: var(--primary);
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
  margin: 96px auto;
}
@keyframes spin { to { transform: rotate(360deg); } }
.hidden { display: none !important; }
</style>
</head>
<body>
<div id="app"></div>
<script>
const API = '';
let currentPage = 'overview';
let sessionChecked = false;

// ========================================
// API helpers
// ========================================
async function api(path, opts) {
  const res = await fetch(API + path, { ...opts, credentials: 'include' });
  if (res.status === 401) { showLogin(); throw new Error('Not authenticated'); }
  if (!res.ok) throw new Error((await res.json().catch(()=>({}))).error || 'HTTP ' + res.status);
  return res.json();
}

// ========================================
// Login page
// ========================================
function showLogin() {
  document.getElementById('app').innerHTML = \`
  <div class="login-wrap">
    <div class="login-box">
      <div class="login-logo">
        <div class="logo"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z"/></svg></div>
        <h1>Curator Dashboard</h1>
        <p>Sign in with your Telegram account</p>
      </div>
      <div class="card">
        <div class="login-form">
          <div>
            <label>Telegram User ID</label>
            <input type="text" id="userId" placeholder="e.g., 123456789" autofocus>
            <a href="https://t.me/userinfobot" target="_blank" class="login-help">Get your ID from @userinfobot</a>
          </div>
          <div id="loginAlert"></div>
          <button class="btn btn-primary" style="padding:10px 16px;font-size:14px" onclick="sendMagicLink()">
            Send Magic Link →
          </button>
        </div>
      </div>
      <p class="login-footer">The bot will DM you a one-time login link.<br>Click it to log in — no password needed.</p>
    </div>
  </div>\`;
  document.getElementById('userId').addEventListener('keypress', e => { if(e.key==='Enter') sendMagicLink(); });
}

async function sendMagicLink() {
  const uid = document.getElementById('userId').value.trim();
  if (!uid) { document.getElementById('loginAlert').innerHTML = '<div class="alert alert-error">Enter your Telegram user ID</div>'; return; }
  const btn = event.target; btn.disabled = true; btn.textContent = 'Sending...';
  try {
    const r = await api('/api/auth/request', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({user_id: uid}) });
    document.getElementById('loginAlert').innerHTML = '<div class="alert alert-success">✅ Magic link sent! Check your Telegram DMs.</div>';
  } catch(e) {
    document.getElementById('loginAlert').innerHTML = '<div class="alert alert-error">' + e.message + '</div>';
  }
  btn.disabled = false; btn.textContent = 'Send Magic Link →';
}

// ========================================
// Auth callback (check URL for token)
// ========================================
async function checkAuthCallback() {
  const params = new URLSearchParams(window.location.search);
  const token = params.get('token');
  if (token) {
    try {
      await api('/api/auth/verify?token=' + encodeURIComponent(token));
      window.history.replaceState({}, '', '/dashboard');
      sessionChecked = true;
      showApp();
      return true;
    } catch(e) {
      window.history.replaceState({}, '', '/dashboard');
    }
  }
  return false;
}

// ========================================
// Check session
// ========================================
async function checkSession() {
  if (await checkAuthCallback()) return;
  try {
    await api('/api/dashboard/overview');
    sessionChecked = true;
    showApp();
  } catch {
    showLogin();
  }
}

// ========================================
// Main app
// ========================================
function showApp() {
  const nav = [
    {id:'overview', label:'Overview', icon:'grid'},
    {id:'pending', label:'Pending', icon:'clock'},
    {id:'vault', label:'Vault', icon:'folder'},
    {id:'decommissioned', label:'Decommissioned', icon:'trash'},
    {id:'dead-letters', label:'Dead Letters', icon:'skull'},
    {id:'errors', label:'Errors', icon:'alert'},
    {id:'backups', label:'Backups', icon:'drive'},
    {id:'settings', label:'Settings', icon:'gear'},
  ];
  const icons = {
    grid:'<path d="M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z"/>',
    clock:'<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    folder:'<path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/>',
    trash:'<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/>',
    skull:'<path d="M12 2C7.58 2 4 5.58 4 10c0 3.54 2.36 6.53 5.5 7.58V22h5v-4.42C17.64 16.53 20 13.54 20 10c0-4.42-3.58-8-8-8zM9 12a2 2 0 110-4 2 2 0 010 4zm6 0a2 2 0 110-4 2 2 0 010 4z"/>',
    alert:'<circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>',
    drive:'<path d="M22 12H2L5 4h14l3 8zM5 14h14v6H5z"/>',
    gear:'<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 01-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06a1.65 1.65 0 001.82.33H9a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"/>',
    moon:'<path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z"/>',
    sun:'<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>',
  };
  document.getElementById('app').innerHTML = \`
  <div class="layout">
    <aside class="sidebar" id="sidebar">
      <div class="sidebar-header">
        <div class="logo"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z"/></svg></div>
        <div><h1>Curator</h1><p>Dashboard</p></div>
      </div>
      <nav class="nav" id="nav"></nav>
      <div class="sidebar-footer">
        <button class="nav-item" onclick="toggleSidebar()"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15 18 9 12 15 6"/></svg><span>Collapse</span></button>
        <div class="sidebar-version">v1.0.0 · Cloudflare Edge</div>
      </div>
    </aside>
    <main class="main" id="main"><div class="spinner"></div></main>
  </div>\`;
  const navEl = document.getElementById('nav');
  nav.forEach(item => {
    const btn = document.createElement('button');
    btn.className = 'nav-item' + (item.id === currentPage ? ' active' : '');
    btn.onclick = () => { currentPage = item.id; showApp(); loadPage(); };
    btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + icons[item.icon] + '</svg><span>' + item.label + '</span>';
    navEl.appendChild(btn);
  });
  loadPage();
}

function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('collapsed');
}

// ========================================
// Page loader
// ========================================
async function loadPage() {
  const main = document.getElementById('main');
  main.innerHTML = '<div class="spinner"></div>';
  // Update active nav
  document.querySelectorAll('.nav-item').forEach((el, i) => {
    el.classList.toggle('active', el.onclick && false); // will set below
  });
  const navBtns = document.querySelectorAll('#nav .nav-item');
  const pages = ['overview','pending','vault','decommissioned','dead-letters','errors','backups','settings'];
  navBtns.forEach((btn, i) => { btn.classList.toggle('active', pages[i] === currentPage); });

  try {
    if (currentPage === 'overview') await loadOverview();
    else if (currentPage === 'pending') await loadPending();
    else if (currentPage === 'vault') await loadVault();
    else if (currentPage === 'decommissioned') await loadDecommissioned();
    else if (currentPage === 'dead-letters') await loadDeadLetters();
    else if (currentPage === 'errors') await loadErrors();
    else if (currentPage === 'backups') await loadBackups();
    else if (currentPage === 'settings') await loadSettings();
  } catch(e) {
    if (e.message !== 'Not authenticated') main.innerHTML = '<div class="card"><div class="alert alert-error">' + e.message + '</div></div>';
  }
}

// ========================================
// Pages
// ========================================
function fmtAgo(iso) {
  if (!iso) return 'never';
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return s + 's ago';
  if (s < 3600) return Math.floor(s/60) + 'm ago';
  if (s < 86400) return Math.floor(s/3600) + 'h ago';
  return Math.floor(s/86400) + 'd ago';
}
function fmtStars(n) { return n >= 1000 ? (n/1000).toFixed(1)+'k' : (n||0); }
function trunc(s, n) { return s && s.length > n ? s.substring(0,n-3)+'...' : s || ''; }
function esc(s) { return (s||'').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

async function loadOverview() {
  const d = await api('/api/dashboard/overview');
  const s = d.stats;
  const cards = [
    {label:'Pending', val:s.pending, color:'#D97706', bg:'rgba(245,158,11,0.12)', icon:'<path d="M12 6v6l4 2"/>'},
    {label:'In Vault', val:s.inVault, color:'#059669', bg:'rgba(16,185,129,0.12)', icon:'<polyline points="20 6 9 17 4 12"/>'},
    {label:'Decommissioned', val:s.decommissioned, color:'#DC2626', bg:'rgba(239,68,68,0.12)', icon:'<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6"/>'},
    {label:'Dead Letters', val:s.deadLetters, color:'#71717A', bg:'rgba(113,113,122,0.12)', icon:'<circle cx="12" cy="12" r="10"/>'},
  ];
  let html = '<div class="page-header"><h1>Overview</h1><p>System status and recent activity</p></div>';
  html += '<div class="stats-grid">';
  cards.forEach(c => {
    html += '<div class="stat-card"><div class="stat-label">'+c.label+'</div><div class="stat-value">'+c.val+'</div><div class="stat-icon" style="background:'+c.bg+'"><svg viewBox="0 0 24 24" fill="none" stroke="'+c.color+'" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:16px;height:16px">'+c.icon+'</svg></div></div>';
  });
  html += '</div><div class="two-col">';

  // System status
  const desktopOn = s.lastPoll && Date.now() - new Date(s.lastPoll).getTime() < 600000;
  html += '<div class="card"><div class="card-header"><span class="card-title">System Status</span><span style="font-size:12px;color:var(--muted)">Total: '+s.total+'</span></div>';
  html += statusRow('Desktop', desktopOn, fmtAgo(s.lastPoll));
  html += statusRow('Backup', s.lastBackup != null, fmtAgo(s.lastBackup));
  html += statusRow('Last Sync', s.lastSync != null, fmtAgo(s.lastSync));
  html += '</div>';

  // Activity
  html += '<div class="card"><div class="card-header"><span class="card-title">Recent Activity</span><span style="font-size:12px;color:var(--muted)">24h</span></div>';
  if (!d.recent_activity || d.recent_activity.length === 0) {
    html += '<div class="empty"><p>No recent activity</p></div>';
  } else {
    html += '<div class="activity-list">';
    d.recent_activity.slice(0,15).forEach(a => {
      const t = new Date(a.occurred_at).toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit',hour12:false});
      html += '<div class="activity-item"><span class="activity-time">'+t+'</span><span class="activity-text">'+esc(trunc(a.message,100))+'</span></div>';
    });
    html += '</div>';
  }
  html += '</div></div>';
  document.getElementById('main').innerHTML = html;
}

function statusRow(label, on, detail) {
  return '<div class="status-row"><div class="status-left"><span style="color:'+(on?'var(--accent)':'var(--muted-2)')+'">●</span><span>'+label+'</span></div><div class="status-right"><span style="font-size:12px;color:var(--muted)">'+detail+'</span></div></div>';
}

async function loadPending() {
  const d = await api('/api/dashboard/pending');
  const p = d.pending || [];
  let html = '<div class="page-header"><h1>Pending Links</h1><p>'+p.length+' link(s) waiting for processing</p></div>';
  if (p.length === 0) { html += '<div class="card"><div class="empty"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg><p>No pending links</p></div></div>'; }
  else {
    html += '<div class="card" style="padding:0"><div class="table-wrap"><table><thead><tr><th>Link</th><th>Stars</th><th>Description</th><th>Forwarded</th><th></th></tr></thead><tbody>';
    p.forEach(l => {
      const m = l.github_metadata;
      // v0.22.0 — website links (url_type='non_github') have no owner/repo
      const isWebsite = l.url_type === 'non_github' || !l.github_owner;
      const linkCell = isWebsite
        ? '<a href="'+esc(l.url_normalized)+'" target="_blank" class="link">🌐 '+esc(trunc(l.url_normalized,60))+'</a>'
        : '<a href="https://github.com/'+l.github_owner+'/'+l.github_repo+'" target="_blank" class="link">'+l.github_owner+'/'+l.github_repo+'</a>';
      html += '<tr><td>'+linkCell+'</td>';
      html += '<td>'+(m&&m.stars?'⭐ '+fmtStars(m.stars):'—')+'</td>';
      html += '<td style="color:var(--muted);max-width:300px">'+esc(trunc(m&&m.description?m.description:'',70))+'</td>';
      html += '<td style="color:var(--muted);font-size:12px">'+fmtAgo(l.first_seen_at)+'</td>';
      html += '<td><button class="btn btn-ghost btn-icon" style="color:var(--danger)" onclick="decomm(\\''+l.url_normalized+'\\')" title="Decommission"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:16px;height:16px"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6"/></svg></button></td></tr>';
    });
    html += '</tbody></table></div></div>';
  }
  document.getElementById('main').innerHTML = html;
}

async function decomm(url) {
  if (!confirm('Mark as decommissioned?')) return;
  await api('/api/dashboard/action/decommission', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({url_normalized:url})});
  loadPending();
}

async function loadVault() {
  const d = await api('/api/dashboard/vault');
  const e = d.entries || [];
  let html = '<div class="page-header"><h1>Vault</h1><p>'+(d.count||0)+' repos in your vault</p></div>';
  if (e.length === 0) { html += '<div class="card"><div class="empty"><p>No entries</p></div></div>'; }
  else {
    html += '<div class="card" style="padding:0"><div class="table-wrap"><table><thead><tr><th>Repository</th><th>Title</th><th>Category</th><th>Path</th><th>Updated</th></tr></thead><tbody>';
    e.forEach(v => {
      const m = v.url_normalized.match(/github\\.com\\/([^/]+)\\/([^/]+)/);
      const name = m ? m[1]+'/'+m[2] : v.url_normalized;
      html += '<tr><td><a href="https://github.com/'+(m?m[1]:'')+'/'+(m?m[2]:'')+'" target="_blank" class="link">'+name+'</a></td>';
      html += '<td style="color:var(--muted)">'+esc(v.title||'—')+'</td>';
      html += '<td>'+(v.category?'<span class="badge badge-info">'+esc(v.category)+'</span>':'')+'</td>';
      html += '<td class="mono" style="color:var(--muted)">'+esc(v.vault_path)+'</td>';
      html += '<td style="color:var(--muted);font-size:12px">'+new Date(v.last_updated_at).toLocaleDateString()+'</td></tr>';
    });
    html += '</tbody></table></div></div>';
  }
  document.getElementById('main').innerHTML = html;
}

async function loadDecommissioned() {
  const d = await api('/api/dashboard/decommissioned');
  const e = d.events || [];
  let html = '<div class="page-header"><h1>Decommissioned Repos</h1><p>'+e.length+' repo(s) marked as dead</p></div>';
  if (e.length === 0) { html += '<div class="card"><div class="empty"><p>No decommissioned repos</p></div></div>'; }
  else {
    html += '<div class="card" style="padding:0"><div class="table-wrap"><table><thead><tr><th>URL</th><th>Reason</th><th>Source</th><th>Date</th></tr></thead><tbody>';
    e.forEach(ev => {
      const m = ev.url_normalized.match(/github\\.com\\/([^/]+)\\/([^/]+)/);
      const name = m ? m[1]+'/'+m[2] : ev.url_normalized;
      html += '<tr><td class="mono">'+name+'</td><td><span class="badge badge-danger">'+ev.reason+'</span></td><td style="font-size:12px;color:var(--muted)">'+ev.source+'</td><td style="font-size:12px;color:var(--muted)">'+fmtAgo(ev.decommissioned_at)+'</td></tr>';
    });
    html += '</tbody></table></div></div>';
  }
  document.getElementById('main').innerHTML = html;
}

async function loadDeadLetters() {
  const d = await api('/api/dashboard/dead-letters');
  const l = d.letters || [];
  let html = '<div class="page-header"><h1>Dead Letters</h1><p>'+l.length+' link(s) that could not be processed</p></div>';
  if (l.length === 0) { html += '<div class="card"><div class="empty"><p>No dead letters</p></div></div>'; }
  else {
    html += '<div class="card" style="padding:0"><div class="table-wrap"><table><thead><tr><th>URL</th><th>Reason</th><th>First Attempt</th><th>Attempts</th><th></th></tr></thead><tbody>';
    l.forEach(x => {
      html += '<tr><td class="mono" style="max-width:300px">'+esc(trunc(x.url_original,60))+'</td><td><span class="badge badge-warning">'+x.reason+'</span></td><td style="font-size:12px;color:var(--muted)">'+fmtAgo(x.first_attempted_at)+'</td><td style="color:var(--muted)">'+x.attempt_count+'</td><td><button class="btn btn-ghost btn-icon" style="color:var(--danger)" onclick="forget(\\''+x.url_normalized+'\\')" title="Forget"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:16px;height:16px"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6"/></svg></button></td></tr>';
    });
    html += '</tbody></table></div></div>';
  }
  document.getElementById('main').innerHTML = html;
}

async function forget(url) {
  if (!confirm('Forget this URL?')) return;
  await api('/api/dashboard/action/forget', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({url_normalized:url})});
  loadDeadLetters();
}

async function loadErrors() {
  const d = await api('/api/dashboard/errors');
  const e = d.errors || [];
  let html = '<div class="page-header"><div><h1>Desktop Errors</h1><p>'+e.length+' recent error(s)</p></div></div>';
  html += '<div style="margin-bottom:16px"><button class="btn btn-outline" onclick="clearErrors()">Clear All</button></div>';
  if (e.length === 0) { html += '<div class="card"><div class="empty"><p>No errors ✅</p></div></div>'; }
  else {
    const colors = {CRITICAL:'#DC2626',WARNING:'#D97706',INFO:'#2563EB',DEBUG:'#71717A'};
    const bgs = {CRITICAL:'rgba(239,68,68,0.08)',WARNING:'rgba(245,158,11,0.08)',INFO:'rgba(59,130,246,0.08)',DEBUG:'rgba(113,113,122,0.08)'};
    const badges = {CRITICAL:'badge-danger',WARNING:'badge-warning',INFO:'badge-info',DEBUG:'badge-neutral'};
    e.forEach(err => {
      html += '<div class="error-card" style="background:'+bgs[err.severity]+'">';
      html += '<div class="error-header"><span class="badge '+badges[err.severity]+'">'+err.severity+'</span><span class="mono" style="font-weight:600">'+esc(err.error_code)+'</span><span style="font-size:12px;color:var(--muted);margin-left:auto">'+fmtAgo(err.occurred_at)+'</span></div>';
      html += '<div class="error-msg">'+esc(trunc(err.message,200))+'</div>';
      if (err.details) html += '<details style="margin-top:8px"><summary style="font-size:12px;color:var(--muted);cursor:pointer">Show details</summary><div class="error-details">'+esc(err.details)+'</div></details>';
      html += '</div>';
    });
  }
  document.getElementById('main').innerHTML = html;
}

async function clearErrors() {
  await api('/api/dashboard/action/clear-errors', {method:'POST'});
  loadErrors();
}

async function loadBackups() {
  const d = await api('/api/dashboard/backups');
  const b = d.backups || [];
  let html = '<div class="page-header"><div><h1>Backups</h1><p>'+b.length+' backup(s) available</p></div><button class="btn btn-accent" onclick="triggerBackup()">Backup Now</button></div>';
  if (b.length === 0) { html += '<div class="card"><div class="empty"><p>No backups yet</p></div></div>'; }
  else {
    html += '<div class="card" style="padding:0"><div class="table-wrap"><table><thead><tr><th>File Name</th><th>Size</th><th>Repos</th><th>Created</th><th>Status</th></tr></thead><tbody>';
    b.forEach(x => {
      const sz = x.file_size_bytes >= 1048576 ? (x.file_size_bytes/1048576).toFixed(1)+' MB' : (x.file_size_bytes/1024).toFixed(1)+' KB';
      const st = x.status === 'uploaded' ? '<span class="badge badge-success">Uploaded</span>' : x.status === 'uploading' ? '<span class="badge badge-info">Uploading</span>' : '<span class="badge badge-danger">Failed</span>';
      html += '<tr><td class="mono">'+esc(x.file_name)+'</td><td>'+sz+'</td><td style="color:var(--muted)">'+x.repo_count+'</td><td style="font-size:12px;color:var(--muted)">'+fmtAgo(x.created_at)+'</td><td>'+st+'</td></tr>';
    });
    html += '</tbody></table></div></div>';
  }
  document.getElementById('main').innerHTML = html;
}

async function triggerBackup() {
  await api('/api/dashboard/action/backup', {method:'POST'});
  alert('Backup triggered');
  loadBackups();
}

async function loadSettings() {
  const d = await api('/api/dashboard/settings');
  const s = d.settings || {};
  let html = '<div class="page-header"><h1>Settings</h1><p>System configuration</p></div>';
  html += '<div class="card" style="max-width:600px;margin-bottom:16px"><div class="card-header"><span class="card-title">System Status</span></div>';
  html += statusRow('Desktop', s.desktop_last_poll && Date.now()-new Date(s.desktop_last_poll).getTime()<600000, fmtAgo(s.desktop_last_poll));
  html += statusRow('Vault Index', s.vault_index_entry_count != null, (s.vault_index_entry_count||0)+' entries');
  html += '</div>';
  html += '<div class="card" style="max-width:600px;margin-bottom:16px"><div class="card-header"><span class="card-title">Backup</span></div>';
  html += statusRow('Status', s.gdrive_auth_status === 'ok', s.gdrive_auth_status === 'ok' ? 'Connected' : 'Disconnected');
  html += statusRow('Last Backup', s.gdrive_last_backup != null, fmtAgo(s.gdrive_last_backup));
  html += '</div>';
  html += '<div class="card" style="max-width:600px"><div class="card-header"><span class="card-title">Session</span></div>';
  html += '<div style="display:flex;gap:8px"><button class="btn btn-outline" onclick="logout()">Log Out</button><button class="btn btn-danger" onclick="logoutAll()">Log Out All Devices</button></div></div>';
  document.getElementById('main').innerHTML = html;
}

async function logout() { await api('/api/auth/logout', {method:'POST'}); showLogin(); }
async function logoutAll() { if(confirm('Log out all devices?')) { await api('/api/auth/logout-all', {method:'POST'}); showLogin(); } }

// ========================================
// Init
// ========================================
checkSession();
</script>
</body>
</html>`;

