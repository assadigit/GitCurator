#!/usr/bin/env bash
# ============================================================
#  gitcurator-cli.sh — Unix launcher for the GitCurator visual CLI
#
#  ./gitcurator-cli.sh            → fully automatic run (--auto --yes)
#  ./gitcurator-cli.sh --init     → first-run wizard
#  ./gitcurator-cli.sh --status   → config + cache + strike summary
#  ./gitcurator-cli.sh ...        → any flag is forwarded to the CLI
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "[GitCurator] python3 not found — install Python 3.10+ first." >&2
    exit 1
fi

if [ "$#" -eq 0 ]; then
    exec python3 main.py --cli --auto --yes
fi

exec python3 main.py --cli "$@"
