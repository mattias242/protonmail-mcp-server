import asyncio
import functools
import logging
import math
import re
import unicodedata
from datetime import date
from email.parser import HeaderParser
from typing import Any

import aioimaplib

from . import policy
from .config import Settings
from .email_parser import (
    FieldCleaner,
    clean_msg_ids,
    decode_header_value,
    iso_date,
    suspicious_reasons,
)
from .mailbox_names import decode_modified_utf7, describe_mailbox, encode_modified_utf7

logger = logging.getLogger(__name__)

# Format: (\Flags) "delimiter" "name"  eller  (\Flags) "delimiter" name;
# avgränsaren kan också vara NIL. Namnet är en quoted-string (RFC 3501) eller en atom.
_LIST_RE = re.compile(
    r'^\(([^)]*)\)\s+(?:"(?:[^"\\]|\\.)*"|NIL)\s+(?:"((?:[^"\\]|\\.)*)"|([^\s"]+))$',
    re.IGNORECASE,
)
_QUOTED_ESCAPE_RE = re.compile(r'\\(.)')
_UID_RE = re.compile(r'\bUID\s+(\d+)\b')
_FLAGS_RE = re.compile(r'FLAGS\s+\(([^)]*)\)')
# aioimaplib tar bort "* " före otaggade svar, men tål det om det står kvar
_FETCH_START_RE = re.compile(r'^(?:\*\s+)?\d+\s+FETCH\s+\(', re.IGNORECASE)
_LITERAL_END_RE = re.compile(r'\{\d+\}$')
_EXISTS_RE = re.compile(r'^(?:\*\s+)?(\d+)\s+EXISTS\b', re.IGNORECASE)
# Statuslistan sist på STATUS-raden: '"namn" (MESSAGES 5 UNSEEN 1)'. Bara den
# tolkas — mappnamnet före kan själv innehålla t.ex. "Messages 0".
_STATUS_ITEMS_RE = re.compile(r'\(([^()]*)\)\s*$')
# aioimaplib tolkar svaret rekursivt, en stackram per rad; ~3000 brev i ett
# FETCH-svar ger RecursionError och en låst anslutning
_FETCH_CHUNK_SIZE = 200
# Så många av de nyaste kandidaterna jämförs lokalt mot avkodat ämne och
# avsändare (Bridge söker bara i okodade headers). 1000 brev tar ~4 s.
_LOCAL_SCAN_LIMIT = 1000
# Metadata per brev i list_emails och search_emails: samma fält i båda
_METADATA_ITEMS = "(UID FLAGS BODY.PEEK[HEADER.FIELDS (DATE FROM TO SUBJECT MESSAGE-ID)])"
# fullmatch: "$" släpper igenom ett avslutande "\n", och "\d" matchar Unicode-siffror
_VALID_UID_RE = re.compile(r'[0-9]+')
# Bara kontrolltecken (CR, LF, NUL m.fl.) kan bryta ut ur en quoted-string.
# Backslash och citattecken escapas av _quote_mailbox; "%", "*" och ".." är
# giltiga namn hos Proton och har ingen särskild betydelse utanför LIST-mönster.
# Ensamma surrogater går inte att koda till modified UTF-7.
_VALID_MAILBOX_RE = re.compile(r'[^\x00-\x1F\x7F\uD800-\uDFFF]{1,255}')


def _validate_mailbox(name: str) -> str:
    if not _VALID_MAILBOX_RE.fullmatch(name):
        raise ValueError(f"Ogiltigt mailbox-namn: {name!r}")
    return name


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_ISO_DATE_RE = re.compile(r'^(\d{4})-(\d{2})-(\d{2})$')
_IMAP_DATE_RE = re.compile(r'^(\d{1,2})-([A-Za-z]{3})-(\d{4})$')
# Kontrolltecken (CR, LF, NUL m.fl.) kan avsluta ett IMAP-kommando i förtid
_CONTROL_CHAR_RE = re.compile(r'[\x00-\x1F\x7F]')


def _to_imap_date(value: str, field: str = "datum") -> str:
    """Accepterar bara YYYY-MM-DD eller DD-Mon-YYYY, returnerar alltid DD-Mon-YYYY.

    Allt annat ger ValueError, eftersom värdet skickas ociterat i SEARCH: ett
    okänt format får servern att svara BAD, och ett påhängt CRLF blir ett eget
    kommando. Månadsnamnen är engelska oavsett locale (RFC 3501 date-month).
    """
    text = value.strip()
    iso = _ISO_DATE_RE.match(text)
    imap = _IMAP_DATE_RE.match(text)
    try:
        if iso:
            year, month, day = (int(g) for g in iso.groups())
        elif imap and imap.group(2).capitalize() in _MONTHS:
            day, year = int(imap.group(1)), int(imap.group(3))
            month = _MONTHS.index(imap.group(2).capitalize()) + 1
        else:
            raise ValueError
        date(year, month, day)
    except ValueError:
        raise ValueError(
            f"Ogiltigt datum för {field}: {value!r} — använd YYYY-MM-DD eller DD-Mon-YYYY"
        ) from None
    return f"{day:02d}-{_MONTHS[month - 1]}-{year:04d}"


def _validate_uid(uid: str) -> str:
    """Kastar ValueError om uid inte är ett rent heltal (förhindrar IMAP sequence-set-injektion)."""
    if not _VALID_UID_RE.fullmatch(uid):
        raise ValueError(f"Ogiltigt UID: {uid!r} — måste vara ett heltal")
    return uid


