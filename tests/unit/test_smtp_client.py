"""Unit tests for SMTPClient – send_email()."""

from __future__ import annotations

import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from protonmail_mcp.config import Settings
from protonmail_mcp.smtp_client import SMTPClient, _build_message, _normalize_recipients


@pytest.fixture
def settings() -> Settings:
    return Settings(
        username="user@proton.me",
        password="bridge-password",
        smtp_host="127.0.0.1",
        smtp_port=1026,
    )


@pytest.fixture
def mock_smtp():
    """Mock aiosmtplib.SMTP as async context manager."""
    smtp_instance = AsyncMock()
    smtp_instance.login = AsyncMock()
    smtp_instance.send_message = AsyncMock()

    smtp_cm = MagicMock()
    smtp_cm.__aenter__ = AsyncMock(return_value=smtp_instance)
    smtp_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("protonmail_mcp.smtp_client.aiosmtplib.SMTP", return_value=smtp_cm) as smtp_cls:
        yield smtp_cls, smtp_instance


# -------------------------------------------------------------------
# _normalize_recipients()
# -------------------------------------------------------------------


def test_normalize_recipients_none():
    assert _normalize_recipients(None) == []


def test_normalize_recipients_string():
    assert _normalize_recipients("alice@example.com") == ["alice@example.com"]


def test_normalize_recipients_list():
    assert _normalize_recipients(["a@b.com", "c@d.com"]) == ["a@b.com", "c@d.com"]


# -------------------------------------------------------------------
# _build_message()
# -------------------------------------------------------------------


def test_build_message_plain():
    msg = _build_message(
        from_addr="sender@test.com",
        to_list=["alice@test.com"],
        subject="Test",
        body="Hello",
    )
    assert isinstance(msg, MIMEText)
    assert msg["From"] == "sender@test.com"
    assert msg["To"] == "alice@test.com"
    assert msg["Subject"] == "Test"


def test_build_message_html():
    msg = _build_message(
        from_addr="sender@test.com",
        to_list=["alice@test.com"],
        subject="HTML",
        body="Plain",
        body_html="<b>HTML</b>",
    )
    assert isinstance(msg, MIMEMultipart)
    parts = msg.get_payload()
    assert len(parts) == 2


def test_build_message_cc_and_reply_to():
    msg = _build_message(
        from_addr="sender@test.com",
        to_list=["alice@test.com"],
        subject="Test",
        body="Hi",
        cc_list=["cc@test.com"],
        reply_to="reply@test.com",
    )
    assert "cc@test.com" in msg["Cc"]
    assert msg["Reply-To"] == "reply@test.com"


def test_build_message_no_bcc_header():
    """_build_message should never include a Bcc header."""
    msg = _build_message(
        from_addr="sender@test.com",
        to_list=["alice@test.com"],
        subject="Test",
        body="Hi",
    )
    assert msg["Bcc"] is None


# -------------------------------------------------------------------
# to: str vs list
# -------------------------------------------------------------------


async def test_send_email_to_string(settings: Settings, mock_smtp):
    """to as a single string should send to that one recipient."""
    smtp_cls, smtp_instance = mock_smtp
    client = SMTPClient(settings)

    result = await client.send_email(
        to="alice@example.com",
        subject="Hello",
        body="Hi Alice",
    )

    assert result is True
    smtp_instance.send_message.assert_awaited_once()
    call_kwargs = smtp_instance.send_message.call_args
    assert call_kwargs.kwargs["recipients"] == ["alice@example.com"]


async def test_send_email_to_list(settings: Settings, mock_smtp):
    """to as a list should send to all recipients."""
    smtp_cls, smtp_instance = mock_smtp
    client = SMTPClient(settings)

    result = await client.send_email(
        to=["alice@example.com", "bob@example.com"],
        subject="Hello",
        body="Hi all",
    )

    assert result is True
    call_kwargs = smtp_instance.send_message.call_args
    recipients = call_kwargs.kwargs["recipients"]
    assert "alice@example.com" in recipients
    assert "bob@example.com" in recipients


# -------------------------------------------------------------------
# cc and bcc
# -------------------------------------------------------------------


