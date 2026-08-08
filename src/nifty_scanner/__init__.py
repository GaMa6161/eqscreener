"""Nifty EOD swing screener package.

Pipeline: fetch NSE EOD bhavcopy -> append to a Parquet history store ->
compute indicators -> rank swing candidates + sector rotation -> consolidate
news -> render an HTML dashboard/email -> deploy to Hostinger + email.
"""

__version__ = "0.1.0"