def _escape_imap_string(value: str) -> str:
    """Escapar dubbla citattecken och backslash i ett IMAP quoted-string (RFC 3501)."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _imap_quoted(value: str, field: str) -> str:
    """Gör ett användarvärde till en IMAP quoted-string, eller kastar ValueError.

    Alla fritextvärden som interpoleras i ett IMAP-kommando ska gå hit.
    Escaping räcker inte: ett CR/LF i värdet avslutar kommandot, och resten
    av raden körs som ett eget kommando (aioimaplib kontrollerar inget).
    """
    if _CONTROL_CHAR_RE.search(value):
        raise ValueError(f"{field} får inte innehålla kontrolltecken (t.ex. radbrytning): {value!r}")
    return f'"{_escape_imap_string(value)}"'


def _quote_mailbox(name: str) -> str:
    """Validerar, kodar och citerar ett läsbart mailbox-namn för IMAP.

    Namnet kodas till modified UTF-7 (RFC 3501 §5.1.3), så att "Labels/Näs"
    skickas som "Labels/N&AOQ-s", och citeras som quoted-string.

    Mailbox-namn som innehaller tecken utanfor IMAP:s "atom"-syntax (t.ex.
    hakparenteser eller mellanslag) maste skickas citerade, annars misslyckas
    SELECT/STATUS/CREATE/COPY/RENAME tyst eller med fel mot vissa IMAP-servrar.
    """
    _validate_mailbox(name)
    return f'"{_escape_imap_string(encode_modified_utf7(name))}"'


def _decode(b: bytes | bytearray | str) -> str:
    if isinstance(b, (bytes, bytearray)):
        return b.decode("utf-8", errors="replace")
    return b


def _response_text(resp) -> str:
    """Serverns förklaring i ett IMAP-svar, t.ex. "no such mailbox"."""
    return " ".join(_decode(line) for line in resp.lines).strip()


def _exists(resp) -> int | None:
    """EXISTS-räknaren ur ett SELECT-svar, eller None om den saknas."""
    for line in resp.lines:
        if isinstance(line, bytearray):
            continue
        m = _EXISTS_RE.search(_decode(line))
        if m:
            return int(m.group(1))
    return None


def _parse_seqnums(resp) -> list[str]:
    if resp.result != "OK" or not resp.lines:
        return []
    line = _decode(resp.lines[0]).strip()
    return [n for n in line.split() if n.isdigit()]


def _parse_list_line(line: str) -> str | None:
    """Extraherar det läsbara mappnamnet ur en IMAP LIST-rad. Returnerar None om raden inte matchar."""
    entry = _parse_list_entry(line)
    return entry[1] if entry else None


def _parse_list_entry(line: str) -> tuple[list[str], str] | None:
    """Extraherar attributen och det läsbara mappnamnet ur en IMAP LIST-rad.

    Namnet avescapas och avkodas från modified UTF-7, så att _quote_mailbox
    ger tillbaka exakt samma namn. Returnerar None om raden inte matchar.
    """
    line = line.strip()
    if not line or line.startswith("command"):
        return None
    m = _LIST_RE.match(line)
    if not m:
        return None
    attributes = m.group(1).split()
    if m.group(2) is not None:
        wire = _QUOTED_ESCAPE_RE.sub(r"\1", m.group(2))
    else:
        wire = m.group(3)
    try:
        return attributes, decode_modified_utf7(wire)
    except ValueError:
        logger.warning("Mappnamnet är inte giltig modified UTF-7, behålls oavkodat: %r", wire)
        return attributes, wire


def _parse_headers(raw: bytes) -> dict[str, str]:
    """Parsar raw RFC822-headers till en dict med lowercase-nycklar."""
    # HeaderParser vecklar ut vikta rader (RFC 5322 §2.2.3); värdena
    # lämnas annars råa, eftersom get_email_headers används för analys;
    # osynliga tecken rensas i verktygslagret (email_parser.clean_raw_headers).
    # Radvis strip i stället för re.sub(r"\s*\r?\n\s*"), som tar kvadratisk
    # tid på en lång blankteckenföljd utan radbrytning (100 000 blanksteg: 50 s).
    # Rå 8-bitars text (RFC 6532) avkodas före parsningen — BytesHeaderParser
    # gör den annars till ersättningstecken.
    result: dict[str, str] = {}
    for k, v in HeaderParser().parsestr(_decode_header_bytes(raw)).items():
        parts = (part.strip() for part in str(v).split("\n"))
        result[k.lower()] = " ".join(part for part in parts if part)
    return result


def _close_transport(client: aioimaplib.IMAP4) -> None:
    transport = getattr(getattr(client, "protocol", None), "transport", None)
    if transport is not None:
        try:
            transport.close()
        except Exception:
            pass


async def _with_timeout(aw, seconds: float):
    """Väntar in aw högst seconds sekunder (asyncio.timeout); TimeoutError annars.

    Det inre anropet väntas in direkt i den egna uppgiften, så ett avbrott
    utifrån når alltid fram som CancelledError, även när det kommer i samma
    ögonblick som anropet blir klart. (asyncio.wait_for i Python 3.10/3.11
    svalde just det avbrottet, bpo-42130; testerna i
    test_efterfix_l7_avbrott*.py vaktar mot att det kommer tillbaka.)
    """
    async with asyncio.timeout(seconds):
        return await aw


def _serialized(method):
    """Kör IMAP-operationen under klientens lås.

    MCP-servern kan ta emot flera verktygsanrop samtidigt, men en IMAP-session
    har ett tillstånd (inloggad, vald mapp). Utan lås kan två anrop logga in
    samtidigt eller byta mapp åt varandra mitt i en FETCH.
    """
    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        async with self._lock:
            try:
                return await method(self, *args, **kwargs)
            except (asyncio.CancelledError, asyncio.TimeoutError, OSError, aioimaplib.AioImapException):
                # Efter timeout eller avbrott har aioimaplib kvar hängande kommandon
                # och protokollet är i okänt tillstånd — börja om med ny anslutning.
                self._discard_connection()
                raise
    return wrapper


class _BridgeProtocol(aioimaplib.IMAP4ClientProtocol):
    """aioimaplib:s protokoll med två rättelser.

    1. aioimaplib 2.0.1 gör line.replace(b'* ', b'') på hela den otaggade
       raden, så LIST-raden för "Folders/* Viktigt" blev "Folders/Viktigt"
       och namnet gick sedan inte att öppna. Här tas bara det inledande "* "
       bort; resten av _untagged_response är densamma som i aioimaplib.
    2. När anslutningen bryts (Bridge startas om) avbryts väntande kommandon
       direkt med ConnectionError, och nya kommandon skickas inte ut på den
       döda transporten. Annars väntar de ut hela timeouten och felet blir
       aioimaplib:s råa CommandTimeout.
    """

    lost: ConnectionError | None = None

    def connection_lost(self, exc: Exception | None) -> None:
        reason = f": {exc}" if exc else ""
        self.lost = ConnectionError(f"Anslutningen till ProtonMail Bridge bröts{reason}")
        for command in [self.pending_sync_command, *self.pending_async_commands.values()]:
            # Command saknar ett publikt sätt att misslyckas; _exception är det
            # aioimaplib självt använder för CommandTimeout
            if command is not None and not command._event.is_set():
                command._exception = ConnectionError(
                    f"Anslutningen till ProtonMail Bridge bröts under {command.name}{reason} "
                    "— försök igen, nästa anrop ansluter på nytt"
                )
                command.close(b"connection lost", "NO")
        super().connection_lost(exc)

    def send(self, line: str, scrub: str = None) -> None:
        if self.lost is not None:
            raise ConnectionError(
                f"{self.lost} — försök igen, nästa anrop ansluter på nytt"
            )
        super().send(line, scrub=scrub)

    def _untagged_response(self, line: bytes):
        if line.startswith(b"* "):
            line = line[2:]
        if self.pending_sync_command is not None:
            self.pending_sync_command.append_to_resp(line)
            return self.pending_sync_command
        match = aioimaplib.aioimaplib.message_data_re.match(line)
        if match:
            cmd_name, text = match.group(1), match.string
        else:
            cmd_name, _, text = line.partition(b" ")
        command = self.pending_async_commands.get(cmd_name.decode().upper())
        if command is not None:
            command.append_to_resp(text)
            return command
        # NOOP är asynkront, och servern kan skicka oombedda otaggade svar
        command = self.pending_async_commands.get("NOOP")
        if command is not None:
            command.append_to_resp(line)
        else:
            logger.debug("Ignorerat otaggat svar: %r", line)
        return command


def _use_bridge_protocol(client) -> None:
    """Låter en nyskapad aioimaplib.IMAP4 använda _BridgeProtocol.

    IMAP4 skapar protokollet själv, och anslutningen tar det via
    `lambda: self.protocol`, så klassbytet på instansen gäller hela vägen.
    Testattrapper (MagicMock) lämnas orörda.
    """
    protocol = getattr(client, "protocol", None)
    if type(protocol) is aioimaplib.IMAP4ClientProtocol:
        protocol.__class__ = _BridgeProtocol


# aioimaplib:s tillstånd före inloggning och efter utloggning (övriga: AUTH, SELECTED)
_NOT_LOGGED_IN_STATES = (aioimaplib.STARTED, aioimaplib.CONNECTED, aioimaplib.NONAUTH, aioimaplib.LOGOUT)


class IMAPAuthenticationError(Exception):
    """Bridge nekade inloggningen (fel användarnamn/Bridge-lösenord, eller kontot inte laddat)."""


class IMAPClient:
    # aioimaplib:s timeout per kommando (hälsning, LOGIN, SELECT, SEARCH, ...).
    # Sätts från PROTONMAIL_IMAP_TIMEOUT: SEARCH i All Mail tar 5–9 s mot Bridge.
    timeout: float = 30.0
    # UID STORE/COPY/EXPUNGE, som aioimaplib 2.0.1 kör helt utan timeout.
    # COPY till en etikett kräver ett anrop till Proton-API:t, därav lika lång tid.
    uid_timeout: float = 30.0
    # Hälsokontrollen före varje anrop; svarar inte NOOP ansluter vi på nytt
    noop_timeout: float = 5.0

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.timeout = self.uid_timeout = settings.imap_timeout
        self._client: aioimaplib.IMAP4 | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        host, port = self._settings.imap_host, self._settings.imap_port
        logger.info("Ansluter till IMAP %s:%d (plain)", host, port)

        def forget_on_connection_lost(_exc: Exception | None) -> None:
            # Bridge omstartad eller nere: släpp klienten så att nästa anrop
            # ansluter direkt i stället för att vänta ut NOOP-timeouten. Mitt
            # i ett anrop (låset taget) får klienten vara kvar: dess kommandon
            # ger ConnectionError (_BridgeProtocol), och _serialized släpper
            # den — annars blir nästa self._client.x ett AttributeError.
            if self._client is client:
                logger.warning("Anslutningen till ProtonMail Bridge bröts")
                if not self._lock.locked():
                    self._client = None

        # Bridge v3 på port 1143 kräver plain IMAP (inte direkt SSL/TLS)
        client = aioimaplib.IMAP4(
            host=host, port=port, timeout=self.timeout, conn_lost_cb=forget_on_connection_lost
        )
        _use_bridge_protocol(client)
        # self._client sätts först när inloggningen lyckats. Annars fastnar en
        # klient i NONAUTH, där NOOP lyckas men allt annat avvisas, för alltid.
        try:
            await self._wait_for_socket(client, host, port)
            try:
                await client.wait_hello_from_server()
            except asyncio.TimeoutError:
                raise asyncio.TimeoutError(
                    f"ProtonMail Bridge på {host}:{port} skickade ingen hälsning "
                    f"inom {self.timeout:g} s — körs Bridge?"
                ) from None
            try:
                resp = await client.login(self._settings.username, self._settings.password)
            except asyncio.TimeoutError:
                raise asyncio.TimeoutError(
                    f"ProtonMail Bridge på {host}:{port} svarade inte på inloggningen "
                    f"inom {self.timeout:g} s"
                ) from None
            if resp.result != "OK":
                detail = _response_text(resp)
                raise IMAPAuthenticationError(
                    f"Inloggningen mot ProtonMail Bridge misslyckades ({resp.result} {detail}) — "
                    "kontrollera PROTONMAIL_USERNAME och Bridge-lösenordet"
                )
        except BaseException:
            # Även CancelledError: stäng socketen så att ingen halvinloggad session blir kvar
            _close_transport(client)
            raise
        self._client = client
        logger.info("IMAP inloggad som %s", self._settings.username)

    async def _wait_for_socket(self, client: aioimaplib.IMAP4, host: str, port: int) -> None:
        """Väntar in TCP-anslutningen så att "connection refused" syns direkt.

        Annars väntar wait_hello_from_server ut hela timeouten och felet från
        aioimaplib:s anslutningstask hämtas aldrig. _client_task är privat i
        aioimaplib, därav den försiktiga åtkomsten.
        """
        task = getattr(client, "_client_task", None)
        if not isinstance(task, asyncio.Future):
            return
        try:
            await _with_timeout(task, self.timeout)
        except (OSError, asyncio.TimeoutError) as exc:
            reason = str(exc) or type(exc).__name__
            raise ConnectionError(
                f"Kan inte nå ProtonMail Bridge på {host}:{port} — körs Bridge? ({reason})"
            ) from exc

    async def disconnect(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.logout()
            except Exception:
                pass
            finally:
                _close_transport(client)

    def _discard_connection(self) -> None:
        """Släpper anslutningen och stänger dess socket; nästa anrop ansluter på nytt."""
        client, self._client = self._client, None
        if client is not None:
            _close_transport(client)

    async def _select(self, mailbox: str):
        resp = await self._client.select(_quote_mailbox(mailbox))
        if resp.result != "OK":
            # Bridge (och aioimaplib) står kvar i förra mappen efter ett misslyckat
            # SELECT, tvärtemot RFC 3501 §6.3.1, så nästa kommando skulle träffa fel
            # mapp. CLOSE skulle expungera där och UNSELECT saknas — kasta anslutningen.
            self._discard_connection()
            detail = _response_text(resp)
            raise ValueError(
                f"Mappen finns inte eller kan inte väljas: {mailbox!r}"
                + (f" ({detail})" if detail else "")
            )
        if _exists(resp) == 0:
            await self._refuse_container(mailbox)
        return resp

    async def _refuse_container(self, mailbox: str) -> None:
        """Kastar ValueError om mailbox är en \\Noselect-behållare (Folders, Labels).

        Bridge svarar OK med 0 mejl på SELECT och STATUS av behållarna, så de
        ser ut som tomma mappar. Attributen finns bara i LIST, och LIST frågas
        bara när mappen ser tom ut, så vanliga anrop kostar ingen extra rundresa.
        Kontrollen bygger på serverns egna attribut — samma källa som
        selectable i list_mailboxes — i stället för en lista med kända namn.
        Nekas LIST går det inte att avgöra, och då får det tomma svaret stå.
        """
        resp = await self._client.list('""', _quote_mailbox(mailbox))
        if resp.result != "OK":
            logger.warning("LIST för %r nekades, kan inte avgöra om det är en behållare", mailbox)
            return
        for line in resp.lines:
            entry = _parse_list_entry(_decode(line))
            # "*" och "%" i namnet är jokertecken i LIST: bara exakt samma namn räknas
            if entry and entry[1] == mailbox and not describe_mailbox(mailbox, entry[0])["selectable"]:
                raise ValueError(
                    f"{mailbox} är en behållare, inte en mapp, och innehåller inga mejl. "
                    f"Ange en mapp under den (t.ex. {mailbox}/…); list_mailboxes visar "
                    "vilka som går att öppna (selectable: true)."
                )

    async def _uid(self, command: str, *args: str):
        """UID STORE/COPY/EXPUNGE med timeout. UID FETCH har redan aioimaplib:s egen."""
        try:
            return await _with_timeout(self._client.uid(command, *args), self.uid_timeout)
        except asyncio.TimeoutError:
            raise asyncio.TimeoutError(
                f"ProtonMail Bridge svarade inte på UID {command.upper()} inom {self.uid_timeout:g} s"
            ) from None

    async def _ensure_connected(self) -> None:
        # NOOP är tillåtet i NONAUTH, så NOOP ensam visar inte att klienten är inloggad
        protocol = getattr(self._client, "protocol", None) if self._client is not None else None
        state = getattr(protocol, "state", None)
        if state in _NOT_LOGGED_IN_STATES:
            logger.warning("IMAP-klienten är inte inloggad (%s) — återansluter", state)
            self._discard_connection()
        elif isinstance(getattr(protocol, "lost", None), ConnectionError):
            logger.warning("Anslutningen till ProtonMail Bridge är bruten — återansluter")
            self._discard_connection()
        if self._client is None:
            await self.connect()
            return
        try:
            await _with_timeout(self._client.noop(), self.noop_timeout)
        except Exception:
            logger.warning("IMAP NOOP misslyckades — återansluter")
            self._discard_connection()
            await self.connect()

    async def _list(self, pattern: str) -> list[tuple[list[str], str]]:
        """LIST "" <pattern> som (attribut, läsbart namn). pattern är redan citerat.

        Kastar ValueError om Bridge inte svarar OK: ett nekat LIST får inte se
        ut som en brevlåda utan mappar.
        """
        resp = await self._client.list('""', pattern)
        _require_ok(resp, "Listningen av mappar")
        entries = []
        for line in resp.lines:
            entry = _parse_list_entry(_decode(line))
            if entry and entry[1]:
                entries.append(entry)
        return entries

    @_serialized
    async def list_mailboxes(self) -> list[dict[str, Any]]:
        await self._ensure_connected()
        return [describe_mailbox(name, attributes) for attributes, name in await self._list("*")]

    @_serialized
    async def list_messages(
        self, mailbox: str, page: int = 1, page_size: int = 20
    ) -> dict[str, Any]:
        _quote_mailbox(mailbox)
        await self._ensure_connected()
        select_resp = await self._select(mailbox)

        exists = _exists(select_resp) or 0

        if page < 1 or page_size < 1:
            raise ValueError(f"page och page_size måste vara >= 1 (fick page={page}, page_size={page_size})")

        total_pages = math.ceil(exists / page_size) if exists > 0 else 0

        # Beräkna sekvensintervall för nyaste-först paginering. En sida förbi
        # slutet är tom — annars skulle max(1, ...) nedan ge det äldsta brevet.
        offset = (page - 1) * page_size
        if offset >= exists:
            return {
                "messages": [],
                "total": exists,
                "page": page,
                "pages": total_pages,
                "has_more": False,
            }

        stop = exists - offset
        start = max(1, stop - page_size + 1)

        fetch_resp = await self._client.fetch(f"{start}:{stop}", _METADATA_ITEMS)
        # Ett nekat FETCH får inte se ut som en tom sida
        _require_ok(fetch_resp, f"Hämtningen av sida {page} i {mailbox}")

        msgs = _sort_newest_first(_parse_fetch_metadata(fetch_resp.lines))
        return {
            "messages": msgs,
            "total": exists,
            "page": page,
            "pages": total_pages,
            "has_more": page < total_pages,
        }

    @_serialized
    async def get_message(self, mailbox: str, uid: str) -> bytes | None:
        safe_uid = _validate_uid(uid)
        _quote_mailbox(mailbox)
        await self._ensure_connected()
        await self._select(mailbox)
        resp = await self._client.uid("fetch", safe_uid, "BODY.PEEK[]")
        # NO/BAD är ett serverfel, inte "finns inte": None betyder bara att
        # svaret var OK men saknade meddelandet
        _require_ok(resp, f"Hämtningen av UID {safe_uid} i {mailbox}")
        # lines[0] = "SEQNUM FETCH (BODY[] {SIZE}"
        # lines[1] = bytearray with raw email
        for line in resp.lines:
            if isinstance(line, bytearray) and len(line) > 10:
                return bytes(line)
        return None

    @_serialized
    async def search_messages(
        self,
        mailbox: str,
        from_addr: str | None = None,
        subject: str | None = None,
        since: str | None = None,
        before: str | None = None,
        unseen: bool | None = None,
        max_results: int = 50,
        *,
        to_addr: str | None = None,
        cc_addr: str | None = None,
        text: str | None = None,
        body: str | None = None,
        flagged: bool | None = None,
        answered: bool | None = None,
        larger_than: int | None = None,
        smaller_than: int | None = None,
        has_attachment: bool | None = None,
    ) -> dict[str, Any]:
        """Söker i mappen och returnerar de nyaste träffarna med metadata.

        Bridge jämför SUBJECT, FROM, TO och CC mot de okodade headervärdena,
        så "ä" eller ett ord i ett B-kodat ämne ger aldrig träff på servern.
        Servern söker därför bara på datum, flaggor, storlek och brödtext, och
        ämne/avsändare/mottagare jämförs lokalt mot avkodade värden för de
        _LOCAL_SCAN_LIMIT nyaste kandidaterna. Är alla termer ASCII får servern
        dessutom leta bland äldre brev, där den åtminstone hittar träffar i
        okodade headers.

        text (IMAP TEXT, hela meddelandet) och body (IMAP BODY, brödtexten)
        söks på servern; se _text_search_criteria för hur åäö hanteras.

        has_attachment avgörs lokalt ur BODYSTRUCTURE: en del med
        Content-Disposition attachment är en bilaga, en inbäddad (inline) bild
        är det inte. Bridge bygger alla mejl med bilagor som multipart/mixed
        (verifierat live), så BODYSTRUCTURE hämtas bara för multipart-mejl, och
        med has_attachment=True söker servern bara bland dem.

        Returnerar dict med messages (nyaste först, högst max_results), total
        (antal hittade träffar; en undre gräns när complete är False),
        has_more, complete (alla kandidater kontrollerades) och note.
        """
        # Validera allt innan något skickas till servern
        if max_results < 1:
            raise ValueError(f"max_results måste vara >= 1 (fick {max_results})")
        terms: list[tuple[str, str]] = []  # (fält i metadata, sökterm)
        text_criteria: list[str] = []
        if from_addr:
            text_criteria += ["FROM", _imap_quoted(from_addr, "from_addr")]
            terms.append(("from", from_addr))
        if subject:
            text_criteria += ["SUBJECT", _imap_quoted(subject, "subject")]
            terms.append(("subject", subject))
        if to_addr:
            text_criteria += ["TO", _imap_quoted(to_addr, "to_addr")]
            terms.append(("to", to_addr))
        if cc_addr:
            text_criteria += ["CC", _imap_quoted(cc_addr, "cc_addr")]
            terms.append(("cc", cc_addr))
        criteria: list[str] = []
        if since:
            criteria += ["SINCE", _to_imap_date(since, "since")]
        if before:
            criteria += ["BEFORE", _to_imap_date(before, "before")]
        criteria += _flag_criteria(unseen, "SEEN", "UNSEEN", negate_true=True)
        criteria += _flag_criteria(flagged, "FLAGGED", "UNFLAGGED")
        criteria += _flag_criteria(answered, "ANSWERED", "UNANSWERED")
        if larger_than is not None:
            criteria += ["LARGER", str(_validate_size(larger_than, "larger_than"))]
        if smaller_than is not None:
            criteria += ["SMALLER", str(_validate_size(smaller_than, "smaller_than"))]
        if text:
            criteria += _text_search_criteria("TEXT", text, "text")
        if body:
            criteria += _text_search_criteria("BODY", body, "body")
        _flag_criteria(has_attachment, "", "")  # kastar ValueError om det inte är bool/None
        if has_attachment is True:
            criteria += ["HEADER", "Content-Type", '"multipart"']
        _quote_mailbox(mailbox)

        await self._ensure_connected()
        await self._select(mailbox)

        candidates = await self._search_seqnums(criteria)
        if not terms and has_attachment is None:
            newest = candidates[-max_results:]
            msgs = _sort_newest_first(await self._fetch_metadata(newest))
            return _search_result(msgs, len(candidates), max_results, complete=True)

        async def local_filter(seqnums: list[str]) -> list[dict[str, Any]]:
            msgs = await self._fetch_metadata(seqnums, content_type=has_attachment is not None)
            return await self._filter_attachments(_filter_messages(msgs, terms), has_attachment)

        window = candidates[-_LOCAL_SCAN_LIMIT:]
        matches = await local_filter(window)
        complete = len(candidates) <= len(window)
        ascii_terms = all(term.isascii() for _, term in terms)
        unchecked = 0
        if not complete and ascii_terms and len(matches) <= max_results:
            # Utan ämnes-/adressvillkor är kandidaterna själva de äldre träffarna
            raw_hits = await self._search_seqnums(criteria + text_criteria) if text_criteria else candidates
            older = [n for n in raw_hits if int(n) < int(window[0])]
            # Nyaste först, tills det finns fler träffar än max_results
            while older and len(matches) <= max_results:
                chunk, older = older[-_FETCH_CHUNK_SIZE:], older[:-_FETCH_CHUNK_SIZE]
                matches += await local_filter(chunk)
            unchecked = len(older)
            if not terms:
                complete = unchecked == 0

        note = None
        if not complete and not terms:
            note = (
                f"Bilagorna kontrollerades inte för alla {len(candidates)} brev som matchar "
                "övriga villkor, så total är en undre gräns. Begränsa med since eller before."
            )
        elif not complete:
            note = (
                f"Bara de {len(window)} nyaste av {len(candidates)} brev som matchar övriga "
                "villkor jämfördes mot avkodat ämne, avsändare och mottagare. "
            )
            if ascii_terms:
                note += (
                    "Äldre brev söktes på servern, som bara jämför mot okodade headers, "
                    "så äldre träffar i kodade headers kan saknas. "
                )
            note += "Begränsa med since eller before för att söka bland äldre brev."
        return _search_result(
            _sort_newest_first(matches), len(matches), max_results,
            complete=complete, note=note, more=unchecked > 0,
        )

    async def _search_seqnums(self, criteria: list[str]) -> list[str]:
        """Kör SEARCH och returnerar sekvensnumren i stigande ordning (äldst först)."""
        command = " ".join(criteria) or "ALL"
        # Skyddsnät: värdena är redan validerade, men ett kontrolltecken här
        # skulle avsluta kommandot i förtid och göra resten till ett nytt
        if _CONTROL_CHAR_RE.search(command):
            raise ValueError(f"Otillåtet tecken i IMAP-sökkommandot: {command!r}")
        resp = await self._client.search(command)
        if resp.result != "OK":
            # Annars ser t.ex. ett ogiltigt datum ut som "inga träffar"
            raise ValueError(f"Sökningen misslyckades ({resp.result}): {_response_text(resp)}")
        return sorted(_parse_seqnums(resp), key=int)

    async def _fetch_metadata(self, seqnums: list[str], content_type: bool = False) -> list[dict[str, Any]]:
        """Hämtar UID, FLAGS och sökheaders i delar om högst _FETCH_CHUNK_SIZE.

        content_type: hämta även Content-Type (nyckeln "content_type"), som
        _filter_attachments behöver och tar bort igen.
        """
        fields = "DATE FROM TO CC SUBJECT MESSAGE-ID" + (" CONTENT-TYPE" if content_type else "")
        extra = ("cc", "content-type") if content_type else ("cc",)
        msgs: list[dict[str, Any]] = []
        for start in range(0, len(seqnums), _FETCH_CHUNK_SIZE):
            chunk = seqnums[start:start + _FETCH_CHUNK_SIZE]
            fetch_resp = await self._client.fetch(
                ",".join(chunk),
                f"(UID FLAGS BODY.PEEK[HEADER.FIELDS ({fields})])",
            )
            if fetch_resp.result != "OK":
                raise ValueError(
                    f"Hämtningen av sökträffarna misslyckades ({fetch_resp.result}): {_response_text(fetch_resp)}"
                )
            msgs += _parse_fetch_metadata(fetch_resp.lines, extra_headers=extra)
        return msgs

    async def _filter_attachments(
        self, msgs: list[dict[str, Any]], has_attachment: bool | None
    ) -> list[dict[str, Any]]:
        """Behåller mejlen med (True) eller utan (False) bilaga; None behåller alla.

        Bara multipart-mejl kan ha bilagor hos Bridge, så BODYSTRUCTURE hämtas
        bara för dem. Nyckeln content_type tas bort ur metadata.
        """
        if has_attachment is not None:
            multipart = [m["uid"] for m in msgs if "multipart" in m.get("content_type", "").lower()]
            with_attachment = await self._fetch_attachment_uids(multipart)
            msgs = [m for m in msgs if (m["uid"] in with_attachment) == has_attachment]
        for m in msgs:
            m.pop("content_type", None)
        return msgs

    async def _fetch_attachment_uids(self, uids: list[str]) -> set[str]:
        """UID:n bland uids (i vald mapp) som har minst en bilaga enligt BODYSTRUCTURE."""
        found: set[str] = set()
        for start in range(0, len(uids), _FETCH_CHUNK_SIZE):
            chunk = uids[start:start + _FETCH_CHUNK_SIZE]
            resp = await self._uid("fetch", ",".join(chunk), "(UID BODYSTRUCTURE)")
            _require_ok(resp, "Hämtningen av mejlens struktur (BODYSTRUCTURE)")
            found |= _attachment_uids(resp.lines)
        return found

    @_serialized
    @policy.write_operation("set_flags")
    async def set_flags(self, mailbox: str, uid: str, flags: str, add: bool) -> bool:
        safe_uid = _validate_uid(uid)
        _quote_mailbox(mailbox)
        await self._ensure_connected()
        await self._select(mailbox)
        await self._require_uid(mailbox, safe_uid)
        action = "+FLAGS" if add else "-FLAGS"
        resp = await self._uid("store", safe_uid, action, flags)
        _require_ok(resp, f"Ändringen av flaggorna på UID {safe_uid} i {mailbox}")
        return True

    @_serialized
    @policy.write_operation("move_message", destructive=True)
    async def move_message(self, mailbox: str, uid: str, target: str) -> bool | dict[str, Any]:
        """Flyttar ett mejl med UID MOVE. Kastar ValueError om flytten avvisas eller misslyckas.

        Till en etikett (Labels/…) läggs etiketten till och mejlet ligger kvar
        i källmappen. Starred avvisas som mål: Bridge tar då bort mejlet ur
        källmappen (stjärnmärkning är flaggan \\Flagged). Ligger mejlet redan i
        Trash/Drafts och det är målet görs ingenting, se _move.
        """
        return await self._move(mailbox, uid, target)

    async def _move(self, mailbox: str, uid: str, target: str) -> bool | dict[str, Any]:
        """UID MOVE av ett mejl. True, eller ett svar med "note" om mejlet redan låg i target.

        Ligger mejlet redan i Trash/Drafts (samma Proton-mejl i en annan vy,
        t.ex. Sent-kopian av ett mejl till sig själv) skickas ingen flytt:
        Bridge skulle då radera det permanent (imap_bulk.already_in_target).
        Svaret blir {"ok": [uid], "failed": [], "note": förklaring}.
        """
        safe_uid = _validate_uid(uid)
        _quote_mailbox(mailbox)
        quoted_target = _quote_mailbox(target)
        _check_move(mailbox, target)
        await self._ensure_connected()
        await self._select(mailbox)
        await self._require_uid(mailbox, safe_uid)
        from . import imap_bulk  # imap_bulk bygger på den här modulen

        skipped, unclear = await imap_bulk.already_in_target(self, mailbox, [safe_uid], target)
        if unclear:
            raise ValueError(unclear[0]["error"])
        if skipped:
            return {
                "ok": [safe_uid], "failed": [],
                "note": imap_bulk.already_there_note([safe_uid], mailbox, target),
            }
        resp = await self._uid("move", safe_uid, quoted_target)
        _require_ok(resp, f"Flytten av UID {safe_uid} från {mailbox} till {target}")
        return True

    @_serialized
    @policy.write_operation("delete_message", destructive=True)
    async def delete_message(self, mailbox: str, uid: str, permanent: bool = False) -> bool | dict[str, Any]:
        """Flyttar ett mejl till Trash, eller raderar det permanent ur Trash/Drafts.

        Ligger mejlet redan i Trash (i en annan vy) flyttas det inte, se _move.

        Permanent radering görs med UID STORE \\Deleted + UID EXPUNGE på just
        det UID:t, aldrig med EXPUNGE på hela mappen.
        """
        safe_uid = _validate_uid(uid)
        key = _mailbox_key(mailbox)
        if not permanent:
            if key == "trash":
                raise ValueError(
                    "Mejlet ligger redan i Trash. Använd permanent=True för att radera det permanent."
                )
            return await self._move(mailbox, safe_uid, "Trash")

        if key not in _READD_DELETES:
            raise ValueError(
                f"Permanent radering görs bara i Trash eller Drafts, inte i {mailbox}. "
                "Ta bort mejlet utan permanent först, så hamnar det i Trash."
            )
        from . import imap_bulk  # imap_bulk bygger på den här modulen

        await self._ensure_connected()
        await self._select(mailbox)
        await self._require_uid(mailbox, safe_uid)
        _, failed = await imap_bulk.permanent_delete_found(
            self, mailbox, [safe_uid], f"Den permanenta raderingen av UID {safe_uid} i {mailbox}"
        )
        if failed:
            raise ValueError(failed[0]["error"])
        return True

    async def _require_uid(self, mailbox: str, uid: str) -> None:
        """Kontrollerar att UID:t finns i den valda mappen.

        Bridge svarar OK på UID STORE/MOVE/EXPUNGE även för UID:n som inte
        finns, så utan kontrollen skulle verktygen rapportera lyckat för ingenting.
        """
        resp = await self._uid("fetch", uid, "(UID)")
        _require_ok(resp, f"Kontrollen av UID {uid} i {mailbox}")
        for line in resp.lines:
            m = _UID_RE.search(_decode(line))
            if m and int(m.group(1)) == int(uid):
                return
        raise ValueError(
            f"Inget meddelande med UID {uid} i {mailbox}. "
            "UID:n gäller per mapp — kontrollera att mailbox är samma mapp som UID:t kom från."
        )

    @_serialized
    async def get_message_headers(self, mailbox: str, uid: str) -> dict | None:
        safe_uid = _validate_uid(uid)
        _quote_mailbox(mailbox)
        await self._ensure_connected()
        await self._select(mailbox)
        resp = await self._client.uid(
            "fetch", safe_uid,
            "BODY.PEEK[HEADER.FIELDS (FROM TO CC SUBJECT DATE MESSAGE-ID REPLY-TO)]"
        )
        _require_ok(resp, f"Hämtningen av rubrikerna för UID {safe_uid} i {mailbox}")
        for line in resp.lines:
            if isinstance(line, bytearray) and len(line) > 4:
                return _parse_headers(bytes(line))
        return None

    @_serialized
    @policy.write_operation("create_folder")
    async def create_folder(self, name: str) -> bool:
        kind = _check_user_folder(name)
        quoted = _quote_mailbox(name)
        await self._ensure_connected()
        resp = await self._client.create(quoted)
        _require_ok(resp, f"Att skapa {kind}en {name}" + _parent_hint(name))
        return True

    @_serialized
    @policy.write_operation("delete_folder", destructive=True)
    async def delete_folder(self, name: str, force: bool = False) -> bool:
        """Tar bort en mapp eller etikett; vägrar om den inte är tom, utom med force.

        Bridge tar bort mappen även när den innehåller mejl, och för en etikett
        försvinner den då från alla mejl — därför kontrolleras STATUS först.
        Bridge tittar inte heller på undermappar, så en mapp med undermappar
        räknas inte som tom (LIST "<namn>/*").
        """
        kind = _check_user_folder(name)
        quoted = _quote_mailbox(name)
        await self._ensure_connected()
        status = await self._client.status(quoted, "(MESSAGES)")
        _require_ok(status, f"Kontrollen av {kind}en {name}")
        count = _status_messages(status)
        if count is None:
            raise ValueError(f"Kunde inte läsa antal mejl i {name}: {_response_text(status)}")
        if count and not force:
            raise ValueError(
                f"{kind.capitalize()}en {name} är inte tom ({count} mejl) och tas inte bort. "
                f"Med force=True tas den bort ändå: mejlen raderas inte, men för en etikett "
                f"försvinner etiketten från alla {count} mejl, och för en mapp ligger mejlen "
                "sedan inte kvar i mappen (de finns i All Mail)."
            )
        if not force:
            await self._refuse_subfolders(name, kind)
        resp = await self._client.delete(quoted)
        _require_ok(resp, f"Att ta bort {kind}en {name}")
        return True

    async def _refuse_subfolders(self, name: str, kind: str) -> None:
        """Kastar ValueError om mappen har undermappar, tomma eller inte."""
        # Kodat och citerat som namnet; "*" och "%" i själva namnet är också
        # jokertecken i LIST, därav prefixkontrollen på svaret
        pattern = _quote_mailbox(name)[:-1] + '/*"'
        prefix = name + "/"
        children = sorted(n for _, n in await self._list(pattern) if n.startswith(prefix))
        if children:
            shown = ", ".join(children[:5]) + (" …" if len(children) > 5 else "")
            raise ValueError(
                f"{kind.capitalize()}en {name} har {len(children)} "
                f"undermapp{'ar' if len(children) != 1 else ''} ({shown}) och tas "
                "inte bort. Bridge kontrollerar inte undermapparna, och vad Proton gör med dem "
                "och deras mejl när föräldern tas bort är inte känt. Ta bort eller flytta "
                "undermapparna först, eller använd force=True för att ta bort den ändå."
            )

    @_serialized
    @policy.write_operation("rename_folder", destructive=True)
    async def rename_folder(self, old_name: str, new_name: str) -> bool:
        old_kind = _check_user_folder(old_name)
        new_kind = _check_user_folder(new_name)
        if old_kind != new_kind:
            raise ValueError(
                f"Proton kan inte byta mellan mapp och etikett ({old_name} → {new_name}). "
                "Behåll prefixet: Folders/ → Folders/ eller Labels/ → Labels/."
            )
        quoted_old, quoted_new = _quote_mailbox(old_name), _quote_mailbox(new_name)
        await self._ensure_connected()
        resp = await self._client.rename(quoted_old, quoted_new)
        _require_ok(resp, f"Namnbytet {old_name} → {new_name}" + _parent_hint(new_name))
        return True

    @_serialized
    async def get_mailbox_status(self, mailbox: str) -> dict[str, int]:
        quoted = _quote_mailbox(mailbox)
        await self._ensure_connected()
        resp = await self._client.status(quoted, "(MESSAGES UNSEEN)")
        if resp.result != "OK":
            raise ValueError(
                f"Mappen finns inte eller kan inte läsas: {mailbox!r} ({_response_text(resp)})"
            )
        items = _status_items(resp)
        if items.get("messages") == 0:
            await self._refuse_container(mailbox)
        return {key: items[key] for key in ("messages", "unseen") if key in items}


def _header_matches(value: str, term: str) -> bool:
    """Skiftlägesoberoende delsträngsmatchning, som IMAP SEARCH, på avkodad text.

    NFC-normalisering gör att ett "ä" skrivet som a + kombinerande trema
    (vanligt från macOS) hittar ett förkomponerat "ä" och tvärtom.
    """
    def fold(text: str) -> str:
        return unicodedata.normalize("NFC", text).casefold()
    return fold(term) in fold(value)


def _flag_criteria(value: bool | None, true_key: str, false_key: str, negate_true: bool = False) -> list[str]:
    """SEARCH-nyckel för ett tristate-filter: True, False eller None (inget villkor).

    negate_true: nyckeln för True är false_key (unseen=True → UNSEEN).
    """
    if value is None:
        return []
    if not isinstance(value, bool):
        raise ValueError(f"Filtret måste vara true, false eller null (fick {value!r})")
    if negate_true:
        value = not value
    return [true_key if value else false_key]


def _validate_size(value: object, field: str) -> int:
    """Storleksgräns i byte för LARGER/SMALLER: ett heltal >= 0 (inte bool)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} måste vara ett heltal >= 0 (byte), fick {value!r}")
    return value


