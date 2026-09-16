import { createHash } from "crypto";
import { readFile } from "fs/promises";
import path from "path";

/**
 * verify-core.ts — shared constants, types and the code fingerprint used by
 * /api/verify and /api/history (v30.2).
 *
 * IMPORTANT: this module imports node:fs / node:crypto — it must ONLY be
 * imported from server routes. The dashboard imports the TYPES via
 * `import type { … } from "@/lib/verify-core"` which is erased at compile
 * time, so nothing node-side ever reaches the client bundle.
 */

/**
 * Location of the Python application inside this repository.
 *
 * The dashboard normally runs from `dashboard/` (`bun run dev`), so the app
 * tree is the sibling `../app` directory. GITCURATOR_APP_DIR overrides this
 * for non-standard layouts (absolute or relative path).
 */
export const APP_DIR = process.env.GITCURATOR_APP_DIR ?? path.join(process.cwd(), "..", "app");

/** The Python files touched by the v30/v30.1 releases (compile gate). */
export const PY_FILES = [
  "main.py",
  "links.py",
  "storage.py",
  "note_builder.py",
  "llm_client.py",
  "telegram_fetch_worker.py",
  "telethon_fetcher.py",
  "backfill_manager.py",
  "cloudflare_sync.py",
] as const;

export const TEST_MODULES = ["tests.test_core", "tests.test_e2e"] as const;

/* ------------------------------------------------------------------ */
/* Types (shared shape with the dashboard)                             */
/* ------------------------------------------------------------------ */

export interface CompileFileResult {
  file: string;
  ok: boolean;
  ms: number;
  error?: string;
}

export type CaseStatus = "ok" | "FAIL" | "ERROR" | "skipped";

export interface TestCaseResult {
  name: string;
  status: CaseStatus;
  /** v30.3: per-case wall-clock duration from tests/timing_runner.py */
  ms?: number;
}

export interface SuiteResult {
  suite: string;
  total: number;
  passed: number;
  failed: number;
  errors: number;
  skipped: number;
  /** v30.2: wall-clock duration of this module's unittest run. */
  durationMs?: number;
  cases: TestCaseResult[];
}

export interface VerifyResult {
  ranAt: string;
  pythonVersion: string;
  durationMs: number;
  ok: boolean;
  compile: CompileFileResult[];
  suites: SuiteResult[];
  testsTotal: number;
  testsPassed: number;
  summaryLine: string;
  /** v30.2: sha256 fingerprint of the audited code at run time. */
  codeHash: string;
  /** v30.4: per-file sha256 map — lets /api/history name WHICH files drifted. */
  fileHashes?: Record<string, string>;
}

/* ------------------------------------------------------------------ */
/* Code fingerprint — "which version of the code was verified?"        */
/* ------------------------------------------------------------------ */

/**
 * Deterministic sha256 over the 9 audited .py files (name + content).
 * Stored with every run so the History tab can flag code drift:
 * "the working tree changed since the last green verification".
 */
export async function computeCodeHash(): Promise<string> {
  const h = createHash("sha256");
  for (const file of PY_FILES) {
    h.update(file);
    h.update("\0");
    try {
      h.update(await readFile(path.join(APP_DIR, file)));
    } catch {
      h.update("<missing>");
    }
    h.update("\0");
  }
  return h.digest("hex");
}

/**
 * v30.4 — per-file sha256 map (same input set as computeCodeHash).
 * Persisted inside resultJson; compared against the live tree so the
 * History tab can list exactly which files drifted since the last run.
 * A missing file hashes to "<missing>" (mirrors computeCodeHash).
 */
export async function computeFileHashes(): Promise<Record<string, string>> {
  const out: Record<string, string> = {};
  for (const file of PY_FILES) {
    const h = createHash("sha256");
    try {
      h.update(await readFile(path.join(APP_DIR, file)));
      out[file] = h.digest("hex").slice(0, 16);
    } catch {
      out[file] = "<missing>";
    }
  }
  return out;
}
