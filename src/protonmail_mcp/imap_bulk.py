"""Gemensamt för IMAP-operationer på flera UID:n i samma mapp.

Resultatet har formen {"ok": [uid, ...], "failed": [{"uid": ..., "error": ...}]},
med UID:na i den ordning de angavs. Formatfel (ogiltigt UID, för många UID:n)
ger ValueError för hela anropet innan Bridge kontaktas; fel som gäller ett
enskilt mejl (finns inte, Bridge nekar) hamnar i "failed".
"""
import asyncio
import logging
import re
import time
from typing import Any

import aioimaplib

from . import policy

from .imap_client import (
    _READD_DELETES,
    _UID_RE,
    IMAPClient,
    _check_move,
    _decode,
    _mailbox_key,
    _quote_mailbox,
    _require_ok,
    _response_text,
    _serialized,
    _validate_uid,
)

logger = logging.getLogger(__name__)

MAX_UIDS = 100

# Fel som ett "bästa försök"-steg fångar: Bridge nekar (ValueError), timeout
# (TimeoutError är ett OSError), bruten anslutning och aioimaplib:s egna fel.
# Allt utom ValueError lämnar protokollet i okänt läge: kasta anslutningen.
BEST_EFFORT_ERRORS = (ValueError, OSError, aioimaplib.AioImapException)


def drop_connection_after(imap: IMAPClient, exc: BaseException) -> str:
    """Kastar anslutningen efter ett fel som inte är ValueError; returnerar felet som text."""
    if not isinstance(exc, ValueError):
        imap._discard_connection()
    return str(exc) or type(exc).__name__

BulkResult = dict[str, Any]


def validate_uids(uids: list[str]) -> list[str]:
    """Validerar varje UID och tar bort dubbletter (första förekomsten behålls)."""
    if not uids:
        raise ValueError("Ange minst ett UID")
    if len(uids) > MAX_UIDS:
        raise ValueError(f"För många UID:n ({len(uids)}) — högst {MAX_UIDS} per anrop")
    unique: list[str] = []
    for uid in uids:
        _strict_uid(uid)
        if uid not in unique:
            unique.append(uid)
    return unique


def _strict_uid(uid) -> str:
    # isascii/isdigit utöver _validate_uid: UID:na sätts ihop till ett UID-set,
    # så ett avslutande radbyte eller en Unicode-siffra får inte slinka igenom
    if not (isinstance(uid, str) and uid.isascii() and uid.isdigit()):
        raise ValueError(f"Ogiltigt UID: {uid!r} — måste vara ett heltal")
    return _validate_uid(uid)


def uid_set(uids: list[str]) -> str:
    """UID-set för ett IMAP-kommando, t.ex. "1,5,9". Varje UID valideras igen."""
    if not uids:
        raise ValueError("Tomt UID-set")
    return ",".join(_strict_uid(uid) for uid in uids)


def missing_error(uid: str, mailbox: str) -> str:
    return (
        f"Inget meddelande med UID {uid} i {mailbox}. "
        "UID:n gäller per mapp — kontrollera att mailbox är samma mapp som UID:t kom från."
    )


def result(uids: list[str], ok: list[str], failed: list[dict[str, str]]) -> BulkResult:
    """Sorterar ok och failed i samma ordning som uids."""
    order = {uid: i for i, uid in enumerate(uids)}
    return {
        "ok": sorted(set(ok), key=order.__getitem__),
        "failed": sorted(failed, key=lambda f: order[f["uid"]]),
    }


def single(res: BulkResult) -> bool:
    """Returform för ett enskilt UID: True, eller ValueError med felet."""
    if res["failed"]:
        raise ValueError(res["failed"][0]["error"])
    return True


