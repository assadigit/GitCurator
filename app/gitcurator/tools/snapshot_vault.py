#!/usr/bin/env python3
"""
snapshot_vault.py — zip a vault into a timestamped backup (Phase 0, v0.09.5).

Before any risky operation on a real vault (SPEC.md non-negotiable #1),
take a snapshot first: this tool zips the ENTIRE vault — notes, _moc,
_inbox, attachments, even the .obsidian settings — into a single
timestamped .zip file written OUTSIDE the vault. Restoring is a plain
"extract here" over the vault folder.

The tool refuses to write the zip inside the vault it is snapshotting.

Usage (from the app/ folder, or from anywhere via -m):
    python tools/snapshot_vault.py "C:\\path\\to\\vault"
    python tools/snapshot_vault.py "C:\\path\\to\\vault" --out "D:\\backups"
    python -m gitcurator.tools.snapshot_vault "C:\\path\\to\\vault"

Windows-friendly: os.path only, UTF-8 filenames handled by zipfile,
no symlinks followed beyond what os.walk reports. Pure standard library.
"""

import argparse
import os
import sys
import time
import zipfile
from datetime import datetime

# ---------------------------------------------------------------------------
# Bootstrap: make the app/ folder importable no matter how we are launched
# ---------------------------------------------------------------------------
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)

from gitcurator.constants import APP_DIR
from gitcurator.core.storage import unique_path

# ===========================================================================
# CONFIGURATION (safe to edit)
# ===========================================================================
# Default folder for snapshots — always OUTSIDE the vault (inside the app
# folder, next to config.json).
DEFAULT_SNAPSHOT_DIR = os.path.join(APP_DIR, "reports", "snapshots")

# Compression: ZIP_DEFLATED needs zlib (in every standard Python build).
ZIP_COMPRESSION = zipfile.ZIP_DEFLATED
# ===========================================================================


def _is_inside(child: str, parent: str) -> bool:
    """True when path ``child`` lies inside folder ``parent`` (or equals it)."""
    try:
        child_r = os.path.realpath(child)
        parent_r = os.path.realpath(parent)
        return os.path.commonpath([child_r, parent_r]) == parent_r
    except (ValueError, OSError):
        return False


def snapshot_vault(vault_path: str, out_dir: str = None) -> dict:
    """Zip ``vault_path`` into a timestamped file in ``out_dir``.

    Returns a dict: {zip_path, file_count, byte_count, duration_s, skipped}.
    Raises ValueError when out_dir lies inside the vault, or when the vault
    does not exist. Individual unreadable files are skipped and reported,
    never fatal.
    """
    vault = os.path.abspath(vault_path)
    if not os.path.isdir(vault):
        raise ValueError(f"vault folder not found: {vault}")
    out_dir = os.path.abspath(out_dir or DEFAULT_SNAPSHOT_DIR)
    if _is_inside(out_dir, vault):
        raise ValueError("the snapshot folder must be OUTSIDE the vault — "
                         "a zip cannot contain itself")

    os.makedirs(out_dir, exist_ok=True)
    vault_name = os.path.basename(vault.rstrip(os.sep)) or "vault"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    zip_path = unique_path(os.path.join(out_dir, f"{vault_name}_snapshot_{stamp}.zip"))

    started = time.time()
    file_count = 0
    byte_count = 0
    skipped = []

    with zipfile.ZipFile(zip_path, 'w', ZIP_COMPRESSION) as zf:
        for root, dirs, files in os.walk(vault):
            # Record empty directories too, so a restore recreates the
            # full folder structure.
            for d in dirs:
                dpath = os.path.join(root, d)
                arc = os.path.relpath(dpath, vault).replace(os.sep, '/')
                try:
                    zf.write(dpath, arc)
                except Exception:
                    pass  # a directory entry is cosmetic; files matter
            for fname in files:
                fpath = os.path.join(root, fname)
                arc = os.path.relpath(fpath, vault).replace(os.sep, '/')
                # Defensive: never zip the archive into itself (only
                # possible with a weird --out; the guard above already
                # refuses in-vault targets).
                if os.path.abspath(fpath) == os.path.abspath(zip_path):
                    continue
                try:
                    zf.write(fpath, arc)
                    file_count += 1
                    byte_count += os.path.getsize(fpath)
                except Exception as exc:
                    skipped.append(f"{arc} ({exc})")

    return {
        'zip_path': zip_path,
        'file_count': file_count,
        'byte_count': byte_count,
        'duration_s': round(time.time() - started, 2),
        'skipped': skipped,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="snapshot_vault",
        description="Zip a vault into a timestamped backup file written "
                    "OUTSIDE the vault. Restore = unzip over the vault folder.")
    parser.add_argument("vault", help="path to the vault folder to snapshot")
    parser.add_argument("--out", default=None,
                        help=f"snapshot folder (default: {DEFAULT_SNAPSHOT_DIR}; "
                             "must be outside the vault)")
    args = parser.parse_args(argv)

    try:
        result = snapshot_vault(args.vault, args.out)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2

    print(f"Snapshot created: {result['zip_path']}")
    print(f"  Files:      {result['file_count']}")
    print(f"  Size:       {result['byte_count']:,} bytes "
          f"(uncompressed) in {result['duration_s']}s")
    if result['skipped']:
        print(f"  ⚠️ Skipped {len(result['skipped'])} unreadable file(s):")
        for s in result['skipped'][:10]:
            print(f"     - {s}")
        if len(result['skipped']) > 10:
            print(f"     … and {len(result['skipped']) - 10} more")
    return 0


if __name__ == '__main__':
    sys.exit(main())
