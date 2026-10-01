"""
backfill_manager.py — One-time cutover migration from Telegram to Worker
=========================================================================
Reads all links from the Telegram bot queue (via existing Telethon worker),
pushes them to the Cloudflare Worker in chunks (idempotent), verifies,
then switches the desktop to polling the Worker instead of Telegram.

Flow:
  1. Read all bot-queue messages (via existing telegram_fetch_worker.py)
  2. Extract GitHub URLs + first-seen timestamps
  3. Push to Worker in chunks of 50 (idempotent — restartable)
  4. Verify: Worker ledger count == local bot-queue count
  5. Mark cutover complete

Usage:
  from backfill_manager import BackfillManager

  manager = BackfillManager(cloudflare_sync, error_reporter)
  success, result = manager.run(all_links, chunk_size=50, progress_callback=on_progress)
"""

import os
import sys
import time
import logging
from typing import List, Dict, Any, Tuple, Optional, Callable

# v30 — Fix (Standardize link parsing): single source of truth in links.py.
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)
# v32 — package bootstrap: this script runs as a subprocess (or
# directly), so put the app/ root back on sys.path for `gitcurator.*`.
import os as _os, sys as _sys
_PKG_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _PKG_ROOT not in _sys.path:
    _sys.path.insert(0, _PKG_ROOT)
from gitcurator.core import links as _links

from gitcurator.cloud.cloudflare_sync import CloudflareSync
from gitcurator.integrations.error_reporter import ErrorReporter, SEVERITY_CRITICAL, SEVERITY_WARNING, SEVERITY_INFO

logger = logging.getLogger(__name__)


# ========================================
# Constants
# ========================================

DEFAULT_CHUNK_SIZE = 50
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds


# ========================================
# BackfillManager
# ========================================

