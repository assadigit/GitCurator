import { NextResponse } from "next/server";
import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { readFile, readdir, stat } from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";
import { db } from "@/lib/db";

/**
 * /api/vault-seal — VaultSeal status & history (v0.0.5).
 *
 * VaultSeal is GitCurator's automatic vault backup: after every curation
 * run the whole Obsidian vault is committed and pushed to a PRIVATE
 * GitHub repository (app/vaultseal.py — pure stdlib, token never
 * persisted, skip-when-unchanged).
 *
 *   GET                      → vault git status + app config + events + stats
 *   POST  { mode: "seal-now" }  → run the REAL app/vaultseal.py against the
 *                                 local vault (local commit — the dashboard
 *                                 holds no GitHub token by design; pushing
 *                                 is the app's job) and persist the result
 *   POST  { mode: "simulate" }  → record a plausible fake event (try the
 *                                 timeline without a vault)
 *   POST  { mode: "record", event } → persist a real result reported by
 *                                 the app (used by the release demo)
 *   DELETE ?id=… | (none)     → delete one event / clear the history
 *
 * Everything is best-effort: a missing vault degrades to nulls, the
 * endpoint never 500s on partial state.
 */

const execFileAsync = promisify(execFile);

/* Path resolution — portable across the sandbox + cloned-repo layouts:
 * the Python tree first (GITCURATOR_APP_DIR, else the audit sandbox dir,
 * else the sibling ../app of a cloned dashboard), then the vault itself
 * (GITCURATOR_VAULT_DIR, else <cwd>/demo-vault). */
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

/** The machine-state lines vaultseal.py manages in the vault's .gitignore. */
const HYGIENE_LINES = [
  ".obsidian/workspace.json",
  ".obsidian/workspace-mobile.json",
  ".obsidian/cache",
  ".trash/",
  "__pycache__/",
  ".DS_Store",
];

const EXEC_OPTS = { timeout: 60_000, maxBuffer: 4 * 1024 * 1024, windowsHide: true } as const;
const GIT_OPTS = { timeout: 5000, maxBuffer: 1024 * 1024, windowsHide: true } as const;

/* ------------------------------------------------------------------ */
/* Types (shared with the dashboard via type-only imports)            */
/* ------------------------------------------------------------------ */

export interface VaultSealEventRow {
  id: string;
  sealedAt: string; // ISO
  status: "sealed" | "skipped" | "failed";
  commitMessage: string;
  commitSha: string | null;
  filesChanged: number;
  pushed: boolean;
  repoName: string | null;
  repoUrl: string | null;
  repoPrivate: boolean;
  source: "app" | "demo" | "simulate";
  skippedReason: string | null;
  error: string | null;
  durationMs: number;
}

export interface VaultLastCommit {
  sha: string;
  short: string;
  subject: string;
  date: string; // ISO
}

export interface VaultStatus {
  path: string | null;
  exists: boolean;
  isGitRepo: boolean;
  hasCommits: boolean;
  dirtyCount: number | null;
  notesCount: number | null; // .md files (excluding .obsidian)
  totalFiles: number | null;
  sizeBytes: number | null;
  lastCommit: VaultLastCommit | null;
  remoteUrl: string | null;
  repoName: string | null; // "owner/repo" derived from the remote
  gitignoreOk: boolean | null; // VaultSeal hygiene lines present
}

export interface VaultSealConfig {
  enabled: boolean;
  autoPush: boolean;
  repoName: string;
}

export interface VaultSealStats {
  total: number;
  sealed: number;
  skipped: number;
  failed: number;
  lastSealedAt: string | null;
  filesSealed: number;
}

export interface VaultSealData {
  vault: VaultStatus | null;
  config: VaultSealConfig | null;
  appDir: string;
  events: VaultSealEventRow[];
  stats: VaultSealStats;
  fetchedAt: string;
}

