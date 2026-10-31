import binascii
import codecs
import email
import email.header
import email.quoprimime
import email.utils
import html
import re
from datetime import timezone
from email.message import Message
from typing import Any

from .i18n import _
from .html_text import HtmlText, html_to_text, html_to_text_report
from .sanitize import (
    UNTRUSTED_CONTENT_NOTICE,
    Sanitized,
    clean_text,
    injection_signals,
    sanitize_address,
    sanitize_address_text,
    sanitize_text,
)

# Etiketter som är vanliga i post men saknas i Python eller oftast är fel
_CHARSET_ALIASES = {
    "us-ascii": "utf-8",  # 8-bitars data märkt us-ascii är nästan alltid utf-8
    "ascii": "utf-8",
    "gb2312": "gb18030",  # gb2312 ⊂ gbk ⊂ gb18030; felmärkt GBK är vanligt
    "gbk": "gb18030",
    "x-gbk": "gb18030",
    "x-mac-roman": "mac_roman",
    "macintosh": "mac_roman",
    "ks_c_5601-1987": "cp949",
}

# Codecs som Python räknar som text men som omtolkar innehållet i stället för
# att avkoda en teckenkodning (unicode_escape tolkar \x41, punycode/idna är
# domännamnsformat)
_REJECTED_CODECS = {"unicode-escape", "raw-unicode-escape", "punycode", "idna", "undefined"}
_BOM_CODECS = {"utf-16", "utf-32"}


def _lookup_charset(charset: str | None) -> str | None:
    """Returnera ett användbart codec-namn för en MIME-charset, eller None."""
    if not charset:
        return None
    name = str(charset).strip().strip('"').lower()
    name = name.split("*", 1)[0]  # RFC 2231 §5: språktagg, "utf-8*sv"
    name = _CHARSET_ALIASES.get(name, name)
    try:
        info = codecs.lookup(name)
    except (LookupError, ValueError, TypeError):
        return None
    if not getattr(info, "_is_text_encoding", True) or info.name in _REJECTED_CODECS:
        return None
    return info.name


def _decode_bytes(data: bytes, charset: str | None) -> str:
    """Avkoda bytes med deklarerad charset utan att någonsin kasta.

    Ordning: deklarerad charset (strikt) → utf-8 (strikt) → cp1252 (strikt) →
    deklarerad charset med ersättningstecken → latin-1, som alltid lyckas.
    """
    codec = _lookup_charset(charset)
    if codec in _BOM_CODECS and not data.startswith(
        (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE, codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)
    ):
        codec = None  # utan BOM gissar Python byteordning och ger mojibake
    for candidate in (codec, "utf-8", "cp1252"):
        if candidate is None:
            continue
        try:
            return data.decode(candidate)
        except (UnicodeError, LookupError, ValueError):
            continue
    if codec is not None:
        try:
            return data.decode(codec, errors="replace")
        except (UnicodeError, LookupError, ValueError):
            pass
    return data.decode("latin-1")


# RFC 2047 encoded-word. encoded-text får inte innehålla '?' (§4), så texten
# slutar vid första '?' — annars letar varje oavslutat "=?" efter "?=" ända
# till slutet av rubriken och avkodningen blir kvadratisk (S1). Ett okodat '?'
# sist i ett Q-ord ("du??=") tillåts ändå, som i email.header.
_ENCODED_WORD_RE = re.compile(
    r"=\?(?P<charset>[^?]*)\?(?P<encoding>[qQbB])\?(?P<text>[^?]*\??)\?="
)


def _decode_encoded_word(encoding: str, text: str) -> bytes | None:
    try:
        if encoding.lower() == "q":
            return email.quoprimime.header_decode(text).encode("latin-1")
        padded = text + "=" * (-len(text) % 4)
        return binascii.a2b_base64(padded.encode("ascii"))
    except (binascii.Error, UnicodeError, ValueError):
        return None


def _decode_rfc2047(text: str) -> str:
    """Avkoda RFC 2047-ord. Blanktecken mellan två kodade ord tas bort, och
    intilliggande ord med samma charset avkodas tillsammans så att ett tecken
    som delats mellan två ord blir helt."""
    out: list[str] = []
    pending = b""
    pending_charset = ""
    prev_encoded = False
    pos = 0

    def flush() -> None:
        nonlocal pending
        if pending:
            out.append(_decode_bytes(pending, pending_charset))
        pending = b""

    for m in _ENCODED_WORD_RE.finditer(text):
        between = text[pos : m.start()]
        pos = m.end()
        decoded = _decode_encoded_word(m.group("encoding"), m.group("text"))
        if decoded is None:  # trasigt kodat ord — behåll det som text
            flush()
            out.append(between + m.group(0))
            prev_encoded = False
            continue
        charset = m.group("charset")
        if not (prev_encoded and not between.strip()):
            flush()
            out.append(between)
        elif charset.lower() != pending_charset.lower():
            flush()
        if not pending:
            pending_charset = charset
        pending += decoded
        prev_encoded = True
    flush()
    out.append(text[pos:])
    return "".join(out)


