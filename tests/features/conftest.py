"""Gemensamma fixtures för BDD-scenarion."""
import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.fixture
def mock_ctx():
    """Skapa en mock MCP Context med AppContext (imap + smtp)."""
    ctx = MagicMock()
    ctx.request_context.lifespan_context.imap = AsyncMock()
    ctx.request_context.lifespan_context.smtp = AsyncMock()
    return ctx


@pytest.fixture
def sample_raw_email():
    """Ett minimalt RFC822-meddelande för parsertester."""
    return (
        "From: sender@example.com\r\n"
        "To: recipient@example.com\r\n"
        "Subject: Test subject\r\n"
        "Date: Sun, 16 Mar 2026 10:00:00 +0100\r\n"
        "Message-ID: <test-id@example.com>\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n"
        "Hello world!\r\n"
    ).encode("utf-8")
