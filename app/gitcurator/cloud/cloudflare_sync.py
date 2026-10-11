"""
cloudflare_sync.py — Desktop ↔ Cloudflare Worker sync module
=============================================================
Handles all communication between the desktop app and the Cloudflare Worker.

Features:
  - HMAC-authenticated requests (token never on wire after pairing)
  - Poll /api/pending for new links
  - Push vault_mirror after each batch (debounced, idempotent)
  - Push decommission events (real-time 404s)
  - Push desktop errors
  - Push GDrive backup status
  - Backfill flow (one-time cutover migration)

Usage:
  from cloudflare_sync import CloudflareSync

  sync = CloudflareSync(config)
  if not sync.is_paired():
      sync.pair("ABC12345")  # code from /pair command

  pending = sync.get_pending()
  sync.push_vault_mirror(vault_index_entries, decommission_events)
  sync.push_decommission(url, reason="404", details="HTTP 404")
  sync.push_error("CRITICAL", "GDRIVE_AUTH_EXPIRED", "OAuth token expired")
"""

import json
import time
import hmac
import hashlib
import uuid as _uuid
import urllib.request
import urllib.error
import threading
from typing import Optional, List, Dict, Any, Tuple
from pathlib import Path


# ========================================
# v0.60.2 — THE CHANNEL'S OWN NAME (the 1010 law)
# ========================================
# The owner's report chain (v0.60.0 → v0.60.1): the banish-gate ask
# never reached Telegram even though the scan grammar was proven by
# 17 tests. Two stacked causes, found from a REAL run against the live
# worker: (1) the desktop had never completed /pair (the worker's
# desktop_installs table was EMPTY — CloudflareSync.is_enabled() is
# False, make_telegram_confirm returns None, the gate defers), and
# (2) even a paired install could not talk: every request this module
# sends rode urllib's default signature ``Python-urllib/3.x``, and
# Cloudflare's edge answers that with **error 1010 — "banned browser
# signature"** (a 403 that never reaches the Worker's own code — no
# HMAC check, no route, nothing). Replayed with any other name
# (``GitCurator/…``, curl, a browser UA) the identical request passes.
# So the channel now introduces itself on EVERY leg — pair, the HMAC
# API, health — as the app it is; the honest identity passes the edge,
# the banned one never did. Keep this in sync with VERSION at release
# time (the string only needs to not be a banned signature).
HTTP_USER_AGENT = "GitCurator/0.66.0 (+https://github.com/assadigit/GitCurator)"


# ========================================
# Configuration keys (stored in config.json)
# ========================================

CONFIG_KEYS = {
    'cloudflare_worker_url': '',        # https://github-curator-bot.xxx.workers.dev
    'cloudflare_install_id': '',        # UUID from pairing
    'cloudflare_shared_secret': '',     # 64-char hex from pairing
    'cloudflare_enabled': False,        # Master toggle
    'cloudflare_poll_interval': 300,    # 5 minutes (seconds)
}


# ========================================
# CloudflareSync
# ========================================

