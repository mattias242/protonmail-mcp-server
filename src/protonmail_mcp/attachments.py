"""Bilagekatalogen: all filåtkomst för bilagor stannar i en katalog.

Filnamn i mejl styrs av avsändaren och sökvägar till filer som ska skickas
kommer från klienten (en AI-modell). Båda behandlas som opålitliga:

- Avsändarens filnamn saneras till ett enda rent filnamn (ingen sökväg,
  inga kontroll- eller riktningstecken) innan det används på disk, och en
  befintlig fil skrivs aldrig över — en krock ger ett suffix, " (1)".
- En sökväg att skicka löses upp (symlänkar följs) och måste hamna inuti
  bilagekatalogen; path traversal, absoluta sökvägar utanför och symlänkar
  som pekar ut avvisas, liksom namn med kontrolltecken.
- Själva katalogen måste vara en riktig katalog som du äger och som andra
  inte kan skriva i, och sökvägen dit får inte gå via någon symlänk (utom
  systemets egna, som /var och /tmp på macOS: ägda av root, i en katalog som
  bara root eller du kan ändra, och bara när servern inte själv kör som
  root). Annars räcker det att en uppackad zip lägger symlänken
  "protonmail-mcp -> .." i ~/Downloads för att hela hemkatalogen ska räknas
  som "inuti". Katalogen skapas därför redan när servern startar.
- En fil som ska skickas öppnas med O_NOFOLLOW och jämförs (fstat: enhet,
  inod, storlek, mtime och ctime) med den fil som kontrollerades, så att den
  inte kan bytas ut däremellan — inte heller på filsystem som återanvänder
  inodnummer. Hårda länkar, andra filtyper än vanliga filer och filer som ser
  ut att innehålla hemligheter (nycklar, .env, .netrc, nyckelringar) skickas
  aldrig.
- En sparad fil får com.apple.quarantine på macOS, som filer från Mail och
  Safari, så att Gatekeeper och Terminal varnar innan den körs.
"""
from __future__ import annotations

import email.policy
import logging
import mimetypes
import os
import re
import stat
import sys
import time
import unicodedata
from collections.abc import Sequence
from email.message import EmailMessage
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, NamedTuple, Optional

from pydantic import Field

from .i18n import _

if TYPE_CHECKING:
    from .config import Settings

logger = logging.getLogger(__name__)

# Gränser för bilagor som skickas (send_email, reply_to_email, forward_email).
# Proton tar emot högst 25 MB bilagor per mejl.
MAX_SEND_FILES = 20
MAX_SEND_BYTES = 25 * 1024 * 1024

# Parametertyp för verktygen: filnamn/sökvägar inom bilagekatalogen
AttachmentPaths = Optional[
    Annotated[list[Annotated[str, Field(min_length=1)]], Field(max_length=MAX_SEND_FILES)]
]

# Längsta filnamn i byte (UTF-8). De flesta filsystem tillåter 255; marginalen
# räcker till kollisionssuffixet " (999)".
_MAX_NAME_BYTES = 200
_MAX_EXTENSION_CHARS = 16
_MAX_COLLISIONS = 1000
_FALLBACK_STEM = "bilaga"
# Tecken som Windows och många synkverktyg inte tål i filnamn
_RESERVED_CHARS_RE = re.compile(r'[<>:"|?*]')
# Enhetsnamn som Windows tolkar som enheter oavsett filändelse
_WINDOWS_DEVICES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)


def _is_unsafe_char(ch: str) -> bool:
    """Kontrolltecken (Cc), format-/riktningstecken som U+202E (Cf),
    radavgränsare (Zl, Zp) samt surrogat, privata och otilldelade tecken."""
    return unicodedata.category(ch) in {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}


def _extension_for(content_type: str | None) -> str:
    if content_type:
        ext = mimetypes.guess_extension(content_type.lower(), strict=False)
        if ext:
            return ext
    return ".bin"


