# GitCurator — Agent Spec (SPEC.md)

You are an AI coding agent joining an existing project. This file is your briefing.
It is written to be read at the start of **every** session. Read all of it.

- Spec written against **GitCurator v0.09.4**. Every file name, function name and line reference below was true at that time. **If reality differs, reality wins.** Do not force the code to match this file: note the difference in your report and in `STATUS.md`.
- *(Update, v0.26.0 hygiene pass: `STATUS.md` and the other session-history docs were archived to `docs/history/` — new differences go in the report and CHANGELOG, not a new STATUS.)*
- Current progress lives in `STATUS.md`. Read it right after this file. *(Archived at `docs/history/STATUS.md` since v0.26.0 — `CHANGELOG.md` is the living history now.)*

---

## 0. Who you work for, and how to talk to them

The owner is a designer and self-described "vibe coder". **They cannot read or patch code.** They rely on you to deliver complete, working results and to prove they work.

- Explain everything in **plain language**. No jargon without a one-line explanation.
- Deliver **complete files**, never fragments or placeholders. Any script you add starts with a clearly marked configuration block.
- Never say "done" without showing the test output that proves it.
- The owner prefers quality over speed. Take the time to get it right and verify it.
- The owner runs **Windows** (the app ships `.bat` launchers). Use `os.path`, open every file with an explicit UTF-8 encoding, and do not use symlinks.

## 1. Mission

GitCurator turns links sent to a Telegram bot into Obsidian notes, using a local LLM. Today it only understands **GitHub repositories**. The mission is to grow it, phase by phase, into a small "library builder" for a personal second brain:

1. **GitHub Projects vault** (exists, 600+ notes): AI-directed directory of hand-picked repos.
2. **Websites vault** (new): AI-directed directory of useful websites and tools, filed into a category taxonomy.
3. **Manual Notes vault** (owner-written): where the owner's ideas live. It later receives a read-only mirror of the two libraries so ideas can link to them.
4. A later **linking layer** connects an idea to the projects and sites that could help with it.

The end goal is **recall**: when an idea arises, the owner can find the right project or site quickly. Tidiness is only a means to that.

## 2. Non-negotiables

