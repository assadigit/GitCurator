# GitCurator — Build Status

This file is the memory between sessions. Every session reads it first and updates it last.
Keep it short, factual, and in plain language.

- **Version at start of this build:** 0.13.0 (now 0.14.0 on the branch below)
- **Spec:** `SPEC.md` (root of the repo)
- **Current phase:** 5 — in review (agent finished, waiting for the owner). Phases 0–4 approved & merged.

## Phases

| Phase | Name | Status | Branch | Version | Owner approval |
|---|---|---|---|---|---|
| 0 | Groundwork | approved | `phase-0-groundwork` (**merged into main 2026-09-29**) | 0.09.5 | yes — review waived by owner 2026-09-29 |
| 1 | Vault settings and ownership | approved | `phase-1-vault-settings` (**merged into main 2026-09-29**, tag v0.10.0) | 0.10.0 | yes — owner said "Merge. proceed" 2026-09-29 |
| 2 | Website pipeline | approved | `phase-2-website-pipeline` (**merged into main 2026-09-29**, tag v0.11.0) | 0.11.0 | yes — owner said "proceed" 2026-09-29 (golden set approved as proposed; 50-link trial prepped) |
| 3 | Moves as corrections, and the backfill | approved | `phase-3-moves-backfill` (**merged into main 2026-09-29**, tag v0.12.0) | 0.12.0 | yes — owner said "Proceed. merge" 2026-09-29 |
| 4 | LLM backends | approved | `phase-4-llm-backends` (**merged into main 2026-09-29**, tag v0.13.0) | 0.13.0 | yes — owner said "Proceed" 2026-09-29 |
| 5 | Mirror into Manual Notes | approved | `phase-5-manual-mirror` (**merged into main 2026-09-29**, tag v0.14.0) | 0.14.0 | yes — owner said "proceed and merge" 2026-09-29 |
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

## Phase 3 summary (what exists on `phase-3-moves-backfill`)