def _truncate_utf8(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")[:max_bytes]
    return encoded.decode("utf-8", errors="ignore")


def _strip_dots_and_space(text: str) -> str:
    r"""Ta bort inledande punkter (dolda filer, "..") och avslutande punkter,
    även blandat med blanktecken som " . .dold". Linjär tid — ett regex som
    [\s.]+$ blir kvadratiskt på långa serier, och namnet styrs av avsändaren."""
    start, end = 0, len(text)
    while start < end and (text[start] == "." or text[start].isspace()):
        start += 1
    while end > start and (text[end - 1] == "." or text[end - 1].isspace()):
        end -= 1
    return text[start:end]


def sanitize_filename(name: str | None, content_type: str | None = None) -> str:
    """Gör ett avsändarstyrt filnamn till ett säkert filnamn utan sökväg.

    Behåller bara sista ledet efter / eller \\, tar bort osäkra tecken,
    inledande punkter (inga dolda filer, inget "..") och avslutande punkter
    och blanksteg. Blir inget kvar används "bilaga" med en filändelse
    gissad ur content_type.
    """
    text = unicodedata.normalize("NFC", name or "")
    text = "".join(ch for ch in text if not _is_unsafe_char(ch))
    text = re.split(r"[/\\]", text)[-1]
    text = _RESERVED_CHARS_RE.sub("_", text)
    text = _strip_dots_and_space(text)
    if not text:
        return _(_FALLBACK_STEM) + _extension_for(content_type)
    stem, dot, ext = text.rpartition(".")
    if not dot or not stem or len(ext) > _MAX_EXTENSION_CHARS:
        stem, ext = text, ""
    if stem.split(".")[0].upper() in _WINDOWS_DEVICES:
        stem = "_" + stem
    suffix = f".{ext}" if ext else ""
    stem = _truncate_utf8(stem, _MAX_NAME_BYTES - len(suffix.encode("utf-8"))).rstrip(". ")
    return (stem or _(_FALLBACK_STEM)) + suffix


def _numbered(name: str, n: int) -> str:
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        return f"{name} ({n})"
    return f"{stem} ({n}).{ext}"


# Skydd på djupet: filer som ser ut att innehålla hemligheter skickas inte
# ens om de ligger i bilagekatalogen (t.ex. om PROTONMAIL_ATTACHMENT_DIR
# pekar på hemkatalogen). Jämförs utan hänsyn till skiftläge, per sökvägsled.
_SENSITIVE_NAMES = frozenset({".ssh", ".gnupg", ".aws", ".netrc", ".env"})
_SENSITIVE_EXTENSIONS = frozenset({".pem", ".key"})
_PRIVATE_KEY_RE = re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----")

_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)


def _current_uid() -> int | None:
    getuid = getattr(os, "getuid", None)
    return getuid() if getuid else None


def _is_sensitive_part(part: str) -> bool:
    """Ett sökvägsled som ser ut att vara eller rymma hemligheter.

    id_* räknas bara utan filändelse eller med .pub (id_rsa, id_ed25519,
    id_ecdsa_sk.pub) — id_kort.pdf är en vanlig fil.
    """
    low = part.lower()
    if low in _SENSITIVE_NAMES or low.startswith(".env.") or "keychain" in low:
        return True
    suffix = Path(low).suffix
    if low.startswith("id_") and suffix in ("", ".pub"):
        return True
    return suffix in _SENSITIVE_EXTENSIONS


def _refuse_secret(name: str, why: str) -> ValueError:
    return ValueError(
        _("{name!r} ser ut att innehålla hemligheter ({why}) och skickas inte som "
        "bilaga. Vill du verkligen skicka den: gör det själv från Proton.",
        name=name, why=why)
    )


_QUARANTINE_ATTR = b"com.apple.quarantine"


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _quarantine_value(now: float | None = None) -> bytes:
    """Värdet som Safari och Mail skriver: flaggor;tid (hex);program;(uuid)."""
    stamp = int(time.time() if now is None else now)
    return f"0083;{stamp:x};protonmail-mcp;".encode("ascii")


