"""vault_scan.py — v0.61.0 THE VAULT SCAN: the LLM reads the library itself.

The owner's ask (session, verbatim): "HEADLINE — build the VAULT SCAN
mechanism (v0.61.0). New CTA row: [Fetch] [Scan] [Test Connection]. The
Scan run: LLM scans the vault (folder walk + note contents → LLM
analysis); Detects notes I manually tagged 'auto-delete' — REUSE the
v0.60.1 grammar exactly (frontmatter list + body inline tags, both
auto_delete/auto-delete spellings, 🗑️, BANISH_WORDs) — do not invent a
second grammar; Suggests folder/subfolder creation for orphaned /
not-categorized / too-broad-category websites, and which notes should
move where; Confirms with me on Telegram BEFORE deleting or moving
anything — the v0.60.0 banish-gate round-trip is the template (ask →
buttons → act; no response in 300s → safe defer, nothing done)."

Four laws, kept:

1. **ONE deletion grammar.** The scan's deletions ARE
   :func:`website_pipeline.scan_pending_banishments` — both doors
   (the note's frontmatter tags + body inline tags, the master-table
   gesture), deduped by canonical URL, exactly v0.60.1. This module
   never re-detects, never re-words, never invents a second grammar.

2. **The plan is a PROPOSAL.** The LLM proposes folders and moves; the
   owner confirms on Telegram (the injected ``scan_confirm`` channel —
   the banish-gate round-trip template); NOTHING moves or deletes until
   he answers, and a timeout (300s) is a safe defer — the vault is left
   byte-for-byte as it was.

3. **Existing notes are never rewritten.** A move is a FILE move: the
   note's bytes are read from the old path and written to the new path
   verbatim, then the old file is removed (the ``_banish_note_file``
   mechanics — dry-run aware, honest about what actually moved). The
   folder IS the category (the vault's own convention —
   ``note_state``'s moves-as-corrections law reads a hand move the same
   way), so the state DB row is re-pointed at the new path; the note's
   content is never touched.

4. **The LLM is a guest.** Its JSON is VALIDATED before the owner ever
   sees it: only listed candidate notes move, only legal destinations
   (an existing folder or a validated new one, never ``_review`` /
   ``_moc`` / dot-folders / the vault root), only app-owned notes (a
   hand-written note is the owner's — counted, never moved), names
   sanitized. Anything the validation rejects is dropped with a log
   line, never a crash.

v0.63.0 — THE THREE LAWS (the owner read the first draft of a real
plan and taught the scan its second lesson):

* **THE FRESH-EYES LAW** — before any new folder is proposed, the
  LLM is handed the LATEST tree and told to check it; and the
  validator ENFORCES it: a proposed folder that already exists (same
  path, or the same path ignoring case/spaces/underscores/hyphens, or
  a near-identical sibling under the same parent) is ABSORBED into
  the existing folder — the creation is dropped, the moves re-route
  into the folder that is already there, and the log says so. A plan
  may never duplicate (or near-duplicate) a category the library
  already holds.
* **THE DEPTH LAW** — every website note files at least
  ``Broad_Category/Subfolder`` deep: never the vault root, never a
  bare top-level category folder. Notes found straying at a category
  root become move candidates; a move whose destination is a single
  segment is dropped with a warning; a new folder must be a
  subfolder (two levels or more) by the same law.
* **THE CORE-NOTE LAW** — the library's own catalog notes — the
  Website Directory above all — are the library itself, never filing
  material: they are marked ``core`` in the inventory, excluded from
  the move candidates, and named to the LLM as untouchable.

Pure stdlib, no Qt, no network (the LLM and the Telegram channel are
injected callables — the hermetic law). Dry-run aware (``dryrun``).
"""

import os
import re
from typing import Callable, Dict, List, Optional

from gitcurator.core import dryrun as _dryrun
from gitcurator.core import website_pipeline as _wp
from gitcurator.core.llm_client import extract_json
from gitcurator.core.website_directory import DIRECTORY_FILENAME

