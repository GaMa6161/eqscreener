"""Sector rotation ranking and defined-risk options-idea generation.

Option ideas are mechanical, educational scaffolding derived from sector
momentum - NOT trade recommendations. Strike/expiry selection, greeks and IV
must be handled in your broker/analysis layer (no free Indian feed gives clean
per-strike greeks/IV).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def rank_sectors(metrics: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Aggregate constituent metrics into a ranked sector table.

    Robust to the bootstrap window where every stock has insufficient history and
    the indicator columns are therefore absent.
    """
    if metrics.empty or "sector" not in metrics:
        return pd.DataFrame()

    m = metrics.copy()
    for col in ("ret_21", "ret_63", "rs"):
        m[col] = pd.to_numeric(m[col], errors="coerce") if col in m else np.nan
    above = m["above_ema50"] if "above_ema50" in m else pd.Series(index=m.index, dtype=object)
    m["_above"] = above.map(lambda v: 1.0 if v is True else (0.0 if v is False else float("nan")))
    passed = m["passed"] if "passed" in m else pd.Series(False, index=m.index)
    m["_passed"] = passed.map(lambda v: 1 if v else 0)

    grp = m.groupby("sector")
    table = pd.DataFrame({
        "constituents": grp.size(),
        "avg_ret_21": grp["ret_21"].mean(),
        "avg_ret_63": grp["ret_63"].mean(),
        "avg_rs": grp["rs"].mean(),
        "breadth_above_ema50": grp["_above"].mean() * 100.0,
        "passing": grp["_passed"].sum(),
    }).reset_index()

    table["momentum_score"] = (
        0.6 * table["avg_ret_63"].fillna(0.0)
        + 0.4 * table["avg_ret_21"].fillna(0.0)
        + 0.1 * (table["breadth_above_ema50"].fillna(0.0) - 50.0)
    ).round(2)

    table = table.sort_values("momentum_score", ascending=False).reset_index(drop=True)
    table["rank"] = table.index + 1
    for col in ("avg_ret_21", "avg_ret_63", "avg_rs", "breadth_above_ema50"):
        table[col] = table[col].round(2)
    return table


def _top_names(metrics: pd.DataFrame, sector: str, n: int = 4) -> str:
    subset = metrics[(metrics["sector"] == sector) & (metrics["score"].notna())]
    subset = subset.sort_values("score", ascending=False).head(n)
    return ", ".join(subset["symbol"].tolist()) if not subset.empty else "-"


def options_ideas(sector_ranking: pd.DataFrame, metrics: pd.DataFrame, params: dict) -> list[dict]:
    """Directional, defined-risk ideas from sector leaders/laggards."""
    if sector_ranking.empty:
        return []

    index_map: dict[str, str] = params.get("index_option_sectors", {}) or {}
    top_n = params.get("top_n_leaders", 3)
    bottom_n = params.get("bottom_n_laggards", 3)

    ideas: list[dict] = []
    leaders = sector_ranking.head(top_n)
    laggards = sector_ranking.tail(bottom_n).iloc[::-1]

    for _, r in leaders.iterrows():
        sector = r["sector"]
        if sector in index_map:
            instrument, structure = index_map[sector], "Bull call (debit) spread - buy ATM call, sell higher OTM call"
        else:
            instrument, structure = f"Strongest F&O name in {sector}", "Bull call spread on the strongest name (defined risk)"
        ideas.append({
            "sector": sector, "bias": "Bullish", "instrument": instrument, "structure": structure,
            "rationale": f"Sector rank #{int(r['rank'])}, 3M avg {r['avg_ret_63']}%, breadth {r['breadth_above_ema50']}% >50-EMA",
            "watchlist": _top_names(metrics, sector),
        })

    for _, r in laggards.iterrows():
        sector = r["sector"]
        if sector in index_map:
            instrument, structure = index_map[sector], "Bear put (debit) spread - buy ATM put, sell lower OTM put"
        else:
            instrument, structure = f"Weakest F&O name in {sector}", "Bear put spread / avoid longs (defined risk only)"
        ideas.append({
            "sector": sector, "bias": "Bearish / Avoid", "instrument": instrument, "structure": structure,
            "rationale": f"Sector rank #{int(r['rank'])} (weakest), 3M avg {r['avg_ret_63']}%, breadth {r['breadth_above_ema50']}% >50-EMA",
            "watchlist": "-",
        })
    return ideas
