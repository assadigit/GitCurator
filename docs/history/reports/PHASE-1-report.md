# Phase 1 report — Vault settings and ownership (v0.10.0)

**Branch:** `phase-1-vault-settings` (not merged — awaiting your review)
**Date:** 2026-09-29 · **Suite:** 168 tests, all passing

## Plan (as executed)

1. Merged the approved Phase 0 branch into `main` (your instruction), branched `phase-1-vault-settings` from it.
2. New `core/note_state.py` (pure stdlib, no PyQt): the `note_state` table in `cache.db` (same resolved path as `CacheDB` — the v0.09.4 split-state bug can't come back), fingerprinting, one-time baseline recording, and the full change detector Phase 3 will act on.
3. Note builder: ownership stamps (`managed_by`, `schema_version`, `prompt_version`) + the banner line on new notes; the three human placeholder sections dropped from new notes (SPEC-mandated).
4. Config: five new optional keys with safe defaults; `config.example.json` documents them pre-filled with your two new repos; old configs load unchanged (tested).
5. GUI 📁 Vault page: Websites + Manual vault pickers with live status, websites backup-repo field, two pipeline switches — wired through the one merge-based save path.
6. `--cli --status`: the same vault map + taxonomy status + a note-state section.
7. Worker hooks (thin, marked): pipeline guard before any fetch, baseline recording at run start (skipped in dry-run), record-on-write after each successful note write.
8. `vaultseal.websites_seal_from_config` — the second VaultSeal, dormant until the websites pipeline is ON; wired into all three batch-finish paths behind that gate.
9. 38 new tests; scan-tool fixture updated to simulate legacy notes (new notes no longer contain the sections it looks for); CI → 21 modules, 168 tests.
10. `VERSION` 0.10.0, CHANGELOG, README, STATUS updated.

---

## 1. What I built

The app now **knows your three vaults exist** and stamps its ownership on new notes:

- **Settings → 📁 Vault** now shows, under the existing GitHub vault picker: the Websites vault path (live status: *not set / will be created / found*), its backup repo, the Manual Notes vault path (optional until Phase 5), and two pipeline switches — GitHub **ON** (today's behavior exactly), Websites **OFF** (nothing behind it yet; Phase 2 turns it on with your approval).
- **`--cli --status`** prints the same map plus the taxonomy file status and the new note-state count.
- **New notes** carry `managed_by: gitcurator`, `schema_version`, `prompt_version` in frontmatter and a one-line *Managed by GitCurator* banner; the three empty human placeholder sections are gone from new notes. **Existing notes are never rewritten.**
- **The "moves are corrections" record exists**: your first real batch silently records a per-note baseline (path + fingerprint + category) in `cache.db`. Nothing acts on it yet — Phase 3 uses it so your folder moves are treated as corrections, not damage. A dry-run records nothing.
- **A second VaultSeal** for the Websites vault (its own private repo) — built, tested, dormant while the pipeline is OFF.

## 2. What changes for you

Almost nothing today. Your GitHub pipeline runs exactly as before (that's an acceptance criterion, and it's tested). You gain the vault settings page and a status command. The **one visible change**: notes created from now on look slightly different — a banner line at the top, three new frontmatter keys, and no empty *My Ideas / Social Signal / Journal* placeholder sections (your own writing in **old** notes is untouched and still protected).

## 3. How to check it yourself (about 5 minutes, from the `app` folder)

```cmd
REM 1) Pull the branch, then look at the settings screen:
REM    Settings (the gear icon) -> "📁 Vault" — you should see the Websites
REM    and Manual Notes pickers with live status, and the two switches.
REM    Type a path that doesn't exist -> "will be created" appears live.

REM 2) Same map from the CLI:
python main.py --cli --status
REM    -> new rows: Websites vault / Manual vault / Websites repo /
REM       Pipelines / Taxonomy, and a "Note state" section at the bottom.

REM 3) (Optional) rehearse a batch — still writes nothing:
python main.py --cli --auto --yes --dry-run

REM 4) When you're happy: run your normal batch once (for real). The log
REM    gets ONE new line: "Note-state baseline recorded: N notes". That's
REM    the Phase 3 groundwork being laid — nothing else changes.
```

## 4. Test results

- **Full gate, exactly as CI runs it:** 21 modules compiled, **168 tests, all passing** — 130 existing (none changed in behavior; the Phase-0 scan fixture was updated to simulate *legacy* notes, since new notes no longer contain the sections it scans for) + **38 new** in `tests/test_phase1.py`.
- New tests cover: the fingerprint (stability; the Phase-6 recall-block invisibility rule), folder→category mapping (including the `<Category>/<Subcategory>` layout Phase 2 uses), baseline recording (one-time, skips unmanaged notes, two vaults independent), **every row of the SPEC §4.4 table** detected (moved / edited / deleted / duplicates / unmanaged / unmapped / unknown), the note format (stamps in, banner in, legacy sections out, existing sections intact), old-config fallbacks + the pipelines merge, VaultIndex on two vaults, the websites seal (3 skip paths + a real seal), the CLI vault map, and a **real ProcessingWorker batch** on a synthetic vault recording the baseline — with a dry-run of the same batch recording **nothing**.

## 5. Decisions I made

1. **The websites seal is wired into all three batch-finish paths but gated on the pipeline switch** (default OFF = silent no-op). *Rejected:* builder-only, wiring in Phase 2 — the wiring is the risky part; doing it now under a default-off switch means Phase 2 flips one switch, and the ON path is already test-covered.
2. **Baseline records only when a vault has NO rows yet** (first real run). *Rejected:* refreshing the baseline every run — that would silently absorb your edits and moves, defeating the whole point of the record.
3. **Notes the app writes during a run are recorded one-by-one** (record-on-write) so the record stays complete between Phase 1 and Phase 3. *Rejected:* a run-end sweep — it couldn't tell app-written notes from hand-pasted ones.
4. **The fingerprint ignores a delimited recall block** (`<!-- gitcurator:recall:start/end -->`, defined now) plus trailing whitespace. *Rejected:* hashing raw bytes — Phase 6's app-added recall blocks would then read as human edits on all 600+ notes.
5. **Ownership banner is one short Obsidian callout line** (`> [!info] Managed by GitCurator — machine-written note.`). *Rejected:* a longer banner with move/edit rules — noise in every note; the rules live in the run reports.
6. **The GitHub pipeline switch is checked before anything is fetched.** *Rejected:* checking after the fetch — an OFF pipeline must consume nothing (no queue reads, no marks).
7. **The vault page saves on commit** (Enter / focus-out / Browse / toggle), not per keystroke, while the status labels update live per keystroke. *Rejected:* save-per-keystroke — chatty disk writes for no benefit.
8. **`taxonomy_path` defaults to the bundled `app/taxonomy/website-library-categories.md`** via a `resolve_taxonomy_path()` helper; the config key is just an override. *Rejected:* requiring the key — one more thing to misconfigure for zero benefit.

## 6. Risks and unknowns

- **New note shape** is a visible change to *new* notes only. If you dislike the banner wording or the missing placeholder sections, say so — both are one-line changes.
- The worker integration test runs the real `ProcessingWorker` with a **patched GitHub client** (this sandbox blocks api.github.com; the patch fails fast where the network would). Everything before that point — pipeline guard, cache setup, baseline recording — is the real code path.
- The record-on-write hook and baseline are best-effort by design: a failure there logs nothing and never breaks a batch. The trade-off: a silently missing record is possible (and harmless until Phase 3, which re-derives what it can).
- `--headless` mode still has no `--dry-run` (unchanged from Phase 0's scope decision).

## 7. Questions for you

1. When you run your first real batch after merging, the log will say **"Note-state baseline recorded: N notes"** — if N isn't in the neighborhood of your vault's note count (600+), tell me what it said.
2. Banner wording OK as-is, or would you like it plainer/different?
3. Ready for me to **merge this into `main`** when you approve, or do you want to poke the settings screen first?