async def existing_uids(imap: IMAPClient, mailbox: str, uids: list[str]) -> tuple[list[str], list[dict[str, str]]]:
    """Delar upp UID:na i de som finns i den valda mappen och de som saknas.

    Bridge svarar OK på UID STORE/COPY/MOVE/EXPUNGE även för UID:n som inte
    finns, så utan kontrollen skulle de rapporteras som lyckade. Ett nekat
    FETCH ger ValueError för hela anropet — då har inget skrivits.
    """
    resp = await imap._uid("fetch", uid_set(uids), "(UID)")
    _require_ok(resp, f"Kontrollen av UID:n i {mailbox}")
    seen: set[int] = set()
    for line in resp.lines:
        m = _UID_RE.search(_decode(line))
        if m:
            seen.add(int(m.group(1)))
    found = [uid for uid in uids if int(uid) in seen]
    missing = [{"uid": uid, "error": missing_error(uid, mailbox)} for uid in uids if int(uid) not in seen]
    return found, missing


def fail_all(uids: list[str], error: str) -> list[dict[str, str]]:
    return [{"uid": uid, "error": error} for uid in uids]


def _failure(what: str, resp) -> str:
    return f"{what} misslyckades ({resp.result}): {_response_text(resp)}"


async def _split_after_failure(
    imap: IMAPClient, mailbox: str, uids: list[str], error: str
) -> tuple[list[str], list[dict[str, str]]]:
    """Efter ett nekat MOVE/EXPUNGE: de UID:n som är kvar i mappen misslyckades.

    Bridge kan ha hunnit genomföra en del av kommandot; de UID:n som inte
    längre finns i mappen räknas som klara. Går det inte att kontrollera
    räknas alla som misslyckade.
    """
    try:
        left, _ = await existing_uids(imap, mailbox, uids)
    except ValueError as exc:
        return [], fail_all(uids, f"{error} (kunde inte kontrollera vilka som genomfördes: {exc})")
    left_set = set(left)
    return [u for u in uids if u not in left_set], fail_all(left, error)


async def store_flags(
    imap: IMAPClient, mailbox: str, uids: list[str], flags: str, add: bool, what: str
) -> BulkResult:
    """UID STORE ±FLAGS på de UID:n som finns i mailbox, i ett kommando. Kräver låset."""
    uids = validate_uids(uids)
    _quote_mailbox(mailbox)
    await imap._ensure_connected()
    await imap._select(mailbox)
    found, failed = await existing_uids(imap, mailbox, uids)
    ok: list[str] = []
    if found:
        resp = await imap._uid("store", uid_set(found), "+FLAGS" if add else "-FLAGS", flags)
        if resp.result == "OK":
            ok = found
        else:
            failed += fail_all(found, _failure(f"{what} i {mailbox}", resp))
    return result(uids, ok, failed)


@_serialized
@policy.write_operation("set_flags")
async def set_flags(imap: IMAPClient, mailbox: str, uids: list[str], flags: str, add: bool) -> BulkResult:
    """Sätter eller tar bort flaggor (t.ex. \\Seen) på flera mejl i samma mapp."""
    return await store_flags(imap, mailbox, uids, flags, add, "Ändringen av flaggorna")


async def already_in_target(
    imap: IMAPClient, mailbox: str, uids: list[str], target: str
) -> tuple[list[str], list[dict[str, str]]]:
    """(UID:n vars mejl redan ligger i target, failed för UID:n där det inte går att avgöra).

    uids finns i den valda mappen mailbox. Gäller bara när target är Trash
    eller Drafts: flyttas ett mejl dit som redan ligger där tar Bridge (gluon)
    först bort det därifrån, och Proton svarar sedan "Message does not exist"
    (Code=2501) — mejlet är då raderat permanent. Det händer live med ett mejl
    till sig själv, som är ETT Proton-mejl med både Inbox och Sent: efter att
    INBOX-kopian flyttats till Trash visar Bridges Sent-vy det i 20–40 s till
    (händelsehämtningen).

    Mejlen känns igen på X-Pm-Internal-Id (Protons mejl-id), med Message-ID,
    tid och storlek som reserv när id:t saknas (imap_labels._find_in_label).
    Fail-closed åt båda håll: finns flera mejl i target som inte går att
    skilja från källan flyttas mejlet inte, men det räknas inte heller som
    klart — det hamnar i failed med en förklaring. UID:n som saknar både id
    och Message-ID kontrolleras inte. Mappen mailbox är vald igen efteråt.
    Ett nekat FETCH eller SEARCH ger ValueError — då har inget skrivits.
    """
    if _mailbox_key(target) not in _READD_DELETES or not uids:
        return [], []
    from .imap_labels import AMBIGUOUS, _fetch_identities_by_uid, _find_in_label, searchable

    identities = await _fetch_identities_by_uid(imap, mailbox, uids)
    sources = {uid: identities[uid] for uid in uids if uid in identities and searchable(identities[uid])}
    if not sources:
        return [], []
    await imap._select(target)
    found, errors = await _find_in_label(imap, target, sources)
    await imap._select(mailbox)
    unclear = [
        {"uid": uid, "error": (
            f"UID {uid} i {mailbox} flyttades inte till {target}: det går inte att avgöra om "
            f"mejlet redan ligger där. {target} har flera mejl med samma Message-ID, tid och "
            f"storlek, och en flytt dit av ett mejl som redan ligger där raderar det permanent. "
            f"Kontrollera {target} med list_emails och flytta mejlet själv om det behövs."
        )}
        for uid, error in errors.items() if AMBIGUOUS in error
    ]
    return [uid for uid in sources if uid in found], unclear


