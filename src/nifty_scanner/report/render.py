"""Render HTML (emails + static dashboard) and write JSON/CSV site artifacts."""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .. import config

log = logging.getLogger(__name__)

_env = Environment(
    loader=FileSystemLoader(str(config.TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


def _fmt_dt(value, fmt: str = "%d %b %H:%M") -> str:
    if not value:
        return ""
    if isinstance(value, str):
        return value
    try:
        return value.strftime(fmt)
    except Exception:
        return str(value)


def _fmt_pct(value) -> str:
    if value is None or value == "" or (isinstance(value, float) and value != value):
        return "-"
    try:
        return f"{float(value):+.2f}%"
    except Exception:
        return str(value)


def _fmt_num(value) -> str:
    if value is None or value == "" or (isinstance(value, float) and value != value):
        return "-"
    try:
        return f"{float(value):,.2f}"
    except Exception:
        return str(value)


_env.filters["fmt_dt"] = _fmt_dt
_env.filters["fmt_pct"] = _fmt_pct
_env.filters["fmt_num"] = _fmt_num


def to_records(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> list of dicts with NaN -> None (clean templating/JSON)."""
    if df is None or df.empty:
        return []
    return df.astype(object).where(pd.notna(df), None).to_dict("records")


def render_eod_email(context: dict) -> str:
    return _env.get_template("email_eod.html").render(**context)


def render_news_email(context: dict) -> str:
    return _env.get_template("email_news.html").render(**context)


def render_dashboard(context: dict) -> str:
    return _env.get_template("dashboard.html").render(**context)


def _write_json(name: str, obj) -> None:
    path = config.SITE_DIR / name
    path.write_text(json.dumps(obj, default=str, indent=2), encoding="utf-8")


def _copy_static() -> None:
    if config.STATIC_DIR.exists():
        shutil.copytree(config.STATIC_DIR, config.SITE_DIR / "static", dirs_exist_ok=True)


def write_site(context: dict, candidates: pd.DataFrame | None = None) -> None:
    """Render the dashboard and write JSON/CSV artifacts into the site dir."""
    config.ensure_dirs()
    (config.SITE_DIR / "index.html").write_text(render_dashboard(context), encoding="utf-8")
    _copy_static()

    _write_json("screener.json", context.get("candidates", []))
    _write_json("sectors.json", context.get("sector_ranking", []))
    _write_json("options.json", context.get("options_ideas", []))
    _write_json("news.json", context.get("news", {}))
    _write_json("meta.json", {
        "title": context.get("title"),
        "date": context.get("date_str"),
        "generated_at": context.get("generated_at"),
        "stats": context.get("stats", {}),
    })

    if candidates is not None and not candidates.empty:
        cols = ["symbol", "name", "sector", "close", "rsi", "adx", "vol_mult", "rs",
                "ret_21", "ret_63", "near_52w_pct", "stop", "risk_pct", "qty", "score"]
        tidy = candidates[[c for c in cols if c in candidates.columns]].copy()
        tidy.to_csv(config.SITE_DIR / "screener.csv", index=False)
    log.info("site written -> %s", config.SITE_DIR)


def write_premarket_page(context: dict) -> None:
    config.ensure_dirs()
    (config.SITE_DIR / "premarket.html").write_text(render_news_email(context), encoding="utf-8")
    _copy_static()
    _write_json("cues.json", context.get("cues", []))
    _write_json("news.json", context.get("news", {}))


def screener_csv_path():
    return config.SITE_DIR / "screener.csv"
