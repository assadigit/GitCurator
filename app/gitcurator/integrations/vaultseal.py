#!/usr/bin/env python3
"""
VaultSeal — automatic post-run vault backup to a private GitHub repository.

Obsidian's free tier has no sync and no off-site backup. GitCurator already
writes every curated note into your vault; VaultSeal closes the loop by
sealing the whole vault into git after every curation run — 1 new project
or 100 — and pushing it to a PRIVATE GitHub repository:

    run finishes -> git add -A -> commit "seal: ..." -> push (private repo)

Restore is plain git: ``git clone <repo>`` gives the full vault at any
point in its history, and Obsidian opens the clone directly.

Design rules (inherited from the v30 testable-core discipline)
-------------------------------------------------------------
* Pure stdlib — no PyQt6 / PyGithub / telethon imports, so the module stays
  importable headlessly and inside the 45-test CI gate.
* NEVER raises: ``seal()`` always returns a ``SealResult``. A backup that
  fails must never fail the curation run it protects.
* Credential hygiene: the GitHub token is used only for API calls and a
  one-time push URL. It is never written to .git/config (remotes stay
  token-less), never persisted, never logged.
* Skip when unchanged: an idle vault produces no commit and no push.
* Machine-specific Obsidian state (workspace.json, .trash/, OS noise) is
  excluded from the backup via a managed .gitignore block — merged
  idempotently, never clobbering user rules.

Usage (standalone):
    python vaultseal.py --vault /path/to/vault [--repo-name NAME]
                        [--token $GITHUB_TOKEN] [--no-push] [--json]
    python vaultseal.py --vault /path/to/vault --status
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

__all__ = ["VaultSeal", "SealResult", "seal_from_config", "slugify"]

GITHUB_API = "https://api.github.com"
DEFAULT_TIMEOUT = 60          # seconds, per git invocation
PUSH_TIMEOUT = 180            # pushes can be slow on big vaults / slow links
API_TIMEOUT = 30              # seconds, per GitHub API call

IDENTITY_NAME = "GitCurator VaultSeal"
IDENTITY_EMAIL = "vaultseal@users.noreply.github.com"

# Machine-specific Obsidian state + OS noise: sealed OUT of the backup.
DEFAULT_IGNORE_LINES: Tuple[str, ...] = (
    ".obsidian/workspace.json",
    ".obsidian/workspace-mobile.json",
    ".obsidian/workspace.json.bak",
    ".obsidian/cache",
    ".trash/",
    "__pycache__/",
    "*.pyc",
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
    "*.tmp",
    "*.bak",
    "~$*",
)
GITIGNORE_HEADER = "# --- VaultSeal (machine-specific state — not backed up) ---"

LogFn = Callable[[str, str], None]


def _noop_log(msg: str, level: str = "info") -> None:  # pragma: no cover
    return None


def slugify(name: str) -> str:
    """'Github Projects(Automated)' -> 'github-projects-automated'."""
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name or "").strip("-").lower()
    return re.sub(r"-{2,}", "-", s)


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class SealResult:
    """Outcome of one seal() call. ``ok`` == True means the vault is safe
    (a commit was created, or there was genuinely nothing to do)."""

    sealed: bool = False                 # a commit was created
    skipped_reason: Optional[str] = None  # set when nothing needed doing
    error: Optional[str] = None            # set when the seal failed
    commit_sha: Optional[str] = None
    commit_message: str = ""
    files_changed: int = 0
    pushed: bool = False
    repo_name: Optional[str] = None       # "owner/repo" when resolved
    repo_url: Optional[str] = None         # https://github.com/owner/repo
    private: bool = True
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        if self.error:
            return False
        return self.sealed or self.skipped_reason is not None

    def describe(self) -> str:
        if self.skipped_reason:
            return f"skipped — {self.skipped_reason}"
        if self.error:
            base = f"failed — {self.error}"
            if self.sealed:
                base += " (commit kept locally)"
            return base
        bits = [f"sealed {self.files_changed} file(s)"]
        if self.repo_name:
            bits.append(f"→ {self.repo_name}")
        if self.commit_sha:
            bits.append(f"({self.commit_sha})")
        if not self.pushed:
            bits.append("[local only — no push]")
        return " ".join(bits)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sealed": self.sealed,
            "skipped_reason": self.skipped_reason,
            "error": self.error,
            "commit_sha": self.commit_sha,
            "commit_message": self.commit_message,
            "files_changed": self.files_changed,
            "pushed": self.pushed,
            "repo_name": self.repo_name,
            "repo_url": self.repo_url,
            "private": self.private,
            "duration_ms": self.duration_ms,
            "ok": self.ok,
        }


# ---------------------------------------------------------------------------
# VaultSeal
# ---------------------------------------------------------------------------

class VaultSeal:
    """Seal one Obsidian vault into its private GitHub mirror."""

    def __init__(
        self,
        vault_path: str,
        token: str = "",
        repo_name: str = "",
        auto_push: bool = True,
        enabled: bool = True,
        timeout: int = DEFAULT_TIMEOUT,
        log: Optional[LogFn] = None,
    ) -> None:
        raw = (str(vault_path) if vault_path else "").strip()
        self.vault: Optional[Path] = Path(raw).expanduser() if raw else None
        self.token: Optional[str] = ((token or "").strip() or None)
        self.repo_name: str = (repo_name or "").strip()
        # Pushing requires a token — without one we degrade to local-only.
        self.auto_push: bool = bool(auto_push and self.token)
        self.enabled: bool = bool(enabled)
        self.timeout: int = int(timeout or DEFAULT_TIMEOUT)
        self._log: LogFn = log or _noop_log
        self._owner: Optional[str] = None      # resolved lazily from the token
        self._repo_full: Optional[str] = None  # "owner/repo" after ensure_repo
        self._remote_url: Optional[str] = None

    # -- plumbing ----------------------------------------------------------

    def _git(self, *args: str, timeout: Optional[int] = None) -> Tuple[int, str]:
        """Run git inside the vault. Returns (returncode, combined output).
        Never raises — missing git / timeouts come back as rc 127."""
        if self.vault is None:
            return 127, "no vault path configured"
        try:
            proc = subprocess.run(
                ["git", "-C", str(self.vault), *args],
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
            )
        except subprocess.TimeoutExpired:
            return 124, f"git {' '.join(args[:2])} timed out after {timeout or self.timeout}s"
        except (FileNotFoundError, OSError) as e:
            return 127, f"git not available: {e}"
        out = ((proc.stdout or "") + (proc.stderr or "")).strip()
        return proc.returncode, out

    def _api(self, method: str, api_path: str,
             body: Optional[Dict[str, Any]] = None) -> Tuple[int, Dict[str, Any]]:
        """GitHub REST call with the token. Returns (http_status, payload).
        Network failures come back as status 0 — never raises."""
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "GitCurator-VaultSeal",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(GITHUB_API + api_path, data=data,
                                     method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=API_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", "replace") or "{}"
                return resp.status, json.loads(raw)
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read().decode("utf-8", "replace") or "{}")
            except Exception:
                payload = {}
            return e.code, payload
        except Exception as e:  # timeout / DNS / connection refused
            return 0, {"message": str(e)}

    def _owner_login(self) -> Optional[str]:
        if self._owner is None:
            if not self.token:
                return None
            status, payload = self._api("GET", "/user")
            if status != 200:
                # v32.1: 401 = the token itself is bad (expired / revoked /
                # rotated) — name the fix instead of a bare status dump.
                hint = (
                    " — token invalid or expired; update it in "
                    "Settings → Credentials → GitHub Token (then 'Test GitHub Token')"
                    if status == 401 else ""
                )
                self._log(
                    f"VaultSeal: could not resolve the GitHub user for the token "
                    f"({status}: {payload.get('message', 'network error')}){hint}",
                    "warning",
                )
                return None
            self._owner = str(payload.get("login") or "") or None
        return self._owner

    def default_repo_name(self) -> str:
        return slugify(self.vault.name) if self.vault else ""

    # -- repository bootstrap ----------------------------------------------

    def ensure_gitignore(self) -> List[str]:
        """Merge the managed ignore block into the vault's .gitignore.
        Idempotent — returns the lines actually added ([] when clean)."""
        if self.vault is None:
            return []
        gi = self.vault / ".gitignore"
        try:
            current = gi.read_text(encoding="utf-8") if gi.exists() else ""
        except OSError:
            current = ""
        lines = [ln.rstrip("\r\n") for ln in current.splitlines()]
        have = set(lines)
        added = [ln for ln in DEFAULT_IGNORE_LINES if ln not in have]
        if not added:
            return []
        block = "\n".join([GITIGNORE_HEADER, *added])
        new_text = (current.rstrip("\n") + "\n\n" + block + "\n") if current.strip() \
            else (block + "\n")
        tmp = self.vault / ".gitignore.tmp"
        try:
            tmp.write_text(new_text, encoding="utf-8")
            os.replace(tmp, gi)  # atomic — readers see old or new, never half
        except OSError as e:
            self._log(f"VaultSeal: could not update .gitignore: {e}", "warning")
        return added

    def ensure_repo(self) -> Tuple[bool, Optional[str]]:
        """Bootstrap the vault as a git repo with identity + remote.
        Returns (ok, error_message). Safe to call repeatedly."""
        if self.vault is None or not self.vault.is_dir():
            return False, f"vault directory not found: {self.vault}"

        # 1) The vault must be its OWN repository. A vault nested inside
        #    another git repo (a dotfiles tree, a synced workspace, the
        #    GitCurator sandbox itself...) must never seal the PARENT's
        #    files: ``git add -A`` from a subdirectory is REPO-WIDE in
        #    git >= 2.0. We therefore compare the resolved toplevel with
        #    the vault itself and bootstrap a nested repo on mismatch —
        #    which also covers the plain "no repo yet" case.
        vault_real = os.path.realpath(str(self.vault))
        rc, out = self._git("rev-parse", "--show-toplevel")
        toplevel = os.path.realpath(out.strip()) if rc == 0 and out.strip() else None
        if toplevel != vault_real:
            rc, out = self._git("init", "-b", "main")
            if rc != 0:
                # older git without -b: plain init + move HEAD to main
                rc2, out2 = self._git("init")
                if rc2 == 0:
                    self._git("symbolic-ref", "HEAD", "refs/heads/main")
                else:
                    return False, f"git init failed: {out2 or out}"
            self._log("VaultSeal: initialised a git repository inside the vault",
                      "info")

        # 2) machine-state hygiene (idempotent)
        self.ensure_gitignore()

        # 3) local identity — only when unset, never touches global config
        rc, out = self._git("config", "user.email")
        if rc != 0 or not out:
            self._git("config", "user.name", IDENTITY_NAME)
            self._git("config", "user.email", IDENTITY_EMAIL)

        # 4) remote (only when pushing is possible)
        if not self.auto_push:
            return True, None
        owner = self._owner_login()
        if not owner:
            # v32.1: actionable — the token was rejected, not a mystery.
            return False, ("could not resolve the GitHub account for the token "
                           "(invalid or expired — update the GitHub Token in "
                           "Settings → Credentials, then 'Test GitHub Token')")
        repo = self.repo_name or self.default_repo_name() or "gitcurator-vault"

        status, payload = self._api("GET", f"/repos/{owner}/{repo}")
        if status == 404:
            status, payload = self._api("POST", "/user/repos", {
                "name": repo,
                "private": True,
                "description": "VaultSeal — automatic Obsidian vault backup (GitCurator)",
                "has_issues": False,
                "has_wiki": False,
                "has_projects": False,
                "auto_init": False,
            })
            if status != 201:
                # 422 usually means the name is taken (race) — re-check.
                re_status, _ = self._api("GET", f"/repos/{owner}/{repo}")
                if re_status != 200:
                    return False, (f"could not create the private repo "
                                  f"{owner}/{repo}: {payload.get('message', status)}")
            self._log(f"VaultSeal: created private repository {owner}/{repo}",
                      "success")
        elif status != 200:
            return False, (f"GitHub API error checking {owner}/{repo}: "
                           f"{payload.get('message', status)}")

        url = f"https://github.com/{owner}/{repo}.git"
        rc, _ = self._git("remote", "get-url", "origin")
        if rc != 0:
            self._git("remote", "add", "origin", url)  # token-less remote
        self._repo_full = f"{owner}/{repo}"
        self._remote_url = f"https://github.com/{owner}/{repo}"
        return True, None

    # -- the seal -----------------------------------------------------------

    def _commit_message(self, run_summary: Dict[str, Any]) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        processed = run_summary.get("processed")
        total = run_summary.get("total")
        if isinstance(processed, int) and isinstance(total, int) and total > 0:
            return f"seal: {processed} note(s) curated ({processed}/{total} repos) — {ts}"
        return f"seal: vault snapshot — {ts}"

    def _seal(self, run_summary: Dict[str, Any]) -> SealResult:
        if not self.enabled:
            return SealResult(skipped_reason="VaultSeal is disabled in config")
        if self.vault is None or not self.vault.is_dir():
            return SealResult(error=f"vault directory not found: {self.vault}")

        ok, err = self.ensure_repo()
        if not ok:
            return SealResult(error=err)

        meta = {
            "repo_name": self._repo_full,
            "repo_url": self._remote_url,
            "private": True,
        }

        # Stage everything — pathspec-limited to the vault subtree so a
        # hypothetical parent repo can never swallow unrelated files (the
        # managed .gitignore keeps machine state out either way).
        self._git("add", "-A", "--", ".")
        rc, porcelain = self._git("status", "--porcelain")
        rc_head, _ = self._git("rev-parse", "--verify", "-q", "HEAD")
        dirty = [ln for ln in porcelain.splitlines() if ln.strip()] if rc == 0 else []

        if not dirty and rc_head == 0:
            return SealResult(skipped_reason="vault unchanged since the last seal",
                              **meta)

        message = self._commit_message(run_summary)
        rc, out = self._git("commit", "-m", message, "--quiet")
        if rc != 0:
            return SealResult(error=f"git commit failed: {out}", **meta)
        _, sha = self._git("rev-parse", "--short=7", "HEAD")

        result = SealResult(
            sealed=True,
            commit_sha=sha or None,
            commit_message=message,
            files_changed=len(dirty),
            **meta,
        )

        if not self.auto_push:
            self._log("VaultSeal: committed locally — no GitHub token configured, "
                      "push skipped (set the token in Settings to enable pushing)",
                      "warning")
            return result

        # One-time token URL: never written to .git/config, never logged.
        push_url = f"https://x-access-token:{self.token}@github.com/{self._repo_full}.git"
        ok, err = self._push(push_url)
        if not ok:
            result.error = f"push failed (local commit kept): {err[:300]}"
            self._log(f"VaultSeal: {result.error}", "warning")
        else:
            result.pushed = True
            # The reconciliation path may have rebased/merged — re-read the
            # tip so the log line names the sha actually on the mirror.
            _, sha = self._git("rev-parse", "--short=7", "HEAD")
            result.commit_sha = sha or result.commit_sha
            self._log(
                f"VaultSeal: sealed {len(dirty)} file(s) → {self._repo_full} ({sha})",
                "success",
            )
        return result

    # -- v0.21.0 push reconciliation -----------------------------------------

    def _sanitize_fetch_head(self) -> None:
        """Strip the access token out of ``.git/FETCH_HEAD`` after a fetch.

        ``git fetch <token-url>`` writes the full URL (token included) into
        FETCH_HEAD. The design rule is that the token is never persisted
        anywhere — best effort, failure tolerated (next fetch overwrites
        the file anyway)."""
        if self.vault is None or not self.token:
            return
        try:
            fh = self.vault / ".git" / "FETCH_HEAD"
            if not fh.exists():
                return
            text = fh.read_text(encoding="utf-8", errors="replace")
            if self.token in text:
                fh.write_text(text.replace(self.token, "***"), encoding="utf-8")
        except OSError:
            pass

    def _rescue(self, push_url: str, original_err: str) -> Tuple[bool, str]:
        """Last resort after an irreconcilable mirror: push the seal to a
        ``seal-rescue/<timestamp>`` branch. The local commit is never lost,
        the remote main is never force-pushed, and the error names the
        branch so the owner can reconcile at their leisure."""
        branch = f"seal-rescue/{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        rc, out = self._git("push", push_url, f"HEAD:refs/heads/{branch}",
                            timeout=PUSH_TIMEOUT)
        if rc == 0:
            return False, (
                f"mirror main rejected the seal and could not be reconciled "
                f"automatically — the commit was pushed to branch '{branch}' "
                f"instead (local commit kept). Reconcile on GitHub: open the "
                f"mirror repo → Compare & pull request '{branch}' → main, or "
                f"clone the mirror and merge locally. ({original_err[:150]})")
        return False, (f"{original_err} (rescue push to '{branch}' also "
                       f"failed: {out[:150]})")

    def _push(self, push_url: str) -> Tuple[bool, str]:
        """Push HEAD:refs/heads/main to the mirror (v0.21.0).

        Plain push first. When GitHub rejects it because the remote has
        commits we lack (a web-UI edit on the mirror, a second machine
        sealing, or the mirror repo having been used for something else
        mid-flight — exactly the v0.20.0 websites-mirror failure), the
        seal RECONCILES instead of failing forever:

          fetch → remote already inside HEAD → retry the plain push
          fetch → shared history → rebase our commit(s) onto it → push
          fetch → unrelated histories (fresh ``git init`` / repurposed
                   mirror) → merge --allow-unrelated-histories → push

        A reconciliation that cannot complete cleanly (conflicts) never
        destroys anything: the seal lands on a rescue branch and the
        error says exactly where. ``main`` is NEVER force-pushed — a
        backup tool must never discard remote history it cannot see.
        Returns (ok, error_detail). Never raises."""
        rc, out = self._git("push", push_url, "HEAD:refs/heads/main",
                            timeout=PUSH_TIMEOUT)
        if rc == 0:
            return True, ""
        if "fetch first" not in out and "non-fast-forward" not in out.lower():
            # Auth / network / permission failure — reconciliation does not
            # apply; report the original error verbatim.
            return False, out

        # The remote is ahead: bring its main in (FETCH_HEAD) and reconcile.
        frc, fout = self._git("fetch", push_url, "refs/heads/main",
                              timeout=PUSH_TIMEOUT)
        self._sanitize_fetch_head()
        if frc != 0:
            return False, (f"{out} — and fetching the mirror to reconcile "
                           f"failed: {fout[:150]}")

        # Remote already contained in HEAD (stale rejection) → plain retry.
        rc_anc, _ = self._git("merge-base", "--is-ancestor",
                              "FETCH_HEAD", "HEAD")
        if rc_anc == 0:
            rc2, out2 = self._git("push", push_url, "HEAD:refs/heads/main",
                                  timeout=PUSH_TIMEOUT)
            return (rc2 == 0), out2

        rc_base, _ = self._git("merge-base", "HEAD", "FETCH_HEAD")
        if rc_base == 0:
            # Shared history: replay our seal commit(s) on top of the remote.
            rrc, _rout = self._git("rebase", "FETCH_HEAD")
            if rrc == 0:
                rc3, out3 = self._git("push", push_url,
                                      "HEAD:refs/heads/main",
                                      timeout=PUSH_TIMEOUT)
                if rc3 == 0:
                    return True, ""
                return self._rescue(push_url, out3)
            self._git("rebase", "--abort")
            return self._rescue(push_url, out)

        # Unrelated histories: keep BOTH (merge) when the trees do not
        # collide; a collision (same path, different content) → rescue.
        mrc, _mout = self._git(
            "merge", "FETCH_HEAD", "--allow-unrelated-histories", "-m",
            "seal: reconcile mirror histories (v0.21.0)")
        if mrc == 0:
            rc4, out4 = self._git("push", push_url, "HEAD:refs/heads/main",
                                  timeout=PUSH_TIMEOUT)
            if rc4 == 0:
                return True, ""
            return self._rescue(push_url, out4)
        self._git("merge", "--abort")
        return self._rescue(push_url, out)

    def seal(self, run_summary: Optional[Dict[str, Any]] = None) -> SealResult:
        """Commit + best-effort push of the whole vault. NEVER raises."""
        started = time.monotonic()
        try:
            result = self._seal(run_summary or {})
        except Exception as e:  # a backup failure must never bubble up
            result = SealResult(error=f"{type(e).__name__}: {e}")
        result.duration_ms = int((time.monotonic() - started) * 1000)
        return result

    # -- introspection --------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        """Snapshot for UIs: repo state, dirty files, last commit."""
        info: Dict[str, Any] = {
            "vault_path": str(self.vault) if self.vault else None,
            "exists": bool(self.vault and self.vault.is_dir()),
            "is_git_repo": False,
            "has_commits": False,
            "dirty_count": None,
            "last_commit": None,
            "remote_url": None,
            "repo_name": self._repo_full or (self.repo_name or None),
        }
        if not info["exists"]:
            return info
        rc, out = self._git("rev-parse", "--show-toplevel")
        info["is_git_repo"] = (
            rc == 0
            and bool(out.strip())
            and os.path.realpath(out.strip()) == os.path.realpath(str(self.vault))
        )
        if not info["is_git_repo"]:
            return info
        rc, _ = self._git("rev-parse", "--verify", "-q", "HEAD")
        info["has_commits"] = rc == 0
        rc, out = self._git("status", "--porcelain")
        if rc == 0:
            info["dirty_count"] = len([ln for ln in out.splitlines() if ln.strip()])
        if info["has_commits"]:
            rc, out = self._git("log", "-1", "--format=%h%x00%s%x00%aI")
            if rc == 0 and out:
                parts = (out.split("\x00") + ["", "", ""])[:3]
                info["last_commit"] = {
                    "sha": parts[0], "subject": parts[1], "date": parts[2],
                }
        rc, out = self._git("remote", "get-url", "origin")
        info["remote_url"] = out if rc == 0 else None
        return info


# ---------------------------------------------------------------------------
# Config bridge (used by main.py — GUI + headless)
# ---------------------------------------------------------------------------

def seal_from_config(config: Dict[str, Any],
                     run_summary: Optional[Dict[str, Any]] = None,
                     log: Optional[LogFn] = None) -> SealResult:
    """Build a VaultSeal from the app's config dict and seal once.

    Reads: vault_path, github_token, and the ``vaultseal`` section:
      { "enabled": true, "repo_name": "", "auto_push": true }
    """
    cfg = (config or {}).get("vaultseal") or {}
    if not isinstance(cfg, dict):
        cfg = {}
    sealer = VaultSeal(
        vault_path=(config or {}).get("vault_path") or "",
        token=(config or {}).get("github_token") or "",
        repo_name=cfg.get("repo_name") or "",
        auto_push=cfg.get("auto_push", True),
        enabled=cfg.get("enabled", True),
        log=log,
    )
    return sealer.seal(run_summary)


def websites_seal_from_config(config: Dict[str, Any],
                              run_summary: Optional[Dict[str, Any]] = None,
                              log: Optional[LogFn] = None) -> SealResult:
    """Seal the WEBSITES vault into its own private repo (Phase 1, v0.10.0).

    A second, independent VaultSeal instance (SPEC §4.1: each machine vault
    gets its own backup repo). Reads: ``website_vault_path``,
    ``website_repo_name``, ``github_token`` and the ``pipelines.websites``
    switch. It is a no-op unless the websites pipeline is ON and a websites
    vault path is configured — with the default switch (off) this never
    runs, which is exactly the Phase 1 acceptance ("websites off behaves
    exactly as v0.09.4").
    """
    cfg = config or {}
    pipelines = cfg.get("pipelines") or {}
    if not isinstance(pipelines, dict) or not pipelines.get("websites", False):
        return SealResult(skipped_reason="websites pipeline is off")
    vault_path = (cfg.get("website_vault_path") or "").strip()
    if not vault_path:
        return SealResult(skipped_reason="no websites vault configured")
    repo_name = (cfg.get("website_repo_name") or "").strip()
    sealer = VaultSeal(
        vault_path=vault_path,
        token=cfg.get("github_token") or "",
        repo_name=repo_name,
        auto_push=True,
        enabled=True,
        log=log,
    )
    return sealer.seal(run_summary)


# ---------------------------------------------------------------------------
# Standalone CLI (manual seals + the dashboard's "Seal vault now" button)
# ---------------------------------------------------------------------------

def _main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="VaultSeal — seal an Obsidian vault into a private GitHub repository")
    parser.add_argument("--vault", required=True, help="path to the Obsidian vault")
    parser.add_argument("--repo-name", default="",
                        help="backup repo name (default: slugified vault folder name)")
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""),
                        help="GitHub token (default: env GITHUB_TOKEN)")
    parser.add_argument("--no-push", action="store_true",
                        help="commit locally only, never push")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    parser.add_argument("--status", action="store_true",
                        help="print the vault git status and exit")
    args = parser.parse_args(argv)

    def log(msg: str, level: str = "info") -> None:
        print(f"[vaultseal] [{level}] {msg}", file=sys.stderr, flush=True)

    sealer = VaultSeal(
        vault_path=args.vault,
        token=args.token,
        repo_name=args.repo_name,
        auto_push=not args.no_push,
        log=log,
    )
    if args.status:
        print(json.dumps(sealer.status(), indent=2))
        return 0

    result = sealer.seal()
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(result.describe())
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(_main())
