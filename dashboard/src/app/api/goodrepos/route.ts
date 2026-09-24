import { NextResponse } from "next/server";
import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";
import { db } from "@/lib/db";

/**
 * /api/goodrepos — GoodRepos status & history (v0.0.7).
 *
 * GoodRepos is GitCurator's public curated directory: after every curation
 * run the vault's curated notes become an emoji-rich README directory
 * (organized like "AI → Skills → …") mirrored into category folders of a
 * PUBLIC GitHub repository (gitcurator/integrations/goodrepos.py — pure
 * stdlib, token never persisted, skip-when-unchanged, history-preserving
 * fetch + reset --mixed).
 *
 *   GET                          → directory scan + app config + events + stats
 *   POST  { mode: "publish-now" } → run the REAL module against the local
 *                                  vault with --no-push (the dashboard holds
 *                                  no GitHub token by design) and persist
 *   POST  { mode: "simulate" }    → record a plausible fake event
 *   POST  { mode: "record", event } → persist a real result reported by the app
 *   DELETE ?id=… | (none)        → delete one event / clear the history
 *
 * Everything is best-effort: a missing vault degrades to nulls, the
 * endpoint never 500s on partial state.
 */

const execFileAsync = promisify(execFile);

/* Path resolution — portable across the sandbox + cloned-repo layouts
 * (mirrors /api/vault-seal): the Python tree first, then the vault. */
const APP_DIR =
  process.env.GITCURATOR_APP_DIR ??
  (existsSync("/home/z/my-project/audit/Github V6.7")
    ? "/home/z/my-project/audit/Github V6.7"
    : path.join(process.cwd(), "..", "app"));

const VAULT_DIR =
  process.env.GITCURATOR_VAULT_DIR ??
  (existsSync(path.join(process.cwd(), "demo-vault"))
    ? path.join(process.cwd(), "demo-vault")
    : null);

const GOODREPOS_SCRIPT = path.join(APP_DIR, "gitcurator", "integrations", "goodrepos.py");

const EXEC_OPTS = { timeout: 60_000, maxBuffer: 4 * 1024 * 1024, windowsHide: true } as const;

/* ------------------------------------------------------------------ */
/* Types                                                               */
/* ------------------------------------------------------------------ */

export interface GoodReposEventRow {
  id: string;
  publishedAt: string; // ISO
  status: "published" | "skipped" | "failed";
  commitMessage: string;
  commitSha: string | null;
  filesChanged: number;
  pushed: boolean;
  repoName: string | null;
  repoUrl: string | null;
  repoPublic: boolean;
  entries: number;
  categories: number;
  source: "app" | "demo" | "simulate";
  skippedReason: string | null;
  error: string | null;
  durationMs: number;
}

export interface GoodReposStatus {
  vaultPath: string | null;
  exists: boolean;
  enabled: boolean;
  autoPush: boolean;
  repoName: string;
  repoUrl: string | null;
  entries: number;
  categories: number;
  tree: Record<string, number>;
  generated: string | null;
}

export interface GoodReposConfig {
  enabled: boolean;
  autoPush: boolean;
  repoName: string;
}

export interface GoodReposStats {
  total: number;
  published: number;
  skipped: number;
  failed: number;
  lastPublishedAt: string | null;
  entriesPublished: number;
}

export interface GoodReposData {
  status: GoodReposStatus | null;
  config: GoodReposConfig | null;
  appDir: string;
  events: GoodReposEventRow[];
  stats: GoodReposStats;
  fetchedAt: string;
}

/** The JSON shape printed by `python goodrepos.py --status`. */
interface PythonStatus {
  vault_path: string | null;
  exists: boolean;
  enabled: boolean;
  auto_push: boolean;
  repo_name: string;
  repo_url: string | null;
  entries: number;
  categories: number;
  tree: Record<string, number>;
  generated: string | null;
}

/** The JSON shape printed by `python goodrepos.py … --json`. */
interface PythonPublishResult {
  published: boolean;
  skipped_reason: string | null;
  error: string | null;
  commit_sha: string | null;
  commit_message: string;
  files_changed: number;
  pushed: boolean;
  repo_name: string | null;
  repo_url: string | null;
  entries: number;
  categories: number;
  duration_ms: number;
  ok: boolean;
}

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */

