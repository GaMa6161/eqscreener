"""Parquet OHLCV history store, appended daily from the NSE bhavcopy.

The store is a single long-format Parquet file (columns: date, symbol, open,
high, low, close, volume, turnover). It is committed to the repo so history
accumulates across GitHub Actions runs. A one-time `backfill` bootstraps enough
history for EMA200 / 52-week / relative-strength calculations.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .. import config
from . import bhavcopy

log = logging.getLogger(__name__)

COLUMNS = ["date", "symbol", "open", "high", "low", "close", "volume", "turnover"]


def load() -> pd.DataFrame:
    path = config.HISTORY_PATH
    if not path.exists():
        return pd.DataFrame(columns=COLUMNS)
    df = pd.read_parquet(path)
    df["date"] = pd.to_datetime(df["date"])
    return df


def save(df: pd.DataFrame) -> None:
    config.ensure_dirs()
    df = df.sort_values(["symbol", "date"]).reset_index(drop=True)
    df.to_parquet(config.HISTORY_PATH, index=False)


def latest_date(df: pd.DataFrame):
    return None if df.empty else df["date"].max()


def _existing_dates(df: pd.DataFrame) -> set:
    if df.empty:
        return set()
    return set(pd.to_datetime(df["date"]).dt.date.unique())


def _collect(start: date, end: date, keep: set[str], existing: set) -> list[pd.DataFrame]:
    """Fetch bhavcopies for trading days in [start, end] not already present."""
    frames: list[pd.DataFrame] = []
    allowed = config.UNIVERSE["allowed_series"]
    d = start
    while d <= end:
        if d.weekday() < 5 and d not in existing:  # skip weekends + known dates
            bc = bhavcopy.fetch_cm_bhavcopy(d, allowed_series=allowed)
            if bc is not None and not bc.empty:
                sub = bc[bc["symbol"].isin(keep)]
                if not sub.empty:
                    frames.append(
                        sub[["date", "symbol", "open", "high", "low", "close", "volume", "turnover"]]
                    )
                    log.info("bhavcopy %s: +%d rows", d.isoformat(), len(sub))
        d += timedelta(days=1)
    return frames


def _merge(existing_df: pd.DataFrame, frames: list[pd.DataFrame]) -> pd.DataFrame:
    if not frames:
        return existing_df
    new = pd.concat(frames, ignore_index=True)
    combined = pd.concat([existing_df, new], ignore_index=True) if not existing_df.empty else new
    combined = combined.drop_duplicates(["symbol", "date"], keep="last")
    return combined.sort_values(["symbol", "date"]).reset_index(drop=True)


def backfill(universe: pd.DataFrame, days: int | None = None) -> pd.DataFrame:
    """Bootstrap ~`days` calendar days of history for the universe."""
    days = days or config.DATA["backfill_days"]
    keep = set(universe["symbol"])
    existing = load()
    existing_dates = _existing_dates(existing)
    end = date.today()
    start = end - timedelta(days=days)
    frames = _collect(start, end, keep, existing_dates)
    merged = _merge(existing, frames)
    save(merged)
    log.info("backfill complete: %d rows, %d symbols", len(merged), merged["symbol"].nunique())
    return merged


def update(universe: pd.DataFrame, days_back: int = 7) -> pd.DataFrame:
    """Append the latest available trading day(s) (fills small gaps too)."""
    keep = set(universe["symbol"])
    existing = load()
    existing_dates = _existing_dates(existing)
    end = date.today()
    start = end - timedelta(days=days_back)
    frames = _collect(start, end, keep, existing_dates)
    merged = _merge(existing, frames)
    if frames:
        save(merged)
    last = latest_date(merged)
    log.info("update complete: latest=%s rows=%d", None if last is None else last.date(), len(merged))
    return merged


def equal_weight_benchmark(df: pd.DataFrame) -> pd.Series:
    """Synthetic equal-weight index level from the universe (self-contained RS
    benchmark that never depends on an external feed)."""
    if df.empty:
        return pd.Series(dtype=float)
    wide = df.pivot_table(index="date", columns="symbol", values="close", aggfunc="last").sort_index()
    wide = wide.loc[:, wide.notna().sum() >= 60]
    if wide.empty:
        return pd.Series(dtype=float)
    first = wide.apply(lambda s: s.loc[s.first_valid_index()] if s.first_valid_index() is not None else np.nan)
    norm = wide.divide(first, axis=1)
    return (norm.mean(axis=1, skipna=True) * 1000.0).dropna()


# --- Offline demo data ------------------------------------------------------
def generate_demo(universe: pd.DataFrame, days: int = 400, seed: int = 7,
                  sector_bias: dict[str, float] | None = None) -> pd.DataFrame:
    """Deterministic synthetic history (geometric brownian motion) so the whole
    pipeline runs offline. `sector_bias` adds per-sector drift so rotation and the
    screener produce meaningful, reproducible output."""
    rng = np.random.default_rng(seed)
    sector_bias = sector_bias or {}
    idx = pd.bdate_range(end=date.today() - timedelta(days=1), periods=days)
    frames: list[pd.DataFrame] = []
    for row in universe.itertuples(index=False):
        drift = 0.0004 + sector_bias.get(getattr(row, "sector", ""), 0.0)
        start = float(rng.uniform(150, 3500))
        shocks = rng.normal(drift, 0.018, size=days)
        close = start * np.exp(np.cumsum(shocks))
        intraday = np.abs(rng.normal(0.0, 0.01, size=days)) + 0.003
        high = close * (1 + intraday)
        low = close * (1 - intraday)
        open_ = close * (1 + rng.normal(0.0, 0.006, size=days))
        base_vol = rng.uniform(3e5, 5e6)
        volume = (base_vol * (1 + np.abs(rng.normal(0, 0.4, size=days)))).astype(np.int64)
        volume[-1] = int(volume[-1] * rng.uniform(1.3, 2.6))  # elevated scan-day volume
        frames.append(pd.DataFrame({
            "date": idx,
            "symbol": row.symbol,
            "open": open_, "high": high, "low": low, "close": close,
            "volume": volume, "turnover": close * volume,
        }))
    return pd.concat(frames, ignore_index=True)
