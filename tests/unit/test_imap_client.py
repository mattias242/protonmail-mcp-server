"""Tester för imap_client.py — validering, escaping, parsning och IMAP-operationer."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio
from types import SimpleNamespace

from protonmail_mcp.imap_client import (
    _validate_uid,
    _escape_imap_string,
    _parse_seqnums,
    _parse_fetch_metadata,
    _parse_list_line,
    _parse_headers,
    IMAPClient,
)


# ---------------------------------------------------------------------------
# _validate_uid
# ---------------------------------------------------------------------------
class TestValidateUid:
    def test_valid_numeric_uid(self):
        assert _validate_uid("123") == "123"

    def test_rejects_sequence_set(self):
        with pytest.raises(ValueError, match="Ogiltigt UID"):
            _validate_uid("1:*")

    def test_rejects_injection_attempt(self):
        with pytest.raises(ValueError, match="Ogiltigt UID"):
            _validate_uid("123 STORE")

    def test_rejects_empty_string(self):
        with pytest.raises(ValueError, match="Ogiltigt UID"):
            _validate_uid("")

    def test_rejects_negative(self):
        with pytest.raises(ValueError, match="Ogiltigt UID"):
            _validate_uid("-1")


# ---------------------------------------------------------------------------
# _escape_imap_string
# ---------------------------------------------------------------------------
class TestEscapeImapString:
    def test_normal_string_unchanged(self):
        assert _escape_imap_string("normal") == "normal"

    def test_escapes_double_quote(self):
        assert _escape_imap_string('with"quote') == 'with\\"quote'

    def test_escapes_backslash(self):
        assert _escape_imap_string("with\\back") == "with\\\\back"

    def test_escapes_both(self):
        assert _escape_imap_string('a\\"b') == 'a\\\\\\"b'


# ---------------------------------------------------------------------------
# _parse_seqnums
# ---------------------------------------------------------------------------
class TestParseSeqnums:
    def test_ok_response_with_numbers(self):
        resp = SimpleNamespace(result="OK", lines=[b"1 2 3 45"])
        assert _parse_seqnums(resp) == ["1", "2", "3", "45"]

    def test_filters_non_digits(self):
        resp = SimpleNamespace(result="OK", lines=[b"1 SEARCH 2 3"])
        assert _parse_seqnums(resp) == ["1", "2", "3"]

    def test_not_ok_returns_empty(self):
        resp = SimpleNamespace(result="NO", lines=[b"1 2 3"])
        assert _parse_seqnums(resp) == []

    def test_empty_lines_returns_empty(self):
        resp = SimpleNamespace(result="OK", lines=[])
        assert _parse_seqnums(resp) == []


# ---------------------------------------------------------------------------
# _parse_list_line
# ---------------------------------------------------------------------------
class TestParseListLine:
    def test_quoted_name(self):
        assert _parse_list_line('(\\HasNoChildren) "/" "INBOX"') == "INBOX"

    def test_unquoted_name(self):
        assert _parse_list_line('(\\HasNoChildren) "/" Sent') == "Sent"

    def test_subfolder(self):
        assert _parse_list_line('(\\HasNoChildren) "/" "Folders/Sub"') == "Folders/Sub"

    def test_command_line_skipped(self):
        assert _parse_list_line("command completed") is None

    def test_empty_line_skipped(self):
        assert _parse_list_line("") is None

    def test_non_matching_line(self):
        assert _parse_list_line("garbage data") is None


# ---------------------------------------------------------------------------
# _parse_headers
# ---------------------------------------------------------------------------
class TestParseHeaders:
    def test_parses_basic_headers(self):
        raw = b"Subject: Hello\r\nFrom: a@b.com\r\n"
        result = _parse_headers(raw)
        assert result["subject"] == "Hello"
        assert result["from"] == "a@b.com"

    def test_empty_bytes(self):
        assert _parse_headers(b"") == {}

    def test_header_with_colon_in_value(self):
        raw = b"Subject: Re: Hello\r\n"
        result = _parse_headers(raw)
        assert result["subject"] == "Re: Hello"


# ---------------------------------------------------------------------------
# _parse_fetch_metadata
# ---------------------------------------------------------------------------
class TestParseFetchMetadata:
    def test_parses_uid_flags_and_headers(self):
        lines = [
            '1 FETCH (UID 42 FLAGS (\\Seen \\Flagged) BODY[HEADER.FIELDS (DATE FROM TO SUBJECT MESSAGE-ID)] {120}',
            bytearray(
                b"Subject: Test mail\r\n"
                b"From: alice@example.com\r\n"
                b"To: bob@example.com\r\n"
                b"Date: Mon, 16 Mar 2026 10:00:00 +0100\r\n"
                b"Message-ID: <abc@example.com>\r\n"
            ),
            ")",
        ]
        result = _parse_fetch_metadata(lines)
        assert len(result) == 1
        msg = result[0]
        assert msg["uid"] == "42"
        assert "\\Seen" in msg["flags"]
        assert "\\Flagged" in msg["flags"]
        assert msg["subject"] == "Test mail"
        assert msg["from"] == "alice@example.com"
        assert msg["to"] == "bob@example.com"
        assert msg["date"] == "Mon, 16 Mar 2026 10:00:00 +0100"
        assert msg["message_id"] == "<abc@example.com>"

    def test_multiple_messages(self):
        lines = [
            '1 FETCH (UID 10 FLAGS () BODY[HEADER.FIELDS (DATE FROM TO SUBJECT MESSAGE-ID)] {50}',
            bytearray(b"Subject: First\r\nFrom: a@b.com\r\n"),
            ")",
            '2 FETCH (UID 20 FLAGS (\\Seen) BODY[HEADER.FIELDS (DATE FROM TO SUBJECT MESSAGE-ID)] {50}',
            bytearray(b"Subject: Second\r\nFrom: c@d.com\r\n"),
            ")",
        ]
        result = _parse_fetch_metadata(lines)
        assert len(result) == 2
        assert result[0]["uid"] == "10"
        assert result[1]["uid"] == "20"

    def test_no_header_bytearray(self):
        """FETCH-rad utan efterföljande bytearray — headers ska vara tomma."""
        lines = [
            '1 FETCH (UID 99 FLAGS (\\Seen) BODY[HEADER.FIELDS (DATE FROM TO SUBJECT MESSAGE-ID)] {0}',
            ")",
        ]
        result = _parse_fetch_metadata(lines)
        assert len(result) == 1
        assert result[0]["uid"] == "99"
        assert result[0]["subject"] == ""


# ---------------------------------------------------------------------------
# IMAPClient.list_mailboxes
# ---------------------------------------------------------------------------
class TestListMailboxes:
    @pytest.fixture
    def client(self, monkeypatch):
        monkeypatch.setenv("PROTONMAIL_USERNAME", "user")
        monkeypatch.setenv("PROTONMAIL_PASSWORD", "pass")
        from protonmail_mcp.config import Settings
        settings = Settings()
        imap = IMAPClient(settings)
        imap._client = AsyncMock()
        return imap

    @pytest.mark.asyncio
    async def test_parses_mailbox_names(self, client):
        client._client.list = AsyncMock(return_value=SimpleNamespace(
            result="OK",
            lines=[
                '(\\HasNoChildren) "/" "INBOX"',
                '(\\HasNoChildren) "/" "Sent"',
                '(\\HasNoChildren) "/" "Folders/Sub"',
                'command completed',
            ],
        ))
        result = await client.list_mailboxes()
        names = [m["name"] for m in result]
        assert "INBOX" in names
        assert "Sent" in names
        assert "Folders/Sub" in names

    @pytest.mark.asyncio
    async def test_empty_on_failure(self, client):
        client._client.list = AsyncMock(return_value=SimpleNamespace(
            result="NO",
            lines=[],
        ))
        result = await client.list_mailboxes()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_mailboxes_returns_type_folder(self, client):
        """Vanlig mapp ska ha type='folder'."""
        client._client.list = AsyncMock(return_value=SimpleNamespace(
            result="OK",
            lines=[
                '(\\HasNoChildren) "/" "INBOX"',
                'command completed',
            ],
        ))
        result = await client.list_mailboxes()
        assert len(result) == 1
        assert result[0] == {"name": "INBOX", "type": "folder"}

    @pytest.mark.asyncio
    async def test_list_mailboxes_returns_type_label(self, client):
        """Labels/Arbete ska ha type='label'."""
        client._client.list = AsyncMock(return_value=SimpleNamespace(
            result="OK",
            lines=[
                '(\\HasNoChildren) "/" "Labels/Arbete"',
                'command completed',
            ],
        ))
        result = await client.list_mailboxes()
        assert len(result) == 1
        assert result[0] == {"name": "Labels/Arbete", "type": "label"}

    @pytest.mark.asyncio
    async def test_list_mailboxes_mixed(self, client):
        """Blandat mappar och labels ska ge rätt type på alla."""
        client._client.list = AsyncMock(return_value=SimpleNamespace(
            result="OK",
            lines=[
                '(\\HasNoChildren) "/" "INBOX"',
                '(\\HasNoChildren) "/" "Sent"',
                '(\\HasNoChildren) "/" "Labels/Arbete"',
                '(\\HasNoChildren) "/" "Labels/Privat"',
                '(\\HasNoChildren) "/" "Folders/Projekt"',
                'command completed',
            ],
        ))
        result = await client.list_mailboxes()
        assert len(result) == 5
        types = {m["name"]: m["type"] for m in result}
        assert types["INBOX"] == "folder"
        assert types["Sent"] == "folder"
        assert types["Labels/Arbete"] == "label"
        assert types["Labels/Privat"] == "label"
        assert types["Folders/Projekt"] == "folder"


# ---------------------------------------------------------------------------
# IMAPClient.get_message
# ---------------------------------------------------------------------------
class TestGetMessage:
    @pytest.fixture
    def client(self, monkeypatch):
        monkeypatch.setenv("PROTONMAIL_USERNAME", "user")
        monkeypatch.setenv("PROTONMAIL_PASSWORD", "pass")
        from protonmail_mcp.config import Settings
        settings = Settings()
        imap = IMAPClient(settings)
        imap._client = AsyncMock()
        return imap

    @pytest.mark.asyncio
    async def test_returns_bytes_from_bytearray(self, client):
        raw_email = bytearray(b"From: a@b.com\r\nSubject: Hi\r\n\r\nBody here")
        client._client.uid = AsyncMock(return_value=SimpleNamespace(
            result="OK",
            lines=[
                '1 FETCH (BODY[] {40}',
                raw_email,
                ")",
            ],
        ))
        result = await client.get_message("INBOX", "42")
        assert isinstance(result, bytes)
        assert result == bytes(raw_email)

    @pytest.mark.asyncio
    async def test_returns_none_on_failure(self, client):
        client._client.uid = AsyncMock(return_value=SimpleNamespace(
            result="NO",
            lines=[],
        ))
        result = await client.get_message("INBOX", "42")
        assert result is None

    @pytest.mark.asyncio
    async def test_validates_uid(self, client):
        with pytest.raises(ValueError, match="Ogiltigt UID"):
            await client.get_message("INBOX", "1:*")

    @pytest.mark.asyncio
    async def test_returns_none_when_ok_but_no_bytearray(self, client):
        """OK-respons utan bytearray-data ska returnera None."""
        client._client.uid = AsyncMock(return_value=SimpleNamespace(
            result="OK",
            lines=[
                '1 FETCH (BODY[] {0}',
                ")",
            ],
        ))
        result = await client.get_message("INBOX", "42")
        assert result is None


# ---------------------------------------------------------------------------
# IMAPClient.search_messages — escaping
# ---------------------------------------------------------------------------
class TestSearchMessages:
    @pytest.fixture
    def client(self, monkeypatch):
        monkeypatch.setenv("PROTONMAIL_USERNAME", "user")
        monkeypatch.setenv("PROTONMAIL_PASSWORD", "pass")
        from protonmail_mcp.config import Settings
        settings = Settings()
        imap = IMAPClient(settings)
        imap._client = AsyncMock()
        return imap

    @pytest.mark.asyncio
    async def test_escapes_from_addr_with_quote(self, client):
        """from_addr med " ska escapas korrekt i IMAP-sökkriterierna."""
        client._client.search = AsyncMock(return_value=SimpleNamespace(
            result="OK",
            lines=[b"1"],
        ))
        client._client.fetch = AsyncMock(return_value=SimpleNamespace(
            result="OK",
            lines=['1 FETCH (UID 100)'],
        ))
        await client.search_messages("INBOX", from_addr='user"name@example.com')
        # Verifiera att search anropades med escaped sträng
        call_args = client._client.search.call_args[0][0]
        assert '\\"' in call_args
        assert 'user\\"name@example.com' in call_args


