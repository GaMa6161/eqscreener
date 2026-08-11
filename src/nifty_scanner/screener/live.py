"""Intraday live scanners powered by Dhan market quotes + option chain."""
from __future__ import annotations

import logging
from datetime import date

import numpy as np
import pandas as pd

from .. import config
from ..data import dhan as dhan_mod
from . import strikes as strike_mod

log = logging.getLogger(__name__)


def summarise_live_activity(quotes: pd.DataFrame, params: dict | None = None) -> dict:
    params = params or config.LIVE
    top_n = int(params.get("top_n", 15))
    empty = {
        "session_date": date.today().isoformat(),
        "breadth": {},
        "gainers": [],
        "losers": [],
        "volume_leaders": [],
        "unusual_volume": [],
        "turnover_leaders": [],
    }
    if quotes is None or quotes.empty:
        return empty

    q = quotes.copy()
    q["turnover_cr"] = (q["ltp"] * q["volume"] / 1e7).round(2)
    advances = int((q["chg_pct"] > 0).sum())
    declines = int((q["chg_pct"] < 0).sum())

    def _panel(df: pd.DataFrame) -> list[dict]:
        if df.empty:
            return []
        cols = ["symbol", "name", "sector", "ltp", "chg_pct", "from_open_pct",
                "volume", "turnover_cr", "high", "low", "open", "is_fno"]
        out = df[[c for c in cols if c in df.columns]].copy()
        out = out.rename(columns={"ltp": "close"})
        out["vol_mult"] = None
        return out.astype(object).where(pd.notna(out), None).to_dict("records")

    liquid = q[q["volume"] > 0].copy() if "volume" in q else q
    return {
        "session_date": date.today().isoformat(),
        "breadth": {
            "scanned": int(len(q)),
            "advances": advances,
            "declines": declines,
            "unchanged": int(len(q) - advances - declines),
            "advance_decline_ratio": round(advances / declines, 2) if declines else None,
            "avg_chg_pct": round(float(q["chg_pct"].mean(skipna=True)), 2) if q["chg_pct"].notna().any() else None,
            "total_turnover_cr": round(float((q["ltp"] * q["volume"]).sum() / 1e7), 1),
        },
        "gainers": _panel(liquid.sort_values("chg_pct", ascending=False).head(top_n)),
        "losers": _panel(liquid.sort_values("chg_pct", ascending=True).head(top_n)),
        "volume_leaders": _panel(liquid.sort_values("volume", ascending=False).head(top_n)),
        "unusual_volume": _panel(liquid.sort_values("from_open_pct", key=lambda s: s.abs(), ascending=False).head(top_n)),
        "turnover_leaders": _panel(
            liquid.assign(_t=liquid["ltp"] * liquid["volume"]).sort_values("_t", ascending=False).head(top_n)
        ),
    }


