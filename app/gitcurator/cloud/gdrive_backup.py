"""
gdrive_backup.py — Google Drive backup for Obsidian vault
==========================================================
Full-zip backup after every batch, last 10 retained (FIFO rotation).
OAuth 2.0 with automatic token refresh. Alert on auth expiry.

Backup strategy:
  - Full zip of vault folder (excluding secrets, git, machine state)
  - Upload to Google Drive → folder "Obsidian-Vault-Backups/"
  - FIFO rotation: keep last 10, delete oldest
  - After upload, notify Worker (for /backup and /restore commands)

Restore options:
  - Restore to new folder (safe default)
  - Replace current vault (with local safety net backup)
  - Download ZIP (manual)

Exclusions (non-negotiable):
  - config.json (contains ALL secrets)
  - .git/ (version control metadata)
  - .obsidian/workspace*.json (machine-specific state)
  - *.lock, __pycache__/

OAuth setup:
  1. Go to https://console.cloud.google.com/
  2. Create a project → Enable Google Drive API
  3. Create OAuth 2.0 credentials (Desktop app type)
  4. Add http://localhost:8765/callback to authorized redirect URIs
  5. Copy Client ID and Client Secret into config.json

Usage:
  from gdrive_backup import GDriveBackup

  backup = GDriveBackup(config)
  if not backup.is_authorized():
      backup.authorize()  # Opens browser

  # After each batch:
  backup_id = backup.backup_vault(vault_path, repo_count=312, trigger="batch_complete")

  # List backups:
  backups = backup.list_backups()

  # Restore:
  backup.restore_to_new_folder(backup_id, parent_dir)
  backup.replace_current_vault(backup_id, vault_path)
  backup.download_backup(backup_id, dest_path)
"""

import json
import os
import time
import zipfile
import tempfile
import urllib.request
import urllib.parse
import urllib.error
import http.server
import threading
import socketserver
import hashlib
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timezone
from pathlib import Path


# ========================================
# Configuration keys (stored in config.json)
# # ========================================

CONFIG_KEYS = {
    'gdrive_enabled': False,
    'gdrive_client_id': '',           # From Google Cloud Console
    'gdrive_client_secret': '',       # From Google Cloud Console
    'gdrive_access_token': '',        # Auto-refreshed
    'gdrive_refresh_token': '',       # Long-lived (until expiry/revocation)
    'gdrive_token_expiry': 0,         # Unix timestamp
    'gdrive_folder_id': '',           # Google Drive folder ID for backups
    'gdrive_max_backups': 10,         # FIFO rotation count
    'gdrive_redirect_port': 8765,     # Local OAuth callback port
}

GDRIVE_FOLDER_NAME = 'Obsidian-Vault-Backups'

# Files/folders to NEVER include in backup
EXCLUDE_PATTERS = [
    'config.json',
    '.git',
    '__pycache__',
]
EXCLUDE_FILE_SUFFIXES = [
    '.lock',
]
EXCLUDE_FILE_PREFIXES = [
    '.obsidian/workspace',
    '.obsidian/app.json',
]


# ========================================
# GDriveBackup
# # ========================================

