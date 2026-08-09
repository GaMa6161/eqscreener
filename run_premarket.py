#!/usr/bin/env python3
"""Convenience wrapper for the pre-market job - run it manually on your PC.

Prefer the CLI:  nifty-scanner run-news
Examples:
    python run_premarket.py --demo --dry-run   # offline synthetic data, no email
    python run_premarket.py --dry-run          # live data, build page, skip email
    python run_premarket.py                    # live data + email
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "src"))

from nifty_scanner.cli import main  # noqa: E402


def _run() -> None:
    ap = argparse.ArgumentParser(description="Pre-market news + cues brief")
    ap.add_argument("--demo", action="store_true", help="offline synthetic data")
    ap.add_argument("--dry-run", action="store_true", help="build page, skip email")
    a = ap.parse_args()
    argv = ["run-news"]
    if a.demo:
        argv.append("--demo")
    if a.dry_run:
        argv.append("--no-email")
    main(argv)


if __name__ == "__main__":
    _run()
