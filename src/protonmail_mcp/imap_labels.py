"""Etiketter och stjärnmärkning i Proton via Bridge.

Bridge-semantik (se rapport-proton.md, "Sammanfattning: Bridge-semantik"):
- UID COPY till Labels/X lägger till etiketten; mejlet ligger kvar i sin mapp.
- UID EXPUNGE i Labels/X tar bara bort etiketten, inget annat.
- \\Flagged är samma sak som Starred, globalt i alla vyer.
- UID:n gäller per vy: mejlet har ett UID i INBOX och ett annat i Labels/X.

Funktionerna är modulnivåfunktioner med IMAPClient som första argument och
körs under klientens lås (_serialized), som IMAPClient:s egna metoder.
"""
import logging
import re
from email.parser import HeaderParser
from typing import Any

from .i18n import _
from . import policy
from .imap_bulk import (
    _FLAGS_RE,
    BEST_EFFORT_ERRORS,
    BulkResult,
    _split_after_failure,
    drop_connection_after,
    existing_uids,
    fail_all,
    missing_error,
    result,
    store_flags,
    uid_set,
    validate_uids,
)
from .imap_client import (
    _FETCH_CHUNK_SIZE,
    _FETCH_START_RE,
    _LITERAL_END_RE,
    _UID_RE,
    IMAPClient,
    _CONTROL_CHAR_RE,
    _check_user_folder,
    _decode,
    _decode_header_bytes,
    _imap_quoted,
    _mailbox_key,
    _quote_mailbox,
    _require_ok,
    _response_text,
    _serialized,
)
from .imap_threads import _MAX_ID_LENGTH, _MSG_ID_RE

logger = logging.getLogger(__name__)

LABEL_PREFIX = "Labels/"
STARRED = "Starred"
# Systemmappar (och behållarna Folders/Labels) som inte är etiketter
_NOT_LABELS = ("inbox", "archive", "sent", "spam", "trash", "drafts", "all mail", "folders", "labels")
# Mejlets identitet i en vy. X-Pm-Internal-Id är Protons eget mejl-id, som Bridge
# sätter på alla mejl: samma i alla vyer för samma Proton-mejl, olika för olika
# mejl (verifierat live). Ett mejl till sig själv är ETT Proton-mejl med både
# Inbox och Sent, alltså samma id i INBOX och Sent. Message-ID, tid och storlek
# räcker INTE: live finns två olika mejl i Trash med samma tre värden. De används
# bara som reserv när rubriken saknas, och då räknas flera träffar som tvetydiga.
_IDENTITY_ITEMS = "(UID INTERNALDATE RFC822.SIZE BODY.PEEK[HEADER.FIELDS (MESSAGE-ID X-PM-INTERNAL-ID)])"
_INTERNAL_ID_HEADER = "X-Pm-Internal-Id"
# Protons id:n är base64url (88 tecken live); allt annat litar vi inte på
_INTERNAL_ID_RE = re.compile(r"[A-Za-z0-9+/=_-]{1,200}")
_INTERNALDATE_RE = re.compile(r'INTERNALDATE\s+"([^"]*)"', re.IGNORECASE)
_SIZE_RE = re.compile(r'RFC822\.SIZE\s+(\d+)', re.IGNORECASE)
# Så många Message-ID per SEARCH (OR-kedja); Bridge går igenom hela vyn per sökning
_SEARCH_GROUP = 20


