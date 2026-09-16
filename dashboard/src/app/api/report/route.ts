import { NextResponse } from "next/server";
import { releaseReport } from "@/lib/report";

/**
 * GET /api/report — the v30 release & audit report consumed by the dashboard.
 *
 * Serves the typed report data from src/lib/report.ts with no-store caching
 * so freshly-deployed fixes are always reflected immediately.
 */
export async function GET() {
  return NextResponse.json(releaseReport, {
    status: 200,
    headers: {
      "Cache-Control": "no-store",
    },
  });
}
