# Changelog

All notable changes to GitCurator are documented here.
Versioning: [SemVer](https://semver.org/) — `MAJOR.MINOR.PATCH`, tagged `vMAJOR.MINOR.PATCH`.

> **v0.09 lineage note** — the tree forked after v0.07: the owner's local
> lineage shipped **0.07.1 / 0.08** (Lucide icons, main-screen redesign,
> 404 quarantine, its own rich-based CLI) while the sandbox lineage shipped
> **0.07.2 / 0.07.3** (CLI model picker + pre-flight, GUI strike manager).
> Both branches are preserved below, as-is. **v0.09 is the merge**: one
> tree, one CLI, one dead-link system, going forward.

## [0.09.5] — Phase 0 groundwork: rehearse before you touch the vault — 2026-09-29

The phased build (SPEC.md) starts here. Phase 0 adds **tools and safety
nets only — no behavior change to the app**. Everything below runs
against a vault you point it at; nothing was run against a real vault.

### What you get

1. **`--dry-run` on the CLI** — add it to any batch command
   (`python main.py --cli --auto --dry-run`, `--import-file`, ranges,
   `--retry-failed`) and the batch runs for real — fetches, analyzes,
   builds every note in memory — but **writes nothing**: no notes, no
   banners, no inbox tables, no master index/MOCs, no reports, no
   manifest. It also skips the three post-batch side effects: the
   VaultSeal backup push, the Good Repos publish, and marking the bot
   queue read / advancing `last_processed_msg_id`. At the end it prints
   how many vault operations were withheld and saves a readable report
   of every one (with a content preview) to `app/reports/dry-runs/`.
   Even the anti-repeat state is safe: the batch reads the real
   `cache.db` through a throwaway copy, so a dry-run can never mark
   links "processed" and make the next real run skip them.
2. **`gitcurator/tools/scan_vault_edits.py`** — a strictly read-only
   scan of any vault folder: how many notes per category (and in
   `_review`, and in folders that don't match any known category), which
   notes are missing their `source:` line (invisible to the anti-duplicate
   index), which notes share the same `source:` (duplicates), and which
   notes contain anything you wrote yourself in *My Ideas & Notes*,
   *Social Signal (Manual)* or *Journal* beyond the template placeholders
   — the content the app must never overwrite. Writes a Markdown report
   **outside** the vault (`app/reports/scan/`); refuses to write inside
   the vault it scanned.
3. **`gitcurator/tools/snapshot_vault.py`** — zips the entire vault
   (notes, `_moc`, `_inbox`, `attachments`, even `.obsidian`) into one
   timestamped `.zip` outside the vault (`app/reports/snapshots/`).
   Restore is a plain "extract here". Run this before any risky
   operation on a real vault.
4. **`gitcurator/tools/pick_golden_links.py`** — reads your
   `unique_links.csv` (columns detected automatically; tolerates
   semicolons, tabs, BOM, headerless files), excludes GitHub repo links
   (they belong to the GitHub pipeline), and picks **30 diverse
   candidates** — spread across as many domains as possible, always the
   same pick for the same file — into `app/tests/golden/websites_candidates.json`
   as the candidate golden set for the future Websites pipeline. You
   approve the final list in Phase 2.

### Diagnosis / notes
- The dry-run switch lives in the new `core/dryrun.py` (pure stdlib, no
  PyQt). `core/storage.py`'s atomic writers consult it, which
  automatically covers every canonical vault write today and in future
  phases; the handful of legacy raw writes inside the batch (master
  index, MOCs, per-run reports, the 404 log, banner failure markers,
  inbox tables, the link manifest, folder creation, banner moves) were
  each routed through behavior-identical helpers — same bytes on disk
  when dry-run is off.
- The per-platform inbox tables are now written through the shared
  atomic writer (`storage.atomic_write_text`) instead of a duplicated
  inline copy — identical content, plus fsync durability.
- An end-to-end rehearsal of the dry-run (fake LLM, stubbed GitHub API,
  synthetic vault) caught a real deadlock in the fresh-install case (no
  `cache.db` yet): the shadow-cache copy ran while holding the log lock.
  Fixed, with a regression test that fails instead of hanging.
- Cosmetic, known: during a dry-run, a few log lines still say e.g.
  "Final report saved: …". Read them as "would save" — the authoritative
  truth is the closing "DRY-RUN COMPLETE" line and the dry-run report.

### Verification
- 24 new tests (`tests/test_phase0.py`) over synthetic vaults in temp
  folders: the dry-run module and storage gate; the real worker write
  slice (note + inbox + manifest) performing normally when off and
  touching nothing when on; the scan tool's findings and its
  read-only guarantee (hash of every file before/after); the snapshot
  zip's completeness (CRC check, unicode names, empty folders) and its
  refusals; the golden picker's diversity, determinism and column
  variants; the CLI flag plumbing.
- Full suite: **130 tests, all passing** (106 existing + 24 new). CI
  now compiles 20 modules and runs the six test modules.
- End-to-end dry-run rehearsal on a synthetic vault: 24 vault
  operations logged, vault byte-for-byte identical afterwards
  (verified by hashing every file before and after), no `cache.db`
  created, seal/publish/mark-read skipped, report written outside the
  vault.

## [0.09.4] — the bot queue can no longer look fully-unprocessed — 2026-09-22

Owner report: "it finds all 618 links in the bot to be processed, but in
reality 99.5% of them are already processed — the app thinks none of them
is processed. We had a special mechanism to make sure the app knows what
is already done (so it doesn't repeat, or duplicate by mistake)."

### Diagnosis
The GUI's anti-repeat design is **four layers**: (1) the vault index —
`VaultIndex`, rebuilt from every note's `source:` frontmatter, is the
ground truth for "already done" and filters the queue check AND every URL
during processing; (2) `cache.db` (`processed_repos` + the 404 quarantine
+ the retry queue); (3) `last_processed_msg_id` — "Process New" fetches
only bot messages newer than the last **fully-verified** batch; (4) Phase
5 CLEAR — the bot messages are only marked read when every link verified.
The CLI `--auto` flow only ever used layers 1–2 — and both could silently
fail, with no diagnostic, in ways the GUI was immune to:

- **`cache.db` was CWD-relative** (`CacheDB(db_path="cache.db")`). The GUI
  and every documented launcher run with cwd = the app folder, so it always
  opened `<app>/cache.db` — but a CLI run started from ANY other directory
  (scheduled task, `python C:\…\app\main.py --cli --auto` from a project
  folder) silently created a **parallel empty cache** in that cwd:
  processed repos, quarantine and retry queue all read 0. Split state —
  the app "thinks none of the links is processed".
- **The vault filter degrades silently**: when `vault_path` in
  config.json doesn't match the vault the notes actually live in (the GUI
  reads its live vault dropdown; the CLI only has the file — and any
  past drift, a typo in `--init`, or a moved vault is invisible), the
  classification indexes 0 notes → every queue link comes back "pending"
  → a full re-processing run into the wrong vault, duplicating ~all notes.
- **No min_id, no CLEAR**: `--auto` always re-fetched the entire bot
  history and never advanced `last_processed_msg_id` or marked the queue
  read, so every run carried the full 618-link history through the only
  two (silently fallible) dedup layers.

### Fixed
- **`cache.db` is anchored to the app folder** (`CacheDB` default path).
  One cache per installation, shared by the GUI and the CLI regardless of
  the working directory. Behavior is byte-identical for every documented
  launch path (cwd == app folder); explicit `db_path` arguments (tests)
  are untouched. This also fixes `--status` / `--list-dead` /
  `--reset-dead` / `--retry-failed`, which were equally cwd-sensitive.
- **`--auto` fetches like the GUI's "Process New"**: it passes
  `min_id=last_processed_msg_id` (printed as "Skipping bot messages up to
  ID N"), so previously-verified messages are never re-fetched at all.
  0 (first run) keeps the full-history + vault-classification behavior.
- **Phase 5 CLEAR, CLI edition**: after a successful bot-queue batch the
  LinkTracker's verdict decides — **all clear** → the bot messages are
  marked read (same engine as the GUI's auto-mark) and
  `last_processed_msg_id` is advanced and persisted to config.json;
  **anything unverified** → an explicit `⏸️ N link(s) not verified — the
  bot queue stays UNREAD and last_processed_msg_id is NOT advanced; the
  next --auto re-fetches and retries them`. Nothing is lost, nothing
  repeats — the GUI's exact semantics.
- **Dedup visibility + wrong-vault guard**: the queue summary now also
  prints the ground-truth counts ("Dedup ground truth — vault index: N
  note(s) · cache: M processed repo(s)"). On the wrong-vault signature
  (pending links, **zero** in-vault, **zero** notes indexed, but the cache
  knows processed repos) the CLI stops before processing and says so:
  which vault indexed 0 notes, what the cache knows, why processing now
  would duplicate, and the three ways to fix it (`--init`, a one-off
  `--vault` run, or editing config.json). `--yes` mode never re-processes
  on this signature; interactive mode asks `[y/N]` (default No). Fresh
  installs (empty cache + empty vault) are unaffected.

### Verification
- Reproduction harness (stubbed fetch worker, real VaultIndex/CacheDB):
  correct vault → 4 in-vault / 3 pending across quoted, unquoted,
  trailing-slash and `www.` note formats; empty-wrong vault → the exact
  owner symptom (all pending) — now caught by the guard: diagnostics,
  exit 1, batch never started.
- Phase 5 CLEAR harness (fake worker in the real Qt loop): all-clear →
  exit 0, `last_processed_msg_id` 100→777 persisted, queue marked read;
  one failed link → nothing advanced, nothing marked, retry message.
- min_id wiring: `last_processed_msg_id: 42` in config → the fetch
  receives `min_id=42`.
- Cache anchor: `CacheDB()` instantiated from a foreign cwd opens
  `<app>/cache.db`; no stray cache.db in that cwd.
- Full suite: 106/106 green (gc-venv, offscreen Qt). CLI smoke matrix:
  `--help`, `--status` / `--list-dead` from a foreign cwd, banner v0.09.4,
  PYTHONIOENCODING=cp1252 simulation — no tracebacks, correct exit codes.

## [0.09.3] — CLI `--login`: configs + Telegram verification code in the terminal — 2026-09-21

Owner request: "Update the CLI version, so the user can also enter configs
there, like telegram api id etc. and the telegram verification number —
currently it's only available on GUI."

### Added
- **`--login` command** (`python main.py --cli --login`) — the interactive
  Telegram login, end-to-end in the terminal: connects with the saved
  credentials (through the configured proxy, DC-rotation retries), and
  when the session is missing/expired the worker sends the verification
  code and the CLI prompts for it (`📩 Enter the Telegram login code:`),
  including the 2FA password when the account has one. On success the
  session file is saved — **the same `session.session` the GUI uses**, so
  every future run (CLI or GUI, `--auto` included) skips the login.
  Reuses the exact same subprocess engine as the GUI's Test Connectivity
  button (`_telegram_test_job` via `_gui_symbol`) — no new worker code,
  one code path. Failure output is actionable: rate-limited → wait and
  rerun; session/auth errors → "rerun `--login` once your proxy node
  works"; otherwise → proxy checklist.
- **`--init` now finishes the setup**: after saving config.json the wizard
  offers "Log in to Telegram now (enter the verification code)? (Y/n)" —
  `GitCurator-CLI-Setup.bat` becomes a complete first-run experience
  (credentials → code → ready). Non-interactive stdin (pipes/CI) skips
  the offer automatically; a failed login never fails the wizard itself.
- **`--auto` fetch failures now name the remedy**: when the error smells
  like session/auth ("code", "auth", "session", "password", "login"),
  the CLI prints "Tip: run `python main.py --cli --login` to
  (re-)authenticate with a fresh verification code."

### Changed
- Banner/version stamps: `v0.09.3` (cli.py banner, VERSION, README).

### Verified
- Flow simulation with a stubbed engine (success path: code typed via
  stdin → "session saved" card, exit 0; failure path: actionable retry
  advice, exit 1); no-config and missing-credentials paths exit 1 with
  the right guidance; `--help` and the no-args help list show `--login`;
  106/106 tests green after the change.

## [0.09.2] — launcher regression fixed (the "'tlocal' is not recognized" wall) — 2026-09-21

Owner report: double-clicking `Start-GitCurator-CLI.bat` printed a wall of
`'GitCurator' / 'Double-click' / '1.' / 'pre-flight' / 'Kept' / 'M' /
'tlocal' / 'tle' / '/d' is not recognized as an internal or external
command` errors **before the app started**. The app itself then ran fine.

### Root cause
- v0.09.1's belt-and-suspenders fix #2 — `chcp 65001` **inside** the
  `.bat` launchers — backfired. `cmd.exe` re-parses a batch file after a
  mid-file codepage switch, and it recomputes its file position under the
  NEW codepage: the em-dash (`—`) characters v0.09.1 also added to the REM
  comments are **3 bytes in UTF-8 but 1 byte in ANSI**, so the parser's
  byte offset shifted, cmd jumped back into the REM header block, and
  executed line fragments as commands (entering `setlocal` at +2 bytes →
  `'tlocal'`, `title` at +2 → `'tle'`, `cd /d` at +3 → `'/d'`, REM lines
  at +2/+5 → `'M'`/`'GitCurator'`/`'Double-click'`/…). It then re-synced
  at the `chcp` line and the run continued — hence "errors, then the app
  worked anyway".
- The `chcp` was **redundant from day one**: fix #1 (the in-app
  `_harden_stdio()` in `gitcurator/cli.py`: `SetConsoleOutputCP(65001)` +
  `SetConsoleCP(65001)` + UTF-8/replace std streams, at import time)
  already handles every console — and is the layer that made the owner's
  banner render perfectly in the very same run that showed the garbage.

### Fixed
- All three `.bat` launchers (`Start-GitCurator-CLI.bat`,
  `GitCurator-CLI.bat`, `GitCurator-CLI-Setup.bat`):
  - `chcp 65001` **removed** — a batch file never switches the codepage
    mid-run, so the cmd re-parse bug cannot trigger; console encoding is
    exclusively the app's job.
  - Content is now **pure ASCII** (em-dashes → `-`): a byte-identical
    parse under cp437/cp850/cp1252/65001, immune to any codepage.
  - Line endings normalized to **CRLF** (canonical Windows batch format).
- No Python changes — v0.09.1's in-app encoding fix is untouched and
  remains the single source of truth for console encoding.

### Verified
- Byte-level: all three `.bat` files are pure ASCII with CRLF terminators
  (`file` → "DOS batch file, ASCII text, with CRLF line terminators").
- Full test suite re-run (106/106 green) + CLI command matrix from three
  working directories; banner and output unchanged.
- Note for the run log that motivated this: the same console session also
  showed `Fetch failed: IncompleteReadError … Telegram closed the
  connection through your proxy (exit node blocked / DPI). Try a different
  v2ray node.` — that part is **environmental** (proxy exit node blocked
  + the Telegram session needing re-login), not a code defect: the app's
  pre-flight, proxy check, DC-rotation connect retry, and the actionable
  error all behaved exactly as designed. After switching to a working
  v2ray node, re-running the launcher will prompt for the Telegram login
  code (the CLI's interactive login path) and resume.

## [0.09.1] — CLI-not-running root-cause fix · UI polish — 2026-09-20

Two jobs, nothing else: **make the CLI actually run** and **polish the UI**.
No restructuring, no layout changes, no behavior changes.

### Fixed — the CLI was not running on Windows
- **Root cause found and reproduced**: on a Windows console with a legacy
  codepage (cp437 / cp850 / cp1252 — anything but 65001, still the default
  on many systems), the CLI's very first `print()` — the banner's
  block-drawing glyphs — raised
  `UnicodeEncodeError: 'charmap' codec can't encode characters` and killed
  the process **before any command could run**. Every `main.py --cli …`
  invocation and both double-click `.bat` launchers therefore appeared
  simply "not to run" (window with a traceback, or flash-and-close).
  **Fix (two layers):**
  1. `gitcurator/cli.py` now reconfigures stdout/stderr to UTF-8 with
     `errors="replace"` at import time (covers `--help` too, before
     argparse prints anything) and switches the Windows console codepage
     to 65001 via `kernel32.SetConsoleOutputCP` — the glyphs render, not
     just encode. No-op on already-UTF-8 streams (Windows Terminal,
     modern Linux, pipes).
  2. All three `.bat` launchers (`Start-GitCurator-CLI.bat`,
     `GitCurator-CLI.bat`, `GitCurator-CLI-Setup.bat`) now run
     `chcp 65001` before launching Python.
- **CLI no longer dies mid-command when GUI packages are missing**: the
  lazy imports of the pipeline engine (`gitcurator.gui.app`) hit that
  module's dependency guards, which call `sys.exit(1)` — a
  *BaseException* invisible to plain `except Exception` — so
  `--status` / `--list-dead` / `--reset-dead` / `--auto` could TERMINATE
  the whole process mid-command on a machine without the full GUI stack
  (despite the "no extra packages needed" launcher promise). A new
  `_gui_symbol()` helper converts the guard's exit (and any unguarded Qt
  ImportError, e.g. from `gui/icons.py`) into a clean, actionable
  `ImportError` — commands now print "pip install -r requirements.txt"
  and exit non-zero instead of a traceback or a silent kill.
- **CLI output noise removed**: importing the pipeline engine printed a
  2 KB one-line `[main] LOADED version …` blob to stderr in the middle of
  `--status` output (between banner and stats). The stamp now only prints
  for GUI loads, never in `--cli` mode.
- Verified: full command matrix (`--help`, `--status`, `--list-dead`,
  `--reset-dead`, no-args help, `--auto` paths) under simulated
  cp1252 / cp850 / latin1 consoles, from three different working
  directories, with and without PyQt6 present — correct output, correct
  exit codes, zero tracebacks. 106/106 automated tests green.

### UI polish (visual-only: tokens + stylesheets, zero layout changes)
- **The SYNC hero button finally pops** — its lavender fill was so pale
  (#C4BCF5) it read muted/disabled against the cream background (design
  review: "looks disabled"). Deepened to #B3A7F2 in both themes, and
  hover now DARKENS the fill (a real press affordance — the old
  lighter-hover felt inert).
- **Saturated status colors in dark mode** — the pastel log colors read
  muddy on plum ("warning yellow and error red lack saturation"). Now:
  mint #7CE2A9, butter #FFD37E, rose #FF9AAB, lifted info grey — all
  ≥7.5:1 on the plum panels. Light-mode warning deepened to #75510A.
- **Progress bar is visible now** — the track was nearly invisible on
  both themes. Light: warmer, darker track (#E3DACA); dark: recessed
  "well" track (#17131F, matching the log well figure-ground).
- **Log toolbar row aligned** — filter buttons (All/Errors/Warnings/
  Success) were ~25 px next to a 31 px search box. Filters are taller,
  the search box got height-harmonizing QSS (and the missing
  `setObjectName("log_search")` that the QSS needed), and focus states
  keep constant height.
- **Test Connectivity no longer a ghost in dark mode** — its fill was
  the sheet color itself (invisible body, outline only). Now the raised
  panel tone (#352F4A), balancing the saturated SYNC button.
- **Dim text lifted** — "PROCESSED" caption and breadcrumb brighter and
  11 px (was 10 px); header spacing 8 → 10 px.
- Verified with rendered offscreen screenshots in both themes, before/
  after design review: primary-action hierarchy, log scannability and
  toolbar alignment all confirmed improved; no regressions.

## [0.09] — Lineage Merge — one tree again — 2026-09-18

The owner asked for a single proper merged lineage instead of two parallel
fix-zips. This is it: **their v0.08 GUI work + our v0.07.2/3 pipeline
fixes, unified** — and one deliberate design reconciliation.

### Kept from v0.08 (the owner's local lineage)
- **Official Lucide SVG icons** everywhere (`gui/icons.py` — verbatim
  lucide-static v0.544.0 geometry, QSvgRenderer at 2× HiDPI, theme-tinted
  at render time; graceful degradation to text if QtSvg is missing).
  The icon-button clipping problem is solved by vector icons (34×30),
  superseding the v0.07 38×36 emoji workaround.
- **Main-screen redesign** — hero lavender CTA row (SYNC grows, Test
  Connectivity beside it), PROCESSED pipeline strip with caption, recessed
  log well as the stretch-growable region, status dots as tinted SVGs.
- **404 quarantine machinery** — batch-start dead-set pre-filter (dead
  links never reach the GitHub API), attempt counting in
  `decommissioned_repos.fail_count`, one aggregate summary line instead
  of per-URL spam, `notfound_links.md` written ONCE at confirmation,
  `More ▸ View 404 Quarantine` viewer + reset.
- **Telegram worker v3.4** — proxy pre-flight, own connect-retry policy
  with DC rotation (only for unauthorized sessions), session-copy tip,
  actionable network errors.
- **Config credential healing** — hand-edited trailing whitespace in
  tokens can no longer poison PyGithub headers.

### Kept from v0.07.2/0.07.3 (the sandbox lineage)
- **The 3-layer model picker** (owner report: *"it didn't let me choose a
  new model … Model wasn't found = failed cli"*): pre-flight numbered menu
  BEFORE any work, warmup stand-in resolution before the first repo,
  mid-batch recovery through `_wait_for_llm_decision(err, client, model)`
  + `model_prompt_callback`; `_pick_best_model` heuristic (embedders
  excluded → same family → same size → biggest); every choice persists to
  `config.json`. A missing Ollama model can no longer fail or degrade a
  batch in any mode.
- **Our zero-dependency visual CLI** (`main.py --cli` → `gitcurator/cli.py`,
  plain ANSI) — `--init`, `--auto`, `--status`, `--retry-failed`,
  `--mark-read`, all batch modes, `--strikes N`.
- **GUI threshold manager** (Settings → Dashboard) — rebadged as the
  quarantine manager below.

### Unified (the reconciliation)
- **ONE CLI** — the v0.08 companion (`app/cli.py` + `cli_app.py`, rich-
  based, with the green-✓-on-missing-model pre-flight that caused the
  owner's report) is retired; our engine absorbs its good ideas:
  `--list-dead` / `--reset-dead` are ported. `Start-GitCurator-CLI.bat`
  keeps its name (muscle memory) and now drives `main.py --cli --auto`.
  The `rich` dependency is gone.
- **ONE dead-link system** — the v0.08 quarantine keeps its machinery but:
  - the threshold is **configurable again** (`notfound_strike_threshold`,
    default 3, min 2 — GUI spinbox, `--strikes N`, config.json; v0.08
    hardcoded 3);
  - **consecutive semantics restored** — a successful fetch calls
    `reset_dead_links(url)` (v0.08 never reset, so stale attempts could
    quarantine a live repo after one transient miss);
  - **v0.07 caches auto-migrate** — `notfound_strikes` rows move into
    `fail_count` on first open (higher count wins), the old table is
    dropped;
  - `get_quarantine_stats()` added for viewers that show in-progress
    attempts AND confirmed rows.
- **Both quarantine viewers kept** on the one system: Settings →
  Dashboard manager (attempts + ⛔ + spinbox + reset) and the More-menu
  viewer (confirmed + reset).

### Tests & verification
- `tests/test_quarantine.py` extended 9 → 14 (configurable threshold,
  threshold-parameter steering, success-reset consecutive semantics,
  v0.07 strike-table migration incl. max-wins, stats in-progress rows).
- `tests/smoke_v007.py` rewritten for the merged design: 40 checks
  (900×600, 34×30 SVG icon buttons, icon pack renders, theme round-trip
  via tooltip, log-group stretch, quarantine API + manager + More-menu
  viewer, model-picker presence + heuristic).
- Full suite: **106/106 unit tests**, 40/40 smoke, 4/4 runner self-tests,
  16 modules compile; offline model-picker harness scenarios A/C/D re-run
  **PASS** against the merged worker; CLI `--status`/`--list-dead`/
  `--reset-dead` verified; GUI spinbox→config.json persistence +
  merge-safety re-verified; strike→quarantine migration verified e2e.
- CI: compile gate 16 modules (adds `gui/icons.py`), suite swaps
  `test_strikes` for `test_quarantine`.

## [0.08] — Icon Fix · 6:4 Window · 404 Quarantine · Visualized CLI — 2026-09-17

Four owner requests, all delivered. The headliners: every icon was
silently rendering 2× too big and clipped (the "partial sun"), the window
was an ultra-wide strip, dead repos kept being re-404'd every session,
and the app gained a fully visualized one-click CLI companion.

### Fixed — icons were big for their area ("only parts of the sun visible")
- **Root cause**: `QSvgRenderer.render(painter)` without a target rect
  paints the SVG at its NATURAL 24×24-unit size inside the painter's
  LOGICAL coordinate system. The icons render onto a DPR-2 pixmap whose
  logical area is the icon size (16 px) — so every glyph painted at
  24/16 = 1.5× (and 2× at the 12 px proxy dot) of its area and was
  clipped at the right and bottom edges. Pixel-probe before the fix:
  ink bbox `(2,2)-(31,31)` on a 32-device-px canvas touching both edges;
  after: `(1,1)-(30,30)`, no edge contact, all 12 glyphs verified.
- `icons.pixmap()` now passes an explicit `QRectF(0, 0, size, size)` so
  the viewBox maps exactly onto the icon area — the sun, moon, gear,
  layers, loader, search, trash, dots: every glyph is complete at last.

### Changed — window re-proportioned 1000×375 → 900×600 (exact 6:4)
- Owner: "the app is unnecessarily long — too much width, low height; I
  prefer a ratio like 6×4". The v33 1000×375 window was a 2.67:1
  ultra-wide strip; the new 900×600 is an exact 3:2. The extra 225 px of
  height goes to the log panel (the main view's growable region) — long
  batches show far more history without scrolling.

### Added — 404 QUARANTINE (dead links ignored after 3 tries, across sessions)
- Owner: "6-8 GitHub repos are deleted and became 404; the app repeats to
  find and 404 them again and add to the logs. After 2-3 tries across
  different sessions, ignore those links."
- **Why it kept happening**: the decommissioned-repos table existed, but
  `is_decommissioned()` was consulted ONLY on the bot-queue path. The
  Telethon channel fetch (keyword/marker/single-ID) and import-file paths
  funneled straight into `get_repo()` → 404 → log → re-decommission →
  repeat, every single session, forever.
- `decommissioned_repos` gains a `fail_count` column (with a migration:
  pre-v0.08 rows were 404'd in earlier sessions, so they start CONFIRMED
  dead — the fix takes effect on the owner's cache immediately).
- `record_404()` counts consecutive failures in cache.db — the count
  survives restarts by construction. Attempts 1-2 log normally ("attempt
  1/3"); at the threshold (3) the link is CONFIRMED dead, the log line
  says QUARANTINED, and `_inbox/notfound-links/notfound_links.md` gets
  its single permanent row (previously one duplicate row per run,
  forever).
- **The ProcessingWorker now skips confirmed-dead links BEFORE any
  GitHub API call** — this covers every input path at once (bot queue,
  channel fetch, imports, retries). Skipped links are counted silently
  and reported in ONE aggregate line in the batch summary instead of
  per-URL spam.
- Queue/verify filters use the confirmed-dead set only — unconfirmed
  entries (1-2 attempts) still surface as pending so they receive their
  remaining attempts.
- **Management**: GUI `More ▸ 🚫 View 404 Quarantine` lists every
  confirmed-dead link with attempts + date and offers a one-click reset
  (for false positives — a repo that went PRIVATE reads as 404 to an
  unauthorized token). CLI: `--list-dead` / `--reset-dead`.

### Added — VISUALIZED CLI companion (`cli.py` + `Start-GitCurator-CLI.bat`)
- Owner: "I want a CLI version — store the credentials in the project
  folder, start it via a .bat for easy clicking, and it automatically
  does the rest as long as credentials are okay. I want a visualized CLI
  — different colors, loading animations."
- **One-click launch**: `Start-GitCurator-CLI.bat` (double-click on
  Windows; `cli.sh` on Linux/macOS) finds Python, one-time-installs
  `rich`, and runs the pipeline. Credentials come from the SAME
  `config.json` the GUI uses.
- **The default flow needs zero flags**: colored ASCII banner → config
  summary panel (masked secrets) → spinner-driven pre-flight checks
  (GitHub token + rate limit, proxy socket, LLM provider) → bot-queue
  fetch through the same hardened subprocess worker (proxy pre-flight,
  DC rotation) → queue stats table → rich live progress bar with the
  current repo, M/N, elapsed → level-colored log lines → post-run
  (mark-read, VaultSeal, Good Repos) → summary table (processed /
  warnings / retry queue / quarantined total / elapsed).
- Shares the pipeline code with the GUI verbatim (ProcessingWorker,
  CacheDB, quarantine) — identical behavior by construction, including
  headless-mode safety (LLM failure → fallback note, Ctrl+C → graceful
  stop, single-instance lock so it refuses to run beside the GUI).
- Flags: `--check`, `--dry-run`, `--import-file F`, `--list-dead`,
  `--reset-dead`, `--skip-checks`, `--no-seal`, `--quiet`, `--config`,
  `--vault`, `--version`.
- Signal bridging: the worker's cross-thread Qt signals are delivered to
  the rich renderer through a main-thread QObject bridge (rich's Live
  display is not thread-safe — plain callables would have run the
  callbacks on the worker thread, exactly like `run_headless` could get
  away with only because `print()` is thread-safe).

### Verification
- 101/101 tests (92 existing + 9 new quarantine regression tests:
  schema, migration, cross-session counting, confirmed-only filtering,
  reset, legacy shape, threshold).
- End-to-end CLI run: 4 consecutive sessions against a real 404 repo —
  attempt 1/3 → 2/3 → 3/3 QUARANTINED → silently skipped with zero log
  spam and zero GitHub API calls; `notfound_links.md` gained exactly one
  row; summary table rendered each time.
- Offscreen smoke: 33/33 UI checks; window exactly 900×600; VLM visual
  review of light + dark screenshots: sun/moon/gear all fully rendered,
  layout balanced, no defects.
- `python -m py_compile` on all 17 audited modules (CI gate updated,
  which also finally fixes the `branches: ain]` trigger typo at its
  source — the v0.06 fix had not persisted).


## [0.07.3] — GUI 404-Strike Manager — 2026-09-18

Closes the v0.07 P1 follow-up: the deleted-repo threshold existed in
`config.json` / CLI (`--strikes N`) only — now it's a first-class citizen
of the GUI.

### Settings → Dashboard → "Deleted Repos — 404 Strike Counter"
- **Threshold spinbox** (2–10, default 3) — every change is saved to
  `config.json` immediately through the merge-safe `save_config` (unknown
  keys survive; `ProcessingWorker` reads the value per batch, so the next
  run picks it up without a restart). The initial `setValue` is
  signal-blocked so launching the app never rewrites `config.json`.
- **Live strike table** — every URL currently carrying 404 strikes
  (highest first, last-seen timestamp, ⛔ flag once the threshold is
  reached), capped at 30 rows with a pointer to `--status`. Refreshed by
  its own 🔄 button and rides along with **Refresh Dashboard**.
- **♻️ Reset Counters** — confirmation, then clears every strike row so
  restored/repos-that-came-back get re-checked on the next run instead of
  being auto-ignored.
- `refresh_strike_view` is best-effort: missing vault / cache.db shows a
  friendly empty-state message, never a dialog or crash.

### Tests & docs
- `tests/smoke_v007.py` extended 15 → **24 checks** (spin exists, range
  2–10, initial value matches config, group/text widgets, all three new
  methods, render-without-vault path).
- E2E verified offscreen: spin→`config.json` persistence, merge safety
  (probe key survives), strike table lists a threshold-reached URL with
  the ⛔ flag.
- `VERSION` → 0.07.3, `__VERSION__` stamp, README (GUI strike-manager
  section + changelog pointer).

## [0.07.2] — CLI Model Picker + Pre-flight Checks — 2026-09-18

Owner report: *"See it didn't let me choose a new model … Model wasn't found
= failed cli."* — with several Ollama models installed but the configured one
not pulled, the CLI logged *"The first LLM-failure dialog lets you pick one"*,
but that dialog only exists in the GUI: headless mode silently skipped every
repo's LLM analysis, so the whole batch degraded to placeholder notes.

### 1. Pre-flight checks (`gitcurator/cli.py`, all batch modes)
- **Run-configuration card** — boxed vault / bot-queue / proxy / LLM /
  masked-token snapshot before `--auto` runs (matches the GUI's settings
  summary).
- **GitHub token** — validated live (`authenticated as <login>`); a rejected
  token (401) aborts with a fix hint (interactive runs may override); no
  token warns about the 60 req/h unauthenticated limit.
- **Proxy** — TCP reachability + latency of the configured proxy (the Telethon
  fetch depends on it); unreachable → actionable warning.
- **Ollama + model** — the v0.08-lineage pre-flight showed a green ✓ while
  noting "(configured '…' not pulled)" and then failed the whole batch. Now:
  server down → warning (the pipeline's auto-start still gets its chance),
  no models → abort with `ollama pull` hint, **configured model missing →
  interactive numbered model menu BEFORE any fetching happens**:
  - smart recommendation first (same family + parameter size as the
    configured model, embedding models like `nomic-embed-text` flagged
    *"(embedding — cannot analyze)"* and never recommended),
  - pick by number or unique name substring; `Enter` takes the
    recommendation; `q` aborts with the `ollama pull` hint,
  - the choice is **saved to config.json** (GUI + CLI share it),
  - non-interactive runs (piped stdin / scheduled) auto-pick the
    recommendation and log the switch — a missing model can no longer
    fail a whole scheduled batch.

### 2. Pipeline headless model resolution (`gui/app.py`)
- **`model_prompt_callback`** — the CLI registers a console-menu callback on
  `ProcessingWorker`; the warmup-failure path (configured model missing,
  several installed) now asks the host instead of logging a promise no
  headless run could keep. The choice is applied to the whole batch
  (`_apply_model_choice`), persisted, and the stand-in is **re-warmed to
  verify it actually works** before the batch starts.
- **Auto-pick fallback** — headless without a console (or after declining):
  `_pick_best_model` chooses the closest stand-in (embedding models
  excluded → same family → same size → biggest) and logs the switch —
  never again does the batch continue with a known-missing model.
- **Mid-batch recovery** — `_wait_for_llm_decision` gained `err / client /
  model` context: a model-not-found failure mid-run (model deleted while
  the batch was running) triggers the same console menu / auto-pick via the
  existing retry plumbing; connection errors still skip with the
  "start the server" hint; after one decline the menu never nags again.
- GUI behavior is unchanged (the first LLM-failure dialog still offers the
  list and persists the choice batch-wide).

### 3. Fixes & polish
- **`--status`** now reports whether the configured model is actually
  installed (`N model(s) installed · 'X' ready` / warning + fix hint) and
  still degrades silently when the server is down.
- **`__config_path__` leak** — CLI model-switch saves wrote the private key
  into `config.json`; all saves now filter `__…__` keys.
- **Direct-launch `sys.path`** — `python gitcurator/cli.py …` computed the
  app root one directory too high (worked only via `main.py`); fixed.
- Verification: 110/110 unit tests, 15/15 GUI smoke checks, and a 4-scenario
  offline e2e (mock Ollama reproducing the owner's exact model list + fake
  PyGithub): non-interactive auto-pick, interactive menu pick honored,
  warmup-callback switch with re-warm verification, mid-batch
  decision matrix (choice / decline→auto-pick / decline-once / connection
  error). All batches completed 2/2 repos with the switched model and
  exit code 0.

## [0.07.1] — Connectivity Hotfix + Official Lucide Icons — 2026-09-18

The first v0.07 build failed to connect on the owner's machine for three
separate reasons (two app bugs, one environmental) — all three are fixed,
and the hand-drawn "Lucide-style" icons are replaced by the real thing.

### Connectivity — what the failure log actually said
- **Proxy disabled on the first queue check** (`build_proxy received:
  {'enabled': False}`): the Proxy checkbox state is the source of truth
  and had been toggled off on the machine — the queue check then spawned
  the worker SILENTLY on a direct connection (blocked in Iran) and burned
  four cryptic `ConnectionRefusedError [WinError 1225]` retries.
  `check_bot_queue` now logs a loud warning before the spawn, and the
  worker prints a proxy-disabled banner and pre-flights the proxy port
  (2s socket test) with an actionable error when v2rayN is down. The app
  NEVER silently falls back to a direct connection — in censored regions
  that both fails and leaks Telegram usage to the ISP.
- **GitHub header crash** (`Invalid … character(s) in header value:
  'token ghp_…\n'`): a token pasted with a trailing newline. The direct
  token test stripped its input, the combined Telegram+GitHub test did
  not — same field, six seconds apart, one passed and one crashed. Every
  token read strips now, `load_config` heals already-poisoned files, and
  `save_config` strips at the source so the newline can never persist.
- **`IncompleteReadError` was never retried** (`Server closed the
  connection: 0 bytes read on a total of 8 expected bytes`): Telethon's
  internal `connection_retries` only retries `ConnectionError` subclasses,
  so the classic DPI / exit-node reset aborted instantly with zero
  retries. The worker now owns the retry policy: 3 attempts, 3s/6s sleeps,
  clear per-attempt logs, alternate Telegram DCs on attempts 2-3 (fresh
  sessions only — auth keys are DC-bound), and a plain-language hint that
  a reset usually means the v2ray EXIT NODE is blocked (the remaining
  `IncompleteReadError` the owner saw at 02:18-02:19 was exactly this:
  their proxy exit node was being reset by Telegram/DPI — app-side
  retries now make it robust, changing the node fixes it outright).
- **Session guidance**: a fresh extract has no `session.session`, so
  every Telethon flow asked for a login code. The worker now prints a
  tip to copy `session.session` from the previous install instead of
  re-authenticating.
- Worker version bumped to 3.4 (visible in the log's first line —
  confirms the fixed worker is the one running).

### Icons — official Lucide pack, verbatim
- The v0.07 draft shipped hand-redrawn "Lucide-style" paths; the owner
  rejected self-drawn icons. `gitcurator/gui/icons.py` now embeds the
  REAL `lucide-static` v0.544.0 SVGs (ISC license, full text in the
  module) — layers, settings, moon/sun, refresh-cw, square (solid stop),
  play (solid), loader-circle, activity, search, trash-2, circle (status
  dot). Same tint-at-render machinery (QSvgRenderer, 2× HiDPI), same
  public API, graceful QtSvg-less fallback.

### Upgrade notes
- If Telegram still resets the connection with the proxy ON, the exit
  node is blocked — switch to a different v2ray/xray node (the app now
  says so in the log).
- Copy `session.session` from your previous app folder to skip the login
  code (the worker prints this tip too).

## [0.07] — Main-Screen Design Release — 2026-09-18 *(local lineage)*

## [0.07] — Visual CLI + 404 Strike Policy + 6:4 Window — 2026-09-18 *(sandbox lineage — same zip, other half of the story)*

Owner requests: *sun icon clipped in the theme toggle* · *window aspect
ratio 6:4 or 5:7* · *deleted repos: persist the 404 failure count locally
and auto-ignore after 2–3 misses* · *a visual CLI version with colored
output, spinners/progress bars, locally stored credentials, a double-click
.bat launcher, fully automatic*.

### 1. Visual CLI (`gitcurator/cli.py`, ~700 lines, zero new dependencies)
- **Colored output** — per-level colors + icons (`•` info, `✔` success,
  `▲` warning, `✖` error), dimmed timestamps, ASCII banner. Honors
  `NO_COLOR`, `--no-color`, and auto-disables on non-TTY pipes;
  colorama (already in requirements.txt) enables ANSI on legacy Windows
  consoles.
- **Spinner + progress bar** — a braille spinner (`⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏`)
  owns the terminal's bottom line; during batches it renders
  `⠹ Processing batch  ██████████░░░░░░░ 12/40 (30%) owner/repo`.
  Log lines print above it (clear → print → redraw), driven by a 100 ms
  QTimer so Ctrl+C stays responsive.
- **`--auto` — fully automatic run**: bot-queue SYNC (fetch UNDONE,
  vault-filtered) → PROCESS (same ProcessingWorker pipeline as the GUI)
  → VaultSeal git commit → Good Repos publish. Interactive Telegram
  login codes are prompted in-terminal; `>10`-repo confirmation is
  skippable with `--yes`.
- **`--init` — first-run wizard**: asks for Telegram/GitHub/LLM/proxy
  settings and saves them to the SAME `config.json` the GUI uses
  (credentials stay local; secrets are masked in every status view).
- **`--status`** — masked config summary + cache.db stats: processed,
  decommissioned, retry-queue size, and every URL currently on a 404
  strike with its count and last-seen time.
- **Manual modes** — `--import-file`, `--from-id/--to-id`,
  `--offset-start/--count`, `--single-id`, `--retry-failed` (reprocesses
  the whole retry queue), `--mark-read` (clears the bot's unread queue).
  All batch modes share the visual renderer and return proper exit
  codes (a failed batch exits 1 — the .bat shows the setup hint).
- **`--strikes N`** — per-run override of the 404 threshold.
- **Entry points** — `python main.py --cli …` (shim dispatches before the
  GUI import so `--status` never loads the widget stack),
  `python gitcurator/cli.py …`, `python -m gitcurator.cli …`.

### 2. Double-click launchers (Windows + Unix)
- `GitCurator-CLI.bat` — one double-click → the fully automatic run
  (`--cli --auto --yes`), with Python detection and exit-code hints.
- `GitCurator-CLI-Setup.bat` — one-time first-run wizard (`--cli --init`).
- `gitcurator-cli.sh` — Unix launcher (defaults to `--auto --yes`;
  forwards any flags).

### 3. 404 strike policy (deleted repos, persisted)
- New `notfound_strikes` table in `cache.db` (`url`, `strikes`,
  `first_seen`, `last_seen`); the counter is **persisted across
  sessions** — each batch run that sees a 404 for a URL increments it.
- A URL is only **decommissioned (auto-ignored)** after
  `notfound_strike_threshold` consecutive 404s (config key, default 3,
  clamped to ≥2). Sub-threshold misses are marked failed so the URL
  returns with the next SYNC / Retry-Failed run and gets another chance
  (protects against transient GitHub hiccups and false 404s).
- A successful fetch **resets** the counter (repo restored / rename
  reverted). Applies to both 404 paths (direct and post-rate-limit-wait).
- Decommission reasons now record the strike count
  (`404 Not Found (after 3 strikes)`), and the permanent
  `_inbox/notfound-links/notfound_links.md` record includes it too.

### 4. GUI fixes
- **Sun-icon clipping fixed** — the theme-toggle and settings icon
  buttons were 32×30 while the icon-button design spec (and the emoji
  glyph metrics at 16 px, which render ~20 px tall with padding on
  Windows display scaling) call for a 38×36 target. Both buttons are now
  38×36 — `☀️`/`🌙`/`⚙️` never clip at 100–150% DPI.
- **6:4 aspect-ratio window** — the main window is now 900×600 (was the
  ultra-compact 1000×375; 5:7 portrait was rejected because the toolbar
  and progress row are landscape-oriented). The +225 px of height flows
  entirely into the always-visible Progress Logs panel (its minimum grew
  64 → 180 px, ~4× the visible log lines); hero CTAs and the progress
  row keep their compact sizes.

### 5. Tests & CI
- New `tests/test_strikes.py` — 18 tests: strike persistence across DB
  reopens (the counter's whole point), URL normalization, clear-on-
  success, stats listing, `_handle_404` sub-threshold vs decommission
  paths, threshold clamping (min 2 / default 3 / junk → 3), notfound
  record contents, CLI parser/masking/bar/no-color behavior.
- Full suite: **110/110 green** (92 previous + 18 new; PyQt6-dependent
  suites still auto-skip cleanly without Qt).
- CI compile gate now includes `gitcurator/cli.py`; the suite runs
  `tests.test_strikes` too.
- CLI verified end-to-end headlessly: `--help`, no-arg friendly banner,
  `--status` (with live strike rows), and a full `--import-file` batch
  (spinner + progress rendering, inbox notes for non-GitHub links,
  manifest, VaultSeal git commit `5108f88`, Good Repos skip, exit code
  propagation).


A full design review of the main/status screen graded it **C overall**
(D+ design-system consistency): four icon languages on one screen, a log
panel indistinguishable from the background, a status counter that said
`- / -` while the real numbers sat in a log line, no active state on the
filter tabs, and a hue soup (pink STOP, mint CTA, green dot, blue text,
lavender everything else). v0.07 fixes every named finding.

### One icon system (was: pixel-art + emoji + outline + solid mixed)
- New `gitcurator/gui/icons.py` — a single flat-outline, Lucide-style
  SVG set (layers, settings, moon/sun, refresh, stop, play, loader,
  activity, search, trash, dot), tinted per theme at render time via
  `QSvgRenderer` (rendered at 2× for HiDPI). No new dependency — QtSvg
  ships with PyQt6, and the helpers degrade to text-only buttons if it
  is ever unavailable.
- Every main-screen glyph replaced: logo tile, settings, theme toggle,
  SYNC/PROCESS/FETCHING/STOP hero states, Test Connectivity, proxy
  status dot, log search (leading action), clear-log button.

### Palette collapsed to one accent + semantic states
- SYNC/PROCESS hero fill is now the **lavender anchor** (`#C4BCF5` +
  deep-plum text, 9.0:1) — the app's single interactive-chrome accent.
  The old mint CTA made the primary button read as a second, unrelated
  hue; mint/green is now reserved for success states only.
- STOP is a decisive red kill-switch (`#D63A24`, white text 4.7:1 AA;
  hover `#C43320` 5.5:1). It only renders while a batch runs, so the
  screen's loudest element is also its most urgent one.
- Progress-bar chunk is the accent (was mint — a success-state color
  doing chrome work). Semantic dot/label colors for proxy health.

### The status readout says something real
- The `– / –` counter is now labeled **PROCESSED x / y** and is
  truthful at all times: live counts during a batch, `0 / n` for a
  fetched queue, and the last batch manifest's real totals while idle
  (`_refresh_pipeline_counter()`, wired into init, vault change, SYNC
  fetch, batch finish and progress-bar reset). Tooltips explain the
  numbers. Nielsen "Visibility of System Status" restored.

### Figure-ground for the log panel
- Dark theme: the log well drops to a recessed `#17131F` (window
  `#221E2E` → card `#2B2639` → well `#17131F` is now a visible depth
  hierarchy; was `#241F31` ≈ 1.09:1 against the card). Log timestamps
  re-tinted per theme (were fixed `#666`, ~2.4:1 on the new well).

### Filter tabs with a legible active state
- All/Errors/Warnings/Success became a segmented control: checked tab
  gets a filled accent background (lavender/plum text dark, violet/white
  light), hover states, focus rings. Was: four identical gray buttons
  with zero state differentiation.

### Whitespace redistributed, hierarchy corrected
- SYNC and Test Connectivity share ONE horizontal row (SYNC grows) —
  the stacked layout's wasted vertical space now belongs to the log
  panel (min-height 72), so the empty state no longer reads as a void.
- One 10px spacing rhythm between the four bands (top bar / CTA card /
  pipeline strip / log) instead of uneven 8px gaps.
- The counter, bar and proxy health sit in ONE connected strip — the
  review noted the counter and "Connected" status were "two halves of
  the same story at opposite screen edges."

### Accessibility
- Placeholder text lifted to AA: `#A6A2AC` on plum (5.8:1, was 4.30:1)
  and `#7A7288` on white (4.6:1) via the `QPalette.PlaceholderText`
  role (guarded for Qt < 6.5).
- Accessible names on every icon-only control (settings, theme toggle,
  clear log, proxy dot + label, search box).
- Pointing-hand cursors on the filter tabs and clear button.

### Verified
- 92/92 tests green (incl. the 3 GUI worker-guarantee tests), 33/33
  offscreen smoke checks of the new screen, and an independent vision
  review of rendered light + dark screenshots: 14/14 checklist PASS.

## [0.06] — Reliability & Performance Release — 2026-09-17

Owner-reported symptom: *"it's buggy and doesn't work — it just says
another project is running, but does nothing"* with the log showing
`⏳ Another Telegram operation is already running` on every button from
19:49 to 19:52 with no operation ever finishing.

### Root causes (both reproduced, both fixed)

**1. The Telegram busy-lock could stick at "held" forever.**
- The subprocess runner read the telethon child's stderr with an
  UNBOUNDED blocking loop; its `proc.wait(timeout=300)` only ran after
  stderr closed, i.e. never for a stalled child (dead SOCKS proxy,
  session-file contention). The startup auto bot-check acquired the
  lock, hung there forever, and every subsequent Telegram operation was
  denied — the exact log the owner captured.
- 15 early-return paths (missing credentials / empty fields) exited
  AFTER acquiring the lock without releasing it.
- Two signal-ordering self-deadlocks guaranteed denials: the keyword-mode
  finisher started the batch while its own worker still held the lock
  (100% reproducible), and Verify All's finisher called
  `clear_bot_queue()` the same way.
- `ProcessingWorker.run()` had no top-level try/except: any crash in the
  ~850-line pipeline killed the thread silently — no `finished_signal`,
  no `processing_finished`, lock stuck for telegram batches.
- `processing_finished` released the lock unconditionally, even for
  direct/import batches that never acquired it (could free a live
  worker's lock → two telethon children on one session).
- closeEvent never killed telethon children; an orphan held
  `session.session`'s SQLite, making the NEXT launch's auto-check stall.

**2. A zombie process made the NEXT launch print "Another instance is
already running" and exit.** The startup auto-check opens modal dialogs
2s after launch; if the main window was closed in those 2s (or a hidden
parented dialog outlived it), Qt never emitted `lastWindowClosed`,
`app.exec()` never returned, the `finally` in `main()` never deleted
`app.lock` — the lingering process then blocked every future launch.

### Fixes
- **New module `gitcurator/gui/telegram_lock.py`** — `TelegramLockManager`
  with owner tracking, age reporting, owner-scoped release, force-release
  and a context manager. The busy message now names the holder and its
  age instead of a dead-end "please wait".
- **New module `gitcurator/integrations/subprocess_runner.py`** — a
  runner with a REAL timeout: idle-based kill (no child output for 180s
  default; progress lines keep healthy fetches alive), a 30-min hard
  cap, generous auth-grace while the user types a login code / 2FA
  password, and a process registry so closeEvent kills every orphan.
  Ships a 4-scenario self-test (`python -m
  gitcurator.integrations.subprocess_runner`).
- All 15 leak paths release on early return; every acquire site names
  its owner; `_keep_worker` releases only if that worker still holds the
  lock; the two self-deadlock finishers defer one event-loop tick
  (`QTimer.singleShot(0, …)`); `TestWorker.run` catches BaseException
  and always emits `finished_signal`; `ProcessingWorker.run` is wrapped
  so the batch ALWAYS emits `finished_signal` exactly once.
- **Watchdog** — a 60s QTimer force-releases any lock held > 35 min
  (longer than the runner's hard cap) with a loud log line.
- **Zombie fix** — `_closing` flag + guards in every modal helper and
  the startup auto-check (dialogs are logged, not shown, during
  shutdown); closeEvent stops the timers and ends with an explicit
  `QApplication.quit()` so `app.exec()` always returns and `app.lock` is
  always removed.
- `telegram_fetch_worker.py`: Telethon `timeout=45`, bounded `connect()`
  (90s), retry caps, and a progress heartbeat every 500 fetched messages
  so large healthy fetches are never mistaken for stalls.
- Startup lock-reset removed (fresh window = fresh lock; the old blind
  reset could wipe a legitimately-acquired lock from the first 2s).

### Performance
- O(n²) link dedup → set-backed in all three extraction blocks
  (telegram_fetch_worker ×2, message-range fetch; a 5,000-link bot chat
  went from ~25M list comparisons to hash lookups).
- SQLite: WAL journal + `synchronous=NORMAL` + indexes on the hot
  lookup columns (`processed_repos.url/.note_path`, `failed_repos.url`);
  verify-all now opens ONE CacheDB connection instead of two.
- `check_bot_queue` vault filtering (VaultIndex rebuild — a full walk of
  every note — plus decommissioned classification) moved from the GUI
  thread into the background worker; the GUI used to freeze 0.5–5s on
  every queue check including the startup auto-check.
- `LinkTracker.mark_processing` no longer rewrites the entire manifest
  JSON per link (~1,000 full-manifest writes per 500-link batch → 0;
  crash recovery unaffected — reconciliation already retries "pending").
- Telethon import deferred to the headless `--single-id` path
  (~0.5–1s faster GUI cold start; the asyncio stack no longer loads in
  the GUI process).

### Modularity & hygiene
- New testable modules (telegram_lock, subprocess_runner) with the bug
  context documented at the top of each.
- Dead files moved to `app/_attic/` (4 screenshot scripts, superseded
  smoke script, 3 byte-identical prompt duplicates; `app/prompts/`
  keeps the canonical copies).
- `.gitignore` now excludes the live credential stores (config.json,
  both installer.config.json) — an accidental `git add .` can no longer
  commit five live tokens. New `app/config.example.json` template with
  every secret masked.

### Verification
- 73/73 existing unit + e2e + goodrepos tests green.
- Subprocess runner self-test: healthy worker / hung-worker kill /
  interactive-auth grace / hard cap — all pass.
- Offscreen GUI smoke: module import, MainWindow construction, lock
  semantics (double-acquire denial, wrong-owner release protection,
  watchdog force-release), TestWorker SystemExit → finished_signal,
  clean event-loop exit.
- **End-to-end forever-bug repro** through the real
  `MainWindow.check_bot_queue()` path: (A) fast-fail child releases the
  lock and a second Telegram operation is admitted; (B) a HUNG child
  (simulated dead proxy, 600s sleep) is killed after 6.1s by the idle
  timeout and the lock auto-releases; (C) `app.exec()` returns after
  close — no zombie, no stale app.lock.

### Standing security note (unchanged P0)
Rotate every credential that has appeared in chat/logs (Telegram bot
token + api_id/api_hash, Cloudflare tokens, GitHub PAT) and update them
in the app and on the worker. Git history predating v0.0.10 still holds
old blobs.

## [0.05] — Ollama Auto-Start Release — 2026-09-17

Owner-reported fix: "a problem that prevents processing… tried with
different LLMs, but it fails to actually digest and process the new
link." The log showed the classic wall:
`HTTPConnectionPool(localhost:11434) … Connection refused` → retries →
`❌ LLM failed for <repo>` five minutes later.

### Root cause
**The Ollama server was not running** (connection refused = nothing is
listening on `localhost:11434`). Switching models can never fix a dead
server — every model lives on the same server — yet the old failure UI
presented a model-picker, sending the user down exactly that dead end.

### What v0.05 does about it
- **Auto-start at batch start** — the pre-flight check in `run()` now
  spawns `ollama serve` detached (same flags as the 🚀 Start Server
  button: survives the app on Windows/Unix), polls until the server
  answers (16 probes × 1.5s), and continues the batch seamlessly.
- **Clear failure trail** — if Ollama isn't installed / never comes up,
  the log says exactly that (install URL, the Start Server button, the
  `ollama serve` command) instead of a raw traceback.
- **Per-repo diagnosis** — mid-batch LLM failures are now inspected: a
  connection-refused family error (incl. the urllib3/requests wording
  from the owner's log) logs "💡 That is a CONNECTION error… choosing a
  different model will NOT fix it" BEFORE the model-picker dialog.
- **Dialog warning** — the "LLM Analysis Failed" dialog now shows a
  highlighted warning box when the server is unreachable, instead of a
  silent empty model dropdown.
- New helpers: `ProcessingWorker._autostart_ollama()` (thread-safe,
  GUI only via the log signal) and `_looks_like_connection_error()`
  (string+exception-type detection; `TimeoutError` explicitly excluded —
  it is an `OSError` subclass in Python 3 and must NOT read as "down").

### Verification
- 85/85 offscreen GUI smoke checks (+7 new: connection-error detection
  incl. the owner's exact urllib3 string, timeout non-detection, and the
  auto-start not-installed branch).
- 73/73 backend tests green.

## [0.04] — Credentials Release — config pre-filled — 2026-09-17

Owner-reported fix: "I still get the same error" — the v0.03 zip shipped a
credential-free `config.json` (v0.0.10's sanitization), so on a fresh unzip
SYNC bailed with **"❌ Telegram credentials required."** (`api_id` 0,
`api_hash`/`phone` empty). Per the owner's explicit request this release
**pre-fills the credentials into the app config**.

### What's pre-filled now
- `app/config.json`:
  - `telegram_api_id` / `telegram_api_hash` — the owner's Telegram API pair.
  - `bot_token` (@githubfetcherbot) + `bot_username`.
  - `github_token` — the owner's PAT (repo fetching + VaultSeal/GoodRepos pushes).
  - `cloudflare_worker_url` — the LIVE worker
    (`https://github-to-obsidian-bot.aliassadi-plus.workers.dev`, verified
    via `/health` before shipping).
  - Reserved keys (kept alive by the v30 merge-save, no consumer yet):
    `cloudflare_api_email`, `cloudflare_api_token`, `cloudflare_account_id`,
    `cloudflare_workers_ai_token`, `telegram_user_id`.
- `app/installer.config.json` + `app/cloudflare-bot/installer.config.json`:
  `bot_token`, `github_pat`, `allowed_user_ids` (= the sole bot-chat user
  92788333, recovered from the worker's D1 ledger), and a freshly generated
  random `hmac_secret` — `node install.js` now validates out of the box.

### The one field still empty
- `telegram_phone` — the owner's own phone number was not in the provided
  credential list. Every Telegram fetch (SYNC included) needs it once:
  **Settings → Credentials → Phone** (`+98…` international format). The app
  saves it on first SYNC and never asks again (the Telethon session survives
  restarts).

### Note
- The Cloudflare Workers AI token is stored but not yet consumed by any
  code path — it's parked as a reserved key for future use.
- Zip note: this release intentionally ships real credentials inside
  `config.json`/`installer.config.json` (private repo + owner's explicit
  request). The v0.0.10 "credential-free tree" guarantee is lifted for
  this release line.

## [0.03] — GUI Redesign Release — two-stage SYNC — 2026-09-17

Owner-reported fix: clicking SYNC used to raise the "Nothing to Process"
error box. SYNC now runs the fetch itself, per the requested flow:
**click SYNC → it fetches all undone items in the Telegram bot → the button
turns into PROCESS → click PROCESS → it starts.**

### The new hero-button flow (GUI-only, zero pipeline changes)
- **SYNC** — fetches every undone item from the Telegram bot (the bot-queue
  check: already-in-vault and decommissioned repos are skipped), logging the
  queue summary; the button reads `⏳ FETCHING…` while it runs.
- **PROCESS (N)** — after the fetch, the button shows the pending count and
  the progress bar reads `N ready to process`; clicking starts the batch
  through the existing confirm-gated path.
- **STOP** — while the batch runs (unchanged mirror behavior), then back to
  SYNC when it finishes.
- Nothing fetched (0 pending) with an Input mode selected → PROCESS runs
  that mode (Markers/Single/Range/Import keep their launcher); 0 pending and
  no mode → "✅ All caught up".
- Bot not configured → SYNC falls back to the legacy input-mode path, or
  shows a friendly setup hint instead of the old error.
- `check_bot_queue()` gained an optional `on_done(name, result)` completion
  callback + `bool` return for early bails (backward compatible).

### Fixes
- **Progress-bar text never rendered while idle** — a fresh QProgressBar
  holds `value = -1` (unset, out of range), so the QSS-styled bar drew no
  text until the first batch ran; the idle "Ready" (and the new "N ready to
  process") label was invisible. Pinned to `setValue(0)` at construction.
- "Nothing to Process" / SYNC tooltips re-worded for the new flow.

### Verification
- 78/78 offscreen GUI smoke checks (20 new for the two-stage state machine:
  fetching render, PROCESS (N), count refresh, 0-pending/error fallbacks,
  PROCESS routing, busy-lock bail, full SYNC→PROCESS→STOP→SYNC cycle).
- 73/73 backend tests green. `VERSION` → `0.03`; `gui/app.py` stamp → `0.03`.

## [0.02] — GUI Redesign Release — compact pass — 2026-09-17

New owner-requested release-zip line for the GUI redesign deliveries
(`gitcurator-v0.02.zip`; numbering v0.01, v0.02, … — each new delivery
bumps the version). Not related to the deleted early session zips that
happened to use v0.01–v0.09 names. Built with `git archive` from the
`gui-redesign-v33` branch: tracked tree only — no `.git`, no runtime
caches, no sessions.

### GUI redesign (GUI-only, zero business-logic changes)
- **v0.01 — wireframe redesign (first delivery):** minimal main view —
  logo + SYNC CTA + Test Connectivity + progress + logs — with every one
  of the 9 tabs moved into a Settings window (sidebar master-detail).
- **v0.02 — compact pass (this release):**
  - Main window halved to 1000×375; log panel height halved.
  - Settings > Vault: the giant gap is gone — pages are top-aligned at
    natural height inside the scroll area.
  - Settings > LLM: everything fits the viewport — combo min-width caps,
    🔄 Refresh + 🚀 Start Server share the Model row.
  - Verification: 73/73 backend tests + 60/60 offscreen GUI checks green.
- `VERSION` → `0.02`; `gui/app.py` version stamp → `0.02`.

## [0.0.10] — Security Hygiene & Deploy Kit — 2026-09-17

Session wrap-up release: closes the Session-1 audit finding *live secrets
committed* in the git tree, and adds the two-minute Cloudflare update path.

### Security
- **Live credentials removed from the git tree** — `app/session.session` and
  `app/session.session.bak` (full Telethon account access) are untracked and
  gitignored; `app/config.json` and both `installer.config.json` files are
  clean templates again (empty credential fields, as their `_comment` headers
  intend); the real bot token that appeared in `DEPLOYMENT.md` examples is
  now `YOUR_BOT_TOKEN`; `gitcurator/gui/app.py`, `tools/test.py` and
  `tools/diagnose_code.py` use placeholder credentials. The repository is
  private and every value was already exposed in chat (credential rotation
  remains the standing P0 — see the dashboard's Go-Live tab), but the tree
  is now safe against a future visibility flip. Note: git **history** still
  contains the pre-scrub blobs — run `git filter-repo` before ever making
  this repository public.
- **Release zips v0.01–v0.09 deleted from download/ + public/** — v0.01–v0.07
  shipped `session.session` directly in the working tree; v0.08/v0.09
  excluded the file from the tree but bundled the whole `.git` folder, whose
  object store still carried the session blob (`44213fc2…`) plus every other
  historical secret — recoverable by anyone who unzipped them. Only the
  v0.0.10 zip is clean: it is built via `git archive` (tracked tree only, no
  `.git`, no runtime caches, no sessions).

### Added
- **`deploy-latest.ps1` / `deploy-latest.sh`** — the two-minute update path
  for an existing Cloudflare deployment: wrangler presence + auth check,
  idempotent D1 schema apply (`IF NOT EXISTS` — existing data untouched),
  `wrangler deploy`, a live health check against the deployed worker, and an
  optional `--with-secret` / `-WithSecret` flow that sets the v30
  `WEBHOOK_SECRET` anti-impersonation hardening and prints the matching
  Telegram `setWebhook` command. All secrets and data persist across
  deploys — nothing is re-entered.
- **DEPLOYMENT.md** gained a "Quick path — update an EXISTING deployment"
  section ahead of the 45-minute from-scratch guide.

### Verified
- 12/12 py_compile and 73/73 tests on the sanitized tree (repo mirror AND
  the live copy stay byte-identical); `git grep` sweep for every known
  secret value (bot token, api_id/hash, phone, user id, bot-id prefix)
  returns clean; `bash -n` on the deploy script; PowerShell twin reviewed
  line-by-line (no pwsh in the sandbox).

## [0.0.9] — Backup Tab Scrollout Fix Pack — 2026-09-17

Three usability fixes for the Backup tab (internal `v32.2`): its four
sections no longer clip inside the fixed window, the theme toggle now
re-colors all three status dots, and the dark scrollbar handle is
readable on plum.

### Fixed
- **Backup tab scrollout** — the Backup tab's four sections (Vault Backup,
  VaultSeal — GitHub Mirror, Good Repos — Public Directory, Dashboard) no
  longer clip inside the fixed 1000×750 window: the tab content is wrapped
  in a vertical-only `QScrollArea` (horizontal scrollbar always off) and
  compacted — status dots share the action rows, the two settings
  checkboxes sit side-by-side, info notes are single wrapped sentences.
  Visible-pane/natural-content ratio dropped from ~2.2 to 1.31; every
  control (Backup Now / Restore / toggles / Seal Now / Publish Now /
  Worker URL / Open Dashboard) is reachable by scrolling.
- **Theme toggle re-themes all three Backup status dots** — previously the
  Good Repos status kept light-theme deep-butter on the plum background
  (~2.5:1) after switching to dark; `toggle_theme()` and the startup
  dark-restore block now re-run all three refreshers; the backup dot also
  refreshes at construction; hardcoded `#AE2237` error reds route through
  theme-aware `_status_colors()`.
- **Dark scrollbar handle contrast raised** — `#3B344F` → `#7A7199`
  (~3.2:1 on plum, WCAG 1.4.11 non-text), hover `#8D84AD`.

### Verified
- 12/12 py_compile gate and 73/73 tests (34 core + 28 goodrepos + 11 e2e)
  on both the live copy and the repo mirror; offscreen GUI smoke
  (scroll-area config, four-section single column, 617px pane / 811px
  content, post-scroll visibility of the Dashboard section + Open
  Dashboard button, THEME-SWITCH assertion for all three dots); VLM QA
  of 4 screenshots (top/bottom × light/dark) with pixel forensics.

## [0.0.8] — Resilience Fix Pack: 401 Fallback, Windows-safe MOCs, Visible Theme Toggle

Four fixes driven by a real Windows run (internal `v32.1`): a rejected
GitHub token no longer kills a batch, quoted LLM categories no longer
crash the master index, the light/dark toggle is finally *findable*, and
token health is one click away.

### Bad-credentials fallback (the 401 wall)
- **Before**: an expired/rotated GitHub token failed *every* repo with a
  raw `401 {"message": "Bad credentials"}` blob, and the whole batch
  ended with "7 links need retry" + VaultSeal/GoodRepos both failing to
  resolve the account.
- **Now**: the first 401 logs ONE actionable error (token invalid,
  expired, or rotated + exactly where to fix it), silently drops the
  token for the rest of the batch (anonymous access, 60 req/h), retries
  the current repo, and keeps going.
- **VaultSeal / GoodRepos** messages now name the fix too:
  "could not resolve the GitHub account for the token (invalid or
  expired — update the GitHub Token in Settings → Credentials, then
  'Test GitHub Token')".

### MOC filename sanitizer (Windows `[Errno 22]` crash)
- The LLM occasionally returns categories with literal quotes or `>`
  separators (`"Agents_Skills"`, `AI > Skills`). The master-index
  generator built `_moc/"Agents_Skills".md` — quotes are ILLEGAL in
  Windows filenames, so index generation died with
  `[Errno 22] Invalid argument`.
- **`_safe_moc_name()`** — one canonical sanitizer (`/` `\` → `_`;
  strips `<>:"|?*` + control chars; collapses whitespace; no trailing
  dots/spaces) used by BOTH the file writes and the
  `[[_moc/…|View MOC]]` wiki-links so they always match.

### Always-visible light/dark toggle
- The theme toggle lived in *More → Settings* — undiscoverable, and with
  `dark_mode: true` saved the app read as "dark only". There is now a
  compact **🌙/☀️ icon button in the action row** (38×36, pastel violet
  on white / lavender on plum, tooltip names the current mode) that
  flips the full pastel cream/plum theme and persists the choice. The
  Settings-menu entry stays in sync.

### One-click GitHub token tester
- **Credentials → "🔑 Test GitHub Token"** validates the token *as
  typed* (before Save) via `GET /user`: shows the account login on
  success ("5000 requests/hour enabled"), or an actionable 401/403
  message on failure — the #1 field error, caught before a batch burns
  its repos on it.

### Fixes
- `gui/app.py` 401 branch, `_safe_moc_name` + 3 call sites, `icon` button
  variant, `theme_toggle_btn` + `_sync_theme_toggle_btn`, `test_github_token`
- `integrations/vaultseal.py`, `integrations/goodrepos.py` — enriched
  could-not-resolve messages
- Gates: 12/12 compiles + **73/73 tests** (live copy *and* repo mirror)
  + offscreen smoke (both themes, toggle geometry, sanitizer cases
  including the exact `"Agents_Skills"` crash).

## [0.0.7] — Modular Core & Good Repos: Public Directory, Pastel UI

Three user-facing asks, one release: the flat `app/` became a proper Python
package, every curation run now also publishes a **public** curated directory
(`good-repos`), and the whole GUI speaks **pastel** (internal `v32.0`).

### Modular package layout (breaking-free)
- **`app/gitcurator/`** — the seventeen flat modules moved into purposeful
  subpackages: `core/` (links · storage · note_builder · llm_client),
  `integrations/` (telegram fetchers · vaultseal · goodrepos ·
  error_reporter), `cloud/` (cloudflare + gdrive), `gui/` (the PyQt6
  application), `tools/` (developer utilities) + `constants.py` (shared
  design tokens + config defaults).
- **Thin `main.py` launcher** — every historical entry point keeps working
  unchanged: `python main.py` (GUI), `python main.py --headless …`, and
  `python -m unittest tests.test_core …` from the app dir.
- **Path hardening** — `config.json` is now anchored to the app root (was
  cwd-relative); the Telegram subprocess and `assets/fonts` resolve through
  the new package layout; fetcher scripts bootstrap `sys.path` themselves so
  direct execution still works.
- **CI** — the workflow compiles the 12 audited modules at their new paths
  and runs all three suites; also fixed a pre-existing YAML typo that had
  mangled `branches: [main]` into `branches: ain]`.

### GoodRepos — the public curated directory
- **After every run** (GUI or headless, right after VaultSeal), the curated
  notes become an emoji-rich README directory — organized like
  *AI → Skills → …* — with the full notes mirrored into category folders of
  the **public** `good-repos` GitHub repository. Everyone can browse and
  benefit from the curation.
- **README generator** — stats line (`N repos · M categories · Updated`),
  anchor-verified contents, per-category counts, one line per repo (link,
  TL;DR, ⭐ stars, 🔧 language, `tags`), GitHub-exact anchor math.
- **History-preserving publishes** — fetch + `reset --mixed` keeps the
  remote history; each publish is a real diff; unchanged content is a no-op
  ("directory unchanged since the last publish").
- **Config + UI** — `goodrepos` section (enabled / repo_name / auto_push),
  a Backup-tab group with status line and "Publish Now", theme-aware status
  colors; standalone CLI (`--vault --repo-name --token --no-push --json
  --status --dry-run`).
- **28 new tests** (`tests/test_goodrepos.py`) — scan, README structure,
  anchors, sorting, idempotency, frontmatter edge cases, config bridge.
  Total suite: **73 tests**.
- **Dashboard** — new **Good Repos** tab (live directory scan, category
  tree, publish timeline, publish-now/simulate via `GET/POST/DELETE
  /api/goodrepos`) + `GoodReposEvent` Prisma model; the compile gate grew
  to 12 files.

### Pastel theme (AA-verified)
- **Light: pastel cream** — `#FBF8F2` window, white sheets, warm-sand
  borders, plum text; mint filled primary (`#B9E3C9` + deep-forest `#17402B`
  text, 8.3:1), violet outlined secondary (`#5F54B4`, 6.2:1), pastel-rose
  danger (`#F6C6CD` + `#5E1120`, 8.8:1), butter warnings, lavender/mint
  info notes.
- **Dark: pastel night** — soft plum `#221E2E`/`#2B2639` surfaces, warm
  white text, lavender `#C4BCF5` accents (8.2:1), pastel mint progress
  chunk.
- **30 text pairs verified programmatically** (all ≥ 4.5:1) — pastel fills
  always carry a deep companion text color; `_btn_style` gained the `text`
  parameter; `_status_colors()` makes every status label theme-aware.
- **Verification** — window-composite histograms in both themes (light:
  white/cream/lavender/mint dominant; dark: plum dominant, zero white
  leakage), offscreen GUI smoke across all tabs, VLM review 9/10 light.

### Fixes
- **CI YAML typo** — `branches: ain]` → `branches: [main]` (pre-existing;
  pushes/tags had still triggered, PRs had not).
- Duplicate `CONFIG_FILE` definition in the extracted constants (the flat
  copy silently overrode the anchored one).
- Dialog header blue `#2196F3` replaced with the violet accent (no-blue
  rule); remaining zinc literals in dialogs migrated to the pastel family.

### Verification (this release)
- 73/73 tests (34 + 11 + 28) and 12/12 py_compiles — locally, from the
  repo mirror, and live through the dashboard gate (`POST /api/verify`).
- Real public repo created + published end-to-end from the demo vault
  (7 repos across 7 categories, commit `c958518`), all README entry links
  verified intact via the GitHub API; idempotent re-publish skips.
- Offscreen PyQt6 smoke: window, 9 tabs, theme toggle, GoodRepos group;
  pixel-sampled histograms in both themes; VLM screenshot QA.

## [0.0.6] — UI/UX Overhaul: Fixed Window, AA Contrast, Button Hierarchy

A full visual-standards pass over the PyQt6 GUI (internal `v31.1`).
No business logic, API calls, or file paths were changed — presentation
layer only.

### Window & Layout
- **Fixed 1000×750 window** — one size for every tab; the window never
  resizes when switching tabs (preserves spatial memory).
- **Every tab scrolls independently** — each tab's content is wrapped in a
  `QScrollArea` starting at the same top position at its natural height.
  No padded/fixed-height containers, no filler stretches.
- **Exactly one growable region per tab** — the results/list/log panel
  (Bot queue, Dashboard stats, Sources results, and the global log).
  Removed the old 150px height caps; controls stay content-sized.

### Color & Contrast (WCAG AA)
- Primary action fill `#10B981` → **`#047857`** (white text 7.4:1).
- Secondary/read action `#6366F1` → **`#4338CA`** (7.9:1).
- Destructive `#EF4444` → **`#B91C1C`** (6.5:1).
- Red removed from non-error elements — the pending-count badge is now
  neutral zinc (a pending count is routine, not an error).
- Log text colors are theme-aware (dark shades on light, light shades on
  dark) so every level stays readable in both themes.

### Button Hierarchy
- **Three variants only** — filled primary (max one per tab), outlined
  secondary, filled danger. Plus a quiet ghost for log-panel utilities.
- **Overflow "More" menu** (single) — tests, verification, export, retry,
  recategorize and preview moved out of the action bar (Hick's law).
- **Dark-mode toggle moved into a Settings submenu** — a display
  preference no longer sits beside batch-job triggers.

### System Status & Safety
- **Determinate progress bar, visible only while a batch runs** —
  labeled `Processing X of Y — owner/repo`; hidden at rest.
- **Large-batch confirmation** — any batch >10 items asks for exact-count
  confirmation first (bot queue, Process New, sources, retry, fetches).
- **Labeled proxy status** — the dot is now paired with a text label
  (`Connected` / `Idle` / `Error`) per WCAG 1.4.1.

### Design Tokens
- **Spacing scale 4/8/16/24/32/48px** applied across QSS and layouts.
- **Type scale of four sizes** — 16px section/dialog titles, 13px
  labels/buttons, 12px body, 12px mono for log/results.

### Accessibility
- **2px focus outlines (2px offset)** on every interactive element
  (buttons, inputs, combos, checks, radios) in both themes.

### Microcopy & Icons
- Routine **pending counts use ⏳**; ❌ is reserved for actual failures.
- **Sources tab now uses 📡** (Proxy keeps 🌐) — two destinations no
  longer share one icon.

### Notes
- Dark mode fixed for real: semi-transparent (rgba) widget fills were
  composited over a light base by Qt's QSS engine — replaced with solid
  theme-aware panels (`tab_sheet` / `info_header` / `info_note` rules).
- Outlined variants re-apply on theme switch via a tracked button list.

## [0.0.5] — VaultSeal: Automatic Vault Backup

Obsidian's free tier has no sync. GitCurator now closes that gap itself:
after every curation run — 1 new project or 100 — the whole vault is
committed and pushed to a **private GitHub repository**. Restore is plain
`git clone`: the full vault at any point in its history.

### Python application (`app/`)
- **NEW `vaultseal.py`** (pure stdlib, 547 lines) — the VaultSeal engine:
  - `VaultSeal.seal()` — git init (when needed) → managed `.gitignore`
    hygiene → `git add -A` → `seal:` commit → one-time-token-URL push.
    Never raises (a backup failure must never fail the run it protects);
    skip-when-unchanged; failed pushes keep the local commit.
  - **Nested-repo guard** — a vault inside another git repository (a
    dotfiles tree, a synced workspace…) bootstraps its OWN repo: git's
    `add -A` is repo-wide from subdirectories since git 2.0, so without
    the guard a parent's files would be sealed. Caught live during QA
    (the sandbox workspace itself) and fixed with a toplevel comparison;
    regression-tested.
  - **Hygiene** — `workspace.json`, `workspace-mobile.json`,
    `.obsidian/cache`, `.trash/`, `__pycache__/`, OS noise sealed OUT via
    an idempotently-merged managed `.gitignore` block.
  - **Credential hygiene** — the token lives only in memory: API calls +
    one-time push URLs. The remote stays token-less, `.git/config` never
    sees the token, and it is never logged.
  - Repo bootstrap over the GitHub API — resolves the login from the
    token, reuses an existing private repo or creates it (described as
    "VaultSeal — automatic Obsidian vault backup (GitCurator)").
- **Post-run hook wired into both surfaces**: the PyQt6 GUI
  (`processing_finished` → background `VaultSealWorker` QThread, mirrors
  the v29.4 BackupWorker pattern) and the headless CLI (`_on_finished`,
  before the exit print). Runs for FAILED batches too — notes written
  before a mid-run failure are exactly what you want backed up; an
  unchanged vault is a no-op.
- **Backup tab (GUI)** — new "🛡️ VaultSeal — GitHub Mirror" group: enable
  checkbox, push toggle, repo-name override ("auto" = derived from the
  vault folder), live status line (Ready / Local-only / Disabled), and a
  manual **Seal Now** button that exercises the exact post-run path.
- **Config** — new `vaultseal` section (`enabled` / `repo_name` /
  `auto_push`), merge-safe through the v30 `merge_config` machinery.
- Internal lineage bumped to **v31.0**; compile gate + CI now cover 10
  modules (`+vaultseal.py`). 45/45 tests still green (2.3s).

### Verification dashboard (`dashboard/`)
- **NEW GET/POST/DELETE `/api/vault-seal`** — live git state of the vault
  (own-repo detection, dirty count, notes/files/size, last commit, remote,
  hygiene check), the app's `vaultseal` config section (token never
  returned), the persisted seal history + stats; `seal-now` runs the REAL
  `app/vaultseal.py` (local commit — the dashboard holds no GitHub token
  by design), `simulate` records a plausible event, `record` persists
  results reported by the app.
- **NEW Prisma model `VaultSealEvent`** (sealedAt, status
  sealed/skipped/failed, commit sha/message, files, pushed, repo,
  provenance source app/demo/simulate, duration).
- **NEW Vault Seal tab (9th)** — protection/vault/last-seal stat cards
  (CountUp, hover lift), a "how a seal works" 4-step walkthrough with
  hygiene chips, and the seal history timeline with per-event badges
  (status, source, pushed), delete + clear, and the git-clone recovery
  note. Mobile tab grid now 3×3.
- Export JSON includes the VaultSeal snapshot; footer lists
  `/api/vault-seal`; version strings bumped to 0.0.5.

### Verified live
- A real demo vault was sealed to the private
  `github.com/assadigit/GitCurator-Vault`: initial 12-file seal,
  incremental 1-file fast-forward push, and a no-op skip — all confirmed
  over the GitHub API. The workspace-junk first attempt (pre-guard) was
  force-replaced with clean history; no credentials were ever in it.

## [0.0.4] — Provenance & CI History

Verification runs now carry their origin, and the CI pipeline gets a
visible track record in the dashboard.

### Verification provenance
- `VerificationRun` (Prisma) gains a `source` column (default
  `"manual"`): `"manual"` for button-triggered runs, `"startup"` for the
  scheduler's first pass 60s after boot, `"scheduled"` for the 6-hour
  intervals. Existing rows backfill to `"manual"`.
- `POST /api/verify` accepts an optional JSON body
  `{"source": "manual"|"startup"|"scheduled"}` — validated against a
  closed whitelist before it touches the database; no body (the normal
  dashboard button) still records `"manual"`. In-flight runs coalesce,
  so the source of whoever started the run is the one recorded.
- `src/instrumentation.ts` now identifies itself — the startup pass and
  the interval passes each send their own source.
- `GET /api/history` returns `source` per run plus a
  `stats.sources { manual, startup, scheduled }` split.
- History tab: provenance badge on every run row (manual = zinc cursor,
  startup = amber refresh, scheduled = teal clock, each with an
  explanatory tooltip), source-count chips under the Total-runs stat
  tile, and a `source` column in the CSV export.

### CI run history
- `GET /api/releases` returns `ciHistory` — the last 10 GitHub Actions
  runs (newest first) from the same token-gated Actions API call as the
  existing latest-run chip. No token → `null` → the card simply doesn't
  render; zero runs → `[]` → also hidden (the "not run yet" chip covers
  it).
- Releases tab: new "CI run history" card — a 5×2 (mobile) / 10-wide
  (desktop) grid of run-number chips, colored by conclusion (emerald
  success / amber pulse while running / rose failure), each linking to
  its run on GitHub, with a pass-rate summary and last-failure line.

### Styling & UX polish
- Version history renders as a timeline: connector rail + per-release
  dots on the left, the newest dot emerald with a soft halo.
- The current release card gets an emerald border, ring and gradient
  wash; "current" badge gains a pulsing dot.
- Repo status / Backups / Workflow / Release cards lift on hover
  (translate + border + shadow), matching the History stat tiles.
- Repo status tile icons are now color-coded (VERSION emerald, Last tag
  amber, Commits teal, Working tree status-colored); the hero "Release"
  badge is emerald-tinted.

## [0.0.3] — CI Pipeline & Actions Status

The repository gets its own gate: GitHub Actions now runs the exact
verification the dashboard performs — on every push, tag and pull request —
and the dashboard surfaces the live CI status.

### CI (`​.github/workflows/ci.yml`)
- Runs on push (main + v* tags), pull requests and manual dispatch.
- The gate mirrors POST /api/verify 1:1 — py_compile over the 9 audited
  modules, then the full 45-case suite (`tests.test_core` +
  `tests.test_e2e`) with verbose output.
- Zero pip installs: the testable core is pure stdlib by design, so the
  whole gate boots in seconds on ubuntu-latest / Python 3.12.

### Verification dashboard (`dashboard/`)
- `/api/releases` now also reports the newest GitHub Actions run
  (status, conclusion, run number, link) — read-only, best-effort, and
  only when a `GITCURATOR_GH_TOKEN` (or `GITHUB_TOKEN`) env var is set;
  private-repo API reads need a token. Without it the field is null and
  the UI shows "CI n/a" — never an error.
- Releases tab: CI status chip beside the repo title — passing (emerald) /
  running (amber pulse) / failing (rose) / not-run (zinc), linking
  straight to the run on GitHub with a status tooltip.
- `.env.example` documents the optional read-only token (recommend a
  fine-grained PAT with Actions:read only).
- Version bumped to 0.0.3 across report/layout/fallbacks.


## [0.0.2] — Repository & Release Console

The dashboard becomes release-aware: live git status of this repository, the
parsed changelog, and the downloadable zip backups — plus version alignment
(repo-level SemVer 0.0.x; the v30.x numbers live on as the internal build
lineage).

### Verification dashboard (`dashboard/`)
- **NEW GET /api/releases** — live repository status: last commit, commit
  count, last tag, dirty-file detection, VERSION, the CHANGELOG.md parsed
  into structured releases, and the local .zip backups (public/ + download/
  scan with served flags). Best-effort by construction — never 500s.
- **NEW Releases tab (8th)** — repository status card (private badge,
  Open-on-GitHub link, clean/dirty working-tree indicator with file chips),
  backups card with per-zip Download buttons, the "how the next version
  ships" workflow card, and the full version history rendered from
  CHANGELOG.md in a scrollable timeline.
- **Header Backup button** — one-click download of the newest served zip
  with toast + tooltip (name, size, contents).
- **Versioning aligned** — the dashboard version now equals the repo
  version (0.0.2); the internal v30.x lineage is shown as a hero badge;
  page title and metadata renamed to GitCurator.
- **Mobile tab grid** — the 8 tabs lay out as a 2×4 grid on phones
  (replacing horizontal scroll); "Risks & Roadmap" shortens to "Risks"
  below the sm breakpoint; trigger typography scales responsively.
- Export JSON now includes the releases/repo snapshot; footer lists
  /api/releases; eslint config ignores the staging repos/ tree.

### Notes
- First release produced by the complete round-trip: sandbox QA → staging
  mirror → VERSION/CHANGELOG bump → tag v0.0.2 → GitHub push →
  GitCurator-v0.02.zip backup (download/ + public/).


## [0.0.1] — Initial repository import (V0.01)

First versioned snapshot of GitCurator, published to GitHub as the canonical
online home for all future development. Internal build lineage: the code
below already carries the full v30.x hardening sprint (v30.0 → v30.4).

### Python application (`app/`)
- **Core pipeline** — Telegram Saved Messages → link extraction → GitHub
  repo metadata → local Ollama LLM analysis → structured Obsidian notes,
  with PyQt6 GUI and a headless CLI mode.
- **v30 hardening sprint (all fixes verified by 45 automated tests):**
  - Headless mode can no longer hang (LLM-failure / disk-full / auth-code
    signals all have receivers or non-blocking defaults).
  - `save_config()` rewritten as a merge (unknown keys preserved, atomic
    write) — the "model choice never persisted" bug is fixed end-to-end.
  - All Ollama calls are timeout-wrapped (`llm_timeout_s`, default 300 s).
  - Atomic note/banner writes via tempfile + `os.replace`.
  - URL parsing converged into a single `links.py` module (4 previous
    drifting copies removed).
  - LLM output sanitized before frontmatter (YAML injection neutralized);
    README content treated as untrusted input.
  - Cloudflare stack: HMAC signing fixed (sha256 of shared secret),
    webhook secret-token verification added, stale root-worker duplicates
    quarantined and deleted; `cloudflare-bot/` is canonical.
  - Testable core extracted: `links.py`, `storage.py`, `note_builder.py`,
    `llm_client.py` + 34 unit tests and 11 end-to-end tests (45/45 green).
  - CacheDB hardened: `busy_timeout`, connect timeout, cross-thread locking,
    idempotent close.

### Verification dashboard (`dashboard/`)
- Next.js 16 live verification console for the Python app:
  - `/api/verify` — on-demand compile gate (9 files) + full test run with
    per-case timing and code fingerprints (sha256 per file).
  - `/api/history` — SQLite-persisted run history (Prisma), drift detection
    ("working tree changed since last verification"), timing insights,
    regression/flaky detection, CSV export.
  - 7-tab UI: Overview, Fixes, Audit, Tests, Go-Live checklist, History,
    Insights — dark/light/system themes, fully responsive, WCAG-checked.
- Portable layout: expects the Python app at `../app` (override with
  `GITCURATOR_APP_DIR`).

### Notes
- Runtime caches (`app/cache.db`, `app/error_outbox.db`,
  `dashboard/db/custom.db`) are gitignored; zip backups include them.