- `core/note_state.py` grown into the full §4.4 machine: `apply_corrections` (front-matter line surgery + atomic write + lock + corrections log), `run_start_check` (baseline → detect → apply; dry-run detects and logs, records nothing), taxonomy-aware folder resolver for the Websites vault, `move_summary_lines` ("N notes moved from X to Y").
- New `cache.db` tables: `corrections_log` (append-only) + `note_state_dismissed` (deleted notes; named to avoid Phase 2's `dismissed_urls` collision — caught by the full suite).
- Wiring: start-of-run state machine for BOTH vaults (pipeline switches respected, dry-run aware); dismissed URLs filtered before any GitHub API call; run report "Note State" section; `--cli --status` corrections/dismissed counts; Recategorize dialog skips `category_locked` notes.
- Websites classifier never overrides a locked note (including retry upgrades) — tested through the real pipeline with the fake LLM.
- `tools/backfill_websites.py`: resumable (`backfill_state` checkpoint in `cache.db`), polite (fixed + per-domain pauses), batched (`--limit 30`), dry-run first, `--report`, Ollama or any OpenAI-compatible endpoint.
- Tests: `tests/test_phase3.py` (36). CI: 28 compiled modules, 275 tests + the offline golden run.
- Report for the owner: `docs/reports/PHASE-3-report.md`.

## Phase 4 summary (what exists on `phase-4-llm-backends`)

- `core/llm_client.py` — the shared LLM client: `openai_chat` (every OpenAI-compatible endpoint through the SAME wall-clock timeout wrapper as Ollama; `response_format` JSON mode with a memoized clean fallback when a server rejects it; clear `CloudLLMError` family on malformed bodies), `openai_list_models` + `preflight_openai` (the `/v1/models` pre-flight), `ollama_chat` (explicit `options.num_ctx` on EVERY call + the over-budget warning), `resolve_task_model` (per-task overrides), `estimate_tokens`.
- Relabel everywhere: "Cloud API" → **"OpenAI-compatible endpoint (llama.cpp, vLLM, LM Studio, cloud)"** — one constant (`CLOUD_PROVIDER_LABEL`), GUI radio + settings group + tooltips, CLI wizard/status, README. Config VALUE stays `cloud`; old configs load unchanged.
- New config (optional): `llm_num_ctx` (default 8192; 0 = server decides) + `models` (`{"classify": "", "analyze": ""}`). GUI: a shared Context-window field + Test Connection now starts with the `/v1/models` pre-flight; the batch-start pre-flight covers the default AND both overrides (warn-never-block).
- Per-task model overrides wired through both worker routers (GitHub analyze + websites classify/analyze), the backfill tool and the golden runner; the `llm_call` contract is now `llm_call(messages, task=None)`.
- The deferred Phase-3 few-shot item shipped: the w01 category prompt carries `PAST_CORRECTIONS` (this URL's own history + the owner's 3 most recent moves in the Websites vault, deduped) — the classifier follows the owner's filing taste.
- `tools/run_golden_websites.py --live --backend openai|ollama` (+ `--num-ctx`, per-backend pre-flight, transient-retry parity on both paths); comparison report: `docs/reports/golden-backends-report.md` — 20 links classified on both backends: 18/20 same category (16/20 same category+subcategory); the only category splits are the two known judgment-call sites, split 1-1 between the backends.
- Tests: `tests/test_phase4.py` (45). CI: 28 compiled modules, 320 tests + the offline golden run.
- Report for the owner: `docs/reports/PHASE-4-report.md`.

## Phase 5 summary (what exists on `phase-5-manual-mirror`)

- `core/mirror.py` — the one-way sync engine into `<manual_vault>/Library/GitHub Projects/…` and `<manual_vault>/Library/Websites/…`: mirror copies carry `mirror_of` front-matter + a read-only `> [!warning]` banner; matching by RAW `source` value so moves propagate and stale copies are removed; **only marker files are ever changed/deleted, only under Library/**; validated target paths (`..`/absolute/drive-letter/backslash rejected) + realpath containment re-verified before every write and delete; idempotent; `run_mirror(..., apply=False)` is the default dry-run.
- `tools/mirror_manual.py` — CLI: dry-run by default, `--apply` for a real run, `--manual-vault/--github-vault/--websites-vault` overrides (run against a COPY for the owner review), report file (default `app/reports/mirror/`, refuses to land inside any vault), exit codes 0/1/2/3.
- Refusals: manual vault unset, overlapping either machine vault in EITHER direction, `Library/` exists but is a file, `--apply` with a not-yet-existing manual folder.
- GitHub banner-image references are stripped from mirror copies (can never resolve inside the manual vault — see PHASE-5-report decision #2).
- Wiring: `--cli --status` "Library mirror" row; GUI 📁 Vault Manual Notes group updated + tool hint; README §"The Manual Notes Library mirror"; CHANGELOG; VERSION 0.14.0.
- Tests: `tests/test_phase5.py` (47). CI: 30 compiled modules, 367 tests + the offline golden run.
- Report for the owner: `docs/reports/PHASE-5-report.md` (§3 = the copy-rehearsal commands; §7 = 3 questions).

## Inputs still needed from the owner

- [x] `website-library-categories.md` placed at `app/taxonomy/` (done; needed by Phase 2)
- [x] **Phase 2 review** — owner said "proceed" 2026-09-29: golden set approved as proposed, phase 2 merged (v0.11.0), 50-link trial prepped (`docs/trials/`)
- [x] **Phase 3 review** — done 2026-09-29: owner said "Proceed. merge" — merged into main (2caad6d, tag v0.12.0); local gate green before merging (28 compiles + 275/275 + offline golden 30/30, 0 invalid) and the mirror gate green on main after (run 36578119089).
- [ ] **Cloudflare token permission**: the new Workers AI token (cfut_…, given 2026-09-29) verifies as active but still lacks the Workers AI permission — every `/ai` endpoint answers "Authentication error". Fix: Cloudflare dashboard → My Profile → API Tokens → the token → Edit → add **Account → Workers AI → Read** → Save. Then the app's cloud provider can use `cloud_api_url = https://api.cloudflare.com/client/v4/accounts/20b665b0bc839144c1e9f16aaf07953d/ai/v1`.
- [x] **`unique_links.csv`** (done — the owner committed it to the repo root on `main`; the picker ran for real: 784 rows → 30 candidates across 30 domains → `app/tests/golden/websites_candidates.json`, committed on `phase-0-groundwork`)
- [x] **Phase 1 inputs** (received 2026-09-29, see "Vault map" below)
- [x] Manual Notes vault path — **received 2026-09-29**: `C:\Users\Ali Assadi\Documents\Obsidian Vault` (recorded in the Vault map below; Phase 5 wired, merged v0.14.0)
- [x] ~~Path of a **copy** of the GitHub vault, for the Phase 0 owner review~~ **waived by the owner 2026-09-29** ("this is unnecessary") — Phase 0 approved without the scan. The scan tool stays available any time via one command.
- [ ] **Phase 2 review**: superseded 2026-09-29 — owner said "proceed" (approved as proposed, merged v0.11.0).
- [x] **GitHub Actions billing**: worked around 2026-09-29 per the owner's instruction — both directory repos public until Oct 1 (unlimited free minutes); the mirror gate in the websites repo runs the full suite against private GitCurator. Billing itself still needs fixing in GitHub Settings → Billing & plans for private-repo Actions.
- [x] **Cloudflare tokens**: the owner provided a NEW token 2026-09-29 — still missing the Workers AI permission (see "Inputs still needed" above for the exact fix).

## Vault map (owner-provided 2026-09-29 — the single source of truth for Phase 1+)

**Temporary repo state (2026-09-29 → 2026-10-01, owner instruction):** both directory repos are **PUBLIC** until the month resets (unlimited free GitHub Actions minutes — see the mirror CI gate below). **Do not run a vault seal to either repo while public** — notes would be public. Revert both to private after Oct 1. GitCurator itself must stay **private**: its git history contains live secrets (Telegram bot token, an old-but-live GitHub PAT, api_id/api_hash) — see the session log 2026-09-29 (phase-3 session) for remediation options.

| What | Windows path / repo | Config key it will land in |
|---|---|---|
| GitHub Projects vault (existing, 600+ notes) | `G:\Docs\Github Projects Obsidian Vault` | `vault_path` (unchanged meaning) |
| Websites vault (new; folder may not exist yet — status "will be created" is fine) | `G:\Docs\Documents\Obsidian Website Directory` | `website_vault_path` |
| Manual Notes vault (owner-owned; receives the read-only `Library/` mirror — Phase 5) | `C:\Users\Ali Assadi\Documents\Obsidian Vault` | `manual_vault_path` |
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
- 2026-09-29, Phase 5 — overlap refusal in BOTH directions (spec names only "equals or contains"; manual-inside-machine is equally dangerous). Rejected: literal one-direction check. Banner-image references stripped from mirror copies (can never resolve inside the manual vault; fixing them needs writes outside Library/). Rejected: copying banners (violates the Library/ jail), leaving broken links. Tool-only invocation (no auto-run at batch end — your vault is written when you say so). Rejected: wiring the mirror into every batch finish. Matching by RAW source value (exact identity; can't drift when URL normalization changes). Rejected: matching by normalized URL. Full list with alternatives: `docs/reports/PHASE-5-report.md` §5.

## Open questions for the owner

- ~~Phase 4 review~~ — answered 2026-09-29 ("Proceed" — merged, v0.13.0).
- ~~Phase 5 review~~ — merged 2026-09-29 (owner: "proceed and merge", v0.14.0). Still open for the owner afterwards: the 5-minute in-Obsidian verification on a COPY of Manual Notes (`docs/reports/PHASE-5-report.md` §3 — dry-run, `--apply`, `[[a library note]]` backlink check) and the 3 small questions in report §7 (auto-run at batch end? banner images in mirrors?).
- **Phase 6 go**: Linking (recall hooks, embeddings, candidate search, link store) per SPEC §6 — starts on the owner's word, after the Phase 5 in-Obsidian verification.
- The backfill: first real batches run here into a test vault (readable notes before you touch your machine), or you run it yourself after merging? (Still open — also blocked on a Workers-AI-capable token or your local Ollama for the live LLM.)
- After the first real v0.12.0 run: what did the "Note-state baseline recorded: N notes" log line say? (sanity check)

## Notes for Phase 3

- **Mirror CI gate** (2026-09-29): the public `my-awesome-websites-directory` repo runs GitCurator's exact gate against the private repo via the `GC_PAT` secret + `repository_dispatch` — free unlimited Actions minutes while the billing block lasts. Trigger: API call `event_type=gate` with optional `ref` (any branch/tag); weekly heartbeat cron. First green cloud run since Sep 24: run 36569001597 (v0.11.0 gate on phase-2 branch, then main).
- **Secrets in GitCurator's history** (found 2026-09-29 before making it public — it stayed private): the LIVE Telegram bot token + api_id/api_hash in old commits (`app/config.json`, `installer.config.json`, `cloudflare-bot/DEPLOYMENT.md` — all since removed from HEAD, but present in history), plus ONE still-live fine-grained GitHub PAT and one dead ghp_ token. Options if the owner ever wants it public: (a) rotate the bot token (BotFather /revoke) + the PAT, accept the permanent api_id/hash exposure; (b) history purge (git filter-repo — rewrites every commit hash, needs a re-clone on Windows); (c) keep it private (current choice). Recommended regardless: rotate that old PAT.
- Runner-image fix: GitHub runners stopped bundling `libEGL.so.1` (same issue as the build sandbox) — both CI files now `apt-get install libegl1 libgl1 libxkbcommon0 libdbus-1-3 libfontconfig1 libglib2.0-0` before the suite.

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
- 2026-09-29, merge-2 + infra (owner: "make the two repos temporarily public… unlimited github actions / use this token / yes prep them / proceed") — (1) phase-2 addendum on the branch (50-link trial file + `docs/trials/README.md` guide, Websites switch labels updated to v0.11.0 reality, ci.yml installs Qt system libs — runner images stopped bundling libEGL) then **phase 2 merged into main** (`2cf8f4c`, tag **v0.11.0**); (2) both directory repos flipped **public until Oct 1** per the owner's instruction — a mirror CI gate (`.github/workflows/gate-runner.yml` + `GC_PAT` secret in the public websites repo) now runs the full gate against private GitCurator: first green cloud run since the Sep-24 billing block (run 36569001597, and main after the merge); (3) GitCurator itself was NOT made public — a history secret-scan found the live bot token + a live old PAT in early commits (kept private, options documented); (4) the new Cloudflare token verified ACTIVE but still without Workers AI permission (exact fix in "Inputs still needed"); (5) 50-link trial prepped from the real CSV (same spread rule as the golden set, 50 domains). Full gate green before merging: 27 compiles + 239/239 + offline golden 30/30.
- 2026-09-29, Phase 3 — **moves as corrections + the backfill (v0.12.0)** on branch `phase-3-moves-backfill` (NOT merged): the §4.4 state machine runs at the start of every batch for both vaults (moves→corrections with front-matter updates + `category_locked` + corrections log; edits flagged; deletes dismissed forever; duplicates/unmanaged/unmapped report-only); taxonomy-aware folder resolution for the Websites vault (nested legal folders never "unmapped" — a real bug the tests caught); locked notes beat the classifier (pipeline + Recategorize dialog); `tools/backfill_websites.py` (resumable checkpoint, polite, batched, dry-run first, Ollama or OpenAI-compatible); run report "Note State" section + `--status` counts. 36 new tests; **full gate 28 compiles + 275/275 + offline golden 30/30, 0 invalid**. Test-caught bugs fixed en route: the dismissed-table name collision with Phase 2 (no such column: url), unmapped moves incorrectly treated as corrections, and the `p3_moc_` tmp-prefix trap (a `_moc` substring in a temp path makes the vault walk skip everything). `docs/reports/PHASE-3-report.md` written. In review.
- 2026-09-29, merge-3 (owner: "Proceed. merge") — `phase-3-moves-backfill` **merged into main** (`2caad6d`, tag **v0.12.0**, pushed). Local gate re-run green before merging: 28 compiles + 275/275 tests + offline golden 30/30 (0 invalid). Mirror gate green on main after the merge (run 36578119089). Phase 4 (LLM backends) started on branch `phase-4-llm-backends` (v0.13.0): relabel "Cloud API" → "OpenAI-compatible endpoint", shared timeout wrapper, JSON mode with clean fallback, explicit context window (`num_ctx`), `/v1/models` pre-flight, per-task model overrides (`models.classify`/`models.analyze`) + the deferred Phase-3 few-shot item (past corrections as classifier examples).
- 2026-09-29, Phase 4 — **LLM backends (v0.13.0)** on branch `phase-4-llm-backends` (NOT merged): the relabel (one `CLOUD_PROVIDER_LABEL` constant, GUI/CLI/README), the shared client in `core/llm_client.py` (same timeout wrapper for both providers, JSON-mode `response_format` with a memoized clean fallback, explicit `num_ctx` on every Ollama call + over-budget warnings on both, `/v1/models` pre-flight at batch start + Test Connection, per-task model overrides `models.classify`/`models.analyze` through every router), the deferred Phase-3 few-shot hook (w01 `PAST_CORRECTIONS` from the corrections log), `--live --backend openai|ollama` for the golden runner (+ transient-retry parity, found the hard way), backfill through the shared helpers. 45 new tests; **full gate 28 compiles + 320/320 + offline golden 30/30, 0 invalid**. Live comparison run on both backends (llm-shim + a disclosed Ollama-API bridge, `options.num_ctx` verified on the wire): 18/20 same category, 16/20 same category+subcategory, splits only on the two known judgment-call sites — `docs/reports/golden-backends-report.md`. In review.
- 2026-09-29, merge-4 (owner: "Proceed") — `phase-4-llm-backends` **merged into main** (`0376bd6`, tag **v0.13.0**, pushed). Local gate re-run green before merging: 28 compiles + 320/320 tests + offline golden 30/30 (0 invalid). Mirror gate dispatched on main after the merge. Phase 5 (Mirror into Manual Notes) started on branch `phase-5-manual-mirror`; owner provided the Manual Notes vault path in the same message.
- 2026-09-29, Phase 5 — **the Manual Notes Library mirror (v0.14.0)** on branch `phase-5-manual-mirror` (NOT merged): `core/mirror.py` (one-way sync into `<manual>/Library/{GitHub Projects,Websites}/`, `mirror_of` marker + read-only banner, RAW-source matching so moves propagate, marker-only deletions, outside-Library guarantee with validated paths + realpath containment, idempotent, dry-run default) + `tools/mirror_manual.py` (dry-run default, `--apply`, vault-path overrides for the copy rehearsal, report file, exit codes) + status/GUI/README wiring. Owner provided the Manual Notes vault path (`C:\Users\Ali Assadi\Documents\Obsidian Vault` — vault map updated). 47 new tests; **full gate 30 compiles + 367/367 + offline golden 30/30, 0 invalid**. Test-caught en route: mirror banner inserted inside front matter (off-by-one after the `mirror_of` insert); Bash-tool display artifact (`[h` sequences eaten — same class as merge-1's `[m`; verify bracket text via Read) briefly masqueraded as a Python `%`-formatting bug. `docs/reports/PHASE-5-report.md` written. In review.
- 2026-09-29, merge-5 (owner: "proceed and merge") — `phase-5-manual-mirror` **merged into main** (`7ba893f`, tag **v0.14.0**, pushed). Local gate re-run green before merging: 30 compiles + 367/367 tests + offline golden 30/30 (0 invalid). Mirror gate green on the branch before merging (run 36592301839 — checkout ref phase-5-manual-mirror, 367 tests in 31.9s) and on main after the merge (run 36597282918). Phase 6 (Linking — recall hooks, embeddings, candidate search, link store) awaits the owner's go, after the Phase 5 in-Obsidian verification (PHASE-5-report §3) and its §7 questions.