#: v0.61.0 — the scan's own log prefix (the story the log tells).
SCAN_PREFIX = "🔍 Vault scan:"
#: The prompt file (the house prompts/ convention — w01/r01/l01 …).
SCAN_PROMPT_RELPATH = os.path.join('prompts', 's01_vaultscan.txt')
#: A leaf folder holding more notes than this reads as TOO BROAD (the
#: LLM is told so; config ``scan_too_broad_threshold`` overrides).
DEFAULT_TOO_BROAD = 40
#: How many candidate notes ride one LLM ask (the context budget; the
#: rest are summarized as counts — an honest plan for the head of the
#: pile beats a truncated one; config ``scan_max_notes`` overrides).
DEFAULT_MAX_NOTES = 300
#: Folders the scan never touches (the review machinery and the index
#: own them) and never proposes as destinations.
_PROTECTED_FOLDERS = ('_review', '_moc', '_inbox')

#: A folder-name cell the validator accepts: Title-Case-ish words,
#: hyphens/underscores/spaces, no path tricks (the vault's own
#: convention — "AI-Domain", "UI_UX_Product_Design").
_SAFE_FOLDER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _\-&+()']{0,39}$")
_SAFE_NOTE_NAME = re.compile(r"^[^/\\:*?\"<>|\r\n]+$")


def _read_prompt() -> str:
    """The scan prompt (``app/prompts/s01_vaultscan.txt``)."""
    from gitcurator.constants import APP_DIR
    path = os.path.join(APP_DIR, SCAN_PROMPT_RELPATH)
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read()
    except Exception:
        return ''


def _parse_note_frontmatter(path: str) -> Dict:
    """The scan's frontmatter read: ``source``, ``category``,
    ``subcategory``, ``managed_by``, ``kind``, ``tags`` — the same
    string-scan style as the banishment's own parser (the house law:
    core never grows a YAML dependency). Block-style tag lists are read
    like ``_parse_banish_frontmatter`` reads them. v0.63.0 — ``kind``
    joins the read (the core-note law: a ``kind: directory`` note is
    the machinery's own catalog, never filing material)."""
    out: Dict = {'source': '', 'category': '', 'subcategory': '',
                 'managed_by': '', 'kind': '', 'tags': []}
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.read(200_000).splitlines()
    except Exception:
        return out
    if not lines or lines[0].strip() != '---':
        return out
    in_tags = False
    for line in lines[1:]:
        s = line.strip()
        if s == '---':
            break
        if in_tags and s.startswith('- '):
            out['tags'].append(s[2:].strip().strip('"').strip("'"))
            continue
        in_tags = False
        if ':' not in s:
            continue
        key, _, val = s.partition(':')
        key = key.strip().lower()
        val = val.strip().strip('"').strip("'")
        if key == 'tags':
            inner = val
            if inner.startswith('[') and inner.endswith(']'):
                inner = inner[1:-1]
            elif not inner:
                in_tags = True
            out['tags'] = [t.strip().strip('"').strip("'")
                           for t in inner.split(',') if t.strip()]
        elif key in ('source', 'category', 'subcategory', 'managed_by',
                     'kind'):
            out[key] = val
    return out


def _is_core_note(name: str, fm: Dict) -> bool:
    """v0.63.0 — THE CORE-NOTE LAW's predicate: is this note one of the
    library's own catalog files? The Website Directory (the
    auto-generated index of every site — any file whose stem ends in
    "Website Directory", so the owner's spelling survives an emoji or
    numbering change) and every note the machinery owns outright
    (``kind: directory``) live where the app put them: at the vault
    root, by design. They are never move candidates and never
    re-filed — moving the catalog would scatter the library's own
    table of contents."""
    if name == DIRECTORY_FILENAME:
        return True             # the canonical catalog, by its exact name
    if (name or '').strip().lower().endswith('website directory.md'):
        return True            # the same catalog under the owner's spelling
    return str((fm or {}).get('kind') or '').strip().lower() == 'directory'