class BackfillManager:
    """Handles the one-time cutover from Telegram polling to Worker polling."""

    def __init__(self, sync: CloudflareSync, error_reporter: ErrorReporter):
        self.sync = sync
        self.error_reporter = error_reporter
        self._cancelled = False

    def cancel(self):
        """Cancel the backfill (user clicked Cancel)."""
        self._cancelled = True

    # ========================================
    # Main backfill flow
    # ========================================

    def run(
        self,
        all_links: List[Dict[str, Any]],
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> Tuple[bool, Dict]:
        """
        Run the full backfill flow.

        Args:
            all_links: List of link dicts with keys:
                - url_normalized (str)
                - url_original (str)
                - url_type (str) — 'github' | 'non_github'
                - github_owner (str, optional)
                - github_repo (str, optional)
                - first_seen_at (str, ISO timestamp)
            chunk_size: Number of entries per chunk (default 50)
            progress_callback: Called with (current, total, message) for GUI updates

        Returns (success, result_dict):
            result_dict = {
                'total': int,
                'inserted': int,
                'skipped': int,
                'chunks': int,
                'duration_seconds': float,
                'verified': bool,
                'error': str (if failed)
            }
        """
        self._cancelled = False
        start_time = time.time()

        if not self.sync.is_enabled():
            return False, {'error': 'Cloudflare sync not enabled — pair first'}

        total = len(all_links)
        if total == 0:
            logger.info("Backfill: no links to migrate")
            return True, {
                'total': 0, 'inserted': 0, 'skipped': 0,
                'chunks': 0, 'duration_seconds': 0,
                'verified': True
            }

        # Deduplicate by url_normalized
        seen_urls = set()
        unique_links = []
        for link in all_links:
            url = link.get('url_normalized', '')
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique_links.append(link)

        total_unique = len(unique_links)
        logger.info(f"Backfill: {total} links ({total_unique} unique) in chunks of {chunk_size}")

        if progress_callback:
            progress_callback(0, total_unique, f"Starting backfill: {total_unique} links...")

        # Push in chunks
        total_inserted = 0
        total_skipped = 0
        chunks = (total_unique + chunk_size - 1) // chunk_size

        for chunk_idx in range(chunks):
            if self._cancelled:
                logger.info("Backfill cancelled by user")
                return False, {
                    'error': 'Cancelled by user',
                    'total': total_unique,
                    'inserted': total_inserted,
                    'skipped': total_skipped,
                    'chunks': chunk_idx,
                    'duration_seconds': time.time() - start_time,
                    'verified': False
                }

            chunk_start = chunk_idx * chunk_size
            chunk_end = min(chunk_start + chunk_size, total_unique)
            chunk = unique_links[chunk_start:chunk_end]

            if progress_callback:
                progress_callback(
                    chunk_start,
                    total_unique,
                    f"Pushing chunk {chunk_idx + 1}/{chunks} ({len(chunk)} links)..."
                )

            # Retry logic
            success = False
            last_error = ''
            for attempt in range(MAX_RETRIES):
                try:
                    ok, response = self.sync.backfill_chunk(
                        entries=chunk,
                        chunk_num=chunk_idx,
                        total_chunks=chunks
                    )
                    if ok:
                        total_inserted += response.get('inserted', 0)
                        total_skipped += response.get('skipped', 0)
                        success = True
                        break
                    else:
                        last_error = response.get('error', 'unknown')
                        logger.warning(f"Chunk {chunk_idx} attempt {attempt + 1} failed: {last_error}")
                except Exception as e:
                    last_error = str(e)
                    logger.warning(f"Chunk {chunk_idx} attempt {attempt + 1} exception: {e}")

                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (attempt + 1))

            if not success:
                error_msg = f"Chunk {chunk_idx} failed after {MAX_RETRIES} attempts: {last_error}"
                logger.error(error_msg)
                self.error_reporter.log(
                    SEVERITY_CRITICAL,
                    'BACKFILL_CHUNK_FAILED',
                    error_msg,
                    {'chunk': chunk_idx, 'error': last_error}
                )
                return False, {
                    'error': error_msg,
                    'total': total_unique,
                    'inserted': total_inserted,
                    'skipped': total_skipped,
                    'chunks': chunk_idx,
                    'duration_seconds': time.time() - start_time,
                    'verified': False
                }

            # Small delay between chunks to avoid rate limits
            time.sleep(0.5)

        if progress_callback:
            progress_callback(total_unique, total_unique, "Verifying backfill...")

        # Verify
        verified = self._verify(total_unique)

        duration = time.time() - start_time

        result = {
            'total': total_unique,
            'inserted': total_inserted,
            'skipped': total_skipped,
            'chunks': chunks,
            'duration_seconds': round(duration, 2),
            'verified': verified
        }

        if verified:
            logger.info(f"Backfill complete: {total_inserted} inserted, {total_skipped} skipped in {duration:.1f}s")
            self.error_reporter.log(
                SEVERITY_INFO,
                'BACKFILL_COMPLETE',
                f"Backfill complete: {total_inserted} links migrated in {duration:.1f}s",
                result
            )
            if progress_callback:
                progress_callback(total_unique, total_unique, f"✅ Backfill complete: {total_inserted} links migrated")
        else:
            logger.warning("Backfill verification failed — counts don't match")
            self.error_reporter.log(
                SEVERITY_WARNING,
                'BACKFILL_VERIFY_MISMATCH',
                f"Backfill verification failed: local={total_unique}, worker mismatch",
                result
            )

        return verified, result

    # ========================================
    # Verification
    # ========================================

    def _verify(self, expected_count: int) -> bool:
        """Verify that the Worker has the expected number of ledger entries."""
        try:
            verify_data = self.sync.verify()
            if not verify_data:
                return False

            worker_total = verify_data.get('stats', {}).get('total_ledger', 0)

            # Allow for some pre-existing entries (if backfill was partially run before)
            # We verify that worker_total >= expected_count
            if worker_total >= expected_count:
                logger.info(f"Verification passed: Worker has {worker_total} entries (expected >= {expected_count})")
                return True
            else:
                logger.warning(f"Verification failed: Worker has {worker_total}, expected >= {expected_count}")
                return False

        except Exception as e:
            logger.error(f"Verification error: {e}")
            return False

    # ========================================
    # Helper: extract links from bot queue
    # ========================================

    @staticmethod
    def extract_links_from_bot_messages(messages: List[Dict]) -> List[Dict]:
        """
        Extract GitHub URLs from Telegram bot messages.

        Args:
            messages: List of message dicts from telegram_fetch_worker.py
                Each message: {message_id, text, date, ...}

        Returns: List of link dicts ready for backfill.
        """
        import re
        from gitcurator.cloud.cloudflare_sync import CloudflareSync

        # v30 — delegated to links.py (single source of truth)
        github_pattern = _links.GITHUB_URL_PATTERN
        url_pattern = _links.ALL_LINKS_PATTERN

        seen_urls = set()
        links = []

        for msg in messages:
            text = msg.get('text', '')
            if not text:
                continue

            date = msg.get('date')
            if hasattr(date, 'isoformat'):
                first_seen = date.isoformat()
            else:
                first_seen = str(date) if date else None

            # Find all URLs
            urls = url_pattern.findall(text)
            for raw_url in urls:
                # Clean trailing punctuation
                raw_url = raw_url.rstrip('.,);:')

                # Check if GitHub
                github_match = github_pattern.match(raw_url)
                if github_match:
                    url_norm = CloudflareSync.normalize_url(raw_url)
                    if url_norm in seen_urls:
                        continue
                    seen_urls.add(url_norm)

                    links.append({
                        'url_normalized': url_norm,
                        'url_original': raw_url,
                        'url_type': 'github',
                        'github_owner': github_match.group(1),
                        'github_repo': github_match.group(2),
                        'first_seen_at': first_seen
                    })
                else:
                    # Non-GitHub URL — still record for completeness
                    url_norm = CloudflareSync.normalize_url(raw_url)
                    if url_norm in seen_urls:
                        continue
                    seen_urls.add(url_norm)

                    links.append({
                        'url_normalized': url_norm,
                        'url_original': raw_url,
                        'url_type': 'non_github',
                        'github_owner': None,
                        'github_repo': None,
                        'first_seen_at': first_seen
                    })

        return links

    # ========================================
    # Helper: check if cutover already done
    # ========================================

    def is_cutover_complete(self) -> bool:
        """Check if the cutover has already been completed."""
        try:
            verify_data = self.sync.verify()
            if not verify_data:
                return False
            return verify_data.get('cutover_complete', False)
        except Exception:
            return False