# Högst så många skiftlägesvarianter av ett sökord med åäö skickas till Bridge
_CASE_VARIANTS = 4


def _quoted_printable(text: str) -> str:
    """Tecken utanför ASCII som Bridge skriver dem i quoted-printable: ö → =C3=B6."""
    return "".join(
        ch if ch.isascii() else "".join(f"={b:02X}" for b in ch.encode("utf-8"))
        for ch in text
    )


def _text_search_criteria(key: str, value: str, field: str) -> list[str]:
    """SEARCH-kriterier för TEXT eller BODY som hittar sökordet även med åäö.

    Bridge (Gluon) jämför sökordet med det okodade meddelandet. Textdelarna
    skriver Bridge som quoted-printable i UTF-8 (verifierat live: 150 av 150
    textdelar), så "behöver" står som "beh=C3=B6ver" och hittas aldrig som det
    skrivs. Ett ord utan åäö skickas som det är. Ett ord med åäö skickas både
    som det är (8-bitars text) och quoted-printable-kodat, i några
    skiftlägesvarianter: Bridge viker bara ASCII, och "Ö" (=C3=96) är andra
    byte än "ö" (=C3=B6). Varianterna kombineras med OR.

    Begränsningar (dokumenteras i verktyget): ett ord som quoted-printable
    radbryter mitt i ("beh=\r\növer") hittas inte, inte heller ord i
    base64-kodade delar eller HTML-entiteter (&ouml;).
    """
    _imap_quoted(value, field)  # kontrolltecken avvisas innan något byggs
    term = unicodedata.normalize("NFC", value)
    if term.isascii():
        return [key, _imap_quoted(term, field)]
    forms: list[str] = []
    for variant in (term, term.lower(), term.capitalize(), term.upper())[:_CASE_VARIANTS]:
        for form in (variant, _quoted_printable(variant)):
            # Bridge viker ASCII själv: varianter som bara skiljer i ASCII är dubbletter
            if not any(_ascii_fold(form) == _ascii_fold(f) for f in forms):
                forms.append(form)
    keys = [[key, _imap_quoted(form, field)] for form in forms]
    criteria: list[str] = []
    for i, pair in enumerate(keys):
        if i < len(keys) - 1:
            criteria.append("OR")
        criteria += pair
    return criteria