def scan_vault_inventory(vault_path: str,
                         log: Optional[Callable] = None) -> Dict:
    """v0.61.0 — the folder walk + per-note digest (the LLM's eyes).

    Walks the vault the banishment walk walks (dot-folders and
    ``_inbox`` skipped; ``_review`` / ``_moc`` notes are SEEN but never
    become move candidates — the review machinery owns them). Every
    note carries its title (the file name), source URL, domain,
    category/subcategory frontmatter, ownership, and the folder it
    lives in. v0.62.0 — the TRASH DOOR's waiting room: notes resting
    in the root ``Trash`` folder (any spelling, its subfolders) are
    COUNTED (``trash_notes`` — they await the deletion gate, the log
    says so) but never enter the tree, the notes list, or the totals —
    they are on their way out, not library material, and the LLM is
    never shown them (it must neither rescue nor re-file them).
    v0.63.0 — the CORE-NOTE LAW: the library's own catalog notes
    (the Website Directory, any ``kind: directory`` note) are marked
    ``core`` on their row and counted (``core_notes`` — the log says
    they stay at their post); they stay in the tree (they ARE the
    library's front door) but never become move candidates.
    Pure file reads; never raises. Returns
    ``{'folders': [{'rel', 'note_count'}], 'notes': […],
    'root_notes': N, 'uncategorized_notes': N, 'hand_notes': N,
    'trash_notes': N, 'core_notes': N, 'total_notes': N}``."""
    log = log or (lambda *a, **k: None)
    out: Dict = {'folders': [], 'notes': [], 'root_notes': 0,
                 'uncategorized_notes': 0, 'hand_notes': 0,
                 'trash_notes': 0, 'core_notes': 0, 'total_notes': 0}
    if not vault_path or not os.path.isdir(vault_path):
        return out
    folder_counts: Dict[str, int] = {}
    for root, dirs, files in os.walk(vault_path):
        dirs[:] = sorted(d for d in dirs
                         if d != '_inbox' and not d.startswith('.'))
        rel_root = os.path.relpath(root, vault_path).replace(os.sep, '/')
        in_trash = _wp._is_trash_folder(rel_root)
        for name in sorted(files):
            if not name.lower().endswith('.md'):
                continue
            path = os.path.join(root, name)
            if not os.path.isfile(path):
                continue
            fm = _parse_note_frontmatter(path)
            src = (fm.get('source') or '').strip()
            domain = ''
            if src.lower().startswith(('http://', 'https://')):
                try:
                    from urllib.parse import urlparse
                    domain = urlparse(src).netloc or ''
                except Exception:
                    domain = ''
            app_owned = (fm.get('managed_by') or '').strip().lower() \
                == _wp.MANAGED_BY_GITCURATOR
            core = _is_core_note(name, fm)
            if in_trash:
                # v0.62.0 — the waiting room: counted (honestly), never
                # listed, never a candidate, never shown to the LLM.
                out['trash_notes'] += 1
                continue
            in_root = rel_root == '.'
            folder_rel = '' if in_root else rel_root
            if folder_rel and folder_rel not in _PROTECTED_FOLDERS \
                    and not folder_rel.startswith(tuple(
                        f + '/' for f in _PROTECTED_FOLDERS)):
                folder_counts[folder_rel] = \
                    folder_counts.get(folder_rel, 0) + 1
            if in_root:
                out['root_notes'] += 1
            low_folder = folder_rel.lower()
            if low_folder == 'uncategorized' \
                    or low_folder.startswith('uncategorized/'):
                out['uncategorized_notes'] += 1
            if not app_owned:
                out['hand_notes'] += 1
            if core:
                out['core_notes'] += 1
            out['total_notes'] += 1
            out['notes'].append({
                'file': name, 'path': path, 'rel':
                    (name if in_root else f"{folder_rel}/{name}"),
                'title': os.path.splitext(name)[0], 'source': src,
                'domain': domain,
                'category': (fm.get('category') or '').strip(),
                'subcategory': (fm.get('subcategory') or '').strip(),
                'tags': fm.get('tags') or [],
                'app_owned': app_owned, 'folder': folder_rel,
                'in_root': in_root, 'core': core,
                'protected': folder_rel in _PROTECTED_FOLDERS
                or folder_rel.startswith(tuple(
                    f + '/' for f in _PROTECTED_FOLDERS))})
    out['folders'] = [{'rel': rel, 'note_count': n}
                      for rel, n in sorted(folder_counts.items())]
    if out['trash_notes']:
        log(f"🗑️ {out['trash_notes']} note(s) rest in the Trash folder — "
            f"they await the deletion review (the same gate as your "
            f"auto-delete marks)", "info")
    if out['core_notes']:
        log(f"🏛️ {out['core_notes']} core catalog note(s) (the Website "
            f"Directory and its kin) stay at their post — core notes "
            f"are never re-filed", "info")
    return out


