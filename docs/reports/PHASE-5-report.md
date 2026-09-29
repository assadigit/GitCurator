# Phase 5 report — the Manual Notes Library mirror (v0.14.0)

Branch: `phase-5-manual-mirror` (off `main` @ `0376bd6`, v0.13.0).
SPEC: §6 Phase 5 + §4.1 (vaults and ownership).

## 1. What I built

- `app/gitcurator/core/mirror.py` — the one-way sync engine: scans both
  machine vaults for library notes (a `.md` WITH a `source:` line, the
  same rule as VaultIndex), builds mirror copies under
  `<manual>/Library/GitHub Projects/…` and `<manual>/Library/Websites/…`,
  and plans/applies creates, updates, moves and orphan deletions.
- `app/gitcurator/tools/mirror_manual.py` — the CLI entry. **Dry-run by
  default**; `--apply` performs the sync. Vault paths from `config.json`
  (`vault_path`, `website_vault_path`, `manual_vault_path`), each
  overridable by flag (`--manual-vault` for running against a copy).
- Small wiring: a "Library mirror" row in `--cli --status`; the GUI 📁
  Vault page's Manual Notes group now states what the app writes and
  points at the tool; README + CHANGELOG + VERSION 0.14.0.

## 2. What changes for you

- Nothing automatic: the mirror runs only when you run the tool. Batches,
  the GUI and the pipelines behave exactly as v0.13.0.
- Once you set `manual_vault_path` (GUI 📁 Vault page or `config.json`)
  and run the tool, your Manual Notes vault gains a `Library/` folder
  with read-only copies of both libraries — linkable from your ideas
  (`[[Some repo]]`), with backlinks working in Obsidian.
- Every mirror copy carries a `mirror_of:` line and a read-only
  `> [!warning]` banner. GitHub notes' banner-image links are dropped in
  mirror copies (see §5).

## 3. How to check it yourself (about 5 minutes)

On your machine, from the `app/` folder (paths as examples — use yours):

```cmd
:: 1. rehearse against a COPY of your Manual Notes vault (writes nothing)
python gitcurator/tools/mirror_manual.py --manual-vault "C:\Users\Ali Assadi\Documents\Obsidian Vault - COPY" > plan.txt
type plan.txt

:: 2. the real thing — first the plan, then apply
python gitcurator/tools/mirror_manual.py
python gitcurator/tools/mirror_manual.py --apply

:: 3. SPEC owner review: in Obsidian, open an idea note in your Manual
::    Notes vault, type [[ and pick a mirror note (e.g. a repo name),
::    save, and open the mirror note — the backlink to your idea shows.
```

The dry run prints/creates a report (`app/reports/mirror/…`) listing
every CREATE/UPDATE/MOVE/DELETE with counts, plus skipped files. Run it
a second time — "Totals: 0 change(s)" (idempotent).

## 4. Test results

- New: `tests/test_phase5.py` — **47 tests**, all passing.
- Full gate on the branch: **30 modules compiled, 367/367 tests
  (34 core + 11 e2e + 28 goodrepos + 19 reliability + 14 quarantine +
  24 phase0 + 38 phase1 + 71 phase2 + 36 phase3 + 45 phase4 + 47
  phase5), 0 skips**, plus the offline golden run 30/30, 0 invalid.
- Acceptance mapped to tests: nothing outside `Library/` ever
  created/changed/deleted (`TestNothingOutsideLibrary`, full-tree
  snapshots before/after); idempotent (`test_idempotent_second_run_…`,
  byte-identical); moves propagate
  (`test_moves_propagate_matched_by_source`, plus a two-note swap);
  dry-run default (`test_dry_run_writes_nothing`, tool tests);
  refusals (`TestSafetyRefusals`); traversal (`TestSafeJoin` battery:
  `..`, absolute, backslash, drive letters, empty/dot components).

## 5. Decisions I made (each with the rejected alternative)

