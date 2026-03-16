"""Unit-tester för MCP tool-funktioner i tools/."""
import pytest
from unittest.mock import MagicMock, AsyncMock

from protonmail_mcp.tools.mailboxes import list_mailboxes, get_mailbox_status
from protonmail_mcp.tools.messages import list_emails, get_email
from protonmail_mcp.tools.search import search_emails
from protonmail_mcp.tools.manage import (
    mark_email_read,
    mark_email_unread,
    move_email,
    delete_email,
)
from protonmail_mcp.tools.send import send_email


@pytest.fixture
def mock_ctx():
    """Skapa en mock Context med AppContext (imap + smtp)."""
    ctx = MagicMock()
    ctx.request_context.lifespan_context.imap = AsyncMock()
    ctx.request_context.lifespan_context.smtp = AsyncMock()
    return ctx


# --- mailboxes.py ---


class TestListMailboxes:
    async def test_delegates_to_imap(self, mock_ctx):
        mock_ctx.request_context.lifespan_context.imap.list_mailboxes.return_value = [
            {"name": "INBOX"}, {"name": "Sent"}
        ]
        result = await list_mailboxes(mock_ctx)
        mock_ctx.request_context.lifespan_context.imap.list_mailboxes.assert_called_once()
        assert result == [{"name": "INBOX"}, {"name": "Sent"}]


class TestGetMailboxStatus:
    async def test_delegates_with_mailbox_arg(self, mock_ctx):
        mock_ctx.request_context.lifespan_context.imap.get_mailbox_status.return_value = {
            "messages": 42, "unseen": 3
        }
        result = await get_mailbox_status(mock_ctx, mailbox="Drafts")
        mock_ctx.request_context.lifespan_context.imap.get_mailbox_status.assert_called_once_with("Drafts")
        assert result == {"messages": 42, "unseen": 3}

    async def test_default_mailbox_is_inbox(self, mock_ctx):
        mock_ctx.request_context.lifespan_context.imap.get_mailbox_status.return_value = {}
        await get_mailbox_status(mock_ctx)
        mock_ctx.request_context.lifespan_context.imap.get_mailbox_status.assert_called_once_with("INBOX")


# --- messages.py ---


class TestListEmails:
    async def test_delegates_with_page_args(self, mock_ctx):
        mock_ctx.request_context.lifespan_context.imap.list_messages.return_value = {
            "messages": [{"uid": "1"}], "total": 1, "page": 2, "pages": 3, "has_more": True
        }
        result = await list_emails(mock_ctx, mailbox="Sent", page=2, page_size=10)
        mock_ctx.request_context.lifespan_context.imap.list_messages.assert_called_once_with(
            "Sent", page=2, page_size=10
        )
        assert result["page"] == 2
        assert result["messages"] == [{"uid": "1"}]

    async def test_default_args(self, mock_ctx):
        mock_ctx.request_context.lifespan_context.imap.list_messages.return_value = {
            "messages": [], "total": 0, "page": 1, "pages": 0, "has_more": False
        }
        await list_emails(mock_ctx)
        mock_ctx.request_context.lifespan_context.imap.list_messages.assert_called_once_with(
            "INBOX", page=1, page_size=20
        )


class TestGetEmail:
    async def test_returns_none_when_raw_is_none(self, mock_ctx):
        mock_ctx.request_context.lifespan_context.imap.get_message.return_value = None
        result = await get_email(mock_ctx, uid="123", mailbox="INBOX")
        mock_ctx.request_context.lifespan_context.imap.get_message.assert_called_once_with("INBOX", "123")
        assert result is None

    async def test_returns_parsed_dict_when_mail_exists(self, mock_ctx, sample_raw_email):
        mock_ctx.request_context.lifespan_context.imap.get_message.return_value = sample_raw_email
        result = await get_email(mock_ctx, uid="456")
        assert isinstance(result, dict)
        assert result["subject"] == "Test subject"
        assert result["from"] == "sender@example.com"
        assert "Hello world!" in result["body_plain"]


# --- search.py ---