def label_mailbox(label: str) -> str:
    """Gör ett etikettnamn ("X" eller "Labels/X") till vynamnet "Labels/X".

    Kastar ValueError för Starred (stjärnmärkning, inte etikett), mappar
    (Folders/…), systemmappar, tomma namn och underetiketter.
    """
    name = label.strip()
    key = name.casefold()
    if key.startswith(LABEL_PREFIX.casefold()):
        name = LABEL_PREFIX + name[len(LABEL_PREFIX):].strip()
        key = name.casefold()
    bare = key[len(LABEL_PREFIX):] if key.startswith(LABEL_PREFIX.casefold()) else key
    if bare == "starred":
        raise ValueError(
            _("Starred är Protons stjärnmärkning, inte en etikett. "
            "Använd flag_email för att stjärnmärka och unflag_email för att ta bort stjärnan.")
        )
    if key.startswith("folders/"):
        raise ValueError(
            _("{label!r} är en mapp, inte en etikett. Ett mejl ligger i exakt en mapp — "
            "använd move_email för att flytta det dit.",
            label=label)
        )
    if bare in _NOT_LABELS and not key.startswith(LABEL_PREFIX.casefold()):
        raise ValueError(
            _("{label!r} är en systemmapp, inte en etikett. Använd move_email för att flytta "
            "mejl dit, eller ange en etikett (t.ex. \"Kvitton\" eller \"Labels/Kvitton\").",
            label=label)
        )
    if not key.startswith(LABEL_PREFIX.casefold()):
        name = LABEL_PREFIX + name
    if name == LABEL_PREFIX:
        raise ValueError(_("Etikettnamnet är tomt. Ange t.ex. \"Kvitton\" eller \"Labels/Kvitton\"."))
    _check_user_folder(name)
    _quote_mailbox(name)
    return name


async def _require_label(imap: IMAPClient, label: str) -> str:
    """Kontrollerar med LIST att etiketten finns och returnerar dess exakta namn.

    Bridge jämför namn exakt, men för en människa är "faktura" och "Faktura"
    samma etikett. Finns ingen exakt träff men precis en etikett med samma
    namn utan hänsyn till versaler används den — att i stället hänvisa till
    create_folder skulle skapa en nästan-dubblett. Flera sådana ger fel som
    räknar upp dem. Finns ingen alls: ValueError med hänvisning till create_folder.
    """
    # "*" och "%" i namnet är jokertecken i LIST: bara exakt samma namn räknas
    if any(name == label for _, name in await imap._list(_quote_mailbox(label))):
        return label
    key = label.casefold()
    variants = sorted({name for _, name in await imap._list("*") if name.casefold() == key})
    if len(variants) == 1:
        return variants[0]
    if variants:
        raise ValueError(
            _("Etiketten {label} finns inte med exakt det namnet, men det finns flera med annan "
            "versal/gemen: {variants}. Ange vilken som avses med exakt namn.",
            label=label, variants=', '.join(variants))
        )
    raise ValueError(
        _("Etiketten {label} finns inte. Skapa den först med create_folder(\"{label}\"); "
        "list_mailboxes visar vilka etiketter som finns.",
        label=label)
    )


def _usable_message_id(value: str | None) -> str | None:
    """Message-ID att söka på, eller None.

    Rubriken styrs av avsändaren och kan vara vikt mitt i eller ha en
    kommentar ("<a@x>\\r\\n (kommentar)"); då tas msg-id:t ut som i
    get_thread. Saknas vinkelparenteser används värdet om det är ett enda
    ord utan kontrolltecken. Annars None, så att UID:t får ett eget fel i
    stället för att hela massanropet avvisas.
    """
    text = (value or "").strip()
    m = _MSG_ID_RE.search(text)
    candidate = m.group() if m else text
    if (
        not candidate
        or len(candidate) > _MAX_ID_LENGTH
        or _CONTROL_CHAR_RE.search(candidate)
        or (m is None and re.search(r"\s", candidate))
    ):
        return None
    return candidate


