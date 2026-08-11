"""Latest-session market activity from the local OHLCV history store.

Summarises breadth, gainers/losers, volume leaders and unusual-volume names
for the most recent trading day available in the Parquet store.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .. import indicators as ta

CRORE = 1e7


def _round(v, n: int = 2):
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return None
    return round(float(v), n)


def session_frame(history: pd.DataFrame, universe: pd.DataFrame) -> pd.DataFrame:
    """One row per symbol for the latest session, with day change and volume context."""
    if history.empty:
        return pd.DataFrame()

    names = dict(zip(universe["symbol"], universe["name"]))
    sectors = dict(zip(universe["symbol"], universe["sector"]))
    hist = history.sort_values(["symbol", "date"]).copy()
    hist["prev_close"] = hist.groupby("symbol")["close"].shift(1)
    hist["avg_vol_20"] = hist.groupby("symbol")["volume"].transform(
        lambda s: ta.sma(s, 20)
    )

    last_date = hist["date"].max()
    day = hist[hist["date"] == last_date].copy()
    if day.empty:
        return pd.DataFrame()

    day["name"] = day["symbol"].map(names).fillna(day["symbol"])
    day["sector"] = day["symbol"].map(sectors).fillna("")
    day["chg_pct"] = (day["close"] / day["prev_close"] - 1.0) * 100.0
    day["vol_mult"] = day["volume"] / day["avg_vol_20"].replace(0, np.nan)
    turn = day["turnover"].fillna(0.0)
    day["turnover_cr"] = np.where(turn > 0, turn / CRORE, day["close"] * day["volume"] / CRORE)
    day["session_date"] = pd.to_datetime(day["date"]).dt.strftime("%Y-%m-%d")
    return day


def summarise_activity(history: pd.DataFrame, universe: pd.DataFrame, params: dict) -> dict:
    """Build market-activity panels used by the dashboard."""
    day = session_frame(history, universe)
    empty = {
        "session_date": None,
        "breadth": {},
        "gainers": [],
        "losers": [],
        "volume_leaders": [],
        "unusual_volume": [],
        "turnover_leaders": [],
    }
    if day.empty:
        return empty

    top_n = int(params.get("top_n", 15))
    min_price = float(params.get("min_price", 50.0))
    min_turnover = float(params.get("min_turnover_cr", 5.0))
    unusual_mult = float(params.get("unusual_volume_multiple", 2.0))

    liquid = day[
        (day["close"] >= min_price)
        & (day["turnover_cr"].fillna(0) >= min_turnover)
        & day["chg_pct"].notna()
    ].copy()

    advances = int((day["chg_pct"] > 0).sum())
    declines = int((day["chg_pct"] < 0).sum())
    unchanged = int((day["chg_pct"] == 0).sum()) + int(day["chg_pct"].isna().sum())
    avg_chg = float(day["chg_pct"].mean(skipna=True)) if day["chg_pct"].notna().any() else 0.0

    cols = [
        "symbol", "name", "sector", "close", "chg_pct", "volume",
        "vol_mult", "turnover_cr", "high", "low", "open",
    ]

    def _panel(df: pd.DataFrame) -> list[dict]:
        if df.empty:
            return []
        out = df[cols].copy()
        for c in ("close", "chg_pct", "vol_mult", "turnover_cr", "high", "low", "open"):
            out[c] = out[c].map(lambda v: _round(v, 2 if c != "chg_pct" else 2))
        out["volume"] = out["volume"].fillna(0).astype("int64")
        return out.astype(object).where(pd.notna(out), None).to_dict("records")

    gainers = liquid.sort_values("chg_pct", ascending=False).head(top_n)
    losers = liquid.sort_values("chg_pct", ascending=True).head(top_n)
    vol_leaders = liquid.sort_values("volume", ascending=False).head(top_n)
    turn_leaders = liquid.sort_values("turnover_cr", ascending=False).head(top_n)
    unusual = liquid[liquid["vol_mult"].fillna(0) >= unusual_mult].sort_values(
        "vol_mult", ascending=False
    ).head(top_n)

    return {
        "session_date": str(day["session_date"].iloc[0]),
        "breadth": {
            "scanned": int(len(day)),
            "advances": advances,
            "declines": declines,
            "unchanged": unchanged,
            "advance_decline_ratio": _round(advances / declines, 2) if declines else None,
            "avg_chg_pct": _round(avg_chg, 2),
            "total_turnover_cr": _round(float(day["turnover_cr"].sum()), 1),
        },
        "gainers": _panel(gainers),
        "losers": _panel(losers),
        "volume_leaders": _panel(vol_leaders),
        "unusual_volume": _panel(unusual),
        "turnover_leaders": _panel(turn_leaders),
    }
