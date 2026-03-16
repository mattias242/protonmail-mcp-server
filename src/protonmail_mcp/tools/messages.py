from mcp.server.fastmcp import Context

from ..server import mcp
from ..email_parser import parse_email
from . import _get_app


@mcp.tool()
async def list_emails(
    ctx: Context,
    mailbox: str = "INBOX",
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    """Lista e-post i en mapp med metadata (paginerat).

    Args:
        mailbox: Mappnamn (standard: INBOX)
        limit: Antal e-post att returnera (standard: 20)
        offset: Hoppa över de första N e-posten (standard: 0)
    """
    app = _get_app(ctx)
    return await app.imap.list_messages(mailbox, limit=limit, offset=offset)


@mcp.tool()
async def get_email(ctx: Context, uid: str, mailbox: str = "INBOX") -> dict | None:
    """Hämta fullständigt innehåll för ett e-post via UID.

    Args:
        uid: E-postets UID
        mailbox: Mappnamn (standard: INBOX)
    """
    app = _get_app(ctx)
    raw = await app.imap.get_message(mailbox, uid)
    if raw is None:
        return None
    return parse_email(raw)
