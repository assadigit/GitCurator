// fake-db.mjs — a tiny in-memory D1 shim for the worker's Node tests.
// Not a SQL engine: it pattern-matches the exact statements the tested
// code paths issue (see db.js / api.js / queue-consumer.js). Tables are
// plain Maps so tests can seed and assert directly.

export class FakeD1 {
  constructor() {
    this.ledger = new Map();        // url_normalized -> row
    this.mirror = new Map();        // url_normalized -> row
    this.decommission = [];         // rows
    this.deadLetters = new Map();   // url_normalized -> row
    this.errors = [];
    this.state = new Map();         // key -> value
    this.installs = new Map();      // install_id -> row
    this.activity = [];
    this.statements = [];           // every (sql, args) seen, for assertions
  }

  prepare(sql) {
    const stmt = {
      _args: [],
      bind(...args) { this._args = args; return this; },
      async first() { stmt._db.statements.push([sql, this._args]); return stmt._db._query(sql, this._args, 'first'); },
      async all() { stmt._db.statements.push([sql, this._args]); return { results: stmt._db._query(sql, this._args, 'all') }; },
      async run() { stmt._db.statements.push([sql, this._args]); return stmt._db._query(sql, this._args, 'run'); },
      _db: null
    };
    stmt._db = this;
    return stmt;
  }

  _query(sql, args, mode) {
    const s = sql.replace(/\s+/g, ' ').trim();

    if (s.includes('FROM ever_seen_ledger WHERE url_normalized = ?')) {
      return this.ledger.get(args[0]) || null;
    }
    if (s.includes('INSERT INTO ever_seen_ledger')) {
      const [urlN, urlO, type, owner, repo, firstSeen, lastSeen, msgId, chatId, botReply] = args;
      this.ledger.set(urlN, {
        id: this.ledger.size + 1, url_normalized: urlN, url_original: urlO,
        url_type: type, github_owner: owner, github_repo: repo,
        first_seen_at: firstSeen, last_seen_at: lastSeen, forward_count: 1,
        telegram_message_id: msgId, telegram_chat_id: chatId,
        bot_reply_message_id: botReply, forgotten: 0,
        github_stars: null, github_description: null, github_language: null,
        github_topics: null, github_readme_excerpt: null
      });
      return { success: true };
    }
    if (s.includes('UPDATE ever_seen_ledger SET forward_count = forward_count + 1')) {
      const row = this.ledger.get(args[2]);
      if (row) { row.forward_count += 1; row.last_seen_at = args[0]; }
      return { success: true };
    }
    if (s.includes('UPDATE ever_seen_ledger SET bot_reply_message_id')) {
      const row = this.ledger.get(args[1]);
      if (row) row.bot_reply_message_id = args[0];
      return { success: true };
    }
    if (s.includes('FROM vault_mirror WHERE url_normalized = ?')) {
      return this.mirror.get(args[0]) || null;
    }
    if (s.includes('INSERT INTO vault_mirror')) {
      const [urlN, path, category, title, status, firstAt, lastAt, syncId] = args;
      this.mirror.set(urlN, {
        url_normalized: urlN, vault_path: path, category, title, status,
        first_processed_at: firstAt, last_updated_at: lastAt, desktop_sync_id: syncId
      });
      return { success: true };
    }
    if (s.startsWith('SELECT l.* FROM ever_seen_ledger l')) {
      // /api/pending: ledger rows not in mirror (or pending), not
      // decommissioned, not POLICY-dead (v0.26.0: only blocked_domain
      // is hidden — a transient dlq_exhausted row must stay visible to
      // the desktop sync) — oldest first, LIMIT 100.
      const dead = new Set([...this.deadLetters.values()]
        .filter(r => !r.resolved && r.reason === 'blocked_domain')
        .map(r => r.url_normalized));
      const decomm = new Set(this.decommission.map(r => r.url_normalized));
      const rows = [...this.ledger.values()]
        .filter(r => !r.forgotten)
        .filter(r => {
          const m = this.mirror.get(r.url_normalized);
          return (!m || m.status === 'pending');
        })
        .filter(r => !decomm.has(r.url_normalized))
        .filter(r => !dead.has(r.url_normalized))
        .sort((a, b) => String(a.first_seen_at).localeCompare(String(b.first_seen_at)));
      return mode === 'first' ? null : rows;
    }
    if (s.includes('FROM decommission_events WHERE url_normalized = ?')) {
      const hit = this.decommission.some(r => r.url_normalized === args[0]);
      return hit ? { 1: 1 } : null;
    }
    if (s.includes('INSERT INTO decommission_events')) {
      const [urlN, source, reason, decommissionedAt, details, syncId] = args;
      this.decommission.push({
        url_normalized: urlN, source, reason, details,
        decommissioned_at: decommissionedAt, desktop_sync_id: syncId
      });
      return { success: true };
    }
    if (s.includes('FROM decommission_events WHERE decommissioned_at > ?')) {
      const rows = this.decommission
        .filter(r => r.source === 'bot' && String(r.decommissioned_at) > args[0])
        .sort((a, b) => String(b.decommissioned_at).localeCompare(String(a.decommissioned_at)))
        .slice(0, args[1]);
      return mode === 'first' ? null : rows;
    }
    if (s.includes("FROM dead_letters WHERE url_normalized = ? AND reason = 'blocked_domain'")) {
      // v0.26.0 deadLetterIsDead: only POLICY dead letters block intake.
      const row = this.deadLetters.get(args[0]);
      return (row && !row.resolved && row.reason === 'blocked_domain') ? { 1: 1 } : null;
    }
    if (s.includes('INSERT INTO dead_letters')) {
      const [urlN, urlO, reason, firstAt, lastAt, msgId] = args;
      const existing = this.deadLetters.get(urlN);
      if (existing) {
        existing.last_attempted_at = lastAt;
        existing.attempt_count += 1;
      } else {
        this.deadLetters.set(urlN, {
          url_normalized: urlN, url_original: urlO, reason,
          first_attempted_at: firstAt, last_attempted_at: lastAt,
          attempt_count: 1, telegram_message_id: msgId, resolved: 0
        });
      }
      return { success: true };
    }
    if (s.includes('FROM sync_state WHERE key = ?')) {
      const v = this.state.get(args[0]);
      return v === undefined ? null : { value: v };
    }
    if (s.includes('FROM sync_state') && s.includes("key = '")) {
      const key = s.match(/key = '([^']+)'/)[1];
      const v = this.state.get(key);
      return v === undefined ? null : { value: v };
    }
    if (s.includes('url_type, COUNT(*) as count FROM ever_seen_ledger')) {
      const byType = {};
      for (const r of this.ledger.values()) {
        if (r.forgotten) continue;
        byType[r.url_type] = (byType[r.url_type] || 0) + 1;
      }
      return mode === 'first' ? null : Object.entries(byType).map(([url_type, count]) => ({ url_type, count }));
    }
    if (s.includes('COUNT(*) as count FROM ever_seen_ledger')) {
      return { count: [...this.ledger.values()].filter(r => !r.forgotten).length };
    }
    if (s.includes('COUNT(DISTINCT url_normalized) as count FROM decommission_events')) {
      return { count: new Set(this.decommission.map(r => r.url_normalized)).size };
    }
    if (s.includes('COUNT(*) as count FROM dead_letters')) {
      return { count: [...this.deadLetters.values()].filter(r => !r.resolved).length };
    }
    if (s.includes('INSERT INTO sync_state')) {
      this.state.set(args[0], args[1]);
      return { success: true };
    }
    if (s.includes('INSERT INTO activity_log')) {
      this.activity.push({ event_type: args[0], url: args[1], message: args[2] });
      return { success: true };
    }
    if (s.includes('FROM desktop_installs WHERE install_id = ?')) {
      return this.installs.get(args[0]) || null;
    }
    if (s.includes('INSERT INTO desktop_installs')) {
      this.installs.set(args[0], {
        install_id: args[0], shared_secret_hash: args[1],
        last_seen_at: new Date().toISOString()
      });
      return { success: true };
    }
    if (s.includes('UPDATE desktop_installs SET last_seen_at')) {
      const row = this.installs.get(args[0]);
      if (row) row.last_seen_at = args[1];
      return { success: true };
    }
    if (s.includes('GROUP_CONCAT(url_normalized')) {
      const table = s.includes('FROM ever_seen_ledger') ? this.ledger : this.mirror;
      const concat = [...table.keys()].join('|');
      return concat ? { concat } : null;
    }
    if (s.includes('status, COUNT(*) as count FROM vault_mirror')) {
      const counts = {};
      for (const r of this.mirror.values()) counts[r.status] = (counts[r.status] || 0) + 1;
      return Object.entries(counts).map(([status, count]) => ({ status, count }));
    }
    if (s.includes('SELECT COUNT(*) as count FROM vault_mirror')) {
      return { count: this.mirror.size };
    }
    throw new Error(`FakeD1: unhandled statement: ${s.slice(0, 90)}`);
  }
}