def _move_candidates(inventory: Dict, too_broad: int) -> List[Dict]:
    """Which notes the LLM may propose moving: app-owned, not protected
    (``_review`` / ``_moc`` are the machinery's), not core (the Website
    Directory is the library's own catalog — v0.63.0's core-note law),
    and either orphaned (vault root), uncategorized, a STRAY (filed
    directly in a top-level category folder with no subfolder —
    v0.63.0's depth law: every website lives under a subfolder), or
    filed in a TOO-BROAD folder."""
    broad = {f['rel'] for f in (inventory.get('folders') or [])
             if f.get('note_count', 0) > too_broad}
    out: List[Dict] = []
    for n in (inventory.get('notes') or []):
        if not n.get('app_owned') or n.get('protected') or n.get('core'):
            continue
        folder = n.get('folder') or ''
        if n.get('in_root') or not folder:
            out.append(n)            # orphaned — the vault root
            continue
        low = folder.lower()
        if low == 'uncategorized' or low.startswith('uncategorized/'):
            out.append(n)
            continue
        if '/' not in folder:
            out.append(n)            # v0.63.0 — a stray at the category
            continue                 # root (depth law: a subfolder waits)
        if folder in broad or any(folder.startswith(b + '/')
                                  for b in broad):
            out.append(n)
    return out


def _folder_tree_lines(inventory: Dict, too_broad: int) -> List[str]:
    lines = []
    for f in (inventory.get('folders') or []):
        mark = '  [TOO BROAD]' \
            if f.get('note_count', 0) > too_broad else ''
        lines.append(f"{f['rel']} — {f.get('note_count', 0)} notes{mark}")
    return lines


def _digest_lines(candidates: List[Dict]) -> List[str]:
    lines = []
    for n in candidates:
        tags = ','.join((n.get('tags') or [])[:6])
        bits = [n.get('title') or n.get('file')]
        if n.get('domain'):
            bits.append(n['domain'])
        now = n.get('folder') or '(vault root)'
        bits.append(f"now: {now}")
        if tags:
            bits.append(f"tags: {tags}")
        lines.append(' - ' + ' — '.join(bits))
    return lines


def _fold_segment(seg: str) -> str:
    """One folder cell's case/separator-insensitive identity."""
    return re.sub(r'[^a-z0-9]+', '', (seg or '').lower())


def _fold(rel: str) -> str:
    """v0.63.0 — THE FRESH-EYES LAW's identity: a folder path folded
    past case, spaces, underscores and hyphens, segment by segment
    (``'Design/Color_Tools'`` and ``'design colortools'`` fold the
    same), so a near-duplicate category can never hide behind its
    spelling. Segment-wise (not one flat squash) so ``A/B`` never
    collides with ``A_B``."""
    parts = [p.strip() for p in (rel or '').replace('\\', '/').split('/')]
    return '/'.join(_fold_segment(p) for p in parts if p)


def _sanitize_folder(rel: str) -> str:
    """One validated relative folder path (forward slashes, no
    traversal, no dot-folders, every cell a safe name). Returns '' when
    the path is illegal — the caller drops the move. v0.62.0 — the
    trash law: no cell may read ``Trash`` (any spelling) — the trash
    door is a DELETION gesture the owner performs by hand, never a
    destination the plan may file into (a filing move into Trash
    would be a deletion through the back door, past the gate)."""
    rel = (rel or '').strip().replace('\\', '/').strip('/')
    if not rel or rel == '.':
        return ''
    parts = [p.strip() for p in rel.split('/')]
    for p in parts:
        if not p or p.startswith('.') or p in _PROTECTED_FOLDERS \
                or '..' in p or not _SAFE_FOLDER_NAME.match(p) \
                or p.lower() in _wp.TRASH_FOLDER_NAMES:
            return ''
    if parts[0].lower() == 'uncategorized':
        return ''       # never propose INTO the parking lot
    return '/'.join(parts)


