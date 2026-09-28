# GitCurator — Session Prompts (copy and paste)

## How to use this

1. The files are already in the repo: `SPEC.md` and `STATUS.md` in the root, the taxonomy at `app/taxonomy/website-library-categories.md`, and this file at `docs/KICKOFF.md`. Nothing to place.
2. Give the coding AI access to the repo, then paste **one** prompt from below. One phase per session, and start a **fresh session** for each phase.
3. When the AI finishes, it writes a report (also saved in `docs/reports/`). Read section "How to check it yourself" and try it. Ask the AI in the same session if anything is unclear.
4. **Only paste the next phase's prompt after you have approved the previous phase.** Each prompt below states your approval, so do not paste it until it is true.
5. If a session is cut off, use the "Resume" prompt. If you want changes, use the "Fix after review" prompt.

Things the AI will ask you for along the way: the path to your `unique_links.csv` (Phase 0), a private GitHub repo for the Websites vault backup and a folder for the Websites vault (Phase 1), and the path to your Manual Notes vault (Phase 5).

---

## Phase 0 — Groundwork

```
You are starting a new working session on the GitCurator repository (a Python app that turns links from a Telegram bot into Obsidian notes using a local LLM).

Read these files first, in this order: SPEC.md, STATUS.md, app/README.md, and the newest entry of CHANGELOG.md.

Your job this session is Phase 0 of SPEC.md, and only Phase 0. Follow the "Non-negotiables" and the "Working protocol" in SPEC.md exactly. Work on a new branch named phase-0-groundwork. Do not merge it. Do not run anything against my real vaults; use temporary folders and synthetic data.

I cannot read code, so explain everything in plain language and prove your work with test output. When you finish, write the report in the format from SPEC.md section 3 (also save it as docs/reports/PHASE-0-report.md), update STATUS.md, and stop. Do not start Phase 1.

If you need something from me (for example the path to my unique_links.csv), ask in your first message, then continue with whatever does not depend on it.
```

## Phase 1 — Vault settings and ownership

```
You are starting a new working session on the GitCurator repository.

Read these files first, in this order: SPEC.md, STATUS.md, app/README.md, the newest entry of CHANGELOG.md, and docs/reports/PHASE-0-report.md.

I confirm that Phase 0 has been reviewed and approved by me. Record that in STATUS.md. Your job this session is Phase 1 of SPEC.md, and only Phase 1. Follow the "Non-negotiables" and the "Working protocol" in SPEC.md exactly. Work on a new branch named phase-1-vault-settings. Do not merge it. Do not run anything against my real vaults; use temporary folders and synthetic data.

The GitHub pipeline must behave exactly as before when the new website pipeline is switched off, and my existing config.json must keep working.

I cannot read code, so explain everything in plain language and prove your work with test output. When you finish, write the report in the format from SPEC.md section 3 (also save it as docs/reports/PHASE-1-report.md), update STATUS.md, and stop. Do not start Phase 2.
```

## Phase 2 — Website pipeline

```
You are starting a new working session on the GitCurator repository.

Read these files first, in this order: SPEC.md, STATUS.md, app/README.md, the newest entry of CHANGELOG.md, and docs/reports/PHASE-1-report.md. Also read app/taxonomy/website-library-categories.md.

I confirm that Phase 1 has been reviewed and approved by me. Record that in STATUS.md. Your job this session is Phase 2 of SPEC.md, and only Phase 2. If it is too big for one session, finish a coherent first half (the taxonomy parser, fetching and extraction with fixtures), report, and stop; I will start another session for the rest. Follow the "Non-negotiables" and the "Working protocol" in SPEC.md exactly. Work on a new branch named phase-2-website-pipeline. Do not merge it. Do not run anything against my real vaults; use temporary folders and synthetic data. Write to a real vault only after a dry-run, a snapshot, and my explicit OK in this session.

I cannot read code, so explain everything in plain language and prove your work with test output. When you finish, write the report in the format from SPEC.md section 3 (also save it as docs/reports/PHASE-2-report.md), update STATUS.md, and stop. Do not start Phase 3.
```

## Phase 3 — Moves as corrections, and the backfill

```
You are starting a new working session on the GitCurator repository.

Read these files first, in this order: SPEC.md, STATUS.md, app/README.md, the newest entry of CHANGELOG.md, and docs/reports/PHASE-2-report.md.

I confirm that Phase 2 has been reviewed and approved by me. Record that in STATUS.md. Your job this session is Phase 3 of SPEC.md, and only Phase 3. Follow the "Non-negotiables" and the "Working protocol" in SPEC.md exactly. Work on a new branch named phase-3-moves-and-backfill. Do not merge it. Do not run anything against my real vaults; use temporary folders and synthetic data. Write to a real vault only after a dry-run, a snapshot, and my explicit OK in this session.

Important: the first run after this feature ships must record a baseline silently. It must not treat my 600+ existing GitHub notes as changed.

I cannot read code, so explain everything in plain language and prove your work with test output. When you finish, write the report in the format from SPEC.md section 3 (also save it as docs/reports/PHASE-3-report.md), update STATUS.md, and stop. Do not start Phase 4.
```

