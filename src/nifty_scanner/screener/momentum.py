"""Short-horizon momentum ranking for equities.

Ranks names by multi-timeframe returns, relative strength, volume surge and
trend quality. Looser than the swing gates so the dashboard always surfaces a
usable momentum shortlist (bullish and bearish).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .. import config, indicators as ta


def _last(series: pd.Series) -> float:
    val = series.iloc[-1] if len(series) else np.nan
    return float(val) if pd.notna(val) else float("nan")


def _isnan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def compute_momentum(
    history: pd.DataFrame,
    universe: pd.DataFrame,
    benchmark_close: pd.Series | None,
    params: dict,
) -> pd.DataFrame:
    """One momentum row per liquid symbol."""
    min_rows = int(params.get("min_history_rows", 60))
    vol_len = int(params.get("volume_avg_length", 20))
    rs_look = int(params.get("rs_lookback", 21))
    atr_len = int(params.get("atr_length", 14))
    min_price = float(params.get("min_price", 50.0))
    min_turnover = float(params.get("min_avg_turnover_cr", 5.0))

    names = dict(zip(universe["symbol"], universe["name"]))
    sectors = dict(zip(universe["symbol"], universe["sector"]))
    groups = {sym: g.sort_values("date") for sym, g in history.groupby("symbol")}

    rows: list[dict] = []
    for symbol in universe["symbol"]:
        df = groups.get(symbol)
        base = {"symbol": symbol, "name": names.get(symbol, symbol), "sector": sectors.get(symbol, "")}
        if df is None or len(df) < min_rows:
            continue

        close, high, low, vol = df["close"], df["high"], df["low"], df["volume"]
        c = _last(close)
        if c < min_price:
            continue

        ema20, ema50 = _last(ta.ema(close, 20)), _last(ta.ema(close, 50))
        rsi = _last(ta.rsi(close, 14))
        adx = _last(ta.adx(high, low, close, atr_len)["adx"])
        atr = _last(ta.atr(high, low, close, atr_len))
        avg_vol = _last(ta.sma(vol, vol_len))
        vol_mult = _last(vol) / avg_vol if avg_vol else float("nan")

        turn_src = df["turnover"] if df["turnover"].fillna(0).gt(0).any() else (close * vol)
        turnover_cr = _last(turn_src.rolling(vol_len, min_periods=vol_len).mean()) / 1e7
        if not _isnan(turnover_cr) and turnover_cr < min_turnover:
            continue

        ret_5 = _last(ta.pct_return(close, 5))
        ret_10 = _last(ta.pct_return(close, 10))
        ret_21 = _last(ta.pct_return(close, 21))
        rs = ta.relative_strength(close, benchmark_close, rs_look) if benchmark_close is not None else float("nan")

        trend_up = bool(c > ema20 > ema50) if not (_isnan(ema20) or _isnan(ema50)) else False
        trend_down = bool(c < ema20 < ema50) if not (_isnan(ema20) or _isnan(ema50)) else False

        bull_score = (
            35.0 * (0 if _isnan(ret_5) else ret_5 * 100)
            + 25.0 * (0 if _isnan(ret_10) else ret_10 * 100)
            + 15.0 * (0 if _isnan(ret_21) else ret_21 * 100)
            + 40.0 * (0 if _isnan(rs) else (rs - 1.0))
            + 8.0 * max(0.0, (0 if _isnan(vol_mult) else vol_mult) - 1.0)
            + (5.0 if trend_up else 0.0)
            + max(0.0, (0 if _isnan(rsi) else rsi) - 50.0) / 4.0
            + max(0.0, (0 if _isnan(adx) else adx) - 18.0) / 8.0
        )
        bear_score = (
            -35.0 * (0 if _isnan(ret_5) else ret_5 * 100)
            + -25.0 * (0 if _isnan(ret_10) else ret_10 * 100)
            + -15.0 * (0 if _isnan(ret_21) else ret_21 * 100)
            + 40.0 * (0 if _isnan(rs) else (1.0 - rs))
            + 8.0 * max(0.0, (0 if _isnan(vol_mult) else vol_mult) - 1.0)
            + (5.0 if trend_down else 0.0)
            + max(0.0, 50.0 - (0 if _isnan(rsi) else rsi)) / 4.0
            + max(0.0, (0 if _isnan(adx) else adx) - 18.0) / 8.0
        )

        direction = "Bullish" if bull_score >= bear_score else "Bearish"
        score = round(bull_score if direction == "Bullish" else bear_score, 2)

        rows.append({
            **base,
            "close": round(c, 2),
            "ema20": round(ema20, 2) if not _isnan(ema20) else np.nan,
            "ema50": round(ema50, 2) if not _isnan(ema50) else np.nan,
            "rsi": round(rsi, 1) if not _isnan(rsi) else np.nan,
            "adx": round(adx, 1) if not _isnan(adx) else np.nan,
            "atr": round(atr, 2) if not _isnan(atr) else np.nan,
            "vol_mult": round(vol_mult, 2) if not _isnan(vol_mult) else np.nan,
            "turnover_cr": round(turnover_cr, 1) if not _isnan(turnover_cr) else np.nan,
            "ret_5": round(ret_5 * 100, 2) if not _isnan(ret_5) else np.nan,
            "ret_10": round(ret_10 * 100, 2) if not _isnan(ret_10) else np.nan,
            "ret_21": round(ret_21 * 100, 2) if not _isnan(ret_21) else np.nan,
            "rs": round(rs, 3) if not _isnan(rs) else np.nan,
            "trend_up": trend_up,
            "trend_down": trend_down,
            "direction": direction,
            "bull_score": round(bull_score, 2),
            "bear_score": round(bear_score, 2),
            "score": score,
        })

    return pd.DataFrame(rows)


def rank_momentum(
    history: pd.DataFrame,
    universe: pd.DataFrame,
    benchmark_close: pd.Series | None,
    params: dict | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return (bullish top-N, bearish top-N, full ranked table)."""
    params = params or config.MOMENTUM
    metrics = compute_momentum(history, universe, benchmark_close, params)
    if metrics.empty:
        empty = metrics
        return empty, empty, empty

    top_n = int(params.get("max_results", 20))
    bull = (
        metrics[metrics["direction"] == "Bullish"]
        .sort_values("bull_score", ascending=False)
        .head(top_n)
        .reset_index(drop=True)
    )
    bear = (
        metrics[metrics["direction"] == "Bearish"]
        .sort_values("bear_score", ascending=False)
        .head(top_n)
        .reset_index(drop=True)
    )
    all_ranked = metrics.sort_values("score", ascending=False).reset_index(drop=True)
    return bull, bear, all_ranked
