"""Consolidated news via RSS feeds (no API key required), grouped by category.

Feeds are fetched with a browser UA + timeout (some publishers block the default
requests/feedparser agent), de-duplicated by headline, and sorted newest-first.
"""
from __future__ import annotations

import html
import logging
import time
from datetime import datetime, timezone

from .. import config
from ..utils import get_session

log = logging.getLogger(__name__)


def _published(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        val = entry.get(key)
        if val:
            try:
                return datetime.fromtimestamp(time.mktime(val), tz=timezone.utc)
            except Exception:
                return None
    return None


def _parse(url: str, session):
    import feedparser  # lazy import
    try:
        resp = session.get(url, timeout=20)
        if resp.status_code == 200 and resp.content:
            return feedparser.parse(resp.content)
    except Exception as exc:
        log.debug("feed fetch failed %s: %s", url, exc)
    try:
        return feedparser.parse(url)  # let feedparser try directly
    except Exception:
        return None


def fetch_news(feeds: list[dict] | None = None, max_per_category: int | None = None) -> dict[str, list[dict]]:
    """Return {category: [ {title, link, source, category, published}, ... ]}."""
    feeds = feeds if feeds is not None else config.NEWS["feeds"]
    max_per = max_per_category or config.NEWS["max_per_category"]
    categories = config.NEWS["categories"]

    session = get_session()
    buckets: dict[str, list[dict]] = {c: [] for c in categories}
    seen: set[str] = set()

    for feed in feeds:
        url, name = feed.get("url", ""), feed.get("name", "")
        category = feed.get("category", "Global")
        if not url:
            continue
        parsed = _parse(url, session)
        if parsed is None:
            continue
        for entry in getattr(parsed, "entries", []):
            title = html.unescape((entry.get("title") or "").strip())
            if not title:
                continue
            key = title.lower()
            if key in seen:
                continue
            seen.add(key)
            buckets.setdefault(category, []).append({
                "title": title,
                "link": (entry.get("link") or "").strip(),
                "source": name,
                "category": category,
                "published": _published(entry),
            })

    floor = datetime.min.replace(tzinfo=timezone.utc)
    for category, items in buckets.items():
        items.sort(key=lambda x: x["published"] or floor, reverse=True)
        buckets[category] = items[:max_per]
    return buckets


def total(buckets: dict[str, list[dict]]) -> int:
    return sum(len(v) for v in buckets.values())


def demo_news(max_per_category: int = 6) -> dict[str, list[dict]]:
    now = datetime.now(timezone.utc)
    samples = {
        "India": [
            ("Nifty ends higher; IT and auto lead the advance", "Markets Desk"),
            ("FIIs turn net buyers; DIIs add to positions", "Markets Desk"),
            ("Rupee steady against dollar ahead of inflation data", "Forex Desk"),
        ],
        "Global": [
            ("US markets close higher as tech rallies; Nasdaq up 1.2%", "Wall Street"),
            ("Asian markets mixed; Nikkei gains, Hang Seng flat", "Asia Desk"),
        ],
        "Commodities": [
            ("Crude oil slips below $80 on demand concerns", "Commodities Desk"),
            ("Gold holds near record as yields ease", "Commodities Desk"),
        ],
        "Currencies": [
            ("Dollar index eases; emerging-market currencies firm", "Forex Desk"),
        ],
    }
    return {
        cat: [{"title": t, "link": "#", "source": s, "category": cat, "published": now}
              for t, s in items[:max_per_category]]
        for cat, items in samples.items()
    }
