"""Mappnamn i modified UTF-7 (RFC 3501 §5.1.3).

IMAP skickar mappnamn som 7-bitars ASCII. Utskrivbar ASCII (0x20–0x7E)
står för sig själv, utom "&" som skrivs "&-". Allt annat skrivs som
UTF-16BE i base64 mellan "&" och "-", med "," i stället för "/" och utan
"="-utfyllnad. "Näs" blir alltså "N&AOQ-s".
"""
import base64
import re

from .i18n import _

_B64_RE = re.compile(r"[A-Za-z0-9+,]+")


def _is_printable_ascii(ch: str) -> bool:
    return 0x20 <= ord(ch) <= 0x7E


def _b64_run(text: str) -> str:
    try:
        raw = text.encode("utf-16-be")
    except UnicodeEncodeError as e:
        raise ValueError(_("Ogiltigt tecken (ensam surrogat) i mappnamn: {text!r}", text=text)) from e
    return "&" + base64.b64encode(raw).decode("ascii").rstrip("=").replace("/", ",") + "-"


def encode_modified_utf7(name: str) -> str:
    """Kodar ett läsbart mappnamn till modified UTF-7."""
    out: list[str] = []
    run: list[str] = []
    for ch in name:
        if _is_printable_ascii(ch):
            if run:
                out.append(_b64_run("".join(run)))
                run.clear()
            out.append("&-" if ch == "&" else ch)
        else:
            run.append(ch)
    if run:
        out.append(_b64_run("".join(run)))
    return "".join(out)


def decode_modified_utf7(wire: str) -> str:
    """Avkodar ett mappnamn i modified UTF-7.

    Strikt: kastar ValueError om namnet inte är kanonisk modified UTF-7,
    så att encode_modified_utf7(decode_modified_utf7(x)) == x alltid gäller.
    """
    out: list[str] = []
    i = 0
    while i < len(wire):
        ch = wire[i]
        if not _is_printable_ascii(ch):
            raise ValueError(_("Inte modified UTF-7 (tecken utanför ASCII): {wire!r}", wire=wire))
        if ch != "&":
            out.append(ch)
            i += 1
            continue
        end = wire.find("-", i + 1)
        if end == -1:
            raise ValueError(_("Inte modified UTF-7 (saknar avslutande '-'): {wire!r}", wire=wire))
        chunk = wire[i + 1:end]
        if not chunk:
            out.append("&")
        else:
            if not _B64_RE.fullmatch(chunk):
                raise ValueError(_("Inte modified UTF-7 (ogiltig base64): {wire!r}", wire=wire))
            padded = chunk.replace(",", "/") + "=" * (-len(chunk) % 4)
            try:
                out.append(base64.b64decode(padded, validate=True).decode("utf-16-be"))
            except ValueError as e:  # binascii.Error och UnicodeDecodeError
                raise ValueError(_("Inte modified UTF-7 ({e}): {wire!r}", e=e, wire=wire)) from e
        i = end + 1
    name = "".join(out)
    # Fångar allt som avkodas men inte är kanoniskt: base64-kodad utskrivbar
    # ASCII, intilliggande sekvenser och utfyllnadsbitar som inte är noll
    if encode_modified_utf7(name) != wire:
        raise ValueError(_("Inte kanonisk modified UTF-7: {wire!r}", wire=wire))
    return name


# Special-use-attribut (RFC 6154). Bridge skickar dem i LIST trots att
# SPECIAL-USE inte annonseras.
_SPECIAL_USE = {a.lower(): a for a in ("\\All", "\\Archive", "\\Drafts", "\\Flagged", "\\Junk", "\\Sent", "\\Trash")}
# \NonExistent (RFC 5258) innebär \Noselect
_NOSELECT = {"\\noselect", "\\nonexistent"}


def describe_mailbox(name: str, attributes: list[str]) -> dict:
    """Beskriver en mapp utifrån namnet och LIST-attributen.

    type: "container" för \\Noselect (Folders, Labels och pseudo-föräldrar till
    etiketter med "/" i namnet), "label" för Labels/*, "folder" för Folders/*
    och "system" för Protons övriga mappar (INBOX, Drafts, Sent, Starred,
    Archive, Spam, Trash, All Mail).
    """
    attrs = {a.lower() for a in attributes}
    selectable = not (attrs & _NOSELECT)
    special_use = next((_SPECIAL_USE[a] for a in sorted(attrs) if a in _SPECIAL_USE), None)
    if not selectable:
        mb_type = "container"
    elif name.startswith("Labels/"):
        mb_type = "label"
    elif name.startswith("Folders/"):
        mb_type = "folder"
    else:
        mb_type = "system"
    return {"name": name, "type": mb_type, "selectable": selectable, "special_use": special_use}
