#!/usr/bin/env python3
"""
GoodRepos — automatic post-run publisher of the curated vault as a PUBLIC
GitHub repository ("good-repos").

VaultSeal keeps a PRIVATE mirror of the whole vault for its owner. GoodRepos
is the other half of the loop: after every curation run it turns the same
vault into something everyone can browse — a beautiful, emoji-rich,
auto-maintained README directory of every curated repository (organized like
"AI > Skills > ..."), plus the underlying notes mirrored into category
folders:

    run finishes -> scan category notes -> build staging tree + README
                 -> git init/fetch/commit -> push (public repo)

The published repo is intentionally a REGENERATED DIRECTORY, not a raw copy:
only the curated notes, the vault's ``_index.md`` + ``links_manifest.json``,
and one generated ``README.md`` ever leave the vault. No config, no
``.obsidian/``, no session state — inherently safe.

Design rules (inherited from VaultSeal / the v30 testable-core discipline)
-------------------------------------------------------------------------
* Pure stdlib — no PyQt6 / PyGithub / telethon imports, so the module stays
  importable headlessly and inside the CI gate.
* NEVER raises: ``publish()`` always returns a ``PublishResult``. A
  publishing failure must never fail the curation run it follows.
* Credential hygiene: the GitHub token is used only for API calls and a
  one-time push/fetch URL. It is never written to .git/config (remotes stay
  token-less — any ``origin`` is removed defensively), never persisted,
  never logged, and redacted from any captured git output.
* Skip when unchanged: a vault whose directory already matches the remote
  produces no commit and no push.
* Staging happens in a fresh ``tempfile.mkdtemp`` OUTSIDE any repository, so
  the sandbox workspace being a git repo can never leak into the directory.

Usage (standalone):
    python goodrepos.py --vault /path/to/vault [--repo-name good-repos]
                        [--token $GITHUB_TOKEN] [--no-push] [--json] [--status]
    python goodrepos.py --vault /path/to/vault --dry-run --json
"""

from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

__all__ = [
    "GoodRepos",
    "PublishResult",
    "publish_from_config",
    "CATEGORY_EMOJI",
    "format_stars",
    "github_anchor",
    "display_name",
    "emoji_for",
]

GITHUB_API = "https://api.github.com"
DEFAULT_TIMEOUT = 60          # seconds, per local git invocation
PUSH_TIMEOUT = 180            # pushes/fetches can be slow on big vaults
API_TIMEOUT = 30              # seconds, per GitHub API call

DEFAULT_REPO_NAME = "good-repos"
REPO_DESCRIPTION = "✨ A curated directory of good GitHub repositories — auto-maintained by GitCurator"

IDENTITY_NAME = "GoodRepos Publisher"
IDENTITY_EMAIL = "goodrepos@users.noreply.github.com"

# Machine-specific Obsidian state + scaffolding: never published.
IGNORED_DIRS = frozenset({
    ".obsidian", ".trash", "_inbox", "_review", "_moc", "__pycache__", ".git",
})

# Category folder component -> emoji (fallback 📁). Keys are RAW folder
# names as they appear in the vault ("LLM-Tools", "AI-Domain", ...).
CATEGORY_EMOJI: Dict[str, str] = {
    "AI-Domain": "🤖",
    "Agents": "🧠",
    "Skills": "✨",
    "MCP": "🔌",
    "LLM-Tools": "🧰",
    "Tools": "🛠️",
    "Scraping": "🕷️",
    "Automation": "⚙️",
    "Dev-Tools": "🧰",
    "Networking": "🌐",
    "Media": "🎨",
    "Documentation": "📚",
    "Guides": "📖",
    "References": "📑",
    "Research": "🔬",
    "Frameworks": "🏗️",
    "Web-Frameworks": "🌐",
    "Backend": "🗄️",
    "Frontend": "🎨",
    "Infrastructure": "🏗️",
    "Deployment": "🚀",
    "Cloud": "☁️",
    "Containerization": "📦",
    "Uncategorized": "📦",
}

LogFn = Callable[[str, str], None]


def _noop_log(msg: str, level: str = "info") -> None:  # pragma: no cover
    return None


# ---------------------------------------------------------------------------
# Small deterministic helpers (public — reused by the tests)
# ---------------------------------------------------------------------------

