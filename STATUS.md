# GitCurator — Build Status

This file is the memory between sessions. Every session reads it first and updates it last.
Keep it short, factual, and in plain language.

- **Version at start of this build:** 0.10.0 (now 0.11.0 on the branch below)
- **Spec:** `SPEC.md` (root of the repo)
- **Current phase:** 2 — in review (agent finished, waiting for the owner). Phases 0–1 approved & merged.

## Phases

| Phase | Name | Status | Branch | Version | Owner approval |
|---|---|---|---|---|---|
| 0 | Groundwork | approved | `phase-0-groundwork` (**merged into main 2026-09-29**) | 0.09.5 | yes — review waived by owner 2026-09-29 |
| 1 | Vault settings and ownership | approved | `phase-1-vault-settings` (**merged into main 2026-09-29**, tag v0.10.0) | 0.10.0 | yes — owner said "Merge. proceed" 2026-09-29 |
| 2 | Website pipeline | in review | `phase-2-website-pipeline` (pushed, NOT merged) | 0.11.0 | pending |
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

## Phase 1 summary (what exists on `phase-1-vault-settings`)

- `core/note_state.py` — the SPEC §4.4 record: `note_state` table in `cache.db` (vault, source URL, path, fingerprint, category, subcategory, locked), one-time silent baseline at run start, record-on-write, and the pure change detector (moved/edited/deleted/duplicates/unmanaged/unmapped/unknown) Phase 3 will act on. Dry-runs record nothing.
- Ownership stamps on NEW notes (`managed_by`, `schema_version`, `prompt_version`, one-line banner); the three human placeholder sections are no longer written. Existing notes never rewritten.
- Vault settings: `website_vault_path`, `manual_vault_path`, `website_repo_name`, `taxonomy_path`, `pipelines{github:on, websites:off}` — safe defaults, old configs load unchanged (tested). GUI 📁 Vault page (pickers + live status + switches) and `--cli --status` vault map.
- `vaultseal.websites_seal_from_config` — second VaultSeal, wired into all three batch-finish paths, gated on `pipelines.websites` (default off → dormant).
- Tests: `tests/test_phase1.py` (38). CI: 21 compiled modules, 168 tests.
- Report for the owner: `docs/reports/PHASE-1-report.md`.

## Phase 2 summary (what exists on `phase-2-website-pipeline`)

