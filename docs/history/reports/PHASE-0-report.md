# Phase 0 report — Groundwork (v0.09.5)

**Branch:** `phase-0-groundwork` (not merged — awaiting your review)
**Date:** 2026-09-29 · **Suite:** 130 tests, all passing

## Plan (as executed)

1. Verify the SPEC §5 code facts Phase 0 depends on; record differences.
2. Baseline first: compile the 16 audited modules + run the 106 existing tests — all green before any change.
3. New `core/dryrun.py` (pure stdlib, no PyQt): the log-instead-of-write switch, a withheld-operations log, behavior-identical filesystem helpers, and a throwaway copy of `cache.db` for dry-run batches.
4. Gate `core/storage.py`'s atomic writers on the switch (2-line hook) — every canonical vault write is covered automatically.
5. Thin, marked hooks in `gui/app.py`: `ProcessingWorker(dry_run=…)`, switch flipped for the batch duration only, and the legacy raw writes inside the batch path routed through the helpers.
6. `--dry-run` flag on every CLI batch mode; dry-run also skips VaultSeal, Good Repos publish, bot-queue mark-read and `last_processed_msg_id`; saves a Markdown report outside the vault.
7. `tools/scan_vault_edits.py` — read-only vault scan → Markdown report outside the vault.
8. `tools/snapshot_vault.py` — timestamped zip of the whole vault, outside the vault.
9. `tools/pick_golden_links.py` — CSV → 30 domain-diverse candidates → `app/tests/golden/websites_candidates.json`.
10. 24 new tests over synthetic vaults; end-to-end dry-run rehearsal (found and fixed one real deadlock).
11. CI: 20 compiled modules, 6 test modules, 130 tests. `VERSION` → 0.09.5, CHANGELOG, README, STATUS updated.

---

## 1. What I built

Three safety tools and one safety mechanism, and **no change to how the app behaves**:

- **`--dry-run` (CLI flag):** any batch command can now be *rehearsed* — it fetches, analyzes and builds every note in memory, then writes **nothing** (no notes, banners, inbox tables, index/MOCs, reports, manifest), skips the backup push, the public-directory publish and the bot-queue bookkeeping, and prints + saves a report of every operation it *would* have performed.
- **`scan_vault_edits.py`:** a strictly read-only X-ray of a vault: notes per category, notes missing `source:`, duplicate `source:` URLs, and any note where you wrote your own content in *My Ideas & Notes / Social Signal (Manual) / Journal*.
- **`snapshot_vault.py`:** one timestamped zip of the entire vault, written outside it.
- **`pick_golden_links.py`:** reads your `unique_links.csv`, ignores GitHub links, and picks 30 diverse website candidates (deterministic) as the Phase 2 golden set.

## 2. What changes for you

**Nothing in day-to-day use.** The GUI is untouched, every existing command behaves exactly as before, and existing configs need nothing. You gain one optional flag (`--dry-run`) and three tools you can run whenever you want. A new folder `app/reports/` will collect scan reports, snapshots and dry-run reports (it is git-ignored).

## 3. How to check it yourself (about 5 minutes, from the `app` folder)

```cmd
REM 1) X-ray a COPY of your GitHub vault (read-only; report lands outside it)
python gitcurator/tools/scan_vault_edits.py "C:\path\to\vault-COPY"

REM 2) Zip that copy (timestamped backup, also outside the vault)
python gitcurator/tools/snapshot_vault.py "C:\path\to\vault-COPY"

REM 3) Rehearse your normal automatic run WITHOUT writing anything
python main.py --cli --auto --yes --dry-run
REM    -> at the end it says how many vault operations were withheld and
REM       where the full report is (app\reports\dry-runs\...)
REM    -> run it again WITHOUT --dry-run and the same batch really runs

REM 4) Golden-set candidates from your bookmarks export
python gitcurator/tools/pick_golden_links.py "C:\path\to\unique_links.csv"
```

Note: a dry-run still contacts GitHub/Ollama (it rehearses the *writes*, it is not offline).

## 4. Test results

- **Before any change (baseline):** 16 modules compiled, **106 existing tests — all passing** (34 core + 11 e2e + 28 goodrepos + 19 reliability + 14 quarantine). No pre-existing failures.
- **After Phase 0:** 20 modules compile; **130 tests, all passing** (106 existing unchanged + **24 new** in `tests/test_phase0.py`).
- **End-to-end rehearsal** (synthetic vault, fake LLM, stubbed GitHub API): dry-run batch completed with **24 vault operations logged and 0 performed**; the vault was **byte-for-byte identical** afterwards (every file hashed before/after); **no `cache.db` was created**; seal/publish/mark-read skipped; the report was written outside the vault. The rehearsal caught a real deadlock (fresh-install case) which was fixed and covered by a regression test.
- The scan and snapshot tools were also run as real command-line invocations on a synthetic vault (outputs shown in the session chat): correct findings, correct zip, vault untouched.

## 5. Decisions I made

