#!/usr/bin/env bash
# Run the Nifty Eq Scanner manually on your PC and email the digest.
#
# Usage:
#   ./scripts/run_local.sh            # full EOD digest (default)
#   ./scripts/run_local.sh scan       # activity + momentum + option strikes
#   ./scripts/run_local.sh live       # Dhan live (needs DHAN_* in .env)
#   ./scripts/run_local.sh eod        # full EOD digest
#   ./scripts/run_local.sh news       # pre-market news + global cues
#   ./scripts/run_local.sh both       # eod + news
#   ./scripts/run_local.sh demo       # offline demo dashboard
#   ./scripts/run_local.sh serve      # local dashboard server
#
# First EOD/scan run auto-downloads ~9 months of history (a few minutes); later
# runs just append the newest day. Configure SMTP in .env to receive email
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
  scan) python -m nifty_scanner.cli run-scan --no-email ;;
  live) python -m nifty_scanner.cli run-live ;;
  demo) python -m nifty_scanner.cli run-scan --demo --no-email
        python -m nifty_scanner.cli serve ;;
  news) python -m nifty_scanner.cli run-news ;;
  both) python -m nifty_scanner.cli run-eod && python -m nifty_scanner.cli run-news ;;
  serve) python -m nifty_scanner.cli serve ;;
  *) echo "usage: $0 [eod|scan|live|demo|news|both|serve]"; exit 1 ;;
esac
