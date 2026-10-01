-- ========================================
-- GitHub Project Curator Bot — D1 Schema
-- ========================================
-- Apply with: wrangler d1 execute curator-bot --file=schema.sql
-- ========================================

-- ========================================
-- 1. ever_seen_ledger (immutable, bot-owned — the safety net)
-- ========================================
CREATE TABLE IF NOT EXISTS ever_seen_ledger (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  url_normalized     TEXT NOT NULL UNIQUE,
  url_original       TEXT NOT NULL,
  url_type           TEXT NOT NULL DEFAULT 'github',  -- 'github' | 'non_github'
  github_owner       TEXT,
  github_repo        TEXT,
  github_stars       INTEGER,
  github_description TEXT,
  github_language    TEXT,
  github_topics      TEXT,     -- JSON array
  github_readme_excerpt TEXT,
  github_fetched_at  TEXT,
  first_seen_at      TEXT NOT NULL,
  last_seen_at       TEXT NOT NULL,
  forward_count      INTEGER DEFAULT 1,
  telegram_message_id INTEGER,
  telegram_chat_id   INTEGER,
  bot_reply_message_id INTEGER,
  redirected_from    TEXT,     -- if this URL was a redirect target, original URL
  forgotten          INTEGER DEFAULT 0,
  notes              TEXT
);

CREATE INDEX IF NOT EXISTS idx_ledger_url ON ever_seen_ledger(url_normalized);
CREATE INDEX IF NOT EXISTS idx_ledger_type ON ever_seen_ledger(url_type);
CREATE INDEX IF NOT EXISTS idx_ledger_seen ON ever_seen_ledger(last_seen_at);
CREATE INDEX IF NOT EXISTS idx_ledger_forgotten ON ever_seen_ledger(forgotten);

