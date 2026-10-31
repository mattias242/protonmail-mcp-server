"""Konversationer (trådar) för ett mejl, byggda av Message-ID-kedjan.

Bridge saknar IMAP THREAD, så tråden byggs här: Message-ID, In-Reply-To och
References för mejlet ger en uppsättning ID:n, och All Mail söks efter mejl
som har ett av dem som Message-ID eller som refererar till något av dem.
Nya mejl ger nya ID:n, och sökningen upprepas tills inget nytt hittas (med
tak för antal rundor och antal mejl). All Mail används för att få med både
mottagna och skickade mejl.

Verifierat live (läs-endast): HEADER Message-ID/In-Reply-To/References-sökning
fungerar i All Mail och tar 3–8 s per SEARCH på ~32 000 mejl; en OR-kedja med
flera ID:n tar ungefär lika lång tid som ett enda.

References styrs av avsändaren och kan innehålla tusentals påhittade ID:n.
Därför finns tak på antal ID:n som följs, antal SEARCH och total tid per
anrop, och IMAP-låset hålls bara under en sökning åt gången (SELECT + SEARCH
+ FETCH), så att andra verktygsanrop kan köras mellan sökningarna.
"""
from __future__ import annotations

import json
import re
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any

from .i18n import _
from .imap_client import (
    _FETCH_CHUNK_SIZE,
    _CONTROL_CHAR_RE,
    _imap_quoted,
    _parse_fetch_metadata,
    _quote_mailbox,
    _require_ok,
    _serialized,
    _validate_uid,
)

ALL_MAIL = "All Mail"
# Högst så många sökrundor (varje runda är 1+ SEARCH i All Mail, 3–8 s styck)
_MAX_ROUNDS = 6
# Så många ID:n per SEARCH; varje ID ger tre HEADER-nycklar i OR-kedjan
_IDS_PER_SEARCH = 20
# Tak per anrop. En vanlig konversation behöver 1–5 SEARCH (startmejlets ID:n,
# sedan de nya ID:na från träffarna); 10 SEARCH är 30–80 s i All Mail och
# räcker för 200 ID:n, lika många som max_messages tillåter mejl.
_MAX_SEARCHES = 10
_MAX_IDS = 200
# Ingen ny SEARCH startas efter så här lång tid (varje SEARCH har dessutom
# klientens egen timeout), så att ett anrop aldrig tar mer än ungefär en minut
_TIME_BUDGET_S = 60.0
_clock = time.monotonic
# Så många av varje mejls ID:n (eget, In-Reply-To, roten och de närmaste i
# References) söks före resten av References. Annars kan ett enda mejl med
# tusentals påhittade ID:n förbruka hela budgeten innan de ID:n som de
# riktiga mejlen i konversationen ger hinner sökas.
_NEAREST_IDS = 10
_MAX_LIMIT = 200
_THREAD_FIELDS = "DATE FROM TO CC SUBJECT MESSAGE-ID IN-REPLY-TO REFERENCES"
_EXTRA = ("cc", "in-reply-to", "references")
# msg-id enligt RFC 5322: "<" id-left "@" id-right ">"; i praktiken allt utom
# blanktecken, vinkelparenteser och citattecken (som skulle kräva escaping)
_MSG_ID_RE = re.compile(r'<[^<>\s"\\]+>')
# Bridge lägger Protons eget meddelande-ID i References. Det är inget
# Message-ID och pekar bara på mejlet självt (kontrollerat live).
_PROTON_INTERNAL_SUFFIX = "@protonmail.internalid>"
_MAX_ID_LENGTH = 998


def _message_ids(*values: str | None) -> list[str]:
    """Alla msg-id i värdena, i ordning och utan dubbletter."""
    ids: list[str] = []
    seen: set[str] = set()
    for value in values:
        for msg_id in _MSG_ID_RE.findall(value or ""):
            if (
                msg_id not in seen
                and len(msg_id) <= _MAX_ID_LENGTH
                and not _CONTROL_CHAR_RE.search(msg_id)
                and not msg_id.lower().endswith(_PROTON_INTERNAL_SUFFIX)
            ):
                ids.append(msg_id)
                seen.add(msg_id)
    return ids


def _thread_criteria(ids: list[str]) -> list[str]:
    """SEARCH-kriterier: mejl med något av ID:na som Message-ID, In-Reply-To eller References."""
    keys: list[list[str]] = []
    for msg_id in ids:
        quoted = _imap_quoted(msg_id, "Message-ID")
        for header in ("Message-ID", "In-Reply-To", "References"):
            keys.append(["HEADER", header, quoted])
    criteria: list[str] = []
    for i, key in enumerate(keys):
        if i < len(keys) - 1:
            criteria.append("OR")
        criteria += key
    return criteria


