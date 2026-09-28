# GitCurator — Build Status

This file is the memory between sessions. Every session reads it first and updates it last.
Keep it short, factual, and in plain language.

- **Version at start of this build:** 0.09.4 (now 0.09.5 on the branch below)
- **Spec:** `SPEC.md` (root of the repo)
- **Current phase:** 0 — **approved** (owner waived the vault-copy scan review on 2026-09-29). Phase 1 not started.

## Phases

| Phase | Name | Status | Branch | Version | Owner approval |
|---|---|---|---|---|---|
| 0 | Groundwork | approved | `phase-0-groundwork` (pushed, NOT merged) | 0.09.5 | yes — review waived by owner 2026-09-29 |
| 1 | Vault settings and ownership | not started | | | |
| 2 | Website pipeline | not started | | | |
| 3 | Moves as corrections, and the backfill | not started | | | |
| 4 | LLM backends | not started | | | |
| 5 | Mirror into Manual Notes | not started | | | |
| 6 | Linking | not started | | | |

Status values: `not started`, `in progress`, `in review` (agent finished, waiting for the owner), `approved`.

## Phase 0 summary (what exists on `phase-0-groundwork`)

- `app/gitcurator/core/dryrun.py` — the dry-run switch (log vault writes instead of performing them) + shadow cache + report renderer.
- `--dry-run` flag on every visual-CLI batch mode; dry-run batches also skip VaultSeal, Good Repos publish, bot-queue mark-read and `last_processed_msg_id`; report saved to `app/reports/dry-runs/`.
- `app/gitcurator/tools/scan_vault_edits.py` (read-only vault scan → `app/reports/scan/`), `snapshot_vault.py` (zip backup → `app/reports/snapshots/`), `pick_golden_links.py` (CSV → `app/tests/golden/websites_candidates.json`).
- Tests: `app/tests/test_phase0.py` (24). CI: 20 compiled modules, 130 tests. `app/reports/` is git-ignored.
- Report for the owner: `docs/reports/PHASE-0-report.md`.

## Inputs still needed from the owner

- [x] `website-library-categories.md` placed at `app/taxonomy/` (done; needed by Phase 2)
- [x] **`unique_links.csv`** (done — the owner committed it to the repo root on `main`; the picker ran for real: 784 rows → 30 candidates across 30 domains → `app/tests/golden/websites_candidates.json`, committed on `phase-0-groundwork`)
- [ ] Websites vault folder location and a private GitHub repo for its backup (needed by Phase 1)
- [ ] Manual Notes vault path (needed by Phase 5)
- [x] ~~Path of a **copy** of the GitHub vault, for the Phase 0 owner review~~ **waived by the owner 2026-09-29** ("this is unnecessary") — Phase 0 approved without the scan. The scan tool stays available any time via one command.

## Differences found between SPEC.md and the real code

(Agents: add an entry whenever you find one. Format: date, phase, what the spec said, what the code actually does, what you did.)

- 2026-09-29, Phase 0 — Spec §5: non-GitHub links "only reach `_inbox/non_github_links.md`". Reality: they are written to **per-platform** files (`_inbox/x_twitter_links.md`, `reddit_links.md`, …) by the module-level `write_inbox_links_by_platform()` (`gui/app.py`). What I did: nothing (no behavior change in Phase 0); the dry-run gates that function directly. **Phase 2 must hook there.**
- 2026-09-29, Phase 0 — Spec §5 implies vault writes go through `core/storage.py` helpers. Reality: notes and banners do, but ~10 raw `open(..., 'w')` writes exist inside the batch path (master index, MOCs, per-run reports, 404 log, banner markers, undo list) plus two inline atomic copies (inbox tables, links_manifest.json). What I did: routed them all through `core/dryrun.py` helpers for Phase 0 (behavior-identical when dry-run is off); a later phase should consolidate them onto `storage.atomic_write_*` per non-negotiable #11.
- 2026-09-29, Phase 0 — Spec §5 said VaultIndex reads "the first ~800 characters": true in code; the docstring inside `VaultIndex.rebuild()` still says 500 (stale comment only, no behavior difference). Left as-is.
- 2026-09-29, Phase 0 — `--headless` mode did not get `--dry-run` (scope: the flag is on the visual CLI only, per spec "a `--dry-run` flag on the CLI run"). Phase 2+ can add it with one line if wanted.

