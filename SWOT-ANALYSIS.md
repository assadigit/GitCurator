# GitCurator — SWOT Analysis (v0.26.0, software-engineering focus)

**Scope:** the full engineering picture after the v0.25.0 hygiene &
modularization pass and the v0.26.0 SWOT hardening pass. The previous
SWOT (v0.06 era) is archived at `docs/history/SWOT-ANALYSIS.md`.
**Evidence:** 837 Python tests + 37 Worker Node tests + the 30-link
offline golden set, all green; the AST fingerprint / public-surface /
fresh-import safety net over 94 modules; `wrangler deploy --dry-run`;
an adversarial review pass (worklog, Task 14) that independently
reproduced the v0.25.0 gates.

---

## What v0.26.0 already fixed (found by this analysis, each locked by tests)

| # | Was | Fix (commit) |
|---|-----|--------------|
| 1 | `mirror.py` ⇄ `linking.py` import **cycle** (a lazy in-function import dodged it) | shared leaf module `core/mirror_keys.py`; the package import graph is one-way (`7d64c1e`) |
| 2 | **12 bare `except:`** blocks silently swallowing KeyboardInterrupt/SystemExit | all narrowed to `except Exception:`; AST-based test keeps the package clean (`5547aa8`) |
| 3 | TLS verification **silently hard-disabled** on 4 outbound paths (cloud LLM ×2, banner, sources) | explicit `verify_ssl` config flag via `core/netctx.py` (default off = historical behavior; the websites fetcher always verified) (`d771985`) |
| 4 | `owner.github.io/<repo>` typed `non_github` by the Worker but routed to the GitHub pipeline by the desktop — **two identities for one link** | Worker ports the desktop's mapping exactly; one canonical ledger identity (`ec2e5ff`) |
| 5 | A **transient** `dlq_exhausted` dead letter permanently hid a ledgered link from `/pending` and earned re-sends a permanent 💀 | only the POLICY reason (`blocked_domain`) hides/blocks now; transient failures stay visible + re-sendable (`ec2e5ff`) |
| 6 | Failed `enrich` messages **acked silently** in the DLQ | recorded to the activity log (audit), then acked (`ec2e5ff`) |
| 7 | Run summaries **accumulated forever** in the vault root (and VaultSeal committed them all) | capped to `summary_keep_last` (default 10; exact-pattern matcher, dry-run aware) (`5cb739c`) |
| 8 | `tools/diagnose_code.py` **executed at import** (read config.json, printed, leaked module attrs) | work moved behind `load_credentials()` + the `__main__` guard (`5cb739c`) |
| 9 | 79-file root `dashboard/` duplicated the CI gate; `unique_links.csv` (personal bookmarks) sat in a **public** repo | both removed from the tree (owner-approved Tier B); the CSV is gitignored — history purge is a separate owner operation (`bb5cf35`) |

## Strengths

1. **Test discipline that survives refactors.** 837 zero-network tests
   (23 modules) + 37 Worker tests + a 30-link offline golden set run on
   every push; the v0.25.0 splits were proven byte-for-byte by an AST
   fingerprint net (1,047 hashes), a public-surface check and a
   fresh-process import check — the same net now guards every future
   change (allowlists decode to documented intentional edits only).
2. **Modular, one-way architecture.** `gui/app.py` 13.8k → a ~470-line
   facade; `main_window/` = shell + 15 domain mixins; the batch worker =
   shell + 6 domain mixins; the CLI = commands + terminal + run engine;
   `WebsiteStateDB` extracted; no import cycles (fresh-import proven).
3. **The app⇄Worker contract is now specified and tested on both
   sides.** HMAC (incl. query strings), `/api/pending` shape, dedup,
   blocked/self-domain policy, the DLQ drain, GitHub-Pages identity and
   worker-version staleness all have Node tests; the desktop has the
   matching check in Test Connection.
4. **Safety properties are design invariants, not habits.** Dry-run
   everywhere, atomic writes, moves-are-corrections, sealed-vault
   reconciliation (main never force-pushed), secrets never in
   `.git/config`, the taxonomy may only file names the owner wrote.
5. **Reproducible releases.** The Windows zip is built deterministically
   from the tag (byte-identical rebuilds, verified across the public
   history scrub).
6. **Agent-ready.** `AGENTS.md` briefing, an architecture map, and a
   do-not-read list keep future sessions effective from minute one.

## Weaknesses (remaining)

1. **~410 broad `except Exception` blocks**, several silent in
   non-critical paths. The dangerous bare forms are gone (fixed above),
   but a logging pass is still owed so failures leave a trace.