def already_there_note(uids: list[str], mailbox: str, target: str) -> str:
    """Förklaringen i svaret när mejl redan låg i target och lämnades orörda."""
    where = "papperskorgen (Trash)" if _mailbox_key(target) == "trash" else target
    which = "det" if len(uids) == 1 else "dem"
    return (
        f"UID {', '.join(uids)} i {mailbox} ligger redan i {where}: det är samma Proton-mejl "
        f"som syns i båda vyerna, t.ex. ett mejl till sig själv som ligger i både INBOX och Sent. "
        f"Inget skickades till Bridge för {which}, eftersom en ny flytt dit skulle radera "
        f"mejlet permanent. Bridge-vyn uppdateras inom ~40 s."
    )


async def _move_found(imap: IMAPClient, mailbox: str, uids: list[str], target: str) -> BulkResult:
    quoted_target = _quote_mailbox(target)
    await imap._ensure_connected()
    await imap._select(mailbox)
    found, failed = await existing_uids(imap, mailbox, uids)
    skipped, unclear = await already_in_target(imap, mailbox, found, target)
    failed += unclear
    held = set(skipped) | {f["uid"] for f in unclear}
    to_move = [uid for uid in found if uid not in held]
    ok: list[str] = list(skipped)
    if to_move:
        resp = await imap._uid("move", uid_set(to_move), quoted_target)
        if resp.result == "OK":
            ok += to_move
        else:
            moved, more = await _split_after_failure(
                imap, mailbox, to_move, _failure(f"Flytten från {mailbox} till {target}", resp)
            )
            ok += moved
            failed += more
    res = result(uids, ok, failed)
    if skipped:
        res["note"] = already_there_note(skipped, mailbox, target)
    return res


@_serialized
@policy.write_operation("move", destructive=True)
async def move(imap: IMAPClient, mailbox: str, uids: list[str], target: str) -> BulkResult:
    """Flyttar flera mejl med ett UID MOVE. Samma vakter som move_message, för alla UID:n."""
    uids = validate_uids(uids)
    _quote_mailbox(mailbox)
    _quote_mailbox(target)
    _check_move(mailbox, target)
    return await _move_found(imap, mailbox, uids, target)


@_serialized
@policy.write_operation("delete", destructive=True)
async def delete(imap: IMAPClient, mailbox: str, uids: list[str], permanent: bool = False) -> BulkResult:
    """Flyttar flera mejl till Trash, eller raderar dem permanent ur Trash/Drafts.

    Samma vakter och texter som IMAPClient.delete_message. Permanent radering
    görs med UID STORE \\Deleted + UID EXPUNGE på just de UID:na, aldrig med
    EXPUNGE på hela mappen.
    """
    uids = validate_uids(uids)
    _quote_mailbox(mailbox)
    key = _mailbox_key(mailbox)
    if not permanent:
        if key == "trash":
            raise ValueError(
                "Mejlet ligger redan i Trash. Använd permanent=True för att radera det permanent."
            )
        _check_move(mailbox, "Trash")
        return await _move_found(imap, mailbox, uids, "Trash")

    if key not in _READD_DELETES:
        raise ValueError(
            f"Permanent radering görs bara i Trash eller Drafts, inte i {mailbox}. "
            "Ta bort mejlet utan permanent först, så hamnar det i Trash."
        )
    await imap._ensure_connected()
    await imap._select(mailbox)
    found, failed = await existing_uids(imap, mailbox, uids)
    ok: list[str] = []
    if found:
        ok, more = await permanent_delete_found(imap, mailbox, found, f"Den permanenta raderingen i {mailbox}")
        failed += more
    return result(uids, ok, failed)