async function readStatus(): Promise<GoodReposStatus | null> {
  if (!VAULT_DIR || !existsSync(VAULT_DIR) || !existsSync(GOODREPOS_SCRIPT)) return null;
  try {
    const { stdout } = await execFileAsync(
      process.env.PYTHON_BIN ?? "python3",
      [GOODREPOS_SCRIPT, "--vault", VAULT_DIR, "--status"],
      EXEC_OPTS,
    );
    const s = JSON.parse(stdout) as PythonStatus;
    return {
      vaultPath: s.vault_path,
      exists: Boolean(s.exists),
      enabled: Boolean(s.enabled),
      autoPush: Boolean(s.auto_push),
      repoName: s.repo_name || "good-repos",
      repoUrl: s.repo_url ?? "https://github.com/assadigit/good-repos",
      entries: s.entries ?? 0,
      categories: s.categories ?? 0,
      tree: s.tree ?? {},
      generated: s.generated ?? null,
    };
  } catch {
    return null;
  }
}

/** The goodrepos section of the live app config (never returns the token). */
async function readAppConfig(): Promise<GoodReposConfig | null> {
  try {
    const raw = await readFile(path.join(APP_DIR, "config.json"), "utf8");
    const cfg = JSON.parse(raw) as Record<string, unknown>;
    const gr = (cfg.goodrepos ?? {}) as Record<string, unknown>;
    return {
      enabled: typeof gr.enabled === "boolean" ? gr.enabled : true,
      autoPush: typeof gr.auto_push === "boolean" ? gr.auto_push : true,
      repoName: typeof gr.repo_name === "string" ? gr.repo_name : "good-repos",
    };
  } catch {
    return null;
  }
}

function toRow(e: {
  id: string; publishedAt: Date; status: string; commitMessage: string; commitSha: string | null;
  filesChanged: number; pushed: boolean; repoName: string | null; repoUrl: string | null;
  repoPublic: boolean; entries: number; categories: number; source: string;
  skippedReason: string | null; error: string | null; durationMs: number;
}): GoodReposEventRow {
  return {
    id: e.id,
    publishedAt: e.publishedAt.toISOString(),
    status: (["published", "skipped", "failed"].includes(e.status) ? e.status : "failed") as GoodReposEventRow["status"],
    commitMessage: e.commitMessage,
    commitSha: e.commitSha,
    filesChanged: e.filesChanged,
    pushed: e.pushed,
    repoName: e.repoName,
    repoUrl: e.repoUrl,
    repoPublic: e.repoPublic,
    entries: e.entries,
    categories: e.categories,
    source: (["app", "demo", "simulate"].includes(e.source) ? e.source : "app") as GoodReposEventRow["source"],
    skippedReason: e.skippedReason,
    error: e.error,
    durationMs: e.durationMs,
  };
}

async function readEvents(): Promise<{ events: GoodReposEventRow[]; stats: GoodReposStats }> {
  const rows = await db.goodReposEvent.findMany({ orderBy: { publishedAt: "desc" }, take: 25 });
  const all = await db.goodReposEvent.findMany({
    select: { status: true, entries: true, publishedAt: true },
    orderBy: { publishedAt: "desc" },
    take: 500,
  });
  const stats: GoodReposStats = {
    total: all.length,
    published: all.filter((e) => e.status === "published").length,
    skipped: all.filter((e) => e.status === "skipped").length,
    failed: all.filter((e) => e.status === "failed").length,
    lastPublishedAt: rows.find((e) => e.status === "published")?.publishedAt.toISOString() ?? null,
    entriesPublished: all.reduce((acc, e) => acc + (e.status === "published" ? e.entries : 0), 0),
  };
  return { events: rows.map(toRow), stats };
}

interface RecordEvent {
  status: string;
  commitMessage: string;
  commitSha?: string | null;
  filesChanged?: number;
  pushed?: boolean;
  repoName?: string | null;
  repoUrl?: string | null;
  repoPublic?: boolean;
  entries?: number;
  categories?: number;
  source?: string;
  skippedReason?: string | null;
  error?: string | null;
  durationMs?: number;
}

async function persistEvent(rec: RecordEvent) {
  const row = await db.goodReposEvent.create({
    data: {
      status: ["published", "skipped", "failed"].includes(rec.status) ? rec.status : "failed",
      commitMessage: rec.commitMessage || "publish: (no message)",
      commitSha: rec.commitSha ?? null,
      filesChanged: Math.max(0, Math.round(rec.filesChanged ?? 0)),
      pushed: Boolean(rec.pushed),
      repoName: rec.repoName ?? null,
      repoUrl: rec.repoUrl ?? null,
      repoPublic: rec.repoPublic ?? true,
      entries: Math.max(0, Math.round(rec.entries ?? 0)),
      categories: Math.max(0, Math.round(rec.categories ?? 0)),
      source: ["app", "demo", "simulate"].includes(rec.source ?? "") ? (rec.source as string) : "app",
      skippedReason: rec.skippedReason ?? null,
      error: rec.error ?? null,
      durationMs: Math.max(0, Math.round(rec.durationMs ?? 0)),
    },
  });
  return toRow(row);
}

