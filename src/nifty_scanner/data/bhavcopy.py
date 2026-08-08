"""Download and parse the official NSE end-of-day (EOD) bhavcopy.

Since 8 Jul 2024 NSE publishes the Unified Distilled File Format (UDiFF). The
Capital Market file is a zipped CSV, one per trading day, served from the
`nsearchives` archive host which is not behind Cloudflare and works from any IP
(including GitHub Actions runners). Holidays/weekends return HTTP 404.

    https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{YYYYMMDD}_F_0000.csv.zip
"""
from __future__ import annotations

import io
import logging
import zipfile
from datetime import date

import pandas as pd

from ..utils import get_session, yyyymmdd

log = logging.getLogger(__name__)

CM_URLS = (
    "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d}_F_0000.csv.zip",
    "https://archives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d}_F_0000.csv.zip",
)

# UDiFF column -> normalized name.
_COLMAP = {
    "TradDt": "date",
    "TckrSymb": "symbol",
    "SctySrs": "series",
    "OpnPric": "open",
    "HghPric": "high",
    "LwPric": "low",
    "ClsPric": "close",
    "LastPric": "last",
    "PrvsClsgPric": "prev_close",
    "TtlTradgVol": "volume",
    "TtlTrfVal": "turnover",
    "TtlNbOfTxsExctd": "trades",
}

_NUMERIC = ["open", "high", "low", "close", "last", "prev_close", "volume", "turnover", "trades"]


def _parse_zip(content: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        with zf.open(name) as fh:
            df = pd.read_csv(fh)
    df.columns = [str(c).strip() for c in df.columns]
    keep = {k: v for k, v in _COLMAP.items() if k in df.columns}
    df = df[list(keep)].rename(columns=keep)
    if "series" in df:
        df["series"] = df["series"].astype(str).str.strip()
    if "symbol" in df:
        df["symbol"] = df["symbol"].astype(str).str.strip()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for col in _NUMERIC:
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["symbol", "date", "close"])


def fetch_cm_bhavcopy(d: date, allowed_series: tuple[str, ...] | None = None) -> pd.DataFrame | None:
    """Return the normalized cash-market bhavcopy for `d`, or None if unavailable.

    Columns: date, symbol, series, open, high, low, close, prev_close, last,
    volume, turnover, trades.
    """
    session = get_session()
    for template in CM_URLS:
        url = template.format(d=yyyymmdd(d))
        try:
            resp = session.get(url, timeout=30)
        except Exception as exc:  # network hiccup -> try next mirror
            log.debug("bhavcopy request failed %s: %s", url, exc)
            continue
        if resp.status_code == 404:
            return None  # holiday / not published yet
        if resp.status_code != 200 or not resp.content:
            log.debug("bhavcopy %s -> HTTP %s", url, resp.status_code)
            continue
        try:
            df = _parse_zip(resp.content)
        except Exception as exc:
            log.warning("failed to parse bhavcopy %s: %s", url, exc)
            continue
        if allowed_series:
            df = df[df["series"].isin(allowed_series)]
        return df.reset_index(drop=True)
    return None
