"""Gemensamma fixtures för alla tester."""
import pytest


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
