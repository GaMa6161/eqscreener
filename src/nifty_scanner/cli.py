"""Command-line entry point.

    nifty-scanner build-universe [--refresh]
    nifty-scanner backfill [--days 420]
    nifty-scanner update [--days 7]
    nifty-scanner run-eod  [--demo] [--no-email] [--deploy]
    nifty-scanner run-scan [--demo] [--no-email] [--deploy]
    nifty-scanner run-news [--demo] [--no-email] [--deploy]
    nifty-scanner run-live [--demo] [--once] [--interval 60]
    nifty-scanner serve [--port 8765]
"""
from __future__ import annotations

import argparse
import logging
import time
import webbrowser
from datetime import date
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pandas as pd

from . import backtest, config, tracking
from .config import EmailConfig, FtpConfig
from .data import adjust as adjust_mod
from .data import earnings as earnings_mod
from .data import dhan as dhan_mod
from .data import history as hist
from .data import universe as uni
from .news import cues as cues_mod
from .news import feeds
from .report import deploy, email_send, render
from .screener import activity, live as live_mod, momentum, sectors, strikes, swing
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


def _load_market(args) -> tuple[pd.DataFrame, pd.DataFrame]:
    if args.demo:
        universe = _demo_universe()
        history = hist.generate_demo(universe, days=400, sector_bias=DEMO_SECTOR_BIAS)
    else:
        universe = uni.load_universe(refresh=False)
        history = _ensure_history(universe)
    # Bhavcopy prices are raw. Adjust on read so the stored Parquet stays raw and
    # can never be double-adjusted; see data/adjust.py.
    _LAST_EVENTS.clear()
    events = adjust_mod.detect_events(history)
    _LAST_EVENTS.extend(adjust_mod.summarise(events))
    history = adjust_mod.adjust_history(history, events)
    return universe, history


# Corporate actions found on the most recent load, surfaced in the data-health panel.
_LAST_EVENTS: list[dict] = []


def _build_eod_context(universe: pd.DataFrame, history: pd.DataFrame, *, demo: bool) -> tuple[dict, pd.DataFrame]:
    """Shared pipeline: activity + momentum + swing + sectors + strike ideas."""
    benchmark = hist.equal_weight_benchmark(history)
    candidates, metrics = swing.run_screener(
        history, universe, benchmark, config.SCREENER, earnings=earnings_mod.load()
    )
    sector_rank = sectors.rank_sectors(metrics, config.SECTORS)
    ideas = sectors.options_ideas(sector_rank, metrics, config.SECTORS)
    market_activity = activity.summarise_activity(history, universe, config.ACTIVITY)
    bull_mom, bear_mom, _ = momentum.rank_momentum(history, universe, benchmark, config.MOMENTUM)
    strike_book = strikes.build_strike_book(
        bull_mom, bear_mom, sector_rank, metrics, swing_candidates=candidates
    )

    try:
        news = feeds.demo_news() if demo else feeds.fetch_news()
    except Exception as exc:
        log.warning("news fetch failed: %s", exc)
        news = {}

    # Streaks + paper ledger. Mark existing trades to market BEFORE opening new
    # ones, so today's entries are not immediately judged on today's bar.
    session = str(market_activity.get("session_date") or history["date"].max())[:10]
    state = tracking.load_state()
    candidates = tracking.apply_streaks(candidates, state, session)
    ledger = tracking.mark_to_market(history, state, session, config.TRACKING)
    ledger["opened"] = tracking.open_positions(candidates, state, session, config.TRACKING)
    tracking.save_state(state)

    ctx = _base_context()
    ctx.update({
        "benchmark_label": config.MARKET["benchmark_label"],
        "tracking": tracking.summarise(state, config.TRACKING["show_limit"]),
        "ledger": ledger,
        "corporate_actions": list(_LAST_EVENTS),
        "risk_pct_per_trade": config.account()["risk_pct"],
        "stats": {
            "scanned": int(len(metrics)),
            "passed": int(metrics["passed"].sum()) if not metrics.empty else 0,
            "momentum_bull": int(len(bull_mom)),
            "momentum_bear": int(len(bear_mom)),
            "strike_ideas": int(len(strike_book)),
            "session_date": market_activity.get("session_date"),
        },
        "candidates": render.to_records(candidates),
        "sector_ranking": render.to_records(sector_rank),
        "options_ideas": ideas,
        "market_activity": market_activity,
        "momentum_bull": render.to_records(bull_mom),
        "momentum_bear": render.to_records(bear_mom),
        "strike_ideas": strike_book,
        "news": news,
    })
    return ctx, candidates