def _ascii_fold(text: str) -> str:
    return "".join(ch.lower() if ch.isascii() else ch for ch in text)


# Content-Disposition i BODYSTRUCTURE: ("attachment" ("filename" "x.pdf")) eller ("attachment" NIL).
# Kräver "(" före, så ett filnamn eller en parameter som heter "attachment" inte räknas.
_ATTACHMENT_DISPOSITION_RE = re.compile(r'\(\s*"attachment"\s+(?:\(|NIL\b)', re.IGNORECASE)
_QUOTED_STRING_RE = re.compile(r'"(?:[^"\\]|\\.)*"')


def _attachment_uids(lines: list) -> set[str]:
    """UID:n i ett FETCH (UID BODYSTRUCTURE)-svar som har en del med disposition attachment.

    Ett FETCH-svar kan delas upp av literaler (t.ex. ett filnamn med
    specialtecken); literalens innehåll ersätts med en tom sträng så att
    strukturen runt den behålls.
    """
    responses: list[str] = []
    for line in lines:
        if isinstance(line, bytearray):
            if responses:
                responses[-1] += '""'
            continue
        text = _decode(line).strip()
        if _FETCH_START_RE.match(text):
            responses.append("")
        elif not responses:
            continue
        responses[-1] += _LITERAL_END_RE.sub("", text)
    found = set()
    for response in responses:
        uid = _UID_RE.search(_QUOTED_STRING_RE.sub('""', response))
        if uid and _ATTACHMENT_DISPOSITION_RE.search(response):
            found.add(uid.group(1))
    return found


