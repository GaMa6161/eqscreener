"""Walk-forward test of the screener gates.

Until now there was no way to tell whether the eight gates and the composite
score had any edge - and therefore no basis for tuning a threshold beyond taste.
This replays the screen across history: at each step it rebuilds the metrics
using only bars up to that date, takes the names that passed, and measures what
they actually did over the following sessions.

The benchmark comparison is the part that matters. In a rising market almost any
long screen shows positive returns; the question is whether these names beat an
equal-weighted basket of the same universe over the same window. That difference
is the `excess` column.

Deliberately honest about lookahead: metrics at date D are computed from a
history slice ending at D, and returns are measured from D's close forward, so
nothing from the future leaks into the selection.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from . import config
from .screener import swing

log = logging.getLogger(__name__)


def _forward_returns(history: pd.DataFrame, dates: list, start_idx: int, horizons: list[int]) -> pd.DataFrame:
    """Per-symbol forward return from `dates[start_idx]` over each horizon."""
    entry_date = dates[start_idx]
    entry = history[history["date"] == entry_date].set_index("symbol")["close"]
    out = pd.DataFrame({"entry": entry})
    for h in horizons:
        idx = start_idx + h
        if idx >= len(dates):
            out[f"fwd_{h}"] = np.nan
            continue
        later = history[history["date"] == dates[idx]].set_index("symbol")["close"]
        out[f"fwd_{h}"] = (later / out["entry"] - 1.0) * 100.0
    return out


def run(
    history: pd.DataFrame,
    universe: pd.DataFrame,
    params: dict | None = None,
    bt_params: dict | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Replay the screen across history.

    Returns (per-signal rows, summary dict). `history` must already be
    split-adjusted - unadjusted bars produce fake -80% forward returns.
    """
    params = params or config.SCREENER
    bt = bt_params or config.BACKTEST
    horizons = bt["horizons"]
    step = bt["step_days"]
    warmup = bt["min_warmup_rows"]

    history = history.sort_values(["symbol", "date"])
    dates = sorted(history["date"].unique())
    if len(dates) <= warmup + max(horizons):
        log.warning("not enough history to backtest: %d sessions", len(dates))
        return pd.DataFrame(), {"signals": 0, "windows": 0}

    rows: list[dict] = []
    windows = 0
    # Stop early enough that the longest horizon still has bars to measure.
    last_start = len(dates) - max(horizons) - 1
    for i in range(warmup, last_start + 1, step):
        as_of = dates[i]
        past = history[history["date"] <= as_of]
        benchmark = _equal_weight(past)
        candidates, _ = swing.run_screener(past, universe, benchmark, params)
        if candidates.empty:
            continue
        windows += 1

        fwd = _forward_returns(history, dates, i, horizons)
        # Universe-wide mean over the same window = what "just buy everything" did.
        bench_fwd = {f"fwd_{h}": fwd[f"fwd_{h}"].mean() for h in horizons}

        for _, cand in candidates.iterrows():
            symbol = cand["symbol"]
            if symbol not in fwd.index:
                continue
            row = {
                "date": str(as_of)[:10],
                "symbol": symbol,
                "sector": cand.get("sector", ""),
                "score": cand.get("score", np.nan),
            }
            for h in horizons:
                r = fwd.loc[symbol, f"fwd_{h}"]
                row[f"ret_{h}"] = round(r, 2) if pd.notna(r) else np.nan
                row[f"excess_{h}"] = (
                    round(r - bench_fwd[f"fwd_{h}"], 2)
                    if pd.notna(r) and pd.notna(bench_fwd[f"fwd_{h}"]) else np.nan
                )
            rows.append(row)

    signals = pd.DataFrame(rows)
    return signals, summarise(signals, horizons, windows)


def _equal_weight(df: pd.DataFrame) -> pd.Series:
    """Equal-weight index of the slice, matching history.equal_weight_benchmark."""
    if df.empty:
        return pd.Series(dtype=float)
    wide = df.pivot_table(index="date", columns="symbol", values="close")
    if wide.empty:
        return pd.Series(dtype=float)
    norm = wide / wide.ffill().bfill().iloc[0]
    return (norm.mean(axis=1, skipna=True) * 1000.0).dropna()


def summarise(signals: pd.DataFrame, horizons: list[int], windows: int) -> dict:
    """Hit rate and average edge per horizon."""
    if signals.empty:
        return {"signals": 0, "windows": windows, "horizons": {}}

    out = {}
    for h in horizons:
        ret, exc = signals[f"ret_{h}"].dropna(), signals[f"excess_{h}"].dropna()
        if ret.empty:
            continue
        out[h] = {
            "n": int(len(ret)),
            "hit_rate": round(100.0 * (ret > 0).mean(), 1),
            "avg_return": round(float(ret.mean()), 2),
            "median_return": round(float(ret.median()), 2),
            "avg_excess": round(float(exc.mean()), 2) if not exc.empty else None,
            "beat_benchmark_pct": round(100.0 * (exc > 0).mean(), 1) if not exc.empty else None,
        }
    return {"signals": int(len(signals)), "windows": windows, "horizons": out}


def format_report(summary: dict) -> str:
    """Plain-text table for the CLI."""
    if not summary.get("horizons"):
        return "[backtest] no signals produced - not enough history?"
    lines = [
        f"[backtest] {summary['signals']} signals across {summary['windows']} screen dates",
        "",
        f"{'horizon':>8} {'n':>6} {'hit%':>7} {'avg ret%':>10} {'med ret%':>10} {'avg excess%':>13} {'beat bench%':>12}",
        "-" * 70,
    ]
    for h, s in summary["horizons"].items():
        lines.append(
            f"{str(h) + 'd':>8} {s['n']:>6} {s['hit_rate']:>7} {s['avg_return']:>10} "
            f"{s['median_return']:>10} {str(s['avg_excess']):>13} {str(s['beat_benchmark_pct']):>12}"
        )
    lines += [
        "",
        "avg excess% is the edge over an equal-weighted basket of the same",
        "universe over the same window. If it is not clearly positive, the",
        "gates are selecting names no better than the market itself.",
    ]
    return "\n".join(lines)