# ---------------------------------------------------------------------------
# IMAPClient.delete_message — UID-validering
# ---------------------------------------------------------------------------
class TestDeleteMessage:
    @pytest.fixture
    def client(self, monkeypatch):
        monkeypatch.setenv("PROTONMAIL_USERNAME", "user")
        monkeypatch.setenv("PROTONMAIL_PASSWORD", "pass")
        from protonmail_mcp.config import Settings
        settings = Settings()
        imap = IMAPClient(settings)
        imap._client = AsyncMock()
        return imap

    @pytest.mark.asyncio
    async def test_rejects_invalid_uid(self, client):
        with pytest.raises(ValueError, match="Ogiltigt UID"):
            await client.delete_message("INBOX", "1:*")

    @pytest.mark.asyncio
    async def test_successful_delete(self, client):
        client._client.uid = AsyncMock(return_value=SimpleNamespace(result="OK"))
        client._client.expunge = AsyncMock(return_value=SimpleNamespace(result="OK"))
        result = await client.delete_message("INBOX", "42")
        assert result is True


# ---------------------------------------------------------------------------
# IMAPClient.connect — error paths
# ---------------------------------------------------------------------------
class TestConnectErrors:
    @pytest.fixture
    def client(self, monkeypatch):
        monkeypatch.setenv("PROTONMAIL_USERNAME", "user")
        monkeypatch.setenv("PROTONMAIL_PASSWORD", "pass")
        from protonmail_mcp.config import Settings
        settings = Settings()
        return IMAPClient(settings)

    @pytest.mark.asyncio
    async def test_timeout_during_connect(self, client, monkeypatch):
        """asyncio.TimeoutError från wait_hello_from_server ska bubbla upp."""
        import asyncio

        mock_imap = AsyncMock()
        mock_imap.wait_hello_from_server = AsyncMock(side_effect=asyncio.TimeoutError)

        with patch("protonmail_mcp.imap_client.aioimaplib.IMAP4", return_value=mock_imap):
            with pytest.raises(asyncio.TimeoutError):
                await client.connect()

    @pytest.mark.asyncio
    async def test_login_failure(self, client, monkeypatch):
        """Login som kastar exception ska bubbla upp."""
        mock_imap = AsyncMock()
        mock_imap.wait_hello_from_server = AsyncMock()
        mock_imap.login = AsyncMock(side_effect=Exception("LOGIN failed"))

        with patch("protonmail_mcp.imap_client.aioimaplib.IMAP4", return_value=mock_imap):
            with pytest.raises(Exception, match="LOGIN failed"):
                await client.connect()