/** The JSON shape printed by `python vaultseal.py --json`. */
interface PythonSealResult {
  sealed: boolean;
  skipped_reason: string | null;
  error: string | null;
  commit_sha: string | null;
  commit_message: string;
  files_changed: number;
  pushed: boolean;
  repo_name: string | null;
  repo_url: string | null;
  private: boolean;
  duration_ms: number;
  ok: boolean;
}

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */

async function git(args: string[]): Promise<string | null> {
  if (!VAULT_DIR) return null;
  try {
    const { stdout } = await execFileAsync("git", ["-C", VAULT_DIR, ...args], GIT_OPTS);
    return stdout.trim();
  } catch {
    return null;
  }
}

function ownerRepoFromRemote(remote: string | null): string | null {
  if (!remote) return null;
  const m = remote.match(/github\.com[/:]([^/\s]+)\/([^/\s]+?)(?:\.git)?$/i);
  return m ? `${m[1]}/${m[2]}` : null;
}

/** Walk the vault (bounded) — file/notes/size stats. */
async function vaultStats(): Promise<{ totalFiles: number; notesCount: number; sizeBytes: number }> {
  const out = { totalFiles: 0, notesCount: 0, sizeBytes: 0 };
  if (!VAULT_DIR) return out;
  const MAX = 5000;
  const queue: string[] = [VAULT_DIR];
  while (queue.length > 0 && out.totalFiles < MAX) {
    const dir = queue.shift()!;
    let names: string[];
    try {
      names = await readdir(dir);
    } catch {
      continue;
    }
    for (const name of names) {
      if (name === ".git" || name === "node_modules") continue;
      const full = path.join(dir, name);
      let st;
      try {
        st = await stat(full);
      } catch {
        continue;
      }
      if (st.isDirectory()) {
        queue.push(full);
      } else if (st.isFile()) {
        out.totalFiles += 1;
        out.sizeBytes += st.size;
        if (name.endsWith(".md") && !full.includes(".obsidian")) out.notesCount += 1;
      }
    }
  }
  return out;
}

async function readVaultStatus(): Promise<VaultStatus | null> {
  if (!VAULT_DIR) return null;
  const base: VaultStatus = {
    path: VAULT_DIR,
    exists: existsSync(VAULT_DIR),
    isGitRepo: false,
    hasCommits: false,
    dirtyCount: null,
    notesCount: null,
    totalFiles: null,
    sizeBytes: null,
    lastCommit: null,
    remoteUrl: null,
    repoName: null,
    gitignoreOk: null,
  };
  if (!base.exists) return base;

  // VaultSeal's own definition of "is a repo": the vault is its OWN repo
  // (toplevel == vault), never a parent's subdirectory.
  const toplevel = await git(["rev-parse", "--show-toplevel"]);
  base.isGitRepo = toplevel !== null && path.resolve(toplevel) === path.resolve(VAULT_DIR);

  const stats = await vaultStats();
  base.totalFiles = stats.totalFiles;
  base.notesCount = stats.notesCount;
  base.sizeBytes = stats.sizeBytes;

  // .gitignore hygiene — are the managed lines present?
  try {
    const gi = await readFile(path.join(VAULT_DIR, ".gitignore"), "utf8");
    base.gitignoreOk = HYGIENE_LINES.every((l) => gi.includes(l));
  } catch {
    base.gitignoreOk = false;
  }

  if (!base.isGitRepo) return base;

  const head = await git(["rev-parse", "--verify", "-q", "HEAD"]);
  base.hasCommits = head !== null && head !== "";
  const porcelain = await git(["status", "--porcelain"]);
  base.dirtyCount = porcelain === null ? null : porcelain.split("\n").filter(Boolean).length;
  base.remoteUrl = await git(["remote", "get-url", "origin"]);
  base.repoName = ownerRepoFromRemote(base.remoteUrl);

  if (base.hasCommits) {
    const log = await git(["log", "-1", "--format=%H%x00%h%x00%s%x00%aI"]);
    if (log) {
      const [sha, short, subject, date] = log.split("\x00");
      if (sha) base.lastCommit = { sha, short: short || sha.slice(0, 7), subject: subject || "", date: date || "" };
    }
  }
  return base;
}