def _llm_propose(inventory: Dict, candidates: List[Dict],
                 llm_call: Callable, log: Callable,
                 too_broad: int, max_notes: int) -> Dict:
    """The LLM pass: folder tree + candidate digests → the filing
    proposal JSON (``new_folders`` / ``moves`` / ``summary``). A broken
    or unparseable answer is an EMPTY proposal with a warning — the
    scan never crashes on its guest. v0.63.0 — the tree is introduced
    as THE LATEST STATE and the three laws ride the ask itself (the
    guest hears the fresh-eyes / depth / core-note rules twice — once
    in the system prompt, once at the point of decision)."""
    prompt = _read_prompt()
    if not prompt or not candidates:
        return {'new_folders': [], 'moves': [], 'summary':
                'nothing to file' if not candidates else ''}
    tree = '\n'.join(_folder_tree_lines(inventory, too_broad)) or '(empty)'
    shown = candidates[:max_notes]
    digest = '\n'.join(_digest_lines(shown))
    if len(candidates) > len(shown):
        digest += (f"\n… +{len(candidates) - len(shown)} more candidate "
                   f"notes (summarized away — plan for the listed ones "
                   f"only)")
    user_msg = (
        f"Folder tree (relative to the vault root) — THE LATEST STATE, "
        f"checked fresh this run; read it before proposing any folder:\n"
        f"{tree}\n\n"
        f"Move-candidate notes ({len(shown)} of {len(candidates)}):\n"
        f"{digest}\n\n"
        f"The three laws, again, at the point of decision: (1) check "
        f"the tree above for the same or a near-identical existing "
        f"folder before proposing ANY new one — reuse it instead; "
        f"(2) every destination is at least Category/Subfolder deep — "
        f"never a bare category root, never the vault root; (3) core "
        f"catalog notes (the Website Directory) are never moved.\n\n"
        f"Return the JSON filing plan now.")
    try:
        raw = llm_call([
            {'role': 'system', 'content': prompt},
            {'role': 'user', 'content': user_msg}], task='vaultscan')
    except Exception as e:
        log(f"⚠️ The scan's LLM call failed: {e} — no filing plan this "
            f"run (the deletions below are unaffected)", "warning")
        return {'new_folders': [], 'moves': [], 'summary': ''}
    try:
        data = extract_json(raw or '')
    except Exception as e:
        log(f"⚠️ The scan's LLM answer was not JSON I could read "
            f"({e}) — no filing plan this run", "warning")
        return {'new_folders': [], 'moves': [], 'summary': ''}
    if not isinstance(data, dict):
        return {'new_folders': [], 'moves': [], 'summary': ''}
    return data


def _absorb_near_duplicate(clean: str,
                            existing: List[str]) -> Optional[str]:
    """v0.63.0 — THE FRESH-EYES LAW's lookup: which existing folder (if
    any) already covers ``clean``? Exact paths folded equal match first;
    then a near-identical SIBLING — same folded parent, leaf names that
    contain one another (both at least 5 folded chars, so tiny cells
    like 'Web' never false-hit 'Web_Workers') — so ``Design/Palettes``
    is absorbed by an existing ``Design/Color_Palettes``. Returns the
    existing folder that absorbs the proposal, or ``None`` when the
    library genuinely lacks the category. Sorted iteration keeps the
    pick deterministic."""
    hit = next((ex for ex in sorted(existing)
                if _fold(ex) == _fold(clean)), None)
    if hit is not None:
        return hit
    parts = clean.split('/')
    parent_fold = _fold('/'.join(parts[:-1]))
    leaf_fold = _fold_segment(parts[-1])
    if len(leaf_fold) < 5:
        return None            # too short to match by containment
    for ex in sorted(existing):
        ex_parts = ex.split('/')
        if len(ex_parts) < 2 \
                or _fold('/'.join(ex_parts[:-1])) != parent_fold:
            continue           # not a sibling under the same parent
        ex_leaf = _fold_segment(ex_parts[-1])
        if len(ex_leaf) >= 5 and (leaf_fold in ex_leaf
                                  or ex_leaf in leaf_fold):
            return ex
    return None