## Decisions log

(Agents: record every decision the owner made or that you made on their behalf, with the rejected alternative.)

- 2026-09-29, Phase 0 — Dry-run gates `storage.atomic_write_text/bytes` globally, including config.json writes. Rejected: gating only vault paths (a dry-run would then persist `last_processed_msg_id`/model changes and change what the next real run fetches).
- 2026-09-29, Phase 0 — Dry-run batches read `cache.db` through a throwaway copy (shadow cache) so nothing persists. Rejected: writing the real cache (next real run would skip links) and read-only mode (CacheDB has no such plumbing).
- 2026-09-29, Phase 0 — Dry-run also skips VaultSeal, Good Repos publish, mark-read, `last_processed_msg_id`. Rejected: gating only file writes (a rehearsal must not push backups or consume the queue).
- 2026-09-29, Phase 0 — Golden picker is deterministic (domain round-robin); category column carried as metadata. Rejected: random sampling (not comparable between runs).
- 2026-09-29, Phase 0 — Tool output defaults to `app/reports/` (git-ignored), `--out` override; every tool refuses to write inside the vault it targets. Rejected: writing next to the vault.
- 2026-09-29, Phase 0 — Inbox tables now written via `storage.atomic_write_text` (same bytes + fsync) instead of the duplicated inline copy. Rejected: keeping the private copy.
- 2026-09-29, Phase 0 — **Owner waived the Phase 0 owner-review scan** of a GitHub-vault copy ("this is unnecessary" — the owner wants to move to the Websites vault). Phase 0 marked approved as-is. Consequence: no "before photo" of human-written sections; the spec's non-negotiables (human sections are never touched by the app) still stand, and the scan/snapshot tools remain available on demand.

## Open questions for the owner

- (none for Phase 0 — closed.) For Phase 1, whenever the owner is ready: **where the Websites vault will live** (create it in Obsidian, tell me the path) and **which private GitHub repo** will back it up. Neither blocks starting Phase 1 (safe defaults, websites pipeline OFF until the owner switches it on).

## Notes for Phase 2 (from the real golden-set run)

- The candidate list contains **one unresolved t.co short link** (`https://t.co/Mxdio85wvQ`, likely `phosphoricons.com` per the CSV's own Domain column — the CSV flags it "Short link unresolved — domain guessed from display text, please verify"). The owner should confirm or swap it when approving the final list.
- The CSV's `Category` column is **empty in all 784 rows** — expected categories are assigned with the owner in Phase 2, as the spec says.
- The CSV has a `Needs Review` column (61 rows flagged, 4 unresolved t.co short links) the picker does not use; Phase 2 may want it when finalizing the golden set.

## Session log

(Agents: one line per session: date, phase, what was done, test result.)

- 2026-09-29, Phase 0 — groundwork on branch `phase-0-groundwork` (NOT merged): dry-run mechanism + `--dry-run` CLI flag, scan/snapshot/golden tools, 24 new tests, CI updated to 20 modules + 130 tests, VERSION 0.09.5, CHANGELOG + README, `docs/reports/PHASE-0-report.md`. Baseline 106 tests green before changes; final 130/130 green; end-to-end dry-run rehearsal on a synthetic vault left it byte-identical (one deadlock found and fixed on the way). Waiting for owner review.
- 2026-09-29, Phase 0 (same day, follow-up) — the owner committed `unique_links.csv` to `main`; ran `pick_golden_links.py` on the real file: 784 rows, 2 duplicates removed, 5 GitHub links excluded, 777 non-GitHub links, **30 candidates across 30 domains** selected deterministically (two runs identical). Real candidates JSON committed to the branch; full gate re-run green (20 compiles + 130/130 tests). One unresolved t.co short link noted for Phase 2.
- 2026-09-29, Phase 0 (same day, close-out) — owner waived the vault-copy scan review; Phase 0 marked **approved**. Branch left unmerged; awaiting the owner's word on merging before Phase 1 starts on top of it.
