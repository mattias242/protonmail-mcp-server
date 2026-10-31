"""Gemensamma fixtures för alla tester."""
import asyncio.base_events

import pytest

# Bridges standardportar (IMAP, SMTP). Enhets- och BDD-tester får aldrig nå
# dem: en mock som faller igenom skulle annars logga in mot riktiga Bridge.
_BRIDGE_PORTS = {1143, 1026}


@pytest.fixture(autouse=True)
def _block_bridge_ports(request, monkeypatch):
    if request.node.get_closest_marker("integration"):
        return
    original = asyncio.base_events.BaseEventLoop.create_connection

    async def guarded(self, protocol_factory, host=None, port=None, *args, **kwargs):
        if port in _BRIDGE_PORTS:
            raise ConnectionRefusedError(
                f"Anslutning till {host}:{port} är blockerad i tester (ej integration)"
            )
        return await original(self, protocol_factory, host, port, *args, **kwargs)

    monkeypatch.setattr(asyncio.base_events.BaseEventLoop, "create_connection", guarded)


@pytest.fixture(autouse=True)
def _default_language():
    """Varje test börjar på svenska: ett test som bytt språk (PROTONMAIL_LANGUAGE=en,
    i18n.set_language) får inte färga nästa."""
    yield
    import sys

    from protonmail_mcp import i18n

    i18n.set_language(i18n.DEFAULT)
    server = sys.modules.get("protonmail_mcp.server")
    if server is not None:
        server.apply_language(i18n.DEFAULT)


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


@pytest.fixture
def sample_html_email():
    """E-post med enbart HTML-body."""
    return (
        "From: sender@example.com\r\n"
        "To: recipient@example.com\r\n"
        "Subject: HTML mail\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        "\r\n"
        "<html><body><p>Hello!</p></body></html>\r\n"
    ).encode("utf-8")


@pytest.fixture
def sample_multipart_email():
    """E-post med plain + HTML."""
    return (
        "From: sender@example.com\r\n"
        "To: a@b.com, c@d.com\r\n"
        "Cc: e@f.com\r\n"
        "Subject: Multipart\r\n"
        "MIME-Version: 1.0\r\n"
        'Content-Type: multipart/alternative; boundary="boundary"\r\n'
        "\r\n"
        "--boundary\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n"
        "Plain text\r\n"
        "--boundary\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        "\r\n"
        "<p>HTML</p>\r\n"
        "--boundary--\r\n"
    ).encode("utf-8")
