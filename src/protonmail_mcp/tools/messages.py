from mcp.server.fastmcp import Context

from ..server import mcp
from ..email_parser import parse_email
from . import _get_app


@mcp.tool()
async def list_emails(
    ctx: Context,
    mailbox: str = "INBOX",
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """Lista e-post i en mapp med metadata (sidbaserad paginering).

    Args:
        mailbox: Mappnamn (standard: INBOX)
        page: Sidnummer, 1-baserat (standard: 1)
        page_size: Antal e-post per sida (standard: 20)

    Returns:
        dict med nycklar: messages, total, page, pages, has_more
    """
    app = _get_app(ctx)
    return await app.imap.list_messages(mailbox, page=page, page_size=page_size)


@mcp.tool()
async def get_email(
    ctx: Context,
    uid: str,
    mailbox: str = "INBOX",
    body_format: str = "text",
    max_length: int | None = 500,
) -> dict | None:
    """Hämta fullständigt innehåll för ett e-post via UID.

    Args:
        uid: E-postets UID
        mailbox: Mappnamn (standard: INBOX)
        body_format: Format för body — "text" (plain text, default), "full" (rå body utan
            trunkering), eller "stripped" (text utan citat och signaturblock)
        max_length: Max antal tecken i body (standard: 500). None = ingen trunkering.
            Ignoreras för body_format="full".
    """
    app = _get_app(ctx)
    raw = await app.imap.get_message(mailbox, uid)
    if raw is None:
        return None
    return parse_email(raw, body_format=body_format, max_length=max_length)
