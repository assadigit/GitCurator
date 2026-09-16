import { NextResponse } from "next/server";
import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { readFile, readdir, stat } from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";

/**
 * GET /api/releases — repository & release status for the Releases tab (v0.0.2).
 *
 * Reports on the GitCurator git repository (staging clone), the parsed
 * CHANGELOG.md, and the local .zip backups:
 *   - last commit, commit count, last tag, dirty-file indicator
 *   - structured releases parsed from CHANGELOG.md
 *   - backup zips found in public/ (served for download) and download/
 *
 * Everything is best-effort: a missing repo / git binary / changelog
 * degrades to nulls, the endpoint never 500s on partial state.
 *
 * Path resolution (portable across sandbox + cloned-repo layouts):
 *   GITCURATOR_REPO_DIR overrides; otherwise <cwd>/repos/GitCurator when it
 *   exists (sandbox layout), else the parent of cwd (dashboard/ inside a
 *   clone — the repo root itself).
 */

const execFileAsync = promisify(execFile);

const REPO_DIR =
  process.env.GITCURATOR_REPO_DIR ??
  (existsSync(path.join(process.cwd(), "repos", "GitCurator"))
    ? path.join(process.cwd(), "repos", "GitCurator")
    : path.join(process.cwd(), ".."));

const REPO_URL = "https://github.com/assadigit/GitCurator";

/** Directories scanned for GitCurator-vX.YY.zip backups. */
function backupDirs(): string[] {
  const dirs = [path.join(process.cwd(), "public")];
  const dl = path.join(process.cwd(), "download");
  if (existsSync(dl)) dirs.push(dl);
  const override = process.env.GITCURATOR_BACKUP_DIRS;
  if (override) dirs.push(...override.split(path.delimiter).filter(Boolean));
  return dirs;
}

const BACKUP_NAME_RE = /^GitCurator-v[\d.]+\.zip$/i;

/* ------------------------------------------------------------------ */
/* Types (shared with the dashboard via type-only imports)            */
/* ------------------------------------------------------------------ */

export interface RepoHeadCommit {
  sha: string;
  short: string;
  subject: string;
  date: string; // ISO
}

export interface RepoStatus {
  ok: boolean;
  repoDir: string;
  url: string;
  version: string;
  commitCount: number | null;
  lastTag: string | null;
  headCommit: RepoHeadCommit | null;
  dirtyCount: number | null;
  dirtyFiles: string[];
}

export interface ReleaseSection {
  heading: string;
  bullets: string[];
}

export interface ReleaseEntry {
  version: string;
  title: string;
  intro: string;
  sections: ReleaseSection[];
}

export interface BackupFile {
  name: string;
  url: string; // served URL when served=true, else informational
  sizeBytes: number;
  modifiedAt: string; // ISO
  served: boolean; // present in public/ → downloadable
}

/** v0.0.3 — newest GitHub Actions run (best-effort, needs a token for private repos). */
export interface CiRun {
  status: "queued" | "in_progress" | "completed" | "unknown";
  conclusion: string | null; // success / failure / cancelled / … (when completed)
  ok: boolean; // completed && success
  runNumber: number | null;
  name: string | null;
  htmlUrl: string | null;
  headBranch: string | null;
  headSha: string | null;
  createdAt: string | null;
  updatedAt: string | null;
}

export interface ReleasesData {
  repo: RepoStatus;
  releases: ReleaseEntry[];
  backups: BackupFile[];
  ci: CiRun | null; // null = no token / API unreachable
  fetchedAt: string;
}

/* ------------------------------------------------------------------ */
/* Git helpers (5s timeout each, best-effort)                          */
/* ------------------------------------------------------------------ */

const GIT_OPTS = { timeout: 5000, maxBuffer: 1024 * 1024, windowsHide: true } as const;

async function git(args: string[]): Promise<string | null> {
  try {
    const { stdout } = await execFileAsync("git", ["-C", REPO_DIR, ...args], GIT_OPTS);
    return stdout.trim();
  } catch {
    return null;
  }
}

/* ------------------------------------------------------------------ */
/* CHANGELOG.md parser                                                 */
/* ------------------------------------------------------------------ */