def _parse_identities(lines: list) -> dict[str, dict[str, Any]]:
    """UID → {"message_id", "internal_id", "date", "size"} ur ett FETCH-svar med _IDENTITY_ITEMS.

    UID och övriga fält kan stå både före och efter header-literalen (Bridge
    lägger UID efter vid UID FETCH), och svaren kan komma i valfri ordning.
    """
    out: dict[str, dict[str, Any]] = {}
    i = 0
    while i < len(lines):
        head = lines[i]
        i += 1
        if isinstance(head, bytearray):
            continue
        head_s = _decode(head).strip()
        if not _FETCH_START_RE.match(head_s):
            continue
        raw = b""
        tail = ""
        if _LITERAL_END_RE.search(head_s):
            if i < len(lines) and isinstance(lines[i], bytearray):
                raw = bytes(lines[i])
                i += 1
            if i < len(lines) and not isinstance(lines[i], bytearray):
                candidate = _decode(lines[i]).strip()
                if not _FETCH_START_RE.match(candidate):
                    tail = candidate
                    i += 1
        meta = f"{_LITERAL_END_RE.sub('', head_s)} {tail}"
        uid_m = _UID_RE.search(meta)
        if not uid_m:
            continue
        date_m = _INTERNALDATE_RE.search(meta)
        size_m = _SIZE_RE.search(meta)
        # Som get_thread: UTF-8, annars latin-1 — aldrig surrogattecken som
        # skulle få aioimaplib att kasta mitt i SEARCH-kommandot
        headers = HeaderParser().parsestr(_decode_header_bytes(raw)) if raw else None
        message_id = headers.get("message-id") if headers is not None else None
        internal_ids = (headers.get_all(_INTERNAL_ID_HEADER) or []) if headers is not None else []
        out[uid_m.group(1)] = {
            "message_id": _usable_message_id(str(message_id)) if message_id else None,
            # Bara ett enda, välformat värde räknas; annars reservvägen
            "internal_id": _usable_internal_id(internal_ids),
            "date": date_m.group(1) if date_m else None,
            "size": size_m.group(1) if size_m else None,
        }
    return out


def _usable_internal_id(values: list) -> str | None:
    if len(values) != 1:
        return None
    value = str(values[0]).strip()
    return value if _INTERNAL_ID_RE.fullmatch(value) else None


def searchable(info: dict) -> bool:
    """Om mejlet går att leta upp i en annan vy (via X-Pm-Internal-Id eller Message-ID)."""
    return bool(info.get("internal_id") or info.get("message_id"))


async def _fetch_identities_by_uid(imap: IMAPClient, mailbox: str, uids: list[str]) -> dict[str, dict]:
    resp = await imap._client.uid("fetch", uid_set(uids), _IDENTITY_ITEMS)
    _require_ok(resp, _("Hämtningen av Message-ID i {mailbox}", mailbox=mailbox))
    return _parse_identities(resp.lines)


# Felet från _find_in_label när mejlet inte finns i vyn, och när det inte går
# att avgöra vilket av flera likadana mejl det är
NOT_IN_VIEW = "har inte etiketten"
AMBIGUOUS = "flera mejl"


def _search_term(info: dict) -> str:
    if info.get("internal_id"):
        return f"HEADER {_INTERNAL_ID_HEADER} {_imap_quoted(info['internal_id'], _INTERNAL_ID_HEADER)}"
    return f"HEADER Message-ID {_imap_quoted(info['message_id'], 'Message-ID')}"


def _same_mail(info: dict, candidates: dict[str, dict]) -> tuple[list[str], bool]:
    """(UID:n i vyn som är samma mejl som info, om svaret är säkert).

    Med X-Pm-Internal-Id: exakt samma id. Ett mejl i vyn som saknar id men
    har samma Message-ID, tid och storlek gör svaret osäkert. Utan id:
    Message-ID, tid och storlek — mer än en träff är tvetydigt.
    """
    def legacy(cand: dict) -> bool:
        return (
            bool(info.get("message_id")) and cand["message_id"] == info["message_id"]
            and cand["date"] == info["date"] and cand["size"] == info["size"]
        )

    if info.get("internal_id"):
        same = [uid for uid, cand in candidates.items() if cand.get("internal_id") == info["internal_id"]]
        unknown = [uid for uid, cand in candidates.items() if not cand.get("internal_id") and legacy(cand)]
        return same + unknown, len(same) <= 1 and not unknown
    same = [uid for uid, cand in candidates.items() if legacy(cand)]
    return same, len(same) <= 1


