import logging
import re
from typing import Any

import aioimaplib

from .config import Settings

logger = logging.getLogger(__name__)

# Format: (\Flags) "delimiter" "name"  eller  (\Flags) "delimiter" name
_LIST_RE = re.compile(r'\([^)]*\)\s+"[^"]*"\s+(.+)$')
_UID_RE = re.compile(r'\bUID\s+(\d+)\b')
_FLAGS_RE = re.compile(r'FLAGS\s+\(([^)]*)\)')
_STATUS_NUM_RE = re.compile(r'(\w+)\s+(\d+)')
_VALID_UID_RE = re.compile(r'^\d+$')


def _validate_uid(uid: str) -> str:
    """Kastar ValueError om uid inte är ett rent heltal (förhindrar IMAP sequence-set-injektion)."""
    if not _VALID_UID_RE.match(uid):
        raise ValueError(f"Ogiltigt UID: {uid!r} — måste vara ett heltal")
    return uid


def _escape_imap_string(value: str) -> str:
    """Escapar dubbla citattecken och backslash i ett IMAP quoted-string (RFC 3501)."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _decode(b: bytes | bytearray | str) -> str:
    if isinstance(b, (bytes, bytearray)):
        return b.decode("utf-8", errors="replace")
    return b


def _parse_seqnums(resp) -> list[str]:
    if resp.result != "OK" or not resp.lines:
        return []
    line = _decode(resp.lines[0]).strip()
    return [n for n in line.split() if n.isdigit()]


def _parse_list_line(line: str) -> str | None:
    """Extraherar mappnamn ur en IMAP LIST-rad. Returnerar None om raden inte matchar."""
    line = line.strip()
    if not line or line.startswith("command"):
        return None
    m = _LIST_RE.match(line)
    if not m:
        return None
    return m.group(1).strip().strip('"')


def _parse_headers(raw: bytes) -> dict[str, str]:
    """Parsar raw RFC822-headers till en dict med lowercase-nycklar."""
    result: dict[str, str] = {}
    for hline in raw.decode("utf-8", errors="replace").splitlines():
        if ": " in hline:
            k, _, v = hline.partition(": ")
            result[k.lower()] = v.strip()
    return result


class IMAPClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: aioimaplib.IMAP4 | None = None

    async def connect(self) -> None:
        logger.info("Ansluter till IMAP %s:%d (plain)", self._settings.imap_host, self._settings.imap_port)
        # Bridge v3 på port 1143 kräver plain IMAP (inte direkt SSL/TLS)
        self._client = aioimaplib.IMAP4(
            host=self._settings.imap_host,
            port=self._settings.imap_port,
        )
        await self._client.wait_hello_from_server()
        await self._client.login(self._settings.username, self._settings.password)
        logger.info("IMAP inloggad som %s", self._settings.username)

    async def disconnect(self) -> None:
        if self._client:
            try:
                await self._client.logout()
            except Exception:
                pass
            self._client = None

    async def _ensure_connected(self) -> None:
        if self._client is None:
            await self.connect()

    async def list_mailboxes(self) -> list[dict[str, str]]:
        await self._ensure_connected()
        resp = await self._client.list('""', "*")
        mailboxes = []
        if resp.result == "OK":
            for line in resp.lines:
                name = _parse_list_line(_decode(line))
                if name:
                    mailboxes.append({"name": name})
        return mailboxes

    async def list_messages(
        self, mailbox: str, limit: int = 20, offset: int = 0
    ) -> list[dict[str, Any]]:
        await self._ensure_connected()
        select_resp = await self._client.select(mailbox)

        # Hämta EXISTS-räknaren från SELECT-svaret
        exists = 0
        for line in select_resp.lines:
            text = _decode(line)
            m = re.search(r'(\d+)\s+EXISTS', text)
            if m:
                exists = int(m.group(1))
                break

        if exists == 0:
            return []

        # Beräkna sekvensintervall för nyaste-först paginering
        stop = max(1, exists - offset)
        start = max(1, stop - limit + 1)

        fetch_resp = await self._client.fetch(
            f"{start}:{stop}",
            "(UID FLAGS BODY[HEADER.FIELDS (DATE FROM TO SUBJECT MESSAGE-ID)])",
        )
        if fetch_resp.result != "OK":
            return []

        msgs = _parse_fetch_metadata(fetch_resp.lines)
        msgs.reverse()  # nyaste överst
        return msgs

    async def get_message(self, mailbox: str, uid: str) -> bytes | None:
        await self._ensure_connected()
        await self._client.select(mailbox)
        resp = await self._client.uid("fetch", _validate_uid(uid), "BODY[]")
        if resp.result != "OK":
            return None
        # lines[0] = "SEQNUM FETCH (BODY[] {SIZE}"
        # lines[1] = bytearray with raw email
        for line in resp.lines:
            if isinstance(line, bytearray) and len(line) > 10:
                return bytes(line)
        return None

    async def search_messages(
        self,
        mailbox: str,
        from_addr: str | None = None,
        subject: str | None = None,
        since: str | None = None,
        before: str | None = None,
        unseen: bool | None = None,
        max_results: int = 200,
    ) -> list[str]:
        await self._ensure_connected()
        await self._client.select(mailbox)

        criteria: list[str] = []
        if from_addr:
            criteria += ["FROM", f'"{_escape_imap_string(from_addr)}"']
        if subject:
            criteria += ["SUBJECT", f'"{_escape_imap_string(subject)}"']
        if since:
            criteria += ["SINCE", since]
        if before:
            criteria += ["BEFORE", before]
        if unseen is True:
            criteria.append("UNSEEN")
        elif unseen is False:
            criteria.append("SEEN")
        if not criteria:
            criteria = ["ALL"]

        # search() returnerar sekvensnummer — vi hämtar sedan deras UID:n
        resp = await self._client.search(" ".join(criteria))
        seqnums = _parse_seqnums(resp)
        if not seqnums:
            return []

        # Begränsa till nyaste max_results för att undvika rekursionsproblem
        seqnums = seqnums[-max_results:]

        fetch_resp = await self._client.fetch(",".join(seqnums), "(UID)")
        if fetch_resp.result != "OK":
            return []

        uids = []
        for line in fetch_resp.lines:
            m = _UID_RE.search(_decode(line))
            if m:
                uids.append(m.group(1))
        uids.reverse()  # nyaste först
        return uids

    async def set_flags(self, mailbox: str, uid: str, flags: str, add: bool) -> bool:
        await self._ensure_connected()
        await self._client.select(mailbox)
        action = "+FLAGS" if add else "-FLAGS"
        resp = await self._client.uid("store", _validate_uid(uid), action, flags)
        return resp.result == "OK"

    async def move_message(self, mailbox: str, uid: str, target: str) -> bool:
        await self._ensure_connected()
        await self._client.select(mailbox)
        safe_uid = _validate_uid(uid)
        copy_resp = await self._client.uid("copy", safe_uid, target)
        if copy_resp.result != "OK":
            return False
        await self._client.uid("store", safe_uid, "+FLAGS", r"(\Deleted)")
        await self._client.expunge()
        return True

    async def delete_message(self, mailbox: str, uid: str) -> bool:
        await self._ensure_connected()
        await self._client.select(mailbox)
        safe_uid = _validate_uid(uid)
        await self._client.uid("store", safe_uid, "+FLAGS", r"(\Deleted)")
        await self._client.expunge()
        return True

    async def get_mailbox_status(self, mailbox: str) -> dict[str, int]:
        await self._ensure_connected()
        resp = await self._client.status(mailbox, "(MESSAGES UNSEEN)")
        status: dict[str, int] = {}
        if resp.result == "OK":
            for line in resp.lines:
                text = _decode(line)
                for m in _STATUS_NUM_RE.finditer(text):
                    key = m.group(1).lower()
                    if key in ("messages", "unseen"):
                        status[key] = int(m.group(2))
        return status


def _parse_fetch_metadata(lines: list) -> list[dict[str, Any]]:
    """Parsar FETCH-svar med UID, FLAGS och header-fält."""
    messages = []
    i = 0
    while i < len(lines):
        line_s = _decode(lines[i]).strip()

        if "FETCH" in line_s and "UID" in line_s:
            uid_m = _UID_RE.search(line_s)
            flags_m = _FLAGS_RE.search(line_s)
            uid = uid_m.group(1) if uid_m else ""
            flags = flags_m.group(1).split() if flags_m else []

            # Nästa rad kan vara header-data (bytearray)
            headers_raw = b""
            if i + 1 < len(lines) and isinstance(lines[i + 1], bytearray):
                headers_raw = bytes(lines[i + 1])
                i += 1

            header_dict = _parse_headers(headers_raw)

            messages.append({
                "uid": uid,
                "flags": flags,
                "subject": header_dict.get("subject", ""),
                "from": header_dict.get("from", ""),
                "to": header_dict.get("to", ""),
                "date": header_dict.get("date", ""),
                "message_id": header_dict.get("message-id", ""),
            })
        i += 1
    return messages
