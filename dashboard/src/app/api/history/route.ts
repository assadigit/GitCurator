import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { computeCodeHash, computeFileHashes } from "@/lib/verify-core";
import {
  buildTimingInsights,
  diffFileHashes,
  type TimingInsights,
} from "@/lib/insights";

/**
 * /api/history — verification-run history (v30.2) + timing insights (v30.4).
 *
 * GET    → latest 100 persisted runs + aggregate stats + LIVE code-drift
 *          check (current sha256 fingerprint vs. the last run's fingerprint)
 *          + WHICH files drifted (per-file hashes, v30.4)
 *          + per-case timing leaderboard (v30.4).
 * DELETE → ?id=<cuid> removes one run; no id clears the whole history.
 *
 * No user-controlled strings reach a shell — ids are validated and passed
 * through Prisma's parameterized queries only.
 */

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const MAX_RUNS = 100;

/* ------------------------------------------------------------------ */
/* Types (imported type-only by the dashboard)                         */
/* ------------------------------------------------------------------ */

export interface HistoryRun {
  id: string;
  ranAt: string;
  ok: boolean;
  durationMs: number;
  pythonVersion: string;
  testsTotal: number;
  testsPassed: number;
  compileOk: number;
  compileTotal: number;
  summaryLine: string;
  codeHash: string;
  /** true when the code fingerprint differs from the PREVIOUS (older) run */
  codeChanged: boolean;
}

export interface HistoryStats {
  totalRuns: number;
  okRuns: number;
  passRate: number;
  avgDurationMs: number;
  minDurationMs: number;
  maxDurationMs: number;
  /** consecutive green runs counting from the newest */
  streak: number;
  distinctCodeVersions: number;
}

export interface HistoryData {
  runs: HistoryRun[];
  stats: HistoryStats | null;
  /** live fingerprint of the audited tree, computed on request */
  currentCodeHash: string | null;
  /** current tree differs from the last verified fingerprint */
  codeDrifted: boolean;
  lastRunAt: string | null;
  /** v30.4: which audited files changed since the last run (when drifted
   *  and the run carries per-file hashes; null = not computable) */
  changedFiles: string[] | null;
  /** v30.4: per-case timing leaderboard + window metadata */
  insights: TimingInsights | null;
}

/* ------------------------------------------------------------------ */
/* GET                                                                 */
/* ------------------------------------------------------------------ */

export async function GET() {
  try {
    // Rows are newest-first; codeChanged compares each row to the one older.
    const rows = await db.verificationRun.findMany({
      orderBy: { ranAt: "desc" },
      take: MAX_RUNS,
    });

    const runs: HistoryRun[] = rows.map((row, i) => ({
      id: row.id,
      ranAt: row.ranAt.toISOString(),
      ok: row.ok,
      durationMs: row.durationMs,
      pythonVersion: row.pythonVersion,
      testsTotal: row.testsTotal,
      testsPassed: row.testsPassed,
      compileOk: row.compileOk,
      compileTotal: row.compileTotal,
      summaryLine: row.summaryLine,
      codeHash: row.codeHash,
      codeChanged: i < rows.length - 1 ? row.codeHash !== rows[i + 1].codeHash : false,
    }));

    const totalRuns = rows.length;
    let stats: HistoryStats | null = null;
    if (totalRuns > 0) {
      const okRuns = rows.filter((r) => r.ok).length;
      let streak = 0;
      for (const r of rows) {
        if (r.ok) streak += 1;
        else break;
      }
      const durations = rows.map((r) => r.durationMs);
      stats = {
        totalRuns,
        okRuns,
        passRate: Math.round((okRuns / totalRuns) * 100),
        avgDurationMs: Math.round(durations.reduce((a, b) => a + b, 0) / totalRuns),
        minDurationMs: Math.min(...durations),
        maxDurationMs: Math.max(...durations),
        streak,
        distinctCodeVersions: new Set(rows.map((r) => r.codeHash)).size,
      };
    }

    // Live drift check — computed fresh on every request.
    let currentCodeHash: string | null = null;
    try {
      currentCodeHash = await computeCodeHash();
    } catch {
      currentCodeHash = null;
    }
    const codeDrifted =
      currentCodeHash !== null &&
      runs.length > 0 &&
      runs[0].codeHash !== currentCodeHash;

    // v30.4 — name WHICH files drifted: compare the newest run's
    // persisted per-file hashes against the live tree. Only computable
    // when the newest run carries fileHashes (v30.4+ runs).
    let changedFiles: string[] | null = null;
    if (codeDrifted && rows.length > 0) {
      try {
        const lastResult = JSON.parse(rows[0].resultJson) as {
          fileHashes?: Record<string, string>;
        };
        if (lastResult.fileHashes) {
          const current = await computeFileHashes();
          const diff = diffFileHashes(lastResult.fileHashes, current);
          changedFiles = [...diff.changed, ...diff.added, ...diff.removed];
        }
      } catch {
        changedFiles = null; // snapshot unreadable — drift stays flagged
      }
    }

    // v30.4 — per-case timing leaderboard from the persisted snapshots.
    let insights: TimingInsights | null = null;
    try {
      insights = buildTimingInsights(rows);
    } catch {
      insights = null; // analytics are best-effort
    }

    const data: HistoryData = {
      runs,
      stats,
      currentCodeHash,
      codeDrifted,
      lastRunAt: runs.length > 0 ? runs[0].ranAt : null,
      changedFiles,
      insights,
    };

    return NextResponse.json(data, {
      status: 200,
      headers: { "Cache-Control": "no-store" },
    });
  } catch (e) {
    const message = e instanceof Error ? e.message : "history query failed";
    return NextResponse.json(
      { error: message },
      { status: 500, headers: { "Cache-Control": "no-store" } },
    );
  }
}

/* ------------------------------------------------------------------ */
/* DELETE                                                              */
/* ------------------------------------------------------------------ */

export async function DELETE(req: NextRequest) {
  const id = req.nextUrl.searchParams.get("id");

  if (id) {
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(id)) {
      return NextResponse.json(
        { error: "invalid id" },
        { status: 400, headers: { "Cache-Control": "no-store" } },
      );
    }
    try {
      await db.verificationRun.delete({ where: { id } });
      return NextResponse.json(
        { deleted: 1 },
        { status: 200, headers: { "Cache-Control": "no-store" } },
      );
    } catch (e) {
      // Prisma throws P2025 when the row does not exist — treat as deleted.
      const code = (e as { code?: string }).code;
      if (code === "P2025") {
        return NextResponse.json(
          { deleted: 0 },
          { status: 200, headers: { "Cache-Control": "no-store" } },
        );
      }
      const message = e instanceof Error ? e.message : "delete failed";
      return NextResponse.json(
        { error: message },
        { status: 500, headers: { "Cache-Control": "no-store" } },
      );
    }
  }

  // No id — clear the whole history.
  try {
    const r = await db.verificationRun.deleteMany({});
    return NextResponse.json(
      { deleted: r.count },
      { status: 200, headers: { "Cache-Control": "no-store" } },
    );
  } catch (e) {
    const message = e instanceof Error ? e.message : "deleteMany failed";
    return NextResponse.json(
      { error: message },
      { status: 500, headers: { "Cache-Control": "no-store" } },
    );
  }
}