class CloudflareSync:
    """Handles all communication with the Cloudflare Worker."""

    def __init__(self, config: dict):
        self.config = config
        self._lock = threading.Lock()
        self._last_poll = 0

    # ========================================
    # Configuration
    # ========================================

    @property
    def worker_url(self) -> str:
        return self.config.get('cloudflare_worker_url', '').rstrip('/')

    @property
    def install_id(self) -> str:
        return self.config.get('cloudflare_install_id', '')

    @property
    def shared_secret(self) -> str:
        return self.config.get('cloudflare_shared_secret', '')

    def is_enabled(self) -> bool:
        return (
            self.config.get('cloudflare_enabled', False)
            and bool(self.worker_url)
            and bool(self.install_id)
            and bool(self.shared_secret)
        )

    def is_paired(self) -> bool:
        return bool(self.install_id) and bool(self.shared_secret)

    # ========================================
    # Pairing
    # ========================================

    def pair(self, code: str) -> Tuple[bool, str]:
        """
        Pair with the Worker using a code from /pair command.
        Returns (success, message).
        """
        if not self.worker_url:
            return False, "No Worker URL configured. Enter it in the Cloudflare tab first."

        try:
            url = f"{self.worker_url}/api/pair"
            body = json.dumps({"code": code}).encode('utf-8')

            req = urllib.request.Request(
                url,
                data=body,
                headers={'Content-Type': 'application/json',
                         # v0.60.2 — the 1010 law: urllib's default
                         # signature is banned at Cloudflare's edge; the
                         # pairing leg must introduce itself too.
                         'User-Agent': HTTP_USER_AGENT},
                method='POST'
            )

            # Bypass proxy (fixes 403 from v2rayN intercepting workers.dev)
            proxy_handler = urllib.request.ProxyHandler({})
            opener = urllib.request.build_opener(proxy_handler)

            with opener.open(req, timeout=30) as resp:
                data = json.loads(resp.read())

            if data.get('success'):
                self.config['cloudflare_install_id'] = data['install_id']
                self.config['cloudflare_shared_secret'] = data['shared_secret']
                self.config['cloudflare_enabled'] = True
                return True, "Paired successfully!"
            else:
                return False, data.get('error', 'Pairing failed')

        except urllib.error.HTTPError as e:
            # Read the actual response body for debugging
            try:
                body_text = e.read().decode('utf-8', errors='replace')
            except Exception:
                body_text = ''

            try:
                err_data = json.loads(body_text)
                err_msg = err_data.get('error', f'HTTP {e.code}')
            except Exception:
                err_msg = body_text[:200] if body_text else f'HTTP {e.code}'

            if e.code == 403:
                return False, f"HTTP 403 from Worker.\n\nResponse: {err_msg}\n\nWorker URL: {self.worker_url}\n\nThis usually means:\n1. Worker needs redeploy: run 'npx wrangler deploy' in the cloudflare folder\n2. Wrong Worker URL (check spelling)\n3. Cloudflare blocking the request"
            if e.code == 401:
                return False, f"Invalid or expired pairing code (401).\n\nResponse: {err_msg}\n\nRun /pair again in Telegram and enter the code within 10 minutes."
            return False, f"HTTP {e.code}: {err_msg}"
        except urllib.error.URLError as e:
            return False, f"Cannot reach Worker at {self.worker_url}: {e.reason}"
        except Exception as e:
            return False, f"Pairing error: {type(e).__name__}: {e}"

    def unpair(self):
        """Remove pairing (for reset/debug)."""
        self.config['cloudflare_install_id'] = ''
        self.config['cloudflare_shared_secret'] = ''
        self.config['cloudflare_enabled'] = False

    # ========================================
    # HMAC request signing
    # ========================================

    def _sign_request(self, method: str, path: str, body: str) -> Dict[str, str]:
        """Generate HMAC signature headers for a request.

        v30 — Fix (HMAC key mismatch — the 401-on-every-endpoint bug):
        The Worker's pairing handler stores sha256(shared_secret) in the DB
        (cloudflare-bot/src/api.js handlePair -> installInsert(..., secretHash))
        and verifies with hmacSha256(install.shared_secret_hash, message) —
        i.e. the HMAC KEY is the sha256 hex digest of the raw secret.

        The desktop used to sign with the RAW shared_secret as the key, so
        every one of the 8 sync endpoints (pending / vault_mirror /
        decommission / errors / gdrive status / ...) failed with
        401 'Invalid signature'. Sign with the same sha256-hex key the
        Worker derives, byte-for-byte (hex string encoded as UTF-8)."""
        timestamp = str(int(time.time() * 1000))
        body_hash = hashlib.sha256(body.encode('utf-8')).hexdigest()
        message = f"{method}\n{path}\n{timestamp}\n{body_hash}"

        # Key = sha256(raw shared secret) as lowercase hex — matches the
        # Worker's stored shared_secret_hash exactly (utils.js sha256()
        # returns lowercase hex; hmacSha256() uses its UTF-8 bytes as key).
        key_hex = hashlib.sha256(
            self.shared_secret.encode('utf-8')
        ).hexdigest()

        signature = hmac.new(
            key_hex.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        return {
            'X-Auth-Install': self.install_id,
            'X-Auth-Timestamp': timestamp,
            'X-Auth-Signature': signature,
            'Content-Type': 'application/json',
            # v0.60.2 — the 1010 law (the ask-never-arrived chain's
            # second cause): urllib's default ``Python-urllib/3.x``
            # signature is banned at Cloudflare's edge — every HMAC
            # request died with error 1010 before the Worker ever saw
            # it. The channel introduces itself as the app instead.
            'User-Agent': HTTP_USER_AGENT
        }

    def _make_request(self, method: str, path: str, body: dict = None) -> Tuple[int, dict]:
        """
        Make an HMAC-signed request to the Worker.
        Returns (status_code, response_json).
        """
        url = f"{self.worker_url}{path}"
        body_str = json.dumps(body) if body else '{}'

        headers = self._sign_request(method, path, body_str)

        req = urllib.request.Request(
            url,
            data=body_str.encode('utf-8'),
            headers=headers,
            method=method
        )

        # Build an opener that BYPASSES proxy for workers.dev
        # (fixes 403 from v2rayN proxy intercepting the request)
        proxy_handler = urllib.request.ProxyHandler({})  # Empty = no proxy
        opener = urllib.request.build_opener(proxy_handler)

        try:
            with opener.open(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            try:
                err_data = json.loads(e.read())
                return e.code, err_data
            except Exception:
                return e.code, {'error': str(e)}
        except Exception as e:
            return 0, {'error': str(e)}

    # ========================================
    # Poll pending links
    # ========================================

    def get_pending(self) -> List[Dict[str, Any]]:
        """
        Fetch pending links from the Worker.
        Returns list of pending link dicts.
        """
        if not self.is_enabled():
            return []

        with self._lock:
            self._last_poll = time.time()
            status, data = self._make_request('GET', '/api/pending')

        if status == 200 and data.get('success'):
            return data.get('pending', [])
        else:
            print(f"[CloudflareSync] get_pending failed: {status} {data}")
            return []

    def should_poll(self) -> bool:
        """Check if enough time has passed since last poll."""
        interval = self.config.get('cloudflare_poll_interval', 300)
        return (time.time() - self._last_poll) >= interval

    # ========================================
    # Push vault mirror
    # ========================================

    def push_vault_mirror(
        self,
        entries: List[Dict[str, Any]],
        decommission_events: List[Dict[str, Any]] = None,
        vault_index_hash: str = None
    ) -> Tuple[bool, dict]:
        """
        Push vault_mirror entries to the Worker (idempotent via sync_id).

        Args:
            entries: List of {url_normalized, vault_path, category, title, status, ...}
            decommission_events: List of {url_normalized, reason, details}
            vault_index_hash: SHA256 of local vault index (for change detection)

        Returns (success, response).
        """
        if not self.is_enabled():
            return False, {'error': 'Not enabled'}

        sync_id = str(_uuid.uuid4())
        body = {
            'install_id': self.install_id,
            'sync_id': sync_id,
            'vault_index_hash': vault_index_hash or '',
            'entries': entries,
            'decommission_events': decommission_events or []
        }

        with self._lock:
            status, data = self._make_request('POST', '/api/vault_mirror/push', body)

        if status == 200 and data.get('success'):
            return True, data
        else:
            print(f"[CloudflareSync] push_vault_mirror failed: {status} {data}")
            return False, data

    # ========================================
    # Push decommission (real-time)
    # ========================================

    def push_decommission(self, url: str, reason: str, details: str = '') -> bool:
        """Push a real-time decommission event (e.g., 404 detected)."""
        if not self.is_enabled():
            return False

        body = {
            'install_id': self.install_id,
            'url_normalized': url,
            'reason': reason,
            'details': details
        }

        status, data = self._make_request('POST', '/api/decommission', body)

        if status == 200 and data.get('success'):
            return True
        else:
            print(f"[CloudflareSync] push_decommission failed: {status} {data}")
            return False

    # ========================================
    # v0.60.0 — THE CONFIRMATION GATE (banish round-trip)
    # ========================================

    def propose_banish(self, count: int, items: List[Dict[str, Any]],
                       timeout_s: float = 300.0) -> Tuple[Optional[str], str]:
        """v0.60.0 — ask the owner to confirm the pending deletions.

        POSTs ``/api/banish/propose`` (HMAC-signed like every desktop
        endpoint): the Worker sends the Telegram message with the count
        and the two buttons (🗑️ Delete all N / ✋ Keep all N) and keeps
        the ask in its state table. Returns ``(id, '')`` on success —
        the id the status polling and the result report both address —
        or ``(None, reason)`` when the ask could not be placed."""
        if not self.is_enabled():
            return None, 'not enabled'
        body = {'install_id': self.install_id,
                'count': int(count or 0),
                'items': list(items or []),
                'timeout_s': float(timeout_s or 300.0)}
        with self._lock:
            status, data = self._make_request(
                'POST', '/api/banish/propose', body)
        if status == 200 and data.get('success') and data.get('id'):
            return str(data.get('id')), ''
        return None, str(data.get('error') or f'HTTP {status}')

    def banish_status(self, confirm_id: str) -> Optional[str]:
        """v0.60.0 — poll the ask's status ('pending' / 'confirmed' /
        'declined' / 'timeout' / 'superseded'); None when the Worker
        could not answer (the caller treats it as still-pending and
        keeps polling until its own deadline)."""
        if not self.is_enabled() or not confirm_id:
            return None
        with self._lock:
            status, data = self._make_request(
                'GET', f'/api/banish/status?id={confirm_id}')
        if status == 200 and data.get('success'):
            return str(data.get('status') or 'pending')
        return None

    def report_banish_result(self, confirm_id: str, outcome: str,
                             deleted: int = 0) -> bool:
        """v0.60.0 — close the ask: outcome 'deleted' (the confirmed
        run's final count — the Worker edits the Telegram message into
        the closing line) or 'timeout' (the desktop's clock ran out —
        the message becomes the no-answer story). Best-effort: a failed
        report never fails the run."""
        if not self.is_enabled() or not confirm_id:
            return False
        body = {'install_id': self.install_id,
                'id': confirm_id, 'outcome': str(outcome or ''),
                'deleted': int(deleted or 0)}
        with self._lock:
            status, data = self._make_request(
                'POST', '/api/banish/result', body)
        return status == 200 and bool(data.get('success'))

    # ========================================
    # v0.61.0 — THE VAULT SCAN's round-trip (the banish trio's twin)
    # ========================================

    def propose_scan(self, deletions: int, moves: int, new_folders: int,
                     items: List[Dict[str, Any]],
                     summary: str = '',
                     timeout_s: float = 300.0
                     ) -> Tuple[Optional[str], str]:
        """v0.61.0 — ask the owner to confirm the VAULT SCAN plan.

        POSTs ``/api/scan/propose`` (HMAC-signed like every desktop
        endpoint — the 1010 law's UA included): the Worker sends the
        Telegram message with the plan's counts and the two buttons
        (🗂️ Apply plan / ✋ Keep everything) and keeps the ask in its
        state table (``scan_confirm:<id>``). Returns ``(id, '')`` or
        ``(None, reason)``."""
        if not self.is_enabled():
            return None, 'not enabled'
        body = {'install_id': self.install_id,
                'deletions': int(deletions or 0),
                'moves': int(moves or 0),
                'new_folders': int(new_folders or 0),
                'items': list(items or []),
                'summary': str(summary or '')[:400],
                'timeout_s': float(timeout_s or 300.0)}
        with self._lock:
            status, data = self._make_request(
                'POST', '/api/scan/propose', body)
        if status == 200 and data.get('success') and data.get('id'):
            return str(data.get('id')), ''
        return None, str(data.get('error') or f'HTTP {status}')

    def scan_status(self, confirm_id: str) -> Optional[str]:
        """v0.61.0 — poll the scan ask's status ('pending' /
        'confirmed' / 'declined' / 'timeout' / 'superseded'); None when
        the Worker could not answer (the caller keeps polling until its
        own deadline)."""
        if not self.is_enabled() or not confirm_id:
            return None
        with self._lock:
            status, data = self._make_request(
                'GET', f'/api/scan/status?id={confirm_id}')
        if status == 200 and data.get('success'):
            return str(data.get('status') or 'pending')
        return None

    def report_scan_result(self, confirm_id: str, outcome: str,
                           applied: int = 0, moved: int = 0,
                           folders: int = 0) -> bool:
        """v0.61.0 — close the scan ask: outcome 'applied' (the counts
        that actually landed — deletions, moves, folders — the Worker
        edits the message into the closing line) or 'timeout' (the
        no-answer story). Best-effort: a failed report never fails the
        run."""
        if not self.is_enabled() or not confirm_id:
            return False
        body = {'install_id': self.install_id,
                'id': confirm_id, 'outcome': str(outcome or ''),
                'applied': int(applied or 0),
                'moved': int(moved or 0),
                'folders': int(folders or 0)}
        with self._lock:
            status, data = self._make_request(
                'POST', '/api/scan/result', body)
        return status == 200 and bool(data.get('success'))

    # ========================================
    # Push errors
    # ========================================

    def push_errors(self, errors: List[Dict[str, Any]]) -> bool:
        """
        Push desktop errors to the Worker.

        Args:
            errors: List of {severity, error_code, message, details, occurred_at}
        """
        if not self.is_enabled():
            return False

        body = {
            'install_id': self.install_id,
            'errors': errors
        }

        status, data = self._make_request('POST', '/api/errors', body)

        if status == 200 and data.get('success'):
            return True
        else:
            print(f"[CloudflareSync] push_errors failed: {status} {data}")
            return False

    # ========================================
    # Push GDrive backup status
    # ========================================

    def push_backup_status(self, backup_data: Dict[str, Any]) -> bool:
        """Push GDrive backup metadata to the Worker."""
        if not self.is_enabled():
            return False

        body = {
            'install_id': self.install_id,
            **backup_data
        }

        status, data = self._make_request('POST', '/api/gdrive/backup_status', body)

        if status == 200 and data.get('success'):
            return True
        else:
            print(f"[CloudflareSync] push_backup_status failed: {status} {data}")
            return False

    # ========================================
    # Verify (full reconciliation)
    # ========================================

    def verify(self) -> Optional[dict]:
        """Get full reconciliation data from the Worker."""
        if not self.is_enabled():
            return None

        status, data = self._make_request('GET', '/api/verify')

        if status == 200 and data.get('success'):
            return data
        else:
            print(f"[CloudflareSync] verify failed: {status} {data}")
            return None

    # ========================================
    # Get decommissions (pull bot-side)
    # ========================================

    def get_decommissions(self, since: str = '1970-01-01T00:00:00Z') -> List[Dict]:
        """Pull bot-originated decommission events."""
        if not self.is_enabled():
            return []

        status, data = self._make_request('GET', f'/api/decommissions?since={since}')

        if status == 200 and data.get('success'):
            return data.get('decommissions', [])
        else:
            print(f"[CloudflareSync] get_decommissions failed: {status} {data}")
            return []

    # ========================================
    # Backfill (one-time cutover migration)
    # ========================================

    def backfill_chunk(
        self,
        entries: List[Dict[str, Any]],
        chunk_num: int,
        total_chunks: int
    ) -> Tuple[bool, dict]:
        """
        Push a backfill chunk to the Worker (idempotent).

        Args:
            entries: List of {url_normalized, url_original, url_type, github_owner, github_repo, first_seen_at}
            chunk_num: Current chunk number (0-indexed)
            total_chunks: Total number of chunks

        Returns (success, response with progress).
        """
        if not self.is_enabled():
            return False, {'error': 'Not enabled'}

        body = {
            'install_id': self.install_id,
            'entries': entries,
            'chunk_num': chunk_num,
            'total_chunks': total_chunks
        }

        status, data = self._make_request('POST', '/api/backfill', body)

        if status == 200 and data.get('success'):
            return True, data
        else:
            print(f"[CloudflareSync] backfill_chunk failed: {status} {data}")
            return False, data

    # ========================================
    # Health check
    # ========================================

    def health_check(self) -> Optional[dict]:
        """Check Worker health (no auth required)."""
        try:
            url = f"{self.worker_url}/health"
            # v0.60.2 — the 1010 law: health checks wear the app's name
            # too (urllib's default signature is edge-banned).
            req = urllib.request.Request(
                url, method='GET',
                headers={'User-Agent': HTTP_USER_AGENT})
            # Bypass proxy for workers.dev
            proxy_handler = urllib.request.ProxyHandler({})
            opener = urllib.request.build_opener(proxy_handler)
            with opener.open(req, timeout=10) as resp:
                return json.loads(resp.read())
        except Exception as e:
            print(f"[CloudflareSync] health_check failed: {e}")
            return None

    # ========================================
    # URL normalization (must match Worker + existing desktop logic)
    # ========================================

    @staticmethod
    def normalize_url(raw_url: str) -> str:
        """Normalize URL for dedup. Must match Worker's normalizeUrl()."""
        u = raw_url.strip().rstrip('.,);')

        # twitter.com → x.com
        u = u.replace('twitter.com', 'x.com')

        # Strip fragment
        if '#' in u:
            u = u.split('#')[0]

        # Strip query string
        if '?' in u:
            u = u.split('?')[0]

        # Strip trailing slashes
        u = u.rstrip('/')
        if u.endswith(':'):
            u += '//'

        # Lowercase domain
        if '://' in u:
            scheme, rest = u.split('://', 1)
            if '/' in rest:
                slash_idx = rest.index('/')
                domain = rest[:slash_idx].lower()
                path = rest[slash_idx:]
                u = f"{scheme}://{domain}{path}"
            else:
                u = f"{scheme}://{rest.lower()}"

        return u


# ========================================
# Integration helper: build vault_mirror entries from VaultIndex
# ========================================

def build_vault_mirror_entries(vault_index: dict, cache_db=None) -> List[Dict[str, Any]]:
    """
    Build vault_mirror entries from the desktop's VaultIndex.

    Args:
        vault_index: Dict of {url_normalized: {path, category, title, ...}}
        cache_db: Optional CacheDB instance for decommission lookup

    Returns list of entries for push_vault_mirror().
    """
    entries = []
    for url, info in vault_index.items():
        entries.append({
            'url_normalized': url,
            'vault_path': info.get('path', ''),
            'category': info.get('category', ''),
            'title': info.get('title', ''),
            'status': 'in_vault',
            'first_processed_at': info.get('first_processed'),
            'last_updated_at': info.get('last_updated') or _now_iso()
        })
    return entries


def build_decommission_events(cache_db) -> List[Dict[str, Any]]:
    """
    Build decommission_events list from the desktop's CacheDB.

    Args:
        cache_db: CacheDB instance with get_all_decommissioned() method

    Returns list of decommission events.
    """
    events = []
    if cache_db and hasattr(cache_db, 'get_all_decommissioned'):
        for repo in cache_db.get_all_decommissioned():
            events.append({
                'url_normalized': repo.get('url_normalized', repo.get('url', '')),
                'reason': repo.get('reason', '404'),
                'details': repo.get('details', ''),
                'decommissioned_at': repo.get('decommissioned_at', _now_iso())
            })
    return events


def _now_iso() -> str:
    """Get current UTC ISO timestamp."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