def _fsetxattr(fd: int, name: bytes, value: bytes) -> None:
    """fsetxattr(2) på macOS via ctypes — os.setxattr finns inte där."""
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    func = libc.fsetxattr  # AttributeError om symbolen saknas
    func.argtypes = [
        ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p,
        ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int,
    ]
    func.restype = ctypes.c_int
    if func(fd, name, value, len(value), 0, 0) != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))


def _mark_quarantined(fd: int, path: Path) -> None:
    """Sätt com.apple.quarantine på en nysparad fil (bara macOS).

    Ett fel stoppar inte sparningen; det loggas som varning.
    """
    if not _is_macos():
        return
    try:
        _fsetxattr(fd, _QUARANTINE_ATTR, _quarantine_value())
    except (OSError, AttributeError) as exc:
        logger.warning("Kunde inte sätta karantänmärkning på %s: %s", path, exc)


def _is_system_symlink(link: Path, st: os.stat_result) -> bool:
    """En symlänk som systemet äger, som /var och /tmp på macOS.

    Den ska ägas av root och ligga i en katalog som bara root eller du kan
    ändra (ingen annan kan då byta ut den). Körs processen själv som root
    godtas ingen symlänk: då ägs alla symlänkar den, eller en zip den packar
    upp, skapar av root, och ägaren skiljer inte systemets från andras.
    """
    uid = _current_uid()
    if st.st_uid != 0 or uid == 0:
        return False
    try:
        parent = os.lstat(link.parent)
    except OSError:
        return False
    return parent.st_uid in (0, uid) and not parent.st_mode & 0o022


class _CheckedFile(NamedTuple):
    path: Path  # upplöst, utan symlänkar
    stat: os.stat_result  # lstat vid kontrollen
    relative: Path  # relativt bilagekatalogen