def github_anchor(text: str) -> str:
    """Anchor GitHub generates for a heading: lowercase, drop anything that
    is not alphanumeric/space/hyphen (emoji, punctuation), spaces->hyphens.

    "🤖 AI Domain"  -> "-ai-domain"   (the emoji leaves a leading hyphen)
    "🧠 Agents (2)" -> "-agents-2"
    """
    s = (text or "").strip().lower()
    kept = [ch for ch in s if ch.isalnum() or ch in (" ", "-")]
    return "".join(kept).replace(" ", "-")


def display_name(folder: str) -> str:
    """Folder component -> display name: hyphen->space (already title-case).
    "AI-Domain" -> "AI Domain", "Web-Frameworks" -> "Web Frameworks"."""
    return (folder or "").replace("-", " ").strip()


def emoji_for(*components: str) -> str:
    """Emoji for a category path. Looks the components up from the deepest
    one backwards (e.g. "AI-Domain/Agents/Frameworks" -> "Frameworks").
    Fallback: 📁."""
    for comp in reversed(components):
        if comp and comp in CATEGORY_EMOJI:
            return CATEGORY_EMOJI[comp]
    return "📁"


def format_stars(stars: Optional[int]) -> str:
    """⭐ count for the entry line: thousands separators below 10k,
    compact "1.2k" style from 10k up (140232 -> "140.2k", 10000 -> "10k").
    Returns "" for anything unparsable."""
    if stars is None:
        return ""
    try:
        n = int(stars)
    except (TypeError, ValueError):
        return ""
    if n >= 10_000:
        s = f"{n / 1000:.1f}"
        if s.endswith(".0"):
            s = s[:-2]
        return s + "k"
    return f"{n:,}"


def _intro_name(display: str) -> str:
    """Display name for the top-category intro sentence: lowercase, keeping
    short ALL-CAPS acronyms ("AI Domain" -> "AI domain", "Tools" -> "tools")."""
    words = []
    for w in (display or "").split():
        if len(w) <= 4 and w.isalpha() and w.isupper():
            words.append(w)
        else:
            words.append(w.lower())
    return " ".join(words)


# ---------------------------------------------------------------------------
# Note parsing (tiny YAML-frontmatter subset — pure stdlib)
# ---------------------------------------------------------------------------

_FM_KEY_RE = re.compile(r"^([A-Za-z0-9_\-]+):\s*(.*)$")
_FM_ITEM_RE = re.compile(r"^\s*-\s+(.*)$")
_URL_RE = re.compile(r"github\.com/([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+)")


def _parse_scalar(raw: str) -> Any:
    """Frontmatter value -> python scalar/list. Handles quoted strings and
    inline "[a, b, c]" lists. Never raises."""
    s = (raw or "").strip()
    if s.startswith("[") and s.endswith("]") and len(s) >= 2:
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part) for part in inner.split(",")]
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1].strip()
    return s


def _parse_frontmatter(lines: List[str]) -> Dict[str, Any]:
    """Parse the '---' frontmatter block. Supports scalars, inline lists and
    block lists (aliases). Unknown shapes are ignored, never fatal."""
    fm: Dict[str, Any] = {}
    if not lines or lines[0].strip() != "---":
        return fm
    key: Optional[str] = None
    for ln in lines[1:]:
        if ln.strip() == "---":
            break
        m = _FM_KEY_RE.match(ln)
        if m:
            key = m.group(1)
            fm[key] = _parse_scalar(m.group(2))
            continue
        m = _FM_ITEM_RE.match(ln)
        if m and key:
            value = _parse_scalar(m.group(1))
            current = fm.get(key)
            if isinstance(current, list):
                current.append(value)
            elif current in (None, ""):
                fm[key] = [value]
            else:
                fm[key] = [current, value]
    return fm