# ---------------------------------------------------------------------------
# IMAPClient.list_mailboxes — BAD result
# ---------------------------------------------------------------------------
class TestListMailboxesBadResult:
    @pytest.fixture
    def client(self, monkeypatch):
        monkeypatch.setenv("PROTONMAIL_USERNAME", "user")
        monkeypatch.setenv("PROTONMAIL_PASSWORD", "pass")
        from protonmail_mcp.config import Settings
        settings = Settings()
        imap = IMAPClient(settings)
        imap._client = AsyncMock()
        return imap

    @pytest.mark.asyncio
    async def test_bad_result_returns_empty(self, client):
        """list() med result='BAD' ska returnera tom lista."""
        client._client.list = AsyncMock(return_value=SimpleNamespace(
            result="BAD",
            lines=["some error"],
        ))
        result = await client.list_mailboxes()
        assert result == []


# ---------------------------------------------------------------------------
# IMAPClient.search_messages — empty search response
# ---------------------------------------------------------------------------
class TestSearchMessagesEmpty:
    @pytest.fixture
    def client(self, monkeypatch):
        monkeypatch.setenv("PROTONMAIL_USERNAME", "user")
        monkeypatch.setenv("PROTONMAIL_PASSWORD", "pass")
        from protonmail_mcp.config import Settings
        settings = Settings()
        imap = IMAPClient(settings)
        imap._client = AsyncMock()
        return imap

    @pytest.mark.asyncio
    async def test_empty_search_returns_empty(self, client):
        """search som returnerar OK men tom rad ska ge tom lista."""
        client._client.search = AsyncMock(return_value=SimpleNamespace(
            result="OK",
            lines=[b""],
        ))
        result = await client.search_messages("INBOX")
        assert result == []

    @pytest.mark.asyncio
    async def test_search_no_result_returns_empty(self, client):
        """search som returnerar NO ska ge tom lista."""
        client._client.search = AsyncMock(return_value=SimpleNamespace(
            result="NO",
            lines=[],
        ))
        result = await client.search_messages("INBOX", from_addr="nobody@example.com")
        assert result == []