def _ids_of(meta: dict[str, Any]) -> list[str]:
    """Mejlets ID:n, de närmaste först: eget, In-Reply-To, roten, sedan References bakifrån.

    När taket för antal ID:n nås är det de avlägsnaste (och avsändarens
    eventuella skräp mitt i References) som inte söks.
    """
    refs = _message_ids(meta.get("references"))
    return _message_ids(meta["message_id"], meta.get("in_reply_to"), *refs[:1], *refs[:0:-1])


def _belongs(meta: dict[str, Any], known) -> bool:
    """Exakt kontroll: servern söker delsträngar, så "<a@x>" träffar även "<ba@x>"."""
    return any(msg_id in known for msg_id in _ids_of(meta))


def _date_key(meta: dict[str, Any]) -> tuple[int, float, int]:
    """Sorteringsnyckel: datum (UTC), mejl utan tolkbart datum sist, sedan UID."""
    uid = int(meta["uid"]) if meta["uid"].isdigit() else 0
    try:
        when = parsedate_to_datetime(meta["date"])
    except (TypeError, ValueError, IndexError):
        return (1, 0.0, uid)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (0, when.timestamp(), uid)


def _public(meta: dict[str, Any], mailbox: str) -> dict[str, Any]:
    out = {k: v for k, v in meta.items() if k != "references"}
    out["mailbox"] = mailbox
    return out


async def _fetch_thread_metadata(imap, seqnums: list[str]) -> list[dict[str, Any]]:
    msgs: list[dict[str, Any]] = []
    for start in range(0, len(seqnums), _FETCH_CHUNK_SIZE):
        chunk = seqnums[start:start + _FETCH_CHUNK_SIZE]
        resp = await imap._client.fetch(
            ",".join(chunk), f"(UID FLAGS BODY.PEEK[HEADER.FIELDS ({_THREAD_FIELDS})])"
        )
        _require_ok(resp, _("Hämtningen av konversationens mejl i {ALL_MAIL}", ALL_MAIL=ALL_MAIL))
        msgs += _parse_fetch_metadata(resp.lines, extra_headers=_EXTRA)
    return msgs


@_serialized
async def _read_start(imap, uid: str, mailbox: str) -> dict[str, Any]:
    await imap._ensure_connected()
    await imap._select(mailbox)
    resp = await imap._uid("fetch", uid, f"(UID FLAGS BODY.PEEK[HEADER.FIELDS ({_THREAD_FIELDS})])")
    _require_ok(resp, _("Hämtningen av UID {uid} i {mailbox}", uid=uid, mailbox=mailbox))
    start = next((m for m in _parse_fetch_metadata(resp.lines, extra_headers=_EXTRA) if m["uid"] == uid), None)
    if start is None:
        raise ValueError(
            _("Inget meddelande med UID {uid} i {mailbox}. "
            "UID:n gäller per mapp — kontrollera att mailbox är samma mapp som UID:t kom från.",
            uid=uid, mailbox=mailbox)
        )
    return start


@_serialized
async def _search_step(imap, ids: list[str]) -> list[dict[str, Any]]:
    """En SEARCH i All Mail och metadata för träffarna, under låset.

    All Mail väljs om varje gång: mellan stegen kan andra anrop ha valt en
    annan mapp, och sekvensnumren gäller bara inom samma steg.
    """
    await imap._ensure_connected()
    await imap._select(ALL_MAIL)
    seqnums = await imap._search_seqnums(_thread_criteria(ids))
    return await _fetch_thread_metadata(imap, seqnums)