def _to_int(value: Any) -> Optional[int]:
    """Best-effort int (handles "140232", "1,402"). None when unparsable."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip().replace(",", "")
        if re.fullmatch(r"-?\d+", s):
            try:
                return int(s)
            except ValueError:  # pragma: no cover — regex already filtered
                return None
    return None


def _owner_repo_from_url(url: Any) -> str:
    """"https://github.com/owner/repo/issues/42" -> "owner/repo"."""
    if not isinstance(url, str) or not url:
        return ""
    m = _URL_RE.search(url)
    if not m:
        return ""
    owner, repo = m.group(1), m.group(2)
    if repo.endswith(".git") and len(repo) > 4:
        repo = repo[:-4]
    return f"{owner}/{repo}"


def _as_tags(value: Any) -> List[str]:
    """Tags -> clean list of strings. Tolerates the malformed scalar shape
    occasionally produced by the LLM ("cp, python, server, tools]")."""
    if isinstance(value, list):
        raw = [str(t) for t in value]
    elif isinstance(value, str):
        s = value.strip().strip("[]").strip()
        raw = [p.strip() for p in s.split(",")] if s else []
    else:
        raw = []
    return [t for t in (r.strip().strip('"').strip("'") for r in raw) if t]


def _as_aliases(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(a).strip() for a in value if str(a).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


@dataclass
class _Note:
    """One curated note, parsed just enough to feed the directory README."""

    path: Path                       # absolute path inside the vault
    rel_path: str                    # vault-relative, POSIX ("AI-Domain/…/x.md")
    display: str = ""                # "owner/repo"
    url: str = ""                    # source url ("" when unknown)
    title: str = ""                  # "# <title>" from the body
    tldr: str = ""                   # "> **TL;DR:** …" (fallback: What is it?)
    tags: List[str] = field(default_factory=list)
    stars: Optional[int] = None
    language: str = ""
    category: str = ""               # frontmatter category ("" when missing)
    date_processed: str = ""
    credibility: str = ""


def _parse_note(path: Path, rel_path: str) -> _Note:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        raw = ""
    lines = raw.splitlines()

    fm = _parse_frontmatter(lines)
    body_start = len(lines)
    if lines and lines[0].strip() == "---":
        for i, ln in enumerate(lines[1:], start=1):
            if ln.strip() == "---":
                body_start = i + 1
                break

    # body mining: "# title", "> **TL;DR:** …", "## What is it?" fallback
    title, tldr, fallback = "", "", ""
    mode = None
    for ln in lines[body_start:]:
        s = ln.strip()
        if not title and s.startswith("# ") and not s.startswith("##"):
            title = s[2:].strip()
        if not tldr and s.startswith(">") and "**TL;DR:**" in s:
            tldr = s.split("**TL;DR:**", 1)[1].strip().strip('"').strip()
        if s.startswith("## "):
            mode = s[3:].strip().lower()
            continue
        if mode == "what is it?" and s and not fallback:
            fallback = s

    source = fm.get("source") or ""
    aliases = _as_aliases(fm.get("aliases"))
    display = _owner_repo_from_url(source)
    if display:
        url = f"https://github.com/{display}"  # canonical link
    else:
        url = source if isinstance(source, str) and source else ""
        display = next((a for a in aliases if "/" in a), "")
        if not display:
            display = next((a for a in aliases), "")
        if not display:
            stem = path.stem or "unknown"
            display = stem.split("_", 1)[0] if "_" in stem else stem

    stars = _to_int(fm.get("stars"))
    language = str(fm.get("primary_language") or "").strip()

    return _Note(
        path=path,
        rel_path=rel_path,
        display=display,
        url=url,
        title=title,
        tldr=tldr or fallback,
        tags=_as_tags(fm.get("tags")),
        stars=stars,
        language=language,
        category=str(fm.get("category") or "").strip(),
        date_processed=str(fm.get("date_processed") or "").strip(),
        credibility=str(fm.get("credibility_score") or "").strip(),
    )


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class PublishResult:
    """Outcome of one publish() call. ``ok`` == True means the public
    directory is up to date (a commit was pushed/kept, there was genuinely
    nothing to do, or a dry-run built the tree)."""

    published: bool = False                 # a directory was built/committed
    skipped_reason: Optional[str] = None    # set when nothing needed doing
    error: Optional[str] = None             # set when the publish failed
    commit_sha: Optional[str] = None
    commit_message: str = ""
    files_changed: int = 0
    pushed: bool = False
    repo_name: Optional[str] = None         # "owner/repo" when resolved
    repo_url: Optional[str] = None          # https://github.com/owner/repo
    entries: int = 0                        # curated repos found
    categories: int = 0                     # category folders found
    duration_ms: int = 0
    dry_run: bool = False
    staging_dir: Optional[str] = None       # where the tree was built

    @property
    def ok(self) -> bool:
        if self.error:
            return False
        return self.published or self.skipped_reason is not None

    def describe(self) -> str:
        if self.skipped_reason:
            return f"skipped — {self.skipped_reason}"
        if self.error:
            base = f"failed — {self.error}"
            if self.published:
                base += " (commit kept locally)"
            return base
        if self.dry_run:
            return (f"built a directory of {self.entries} repos across "
                    f"{self.categories} categories (dry-run — no git, no push) "
                    f"— staged at {self.staging_dir}")
        bits = [f"published {self.entries} repos across {self.categories} categories"]
        if self.repo_name:
            bits.append(f"→ {self.repo_name}")
        if self.commit_sha:
            bits.append(f"({self.commit_sha})")
        if not self.pushed:
            bits.append("[local only — no push]")
        return " ".join(bits)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "published": self.published,
            "skipped_reason": self.skipped_reason,
            "error": self.error,
            "commit_sha": self.commit_sha,
            "commit_message": self.commit_message,
            "files_changed": self.files_changed,
            "pushed": self.pushed,
            "repo_name": self.repo_name,
            "repo_url": self.repo_url,
            "entries": self.entries,
            "categories": self.categories,
            "duration_ms": self.duration_ms,
            "dry_run": self.dry_run,
            "staging_dir": self.staging_dir,
            "ok": self.ok,
        }


# ---------------------------------------------------------------------------
# Staging lifetime
# ---------------------------------------------------------------------------

# Staging dirs created by real publishes are reused across publish() calls of
# the same instance (that is what makes skip-when-unchanged work without a
# remote) and removed when the process exits. Dry-run dirs are NEVER removed —
# the CLI reports their path so a human can inspect the built directory.
_STAGING_CLEANUP: List[Path] = []


def _cleanup_staging() -> None:  # pragma: no cover — process-exit hygiene
    for d in _STAGING_CLEANUP:
        shutil.rmtree(d, ignore_errors=True)


atexit.register(_cleanup_staging)


def _git_env() -> Dict[str, str]:
    """Environment for git: strip repo-hijacking vars, never prompt."""
    env = dict(os.environ)
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG",
              "GIT_CONFIG_NOSYSTEM"):
        env.pop(k, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


# ---------------------------------------------------------------------------
# GoodRepos
# ---------------------------------------------------------------------------

class GoodRepos:
    """Publish one curated Obsidian vault as its public good-repos directory."""

    def __init__(
        self,
        vault_path: str,
        token: str = "",
        repo_name: str = DEFAULT_REPO_NAME,
        auto_push: bool = True,
        enabled: bool = True,
        timeout: int = DEFAULT_TIMEOUT,
        log: Optional[LogFn] = None,
    ) -> None:
        raw = (str(vault_path) if vault_path else "").strip()
        self.vault: Optional[Path] = Path(raw).expanduser() if raw else None
        self.token: Optional[str] = ((token or "").strip() or None)
        self.repo_name: str = (repo_name or "").strip() or DEFAULT_REPO_NAME
        # Pushing requires a token — without one we degrade to local-only.
        self.auto_push: bool = bool(auto_push and self.token)
        self.enabled: bool = bool(enabled)
        self.timeout: int = int(timeout or DEFAULT_TIMEOUT)
        self._log: LogFn = log or _noop_log
        self._owner: Optional[str] = None      # resolved lazily from the token
        self._repo_full: Optional[str] = None  # "owner/repo" after _ensure_repo
        self._remote_url: Optional[str] = None
        self._staging: Optional[Path] = None

    # -- plumbing ----------------------------------------------------------

    def _git(self, *args: str, timeout: Optional[int] = None) -> Tuple[int, str]:
        """Run git inside the staging dir. Returns (returncode, combined
        output). Never raises — missing git / timeouts come back as rc 127.
        The token is redacted from any captured output before it is returned."""
        if self._staging is None:
            return 127, "no staging directory configured"
        try:
            proc = subprocess.run(
                ["git", "-C", str(self._staging), *args],
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
                env=_git_env(),
            )
        except subprocess.TimeoutExpired:
            return 124, f"git {' '.join(args[:2])} timed out after {timeout or self.timeout}s"
        except (FileNotFoundError, OSError) as e:
            return 127, f"git not available: {e}"
        out = ((proc.stdout or "") + (proc.stderr or "")).strip()
        return proc.returncode, self._redact(out)

    def _redact(self, text: str) -> str:
        """Remove the token from any text that will be stored or logged."""
        if self.token and text and self.token in text:
            return text.replace(self.token, "***")
        return text

    def _api(self, method: str, api_path: str,
             body: Optional[Dict[str, Any]] = None) -> Tuple[int, Dict[str, Any]]:
        """GitHub REST call with the token. Returns (http_status, payload).
        Network failures come back as status 0 — never raises."""
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "GitCurator-GoodRepos",
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
                self._log(
                    f"GoodRepos: could not resolve the GitHub user for the token "
                    f"({status}: {payload.get('message', 'network error')})",
                    "warning",
                )
                return None
            self._owner = str(payload.get("login") or "") or None
        return self._owner

    def _token_url(self) -> str:
        """One-time authenticated URL. Used inline in git commands only —
        never stored in remotes or .git/config, never logged."""
        return f"https://x-access-token:{self.token}@github.com/{self._repo_full}.git"

    # -- repository bootstrap ----------------------------------------------

    def _ensure_repo(self) -> Tuple[bool, Optional[str]]:
        """Make sure the PUBLIC good-repos repository exists. Returns
        (ok, error_message). Safe to call repeatedly."""
        owner = self._owner_login()
        if not owner:
            return False, "could not resolve the GitHub account for the token"
        repo = self.repo_name

        status, payload = self._api("GET", f"/repos/{owner}/{repo}")
        if status == 200:
            if payload.get("private"):
                self._log(
                    f"GoodRepos: {owner}/{repo} is PRIVATE — publishing anyway "
                    f"(flip it to public on GitHub so everyone can browse)",
                    "warning",
                )
        elif status == 404:
            status, payload = self._api("POST", "/user/repos", {
                "name": repo,
                "private": False,
                "description": REPO_DESCRIPTION,
                "has_issues": True,
                "has_wiki": False,
                "auto_init": False,
            })
            if status != 201:
                # 422 usually means the name is taken (race) — re-check.
                re_status, _ = self._api("GET", f"/repos/{owner}/{repo}")
                if re_status != 200:
                    return False, (f"could not create the public repo "
                                  f"{owner}/{repo}: {payload.get('message', status)}")
            self._log(f"GoodRepos: created public repository {owner}/{repo}",
                      "success")
        else:
            return False, (f"GitHub API error checking {owner}/{repo}: "
                           f"{payload.get('message', status)}")

        self._repo_full = f"{owner}/{repo}"
        self._remote_url = f"https://github.com/{owner}/{repo}"
        return True, None

    def _fetch_history(self) -> bool:
        """Fetch the remote's main into the staging repo and align HEAD/index
        with it (reset --mixed), so the next commit is a real diff on top of
        the existing history — and skip-when-unchanged works. Returns True
        when the remote history was aligned; False means 'fresh directory'."""
        if not self._repo_full or not self.token:
            return False
        rc, out = self._git("fetch", self._token_url(), "main",
                            timeout=PUSH_TIMEOUT)
        self._sanitize_fetch_head()
        if rc != 0:
            self._log("GoodRepos: no remote history found — starting a fresh "
                      "directory", "info")
            return False
        rc, out = self._git("reset", "--mixed", "FETCH_HEAD")
        if rc != 0:
            self._log(f"GoodRepos: could not align with the remote history: "
                      f"{out}", "warning")
            return False
        return True

    def _sanitize_fetch_head(self) -> None:
        """git strips credentials when it writes .git/FETCH_HEAD, but belt
        and braces: if the token ever leaked in there, redact it."""
        if self._staging is None or not self.token:
            return
        fh = self._staging / ".git" / "FETCH_HEAD"
        try:
            if fh.is_file():
                text = fh.read_text(encoding="utf-8", errors="replace")
                if self.token in text:
                    fh.write_text(text.replace(self.token, "***"),
                                  encoding="utf-8")
        except OSError:
            pass

    # -- vault scanning ------------------------------------------------------

    def scan_vault(self) -> Dict[str, List[_Note]]:
        """Walk the vault's category folders and parse every curated note.
        Returns {relative category dir -> [notes]}. Never raises."""
        if self.vault is None or not self.vault.is_dir():
            return {}
        found: Dict[str, List[_Note]] = {}
        for dirpath, dirnames, filenames in os.walk(self.vault):
            dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS)
            rel_dir = os.path.relpath(dirpath, self.vault).replace(os.sep, "/")
            if rel_dir == ".":  # vault root: not a category folder
                continue
            notes = []
            for fn in sorted(filenames):
                if not fn.lower().endswith(".md"):
                    continue
                rel_path = f"{rel_dir}/{fn}"
                if ".." in rel_path.split("/"):  # paranoid; os.walk is safe
                    continue
                notes.append(_parse_note(Path(dirpath) / fn, rel_path))
            if notes:
                found[rel_dir] = notes
        return found

    # -- README (the emoji directory) ----------------------------------------

    @staticmethod
    def _top_heading(top: str) -> str:
        return f"{emoji_for(top)} {display_name(top)}"

    @staticmethod
    def _sub_heading(sub: str, count: int) -> str:
        components = [c for c in sub.split("/") if c]
        disp = " / ".join(display_name(c) for c in components)
        return f"{emoji_for(*components)} {disp} ({count})"

    @staticmethod
    def _entry_line(note: _Note) -> str:
        if note.url:
            line = f"- 📦 [{note.display}]({note.url})"
        else:
            line = f"- 📦 {note.display}"
        if note.tldr:
            line += f" — {note.tldr}"
        if note.stars is not None:
            line += f" · ⭐ {format_stars(note.stars)}"
        if note.language:
            line += f" · 🔧 {note.language}"
        if note.tags:
            line += " · " + " ".join(f"`{t}`" for t in note.tags)
        return line

    def _build_readme(self, cats: Dict[str, List[_Note]]) -> str:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        entries = sum(len(notes) for notes in cats.values())

        # group category dirs: top component -> sub path ("") -> notes
        tops: Dict[str, Dict[str, List[_Note]]] = {}
        for rel in sorted(cats):
            parts = rel.split("/")
            top, sub = parts[0], "/".join(parts[1:])
            tops.setdefault(top, {}).setdefault(sub, []).extend(cats[rel])
        top_order = sorted(tops, key=lambda t: (display_name(t).lower(), t))

        def sub_display(sub: str) -> str:
            return " / ".join(display_name(c) for c in sub.split("/") if c)

        def sub_order(top: str) -> List[str]:
            # alphabetical by DISPLAY name (the emoji would win otherwise)
            return sorted((s for s in tops[top] if s),
                          key=lambda s: (sub_display(s).lower(), s))

        def sort_key(n: _Note):
            stars = n.stars if n.stars is not None else -1
            return (-stars, n.display.lower(), n.rel_path)

        out: List[str] = [
            "# 🗂️ Good Repos",
            "",
            "> ✨ A curated directory of quality GitHub repositories — auto-maintained by",
            "> [GitCurator](https://github.com/assadigit/GitCurator).",
            "",
            f"📊 **{entries} {'repo' if entries == 1 else 'repos'}** · "
            f"🗂️ **{len(cats)} {'category' if len(cats) == 1 else 'categories'}** · "
            f"🕰️ Updated {today}",
            "",
            "## 📇 Contents",
            "",
        ]
        for top in top_order:
            heading = self._top_heading(top)
            out.append(f"- [{heading}](#{github_anchor(heading)})")
            for sub in sub_order(top):
                count = len(tops[top][sub])
                sheading = self._sub_heading(sub, count)
                out.append(f"  - [{emoji_for(*sub.split('/'))} {sub_display(sub)}]"
                           f"(#{github_anchor(sheading)})")
        out.append("")

        for top in top_order:
            heading = self._top_heading(top)
            out += [f"## {heading}", "",
                    f"_Curated repositories for the {_intro_name(display_name(top))}._",
                    ""]
            direct = tops[top].get("", [])
            if direct:
                out += [self._entry_line(n) for n in sorted(direct, key=sort_key)]
                out.append("")
            for sub in sub_order(top):
                notes = tops[top][sub]
                out.append(f"### {self._sub_heading(sub, len(notes))}")
                out.append("")
                out += [self._entry_line(n) for n in sorted(notes, key=sort_key)]
                out.append("")

        out += [
            "---",
            "",
            "_🤖 This directory is regenerated automatically after every GitCurator "
            "curation run. Browsing the category folders gives you the full curated "
            "notes._",
            "",
        ]
        return "\n".join(out)

    # -- staging -------------------------------------------------------------

    def _new_staging(self, register: bool) -> Optional[Path]:
        try:
            staging = Path(tempfile.mkdtemp(prefix="goodrepos-"))
        except OSError as e:
            self._log(f"GoodRepos: could not create a staging directory: {e}",
                      "warning")
            return None
        if register:
            _STAGING_CLEANUP.append(staging)
        return staging

    def _clean_staging(self, staging: Path) -> None:
        """Remove everything except .git so the tree can be regenerated
        without stale files surviving from a previous publish."""
        try:
            children = list(staging.iterdir())
        except OSError:
            return
        for child in children:
            if child.name == ".git":
                continue
            try:
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink()
            except OSError:
                pass

    def _build_tree(self, staging: Path,
                    cats: Dict[str, List[_Note]]) -> int:
        """Copy the curated notes at their vault-relative paths, the root
        _index.md + links_manifest.json when present, and write README.md.
        Returns the number of files written."""
        written = 0
        for rel, notes in cats.items():
            for note in notes:
                dest = staging.joinpath(*note.rel_path.split("/"))
                try:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(note.path, dest)
                    written += 1
                except OSError as e:
                    self._log(f"GoodRepos: could not copy {note.rel_path}: {e}",
                              "warning")
        for name in ("_index.md", "links_manifest.json"):
            src = self.vault / name if self.vault else None
            if src and src.is_file():
                try:
                    shutil.copy2(src, staging / name)
                    written += 1
                except OSError as e:
                    self._log(f"GoodRepos: could not copy {name}: {e}", "warning")
        try:
            (staging / "README.md").write_text(self._build_readme(cats),
                                               encoding="utf-8")
            written += 1
        except OSError as e:
            self._log(f"GoodRepos: could not write README.md: {e}", "warning")
        return written

    # -- the publish ----------------------------------------------------------

    def _publish(self, run_summary: Dict[str, Any],
                 dry_run: bool) -> PublishResult:
        if not self.enabled:
            return PublishResult(skipped_reason="GoodRepos is disabled in config")
        if self.vault is None or not self.vault.is_dir():
            return PublishResult(error=f"vault directory not found: {self.vault}")

        processed = run_summary.get("processed")
        total = run_summary.get("total")
        if isinstance(processed, int) and isinstance(total, int) and total > 0:
            self._log(f"GoodRepos: publishing after a curation run that "
                      f"processed {processed}/{total} repos", "info")

        cats = self.scan_vault()
        entries = sum(len(notes) for notes in cats.values())
        base: Dict[str, Any] = {
            "entries": entries,
            "categories": len(cats),
            "repo_name": self._repo_full or self.repo_name,
            "repo_url": self._remote_url,
        }
        if not cats:
            return PublishResult(
                skipped_reason="vault contains no curated notes", **base)

        # -- staging tree (fresh mkdtemp, OUTSIDE any repo) ----------------
        if dry_run:
            staging = self._new_staging(register=False)
        else:
            if self._staging is None:
                self._staging = self._new_staging(register=True)
            staging = self._staging
            self._clean_staging(staging)
        if staging is None:
            return PublishResult(error="could not create a staging directory",
                                 **base)
        base["staging_dir"] = str(staging)

        written = self._build_tree(staging, cats)

        if dry_run:
            return PublishResult(published=True, files_changed=written,
                                 dry_run=True, **base)

        # -- git: init + identity --------------------------------------------
        rc, out = self._git("init", "-b", "main")
        if rc != 0:
            # older git without -b: plain init + move HEAD to main
            rc2, out2 = self._git("init")
            if rc2 == 0:
                self._git("symbolic-ref", "HEAD", "refs/heads/main")
            else:
                return PublishResult(error=f"git init failed: {out2 or out}",
                                     **base)
        self._git("config", "user.name", IDENTITY_NAME)
        self._git("config", "user.email", IDENTITY_EMAIL)

        # -- remote prep (only when pushing is possible) ---------------------
        if self.auto_push:
            ok, err = self._ensure_repo()
            if not ok:
                return PublishResult(error=err, **base)
            self._fetch_history()

        # -- stage + skip-when-unchanged ---------------------------------------
        self._git("add", "-A", "--", ".")
        rc, porcelain = self._git("status", "--porcelain")
        rc_head, _ = self._git("rev-parse", "--verify", "-q", "HEAD")
        dirty = [ln for ln in porcelain.splitlines() if ln.strip()] if rc == 0 else []

        if not dirty and rc_head == 0:
            return PublishResult(
                skipped_reason="directory unchanged since the last publish",
                **base)

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        message = f"publish: {entries} repos across {len(cats)} categories — {ts}"
        rc, out = self._git("-c", "commit.gpgsign=false", "commit",
                            "-m", message, "--quiet")
        if rc != 0:
            return PublishResult(error=f"git commit failed: {out}", **base)
        _, sha = self._git("rev-parse", "--short=7", "HEAD")

        result = PublishResult(
            published=True,
            commit_sha=sha or None,
            commit_message=message,
            files_changed=len(dirty),
            **base,
        )

        if not self.auto_push:
            self._log("GoodRepos: committed locally — no GitHub token configured, "
                      "push skipped (set the token in Settings to enable pushing)",
                      "warning")
            return result

        # -- push via one-time token URL (never in .git/config, never logged) --
        self._git("remote", "remove", "origin")  # defensive: no token-less remotes
        rc, out = self._git("push", self._token_url(), "HEAD:refs/heads/main",
                            timeout=PUSH_TIMEOUT)
        self._git("remote", "remove", "origin")
        if rc != 0:
            result.error = f"push failed: {out[:300]}"
            self._log(f"GoodRepos: {result.error}", "warning")
        else:
            result.pushed = True
            self._log(
                f"GoodRepos: published {entries} repos across {len(cats)} "
                f"categories → {self._repo_full} ({sha})",
                "success",
            )
        return result

    def publish(self, run_summary: Optional[Dict[str, Any]] = None,
                *, dry_run: bool = False) -> PublishResult:
        """Build + commit + best-effort push of the public directory.
        NEVER raises — always returns a PublishResult.

        ``dry_run=True`` builds the staging tree + README without running any
        git command and keeps the temp dir alive (reported as staging_dir)
        so it can be inspected."""
        started = time.monotonic()
        try:
            result = self._publish(run_summary or {}, dry_run)
        except Exception as e:  # a publishing failure must never bubble up
            result = PublishResult(error=f"{type(e).__name__}: {e}")
        result.duration_ms = int((time.monotonic() - started) * 1000)
        return result

    # -- introspection --------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        """Pure vault scan snapshot for UIs — no git calls, no network."""
        cats = self.scan_vault()
        tree = {rel: len(notes) for rel, notes in sorted(cats.items())}
        return {
            "vault_path": str(self.vault) if self.vault else None,
            "exists": bool(self.vault and self.vault.is_dir()),
            "enabled": self.enabled,
            "auto_push": self.auto_push,
            "repo_name": self._repo_full or self.repo_name,
            "repo_url": self._remote_url,
            "entries": sum(tree.values()),
            "categories": len(tree),
            "tree": tree,
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        }


# ---------------------------------------------------------------------------
# Config bridge (used by main.py — GUI + headless)
# ---------------------------------------------------------------------------

def publish_from_config(config: Dict[str, Any],
                        run_summary: Optional[Dict[str, Any]] = None,
                        log: Optional[LogFn] = None) -> PublishResult:
    """Build a GoodRepos from the app's config dict and publish once.

    Reads: vault_path, github_token, and the ``goodrepos`` section:
      { "enabled": true, "repo_name": "good-repos", "auto_push": true }
    """
    cfg = (config or {}).get("goodrepos") or {}
    if not isinstance(cfg, dict):
        cfg = {}
    publisher = GoodRepos(
        vault_path=(config or {}).get("vault_path") or "",
        token=(config or {}).get("github_token") or "",
        repo_name=cfg.get("repo_name") or DEFAULT_REPO_NAME,
        auto_push=cfg.get("auto_push", True),
        enabled=cfg.get("enabled", True),
        log=log,
    )
    return publisher.publish(run_summary)


# ---------------------------------------------------------------------------
# Standalone CLI (manual publishes + inspection)
# ---------------------------------------------------------------------------

def _main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="GoodRepos — publish the curated vault as a public "
                    "'good-repos' GitHub directory (README + category notes)")
    parser.add_argument("--vault", required=True,
                        help="path to the Obsidian vault")
    parser.add_argument("--repo-name", default=DEFAULT_REPO_NAME,
                        help=f"public directory repo name (default: {DEFAULT_REPO_NAME})")
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""),
                        help="GitHub token (default: env GITHUB_TOKEN)")
    parser.add_argument("--no-push", action="store_true",
                        help="commit the directory in the staging dir, never push")
    parser.add_argument("--dry-run", action="store_true",
                        help="build the directory + README into a temp dir and "
                             "print a summary WITHOUT running any git command; "
                             "the temp dir is kept alive (path reported as "
                             "staging_dir in --json output) so you can inspect it")
    parser.add_argument("--json", action="store_true",
                        help="print the result as JSON")
    parser.add_argument("--status", action="store_true",
                        help="print the vault scan status and exit")
    args = parser.parse_args(argv)

    def log(msg: str, level: str = "info") -> None:
        print(f"[goodrepos] [{level}] {msg}", file=sys.stderr, flush=True)

    publisher = GoodRepos(
        vault_path=args.vault,
        token=args.token,
        repo_name=args.repo_name,
        auto_push=not args.no_push,
        log=log,
    )
    if args.status:
        print(json.dumps(publisher.status(), indent=2))
        return 0

    result = publisher.publish(dry_run=args.dry_run)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(result.describe())
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(_main())
