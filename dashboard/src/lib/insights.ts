/**
 * insights.ts — v30.4 Timing Insights & drift detail.
 *
 * PURE TypeScript (no node: imports) so the dashboard can import it
 * type-only without anything server-side reaching the client bundle.
 *
 * Two jobs:
 *  1. buildTimingInsights() — per-case timing leaderboard computed from the
 *     resultJson snapshots persisted with every VerificationRun:
 *     median/latest wall-clock per test, trend samples, regression and
 *     flakiness flags.
 *  2. diffFileHashes() — which of the 9 audited files changed between the
 *     last run's per-file hashes and the live tree.
 */

import type { VerifyResult } from "@/lib/verify-core";

/* ------------------------------------------------------------------ */
/* Types                                                               */
/* ------------------------------------------------------------------ */

/** One per-case timing observation from a single run (oldest → newest). */
export interface TimingSample {
  ranAt: string;
  ms: number;
  ok: boolean;
}

/** A leaderboard row — one test case aggregated across the window. */
export interface TimingInsight {
  /** full id, e.g. "tests.test_core.TestLinks.test_is_github_url" */
  id: string;
  /** bare test name, e.g. "test_is_github_url" */
  name: string;
  /** suite key, e.g. "tests.test_core.TestLinks" */
  suite: string;
  latestMs: number;
  medianMs: number;
  /** latest-vs-median delta in percent (null when only one sample) */
  deltaPct: number | null;
  /** oldest → newest, capped at TREND_SAMPLES */
  samples: TimingSample[];
  /** true when latest > REGRESSION_FACTOR × median AND above the noise floor */
  regressed: boolean;
  /** true when any sample in the window is FAIL/ERROR */
  flaky: boolean;
}

export interface TimingInsights {
  /** top N by medianMs, descending */
  leaderboard: TimingInsight[];
  /** how many runs the window covers */
  windowRuns: number;
  /** distinct cases seen in the window */
  caseCount: number;
}

/** Result of comparing the last run's per-file hashes to the live tree. */
export interface FileHashDiff {
  changed: string[];
  added: string[];
  removed: string[];
}

/* ------------------------------------------------------------------ */
/* Tuning                                                              */
/* ------------------------------------------------------------------ */

/** runs per window (parsed from resultJson, newest first) */
export const INSIGHT_WINDOW_RUNS = 20;
/** samples kept per case for the sparkline */
export const TREND_SAMPLES = 8;
/** leaderboard rows */
export const LEADERBOARD_SIZE = 12;
/** latest ms must exceed median × this factor to count as regressed */
export const REGRESSION_FACTOR = 1.5;
/** …and exceed this floor, so sub-millisecond jitter never flags */
export const REGRESSION_FLOOR_MS = 100;

/* ------------------------------------------------------------------ */
/* Implementation                                                      */
/* ------------------------------------------------------------------ */

interface RunRowInput {
  ranAt: Date;
  ok: boolean;
  resultJson: string;
}

/** Extract per-case timings from one run's resultJson snapshot. */
function extractCases(
  row: RunRowInput,
): Map<string, { ms: number; ok: boolean }> {
  const out = new Map<string, { ms: number; ok: boolean }>();
  let result: VerifyResult;
  try {
    result = JSON.parse(row.resultJson) as VerifyResult;
  } catch {
    return out; // corrupted snapshot — skip, don't kill the aggregate
  }
  for (const suite of result.suites ?? []) {
    for (const c of suite.cases ?? []) {
      if (c.ms == null) continue; // pre-v30.3 rows have no per-case timing
      out.set(`${suite.suite}.${c.name}`, {
        ms: c.ms,
        ok: c.status === "ok" || c.status === "skipped",
      });
    }
  }
  return out;
}

function median(values: number[]): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 1
    ? sorted[mid]
    : Math.round((sorted[mid - 1] + sorted[mid]) / 2);
}

/**
 * Build the timing leaderboard from run rows (newest first, as returned
 * by Prisma). Rows without per-case timings (pre-v30.3) contribute nothing.
 */
export function buildTimingInsights(rows: RunRowInput[]): TimingInsights {
  const window = rows.slice(0, INSIGHT_WINDOW_RUNS);
  // per case id → samples in chronological (oldest → newest) order
  const byCase = new Map<string, TimingSample[]>();

  for (let i = window.length - 1; i >= 0; i -= 1) {
    const row = window[i];
    const ranAt = row.ranAt.toISOString();
    for (const [id, c] of extractCases(row)) {
      let arr = byCase.get(id);
      if (!arr) {
        arr = [];
        byCase.set(id, arr);
      }
      arr.push({ ranAt, ms: c.ms, ok: c.ok });
    }
  }

  const all: TimingInsight[] = [];
  for (const [id, samples] of byCase) {
    const parts = id.split(".");
    const name = parts[parts.length - 1];
    const suite = parts.slice(0, -1).join(".");
    const latest = samples[samples.length - 1];
    const med = median(samples.map((s) => s.ms));
    const prev = samples.length >= 2 ? samples[samples.length - 2].ms : null;
    const deltaPct =
      prev != null && prev > 0
        ? Math.round(((latest.ms - prev) / prev) * 100)
        : null;

    all.push({
      id,
      name,
      suite,
      latestMs: latest.ms,
      medianMs: med,
      deltaPct,
      samples: samples.slice(-TREND_SAMPLES),
      regressed:
        samples.length >= 3 &&
        latest.ms > med * REGRESSION_FACTOR &&
        latest.ms > REGRESSION_FLOOR_MS,
      flaky: samples.some((s) => !s.ok),
    });
  }

  all.sort((a, b) => b.medianMs - a.medianMs);
  return {
    leaderboard: all.slice(0, LEADERBOARD_SIZE),
    windowRuns: window.length,
    caseCount: byCase.size,
  };
}

/**
 * Compare per-file hashes (name → hash). Used with the newest run's
 * persisted fileHashes vs. the live computeFileHashes() result.
 */
export function diffFileHashes(
  last: Record<string, string>,
  current: Record<string, string>,
): FileHashDiff {
  const changed: string[] = [];
  const added: string[] = [];
  const removed: string[] = [];
  for (const file of Object.keys(current)) {
    if (!(file in last)) added.push(file);
    else if (last[file] !== current[file]) changed.push(file);
  }
  for (const file of Object.keys(last)) {
    if (!(file in current)) removed.push(file);
  }
  return { changed, added, removed };
}
