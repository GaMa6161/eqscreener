"""Overnight / global market cues for the pre-market brief (optional).

Uses yfinance (indices, commodities, FX) when available; degrades gracefully to
empty/None values so a missing optional dependency never breaks the email.
"""
from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)


def fetch_cues(cues_map: dict[str, str]) -> list[dict]:
    """For each label -> Yahoo ticker, return last close and 1-day % change."""
    try:
        import yfinance as yf
    except Exception:
        log.warning("yfinance not installed; skipping global cues")
        return [{"label": k, "ticker": v, "last": None, "change_pct": None} for k, v in cues_map.items()]

    tickers = list(dict.fromkeys(cues_map.values()))
    out: list[dict] = []
    try:
        data = yf.download(tickers, period="7d", interval="1d", group_by="ticker",
                           auto_adjust=True, threads=True, progress=False)
    except Exception as exc:
        log.warning("cues download failed: %s", exc)
        data = None

    for label, ticker in cues_map.items():
        last = prev = None
        try:
            if data is not None:
                sub = data[ticker] if hasattr(data.columns, "get_level_values") and ticker in data.columns.get_level_values(0) else data
                closes = sub["Close"].dropna() if "Close" in sub else sub["close"].dropna()
                if len(closes) >= 2:
                    last, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
        except Exception:
            pass
        chg = (last / prev - 1.0) * 100.0 if (last and prev) else np.nan
        out.append({
            "label": label, "ticker": ticker,
            "last": round(last, 2) if last else None,
            "change_pct": round(chg, 2) if last and prev else None,
        })
    return out


def demo_cues(cues_map: dict[str, str], seed: int = 3) -> list[dict]:
    rng = np.random.default_rng(seed)
    return [
        {"label": label, "ticker": ticker,
         "last": round(float(rng.uniform(80, 25000)), 2),
         "change_pct": round(float(rng.normal(0.1, 0.9)), 2)}
        for label, ticker in cues_map.items()
    ]