export class FakeKV {
  constructor() { this.map = new Map(); }
  async get(key, type) {
    const v = this.map.get(key);
    if (v === undefined) return null;
    return type === 'json' ? JSON.parse(v) : v;
  }
  async put(key, value, _opts) { this.map.set(key, value); }
  async delete(key) { this.map.delete(key); }
}

export function makeEnv(over = {}) {
  return {
    DB: new FakeD1(),
    CACHE: new FakeKV(),
    INGEST_QUEUE: { send: async () => ({ ok: true }) },
    DLQ: { send: async () => ({ ok: true }) },
    ALLOWED_USER_IDS: '12345',
    BLOCKED_DOMAINS: undefined,
    SELF_DOMAINS: undefined,
    BOT_TOKEN: undefined,
    ...over
  };
}

export function message(body) {
  return {
    body: typeof body === 'string' ? body : JSON.stringify(body),
    acked: false, retried: false,
    ack() { this.acked = true; },
    retry() { this.retried = true; }
  };
}

// A Request stand-in that CAN carry a body on GET — exactly what the
// desktop's urllib does (cloudflare_sync._make_request sends '{}' on GET),
// which undici's Request forbids.
export class FakeRequest {
  constructor(url, { method = 'GET', headers = {}, body = null } = {}) {
    this.url = url;
    this.method = method;
    this.headers = new Map(Object.entries(headers));
    this._body = body;
  }
  clone() {
    return new FakeRequest(this.url, {
      method: this.method,
      headers: Object.fromEntries(this.headers),
      body: this._body
    });
  }
  async text() { return this._body === null ? '' : String(this._body); }
  async json() { return JSON.parse(await this.text()); }
}

