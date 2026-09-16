import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  /* The sandbox preview is served through a *.space-z.ai proxy origin —
     allow it so Next 16 dev assets don't warn / get blocked. */
  allowedDevOrigins: ["*.space-z.ai"],
  typescript: {
    ignoreBuildErrors: true,
  },
  reactStrictMode: false,
  /* v30.4 — src/instrumentation.ts (stable in Next 16) arms the scheduled
     verification gate: first pass 60s after boot, then every 6h. */
};

export default nextConfig;
