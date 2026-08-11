"""Candidate streaks and a paper-trade ledger.

Two gaps this closes.

First, a name that has passed the gates for ten sessions running looks identical
in today's email to one that broke out this morning. Streaks tag each candidate
NEW / day N so the digest distinguishes a fresh signal from a standing one.

Second, the screener used to emit entries and forget them: there was no record
of what actually happened next. Every top candidate is opened as a paper trade
and marked to market each session until it hits its stop, a target, or the
holding limit - which turns the screen into something with a measurable hit rate
instead of a daily opinion.

State lives in `data/state/tracker.json`, committed alongside the history store
so the ledger survives across CI runs.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime

import pandas as pd

from . import config

log = logging.getLogger(__name__)

STATE_DIR = config.DATA_DIR / "state"
STATE_PATH = STATE_DIR / "tracker.json"

OPEN = "open"


def _today_str(d) -> str:
    if isinstance(d, str):
        return d[:10]
    if isinstance(d, (datetime, pd.Timestamp)):
        return d.date().isoformat()
    return date.fromisoformat(str(d)[:10]).isoformat() if d else ""


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"candidates": {}, "positions": []}
    try:
        state = json.loads(STATE_PATH.read_text())
    except Exception as exc:
        log.warning("tracker state unreadable (%s); starting fresh", exc)
        return {"candidates": {}, "positions": []}
    state.setdefault("candidates", {})
    state.setdefault("positions", [])
    return state


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True, default=str))


# --- streaks ---------------------------------------------------------------

def apply_streaks(candidates: pd.DataFrame, state: dict, session: str) -> pd.DataFrame:
    """Tag each candidate with how many consecutive sessions it has qualified."""
    if candidates.empty:
        return candidates
    seen = state.setdefault("candidates", {})
    streaks, flags = [], []
    for symbol in candidates["symbol"]:
        rec = seen.get(symbol)
        if rec and rec.get("last_seen") != session:
            days = int(rec.get("days", 1)) + 1
        elif rec:
            days = int(rec.get("days", 1))
        else:
            days = 1
        seen[symbol] = {"first_seen": (rec or {}).get("first_seen", session),
                        "last_seen": session, "days": days}
        streaks.append(days)
        flags.append("NEW" if days == 1 else f"day {days}")

    # Drop names that no longer qualify so the streak restarts cleanly.
    current = set(candidates["symbol"])
    for symbol in [s for s, r in seen.items() if r.get("last_seen") != session and s not in current]:
        seen.pop(symbol, None)

    out = candidates.copy()
    out["streak_days"] = streaks
    out["streak_label"] = flags
    return out


# --- paper ledger ----------------------------------------------------------

def open_positions(candidates: pd.DataFrame, state: dict, session: str, params: dict) -> int:
    """Open paper trades for the best untracked candidates. Returns how many."""
    top_n = params.get("auto_open_top_n", 0)
    if not top_n or candidates.empty:
        return 0
    live = {p["symbol"] for p in state["positions"] if p["status"] == OPEN}
    opened = 0
    for _, row in candidates.head(top_n).iterrows():
        symbol = row["symbol"]
        if symbol in live:
            continue
        targets = list(row.get("targets") or [])
        state["positions"].append({
            "symbol": symbol,
            "name": row.get("name", symbol),
            "sector": row.get("sector", ""),
            "entry_date": session,
            "entry": float(row["close"]),
            "stop": float(row["stop"]) if pd.notna(row.get("stop")) else None,
            "targets": [float(t) for t in targets],
            "qty": int(row.get("qty") or 0),
            "status": OPEN,
            "exit_date": None,
            "exit": None,
            "r_multiple": None,
            "days_held": 0,
        })
        opened += 1
    return opened


def mark_to_market(history: pd.DataFrame, state: dict, session: str, params: dict) -> dict:
    """Walk open paper trades forward against the latest bars.

    Resolution order within a session is deliberately pessimistic: if a bar's
    range covers both the stop and a target, the stop is assumed to have hit
    first. Daily bars cannot tell you which came first, and the optimistic
    reading is how paper records end up flattering themselves.
    """
    if history.empty:
        return {"closed": 0, "open": 0}

    hist = history.sort_values(["symbol", "date"])
    max_hold = params.get("max_hold_days", 30)
    closed = 0

    for pos in state["positions"]:
        if pos["status"] != OPEN:
            continue
        bars = hist[(hist["symbol"] == pos["symbol"])
                    & (hist["date"].astype(str) > pos["entry_date"])]
        if bars.empty:
            continue

        stop = pos.get("stop")
        targets = pos.get("targets") or []
        risk = (pos["entry"] - stop) if stop else None

        for held, (_, bar) in enumerate(bars.iterrows(), start=1):
            if stop is not None and float(bar["low"]) <= stop:
                _close(pos, stop, bar["date"], "stopped", risk)
                pos["days_held"] = held
                closed += 1
                break
            hit = [t for t in targets if float(bar["high"]) >= t]
            if hit:
                _close(pos, max(hit), bar["date"], f"target{len(hit)}", risk)
                pos["days_held"] = held
                closed += 1
                break
            if held >= max_hold:
                _close(pos, float(bar["close"]), bar["date"], "timeout", risk)
                pos["days_held"] = held
                closed += 1
                break
        else:
            pos["days_held"] = int(len(bars))
            if risk:
                last_close = float(bars.iloc[-1]["close"])
                pos["r_multiple"] = round((last_close - pos["entry"]) / risk, 2)

    still_open = sum(1 for p in state["positions"] if p["status"] == OPEN)
    return {"closed": closed, "open": still_open}


def _close(pos: dict, price: float, when, status: str, risk: float | None) -> None:
    pos["status"] = status
    pos["exit"] = round(float(price), 2)
    pos["exit_date"] = _today_str(when)
    pos["r_multiple"] = round((price - pos["entry"]) / risk, 2) if risk else None


def summarise(state: dict, limit: int = 10) -> dict:
    """Ledger stats plus the open book, for the digest and dashboard."""
    positions = state.get("positions", [])
    closed = [p for p in positions if p["status"] != OPEN and p.get("r_multiple") is not None]
    live = [p for p in positions if p["status"] == OPEN]

    wins = [p for p in closed if p["r_multiple"] > 0]
    r_values = [p["r_multiple"] for p in closed]
    return {
        "open": sorted(live, key=lambda p: p.get("r_multiple") or 0, reverse=True)[:limit],
        "recently_closed": sorted(closed, key=lambda p: p.get("exit_date") or "", reverse=True)[:limit],
        "stats": {
            "open_count": len(live),
            "closed_count": len(closed),
            "win_rate": round(100.0 * len(wins) / len(closed), 1) if closed else None,
            "avg_r": round(sum(r_values) / len(r_values), 2) if r_values else None,
            "total_r": round(sum(r_values), 2) if r_values else None,
        },
    }