1. **Overlap refusal in BOTH directions.** SPEC says "refuses if
   manual_vault_path equals or contains either machine vault". I also
   refuse the reverse (manual INSIDE a machine vault) — the mirror would
   write into a machine-owned tree, equally forbidden by §4.1.
   *Rejected:* literal one-direction check only.
2. **Banner images are stripped from mirror copies** (`cover:` line +
   `![banner](attachments/banners/…)` embed). The paths are
   vault-root-relative and can never resolve inside the manual vault;
   fixing them would require writing the PNGs OUTSIDE `Library/`
   (forbidden) or rewriting links to a mirrored image tree (scope
   creep, and Phase 6 linking doesn't need banners). Every mirror note
   showing a permanently broken image is worse than two dropped
   decorative lines. *Rejected:* copying banners into the manual vault
   (violates the Library/ jail); leaving broken links (ugly, noisy).
3. **Mirror files are rebuildable policy-read-only.** A hand-edited
   mirror copy is restored on the next sync (the vault table calls
   `Library/` "rebuildable"; the banner warns). *Rejected:* detecting
   and preserving human edits inside mirror copies (needs state; the
   marker already separates "ours" from "yours").
4. **Tool-only invocation in Phase 5** — no auto-run at batch end.
   Your vault gets written when you say so, not as a batch side
   effect; the batch pipeline is unchanged from v0.13.0. *Rejected:*
   wiring the mirror into every batch finish (surprising writes into
   the owner's vault; the SPEC's owner-review step wants a manual
   rehearsal against a copy first).
5. **Matching by RAW source value** (unquoted), not the normalized URL.
   The mirror writes exactly what the source note's `source:` line
   says, so mirror→source matching is exact-string and can't drift
   when normalization rules change. *Rejected:* matching via
   `normalize_url` (would break identity if either normalizer ever
   changes; also can't distinguish two URLs that normalize alike).
6. **One copy per source; duplicates reported.** Two source files with
   the same `source:` → the first (path-sorted) is mirrored, the other
   is listed as a duplicate in the report. Two marker copies with the
   same source → one kept, the other deleted. *Rejected:* mirroring
   both (the Library would show ghost duplicates).
7. **A file without the marker is NEVER touched, even if it blocks a
   mirror copy** — reported as a conflict instead. *Rejected:*
   overwriting unmarked files (a one-bit mistake away from eating your
   notes).
8. **`_review/` notes are mirrored** (they carry `source:` and are real
   notes per VaultIndex semantics). *Rejected:* skipping them (they're
   part of the library; the report shows them at `_review/…`).
9. **The tool's `--out` is a report FILE path** (scan/snapshot use a
   directory). One run = one report; a file path is what you want to
   `type` on Windows. It refuses to write inside any of the three
   vaults. *Rejected:* a report directory with auto-generated names
   (indirection for no benefit here).

## 6. Risks and unknowns

- I could not run the mirror against your REAL Manual Notes vault (it
  lives on your machine); the owner-review run against a copy is the
  real test — that's exactly the SPEC's review step for this phase.
- Windows path semantics are handled (`os.path`-based walking, drive
  letters and backslashes rejected in `_safe_join`, CRLF-tolerant note
  rewriting) but only exercised on Linux in CI; the containment
  predicate (`realpath` + `commonpath`) is the same one the scan tool
  has used since v0.09.5.
- Obsidian-specific behaviors (backlink rendering, `[[ ]]` resolution
  with spaces in note names) are outside what I can verify here; the
  review step in §3 checks them in one minute.
- The mirror reflects whatever is in the vaults NOW: with
  `pipelines.websites` still off, `Library/Websites/` will contain
  only what earlier websites runs left (likely nothing) — expected,
  not a bug.

## 7. Questions for you

1. After the review run against a copy: was the plan what you expected
   (counts, category folders under `Library/GitHub Projects/…`)?
2. Do you want the mirror to also run automatically at the end of every
   batch (currently: explicit tool runs only — decision #4)?
3. Banner images in mirror copies: dropped (decision #2) — fine, or do
   you want a mirrored image tree with rewritten links in a later phase?
