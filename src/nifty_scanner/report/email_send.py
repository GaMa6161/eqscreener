"""Send HTML digests via SMTP, with an optional file attachment and a safe
dry-run fallback when SMTP is not configured."""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from ..config import EmailConfig

log = logging.getLogger(__name__)


def send_email(subject: str, html: str, cfg: EmailConfig, attachments: list[Path] | None = None) -> str:
    """Send `html` to the configured recipients. Returns a status string.

    If SMTP is not configured, nothing is sent (the caller still has the site/HTML).
    """
    if not cfg.is_configured:
        return "dry-run (SMTP not configured)"

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = cfg.sender
    msg["To"] = ", ".join(cfg.recipients)

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText("HTML email - please view in an HTML-capable client.", "plain"))
    alt.attach(MIMEText(html, "html"))
    msg.attach(alt)

    for path in attachments or []:
        path = Path(path)
        if not path.exists():
            continue
        part = MIMEApplication(path.read_bytes(), Name=path.name)
        part["Content-Disposition"] = f'attachment; filename="{path.name}"'
        msg.attach(part)

    if cfg.use_ssl:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(cfg.host, cfg.port, context=context) as server:
            server.login(cfg.user, cfg.password)
            server.sendmail(cfg.sender, list(cfg.recipients), msg.as_string())
    else:
        with smtplib.SMTP(cfg.host, cfg.port) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.login(cfg.user, cfg.password)
            server.sendmail(cfg.sender, list(cfg.recipients), msg.as_string())

    return f"sent to {len(cfg.recipients)} recipient(s)"