/* ------------------------------------------------------------------ */
/* GET                                                                 */
/* ------------------------------------------------------------------ */

export async function GET() {
  const [status, config, { events, stats }] = await Promise.all([
    readStatus(),
    readAppConfig(),
    readEvents(),
  ]);
  const data: GoodReposData = {
    status,
    config,
    appDir: APP_DIR,
    events,
    stats,
    fetchedAt: new Date().toISOString(),
  };
  return NextResponse.json(data, { status: 200, headers: { "Cache-Control": "no-store" } });
}

/* ------------------------------------------------------------------ */
/* POST — publish-now (real, no push) | simulate | record               */
/* ------------------------------------------------------------------ */

export async function POST(request: Request) {
  let body: { mode?: string; event?: RecordEvent } = {};
  try {
    body = (await request.json()) as { mode?: string; event?: RecordEvent };
  } catch {
    body = {};
  }
  const mode = body.mode ?? "simulate";

  /* --- record: persist a real result reported by the app -------------- */
  if (mode === "record") {
    if (!body.event || typeof body.event.commitMessage !== "string") {
      return NextResponse.json({ error: "record mode requires an event object" }, { status: 400 });
    }
    const event = await persistEvent({ source: "app", ...body.event });
    return NextResponse.json({ event }, { status: 201 });
  }

  /* --- publish-now: run the REAL module (local build, no push) -------- */
  if (mode === "publish-now") {
    if (!VAULT_DIR || !existsSync(VAULT_DIR)) {
      return NextResponse.json(
        { error: "No local vault detected on this machine — use Simulate to try the timeline." },
        { status: 409 },
      );
    }
    if (!existsSync(GOODREPOS_SCRIPT)) {
      return NextResponse.json({ error: `goodrepos.py not found at ${GOODREPOS_SCRIPT}` }, { status: 500 });
    }
    try {
      // No token in the dashboard by design — the module builds the README
      // + tree in a local staging repo and commits; pushing stays the app's
      // job (it holds the GitHub token).
      const { stdout } = await execFileAsync(
        process.env.PYTHON_BIN ?? "python3",
        [GOODREPOS_SCRIPT, "--vault", VAULT_DIR, "--no-push", "--json"],
        EXEC_OPTS,
      );
      const result = JSON.parse(stdout) as PythonPublishResult;
      const event = await persistEvent({
        status: result.published ? "published" : result.skipped_reason ? "skipped" : "failed",
        commitMessage: result.commit_message || result.skipped_reason || result.error || "publish",
        commitSha: result.commit_sha,
        filesChanged: result.files_changed,
        pushed: false, // local build — the app pushes with its token
        repoName: "assadigit/good-repos",
        repoUrl: "https://github.com/assadigit/good-repos",
        repoPublic: true,
        entries: result.entries,
        categories: result.categories,
        source: "demo",
        skippedReason: result.skipped_reason,
        error: result.error,
        durationMs: result.duration_ms,
      });
      return NextResponse.json({ event, result }, { status: 201 });
    } catch (e) {
      const msg = e instanceof Error ? e.message : "goodrepos.py failed";
      return NextResponse.json({ error: msg }, { status: 500 });
    }
  }

  /* --- simulate: a plausible fake event ------------------------------- */
  const entries = 3 + Math.floor(Math.random() * 9);
  const categories = Math.max(2, Math.ceil(entries * 0.7));
  const sha = Array.from({ length: 7 }, () => "0123456789abcdef"[Math.floor(Math.random() * 16)]).join("");
  const ts = new Date().toISOString().replace("T", " ").slice(0, 16) + " UTC";
  const event = await persistEvent({
    status: "published",
    commitMessage: `publish: ${entries} repos across ${categories} categories — ${ts}`,
    commitSha: sha,
    filesChanged: entries + 3,
    pushed: true,
    repoName: "assadigit/good-repos",
    repoUrl: "https://github.com/assadigit/good-repos",
    repoPublic: true,
    entries,
    categories,
    source: "simulate",
    durationMs: 600 + Math.floor(Math.random() * 1600),
  });
  return NextResponse.json({ event }, { status: 201 });
}

/* ------------------------------------------------------------------ */
/* DELETE — one event (?id=) or the whole history                       */
/* ------------------------------------------------------------------ */

export async function DELETE(request: Request) {
  const id = new URL(request.url).searchParams.get("id");
  if (id) {
    const deleted = await db.goodReposEvent.deleteMany({ where: { id } });
    return NextResponse.json({ deleted: deleted.count }, { status: 200 });
  }
  const deleted = await db.goodReposEvent.deleteMany({});
  return NextResponse.json({ deleted: deleted.count }, { status: 200 });
}