def _header_to_text(value) -> str:
    """Gör om ett rått rubrikvärde till str utan att tappa 8-bitars tecken.

    Både compat32-parsern och IMAP-metadata lämnar rå UTF-8 (RFC 6532) som
    surrogateescape-str eller som Header-objekt med unknown-8bit; str() på
    dem ger U+FFFD. Bytesen avkodas i stället som utf-8 → cp1252 → latin-1.
    """
    if isinstance(value, email.header.Header):
        try:
            return "".join(
                part if isinstance(part, str)
                else _decode_bytes(part, None if charset in (None, "unknown-8bit") else charset)
                for part, charset in email.header.decode_header(value)
            )
        except Exception:
            return str(value)
    text = str(value)
    if any("\udc80" <= ch <= "\udcff" for ch in text):
        text = _decode_bytes(text.encode("utf-8", "surrogateescape"), None)
    return text


def decode_header_value(value) -> str:
    if not value:
        return ""
    value = _header_to_text(value)
    # Avkodade ord kan dölja radbrytningar (CR/LF men även \v, \f, \x1c-\x1e,
    # \x85, U+2028/2029 som str.splitlines() räknar) — slå ihop till en rad så
    # värdet inte kan injicera headers
    text = _decode_rfc2047(value)
    lines = text.splitlines()
    if lines == [text] or not lines:
        return text
    # Linjär tid (ingen regex med överlappande \s*, som gav ReDoS); trimma
    # varje rad så att en vikt rad "a\r\n b" blir "a b" och inte "a  b"
    return " ".join(stripped for line in lines if (stripped := line.strip()))


# Tecken som kräver citering av visningsnamnet (RFC 5322 specials, som formataddr)
_ADDR_SPECIALS_RE = re.compile(r'[][\\()<>@,:;".]')
_ADDR_ESCAPES_RE = re.compile(r'[\\"]')
# Det som str.splitlines() räknar som radbrytning
_LINE_BREAKS = frozenset("\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029")


def _strip_address_whitespace(addr: str) -> str:
    """Ta bort vikning och blanktecken ur adressdelen, utom blanksteg och tabb
    inuti en citerad lokaldel ("a b"@x.se), där de hör till adressen (F5).
    Radbrytningar tas alltid bort, så adressen kan inte injicera rubriker."""
    out: list[str] = []
    quoted = escaped = False
    for ch in addr:
        if ch in _LINE_BREAKS:
            continue
        if escaped:
            escaped = False
        elif quoted and ch == "\\":
            escaped = True
        elif ch == '"':
            quoted = not quoted
        elif ch.isspace() and not quoted:
            continue
        out.append(ch)
    return "".join(out)


def parse_addresses(header_value) -> list[tuple[str, str]]:
    """Parsa en adressrubrik till (namn, adress)-par med avkodade namn.

    Adresslistan delas upp FÖRE RFC 2047-avkodningen, så att ett kodat
    kommatecken eller "<…>" i namnet inte kan skapa nya adresser. Bara
    namnet avkodas: kodade ord är inte tillåtna i adressdelen (RFC 2047 §5),
    och att avkoda dem där skulle låta avsändaren tillverka en annan adress.
    """
    if not header_value:
        return []
    text = _header_to_text(header_value)
    pairs = []
    for name, addr in email.utils.getaddresses([text]):
        name, addr = decode_header_value(name), _strip_address_whitespace(addr)
        if addr:  # ett namn utan adress går inte att skicka till
            pairs.append((name, addr))
    return pairs


def _parses_to(formatted: str, addr: str) -> bool:
    """Ger strängen exakt en adress, och är det addr?"""
    try:
        found = [a for _, a in email.utils.getaddresses([formatted]) if a]
    except Exception:
        return False
    return found == [addr]