def rank_live_momentum(quotes: pd.DataFrame, params: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rank intraday momentum from day-change + move-from-open + volume."""
    params = params or config.LIVE
    top_n = int(params.get("max_results", 15))
    if quotes is None or quotes.empty:
        empty = pd.DataFrame()
        return empty, empty

    q = quotes.copy()
    q["turnover_cr"] = (q["ltp"] * q["volume"] / 1e7).round(2)
    chg = q["chg_pct"].fillna(0.0)
    fro = q["from_open_pct"].fillna(0.0)
    vol_score = np.log1p(q["volume"].fillna(0).astype(float))
    q["bull_score"] = (1.2 * chg + 1.5 * fro + 0.15 * vol_score).round(2)
    q["bear_score"] = (-1.2 * chg - 1.5 * fro + 0.15 * vol_score).round(2)
    q["direction"] = np.where(q["bull_score"] >= q["bear_score"], "Bullish", "Bearish")
    q["score"] = np.where(q["direction"] == "Bullish", q["bull_score"], q["bear_score"])
    q["close"] = q["ltp"]
    q["ret_5"] = q["from_open_pct"]
    q["ret_10"] = q["chg_pct"]
    q["ret_21"] = q["chg_pct"]
    q["rsi"] = np.nan
    q["vol_mult"] = np.nan
    q["atr"] = ((q["high"] - q["low"]).clip(lower=0) / 2.0).round(2)
    q["rs"] = np.nan

    bull = q[q["direction"] == "Bullish"].sort_values("bull_score", ascending=False).head(top_n).reset_index(drop=True)
    bear = q[q["direction"] == "Bearish"].sort_values("bear_score", ascending=False).head(top_n).reset_index(drop=True)
    return bull, bear


def _nearest_expiry(expiries: list[str], prefer_weekly: bool = True) -> str | None:
    if not expiries:
        return None
    today = date.today().isoformat()
    future = sorted(e for e in expiries if e >= today)
    return (future or sorted(expiries))[0]


def pick_from_chain(
    chain: dict,
    direction: str,
    *,
    symbol: str,
    name: str = "",
    sector: str = "",
    expiry: str,
    instrument_kind: str = "index",
) -> dict | None:
    """Choose a liquid debit-spread from a live Dhan option chain."""
    spot = float(chain.get("last_price") or 0)
    oc = chain.get("oc") or {}
    if not spot or not oc:
        return None

    strikes = sorted(float(k) for k in oc.keys())
    if not strikes:
        return None
    atm = min(strikes, key=lambda s: abs(s - spot))
    bullish = direction.lower().startswith("bull")

    def _leg(strike: float, side: str) -> dict:
        node = oc.get(f"{strike:.6f}") or oc.get(str(strike)) or {}
        # keys sometimes "25650.000000"
        if not node:
            for k, v in oc.items():
                if abs(float(k) - strike) < 1e-6:
                    node = v
                    break
        return (node or {}).get(side.lower()) or (node or {}).get(side.upper()) or {}

    def _score_leg(leg: dict) -> float:
        if not leg:
            return -1.0
        oi = float(leg.get("oi") or 0)
        vol = float(leg.get("volume") or 0)
        bid = float(leg.get("top_bid_price") or 0)
        ask = float(leg.get("top_ask_price") or 0)
        if bid <= 0 or ask <= 0:
            return oi * 0.01
        spread_pct = (ask - bid) / max((ask + bid) / 2.0, 1e-6)
        return oi + 0.2 * vol - 1000.0 * max(spread_pct, 0.0)

    if bullish:
        buy_strike = atm
        # sell further OTM call
        otm = [s for s in strikes if s > atm]
        sell_strike = otm[min(1, len(otm) - 1)] if otm else atm
        # prefer highest-OI near ATM call as long leg if ATM thin
        candidates = [s for s in strikes if abs(s - atm) <= (strikes[1] - strikes[0]) * 2] if len(strikes) > 1 else [atm]
        buy_strike = max(candidates, key=lambda s: _score_leg(_leg(s, "ce")))
        higher = [s for s in strikes if s > buy_strike]
        sell_strike = higher[min(1, len(higher) - 1)] if higher else buy_strike
        buy_leg, sell_leg = _leg(buy_strike, "ce"), _leg(sell_strike, "ce")
        buy_opt, sell_opt = "CE", "CE"
        structure = "Bull call debit spread"
    else:
        buy_strike = atm
        candidates = [s for s in strikes if abs(s - atm) <= (strikes[1] - strikes[0]) * 2] if len(strikes) > 1 else [atm]
        buy_strike = max(candidates, key=lambda s: _score_leg(_leg(s, "pe")))
        lower = [s for s in strikes if s < buy_strike]
        sell_strike = lower[::-1][min(1, len(lower) - 1)] if lower else buy_strike
        buy_leg, sell_leg = _leg(buy_strike, "pe"), _leg(sell_strike, "pe")
        buy_opt, sell_opt = "PE", "PE"
        structure = "Bear put debit spread"

    def _px(leg: dict) -> float | None:
        if not leg:
            return None
        ask = float(leg.get("top_ask_price") or 0)
        ltp = float(leg.get("last_price") or 0)
        return ask or ltp or None

    buy_px, sell_px = _px(buy_leg), _px(sell_leg)
    debit = None
    if buy_px is not None and sell_px is not None:
        debit = round(max(buy_px - sell_px, 0.0), 2)

    return {
        "symbol": symbol,
        "name": name or symbol,
        "sector": sector,
        "spot": round(spot, 2),
        "atr": None,
        "direction": "Bullish" if bullish else "Bearish",
        "score": None,
        "rationale": (
            f"Live chain {expiry}: ATM≈{atm}, "
            f"buy OI={buy_leg.get('oi')} IV={round(float(buy_leg.get('implied_volatility') or 0), 1)}, "
            f"sell OI={sell_leg.get('oi')}"
        ),
        "expiry": expiry,
        "expiry_label": expiry,
        "instrument_kind": instrument_kind,
        "structure": structure,
        "bias": "Bullish" if bullish else "Bearish",
        "buy_option": buy_opt,
        "sell_option": sell_opt,
        "buy_strike": buy_strike,
        "sell_strike": sell_strike,
        "atm_strike": atm,
        "strike_step": abs(strikes[1] - strikes[0]) if len(strikes) > 1 else None,
        "width": abs(sell_strike - buy_strike),
        "buy_ltp": buy_px,
        "sell_ltp": sell_px,
        "est_debit": debit,
        "buy_oi": buy_leg.get("oi"),
        "sell_oi": sell_leg.get("oi"),
        "buy_iv": round(float(buy_leg.get("implied_volatility") or 0), 2) if buy_leg else None,
        "notes": "Strikes chosen from live Dhan option chain by OI/liquidity.",
        "entry_summary": (
            f"{structure}: BUY {buy_strike} {buy_opt}"
            + (f" @ {buy_px}" if buy_px is not None else "")
            + f" / SELL {sell_strike} {sell_opt}"
            + (f" @ {sell_px}" if sell_px is not None else "")
            + f" · {expiry}"
            + (f" · est debit {debit}" if debit is not None else "")
        ),
        "otm_calls": [s for s in strikes if s > atm][:3],
        "otm_puts": [s for s in strikes if s < atm][-3:][::-1],
        "suggested_lots_scaffold": 1,
        "assumed_debit_scaffold": debit,
    }


def fetch_live_quotes(client: dhan_mod.DhanClient, universe: pd.DataFrame) -> pd.DataFrame:
    mapped = dhan_mod.map_universe(universe)
    mapped = mapped.dropna(subset=["security_id"])
    ids = [int(x) for x in mapped["security_id"].tolist()]
    id_to_meta = {
        int(r.security_id): {
            "symbol": r.symbol,
            "name": getattr(r, "name", r.symbol),
            "sector": getattr(r, "sector", ""),
            "is_fno": bool(getattr(r, "is_fno", False)),
        }
        for r in mapped.itertuples(index=False)
    }
    # batch <= 1000
    frames = []
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        raw = client.quote(chunk, index_ids=[dhan_mod.INDEX_IDS["NIFTY"], dhan_mod.INDEX_IDS["BANKNIFTY"]])
        frames.append(dhan_mod.flatten_quotes(raw, id_to_meta))
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    # drop index rows from equity ranking (keep separately if needed)
    return out[out["segment"] == "NSE_EQ"].reset_index(drop=True) if "segment" in out else out


def build_live_strike_ideas(
    client: dhan_mod.DhanClient,
    bull: pd.DataFrame,
    bear: pd.DataFrame,
    mapped_universe: pd.DataFrame,
    params: dict | None = None,
) -> list[dict]:
    """Option ideas for top F&O names + NIFTY/BANKNIFTY from live chains."""
    params = params or config.LIVE
    per_side = int(params.get("chain_per_side", 3))
    ideas: list[dict] = []

    # Index ideas first (always liquid)
    for sym, sid, direction in (
        ("NIFTY", dhan_mod.INDEX_IDS["NIFTY"], "Bullish" if _index_bias(bull, bear) >= 0 else "Bearish"),
        ("BANKNIFTY", dhan_mod.INDEX_IDS["BANKNIFTY"], "Bullish" if _index_bias(bull, bear) >= 0 else "Bearish"),
    ):
        try:
            expiries = client.expiry_list(sid, "IDX_I")
            expiry = _nearest_expiry(expiries)
            if not expiry:
                continue
            chain = client.option_chain(sid, expiry, "IDX_I")
            idea = pick_from_chain(chain, direction, symbol=sym, name=sym, sector="Index",
                                   expiry=expiry, instrument_kind="index")
            if idea:
                ideas.append(idea)
        except Exception as exc:
            log.warning("index chain %s failed: %s", sym, exc)

    # Stock F&O from momentum lists
    sym_to_id = dict(zip(mapped_universe["symbol"], mapped_universe["security_id"]))
    if "is_fno" in mapped_universe.columns:
        fno = set(mapped_universe.loc[mapped_universe["is_fno"] == True, "symbol"])  # noqa: E712
    else:
        fno = set()

    picked = 0
    for df, direction in ((bull, "Bullish"), (bear, "Bearish")):
        if df is None or df.empty:
            continue
        for _, r in df.iterrows():
            if picked >= per_side * 2:
                break
            sym = str(r["symbol"])
            if sym not in fno:
                continue
            sid = sym_to_id.get(sym)
            if sid is None or (isinstance(sid, float) and np.isnan(sid)):
                continue
            try:
                expiries = client.expiry_list(int(sid), "NSE_EQ")
                expiry = _nearest_expiry(expiries)
                if not expiry:
                    continue
                chain = client.option_chain(int(sid), expiry, "NSE_EQ")
                idea = pick_from_chain(
                    chain, direction,
                    symbol=sym, name=str(r.get("name", sym)), sector=str(r.get("sector", "")),
                    expiry=expiry, instrument_kind="equity",
                )
                if idea:
                    idea["score"] = None if pd.isna(r.get("score")) else float(r["score"])
                    ideas.append(idea)
                    picked += 1
            except Exception as exc:
                log.warning("stock chain %s failed: %s", sym, exc)

    # Fallback scaffold if chains unavailable
    if len(ideas) <= 2 and bull is not None and not bull.empty:
        ideas.extend(strike_mod.suggestions_from_momentum(bull.head(3), bear.head(3) if bear is not None else bull.head(0)))
    return ideas


def _index_bias(bull: pd.DataFrame, bear: pd.DataFrame) -> float:
    b = float(bull["chg_pct"].mean()) if bull is not None and not bull.empty else 0.0
    r = float(bear["chg_pct"].mean()) if bear is not None and not bear.empty else 0.0
    return b + r  # rough breadth tilt


def demo_live_quotes(universe: pd.DataFrame, seed: int = 11) -> pd.DataFrame:
    """Synthetic intraday quotes for offline dashboard testing."""
    rng = np.random.default_rng(seed)
    rows = []
    for r in universe.itertuples(index=False):
        prev = float(rng.uniform(200, 3500))
        chg = float(rng.normal(0.2, 1.8))
        ltp = prev * (1 + chg / 100)
        open_ = prev * (1 + float(rng.normal(0, 0.4)) / 100)
        high = max(open_, ltp) * (1 + abs(float(rng.normal(0, 0.3))) / 100)
        low = min(open_, ltp) * (1 - abs(float(rng.normal(0, 0.3))) / 100)
        vol = int(rng.uniform(2e5, 8e6))
        rows.append({
            "security_id": 0,
            "segment": "NSE_EQ",
            "symbol": r.symbol,
            "name": getattr(r, "name", r.symbol),
            "sector": getattr(r, "sector", ""),
            "is_fno": True,
            "ltp": round(ltp, 2),
            "open": round(open_, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "prev_close": round(prev, 2),
            "chg_pct": round(chg, 2),
            "from_open_pct": round((ltp / open_ - 1) * 100, 2),
            "volume": vol,
            "avg_price": round((high + low) / 2, 2),
            "oi": 0,
            "net_change": round(ltp - prev, 2),
            "close": round(ltp, 2),
        })
    return pd.DataFrame(rows)
