#!/usr/bin/env python3
"""gitcurator.gui.workers.pipeline — the pipeline orchestrator.

The single ``run()`` method (848 lines) drives the whole curation batch:
intake -> fetch -> dedup -> per-URL analysis -> notes -> master index ->
final report -> summary log. Kept as ONE method (splitting it would be a
rewrite, not a refactor); extracted verbatim from ProcessingWorker.

"""

import logging, os, threading, time
from datetime import datetime, timedelta

from gitcurator.gui._qt import *  # noqa: F401,F403
from gitcurator.gui.workers._deps import *  # noqa: F401,F403
__all__ = ["PipelineRunMixin"]


class PipelineRunMixin:
    """PipelineRunMixin — see module docstring (methods are verbatim moves)."""


    def run(self):
        logger = logging.getLogger()

        if self.mode == 'direct':
            urls = self.urls if self.urls else []
        elif self.mode == 'import':
            urls = self._fetch_from_import()
        else:
            urls = self._fetch_from_telegram()

        if not urls:
            self.log_message.emit("No URLs found to process.", "warning")
            self.finished_signal.emit(True, "No URLs found.")
            return

        self.total = len(urls)
        self.processed = 0

        # v23 — Phase 1: INTAKE — record ALL links BEFORE any processing so
        # the manifest is the source of truth and survives app crashes. The
        # manifest is written atomically (temp file + rename) inside _save().
        vault_path = self.config.get('vault_path', '')
        if vault_path:
            try:
                self.link_tracker = LinkTracker(vault_path)
                # Determine source label for the manifest
                if self.mode == 'direct':
                    source = 'bot' if getattr(self, '_bot_source', False) else 'import'
                elif self.mode == 'import':
                    source = 'import'
                else:
                    source = 'telegram'
                self.link_tracker.set_source(source)

                # Get non-GitHub URLs (stored by _fetch_from_telegram or
                # _fetch_from_import, or passed in by the bot-queue flow).
                non_github = getattr(self, '_non_github_urls', []) or []
                self.link_tracker.intake(urls, non_github)

                # Non-GitHub links were already recorded in the inbox table
                # by _fetch_from_telegram/_fetch_from_import (or by the bot
                # queue check). Mark them as "recorded" up front — Phase 3
                # verification will confirm they are actually present in the
                # table.
                for ng_url in non_github:
                    self.link_tracker.mark_recorded(ng_url)

                # Write non-GitHub links to the inbox table
                if non_github:
                    self._create_inbox_notes(non_github, source="Bot" if getattr(self, '_bot_source', False) else "Import")

                self.log_message.emit(
                    f"📋 Manifest created: {len(urls)} GitHub + {len(non_github)} non-GitHub links recorded",
                    "info"
                )
            except Exception as lt_err:
                # Manifest is best-effort — never block the batch if it fails.
                self.log_message.emit(
                    f"⚠️ Link tracker init failed (continuing without manifest): {lt_err}",
                    "warning"
                )
                self.link_tracker = None

        github_token = self.config.get('github_token', None)
        if github_token:
            auth = Auth.Token(github_token)
            g = Github(auth=auth)
        else:
            g = Github()

        ollama_base = self.config.get('ollama', {}).get('base_url', 'http://localhost:11434')
        ollama_model = self.config.get('ollama', {}).get('model', 'qwythos-9b')
        # v26 — Fix 4: pick the LLM provider from config. 'cloud' skips the
        # local-Ollama connection check + warmup and routes _llm_analyze
        # through _call_cloud_llm (OpenAI-compatible HTTP API). Default is
        # 'ollama' so existing users see no behavior change.
        llm_provider = self.config.get('llm_provider', 'ollama')
        ollama_client = None
        if llm_provider == 'ollama':
            ollama_client = ollama.Client(host=ollama_base)

            # v30 — Fix (timeouts on every external call): list() with a
            # 15s wall-clock timeout. A hung/zombie Ollama server used to
            # block this QThread forever (batch frozen at "starting...").
            try:
                _llm_client.call_with_timeout(ollama_client.list, 15)
            except Exception as e:
                self.log_message.emit(f"Ollama is not running: {e}", "error")
                self.finished_signal.emit(False, "Ollama not available")
                return

            # v22 Feature 8: Ollama Model Warmup — send a tiny prompt to pre-load
            # the model into memory. This avoids the long latency spike on the
            # first real analysis call (Ollama lazily loads models on first use).
            # Best-effort — if warmup fails (e.g. model not yet pulled), the
            # batch continues anyway and the LLM will fail per-repo later.
            try:
                self.log_message.emit(f"🔥 Warming up Ollama model '{ollama_model}'...", "info")
                _llm_client.call_with_timeout(
                    ollama_client.chat, 120,
                    model=ollama_model,
                    messages=[{"role": "user", "content": "Hi"}],
                    options={"num_predict": 1}
                )
                self.log_message.emit("✅ Model warmed up", "success")
            except Exception as warmup_err:
                # v30 — Fix (model adaptation): the configured model is gone or
                # broken (user pulled a NEW model and retired the old one).
                # Instead of failing on EVERY repo with a modal, look at what
                # Ollama actually has:
                #   - exactly one model available  -> auto-switch to it, persist
                #   - several models             -> list them, let the first
                #                                   per-repo dialog choice
                #                                   persist for the whole batch
                try:
                    self.log_message.emit(
                        f"⚠️ Model warmup failed: {warmup_err}", "warning"
                    )
                    try:
                        available = _llm_client.list_models_with_timeout(ollama_client, 15)
                    except Exception:
                        available = []
                    if ollama_model not in available and len(available) == 1:
                        new_model = available[0]
                        self.log_message.emit(
                            f"🔄 Configured model '{ollama_model}' not found — Ollama has only "
                            f"'{new_model}'. Auto-switching to it (saved to Settings).",
                            "success"
                        )
                        ollama_model = new_model
                        self._apply_model_choice(new_model)
                    elif available:
                        self.log_message.emit(
                            f"⚠️ Configured model '{ollama_model}' not in Ollama's list. "
                            f"Available: {', '.join(available)}. The first LLM-failure "
                            f"dialog lets you pick one — your choice now applies to the "
                            f"rest of the batch and is saved.",
                            "warning"
                        )
                except Exception:
                    pass
        else:
            # Cloud provider — no warmup, but log the selection so the user
            # sees which backend is being used. The Test Connection button
            # in the GUI is the canonical way to verify creds before a batch.
            cloud_model = self.config.get('cloud_model', 'gpt-4o-mini')
            cloud_url = self.config.get('cloud_api_url', 'https://api.openai.com/v1')
            self.log_message.emit(
                f"☁️ Using Cloud LLM provider: {cloud_url} / model '{cloud_model}'",
                "info"
            )
            # Set ollama_model to the cloud model so _llm_analyze's retry
            # fallback messages reference the right model name.
            ollama_model = cloud_model

        # v30 — Fix (CacheDB leak): created AFTER the Ollama early-return so
        # the "Ollama not available" exit can no longer leak the sqlite handle.
        cache = CacheDB()

        # Build vault index for URL-based dedup (ground truth)
        vault_path = self.config.get('vault_path', '')
        if vault_path:
            self._vault_index = VaultIndex(vault_path)
            # Fix: rebuild() calls log_signal.emit(), so pass the signal directly
            self._vault_index.rebuild(log_signal=self.log_message)

        # v22 Feature 6: Batch Undo — snapshot the vault BEFORE processing so
        # we can compute the list of NEW files written by this batch and let
        # the user undo the batch (delete the new files) from the Dashboard.
        # The snapshot is a list of .md file paths that already exist.
        batch_files = set()
        if vault_path and os.path.isdir(vault_path):
            try:
                for root, dirs, files in os.walk(vault_path):
                    # Skip the same non-note folders the vault index skips
                    if any(skip in root for skip in ['.obsidian', 'attachments']):
                        continue
                    for f in files:
                        if f.endswith('.md'):
                            batch_files.add(os.path.join(root, f))
            except Exception:
                pass  # best-effort — undo just won't work this run

        for url in urls:
            if not self.is_running:
                break
            self._current_position += 1  # increment for EVERY URL (including skips)
            self.status_updated.emit(url)
            self.progress_updated.emit(self._current_position, self.total)

            # v23 — Phase 2: mark as processing (manifest is source of truth)
            if self.link_tracker:
                try:
                    self.link_tracker.mark_processing(url)
                except Exception:
                    pass  # best-effort — never break the batch

            # Check GitHub rate limit (v25 pre-flight: threshold raised from
            # 10 to 50 so we always pause with a safe buffer before hitting
            # the hard limit. The wait uses the *exact* reset time from the
            # GitHub response so we resume the moment the limit clears.)
            try:
                rate_limit = g.get_rate_limit()
                remaining = rate_limit.core.remaining
                if remaining < 50:
                    reset_time = rate_limit.core.reset
                    from datetime import timezone
                    reset_local = reset_time.replace(tzinfo=timezone.utc).astimezone()
                    wait_seconds = (reset_time - datetime.now(timezone.utc)).total_seconds()
                    if wait_seconds > 0 and wait_seconds < 3700:  # less than ~1 hour
                        self.log_message.emit(
                            f"⏳ GitHub rate limit reached. Pausing until {reset_local.strftime('%H:%M')} "
                            f"({wait_seconds/60:.1f} minutes, {remaining} remaining)...",
                            "warning"
                        )
                        # Sleep in small chunks so Stop is responsive
                        end_time = time.time() + wait_seconds + 5
                        while time.time() < end_time and self.is_running:
                            time.sleep(min(5, end_time - time.time()))
                        if not self.is_running:
                            break
                        self.log_message.emit("✅ Rate limit wait complete — resuming.", "success")
                    else:
                        self.log_message.emit(
                            f"⚠️ GitHub rate limit low ({remaining} remaining). Reset at {reset_local.strftime('%H:%M')}. Continuing anyway...",
                            "warning"
                        )
                elif remaining % 50 == 0:
                    self.log_message.emit(f"📊 GitHub API: {remaining} requests remaining", "info")
            except Exception:
                pass  # rate limit check is best-effort

            try:
                url = clean_url(url)
                if not url.startswith("https://github.com/"):
                    self.log_message.emit(f"Skipping non-GitHub URL: {url}", "warning")
                    # v23 — Phase 2: mark as skipped (defensive — should not
                    # happen after _fetch_from_import filtering, but be safe)
                    if self.link_tracker:
                        try:
                            self.link_tracker.mark_skipped(url, "non-GitHub URL")
                        except Exception:
                            pass
                    # v26 — Fix 1: emit progress on skip so the bar repaints
                    # (otherwise it appears frozen on the previous URL's status).
                    self.progress_updated.emit(self._current_position, self.total)
                    continue

                parts = url.replace("https://github.com/", "").split("/")
                if len(parts) < 2:
                    self.log_message.emit(f"Invalid GitHub URL: {url}", "warning")
                    if self.link_tracker:
                        try:
                            self.link_tracker.mark_skipped(url, "invalid GitHub URL")
                        except Exception:
                            pass
                    # v26 — Fix 1: emit progress on skip.
                    self.progress_updated.emit(self._current_position, self.total)
                    continue
                owner, repo_name = parts[0], parts[1]

                try:
                    repo = g.get_repo(f"{owner}/{repo_name}")
                    repo_id = repo.id
                except GithubException as e:
                    # v25 pre-flight: GitHub returns 403 when rate-limited.
                    # We catch it specifically, wait the full hour, then
                    # retry THIS repo (no link lost). Other 403s (e.g.
                    # repo blocked by abuse detection) fall through to the
                    # generic error path so the link is marked failed and
                    # retried later.
                    if e.status == 403 and 'rate limit' in str(e).lower():
                        self.log_message.emit(
                            "⏳ GitHub rate limit reached. Waiting 3600s (1 hour) for reset...",
                            "warning"
                        )
                        # Sleep in small chunks so Stop stays responsive
                        _rl_end = time.time() + 3605
                        while time.time() < _rl_end and self.is_running:
                            time.sleep(min(5, _rl_end - time.time()))
                        if not self.is_running:
                            break
                        try:
                            # Retry this repo after the wait
                            repo = g.get_repo(f"{owner}/{repo_name}")
                            repo_id = repo.id
                            self.log_message.emit(
                                "✅ Rate limit wait complete — resuming with this repo.",
                                "success"
                            )
                        except GithubException as e2:
                            if e2.status == 404:
                                self.log_message.emit(f"🗑️ Repo not found (404 after rate-limit) — decommissioning: {url}", "warning")
                                try:
                                    cache.decommission(url, "404 Not Found")
                                except Exception:
                                    pass
                                if self.link_tracker:
                                    try:
                                        self.link_tracker.mark_skipped(url, "404 Not Found — decommissioned")
                                    except Exception:
                                        pass
                            else:
                                self.log_message.emit(f"GitHub API error after rate-limit wait: {e2}", "error")
                                if self.link_tracker:
                                    try:
                                        self.link_tracker.mark_failed(url, f"GitHub API: {e2}")
                                    except Exception:
                                        pass
                            self.progress_updated.emit(self._current_position, self.total)
                            continue
                    elif e.status == 401 and github_token:
                        # v32.1 — Fix (bad-credentials spam): the saved GitHub
                        # token was rejected (expired / revoked / rotated).
                        # Instead of failing EVERY repo with a raw 401 JSON
                        # blob, drop the token for the rest of the batch
                        # (anonymous access, 60 req/h), retry THIS repo, and
                        # tell the user exactly how to fix it. Logged once.
                        if not getattr(self, '_gh_token_dropped', False):
                            self._gh_token_dropped = True
                            self.log_message.emit(
                                "🔑 GitHub token rejected (401 Bad credentials) — it is invalid, expired, or was rotated.",
                                "error",
                            )
                            self.log_message.emit(
                                "   Continuing this batch anonymously (60 requests/hour). "
                                "Fix: Settings → Credentials → paste a fresh token "
                                "(github.com/settings/tokens) → 'Test GitHub Token'.",
                                "warning",
                            )
                            github_token = None  # never re-enter this branch
                        try:
                            g = Github()  # anonymous client from here on
                            repo = g.get_repo(f"{owner}/{repo_name}")
                            repo_id = repo.id
                        except GithubException as e2:
                            self.log_message.emit(f"GitHub API error: {e2}", "error")
                            if self.link_tracker:
                                try:
                                    self.link_tracker.mark_failed(url, f"GitHub API: {e2}")
                                except Exception:
                                    pass
                            self.progress_updated.emit(self._current_position, self.total)
                            continue
                    elif e.status == 404:
                        self.log_message.emit(f"🗑️ Repo not found (404) — decommissioning: {url}", "warning")
                        # Auto-decommission: permanently skip this URL in future batches
                        try:
                            cache.decommission(url, "404 Not Found")
                        except Exception:
                            pass
                        # Write to _inbox/notfound-links/ for permanent record
                        try:
                            vault_path = self.config.get('vault_path', '')
                            if vault_path:
                                nf_folder = os.path.join(vault_path, "_inbox", "notfound-links")
                                os.makedirs(nf_folder, exist_ok=True)
                                nf_path = os.path.join(nf_folder, "notfound_links.md")
                                with open(nf_path, 'a', encoding='utf-8') as nf:
                                    nf.write(f"| {datetime.now().strftime('%Y-%m-%d')} | {url} | 404 Not Found |\n")
                        except Exception:
                            pass
                        # Mark as skipped (not failed — it's deliberately excluded)
                        if self.link_tracker:
                            try:
                                self.link_tracker.mark_skipped(url, "404 Not Found — decommissioned")
                            except Exception:
                                pass
                        self.progress_updated.emit(self._current_position, self.total)
                        continue
                    else:
                        self.log_message.emit(f"GitHub API error: {e}", "error")
                        # v23 — Phase 2: GitHub-level failure (repo gone / API error)
                        if self.link_tracker:
                            try:
                                self.link_tracker.mark_failed(url, f"GitHub API: {e}")
                            except Exception:
                                pass
                        # v26 — Fix 1: emit progress on skip.
                        self.progress_updated.emit(self._current_position, self.total)
                        continue

                # === DEDUP CHECK (vault index is ground truth) ===
                # 1. Check the vault index FIRST — if the note exists in the
                #    vault, skip (deterministic, no AI).
                # 2. If not in vault but in SQLite cache, the note was deleted
                #    → re-process (self-healing).
                # 3. If not in vault and not in cache → process as new.
                if self._vault_index and self._vault_index.has_url(url):
                    note_path = self._vault_index.get_path(url)
                    self.log_message.emit(
                        f"⏭️ Already in vault: {owner}/{repo_name} → {os.path.basename(note_path)}",
                        "info"
                    )
                    # v23 — Phase 2: mark as skipped (dedup) — note already
                    # exists in the vault from a previous batch.
                    if self.link_tracker:
                        try:
                            self.link_tracker.mark_skipped(url, "already in vault")
                        except Exception:
                            pass
                    # v26 — Fix 1: emit progress on skip so the bar repaints.
                    self.progress_updated.emit(self._current_position, self.total)
                    continue

                if cache.is_duplicate(repo_id):
                    # Check if the note file still exists in the vault.
                    # If it was deleted, remove the stale cache entry and
                    # re-process the repo.
                    if cache.is_note_valid(repo_id):
                        note_path = cache.get_note_path(repo_id)
                        self.log_message.emit(
                            f"⏭️ Already processed (note exists): {url} -> {note_path}",
                            "info"
                        )
                        # Also add to vault index for future runs
                        if self._vault_index:
                            self._vault_index.add_url(url, note_path)
                        # v23 — Phase 2: mark as skipped (dedup) — note
                        # exists from a previous batch.
                        if self.link_tracker:
                            try:
                                self.link_tracker.mark_skipped(url, "already in cache")
                            except Exception:
                                pass
                        # v26 — Fix 1: emit progress on skip.
                        self.progress_updated.emit(self._current_position, self.total)
                        continue
                    else:
                        self.log_message.emit(
                            f"♻️ Note file was deleted, re-processing: {url}",
                            "info"
                        )
                        cache.remove_entry(repo_id)
                        # Fall through to re-process

                # Check if this is a fork — analyze the parent instead
                is_fork = False
                parent_info = ""
                try:
                    if repo.fork:
                        is_fork = True
                        parent = repo.parent
                        parent_info = f"This is a fork of {parent.full_name}. "
                        self.log_message.emit(f"🍴 Fork detected — analyzing parent: {parent.full_name}", "info")
                        # Use the parent repo for analysis
                        repo = parent
                        repo_id = repo.id
                        owner_login = repo.owner.login
                        repo_name = repo.name
                except Exception:
                    pass

                stars = repo.stargazers_count
                forks = repo.forks_count
                description = (repo.description or "") + f"\n\n{parent_info}" if parent_info else (repo.description or "")
                topics = repo.get_topics() if hasattr(repo, 'get_topics') else []
                owner_login = repo.owner.login
                org_name = repo.organization.login if repo.organization else owner_login
                org_rep = self._get_org_reputation(org_name)

                try:
                    commits = repo.get_commits(since=datetime.now() - timedelta(days=90))
                    # Use totalCount instead of iterating (avoids fetching all objects)
                    commit_count = commits.totalCount if hasattr(commits, 'totalCount') else sum(1 for _ in commits)
                except Exception as e:
                    self.log_message.emit(f"   ⚠️ Could not fetch commits: {e}", "warning")
                    commit_count = 0

                # Fetch README content (first 2000 chars) for better LLM context
                readme_content = ""
                try:
                    readme = repo.get_readme()
                    import base64
                    readme_raw = base64.b64decode(readme.content).decode('utf-8', errors='ignore')
                    readme_content = readme_raw[:1500]  # cap at 1500 for faster LLM processing
                    self.log_message.emit(f"   📄 README fetched ({len(readme_raw)} chars)", "info")
                except Exception:
                    self.log_message.emit(f"   ⚠️ No README found", "warning")

                # Start banner download in parallel (thread) while LLM analyzes
                banner_result = [None]
                def _download_banner_thread():
                    vault_path_tmp = self.config.get('vault_path', '')
                    if vault_path_tmp:
                        folder = os.path.join(vault_path_tmp, CATEGORY_FOLDERS.get("Uncategorized", "Uncategorized"))
                        os.makedirs(folder, exist_ok=True)
                        banner_result[0] = self._download_banner(owner_login, repo_name, folder)
                banner_thread = threading.Thread(target=_download_banner_thread, daemon=True)
                banner_thread.start()

                # v30 — Fix (model persistence): re-read the model from
                # self.config on EVERY url. When the user picks a new model
                # in the LLM-failure dialog (or the single-model auto-switch
                # fires), _apply_model_choice mutates this same dict — so the
                # rest of the batch uses the new model instead of re-failing
                # and re-prompting on every single link.
                if llm_provider == 'ollama':
                    ollama_model = self.config.get('ollama', {}).get('model', ollama_model)
                else:
                    ollama_model = self.config.get('cloud_model', ollama_model)

                # LLM analysis with README + about_me context
                llm_result = self._llm_analyze(
                    ollama_client, ollama_model,
                    repo_name, description, topics, owner_login, stars, forks,
                    readme_content=readme_content
                )
                summary = llm_result.get("summary", "No summary available.")
                how_it_works = llm_result.get("how_it_works", "No explanation provided.")
                core_value = llm_result.get("core_value", "No core value provided.")
                features = llm_result.get("features", ["Feature 1", "Feature 2"])
                difference = llm_result.get("difference", "No comparison provided.")
                category_guess = llm_result.get("category", "Uncategorized")
                confidence = float(llm_result.get("confidence", 50))
                tags = llm_result.get("tags", [])

                # Lower threshold to 50% — always pick a category, just flag low-confidence
                if confidence < 50:
                    category_key = "Uncategorized"
                else:
                    category_key = category_guess if category_guess in CATEGORY_KEYS else "Uncategorized"

                # Merge GitHub topics with LLM tags (topics are authoritative)
                all_tags = list(tags)
                for t in topics:
                    if t and t not in all_tags:
                        all_tags.append(t)

                # Fetch primary language + all languages from GitHub API
                primary_language = ""
                languages_list = []
                try:
                    primary_language = repo.language or ""
                    if primary_language and primary_language not in all_tags:
                        all_tags.insert(0, primary_language.lower())
                    # Get all languages used
                    langs = repo.get_languages()
                    for lang_name in list(langs.keys())[:5]:
                        if lang_name not in languages_list:
                            languages_list.append(lang_name)
                        # Add language as tag (lowercase)
                        lang_lower = lang_name.lower()
                        if lang_lower not in all_tags:
                            all_tags.append(lang_lower)
                except Exception:
                    pass

                tags = all_tags[:12]  # cap at 12

                # Fetch latest release date
                latest_release_date = ""
                try:
                    releases = repo.get_releases()
                    latest = releases[0] if releases.totalCount > 0 else None
                    if latest:
                        latest_release_date = latest.published_at.strftime("%Y-%m-%d")
                        self.log_message.emit(f"   📦 Latest release: {latest.tag_name} ({latest_release_date})", "info")
                except Exception:
                    pass

                cred_score = self._calculate_credibility(org_rep, stars, commit_count, repo)

                vault_path = self.config.get('vault_path', '')
                if not vault_path:
                    self.log_message.emit("Vault path not configured", "error")
                    # v30 — Fix (CacheDB leak): close the handle before the
                    # early return (this path previously leaked it).
                    cache.close()
                    self.finished_signal.emit(False, "Vault path missing")
                    return

                folder_path = os.path.join(vault_path, CATEGORY_FOLDERS.get(category_key, "Uncategorized"))
                os.makedirs(folder_path, exist_ok=True)

                # Review queue: low-confidence notes go to _review/
                review_mode = confidence < 60
                if review_mode:
                    review_folder = os.path.join(vault_path, "_review")
                    os.makedirs(review_folder, exist_ok=True)
                    folder_path = review_folder
                    self.log_message.emit(f"⚠️ Low confidence ({confidence}%) — note sent to _review/ folder", "warning")

                # v22 Feature 5: Note Quality Score — flag low-quality notes for
                # review. If the note is low-quality AND not already in the
                # review folder (low-confidence), move it to _review/.
                quality_issues = []
                if len(summary) < 50:
                    quality_issues.append("summary too short")
                if isinstance(features, list) and len(features) < 3:
                    quality_issues.append("fewer than 3 features")
                if confidence < 30:
                    quality_issues.append("low confidence")
                if category_key == "Uncategorized":
                    quality_issues.append("uncategorized")
                is_low_quality = len(quality_issues) > 0
                if is_low_quality and not review_mode:
                    review_folder = os.path.join(vault_path, "_review")
                    os.makedirs(review_folder, exist_ok=True)
                    folder_path = review_folder
                    self.log_message.emit(
                        f"⚠️ Low quality ({', '.join(quality_issues)}) — note sent to _review/ folder",
                        "warning"
                    )

                # Wait for banner download to finish (started in parallel above)
                banner_thread.join(timeout=15)
                banner_path = banner_result[0]

                # If banner was downloaded to Uncategorized but category differs, move it
                if banner_path and category_key != "Uncategorized":
                    uncategorized_folder = os.path.join(vault_path, CATEGORY_FOLDERS.get("Uncategorized", "Uncategorized"))
                    if uncategorized_folder in banner_path:
                        import shutil
                        new_banner = os.path.join(folder_path, os.path.basename(banner_path))
                        try:
                            shutil.move(banner_path, new_banner)
                            banner_path = new_banner
                        except Exception:
                            pass

                if banner_path:
                    self.log_message.emit(f"   ✅ Banner: {os.path.basename(banner_path)}", "success")
                else:
                    # v26 — Fix 5: a missing banner is NOT a problem — the
                    # note is still written and the banner can be retried
                    # later. Log as 'info' (not 'warning') so the user
                    # doesn't think something went wrong.
                    self.log_message.emit(
                        f"   ⚠️ No banner (will retry later) — note still written",
                        "info"
                    )

                short_summary = llm_result.get("short_summary", "")

                note_content = self._build_note(
                    url=url, repo_name=repo_name, owner=owner_login,
                    org_name=org_name, stars=stars, forks=forks,
                    commit_count=commit_count, cred_score=cred_score,
                    org_rep=org_rep, summary=summary, tags=tags,
                    category_key=category_key, confidence=confidence,
                    how_it_works=how_it_works, core_value=core_value,
                    features=features, difference=difference,
                    banner_path=banner_path,
                    primary_language=primary_language,
                    languages=languages_list,
                    short_summary=short_summary,
                    latest_release_date=latest_release_date,
                    quality_issues=quality_issues,
                    is_low_quality=is_low_quality
                )

                # v30 — Fix (atomic writes + collision handling): filename via
                # storage helpers; the note is written to a temp file in the
                # same folder then os.replace()d — a crash/disk-full mid-write
                # can never leave a truncated note behind that the cache
                # would then record as processed.
                filename = _storage.build_note_filename(repo_name, category_key, tags)
                full_path = _storage.unique_path(os.path.join(folder_path, filename))

                try:
                    _storage.atomic_write_text(full_path, note_content)
                except OSError as write_err:
                    if "No space" in str(write_err) or "disk" in str(write_err).lower():
                        self.log_message.emit(f"💾 DISK FULL! Cannot write: {full_path}", "error")
                        # v30 — Fix (headless hang-bomb): the GUI-less run has
                        # no dialog to clear _disk_full_paused — the old code
                        # spun on sleep(1) FOREVER. Skip the repo, record it in
                        # the retry queue, keep the batch moving.
                        if self._headless:
                            self.log_message.emit(
                                "⏭️ Headless mode: skipping this repo (disk full). "
                                "Free space, then use 'Retry Failed' in the GUI.",
                                "error"
                            )
                            try:
                                cache.add_failed(url, f"disk full: {write_err}")
                            except Exception:
                                pass
                            if self.link_tracker:
                                try:
                                    self.link_tracker.mark_failed(url, f"disk full: {write_err}")
                                except Exception:
                                    pass
                            self.progress_updated.emit(self._current_position, self.total)
                            continue
                        self.disk_full_signal.emit(full_path)
                        # Wait for resume (is_running stays True, but we set a flag)
                        self._disk_full_paused = True
                        while self._disk_full_paused and self.is_running:
                            time.sleep(1)
                        if not self.is_running:
                            break
                        # Retry the write (wrapped in try/except — disk may
                        # still be full)
                        try:
                            _storage.atomic_write_text(full_path, note_content)
                        except OSError:
                            self.log_message.emit(f"❌ Disk still full — skipping {url}", "error")
                            # v26 — Fix 1: emit progress on skip.
                            self.progress_updated.emit(self._current_position, self.total)
                            continue
                    else:
                        raise

                cache.add_processed(repo_id, url, owner, repo_name, full_path, category_key)
                # v22 Feature 4: Mark any previous failure for this URL as resolved
                # so it no longer shows up in the "Retry Failed" queue.
                cache.mark_failed_resolved(url)
                # Update the vault index incrementally so the next URL in the
                # batch can dedup against this note.
                if self._vault_index:
                    self._vault_index.add_url(url, full_path)
                self.log_message.emit(f"✅ Processed: {owner_login}/{repo_name} -> {full_path}", "success")
                self.processed += 1

                # v23 — Phase 2: mark as processed (note written + cache updated)
                if self.link_tracker:
                    try:
                        self.link_tracker.mark_processed(url, full_path)
                    except Exception:
                        pass

                # Track for the summary log
                if not hasattr(self, '_processed_log'):
                    self._processed_log = []
                self._processed_log.append({
                    'url': url,
                    'repo': f"{owner_login}/{repo_name}",
                    'category': category_key,
                    'note_path': full_path,
                    'banner': bool(banner_path),
                    'credibility': cred_score,
                })

            except Exception as e:
                self.log_message.emit(f"❌ Error processing {url}: {e}", "error")
                # v22 Feature 4: Record the failure so the user can retry later
                # via the "🔄 Retry Failed" button. Best-effort.
                try:
                    cache.add_failed(url, str(e))
                except Exception:
                    pass
                # v23 — Phase 2: mark as failed in the manifest so Phase 5
                # verification blocks the bot-queue mark-read and Phase 4
                # reconciliation can surface it on next launch.
                if self.link_tracker:
                    try:
                        self.link_tracker.mark_failed(url, str(e))
                    except Exception:
                        pass
                # v26 — Fix 1: emit progress on skip.
                self.progress_updated.emit(self._current_position, self.total)
                continue

            time.sleep(self.config.get('delay_between_api_calls', 0.5))
            # v25 pre-flight: For large batches (>50 links), add an extra
            # 1.5s delay between repos so we never hit the GitHub rate limit
            # mid-batch. 5000 requests/hour ÷ 1.5s/repo = ~333 repos/hour, so
            # even a 300-link batch finishes well under the limit.
            if self.total > 50:
                time.sleep(self.config.get('large_batch_extra_delay', 1.5))

        cache.close()

        # v22 Feature 6: Batch Undo — compute the list of NEW .md files
        # written by this batch (anything in the vault now that wasn't in
        # the pre-batch snapshot). Save to `_undo_last_batch.txt` in the
        # vault root so the user can undo via the Dashboard button.
        # Best-effort: any error is logged but doesn't break the batch.
        try:
            new_files = []
            if vault_path and os.path.isdir(vault_path) and batch_files:
                for root, dirs, files in os.walk(vault_path):
                    if any(skip in root for skip in ['.obsidian', 'attachments']):
                        continue
                    for f in files:
                        if f.endswith('.md'):
                            fpath = os.path.join(root, f)
                            if fpath not in batch_files:
                                new_files.append(fpath)
            if vault_path and os.path.isdir(vault_path):
                undo_path = os.path.join(vault_path, '_undo_last_batch.txt')
                with open(undo_path, 'w', encoding='utf-8') as uf:
                    for f in new_files:
                        uf.write(f + '\n')
                if new_files:
                    self.log_message.emit(
                        f"↩️ Batch undo saved: {len(new_files)} new files can be undone via Dashboard → 'Undo Last Batch'",
                        "info"
                    )
        except Exception as undo_err:
            try:
                self.log_message.emit(f"⚠️ Failed to save batch undo list: {undo_err}", "warning")
            except Exception:
                pass

        # Final progress update to 100%
        self.progress_updated.emit(self.total, self.total)

        # v23 — Phase 3: VERIFICATION — check that every "processed" link has
        # a real note file (>100 bytes) on disk and every "recorded" non-GitHub
        # link is actually in the inbox table. Any link that fails verification
        # is marked "failed" in the manifest so Phase 5 (in MainWindow) blocks
        # the bot-queue mark-read and Phase 4 (on next launch) can surface it.
        report = None  # v25: capture for the final report
        if self.link_tracker:
            try:
                report = self.link_tracker.verify(log_signal=self.log_message)
                if not report["verification_passed"]:
                    self.log_message.emit(
                        f"⚠️ {len(report['failed_links'])} links failed verification — will retry on next run",
                        "warning"
                    )
            except Exception as verify_err:
                self.log_message.emit(
                    f"⚠️ Verification failed (continuing): {verify_err}",
                    "warning"
                )

        # Generate summary txt log
        summary_path = self._generate_summary_log()

        # Generate master index + MOCs (incremental, with timestamps)
        self._generate_master_index()

        # v25 pre-flight: comprehensive final report — saved in the vault
        # root as _processing_report_YYYYMMDD_HHMMSS.md. Always generated
        # (even if some links failed) so the user has a complete audit
        # trail of what was processed, what was skipped, and what needs
        # retry. Includes the LinkTracker verification report when present.
        try:
            report_path = self._generate_final_report(report)
            if report_path:
                self.log_message.emit(f"📊 Final report saved: {report_path}", "success")
        except Exception as final_report_err:
            try:
                self.log_message.emit(f"⚠️ Failed to generate final report: {final_report_err}", "warning")
            except Exception:
                pass

        msg = f"Processed {self.processed} out of {self.total} repos."
        if summary_path:
            msg += f" Summary log: {summary_path}"
        self.finished_signal.emit(True, msg)
        self.log_message.emit(f"🏁 Done. Processed {self.processed} repos.", "info")
        if summary_path:
            self.log_message.emit(f"📝 Summary log saved: {summary_path}", "success")

    def stop(self):
        self.is_running = False
