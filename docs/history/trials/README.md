# Websites pipeline — the 50-link trial (Phase 2 owner review)

This is the real-world test of the new Websites pipeline (v0.11.0),
on **50 links from your own `unique_links.csv`** — the same
deterministic spread rule as the golden set: no duplicates, no GitHub
links, one link per site, **50 different sites**. The list itself is
[`websites-trial-50.txt`](websites-trial-50.txt).

You run this **once**, into a **test vault**, and then read the notes.
Nothing about your real vaults is touched.

---

## Before you start (2 minutes)

1. **Create an empty test folder** — for example
   `G:\Docs\Documents\Test Web Vault`. Do **not** use your real
   Websites vault (`G:\Docs\Documents\Obsidian Website Directory`).
2. Open GitCurator → **Settings → 📁 Vault**:
   - **Websites vault** → pick the test folder.
   - Switch **ON**: `Websites — the new pipeline`.
   - Leave the GitHub pipeline ON (it has nothing to do for these
     links — all 50 are non-GitHub).
   - ⚠️ **Leave `Seal after every run` and `Push to GitHub` OFF.**
     The two backup repos are temporarily **public** (until Oct 1);
     a seal now would publish your notes. Your old backup repo is
     unaffected.
3. The model: whatever you use today (your local Ollama). If you want
   to try **Cloudflare Workers AI** instead, set in the same settings
   (or `config.json`):
   - `llm_provider`: `cloud`
   - `cloud_api_url`: `https://api.cloudflare.com/client/v4/accounts/YOUR_CF_ACCOUNT_ID/ai/v1`
   - `cloud_api_key`: *(your new Cloudflare token — after you add the
     Workers AI permission to it, see the phase report)*
   - `cloud_model`: `@cf/meta/llama-3.1-8b-instruct`
4. Close Settings.

## Run it (about 20–45 minutes with a local model)

1. In the main window, set **Import file** to this file:
   `GitCurator\docs\trials\websites-trial-50.txt`
   (browse to it — the `#` comment lines are ignored automatically).
2. Run the batch the way you always do.
3. Watch the log: every link is fetched, read, classified into your
   category file, analyzed, and written. Unfetchable or low-confidence
   links go to `_review/` and are retried over the next days —
   **nothing is dropped**.
4. When it finishes, the summary gains a **Websites section**:
   processed / to-review / skipped / retry queue.

## Read the notes (the actual review)

Open the **test vault** and check:

- Notes are filed under `<Category>/<Subcategory>/<Name>.md` using
  **your** category file's names.
- One note's front-matter: `source`, `category`, `subcategory`,
  `fetch_status`, `managed_by`, `schema_version`…
- The **Best used for** line and the full body (one-line description,
  core offerings, standout feature, pricing, login, similar tools).
- The `_review/` folder — anything there is a judgment call waiting
  for you; those same links are exactly what Phase 3's correction
  flow is for.

## Optional extra check (1 minute)

Run the same import again with the **`--dry-run`** flag: everything is
logged, nothing is written, nothing is recorded.

## Then tell me

- Which notes landed in the wrong folder (that's normal — a few will;
  the correction flow learns from them).
- Anything that looks broken or ugly in a note.
- Whether the `_review` cases make sense to you.

That feedback feeds directly into Phase 3 (moves as corrections +
the full ~930-bookmark backfill).