function parseChangelog(md: string): ReleaseEntry[] {
  const entries: ReleaseEntry[] = [];
  // Split into "## [version] — title" sections.
  const parts = md.split(/^## \[(\d+\.\d+\.\d+)\][^\S\n]*—[^\S\n]*(.+)$/m);
  // parts: [pre, version, title, body, version, title, body, ...]
  for (let i = 1; i + 2 < parts.length + 1; i += 3) {
    const version = parts[i];
    const title = (parts[i + 1] ?? "").trim();
    const body = parts[i + 2] ?? "";
    if (!version) continue;

    // Intro = non-heading, non-bullet prose before the first ### heading.
    const firstHeading = body.indexOf("### ");
    const introBlock = firstHeading === -1 ? body : body.slice(0, firstHeading);
    const intro = introBlock
      .split("\n")
      .map((l) => l.trim())
      .filter((l) => l && !l.startsWith("- "))
      .join(" ")
      .trim();

    // Group the remaining bullets under their ### headings. Wrapped bullet
    // lines (soft line-wraps in the markdown source) are joined onto the
    // bullet they continue.
    const sections: ReleaseSection[] = [];
    let current: ReleaseSection | null = null;
    for (const rawLine of body.split("\n")) {
      const line = rawLine.trim();
      if (line.startsWith("### ")) {
        current = { heading: line.slice(4).trim(), bullets: [] };
        sections.push(current);
      } else if (line.startsWith("- ") && current) {
        current.bullets.push(line.slice(2).trim());
      } else if (line && current && current.bullets.length > 0) {
        // Continuation of the previous (wrapped) bullet.
        current.bullets[current.bullets.length - 1] += ` ${line}`;
      }
    }
    entries.push({ version, title, intro, sections });
  }
  // Newest first.
  entries.sort((a, b) => {
    const pa = a.version.split(".").map(Number);
    const pb = b.version.split(".").map(Number);
    for (let i = 0; i < 3; i++) {
      if ((pb[i] ?? 0) !== (pa[i] ?? 0)) return (pb[i] ?? 0) - (pa[i] ?? 0);
    }
    return 0;
  });
  return entries;
}

/* ------------------------------------------------------------------ */
/* Backup scan                                                         */
/* ------------------------------------------------------------------ */

async function scanBackups(): Promise<BackupFile[]> {
  const byName = new Map<string, BackupFile>();
  for (const dir of backupDirs()) {
    let names: string[];
    try {
      names = await readdir(dir);
    } catch {
      continue;
    }
    for (const name of names) {
      if (!BACKUP_NAME_RE.test(name)) continue;
      try {
        const st = await stat(path.join(dir, name));
        if (!st.isFile()) continue;
        const existing = byName.get(name);
        const served = dir.endsWith("public");
        if (!existing) {
          byName.set(name, {
            name,
            url: `/${name}`,
            sizeBytes: st.size,
            modifiedAt: st.mtime.toISOString(),
            served,
          });
        } else if (served && !existing.served) {
          // The served copy always wins once found.
          byName.set(name, {
            name,
            url: `/${name}`,
            sizeBytes: st.size,
            modifiedAt: st.mtime.toISOString(),
            served: true,
          });
        }
      } catch {
        /* unreadable entry — skip */
      }
    }
  }
  const list = [...byName.values()];
  // Version-descending (v0.02 before v0.01), then name.
  const ver = (n: string) => n.replace(/^GitCurator-v|\.zip$/gi, "").split(".").map(Number);
  list.sort((a, b) => {
    const va = ver(a.name);
    const vb = ver(b.name);
    for (let i = 0; i < 3; i++) {
      if ((vb[i] ?? 0) !== (va[i] ?? 0)) return (vb[i] ?? 0) - (va[i] ?? 0);
    }
    return a.name.localeCompare(b.name);
  });
  return list;
}

/* ------------------------------------------------------------------ */
/* GitHub Actions CI status (v0.0.3 — optional token, best-effort)      */
/* ------------------------------------------------------------------ */

async function fetchCiStatus(): Promise<CiRun | null> {
  // The repo is private — the Actions API needs a token. Read-only,
  // optional: GITCURATOR_GH_TOKEN or GITHUB_TOKEN. No token → null and
  // the dashboard shows "CI: unknown".
  const token = process.env.GITCURATOR_GH_TOKEN ?? process.env.GITHUB_TOKEN;
  if (!token) return null;
  try {
    const r = await fetch(
      "https://api.github.com/repos/assadigit/GitCurator/actions/runs?per_page=1",
      {
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "gitcurator-dashboard",
        },
        cache: "no-store",
        signal: AbortSignal.timeout(5000),
      },
    );
    if (!r.ok) return null;
    const data = (await r.json()) as {
      workflow_runs?: Array<{
        status?: string;
        conclusion?: string | null;
        run_number?: number;
        name?: string;
        html_url?: string;
        head_branch?: string;
        head_sha?: string;
        created_at?: string;
        updated_at?: string;
      }>;
    };
    const run = data.workflow_runs?.[0];
    if (!run) {
      // Repo exists, no runs yet — a known state, not an error.
      return {
        status: "unknown",
        conclusion: null,
        ok: false,
        runNumber: null,
        name: null,
        htmlUrl: null,
        headBranch: null,
        headSha: null,
        createdAt: null,
        updatedAt: null,
      };
    }
    const status = (run.status ?? "unknown") as CiRun["status"];
    return {
      status: ["queued", "in_progress", "completed"].includes(status) ? status : "unknown",
      conclusion: run.conclusion ?? null,
      ok: status === "completed" && run.conclusion === "success",
      runNumber: run.run_number ?? null,
      name: run.name ?? null,
      htmlUrl: run.html_url ?? null,
      headBranch: run.head_branch ?? null,
      headSha: run.head_sha ?? null,
      createdAt: run.created_at ?? null,
      updatedAt: run.updated_at ?? null,
    };
  } catch {
    return null;
  }
}

