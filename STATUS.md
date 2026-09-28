# GitCurator — Build Status

This file is the memory between sessions. Every session reads it first and updates it last.
Keep it short, factual, and in plain language.

- **Version at start of this build:** 0.09.4 (now 0.09.5 on the branch below)
- **Spec:** `SPEC.md` (root of the repo)
- **Current phase:** 0 — in review (agent finished, waiting for the owner)

## Phases

| Phase | Name | Status | Branch | Version | Owner approval |
|---|---|---|---|---|---|
| 0 | Groundwork | in review | `phase-0-groundwork` (pushed, NOT merged) | 0.09.5 | pending |
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
- [ ] **Path to `unique_links.csv`** (needed to run `pick_golden_links.py` for real — asked in the Phase 0 session; the tool is built and tested with synthetic data)
- [ ] Websites vault folder location and a private GitHub repo for its backup (needed by Phase 1)
- [ ] Manual Notes vault path (needed by Phase 5)
- [ ] Path of a **copy** of the GitHub vault, for the Phase 0 owner review (scan a copy, read the report)

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

## Open questions for the owner

- Path to `unique_links.csv` (re-asked in the Phase 0 report).
- Where will the COPY of the GitHub vault for the owner-review scan live?

## Session log

(Agents: one line per session: date, phase, what was done, test result.)

- 2026-09-29, Phase 0 — groundwork on branch `phase-0-groundwork` (NOT merged): dry-run mechanism + `--dry-run` CLI flag, scan/snapshot/golden tools, 24 new tests, CI updated to 20 modules + 130 tests, VERSION 0.09.5, CHANGELOG + README, `docs/reports/PHASE-0-report.md`. Baseline 106 tests green before changes; final 130/130 green; end-to-end dry-run rehearsal on a synthetic vault left it byte-identical (one deadlock found and fixed on the way). Waiting for owner review.