- `core/taxonomy.py` — parses the owner's real `app/taxonomy/website-library-categories.md` (14 categories, 16 subcategories, judgment rules verbatim, tag hints, one-line definitions); exact-match validation; folder paths via `safe_filename`; `TaxonomyError` on anything unparseable.
- `core/web_fetch.py` + `core/web_extract.py` — polite urllib fetcher (timeout, 2 MB streaming cap, per-domain rate limit, clear User-Agent, redirect cap, verified TLS, PDF detection) and stdlib HTML extraction (title, meta/og description, paragraph-preserving text, JS-shell + paywall heuristics, windows-1252/charset handling).
- `core/prompts.py` + `app/prompts/w01_category.txt`, `w02_subcategory.txt`, `w03_analyze.txt` (SPEC Appendix A verbatim) — the loader REFUSES any unfilled `{{SLOT}}` and rejects values containing slot markers.
- `core/website_pipeline.py` — the per-link flow: canonicalize (`normalize_website_url` — tracking params dropped, meaningful query params KEPT), 4-layer dedupe (vault index / websites_processed / dismissed / _review+failed upgrade), two-pass classification with validation + 2 corrective retries then `_review`, w03 analysis, atomic note write into `<Category>/<Subcategory>/`, minimal `_review` notes for unfetchable links, retry queue with multi-day backoff (max 3), app-owned placeholder cleanup on upgrade (hand-edited placeholders are NEVER removed), Stop-responsive, dry-run writes nothing.
- `links.py` — `owner.github.io/repo` → `github.com/owner/repo` (GitHub pipeline), gist detection (`#snippet`), `normalize_website_url` (GitHub's `normalize_url` untouched).
- GUI/CLI wiring (thin hooks in `gui/app.py` + `cli.py`): websites phase runs after the GitHub loop in every batch; `_inbox` dead end bypassed when websites ON (unchanged when OFF); github-off + websites-on works; both-off early return; report/summary/`--status` websites sections; Stop works mid-phase.
- `tools/run_golden_websites.py` — golden runner: `--offline` (fake LLM + canned fetch — ZERO network, runs in CI) and `--live` (real fetches + the configured OpenAI-compatible LLM); side-by-side report; exit 0 only with 0 invalid category names.
- `tests/golden/websites.json` — 30 entries with proposed expected categories (t.co → phosphoricons.com resolved and verified; uncertain domains probed before assigning).
- Tests: `tests/test_phase2.py` (71). CI: 27 compiled modules, 239 tests + the offline golden run.
- Report for the owner: `docs/reports/PHASE-2-report.md`; live golden report: `docs/reports/golden-websites-report.md`.

## Inputs still needed from the owner

- [x] `website-library-categories.md` placed at `app/taxonomy/` (done; needed by Phase 2)
- [x] **`unique_links.csv`** (done — the owner committed it to the repo root on `main`; the picker ran for real: 784 rows → 30 candidates across 30 domains → `app/tests/golden/websites_candidates.json`, committed on `phase-0-groundwork`)
- [x] **Phase 1 inputs** (received 2026-09-29, see "Vault map" below)
- [ ] Manual Notes vault path (needed by Phase 5; optional until then)
- [x] ~~Path of a **copy** of the GitHub vault, for the Phase 0 owner review~~ **waived by the owner 2026-09-29** ("this is unnecessary") — Phase 0 approved without the scan. The scan tool stays available any time via one command.
- [ ] **Phase 2 review**: read `docs/reports/PHASE-2-report.md` and `docs/reports/golden-websites-report.md`; then either "merge" or tell me what to change. The golden set's expected categories are MY proposal — edit `app/tests/golden/websites.json` freely and re-run the live report.
- [ ] **GitHub Actions billing**: runners fail to start since 2026-09-24 ("recent account payments have failed or your spending limit needs to be increased") — fix in GitHub Settings → Billing & plans. Local gate is green and mirrors CI exactly.
- [ ] **Cloudflare tokens**: both provided tokens are valid but carry no Workers AI permission (the /ai endpoints return "Authentication error") — if you want Workers AI as the cloud LLM, the token needs that permission added.

## Vault map (owner-provided 2026-09-29 — the single source of truth for Phase 1+)

| What | Windows path / repo | Config key it will land in |
|---|---|---|
| GitHub Projects vault (existing, 600+ notes) | `G:\Docs\Github Projects Obsidian Vault` | `vault_path` (unchanged meaning) |
| Websites vault (new; folder may not exist yet — status "will be created" is fine) | `G:\Docs\Documents\Obsidian Website Directory` | `website_vault_path` |
| GitHub vault backup repo (created by the agent, private, empty) | [`assadigit/my-awesome-github-directory`](https://github.com/assadigit/my-awesome-github-directory) | `vaultseal.repo_name` |
| Websites vault backup repo (created by the agent, private, empty) | [`assadigit/my-awesome-websites-directory`](https://github.com/assadigit/my-awesome-websites-directory) | `website_repo_name` |

Note: the GitHub vault may currently back up to an older repo (`obsidian-vault`, `GitCurator-Vault` or `github-projects-automated` all exist on the account — the local `config.json` decides, and it lives on the owner's machine). When wiring Phase 1, point `vaultseal.repo_name` at `my-awesome-github-directory` per the owner's instruction; the old repo is left untouched.

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

- Phase 1 review: read `docs/reports/PHASE-1-report.md`; then "merge" (or tell me what to change first).
- After merge + first real run: what did the "Note-state baseline recorded: N notes" log line say? (sanity check)
- For Phase 2 (when you say go): the golden-set list (30 candidates) needs your approval — including the one unresolved t.co short link.

## Notes for Phase 2 (from the real golden-set run)

- The candidate list contains **one unresolved t.co short link** (`https://t.co/Mxdio85wvQ`, likely `phosphoricons.com` per the CSV's own Domain column — the CSV flags it "Short link unresolved — domain guessed from display text, please verify"). The owner should confirm or swap it when approving the final list.
- The CSV's `Category` column is **empty in all 784 rows** — expected categories are assigned with the owner in Phase 2, as the spec says.
- The CSV has a `Needs Review` column (61 rows flagged, 4 unresolved t.co short links) the picker does not use; Phase 2 may want it when finalizing the golden set.

## Session log

(Agents: one line per session: date, phase, what was done, test result.)

- 2026-09-29, Phase 0 — groundwork on branch `phase-0-groundwork` (NOT merged): dry-run mechanism + `--dry-run` CLI flag, scan/snapshot/golden tools, 24 new tests, CI updated to 20 modules + 130 tests, VERSION 0.09.5, CHANGELOG + README, `docs/reports/PHASE-0-report.md`. Baseline 106 tests green before changes; final 130/130 green; end-to-end dry-run rehearsal on a synthetic vault left it byte-identical (one deadlock found and fixed on the way). Waiting for owner review.
- 2026-09-29, Phase 0 (same day, follow-up) — the owner committed `unique_links.csv` to `main`; ran `pick_golden_links.py` on the real file: 784 rows, 2 duplicates removed, 5 GitHub links excluded, 777 non-GitHub links, **30 candidates across 30 domains** selected deterministically (two runs identical). Real candidates JSON committed to the branch; full gate re-run green (20 compiles + 130/130 tests). One unresolved t.co short link noted for Phase 2.
- 2026-09-29, Phase 0 (same day, close-out) — owner waived the vault-copy scan review; Phase 0 marked **approved**. Branch left unmerged; awaiting the owner's word on merging before Phase 1 starts on top of it.
- 2026-09-29, pre-Phase-1 — owner provided all Phase 1 inputs (vault paths + backup repo names). Agent created both private repos via API and verified them (`assadigit/my-awesome-github-directory`, `assadigit/my-awesome-websites-directory` — both private, empty). Recorded in the Vault map. No code changes; bookkeeping only.
- 2026-09-29, Phase 1 — owner said "merge, then go": phase-0-groundwork merged into main (34fdc63), `phase-1-vault-settings` branched. Built: note_state record + baseline, ownership stamps + banner on new notes (human placeholder sections dropped from NEW notes per SPEC §4.5), 5 new config keys + GUI vault page + CLI status vault map, pipeline switches (github default on / websites default off), second VaultSeal gated on the websites switch. 38 new tests; full gate 21 compiles + 168/168 green. `docs/reports/PHASE-1-report.md` written. Branch pushed, NOT merged — in review.
