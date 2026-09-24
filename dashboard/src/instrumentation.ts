/**
 * instrumentation.ts — v30.4 scheduled verification.
 *
 * Next.js calls register() once when the server process boots. We use it to
 * self-schedule the live verification gate:
 *   - first pass: 60s after startup (populates history + refreshes the code
 *     fingerprint after every server restart)
 *   - then: every 6 hours
 *
 * Each pass calls the /api/verify POST handler directly (same module, same
 * in-flight coalescing, same Prisma persistence). Since v0.0.4 the passes
 * identify themselves via the request body — "startup" for the first pass,
 * "scheduled" for the 6-hour intervals — so the History tab can tell
 * machine-triggered runs from manual ones.
 *
 * Guards: nodejs runtime only (never the edge compiler) + a globalThis flag
 * so hot-reloads can't stack duplicate schedulers.
 */

export async function register() {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;

  const g = globalThis as { __curatorScheduledVerify?: boolean };
  if (g.__curatorScheduledVerify) return;
  g.__curatorScheduledVerify = true;

  const FIRST_DELAY_MS = 60_000;
  const INTERVAL_MS = 6 * 60 * 60 * 1000; // 6 hours

  const runScheduled = async (label: "startup" | "scheduled") => {
    try {
      const { POST } = await import("./app/api/verify/route");
      // v0.0.4 — the body tags the persisted run with its provenance.
      const req = new Request("http://internal/api/verify", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ source: label }),
      });
      const res = await POST(req);
      const data = (await res.json()) as {
        ok?: boolean;
        summaryLine?: string;
        error?: string;
      };
      if (data.error) {
        console.error(
          `[scheduled-verify:${label}] failed: ${data.error}`,
        );
      } else {
        console.log(
          `[scheduled-verify:${label}] ${data.summaryLine ?? "ran"} — ok=${data.ok}`,
        );
      }
    } catch (e) {
      console.error(`[scheduled-verify:${label}] crashed:`, e);
    }
  };

  setTimeout(() => {
    void runScheduled("startup");
    setInterval(() => {
      void runScheduled("interval");
    }, INTERVAL_MS);
  }, FIRST_DELAY_MS);

  console.log(
    `[scheduled-verify] armed: first pass in ${FIRST_DELAY_MS / 1000}s, then every ${INTERVAL_MS / 3_600_000}h`,
  );
}
