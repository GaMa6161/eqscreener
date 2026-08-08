"""Shared helpers: HTTP session, logging, and date/timezone utilities."""
from __future__ import annotations

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter, Retry

IST = ZoneInfo("Asia/Kolkata")

# Browser-like headers; NSE archive hosts reject the default requests UA.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}


def get_session() -> requests.Session:
    """A requests session with retries and NSE-friendly headers."""
    session = requests.Session()
    session.headers.update(_HEADERS)
    retry = Retry(
        total=3,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def ist_now() -> datetime:
    return datetime.now(IST)


def yyyymmdd(d: date) -> str:
    return d.strftime("%Y%m%d")
