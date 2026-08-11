"""Optional earnings calendar, used to keep swings out of results-day coin flips.

There is no free, reliable bulk earnings feed for the Nifty 500, so this reads a
file you maintain yourself: `data/earnings.csv` with `symbol,date` columns. When
the file is absent the screener simply runs without the blackout gate, so this
is additive - nothing breaks if you never create it.
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from .. import config

log = logging.getLogger(__name__)

EARNINGS_PATH = config.DATA_DIR / "earnings.csv"


def load(as_of: date | None = None) -> dict[str, int]:
    """Map symbol -> calendar days until its next reported earnings date.

    Only future dates are returned. Symbols with no upcoming entry are absent,
    which the screener treats as "no blackout".
    """
    if not EARNINGS_PATH.exists():
        return {}
    try:
        df = pd.read_csv(EARNINGS_PATH)
    except Exception as exc:  # a malformed calendar must not break the screen
        log.warning("earnings calendar unreadable (%s); continuing without it", exc)
        return {}

    cols = {c.lower().strip(): c for c in df.columns}
    if "symbol" not in cols or "date" not in cols:
        log.warning("earnings.csv needs 'symbol' and 'date' columns; ignoring")
        return {}

    df = df.rename(columns={cols["symbol"]: "symbol", cols["date"]: "date"})
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    df = df.dropna(subset=["date"])
    today = as_of or date.today()

    out: dict[str, int] = {}
    for _, r in df.iterrows():
        days = (r["date"] - today).days
        if days < 0:
            continue
        symbol = str(r["symbol"]).strip().upper()
        out[symbol] = min(days, out.get(symbol, 10**6))
    return out
