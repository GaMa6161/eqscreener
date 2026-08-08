"""Command-line entry point.

    nifty-scanner build-universe [--refresh]
    nifty-scanner backfill [--days 420]
    nifty-scanner update [--days 7]
    nifty-scanner run-eod  [--demo] [--no-email] [--no-deploy]
    nifty-scanner run-news [--demo] [--no-email] [--no-deploy]
"""
from __future__ import annotations

import argparse
import logging
from datetime import date

import pandas as pd

from . import config
from .config import EmailConfig, FtpConfig
from .data import history as hist
from .data import universe as uni
from .news import cues as cues_mod
from .news import feeds
from .report import deploy, email_send, render
from .screener import sectors, swing
from .utils import ist_now, setup_logging

log = logging.getLogger("nifty_scanner")

# Per-sector drift so offline demo runs show meaningful rotation.
DEMO_SECTOR_BIAS = {
    "IT": 0.0016, "Auto": 0.0011, "Pharma": 0.0009, "Bank": 0.0005,
    "FMCG": 0.0002, "Energy": -0.0008, "Metal": -0.0013, "Power": -0.0006,
}


def _demo_universe() -> pd.DataFrame:
    return uni._normalize(pd.read_csv(config.UNIVERSE_FALLBACK_CSV))


def _base_context() -> dict:
    return {
        "title": config.REPORT["title"],
        "date_str": date.today().strftime("%A, %d %b %Y"),
        "generated_at": ist_now().strftime("%Y-%m-%d %H:%M"),
        "disclaimer": config.REPORT["disclaimer"],
        "site_url": config.REPORT.get("site_url", ""),
        "categories": config.NEWS["categories"],
    }


# --- commands ---------------------------------------------------------------
def cmd_build_universe(args) -> None:
    df = uni.load_universe(refresh=args.refresh)
    print(f"[universe] {len(df)} constituents, {df['sector'].nunique()} sectors -> {config.UNIVERSE['cache_csv']}")


def cmd_backfill(args) -> None:
    universe = uni.load_universe(refresh=False)
    df = hist.backfill(universe, days=args.days)
    if df.empty:
        print("[backfill] no data fetched (market holidays or network blocked).")
        return
    print(f"[backfill] {len(df)} rows, {df['symbol'].nunique()} symbols, "
          f"{df['date'].min().date()} -> {df['date'].max().date()}")


def cmd_update(args) -> None:
    universe = uni.load_universe(refresh=False)
    df = hist.update(universe, days_back=args.days)
    last = hist.latest_date(df)
    print(f"[update] latest={None if last is None else last.date()} rows={len(df)}")


def _ensure_history(universe):
    """For manual PC runs: bootstrap history on first use, else append latest days."""
    existing = hist.load()
    n_dates = int(existing["date"].nunique()) if not existing.empty else 0
    if n_dates < config.DATA["min_history_rows"]:
        log.info("history has %d trading day(s) (< %d needed); backfilling once "
                 "(this takes a few minutes the first time)...", n_dates, config.DATA["min_history_rows"])
        return hist.backfill(universe)
    return hist.update(universe, days_back=7)