async def _find_in_label(
    imap: IMAPClient, label: str, sources: dict[str, dict]
) -> tuple[dict[str, str], dict[str, str]]:
    """Hittar varje källmejls UID i etikettens vy (som måste vara vald).

    Söker med SEARCH HEADER X-Pm-Internal-Id, eller HEADER Message-ID när
    id:t saknas (Bridge matchar båda som delsträng), och jämför sedan exakt
    (_same_mail). Returnerar (käll-UID → etikett-UID, käll-UID → fel); felet
    innehåller NOT_IN_VIEW när mejlet inte finns i vyn och AMBIGUOUS när det
    inte går att avgöra vilket av flera mejl det är.
    """
    terms = sorted({_search_term(info) for info in sources.values() if searchable(info)})
    seqnums: set[str] = set()
    for start in range(0, len(terms), _SEARCH_GROUP):
        group = terms[start:start + _SEARCH_GROUP]
        criteria = ["OR"] * (len(group) - 1) + group
        seqnums.update(await imap._search_seqnums(criteria))

    candidates: dict[str, dict] = {}
    ordered = sorted(seqnums, key=int)
    for start in range(0, len(ordered), _FETCH_CHUNK_SIZE):
        chunk = ordered[start:start + _FETCH_CHUNK_SIZE]
        resp = await imap._client.fetch(",".join(chunk), _IDENTITY_ITEMS)
        _require_ok(resp, _("Hämtningen av sökträffarna i {label}", label=label))
        candidates.update(_parse_identities(resp.lines))

    found: dict[str, str] = {}
    errors: dict[str, str] = {}
    for src_uid, info in sources.items():
        same, certain = _same_mail(info, candidates) if searchable(info) else ([], True)
        if not same:
            errors[src_uid] = _("Mejlet med UID {src_uid} {not_in_view} {label}.",
                                src_uid=src_uid, not_in_view=_(NOT_IN_VIEW), label=label)
        elif not certain:
            errors[src_uid] = (
                _("Det finns {ambiguous} i {label} som inte går att skilja från UID {src_uid} "
                "(samma Message-ID, tid och storlek), så det går inte att avgöra vilket det är. "
                "Lista {label} och ange UID:t där med mailbox=\"{label}\".",
                ambiguous=_(AMBIGUOUS), label=label, src_uid=src_uid)
            )
        else:
            found[src_uid] = same[0]
    return found, errors


def internal_id_of(values: list) -> str | None:
    """Protons mejl-id ur rubrikvärdena för X-Pm-Internal-Id, eller None (saknas, flera, felformat)."""
    return _usable_internal_id(values)


@_serialized
async def has_internal_id(imap: IMAPClient, mailbox: str, internal_id: str) -> bool:
    """Om Proton-mejlet med X-Pm-Internal-Id internal_id syns i mailbox.

    Bara läsning: SELECT, SEARCH HEADER X-Pm-Internal-Id och FETCH med
    BODY.PEEK, och exakt jämförelse av id:t (SEARCH är delsträngsmatchning).
    ValueError om id:t är ogiltigt eller Bridge nekar.
    """
    if _usable_internal_id([internal_id]) is None:
        raise ValueError(_("Ogiltigt X-Pm-Internal-Id: {internal_id!r}", internal_id=internal_id))
    _quote_mailbox(mailbox)
    await imap._ensure_connected()
    await imap._select(mailbox)
    source = {"id": {"internal_id": internal_id, "message_id": None, "date": None, "size": None}}
    found, _errors = await _find_in_label(imap, mailbox, source)
    return "id" in found


async def _expunge_in_label(
    imap: IMAPClient, label: str, label_uids: list[str]
) -> tuple[list[str], dict[str, str]]:
    """Tar bort etiketten: UID STORE \\Deleted + UID EXPUNGE på just de UID:na i etikettens vy.

    Returnerar (etikett-UID:n som är klara, etikett-UID → fel). Nekar Bridge
    EXPUNGE kan den ha hunnit ta bort etiketten från en del av mejlen; de
    UID:n som inte längre finns i vyn räknas då som klara, som för move och
    delete. Aldrig EXPUNGE utan UID.
    """
    ordered = sorted(set(label_uids), key=int)
    uids = uid_set(ordered)
    resp = await imap._uid("store", uids, "+FLAGS", r"(\Deleted)")
    if resp.result != "OK":
        error = _("Borttagningen av etiketten {label} misslyckades ({result}): {text}",
                  label=label, result=resp.result, text=_response_text(resp))
        return [], {uid: error for uid in ordered}
    resp = await imap._uid("expunge", uids)
    if resp.result == "OK":
        return ordered, {}
    error = _("Borttagningen av etiketten {label} misslyckades ({result}): {text}",
              label=label, result=resp.result, text=_response_text(resp))
    done, failed = await _split_after_failure(imap, label, ordered, error)
    left = [f["uid"] for f in failed]
    if left:
        # Lämna inte \Deleted kvar: nästa EXPUNGE i vyn skulle annars ta bort etiketten
        await imap._uid("store", uid_set(left), "-FLAGS", r"(\Deleted)")
    return done, {f["uid"]: f["error"] for f in failed}