def cmd_run_eod(args) -> None:
    universe, history = _load_market(args)
    if history.empty:
        print("[eod] no history available (network blocked or market holiday). Try again later.")
        return

    ctx, candidates = _build_eod_context(universe, history, demo=args.demo)
    render.write_site(ctx, candidates)
    print(
        f"[eod] scanned={ctx['stats']['scanned']} passed={ctx['stats']['passed']} "
        f"momentum={ctx['stats']['momentum_bull']}/{ctx['stats']['momentum_bear']} "
        f"strikes={ctx['stats']['strike_ideas']} -> {config.SITE_DIR/'index.html'}"
    )
    print(f"[eod] open the dashboard: {config.SITE_DIR / 'index.html'}")
    print(f"[eod] or run: nifty-scanner serve")

    if not args.no_email:
        subject = f"[{config.REPORT['title']}] EOD Swing Digest - {ctx['date_str']}"
        html = render.render_eod_email(ctx)
        attachments = [render.screener_csv_path(), render.strikes_csv_path()]
        status = email_send.send_email(
            subject, html, EmailConfig.from_env(),
            attachments=[p for p in attachments if p.exists()],
        )
        print(f"[eod] email: {status}")
    if args.deploy:
        print(f"[eod] deploy: {deploy.deploy_dir(config.SITE_DIR, FtpConfig.from_env())}")


def cmd_backtest(args) -> None:
    universe, history = _load_market(args)
    if history.empty:
        print("[backtest] no history available.")
        return

    bt_params = dict(config.BACKTEST)
    if args.step:
        bt_params["step_days"] = args.step

    print(f"[backtest] replaying the screen over {history['date'].nunique()} sessions "
          f"every {bt_params['step_days']} session(s); this takes a minute...")
    signals, summary = backtest.run(history, universe, config.SCREENER, bt_params)
    print()
    print(backtest.format_report(summary))

    if args.csv and not signals.empty:
        config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        path = config.OUTPUT_DIR / "backtest.csv"
        signals.to_csv(path, index=False)
        print(f"\n[backtest] per-signal rows -> {path}")


def cmd_run_scan(args) -> None:
    """Momentum + market activity + strike suggestions (writes the same dashboard)."""
    universe, history = _load_market(args)
    if history.empty:
        print("[scan] no history available (network blocked or market holiday). Try again later.")
        return

    ctx, candidates = _build_eod_context(universe, history, demo=args.demo)
    render.write_site(ctx, candidates)
    breadth = (ctx.get("market_activity") or {}).get("breadth") or {}
    print(
        f"[scan] session={ctx['stats'].get('session_date')} "
        f"adv/dec={breadth.get('advances')}/{breadth.get('declines')} "
        f"momentum bull/bear={ctx['stats']['momentum_bull']}/{ctx['stats']['momentum_bear']} "
        f"strike ideas={ctx['stats']['strike_ideas']}"
    )
    print(f"[scan] dashboard -> {config.SITE_DIR / 'index.html'}")
    print("[scan] serve with: nifty-scanner serve")

    if not args.no_email:
        subject = f"[{config.REPORT['title']}] Momentum + Strikes - {ctx['date_str']}"
        html = render.render_eod_email(ctx)
        attachments = [render.screener_csv_path(), render.strikes_csv_path()]
        status = email_send.send_email(
            subject, html, EmailConfig.from_env(),
            attachments=[p for p in attachments if p.exists()],
        )
        print(f"[scan] email: {status}")
    if getattr(args, "deploy", False):
        print(f"[scan] deploy: {deploy.deploy_dir(config.SITE_DIR, FtpConfig.from_env())}")


def cmd_serve(args) -> None:
    """Serve the static dashboard locally."""
    site = config.SITE_DIR
    live = bool(getattr(args, "live", False))
    page = "live.html" if live else "index.html"
    if not (site / page).exists():
        hint = "nifty-scanner run-live --demo --once" if live else "nifty-scanner run-scan --demo"
        print(f"[serve] {page} missing. Run: {hint}")
        return
    handler = partial(SimpleHTTPRequestHandler, directory=str(site))
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    url = f"http://127.0.0.1:{args.port}/{page}"
    print(f"[serve] dashboard at {url}  (Ctrl+C to stop)")
    if not args.no_open:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[serve] stopped")
    finally:
        server.server_close()


def _live_universe(prefer: str = "nifty50") -> pd.DataFrame:
    if prefer != "nifty500":
        try:
            return _demo_universe()
        except Exception:
            pass
    return uni.load_universe(refresh=False)


def _run_live_once(args) -> dict:
    universe = _live_universe(config.LIVE.get("universe", "nifty50"))
    if args.demo:
        quotes = live_mod.demo_live_quotes(universe)
        mapped = universe.copy()
        mapped["security_id"] = 0
        mapped["is_fno"] = True
        client = None
    else:
        client = dhan_mod.DhanClient()
        dhan_mod.download_equity_map(force=False)
        mapped = dhan_mod.map_universe(universe)
        quotes = live_mod.fetch_live_quotes(client, universe)

    if quotes.empty:
        raise RuntimeError("no live quotes returned (market closed, bad token, or empty universe map)")

    activity_panel = live_mod.summarise_live_activity(quotes, config.LIVE)
    bull, bear = live_mod.rank_live_momentum(quotes, config.LIVE)

    if args.demo or client is None:
        strike_ideas = strikes.suggestions_from_momentum(bull, bear)
        for idea in strike_ideas:
            idea["rationale"] = "(demo scaffold) " + str(idea.get("rationale", ""))
    else:
        strike_ideas = live_mod.build_live_strike_ideas(client, bull, bear, mapped, config.LIVE)

    ctx = _base_context()
    ctx.update({
        "title": f"{config.REPORT['title']} · Live",
        "benchmark_label": "Dhan live quotes",
        "risk_pct_per_trade": config.account()["risk_pct"],
        "live_mode": True,
        "refresh_seconds": int(getattr(args, "interval", None) or config.LIVE["refresh_seconds"]),
        "stats": {
            "scanned": int(len(quotes)),
            "passed": 0,
            "momentum_bull": int(len(bull)),
            "momentum_bear": int(len(bear)),
            "strike_ideas": int(len(strike_ideas)),
            "session_date": activity_panel.get("session_date"),
        },
        "candidates": [],
        "sector_ranking": [],
        "options_ideas": [],
        "market_activity": activity_panel,
        "momentum_bull": render.to_records(bull),
        "momentum_bear": render.to_records(bear),
        "strike_ideas": strike_ideas,
        "news": {},
    })
    render.write_live_site(ctx)
    return ctx