def _tree_roots(existing: set) -> set:
    """v0.63.0 — every folder the tree implies, ancestors included: a
    parent that holds no note of its own (only subfolders) is still a
    real place to hang a new subfolder under — the LLM's
    ``Category/Subfolder`` proposals must not be dropped because the
    category itself has no DIRECT notes."""
    roots = set(existing)
    for rel in existing:
        parts = rel.split('/')
        for i in range(1, len(parts)):
            roots.add('/'.join(parts[:i]))
    return roots


def _validate_moves(data: Dict, candidates: List[Dict],
                    inventory: Dict, log: Callable) -> (List[Dict], List[str]):
    """The guest's proposal meets the vault's laws. Only listed
    candidates move; destinations are existing folders or sanitized new
    ones; the same note is never moved twice; a move to the note's own
    folder is dropped. v0.63.0 — the three laws bite HERE, after the
    guest has spoken: THE FRESH-EYES LAW (a proposed folder that an
    existing folder already covers is ABSORBED — the creation drops,
    the moves re-route into the existing one; a plan may not duplicate
    itself either), THE DEPTH LAW (every destination is at least
    ``Category/Subfolder`` deep — a bare category root is not a filing
    place, and a new folder must be a subfolder, never a lone root
    cell), and THE CORE-NOTE LAW (a listed core note never rides — the
    candidates carry no core rows, and an invented one is not a listed
    candidate to begin with). Returns ``(moves, new_folders)`` — the
    validated plan (each move carries the note's full path)."""
    existing = {f['rel'] for f in (inventory.get('folders') or [])}
    roots = _tree_roots(existing)   # ancestors are parents too
    by_name: Dict[str, Dict] = {}
    for n in candidates:
        by_name.setdefault(n['file'], n)
        by_name.setdefault(n['rel'], n)
    raw_new = data.get('new_folders') or []
    new_folders: List[str] = []
    alias: Dict[str, str] = {}     # proposed path → the folder that covers it
    seen_new = set()
    seen_fold: Dict[str, str] = {}
    for nf in raw_new if isinstance(raw_new, list) else []:
        clean = _sanitize_folder(str(nf or ''))
        if not clean or clean in seen_new:
            continue
        if '/' not in clean:
            log(f"⚠️ Scan plan: new folder “{nf}” is a bare root cell — "
                f"new folders are SUBFOLDERS under a category (the depth "
                f"law) — dropped", "warning")
            continue
        parent = '/'.join(clean.split('/')[:-1])
        if parent and parent not in roots \
                and parent not in new_folders:
            log(f"⚠️ Scan plan: new folder “{nf}” has no parent in the "
                f"tree — dropped", "warning")
            continue
        hit = _absorb_near_duplicate(clean, sorted(existing))
        if hit is not None:
            alias[clean] = hit
            seen_new.add(clean)
            log(f"♻️ Scan plan: “{clean}” already exists as “{hit}” (the "
                f"same or a near-identical category) — filing into the "
                f"existing folder instead, nothing duplicate created",
                "info")
            continue
        plan_hit = seen_fold.get(_fold(clean))
        if plan_hit is not None and plan_hit != clean:
            alias[clean] = plan_hit
            seen_new.add(clean)
            log(f"♻️ Scan plan: “{clean}” duplicates “{plan_hit}” from the "
                f"same plan — filing into the one folder instead",
                "info")
            continue
        seen_new.add(clean)
        seen_fold[_fold(clean)] = clean
        new_folders.append(clean)
    legal = existing | set(new_folders)
    moves: List[Dict] = []
    taken: set = set()
    raw_moves = data.get('moves') or []
    for m in raw_moves if isinstance(raw_moves, list) else []:
        if not isinstance(m, dict):
            continue
        note_ref = str(m.get('note') or m.get('file') or '').strip()
        dest = _sanitize_folder(str(m.get('to') or '').strip())
        if dest:
            dest = alias.get(dest, dest)   # the fresh-eyes re-route
        note = by_name.get(note_ref)
        if note is None or note_ref in taken:
            continue        # not a listed candidate — never our call
        if not dest or dest not in legal:
            log(f"⚠️ Scan plan: “{note_ref}” → “{m.get('to')}” is not a "
                f"legal destination — dropped", "warning")
            continue
        if '/' not in dest:
            log(f"⚠️ Scan plan: “{note_ref}” → “{dest}” files a note "
                f"into a bare category root — every website lives under "
                f"a subfolder (Category/Subfolder) — dropped", "warning")
            continue
        if dest == (note.get('folder') or ''):
            continue        # already there
        taken.add(note_ref)
        moves.append({'note': note_ref, 'path': note['path'],
                      'title': note.get('title') or note_ref,
                      'from': note.get('folder') or '(vault root)',
                      'to': dest,
                      'reason': str(m.get('reason') or '')[:200]})
    return moves, new_folders