/** The vaultseal section of the live app config (never returns the token). */
async function readAppConfig(): Promise<VaultSealConfig | null> {
  try {
    const raw = await readFile(path.join(APP_DIR, "config.json"), "utf8");
    const cfg = JSON.parse(raw) as Record<string, unknown>;
    const vs = (cfg.vaultseal ?? {}) as Record<string, unknown>;
    return {
      enabled: typeof vs.enabled === "boolean" ? vs.enabled : true,
      autoPush: typeof vs.auto_push === "boolean" ? vs.auto_push : true,
      repoName: typeof vs.repo_name === "string" ? vs.repo_name : "",
    };
  } catch {
    return null;
  }
}

function toRow(e: {
  id: string; sealedAt: Date; status: string; commitMessage: string; commitSha: string | null;
  filesChanged: number; pushed: boolean; repoName: string | null; repoUrl: string | null;
  repoPrivate: boolean; source: string; skippedReason: string | null; error: string | null;
  durationMs: number;
}): VaultSealEventRow {
  return {
    id: e.id,
    sealedAt: e.sealedAt.toISOString(),
    status: (["sealed", "skipped", "failed"].includes(e.status) ? e.status : "failed") as VaultSealEventRow["status"],
    commitMessage: e.commitMessage,
    commitSha: e.commitSha,
    filesChanged: e.filesChanged,
    pushed: e.pushed,
    repoName: e.repoName,
    repoUrl: e.repoUrl,
    repoPrivate: e.repoPrivate,
    source: (["app", "demo", "simulate"].includes(e.source) ? e.source : "app") as VaultSealEventRow["source"],
    skippedReason: e.skippedReason,
    error: e.error,
    durationMs: e.durationMs,
  };
}

async function readEvents(): Promise<{ events: VaultSealEventRow[]; stats: VaultSealStats }> {
  const rows = await db.vaultSealEvent.findMany({ orderBy: { sealedAt: "desc" }, take: 25 });
  const all = await db.vaultSealEvent.findMany({
    select: { status: true, filesChanged: true, sealedAt: true },
    orderBy: { sealedAt: "desc" },
    take: 500,
  });
  const stats: VaultSealStats = {
    total: all.length,
    sealed: all.filter((e) => e.status === "sealed").length,
    skipped: all.filter((e) => e.status === "skipped").length,
    failed: all.filter((e) => e.status === "failed").length,
    lastSealedAt: rows.find((e) => e.status === "sealed")?.sealedAt.toISOString() ?? null,
    filesSealed: all.reduce((acc, e) => acc + (e.status === "sealed" ? e.filesChanged : 0), 0),
  };
  return { events: rows.map(toRow), stats };
}

/* ------------------------------------------------------------------ */
/* GET                                                                 */
/* ------------------------------------------------------------------ */

export async function GET() {
  const [vault, config, { events, stats }] = await Promise.all([
    readVaultStatus(),
    readAppConfig(),
    readEvents(),
  ]);
  const data: VaultSealData = {
    vault,
    config,
    appDir: APP_DIR,
    events,
    stats,
    fetchedAt: new Date().toISOString(),
  };
  return NextResponse.json(data, { status: 200, headers: { "Cache-Control": "no-store" } });
}

/* ------------------------------------------------------------------ */
/* POST — seal-now (real) | simulate | record                          */
/* ------------------------------------------------------------------ */

interface RecordEvent {
  status: string;
  commitMessage: string;
  commitSha?: string | null;
  filesChanged?: number;
  pushed?: boolean;
  repoName?: string | null;
  repoUrl?: string | null;
  repoPrivate?: boolean;
  source?: string;
  skippedReason?: string | null;
  error?: string | null;
  durationMs?: number;
}