@_serialized
@policy.write_operation("add_label")
async def add_label(imap: IMAPClient, mailbox: str, uids: list[str], label: str) -> BulkResult:
    """Lägger till etiketten på mejlen med UID COPY; mejlen ligger kvar i mailbox."""
    uids = validate_uids(uids)
    target = label_mailbox(label)
    _quote_mailbox(mailbox)
    if _mailbox_key(mailbox) == _mailbox_key(target):
        raise ValueError(
            _("Mejlen i {target} har redan etiketten {target}. Ange mappen där mejlet ligger "
            "(t.ex. INBOX) som mailbox.",
            target=target)
        )
    await imap._ensure_connected()
    target = await _require_label(imap, target)
    await imap._select(mailbox)
    found, failed = await existing_uids(imap, mailbox, uids)
    ok: list[str] = []
    if found:
        resp = await imap._uid("copy", uid_set(found), _quote_mailbox(target))
        if resp.result == "OK":
            ok = found
        else:
            failed += fail_all(
                found, _("Att lägga till etiketten {target} misslyckades ({result}): {text}",
                         target=target, result=resp.result, text=_response_text(resp))
            )
    return result(uids, ok, failed)


@_serialized
@policy.write_operation("remove_label")
async def remove_label(imap: IMAPClient, mailbox: str, uids: list[str], label: str) -> BulkResult:
    """Tar bort etiketten från mejlen utan att flytta eller radera dem.

    Mejlets UID i etikettens vy hittas via X-Pm-Internal-Id, eller Message-ID
    om id:t saknas (UID:n gäller per vy);
    där görs UID STORE \\Deleted + UID EXPUNGE på just de UID:na, vilket hos
    Bridge bara tar bort etiketten. Är mailbox etiketten själv används UID:na direkt.
    """
    uids = validate_uids(uids)
    target = label_mailbox(label)
    _quote_mailbox(mailbox)
    await imap._ensure_connected()
    target = await _require_label(imap, target)

    if _mailbox_key(mailbox) == _mailbox_key(target):
        await imap._select(target)
        found, failed = await existing_uids(imap, target, uids)
        mapping = {uid: uid for uid in found}
    else:
        await imap._select(mailbox)
        identities = await _fetch_identities_by_uid(imap, mailbox, uids)
        failed = []
        sources: dict[str, dict] = {}
        for uid in uids:
            info = identities.get(uid)
            if info is None:
                failed.append({"uid": uid, "error": missing_error(uid, mailbox)})
            elif not searchable(info):
                failed.append({"uid": uid, "error": (
                    _("Mejlet med UID {uid} i {mailbox} saknar Message-ID och X-Pm-Internal-Id "
                    "(eller har värden som inte går att söka på) och kan inte hittas i "
                    "{target}. Lista {target} och ange UID:t där med mailbox=\"{target}\".",
                    uid=uid, mailbox=mailbox, target=target)
                )})
            else:
                sources[uid] = info
        mapping = {}
        if sources:
            await imap._select(target)
            mapping, errors = await _find_in_label(imap, target, sources)
            failed += [{"uid": uid, "error": error} for uid, error in errors.items()]

    ok: list[str] = []
    if mapping:
        done, errors = await _expunge_in_label(imap, target, list(mapping.values()))
        done_set = set(done)
        ok = [src for src, label_uid in mapping.items() if label_uid in done_set]
        failed += [{"uid": src, "error": errors[label_uid]} for src, label_uid in mapping.items() if label_uid in errors]
    return result(uids, ok, failed)