def cmd_run_live(args) -> None:
    """Poll Dhan quotes + option chains and refresh the live dashboard."""
    interval = int(args.interval or config.LIVE["refresh_seconds"])
    args.interval = interval

    def _once():
        ctx = _run_live_once(args)
        breadth = (ctx.get("market_activity") or {}).get("breadth") or {}
        print(
            f"[live] {ctx['generated_at']} IST  scanned={ctx['stats']['scanned']} "
            f"adv/dec={breadth.get('advances')}/{breadth.get('declines')} "
            f"mom={ctx['stats']['momentum_bull']}/{ctx['stats']['momentum_bear']} "
            f"strikes={ctx['stats']['strike_ideas']} -> {config.SITE_DIR/'live.html'}"
        )

    try:
        _once()
    except Exception as exc:
        print(f"[live] failed: {exc}")
        return

    if args.once:
        print("[live] open with: nifty-scanner serve --live")
        return

    print(f"[live] refreshing every {interval}s  (Ctrl+C to stop)")
    print(f"[live] dashboard: {config.SITE_DIR/'live.html'}  or  nifty-scanner serve --live")
    try:
        while True:
            time.sleep(interval)
            try:
                _once()
            except Exception as exc:
                log.warning("live refresh failed: %s", exc)
                print(f"[live] refresh error: {exc}")
    except KeyboardInterrupt:
        print("\n[live] stopped")


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

    eod = sub.add_parser("run-eod", help="full EOD: activity + momentum + swing + strikes + dashboard")
    eod.add_argument("--demo", action="store_true", help="offline synthetic data (no network)")
    eod.add_argument("--no-email", action="store_true", help="build the dashboard but do not send email")
    eod.add_argument("--deploy", action="store_true", help="also publish the dashboard to Hostinger via FTP (optional)")
    eod.set_defaults(func=cmd_run_eod)

    scan = sub.add_parser("run-scan", help="market activity + momentum stocks + option strike ideas + dashboard")
    scan.add_argument("--demo", action="store_true", help="offline synthetic data (no network)")
    scan.add_argument("--no-email", action="store_true", help="build the dashboard but do not send email")
    scan.add_argument("--deploy", action="store_true", help="also publish the dashboard via FTP (optional)")
    scan.set_defaults(func=cmd_run_scan)

    nw = sub.add_parser("run-news", help="global cues + consolidated news brief + email")
    nw.add_argument("--demo", action="store_true", help="offline synthetic data (no network)")
    nw.add_argument("--no-email", action="store_true", help="build the page but do not send email")
    nw.add_argument("--deploy", action="store_true", help="also publish to Hostinger via FTP (optional)")
    nw.set_defaults(func=cmd_run_news)

    lv = sub.add_parser("run-live", help="Dhan live quotes + momentum + option-chain strikes (auto-refresh)")
    lv.add_argument("--demo", action="store_true", help="offline synthetic live quotes (no Dhan)")
    lv.add_argument("--once", action="store_true", help="single snapshot, then exit")
    lv.add_argument("--interval", type=int, default=None, help="refresh seconds (default 60)")
    lv.set_defaults(func=cmd_run_live)

    bt = sub.add_parser("backtest", help="walk-forward test of the screener gates vs the benchmark")
    bt.add_argument("--demo", action="store_true", help="offline synthetic data (no network)")
    bt.add_argument("--step", type=int, default=None, help="re-screen every N sessions (default 10)")
    bt.add_argument("--csv", action="store_true", help="also write per-signal rows to output/backtest.csv")
    bt.set_defaults(func=cmd_backtest)

    srv = sub.add_parser("serve", help="open a local web server for output/site dashboard")
    srv.add_argument("--port", type=int, default=8765)
    srv.add_argument("--live", action="store_true", help="open live.html instead of index.html")
    srv.add_argument("--no-open", action="store_true", help="do not open a browser tab")
    srv.set_defaults(func=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    setup_logging(logging.DEBUG if args.verbose else logging.INFO)
    config.ensure_dirs()
    args.func(args)


if __name__ == "__main__":
    main()