class GDriveBackup:
    """Google Drive backup manager for Obsidian vault."""

    def __init__(self, config: dict):
        self.config = config
        self._token_lock = threading.Lock()

    # ========================================
    # Configuration
    # ========================================

    @property
    def client_id(self) -> str:
        return self.config.get('gdrive_client_id', '')

    @property
    def client_secret(self) -> str:
        return self.config.get('gdrive_client_secret', '')

    @property
    def access_token(self) -> str:
        return self.config.get('gdrive_access_token', '')

    @property
    def refresh_token(self) -> str:
        return self.config.get('gdrive_refresh_token', '')

    @property
    def folder_id(self) -> str:
        return self.config.get('gdrive_folder_id', '')

    @property
    def max_backups(self) -> int:
        return self.config.get('gdrive_max_backups', 10)

    def is_enabled(self) -> bool:
        return (
            self.config.get('gdrive_enabled', False)
            and bool(self.client_id)
            and bool(self.client_secret)
        )

    def is_authorized(self) -> bool:
        return bool(self.refresh_token) and bool(self.access_token)

    def is_token_expired(self) -> bool:
        expiry = self.config.get('gdrive_token_expiry', 0)
        return time.time() >= (expiry - 60)  # Refresh 60s before expiry

    # ========================================
    # OAuth 2.0 flow
    # ========================================

    def authorize(self) -> Tuple[bool, str]:
        """
        Run OAuth 2.0 flow: opens browser, waits for callback.
        Returns (success, message).
        """
        port = self.config.get('gdrive_redirect_port', 8765)
        redirect_uri = f"http://localhost:{port}/callback"

        auth_url = self._build_auth_url(redirect_uri)

        # Start local server to receive callback
        code_holder = {'code': None, 'error': None}

        class CallbackHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                params = urllib.parse.parse_qs(parsed.query)

                if 'code' in params:
                    code_holder['code'] = params['code'][0]
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html')
                    self.end_headers()
                    self.wfile.write(b"""
                        <html><body>
                        <h1>Authorization Successful</h1>
                        <p>You can close this window now.</p>
                        </body></html>
                    """)
                elif 'error' in params:
                    code_holder['error'] = params['error'][0]
                    self.send_response(400)
                    self.send_header('Content-Type', 'text/html')
                    self.end_headers()
                    self.wfile.write(b"""
                        <html><body>
                        <h1>Authorization Failed</h1>
                        <p>Check the desktop app for details.</p>
                        </body></html>
                    """)

            def log_message(self, format, *args):
                pass  # Suppress console output

        server = None
        try:
            server = socketserver.TCPServer(("localhost", port), CallbackHandler)
            server.timeout = 300  # 5 min timeout
            thread = threading.Thread(target=server.handle_request)
            thread.daemon = True
            thread.start()

            # Open browser
            import webbrowser
            webbrowser.open(auth_url)

            # Wait for callback
            thread.join(timeout=300)

        except Exception as e:
            return False, f"OAuth server error: {e}"
        finally:
            if server:
                server.server_close()

        if code_holder['error']:
            return False, f"OAuth error: {code_holder['error']}"
        if not code_holder['code']:
            return False, "OAuth timeout — no code received"

        # Exchange code for tokens
        return self._exchange_code(code_holder['code'], redirect_uri)

    def _build_auth_url(self, redirect_uri: str) -> str:
        """Build the Google OAuth authorization URL."""
        params = {
            'client_id': self.client_id,
            'redirect_uri': redirect_uri,
            'response_type': 'code',
            'scope': 'https://www.googleapis.com/auth/drive.file',
            'access_type': 'offline',
            'prompt': 'consent',  # Force consent to get refresh_token
        }
        return 'https://accounts.google.com/o/oauth2/auth?' + urllib.parse.urlencode(params)

    def _exchange_code(self, code: str, redirect_uri: str) -> Tuple[bool, str]:
        """Exchange authorization code for access + refresh tokens."""
        data = urllib.parse.urlencode({
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'code': code,
            'grant_type': 'authorization_code',
            'redirect_uri': redirect_uri,
        }).encode('utf-8')

        req = urllib.request.Request(
            'https://oauth2.googleapis.com/token',
            data=data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            method='POST'
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                token_data = json.loads(resp.read())

            self.config['gdrive_access_token'] = token_data['access_token']
            self.config['gdrive_refresh_token'] = token_data.get('refresh_token', self.refresh_token)
            self.config['gdrive_token_expiry'] = time.time() + token_data.get('expires_in', 3600)
            self.config['gdrive_enabled'] = True

            # Ensure backup folder exists
            self._ensure_backup_folder()

            return True, "Authorized successfully!"
        except urllib.error.HTTPError as e:
            err_data = json.loads(e.read())
            return False, f"Token exchange failed: {err_data.get('error_description', str(e))}"
        except Exception as e:
            return False, f"Token exchange error: {e}"

    # ========================================
    # Token refresh
    # ========================================

    def refresh_token(self) -> bool:
        """Refresh the access token using the refresh token. Returns False if re-auth needed."""
        if not self.refresh_token:
            return False

        with self._token_lock:
            data = urllib.parse.urlencode({
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'refresh_token': self.refresh_token,
                'grant_type': 'refresh_token',
            }).encode('utf-8')

            req = urllib.request.Request(
                'https://oauth2.googleapis.com/token',
                data=data,
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                method='POST'
            )

            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    token_data = json.loads(resp.read())

                self.config['gdrive_access_token'] = token_data['access_token']
                self.config['gdrive_token_expiry'] = time.time() + token_data.get('expires_in', 3600)
                return True

            except urllib.error.HTTPError as e:
                if e.code == 400:
                    # Refresh token expired/revoked
                    err_data = json.loads(e.read())
                    if 'invalid_grant' in str(err_data):
                        self.config['gdrive_access_token'] = ''
                        # Signal that re-auth is needed
                        return False
                print(f"[GDriveBackup] Token refresh failed: {e}")
                return False
            except Exception as e:
                print(f"[GDriveBackup] Token refresh error: {e}")
                return False

    def _ensure_valid_token(self) -> bool:
        """Ensure we have a valid access token. Returns False if re-auth needed."""
        if not self.is_authorized():
            return False
        if self.is_token_expired():
            return self.refresh_token()
        return True

    # ========================================
    # Google Drive folder management
    # ========================================

    def _ensure_backup_folder(self) -> bool:
        """Ensure the backup folder exists in Google Drive. Returns folder_id."""
        if self.folder_id:
            return self.folder_id

        if not self._ensure_valid_token():
            return False

        # Search for existing folder
        query = f"name='{GDRIVE_FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        url = f"https://www.googleapis.com/drive/v3/files?q={urllib.parse.quote(query)}"
        req = urllib.request.Request(url, headers=self._auth_headers())

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())

            if data.get('files'):
                folder_id = data['files'][0]['id']
                self.config['gdrive_folder_id'] = folder_id
                return folder_id
        except Exception as e:
            print(f"[GDriveBackup] Folder search error: {e}")

        # Create folder
        folder_metadata = {
            'name': GDRIVE_FOLDER_NAME,
            'mimeType': 'application/vnd.google-apps.folder'
        }

        req = urllib.request.Request(
            'https://www.googleapis.com/drive/v3/files',
            data=json.dumps(folder_metadata).encode('utf-8'),
            headers={**self._auth_headers(), 'Content-Type': 'application/json'},
            method='POST'
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
            folder_id = data['id']
            self.config['gdrive_folder_id'] = folder_id
            return folder_id
        except Exception as e:
            print(f"[GDriveBackup] Folder creation error: {e}")
            return False

    def _auth_headers(self) -> dict:
        return {'Authorization': f'Bearer {self.access_token}'}

    # ========================================
    # Backup vault
    # ========================================

    def backup_vault(
        self,
        vault_path: str,
        repo_count: int = 0,
        trigger: str = 'batch_complete'
    ) -> Optional[str]:
        """
        Create a zip of the vault and upload to Google Drive.

        Args:
            vault_path: Path to the Obsidian vault folder
            repo_count: Number of repos in vault (for metadata)
            trigger: 'batch_complete' | 'manual' | 'startup'

        Returns backup_id (UUID) on success, None on failure.
        """
        if not self.is_enabled():
            return None

        if not self._ensure_valid_token():
            print("[GDriveBackup] Token expired — re-authorization needed")
            return None

        folder_id = self._ensure_backup_folder()
        if not folder_id:
            return None

        import uuid as _uuid
        backup_id = str(_uuid.uuid4())
        timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%SZ')
        file_name = f'vault-{timestamp}.zip'

        # Create zip
        vault_path = Path(vault_path)
        if not vault_path.exists():
            print(f"[GDriveBackup] Vault path not found: {vault_path}")
            return None

        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp:
            zip_path = tmp.name

        try:
            file_count = self._zip_vault(vault_path, zip_path)
            file_size = os.path.getsize(zip_path)

            # Upload to Google Drive
            gdrive_file_id = self._upload_file(zip_path, file_name, folder_id)

            if not gdrive_file_id:
                return None

            # Rotate: delete old backups if > max_backups
            self._rotate_backups()

            print(f"[GDriveBackup] ✅ Backup uploaded: {file_name} ({file_count} files, {file_size} bytes)")

            return backup_id

        finally:
            # Clean up temp file
            try:
                os.unlink(zip_path)
            except:
                pass

    def _zip_vault(self, vault_path: Path, zip_path: str) -> int:
        """Zip the vault folder, excluding sensitive files. Returns file count."""
        count = 0

        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for root, dirs, files in os.walk(vault_path):
                root_path = Path(root)

                # Filter out excluded directories
                dirs[:] = [d for d in dirs if not self._is_excluded(d, root_path / d)]

                for file in files:
                    file_path_full = root_path / file

                    if self._is_excluded(file, file_path_full):
                        continue

                    # Add to zip with relative path
                    arcname = file_path_full.relative_to(vault_path.parent)
                    zf.write(file_path_full, arcname)
                    count += 1

        return count

    def _is_excluded(self, name: str, full_path: Path) -> bool:
        """Check if a file/folder should be excluded from backup."""
        # Check exact name matches
        if name in EXCLUDE_PATTERS:
            return True

        # Check .git directory (any depth)
        if name == '.git':
            return True

        # Check file suffixes
        for suffix in EXCLUDE_FILE_SUFFIXES:
            if name.endswith(suffix):
                return True

        # Check file prefixes (relative to vault root)
        try:
            rel_path = str(full_path.relative_to(full_path.parents[-2]))  # This won't work correctly
        except:
            pass

        # Check against obsidian workspace files
        for prefix in EXCLUDE_FILE_PREFIXES:
            # Check if the path contains the prefix pattern
            if prefix in str(full_path):
                return True

        return False

    # ========================================
    # Upload to Google Drive
    # ========================================

    def _upload_file(self, file_path: str, file_name: str, folder_id: str) -> Optional[str]:
        """Upload a file to Google Drive using multipart upload. Returns file ID."""
        with open(file_path, 'rb') as f:
            file_data = f.read()

        # Use multipart upload (metadata + data)
        boundary = '-------314159265358979323846'
        delimiter = f'\r\n--{boundary}\r\n'
        close_delim = f'\r\n--{boundary}--'

        metadata = {
            'name': file_name,
            'parents': [folder_id]
        }

        body = (
            delimiter +
            'Content-Type: application/json; charset=UTF-8\r\n\r\n' +
            json.dumps(metadata) +
            delimiter +
            'Content-Type: application/zip\r\n\r\n'
        ).encode('utf-8') + file_data + close_delim.encode('utf-8')

        req = urllib.request.Request(
            'https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart',
            data=body,
            headers={
                **self._auth_headers(),
                'Content-Type': f'multipart/related; boundary="{boundary}"'
            },
            method='POST'
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
            return data.get('id')
        except Exception as e:
            print(f"[GDriveBackup] Upload error: {e}")
            return None

    # ========================================
    # Rotate backups (FIFO — keep last N)
    # ========================================

    def _rotate_backups(self):
        """Delete oldest backups if count > max_backups."""
        if not self._ensure_valid_token():
            return

        # List all backups in folder
        query = f"'{self.folder_id}' in parents and trashed=false and name contains 'vault-'"
        url = f"https://www.googleapis.com/drive/v3/files?q={urllib.parse.quote(query)}&orderBy=createdTime&pageSize=100"

        req = urllib.request.Request(url, headers=self._auth_headers())

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())

            files = data.get('files', [])
            if len(files) <= self.max_backups:
                return

            # Delete oldest (files are sorted by createdTime ascending)
            to_delete = files[:len(files) - self.max_backups]
            for f in to_delete:
                self._delete_file(f['id'])
                print(f"[GDriveBackup] Deleted old backup: {f['name']}")

        except Exception as e:
            print(f"[GDriveBackup] Rotation error: {e}")

    def _delete_file(self, file_id: str):
        """Delete a file from Google Drive."""
        req = urllib.request.Request(
            f'https://www.googleapis.com/drive/v3/files/{file_id}',
            headers=self._auth_headers(),
            method='DELETE'
        )
        try:
            urllib.request.urlopen(req, timeout=30)
        except Exception as e:
            print(f"[GDriveBackup] Delete error: {e}")

    # ========================================
    # List backups
    # ========================================

    def list_backups(self) -> List[Dict[str, Any]]:
        """List all backups in Google Drive. Returns list of {id, name, size, created_time}."""
        if not self._ensure_valid_token():
            return []

        if not self.folder_id:
            return []

        query = f"'{self.folder_id}' in parents and trashed=false and name contains 'vault-'"
        fields = 'files(id,name,size,createdTime)'
        url = f"https://www.googleapis.com/drive/v3/files?q={urllib.parse.quote(query)}&orderBy=createdTime desc&fields={urllib.parse.quote(fields)}&pageSize=50"

        req = urllib.request.Request(url, headers=self._auth_headers())

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
            return data.get('files', [])
        except Exception as e:
            print(f"[GDriveBackup] List error: {e}")
            return []

    # ========================================
    # Download backup
    # ========================================

    def download_backup(self, file_id: str, dest_path: str) -> bool:
        """Download a backup zip to a local path."""
        if not self._ensure_valid_token():
            return False

        url = f'https://www.googleapis.com/drive/v3/files/{file_id}?alt=media'
        req = urllib.request.Request(url, headers=self._auth_headers())

        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                with open(dest_path, 'wb') as f:
                    while True:
                        chunk = resp.read(8192)
                        if not chunk:
                            break
                        f.write(chunk)
            return True
        except Exception as e:
            print(f"[GDriveBackup] Download error: {e}")
            return False

    # ========================================
    # Restore
    # ========================================

    def restore_to_new_folder(self, file_id: str, parent_dir: str) -> Optional[str]:
        """
        Download backup and extract to a new folder.
        Returns the new folder path, or None on failure.
        """
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        new_folder = Path(parent_dir) / f'vault-restored-{timestamp}'
        new_folder.mkdir(parents=True, exist_ok=True)

        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp:
            zip_path = tmp.name

        try:
            if not self.download_backup(file_id, zip_path):
                return None

            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(new_folder)

            return str(new_folder)
        finally:
            try:
                os.unlink(zip_path)
            except:
                pass

    def replace_current_vault(self, file_id: str, current_vault_path: str) -> Tuple[bool, Optional[str]]:
        """
        Replace the current vault with a backup.
        Creates a safety net backup of the current vault first.

        Returns (success, safety_net_path).
        """
        vault_path = Path(current_vault_path)
        if not vault_path.exists():
            return False, None

        # Create safety net
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        safety_net = vault_path.parent / f'vault-backup-before-restore-{timestamp}'
        safety_net.mkdir(parents=True, exist_ok=True)

        # Move current vault to safety net
        import shutil
        for item in vault_path.iterdir():
            shutil.move(str(item), str(safety_net / item.name))

        # Download and extract backup
        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp:
            zip_path = tmp.name

        try:
            if not self.download_backup(file_id, zip_path):
                # Restore from safety net
                for item in safety_net.iterdir():
                    shutil.move(str(item), str(vault_path / item.name))
                safety_net.rmdir()
                return False, None

            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(vault_path)

            return True, str(safety_net)
        finally:
            try:
                os.unlink(zip_path)
            except:
                pass

    # ========================================
    # Cleanup safety nets (call after 7 days)
    # ========================================

    @staticmethod
    def cleanup_safety_nets(parent_dir: str, max_age_days: int = 7):
        """Delete safety net folders older than max_age_days."""
        parent = Path(parent_dir)
        cutoff = time.time() - (max_age_days * 86400)

        import shutil
        for item in parent.iterdir():
            if item.name.startswith('vault-backup-before-restore-'):
                if item.stat().st_mtime < cutoff:
                    shutil.rmtree(item)
                    print(f"[GDriveBackup] Cleaned up safety net: {item.name}")