-- ========================================
-- 2. vault_mirror (mutable, desktop-owned — current vault state)
-- ========================================
CREATE TABLE IF NOT EXISTS vault_mirror (
  url_normalized     TEXT PRIMARY KEY,
  vault_path         TEXT,
  category           TEXT,
  title              TEXT,
  status             TEXT NOT NULL DEFAULT 'pending',  -- 'in_vault' | 'pending' | 'decommissioned' | 'dead_letter'
  first_processed_at TEXT,
  last_updated_at    TEXT NOT NULL,
  desktop_sync_id    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mirror_status ON vault_mirror(status);
CREATE INDEX IF NOT EXISTS idx_mirror_category ON vault_mirror(category);
CREATE INDEX IF NOT EXISTS idx_mirror_path ON vault_mirror(vault_path);

-- ========================================
-- 3. decommission_events (two-way sync, union)
-- ========================================
CREATE TABLE IF NOT EXISTS decommission_events (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  url_normalized     TEXT NOT NULL,
  source             TEXT NOT NULL,    -- 'desktop' | 'bot'
  reason             TEXT NOT NULL,    -- '404' | 'manual' | 'invalid_url' | 'fetch_failed'
  decommissioned_at  TEXT NOT NULL,
  details            TEXT,
  sync_id            TEXT
);

CREATE INDEX IF NOT EXISTS idx_decomm_url ON decommission_events(url_normalized);
CREATE INDEX IF NOT EXISTS idx_decomm_source ON decommission_events(source);

-- ========================================
-- 4. dead_letters (links that can never be processed)
-- ========================================
CREATE TABLE IF NOT EXISTS dead_letters (
  url_normalized     TEXT PRIMARY KEY,
  url_original       TEXT NOT NULL,
  reason             TEXT NOT NULL,    -- 'invalid_format' | 'persistent_fetch_fail' | 'blocked_domain' | 'dlq_exhausted' (v0.25.0: the DLQ drain records exhausted-retry links)
  first_attempted_at TEXT NOT NULL,   -- ('not_github' retired in v0.22.0 — websites are accepted now)
  last_attempted_at  TEXT NOT NULL,
  attempt_count      INTEGER DEFAULT 1,
  telegram_message_id INTEGER,
  resolved           INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_deadletters_resolved ON dead_letters(resolved);

-- ========================================
-- 5. desktop_errors (desktop → bot error log)
-- ========================================
CREATE TABLE IF NOT EXISTS desktop_errors (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  severity           TEXT NOT NULL,    -- 'CRITICAL' | 'WARNING' | 'INFO' | 'DEBUG'
  error_code         TEXT NOT NULL,
  message            TEXT NOT NULL,
  details            TEXT,
  occurred_at        TEXT NOT NULL,
  desktop_sync_id    TEXT,
  dm_sent            INTEGER DEFAULT 0,
  dm_message_id      INTEGER,
  acknowledged       INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_errors_severity ON desktop_errors(severity);
CREATE INDEX IF NOT EXISTS idx_errors_time ON desktop_errors(occurred_at);
CREATE INDEX IF NOT EXISTS idx_errors_unack ON desktop_errors(acknowledged);

-- ========================================
-- 6. sync_state (idempotency + reconciliation)
-- ========================================
CREATE TABLE IF NOT EXISTS sync_state (
  key                 TEXT PRIMARY KEY,
  value               TEXT NOT NULL,
  updated_at          TEXT NOT NULL
);

-- Initialize default sync state
INSERT OR IGNORE INTO sync_state (key, value, updated_at) VALUES
  ('desktop_last_sync', '', datetime('now')),
  ('desktop_last_poll', '', datetime('now')),
  ('vault_index_hash', '', datetime('now')),
  ('vault_index_entry_count', '0', datetime('now')),
  ('cutover_complete', '0', datetime('now')),
  ('gdrive_last_backup', '', datetime('now')),
  ('gdrive_auth_status', 'unknown', datetime('now')),
  ('last_webhook_at', '', datetime('now')),
  ('backfill_progress', '{}', datetime('now'));

-- ========================================
-- 7. gdrive_snapshots (backup metadata)
-- ========================================
CREATE TABLE IF NOT EXISTS gdrive_snapshots (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  backup_id          TEXT NOT NULL UNIQUE,
  file_name          TEXT NOT NULL,
  file_size_bytes    INTEGER NOT NULL,
  repo_count         INTEGER NOT NULL,
  trigger            TEXT NOT NULL,    -- 'batch_complete' | 'manual' | 'startup'
  created_at         TEXT NOT NULL,
  gdrive_file_id     TEXT,
  status             TEXT NOT NULL,    -- 'uploading' | 'uploaded' | 'failed'
  error_message      TEXT
);

CREATE INDEX IF NOT EXISTS idx_gdrive_time ON gdrive_snapshots(created_at);
CREATE INDEX IF NOT EXISTS idx_gdrive_status ON gdrive_snapshots(status);

-- ========================================
-- 8. dashboard_sessions (web dashboard auth)
-- ========================================
CREATE TABLE IF NOT EXISTS dashboard_sessions (
  session_id         TEXT PRIMARY KEY,
  user_id            INTEGER NOT NULL,
  magic_token        TEXT UNIQUE,
  created_at         TEXT NOT NULL,
  expires_at         TEXT NOT NULL,
  last_seen_at       TEXT NOT NULL,
  user_agent         TEXT,
  ip_address         TEXT
);

CREATE INDEX IF NOT EXISTS idx_session_user ON dashboard_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_session_expires ON dashboard_sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_session_magic ON dashboard_sessions(magic_token);

-- ========================================
-- 9. desktop_installs (HMAC pairing registry)
-- ========================================
CREATE TABLE IF NOT EXISTS desktop_installs (
  install_id         TEXT PRIMARY KEY,
  shared_secret_hash TEXT NOT NULL,
  paired_at          TEXT NOT NULL,
  last_seen_at       TEXT NOT NULL,
  user_agent         TEXT
);

-- ========================================
-- 10. activity_log (recent activity for dashboard)
-- ========================================
CREATE TABLE IF NOT EXISTS activity_log (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type         TEXT NOT NULL,    -- 'received' | 'processed' | 'decommissioned' | 'error' | 'sync' | 'backup'
  url                TEXT,
  message            TEXT NOT NULL,
  occurred_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_activity_time ON activity_log(occurred_at);
CREATE INDEX IF NOT EXISTS idx_activity_type ON activity_log(event_type);
