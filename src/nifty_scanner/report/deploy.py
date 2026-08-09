"""Publish the static dashboard to Hostinger over FTP/FTPS.

Recursively uploads a local directory into the remote web root (e.g. public_html).
Skips gracefully when FTP is not configured so local/dry runs never fail.
"""
from __future__ import annotations

import ftplib
import logging
from pathlib import Path

from ..config import FtpConfig

log = logging.getLogger(__name__)


def _connect(cfg: FtpConfig) -> ftplib.FTP:
    if cfg.use_tls:
        try:
            ftps = ftplib.FTP_TLS()
            ftps.connect(cfg.host, cfg.port, timeout=30)
            ftps.login(cfg.user, cfg.password)
            ftps.prot_p()
            return ftps
        except Exception as exc:
            log.warning("FTPS failed (%s); falling back to plain FTP", exc)
    ftp = ftplib.FTP()
    ftp.connect(cfg.host, cfg.port, timeout=30)
    ftp.login(cfg.user, cfg.password)
    return ftp


def _ensure_dir(ftp: ftplib.FTP, remote_dir: str) -> None:
    parts = [p for p in remote_dir.split("/") if p]
    path = "/" if remote_dir.startswith("/") else ""
    for part in parts:
        path = f"{path}{part}/" if path else f"{part}/"
        try:
            ftp.mkd(path.rstrip("/"))
        except ftplib.error_perm:
            pass  # already exists


def deploy_dir(local_dir: Path, cfg: FtpConfig, dry_run: bool = False) -> str:
    local_dir = Path(local_dir)
    if not local_dir.exists():
        return f"nothing to deploy ({local_dir} missing)"
    if dry_run:
        return "dry-run (deploy skipped)"
    if not cfg.is_configured:
        return "skipped (FTP not configured)"

    try:
        ftp = _connect(cfg)
    except Exception as exc:
        return f"deploy skipped - could not connect to {cfg.host}: {exc}"

    count = 0
    try:
        _ensure_dir(ftp, cfg.remote_dir)
        for path in sorted(local_dir.rglob("*")):
            rel = path.relative_to(local_dir).as_posix()
            remote = f"{cfg.remote_dir.rstrip('/')}/{rel}"
            if path.is_dir():
                _ensure_dir(ftp, remote)
                continue
            _ensure_dir(ftp, "/".join(remote.split("/")[:-1]))
            with open(path, "rb") as fh:
                ftp.storbinary(f"STOR {remote}", fh)
            count += 1
    except Exception as exc:
        return f"deploy error after {count} file(s): {exc}"
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()
    return f"uploaded {count} file(s) to {cfg.host}/{cfg.remote_dir}"