async function persistEvent(rec: RecordEvent) {
  const row = await db.vaultSealEvent.create({
    data: {
      status: ["sealed", "skipped", "failed"].includes(rec.status) ? rec.status : "failed",
      commitMessage: rec.commitMessage || "seal: (no message)",
      commitSha: rec.commitSha ?? null,
      filesChanged: Math.max(0, Math.round(rec.filesChanged ?? 0)),
      pushed: Boolean(rec.pushed),
      repoName: rec.repoName ?? null,
      repoUrl: rec.repoUrl ?? null,
      repoPrivate: rec.repoPrivate ?? true,
      source: ["app", "demo", "simulate"].includes(rec.source ?? "") ? (rec.source as string) : "app",
      skippedReason: rec.skippedReason ?? null,
      error: rec.error ?? null,
      durationMs: Math.max(0, Math.round(rec.durationMs ?? 0)),
    },
  });
  return toRow(row);
}

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

  /* --- seal-now: run the REAL app/vaultseal.py ----------------------- */
  if (mode === "seal-now") {
    if (!VAULT_DIR || !existsSync(VAULT_DIR)) {
      return NextResponse.json(
        { error: "No local vault detected on this machine — use Simulate to try the timeline." },
        { status: 409 },
      );
    }
    // v0.0.7: vaultseal.py moved to gitcurator/integrations/ (modular layout)
    const script = path.join(APP_DIR, "gitcurator", "integrations", "vaultseal.py");
    if (!existsSync(script)) {
      return NextResponse.json({ error: `vaultseal.py not found at ${script}` }, { status: 500 });
    }
    try {
      // No token in the dashboard by design — the seal commits locally;
      // pushing is the app's job (it holds the GitHub token).
      const { stdout } = await execFileAsync(
        process.env.PYTHON_BIN ?? "python3",
        [script, "--vault", VAULT_DIR, "--no-push", "--json"],
        EXEC_OPTS,
      );
      const result = JSON.parse(stdout) as PythonSealResult;
      const remote = await git(["remote", "get-url", "origin"]);
      const event = await persistEvent({
        status: result.sealed ? "sealed" : result.skipped_reason ? "skipped" : "failed",
        commitMessage: result.commit_message || result.skipped_reason || result.error || "seal",
        commitSha: result.commit_sha,
        filesChanged: result.files_changed,
        pushed: false, // local seal — the app pushes with its token
        repoName: ownerRepoFromRemote(remote),
        repoUrl: remote,
        repoPrivate: true,
        source: "demo",
        skippedReason: result.skipped_reason,
        error: result.error,
        durationMs: result.duration_ms,
      });
      return NextResponse.json({ event, result }, { status: 201 });
    } catch (e) {
      const msg = e instanceof Error ? e.message : "vaultseal.py failed";
      return NextResponse.json({ error: msg }, { status: 500 });
    }
  }

  /* --- simulate: a plausible fake event ------------------------------- */
  const n = 1 + Math.floor(Math.random() * 8);
  const sha = Array.from({ length: 7 }, () => "0123456789abcdef"[Math.floor(Math.random() * 16)]).join("");
  const ts = new Date().toISOString().replace("T", " ").slice(0, 16) + " UTC";
  const event = await persistEvent({
    status: "sealed",
    commitMessage: `seal: ${n} note(s) curated (${n}/${n} repos) — ${ts}`,
    commitSha: sha,
    filesChanged: n,
    pushed: true,
    repoName: "assadigit/GitCurator-Vault",
    repoUrl: "https://github.com/assadigit/GitCurator-Vault",
    repoPrivate: true,
    source: "simulate",
    durationMs: 400 + Math.floor(Math.random() * 1400),
  });
  return NextResponse.json({ event }, { status: 201 });
}

/* ------------------------------------------------------------------ */
/* DELETE — one event (?id=) or the whole history                      */
/* ------------------------------------------------------------------ */

export async function DELETE(request: Request) {
  const id = new URL(request.url).searchParams.get("id");
  if (id) {
    const deleted = await db.vaultSealEvent.deleteMany({ where: { id } });
    return NextResponse.json({ deleted: deleted.count }, { status: 200 });
  }
  const deleted = await db.vaultSealEvent.deleteMany({});
  return NextResponse.json({ deleted: deleted.count }, { status: 200 });
}
