"""Lightweight tests for indicator math. Run: python tests/test_indicators.py

Also works under pytest if installed.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from nifty_scanner import indicators as ta  # noqa: E402


def _series(n=300, seed=1):
    rng = np.random.default_rng(seed)
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0005, 0.015, n))))


def test_ema_tracks_sma_direction():
    s = pd.Series(np.arange(1, 101, dtype=float))
    e = ta.ema(s, 10)
    assert e.iloc[-1] < s.iloc[-1]          # EMA lags a rising line
    assert e.iloc[-1] > s.iloc[-11]         # but leads older SMA points


def test_rsi_bounds():
    s = _series()
    r = ta.rsi(s, 14).dropna()
    assert ((r >= 0) & (r <= 100)).all()


def test_rsi_all_up_is_100():
    s = pd.Series(np.arange(1, 60, dtype=float))
    r = ta.rsi(s, 14)
    assert r.iloc[-1] == 100.0


def test_macd_columns():
    s = _series()
    m = ta.macd(s)
    assert set(m.columns) == {"macd", "signal", "hist"}
    assert np.isclose((m["macd"] - m["signal"]).iloc[-1], m["hist"].iloc[-1])


def test_atr_positive():
    n = 200
    rng = np.random.default_rng(2)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, n)))
    high = close + np.abs(rng.normal(0, 1, n))
    low = close - np.abs(rng.normal(0, 1, n))
    a = ta.atr(high, low, close, 14).dropna()
    assert (a > 0).all()


def test_relative_strength():
    up = pd.Series(np.linspace(100, 150, 100))     # +50%
    flat = pd.Series(np.linspace(100, 100, 100))   # 0%
    rs = ta.relative_strength(up, flat, 63)
    assert rs > 1.0


def _run_all():
    fns = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)} indicator tests passed.")


if __name__ == "__main__":
    _run_all()
