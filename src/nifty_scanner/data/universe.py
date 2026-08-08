"""Stock universe (Nifty 500) with a per-symbol sector map.

The official constituent CSV includes an "Industry" macro-sector column, so one
download gives us both the symbol list and the sector mapping used for rotation.
Falls back to a cached copy, then to a bundled Nifty 50 subset for offline runs.
"""
from __future__ import annotations

import io
import logging

import pandas as pd

from .. import config
from ..utils import get_session

log = logging.getLogger(__name__)

_COLS = {"Company Name": "name", "Industry": "sector", "Symbol": "symbol"}


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [str(c).strip() for c in df.columns]
    have = {k: v for k, v in _COLS.items() if k in df.columns}
    # Bundled fallback already uses lowercase symbol/name/sector.
    if not have and {"symbol", "name", "sector"}.issubset(df.columns):
        out = df[["symbol", "name", "sector"]].copy()
    else:
        out = df.rename(columns=have)[list(have.values())].copy()
    for col in ("symbol", "name", "sector"):
        if col not in out:
            out[col] = ""
        out[col] = out[col].astype(str).str.strip()
    out = out[out["symbol"] != ""].drop_duplicates("symbol").reset_index(drop=True)
    return out[["symbol", "name", "sector"]]


def _download() -> pd.DataFrame | None:
    session = get_session()
    for url in config.UNIVERSE["list_urls"]:
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code == 200 and resp.content:
                return _normalize(pd.read_csv(io.BytesIO(resp.content)))
        except Exception as exc:
            log.debug("universe download failed %s: %s", url, exc)
    return None


def load_universe(refresh: bool = False) -> pd.DataFrame:
    """Return the universe as a DataFrame[symbol, name, sector]."""
    config.ensure_dirs()
    cache = config.UNIVERSE["cache_csv"]

    if not refresh and cache.exists():
        return _normalize(pd.read_csv(cache))

    df = _download()
    if df is not None and len(df) > 100:
        df.to_csv(cache, index=False)
        log.info("universe: downloaded %d constituents -> %s", len(df), cache.name)
        return df

    if cache.exists():
        log.warning("universe: download failed, using cached %s", cache.name)
        return _normalize(pd.read_csv(cache))

    log.warning("universe: download failed, using bundled fallback subset")
    return _normalize(pd.read_csv(config.UNIVERSE_FALLBACK_CSV))


def symbols(universe: pd.DataFrame) -> list[str]:
    return universe["symbol"].tolist()


def sector_map(universe: pd.DataFrame) -> dict[str, str]:
    return dict(zip(universe["symbol"], universe["sector"]))