1. **Never touch the owner's real vaults during development.** Use temporary folders and fake data. A run against a real vault happens only after: a dry-run, a snapshot (Phase 0 tool), and the owner's explicit OK in the session.
2. **Never delete owner notes.** Never overwrite text a human changed.
3. **Never print, log, or commit secrets.** `app/config.json`, `app/cache.db`, Telethon session files and vault content are git-ignored. Keep them that way. Use synthetic fixtures in tests.
4. **The GitHub pipeline must behave exactly as before** unless a phase says otherwise. Existing config files must keep working. Missing new keys fall back to safe defaults.
5. **All existing tests must keep passing**, and every phase adds tests for what it builds (see section 3).
6. **LLM output is untrusted input.** Everything that goes into YAML frontmatter, a filename, or a path passes through the sanitizers in `core/note_builder.py` and `core/storage.py` (`safe_filename`, `sanitize_*`). A security audit fixed this once; do not regress it.
7. **Every network call and every LLM call has a timeout.** One failing item never stops the batch. One failing vault never stops the others.
8. **Keep new logic out of the GUI.** `app/gitcurator/gui/app.py` is about 11,500 lines and also drives the headless CLI. Add new logic as plain modules under `app/gitcurator/core/` with **no PyQt imports**, and make only thin, surgical hooks in `app.py` and `cli.py`.
9. **Do not touch:** `dashboard/`, `app/cloudflare-bot/`, `app/gitcurator/cloud/`, `app/_attic/`. Out of scope. *(Update, v0.25.0 hygiene pass: `app/_attic/` was removed — owner-directed Tier A cleanup; `app/cloudflare-bot/` was opened for this pass's contract fixes. Reality wins.)* *(Update, v0.26.0 hygiene pass: the root `dashboard/` verification console was removed — it duplicated the CI gate; recoverable from git history. `app/gitcurator/cloud/` stays, dormant by owner decision "fixed, not deleted".)* *(Exception, owner-granted 2026-09-30: `app/cloudflare-bot/` was opened for v0.22.0 — the bot now accepts website links per §4.2/§4.3; see CHANGELOG [0.22.0]. The rest of the list stands.)*
10. **Minimal dependencies.** Prefer the standard library plus what is already in `app/requirements.txt`. If you believe a new package is needed, stop and ask (section 3).
11. **Atomic writes only** (`atomic_write_text` in `core/storage.py`) for anything written into a vault.
12. **Do not merge to `main`.** Work on a branch. The owner merges.

## 3. Working protocol (every session)

1. **Read** this file, then `STATUS.md`, `app/README.md`, the newest entry of `CHANGELOG.md`, and the code areas your phase lists.
2. **Verify** the "code facts" in section 5 that your phase depends on. Record any difference in `STATUS.md`.
3. **Plan** in at most 15 lines (put it at the top of your report), then build. Do not ask for approval of the plan unless a stop condition below applies.
4. **Branch:** `phase-<N>-<short-name>`. Commit in small steps with clear messages.
5. **Test:** from the `app/` folder run the compile check and the test suite exactly as CI does (see `.github/workflows/ci.yml`). Headless Qt: set `QT_QPA_PLATFORM=offscreen`.
   - Add every new module to the compile list in `ci.yml`, every new test module to its `unittest` command, and update the test counts in its comments.
6. **Version and docs:** bump `VERSION`, add a `CHANGELOG.md` entry in the existing prose style (what the owner would notice, and why), and update `app/README.md` for anything user-visible. Suggested versions are in section 6.
7. **Report** (template below), update `STATUS.md`, and **stop**. Do not start the next phase. *(v0.26.0: reports land in `docs/history/reports/` now; the phased era is complete.)*

### Stop and ask the owner when

- The spec and the code disagree on something that changes what data is written.
- A step would write to a real vault, or delete anything.
- You believe a new dependency is required.
- An existing test fails **before** you changed anything (report it; do not "fix" it silently).
- You would have to change GitHub-pipeline behavior that the phase did not mention.
- The taxonomy file cannot be parsed unambiguously.

### Report template (save as `docs/reports/PHASE-<N>-report.md`, and repeat it in chat)

1. **What I built**: plain language, at most 10 lines.
2. **What changes for you**: what you will see or do differently. Say "nothing" if that is true.
3. **How to check it yourself**: exact copy-paste commands or clicks, taking about 5 minutes.
4. **Test results**: the counts (old tests, new tests, all passing).
5. **Decisions I made**: each with the alternative I rejected.
6. **Risks and unknowns**: anything I could not verify.
7. **Questions for you**: at most 3.

## 4. Target architecture

### 4.1 Vaults and ownership

| Vault | Owner | Written by | Backup |
|---|---|---|---|
| GitHub Projects (config key `vault_path`, unchanged) | Machine | The app only | Existing private repo (VaultSeal) |
| Websites (`website_vault_path`, new) | Machine | The app only | Own private repo (a second VaultSeal instance) |
| Manual Notes (`manual_vault_path`, Phase 5) | The owner | The owner only, plus the read-only `Library/` mirror | Owner's own repo; the `Library/` folders are excluded (rebuildable) |

Ownership rules:

- Machine-owned notes carry `managed_by: gitcurator` in frontmatter and a short banner line.
- The **one human action allowed** in a machine vault is **moving a note between category folders** (section 4.4).
- If a machine note's *text* was changed by hand, the app flags it in the run report and leaves it alone.
- The app never writes into Manual Notes, except the `Library/` mirror.
- Suggested connections are never written into the owner's own notes.

### 4.2 Intake

One Telegram bot (the existing one) receives everything; there are no commands or prefixes.

- `github.com` repo links go to the GitHub pipeline.
- `owner.github.io/repo` is mapped to `github.com/owner/repo` and goes to the GitHub pipeline.
- Gists (`gist.github.com`) and every other link go to the Website pipeline; gists get the tag `#snippet`.
- Telegram only confirms receipt (as it does today for GitHub links). Linking is silent.

### 4.3 Website pipeline (per link)

1. **Canonicalize** the URL (http/https, `www`, trailing slash, `utm_*`/`fbclid`/`gclid`/`ref` parameters, fragments all ignored). Reuse and, only if needed, extend the existing `normalize_url` (used by `VaultIndex`; likely in `core/links.py`). Do not change its behavior for GitHub links.
2. **Dedupe** against the Websites vault index, the `cache.db` tables, and the dismissed list. If already known, skip and report.
3. **Fetch politely**: timeout, size cap, per-domain rate limit, a clear User-Agent. Record `fetch_status`: `full`, `partial` (JavaScript-only or paywalled), or `failed`.
4. **Extract** title, meta description, main text.
5. **Classify** in two passes with the taxonomy (4.6): category, then subcategory. Low confidence goes to `_review`.
6. **Analyze** into the fields in 4.5, using the prompts in Appendix A. Rule: omit rather than guess.
7. **Build and write** the note atomically to `<website_vault>/<Category>/<Subcategory>/<Name>.md` (or `<Category>/<Name>.md` when there is no subcategory).
8. **Seal** (back up) the vault; send the receipt.

Failure handling: a link that cannot be fetched still gets a minimal note in `_review` with `fetch_status: failed`, and is retried automatically up to 3 times over several days (state kept in `cache.db`). Nothing is silently dropped.

### 4.4 Moves are corrections

The owner will constantly fix the LLM's mistakes by **moving a note to another category folder**. The app must treat this as a correction, never as damage.

Identity of a note is its normalized `source:` frontmatter URL (this is what `VaultIndex` already keys on). Keep using it; do **not** invent a second ID.

The app keeps a persistent record per note in `cache.db` (new table, created in Phase 1): vault, source URL, path, a fingerprint of the file content at last write (excluding app-managed blocks), category, subcategory, locked flag. At the start of every run, **before anything else**, it compares each machine vault with that record:

| Situation | What the app does |
|---|---|
| Same note, different folder (**moved**) | Accept it. Update the note's `category` / `subcategory` lines and tags to match the new folder (targeted line edit, atomic write), set `category_locked: true`, log a correction, never move it back. Moving a note out of `_review` into a category folder counts as a correction too. |
| Same place, content changed by hand (**edited**) | Flag it in the run report. Skip it. |
| Note gone (**deleted**) | Add its URL to a dismissed list. Never re-add. List it in the run report (it may have been accidental). |
| Two files, same source (**duplicate**) | Flag both. Touch neither. |
| File in a machine vault with no `source:` (**unmanaged**) | Ignore it. List it. |
| Moved into a folder that is not in the taxonomy (**unmapped**) | Keep it there. Mark it `unmapped` in the report. Never invent a category. |

Rules of the road:

- The first run after this feature ships **records a baseline silently**. It must not treat 600+ existing notes as "changed".
- Renaming a category is done in the taxonomy file, and the app then renames the folders. Renaming folders by hand looks like a mass move into unknown folders; the app must detect that and report it instead of acting.
- The special folders that `VaultIndex` skips (`_moc`, `_inbox`, `attachments`, `.obsidian`) are skipped here too. `_review` is **not** skipped.
- Category names in a GitHub note's frontmatter come from `CATEGORY_FOLDERS` in `constants.py`. Map folder back to category key with a reverse lookup.
- The public directory and master-index generators (`integrations/goodrepos.py` and the `_moc` files) read categories. After a move they must still produce correct output.

### 4.5 Note formats

**Common frontmatter on every new machine note:** `source`, `aliases`, `tags`, `category`, `date_processed`, `managed_by`, `schema_version`, `prompt_version`. Websites add `subcategory`, `fetch_status`, `pricing`, `login_required`. `category_locked` is added only after a move.

**Website note body** (every field written by the app):

1. Name (title)
2. One-line description (at most 25 words), shown as the TL;DR line
3. Core offerings: 3 to 6 concrete bullets
4. Standout feature: the one thing most worth knowing (may be empty)
5. **Best used for**: one sentence starting "Use when you need to…", written around the problem, not the site's marketing. This is the **recall field**; the linking layer relies on it.
6. Pricing and sign-up: `free|freemium|paid|unknown`, login `yes|no|unknown`
7. Similar tools: only when clearly confident, otherwise empty
8. Source link

**GitHub notes:** same structure as today, with these changes. New notes no longer contain the three human placeholder sections ("My Ideas & Notes", "Social Signal (Manual)", "Journal"); that writing now lives in Manual Notes. New notes get the common frontmatter above. **Existing notes are not rewritten** except by Phase 6's single delimited "recall" block.

### 4.6 The taxonomy file is the source of truth

The owner's `website-library-categories.md` lives at `app/taxonomy/website-library-categories.md`. The app parses it. It is not merely documentation.

Parsing guide (be tolerant; the file is written for humans):

- `### <Category>` headings are categories. Strip emoji and trailing italic notes in parentheses.
- Top-level bullets under a category are subcategories. Strip bold markers and everything after an em dash `—`. A category marked "(no subcategories)" has none.
- A line starting `Example tags:` gives tag hints for that category.
- The section "Judgment-call rules" is passed verbatim to the classifier as rules.
- Ignore "How this works" and "Not yet covered".
- Empty categories ("empty for now") are still real categories and are wired in from day one.
- If the file is ambiguous in a way that changes filing, stop and ask.

Use of the file:

- **Prompt injection:** pass 1 receives only category names, one-line definitions, and the rules. Pass 2 receives only the subcategories of the chosen category. Prompts stay short.
- **Validation:** the model's answer must exactly match a parsed name. If not, reject and retry up to 2 times, then fall back to `_review`.
- **Folders:** the same parse creates the vault folders (use `safe_filename`; category names contain `&` and `/`).

### 4.7 LLM layer

Three backends are required: **Ollama**, **llama.cpp**, and an **OpenAI/Claude-compatible cloud API**. The existing `llm_provider: "cloud"` path already speaks the OpenAI chat format, which `llama-server` and most cloud APIs also speak. So the work is hardening and relabelling, not a new provider. Keep the existing config key names (`llm_provider`, `cloud_api_url`, `cloud_api_key`, `cloud_model`) so current configs keep working.

### 4.8 Linking layer (Phase 6, built last)

- Embed only the recall field, the one-line description and the tags. Never full text.
- Find neighbors across domains and within a domain (for example, similar palette generators). An LLM confirms each candidate with a yes/no and a short reason.
- Links go into a central store and are written only into the `Library/` mirror copies (a delimited "Related (auto)" block) and a `Suggestions` note. Never into river vaults, never into the owner's notes.
- Cap of about 5 to 7 links per note.
- The first 50 suggestions wait for the owner's approval (status `pending`, `approved`, `rejected`). Rejected pairs are never suggested again.
- Silent otherwise: no Telegram messages about links.

## 5. Code facts to verify (as of v0.09.4)

Layout: `app/main.py`; package `app/gitcurator/` with `cli.py`, `constants.py`, `core/` (`links.py`, `llm_client.py`, `note_builder.py`, `storage.py`), `integrations/` (`vaultseal.py`, `goodrepos.py`, `telegram_fetch_worker.py`, `telethon_fetcher.py`, `backfill_manager.py`, `subprocess_runner.py`, `error_reporter.py`), `gui/app.py` (`ProcessingWorker`, `CacheDB`, `VaultIndex`), `tools/`. Also `app/prompts/` (`01_categorize.txt`, `02_summarize.txt`, `03_crosscheck.txt`), `app/system_prompt.txt`, `app/about_me.md`, `app/config.example.json`, `app/tests/`, `.github/workflows/ci.yml`.

- **`VaultIndex`** (`gui/app.py`, about line 458): in-memory map from normalized `source:` URL to note path. It reads the first ~800 characters of each `.md`, skips paths containing `_moc`, `_inbox`, `attachments`, `.obsidian`, and includes `_review`. It is rebuilt at the start of each run and is the ground truth for "already processed".
- **Four anti-repeat layers** (documented in the 0.09.4 changelog entry): (1) `VaultIndex`; (2) `cache.db` (`processed_repos`, 404 quarantine, retry queue), with its path resolved to the app folder; (3) `last_processed_msg_id`; (4) marking bot messages read only when everything verified. **Preserve and extend these. Never bypass them.**
- **`core/links.py`**: `split_links()` separates GitHub from non-GitHub links. Non-GitHub links currently only reach `_inbox/non_github_links.md` (written by an inbox helper inside `ProcessingWorker`; verify the exact name). That dead end is the hook point for the Website pipeline.
- **`core/llm_client.py`**: a timeout wrapper (`call_with_timeout`) and JSON extraction. **`ProcessingWorker._call_llm`** routes on `llm_provider`. **`_call_cloud_llm`** (about line 3276 of `gui/app.py`) posts to `<api_url>/chat/completions` with only a plain `urllib` timeout; it does not use the wrapper, and it sends no JSON-mode request field.
- **`core/note_builder.py`**: `build_note()` is GitHub-shaped. The sanitizers (`sanitize_tags`, `sanitize_aliases`, `yaml_scalar`, `sanitize_body_text`, `sanitize_short_summary`, `sanitize_seq_item`) are generic and reusable. Current GitHub frontmatter: `source`, `aliases`, `tags`, `category`, `stars`, `org`, `primary_language`, `languages`, `credibility_score`, `date_processed`, `last_release`, `cover`, `quality`, `quality_issues`.
- **`core/storage.py`**: `atomic_write_text`, `atomic_write_bytes`, `safe_filename`, `unique_path`, `build_note_filename`, `merge_config` (merges unknown top-level keys without a whitelist), `write_config_file`.
- **`integrations/vaultseal.py`**: `VaultSeal` takes `vault_path` and repo name per instance; `seal_from_config` builds one from config. A second vault means a second instance.
- **`constants.py`**: `CATEGORY_FOLDERS`, `CATEGORY_KEYS`, `DEFAULT_SYSTEM_PROMPT`. `system_prompt.txt` is read at call time.
- **`about_me.md`** personalizes the GitHub prompts ("for your specific objectives…"). The neutral prompts for websites and recall hooks must **not** use it.
- **CI** (`.github/workflows/ci.yml`, Python 3.12, headless Qt): compiles 16 listed modules, then runs `python -m unittest tests.test_core tests.test_e2e tests.test_goodrepos tests.test_reliability tests.test_quarantine` (106 tests at v0.09.4).
- **Versioning:** `VERSION` file plus `CHANGELOG.md` (SemVer, detailed prose, "Owner report / Diagnosis" style).

## 6. Phases

Each phase is useful on its own. Do **one phase per session**. Effort is in rough working sessions. Suggested version bumps in brackets.

### Phase 0 — Groundwork (about 1 session) [0.09.5]

Goal: tools and safety nets only. **No behavior change to the app.**

Do:
- `tools/scan_vault_edits.py`: **read-only** scan of a vault path given on the command line. Reports how many notes exist per category; notes missing `source:`; duplicate `source:` values; and notes where the three human sections ("My Ideas & Notes", "Social Signal (Manual)", "Journal") contain anything beyond the template placeholder text. Output a readable report (Markdown) to a folder outside the vault.
- `tools/snapshot_vault.py`: zips a vault into a timestamped file outside the vault (standard library `zipfile`).
- A **dry-run mechanism**: a small helper so any vault write can be logged instead of performed, plus a `--dry-run` flag on the CLI run. Default behavior stays unchanged.
- `tools/pick_golden_links.py`: reads the owner's `unique_links.csv` (path given as an argument; inspect its columns first) and selects a diverse set of 30 links (spread across domains and categories) as a candidate golden set, written to `app/tests/golden/websites_candidates.json`. The owner approves the final list in Phase 2.
- Create `docs/reports/` and keep `STATUS.md` up to date.
- Tests for the scan and snapshot tools using synthetic vaults.

Acceptance: tools run on a synthetic vault; running them modifies nothing; CI green.
Owner review: run the scan on a **copy** of the GitHub vault and read the report.

### Phase 1 — Vault settings and ownership (1–2 sessions) [0.10.0]

Goal: separate vault settings and ownership markers, with the GitHub pipeline unchanged.

Do:
- New config keys (all optional, safe defaults): `website_vault_path`, `manual_vault_path`, `website_repo_name`, `taxonomy_path`, and pipeline switches (`pipelines.github` default on, `pipelines.websites` default **off**). `vault_path` keeps meaning "GitHub vault".
- Vault tab in the GUI: folder pickers for the new paths, each with a live status ("found", "will be created", "not set"), and the pipeline switches. `--cli --status` shows the same information. Blank paths degrade gracefully with a warning.
- `managed_by`, `schema_version`, `prompt_version` and the ownership banner on **new** notes only. Drop the three human placeholder sections from new GitHub notes (update the tests that expect them).
- New module `core/note_state.py` (no PyQt): the `cache.db` table and helpers from section 4.4, using the same resolved database path as `CacheDB` (do not reintroduce the working-directory bug fixed in 0.09.4). Baseline recording exists but is not yet acted on.
- A second `VaultSeal` instance for the Websites vault, used only when that pipeline is on.

Acceptance: with `pipelines.websites` off, a run behaves exactly as v0.09.4; an old `config.json` loads unchanged; `VaultIndex` works on two vaults; CI green.
Owner review: open the settings screen; run the CLI status; run a normal GitHub batch on a **test** vault and compare.

### Phase 2 — Website pipeline (3–4 sessions) [0.11.0]

Goal: everything in 4.3 and 4.6, ending with a trial run.

Do (all new logic in `core/`, no PyQt):
- `core/taxonomy.py`: the parser and validator from 4.6, tested against the real file (copy it into `tests/fixtures/`).
- `core/web_fetch.py` and `core/web_extract.py`: fetching and extraction (standard library first). Test with **saved HTML fixtures**, including: a normal article-like page, a marketing landing page, a JavaScript-only shell, a paywall stub, non-UTF-8 text, a huge page, a PDF link, a redirect, and a timeout.
- `core/website_pipeline.py` and a website note builder (reuse the sanitizers); prompts as files in `app/prompts/` (`w01_category.txt`, `w02_subcategory.txt`, `w03_analyze.txt`) from Appendix A, with a loader that refuses to run if any `{{slot}}` is left unfilled.
- Routing: extend `split_links()` for `github.io` and gists; when `pipelines.websites` is on, non-GitHub links go through the pipeline instead of the `_inbox` dead end (the old behavior stays when it is off).
- Failure handling, retries and `_review` behavior from 4.3. The website vault gets its own `VaultIndex` and dedupe.
- **Golden set**: finalize `tests/golden/websites.json` from the candidates with the owner's approval (each entry: URL, expected category, optional subcategory). A runner (`tools/run_golden_websites.py`) produces a side-by-side Markdown report. An offline mode with a fake LLM runs in CI; the live LLM run is manual.
- Dry-run everywhere.

Acceptance: fake-LLM pipeline tests pass; every model answer is validated against the taxonomy; no crash on any fixture above; nothing is written in dry-run; the live golden run produces 100% valid category names.
Owner review: read the golden-set report; then approve a 50-link trial into a **test** Websites vault and read those notes.

### Phase 3 — Moves as corrections, and the backfill (2 sessions) [0.12.0]

Goal: section 4.4 in full, then load the ~930 existing bookmarks.

Do:
- The state machine as pure functions in `core/note_state.py`; run it at the start of each run for both vaults; add its results (moved, edited, deleted, duplicate, unmanaged, unmapped) to the run report.
- The corrections log and dismissed list (tables in `cache.db`). The classifier skips locked notes.
- Confirm `goodrepos.py` and the `_moc` master-index outputs stay correct after moves.
- `tools/backfill_websites.py`: reads `unique_links.csv`; slow, polite, resumable (checkpoint in `cache.db`); dry-run first; small batches; progress report.
- Optional, only if simple: put the most similar past corrections into the classifier prompt as examples, and add a report line for repeated moves ("9 notes moved from X to Y").

Acceptance: simulated moves, edits, deletes, duplicates and unmapped folders in a temp vault produce exactly the outcomes in 4.4; running twice changes nothing more; the baseline run flags nothing on an existing vault; the backfill resumes after being interrupted.
Owner review: run the backfill in dry-run, then a 30-link real batch; move two notes by hand; run again and read the report.

### Phase 4 — LLM backends (1–2 sessions) [0.13.0]

Do:
- Relabel the "Cloud API" option as "OpenAI-compatible endpoint (llama.cpp, vLLM, LM Studio, cloud)" in the GUI, CLI and README.
- Route the cloud path through the same timeout wrapper as Ollama.
- Send JSON-mode (`response_format`) where supported, and fall back cleanly when the server rejects it.
- Set the context window explicitly (Ollama `num_ctx`; the equivalent server setting elsewhere) and never truncate silently.
- A `/v1/models` pre-flight check for compatible endpoints.
- Optional per-task model override in config (`models.classify`, `models.analyze`), defaulting to the single configured model.
- Tests against a fake local HTTP server (standard library) covering success, timeout, malformed JSON, and a server that rejects `response_format`.

Acceptance: CI green; the golden set is runnable on Ollama and on a llama.cpp server; a comparison report is produced.
Owner review: run the golden set on both backends.

### Phase 5 — Mirror into Manual Notes (1–2 sessions) [0.14.0]

Do:
- `core/mirror.py`: one-way sync from the GitHub and Websites vaults into `<manual_vault>/Library/GitHub Projects/…` and `<manual_vault>/Library/Websites/…`.
- Mirror notes carry `mirror_of` frontmatter and a read-only banner. The mirror follows moves (matched by `source`) and removes only orphaned mirror files that carry the mirror marker, only under `Library/`.
- It refuses to run if `manual_vault_path` equals or contains either machine vault, and it **never writes outside `Library/`**. Guard against path traversal.
- Dry-run by default; a real run needs an explicit flag.

Acceptance: tests with temporary folders prove nothing outside `Library/` is ever created, changed or deleted; the sync is idempotent; moves propagate.
Owner review: run it against a **copy** of Manual Notes; write `[[a library note]]` in an idea and check that Obsidian shows the backlink.

### Phase 6 — Linking (3–4 sessions) [0.15.0]

Do, in order, pausing for owner approval between steps:
- **Recall hooks:** a neutral "Best used for" sentence for each existing GitHub note, from a new prompt that does **not** use `about_me.md`. Insert it as one delimited block (`<!-- gitcurator:recall --> … <!-- /gitcurator:recall -->`), touching nothing else; support a dry-run diff; the owner approves 20 before any bulk run. Blocks like this are excluded from "edited" detection.
- **Embeddings** with a local embedding model through Ollama, stored in SQLite.
- **Candidate search plus LLM yes/no** confirmation.
- **Link store**, the `Library/` "Related (auto)" block, and the `Suggestions` note, with the cap and the approval flow from 4.8.

Acceptance: a fixed test corpus shows sensible links; rejected pairs never reappear; nothing is ever written outside the allowed places.

## 7. Parked (do not build)

Article and text summarization, podcast scripts, an Articles bot or vault, a Go/Rust port, automation inside Manual Notes, and changes to `dashboard/` or the Cloudflare bot. Keep the shared pipeline code generic so an article mode could be added later. *(The root `dashboard/` was removed in v0.26.0 — this entry now guards only the Cloudflare bot.)*

## Appendix A — Draft prompts

Store each as a file in `app/prompts/`. `{{…}}` marks a slot the app fills; the loader must refuse to run with a slot left unfilled. Record the prompt set's version in each note's `prompt_version`.

### w01_category.txt

```
You are filing a website into a personal library. Choose exactly one category from the list. Follow the judgment rules.

Categories:
{{CATEGORY_NAMES_WITH_ONE_LINE_DEFINITIONS}}

Judgment rules:
{{JUDGMENT_RULES}}

Website: {{URL}}
Title: {{TITLE}}
Description: {{META_DESCRIPTION}}
Page text (may be partial): {{TEXT_EXCERPT}}

Return only JSON: {"category": "<exact name from the list>", "confidence": "high|medium|low", "reason": "<one short sentence>"}
```

### w02_subcategory.txt

```
The website was filed under "{{CATEGORY}}". Choose the best subcategory from this list, or "none" if none fits well.

Subcategories:
{{SUBCATEGORIES_WITH_DEFINITIONS}}

Website: {{URL}}
Title: {{TITLE}}
Description: {{META_DESCRIPTION}}
Page text (may be partial): {{TEXT_EXCERPT}}

Return only JSON: {"subcategory": "<exact name or none>", "confidence": "high|medium|low"}
```

### w03_analyze.txt

```
You are cataloguing a website for a personal knowledge library. The reader is a designer-developer who will search this library later, when an idea comes up, to remember which tool or resource could help.

Rules:
1. Use only what the provided text supports. If something is not stated or clearly implied, leave it out. Never guess features, prices, or names.
2. Write plain, concrete English. No marketing words such as "revolutionary" or "powerful".
3. "best_used_for" is the most important field. Write one sentence starting "Use when you need to" that names the problem it solves, in the words someone would use when thinking about their own task.
4. "similar_tools": list only tools you are confident exist and are genuinely similar. Otherwise return an empty list.
5. "tags": up to 6, lowercase. Prefer these for the category: {{TAG_HINTS}}.
6. Return only valid JSON in exactly this shape:

{
  "name": "",
  "one_line": "",
  "core_offerings": [],
  "standout_feature": "",
  "best_used_for": "",
  "pricing": "free|freemium|paid|unknown",
  "login_required": "yes|no|unknown",
  "similar_tools": [],
  "tags": [],
  "confidence": "high|medium|low"
}

("one_line" is at most 25 words. "core_offerings" has 3 to 6 short bullets. "standout_feature" is empty if there is none.)

URL: {{URL}}
Title: {{TITLE}}
Description: {{META_DESCRIPTION}}
Page text (may be partial): {{TEXT_EXCERPT}}
```

If the page text is missing or clearly partial, the app sets `fetch_status: partial`, gives the model only the title and description, and marks the note accordingly.
