# Phase 3 Report — Moves as Corrections + the Backfill (v0.12.0)

**Plan (5 lines, as built):**
1. `core/note_state.py` — the §4.4 actor on top of the Phase 1 detector:
   `apply_corrections` (targeted front-matter line edits, atomic writes,
   corrections log, dismissed list), `run_start_check` (baseline → detect
   → apply, dry-run aware), a taxonomy-aware folder resolver for the
   Websites vault, and the "N notes moved from X to Y" summary lines.
2. New tables in `cache.db`: `corrections_log` (append-only history) and
   `note_state_dismissed` (deleted notes, never re-added).
3. Wiring: the state machine runs at the START of every batch for both
   vaults (pipeline switches respected); dismissed URLs are filtered
   before any GitHub API call; the run report + `--cli --status` gain
   Note State sections; the Recategorize dialog skips locked notes.
4. The Websites classifier never overrides a locked note — your folder
   wins, including on retry upgrades.
5. `tools/backfill_websites.py` — resumable, polite, batched, dry-run
   first; routes GitHub links out exactly like the app does.

---

## 1. What I built

**Your moves are now corrections, never damage.** From v0.12.0, at the
start of every batch the app compares each vault with its persistent
record and acts per the §4.4 table:

| You did | The app does |
|---|---|
| Moved a note to another folder | Accepts it: updates the note's `category:`/`subcategory:` lines + tags to match the folder, adds `category_locked: true`, logs the correction, never moves it back. Out of `_review/` counts too. |
| Edited a note by hand | Flags it in the run report. File untouched. |
| Deleted a note | URL dismissed — never re-added, even if the link returns from the bot. Listed in the report. |
| Two files with one `source:` | Both flagged, neither touched. |
| File without `source:` | Ignored and listed. |
| Moved into a folder the taxonomy doesn't define | Kept exactly there, reported. Never invents a category. |

The **first run after this ships records a silent baseline** — your 600+
existing notes are never flagged as "changed". A dry-run detects and
logs but records and writes nothing.

**Locked notes beat the model.** Once you place a note yourself, the
classifier is skipped for it — on every re-processing, including the
automatic retry upgrades of `_review` placeholders. The bulk
"Recategorize Notes" dialog skips locked notes as well.

**The backfill.** `tools/backfill_websites.py` loads your existing
bookmarks into the Websites vault:

```cmd
cd app
python gitcurator\tools\backfill_websites.py ..\unique_links.csv --dry-run
python gitcurator\tools\backfill_websites.py ..\unique_links.csv --limit 30
```

- **Resumable** — a per-URL checkpoint in `cache.db`; interrupt it (or
  close the terminal) any time, re-run, and it continues exactly where
  it stopped. Tested with a simulated mid-run interruption.
- **Polite** — a fixed pause between links (default 3 s, `--delay`)
  on top of the pipeline's per-domain pause. **Small batches** —
  `--limit 30` new links per run.
- **Dry-run first** — `--dry-run` writes nothing anywhere (shadow
  cache) and reports exactly what would happen.
- Works with your local **Ollama** or any **OpenAI-compatible
  endpoint** (`--api-url/--api-key/--model` — Cloudflare Workers AI's
  `/ai/v1` included, once the token has the permission, see §6).
- `--report PATH` writes a Markdown batch report.

## 2. What changes for you

- Nothing to configure. The next real run records the baseline silently
  and the corrections begin from then on.
- The run report (and `python main.py --cli --status`) show the Note
  State: corrections applied (with "N notes moved from X to Y" lines),
  edits, deletes, duplicates, unmanaged, unmapped — per vault.
- Websites pipeline OFF (your current default): only the GitHub vault
  is checked; everything else is exactly v0.11.0 behavior.

## 3. How to check it yourself (about 5 minutes)

```cmd
cd app
python -m unittest tests.test_phase3 -v          (36 tests, all green)
python main.py --cli --status                    (Note state block)
```

Then the SPEC's owner review for this phase (5 more minutes, on your
machine):

1. Run the backfill dry-run: `python gitcurator\tools\backfill_websites.py ..\unique_links.csv --dry-run`
2. Run one small real batch: `--limit 5` into your TEST Websites vault
   (the 50-link trial vault from Phase 2 is perfect).
3. In Obsidian, **move two notes by hand** to different category
   folders.
