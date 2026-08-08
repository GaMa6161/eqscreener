"""Swing screener: per-stock metrics on the history store + configurable gates.

Everything is computed on confirmed daily closes (the latest completed EOD bar),
so there is no intraday lookahead or repainting.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .. import config, indicators as ta

CRORE = 1e7  # 1 crore = 10,000,000


def _last(series: pd.Series) -> float:
    val = series.iloc[-1] if len(series) else np.nan
    return float(val) if pd.notna(val) else float("nan")


def compute_metrics(
    history: pd.DataFrame,
    universe: pd.DataFrame,
    benchmark_close: pd.Series | None,
    params: dict,
) -> pd.DataFrame:
    """One metrics row per stock, including pass/fail gates, score and levels."""
    rsi_len = params["momentum"]["rsi_length"]
    atr_len = params.get("atr_length", 14)
    vol_len = params["volume"]["avg_length"]
    rs_look = params["relative_strength"]["lookback"]
    look_high = params["breakout"]["lookback_high"]
    min_rows = config.DATA["min_history_rows"]
    acct = config.account()

    names = dict(zip(universe["symbol"], universe["name"]))
    sectors = dict(zip(universe["symbol"], universe["sector"]))
    groups = {sym: g.sort_values("date") for sym, g in history.groupby("symbol")}

    rows: list[dict] = []
    for symbol in universe["symbol"]:
        df = groups.get(symbol)
        base = {"symbol": symbol, "name": names.get(symbol, symbol), "sector": sectors.get(symbol, "")}
        if df is None or len(df) < min_rows:
            rows.append({**base, "passed": False, "reason": "insufficient data", "score": np.nan})
            continue

        close, high, low, vol = df["close"], df["high"], df["low"], df["volume"]
        c = _last(close)
        ema20, ema50, ema200 = _last(ta.ema(close, 20)), _last(ta.ema(close, 50)), _last(ta.ema(close, 200))
        rsi = _last(ta.rsi(close, rsi_len))
        macd_df = ta.macd(close)
        macd_hist = _last(macd_df["hist"])
        macd_bull = macd_hist > 0 and _last(macd_df["macd"]) > _last(macd_df["signal"])
        adx = _last(ta.adx(high, low, close, atr_len)["adx"])
        atr = _last(ta.atr(high, low, close, atr_len))

        avg_vol = _last(ta.sma(vol, vol_len))
        vol_mult = _last(vol) / avg_vol if avg_vol else float("nan")
        turn_src = df["turnover"] if df["turnover"].fillna(0).gt(0).any() else (close * vol)
        turnover_cr = _last(turn_src.rolling(vol_len, min_periods=vol_len).mean()) / CRORE

        high_n = _last(ta.rolling_high(high, look_high)) if look_high else float("nan")
        is_new_high = bool(look_high and c >= (high_n - 1e-9))
        win = min(252, len(high))
        high_52w = float(high.tail(win).max())
        near_52w_pct = (high_52w - c) / high_52w * 100 if high_52w else float("nan")

        rs = ta.relative_strength(close, benchmark_close, rs_look) if benchmark_close is not None else float("nan")
        ret_21 = _last(ta.pct_return(close, 21))
        ret_63 = _last(ta.pct_return(close, 63))

        row = {
            **base,
            "close": round(c, 2),
            "ema20": round(ema20, 2), "ema50": round(ema50, 2), "ema200": round(ema200, 2),
            "rsi": round(rsi, 1),
            "macd_hist": round(macd_hist, 3), "macd_bull": bool(macd_bull),
            "adx": round(adx, 1) if not math.isnan(adx) else np.nan,
            "vol_mult": round(vol_mult, 2) if not math.isnan(vol_mult) else np.nan,
            "turnover_cr": round(turnover_cr, 1) if not math.isnan(turnover_cr) else np.nan,
            "is_new_high": is_new_high,
            "near_52w_pct": round(near_52w_pct, 1) if not math.isnan(near_52w_pct) else np.nan,
            "rs": round(rs, 3) if not math.isnan(rs) else np.nan,
            "ret_21": round(ret_21 * 100, 1) if not math.isnan(ret_21) else np.nan,
            "ret_63": round(ret_63 * 100, 1) if not math.isnan(ret_63) else np.nan,
            "above_ema50": bool(c > ema50) if not math.isnan(ema50) else False,
            "atr": round(atr, 2) if not math.isnan(atr) else np.nan,
        }
        _apply_gates(row, params)
        _apply_levels(row, params, acct)
        rows.append(row)

    return pd.DataFrame(rows)


def _apply_gates(row: dict, params: dict) -> None:
    reasons: list[str] = []
    if row["close"] < params["min_price"]:
        reasons.append("price<min")
    if not _isnan(row.get("turnover_cr")) and row["turnover_cr"] < params["min_avg_turnover_cr"]:
        reasons.append("illiquid")

    tr = params["trend"]
    if tr["require_close_above_ema200"] and not (row["close"] > row["ema200"]):
        reasons.append("below EMA200")
    if tr["require_ema50_above_ema200"] and not (row["ema50"] > row["ema200"]):
        reasons.append("EMA50<EMA200")
    if tr["require_close_above_ema20"] and not (row["close"] > row["ema20"]):
        reasons.append("below EMA20")

    mo = params["momentum"]
    if not (mo["rsi_min"] <= row["rsi"] <= mo["rsi_max"]):
        reasons.append("RSI out of zone")
    if mo["require_macd_bullish"] and not row["macd_bull"]:
        reasons.append("MACD not bullish")

    bo = params["breakout"]
    if bo["near_52w_high_pct"] and not _isnan(row.get("near_52w_pct")):
        if row["near_52w_pct"] > bo["near_52w_high_pct"]:
            reasons.append("far from 52w high")

    vo = params["volume"]
    if not _isnan(row.get("vol_mult")) and row["vol_mult"] < vo["min_volume_multiple"]:
        reasons.append("low volume")

    rsp = params["relative_strength"]
    if rsp["require_outperform_benchmark"] and not _isnan(row.get("rs")) and row["rs"] <= 1.0:
        reasons.append("lagging benchmark")

    row["passed"] = len(reasons) == 0
    row["reason"] = "" if row["passed"] else ", ".join(reasons)
    row["setups"] = _setups(row, params)
    row["score"] = _score(row)


def _apply_levels(row: dict, params: dict, acct: dict) -> None:
    """ATR-based stop/targets and risk-based position size."""
    atr = row.get("atr", np.nan)
    close = row.get("close", np.nan)
    if _isnan(atr) or _isnan(close):
        row.update({"stop": np.nan, "risk_pct": np.nan, "targets": [], "qty": np.nan, "position_value": np.nan})
        return
    stop = round(close - params["stop_atr_mult"] * atr, 2)
    risk_ps = close - stop
    row["stop"] = stop
    row["risk_pct"] = round(risk_ps / close * 100, 1) if close else np.nan
    row["targets"] = [round(close + m * risk_ps, 2) for m in params.get("target_r_multiples", [2.0, 3.0])]
    budget = acct["capital"] * acct["risk_pct"] / 100.0
    qty = int(budget // risk_ps) if risk_ps > 0 else 0
    row["qty"] = qty
    row["position_value"] = round(qty * close, 0)


def _setups(row: dict, params: dict) -> list[str]:
    """Human-readable tags describing why this name showed up."""
    tags: list[str] = []
    if row.get("is_new_high"):
        tags.append("20d breakout")
    if not _isnan(row.get("near_52w_pct")) and row["near_52w_pct"] <= 5:
        tags.append("near 52w high")
    if not _isnan(row.get("rs")) and row["rs"] > 1.05:
        tags.append("RS leader")
    if not _isnan(row.get("adx")) and row["adx"] >= params["momentum"]["adx_min"]:
        tags.append("strong trend")
    if not _isnan(row.get("vol_mult")) and row["vol_mult"] >= 1.5:
        tags.append("volume surge")
    return tags


def _score(row: dict) -> float:
    def g(k):
        v = row.get(k, np.nan)
        return 0.0 if (v is None or (isinstance(v, float) and math.isnan(v))) else v

    rs = g("rs")
    score = (
        40.0 * (rs - 1.0 if rs else 0.0)
        + 0.4 * g("ret_63")
        + 0.3 * g("ret_21")
        + 5.0 * (g("vol_mult") - 1.0)
        + max(0.0, g("rsi") - 50.0) / 5.0
        + max(0.0, g("adx") - 20.0) / 10.0
        - 0.2 * g("near_52w_pct")
    )
    return round(score, 2)


def _isnan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def run_screener(
    history: pd.DataFrame,
    universe: pd.DataFrame,
    benchmark_close: pd.Series | None,
    params: dict,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (candidates, all_metrics). Candidates pass every gate, sorted by score."""
    metrics = compute_metrics(history, universe, benchmark_close, params)
    if metrics.empty:
        return metrics, metrics
    passed = metrics[metrics["passed"] == True].copy()  # noqa: E712
    passed = passed.sort_values("score", ascending=False).head(params.get("max_results", 25))
    return passed.reset_index(drop=True), metrics
