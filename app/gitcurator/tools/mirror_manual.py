#!/usr/bin/env python3
"""Mirror the two machine vaults into the Manual Notes vault's Library.

v0.14.0 — SPEC §6 Phase 5. One-way, read-only-for-you sync::

    <manual_vault>/Library/GitHub Projects/…   ← your GitHub vault
    <manual_vault>/Library/Websites/…          ← your Websites vault

Every mirror copy carries a ``mirror_of`` front-matter marker and a
read-only banner. Only marker files under ``Library/`` are ever touched;
nothing outside ``Library/`` is created, changed or deleted (tested).

DRY-RUN BY DEFAULT: printing and reporting the plan only. Add
``--apply`` to perform the sync — every safety check re-runs first.

Owner review (SPEC): run against a COPY of your Manual Notes vault::

    python gitcurator/tools/mirror_manual.py --manual-vault "D:\\Manual Notes copy"
    python gitcurator/tools/mirror_manual.py --manual-vault "D:\\Manual Notes copy" --apply

Vault paths come from ``config.json`` (``vault_path``,
``website_vault_path``, ``manual_vault_path``); each can be overridden
on the command line. Exit codes: 0 success, 1 manual vault not set,
2 safety refusal / config error, 3 report write failed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

# --- make the gitcurator package importable (same bootstrap as the other tools)
_HERE = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.dirname(os.path.dirname(_HERE))
if _APP not in sys.path:
    sys.path.insert(0, _APP)

from gitcurator.constants import APP_DIR                      # noqa: E402
from gitcurator.core.mirror import (                          # noqa: E402
    GITHUB_TREE, LIBRARY_FOLDER, WEBSITES_TREE, MirrorError, run_mirror)

DEFAULT_REPORT_DIR = os.path.join(APP_DIR, "reports", "mirror")


def _is_inside(child: str, parent: str) -> bool:
    try:
        child_r = os.path.realpath(child)
        parent_r = os.path.realpath(parent)
        return os.path.commonpath([child_r, parent_r]) == parent_r
    except (ValueError, OSError):
        return False


def _load_config() -> dict:
    cfg_path = os.path.join(APP_DIR, "config.json")
    try:
        with open(cfg_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mirror_manual.py",
        description="One-way Library mirror into the Manual Notes vault "
                    "(dry-run by default; --apply performs it).")
    p.add_argument("--apply", action="store_true",
                   help="PERFORM the sync (default is a dry run that "
                        "writes nothing)")
    p.add_argument("--manual-vault", default="",
                   help="override manual_vault_path (e.g. a COPY of your "
                        "Manual Notes vault for the review run)")
    p.add_argument("--github-vault", default="",
                   help="override vault_path (the GitHub Projects vault)")
    p.add_argument("--websites-vault", default="",
                   help="override website_vault_path")
    p.add_argument("--out", default="",
                   help="report file path (default: "
                        "app/reports/mirror/mirror-report-<timestamp>.txt)")
    p.add_argument("--quiet", action="store_true",
                   help="print only the summary, not per-file action lines")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cfg = _load_config()

    manual = (args.manual_vault or cfg.get("manual_vault_path") or "").strip()
    github = (args.github_vault or cfg.get("vault_path") or "").strip()
    websites = (args.websites_vault
                or cfg.get("website_vault_path") or "").strip()

    if not manual:
        print("ERROR: the Manual Notes vault path is not set. Use the GUI's "
              "📁 Vault page, config.json (manual_vault_path), or "
              "--manual-vault.")
        return 1

    lines: list = []

    def log(msg: str) -> None:
        lines.append(msg)
        if not args.quiet:
            print(msg)

    try:
        plan = run_mirror(manual, github, websites,
                          apply=args.apply, log=log)
    except MirrorError as exc:
        print(f"ERROR: {exc}")
        return 2

    # ---- report file (never inside any of the three vaults) ----
    if args.out:
        report_path = os.path.abspath(args.out)
    else:
        os.makedirs(DEFAULT_REPORT_DIR, exist_ok=True)
        report_path = os.path.join(
            DEFAULT_REPORT_DIR,
            "mirror-report-%s.txt" % time.strftime("%Y%m%d-%H%M%S"))
    for label, vault in (("manual", manual), ("GitHub", github),
                         ("Websites", websites)):
        if vault and _is_inside(report_path, vault):
            print(f"ERROR: the report file must be OUTSIDE the {label} "
                  f"vault — this tool never writes anything into a vault "
                  f"except the Library/ mirror.\n  report: {report_path}"
                  f"\n  vault:   {vault}")
            return 2
    report = plan.render_report()
    try:
        os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report)
    except OSError as exc:
        print(f"ERROR: could not write the report: {exc}")
        return 3

    print()
    print(report if args.quiet else _summary_of(plan))
    print(f"Report: {report_path}")
    return 0


def _summary_of(plan) -> str:
    mode = "APPLIED" if plan.applied else "DRY RUN (nothing written)"
    rows = [f"Library mirror — {mode}"]
    for label in (GITHUB_TREE, WEBSITES_TREE):
        tree = plan.trees.get(label)
        if tree is None:
            continue
        state = ("" if tree.vault_present
                 else " — vault not found, skipped" if tree.vault_path
                 else " — vault not set, skipped")
        rows.append(
            f"  {label}: {tree.sources} library notes{state} · "
            f"created {len(tree.creates)}, updated {len(tree.updates)}, "
            f"moved {len(tree.moves)}, deleted {len(tree.deletes)}, "
            f"kept {tree.keeps}"
            + (f", conflicts {len(tree.conflicts)}" if tree.conflicts else ""))
    rows.append(f"  Totals: {plan.total_changes} change(s) "
                + ("applied." if plan.applied else "needed — run with "
                   "--apply to perform them."))
    return "\n".join(rows)


if __name__ == "__main__":
    sys.exit(main())
