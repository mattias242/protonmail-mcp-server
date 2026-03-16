from mcp.server.fastmcp import Context

from ..server import mcp
from . import _get_app


@mcp.tool()
async def mark_email_read(ctx: Context, uid: str, mailbox: str = "INBOX") -> bool:
    """Markera ett e-post som läst.

    Args:
        uid: E-postets UID
        mailbox: Mappnamn (standard: INBOX)
    """
    app = _get_app(ctx)
    return await app.imap.set_flags(mailbox, uid, r"(\Seen)", add=True)


@mcp.tool()
async def mark_email_unread(ctx: Context, uid: str, mailbox: str = "INBOX") -> bool:
    """Markera ett e-post som oläst.

    Args:
        uid: E-postets UID
        mailbox: Mappnamn (standard: INBOX)
    """
    app = _get_app(ctx)
    return await app.imap.set_flags(mailbox, uid, r"(\Seen)", add=False)


@mcp.tool()
async def move_email(
    ctx: Context, uid: str, target_mailbox: str, mailbox: str = "INBOX"
) -> bool:
    """Flytta ett e-post till en annan mapp.

    Args:
        uid: E-postets UID
        target_mailbox: Målmapp att flytta till
        mailbox: Källmapp (standard: INBOX)
    """
    app = _get_app(ctx)
    return await app.imap.move_message(mailbox, uid, target_mailbox)


@mcp.tool()
async def delete_email(ctx: Context, uid: str, mailbox: str = "INBOX") -> bool:
    """Ta bort ett e-post (markerar som borttaget och expungerar).

    Args:
        uid: E-postets UID
        mailbox: Mappnamn (standard: INBOX)
    """
    app = _get_app(ctx)
    return await app.imap.delete_message(mailbox, uid)