def build_scan_plan(vault_path: str, llm_call: Optional[Callable],
                    log: Optional[Callable] = None,
                    config: Optional[dict] = None) -> Dict:
    """v0.61.0 — THE SCAN'S OWN EYES: what it would tell the owner.

    Part one, the DELETIONS, is exactly
    :func:`website_pipeline.scan_pending_banishments` (the v0.60.1
    grammar — both doors, deduped; no second grammar is invented here).
    Part two, the FILING, is the LLM's validated proposal (folders +
    moves for the orphaned / uncategorized / too-broad notes). Pure
    reads + the injected ``llm_call``; never touches a file; never
    raises. Returns ``{'deletions': […], 'kept_handwritten': N,
    'moves': […], 'new_folders': […], 'summary': str,
    'inventory': {…}}`` — the shape the Telegram ask and the apply
    pass both consume."""
    log = log or (lambda *a, **k: None)
    cfg = config or {}
    too_broad = int(cfg.get('scan_too_broad_threshold',
                            DEFAULT_TOO_BROAD) or DEFAULT_TOO_BROAD)
    max_notes = int(cfg.get('scan_max_notes',
                            DEFAULT_MAX_NOTES) or DEFAULT_MAX_NOTES)
    out: Dict = {'deletions': [], 'kept_handwritten': 0, 'moves': [],
                 'new_folders': [], 'summary': '', 'inventory': {}}
    if not vault_path or not os.path.isdir(vault_path):
        return out
    try:
        gate = _wp.scan_pending_banishments(vault_path, log=log) or {}
    except Exception as e:
        log(f"⚠️ Scan: the deletion grammar's scan failed: {e}",
            "warning")
        gate = {}
    out['deletions'] = list(gate.get('items') or [])
    out['kept_handwritten'] = int(gate.get('kept_handwritten') or 0)
    inventory = scan_vault_inventory(vault_path, log=log)
    out['inventory'] = {
        'total_notes': inventory.get('total_notes') or 0,
        'root_notes': inventory.get('root_notes') or 0,
        'uncategorized_notes': inventory.get('uncategorized_notes') or 0,
        'hand_notes': inventory.get('hand_notes') or 0,
        'trash_notes': inventory.get('trash_notes') or 0,
        'core_notes': inventory.get('core_notes') or 0,
        'folder_count': len(inventory.get('folders') or [])}
    candidates = _move_candidates(inventory, too_broad)
    if not candidates:
        return out
    data = _llm_propose(inventory, candidates, llm_call, log,
                        too_broad, max_notes) \
        if llm_call is not None else {'new_folders': [], 'moves': [],
                                      'summary': ''}
    moves, new_folders = _validate_moves(data, candidates, inventory, log)
    out['moves'] = moves
    out['new_folders'] = new_folders
    out['summary'] = str(data.get('summary') or '')[:400]
    return out


