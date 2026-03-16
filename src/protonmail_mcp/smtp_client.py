import logging
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from typing import Sequence

import aiosmtplib

from .config import Settings

logger = logging.getLogger(__name__)


class SMTPClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # Bridge v3 port 1026: direkt SSL med self-signed cert
        self._ssl_ctx = ssl.create_default_context()
        self._ssl_ctx.check_hostname = False
        self._ssl_ctx.verify_mode = ssl.CERT_NONE

    async def send_email(
        self,
        to: str | Sequence[str],
        subject: str,
        body: str,
        body_html: str | None = None,
        cc: str | Sequence[str] | None = None,
        bcc: str | Sequence[str] | None = None,
        reply_to: str | None = None,
    ) -> bool:
        to_list = [to] if isinstance(to, str) else list(to)
        cc_list = ([cc] if isinstance(cc, str) else list(cc)) if cc else []
        bcc_list = ([bcc] if isinstance(bcc, str) else list(bcc)) if bcc else []

        if body_html:
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(body, "plain", "utf-8"))
            msg.attach(MIMEText(body_html, "html", "utf-8"))
        else:
            msg = MIMEText(body, "plain", "utf-8")

        msg["From"] = self._settings.username
        msg["To"] = ", ".join(to_list)
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid()
        if cc_list:
            msg["Cc"] = ", ".join(cc_list)
        if reply_to:
            msg["Reply-To"] = reply_to

        all_recipients = to_list + cc_list + bcc_list

        logger.info(
            "Skickar e-post (%d mottagare) via SMTP %s:%d",
            len(all_recipients),
            self._settings.smtp_host,
            self._settings.smtp_port,
        )

        # Bridge v3 port 1026: direkt SSL med self-signed cert
        async with aiosmtplib.SMTP(
            hostname=self._settings.smtp_host,
            port=self._settings.smtp_port,
            use_tls=True,
            tls_context=self._ssl_ctx,
        ) as client:
            await client.login(self._settings.username, self._settings.password)
            await client.send_message(msg, recipients=all_recipients)

        logger.info("E-post skickad")
        return True
