from mcp.server.fastmcp import Context

from ..server import mcp
from . import _get_app


@mcp.tool()
async def search_emails(
    ctx: Context,
    mailbox: str = "INBOX",
    from_addr: str | None = None,
    subject: str | None = None,
    since: str | None = None,
    before: str | None = None,
    unseen: bool | None = None,
) -> list[str]:
    """Sök e-post med filter.

    Args:
        mailbox: Mappnamn att söka i (standard: INBOX)
        from_addr: Filtrera på avsändaradress
        subject: Filtrera på ämnesrad
        since: Filtrera e-post efter datum (format: DD-Mon-YYYY, t.ex. 01-Jan-2024)
        before: Filtrera e-post före datum (format: DD-Mon-YYYY)
        unseen: True=bara olästa, False=bara lästa, None=alla

    Returns:
        Lista med UID:n för matchande e-post
    """
    app = _get_app(ctx)
    return await app.imap.search_messages(
        mailbox,
        from_addr=from_addr,
        subject=subject,
        since=since,
        before=before,
        unseen=unseen,
    )
