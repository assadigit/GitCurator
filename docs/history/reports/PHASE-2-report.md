# Phase 2 Report — Website Pipeline (v0.11.0)

**Plan (15 lines, as built):**
1. `core/taxonomy.py` — tolerant parser for your `website-library-categories.md`, exact-match validation, safe folder paths. Tested against the real file.
2. `core/prompts.py` + `app/prompts/w01/w02/w03` — SPEC Appendix A prompts, verbatim; the loader refuses to run with any `{{slot}}` unfilled.
3. `core/web_fetch.py` — polite fetcher: timeout, 2 MB cap, per-domain pause, clear User-Agent, redirect cap, PDF detection.
4. `core/web_extract.py` — stdlib title/description/text extraction; JavaScript-shell and paywall detection; non-UTF-8 handling.
5. `core/website_pipeline.py` — the per-link flow (SPEC §4.3): canonicalize → dedupe (4 layers) → fetch → extract → classify (2 passes, validated) → analyze → atomic write; `_review` + multi-day retries; dry-run.
6. `links.py` — `owner.github.io/repo` → GitHub pipeline; gists → websites with `#snippet`; website URL canonicalization (GitHub's unchanged).
7. Thin hooks in `gui/app.py` + `cli.py` — websites phase after the GitHub loop; `_inbox` bypassed when ON; github-off + websites-on; reports + `--status` sections.
8. Golden set (`tests/golden/websites.json`, 30 links, proposed expected categories) + `tools/run_golden_websites.py` (`--offline` for CI, `--live` for real).
9. Tests: 71 new (`tests/test_phase2.py`); docs: VERSION/CHANGELOG/README/STATUS; CI: 27 modules, 238 tests + offline golden run.

---

## 1. What I built

Your non-GitHub links finally go somewhere real. Flip **Settings → 📁 Vault →
Websites pipeline ON** (after setting the Websites vault path) and every
non-GitHub link from the bot queue, import files and Telegram fetches is
fetched, read, classified into your own category file, analyzed, and written
as a note into the Websites vault — under
`<Category>/<Subcategory>/<Name>.md`, with the full body from the spec
(one-line description, core offerings, standout feature, **Best used for**,
pricing, login, similar tools) and the ownership stamps from Phase 1.
With the switch **OFF** (the default), the old `_inbox/` behavior continues
unchanged — verified by test.

Safety rails built in: every model answer must exactly match a name from
your taxonomy file or it is retried (with a corrective message) and then
routed to `_review/` instead of a wrong folder; low-confidence answers go
to `_review/`; unfetchable links get a minimal `_review` note and are
retried automatically up to 3 times over several days; a successful retry
upgrades the placeholder to a real note — and only deletes the placeholder
if it is still byte-identical to what the app wrote (a hand-edited
placeholder is never touched, just flagged). A dry-run writes nothing and
records nothing. The Stop button works mid-phase.

## 2. What changes for you

- **Nothing until you flip the Websites switch ON.** Default OFF = exactly
  v0.10.0 behavior.
- With it ON: website notes appear in your Websites vault; the run report,
  summary log and `--cli --status` gain a Websites section (processed /
  to-review / skipped / retry queue); non-GitHub links stop landing in
  `_inbox/` tables.
- GitHub Pages links (`owner.github.io/repo`) now process as their GitHub
  repo; gists become website notes with a `#snippet` tag.
- Two things need your attention (both in "Risks" below): GitHub Actions
  billing, and your Cloudflare tokens' missing Workers AI permission.

## 3. How to check it yourself (about 5 minutes)

From the `app/` folder (no GUI needed):

```cmd
python -m unittest tests.test_phase2 -v          (71 tests, all green)
python gitcurator\tools\run_golden_websites.py --offline   (30/30, 0 invalid, no network)
python main.py --cli --status                    (websites counters section)
```

Then the real thing, on a TEST vault:
1. Settings → 📁 Vault → set **Websites vault** to a test folder → switch
   **Websites pipeline ON**.
2. Send yourself 2–3 website links via the bot (or an import file) and run
   a batch.
3. Open the Websites vault: notes filed under your categories; check one
   note's frontmatter (`source`, `category`, `subcategory`,
   `fetch_status`, `managed_by`, …) and its **Best used for** line.
4. Try it again with `--dry-run` — nothing is written, everything is
   logged.

## 4. Test results

- **Before any change:** 168/168 tests green (Phase 1 baseline, main).
- **New tests:** 71 (`tests/test_phase2.py`).
- **Final: 239/239 green, 0 skips.** Compile gate: 27 modules OK.
- **Offline golden run:** 30/30 processed, **0 invalid category names**.
- **Live golden run (real fetches + real model):** see
  `docs/reports/golden-websites-report.md` — the side-by-side verdicts
  against my proposed expected categories are there for your review.

