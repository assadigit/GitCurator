#!/usr/bin/env python3
"""gitcurator.gui.workers.fetch — the source-fetch mixin.

Telegram fetch, import-file fetch and org reputation (verbatim methods
of the original ProcessingWorker).

"""

import os

from gitcurator.gui._qt import *  # noqa: F401,F403
from gitcurator.gui.workers._deps import *  # noqa: F401,F403
__all__ = ["SourceFetchMixin"]


class SourceFetchMixin:
    """SourceFetchMixin — see module docstring (methods are verbatim moves)."""


    def _fetch_from_telegram(self):
        # Back up session file before use
        try:
            import shutil
            # v32.3 fix: anchor to APP_DIR — the fetch subprocess runs with
            # cwd=APP_DIR, so the session lives there; the backup copy used
            # to look in the GUI process's CWD instead.
            session_path = resolve_app_path('session.session')
            if os.path.exists(session_path):
                backup_path = session_path + '.bak'
                shutil.copy2(session_path, backup_path)
        except Exception:
            pass  # backup is best-effort

        api_id = self.config.get('telegram_api_id', 0)
        api_hash = self.config.get('telegram_api_hash', '')
        phone = self.config.get('telegram_phone', '')
        proxy = self.config.get('proxy', {})

        if not api_id or not api_hash or not phone:
            self.log_message.emit("Telegram credentials missing.", "error")
            return []

        try:
            # Build config for the subprocess worker (same as test buttons)
            config = {
                'api_id': int(api_id),
                'api_hash': api_hash,
                'phone': phone,
                'proxy': proxy,
                'session_file': 'session',
                'preview_only': False,
            }
            if self.mode == 'telegram_ids':
                from_id = self.range_from
                to_id = self.range_to
                if from_id is None or to_id is None:
                    self.log_message.emit("Invalid message ID range.", "error")
                    return []
                config['from_id'] = int(from_id)
                config['to_id'] = int(to_id)
            else:
                offset_start = self.offset_start
                offset_count = self.offset_count
                if offset_start is None or offset_count is None:
                    self.log_message.emit("Invalid offset parameters.", "error")
                    return []
                config['offset_start'] = int(offset_start)
                config['count'] = int(offset_count)

            # Run the subprocess worker (identical to test.py execution context)
            self.log_message.emit("Starting Telegram fetch via subprocess...", "info")
            result = _run_telegram_worker(config, self.log_message, code_callback=self.request_code)

            if result.get('success'):
                urls = result.get('urls', [])
                non_github = result.get('non_github_urls', [])
                self.log_message.emit(
                    f"📥 Fetched {len(urls)} GitHub URLs + {len(non_github)} non-GitHub links "
                    f"from {result.get('total_messages', 0)} messages.",
                    "info"
                )
                # Add non-GitHub links to the review table
                if non_github:
                    self._create_inbox_notes(non_github, source="Saved")
                # Store non-GitHub links for the summary report
                self._non_github_urls = non_github
                return urls
            else:
                error = result.get('error', 'Unknown error')
                self.log_message.emit(f"Telegram fetch failed: {error}", "error")
                return []

        except Exception as e:
            self.log_message.emit(f"Exception in Telegram fetch: {e}", "error")
            return []

    def _fetch_from_import(self):
        urls = []
        if not self.import_file or not os.path.exists(self.import_file):
            self.log_message.emit("Import file not found.", "error")
            return []
        with open(self.import_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                urls.append(line)

        # v23 — No Link Left Behind: split the imported URLs into GitHub
        # and non-GitHub lists. Non-GitHub URLs are recorded in the inbox
        # table so they are never silently dropped. The processing loop
        # only sees GitHub URLs.
        github_urls = []
        non_github_urls = []
        for u in urls:
            try:
                cleaned = clean_url(u)
            except Exception:
                cleaned = u
            if cleaned.startswith("https://github.com/"):
                github_urls.append(u)
            else:
                non_github_urls.append(u)

        if non_github_urls:
            try:
                self._create_inbox_notes(non_github_urls, source="Import")
            except Exception as e:
                self.log_message.emit(f"⚠️ Failed to record non-GitHub links from import: {e}", "warning")
            # Store for the manifest intake in run()
            self._non_github_urls = non_github_urls

        self.log_message.emit(
            f"📄 Loaded {len(github_urls)} GitHub URLs + {len(non_github_urls)} non-GitHub URLs from import file.",
            "info"
        )
        return github_urls

    def _get_org_reputation(self, org):
        major = [
            "microsoft", "google", "nvidia", "anthropic", "openai",
            "meta", "amazon", "apple", "ibm", "intel",
            "cloudflare", "aws", "azure", "googlecloud", "gcp",
            "netflix", "uber", "airbnb", "spotify", "twitter", "facebook",
            "github", "gitlab", "docker", "kubernetes", "linux", "redhat"
        ]
        mid = [
            "huggingface", "cohere", "together", "replit", "cursor",
            "vercel", "netlify", "railway", "flyio", "render",
            "supabase", "firebase", "mongodb", "elastic", "datadog"
        ]
        org_lower = org.lower()
        if any(m in org_lower for m in major):
            return 10
        elif any(m in org_lower for m in mid):
            return 7
        elif " " in org and len(org) > 3:
            return 5
        else:
            return 3