# Bridges svar när Proton-API:t säger "Message does not exist" (go-proton-api,
# UnlabelMessages). Live kom det för en hel omgång permanenta raderingar i
# Trash, och mejlen försvann ändå ur Trash vid Bridges nästa händelsehämtning.
_DOES_NOT_EXIST = "Code=2501"
_FLAGS_RE = re.compile(r"FLAGS\s*\(([^)]*)\)", re.IGNORECASE)
ALL_MAIL = "All Mail"
TRASH = "Trash"


async def permanent_delete_found(
    imap: IMAPClient, mailbox: str, found: list[str], what: str
) -> tuple[list[str], list[dict[str, str]]]:
    """UID STORE \\Deleted + UID EXPUNGE på found, som finns i den valda mappen (Trash/Drafts).

    Returnerar (ok, failed). Nekar Bridge EXPUNGE tas \\Deleted bort igen och
    det kontrolleras vilka UID:n som ändå är borta. Aldrig EXPUNGE utan UID.

    Är felet Code=2501 ("Message does not exist") har Bridge (connector.go)
    gjort UnlabelMessages för hela omgången och avbrutit FÖRE DeleteMessage:
    mejlet som redan var raderat i Proton ger felet, och alla andra mejl i
    omgången — vanliga som utkast — tappar mappen men raderas inte. De blir
    kvar i All Mail utan mapp. Därför: identiteten hämtas medan mejlen syns,
    sedan väntas det högst imap_client._GONE_TIMEOUT sekunder tills de
    försvunnit ur mappen (Bridge visar ändringen först efter nästa
    händelsehämtning, var 20:e–40:e sekund), och ett försvunnet mejl räknas
    som raderat först när det inte heller finns i All Mail. Ligger det kvar
    där raderas det via Trash (_settle_vanished). Går det inte att avgöra
    hamnar mejlet i failed med en förklaring, aldrig i ok.

    Kräver låset. Under väntan släpps det mellan kontrollerna (_wait_until_gone),
    så att andra verktyg inte blockeras; anropet som helhet kan ändå ta ~50 s.
    """
    targets = uid_set(found)
    resp = await imap._uid("store", targets, "+FLAGS", r"(\Deleted)")
    if resp.result != "OK":
        return [], fail_all(found, _failure(f"Borttagningsmarkeringen i {mailbox}", resp))
    resp = await imap._uid("expunge", targets)
    if resp.result == "OK":
        return found, []
    # Lämna inte \Deleted kvar: nästa EXPUNGE i mappen skulle annars ta mejlen
    await imap._uid("store", targets, "-FLAGS", r"(\Deleted)")
    error = _failure(what, resp)
    if _DOES_NOT_EXIST not in _response_text(resp):
        return await _split_after_failure(imap, mailbox, found, error)
    identities = await _identities_for_check(imap, mailbox, found)
    vanished, failed = await _split_after_failure(imap, mailbox, found, error)
    if failed and not any(_UNCHECKED in f["error"] for f in failed):
        gone, failed = await _wait_until_gone(imap, mailbox, [f["uid"] for f in failed], error)
        vanished += gone
    ok, more = await _settle_vanished(imap, mailbox, vanished, identities)
    return ok, failed + more


# I felet från _split_after_failure när det inte gick att kontrollera mappen
_UNCHECKED = "kunde inte kontrollera vilka som genomfördes"


