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
 * in-flight coalescing, same Prisma persistence) — triggered runs are
 * indistinguishable from manual ones in the History tab.
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

  const runScheduled = async (label: string) => {
    try {
      const { POST } = await import("./app/api/verify/route");
      const res = await POST();
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