def cmd_run_eod(args) -> None:
    if args.demo:
        universe = _demo_universe()
        history = hist.generate_demo(universe, days=400, sector_bias=DEMO_SECTOR_BIAS)
    else:
        universe = uni.load_universe(refresh=False)
        history = _ensure_history(universe)

    if history.empty:
        print("[eod] no history available (network blocked or market holiday). Try again later.")
        return

    benchmark = hist.equal_weight_benchmark(history)
    candidates, metrics = swing.run_screener(history, universe, benchmark, config.SCREENER)
    sector_rank = sectors.rank_sectors(metrics, config.SECTORS)
    ideas = sectors.options_ideas(sector_rank, metrics, config.SECTORS)

    try:
        news = feeds.demo_news() if args.demo else feeds.fetch_news()
    except Exception as exc:
        log.warning("news fetch failed: %s", exc)
        news = {}

    ctx = _base_context()
    ctx.update({
        "benchmark_label": config.MARKET["benchmark_label"],
        "risk_pct_per_trade": config.account()["risk_pct"],
        "stats": {"scanned": int(len(metrics)), "passed": int(metrics["passed"].sum()) if not metrics.empty else 0},
        "candidates": render.to_records(candidates),
        "sector_ranking": render.to_records(sector_rank),
        "options_ideas": ideas,
        "news": news,
    })

    render.write_site(ctx, candidates)
    print(f"[eod] scanned={ctx['stats']['scanned']} passed={ctx['stats']['passed']} "
          f"candidates={len(candidates)} -> {config.SITE_DIR/'index.html'}")

    print(f"[eod] open the dashboard: {config.SITE_DIR / 'index.html'}")
    if not args.no_email:
        subject = f"[{config.REPORT['title']}] EOD Swing Digest - {ctx['date_str']}"
        html = render.render_eod_email(ctx)
        status = email_send.send_email(subject, html, EmailConfig.from_env(), attachments=[render.screener_csv_path()])
        print(f"[eod] email: {status}")
    if args.deploy:
        print(f"[eod] deploy: {deploy.deploy_dir(config.SITE_DIR, FtpConfig.from_env())}")


def cmd_run_news(args) -> None:
    if args.demo:
        cues = cues_mod.demo_cues(config.CUES)
        news = feeds.demo_news()
    else:
        cues = cues_mod.fetch_cues(config.CUES)
        try:
            news = feeds.fetch_news()
        except Exception as exc:
            log.warning("news fetch failed: %s", exc)
            news = {}

    ctx = _base_context()
    ctx.update({"cues": cues, "news": news})

    render.write_premarket_page(ctx)
    headline_count = feeds.total(news) if isinstance(news, dict) else 0
    print(f"[news] cues={len(cues)} headlines={headline_count} -> {config.SITE_DIR/'premarket.html'}")

    if not args.no_email:
        subject = f"[{config.REPORT['title']}] Pre-Market Brief - {ctx['date_str']}"
        html = render.render_news_email(ctx)
        print(f"[news] email: {email_send.send_email(subject, html, EmailConfig.from_env())}")
    if args.deploy:
        print(f"[news] deploy: {deploy.deploy_dir(config.SITE_DIR, FtpConfig.from_env())}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nifty-scanner", description="Nifty 500 EOD swing screener + auto emails")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    bu = sub.add_parser("build-universe", help="download/cache the Nifty 500 list + sectors")
    bu.add_argument("--refresh", action="store_true", help="force re-download")
    bu.set_defaults(func=cmd_build_universe)

    bf = sub.add_parser("backfill", help="bootstrap the Parquet history store from bhavcopies")
    bf.add_argument("--days", type=int, default=config.DATA["backfill_days"])
    bf.set_defaults(func=cmd_backfill)

    up = sub.add_parser("update", help="append the latest trading day(s) to history")
    up.add_argument("--days", type=int, default=7)
    up.set_defaults(func=cmd_update)

    eod = sub.add_parser("run-eod", help="screener + sectors + local dashboard + email")
    eod.add_argument("--demo", action="store_true", help="offline synthetic data (no network)")
    eod.add_argument("--no-email", action="store_true", help="build the dashboard but do not send email")
    eod.add_argument("--deploy", action="store_true", help="also publish the dashboard to Hostinger via FTP (optional)")
    eod.set_defaults(func=cmd_run_eod)

    nw = sub.add_parser("run-news", help="global cues + consolidated news brief + email")
    nw.add_argument("--demo", action="store_true", help="offline synthetic data (no network)")
    nw.add_argument("--no-email", action="store_true", help="build the page but do not send email")
    nw.add_argument("--deploy", action="store_true", help="also publish to Hostinger via FTP (optional)")
    nw.set_defaults(func=cmd_run_news)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    setup_logging(logging.DEBUG if args.verbose else logging.INFO)
    config.ensure_dirs()
    args.func(args)


if __name__ == "__main__":
    main()
