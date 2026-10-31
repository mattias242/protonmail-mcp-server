"""Språket i det som klienten ser: verktygstitlar och -beskrivningar, promptar,
resurser, serverinstruktionerna och alla fel- och förklaringstexter.

Svenska är källspråket och standard (PROTONMAIL_LANGUAGE=sv). Texterna i koden
skrivs på svenska och går genom ``_``::

    raise ValueError(_("Inget meddelande med UID {uid} i {mailbox}.", uid=uid, mailbox=mailbox))

``_`` slår upp den svenska texten (med platshållarna kvar) i den aktiva
översättningen (texts_en.MESSAGES för engelska) och fyller sedan i värdena med
str.format. Saknas en översättning används svenskan — fail-closed i meningen
att inget blir tomt eller fel, bara oöversatt. Texter som bara har
platshållare och inga ord (t.ex. "{what} i {mailbox}") står ändå i katalogen,
så att ordföljden kan skilja mellan språken.

Verktygens docstrings, titlar, parameterbeskrivningar, promptarnas och
resursernas titlar och beskrivningar översätts inte i koden utan byts ut när
listorna skickas till klienten (server._GuardedMCPServer.list_*), så att
registreringen och kontraktsfacit (tests/snapshots/mcp_kontrakt.json) är
oförändrade på svenska.

Språket sätts en gång när servern startar (server.apply_language, ur
inställningarna) och ligger i en modulvariabel: inställningarna behöver inte
läsas i varje felväg, och testerna kan byta språk med set_language.
tests/unit/test_feat_sprak_en.py kräver att varje text som går genom ``_``
har en engelsk översättning med samma platshållare.
"""
from __future__ import annotations

from typing import Any

DEFAULT = "sv"
LANGUAGES = ("sv", "en")

_language = DEFAULT


def set_language(language: str) -> None:
    """Byt aktivt språk ("sv" eller "en"); okända värden ger ValueError."""
    global _language
    if language not in LANGUAGES:
        raise ValueError(f"Okänt språk {language!r}; giltiga: {', '.join(LANGUAGES)}")
    _language = language


def language() -> str:
    """Det aktiva språket."""
    return _language


def messages() -> dict[str, str]:
    """Översättningskatalogen för det aktiva språket (tom för svenska)."""
    if _language == "en":
        from .texts_en import MESSAGES

        return MESSAGES
    return {}


def _(text: str, /, **values: Any) -> str:
    """Översätt en svensk text till det aktiva språket och fyll i värdena.

    ``text`` är den svenska mallen med str.format-platshållare ({uid},
    {mailbox!r}, {timeout:g}). Bokstavliga klammerparenteser i en mall med
    värden skrivs {{ }}. Utan värden formateras inget, så en text utan
    platshållare får innehålla { och } som de är.
    """
    if _language != "sv":
        text = messages().get(text, text)
    return text.format(**values) if values else text


def translate_lookup(table_name: str, key: str) -> Any | None:
    """Posten för ``key`` i en namngiven tabell i det aktiva språkets katalog, eller None.

    Tabellerna (TOOLS, PROMPTS, RESOURCES, PARAMS, INSTRUCTIONS) ligger i
    texts_<språk>.py; för svenska finns ingen katalog, så svaret är None och
    det registrerade originalet används.
    """
    if _language != "en":
        return None
    from . import texts_en

    table = getattr(texts_en, table_name)
    if isinstance(table, dict):
        return table.get(key)
    return table
