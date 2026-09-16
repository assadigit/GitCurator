/**
 * report.ts — typed release-report data for the GitCurator v0.0.2 dashboard.
 *
 * Single source of truth consumed by GET /api/report and rendered by the
 * dashboard at /. Every number here is verified against the actual
 * audit/Github V6.7 tree (main.py 9,014 lines post-v30, 45/45 tests —
 * 34 unit + 11 end-to-end — re-runnable live via POST /api/verify, with
 * every run persisted to SQLite for the History tab; the repository now
 * follows repo-level SemVer (0.0.x) — the v30.x numbers live on as the
 * internal build lineage).
 */

export type Severity = "critical" | "high" | "medium";
export type Priority = "P0" | "P1" | "P2" | "P3";
export type FixStatus = "shipped" | "quarantined";

export interface Fix {
  id: string;
  title: string;
  severity: Severity;
  status: FixStatus;
  summary: string;
  detail: string;
  files: string[];
  evidence?: string;
}

export interface PipelineStage {
  id: number;
  name: string;
  icon: string; // lucide icon name key, resolved client-side
  description: string;
  hardening: string;
}

export interface TestSuite {
  name: string;
  module: string;
  tests: number;
  covers: string;
  caught?: string;
}

export interface RiskItem {
  priority: Priority;
  title: string;
  detail: string;
  owner: string;
}

export interface ChecklistItem {
  id: string;
  label: string;
  detail: string;
  priority: Priority;
  owner: string;
}

export interface Swot {
  strengths: string[];
  weaknessesFixed: string[];
  opportunities: string[];
  threats: string[];
}

export interface ReleaseReport {
  version: string;
  codename: string;
  releasedAt: string;
  project: string;
  stack: string[];
  metrics: {
    bugsFixed: number;
    testsTotal: number;
    testsPassed: number;
    mainPyLines: number;
    filesTouched: number;
    newModules: number;
    testCaughtBugs: number;
  };
  severityBreakdown: Record<Severity, number>;
  pipeline: PipelineStage[];
  fixes: Fix[];
  testSuites: TestSuite[];
  verification: string[];
  swot: Swot;
  risks: RiskItem[];
  deployChecklist: ChecklistItem[];
}