def format_address(name: str, addr: str) -> str:
    """Som email.utils.formataddr men utan att RFC 2047-koda icke-ASCII-namn:
    namn med specialtecken citeras så att strängen kan parsas tillbaka.

    Ingen tolkningsskillnad: resultatet parsas alltid till exakt addr (som
    reply_to_email skickar till). Går det inte med namnet blir det bara
    adressen, och går inte ens den att parsa tillbaka blir det "".
    """
    candidates = [addr]
    if name:
        if _ADDR_SPECIALS_RE.search(name):
            name = '"%s"' % _ADDR_ESCAPES_RE.sub(r"\\\g<0>", name)
        candidates.insert(0, f"{name} <{addr}>")
    for candidate in candidates:
        if addr and _parses_to(candidate, addr):
            return candidate
    return ""


def _format_shown(name: str, raw: str, shown: str) -> str:
    """format_address för den råa adressen, med adressdelen visad som shown
    (råadressen med osynliga tecken som [U+XXXX], se sanitize_address)."""
    formatted = format_address(name, raw)
    if shown == raw or not formatted:
        return formatted
    if formatted == raw:
        return shown
    return formatted[: -len(raw) - 2] + f"<{shown}>"


def extract_addresses(header_value: str | None) -> list[str]:
    formatted = (format_address(name, addr) for name, addr in parse_addresses(header_value))
    return [a for a in formatted if a]


def _safe_decode(payload: bytes, charset: str | None) -> str:
    """Dekodar payload med angiven charset utan att kasta (se _decode_bytes)."""
    return _decode_bytes(payload, charset)


_MAX_MIME_DEPTH = 100


def _filename(part: Message) -> str:
    """Bilagans filnamn, avkodat men orensat (se _clean_filename)."""
    try:
        return decode_header_value(part.get_filename())
    except Exception:
        return ""


def _clean_filename(filename: str) -> Sanitized:
    """Filnamnet styrs av avsändaren: osynliga tecken och bidi-styrtecken
    (faktura<RLO>fdp.exe som visas som "fakturaexe.pdf") tas bort."""
    return sanitize_text(filename)


def _disposition(part: Message) -> str | None:
    try:
        return part.get_content_disposition()  # gemener: "attachment"/"inline"
    except Exception:
        return None


def _attachment_bytes(part: Message) -> bytes:
    """Bilagans innehåll. Ett bifogat mejl (message/rfc822) blir hela mejlet."""
    if part.is_multipart():
        # message/rfc822: get_payload(decode=True) är None för multipart
        try:
            return b"".join(p.as_bytes() for p in part.get_payload() if isinstance(p, Message))
        except Exception:
            return b""
    return part.get_payload(decode=True) or b""


def _attachment(part: Message, filename: str) -> dict[str, Any]:
    size = len(_attachment_bytes(part))
    return {"filename": filename, "content_type": part.get_content_type(), "size": size}


