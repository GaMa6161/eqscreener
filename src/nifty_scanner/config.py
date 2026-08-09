"""Central configuration: paths, screener thresholds, universe, news feeds and
schedules live here (Python, not YAML). Secrets come from the environment/.env.

Anchor everything to the current working directory (the repo root) so the same
code runs locally, in GitHub Actions, and on a server without path surprises.
Override with the NIFTY_HOME environment variable if needed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from email.utils import formataddr, parseaddr
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(os.getenv("NIFTY_HOME", os.getcwd())).resolve()
load_dotenv(ROOT / ".env")

# --- Paths ------------------------------------------------------------------
DATA_DIR = ROOT / "data"
UNIVERSE_DIR = DATA_DIR / "universe"
HISTORY_DIR = DATA_DIR / "history"
HISTORY_PATH = HISTORY_DIR / "ohlcv.parquet"
CACHE_DIR = DATA_DIR / "cache"
OUTPUT_DIR = ROOT / "output"
SITE_DIR = OUTPUT_DIR / "site"           # static site that gets deployed to Hostinger
WEB_DIR = ROOT / "web"
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

# Bundled offline fallback (a Nifty 50 subset) used when the live list can't be
# downloaded and no cached list exists (keeps demo/CI runs working).
UNIVERSE_FALLBACK_CSV = DATA_DIR / "universe_nifty50.csv"


def ensure_dirs() -> None:
    for d in (UNIVERSE_DIR, HISTORY_DIR, CACHE_DIR, SITE_DIR):
        d.mkdir(parents=True, exist_ok=True)


# --- Market / universe ------------------------------------------------------
MARKET = {
    "timezone": "Asia/Kolkata",
    "benchmark_label": "NIFTY 500 (equal-weight)",
    "currency": "INR",
}

UNIVERSE = {
    "index_name": "NIFTY 500",
    # nsearchives is not behind Cloudflare and works from any IP (incl. CI).
    "list_urls": [
        "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
        "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
    ],
    "cache_csv": UNIVERSE_DIR / "nifty500.csv",
    # Cash-market series to keep. EQ = rolling settlement equity, BE = trade-to-trade.
    "allowed_series": ("EQ", "BE"),
}

DATA = {
    "backfill_days": 420,      # calendar days to look back when bootstrapping (~9 months trading)
    "min_history_rows": 210,   # need >200 for EMA200 / relative strength to be meaningful
    "yahoo_suffix": ".NS",     # only used by the optional yfinance helpers (cues/benchmark)
}

# --- Swing screener thresholds ---------------------------------------------
# A stock must clear every enabled gate to become a candidate.
SCREENER = {
    "min_price": 50.0,             # ignore penny/illiquid names below this price
    "min_avg_turnover_cr": 5.0,    # min 20d avg daily turnover in INR crore (liquidity)
    "trend": {
        "require_close_above_ema200": True,
        "require_ema50_above_ema200": True,
        "require_close_above_ema20": True,
    },
    "momentum": {
        "rsi_length": 14,
        "rsi_min": 50,             # continuation zone lower bound
        "rsi_max": 75,             # avoid over-extended
        "require_macd_bullish": True,
        "adx_min": 18,             # trend-strength floor (soft: scored, not a hard gate)
    },
    "breakout": {
        "lookback_high": 20,       # new N-day high (0 disables)
        "near_52w_high_pct": 15.0, # within X% of the 52-week high
    },
    "volume": {
        "avg_length": 20,
        "min_volume_multiple": 1.3,
    },
    "relative_strength": {
        "lookback": 63,            # ~3 months of trading days
        "require_outperform_benchmark": True,
    },
    "atr_length": 14,
    "stop_atr_mult": 2.0,          # suggested initial stop = close - mult x ATR
    "target_r_multiples": [2.0, 3.0],
    "max_results": 25,
}

# --- Sector rotation + options ideas ---------------------------------------
SECTORS = {
    "breadth_ema": 50,
    "top_n_leaders": 3,
    "bottom_n_laggards": 3,
    # Sectors with liquid INDEX options. Keys match both the Nifty 500 "Industry"
    # macro labels and the short labels in the offline fallback universe.
    "index_option_sectors": {
        "Financial Services": "NIFTY FINANCIAL SERVICES",
        "Information Technology": "NIFTY IT",
        "Fast Moving Consumer Goods": "NIFTY FMCG",
        "Automobile and Auto Components": "NIFTY AUTO",
        "Healthcare": "NIFTY PHARMA",
        "Metals & Mining": "NIFTY METAL",
        "Oil Gas & Consumable Fuels": "NIFTY ENERGY",
        # short labels (offline fallback universe)
        "IT": "NIFTY IT",
        "Bank": "NIFTY BANK",
        "Auto": "NIFTY AUTO",
        "Pharma": "NIFTY PHARMA",
        "FMCG": "NIFTY FMCG",
        "Metal": "NIFTY METAL",
        "Energy": "NIFTY ENERGY",
    },
}

# --- News (consolidated brief for the intraday/pre-market email) -----------
NEWS = {
    "max_per_category": 8,
    "feeds": [
        {"name": "ET Markets", "url": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", "category": "India"},
        {"name": "Moneycontrol Markets", "url": "https://www.moneycontrol.com/rss/marketreports.xml", "category": "India"},
        {"name": "Livemint Markets", "url": "https://www.livemint.com/rss/markets", "category": "India"},
        {"name": "Business Standard Markets", "url": "https://www.business-standard.com/rss/markets-106.rss", "category": "India"},
        {"name": "CNBC Markets", "url": "https://www.cnbc.com/id/20910258/device/rss/rss.html", "category": "Global"},
        {"name": "Yahoo Finance", "url": "https://finance.yahoo.com/news/rssindex", "category": "Global"},
        {"name": "MarketWatch Top", "url": "http://feeds.marketwatch.com/marketwatch/topstories/", "category": "Global"},
        {"name": "Investing.com Commodities", "url": "https://www.investing.com/rss/news_11.rss", "category": "Commodities"},
        {"name": "OilPrice", "url": "https://oilprice.com/rss/main", "category": "Commodities"},
        {"name": "Investing.com Forex", "url": "https://www.investing.com/rss/news_1.rss", "category": "Currencies"},
    ],
    "categories": ("India", "Global", "Commodities", "Currencies"),
}

# Overnight/global cues (Yahoo symbols) shown in the pre-market brief.
CUES = {
    "Nifty 50 (prev close)": "^NSEI",
    "US - S&P 500": "^GSPC",
    "US - Nasdaq": "^IXIC",
    "US - Dow": "^DJI",
    "Japan - Nikkei 225": "^N225",
    "HongKong - Hang Seng": "^HSI",
    "Crude (WTI)": "CL=F",
    "Gold": "GC=F",
    "USD/INR": "INR=X",
    "India VIX": "^INDIAVIX",
}

REPORT = {
    "title": "Nifty Eq Scanner",
    "site_url": os.getenv("SITE_URL", ""),   # optional, shown in emails as a link
    "disclaimer": (
        "For education and research only. This is NOT investment advice and NOT a "
        "recommendation to buy or sell any security. Signals are generated by "
        "mechanical rules and can be wrong. Options ideas are directional scaffolding "
        "only - size, strikes, expiry and greeks must be finalised in your broker layer. "
        "Do your own research and manage risk."
    ),
}


def _as_bool(val: str | None, default: bool = False) -> bool:
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def account() -> dict:
    """Position-sizing inputs from the environment."""
    return {
        "capital": float(os.getenv("ACCOUNT_CAPITAL", "1000000") or "1000000"),
        "risk_pct": float(os.getenv("RISK_PCT_PER_TRADE", "1.0") or "1.0"),
    }


@dataclass
class EmailConfig:
    """SMTP settings pulled from environment variables."""

    host: str = ""
    port: int = 465
    user: str = ""
    password: str = ""
    use_ssl: bool = True
    sender: str = ""
    sender_name: str = ""
    recipients: tuple[str, ...] = ()
    self_copy: str = ""

    @property
    def is_configured(self) -> bool:
        return bool(self.host and self.user and self.password and self.recipients)

    @property
    def from_header(self) -> str:
        """`From:` value, with the display name quoted when it needs it."""
        return formataddr((self.sender_name, self.sender)) if self.sender_name else self.sender

    @property
    def visible_to(self) -> str:
        """The only address that appears in the headers; everyone else is bcc'd."""
        return self.self_copy or self.sender

    @property
    def envelope_recipients(self) -> tuple[str, ...]:
        """Every address the SMTP envelope delivers to, self copy first, deduped."""
        return tuple(dict.fromkeys([self.visible_to, *self.recipients]))

    @classmethod
    def from_env(cls) -> "EmailConfig":
        recipients = tuple(
            r.strip() for r in os.getenv("EMAIL_TO", "").split(",") if r.strip()
        )
        # EMAIL_FROM may be a bare address or "Name <addr>"; the envelope needs the
        # bare address either way.
        parsed_name, parsed_addr = parseaddr(os.getenv("EMAIL_FROM", "") or os.getenv("SMTP_USER", ""))
        sender = parsed_addr or os.getenv("SMTP_USER", "")
        return cls(
            host=os.getenv("SMTP_HOST", ""),
            port=int(os.getenv("SMTP_PORT", "465") or "465"),
            user=os.getenv("SMTP_USER", ""),
            password=os.getenv("SMTP_PASSWORD", ""),
            use_ssl=_as_bool(os.getenv("SMTP_USE_SSL"), True),
            sender=sender,
            sender_name=os.getenv("EMAIL_FROM_NAME", "").strip() or parsed_name,
            recipients=recipients,
            self_copy=os.getenv("EMAIL_SELF_COPY", "").strip() or sender,
        )


@dataclass
class FtpConfig:
    """FTP settings for publishing the static dashboard to Hostinger."""

    host: str = ""
    port: int = 21
    user: str = ""
    password: str = ""
    remote_dir: str = "public_html"
    use_tls: bool = True

    @property
    def is_configured(self) -> bool:
        return bool(self.host and self.user and self.password)

    @classmethod
    def from_env(cls) -> "FtpConfig":
        return cls(
            host=os.getenv("FTP_HOST", ""),
            port=int(os.getenv("FTP_PORT", "21") or "21"),
            user=os.getenv("FTP_USER", ""),
            password=os.getenv("FTP_PASSWORD", ""),
            remote_dir=os.getenv("FTP_REMOTE_DIR", "public_html"),
            use_tls=_as_bool(os.getenv("FTP_USE_TLS"), True),
        )