class AttachmentStore:
    """Läser och skriver bilagor, bara inuti ``root``."""

    def __init__(self, root: Path | str, max_inline_bytes: int = 1024 * 1024) -> None:
        self.root = Path(root).expanduser()
        self.max_inline_bytes = max_inline_bytes

    @classmethod
    def from_settings(cls, settings: Settings) -> AttachmentStore:
        return cls(settings.attachment_dir, max_inline_bytes=settings.attachment_max_inline_bytes)

    def _check_root_stat(self, st: os.stat_result) -> None:
        """Roten måste vara en riktig katalog som du äger och som bara du kan
        skriva i. Används både på lstat (före) och fstat (efter öppning)."""
        if stat.S_ISLNK(st.st_mode):
            raise ValueError(
                _("Bilagekatalogen {root} är en symlänk och används inte (en "
                "uppackad zip i Downloads kan lägga dit en sådan). Ta bort länken, "
                "eller sätt PROTONMAIL_ATTACHMENT_DIR till en riktig katalog.",
                root=self.root)
            )
        if not stat.S_ISDIR(st.st_mode):
            raise ValueError(_("Bilagekatalogen {root} är ingen katalog", root=self.root))
        uid = _current_uid()
        if uid is not None and st.st_uid != uid:
            raise ValueError(_("Bilagekatalogen {root} ägs inte av dig och används inte", root=self.root))
        if st.st_mode & 0o022:
            raise ValueError(
                _("Bilagekatalogen {root} är skrivbar för andra "
                "(rättigheter {mode:o}) och används inte. "
                "Kör: chmod 700 {root}",
                root=self.root, mode=stat.S_IMODE(st.st_mode))
            )

    def _checked_root(self, create: bool = False) -> Path:
        """Kontrollera bilagekatalogen (skapa den med 0700 om ``create``) och
        returnera den upplöst.

        Vägrar en relativ sökväg, en sökväg via en symlänk som inte är
        systemets egen (_is_system_symlink; /var och /tmp på macOS är
        tillåtna), en rot som själv är en symlänk, som någon annan äger eller
        som andra kan skriva i.
        """
        root = self.root
        if not root.is_absolute():
            raise ValueError(
                _("Bilagekatalogen {root} är en relativ sökväg — ange en absolut "
                "sökväg i PROTONMAIL_ATTACHMENT_DIR",
                root=root)
            )
        for parent in reversed(root.parents):  # "/" först
            try:
                st = os.lstat(parent)
            except FileNotFoundError:
                break  # resten saknas och skapas nedan (eller ger "finns inte")
            except OSError as exc:
                raise ValueError(
                    _("Bilagekatalogen {root} kan inte kontrolleras: {error}", root=root, error=exc.strerror or exc)
                ) from None
            if stat.S_ISLNK(st.st_mode) and not _is_system_symlink(parent, st):
                raise ValueError(
                    _("Bilagekatalogen {root} ligger under symlänken {parent} och "
                    "används inte. Sätt PROTONMAIL_ATTACHMENT_DIR till den riktiga "
                    "sökvägen.",
                    root=root, parent=parent)
                )
        try:
            st = os.lstat(root)
        except FileNotFoundError:
            if not create:
                raise ValueError(
                    _("Bilagekatalogen {root} finns inte. Lägg filerna som ska "
                    "skickas där (PROTONMAIL_ATTACHMENT_DIR).",
                    root=root)
                ) from None
            try:
                root.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                os.mkdir(root, 0o700)
                os.chmod(root, 0o700)  # mkdir påverkas av umask
                st = os.lstat(root)
            except OSError as exc:  # t.ex. saknad behörighet eller en fil i vägen
                raise ValueError(
                    _("Bilagekatalogen {root} kan inte skapas: {error}", root=root, error=exc.strerror or exc)
                ) from None
        except OSError as exc:
            raise ValueError(
                _("Bilagekatalogen {root} kan inte kontrolleras: {error}", root=root, error=exc.strerror or exc)
            ) from None
        self._check_root_stat(st)
        return root.resolve()

    def _open_root(self, resolved: Path) -> int:
        """Öppna den kontrollerade roten som katalog utan att följa en symlänk
        och kontrollera det öppnade objektet igen (fstat), så att roten inte
        kan bytas ut mellan kontroll och skrivning."""
        try:
            fd = os.open(resolved, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC)
        except OSError as exc:
            raise ValueError(
                _("Bilagekatalogen {root} ändrades medan den kontrollerades "
                "({error}) — försök igen",
                root=self.root, error=exc.strerror or exc)
            ) from None
        try:
            self._check_root_stat(os.fstat(fd))
        except BaseException:
            os.close(fd)
            raise
        return fd

    def save(self, filename: str | None, data: bytes, content_type: str | None = None) -> Path:
        """Spara data under ett sanerat filnamn och returnera den nya filens sökväg.

        Skriver aldrig över: finns namnet redan (även som symlänk) prövas
        "namn (1).ext", "namn (2).ext" och så vidare. Filen skapas med O_EXCL
        och O_NOFOLLOW och rättigheterna 0600, och karantänmärks på macOS.
        """
        root = self._checked_root(create=True)
        base = sanitize_filename(filename, content_type)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_CLOEXEC
        dir_fd = self._open_root(root)
        try:
            for n in range(_MAX_COLLISIONS):
                name = base if n == 0 else _numbered(base, n)
                path = root / name
                if path.parent != root:  # kan inte hända efter saneringen, men ändå
                    raise ValueError(_("Ogiltigt filnamn för bilagan"))
                try:
                    fd = os.open(name, flags, 0o600, dir_fd=dir_fd)
                except FileExistsError:
                    continue
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                    _mark_quarantined(fh.fileno(), path)
                return path
        finally:
            os.close(dir_fd)
        raise ValueError(
            _("För många filer som heter {base!r} i bilagekatalogen — städa bland dem", base=base)
        )

    def resolve_existing(self, name: str) -> Path:
        """Upplöst sökväg till en befintlig vanlig fil inuti bilagekatalogen.

        ``name`` är ett filnamn eller en sökväg relativt katalogen, eller en
        absolut sökväg som pekar in i den. ~ expanderas inte.
        """
        return self._checked_file(name).path

    def _checked_file(self, name: str) -> _CheckedFile:
        if not name or not name.strip():
            raise ValueError(_("Tomt filnamn för bilaga"))
        if any(_is_unsafe_char(ch) for ch in name):
            raise ValueError(_("Filnamnet {name!r} innehåller kontrolltecken", name=name))
        root = self._checked_root()
        candidate = Path(name)
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            resolved = candidate.resolve(strict=True)
            st = os.lstat(resolved)
        except (FileNotFoundError, NotADirectoryError):
            raise ValueError(_("Filen {name!r} finns inte i bilagekatalogen {root}", name=name, root=root)) from None
        except (RuntimeError, OSError) as exc:  # symlänkloop m.m.
            raise ValueError(_("Filen {name!r} kan inte läsas: {error}",
                   name=name, error=exc.strerror or exc)) from None
        if resolved == root or root not in resolved.parents:
            raise ValueError(
                _("{name!r} ligger utanför bilagekatalogen {root}. Bara filer i "
                "bilagekatalogen (PROTONMAIL_ATTACHMENT_DIR) kan skickas.",
                name=name, root=root)
            )
        if not stat.S_ISREG(st.st_mode):
            raise ValueError(_("{name!r} är inte en vanlig fil", name=name))
        if st.st_nlink > 1:
            raise ValueError(
                _("{name!r} har flera hårda länkar och kan vara en fil utanför "
                "bilagekatalogen — kopiera filen dit i stället",
                name=name)
            )
        return _CheckedFile(resolved, st, resolved.relative_to(root))


