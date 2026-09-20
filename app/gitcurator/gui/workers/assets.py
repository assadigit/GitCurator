#!/usr/bin/env python3
"""gitcurator.gui.workers.assets — the banner-asset mixin.

GitHub opengraph banner download with the v25 429-throttle (verbatim
method of the original ProcessingWorker).

"""

import os, re

from gitcurator.gui._qt import *  # noqa: F401,F403
from gitcurator.gui.workers._deps import *  # noqa: F401,F403
__all__ = ["BannerAssetMixin"]


class BannerAssetMixin:
    """BannerAssetMixin — see module docstring (methods are verbatim moves)."""


    def _download_banner(self, owner, repo_name, folder_path):
        """Download the GitHub social preview banner for a repo.
        Stores banners in a central 'attachments/banners' folder in the vault
        root. Uses retry with backoff for HTTP 429 (rate limit) and caches
        failed downloads to avoid re-trying known failures.
        Returns the local file path if successful, None otherwise."""
        import urllib.request
        import ssl
        import time as _time

        # v25 pre-flight: throttle banner downloads for large batches.
        # opengraph.githubassets.com returns 429 aggressively when we hammer
        # it 200+ times in quick succession. Every 10 banners we pause 2s;
        # every 50 banners we pause 5s. These are best-effort — if we're
        # already rate-limited, the existing 429 backoff handles it.
        try:
            self._banner_count += 1
            if self._banner_count % 50 == 0:
                _time.sleep(self.config.get('banner_throttle_50', 5))
            elif self._banner_count % 10 == 0:
                _time.sleep(self.config.get('banner_throttle_10', 2))
        except Exception:
            pass  # throttle is best-effort — never block on it

        vault_root = self.config.get('vault_path', '')
        if not vault_root:
            vault_root = os.path.dirname(folder_path)
        banners_dir = os.path.join(vault_root, "attachments", "banners")
        os.makedirs(banners_dir, exist_ok=True)

        url = f"https://opengraph.githubassets.com/1/{owner}/{repo_name}"
        safe_name = re.sub(r'[^a-zA-Z0-9\-_]+', '_', repo_name)
        banner_filename = f"{safe_name}_banner.png"
        banner_path = os.path.join(banners_dir, banner_filename)

        # Skip if already downloaded
        if os.path.exists(banner_path):
            return banner_path

        # Check if this repo previously failed (cache file marker)
        failed_marker = os.path.join(banners_dir, f"{safe_name}_failed.marker")
        if os.path.exists(failed_marker):
            # Don't re-try known failures (marker auto-expires after 24h)
            marker_age = _time.time() - os.path.getmtime(failed_marker)
            if marker_age < 86400:  # 24 hours
                return None
            else:
                try:
                    os.remove(failed_marker)
                except OSError:
                    pass

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        # Retry with backoff for rate limiting (429) and transient errors
        max_retries = 3
        for attempt in range(max_retries):
            try:
                req = urllib.request.Request(url, headers={
                    'User-Agent': 'Mozilla/5.0',
                    'Accept': 'image/png,image/*',
                })
                with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
                    content_type = resp.headers.get('Content-Type', '')
                    if 'image' in content_type:
                        data = resp.read()
                        if len(data) > 1000:
                            # v30 — Fix (atomic writes): banner written via
                            # tempfile + os.replace — a crash mid-write can
                            # no longer leave a truncated .png that the
                            # "already downloaded" check would then treat as
                            # complete forever.
                            _storage.atomic_write_bytes(banner_path, data)
                            return banner_path
                return None
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    # Rate limited — wait and retry
                    if attempt < max_retries - 1:
                        wait = 5 * (attempt + 1)  # 5s, 10s, 15s
                        self.log_message.emit(
                            f"   ⏳ Banner rate-limited (429), waiting {wait}s before retry {attempt + 2}/{max_retries}...",
                            "warning"
                        )
                        _time.sleep(wait)
                        continue
                    else:
                        # Mark as failed to avoid re-trying
                        try:
                            with open(failed_marker, 'w') as f:
                                f.write(str(_time.time()))
                        except Exception:
                            pass
                        self.log_message.emit(
                            f"   ⚠️ Banner rate-limited after {max_retries} attempts. Will skip for 24h.",
                            "warning"
                        )
                        return None
                elif e.code == 404:
                    # No banner for this repo — mark as failed (permanent)
                    try:
                        with open(failed_marker, 'w') as f:
                            f.write("404")
                    except Exception:
                        pass
                    return None
                else:
                    if attempt < max_retries - 1:
                        _time.sleep(2)
                        continue
                    return None
            except Exception:
                if attempt < max_retries - 1:
                    _time.sleep(2)
                    continue
                return None

        return None
