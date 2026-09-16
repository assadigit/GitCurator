import { execFile } from "child_process";
import { promisify } from "util";
import { NextResponse } from "next/server";
import { db } from "@/lib/db";
import {
  APP_DIR,
  PY_FILES,
  TEST_MODULES,
  computeCodeHash,
  computeFileHashes,
  type CaseStatus,
  type SuiteResult,
  type TestCaseResult,
  type VerifyResult,
} from "@/lib/verify-core";

/**
 * /api/verify — LIVE verification runner for the GitHub Curator core.
 *
 * POST: executes the real Python verification pipeline against
 * `audit/Github V6.7` and returns structured results:
 *   1. `python3 -m py_compile` per touched .py file (syntax gate)
 *   2. `python3 -m unittest <module> -v` per test module — each module
 *      runs in its own interpreter so we get per-suite wall-clock timing
 *      (v30.2), parsed per-suite and per-case
 *   3. sha256 fingerprint of the 9 audited files (v30.2 code drift)
 *
 * Every run is persisted to SQLite via Prisma (VerificationRun) for the
 * History tab — trends, pass-rate stats and drift detection.
 *
 * v0.0.4 provenance: POST accepts an optional JSON body
 * `{ "source": "manual" | "startup" | "scheduled" }` — anything else
 * (or no body at all) falls back to "manual". The scheduled passes from
 * src/instrumentation.ts identify themselves; in-flight runs coalesce, so
 * the source of whoever STARTED the run is the one recorded.
 *
 * Security: NO user input reaches a shell — commands and paths are fixed
 * constants; `source` is matched against a closed whitelist before it
 * touches the database. GET returns the last cached result without re-running.
 *
 * Concurrency: parallel POSTs coalesce onto a single in-flight run.
 */

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const exec = promisify(execFile);

const COMPILE_TIMEOUT_MS = 15_000;
const TEST_TIMEOUT_MS = 120_000;

/** Closed whitelist for the run-provenance column (v0.0.4). */
const RUN_SOURCES = ["manual", "startup", "scheduled"] as const;
type RunSource = (typeof RUN_SOURCES)[number];

/** Extract + validate the provenance hint from the request body (if any). */
async function readRunSource(req: Request): Promise<RunSource> {
  try {
    const body = (await req.json()) as { source?: unknown };
    return RUN_SOURCES.includes(body?.source as RunSource)
      ? (body.source as RunSource)
      : "manual";
  } catch {
    return "manual"; // no body / not JSON / malformed — the default
  }
}

/* ------------------------------------------------------------------ */
/* Parsing                                                             */
/* ------------------------------------------------------------------ */

const CASE_RE = /^(\S+) \(([^)]+)\) \.\.\. (ok|FAIL|ERROR|skipped)\s*$/gm;
const RAN_RE = /^Ran (\d+) tests? in ([\d.]+)s?\s*$/m;

interface ParsedModule {
  suites: SuiteResult[];
  testsTotal: number;
  testsPassed: number;
  ranSeconds: number | null;
  okLine: boolean;
}

interface TimingCaseJson {
  id: string;
  status: CaseStatus;
  ms?: number;
}

/** Parse the JSON payload printed by tests/timing_runner.py. */
function parseTimingJson(jsonText: string, modMs: number): ParsedModule {
  const payload = JSON.parse(jsonText) as {
    ok: boolean;
    ran: number;
    cases: TimingCaseJson[];
  };

  const bySuite = new Map<string, SuiteResult>();
  let testsPassed = 0;

  for (const c of payload.cases) {
    // id looks like "tests.test_core.TestLinks.test_x"
    const parts = c.id.split(".");
    const suiteKey = parts.length >= 2 ? `${parts[0]}.${parts[1]}` : c.id;
    const name = parts[parts.length - 1];

    let entry = bySuite.get(suiteKey);
    if (!entry) {
      entry = {
        suite: suiteKey, total: 0, passed: 0, failed: 0, errors: 0,
        skipped: 0, durationMs: modMs, cases: [],
      };
      bySuite.set(suiteKey, entry);
    }
    entry.total += 1;
    const caseResult: TestCaseResult = { name, status: c.status };
    if (c.ms != null) caseResult.ms = c.ms;
    entry.cases.push(caseResult);
    switch (c.status) {
      case "ok":
        entry.passed += 1;
        testsPassed += 1;
        break;
      case "FAIL":
        entry.failed += 1;
        break;
      case "ERROR":
        entry.errors += 1;
        break;
      case "skipped":
        entry.skipped += 1;
        testsPassed += 1; // skipped ≠ broken
        break;
    }
  }

  return {
    suites: [...bySuite.values()],
    testsTotal: payload.ran,
    testsPassed,
    ranSeconds: modMs / 1000,
    okLine: payload.ok,
  };
}

