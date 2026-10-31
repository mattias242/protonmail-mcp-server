"""Gemensam felhantering för alla verktyg.

Verktygen kastar interna undantag (ValueError med svensk text, men också
ConnectionError, timeouts, aioimaplib- och aiosmtplib-fel). Utan omslutning
når modellen texten "Error executing tool x: <str(exc)>", som för
anslutningsfel ofta är tom eller engelsk ("[Errno 61] Connect call failed").
Här görs undantagen om till fel på svenska som säger vad modellen eller
användaren kan göra. Stackspår skickas aldrig till klienten; oväntade fel
loggas med stackspår till stderr (stdout är MCP-protokollet). Lösenordet
tas bort ur allt som returneras eller loggas.
"""
import functools
import logging
import socket
import traceback
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote, quote_plus

import aioimaplib
import aiosmtplib
from ..i18n import _
from .._sdk import ToolError

from .. import config
from ..imap_client import IMAPAuthenticationError
from ..smtp_client import SMTPPartialDeliveryError

logger = logging.getLogger(__name__)

_MAX_DETAIL_CHARS = 300

_CONNECTION_ERRORS: tuple[type[BaseException], ...] = (
    ConnectionError,  # även aiosmtplib.SMTPConnectError och SMTPServerDisconnected
    TimeoutError,  # även aiosmtplib.SMTPTimeoutError
    aioimaplib.AioImapException,  # Abort, CommandTimeout, IncompleteRead …
    socket.gaierror,  # värdnamnet går inte att slå upp
)

_UNREACHABLE_PREFIX = "Kan inte nå ProtonMail Bridge"
_UNREACHABLE_ADVICE = (
    "Starta Bridge-appen och kontrollera att kontot är inloggat där, samt "
    "PROTONMAIL_IMAP_HOST/PROTONMAIL_IMAP_PORT och PROTONMAIL_SMTP_HOST/"
    "PROTONMAIL_SMTP_PORT. Försök sedan igen."
)
_MAYBE_SENT = (
    " Det är okänt om mejlet hann skickas, så det kan ha skickats: kontrollera "
    "Sent (list_emails(mailbox=\"Sent\")) innan du försöker igen."
)
_AUTH = (
    "Inloggningen mot ProtonMail Bridge misslyckades{detail}. Kontrollera "
    "PROTONMAIL_USERNAME och att PROTONMAIL_PASSWORD är Bridge-lösenordet som "
    "Bridge-appen visar för kontot (inte lösenordet till Proton-kontot), och att "
    "kontot är inloggat i Bridge. Ett nytt försök hjälper inte förrän det är rättat."
)


def _password() -> str:
    try:
        password = config.get_settings().password
    except Exception:  # ingen konfiguration (t.ex. i tester): inget att dölja
        return ""
    return password if isinstance(password, str) else ""


def _password_forms(password: str) -> list[str]:
    """Formerna som lösenordet kan stå i en feltext, längsta först: som det
    är, URL-kodat (t.ex. i en URL med inloggning) och med blankstegen
    hopslagna (som _detail och _smtp_reply gör)."""
    forms = {
        password,
        quote(password, safe=""),
        quote(password),
        quote_plus(password),
        " ".join(password.split()),
    }
    return sorted((f for f in forms if f), key=len, reverse=True)


def _scrub(text: str) -> str:
    password = _password()
    if not password:
        return text
    for form in _password_forms(password):
        text = text.replace(form, "***")
    return text


def _one_line(text: str) -> str:
    """Texten på en rad, maskerad både före och efter att blankstegen slagits ihop."""
    return _scrub(" ".join(_scrub(text).split()))


def _detail(exc: BaseException) -> str:
    """Undantagets text på en rad, eller typnamnet om texten är tom.

    Lösenordet maskeras före kapningen, så att ingen början av det blir kvar.
    """
    text = _one_line(str(exc)) or type(exc).__name__
    if len(text) > _MAX_DETAIL_CHARS:
        text = text[:_MAX_DETAIL_CHARS] + " …"
    return text