2. **Two deliberately-unsplit files remain large:** `main_window/ui.py`
   (initUI ≈ 1,240 lines — cohesive but big) and `core/llm_client.py`
   (1,381 — kept flat because 7 test files patch its module attributes;
   splitting would silently break those patches).
3. **~3k lines of dormant cloud/ modules** (`cloudflare_manager`,
   `cloudflare_gui`, `gdrive_backup`, `gdrive_gui`) kept by owner
   decision ("fixed, not deleted") with no test coverage — the one part
   of the tree the suite does not exercise.
4. **The deployed Worker lags the repo** (v0.22.0 live vs v0.26.0
   prepared). The staleness check warns, but the DLQ drain, HMAC
   query fix and the identity fixes only take effect after the owner
   runs the 5-minute deploy pack (`app/cloudflare-bot/DEPLOYMENT.md`).
5. **Single-maintainer bus factor** — concentrated session knowledge
   (proxy quirks, the Telethon auth dance) that AGENTS.md only partly
   captures.

## Opportunities

1. **Deploy the prepared Worker** — the single highest-value 5 minutes
   available (closes weakness 4 and activates fix 4/5/6 in production).
2. **A dead-letter resolve endpoint** on the Worker dashboard: the
   `resolved` column exists but nothing can set it; an owner-facing
   "resolve" button would clear transient dead letters without
   re-sending.
3. **mtime-cached vault index** shared by all flows (the index walk
   still repeats per batch flow; verify-all on large vaults pays it).
4. **Coverage measurement** on the existing suite (`.coverage` is
   gitignored but never collected) to aim the logging pass of W-1.
5. **Retire the gitcurator-gate mirror** — the repo is public with free
   Actions minutes; the mirror is dead weight.
6. **Type-check `core/`** (pure stdlib, best typed-first candidate)
   with mypy/pyright in strict-per-module mode.

## Threats

1. **Credential rotation is still pending (P0).** Every secret was
   exposed in chat during development — including again in the session
   that produced this document. Rotate the Telegram bot token +
   api_id/hash, both Cloudflare tokens and the GitHub PAT, then update
   the app (Settings → Credentials) and the Worker
   (`npx wrangler secret put BOT_TOKEN`). Nothing else in the tree
   holds live secrets (history was scrubbed pre-public; verified again
   this pass).
2. **`unique_links.csv` remains in git history** (public repo). Removed
   from the tree in v0.26.0, but a true purge needs a history rewrite
   (`git filter-repo` + force-push) — a separate owner decision with a
   known playbook from the 2026-09-30 scrub.
3. **Telegram/Telethon platform drift** — sessions break server-side
   periodically; timeouts convert hangs into clean errors, but the
   fetch path depends on a session file that can expire any time.
4. **Proxy fragility (v2rayN/SOCKS)** — the whole fetch path assumes a
   working local proxy; failures degrade to user-visible stalls despite
   the pre-flight probe.
5. **Local-LLM coupling** — Ollama model churn (renames/retirements)
   has produced owner-facing failures before; the 3-layer picker
   mitigates, a known-good model pin would remove the class.
6. **Platform quota drift** (Cloudflare D1/Queues, GitHub API rate
   limits) — mitigated by dedup, backoff and throttles, but unbounded
   growth anywhere upstream would surface as dead letters (now visible
   + auditable, fix 5/6).

---

## Priority recommendations (next session)

| # | Item | Why first |
|---|------|-----------|
| P0 | **Rotate every credential** (owner action, 15 min) | Live exposure in chat history |
| P1 | **Deploy the prepared v0.26.0 Worker** (owner action, 5 min — DEPLOYMENT.md) | Activates the DLQ drain + identity fixes in production |
| P2 | Logging pass over the broad excepts (start where coverage is thinnest) | Silent failures leave no trace today |
| P3 | Dead-letter resolve endpoint + dashboard button | Owner self-service for transient dead letters |
| P4 | mtime-cached vault index | Felt speed on large vaults |
| P5 | Decide the dormant cloud/ modules' fate (delete = one command; keep = accept W-3) | 3k untested lines of maintenance surface |

## Verification evidence (v0.26.0)

- `python -m unittest …` (the exact CI module list) → **837/837 OK**
- `npm test` (Worker, Node built-in runner) → **37/37 OK**
- Offline golden run → **30/30 processed, 0 invalid categories**
- Fingerprint (1,047 hashes / 1,064 callables), public-surface and
  fresh-import checks → **PASS** (allowlists = the 26 documented
  intentional edits of this pass)
- `npx wrangler deploy --dry-run` → bundle + bindings valid
  (no production deploy without the owner — prepare-only rule)