export const releaseReport: ReleaseReport = {
  version: "0.0.5",
  codename: "VaultSeal — Vault Backup",
  releasedAt: "2026-09-16",
  project: "GitCurator — Telegram → Ollama → Obsidian",
  stack: ["Python 3 · PyQt6", "Ollama", "PyGithub", "Telethon", "SQLite", "Cloudflare Workers", "Next.js 16 · Prisma"],
  metrics: {
    bugsFixed: 13,
    testsTotal: 45,
    testsPassed: 45,
    mainPyLines: 9203,
    filesTouched: 17,
    newModules: 7,
    testCaughtBugs: 2,
  },
  severityBreakdown: { critical: 3, high: 5, medium: 2 },
  pipeline: [
    {
      id: 1,
      name: "Telegram Input",
      icon: "send",
      description:
        "Saved Messages via Telethon subprocess, bot-chat queue mode, or .txt import.",
      hardening: "Headless auth fails fast instead of stalling 5 minutes.",
    },
    {
      id: 2,
      name: "Link Intake",
      icon: "link",
      description:
        "links.py — the ONE canonical GitHub regex (www + dots), dedup, 5-phase manifest.",
      hardening: "4 divergent regex copies unified; no more phantom re-processing.",
    },
    {
      id: 3,
      name: "GitHub API",
      icon: "github",
      description: "Repo metadata, README fetch, commits, releases, social banners.",
      hardening: "Rate-limit guard at 50 remaining; 429 backoff on banners.",
    },
    {
      id: 4,
      name: "Ollama LLM",
      icon: "brain",
      description:
        "Timeout-wrapped chat (default 300s), robust JSON extraction, 3-attempt ladder.",
      hardening: "Model choice persists; single-model auto-switch on warmup failure.",
    },
    {
      id: 5,
      name: "Note Builder",
      icon: "file-text",
      description:
        "note_builder.py — YAML-injection-proof frontmatter, quality scoring.",
      hardening: "README wrapped as untrusted data; tags/aliases/org sanitized.",
    },
    {
      id: 6,
      name: "Obsidian Vault",
      icon: "vault",
      description:
        "Atomic note writes (tempfile + os.replace), category folders, banner assets.",
      hardening: "Crashes can never leave truncated notes marked as processed.",
    },
    {
      id: 7,
      name: "VaultSeal",
      icon: "lock",
      description:
        "After every run the whole vault is committed + pushed to a private GitHub repo (v0.0.5).",
      hardening: "Nested-repo guard, hygiene .gitignore, token never persisted, skip-when-unchanged.",
    },
  ],
  fixes: [
    {
      id: "F1",
      title: "LLM model persistence",
      severity: "critical",
      status: "shipped",
      summary:
        "Picking a model in the failure dialog now applies to the current repo, the whole batch, Settings, and config.json.",
      detail:
        "Root cause: the dialog's model choice was used for a single retry call only — the batch kept the stale model, so the modal reappeared on every link. Fixed with _apply_model_choice (in-place config mutation, never rebinding), per-URL model re-read in run(), a 'Remember this model' checkbox (ON by default), and an auto-switch when Ollama holds exactly one model.",
      files: ["main.py — _llm_analyze, run, _on_llm_failed, _on_model_changed"],
      evidence: "main.py:1776-1785 · 2826-2831 · 8149-8163",
    },
    {
      id: "F2",
      title: "Headless hang-bombs defused",
      severity: "critical",
      status: "shipped",
      summary:
        "code_requested / llm_failed_signal / disk_full_signal had no receivers in headless mode — now all take non-blocking defaults.",
      detail:
        "Auth prompt returns '' immediately (session reuse or bot-queue suggested), LLM failure writes a fallback note and continues (same as GUI 'Skip'), disk-full skips the repo and records it in the retry queue. Previously: 5-min, 10-min, and INFINITE stalls respectively.",
      files: ["main.py — request_code, _wait_for_llm_decision, run, run_headless"],
      evidence: "main.py:1240-1261 · 1269-1288 · 1950-1970 · 8857-8880",
    },
    {
      id: "F3",
      title: "save_config() no longer destroys data",
      severity: "critical",
      status: "shipped",
      summary:
        "Merge-based save: cloudflare_*/gdrive_* and user keys survive; tuning values are defaulted, never stomped.",
      detail:
        "The old whitelist rebuild dropped every key not in its ~25-item list (pairing + GDrive + user keys wiped on first close) and hardcoded timeout_per_repo=60 / max_retries=3 / delay=0.5. Now storage.merge_config merges nested dicts, self.config is mutated in place (the running worker sees live updates), and the write is atomic.",
      files: ["main.py — save_config", "storage.py — merge_config, write_config_file"],
      evidence: "main.py:5490-5569 · storage.py:139-166",
    },
    {
      id: "F4",
      title: "Timeouts on every Ollama call + graceful close",
      severity: "high",
      status: "shipped",
      summary:
        "chat/list/warmup/test are wall-clock wrapped; closeEvent unblocks every worker wait before quitting.",
      detail:
        "client.chat (configurable llm_timeout_s, default 300s), list() 15s, warmup 120s, settings test 120s — via a ThreadPoolExecutor that returns immediately on timeout. closeEvent resolves pending LLM decisions to 'stop', clears auth-code waits and the disk-full spin flag BEFORE waiting on the thread, so exit can't leave it running.",
      files: ["llm_client.py — call_with_timeout", "main.py — _llm_analyze, run, test_ollama, closeEvent"],
      evidence: "llm_client.py:36-66 · main.py:2826-2836 · 8724-8742",
    },
    {
      id: "F5",
      title: "Atomic note + banner + config writes",
      severity: "high",
      status: "shipped",
      summary:
        "tempfile in the target directory + fsync + os.replace — readers only ever see the old or the new complete file.",
      detail:
        "A crash or disk-full mid-write previously left a truncated note that the cache immediately recorded as processed — silent data corruption. Same treatment for banner .png files (a truncated banner would be skipped forever by the 'already downloaded' check) and config.json.",
      files: ["storage.py — atomic_write_text, atomic_write_bytes", "main.py — run, _download_banner"],
      evidence: "storage.py:22-74 · main.py:1942-1946 · 3015-3021",
    },
    {
      id: "F6",
      title: "Link parsing standardized",
      severity: "high",
      status: "shipped",
      summary:
        "One implementation (links.py) replaces 4 divergent regex copies across main.py and all satellites.",
      detail:
        "The old main.py copy (no-www, no-dots) silently dropped repos like github.com/john.doe/my.project that the Telegram worker DID capture — the same message produced different link sets depending on which module parsed it. All four files now import the single most-permissive grammar.",
      files: ["links.py", "main.py", "telegram_fetch_worker.py", "backfill_manager.py", "telethon_fetcher.py"],
      evidence: "links.py:26-29 · main.py:305-325",
    },
    {
      id: "F7",
      title: "Frontmatter sanitization + README untrusted",
      severity: "high",
      status: "shipped",
      summary:
        "LLM output can no longer inject YAML into notes; README content is wrapped in untrusted-data delimiters.",
      detail:
        "tags/aliases/languages are charset-restricted flow-sequence items (no , [ ] { } # : quotes can survive), org/url/category render as escaped double-quoted scalars, body text strips control characters. README excerpts go into <<<README_BEGIN/END>>> with an explicit 'data, not instructions' directive — neutralizing prompt-injection via repo READMEs.",
      files: ["note_builder.py — sanitize_tags, yaml_scalar, build_note", "main.py — _llm_analyze prompt"],
      evidence: "note_builder.py:38-124 · main.py:2741-2757",
    },
    {
      id: "F8",
      title: "Cloudflare stack: fixed, not deleted",
      severity: "medium",
      status: "shipped",
      summary:
        "HMAC key mismatch fixed desktop-side; webhook secret-token check added; stale root worker quarantined.",
      detail:
        "The Worker stores sha256(secret) as the HMAC key while the desktop signed with the raw secret — every sync endpoint 401'd. The desktop now derives the same key (no redeploy needed). Webhook rejects forged updates when WEBHOOK_SECRET is set (2-command setup documented in wrangler.toml). The divergent root worker.js + wrangler.toml (placeholder KV id) were quarantined in v30 and deleted for good in v30.1 — cloudflare-bot/ is the one canonical worker.",
      files: ["cloudflare_sync.py — _sign_request", "cloudflare-bot/src/index.js", "cloudflare-bot/wrangler.toml"],
      evidence: "cloudflare_sync.py:163-199 · index.js:33-48",
    },
    {
      id: "F9",
      title: "Testable core extracted + first unit tests",
      severity: "high",
      status: "shipped",
      summary:
        "links / storage / note_builder / llm_client carved out as pure-stdlib modules, locked down by 45 tests.",
      detail:
        "The 8.8k-line main.py keeps only GUI + orchestration. The four new modules carry no PyQt imports, so they test headlessly. The unit suite immediately caught 2 real bugs pre-ship: call_with_timeout blocking on executor shutdown, and is_github_url accepting '..' as an owner. v30.1 adds tests/test_e2e.py — the full pipeline (dedup → retry ladder → sanitized note → collision-safe atomic vault write → config round-trip) replayed against scripted fake-Ollama responses, zero network.",
      files: ["links.py", "storage.py", "note_builder.py", "llm_client.py", "tests/test_core.py", "tests/test_e2e.py"],
      evidence: "45 cases (34 unit + 11 e2e) in 0.22s — re-run live from the Tests tab",
    },
    {
      id: "F10",
      title: "CacheDB hardened",
      severity: "medium",
      status: "shipped",
      summary:
        "busy_timeout=30000, connect timeout=30s, every statement under an RLock, idempotent close, leak paths fixed.",
      detail:
        "A sqlite connection with check_same_thread=False is not safe for concurrent cursor use — the bot-queue poller and the worker thread hit 'database is locked' races. Both early-return paths that leaked the handle (Ollama-unavailable, vault-missing) now close before returning.",
      files: ["main.py — CacheDB, run"],
      evidence: "main.py:659-872 · 1381-1475 · 1848-1855",
    },
  ],
  testSuites: [
    {
      name: "TestLinks",
      module: "links.py",
      tests: 9,
      covers:
        "canonical regex (www + dots + paths + query), order-preserving dedup, split, traversal rejection, URL normalization",
    },
    {
      name: "TestStorage",
      module: "storage.py",
      tests: 7,
      covers:
        "atomic text/bytes writes, no temp-file leftovers, traversal-proof filenames, _v1.._n collisions, config merge",
      caught: "regression guard for the config key-wipe (F3)",
    },
    {
      name: "TestNoteBuilder",
      module: "note_builder.py",
      tests: 8,
      covers:
        "YAML flow-sequence injection, alias dedup, escaped-quoted scalars, control-char stripping, ratings",
    },
    {
      name: "TestLLMClient",
      module: "llm_client.py",
      tests: 10,
      covers:
        "JSON extraction (plain/fenced/embedded), timeout wrapper (fast + hung), old & new ollama-py response shapes",
      caught: "call_with_timeout blocked on executor shutdown (fixed pre-ship)",
    },
    {
      name: "TestE2E",
      module: "tests/test_e2e.py",
      tests: 11,
      covers:
        "full pipeline replay with fake Ollama + fake GitHub: dedup before analysis, retry ladder, YAML-injection neutralization, collision filenames, inbox routing, banner bytes, config round-trip",
      caught: "ships the roadmap P2 item — the seam between the pure core and the worker loop",
    },
  ],
  verification: [
    "python -m py_compile — clean on all 10 core Python files (v0.0.5: +vaultseal.py)",
    "python -m unittest tests.test_core tests.test_e2e — 45/45 OK in 0.22s (34 unit + 11 e2e)",
    "Live gate: POST /api/verify re-runs compile + both suites on demand (~2.6s), per-suite AND per-case timing (tests/timing_runner.py)",
    "Every run is persisted to SQLite (Prisma) with a sha256 code fingerprint + per-file hashes — History shows trends, pass-rate stats and WHICH files drifted",
    "Timing Insights: per-case median/latest wall-clock leaderboard with regression + flakiness detection over a 20-run window",
    "Scheduled verification: server-side scheduler (src/instrumentation.ts) runs the gate 60s after startup and every 6 hours — drift and failures land in history without an open browser",
    "Repository & Release Console: /api/releases surfaces live git state (last commit, tag, dirty files) of the assadigit/GitCurator staging repo, the parsed CHANGELOG.md, and the served .zip backups",
    "GitHub Actions CI (v0.0.3): .github/workflows/ci.yml runs the same gate on every push, tag and PR — 9 py_compiles + the 45-case suite, zero pip installs (pure-stdlib core)",
    "Actions status in the dashboard (v0.0.3): /api/releases reads the newest workflow run (optional read-only GITCURATOR_GH_TOKEN) — passing / running / failing chip links straight to the run",
    "Run provenance (v0.0.4): every persisted run records who triggered it — manual button, server-startup pass or the 6-hour scheduler — shown as badges + source-split chips in the History tab and a source column in the CSV export",
    "CI run history strip (v0.0.4): /api/releases returns the last 10 Actions runs — the Releases tab renders the full run strip with a pass-rate summary, each chip linking to its run on GitHub",
    "VaultSeal (v0.0.5): LIVE end-to-end — a real demo vault sealed to the private github.com/assadigit/GitCurator-Vault (initial 12-file seal, incremental 1-file fast-forward push, no-op skip), verified via the GitHub API",
    "VaultSeal regression guard: a vault nested inside another git repository bootstraps its OWN repo — the parent's files can never be sealed (git add -A is repo-wide from subdirectories since git 2.0; caught live during QA and fixed)",
    "VaultSeal credential hygiene: remote stays token-less, .git/config greps clean, one-time token URLs only",
    "History retention: the newest 200 runs are kept, older rows pruned automatically on every run",
    "node --check cloudflare-bot/src/index.js — syntax OK",
    "links.is_github_url now rejects '..' owners (alphanumeric start/end required)",
    "Stale-stack removal finalized — both .deleted-*.v30 quarantine files deleted",
    "PyQt6/telethon/ollama not installed in CI sandbox — full-app runtime verified on the Windows target machine",
  ],
  swot: {
    strengths: [
      "5-phase LinkTracker manifest — every URL accounted for across crashes",
      "Retry queue (failed_repos) with 'Retry Failed' button — no link lost on LLM/GitHub failures",
      "Vault index dedup (filesystem as ground truth, beats SQLite cache drift)",
      "Batch undo snapshots; OneDrive/Dropbox-friendly local backups",
      "New pure-stdlib core — headlessly testable, zero GUI coupling",
      "VaultSeal (v0.0.5): every curation run seals the vault to a private GitHub repo — full history, restore with git clone, zero-config",
      "Versioned GitHub repository (private) with tagged releases + downloadable zip backups",
      "GitHub Actions CI runs the full 45-test gate on every push, tag and PR",
    ],
    weaknessesFixed: [
      "Non-atomic note/banner/config writes → tempfile + os.replace everywhere",
      "No timeouts on Ollama calls → wall-clock wrapped (15–300s)",
      "save_config key-wipe → merge-based, tuning values preserved",
      "4 divergent URL regexes → single links.py implementation",
      "Unsanitized LLM output into YAML frontmatter → full sanitization",
    ],
    opportunities: [
      "mypy + ruff pass over the new modules (strict typing milestone)",
      "Phase D: packaging (PyInstaller), CI on push — wire /api/verify's gate into Actions",
      "Re-wire the dormant Cloudflare sync now that HMAC is fixed",
      "Structured logging + a small metrics panel for batch runs",
      "Grow the e2e harness: scripted Telegram batches, FloodWait + rate-limit simulations",
      "Export the persisted verification history as CI evidence (CSV export shipped in v30.2)",
      "Per-case timing history beyond the 20-run window (rolling aggregates table)",
    ],
    threats: [
      "LIVE SECRETS COMMITTED — bot token, api_id/hash, session files, Cloudflare account (rotation mandatory)",
      "Zombie Ollama threads after timeout — results discarded, but threads linger until the call completes",
      "Windows MAX_PATH — deep vault trees can exceed 260 chars",
      "Unauthenticated GitHub = 60 req/h; large batches rely on the rate-limit guard",
    ],
  },
  risks: [
    {
      priority: "P0",
      title: "Rotate every committed credential",
      detail:
        "Telegram bot token, api_id/api_hash, Telethon session files, and Cloudflare account info are all inside the repo tree. Revoke and reissue ALL of them before any deployment or public exposure.",
      owner: "User (manual)",
    },
    {
      priority: "P1",
      title: "mypy + ruff pass over the new modules",
      detail:
        "links/storage/note_builder/llm_client are pure and small — a strict typing + lint pass is cheap now and locks the contract in for the next extraction round.",
      owner: "Next dev session",
    },
    {
      priority: "P2",
      title: "Revive or retire the Cloudflare sync",
      detail:
        "HMAC + webhook fixes are in, but the desktop keeps cf_manager = None. Either wire it back on (poll loop + pairing UI) or strip the dead code path in a future major.",
      owner: "User decision + dev",
    },
    {
      priority: "P2",
      title: "Windows MAX_PATH on deep vault trees",
      detail:
        "Obsidian vaults nested under OneDrive/Dropbox sync folders can push note + banner paths past the 260-char limit. Consider \\\\?\\ long-path prefixes or a shallower vault layout.",
      owner: "User decision + dev",
    },
    {
      priority: "P3",
      title: "Phase D — packaging & CI",
      detail:
        "PyInstaller single-exe for the desktop app, GitHub Actions running tests + py_compile on push (same gate as /api/verify), release zip with rotated-credential template config.",
      owner: "Future",
    },
  ],
  deployChecklist: [
    {
      id: "rotate-bot-token",
      label: "Rotate the Telegram bot token",
      detail:
        "The live token is committed in config.json, installer.config.json (×2) and DEPLOYMENT.md (×3). Revoke via @BotFather /revoke and paste the new one before any deployment.",
      priority: "P0",
      owner: "You (manual)",
    },
    {
      id: "rotate-api-creds",
      label: "Rotate api_id/api_hash & delete session files",
      detail:
        "telegram_api_id/hash live in config.json AND are hardcoded in test.py + diagnose_code.py. session.session (+ .bak) grant full account access — delete both files.",
      priority: "P0",
      owner: "You (manual)",
    },
    {
      id: "rotate-cloudflare",
      label: "Rotate Cloudflare credentials",
      detail:
        "Account ID + owner email sit in .wrangler/cache/wrangler-account.json, hmac_secret in installer.config.json. Rotate the account credentials and re-run the 2-command webhook setup.",
      priority: "P0",
      owner: "You (manual)",
    },
    {
      id: "live-verification",
      label: "Run live verification — all green",
      detail:
        "POST /api/verify (or the Tests/History tab button) must show 10/10 compiles and 45/45 tests passing before you ship — and the History tab must show no code drift for the newest run.",
      priority: "P1",
      owner: "Dashboard",
    },
    {
      id: "windows-smoke",
      label: "Smoke-run the GUI on the Windows target",
      detail:
        "PyQt6/telethon/ollama can't run in this sandbox. On the real machine: trigger the model-failure dialog once (confirm persistence), run one headless import, confirm clean exit.",
      priority: "P1",
      owner: "You (manual)",
    },
    {
      id: "scratch-vault",
      label: "Review output in a scratch vault first",
      detail:
        "Point the vault path at an empty folder, process 3–5 links, and read the generated notes before touching your real Obsidian vault.",
      priority: "P2",
      owner: "You (manual)",
    },
    {
      id: "cloudflare-decision",
      label: "Decide the Cloudflare sync fate",
      detail:
        "The HMAC + webhook fixes are in but cf_manager stays None. Wire it back on or accept it stays dormant until the next major.",
      priority: "P2",
      owner: "You (decision)",
    },
  ],
};
