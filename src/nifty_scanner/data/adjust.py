"""Back-adjust historical prices for splits and bonus issues.

NSE bhavcopy publishes raw traded prices: on the ex-date of a 5:1 split the
close simply drops to a fifth with no flag. Left alone that discontinuity poisons
every derived series - EMAs average pre- and post-split prices together, the
52-week high stays at the old level for a year, and 63-day returns read as -80%.
Affected names then fail the trend gates for reasons that never happened.

Detection leans on NSE price bands: a genuine single-session move beyond ~35% is
effectively impossible, so a gap that large whose ratio lands on a simple
fraction (2:1, 5:1, 10:1, 3:2 ...) is a corporate action rather than a crash.

Prices are adjusted, never rewritten in place: the Parquet store keeps raw
bhavcopy values and adjustment happens on read, so re-running can never
double-adjust and newly detected events fix history retroactively.
"""
from __future__ import annotations

import logging
from fractions import Fraction

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# A move larger than this is beyond any NSE circuit band -> corporate action.
MIN_GAP = 0.35
# The stock also trades on its ex-date, so the observed ratio carries a few
# percent of genuine price move on top of the corporate-action factor.
RATIO_TOL = 0.06
MAX_DENOMINATOR = 5
MAX_NUMERATOR = 20

PRICE_COLS = ("open", "high", "low", "close")


def _candidates() -> list[Fraction]:
    """Plausible split/bonus factors: 2:1, 5:1, 3:2 (1:2 bonus), 4:3 and so on."""
    out = {
        Fraction(n, d)
        for d in range(1, MAX_DENOMINATOR + 1)
        for n in range(1, MAX_NUMERATOR + 1)
        if 1.2 <= n / d <= MAX_NUMERATOR
    }
    return sorted(out, key=lambda f: (f.denominator, f.numerator))


_CANDIDATES = _candidates()


def _clean_ratio(observed: float) -> tuple[float, bool]:
    """Resolve `observed` to an adjustment factor and whether it is a clean split.

    Prefers the *simplest* fraction within tolerance rather than the nearest one:
    a 10:1 split that also moved 4% on its ex-date shows up as 9.59, which is
    numerically closer to 19/2 but is obviously 10/1.

    Falls back to the observed ratio itself when nothing clean fits - demergers
    separate value by an arbitrary amount. That keeps the series continuous,
    which is what the indicators need, and the event is flagged as approximate.
    """
    if not np.isfinite(observed) or observed <= 0:
        return 1.0, False
    for frac in _CANDIDATES:  # already ordered simplest-first
        cand = float(frac)
        if abs(observed - cand) / cand <= RATIO_TOL:
            return cand, True
    return float(observed), False


def detect_events(df: pd.DataFrame) -> pd.DataFrame:
    """Find probable split/bonus ex-dates.

    Returns columns: symbol, date (the ex-date), ratio. `ratio` > 1 means the
    price was divided by that factor (a 5:1 split gives 5.0).
    """
    if df.empty:
        return pd.DataFrame(columns=["symbol", "date", "ratio"])

    work = df[["symbol", "date", "close"]].sort_values(["symbol", "date"])
    prev = work.groupby("symbol")["close"].shift(1)
    change = work["close"] / prev - 1.0
    suspect = work[(change.abs() > MIN_GAP) & prev.notna()].copy()
    suspect["observed"] = prev[suspect.index] / suspect["close"]

    rows = []
    for _, r in suspect.iterrows():
        ratio, exact = _clean_ratio(float(r["observed"]))
        if abs(ratio - 1.0) < 0.1:
            continue
        rows.append({"symbol": r["symbol"], "date": r["date"], "ratio": ratio, "exact": exact})
    return pd.DataFrame(rows, columns=["symbol", "date", "ratio", "exact"])


def adjust_history(df: pd.DataFrame, events: pd.DataFrame | None = None) -> pd.DataFrame:
    """Return `df` with prices back-adjusted for every detected corporate action.

    Bars before an ex-date have prices divided by the ratio and volume multiplied
    by it, so turnover (price x volume) is preserved and the series is continuous.
    """
    if df.empty:
        return df
    events = detect_events(df) if events is None else events
    if events.empty:
        return df

    out = df.sort_values(["symbol", "date"]).copy()
    # Volume arrives as int64; scaling it produces floats.
    if "volume" in out.columns:
        out["volume"] = out["volume"].astype("float64")
    for symbol, group in events.groupby("symbol"):
        mask_sym = out["symbol"] == symbol
        # Walk events newest-first so factors compound for multiple splits.
        for _, ev in group.sort_values("date", ascending=False).iterrows():
            older = mask_sym & (out["date"] < ev["date"])
            if not older.any():
                continue
            for col in PRICE_COLS:
                if col in out.columns:
                    out.loc[older, col] = out.loc[older, col] / ev["ratio"]
            if "volume" in out.columns:
                out.loc[older, "volume"] = out.loc[older, "volume"] * ev["ratio"]

    log.info(
        "corporate actions: adjusted %d event(s) across %d symbol(s)",
        len(events), events["symbol"].nunique(),
    )
    return out.reset_index(drop=True)


def summarise(events: pd.DataFrame) -> list[dict]:
    """Compact records for the dashboard/email data-health panel."""
    if events.empty:
        return []
    recs = events.sort_values("date", ascending=False).head(12)
    return [
        {
            "symbol": r["symbol"],
            "date": str(pd.to_datetime(r["date"]).date()),
            "ratio": f"{r['ratio']:g}:1" if r["ratio"] > 1 else f"1:{1 / r['ratio']:g}",
            "exact": bool(r.get("exact", True)),
        }
        for _, r in recs.iterrows()
    ]