1. **The dry-run switch gates `storage.atomic_write_text/bytes` globally** (including `config.json` writes). *Rejected:* gating only vault paths — a dry-run that persists `last_processed_msg_id` or a model switch would silently change what the next real run fetches.
2. **A dry-run batch reads `cache.db` through a throwaway copy** ("shadow cache"). *Rejected:* letting a dry-run write the real cache (it would mark repos processed and the next real run would skip them), and rejected opening the real cache read-only (`CacheDB` has no read-only plumbing and the write paths are everywhere).
3. **Dry-run also withholds VaultSeal, the Good Repos publish and the bot-queue mark-read.** *Rejected:* only gating file writes — a rehearsal that pushes a backup or consumes the bot queue is not a rehearsal.
4. **The `--dry-run` flag was added to the visual CLI only**, not to the legacy `--headless` mode. *Rejected:* adding it everywhere in Phase 0 — the documented workflow uses the visual CLI; headless can get it in a later phase with one line.
5. **The golden picker is deterministic** (domain round-robin, biggest domains first) and treats a category column as metadata. *Rejected:* random sampling — you could never compare two runs, and "spread across domains" is the spec's goal.
6. **Scan/snapshot/dry-run reports default to `app/reports/`** (git-ignored), with `--out` to override; all three refuse to write inside the vault they target. *Rejected:* writing next to the vault — your vault folder should never gain files from the app's safety tools.
7. **The per-platform inbox tables are now written through the shared atomic writer** instead of a duplicated inline copy — identical content on disk, plus fsync durability. *Rejected:* keeping the private copy — it was the one write path outside the audited storage module.
8. **Notes in folders that match no category are reported as "unmapped"** rather than ignored (the Phase 3 spec calls this state out; seeing it early is free).
9. **The scan tool trusts the real `note_builder` output as its template ground truth** — the tests generate fixture notes with the real builder, so if the template text ever changes, the tests fail until the tool is updated. *Rejected:* hand-written fixture text (would silently drift).

## 6. Risks and unknowns

- **A dry-run is not offline:** it really calls the GitHub API, your LLM and the banner CDN. It rehearses *writes*, not network traffic.
- **Cosmetic:** during a dry-run a few log lines still say "saved/updated" (e.g. "Final report saved: …"). Read them as "would save" — the closing "DRY-RUN COMPLETE" line and the report are the truth. Fixing every log line would have meant many more hooks in the 11,500-line GUI file; left for a later phase.
- **Link-tracker verification "fails" inside a dry-run** ("note file missing") — correct behavior, since nothing was written; it is in-memory only and persists nothing. The next real run is unaffected.
- **The golden picker had not run against your real `unique_links.csv` at original writing time** — it has now; see §8. Its column detection worked on the real file unmodified.
- The end-to-end rehearsal used a **stubbed GitHub API and a fake local LLM** (this sandbox's network blocks api.github.com). Everything between those two boundaries — parser, pre-flight, worker, note builder, write gating, report — was the real code.
- SPEC §5 said non-GitHub links land in `_inbox/non_github_links.md`; in reality they land in **per-platform** files (`_inbox/x_twitter_links.md` etc.) via `write_inbox_links_by_platform()`. Recorded in STATUS.md; changes nothing in Phase 0, but Phase 2's hook point is that function.

## 7. Questions for you

1. ~~**What is the path to your `unique_links.csv`?**~~ **Answered** — you committed it to the repo root on `main`; the picker ran for real (see §8).
2. For your Phase 0 owner-review: the spec asks you to run the scan on a **copy** of the GitHub vault. When you've made that copy, tell me its path and I'll give you the one-line command — or just run §3.1 yourself; the report is written for reading, not for code readers.

---

## 8. Addendum — the real golden-set run (same day)

You committed `unique_links.csv` to the repo, so I ran the picker on the **real file** (no synthetic data this time):

```text
Golden-set candidates written: app/tests/golden/websites_candidates.json
  CSV rows:                 784
  Duplicate URLs removed:   2
  GitHub links excluded:    5 (they belong to the GitHub pipeline)
  Non-GitHub links:         777
  Selected candidates:      30 of 30 target across 30 domains
```

- The 30 candidates are all design-related sites (mobbin.com, coolors.co, remove.bg, cobalt.tools, …), one per domain — maximum spread, as the spec asked. The full list is in the JSON, committed to this branch; **you approve or edit the final list in Phase 2**.
- **One candidate needs your eye:** `https://t.co/Mxdio85wvQ` is an unresolved Twitter short link. Your CSV itself says the domain was guessed from the display text (`phosphoricons.com`) and flags it "please verify". When we finalize the golden set in Phase 2, you can confirm it or I'll swap in the next candidate.
- Your CSV's **Category column is empty in all 784 rows** — that's fine; expected categories are chosen *with* you in Phase 2, exactly as the spec plans.
- Two runs of the picker produced an identical list (it is deterministic, as designed), and the full gate was re-run afterwards with the real JSON in place: **20 modules compiled, 130/130 tests passing**.