class TestSearchEmails:
    async def test_delegates_all_filter_params(self, mock_ctx):
        mock_ctx.request_context.lifespan_context.imap.search_messages.return_value = ["1", "2", "3"]
        result = await search_emails(
            mock_ctx,
            mailbox="INBOX",
            from_addr="alice@example.com",
            subject="hello",
            since="01-Jan-2024",
            before="01-Feb-2024",
            unseen=True,
        )
        mock_ctx.request_context.lifespan_context.imap.search_messages.assert_called_once_with(
            "INBOX",
            from_addr="alice@example.com",
            subject="hello",
            since="01-Jan-2024",
            before="01-Feb-2024",
            unseen=True,
        )
        assert result == ["1", "2", "3"]

    async def test_defaults_none_filters(self, mock_ctx):
        mock_ctx.request_context.lifespan_context.imap.search_messages.return_value = []
        await search_emails(mock_ctx)
        mock_ctx.request_context.lifespan_context.imap.search_messages.assert_called_once_with(
            "INBOX",
            from_addr=None,
            subject=None,
            since=None,
            before=None,
            unseen=None,
        )


# --- manage.py ---


class TestMarkEmailRead:
    async def test_calls_set_flags_with_add_true(self, mock_ctx):
        mock_ctx.request_context.lifespan_context.imap.set_flags.return_value = True
        result = await mark_email_read(mock_ctx, uid="10", mailbox="INBOX")
        mock_ctx.request_context.lifespan_context.imap.set_flags.assert_called_once_with(
            "INBOX", "10", r"(\Seen)", add=True
        )
        assert result is True


class TestMarkEmailUnread:
    async def test_calls_set_flags_with_add_false(self, mock_ctx):
        mock_ctx.request_context.lifespan_context.imap.set_flags.return_value = True
        result = await mark_email_unread(mock_ctx, uid="10", mailbox="INBOX")
        mock_ctx.request_context.lifespan_context.imap.set_flags.assert_called_once_with(
            "INBOX", "10", r"(\Seen)", add=False
        )
        assert result is True


class TestMoveEmail:
    async def test_delegates_uid_target_and_source(self, mock_ctx):
        mock_ctx.request_context.lifespan_context.imap.move_message.return_value = True
        result = await move_email(mock_ctx, uid="5", target_mailbox="Archive", mailbox="INBOX")
        mock_ctx.request_context.lifespan_context.imap.move_message.assert_called_once_with(
            "INBOX", "5", "Archive"
        )
        assert result is True


class TestDeleteEmail:
    async def test_delegates_to_delete_message(self, mock_ctx):
        mock_ctx.request_context.lifespan_context.imap.delete_message.return_value = True
        result = await delete_email(mock_ctx, uid="99", mailbox="Trash")
        mock_ctx.request_context.lifespan_context.imap.delete_message.assert_called_once_with("Trash", "99")
        assert result is True


# --- send.py ---


class TestSendEmail:
    async def test_delegates_to_smtp_send_email(self, mock_ctx):
        mock_ctx.request_context.lifespan_context.smtp.send_email.return_value = True
        result = await send_email(
            mock_ctx,
            to="bob@example.com",
            subject="Hi",
            body="Hello Bob",
            body_html="<p>Hello Bob</p>",
            cc="cc@example.com",
            bcc="bcc@example.com",
            reply_to="reply@example.com",
        )
        mock_ctx.request_context.lifespan_context.smtp.send_email.assert_called_once_with(
            to="bob@example.com",
            subject="Hi",
            body="Hello Bob",
            body_html="<p>Hello Bob</p>",
            cc="cc@example.com",
            bcc="bcc@example.com",
            reply_to="reply@example.com",
        )
        assert result is True

    async def test_defaults_optional_params_to_none(self, mock_ctx):
        mock_ctx.request_context.lifespan_context.smtp.send_email.return_value = True
        await send_email(mock_ctx, to="bob@example.com", subject="Hi", body="Hello")
        mock_ctx.request_context.lifespan_context.smtp.send_email.assert_called_once_with(
            to="bob@example.com",
            subject="Hi",
            body="Hello",
            body_html=None,
            cc=None,
            bcc=None,
            reply_to=None,
        )