## Phase 4 — LLM backends

```
You are starting a new working session on the GitCurator repository.

Read these files first, in this order: SPEC.md, STATUS.md, app/README.md, the newest entry of CHANGELOG.md, and docs/reports/PHASE-3-report.md.

I confirm that Phase 3 has been reviewed and approved by me. Record that in STATUS.md. Your job this session is Phase 4 of SPEC.md, and only Phase 4. Follow the "Non-negotiables" and the "Working protocol" in SPEC.md exactly. Work on a new branch named phase-4-llm-backends. Do not merge it. Keep the existing config key names so my current settings keep working. Do not run anything against my real vaults.

I cannot read code, so explain everything in plain language and prove your work with test output. When you finish, write the report in the format from SPEC.md section 3 (also save it as docs/reports/PHASE-4-report.md), update STATUS.md, and stop. Do not start Phase 5.
```

## Phase 5 — Mirror into Manual Notes

```
You are starting a new working session on the GitCurator repository.

Read these files first, in this order: SPEC.md, STATUS.md, app/README.md, the newest entry of CHANGELOG.md, and docs/reports/PHASE-4-report.md.

I confirm that Phase 4 has been reviewed and approved by me. Record that in STATUS.md. Your job this session is Phase 5 of SPEC.md, and only Phase 5. Follow the "Non-negotiables" and the "Working protocol" in SPEC.md exactly. Work on a new branch named phase-5-manual-notes-mirror. Do not merge it.

Critical rule for this phase: the app must never create, change or delete anything in my Manual Notes vault outside the Library/ folder. Prove that with tests. Use temporary folders only; a real run happens only on a copy of my Manual Notes vault, after a dry-run and my explicit OK in this session.

I cannot read code, so explain everything in plain language and prove your work with test output. When you finish, write the report in the format from SPEC.md section 3 (also save it as docs/reports/PHASE-5-report.md), update STATUS.md, and stop. Do not start Phase 6.
```

## Phase 6 — Linking

```
You are starting a new working session on the GitCurator repository.

Read these files first, in this order: SPEC.md, STATUS.md, app/README.md, the newest entry of CHANGELOG.md, and docs/reports/PHASE-5-report.md.

I confirm that Phase 5 has been reviewed and approved by me. Record that in STATUS.md. Your job this session is Phase 6 of SPEC.md. It has steps that need my approval between them (recall hooks first, then embeddings, then candidate links, then the link store). Do the first step, report, and stop; I will start another session for each later step. Follow the "Non-negotiables" and the "Working protocol" in SPEC.md exactly. Work on a new branch named phase-6-linking. Do not merge it.

For the recall hooks: never modify my existing notes except by inserting the single delimited block described in SPEC.md, always show me a dry-run diff first, and wait for my approval of 20 sample notes before any bulk change.

I cannot read code, so explain everything in plain language and prove your work with test output. When you finish, write the report in the format from SPEC.md section 3 (also save it as docs/reports/PHASE-6-report.md), update STATUS.md, and stop.
```

---

## Resume (session was cut off)

```
You are resuming work on the GitCurator repository after an interrupted session.

Read SPEC.md and STATUS.md, then look at the current git branch, its commits, and any files left half-finished. Work out exactly what was completed and what was not, and tell me in plain language before you continue. Then finish the phase that STATUS.md says is in progress, following the "Non-negotiables" and the "Working protocol" in SPEC.md. Do not start a new phase. When you finish, write the report in the format from SPEC.md section 3, update STATUS.md, and stop.
```

## Fix after review (you found a problem or want a change)

Paste this, then write your feedback on the lines under it.

```
You are continuing the current phase of the GitCurator repository after my review. Read SPEC.md and STATUS.md first, then check out the current phase branch.

Below is my feedback. Fix exactly what I describe, add or update tests so it cannot come back, run the full test suite, update the phase report and STATUS.md, and stop. Do not start a new phase. If my feedback conflicts with SPEC.md, tell me and ask which one wins before changing anything.

My feedback:
```

## Independent review (optional, from a different AI or a fresh session)

Useful because you cannot read the code yourself. A second AI checks the first one's work.

```
You are an independent code reviewer for the GitCurator repository. You are NOT allowed to change any code in this session; review only.

Read SPEC.md and STATUS.md, then review the current phase branch against the phase description and the "Non-negotiables" in SPEC.md. Run the compile check and the test suite exactly as CI does, and report the results.

Check specifically: (1) does anything write to a vault outside what the phase allows, (2) does the GitHub pipeline still behave as before, (3) is any LLM output written into YAML, a filename or a path without going through the sanitizers, (4) does any network or LLM call lack a timeout, (5) can any secret end up in git, logs or reports, (6) are the tests real (do they fail if the feature is broken), and (7) does the report match what the code actually does.

Give me a plain-language verdict (approve, approve with small fixes, or do not approve), the list of problems ordered by severity, and how to confirm each one myself. I cannot read code, so do not just point at line numbers; explain what could go wrong for me.
```