def _smtp_reply(exc: aiosmtplib.SMTPResponseException) -> str:
    return _one_line(f"{exc.code} {exc.message}")


def _partial_delivery(exc: SMTPPartialDeliveryError) -> str:
    refused = "; ".join(
        f"{addr} ({code} {_one_line(str(text))})" for addr, (code, text) in exc.refused.items()
    )
    return (
        _("Mejlet skickades bara delvis. Fick mejlet: {delivered}. "
        "Fick det INTE (servern vägrade): {refused}. Skicka inte om till alla — de "
        "som redan fått mejlet får det då två gånger. Skicka vid behov ett nytt mejl "
        "bara till dem som inte fick det, efter att adresserna rättats.",
        delivered=", ".join(exc.delivered), refused=refused)
    )


def _unexpected(exc: BaseException, tool_name: str) -> str:
    stack = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    logger.error("Oväntat fel i verktyget %s:\n%s", tool_name, _scrub(stack))
    return (
        _("Oväntat fel i {tool_name} ({kind}). Detaljerna är loggade i "
        "serverns logg (stderr). Upprepa inte samma anrop; be användaren titta i "
        "loggen eller försök med andra argument.",
        tool_name=tool_name, kind=type(exc).__name__)
    )


def describe_error(exc: BaseException, *, tool_name: str, open_world: bool) -> str:
    """Felmeddelandet som modellen får för ett undantag från ett verktyg.

    open_world: verktyget skickar mejl; vid avbrott vet vi då inte om mejlet
    hann iväg, och det ska sägas.
    """
    if isinstance(exc, ToolError):
        return _scrub(str(exc))
    if isinstance(exc, SMTPPartialDeliveryError):
        return _scrub(_partial_delivery(exc))
    if isinstance(exc, IMAPAuthenticationError):
        return _(_AUTH, detail="")
    if isinstance(exc, aiosmtplib.SMTPAuthenticationError):
        return _scrub(_(_AUTH, detail=_(" (SMTP svarade {reply})", reply=_smtp_reply(exc))))
    if isinstance(exc, aiosmtplib.SMTPRecipientsRefused):
        refused = "; ".join(
            f"{e.recipient} ({_smtp_reply(e)})" for e in exc.recipients
        )
        return _scrub(
            _("Servern vägrade alla mottagare: {refused}. Inget mejl skickades. "
            "Rätta adresserna och försök igen.",
            refused=refused)
        )
    if isinstance(exc, aiosmtplib.SMTPResponseException):
        return _scrub(
            _("ProtonMail Bridge vägrade skicka mejlet ({reply}). "
            "Inget mejl skickades.",
            reply=_smtp_reply(exc))
        )
    if isinstance(exc, _CONNECTION_ERRORS):
        detail = _detail(exc)
        if detail.startswith(_(_UNREACHABLE_PREFIX)):
            # imap_client har redan formulerat det (med värd och port)
            text = f"{detail}. {_(_UNREACHABLE_ADVICE)}"
        else:
            text = _("{prefix} ({detail}) — körs Bridge? {advice}",
                     prefix=_(_UNREACHABLE_PREFIX), detail=detail, advice=_(_UNREACHABLE_ADVICE))
        if open_world:
            text += _(_MAYBE_SENT)
        return _scrub(text)
    if isinstance(exc, ValueError) and not isinstance(exc, UnicodeError):
        # Verktygens egna fel: redan formulerade på svenska för modellen
        return _scrub(str(exc))
    return _unexpected(exc, tool_name)


def with_error_messages(
    fn: Callable[..., Awaitable[Any]], tool_name: str, *, open_world: bool
) -> Callable[..., Awaitable[Any]]:
    """Omslut ett verktyg så att undantag blir ToolError med describe_error-text.

    Signatur, docstring och annoteringar behålls (functools.wraps), så att
    SDK:n bygger samma schema som för den omslutna funktionen.
    CancelledError (BaseException) släpps igenom orörd.
    """

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:
            message = describe_error(exc, tool_name=tool_name, open_world=open_world)
            raise ToolError(message) from exc

    wrapper._error_messages = True  # type: ignore[attr-defined]
    return wrapper
