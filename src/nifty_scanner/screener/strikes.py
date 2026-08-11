"""Suggest defined-risk option strikes from underlying price action.

No live option chain is required: strikes are snapped to typical NSE equity /
index intervals around spot. Use as a starting scaffold in your broker —
confirm OI, IV, bid/ask and exact available strikes before entering.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .. import config


def strike_step(price: float, kind: str = "equity") -> float:
    """Typical NSE strike interval by underlying price band."""
    p = abs(float(price))
    if kind == "index":
        # Nifty / Bank Nifty style steps (approximate; exchange can change).
        if p < 10000:
            return 50.0
        return 100.0
    if p < 50:
        return 2.5
    if p < 250:
        return 5.0
    if p < 500:
        return 10.0
    if p < 1000:
        return 10.0
    if p < 2500:
        return 25.0
    if p < 5000:
        return 50.0
    if p < 10000:
        return 100.0
    return 100.0


def round_strike(price: float, step: float | None = None, kind: str = "equity") -> float:
    step = step or strike_step(price, kind=kind)
    if step <= 0:
        return round(price, 2)
    return round(round(price / step) * step, 2 if step < 1 else 0 if step >= 1 and float(step).is_integer() else 2)


def next_thursday(from_date: date | None = None) -> date:
    """Weekly index options typically expire on Thursday (IST)."""
    d = from_date or date.today()
    # weekday: Mon=0 ... Thu=3
    days_ahead = (3 - d.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7  # next week if today is Thursday (avoid same-day theta crush default)
    return d + timedelta(days=days_ahead)


def next_monthly_expiry(from_date: date | None = None) -> date:
    """Last Thursday of the current month, or next month if already passed."""
    d = from_date or date.today()
    year, month = d.year, d.month

    def last_thursday(y: int, m: int) -> date:
        if m == 12:
            nxt = date(y + 1, 1, 1)
        else:
            nxt = date(y, m + 1, 1)
        last = nxt - timedelta(days=1)
        while last.weekday() != 3:
            last -= timedelta(days=1)
        return last

    exp = last_thursday(year, month)
    if d >= exp:
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
        exp = last_thursday(year, month)
    return exp


def _spread_for_bull(spot: float, atr: float | None, kind: str, width_steps: int) -> dict:
    step = strike_step(spot, kind=kind)
    atm = round_strike(spot, step, kind=kind)
    # Prefer slight OTM long call when already extended; else ATM.
    buy = atm if spot <= atm else round_strike(atm + step, step, kind=kind)
    # Width: prefer ATR-based width when available, else N steps.
    if atr and not (isinstance(atr, float) and math.isnan(atr)) and atr > 0:
        width = max(step, round(atr / step) * step)
        width = max(step, min(width, step * max(width_steps, 2)))
    else:
        width = step * width_steps
    sell = round_strike(buy + width, step, kind=kind)
    if sell <= buy:
        sell = round_strike(buy + step * width_steps, step, kind=kind)
    return {
        "structure": "Bull call debit spread",
        "bias": "Bullish",
        "buy_option": "CE",
        "sell_option": "CE",
        "buy_strike": buy,
        "sell_strike": sell,
        "atm_strike": atm,
        "strike_step": step,
        "width": round(sell - buy, 2),
        "notes": "Buy lower CE, sell higher CE. Max loss = net debit; max gain = width − debit.",
    }


def _spread_for_bear(spot: float, atr: float | None, kind: str, width_steps: int) -> dict:
    step = strike_step(spot, kind=kind)
    atm = round_strike(spot, step, kind=kind)
    buy = atm if spot >= atm else round_strike(atm - step, step, kind=kind)
    if atr and not (isinstance(atr, float) and math.isnan(atr)) and atr > 0:
        width = max(step, round(atr / step) * step)
        width = max(step, min(width, step * max(width_steps, 2)))
    else:
        width = step * width_steps
    sell = round_strike(buy - width, step, kind=kind)
    if sell >= buy:
        sell = round_strike(buy - step * width_steps, step, kind=kind)
    return {
        "structure": "Bear put debit spread",
        "bias": "Bearish",
        "buy_option": "PE",
        "sell_option": "PE",
        "buy_strike": buy,
        "sell_strike": sell,
        "atm_strike": atm,
        "strike_step": step,
        "width": round(buy - sell, 2),
        "notes": "Buy higher PE, sell lower PE. Max loss = net debit; max gain = width − debit.",
    }


def suggest_for_underlying(
    symbol: str,
    spot: float,
    direction: str,
    *,
    name: str = "",
    sector: str = "",
    atr: float | None = None,
    score: float | None = None,
    rationale: str = "",
    instrument_kind: str = "equity",
    width_steps: int | None = None,
    as_of: date | None = None,
) -> dict:
    """Build one strike suggestion dict for an underlying."""
    params = config.OPTIONS_STRIKES
    width_steps = width_steps or int(params.get("spread_width_steps", 2))
    direction = (direction or "Bullish").strip()
    bullish = direction.lower().startswith("bull")

    legs = (
        _spread_for_bull(spot, atr, instrument_kind, width_steps)
        if bullish
        else _spread_for_bear(spot, atr, instrument_kind, width_steps)
    )

    if instrument_kind == "index":
        expiry = next_thursday(as_of)
        expiry_label = f"Weekly {expiry.isoformat()} (Thu)"
    else:
        expiry = next_monthly_expiry(as_of)
        expiry_label = f"Monthly {expiry.isoformat()} (last Thu)"

    otm_calls = [
        round_strike(legs["atm_strike"] + i * legs["strike_step"], legs["strike_step"], instrument_kind)
        for i in (1, 2, 3)
    ]
    otm_puts = [
        round_strike(legs["atm_strike"] - i * legs["strike_step"], legs["strike_step"], instrument_kind)
        for i in (1, 2, 3)
    ]

    acct = config.account()
    # Rough sizing scaffold: risk budget vs assumed debit ≈ 30% of width (unknown premium).
    assumed_debit = max(legs["width"] * 0.30, legs["strike_step"] * 0.15)
    risk_budget = acct["capital"] * acct["risk_pct"] / 100.0
    # Without an F&O lot master, size is an educational scaffold only.
    suggested_lots = max(1, int(risk_budget // max(assumed_debit * 50, 1)))
    suggested_lots = min(suggested_lots, int(params.get("max_lots_scaffold", 5)))

    return {
        "symbol": symbol,
        "name": name or symbol,
        "sector": sector,
        "spot": round(float(spot), 2),
        "atr": None if atr is None or (isinstance(atr, float) and math.isnan(atr)) else round(float(atr), 2),
        "direction": "Bullish" if bullish else "Bearish",
        "score": score,
        "rationale": rationale,
        "expiry": expiry.isoformat(),
        "expiry_label": expiry_label,
        "instrument_kind": instrument_kind,
        "suggested_lots_scaffold": suggested_lots,
        "assumed_debit_scaffold": round(assumed_debit, 2),
        "otm_calls": otm_calls,
        "otm_puts": otm_puts,
        **legs,
        "entry_summary": (
            f"{legs['structure']}: BUY {legs['buy_strike']} {legs['buy_option']} / "
            f"SELL {legs['sell_strike']} {legs['sell_option']} · {expiry_label}"
        ),
    }


def suggestions_from_momentum(
    bullish: pd.DataFrame,
    bearish: pd.DataFrame,
    params: dict | None = None,
) -> list[dict]:
    """Strike ideas for top momentum names (equity options scaffold)."""
    params = params or config.OPTIONS_STRIKES
    per_side = int(params.get("per_side", 8))
    ideas: list[dict] = []

    for df, direction in ((bullish.head(per_side), "Bullish"), (bearish.head(per_side), "Bearish")):
        if df is None or df.empty:
            continue
        for _, r in df.iterrows():
            ideas.append(
                suggest_for_underlying(
                    symbol=str(r["symbol"]),
                    spot=float(r["close"]),
                    direction=direction,
                    name=str(r.get("name", r["symbol"])),
                    sector=str(r.get("sector", "")),
                    atr=None if pd.isna(r.get("atr")) else float(r["atr"]),
                    score=None if pd.isna(r.get("score")) else float(r["score"]),
                    rationale=(
                        f"Momentum {direction.lower()}: 5d {r.get('ret_5')}%, "
                        f"10d {r.get('ret_10')}%, RS {r.get('rs')}, vol× {r.get('vol_mult')}"
                    ),
                    instrument_kind="equity",
                )
            )
    return ideas


def _fetch_index_spots(instruments: list[str], yahoo_map: dict[str, str]) -> dict[str, float]:
    """Last close for sector indices via yfinance. Missing tickers are skipped."""
    import time

    wanted = {name: yahoo_map[name] for name in instruments if name in yahoo_map}
    if not wanted:
        return {}
    try:
        import yfinance as yf
    except Exception:
        return {}

    out: dict[str, float] = {}
    # Per-ticker history is more reliable than multi-ticker download for CNX indices.
    for i, (name, ticker) in enumerate(wanted.items()):
        if i:
            time.sleep(0.35)  # avoid Yahoo soft rate-limits
        try:
            hist = yf.Ticker(ticker).history(period="10d")
            if hist is None or hist.empty or "Close" not in hist.columns:
                continue
            closes = hist["Close"].dropna()
            if len(closes):
                out[name] = float(closes.iloc[-1])
        except Exception:
            continue
    return out


def suggestions_from_swing(
    candidates: pd.DataFrame,
    params: dict | None = None,
) -> list[dict]:
    """Strike ideas for top swing-screener names (bullish debit spreads)."""
    params = params or config.OPTIONS_STRIKES
    top_n = int(params.get("swing_top_n", 8))
    if candidates is None or candidates.empty:
        return []
    ideas: list[dict] = []
    for _, r in candidates.head(top_n).iterrows():
        if pd.isna(r.get("close")):
            continue
        ideas.append(
            suggest_for_underlying(
                symbol=str(r["symbol"]),
                spot=float(r["close"]),
                direction="Bullish",
                name=str(r.get("name", r["symbol"])),
                sector=str(r.get("sector", "")),
                atr=None if pd.isna(r.get("atr")) else float(r["atr"]),
                score=None if pd.isna(r.get("score")) else float(r["score"]),
                rationale=(
                    f"Swing candidate: score {r.get('score')}, RSI {r.get('rsi')}, "
                    f"RS {r.get('rs')}, setups {', '.join(r.get('setups') or []) or '-'}"
                ),
                instrument_kind="equity",
            )
        )
    return ideas


def suggestions_from_sectors(
    sector_ranking: pd.DataFrame,
    metrics: pd.DataFrame,
    params: dict | None = None,
) -> list[dict]:
    """Index-option strike scaffolds for leading / lagging sectors.

    Uses real Yahoo index closes when available. Skips an index rather than
    inventing a synthetic spot from constituent medians.
    """
    params = params or config.OPTIONS_STRIKES
    sector_cfg = config.SECTORS
    index_map: dict[str, str] = sector_cfg.get("index_option_sectors", {}) or {}
    yahoo_map: dict[str, str] = sector_cfg.get("index_yahoo_tickers", {}) or {}
    top_n = int(sector_cfg.get("top_n_leaders", 3))
    bottom_n = int(sector_cfg.get("bottom_n_laggards", 3))
    if sector_ranking is None or sector_ranking.empty:
        return []

    leaders = sector_ranking.head(top_n)
    laggards = sector_ranking.tail(bottom_n).iloc[::-1]
    instruments = []
    for frame in (leaders, laggards):
        for _, r in frame.iterrows():
            inst = index_map.get(r["sector"])
            if inst:
                instruments.append(inst)
    spots = _fetch_index_spots(instruments, yahoo_map)

    ideas: list[dict] = []

    for _, r in leaders.iterrows():
        sector = r["sector"]
        instrument = index_map.get(sector)
        if not instrument:
            continue
        spot = spots.get(instrument)
        if spot is None:
            continue
        ideas.append(
            suggest_for_underlying(
                symbol=instrument,
                spot=spot,
                direction="Bullish",
                name=instrument,
                sector=sector,
                atr=spot * 0.012,
                score=None if pd.isna(r.get("momentum_score")) else float(r["momentum_score"]),
                rationale=(
                    f"Sector leader #{int(r['rank'])}: 3M avg {r.get('avg_ret_63')}%, "
                    f"breadth {r.get('breadth_above_ema50')}% >50-EMA. "
                    f"Index spot from Yahoo last close — confirm live in broker."
                ),
                instrument_kind="index",
            )
        )

    for _, r in laggards.iterrows():
        sector = r["sector"]
        instrument = index_map.get(sector)
        if not instrument:
            continue
        spot = spots.get(instrument)
        if spot is None:
            continue
        ideas.append(
            suggest_for_underlying(
                symbol=instrument,
                spot=spot,
                direction="Bearish",
                name=instrument,
                sector=sector,
                atr=spot * 0.012,
                score=None if pd.isna(r.get("momentum_score")) else float(r["momentum_score"]),
                rationale=(
                    f"Sector laggard #{int(r['rank'])}: 3M avg {r.get('avg_ret_63')}%, "
                    f"breadth {r.get('breadth_above_ema50')}% >50-EMA. "
                    f"Index spot from Yahoo last close — confirm live in broker."
                ),
                instrument_kind="index",
            )
        )
    return ideas


def build_strike_book(
    bullish: pd.DataFrame,
    bearish: pd.DataFrame,
    sector_ranking: pd.DataFrame,
    metrics: pd.DataFrame,
    swing_candidates: pd.DataFrame | None = None,
) -> list[dict]:
    """Combined swing + momentum equity + sector-index strike suggestions."""
    swing = suggestions_from_swing(swing_candidates)
    equity = suggestions_from_momentum(bullish, bearish)
    index_ideas = suggestions_from_sectors(sector_ranking, metrics)

    # Deduplicate by symbol+bias, prefer swing (listed first).
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for idea in swing + equity + index_ideas:
        key = (str(idea.get("symbol", "")), str(idea.get("bias") or idea.get("direction") or ""))
        if not key[0] or key in seen:
            continue
        seen.add(key)
        out.append(idea)
    return out
