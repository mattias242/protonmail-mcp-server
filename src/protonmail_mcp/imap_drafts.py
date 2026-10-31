"""Utkast i Drafts via IMAP APPEND.

Bridge gör ett APPEND till Drafts till ett riktigt utkast i Proton, som syns
och kan redigeras och skickas i Protons appar. EXPUNGE i Drafts raderar
permanent; därför flyttas ett ersatt utkast till Trash i stället. Funktionerna här tar IMAPClient som första argument och körs under
klientens lås (``_serialized``), som metoderna i IMAPClient.
"""
from __future__ import annotations

import asyncio
import re

import aioimaplib

from .i18n import _
from . import policy

from .imap_client import (
    IMAPClient,
    _UID_RE,
    _decode,
    _imap_quoted,
    _quote_mailbox,
    _require_ok,
    _serialized,
    _validate_uid,
    _with_timeout,
)

DRAFTS = "Drafts"
TRASH = "Trash"
# Bridge sätter alltid \Seen på utkast; \Draft gör det till ett utkast
_DRAFT_FLAGS = r"(\Draft \Seen)"
# Svarskod från UIDPLUS (RFC 4315): [APPENDUID <uidvalidity> <uid>]
_APPENDUID_RE = re.compile(r"\[APPENDUID\s+\d+\s+(\d+)\]", re.IGNORECASE)


async def _append(imap: IMAPClient, message: bytes):
    """APPEND till Drafts med timeout (aioimaplib har ingen egen för APPEND)."""
    try:
        return await _with_timeout(
            imap._client.append(message, mailbox=_quote_mailbox(DRAFTS), flags=_DRAFT_FLAGS),
            imap.uid_timeout,
        )
    except asyncio.TimeoutError:
        raise asyncio.TimeoutError(
            _("ProtonMail Bridge svarade inte på APPEND till {DRAFTS} inom {uid_timeout:g} s",
  DRAFTS=DRAFTS, uid_timeout=imap.uid_timeout)
        ) from None


def _appenduid(resp) -> str | None:
    for line in resp.lines:
        m = _APPENDUID_RE.search(_decode(line))
        if m:
            return m.group(1)
    return None


async def _find_by_message_id(imap: IMAPClient, message_id: str) -> str | None:
    """UID för utkastet med det här Message-ID:t i Drafts (det nyaste om flera).

    Används bara när Bridge inte svarar med APPENDUID. Utkastet är då redan
    sparat, så ett fel här ger None i stället för ett fel — annars kunde den
    som anropar tro att inget sparades och skapa ett till.
    """
    try:
        criteria = ["HEADER", "Message-ID", _imap_quoted(message_id, "Message-ID")]
        await imap._select(DRAFTS)
        seqnums = await imap._search_seqnums(criteria)
        if not seqnums:
            return None
        resp = await imap._client.fetch(",".join(seqnums), "(UID)")
    except ValueError:
        return None
    if resp.result != "OK":
        return None
    uids = [int(m.group(1)) for line in resp.lines if (m := _UID_RE.search(_decode(line)))]
    return str(max(uids)) if uids else None


async def _store_draft(imap: IMAPClient, message: bytes, message_id: str | None) -> str | None:
    resp = await _append(imap, message)
    _require_ok(resp, _("Sparandet av utkastet i {DRAFTS}", DRAFTS=DRAFTS))
    uid = _appenduid(resp)
    if uid is None and message_id:
        uid = await _find_by_message_id(imap, message_id)
    return uid


@_serialized
@policy.write_operation("append_draft", draft=True, mailbox=DRAFTS)
async def append_draft(imap: IMAPClient, message: bytes, message_id: str | None) -> str | None:
    """Lägg ett färdigt meddelande i Drafts som utkast (flaggorna \\Draft \\Seen).

    Returnerar utkastets UID i Drafts: ur APPENDUID om Bridge svarar med det,
    annars genom att söka upp Message-ID:t. None om UID:t inte gick att få fram
    (utkastet är ändå sparat). Kastar ValueError om Bridge vägrar spara.
    """
    await imap._ensure_connected()
    return await _store_draft(imap, message, message_id)


async def _move_to_trash(imap: IMAPClient, uid: str) -> None:
    """Flytta ett utkast ur Drafts till Trash (UID MOVE på just det UID:t).

    Bridge flyttar då utkastet till papperskorgen i Proton, där det går att
    återställa. Låset hålls redan här, så klientens odekorerade _move används.
    """
    await imap._move(DRAFTS, uid, TRASH)


def _new_draft_saved(new_uid: str | None, message_id: str | None) -> str:
    return (
        _("Det nya utkastet sparades i {DRAFTS} (UID {new_uid}, "
        "Message-ID {message_id})",
        DRAFTS=DRAFTS, new_uid=new_uid or _("okänt"), message_id=message_id or _("okänt"))
    )


_RETRY_WARNING = "Kör inte update_draft igen — då blir det ett utkast till."


