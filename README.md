# Nifty Eq Scanner

An **end-of-day (EOD) swing screener for the Nifty 500** with **sector-rotation
ranking**, **defined-risk sectorial options ideas**, and a **consolidated global
news brief**. You **run it manually on your PC** and it **emails you the digest**
(and writes a local HTML dashboard you can open in a browser).

> **Not investment advice.** Signals are produced by mechanical rules and can be
> wrong. Options ideas are directional scaffolding only. This is an engineering /
> research tool - do your own research and manage risk.

---

## What it does

Two commands you run when you want them:

| Command | When | Contents |
|---------|------|----------|
| `run-eod` | after market close (bhavcopy ~6-7pm IST) | ranked swing candidates (with ATR stops/targets + position sizing), sector rotation, sectorial options ideas -> **emailed** + `output/site/index.html` |
| `run-news` | before the open | overnight global cues (US/Asia/crude/gold/USDINR/VIX) + consolidated news headlines -> **emailed** + `output/site/premarket.html` |

Everything runs locally on your machine. No server, no hosting required.

---

## Quickstart (manual, on your PC)

Requires Python 3.11+ (works on macOS / Windows / Linux).

```bash
# 1) Set up a virtual environment + install
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e '.[extras]'           # extras = yfinance, for the overnight cues

# 2) See a full sample instantly (offline synthetic data, no network, no email):
nifty-scanner run-eod --demo --no-email
open output/site/index.html          # macOS  (Windows: start ...; Linux: xdg-open ...)

# 3) Configure email: copy .env.example -> .env and fill the SMTP section
cp .env.example .env
#    (Gmail: use an App Password, not your normal password)

# 4) Run for real. The FIRST run auto-downloads ~9 months of history
#    (a few minutes); later runs just add the latest day.
nifty-scanner run-eod                 # screens + emails you the digest
nifty-scanner run-news                # global cues + news brief, emailed
```

Or use the launcher script:

```bash
./scripts/run_local.sh eod     # or: news  |  both
```

If you don't configure `.env`, everything still runs and writes the dashboard to
`output/site/` - it just prints `email: dry-run (SMTP not configured)` instead of
sending.

### Commands

| Command | What it does |
|---------|--------------|
| `nifty-scanner run-eod [--demo] [--no-email]` | screener + sectors + dashboard, emailed |
| `nifty-scanner run-news [--demo] [--no-email]` | cues + news brief, emailed |
| `nifty-scanner backfill [--days 420]` | (optional) pre-download history; run-eod does this automatically |
| `nifty-scanner build-universe [--refresh]` | (optional) refresh the Nifty 500 list |

`--demo` = offline synthetic data. `--no-email` = build files but don't send.
`--deploy` = *also* upload the dashboard to a website via FTP (optional, see below).

---

## Email setup

Fill the SMTP section of `.env`. Common choices:

- **Gmail** - `smtp.gmail.com` : `465`, and use an **App Password**
  (Google Account -> Security -> 2-Step Verification -> App passwords). Your normal
  password will not work.
- **Outlook** - `smtp-mail.outlook.com` : `587` with `SMTP_USE_SSL=false`.
- **Hostinger / other mailbox** - `smtp.hostinger.com` : `465`.

```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=465
SMTP_USER=you@gmail.com
SMTP_PASSWORD=your-app-password
SMTP_USE_SSL=true
EMAIL_FROM=you@gmail.com
EMAIL_TO=you@gmail.com,friend@example.com
```

The EOD email includes the ranked table and attaches `screener.csv`.

---

## How data is fetched (all free)

- **EOD prices** - the official **NSE UDiFF bhavcopy** (one zipped CSV per trading
  day) from `nsearchives.nseindia.com`, parsed and appended to a local **Parquet**
  history store (`data/history/ohlcv.parquet`) that grows over time.
- **Universe + sectors** - the official **Nifty 500 constituent CSV** (its
  `Industry` column is the sector map).
- **Relative strength** - benchmarked against a self-contained equal-weight index
  built from the universe (no external feed needed).
- **News** - free **RSS feeds** grouped India / Global / Commodities / Currencies.
- **Overnight cues** - via `yfinance` (indices, crude, gold, USD/INR, India VIX).

**Options note:** free live option chains / greeks / IV aren't reliably available,
so sectorial options output is directional-bias + defined-risk structure (e.g. "IT
leading -> bull call spread on NIFTY IT"). Exact strikes/greeks need a broker API
(Kite/Upstox/Dhan) - a clean future upgrade.

---

## Scheduling it automatically (optional)

You still launch it yourself, but if you want it hands-off on your PC:

- **macOS/Linux cron** - see `scripts/crontab.example`.
- **macOS** can also use `launchd`; **Windows** can use Task Scheduler to run
  `python -m nifty_scanner.cli run-eod`.

Your PC must be awake at the scheduled time.

---

## Publishing the dashboard to a website (optional)

The dashboard is always written to `output/site/`. If you also want it online, add
the FTP section to `.env` and run with `--deploy`:

```bash
nifty-scanner run-eod --deploy
```

There are also optional GitHub Actions workflows (`.github/workflows/`) that can run
the emails in the cloud on a schedule - enable them only if you want that; they are
not needed for manual PC use.

---

## Configuration

- **`src/nifty_scanner/config.py`** - all tunables: screener thresholds (RSI zone,
  EMA trend gates, volume multiple, RS lookback, liquidity floor, ATR stop/targets),
  sector settings + index-option map, RSS feeds, cue tickers, disclaimer.
- **`.env`** - copy from `.env.example`; SMTP is all you need for manual use. Never
  commit `.env`.

---

## Project structure

```
.
├── pyproject.toml              # package + `nifty-scanner` CLI
├── requirements.txt
├── .env.example                # SMTP (required) + FTP (optional)
├── run_eod.py / run_premarket.py   # wrappers: `python run_eod.py`
├── scripts/run_local.sh        # one-command launcher
├── data/
│   ├── universe/nifty500.csv   # list + sector map (auto-downloaded)
│   └── history/ohlcv.parquet   # local history store (auto-grows)
├── src/nifty_scanner/
│   ├── config.py  utils.py  indicators.py  cli.py
│   ├── data/{bhavcopy,universe,history}.py
│   ├── screener/{swing,sectors}.py
│   ├── news/{feeds,cues}.py
│   └── report/{render,email_send,deploy}.py
├── web/templates/*.html  web/static/style.css
└── tests/test_indicators.py
```

Run tests: `python tests/test_indicators.py`.

---

## Caveats

- NSE bhavcopy and RSS are free/unofficial - fine for personal use; expect
  occasional gaps (the code skips missing days/symbols gracefully).
- The first `run-eod` backfills history and takes a few minutes.
- For live intraday / options greeks, add a broker API (Upstox/Dhan/Angel free
  tiers; Kite paid) - the clean next upgrade.