async def _without_flag(imap: IMAPClient, uids: list[str]) -> list[str]:
    """De av uids i Starred (som är vald) som inte har \\Flagged."""
    resp = await imap._uid("fetch", uid_set(sorted(set(uids), key=int)), "(UID FLAGS)")
    _require_ok(resp, _("Hämtningen av flaggorna i {STARRED}", STARRED=STARRED))
    unflagged: set[str] = set()
    for line in resp.lines:
        text = _decode(line)
        uid_m, flags_m = _UID_RE.search(text), _FLAGS_RE.search(text)
        if uid_m and flags_m and "\\flagged" not in flags_m.group(1).casefold().split():
            unflagged.add(uid_m.group(1))
    return [uid for uid in uids if uid in unflagged]


def _starred_note(detail: str) -> str:
    return (
        _("Stjärnan är borttagen, men mejlet kunde inte tas bort ur Starred-vyn ({detail}). "
        "Det kan synas där, utan stjärna, tills Bridge hämtat ändringen från Proton (inom ~40 s).",
        detail=detail)
    )


async def _leave_starred_view(imap: IMAPClient, mailbox: str, uids: list[str]) -> str | None:
    """Tar bort avmarkerade mejl ur Starred-vyn (UID EXPUNGE på just deras UID:n där).

    -FLAGS \\Flagged tar bort stjärnan i Proton direkt (Bridge anropar
    UnlabelMessages med Starred), men Bridges Starred-vy uppdateras först när
    Bridge hämtar händelserna från Proton, var 20:e–40:e sekund
    (internal/user/user.go: EventPeriod + EventJitter). Till dess ligger mejlet
    kvar i Starred utan \\Flagged. EXPUNGE i Starred tar bara bort Starred,
    som för en etikett — men det tar bort stjärnan i Proton. Därför tas bara
    UID:n i Starred bort som är samma Proton-mejl (_find_in_label,
    X-Pm-Internal-Id) och som saknar \\Flagged: en tvilling med samma
    Message-ID, tid och storlek som har kvar stjärnan rörs inte. Bästa försök: stjärnan är redan borta, så ett fel här — även en timeout
    eller en bruten anslutning — loggas och returneras som en förklaring till
    svaret ("note"), aldrig som ett fel för hela anropet. Efter timeout eller
    avbrott kastas anslutningen. None när allt gick.
    """
    try:
        if _mailbox_key(mailbox) == "starred":
            mapping = {uid: uid for uid in uids}
        else:
            identities = await _fetch_identities_by_uid(imap, mailbox, uids)
            sources = {uid: info for uid, info in identities.items() if uid in uids and searchable(info)}
            if not sources:
                return None
            await imap._select(STARRED)
            mapping, _ = await _find_in_label(imap, STARRED, sources)
        if mapping:
            await imap._select(STARRED)
            starred_uids = await _without_flag(imap, list(mapping.values()))
            if not starred_uids:
                return None
            _, errors = await _expunge_in_label(imap, STARRED, starred_uids)
            if errors:
                detail = next(iter(errors.values()))
                logger.warning("Kunde inte ta bort mejl ur Starred-vyn: %s", detail)
                return _starred_note(detail)
    except BEST_EFFORT_ERRORS as exc:
        detail = drop_connection_after(imap, exc)
        logger.warning("Kunde inte ta bort mejl ur Starred-vyn: %s", detail)
        return _starred_note(detail)
    return None


@_serialized
@policy.write_operation("set_starred")
async def set_starred(imap: IMAPClient, mailbox: str, uids: list[str], starred: bool) -> BulkResult:
    """Stjärnmärker (\\Flagged) eller avmarkerar mejlen; gäller i alla vyer hos Proton.

    Vid avmarkering tas mejlen också bort ur Starred-vyn, som annars visar dem
    tills Bridge hämtat nästa händelse från Proton (se _leave_starred_view).
    """
    what = _("Stjärnmärkningen") if starred else _("Borttagningen av stjärnmärkningen")
    res = await store_flags(imap, mailbox, uids, r"(\Flagged)", starred, what)
    if not starred and res["ok"]:
        note = await _leave_starred_view(imap, mailbox, res["ok"])
        if note:
            res["note"] = note
    return res