# Keynote-presentationer slutar också på .key men är zip-arkiv
_ZIP_MAGIC = b"PK\x03\x04"


def _only_key_extension(part: str) -> bool:
    """Namnet är känsligt bara för ändelsen .key (inte t.ex. id_rsa.key)."""
    return part.lower().endswith(".key") and not _is_sensitive_part(part[:-4])


def _sensitive_reason(name: str, checked: _CheckedFile) -> str | None:
    """Varför filen ser ut att vara hemlig, utifrån namnet som angavs och
    den upplösta sökvägen inom bilagekatalogen; None om inget träffar.

    Filnamnet (sista ledet) får sluta på .key om filen börjar som ett
    zip-arkiv (PK\\x03\\x04), som en Keynote-presentation. Innehållet
    kontrolleras ändå mot privata nycklar när filen läses.
    """
    given = Path(name)
    given_parts = [given.name] if given.is_absolute() else list(given.parts)
    candidates = [
        *((part, i == len(checked.relative.parts) - 1) for i, part in enumerate(checked.relative.parts)),
        *((part, i == len(given_parts) - 1) for i, part in enumerate(given_parts)),
    ]
    is_zip: bool | None = None
    for part, is_file_name in candidates:
        if not _is_sensitive_part(part):
            continue
        if is_file_name and _only_key_extension(part):
            if is_zip is None:
                is_zip = _read_checked(name, checked, len(_ZIP_MAGIC)) == _ZIP_MAGIC
            if is_zip:
                continue
        return _("namnet {part!r}", part=part)
    return None


def _identity(st: os.stat_result) -> tuple[int, ...]:
    """Det som skiljer en fil från en annan som tagit dess plats.

    st_dev/st_ino räcker inte: ext4 och overlayfs (Linux, Docker, GitHub
    Actions) återanvänder ofta inodnumret direkt när en fil tas bort och en ny
    skapas. Storlek, mtime och ctime (som användaren inte kan sätta) avslöjar
    då bytet.
    """
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _read_checked(name: str, checked: _CheckedFile, limit: int) -> bytes:
    """Läs högst ``limit`` byte ur filen som kontrollerades — och bara den.

    O_NOFOLLOW gör att en fil som bytts mot en symlänk inte öppnas, och
    filens identitet (_identity) jämförs så att en annan fil (eller en katalog
    på vägen som bytts mot en symlänk) upptäcks. O_NONBLOCK gör att en FIFO
    inte hänger.
    """
    changed = ValueError(_("{name!r} ändrades medan den kontrollerades och skickas inte", name=name))
    try:
        fd = os.open(checked.path, os.O_RDONLY | _O_NOFOLLOW | _O_NONBLOCK | _O_CLOEXEC)
    except OSError:
        raise changed from None
    with os.fdopen(fd, "rb") as fh:
        st = os.fstat(fh.fileno())
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_nlink != 1
            or _identity(st) != _identity(checked.stat)
        ):
            raise changed
        return fh.read(limit)


