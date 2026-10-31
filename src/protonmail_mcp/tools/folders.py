from ..i18n import _
from .._sdk import Context

from ..imap_client import _LEVEL_SEP_RE
from . import _get_app, ADDS, DESTRUCTIVE, tool
from ._types import Mailbox

# Protons svar när namnet redan används (go-proton-api: HTTP 409, Code=2500)
_NAME_TAKEN = ("Code=2500", "Status=409")
_USER_PREFIXES = ("Folders/", "Labels/")


def _last_level(name: str) -> str:
    """Sista nivån i ett mapp- eller etikettnamn, utan prefix ("Folders/A/B" → "B")."""
    for prefix in _USER_PREFIXES:
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return _LEVEL_SEP_RE.split(name)[-1].casefold()


async def _name_taken_error(app, name: str, bridge_error: str) -> ValueError:
    """Begripligt fel när Proton nekar CREATE för att namnet redan används."""
    kind = _("mappen") if name.startswith("Folders/") else _("etiketten")
    try:
        existing = [m["name"] for m in await app.imap.list_mailboxes()]
    except ValueError:
        existing = []
    key = _last_level(name)
    twins = sorted(
        other for other in existing
        if other != name and other.startswith(_USER_PREFIXES) and _last_level(other) == key
    )
    used_by = _(" Namnet används redan av {twins}.", twins=", ".join(twins)) if twins else ""
    return ValueError(
        _("Kunde inte skapa {kind} {name}: namnet är upptaget.{used_by} Proton kräver att "
        "namnen är unika över mappar och etiketter tillsammans, så en mapp och en etikett "
        "kan inte heta likadant. Välj ett annat namn, eller använd den befintliga "
        "(list_mailboxes visar vilka som finns). "
        "(Bridge: {bridge_error})",
        kind=kind, name=name, used_by=used_by, bridge_error=bridge_error)
    )


@tool("Skapa mapp eller etikett", **ADDS)
async def create_folder(ctx: Context, name: Mailbox) -> bool:
    """Skapa en ny mapp eller etikett i Proton.

    Namnet avgör typen och måste ha ett av prefixen:
    - "Folders/Namn" skapar en mapp. Ett mejl ligger i exakt en mapp.
      Undermappar ("Folders/A/B") går bra om "Folders/A" redan finns.
    - "Labels/Namn" skapar en etikett. Ett mejl kan ha flera etiketter.
      Underetiketter ("Labels/A/B") finns inte i Proton.
    Namn utan prefix avvisas med fel; prefixet sätts inte automatiskt
    eftersom mapp och etikett beter sig olika.

    Args:
        name: Namn med prefix, t.ex. "Folders/Projekt" eller "Labels/Kvitton"

    Proton kräver unika namn över mappar och etiketter tillsammans: finns
    "Labels/Kvitton" går det inte att skapa "Folders/Kvitton". Felet säger då
    vilken mapp eller etikett som redan har namnet.

    Returns:
        True när mappen är skapad. Annars kastas ett fel med förklaring.
    """
    app = _get_app(ctx)
    try:
        return await app.imap.create_folder(name)
    except ValueError as exc:
        if any(code in str(exc) for code in _NAME_TAKEN):
            raise await _name_taken_error(app, name, str(exc)) from exc
        raise


@tool("Ta bort mapp eller etikett", **DESTRUCTIVE)
async def delete_folder(ctx: Context, name: Mailbox, force: bool = False) -> bool:
    """Ta bort en egen mapp (Folders/…) eller etikett (Labels/…).

    Mappen måste vara tom och sakna undermappar; annars avvisas anropet med
    fel och inget ändras. Med force=True tas den bort ändå. Mejlen raderas inte, men:
    - för en etikett försvinner etiketten från alla mejl som hade den,
    - för en mapp ligger mejlen sedan inte kvar i mappen (de finns i All Mail),
    - vad Proton gör med undermappar och deras mejl är inte känt.
    Systemmapparna (INBOX, Sent, Trash, All Mail …) kan inte tas bort.

    Args:
        name: Namn med prefix, t.ex. "Folders/Gammalt" eller "Labels/Kvitton"
        force: Ta bort även om mappen innehåller mejl (standard: False)

    Returns:
        True när mappen är borttagen. Annars kastas ett fel med förklaring.
    """
    app = _get_app(ctx)
    return await app.imap.delete_folder(name, force=force)


@tool("Byt namn på mapp eller etikett", **DESTRUCTIVE)
async def rename_folder(ctx: Context, old_name: Mailbox, new_name: Mailbox) -> bool:
    """Byt namn på en egen mapp (Folders/…) eller etikett (Labels/…).

    Båda namnen måste ha samma prefix: en mapp kan inte bli en etikett eller
    tvärtom. Systemmapparna (INBOX, Sent, Trash, All Mail …) kan inte döpas om.

    Args:
        old_name: Nuvarande namn, t.ex. "Folders/Projekt"
        new_name: Nytt namn med samma prefix, t.ex. "Folders/Projekt 2026"

    Returns:
        True när namnet är bytt. Annars kastas ett fel med förklaring.
    """
    app = _get_app(ctx)
    return await app.imap.rename_folder(old_name, new_name)