def _text_of(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    try:
        charset = part.get_content_charset()
    except Exception:
        charset = None
    return _safe_decode(payload, charset)


def _collect(part: Message, out: dict[str, list], depth: int = 0, section: str = "") -> None:
    """Samla brödtext och bilagor för ett meddelande.

    Går inte ner i message/* (vidarebefordrade mejl är bilagor, inte en del
    av ytterbrevet). I multipart/alternative tas en variant per typ; i övriga
    multipart (mixed, related, signed …) slås alla inline-textdelar ihop.

    Bilagor samlas som (del, filnamn, delnummer), där delnumret är IMAP:s
    sektionsnummer (RFC 3501 §6.4.5, t.ex. "1.2") — stabilt för mejlet.
    """
    if depth > _MAX_MIME_DEPTH:
        return
    ctype = part.get_content_type()
    maintype = part.get_content_maintype()
    here = section or "1"  # ett meddelande utan multipart har bara del 1

    if maintype == "message" and part.is_multipart():
        out["attachments"].append((part, _filename(part), here))
        return

    if part.is_multipart():
        children = [c for c in part.get_payload() if isinstance(c, Message)]
        numbered = [
            (child, f"{section}.{n}" if section else str(n))
            for n, child in enumerate(children, 1)
        ]
        if part.get_content_subtype() != "alternative":
            for child, child_section in numbered:
                _collect(child, out, depth + 1, child_section)
            return
        for child, child_section in numbered:
            sub: dict[str, list] = {"plain": [], "html": [], "attachments": []}
            _collect(child, sub, depth + 1, child_section)
            for kind in ("plain", "html"):
                if not out[kind] and any(sub[kind]):
                    out[kind].extend(sub[kind])
            out["attachments"].extend(sub["attachments"])
        return

    disposition = _disposition(part)
    filename = _filename(part)
    is_text_body = (
        ctype in ("text/plain", "text/html") or maintype == "multipart"  # trasig multipart utan boundary
    ) and disposition != "attachment" and (not filename or disposition == "inline")
    if is_text_body:
        if text := _text_of(part):
            out["html" if ctype == "text/html" else "plain"].append(text)
    elif disposition == "attachment" or filename:
        out["attachments"].append((part, filename, here))
    elif disposition == "inline" or part.get("Content-ID"):
        pass  # inbäddad resurs utan filnamn, t.ex. cid-logga i multipart/related
    elif maintype == "text" and ctype != "text/calendar":
        pass  # annan textvariant i alternative, t.ex. text/x-amp-html
    else:
        out["attachments"].append((part, filename, here))


def _parts(msg: Message) -> dict[str, list]:
    """Brödtextdelar och bilagor. out["filename_removed"] är antal tecken
    som rensats ur bilagornas filnamn (ett heltal i en lista)."""
    out: dict[str, list] = {"plain": [], "html": [], "attachments": []}
    _collect(msg, out)
    attachments = []
    removed = 0
    for index, (part, filename, section) in enumerate(out["attachments"]):
        cleaned = _clean_filename(filename)
        removed += cleaned.count
        attachments.append({"index": index, "part": section, **_attachment(part, cleaned.text)})
    out["attachments"] = attachments
    out["filename_removed"] = [removed]
    return out


def get_attachment_part(raw: bytes, index: int) -> dict[str, Any]:
    """Plocka ut bilaga nummer ``index`` (0-baserat) ur ett rått mejl.

    Samma MIME-genomgång som bilagelistan i parse_email, så indexet pekar på
    exakt den bilaga som listas där. Returnerar index, part, filename,
    content_type, size, charset (eller None) och data (bilagans bytes).
    """
    msg = email.message_from_bytes(raw)
    out: dict[str, list] = {"plain": [], "html": [], "attachments": []}
    _collect(msg, out)
    found = out["attachments"]
    if not 0 <= index < len(found):
        if not found:
            raise ValueError(_("Mejlet har inga bilagor"))
        count = _("1 bilaga") if len(found) == 1 else _("{n} bilagor", n=len(found))
        raise ValueError(
            _("Ingen bilaga med index {index} — mejlet har {count} (index 0–{last})",
              index=index, count=count, last=len(found) - 1)
        )
    part, filename, section = found[index]
    data = _attachment_bytes(part)
    try:
        charset = part.get_content_charset()
    except Exception:
        charset = None
    return {
        "index": index,
        "part": section,
        "filename": _clean_filename(filename).text,
        "content_type": part.get_content_type(),
        "size": len(data),
        "charset": charset,
        "data": data,
    }


def get_body(msg: Message) -> dict[str, str]:
    parts = _parts(msg)
    return {"plain": "\n".join(parts["plain"]), "html": "\n".join(parts["html"])}


def get_attachments(msg: Message) -> list[dict[str, Any]]:
    return _parts(msg)["attachments"]


def _strip_html(html: str) -> str:
    """Gör om HTML till läsbar text (se html_text.html_to_text)."""
    return html_to_text(html)


def _strip_quotes_and_signature(text: str) -> str:
    """Ta bort citat (rader som börjar med >) och signaturblock (efter '-- ')."""
    lines = text.splitlines()
    result = []
    for line in lines:
        # Signaturavgränsaren är exakt "-- " (RFC 3676 §4.3); ett ensamt "--"
        # är ofta en avskiljare mitt i texten och får inte kapa resten
        if line == "-- ":
            break
        # Hoppa över citerade rader
        if line.startswith(">"):
            continue
        result.append(line)
    return "\n".join(result)


def _truncate(text: str, max_length: int | None) -> str:
    """Trunkera text till max_length tecken med hint om kvarvarande."""
    if max_length is None or len(text) <= max_length:
        return text
    remaining = len(text) - max_length
    return text[:max_length] + f"\n[...{remaining} characters remaining]"


_BODY_FORMATS = ("text", "full", "stripped")

# Vedertagen stavning för rubriker som anropare slår upp i headers-dicten
_CANONICAL_HEADERS = {
    name.lower(): name
    for name in (
        "From", "To", "Cc", "Bcc", "Reply-To", "Sender", "Subject", "Date",
        "Message-ID", "In-Reply-To", "References", "Return-Path", "Received",
        "MIME-Version", "Content-Type", "Content-Transfer-Encoding",
        "Content-Disposition", "Content-ID", "List-Unsubscribe", "List-Id",
    )
}


# Rubriker med adresser: bara visningsnamnen rensas (sanitize_address_text)
_ADDRESS_HEADERS = frozenset(
    {"from", "to", "cc", "bcc", "reply-to", "sender", "return-path", "delivered-to",
     "resent-from", "resent-to", "resent-cc", "resent-bcc", "resent-sender"}
)
# Identifierare som andra verktyg slår upp med (trådar, svar): visas bara
# som ASCII-<msg-id> (se clean_msg_ids)
_IDENTIFIER_HEADERS = frozenset({"message-id", "in-reply-to", "references", "resent-message-id"})
# msg-id: "<" … ">" utan blanktecken; ASCII och högst en rubrikrads längd
_MSG_ID_TOKEN_RE = re.compile(r"<[^<>\s]+>")
_MAX_MSG_ID_LEN = 998
_MSG_ID_UNSAFE_RE = re.compile(r"[\x00-\x1f\x7f]")


def clean_msg_ids(value) -> Sanitized:
    """Message-ID, In-Reply-To, References: bara de ASCII-<msg-id> som finns
    i rubriken, åtskilda med ett blanksteg.

    RFC 2047-ord avkodas aldrig (kodade ord är inte tillåtna i msg-id, RFC
    2047 §5) — ett kodat ord står kvar som synlig ASCII. Id med 8-bitars
    tecken, kontrolltecken eller orimlig längd, och all text mellan id:n,
    tas inte med. removed räknar de osynliga tecken som rubriken hade
    visat avkodad (taggtecken i ett kodat ord eller i rå UTF-8), så att de
    flaggas i suspicious_content. Linjär tid.
    """
    if not value:
        return Sanitized("")
    text = _header_to_text(value)
    ids = [
        msg_id for msg_id in _MSG_ID_TOKEN_RE.findall(text)
        if msg_id.isascii() and len(msg_id) <= _MAX_MSG_ID_LEN and not _MSG_ID_UNSAFE_RE.search(msg_id)
    ]
    return Sanitized(" ".join(ids), sanitize_text(decode_header_value(value)).removed)


def _clean_header(lower: str, value: str) -> Sanitized:
    """Rensa ett avkodat rubrikvärde efter vilken sorts rubrik det är.
    Identifierare tas om hand av _headers_dict (före avkodningen)."""
    if lower in _ADDRESS_HEADERS:
        return sanitize_address_text(value)
    return sanitize_text(value)


def _headers_dict(msg: Message, counts: dict[str, int] | None = None) -> dict[str, str]:
    """Rubriker som str. Nycklar är skiftlägesokänsliga i e-post (RFC 5322):
    välkända rubriker får vedertagen stavning, övriga stavningen från första
    förekomsten, och vid dubbletter gäller första förekomsten — samma värde
    som message_id/references m.fl. bygger på.

    Värdena rensas från osynliga tecken (se _clean_header); antal borttagna
    tecken läggs i counts["headers"]."""
    headers: dict[str, str] = {}
    seen: set[str] = set()
    removed = 0
    for key, value in msg.raw_items():
        lower = key.lower()
        if lower in seen:
            continue
        seen.add(lower)
        if lower in _IDENTIFIER_HEADERS:
            cleaned = clean_msg_ids(value)
        else:
            cleaned = _clean_header(lower, decode_header_value(value))
        removed += cleaned.count
        headers[_CANONICAL_HEADERS.get(lower, key)] = cleaned.text
    if counts is not None and removed:
        counts["headers"] = counts.get("headers", 0) + removed
    return headers


def _hidden_total(result: Sanitized) -> int:
    return result.count + result.kept_in_address


def clean_raw_headers(headers: dict[str, str]) -> "tuple[dict[str, str], FieldCleaner]":
    """Rensa RÅA rubrikvärden (get_email_headers) utan att avkoda dem i onödan.

    - Message-ID, In-Reply-To, References: bara ASCII-<msg-id> (clean_msg_ids).
    - Adressrubriker: visningsnamn rensas, osynliga tecken i adressen visas
      som [U+XXXX] (sanitize_address_text); övriga rubriker rensas som text.
    - RFC 2047-ord lämnas okodade, UTOM när värdet avkodat skulle innehålla
      osynliga tecken: då visas det avkodade och rensade värdet, så att inga
      dolda tecken kan gömmas i ett kodat ord som klienten avkodar.

    Returnerar (värden, FieldCleaner) där cleaner räknar per rubriknamn.
    """
    clean = FieldCleaner()
    values: dict[str, str] = {}
    for name, raw in headers.items():
        lower = name.lower()
        if lower in _IDENTIFIER_HEADERS:
            values[name] = clean.add(name, clean_msg_ids(raw))
            continue
        cleaner = sanitize_address_text if lower in _ADDRESS_HEADERS else sanitize_text
        as_raw = cleaner(raw)
        if "=?" in raw:
            decoded = cleaner(decode_header_value(raw))
            if _hidden_total(decoded) > _hidden_total(as_raw):
                as_raw = decoded
        values[name] = clean.add(name, as_raw).strip()
    return values, clean


def _raw_header(msg: Message, name: str) -> str | None:
    """Första förekomsten av en rubrik, rå (före compat32:s Header-omvandling)."""
    wanted = name.lower()
    for key, value in msg.raw_items():
        if key.lower() == wanted:
            return value
    return None


class FieldCleaner:
    """Rensar avsändarstyrda fält och räknar borttagna tecken per fält."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.categories: dict[str, int] = {}
        self.kept_in_address = 0

    def add(self, key: str, result: Sanitized) -> str:
        if result.count:
            self.counts[key] = self.counts.get(key, 0) + result.count
            for category, n in result.removed.items():
                self.categories[category] = self.categories.get(category, 0) + n
        self.kept_in_address += result.kept_in_address
        return result.text

    def text(self, key: str, value: str) -> str:
        return self.add(key, sanitize_text(value))

    def address_text(self, key: str, value: str) -> str:
        return self.add(key, sanitize_address_text(value))

    def pair(self, key: str, name: str, addr: str) -> tuple[str, str]:
        return self.text(key, name), self.add(key, sanitize_address(addr))


def suspicious_reasons(
    texts: list[str], clean: FieldCleaner, html_report: HtmlText | None = None
) -> list[str]:
    """Orsaker till att mejlet ser ut att innehålla promptinjektion.

    texts är redan rensad text (ämne, brödtext …), clean det som rensats i
    mejlets fält. Fasta texter och antal — inget av avsändarens text följer
    med. Mejlet blockeras inte; listan är en varning till klienten.
    Används av get_email och för varje rad i listningarna.
    """
    if html_report is None:
        html_report = HtmlText("")
    reasons: list[str] = []
    visible = injection_signals("\n".join(texts + [html_report.text, html_report.white_text]))
    reasons += [_("misstänkt instruktion till modellen: {label}", label=_(label)) for label in visible]
    hidden = injection_signals(html_report.hidden_text)
    reasons += [
        _("misstänkt instruktion till modellen i gömd HTML-text: {label}", label=_(label))
        for label in hidden if label not in visible
    ]
    if html_report.unsafe:
        reasons.append(
            _("HTML:en kunde inte tolkas säkert: det som inte gick att tolka är utelämnat ur texten")
        )
    # Gömd och vit HTML-text i sig flaggas inte: i en mätning på 300 riktiga
    # mejl hade 228 gömda element (förhandstext, mobilversioner) och 30 vit
    # text, utan något injektionsmönster. Den räknas i hidden_html i stället.
    if n := clean.categories.get("tag"):
        reasons.append(_("osynliga Unicode-taggtecken borttagna: {n} (kan bära dolda instruktioner)", n=n))
    if n := clean.categories.get("bidi"):
        reasons.append(_("bidi-styrtecken borttagna: {n} (kan vända visningsordningen för läsaren)", n=n))
    if clean.kept_in_address:
        reasons.append(
            _("osynliga tecken i en adress: {kept_in_address} (visas som [U+XXXX]; "
            "svara inte till adressen)",
            kept_in_address=clean.kept_in_address)
        )
    return reasons


# Längre än så är inget datum (RFC 5322 date-time är under 40 tecken, med
# kommentar som "(CEST)" något mer); längre text tolkas inte alls
_MAX_DATE_LEN = 200


# Året i den vanliga formen "[Tue, ]14 Oct 26 …" eller "14-Oct-26": två eller tre siffror
_DATE_YEAR_RE = re.compile(r"^(\s*(?:[A-Za-z]+,?\s*)?\d{1,2}[\s-]+[A-Za-z]+[\s-]+)(\d{2,3})(?=\s)")
# Skottsekund (23:59:60), som datetime inte kan representera
_LEAP_SECOND_RE = re.compile(r"(\d{1,2}:\d{2}):60(?!\d)")
# Zon med bara timmar ("+02") eller med kolon ("+02:00"), sist eller före en kommentar
_SHORT_ZONE_RE = re.compile(r"(\s[+-]\d{2})(?::(\d{2}))?(\s*(?:\([^()]*\))?\s*)$")
_UTC_UNKNOWN_LOCAL_RE = re.compile(r"\s-0000(?:\s*\([^()]*\))?\s*$")


def _two_digit_year(m: re.Match[str]) -> str:
    year = int(m.group(2))
    if len(m.group(2)) == 2:
        year += 2000 if year < 50 else 1900  # RFC 5322 §4.3
    else:
        year += 1900  # tresiffrigt år: år − 1900 (RFC 5322 §4.3)
    return f"{m.group(1)}{year}"


def iso_date(value: str | None) -> str | None:
    """RFC 5322-datum som ISO 8601 med tidszon ("2026-10-14T21:45:52+02:00").

    Avsändarens egen tidszon behålls. -0000 är UTC (RFC 5322 §3.3). Saknas
    zonen, eller är den en okänd bokstavszon (t.ex. CEST, som RFC 5322 §4.3
    säger ska behandlas som okänd), blir det lokal tid UTAN offset
    ("2026-10-14T21:45:52") i stället för en påhittad zon. Tvåsiffriga år
    00–49 blir 20xx och 50–99 19xx, tresiffriga år + 1900 (RFC 5322 §4.3).
    "+02" och "+02:00" läses som +0200, och en skottsekund (23:59:60) som
    23:59:59. None om datumet inte går att tolka.
    """
    if not value or len(value) > _MAX_DATE_LEN:
        return None
    try:
        text = _DATE_YEAR_RE.sub(_two_digit_year, value.strip(), count=1)
        text = _LEAP_SECOND_RE.sub(r"\1:59", text, count=1)
        text = _SHORT_ZONE_RE.sub(lambda m: f"{m.group(1)}{m.group(2) or '00'}{m.group(3)}", text, count=1)
        when = email.utils.parsedate_to_datetime(text)
        if when.tzinfo is None and _UTC_UNKNOWN_LOCAL_RE.search(text):
            when = when.replace(tzinfo=timezone.utc)
        return when.isoformat()
    except Exception:  # ValueError, TypeError, IndexError, OverflowError …
        return None


def _hidden_html(report: HtmlText) -> dict[str, Any]:
    """Gömt innehåll i HTML-delen (antal, aldrig text). Tom dict om inget."""
    out: dict[str, Any] = {}
    if report.hidden_elements:
        out["elements"] = report.hidden_elements
        out["reasons"] = dict(sorted(report.hidden_reasons.items()))
        out["hidden_chars"] = len(report.hidden_text)
    if report.white_text:
        out["white_text_chars"] = len(report.white_text)
    return out


def parse_email(
    raw: bytes,
    body_format: str = "text",
    max_length: int | None = 500,
) -> dict[str, Any]:
    if body_format not in _BODY_FORMATS:
        raise ValueError(
            _("Okänt body_format {body_format!r} — giltiga värden: {formats}",
              body_format=body_format, formats=", ".join(_BODY_FORMATS))
        )
    if max_length is not None and max_length < 0:
        raise ValueError(_("max_length måste vara 0 eller större (eller None), fick {max_length}",
                   max_length=max_length))
    msg = email.message_from_bytes(raw)
    parts = _parts(msg)
    clean = FieldCleaner()
    if parts["filename_removed"][0]:
        clean.counts["attachments"] = parts["filename_removed"][0]

    # Hela texten rensas före formatering och trunkering. HTML-texten rensas
    # efter html_to_text, eftersom entiteter (&#8203; &zwnj;) kan avkodas
    # till osynliga tecken.
    body_plain = clean.text("body_plain", "\n".join(parts["plain"]))
    body_html = clean.text("body_html", "\n".join(parts["html"]))
    # HTML-texten (utan gömda element) behövs även när det finns en textdel:
    # injektionsvarningen tittar på både synlig och gömd HTML-text
    html_report = html_to_text_report(body_html) if body_html else HtmlText("")
    full_plain = body_plain

    if body_format == "full":
        # Returnera råa body utan trunkering
        pass
    elif body_format == "text":
        # Saknas text (eller är den bara blanktecken, vanligt i utskick): använd HTML
        if not body_plain.strip() and body_html:
            body_plain = clean.text("body_plain", html_report.text)
        body_plain = _truncate(body_plain, max_length)
    elif body_format == "stripped":
        if not body_plain.strip() and body_html:
            body_plain = clean.text("body_plain", html_report.text)
        body_plain = _strip_quotes_and_signature(body_plain)
        body_plain = _truncate(body_plain, max_length)

    has_html = bool(body_html)
    html_reasons: list[str] = []
    if body_format == "full":
        # Rå HTML följer med; gömt innehåll i den flaggas, eftersom det når
        # modellen här (men inte i body_plain)
        if html_report.hidden_elements:
            html_reasons.append(
                _("gömd HTML-text i body_html: {hidden_elements} element "
                "(syns inte för läsaren men finns i den råa HTML:en)",
                hidden_elements=html_report.hidden_elements)
            )
        if n := sanitize_text(html.unescape(body_html)).removed.get("tag"):
            html_reasons.append(
                _("osynliga Unicode-taggtecken som HTML-entiteter i body_html: {n} "
                "(kan bära dolda instruktioner)",
                n=n)
            )
    else:
        # I "text" och "stripped" returneras ingen HTML: dess synliga text
        # finns redan i body_plain (när textdelen saknas), och det är i rå
        # HTML som gömd text finns. Rå HTML ges av body_format="full".
        body_html = None

    # Visningsnamnen rensas. Ur adresserna tas inget bort: osynliga tecken
    # visas som [U+XXXX] och flaggas, så att visningen avslöjar exakt vad
    # som står i rubriken (och skick vägrar sådana adresser)
    addresses = {
        key: [clean.pair(key, n, a) for n, a in parse_addresses(_raw_header(msg, name))]
        for key, name in (
            ("from", "From"), ("to", "To"), ("cc", "Cc"), ("bcc", "Bcc"), ("reply_to", "Reply-To")
        )
    }
    raw_addresses = {
        key: [a for _, a in parse_addresses(_raw_header(msg, name))]
        for key, name in (
            ("from", "From"), ("to", "To"), ("cc", "Cc"), ("bcc", "Bcc"), ("reply_to", "Reply-To")
        )
    }
    formatted = {
        key: [
            f for f in (_format_shown(n, raw, shown) for (n, shown), raw in zip(pairs, raw_addresses[key]))
            if f
        ]
        for key, pairs in addresses.items()
    }
    # Utan parsbar adress (bara ett namn, trasig rubrik): visa den avkodade texten
    from_ = ", ".join(formatted["from"])
    if not any("@" in addr for _, addr in addresses["from"]):
        # Bara ett namn: visa avkodad text — men aldrig om den avkodade texten
        # i sig kan tolkas som adresser (kodat "a@x.se, evil@y.se")
        decoded = decode_header_value(_raw_header(msg, "From"))
        if not any("@" in a for _, a in email.utils.getaddresses([decoded])):
            # Ingen adress att svara till: allt rensas som text, även ord med @
            from_ = clean.text("from", decoded)

    subject = clean.text("subject", decode_header_value(_raw_header(msg, "Subject")))
    result = {
        "message_id": clean.add("message_id", clean_msg_ids(_raw_header(msg, "Message-ID"))),
        "subject": subject,
        "from": from_,
        "to": formatted["to"],
        "cc": formatted["cc"],
        "bcc": formatted["bcc"],
        "reply_to": formatted["reply_to"],
        # (namn, adress)-par, för anropare som vill ha adressen utan att parsa om
        "addresses": {key: [[n, a] for n, a in pairs] for key, pairs in addresses.items()},
        "date": (date := clean_text(decode_header_value(_raw_header(msg, "Date")))),
        "date_iso": iso_date(date),
        "body_plain": body_plain,
        "body_html": body_html,
        "has_html": has_html,
        "attachments": parts["attachments"],
        "in_reply_to": clean.add("in_reply_to", clean_msg_ids(_raw_header(msg, "In-Reply-To"))),
        "references": clean.add("references", clean_msg_ids(_raw_header(msg, "References"))),
        "headers": _headers_dict(msg, clean.counts),
    }
    # Märkning för klienten: innehållet ovan är avsändarens, och vad som
    # rensats bort (antal tecken per fält, bara fält där något togs bort)
    result["untrusted_content_notice"] = UNTRUSTED_CONTENT_NOTICE
    result["sanitized"] = dict(clean.counts)
    result["suspicious_content"] = (
        suspicious_reasons(
            # Visningsnamn och bilagenamn styrs också av avsändaren
            [subject, full_plain]
            + [name for pairs in addresses.values() for name, _ in pairs if name]
            + [from_]
            + [a["filename"] for a in parts["attachments"] if a.get("filename")],
            clean,
            html_report,
        )
        + html_reasons
    )
    # Gömda HTML-element (finns kvar i body_html men inte i body_plain) och
    # vit text utan bakgrundsfärg, som antal
    result["hidden_html"] = _hidden_html(html_report)
    return result


def parse_envelope(envelope_data: str) -> dict[str, str]:
    """Parsar en IMAP ENVELOPE-sträng till ett enkelt dict med metadata."""
    # Returnerar rådata — full parsning görs av aioimaplib
    return {"raw": envelope_data}
