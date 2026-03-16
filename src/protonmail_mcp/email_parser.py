import email
import email.header
import email.utils
import re
from email.message import Message
from typing import Any


def decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    parts = email.header.decode_header(value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return "".join(decoded)


def extract_addresses(header_value: str | None) -> list[str]:
    if not header_value:
        return []
    addresses = email.utils.getaddresses([header_value])
    return [f"{name} <{addr}>" if name else addr for name, addr in addresses]


def _safe_decode(payload: bytes, charset: str) -> str:
    """Dekodar payload med angiven charset, faller tillbaka till utf-8 vid okänd encoding."""
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


def get_body(msg: Message) -> dict[str, str]:
    plain = ""
    html = ""

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition") or "")
            if "attachment" in disposition:
                continue
            charset = part.get_content_charset() or "utf-8"
            if content_type == "text/plain" and not plain:
                payload = part.get_payload(decode=True)
                if payload:
                    plain = _safe_decode(payload, charset)
            elif content_type == "text/html" and not html:
                payload = part.get_payload(decode=True)
                if payload:
                    html = _safe_decode(payload, charset)
    else:
        charset = msg.get_content_charset() or "utf-8"
        payload = msg.get_payload(decode=True)
        if payload:
            content_type = msg.get_content_type()
            if content_type == "text/html":
                html = _safe_decode(payload, charset)
            else:
                plain = _safe_decode(payload, charset)

    return {"plain": plain, "html": html}


def get_attachments(msg: Message) -> list[dict[str, Any]]:
    attachments = []
    if msg.is_multipart():
        for part in msg.walk():
            disposition = str(part.get("Content-Disposition") or "")
            if "attachment" in disposition:
                filename = decode_header_value(part.get_filename())
                attachments.append(
                    {
                        "filename": filename,
                        "content_type": part.get_content_type(),
                        "size": len(part.get_payload(decode=True) or b""),
                    }
                )
    return attachments


def _strip_html(html: str) -> str:
    """Ta bort HTML-taggar och returnera ren text."""
    return re.sub(r"<[^>]+>", "", html).strip()


def _strip_quotes_and_signature(text: str) -> str:
    """Ta bort citat (rader som börjar med >) och signaturblock (efter '-- ')."""
    lines = text.splitlines()
    result = []
    for line in lines:
        # Signaturblock: allt efter "-- " eller "--" på egen rad
        if line.rstrip() == "-- " or line.rstrip() == "--":
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


def parse_email(
    raw: bytes,
    body_format: str = "text",
    max_length: int | None = 500,
) -> dict[str, Any]:
    msg = email.message_from_bytes(raw)
    body = get_body(msg)

    body_plain = body["plain"]
    body_html = body["html"]

    if body_format == "full":
        # Returnera råa body utan trunkering
        pass
    elif body_format == "text":
        # Om ingen plain text, strippa HTML-taggar som fallback
        if not body_plain and body_html:
            body_plain = _strip_html(body_html)
        body_plain = _truncate(body_plain, max_length)
    elif body_format == "stripped":
        if not body_plain and body_html:
            body_plain = _strip_html(body_html)
        body_plain = _strip_quotes_and_signature(body_plain)
        body_plain = _truncate(body_plain, max_length)

    return {
        "message_id": msg.get("Message-ID", ""),
        "subject": decode_header_value(msg.get("Subject")),
        "from": decode_header_value(msg.get("From")),
        "to": extract_addresses(msg.get("To")),
        "cc": extract_addresses(msg.get("Cc")),
        "bcc": extract_addresses(msg.get("Bcc")),
        "reply_to": extract_addresses(msg.get("Reply-To")),
        "date": msg.get("Date", ""),
        "body_plain": body_plain,
        "body_html": body_html,
        "attachments": get_attachments(msg),
        "headers": dict(msg.items()),
    }


def parse_envelope(envelope_data: str) -> dict[str, str]:
    """Parsar en IMAP ENVELOPE-sträng till ett enkelt dict med metadata."""
    # Returnerar rådata — full parsning görs av aioimaplib
    return {"raw": envelope_data}
