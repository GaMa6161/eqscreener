#!/usr/bin/env python3
"""Convenience wrapper for the EOD job - run it manually on your PC.

Prefer the CLI:  nifty-scanner run-eod
Examples:
    python run_eod.py --demo --dry-run   # offline synthetic data, no email
    python run_eod.py --dry-run          # live data, build dashboard, skip email
    python run_eod.py                    # live data + email (auto-backfills on first run)
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "src"))

from nifty_scanner.cli import main  # noqa: E402


def _run() -> None:
    ap = argparse.ArgumentParser(description="EOD swing digest")
    ap.add_argument("--demo", action="store_true", help="offline synthetic data")
    ap.add_argument("--dry-run", action="store_true", help="build dashboard, skip email")
    a = ap.parse_args()
    argv = ["run-eod"]
    if a.demo:
        argv.append("--demo")
    if a.dry_run:
        argv.append("--no-email")
    main(argv)


if __name__ == "__main__":
    _run()