async def get_thread(imap, uid: str, mailbox: str, max_messages: int = 50) -> dict[str, Any]:
    """Hämtar konversationen som mejlet uid i mailbox ingår i, sorterad på datum.

    Returnerar dict med mailbox, messages (metadata: uid, mailbox, from, to,
    cc, subject, date, message_id, in_reply_to, flags; äldst först), total
    (antal hittade mejl), complete och note. Varje mejls uid gäller i dess
    eget mailbox-fält: All Mail, utom när startmejlet saknar Message-ID —
    då returneras bara startmejlet, med uid och mailbox som det angavs.
    Med fler än max_messages träffar returneras startmejlet och de senaste.

    Låset tas per steg (startmejlet, sedan en SEARCH i taget), inte för
    hela anropet; se taken _MAX_IDS, _MAX_SEARCHES och _TIME_BUDGET_S.
    """
    safe_uid = _validate_uid(uid)
    _quote_mailbox(mailbox)
    if isinstance(max_messages, bool) or not isinstance(max_messages, int) or not 1 <= max_messages <= _MAX_LIMIT:
        raise ValueError(_("max_messages måste vara ett heltal 1–{_MAX_LIMIT} (fick {max_messages!r})",
                           _MAX_LIMIT=_MAX_LIMIT, max_messages=max_messages))

    start = await _read_start(imap, safe_uid, mailbox)
    own_id = _message_ids(start["message_id"])
    if not own_id:
        return {
            "mailbox": mailbox,
            "messages": [_public(start, mailbox)],
            "total": 1,
            "complete": False,
            "note": (
                _("Mejlet saknar Message-ID, så konversationen kan inte sökas fram. "
                "Bara mejlet självt returneras; dess uid gäller i {mailbox}, så läs det "
                "med get_email(uid, mailbox={quoted}).",
                mailbox=mailbox, quoted=json.dumps(mailbox, ensure_ascii=False))
            ),
        }

    deadline = _clock() + _TIME_BUDGET_S
    # ID → (nivå, runda, ordning): nivå 0 för de närmaste ID:na i varje mejl,
    # 1 för resten av References; runda = hur många led från startmejlet
    known: dict[str, tuple[int, int, int]] = {}
    # Fler ID:n än _MAX_IDS från ett mejl kan aldrig sökas; de räknas bara
    dropped: set[str] = set()

    def learn(meta: dict[str, Any], round_no: int) -> None:
        ids = _ids_of(meta)
        for rank, msg_id in enumerate(ids[:_MAX_IDS]):
            if msg_id not in known:
                known[msg_id] = (0 if rank < _NEAREST_IDS else 1, round_no, len(known))
        dropped.update(msg_id for msg_id in ids[_MAX_IDS:] if msg_id not in known)

    learn(start, 1)
    searched: set[str] = set()
    found: dict[str, dict[str, Any]] = {}
    searches = 0
    stopped: str | None = None
    while len(found) <= max_messages:
        pending = sorted(
            (key, msg_id) for msg_id, key in known.items()
            if msg_id not in searched and key[1] <= _MAX_ROUNDS
        )
        if not pending:
            break
        if searches >= _MAX_SEARCHES or len(searched) >= _MAX_IDS:
            stopped = _("Sökningen avbröts efter {searches} sökningar i {ALL_MAIL} (taket per anrop).",
                        searches=searches, ALL_MAIL=ALL_MAIL)
            break
        if _clock() >= deadline:
            stopped = _("Sökningen avbröts vid tidsgränsen på {_TIME_BUDGET_S:.0f} s.", _TIME_BUDGET_S=_TIME_BUDGET_S)
            break
        batch = [msg_id for _, msg_id in pending[:min(_IDS_PER_SEARCH, _MAX_IDS - len(searched))]]
        metas = await _search_step(imap, batch)
        searches += 1
        searched.update(batch)
        for meta in metas:
            if meta["uid"] in found or not _belongs(meta, known.keys()):
                continue
            found[meta["uid"]] = meta
            # Mejlet hittades via det ID i det som ligger närmast startmejlet
            via = min(known[msg_id][1] for msg_id in _ids_of(meta) if msg_id in known)
            learn(meta, via + 1)
    unsearched = len(known) - len(searched) + len(dropped - known.keys())

    messages = sorted(found.values(), key=_date_key)
    start_in_all_mail = next((m for m in messages if own_id[0] in _message_ids(m["message_id"])), None)
    notes: list[str] = []
    if len(messages) > max_messages:
        keep = messages[-max_messages:]
        if start_in_all_mail is not None and start_in_all_mail not in keep:
            keep = [start_in_all_mail, *messages[-(max_messages - 1):]] if max_messages > 1 else [start_in_all_mail]
        messages = sorted(keep, key=_date_key)
        notes.append(
            _("Konversationen har fler mejl än max_messages ({max_messages}); "
            "startmejlet och de senaste returneras.",
            max_messages=max_messages)
        )
    elif stopped or dropped:
        reason = stopped or _("Ett mejl har fler än {_MAX_IDS} ID:n i References; bara de närmaste söktes.",
                              _MAX_IDS=_MAX_IDS)
        notes.append(_("{reason} {unsearched} ID:n söktes inte, så fler mejl kan finnas.",
               reason=reason, unsearched=unsearched))
    elif unsearched:
        notes.append(_("Sökningen avbröts efter maximalt antal rundor; fler mejl kan finnas."))
    if start_in_all_mail is None:
        notes.append(_("Startmejlet hittades inte i {ALL_MAIL}.", ALL_MAIL=ALL_MAIL))

    return {
        "mailbox": ALL_MAIL,
        "messages": [_public(m, ALL_MAIL) for m in messages],
        "total": len(found),
        "complete": not (unsearched or len(found) > max_messages),
        "note": " ".join(notes) or None,
    }
