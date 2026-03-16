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
) -> list[dict]:
    """Sök e-post med filter.

    Args:
        mailbox: Mappnamn att söka i (standard: INBOX)
        from_addr: Filtrera på avsändaradress
        subject: Filtrera på ämnesrad
        since: Filtrera e-post efter datum (format: YYYY-MM-DD eller DD-Mon-YYYY)
        before: Filtrera e-post före datum (format: YYYY-MM-DD eller DD-Mon-YYYY)
        unseen: True=bara olästa, False=bara lästa, None=alla

    Returns:
        Lista med metadata-dicts (uid, subject, from, date, flags) för matchande e-post
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