def apply_scan_plan(plan: Dict, vault_path: str, state,
                    log: Optional[Callable] = None) -> Dict:
    """v0.61.0 — the CONFIRMED plan's hands (the owner answered ✅).

    Runs ONLY after the Telegram gate confirms (the caller's law — the
    v0.60.0 template: ask → buttons → act). Creates the new folders,
    moves each note FILE (bytes read from the old path, written verbatim
    to the new one, old removed — the never-rewrite law), re-points the
    state DB row at the new path (the folder is the category), and
    enforces the DELETIONS through the banishment's own machinery
    (``consume_decommission_table`` + ``banish_marked_notes`` — the same
    passes a confirmed batch runs). Dry-run aware; never raises.
    Returns ``{'folders_created': N, 'notes_moved': N,
    'moved': […], 'failed_moves': N, 'banished': N}``."""
    log = log or (lambda *a, **k: None)
    report: Dict = {'folders_created': 0, 'notes_moved': 0, 'moved': [],
                    'failed_moves': 0, 'banished': 0}
    if not vault_path or not os.path.isdir(vault_path):
        return report
    plan = plan or {}
    # ---- the filing: folders first, then the moves ----------------------
    for rel in (plan.get('new_folders') or []):
        clean = _sanitize_folder(str(rel or ''))
        if not clean:
            continue
        dst = os.path.join(vault_path, *clean.split('/'))
        try:
            _dryrun.makedirs(dst, exist_ok=True)
            if os.path.isdir(dst) or _dryrun.is_enabled():
                report['folders_created'] += 1
                log(f"📁 New folder{' would be ' if _dryrun.is_enabled() else ' '}created: {clean}", "info")
        except Exception as e:
            log(f"⚠️ Could not create the folder {clean}: {e}", "warning")
    for m in (plan.get('moves') or []):
        src = str(m.get('path') or '')
        dest_rel = _sanitize_folder(str(m.get('to') or ''))
        if not src or not os.path.isfile(src) or not dest_rel:
            report['failed_moves'] += 1
            continue
        dst_dir = os.path.join(vault_path, *dest_rel.split('/'))
        dst = os.path.join(dst_dir, os.path.basename(src))
        if os.path.exists(dst):
            log(f"⚠️ A note named {os.path.basename(src)} already lives "
                f"in {dest_rel} — the move is skipped (nothing is "
                f"overwritten)", "warning")
            report['failed_moves'] += 1
            continue
        try:
            _dryrun.makedirs(dst_dir, exist_ok=True)
            # the move itself rides the dry-run stand-in (bytes are
            # never rewritten — shutil.move semantics, rehearsed only
            # under a dry run):
            _dryrun.move(src, dst)
            if _dryrun.is_enabled():
                # a rehearsal: nothing actually moved, but the plan is
                # sound — count it as the move it would be (the
                # honesty law: the dry-run report shows the WOULD-be
                # story, not a failure pile):
                report['notes_moved'] += 1
                report['moved'].append(
                    {'note': m.get('note') or os.path.basename(src),
                     'from': m.get('from') or '', 'to': dest_rel})
                log(f"📦 {os.path.basename(src)} would move → "
                    f"{dest_rel} (dry-run rehearsal)", "info")
            elif os.path.isfile(dst) and not os.path.exists(src):
                report['notes_moved'] += 1
                report['moved'].append(
                    {'note': m.get('note') or os.path.basename(src),
                     'from': m.get('from') or '', 'to': dest_rel})
                log(f"📦 {os.path.basename(src)} moved → {dest_rel}",
                    "info")
                # the folder is the category — re-point the state row
                if state is not None:
                    try:
                        url = ''
                        fm = _parse_note_frontmatter(dst)
                        url = (fm.get('source') or '').strip()
                        if url:
                            canonical = \
                                _wp.normalize_website_url(url)
                            if canonical:
                                state.mark_processed(
                                    canonical, dst,
                                    dest_rel.split('/')[0],
                                    '/'.join(dest_rel.split('/')[1:]),
                                    'full')
                    except Exception:
                        pass    # bookkeeping never fails a move
            else:
                report['failed_moves'] += 1
        except Exception as e:
            log(f"⚠️ Could not move {os.path.basename(src)}: {e}",
                "warning")
            report['failed_moves'] += 1
    # ---- the deletions: the banishment's own machinery ------------------
    try:
        consume = _wp.consume_decommission_table(
            state, vault_path, log=log, apply_banish=True) or {}
        report['banished'] += len(consume.get('banished_urls') or [])
        notes = _wp.banish_marked_notes(
            state, vault_path, log=log) or {}
        report['banished'] += len(notes.get('urls') or [])
    except Exception as e:
        log(f"⚠️ The scan's deletion pass failed: {e}", "warning")
    return report