def _guess_type(name: str) -> tuple[str, str]:
    ctype, encoding = mimetypes.guess_type(name, strict=False)
    if ctype is None or encoding is not None or "/" not in ctype:
        # Okänd typ, eller komprimerad (a.tar.gz är inte application/x-tar)
        return "application", "octet-stream"
    maintype, subtype = ctype.split("/", 1)
    return maintype, subtype


def load_for_sending(
    names: Sequence[str] | None, store: AttachmentStore | None = None
) -> list[EmailMessage]:
    """Läs filer ur bilagekatalogen och gör dem till MIME-bilagor.

    Högst MAX_SEND_FILES filer och MAX_SEND_BYTES byte tillsammans. Typen
    gissas ur filändelsen; filnamnet (sista ledet) RFC 2231-kodas i
    Content-Disposition om det inte är ASCII. Allt kontrolleras innan något
    skickas, så ett fel betyder att inget mejl har gått iväg.
    """
    if not names:
        return []
    if len(names) > MAX_SEND_FILES:
        raise ValueError(_("För många bilagor ({n_names}) — högst {MAX_SEND_FILES} per mejl",
                           n_names=len(names), MAX_SEND_FILES=MAX_SEND_FILES))
    store = store or get_store()
    checked = [store._checked_file(name) for name in names]
    for name, item in zip(names, checked):
        reason = _sensitive_reason(name, item)
        if reason:
            raise _refuse_secret(name, reason)
    total = sum(item.stat.st_size for item in checked)
    if total > MAX_SEND_BYTES:
        raise ValueError(
            _("Bilagorna är tillsammans {total} byte — högst {MAX_SEND_BYTES} byte "
            "(25 MB) per mejl",
            total=total, MAX_SEND_BYTES=MAX_SEND_BYTES)
        )
    parts: list[EmailMessage] = []
    remaining = MAX_SEND_BYTES
    for name, item in zip(names, checked):
        data = _read_checked(name, item, remaining + 1)  # filen kan ha vuxit
        remaining -= len(data)
        if remaining < 0:
            raise ValueError(_("Bilagorna är tillsammans över {MAX_SEND_BYTES} byte (25 MB)",
                   MAX_SEND_BYTES=MAX_SEND_BYTES))
        if _PRIVATE_KEY_RE.search(data):
            raise _refuse_secret(name, _("innehållet är en privat nyckel"))
        maintype, subtype = _guess_type(item.path.name)
        part = EmailMessage(policy=email.policy.SMTP)
        part.set_content(
            data, maintype=maintype, subtype=subtype,
            disposition="attachment", filename=item.path.name,
        )
        parts.append(part)
    return parts


def prepare_attachment_dir(settings: Settings) -> None:
    """Skapa och kontrollera bilagekatalogen när servern startar.

    Skapas katalogen först vid första sparningen hinner en uppackad zip
    lägga en symlänk med samma namn i ~/Downloads. Kastar aldrig: ett fel
    loggas som varning, servern startar ändå och bilageverktygen ger samma
    fel när de används.
    """
    store = AttachmentStore.from_settings(settings)
    try:
        path = store._checked_root(create=True)
    except ValueError as exc:
        logger.warning("%s", exc)
        return
    logger.info("Bilagekatalog: %s", path)


def get_store() -> AttachmentStore:
    """Bilagekatalogen enligt inställningarna (PROTONMAIL_ATTACHMENT_DIR)."""
    from .config import get_settings

    return AttachmentStore.from_settings(get_settings())
