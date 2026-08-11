"""DhanHQ REST client for live quotes and option chains.

Uses plain `requests` (no hard dependency on the `dhanhq` package). Credentials
come from the environment:

    DHAN_CLIENT_ID=...
    DHAN_ACCESS_TOKEN=...   # generate daily from Dhan web/app (API section)

Instrument master is downloaded once and cached under data/cache/.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from .. import config
from ..utils import get_session

log = logging.getLogger(__name__)

BASE = "https://api.dhan.co/v2"
SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
INSTRUMENT_CACHE = config.CACHE_DIR / "dhan_nse_eq.parquet"
INDEX_IDS = {
    "NIFTY": 13,
    "BANKNIFTY": 25,
    "FINNIFTY": 27,
    "NIFTYIT": 29,
    "NIFTY AUTO": 14,
    "NIFTY FMCG": 28,
    "NIFTY PHARMA": 32,
    "NIFTY METAL": 31,
    "NIFTY ENERGY": 42,
    "INDIA VIX": 21,
}


@dataclass
class DhanConfig:
    client_id: str
    access_token: str

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id and self.access_token)

    @classmethod
    def from_env(cls) -> "DhanConfig":
        return cls(
            client_id=os.getenv("DHAN_CLIENT_ID", "").strip(),
            access_token=os.getenv("DHAN_ACCESS_TOKEN", "").strip(),
        )


class DhanClient:
    """Thin REST wrapper with quote + option-chain helpers."""

    def __init__(self, cfg: DhanConfig | None = None, session: requests.Session | None = None):
        self.cfg = cfg or DhanConfig.from_env()
        if not self.cfg.is_configured:
            raise RuntimeError(
                "Dhan credentials missing. Set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN in .env "
                "(Dhan web/app → profile → Access DhanHQ APIs)."
            )
        self.session = session or get_session()
        self._last_quote_ts = 0.0
        self._last_chain_ts = 0.0

    def _headers(self) -> dict[str, str]:
        return {
            "access-token": self.cfg.access_token,
            "client-id": self.cfg.client_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _throttle_quote(self) -> None:
        # Quote APIs: 1 req/sec
        wait = 1.05 - (time.monotonic() - self._last_quote_ts)
        if wait > 0:
            time.sleep(wait)

    def _throttle_chain(self) -> None:
        # Option chain: 1 unique req / 3 sec
        wait = 3.05 - (time.monotonic() - self._last_chain_ts)
        if wait > 0:
            time.sleep(wait)

    def _post(self, path: str, payload: dict, *, kind: str = "quote") -> dict:
        if kind == "chain":
            self._throttle_chain()
        else:
            self._throttle_quote()
        url = f"{BASE}{path}"
        resp = self.session.post(url, json=payload, headers=self._headers(), timeout=30)
        if kind == "chain":
            self._last_chain_ts = time.monotonic()
        else:
            self._last_quote_ts = time.monotonic()
        if resp.status_code >= 400:
            raise RuntimeError(f"Dhan {path} HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        if isinstance(data, dict) and data.get("status") not in (None, "success"):
            raise RuntimeError(f"Dhan {path} error: {data}")
        return data

    def quote(self, nse_eq_ids: list[int], index_ids: list[int] | None = None) -> dict[str, Any]:
        """Full market quote for equity (+ optional index) security IDs."""
        payload: dict[str, list[int]] = {}
        if nse_eq_ids:
            payload["NSE_EQ"] = [int(x) for x in nse_eq_ids]
        if index_ids:
            payload["IDX_I"] = [int(x) for x in index_ids]
        if not payload:
            return {}
        return self._post("/marketfeed/quote", payload, kind="quote")

    def ohlc(self, nse_eq_ids: list[int]) -> dict[str, Any]:
        return self._post("/marketfeed/ohlc", {"NSE_EQ": [int(x) for x in nse_eq_ids]}, kind="quote")

    def expiry_list(self, under_security_id: int, under_seg: str = "IDX_I") -> list[str]:
        data = self._post(
            "/optionchain/expirylist",
            {"UnderlyingScrip": int(under_security_id), "UnderlyingSeg": under_seg},
            kind="chain",
        )
        return list(data.get("data") or [])

    def option_chain(self, under_security_id: int, expiry: str, under_seg: str = "IDX_I") -> dict:
        data = self._post(
            "/optionchain",
            {
                "UnderlyingScrip": int(under_security_id),
                "UnderlyingSeg": under_seg,
                "Expiry": expiry,
            },
            kind="chain",
        )
        return data.get("data") or {}


# --- Instrument master -----------------------------------------------------
def download_equity_map(force: bool = False) -> pd.DataFrame:
    """NSE EQ symbol → security_id map, cached as Parquet."""
    config.ensure_dirs()
    if INSTRUMENT_CACHE.exists() and not force:
        age_h = (time.time() - INSTRUMENT_CACHE.stat().st_mtime) / 3600
        if age_h < 24:
            return pd.read_parquet(INSTRUMENT_CACHE)

    log.info("downloading Dhan scrip master...")
    session = get_session()
    resp = session.get(SCRIP_MASTER_URL, timeout=120)
    resp.raise_for_status()
    path = config.CACHE_DIR / "api-scrip-master.csv"
    path.write_bytes(resp.content)

    df = pd.read_csv(path, low_memory=False)
    eq = df[
        (df["SEM_EXM_EXCH_ID"] == "NSE")
        & (df["SEM_SEGMENT"] == "E")
        & (df["SEM_SERIES"] == "EQ")
    ].copy()
    eq = eq.rename(columns={
        "SEM_SMST_SECURITY_ID": "security_id",
        "SEM_TRADING_SYMBOL": "symbol",
        "SM_SYMBOL_NAME": "name",
    })
    eq["security_id"] = pd.to_numeric(eq["security_id"], errors="coerce").astype("Int64")
    eq = eq.dropna(subset=["security_id", "symbol"])
    eq["symbol"] = eq["symbol"].astype(str).str.strip().str.upper()
    eq = eq.drop_duplicates("symbol", keep="first")[["symbol", "security_id", "name"]]

    # F&O stock underlyings: OPTSTK symbols look like RELIANCE-Oct2026-1400-CE
    opt = df[
        (df["SEM_EXM_EXCH_ID"] == "NSE")
        & (df["SEM_SEGMENT"] == "D")
        & (df["SEM_INSTRUMENT_NAME"] == "OPTSTK")
    ]
    fno_syms = {
        str(s).strip().upper().split("-")[0]
        for s in opt["SEM_TRADING_SYMBOL"].dropna()
        if str(s).strip()
    }
    eq["is_fno"] = eq["symbol"].isin(fno_syms)
    eq.to_parquet(INSTRUMENT_CACHE, index=False)
    log.info("cached %d NSE EQ instruments (%d F&O flags) -> %s", len(eq), int(eq["is_fno"].sum()), INSTRUMENT_CACHE)
    return eq


def map_universe(universe: pd.DataFrame, instrument_map: pd.DataFrame | None = None) -> pd.DataFrame:
    """Attach Dhan security_id (+ is_fno) to a universe DataFrame."""
    imap = instrument_map if instrument_map is not None else download_equity_map()
    out = universe.copy()
    out["symbol"] = out["symbol"].astype(str).str.strip().str.upper()
    merged = out.merge(imap[["symbol", "security_id", "is_fno"]], on="symbol", how="left")
    missing = merged["security_id"].isna().sum()
    if missing:
        log.warning("%d universe symbols missing Dhan security_id", int(missing))
    return merged


def flatten_quotes(raw: dict, id_to_meta: dict[int, dict]) -> pd.DataFrame:
    """Turn Dhan quote response into a tidy DataFrame."""
    rows: list[dict] = []
    data = (raw or {}).get("data") or raw or {}
    for segment, items in data.items():
        if not isinstance(items, dict):
            continue
        for sid, q in items.items():
            try:
                security_id = int(sid)
            except Exception:
                continue
            meta = id_to_meta.get(security_id, {})
            ohlc = q.get("ohlc") or {}
            ltp = float(q.get("last_price") or 0)
            prev = float(ohlc.get("close") or 0)  # prev close in Dhan OHLC.close for live day
            open_ = float(ohlc.get("open") or 0)
            high = float(ohlc.get("high") or 0)
            low = float(ohlc.get("low") or 0)
            chg_pct = ((ltp / prev) - 1.0) * 100.0 if prev else None
            from_open_pct = ((ltp / open_) - 1.0) * 100.0 if open_ else None
            rows.append({
                "security_id": security_id,
                "segment": segment,
                "symbol": meta.get("symbol", str(security_id)),
                "name": meta.get("name", ""),
                "sector": meta.get("sector", ""),
                "is_fno": bool(meta.get("is_fno", False)),
                "ltp": ltp,
                "open": open_,
                "high": high,
                "low": low,
                "prev_close": prev,
                "chg_pct": None if chg_pct is None else round(chg_pct, 2),
                "from_open_pct": None if from_open_pct is None else round(from_open_pct, 2),
                "volume": int(q.get("volume") or 0),
                "avg_price": float(q.get("average_price") or 0),
                "oi": int(q.get("oi") or 0),
                "net_change": float(q.get("net_change") or 0),
            })
    return pd.DataFrame(rows)