4. Run any batch again (or the backfill once more) and read the run
   report's Note State section: your two moves listed as corrections;
   open the notes — their `category:` lines now match the folders you
   chose and `category_locked: true` is set.
5. Delete one note, run again: its URL is listed as dismissed; send
   that link to the bot again — it is skipped, never re-added.

## 4. Test results

- **Before any change:** 239/239 green (v0.11.0 baseline, main).
- **New tests:** 36 (`tests/test_phase3.py`).
- **Final: 275/275 green, 0 skips.** Compile gate: 28 modules OK.
- **Offline golden run:** 30/30, 0 invalid (unchanged).
- **Acceptance, SPEC §6 Phase 3:** simulated moves/edits/deletes/
  duplicates/unmapped folders in temp vaults produce exactly the §4.4
  outcomes ✓ · running twice changes nothing more ✓ · the baseline run
  flags nothing on an existing vault ✓ · the backfill resumes after
  being interrupted ✓ (all as tests).

## 5. Decisions I made (each with the alternative I rejected)

1. **A move into an unknown folder is NOT a correction.** The SPEC's
   unmapped row says "keep it there, never invent a category" — so the
   actor skips it and reports it. Rejected: updating the category line
   to the folder's raw name (that would mint a category nobody defined).
2. **The dismissed table is named `note_state_dismissed`.** The
   Websites pipeline already had a `dismissed_urls` table in the same
   `cache.db` (Phase 2) — the first full-suite run caught the name
   collision ("no such column: url"), so the note-level list got its
   own name. Rejected: renaming the Phase 2 table (it is already part
   of a merged release).
3. **Websites folders are validated against the TAXONOMY, not the
   GitHub category map.** Website notes live under taxonomy names
   (`Design/UI-UX & Product Design`), which the GitHub map can't parse
   — every nested website note would have read as "unmapped". A
   taxonomy-aware resolver fixes that. Rejected: reusing the GitHub
   map (silent misreads).
4. **`category_locked: true` is written into the note front-matter AND
   the database.** The front-matter line makes the lock visible in
   Obsidian and lets the Recategorize dialog skip the note without a
   database lookup; the database flag drives the classifier skip.
   Rejected: database-only (invisible, and the dialog would need a DB
   connection it doesn't have).
5. **The backfill tool is PyQt-free.** It reuses the pipeline's core
   module with a stdlib vault probe instead of importing the GUI's
   VaultIndex. Rejected: importing the GUI package in a tool (slow,
   and tools must stay headless-safe).
6. **The optional "past corrections as classifier examples" item is
   deferred** to Phase 4: it needs the per-task model override
   (`models.classify`) that Phase 4 introduces anyway. The corrections
   log + `move_summary_lines` are built and tested now, so wiring the
   examples later is a one-liner.

## 6. Risks and unknowns

- **GitHub Actions on GitCurator itself is still billing-blocked**
  (since Sep 24). Working bridge this session: the public
  `my-awesome-websites-directory` repo runs the exact same gate
  ("mirror" runner) against private GitCurator via a token secret —
  free, unlimited minutes. GitCurator itself must NOT be made public:
  its history contains your live Telegram bot token and a live GitHub
  PAT (see my message — details and options there).
- **The backfill is a real-crawl tool.** Run it dry first; some sites
  will land in `_review` (bot-blocked or paywalled) — that is the
  designed behavior, and they retry automatically.
- **Corrections are recorded, not yet browsable.** The log is in
  `cache.db` + the run report + `--status`; a small management UI comes
  with the later dashboard work.

## 7. Questions for you (at most 3)

1. **Merge phase 3 into main?** (v0.12.0, tag ready, 275/275.)
2. **The backfill:** want me to run the first real batches here (I can
   use the sandbox LLM endpoint) into a test vault so you can read 20–30
   real notes before you run it on your machine, or will you run it
   yourself after merging?
3. Reminder: the two directory repos are **public until Oct 1** (your
   instruction) — don't run a vault seal to them while public. I'll
   flip them back to private at the start of our next session (after
   Oct 1), or on your word.

---

*Built on branch `phase-3-moves-backfill` (pushed, NOT merged — your
word, as always). Next: Phase 4 (LLM backends: relabeling, JSON mode,
per-task models, golden set on Ollama + llama.cpp) on your go.*
