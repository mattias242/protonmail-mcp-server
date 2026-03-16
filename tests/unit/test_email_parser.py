"""Tester for email_parser.py."""
from protonmail_mcp.email_parser import (
    decode_header_value,
    extract_addresses,
    get_attachments,
    parse_email,
)


class TestParseEmailPlainText:
    """parse_email() med plain-text mail."""

    def test_subject(self, sample_raw_email):
        result = parse_email(sample_raw_email)
        assert result["subject"] == "Test subject"

    def test_from(self, sample_raw_email):
        result = parse_email(sample_raw_email)
        assert result["from"] == "sender@example.com"

    def test_to(self, sample_raw_email):
        result = parse_email(sample_raw_email)
        assert result["to"] == ["recipient@example.com"]

    def test_body_plain(self, sample_raw_email):
        result = parse_email(sample_raw_email)
        assert "Hello world!" in result["body_plain"]

    def test_body_html_empty(self, sample_raw_email):
        result = parse_email(sample_raw_email)
        assert result["body_html"] == ""


class TestParseEmailHtml:
    """parse_email() med HTML-only mail."""

    def test_body_plain_empty(self, sample_html_email):
        result = parse_email(sample_html_email)
        assert result["body_plain"] == ""

    def test_body_html_contains_html(self, sample_html_email):
        result = parse_email(sample_html_email)
        assert "<html>" in result["body_html"]
        assert "<p>Hello!</p>" in result["body_html"]


class TestParseEmailMultipart:
    """parse_email() med multipart mail."""

    def test_body_plain_populated(self, sample_multipart_email):
        result = parse_email(sample_multipart_email)
        assert "Plain text" in result["body_plain"]

    def test_body_html_populated(self, sample_multipart_email):
        result = parse_email(sample_multipart_email)
        assert "<p>HTML</p>" in result["body_html"]

    def test_multiple_to(self, sample_multipart_email):
        result = parse_email(sample_multipart_email)
        assert len(result["to"]) == 2
        assert "a@b.com" in result["to"]
        assert "c@d.com" in result["to"]

    def test_cc_parsed(self, sample_multipart_email):
        result = parse_email(sample_multipart_email)
        assert len(result["cc"]) == 1
        assert "e@f.com" in result["cc"]


class TestDecodeHeaderValue:
    """decode_header_value() med diverse input."""

    def test_plain_string(self):
        assert decode_header_value("Hello") == "Hello"

    def test_none_returns_empty(self):
        assert decode_header_value(None) == ""

    def test_empty_string_returns_empty(self):
        assert decode_header_value("") == ""

    def test_rfc2047_encoded(self):
        encoded = "=?utf-8?B?SMOkbHNuaW5nYXI=?="
        result = decode_header_value(encoded)
        assert result == "H\u00e4lsningar"


class TestExtractAddresses:
    """extract_addresses() med diverse format."""

    def test_name_and_email(self):
        result = extract_addresses("John Doe <john@example.com>")
        assert len(result) == 1
        assert "John Doe <john@example.com>" in result[0]

    def test_email_only(self):
        result = extract_addresses("john@example.com")
        assert result == ["john@example.com"]

    def test_none_returns_empty(self):
        assert extract_addresses(None) == []

    def test_multiple_addresses(self):
        result = extract_addresses("a@b.com, c@d.com")
        assert len(result) == 2


class TestGetAttachments:
    """get_attachments() tester."""

    def test_no_attachments(self, sample_raw_email):
        import email as email_mod
        msg = email_mod.message_from_bytes(sample_raw_email)
        result = get_attachments(msg)
        assert result == []

    def test_no_attachments_multipart(self, sample_multipart_email):
        import email as email_mod
        msg = email_mod.message_from_bytes(sample_multipart_email)
        result = get_attachments(msg)
        assert result == []


class TestParseEmailEdgeCases:
    """Felhantering och edge cases."""

    def test_empty_bytes_no_crash(self):
        result = parse_email(b"")
        assert isinstance(result, dict)
        assert result["subject"] == ""
        assert result["body_plain"] == ""

    def test_message_id(self, sample_raw_email):
        result = parse_email(sample_raw_email)
        assert result["message_id"] == "<test-id@example.com>"

    def test_attachments_empty_for_plain(self, sample_raw_email):
        result = parse_email(sample_raw_email)
        assert result["attachments"] == []

    def test_corrupt_mime_data(self):
        """Korrupt MIME-data (garbage bytes) ska inte krascha."""
        garbage = b"\x80\x81\x82\xff\xfe\x00\x01random garbage data"
        result = parse_email(garbage)
        assert isinstance(result, dict)
        assert "subject" in result
        assert "body_plain" in result

    def test_unknown_charset(self):
        """Meddelande med okänd charset ska hanteras utan krasch."""
        raw = (
            "From: sender@example.com\r\n"
            "To: recipient@example.com\r\n"
            "Subject: Unknown charset\r\n"
            "Content-Type: text/plain; charset=unknown-8bit\r\n"
            "\r\n"
            "Some content\r\n"
        ).encode("utf-8")
        result = parse_email(raw)
        assert isinstance(result, dict)
        # Ska kunna parsa utan krasch — body kan vara tom eller innehålla data
        assert "body_plain" in result

    def test_multipart_without_text_plain(self):
        """Multipart-meddelande utan text/plain — body_plain ska vara tom."""
        raw = (
            "From: sender@example.com\r\n"
            "To: recipient@example.com\r\n"
            "Subject: HTML only multipart\r\n"
            "MIME-Version: 1.0\r\n"
            'Content-Type: multipart/alternative; boundary="bnd"\r\n'
            "\r\n"
            "--bnd\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            "\r\n"
            "<p>Only HTML here</p>\r\n"
            "--bnd--\r\n"
        ).encode("utf-8")
        result = parse_email(raw)
        assert result["body_plain"] == ""
        assert "<p>Only HTML here</p>" in result["body_html"]