async def _identities_for_check(imap: IMAPClient, mailbox: str, uids: list[str]) -> dict[str, dict] | str:
    """Identiteten (imap_labels._parse_identities) för uids i vald mapp, eller felet som text."""
    from .imap_labels import _fetch_identities_by_uid  # imap_labels bygger på den här modulen

    try:
        return await _fetch_identities_by_uid(imap, mailbox, uids)
    except ValueError as exc:
        return str(exc)


async def _wait_until_gone(
    imap: IMAPClient, mailbox: str, uids: list[str], error: str
) -> tuple[list[str], list[dict[str, str]]]:
    """Väntar tills UID:na inte längre finns i mailbox, högst _GONE_TIMEOUT sekunder.

    Kräver låset. Väntan mellan kontrollerna sker utan låset (_sleep_unlocked),
    så att andra verktyg inte står still i upp till 45 s; varje kontroll görs
    med låset. Mappen väljs om inför varje kontroll — ett annat anrop kan ha
    valt en annan mapp, och Bridges köade ändringar syns först då. \\Deleted är
    redan borttagen, så en EXPUNGE från ett annat anrop kan inte ta mejlen.
    Returnerar (borta, failed med error för de som finns kvar).
    """
    from . import imap_client  # läses vid anropet, så att testerna kan korta tiderna

    timeout = imap_client._GONE_TIMEOUT
    deadline = time.monotonic() + timeout
    left = uids
    while True:
        await _sleep_unlocked(imap, imap_client._GONE_INTERVAL)
        try:
            await imap._ensure_connected()
            await imap._select(mailbox)
            left, _ = await existing_uids(imap, mailbox, left)
        except BEST_EFFORT_ERRORS as exc:
            detail = drop_connection_after(imap, exc)
            left_set = set(left)
            return [u for u in uids if u not in left_set], fail_all(left, f"{error} ({_UNCHECKED}: {detail})")
        if not left or time.monotonic() >= deadline:
            break
    left_set = set(left)
    note = f" Mejlet finns kvar i {mailbox} efter {timeout:g} s väntan på att Bridge skulle ta bort det."
    return [u for u in uids if u not in left_set], fail_all(left, error + note)


async def _sleep_unlocked(imap: IMAPClient, seconds: float) -> None:
    """Sover utan klientens lås och har det igen efteråt. Kräver låset.

    Som get_thread, som tar låset per steg: ett anrop som bara väntar på
    Bridge ska inte blockera andra verktyg. Avbryts väntan tas låset ändå
    tillbaka innan avbrottet går vidare, så att _serialized släpper det.
    """
    imap._lock.release()
    try:
        await asyncio.sleep(seconds)
    finally:
        await imap._lock.acquire()


def _maybe_in_all_mail(uid: str, mailbox: str, why: str) -> str:
    return (
        f"Mejlet med UID {uid} försvann ur {mailbox}, men {why}. Bridge svarade Code=2501 och "
        f"avbröt då raderingen: mejlen i samma omgång kan ha tappat {mailbox} utan att raderas, "
        f"och ligger i så fall kvar i All Mail utan mapp. Kontrollera All Mail (search_emails "
        f"med mailbox=\"All Mail\") och radera mejlet därifrån via Trash om det finns kvar."
    )