function parseUnitTestVerbose(out: string): ParsedModule {
  const bySuite = new Map<string, SuiteResult>();
  let m: RegExpExecArray | null;
  let testsTotal = 0;
  let testsPassed = 0;

  while ((m = CASE_RE.exec(out)) !== null) {
    const [, name, fullId, rawStatus] = m;
    const status = rawStatus as ParsedModule["suites"][number]["cases"][number]["status"];
    const parts = fullId.split(".");
    const suiteKey = parts.length >= 2 ? `${parts[0]}.${parts[1]}` : fullId;

    let entry = bySuite.get(suiteKey);
    if (!entry) {
      entry = {
        suite: suiteKey, total: 0, passed: 0, failed: 0, errors: 0,
        skipped: 0, cases: [],
      };
      bySuite.set(suiteKey, entry);
    }
    entry.total += 1;
    entry.cases.push({ name, status });
    testsTotal += 1;
    switch (status) {
      case "ok":
        entry.passed += 1;
        testsPassed += 1;
        break;
      case "FAIL":
        entry.failed += 1;
        break;
      case "ERROR":
        entry.errors += 1;
        break;
      case "skipped":
        entry.skipped += 1;
        testsPassed += 1; // skipped ≠ broken
        break;
    }
  }

  const ran = out.match(RAN_RE);
  return {
    suites: [...bySuite.values()],
    testsTotal,
    testsPassed,
    ranSeconds: ran ? Number(ran[2]) : null,
    okLine: /^\bOK\b/m.test(out),
  };
}

/* ------------------------------------------------------------------ */
/* Runner                                                              */
/* ------------------------------------------------------------------ */

async function runVerification(): Promise<VerifyResult> {
  const t0 = Date.now();

  let pythonVersion = "unknown";
  try {
    const { stdout } = await exec("python3", ["--version"], { timeout: 10_000 });
    pythonVersion = stdout.trim();
  } catch {
    pythonVersion = "unavailable";
  }

  // 0 — code fingerprint (drift detection) + per-file hashes (v30.4)
  const [codeHash, fileHashes] = await Promise.all([
    computeCodeHash(),
    computeFileHashes(),
  ]);

  // 1 — per-file compile gate
  const compile: VerifyResult["compile"] = [];
  for (const file of PY_FILES) {
    const s = Date.now();
    try {
      await exec("python3", ["-m", "py_compile", file], {
        cwd: APP_DIR,
        timeout: COMPILE_TIMEOUT_MS,
      });
      compile.push({ file, ok: true, ms: Date.now() - s });
    } catch (e) {
      const err = e as { stderr?: string; message?: string };
      compile.push({
        file,
        ok: false,
        ms: Date.now() - s,
        error: (err.stderr || err.message || "compile failed").slice(0, 600),
      });
    }
  }

  // 2 — one interpreter per test module → per-suite wall-clock timing +
  //     per-case durations via tests/timing_runner.py (structured JSON,
  //     regex fallback for verbose unittest output if the runner is missing).
  const suites: SuiteResult[] = [];
  let testsTotal = 0;
  let testsPassed = 0;
  let testsExitOk = true;
  let ranSecondsSum = 0;
  let anyParsed = false;

  for (const mod of TEST_MODULES) {
    const s = Date.now();
    let stdout = "";
    let stderr = "";
    try {
      const r = await exec(
        "python3",
        ["tests/timing_runner.py", mod],
        { cwd: APP_DIR, timeout: TEST_TIMEOUT_MS, maxBuffer: 16 * 1024 * 1024 },
      );
      stdout = r.stdout;
      stderr = r.stderr;
    } catch (e) {
      // Non-zero exit (failures) or timeout — the runner prints JSON to
      // stdout and verbose output to stderr, both attached to the error.
      const err = e as { stdout?: string; stderr?: string; killed?: boolean };
      stdout = err.stdout ?? "";
      stderr = err.stderr ?? "";
      testsExitOk = false;
      if (err.killed) {
        stderr += `\n--- verify runner: ${mod} timed out ---`;
      }
    }
    const modMs = Date.now() - s;

    // Prefer the structured JSON payload (per-case status + ms) …
    const sentinel = "###TIMING_JSON###";
    const sIdx = stdout.indexOf(sentinel);
    let parsed: ParsedModule | null = null;
    if (sIdx !== -1) {
      const lineEnd = stdout.indexOf("\n", sIdx);
      const jsonText = stdout.slice(
        sIdx + sentinel.length,
        lineEnd === -1 ? undefined : lineEnd,
      );
      try {
        parsed = parseTimingJson(jsonText, modMs);
      } catch {
        parsed = null; // fall through to regex parsing
      }
    }
    // … fall back to regex-parsing the verbose unittest output.
    if (!parsed) {
      parsed = parseUnitTestVerbose(`${stdout}\n${stderr}`);
      for (const suite of parsed.suites) suite.durationMs = modMs;
    }

    suites.push(...parsed.suites);
    testsTotal += parsed.testsTotal;
    testsPassed += parsed.testsPassed;
    if (parsed.ranSeconds != null) ranSecondsSum += parsed.ranSeconds;
    if (parsed.testsTotal > 0) anyParsed = true;
  }

  const compileOk = compile.every((c) => c.ok);
  const allGreen = compileOk && testsExitOk && testsPassed === testsTotal;
  const summaryLine = anyParsed
    ? `Ran ${testsTotal} tests in ${ranSecondsSum.toFixed(2)}s — ${allGreen ? "OK" : "FAILED"}`
    : "unittest output could not be parsed";

  return {
    ranAt: new Date().toISOString(),
    pythonVersion,
    durationMs: Date.now() - t0,
    ok: allGreen,
    compile,
    suites,
    testsTotal,
    testsPassed,
    summaryLine,
    codeHash,
    fileHashes,
  };
}