## 5. Decisions I made (each with the alternative I rejected)

1. **Dedupe identity for websites keeps meaningful query parameters**
   (`youtube.com/watch?v=abc`), with tracking params (`utm_*`, `fbclid`,
   `gclid`, `ref`) stripped. Rejected: reusing GitHub's `normalize_url`
   (it strips ALL query params — two different YouTube videos would
   collide). GitHub's normalizer is untouched, per SPEC §4.3.1.
2. **Low-confidence classification is not a fetch retry.** The note waits
   in `_review/` for your move (SPEC §4.4 correction flow). Rejected:
   putting low-confidence links in the fetch-retry queue — re-fetching
   doesn't fix a judgment call, and it would burn the 3 retries.
3. **A failed `_review` placeholder is upgraded in place** (same path,
   atomic overwrite) and removed only when the app still owns it
   (fingerprint match). Rejected: always writing a new file (two files,
   one `source:` — exactly the duplicate §4.4 flags) and always deleting
   the old one (could destroy your edits).
4. **The t.co golden entry stays as the original short URL** (it resolves
   to phosphoricons.com — I verified the redirect). Rejected: replacing it
   with the resolved URL — the golden set should test what you actually
   saved.
5. **Live golden run used a temporary OpenAI-compatible endpoint in my
   build sandbox** (the z-ai service), because both of your Cloudflare
   tokens are valid but carry no Workers AI permission (verified — the
   /ai endpoints answer "Authentication error"). Rejected: skipping the
   live run (SPEC acceptance requires it) or blocking the phase on new
   credentials. Phase 4 re-runs the golden set on your real backends
   (Ollama / llama.cpp) anyway.
6. **Expected categories in the golden set are my proposal** — including
   two judgment calls I flagged in the file's `notes` (designmd.me and
   mixkit.co could arguably live elsewhere). Rejected: guessing silently.
   Edit `tests/golden/websites.json` and re-run; my values are just the
   baseline for the side-by-side.
7. **`--offline` mode fakes BOTH the LLM and the fetch** (canned pages per
   golden entry). Rejected: real fetches in offline mode — CI would depend
   on 30 live websites (several block datacenter IPs with 403s — we saw
   pixabay, reddit, coolors, iconscout do exactly that in the live run).

## 6. Risks and unknowns

- **GitHub Actions is down for your account since 2026-09-24** — runners
  fail to start with "recent account payments have failed or your spending
  limit needs to be increased." This is account billing, not code: the
  workflow triggers correctly, and the local gate mirrors CI exactly
  (same compile list, same test command, plus the offline golden run) and
  is fully green. Fix: GitHub → Settings → Billing & plans. Until then,
  trust the local gate output I include in these reports.
- **Your Cloudflare tokens have no Workers AI permission.** Both tokens
  verify as active, but every `/accounts/<id>/ai/...` call returns
  "Authentication error". If you want Workers AI as your cloud model,
  edit the token's permissions in the Cloudflare dashboard (Workers AI
  → Read/Run). The app's `cloud_api_url/cloud_api_key/cloud_model` keys
  are already compatible with Cloudflare's OpenAI-style endpoint.
- **Bot-protected sites (403 for datacenter IPs)** — pixabay, reddit,
  coolors and iconscout refused the golden run's fetch from this sandbox.
  On your home Windows machine they will likely work. Any site that
  still refuses gets the `_review` + retry treatment — nothing is lost.
- **The live model run is rate-limited** by my sandbox endpoint under
  sustained calling (I added retries with backoff on both sides). Your
  local Ollama has no such limit. The verdicts that did complete agreed
  with the expected categories on every classified link — see the report.

## 7. Questions for you (at most 3)

1. **Approve the golden set?** Read `docs/reports/golden-websites-report.md`
   — the "Expected" column is my proposal. Say "golden ok" or tell me
   which rows to change (or edit `app/tests/golden/websites.json`
   yourself and I'll re-run).
2. **Merge phase 2 into main?** (v0.11.0, tag ready.)
3. **The 50-link trial:** SPEC's owner review for this phase is a trial
   run into a TEST Websites vault which you then read. Want me to prep a
   50-link trial file (from your CSV, spread across categories) for the
   next session, or do you want to run the trial yourself first?

---

*Built on branch `phase-2-website-pipeline` (pushed, NOT merged — your
word, as always). All 239 tests green. Next: Phase 3 (moves as corrections
+ the ~930-bookmark backfill) on your go.*