@_serialized
@policy.write_operation("replace_draft", draft=True, mailbox=DRAFTS)
async def replace_draft(
    imap: IMAPClient, old_uid: str, message: bytes, message_id: str | None
) -> str | None:
    """Ersätt utkastet old_uid i Drafts med ett nytt meddelande.

    Ordningen gör att inget går förlorat: först kontrolleras att det gamla
    utkastet finns, sedan sparas det nya med APPEND, och först när det lyckats
    flyttas det gamla till Trash (inte permanent raderat — det går att
    återställa därifrån). Misslyckas APPEND rörs det gamla inte. Misslyckas
    eller avbryts flytten (timeout, bruten anslutning) kastas ValueError som
    anger det nya utkastets UID och Message-ID, så att ingen tror att inget
    sparades. Returnerar det nya utkastets UID (None om Bridge inte angav det).
    """
    safe_uid = _validate_uid(old_uid)
    await imap._ensure_connected()
    await imap._select(DRAFTS)
    await imap._require_uid(DRAFTS, safe_uid)
    new_uid = await _store_draft(imap, message, message_id)
    try:
        await _move_to_trash(imap, safe_uid)
    except ValueError as exc:
        raise ValueError(
            _("{saved}, men det gamla (UID {safe_uid}) kunde "
            "inte flyttas till {TRASH}, så båda finns kvar i {DRAFTS}: {exc}. {retry_warning}",
            saved=_new_draft_saved(new_uid, message_id), safe_uid=safe_uid, TRASH=TRASH, DRAFTS=DRAFTS,
            exc=exc, retry_warning=_(_RETRY_WARNING))
        ) from exc
    except (asyncio.TimeoutError, OSError, aioimaplib.AioImapException) as exc:
        # Det nya utkastet finns redan: ett fel som ser ut som "inget hände"
        # får Claude att köra update_draft igen och skapa ett tredje. Efter
        # avbrottet är protokollets tillstånd okänt, så anslutningen släpps
        # här (_serialized gör det bara för de här undantagstyperna).
        imap._discard_connection()
        detail = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        raise ValueError(
            _("{saved}, men flytten av det gamla (UID "
            "{safe_uid}) till {TRASH} avbröts ({detail}). Det gamla kan "
            "ligga kvar i {DRAFTS} eller redan i {TRASH} — kontrollera med "
            'list_emails(mailbox="{DRAFTS}"). {retry_warning}',
            saved=_new_draft_saved(new_uid, message_id), safe_uid=safe_uid, TRASH=TRASH, detail=detail,
            DRAFTS=DRAFTS, retry_warning=_(_RETRY_WARNING))
        ) from exc
    return new_uid


async def _has_uid(imap: IMAPClient, uid: str) -> bool:
    """Finns UID:t i den valda mappen? (Bridge svarar OK utan data om inte.)"""
    resp = await imap._uid("fetch", uid, "(UID)")
    _require_ok(resp, _("Kontrollen av UID {uid} i {DRAFTS}", uid=uid, DRAFTS=DRAFTS))
    return any(
        (m := _UID_RE.search(_decode(line))) and int(m.group(1)) == int(uid)
        for line in resp.lines
    )


async def _expunge_draft(imap: IMAPClient, uid: str) -> None:
    """UID STORE \\Deleted + UID EXPUNGE på utkastet i Drafts (som är vald).

    Väntar inte som delete_email gör när Bridge nekar med Code=2501: efter
    ett skick har Bridge ofta redan tagit bort utkastet i Proton, och
    remove_sent_draft kontrollerar själv om det finns kvar.
    """
    resp = await imap._uid("store", uid, "+FLAGS", r"(\Deleted)")
    _require_ok(resp, _("Borttagningsmarkeringen av UID {uid} i {DRAFTS}", uid=uid, DRAFTS=DRAFTS))
    resp = await imap._uid("expunge", uid)
    if resp.result != "OK":
        # Lämna inte \Deleted kvar: nästa EXPUNGE i mappen skulle annars ta det
        await imap._uid("store", uid, "-FLAGS", r"(\Deleted)")
    _require_ok(resp, _("Den permanenta raderingen av UID {uid} i {DRAFTS}", uid=uid, DRAFTS=DRAFTS))


@_serialized
@policy.write_operation("remove_sent_draft", mailbox=DRAFTS)
async def remove_sent_draft(imap: IMAPClient, uid: str) -> bool:
    """Ta bort ett utkast ur Drafts efter att det skickats.

    Bridge tar själv bort ett utkast som det skickade meddelandet refererar
    till (<id@protonmail.internalid> i References, som Bridge lägger till när
    utkastet hämtas; internal/services/smtp/smtp.go, getParentID). Därför:
    finns UID:t inte längre räknas utkastet som borttaget; finns det raderas
    det permanent (UID STORE \\Deleted + UID EXPUNGE på just det UID:t).
    Nekas raderingen kontrolleras igen — har Bridge hunnit ta bort det är det
    inget fel.

    Returnerar True om utkastet raderades här, False om det redan var borta.
    Kastar ValueError om det inte gick att radera och fortfarande finns kvar.
    """
    safe_uid = _validate_uid(uid)
    await imap._ensure_connected()
    await imap._select(DRAFTS)
    if not await _has_uid(imap, safe_uid):
        return False
    try:
        await _expunge_draft(imap, safe_uid)
    except ValueError:
        await imap._select(DRAFTS)
        if not await _has_uid(imap, safe_uid):
            return False
        raise
    return True