/* ------------------------------------------------------------------ */
/* Handler                                                             */
/* ------------------------------------------------------------------ */

export async function GET() {
  // Git state — all best-effort.
  const [logOut, tagOut, statusOut, countOut, ci] = await Promise.all([
    git(["log", "-1", "--format=%H%x00%h%x00%s%x00%aI"]),
    git(["describe", "--tags", "--abbrev=0"]),
    git(["status", "--porcelain"]),
    git(["rev-list", "--count", "HEAD"]),
    fetchCiStatus(),
  ]);

  let headCommit: RepoHeadCommit | null = null;
  if (logOut) {
    const [sha, short, subject, date] = logOut.split("\0");
    if (sha) headCommit = { sha, short: short ?? sha.slice(0, 7), subject: subject ?? "", date: date ?? "" };
  }

  const dirtyLines = statusOut === null ? null : statusOut.split("\n").filter(Boolean);
  const dirtyFiles = (dirtyLines ?? [])
    .map((l) => l.slice(3).trim())
    .filter(Boolean)
    .slice(0, 50);

  // VERSION + CHANGELOG from the repo tree.
  let version = "";
  let releases: ReleaseEntry[] = [];
  try {
    version = (await readFile(path.join(REPO_DIR, "VERSION"), "utf8")).trim();
  } catch {
    /* no VERSION file */
  }
  try {
    releases = parseChangelog(await readFile(path.join(REPO_DIR, "CHANGELOG.md"), "utf8"));
  } catch {
    /* no changelog */
  }

  const backups = await scanBackups();

  const data: ReleasesData = {
    repo: {
      ok: headCommit !== null,
      repoDir: REPO_DIR,
      url: REPO_URL,
      version: version || (releases[0]?.version ?? ""),
      commitCount: countOut ? Number(countOut) : null,
      lastTag: tagOut ?? null,
      headCommit,
      dirtyCount: dirtyLines === null ? null : dirtyLines.length,
      dirtyFiles,
    },
    releases,
    backups,
    ci,
    fetchedAt: new Date().toISOString(),
  };

  return NextResponse.json(data, {
    status: 200,
    headers: { "Cache-Control": "no-store" },
  });
}
