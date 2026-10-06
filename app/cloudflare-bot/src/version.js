// ========================================
// version.js — the deployed Worker's version (single source of truth)
// ========================================
// Keep in lockstep at release time:
//   - the repo's VERSION file (the app release)
//   - gitcurator/constants.py EXPECTED_WORKER_VERSION (what Test
//     Connection compares /health against)
//   - package.json "version" here
// The app reads this from GET /health -> {"version": ...} and warns
// when the deployed Worker is older than the app expects.

export const WORKER_VERSION = '0.30.0';
