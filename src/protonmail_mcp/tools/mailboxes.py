from mcp.server.fastmcp import Context

from ..server import mcp
from . import _get_app


@mcp.tool()
async def list_mailboxes(ctx: Context) -> list[dict]:
    """Lista alla mappar/etiketter i brevlådan."""
    app = _get_app(ctx)
    return await app.imap.list_mailboxes()


@mcp.tool()
async def get_mailbox_status(ctx: Context, mailbox: str = "INBOX") -> dict:
    """Hämta antal meddelanden och olästa i en mapp.

    Args:
        mailbox: Mappnamn (standard: INBOX)
    """
    app = _get_app(ctx)
    return await app.imap.get_mailbox_status(mailbox)