async def test_send_email_with_cc_and_bcc(settings: Settings, mock_smtp):
    """cc and bcc should be in all_recipients, but bcc NOT in headers."""
    smtp_cls, smtp_instance = mock_smtp
    client = SMTPClient(settings)

    await client.send_email(
        to="alice@example.com",
        subject="Hello",
        body="Hi",
        cc="charlie@example.com",
        bcc="secret@example.com",
    )

    call_args = smtp_instance.send_message.call_args
    recipients = call_args.kwargs["recipients"]
    assert "alice@example.com" in recipients
    assert "charlie@example.com" in recipients
    assert "secret@example.com" in recipients

    # Verify the message object does NOT have Bcc header
    msg = call_args.args[0]
    assert msg["Bcc"] is None
    # But Cc header IS set
    assert "charlie@example.com" in msg["Cc"]


# -------------------------------------------------------------------
# body_html → MIMEMultipart/alternative
# -------------------------------------------------------------------


async def test_send_email_with_html(settings: Settings, mock_smtp):
    """body_html should produce MIMEMultipart/alternative."""
    smtp_cls, smtp_instance = mock_smtp
    client = SMTPClient(settings)

    await client.send_email(
        to="alice@example.com",
        subject="HTML mail",
        body="Plain text",
        body_html="<h1>HTML</h1>",
    )

    msg = smtp_instance.send_message.call_args.args[0]
    assert isinstance(msg, MIMEMultipart)
    assert msg.get_content_subtype() == "alternative"
    parts = msg.get_payload()
    assert len(parts) == 2
    assert parts[0].get_content_type() == "text/plain"
    assert parts[1].get_content_type() == "text/html"


async def test_send_email_plain_only(settings: Settings, mock_smtp):
    """Without body_html, message should be plain MIMEText."""
    smtp_cls, smtp_instance = mock_smtp
    client = SMTPClient(settings)

    await client.send_email(
        to="alice@example.com",
        subject="Plain mail",
        body="Just text",
    )

    msg = smtp_instance.send_message.call_args.args[0]
    assert isinstance(msg, MIMEText)
    assert msg.get_content_type() == "text/plain"


# -------------------------------------------------------------------
# reply_to
# -------------------------------------------------------------------


async def test_send_email_with_reply_to(settings: Settings, mock_smtp):
    """reply_to should set Reply-To header."""
    smtp_cls, smtp_instance = mock_smtp
    client = SMTPClient(settings)

    await client.send_email(
        to="alice@example.com",
        subject="Reply test",
        body="Hi",
        reply_to="noreply@example.com",
    )

    msg = smtp_instance.send_message.call_args.args[0]
    assert msg["Reply-To"] == "noreply@example.com"


# -------------------------------------------------------------------
# login credentials
# -------------------------------------------------------------------


async def test_login_called_with_correct_credentials(settings: Settings, mock_smtp):
    """login() should be called with username and password from settings."""
    smtp_cls, smtp_instance = mock_smtp
    client = SMTPClient(settings)

    await client.send_email(
        to="alice@example.com",
        subject="Test",
        body="Test",
    )

    smtp_instance.login.assert_awaited_once_with("user@proton.me", "bridge-password")


# -------------------------------------------------------------------
# send_message recipients
# -------------------------------------------------------------------


async def test_send_message_called_with_all_recipients(settings: Settings, mock_smtp):
    """send_message() should receive all recipients (to + cc + bcc)."""
    smtp_cls, smtp_instance = mock_smtp
    client = SMTPClient(settings)

    await client.send_email(
        to="alice@example.com",
        subject="Test",
        body="Test",
        cc=["charlie@example.com"],
        bcc=["secret@example.com"],
    )

    call_kwargs = smtp_instance.send_message.call_args
    recipients = call_kwargs.kwargs["recipients"]
    assert sorted(recipients) == sorted([
        "alice@example.com",
        "charlie@example.com",
        "secret@example.com",
    ])


# -------------------------------------------------------------------
# Privacy: recipient addresses must NOT be logged
# -------------------------------------------------------------------


async def test_recipient_addresses_not_logged(settings: Settings, mock_smtp, caplog):
    """Recipient email addresses must NOT appear in log output (BCC privacy)."""
    smtp_cls, smtp_instance = mock_smtp
    client = SMTPClient(settings)

    with caplog.at_level(logging.DEBUG, logger="protonmail_mcp.smtp_client"):
        await client.send_email(
            to="alice@example.com",
            subject="Privacy test",
            body="Secret",
            cc="charlie@example.com",
            bcc="secret@example.com",
        )

    log_text = caplog.text
    assert "alice@example.com" not in log_text
    assert "charlie@example.com" not in log_text
    assert "secret@example.com" not in log_text
    # But number of recipients should be logged
    assert "3" in log_text or "3 mottagare" in log_text
