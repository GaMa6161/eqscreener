#!/usr/bin/env bash
# Run the Nifty Eq Scanner manually on your PC and email the digest.
#
# Usage:
#   ./scripts/run_local.sh            # EOD swing digest (default)
#   ./scripts/run_local.sh eod        # EOD swing digest
#   ./scripts/run_local.sh news       # pre-market news + global cues
#   ./scripts/run_local.sh both       # run both
#
# First EOD run auto-downloads ~9 months of history (a few minutes); later runs
# just append the newest day. Configure SMTP in .env to receive the email
# (without it, the dashboard is still written to output/site/index.html).
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -d .venv ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

job="${1:-eod}"
case "$job" in
  eod)  python -m nifty_scanner.cli run-eod ;;
  news) python -m nifty_scanner.cli run-news ;;
  both) python -m nifty_scanner.cli run-eod && python -m nifty_scanner.cli run-news ;;
  *) echo "usage: $0 [eod|news|both]"; exit 1 ;;
esac
