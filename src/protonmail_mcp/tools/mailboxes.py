from .._sdk import Context

from ._types import Mailbox
from . import _get_app, READ_ONLY, tool


@tool("Lista mappar och etiketter", **READ_ONLY)
async def list_mailboxes(ctx: Context) -> list[dict]:
    """Lista alla mappar/etiketter i brevlådan.

    Varje post har:
        name: läsbart namn (å/ä/ö avkodade). Skicka det oförändrat till andra
            verktyg; etiketter med "/" i namnet har det escapat som "\\/".
        type: "system" (INBOX, Drafts, Sent, Starred, Archive, Spam, Trash,
            All Mail), "folder" (Folders/*), "label" (Labels/*) eller
            "container" (behållarna Mappar och Etiketter, som heter Folders och
            Labels hos Bridge; går inte att öppna).
        selectable: False för behållare; de innehåller inga mejl.
        special_use: "\\Drafts", "\\Sent", "\\All", "\\Archive", "\\Junk",
            "\\Trash", "\\Flagged" eller None.
    """
    app = _get_app(ctx)
    return await app.imap.list_mailboxes()


@tool("Antal mejl i en mapp", **READ_ONLY)
async def get_mailbox_status(ctx: Context, mailbox: Mailbox = "INBOX") -> dict:
    """Hämta antal meddelanden och olästa i en mapp.

    Kastar fel om mappen inte finns, och om den är en behållare (Mappar eller
    Etiketter, som heter Folders och Labels hos Bridge; selectable: false i
    list_mailboxes) som inte kan innehålla mejl.

    Args:
        mailbox: Mappnamn (standard: INBOX)
    """
    app = _get_app(ctx)
    return await app.imap.get_mailbox_status(mailbox)