def _filter_messages(msgs: list[dict[str, Any]], terms: list[tuple[str, str]]) -> list[dict[str, Any]]:
    return [m for m in msgs if all(_header_matches(m[field], term) for field, term in terms)]


def _search_result(
    msgs: list[dict[str, Any]], total: int, max_results: int,
    complete: bool, note: str | None = None, more: bool = False,
) -> dict[str, Any]:
    return {
        "messages": msgs[:max_results],
        "total": total,
        "has_more": total > max_results or more,
        "complete": complete,
        "note": note,
    }


def _sort_newest_first(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sorterar på UID fallande. UID speglar ankomstordning i mappen, till
    skillnad från Date-headern som avsändaren själv sätter."""
    return sorted(msgs, key=lambda m: int(m["uid"]) if m["uid"].isdigit() else -1, reverse=True)


def _decode_header_bytes(raw: bytes) -> str:
    """Headerblock som text: UTF-8 om det går, annars latin-1 (förlorar inget)."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


# Avsändarstyrd text i listningar: adressrader rensas utom adresserna själva,
# fritext helt. Identifierare visas bara som ASCII-<msg-id> (clean_msg_ids).
# Övriga rubriker (Content-Type …) lämnas.
_METADATA_ADDRESS_HEADERS = frozenset({"from", "to", "cc", "bcc", "reply-to", "sender"})
_METADATA_TEXT_HEADERS = frozenset({"subject", "date"})
_METADATA_ID_HEADERS = frozenset({"message-id", "in-reply-to", "references"})


def _metadata_value(hdrs, name: str, clean: FieldCleaner | None = None) -> str:
    """Ett rubrikvärde för listningen, rensat efter sorts rubrik. Det som
    rensas (och osynliga tecken som lämnas i adresser) räknas i clean."""
    clean = clean if clean is not None else FieldCleaner()
    if name in _METADATA_ID_HEADERS:
        return clean.add(name, clean_msg_ids(hdrs.get(name)))
    value = decode_header_value(hdrs.get(name))
    if name in _METADATA_ADDRESS_HEADERS:
        return clean.address_text(name, value)
    if name in _METADATA_TEXT_HEADERS:
        return clean.text(name, value)
    return value


def _parse_fetch_metadata(lines: list, extra_headers: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """Parsar FETCH-svar med UID, FLAGS och header-fält.

    extra_headers är fler headers (gemener) som tas med avkodade, med "-"
    utbytt mot "_" i nyckeln: ("cc", "in-reply-to") → "cc", "in_reply_to".

    Bara svar som har header-literalen tas med: en oombedd flaggändring från
    en annan klient (`* 1 FETCH (UID 999 FLAGS (\\Seen))`) hamnar i samma svar
    men är inget brev. Samma UID tas bara med en gång. UID och FLAGS kan stå
    både före och efter literalen — Bridge lägger UID efter vid UID FETCH.
    """
    messages = []
    seen_uids: set[str] = set()
    i = 0
    while i < len(lines):
        head = lines[i]
        i += 1
        if isinstance(head, bytearray):
            continue
        head_s = _decode(head).strip()
        if not _FETCH_START_RE.match(head_s) or not _LITERAL_END_RE.search(head_s):
            continue

        headers_raw = b""
        if i < len(lines) and isinstance(lines[i], bytearray):
            headers_raw = bytes(lines[i])
            i += 1
        # Resten av svaret efter literalen, t.ex. ")" eller " UID 11319)"
        tail_s = ""
        if i < len(lines) and not isinstance(lines[i], bytearray):
            candidate = _decode(lines[i]).strip()
            if not _FETCH_START_RE.match(candidate):
                tail_s = candidate
                i += 1

        meta = f"{_LITERAL_END_RE.sub('', head_s)} {tail_s}"
        uid_m = _UID_RE.search(meta)
        if not uid_m or uid_m.group(1) in seen_uids:
            continue
        uid = uid_m.group(1)
        seen_uids.add(uid)
        flags_m = _FLAGS_RE.search(meta)

        # email-parsern hanterar vikta rader; decode_header_value avkodar
        # RFC 2047-ord (åäö, emoji) och slår ihop radbrytningar. Rå 8-bitars
        # headers (RFC 6532) avkodas först, annars blir "ö" till "��"
        hdrs = HeaderParser().parsestr(_decode_header_bytes(headers_raw))

        clean = FieldCleaner()
        meta = {
            "uid": uid,
            "flags": flags_m.group(1).split() if flags_m else [],
            "subject": _metadata_value(hdrs, "subject", clean),
            "from": _metadata_value(hdrs, "from", clean),
            "to": _metadata_value(hdrs, "to", clean),
            "date": (date := _metadata_value(hdrs, "date", clean)),
            "date_iso": iso_date(date),  # jämförbart och sorterbart för klienten
            "message_id": _metadata_value(hdrs, "message-id", clean),
        }
        for name in extra_headers:
            meta[name.replace("-", "_")] = _metadata_value(hdrs, name, clean)
        # Varning (fasta texter, som i get_email) bara när det finns något:
        # taggtecken/bidi som rensats, osynliga tecken i en adress eller ett
        # injektionsmönster i ämnet eller ett visningsnamn
        # Ämne och adressrader (visningsnamnen) läses av injektionsheuristiken
        texts = [meta["subject"], meta["from"], meta["to"], meta.get("cc") or ""]
        if reasons := suspicious_reasons(texts, clean):
            meta["suspicious_content"] = reasons
        messages.append(meta)
    return messages


def _require_ok(resp, what: str) -> None:
    if resp.result != "OK":
        raise ValueError(f"{what} misslyckades ({resp.result}): {_response_text(resp)}")


def _mailbox_key(name: str) -> str:
    return name.casefold()


def _is_label_view(name: str) -> bool:
    """Etiketter (Labels/…) och Starred: ett mejl där ligger också i någon mapp."""
    key = _mailbox_key(name)
    return key == "starred" or key.startswith("labels/")


# Mappar där Bridge raderar permanent när ett mejl som redan finns där läggs dit igen
_READD_DELETES = ("trash", "drafts")
# Nekar Bridge en permanent radering med Code=2501 kan mejlet ändå försvinna när
# Bridge hämtar nästa händelse från Proton (EventPeriod 20 s + jitter 20 s i
# Bridges internal/user/user.go). Så länge väntar raderingen, med en kontroll
# per intervall.
_GONE_TIMEOUT = 45.0
_GONE_INTERVAL = 3.0


def _check_move(source: str, target: str) -> None:
    """Avvisar flyttar som Bridge nekar eller som kan radera mejlet permanent."""
    src, dst = _mailbox_key(source), _mailbox_key(target)
    if src == dst:
        raise ValueError(
            f"Källa och mål är samma mapp ({source}). Bridge tar då först bort mejlet ur "
            "mappen och lägger sedan tillbaka det — i Trash och Drafts raderas det permanent."
        )
    if "all mail" in (src, dst):
        raise ValueError(
            "Bridge tillåter inte flytt till eller från All Mail. Ange mappen där mejlet "
            "ligger (t.ex. INBOX, Archive eller Folders/…) som källa."
        )
    if {src, dst} == {"inbox", "sent"}:
        raise ValueError("Bridge tillåter inte flytt mellan INBOX och Sent.")
    if dst == "starred":
        # Bridge räknar Starred som systemetikett: UID MOVE dit tar bort mejlet
        # ur källmappen, så det bara ligger kvar i Starred och All Mail
        raise ValueError(
            f"Starred är inget mål för flytt: Bridge tar då bort mejlet ur {source}. "
            "Stjärnmärkning görs med flaggan \\Flagged, inte genom att flytta mejlet — "
            "använd flag_email (och unflag_email för att ta bort stjärnan)."
        )
    if dst in _READD_DELETES and _is_label_view(source):
        raise ValueError(
            f"Flytt till {target} från {source} avvisas: mejlet kan redan ligga i {target}, "
            "och då raderar Bridge det permanent. Ange mappen där mejlet ligger "
            "(t.ex. INBOX, Archive eller Folders/…) som källa."
        )


# Ett "/" som inte är escapat med backslash skiljer nivåer åt i namnet
_LEVEL_SEP_RE = re.compile(r'(?<!\\)/')


def _check_user_folder(name: str) -> str:
    """Kontrollerar att namnet är en egen mapp eller etikett hos Proton.

    Returnerar "mapp" för Folders/… och "etikett" för Labels/…. Bridge nekar
    CREATE/RENAME/DELETE för allt annat, utan förklaring.
    """
    for prefix, kind in (("Folders/", "mapp"), ("Labels/", "etikett")):
        if name.startswith(prefix) and name[len(prefix):]:
            if kind == "etikett" and _LEVEL_SEP_RE.search(name[len(prefix):]):
                raise ValueError(
                    f"Underetiketter finns inte i Proton: {name!r}. Etiketter ligger bara "
                    "direkt under Labels/ (t.ex. Labels/Kunder). Använd Folders/… för en "
                    "mapphierarki."
                )
            return kind
    raise ValueError(
        f"Ogiltigt namn {name!r}: egna mappar måste börja med 'Folders/' (t.ex. "
        "'Folders/Projekt') och etiketter med 'Labels/' (t.ex. 'Labels/Kvitton'). "
        "Systemmapparna (INBOX, Trash, All Mail …) kan inte skapas, döpas om eller tas bort."
    )


def _parent_hint(name: str) -> str:
    """Förklaring när en undermapp inte kunde skapas: föräldern måste finnas."""
    parts = _LEVEL_SEP_RE.split(name)
    if name.startswith("Folders/") and len(parts) > 2:
        return f" (för en undermapp måste {'/'.join(parts[:-1])} redan finnas)"
    return ""


def _status_items(resp) -> dict[str, int]:
    """Statuslistan ur ett STATUS-svar, t.ex. {"messages": 5, "unseen": 1}.

    Bara den avslutande parentesen på raden tolkas, aldrig mappnamnet före
    den, och aldrig en literal (bytearray), som också bara kan vara namnet.
    """
    items: dict[str, int] = {}
    for line in resp.lines:
        if isinstance(line, bytearray):
            continue
        m = _STATUS_ITEMS_RE.search(_decode(line))
        if not m:
            continue
        tokens = m.group(1).split()
        for key, value in zip(tokens[::2], tokens[1::2]):
            if value.isascii() and value.isdigit():
                items.setdefault(key.lower(), int(value))
    return items


def _status_messages(resp) -> int | None:
    """MESSAGES ur ett STATUS-svar, eller None om det saknas."""
    return _status_items(resp).get("messages")
