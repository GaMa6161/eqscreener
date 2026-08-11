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
    earnings: dict | None = None,
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
        swing_low = _last(ta.rolling_low(low, params.get("swing_low_lookback", 10)))
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
            "swing_low": round(swing_low, 2) if not math.isnan(swing_low) else np.nan,
            "avg_vol": round(avg_vol, 0) if not math.isnan(avg_vol) else np.nan,
            "earnings_in": (earnings or {}).get(symbol, np.nan),
        }
        _apply_gates(row, params)
        _apply_levels(row, params, acct)
        rows.append(row)

    return _apply_scores(pd.DataFrame(rows), params)


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

    blackout = params.get("earnings_blackout_days", 0)
    if blackout and not _isnan(row.get("earnings_in")) and row["earnings_in"] <= blackout:
        reasons.append(f"earnings in {int(row['earnings_in'])}d")

    row["passed"] = len(reasons) == 0
    row["reason"] = "" if row["passed"] else ", ".join(reasons)
    row["setups"] = _setups(row, params)


def _apply_levels(row: dict, params: dict, acct: dict) -> None:
    """Structural stop, targets, and a position size bounded by risk and liquidity."""
    atr = row.get("atr", np.nan)
    close = row.get("close", np.nan)
    if _isnan(atr) or _isnan(close):
        row.update({"stop": np.nan, "risk_pct": np.nan, "targets": [], "qty": np.nan,
                    "position_value": np.nan, "stop_basis": "", "size_capped_by": ""})
        return

    # Prefer a stop just under the recent swing low - price has to break real
    # structure to take you out, not just wobble. Bounded on both sides: never
    # wider than the ATR stop, never tighter than daily noise.
    atr_stop = close - params["stop_atr_mult"] * atr
    swing_low = row.get("swing_low", np.nan)
    basis = "ATR"
    stop = atr_stop
    if not _isnan(swing_low):
        structural = swing_low - params.get("stop_buffer_atr", 0.25) * atr
        if structural > atr_stop:
            stop, basis = structural, "swing low"
    tightest = close - params.get("min_stop_atr", 0.75) * atr
    if stop > tightest:
        stop, basis = tightest, "min ATR floor"

    stop = round(stop, 2)
    risk_ps = close - stop
    row["stop"] = stop
    row["stop_basis"] = basis
    row["risk_pct"] = round(risk_ps / close * 100, 1) if close else np.nan
    row["targets"] = [round(close + m * risk_ps, 2) for m in params.get("target_r_multiples", [2.0, 3.0])]

    budget = acct["capital"] * acct["risk_pct"] / 100.0
    qty = int(budget // risk_ps) if risk_ps > 0 else 0
    capped_by = ""

    # You cannot trade size you cannot fill: cap against average daily volume.
    avg_vol = row.get("avg_vol", np.nan)
    adv_pct = params.get("max_adv_pct", 0)
    if adv_pct and not _isnan(avg_vol) and avg_vol > 0:
        adv_cap = int(avg_vol * adv_pct / 100.0)
        if adv_cap < qty:
            qty, capped_by = adv_cap, "liquidity"

    # And no single name should dominate the book.
    pos_pct = params.get("max_position_pct", 0)
    if pos_pct and close > 0:
        value_cap = int((acct["capital"] * pos_pct / 100.0) // close)
        if value_cap < qty:
            qty, capped_by = value_cap, "position cap"

    row["qty"] = max(qty, 0)
    row["size_capped_by"] = capped_by
    row["position_value"] = round(row["qty"] * close, 0)


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


def _apply_scores(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Composite score from cross-sectionally percentile-ranked components.

    Ranking first is what makes the weights honest. Scoring the raw values let
    whichever component happened to have the widest numeric range dominate: a
    63-day return spans roughly +/-50 while relative strength spans +/-0.1, so
    the old formula was effectively "sort by 3-month return" no matter what
    weight relative strength carried.

    Every component becomes a 0-100 percentile within the day's universe, so a
    weight of 0.30 really is 30% of the decision. Missing values rank neutral.
    """
    if df.empty:
        return df
    weights = params.get("score_weights") or {}
    if not weights:
        df["score"] = np.nan
        return df

    score = pd.Series(0.0, index=df.index)
    for col, weight in weights.items():
        if col not in df.columns:
            continue
        values = pd.to_numeric(df[col], errors="coerce")
        if col == "near_52w_pct":
            values = -values          # smaller distance from the high is better
        elif col == "rsi":
            values = values.where(values <= 75, 150 - values)  # punish overextension
        ranks = values.rank(pct=True, na_option="keep") * 100.0
        score += ranks.fillna(50.0) * weight

    df["score"] = score.round(1)
    return df


def _isnan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _cap_by_sector(df: pd.DataFrame, max_per_sector: int) -> pd.DataFrame:
    """Keep the best N per sector so the list is not one macro bet in disguise.

    Fifteen candidates that are nine PSU banks is a single position wearing nine
    tickers; correlation shows up as a drawdown, not on the screen.
    """
    if not max_per_sector or df.empty or "sector" not in df.columns:
        return df
    counts: dict[str, int] = {}
    keep = []
    for idx, row in df.iterrows():          # already score-sorted
        sector = row.get("sector") or "Unknown"
        if counts.get(sector, 0) >= max_per_sector:
            continue
        counts[sector] = counts.get(sector, 0) + 1
        keep.append(idx)
    return df.loc[keep]


def run_screener(
    history: pd.DataFrame,
    universe: pd.DataFrame,
    benchmark_close: pd.Series | None,
    params: dict,
    earnings: dict | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (candidates, all_metrics). Candidates pass every gate, sorted by score."""
    metrics = compute_metrics(history, universe, benchmark_close, params, earnings=earnings)
    if metrics.empty:
        return metrics, metrics
    passed = metrics[metrics["passed"] == True].copy()  # noqa: E712
    passed = passed.sort_values("score", ascending=False)
    passed = _cap_by_sector(passed, params.get("max_per_sector", 0))
    passed = passed.head(params.get("max_results", 25))
    return passed.reset_index(drop=True), metrics
