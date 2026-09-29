"""One-way library mirror into the Manual Notes vault — SPEC §6 Phase 5.

Syncs every library note (a .md file WITH a ``source:`` line — the same
rule VaultIndex and ``note_state.scan_vault`` use) from the two machine
vaults into read-only copies under the owner's Manual Notes vault::

    <manual_vault>/Library/GitHub Projects/…   ← the GitHub vault tree
    <manual_vault>/Library/Websites/…          ← the Websites vault tree

Guarantees (proven in ``tests/test_phase5.py``):

- NEVER creates, changes or deletes anything outside those two Library
  folders. Target paths are built from validated components (no ``..``,
  no absolute paths, no drive letters, no separators) and re-verified by
  realpath containment before every single write and delete.
- Mirror copies carry a ``mirror_of`` front-matter marker plus a
  read-only banner. ONLY files carrying that marker are ever updated or
  deleted, and only under Library/. Any other file in the way of a
  mirror copy is reported as a conflict and left untouched.
- Follows moves: sources are matched by their raw ``source`` value, so a
  note the owner moved between category folders re-mirrors at the new
  location and the stale copy is removed (SPEC §4.4 moves-as-corrections
  propagate to the owner's view for free).
- Idempotent: a second run over unchanged sources performs zero writes.
- Dry-run by default: ``run_mirror(..., apply=False)`` plans everything
  and writes nothing. A real run needs ``apply=True`` (the CLI tool:
  ``tools/mirror_manual.py --apply``).

Refuses to run (``MirrorError``) when the manual vault is not set, or
when it overlaps either machine vault in EITHER direction (manual
inside a machine vault, or a machine vault inside manual) — both would
mean writing the mirror into a machine-owned tree.

Deliberately PyQt-free and database-free: the mirror is a pure function
of the files on disk, so it can never disagree with the vaults.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from . import dryrun
from .linking import preserve_related_block   # v0.16.0 — Phase 6 carry-over
from .storage import atomic_write_text

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LIBRARY_FOLDER = "Library"
GITHUB_TREE = "GitHub Projects"
WEBSITES_TREE = "Websites"

#: Front-matter key that marks a file as a machine-written mirror copy.
#: Only files carrying this marker are ever updated or deleted (and only
#: under Library/). SPEC §6 Phase 5.
MIRROR_KEY = "mirror_of"

MIRROR_BANNERS = {
    GITHUB_TREE: (
        "> [!warning] Read-only mirror of a note in the GitHub Projects "
        "vault — GitCurator keeps this copy in sync and overwrites edits "
        "made here."
    ),
    WEBSITES_TREE: (
        "> [!warning] Read-only mirror of a note in the Websites vault — "
        "GitCurator keeps this copy in sync and overwrites edits made here."
    ),
}

# The same special folders note_state skips. A note inside one of these
# is not a library note and is never mirrored. (Substring match on the
# walked path.) v0.20.0: _missing placeholders are NOT library notes —
# unlike VaultIndex, which DOES index them (they are the 404 dedupe key).
SKIPPED_FOLDER_MARKS = ('_moc', '_inbox', '_missing', 'attachments',
                        '.obsidian')

_SOURCE_RE = re.compile(r'^source:\s*(.+)$', re.MULTILINE)
_MIRROR_RE = re.compile(r'^' + re.escape(MIRROR_KEY) + r':\s*(.+)$',
                        re.MULTILINE)

# GitHub-note banner references are vault-root-relative
# (``attachments/banners/x.png``) and can never resolve inside the Manual
# vault — placing the images there would mean writing OUTSIDE Library/
# (forbidden). The reference is decorative, so mirror copies drop these
# two lines instead of carrying a permanently broken image link
# (decision log, Phase 5).
_COVER_LINE_RE = re.compile(r'^cover: attachments/banners/\S*[^\r\n]*\r?\n?',
                            re.MULTILINE)
_BANNER_EMBED_RE = re.compile(
    r'^!\[banner\]\(attachments/banners/[^)\s]*\)[ \t]*\r?\n(\r?\n)?',
    re.MULTILINE)


class MirrorError(Exception):
    """Raised when the mirror must refuse to run (safety) — never mid-write."""


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------

def _is_inside(child: str, parent: str) -> bool:
    """True when path ``child`` lies inside folder ``parent`` (or equals it).

    Same predicate as ``tools/scan_vault_edits._is_inside`` (realpath +
    commonpath), kept local so core stays importable without the tools.
    """
    try:
        child_r = os.path.realpath(child)
        parent_r = os.path.realpath(parent)
        return os.path.commonpath([child_r, parent_r]) == parent_r
    except (ValueError, OSError):
        return False


def _safe_join(root: str, rel: str) -> str:
    """Join ``root`` + ``rel`` with every path-traversal defense on.

    Rejects ``..`` / empty / dot components, absolute ``rel`` values,
    backslashes and drive letters (Windows), then re-verifies by realpath
    that the result is still inside ``root``. Raises MirrorError on any
    violation — callers never need to check the result again.
    """
    rel_norm = (rel or '').replace('\\', '/')
    if rel_norm.startswith('/'):
        raise MirrorError(
            f"unsafe mirror path {rel!r}: absolute paths are not allowed")
    parts = rel_norm.split('/')
    for part in parts:
        if part in ('', '.', '..'):
            raise MirrorError(
                f"unsafe mirror path {rel!r}: empty or parent-reference "
                f"component {part!r}")
        if ':' in part:
            raise MirrorError(
                f"unsafe mirror path {rel!r}: drive-letter-like component "
                f"{part!r}")
    target = os.path.join(root, *parts)
    if not _is_inside(target, root):
        raise MirrorError(
            f"unsafe mirror path {rel!r}: resolves outside the Library tree")
    return target


def check_vault_paths(manual_vault: str, github_vault: str,
                      websites_vault: str, apply: bool = False) -> None:
    """Refuse to run before anything is read or written (SPEC §6 Phase 5).

    - the manual vault must be set;
    - it must not overlap either machine vault in EITHER direction (the
      spec names "equals or contains"; a manual vault INSIDE a machine
      vault is just as dangerous — the mirror would write into a
      machine-owned tree — so both directions refuse);
    - with ``apply=True`` the manual vault folder must already exist
      (the owner creates their own vault; the mirror only fills Library/).
    """
    manual = (manual_vault or '').strip()
    if not manual:
        raise MirrorError(
            "manual_vault_path is not set — configure it (GUI 📁 Vault page, "
            "config.json, or the tool's --manual-vault flag) first")
    for label, machine in (("GitHub Projects", (github_vault or '').strip()),
                           ("Websites", (websites_vault or '').strip())):
        if not machine:
            continue
        if _is_inside(machine, manual):
            raise MirrorError(
                f"refusing to run: the manual vault contains the {label} "
                f"vault\n  manual:  {manual}\n  machine:  {machine}")
        if _is_inside(manual, machine):
            raise MirrorError(
                f"refusing to run: the manual vault is inside the {label} "
                f"vault\n  manual:  {manual}\n  machine:  {machine}")
    if not (github_vault or '').strip() and not (websites_vault or '').strip():
        raise MirrorError(
            "nothing to mirror: neither the GitHub vault (vault_path) nor "
            "the Websites vault (website_vault_path) is set")
    if os.path.exists(manual) and not os.path.isdir(manual):
        raise MirrorError(
            f"the manual vault path is not a folder: {manual}")
    library = os.path.join(manual, LIBRARY_FOLDER)
    if os.path.exists(library) and not os.path.isdir(library):
        raise MirrorError(
            f"'{LIBRARY_FOLDER}' exists in the manual vault but is not a "
            f"folder: {library}")
    if apply and not os.path.isdir(manual):
        raise MirrorError(
            f"the manual vault folder does not exist — create it first (or "
            f"point --manual-vault at a copy): {manual}")


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

@dataclass
class SourceNote:
    path: str        # absolute path in the machine vault
    rel: str         # vault-relative, forward slashes
    source: str      # RAW source value (unquoted, exactly as written)
    text: str = ''   # full note text, loaded while planning


def _unquote(value: str) -> str:
    return value.strip().strip('"\'')


def _scan_source_notes(vault_path: str) -> Tuple[List[SourceNote], List[str],
                                                 int, int]:
    """Read-only walk of a machine vault (PyQt-free, like the backfill's).

    Returns (unique_notes, duplicate_rels, no_source_count, error_count).
    Notes are sorted by vault-relative path for deterministic plans; a
    ``source`` value seen twice keeps the first file (sorted) and reports
    the rest as duplicates — the mirror writes one copy per source.

    Same skip rule as VaultIndex / note_state: folders whose path
    contains one of SKIPPED_FOLDER_MARKS are skipped entirely (_review
    IS scanned — those are real notes).
    """
    notes: List[SourceNote] = []
    duplicate_rels: List[str] = []
    no_source = 0
    errors = 0
    if not vault_path or not os.path.isdir(vault_path):
        return notes, duplicate_rels, no_source, errors
    for root, _dirs, files in os.walk(vault_path):
        if any(mark in root for mark in SKIPPED_FOLDER_MARKS):
            continue
        for fname in files:
            if not fname.endswith('.md'):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, 'r', encoding='utf-8',
                          errors='replace') as f:
                    head = f.read(800)
            except OSError:
                errors += 1
                continue
            match = _SOURCE_RE.search(head)
            if not match:
                no_source += 1
                continue
            rel = os.path.relpath(fpath, vault_path).replace('\\', '/')
            notes.append(SourceNote(path=fpath, rel=rel,
                                    source=_unquote(match.group(1))))
    notes.sort(key=lambda n: n.rel)
    seen: Dict[str, SourceNote] = {}
    unique: List[SourceNote] = []
    for note in notes:
        if note.source in seen:
            duplicate_rels.append(note.rel)
            continue
        seen[note.source] = note
        unique.append(note)
    return unique, duplicate_rels, no_source, errors


def _scan_mirror_tree(tree_root: str) -> Tuple[Dict[str, List[str]], int, int]:
    """Index the mirror files inside one Library tree.

    Returns ({source_value: [rel paths…]}, unmarked_md_count, error_count).
    Every .md file is read — the mirror tree has NO skip rules, because
    orphan detection must see every marker file the app ever wrote there.
    Files without the marker are counted and NEVER touched.
    """
    by_source: Dict[str, List[str]] = {}
    unmarked = 0
    errors = 0
    if not tree_root or not os.path.isdir(tree_root):
        return by_source, unmarked, errors
    for root, _dirs, files in os.walk(tree_root):
        for fname in files:
            if not fname.endswith('.md'):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, 'r', encoding='utf-8',
                          errors='replace') as f:
                    head = f.read(800)
            except OSError:
                errors += 1
                continue
            rel = os.path.relpath(fpath, tree_root).replace('\\', '/')
            match = _MIRROR_RE.search(head)
            value = _unquote(match.group(1)) if match else ''
            if not value:
                unmarked += 1
                continue
            by_source.setdefault(value, []).append(rel)
    for rels in by_source.values():
        rels.sort()
    return by_source, unmarked, errors


# ---------------------------------------------------------------------------
# Mirror-note construction
# ---------------------------------------------------------------------------

def _strip_banner_refs(text: str) -> str:
    """Drop GitHub-note banner references that cannot resolve in the mirror.

    ``cover: attachments/banners/x.png`` (front matter) and
    ``![banner](attachments/banners/x.png)`` (body embed) point at the
    machine vault's attachments folder. The mirror may not write outside
    Library/, so the image could never be placed where the relative link
    resolves — carrying a permanently broken image in every mirror note
    is worse than dropping two decorative lines (decision log, Phase 5).
    """
    text = _COVER_LINE_RE.sub('', text)
    text = _BANNER_EMBED_RE.sub('', text)
    return text


def _line_ending_of(text: str) -> str:
    return '\r\n' if '\r\n' in text.split('\n', 1)[0] else '\n'


def build_mirror_note(content: str, raw_source: str, banner: str) -> str:
    """Turn a machine-vault note into its mirror copy.

    - inserts ``mirror_of: <raw source>`` into the front matter (before
      the closing ``---``); a note without front matter gets a fresh
      block (defensive — every app note has front matter);
    - inserts the read-only banner as the first body line;
    - strips banner image references (see ``_strip_banner_refs``).

    Reads and writes happen in text mode, so content comparison is done
    in newline-translated space and stays stable across platforms.
    """
    text = _strip_banner_refs(content or '')
    nl = _line_ending_of(text)
    mirror_line = f"{MIRROR_KEY}: {raw_source}"

    lines = text.split(nl)
    has_fm = bool(lines) and lines[0].strip() == '---'
    close_idx = None
    if has_fm:
        for i in range(1, len(lines)):
            if lines[i].strip() in ('---', '...'):
                close_idx = i
                break
    if close_idx is not None:
        lines.insert(close_idx, mirror_line)
        body_at = close_idx + 2   # past the (now shifted) closing '---'
    else:
        # no (parsable) front matter — wrap the whole note in a fresh block
        lines = ['---', mirror_line, '---', ''] + lines
        body_at = 4
    # banner as the first body line (skip blank lines that follow the
    # front matter; guarantee exactly one blank before and after it)
    while body_at < len(lines) and lines[body_at].strip() == '':
        body_at += 1
    if body_at > 0 and lines[body_at - 1].strip() == '---':
        lines.insert(body_at, '')
        body_at += 1
    lines.insert(body_at, banner)
    lines.insert(body_at + 1, '')
    return nl.join(lines)


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

@dataclass
class TreePlan:
    label: str                    # "GitHub Projects" / "Websites"
    vault_path: str               # machine vault root ('' when not set)
    vault_present: bool = False   # folder exists and was scanned
    sources: int = 0
    keeps: int = 0
    creates: List[Tuple[str, str, str]] = field(default_factory=list)
    updates: List[Tuple[str, str, str]] = field(default_factory=list)
    moves: List[Tuple[str, str, str, str]] = field(default_factory=list)
    deletes: List[str] = field(default_factory=list)
    conflicts: List[Tuple[str, str]] = field(default_factory=list)
    duplicates: List[str] = field(default_factory=list)
    skipped_no_source: int = 0
    unmarked_files: int = 0
    scan_errors: int = 0

    @property
    def changes(self) -> int:
        return (len(self.creates) + len(self.updates) + len(self.moves)
                + len(self.deletes))


@dataclass
class MirrorPlan:
    manual_vault: str
    github_vault: str = ''
    websites_vault: str = ''
    apply: bool = False
    trees: Dict[str, TreePlan] = field(default_factory=dict)
    applied: bool = False

    @property
    def total_changes(self) -> int:
        return sum(t.changes for t in self.trees.values())

    def render_report(self) -> str:
        mode = ("APPLIED" if self.applied
                else ("APPLY (planned)" if self.apply
                      else "DRY RUN (nothing written)"))
        out: List[str] = [
            f"GitCurator — Manual Notes Library mirror — {mode}",
            f"Manual vault:   {self.manual_vault}",
            f"GitHub vault:   {self.github_vault or '(not set)'}",
            f"Websites vault: {self.websites_vault or '(not set)'}",
            "",
        ]
        for label in (GITHUB_TREE, WEBSITES_TREE):
            tree = self.trees.get(label)
            if tree is None:
                continue
            src_state = ("scanned" if tree.vault_present else
                         ("not set — skipped" if not tree.vault_path
                          else "folder not found — skipped"))
            out.append(f"== {label}  →  Library/{label}/  ({src_state}) ==")
            out.append(
                f"  library notes: {tree.sources} · kept: {tree.keeps} · "
                f"created: {len(tree.creates)} · updated: {len(tree.updates)} "
                f"· moved: {len(tree.moves)} · deleted: {len(tree.deletes)}")
            skipped = []
            if tree.skipped_no_source:
                skipped.append(
                    f"{tree.skipped_no_source} without a source line")
            if tree.duplicates:
                skipped.append(
                    f"{len(tree.duplicates)} duplicate-source file(s)")
            if tree.unmarked_files:
                skipped.append(
                    f"{tree.unmarked_files} unmarked file(s) under Library/ "
                    f"(left untouched)")
            if tree.scan_errors:
                skipped.append(f"{tree.scan_errors} unreadable file(s)")
            if skipped:
                out.append("  skipped: " + "; ".join(skipped))
            for rel, _text, _src in tree.creates:
                out.append(f"  CREATE   {rel}")
            for rel, _text, _src in tree.updates:
                out.append(f"  UPDATE   {rel}")
            for old, new, _text, _src in tree.moves:
                out.append(f"  MOVE     {old}  ->  {new}")
            for rel in tree.deletes:
                out.append(f"  DELETE   {rel}")
            for rel, reason in tree.conflicts:
                out.append(f"  CONFLICT {rel} — {reason}")
            for rel in tree.duplicates:
                out.append(f"  DUP      {rel}")
            out.append("")
        out.append(
            f"Totals: {self.total_changes} change(s) needed"
            + (" — applied." if self.applied
               else (" — NOT applied (dry run)." if not self.apply
                     else " — apply was requested.")))
        out.append(
            "Safety: only files carrying the mirror_of marker under Library/ "
            "are ever changed; nothing outside Library/ is touched.")
        return "\n".join(out) + "\n"


def _plan_tree(label: str, vault_path: str, tree_root: str) -> TreePlan:
    """Compute the action list for one Library tree (pure — writes nothing).

    Matching is by RAW source value, so a note the owner moved between
    category folders keeps its identity: the stale mirror copy is
    removed and a fresh one appears at the new location (a MOVE).
    """
    plan = TreePlan(label=label, vault_path=vault_path)
    notes: List[SourceNote] = []
    if vault_path and os.path.isdir(vault_path):
        plan.vault_present = True
        scanned, dupes, no_source, errors = _scan_source_notes(vault_path)
        notes = scanned
        plan.duplicates = dupes
        plan.skipped_no_source = no_source
        plan.scan_errors = errors
    plan.sources = len(notes)

    by_source = {n.source: n for n in notes}
    mirrors, unmarked, mirror_errors = _scan_mirror_tree(tree_root)
    plan.unmarked_files = unmarked
    plan.scan_errors += mirror_errors

    # Every marker file NOT sitting at its source's current location will
    # be removed — as a MOVE's old path, or as a plain DELETE (orphan /
    # duplicate marker). ``doomed`` is the full removal set, used to know
    # which expected paths are freed by deletions.
    doomed: set = set()
    for source_value, rels in mirrors.items():
        expected = (by_source[source_value].rel
                    if source_value in by_source else None)
        for rel in rels:
            if expected is None or rel != expected:
                doomed.add(rel)
    move_olds: set = set()

    # --- one expected copy per source ---
    for note in notes:
        try:
            expected_abs = _safe_join(tree_root, note.rel)
        except MirrorError as exc:
            plan.conflicts.append((note.rel, f"unsafe path: {exc}"))
            continue
        existing_rels = [r for r in mirrors.get(note.source, [])
                         if r == note.rel]
        try:
            with open(note.path, 'r', encoding='utf-8',
                      errors='replace') as f:
                note.text = f.read()
        except OSError:
            plan.scan_errors += 1
            continue
        text = build_mirror_note(note.text, note.source, MIRROR_BANNERS[label])

        if existing_rels:
            # a marked copy is already at the right spot — compare content
            try:
                with open(expected_abs, 'r', encoding='utf-8',
                          errors='replace') as f:
                    on_disk = f.read()
            except OSError:
                plan.scan_errors += 1
                continue
            # v0.16.0 — Phase 6: the rebuilt copy must carry over the
            # owner-approved Related (auto) block (a rebuild would
            # otherwise silently wipe it); with the block carried over,
            # an unchanged note still compares equal (a KEEP).
            text = preserve_related_block(on_disk, text)
            if on_disk == text:
                plan.keeps += 1
            else:
                plan.updates.append((note.rel, text, note.source))
            continue

        # no marked copy of ours at the expected spot
        if os.path.exists(expected_abs) and note.rel not in doomed:
            plan.conflicts.append(
                (note.rel,
                 "a file without the mirror_of marker is in the way "
                 "(left untouched)"))
            continue
        moved_from = [r for r in mirrors.get(note.source, []) if r != note.rel]
        if moved_from:
            move_olds.add(moved_from[0])
            # v0.16.0 — Phase 6: a MOVED mirror copy keeps its approved
            # Related (auto) block too (read from the old spot).
            old_abs = _safe_join(tree_root, moved_from[0])
            try:
                with open(old_abs, 'r', encoding='utf-8',
                          errors='replace') as f:
                    old_text = f.read()
                text = preserve_related_block(old_text, text)
            except (OSError, MirrorError):
                pass
            plan.moves.append((moved_from[0], note.rel, text, note.source))
        else:
            plan.creates.append((note.rel, text, note.source))

    plan.deletes = sorted(doomed - move_olds)
    return plan


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _cleanup_empty_dirs(tree_root: str) -> None:
    """Remove folders the deletions left empty — inside the tree only.

    ``os.rmdir`` fails on non-empty folders, so a folder holding any
    other file (the owner's, or a file that was never deleted) can never
    be removed. The tree root itself is handled by the caller.
    """
    if not tree_root or not os.path.isdir(tree_root):
        return
    for root, dirs, _files in os.walk(tree_root, topdown=False):
        for d in dirs:
            target = os.path.join(root, d)
            if _is_inside(target, tree_root):
                try:
                    os.rmdir(target)
                except OSError:
                    pass


def _read_marker_source(path: str) -> Optional[str]:
    """Return the mirror_of value of ``path`` (None when unreadable/unmarked)."""
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            head = f.read(800)
    except OSError:
        return None
    match = _MIRROR_RE.search(head)
    return _unquote(match.group(1)) if match else None


def run_mirror(manual_vault: str, github_vault: Optional[str] = None,
               websites_vault: Optional[str] = None, *,
               apply: bool = False,
               log: Optional[Callable[[str], None]] = None) -> MirrorPlan:
    """Plan (and with ``apply=True`` perform) the Library mirror sync.

    Dry-run by default: the returned plan describes every action and the
    manual vault is not touched. With ``apply=True`` every safety check
    re-runs first, deletions happen before writes, and each write target
    is re-verified to be inside its Library tree and either free or a
    mirror-marked copy of the SAME source (paranoia re-check — the plan
    already guarantees it).
    """
    def say(msg: str) -> None:
        if log:
            log(msg)

    github_vault = (github_vault or '').strip()
    websites_vault = (websites_vault or '').strip()
    check_vault_paths(manual_vault, github_vault, websites_vault,
                      apply=apply)

    manual = (manual_vault or '').strip()
    library_root = os.path.join(manual, LIBRARY_FOLDER)
    plan = MirrorPlan(manual_vault=manual, github_vault=github_vault,
                      websites_vault=websites_vault, apply=apply)

    tree_specs = [(GITHUB_TREE, github_vault), (WEBSITES_TREE, websites_vault)]
    for label, vault in tree_specs:
        if not vault:
            continue
        tree_root = os.path.join(library_root, label)
        plan.trees[label] = _plan_tree(label, vault, tree_root)
    if not plan.trees:
        raise MirrorError(
            "nothing to mirror: neither machine vault path is configured")

    if not apply:
        for tree in plan.trees.values():
            say(f"[{tree.label}] {tree.sources} library notes — "
                f"{tree.changes} change(s) planned (dry run)")
        return plan

    # ---- apply: deletions first (incl. move old paths), then writes ----
    for label, _vault in tree_specs:
        tree = plan.trees.get(label)
        if tree is None:
            continue
        tree_root = os.path.join(library_root, label)
        old_paths = [old for old, _new, _text, _src in tree.moves]
        for rel in tree.deletes + old_paths:
            target = _safe_join(tree_root, rel)
            if not _is_inside(target, tree_root):
                raise MirrorError(
                    f"delete escaped the Library tree: {target}")
            if _read_marker_source(target) is None:
                # not ours any more (changed since the plan) — never delete
                say(f"[{label}] SKIP delete {rel} — no mirror marker")
                continue
            dryrun.remove(target)
        writes = ([(rel, text, src) for rel, text, src in tree.creates]
                  + [(rel, text, src) for rel, text, src in tree.updates]
                  + [(new, text, src)
                     for _old, new, text, src in tree.moves])
        for rel, text, src in writes:
            target = _safe_join(tree_root, rel)
            if not _is_inside(target, tree_root):
                raise MirrorError(f"write escaped the Library tree: {target}")
            if os.path.exists(target):
                marker = _read_marker_source(target)
                if marker != src:
                    # a file that is not our mirror copy of this source is
                    # in the way — never touch it
                    say(f"[{label}] SKIP write {rel} — target is not our "
                        f"mirror copy of this source")
                    continue
            dryrun.makedirs(os.path.dirname(target), exist_ok=True)
            atomic_write_text(target, text)
        _cleanup_empty_dirs(tree_root)
        # an entirely emptied tree folder is tidied away (rmdir only
        # succeeds when nothing else is left in it)
        for folder in (tree_root,):
            if os.path.isdir(folder):
                try:
                    os.rmdir(folder)
                except OSError:
                    pass
    # tidy an entirely emptied Library root too (never removes anything
    # that still holds files — rmdir fails on non-empty folders)
    if os.path.isdir(library_root):
        try:
            os.rmdir(library_root)
        except OSError:
            pass

    plan.applied = True
    for tree in plan.trees.values():
        say(f"[{tree.label}] mirrored {tree.sources} notes — "
            f"{tree.changes} change(s) applied")
    return plan