async def _settle_vanished(
    imap: IMAPClient, mailbox: str, vanished: list[str], identities: dict[str, dict] | str
) -> tuple[list[str], list[dict[str, str]]]:
    """Avgör om mejlen som försvunnit ur mailbox efter Code=2501 verkligen är raderade.

    Ett mejl är raderat när det inte heller finns i All Mail. Ligger det
    kvar där (föräldralöst: live sågs det för ett utkast, och enligt Bridges
    connector.go gäller det alla mejl i omgången) läggs det i Trash igen med
    UID COPY från All Mail och raderas där med UID STORE \\Deleted + UID
    EXPUNGE på just det UID:t (_delete_orphan). Returnerar (ok, failed).
    """
    from .imap_labels import AMBIGUOUS, _find_in_label, searchable

    if not vanished:
        return [], []
    if isinstance(identities, str):
        why = f"det gick inte att hämta dess identitet för kontrollen i All Mail ({identities})"
        return [], [{"uid": uid, "error": _maybe_in_all_mail(uid, mailbox, why)} for uid in vanished]
    failed = [
        {"uid": uid, "error": _maybe_in_all_mail(
            uid, mailbox, "det saknar både X-Pm-Internal-Id och Message-ID, så det går inte att leta upp i All Mail"
        )}
        for uid in vanished if not searchable(identities.get(uid, {}))
    ]
    sources = {uid: identities[uid] for uid in vanished if searchable(identities.get(uid, {}))}
    if not sources:
        return [], failed
    try:
        # Väntan kan ha kastat anslutningen (timeout) eller släppt låset
        await imap._ensure_connected()
        await imap._select(ALL_MAIL)
        orphans, errors = await _find_in_label(imap, ALL_MAIL, sources)
    except BEST_EFFORT_ERRORS as exc:
        detail = drop_connection_after(imap, exc)
        logger.warning("Kunde inte kontrollera i All Mail efter Code=2501: %s", detail)
        why = f"kontrollen i All Mail misslyckades ({detail})"
        return [], failed + [{"uid": uid, "error": _maybe_in_all_mail(uid, mailbox, why)} for uid in sources]
    ok = [uid for uid, error in errors.items() if AMBIGUOUS not in error]
    failed += [
        {"uid": uid, "error": _maybe_in_all_mail(
            uid, mailbox, "All Mail har flera mejl som inte går att skilja från det, så det går inte att "
            "avgöra om det raderades"
        )}
        for uid, error in errors.items() if AMBIGUOUS in error
    ]
    pending = list(orphans.items())
    for i, (src_uid, all_mail_uid) in enumerate(pending):
        try:
            problem = await _delete_orphan(imap, src_uid, all_mail_uid, sources[src_uid])
        except BEST_EFFORT_ERRORS as exc:
            # Anslutningen kan vara borta (_select kastar den vid fel): sluta här
            # och redovisa resten, men behåll det som redan är avgjort
            detail = drop_connection_after(imap, exc)
            logger.warning("Återställningen via Trash avbröts: %s", detail)
            why = (
                f"det låg kvar i All Mail utan mapp och raderingen via Trash avbröts ({detail}); "
                "det kan ligga kvar i Trash eller bara i All Mail"
            )
            failed += [
                {"uid": uid, "error": _maybe_in_all_mail(uid, mailbox, why)} for uid, _ in pending[i:]
            ]
            break
        if problem:
            failed.append({"uid": src_uid, "error": (
                f"Mejlet med UID {src_uid} försvann ur {mailbox} men Proton raderade det inte: det låg "
                f"kvar i All Mail utan mapp (UID {all_mail_uid} där). Det händer med alla mejl, även "
                f"utkast, i en omgång där Bridge svarar Code=2501. Ett nytt försök via Trash "
                f"misslyckades: {problem}. Mejlet kan ligga kvar i Trash eller bara i All Mail."
            )})
        else:
            ok.append(src_uid)
    return ok, failed


async def _delete_orphan(imap: IMAPClient, src_uid: str, all_mail_uid: str, identity: dict) -> str | None:
    """COPY från All Mail till Trash och permanent radering där. None om det gick."""
    from .imap_labels import _find_in_label

    # Väljs om för varje mejl: förra varvet slutade i Trash
    await imap._select(ALL_MAIL)
    resp = await imap._uid("copy", uid_set([all_mail_uid]), _quote_mailbox(TRASH))
    if resp.result != "OK":
        return _failure(f"Kopieringen till {TRASH}", resp)
    await imap._select(TRASH)
    found, errors = await _find_in_label(imap, TRASH, {src_uid: identity})
    if src_uid not in found:
        return f"mejlet hittades inte entydigt i {TRASH} efter kopieringen"
    trash_uid = uid_set([found[src_uid]])
    resp = await imap._uid("store", trash_uid, "+FLAGS", r"(\Deleted)")
    if resp.result != "OK":
        return _failure(f"Borttagningsmarkeringen i {TRASH}", resp)
    resp = await imap._uid("expunge", trash_uid)
    if resp.result != "OK":
        await imap._uid("store", trash_uid, "-FLAGS", r"(\Deleted)")
        return _failure(f"Den permanenta raderingen i {TRASH}", resp)
    return None