/* ------------------------------------------------------------------ */
/* Persistence                                                         */
/* ------------------------------------------------------------------ */

/** Keep the newest N runs — older rows are pruned after every insert. */
const RETAIN_RUNS = 200;

async function persistRun(result: VerifyResult, source: RunSource): Promise<void> {
  try {
    await db.verificationRun.create({
      data: {
        ok: result.ok,
        durationMs: result.durationMs,
        pythonVersion: result.pythonVersion,
        testsTotal: result.testsTotal,
        testsPassed: result.testsPassed,
        compileOk: result.compile.filter((c) => c.ok).length,
        compileTotal: result.compile.length,
        summaryLine: result.summaryLine,
        codeHash: result.codeHash,
        resultJson: JSON.stringify(result),
        source,
      },
    });

    // Retention: keep at most RETAIN_RUNS newest rows.
    const count = await db.verificationRun.count();
    if (count > RETAIN_RUNS) {
      const cutoff = await db.verificationRun.findFirst({
        orderBy: [{ ranAt: "desc" }, { id: "desc" }],
        skip: RETAIN_RUNS - 1,
        select: { ranAt: true },
      });
      if (cutoff) {
        await db.verificationRun.deleteMany({
          where: { ranAt: { lt: cutoff.ranAt } },
        });
      }
    }
  } catch (e) {
    // History persistence is best-effort — the live gate must still return.
    console.error("[api/verify] failed to persist run:", e);
  }
}

/* ------------------------------------------------------------------ */
/* Route state                                                         */
/* ------------------------------------------------------------------ */

let inFlight: Promise<VerifyResult> | null = null;
let lastResult: VerifyResult | null = null;

export async function POST(req: Request) {
  // v0.0.4 — provenance is read BEFORE the coalescing check so a body is
  // always consumed exactly once, even when joining an in-flight run.
  const source = await readRunSource(req);
  if (!inFlight) {
    inFlight = runVerification()
      .then(async (result) => {
        lastResult = result;
        await persistRun(result, source);
        return result;
      })
      .finally(() => {
        inFlight = null;
      });
  }
  try {
    const result = await inFlight;
    return NextResponse.json(result, {
      status: 200,
      headers: { "Cache-Control": "no-store" },
    });
  } catch (e) {
    const message = e instanceof Error ? e.message : "verification crashed";
    return NextResponse.json(
      { error: message },
      { status: 500, headers: { "Cache-Control": "no-store" } },
    );
  }
}

export async function GET() {
  let last: VerifyResult | null = lastResult;
  if (!last) {
    // Dev hot-reload or cold start — restore from the persisted history.
    try {
      const row = await db.verificationRun.findFirst({
        orderBy: { ranAt: "desc" },
      });
      if (row) last = JSON.parse(row.resultJson) as VerifyResult;
    } catch {
      /* history unavailable — report as unverified */
    }
  }
  return NextResponse.json(
    { last },
    { status: 200, headers: { "Cache-Control": "no-store" } },
  );
}
