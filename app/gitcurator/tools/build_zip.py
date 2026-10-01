#!/usr/bin/env python3
"""Build the Windows first-run zip (v0.14.1 — the owner's local test build).

Usage (from the app/ folder of a GitCurator checkout):

    python gitcurator/tools/build_zip.py
    python gitcurator/tools/build_zip.py --out C:\\some\\where\\GitCurator.zip

What lands in the zip (one root folder, ``GitCurator/``):

    GitCurator/main.py, gitcurator/…, prompts/, taxonomy/, assets/,
    the six .bat launchers, README.md, WINDOWS-QUICKSTART.md,
    config.example.json, requirements.txt, VERSION, about_me.md,
    system_prompt.txt, gitcurator-cli.sh.

Deliberately EXCLUDED (developer-only, too big, or optional infra):

    app/tests/            the 818-case suite (runs in CI, not on your PC)
    app/_attic/           historical scripts (kept as a guard; folder was
                          removed from the repo at v0.25.0)
    app/cloudflare-bot/   the optional Worker + web dashboard (repo only)

NEVER included, enforced (the build refuses to start if any of these
is tracked under app/ — defense in depth against an accidental
``git add -f``):

    config.json, config.local.json, installer.config.json, cache.db,
    error_outbox.db, *.session*, .env*

Windows-safety rules applied to every ``.bat`` in the zip:

    * pure ASCII (a non-ASCII .bat fails the build — the v0.09.1
      codepage bug class can never ship again), and
    * CRLF line endings (normalized on the way in, idempotent).

The build is deterministic: fixed entry order (sorted), fixed
timestamps and attributes — two builds from the same commit are
byte-identical (sha256-stable), so a released zip can always be
reproduced from its tag.

Exit codes: 0 = built; 2 = fatal (not a git checkout, git missing,
banned file tracked, nothing to zip, write error).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import zipfile

if __package__ in (None, ""):  # pragma: no cover - direct-script bootstrap
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from gitcurator.constants import APP_DIR

# ---------------------------------------------------------------------------
# Policy tables
# ---------------------------------------------------------------------------

# Tracked paths (repo-root-relative, forward slashes) that never ship.
EXCLUDE_PREFIXES = (
    "app/tests/",
    "app/_attic/",
    "app/cloudflare-bot/",
)
EXCLUDE_EXACT: set[str] = set()

# If any of these is tracked under app/ the build refuses to run.
BANNED_EXACT = {
    "app/config.json",
    "app/config.local.json",
    "app/installer.config.json",
    "app/cache.db",
    "app/error_outbox.db",
    "app/cloudflare-bot/installer.config.json",
}
BANNED_SUFFIXES = (".session", ".session-journal")
BANNED_NAMES = {".env", ".env.local"}

ZIP_ROOT = "GitCurator"
# Fixed timestamp for every entry — keeps builds byte-identical.
FIXED_DATE_TIME = (2026, 9, 29, 0, 0, 0)
FIXED_EXTERNAL_ATTR = 0o644 << 16


# ---------------------------------------------------------------------------
# Collection (git ls-files — only committed files, never local junk)
# ---------------------------------------------------------------------------

def collect_tracked_files(repo_root: str) -> list[str]:
    """Return the repo-root-relative tracked paths under app/, sorted."""
    try:
        proc = subprocess.run(
            ["git", "-C", repo_root, "ls-files", "-z", "--", "app"],
            capture_output=True, check=True, timeout=60,
        )
    except FileNotFoundError:
        raise SystemExit("[build_zip] fatal: git is not installed / not on PATH")
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"[build_zip] fatal: git ls-files failed: {exc.stderr.decode(errors='replace').strip()}")
    paths = [p for p in proc.stdout.decode("utf-8", "surrogateescape").split("\0") if p]
    return sorted(paths)


def is_excluded(repo_rel: str) -> bool:
    """Developer-only / optional-infra files that never ship."""
    if repo_rel in EXCLUDE_EXACT:
        return True
    return repo_rel.startswith(EXCLUDE_PREFIXES)


def is_banned(repo_rel: str) -> bool:
    """Secrets / live state that must never even be tracked under app/."""
    if repo_rel in BANNED_EXACT:
        return True
    name = repo_rel.rsplit("/", 1)[-1]
    if name in BANNED_NAMES:
        return True
    return name.endswith(BANNED_SUFFIXES)


# ---------------------------------------------------------------------------
# Zip writing (deterministic; .bat files normalized to ASCII+CRLF)
# ---------------------------------------------------------------------------

def _bat_bytes(data: bytes) -> bytes:
    """Normalize a .bat: refuse non-ASCII, force CRLF (idempotent)."""
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError as exc:
        raise SystemExit(
            f"[build_zip] fatal: a .bat file is not pure ASCII "
            f"(the v0.09.1 codepage bug class): {exc}"
        )
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    # A trailing newline keeps the final line from gluing onto the next.
    if lines and lines[-1] == "":
        lines.pop()
    return ("\r\n".join(lines) + "\r\n").encode("ascii")


def build_zip(sources: list[tuple[str, str]], out_path: str, version: str,
              log=print) -> str:
    """Write the zip. ``sources`` = [(repo-root-relative path, absolute src)].

    Returns the out_path. Deterministic: same sources + version → same bytes.
    """
    entries: list[tuple[str, bytes]] = []
    for repo_rel, src in sources:
        arcname = f"{ZIP_ROOT}/{repo_rel[len('app/'):]}".replace("\\", "/")
        with open(src, "rb") as fh:
            data = fh.read()
        if arcname.lower().endswith(".bat"):
            data = _bat_bytes(data)
        entries.append((arcname, data))
    # VERSION is stamped from the repo, not copied as a tracked app file.
    entries.append((f"{ZIP_ROOT}/VERSION", (version.strip() + "\n").encode("ascii")))
    entries.sort(key=lambda item: item[0])

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for arcname, data in entries:
            info = zipfile.ZipInfo(arcname, date_time=FIXED_DATE_TIME)
            info.external_attr = FIXED_EXTERNAL_ATTR
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)

    total_in = sum(len(data) for _name, data in entries)
    sha = hashlib.sha256()
    with open(out_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            sha.update(chunk)
    log(f"[build_zip] {out_path}")
    log(f"[build_zip] {len(entries)} files, {total_in:,} bytes in, "
        f"{os.path.getsize(out_path):,} bytes zipped")
    log(f"[build_zip] sha256 {sha.hexdigest()}")
    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="build_zip",
        description="Build the deterministic Windows first-run zip from a GitCurator checkout.",
    )
    default_out = os.path.join(APP_DIR, "reports", "dist",
                               "GitCurator-windows.zip")  # version appended below
    parser.add_argument("--out", default=None,
                        help="output .zip path (default: app/reports/dist/GitCurator-vVERSION-windows.zip)")
    args = parser.parse_args(argv)

    repo_root = os.path.dirname(APP_DIR)
    if not os.path.isdir(os.path.join(repo_root, ".git")):
        print(f"[build_zip] fatal: {repo_root} is not a git checkout "
              f"(the zip is built from tracked files only)")
        return 2

    version_path = os.path.join(repo_root, "VERSION")
    if not os.path.isfile(version_path):
        print("[build_zip] fatal: VERSION file not found at the repo root")
        return 2
    with open(version_path, "r", encoding="utf-8") as fh:
        version = fh.read().strip()

    tracked = collect_tracked_files(repo_root)

    banned = [p for p in tracked if is_banned(p)]
    if banned:
        print("[build_zip] fatal: banned file(s) tracked under app/ — "
              "untrack them before building a distributable:")
        for p in banned:
            print(f"  - {p}")
        return 2

    sources = []
    for repo_rel in tracked:
        if is_excluded(repo_rel):
            continue
        src = os.path.join(repo_root, repo_rel)
        if not os.path.isfile(src):
            print(f"[build_zip] fatal: tracked file missing on disk: {repo_rel}")
            return 2
        sources.append((repo_rel, src))
    if not sources:
        print("[build_zip] fatal: nothing to zip (empty source list)")
        return 2

    out_path = args.out or default_out.replace(
        "GitCurator-windows.zip", f"GitCurator-v{version}-windows.zip")
    try:
        build_zip(sources, out_path, version)
    except SystemExit as exc:
        print(str(exc))
        return 2
    print(f"[build_zip] version {version} — dry-run nothing, sign nothing, "
          f"just the app. Test locally: unzip, 1-INSTALL.bat, GitCurator.bat")
    return 0


if __name__ == "__main__":
    sys.exit(main())
