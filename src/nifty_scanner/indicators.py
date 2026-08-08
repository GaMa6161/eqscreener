"""Technical indicators on pandas Series (no TA-Lib / pandas-ta dependency).

All functions are vectorised and return values aligned to the input index.
RSI, ATR and ADX use Wilder's smoothing (the standard in most charting tools),
implemented as an EWM with alpha = 1/length.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(window=length, min_periods=length).mean()


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False, min_periods=length).mean()


def _wilder(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(alpha=1.0 / length, min_periods=length, adjust=False).mean()


def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    """Wilder's RSI (0-100)."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = _wilder(gain, length)
    avg_loss = _wilder(loss, length)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    # When avg_loss == 0 (pure uptrend) RSI is defined as 100.
    return out.where(avg_loss != 0.0, 100.0)


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    """MACD -> DataFrame with columns: macd, signal, hist."""
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    hist = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "hist": hist})


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    ranges = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    return _wilder(true_range(high, low, close), length)


def adx(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.DataFrame:
    """Average Directional Index -> DataFrame with columns: adx, plus_di, minus_di."""
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=high.index)

    atr_ = _wilder(true_range(high, low, close), length).replace(0.0, np.nan)
    plus_di = 100.0 * _wilder(plus_dm, length) / atr_
    minus_di = 100.0 * _wilder(minus_dm, length) / atr_
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    adx_ = _wilder(dx, length)
    return pd.DataFrame({"adx": adx_, "plus_di": plus_di, "minus_di": minus_di})


def rolling_high(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(window=length, min_periods=length).max()


def rolling_low(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(window=length, min_periods=length).min()


def pct_return(series: pd.Series, periods: int) -> pd.Series:
    """Simple return over N periods, as a fraction (0.05 == +5%)."""
    return series.pct_change(periods=periods, fill_method=None)


def relative_strength(close: pd.Series, benchmark: pd.Series, lookback: int) -> float:
    """Comparative strength (1+stock_ret)/(1+bench_ret) over `lookback`.

    >1 means the stock outperformed the benchmark. NaN if not enough data.
    """
    if len(close) <= lookback or benchmark is None or len(benchmark) <= lookback:
        return float("nan")
    stock_ret = close.iloc[-1] / close.iloc[-1 - lookback] - 1.0
    bench_ret = benchmark.iloc[-1] / benchmark.iloc[-1 - lookback] - 1.0
    return (1.0 + stock_ret) / (1.0 + bench_ret)
