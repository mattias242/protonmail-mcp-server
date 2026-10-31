import copy
import email.policy
import logging
import re
import ssl
import time
from email.header import Header
from email.headerregistry import Address
from email.message import EmailMessage, Message
from email.utils import formatdate, make_msgid, parseaddr
from typing import Sequence

import aiosmtplib

from .i18n import _
from . import audit, policy
from .config import Settings, idna_domain
from .email_parser import parse_addresses
from .sanitize import sanitize_text

logger = logging.getLogger(__name__)


# Domänen som punktseparerade etiketter utan tomma led. Etiketterna kan inte
# innehålla punkt, så mönstret är entydigt och linjärt — den tidigare formen
# [^@\s]+\.[^@\s]+ backtrackade kvadratiskt (13 s för en adress på 50 000
# tecken), och vid svar kommer adresserna ur originalets headers.
_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+$')

# Ren sanitetsgräns mot uppenbart felaktiga ämnen (t.ex. en hel text inklistrad
# som ämne). 998 är en gräns per rad, inte per header: generatorn viker långa
# headers på flera rader, så den gränsen gäller inte här.
_MAX_SUBJECT_LENGTH = 10_000


# Kategorierna i sanitize_text, som de beskrivs i felet om dolda tecken
_HIDDEN_KINDS = {
    "tag": "taggtecken",
    "bidi": "bidi-styrtecken",
    "bidi_mark": "riktningsmarkeringar",
    "zero_width": "nollbreddstecken",
    "format": "formattecken",
    "control": "kontrolltecken",
    "variation_selector": "variationsväljare",
    "combining_excess": "kombinationstecken",
    "surrogate": "ensamma surrogattecken",
}


# Den synliga märkningen av ett dolt tecken i visningen ("[U+E0041]")
_VISIBLE_ESCAPE_RE = re.compile(r"\[U\+", re.IGNORECASE)


def _reject_hidden_characters(addr: str, spec: str) -> None:
    """ValueError om adressen (addr-spec) innehåller tecken som inte syns.

    Taggtecken, bidi-styrtecken, nollbreddstecken och andra formattecken (Cf)
    ritas inte, så en adress med sådana ser ut som en annan adress än den
    som mejlet går till. Samma tecken som sanitize.sanitize_text tar bort ur
    text räknas, liksom visningens märkning av dem ("[U+…]", som en LLM kan
    ha kopierat ur get_email). Visningsnamnet kontrolleras inte här.
    """
    # Hela strängen: parseaddr tolkar "[" som avgränsare och kan tappa delen
    if _VISIBLE_ESCAPE_RE.search(str(addr)) or _VISIBLE_ESCAPE_RE.search(spec):
        # get_email visar ett dolt tecken i en adress som "[U+E0041]"; en
        # kopierad visad adress är inte den adress som mejlet kom ifrån
        raise ValueError(
            _("Ogiltig e-postadress: {addr} innehåller \"[U+…]\", som är hur get_email "
            "visar dolda tecken i en adress. Den riktiga adressen innehåller alltså dolda tecken "
            "och ser ut som en annan adress än den som visas; sådana adresser vägras, så inget "
            "skickas eller sparas. Fråga användaren vilken adress mejlet ska till.",
            addr=ascii(addr))
        )
    removed = sanitize_text(spec).removed
    if removed:
        kinds = ", ".join(f"{_(_HIDDEN_KINDS.get(k, k))}: {n}" for k, n in removed.items())
        # ascii(): de dolda tecknen syns som \U000e0041 osv. i felet
        raise ValueError(
            _("Ogiltig e-postadress: {addr} innehåller dolda tecken ({kinds}). "
            "En sådan adress ser ut som en annan adress än den som mejlet skulle gå till, "
            "så inget skickas eller sparas. Skriv adressen utan de dolda tecknen.",
            addr=ascii(addr), kinds=kinds)
        )


def _validate_email_addr(addr: str) -> str:
    """Kastar ValueError om adressen innehåller CRLF eller är ogiltig.

    Adresser med dolda tecken (taggtecken, bidi-styrtecken, nollbreddstecken,
    andra formattecken) vägras. Adressen byggs också som ett Address-objekt
    (_to_address), så att en adress som klarar mönstret men inte går att
    lägga i ett mejl vägras här som ogiltig, innan något byggs eller skickas.
    """
    if '\r' in addr or '\n' in addr:
        raise ValueError(_("Ogiltig e-postadress (CRLF): {addr!r}", addr=addr))
    _name, parsed = parseaddr(addr)
    _reject_hidden_characters(addr, parsed)
    if not parsed or not _EMAIL_RE.match(parsed):
        raise ValueError(_("Ogiltig e-postadress: {addr!r}", addr=addr))
    _to_address(addr)
    return addr


class SMTPPartialDeliveryError(aiosmtplib.SMTPException):
    """Servern tog emot mejlet för några mottagare men vägrade andra.

    aiosmtplib kastar bara när alla mottagare vägras; vägras bara några
    returneras felen tyst. Mejlet HAR då gått iväg till ``delivered``, så den
    som anropar ska inte skicka om till alla.
    """

    def __init__(self, refused: dict[str, tuple[int, str]], delivered: list[str]) -> None:
        self.refused = refused
        self.delivered = delivered
        details = "; ".join(
            f"{addr} ({code} {text})" for addr, (code, text) in refused.items()
        )
        super().__init__(
            _("E-posten skickades till {n_delivered} mottagare "
            "({delivered}), men servern vägrade {n_refused}: "
            "{details}. Skicka inte om till alla — de som redan fått mejlet "
            "får det då två gånger.",
            n_delivered=len(delivered), delivered=", ".join(delivered), n_refused=len(refused), details=details)
        )


def _to_address(addr: str) -> Address:
    """Gör om en (validerad) adressträng till ett Address-objekt.

    Display-namnet behålls som text så att policyn kan RFC 2047-koda just
    namnet; själva adressen får aldrig kodas. En IDN-domän skrivs om till
    punycode (IDNA2008, samma som tillåtelselistan) så att både header och
    RCPT TO blir ren ASCII.
    """
    name, spec = parseaddr(addr)
    local, _at, domain = spec.rpartition("@")
    if not domain.isascii():
        try:
            domain = idna_domain(domain)
        except UnicodeError:
            raise ValueError(_("Ogiltig e-postadress (domän): {addr!r}", addr=addr)) from None
    try:
        return Address(display_name=name, addr_spec=f"{local}@{domain}")
    except Exception:
        # email.headerregistry kastar HeaderParseError (inte ValueError) för
        # t.ex. en oavslutad citerad lokaldel. Utan kedjan (from None): ett
        # stackspår med adressen (kanske Bcc) får inte hamna i stderr-loggen.
        raise ValueError(_("Ogiltig e-postadress: {addr!r}", addr=addr)) from None


def _set_unstructured(msg: EmailMessage, name: str, value: str) -> None:
    """Sätt en fritextheader (t.ex. Subject) så att den överlever ombrytning.

    email.policy-foldern tappar eller dubblar mellanslag när den viker långa
    icke-ASCII-värden (CPython 3.12). Header kodar i stället hela värdet
    som angränsande encoded-words, vars mellanrum mottagaren ignorerar, och
    radlängden hålls under 78 så att policyn inte viker om dem.
    """
    if value.isascii():
        msg[name] = value
    else:
        msg.set_raw(name, Header(value, "utf-8", header_name=name).encode(linesep="\r\n"))


def _validate_subject(subject: str) -> str:
    """Kastar ValueError om ämnesraden innehåller CRLF eller är orimligt lång."""
    if '\r' in subject or '\n' in subject:
        raise ValueError(_("Ämnesraden får inte innehålla radbrytningar"))
    if len(subject) > _MAX_SUBJECT_LENGTH:
        raise ValueError(
            _("Ämnesraden är för lång (max {_MAX_SUBJECT_LENGTH} tecken)", _MAX_SUBJECT_LENGTH=_MAX_SUBJECT_LENGTH)
        )
    return subject


def _normalize_recipients(value: str | Sequence[str] | None) -> list[str]:
    """Normalize a recipient field (to/cc/bcc) to a flat list of addresses."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


# Hårt tak för antalet adressposter (To, Cc och Bcc tillsammans, före
# deduplicering) i ett skick eller utkast, oavsett PROTONMAIL_MAX_RECIPIENTS.
# Kontrolleras innan någon adress valideras, så att en jättelista inte ens
# bearbetas. Proton själv tar emot långt färre mottagare per mejl.
MAX_RECIPIENT_ENTRIES = 1000


def _recipient_lists(
    to: str | Sequence[str] | None,
    cc: str | Sequence[str] | None,
    bcc: str | Sequence[str] | None,
) -> tuple[list[str], list[str], list[str]]:
    """To, Cc och Bcc som listor; ValueError om de tillsammans har fler än
    MAX_RECIPIENT_ENTRIES poster."""
    lists = (_normalize_recipients(to), _normalize_recipients(cc), _normalize_recipients(bcc))
    total = sum(len(lst) for lst in lists)
    if total > MAX_RECIPIENT_ENTRIES:
        raise ValueError(
            _("Mottagarlistan har för många adresser ({total} i To, Cc och Bcc tillsammans; "
            "högst {MAX_RECIPIENT_ENTRIES}). Inget mejl skickades eller sparades.",
            total=total, MAX_RECIPIENT_ENTRIES=MAX_RECIPIENT_ENTRIES)
        )
    return lists


def _unique_recipients(addrs: Sequence[str]) -> list[str]:
    """Adresserna utan dubbletter, utan hänsyn till skiftläge; den första
    förekomsten behålls. Det är den listan som RCPT TO får och som
    PROTONMAIL_MAX_RECIPIENTS räknar, så att en upprepad adress (eller samma
    adress med andra versaler) inte blir flera mottagare hos Bridge."""
    unique: list[str] = []
    seen: set[str] = set()
    for addr in addrs:
        key = addr.lower()
        if key not in seen:
            seen.add(key)
            unique.append(addr)
    return unique


def _build_message(
    *,
    from_addr: str,
    to_list: list[str],
    subject: str,
    body: str,
    body_html: str | None = None,
    cc_list: list[str] | None = None,
    reply_to: str | None = None,
    additional_headers: dict[str, str] | None = None,
    attachments: Sequence[Message] | None = None,
    inline_parts: Sequence[Message] | None = None,
) -> EmailMessage:
    """Build a MIME message without requiring an SMTP connection.

    Byggs med den moderna policyn (email.policy.SMTP): den kodar bara
    display-namn med icke-ASCII-tecken, aldrig hela adressheadern.

    ``attachments`` och ``inline_parts`` är färdiga MIME-delar (t.ex. ur ett
    mejl som vidarebefordras) och bifogas oförändrade. Inline-delar, som
    bilder som HTML-versionen pekar på med cid:, läggs i en multipart/related
    tillsammans med HTML-delen; utan HTML-version blir de vanliga bilagor.
    """
    msg = EmailMessage(policy=email.policy.SMTP)
    msg.set_content(body)
    attached = list(attachments or [])
    if body_html:
        msg.add_alternative(body_html, subtype="html")
        if inline_parts:
            html_part = msg.get_body(("html",))
            html_part.make_related()
            for part in inline_parts:
                html_part.attach(part)
    else:
        attached = list(inline_parts or []) + attached
    if attached:
        msg.make_mixed()
        for part in attached:
            msg.attach(part)

    sender = _to_address(from_addr)
    msg["From"] = sender
    # To är valfri enligt RFC 5322, men får inte vara tom om den finns
    if to_list:
        msg["To"] = [_to_address(a) for a in to_list]
    _set_unstructured(msg, "Subject", subject)
    msg["Date"] = formatdate(localtime=True)
    # Utan domain använder make_msgid() maskinens värdnamn (getfqdn), vilket
    # läcker det lokala nätets namn till mottagarna
    msg["Message-ID"] = make_msgid(domain=sender.domain or "localhost")
    if cc_list:
        msg["Cc"] = [_to_address(a) for a in cc_list]
    if reply_to:
        msg["Reply-To"] = _to_address(reply_to)
    if additional_headers:
        for key, value in additional_headers.items():
            msg[key] = value

    return msg


# Adressheaders i ett färdigt meddelande (utkast) parsas med email.policy,
# vars parser är superlinjär; normala headers är långt under en kilobyte.
_MAX_ADDRESS_HEADER_CHARS = 50_000


def _header_addr_specs(msg: Message, name: str) -> list[str]:
    """Alla adresser (addr-spec) i en adressheader, även ogiltiga.

    Parsas strukturellt med email.policy: RFC 2047-ord avkodas bara i
    display-namnet, så ett kodat kommatecken eller "<…>" i ett namn blir
    ingen mottagare. En post som inte går att tolka kommer med som den är
    (t.ex. "<>"), så att valideringen avvisar den i stället för att den
    tyst försvinner ur mottagarlistan.
    """
    specs: list[str] = []
    for key, value in msg.raw_items():
        if key.lower() != name.lower():
            continue
        text = str(value)
        if len(text) > _MAX_ADDRESS_HEADER_CHARS:
            raise ValueError(_("{name}-headern är orimligt stor ({n_text} tecken)", name=name, n_text=len(text)))
        if not text.isascii():
            # Råa UTF-8-headers (RFC 6532) kommer som surrogat-escapes
            data = text.encode("utf-8", "surrogateescape")
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = data.decode("latin-1")
        try:
            header = email.policy.SMTP.header_fetch_parse(key, text)
            specs.extend(address.addr_spec for address in header.addresses)
        except Exception as exc:  # parsern kan krascha på trasiga adresser (t.ex. IndexError)
            raise ValueError(_("Ogiltig e-postadress i {name}: {text!r}", name=name, text=text)) from exc
    return specs


_RECIPIENT_HEADERS = (("to", "To"), ("cc", "Cc"), ("bcc", "Bcc"))
_INTERNAL_HEADER_PREFIX = "x-pm-"


def _strip_internal_headers(msg: Message) -> None:
    """Ta bort Protons interna rubriker (X-Pm-Internal-Id, X-Pm-Gluon-Id,
    X-Pm-Date, X-Pm-Origin, X-Pm-Content-Encryption …) ur ett utkast som skickas.

    Bridge sätter dem när utkastet visas i Drafts (pkg/message/build.go) men
    läser dem inte vid SMTP. För mottagare med PGP/MIME skickar Bridge hela
    MIME-texten med rubriker (createSendReq → AddMIMEPackage), så de kunde
    annars följa med ut med Protons interna id:n.
    """
    for name in {k for k in msg.keys() if k.lower().startswith(_INTERNAL_HEADER_PREFIX)}:
        del msg[name]


def prepared_recipients(msg: Message) -> dict[str, list[str]]:
    """Mottagarna i ett färdigt meddelande (utkast), exakt som get_email visar dem.

    Granskningen före send_draft sker med get_email, som tolkar To, Cc och
    Bcc med email_parser.parse_addresses och bara visar den första rubriken
    av varje. Samma tolkning används här, så att mejlet går till exakt de
    adresser som visades. För att ingen adress ska kunna döljas för
    granskningen vägras meddelandet (ValueError, inget skickas) om
    - en adressrubrik förekommer mer än en gång, eller
    - den strukturella adressparsern (_header_addr_specs) ger andra adresser
      än visningens tolkning, t.ex. vid kommatecken i en kommentar
      ("a@x.se (Efternamn, Förnamn)"), som visningen inte kan tolka.

    Returnerar {"to": [...], "cc": [...], "bcc": [...]} med de nakna
    adresserna som RCPT TO får (IDN-domäner som punycode).
    """
    result: dict[str, list[str]] = {}
    for key, name in _RECIPIENT_HEADERS:
        values = [value for k, value in msg.raw_items() if k.lower() == key]
        if len(values) > 1:
            raise ValueError(
                _("Utkastet har {n_values} {name}-rubriker. get_email visar bara den "
                "första, så utkastet skickas inte — skriv om det med update_draft.",
                n_values=len(values), name=name)
            )
        structural = _header_addr_specs(msg, name)
        shown = [addr for _, addr in parse_addresses(values[0])] if values else []
        for addr in structural + shown:
            _validate_email_addr(addr)
        if shown != structural:
            raise ValueError(
                _("{name}-rubriken kan inte tolkas entydigt: get_email visar "
                "{shown}, men rubriken innehåller {structural}. "
                "Utkastet skickas inte — skriv om adresserna med update_draft.",
                name=name, shown=shown or _("inga adresser"), structural=structural)
            )
        result[key] = [_to_address(addr).addr_spec for addr in shown]
    if not any(result.values()):
        raise ValueError(_("Meddelandet saknar mottagare (To, Cc och Bcc är tomma)"))
    return result


class SMTPClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # Klockan för PROTONMAIL_MAX_SENDS_PER_HOUR (byts ut i tester)
        self._clock = time.monotonic
        self._rate = policy.SendRateLimiter(lambda: self._clock())
        self._ssl_ctx = ssl.create_default_context()
        self._ssl_ctx.check_hostname = False

        if settings.verify_ssl and settings.smtp_ca_cert:
            # Pinna Bridge-certifikatet
            self._ssl_ctx.verify_mode = ssl.CERT_REQUIRED
            self._ssl_ctx.load_verify_locations(cafile=settings.smtp_ca_cert)
        elif settings.verify_ssl:
            self._ssl_ctx.verify_mode = ssl.CERT_REQUIRED
        else:
            # Standard Bridge-läge: self-signed cert
            self._ssl_ctx.verify_mode = ssl.CERT_NONE

    @property
    def account_address(self) -> str:
        """Kontots adress, det vill säga avsändaren i allt som skickas."""
        return self._settings.username

    async def send_email(
        self,
        to: str | Sequence[str],
        subject: str,
        body: str,
        body_html: str | None = None,
        cc: str | Sequence[str] | None = None,
        bcc: str | Sequence[str] | None = None,
        reply_to: str | None = None,
        additional_headers: dict[str, str] | None = None,
        attachments: Sequence[Message] | None = None,
        inline_parts: Sequence[Message] | None = None,
    ) -> bool:
        _validate_subject(subject)

        to_list, cc_list, bcc_list = _recipient_lists(to, cc, bcc)

        for addr in to_list + cc_list + bcc_list:
            _validate_email_addr(addr)
        if reply_to:
            _validate_email_addr(reply_to)

        msg = _build_message(
            from_addr=self._settings.username,
            to_list=to_list,
            subject=subject,
            body=body,
            body_html=body_html,
            cc_list=cc_list,
            reply_to=reply_to,
            additional_headers=additional_headers,
            attachments=attachments,
            inline_parts=inline_parts,
        )

        # RCPT TO får bara den nakna adressen (med punycode-domän), en gång
        # per mottagare oavsett skiftläge (som send_prepared_message)
        all_recipients = _unique_recipients(
            [_to_address(a).addr_spec for a in to_list + cc_list + bcc_list]
        )
        return await self._deliver(msg, all_recipients)

    async def send_prepared_message(self, msg: Message) -> bool:
        """Skicka ett färdigt meddelande, t.ex. ett utkast ur Drafts.

        Mottagarna läses ur To, Cc och Bcc med prepared_recipients (samma
        tolkning som get_email visar; dubbla eller tvetydiga adressrubriker
        vägras) och valideras som i send_email. Bcc-headern tas bort ur det
        som skickas (de hemliga mottagarna får mejlet via RCPT TO), men
        meddelandet som skickas in ändras inte. Protons interna rubriker
        (X-Pm-*, som Bridge sätter på utkast i Drafts) tas också bort; References
        lämnas orörd, eftersom Bridge hittar utkastet och originalet med
        <id@protonmail.internalid>-referenserna där. Samma TLS, inloggning och
        felhantering som send_email, inklusive SMTPPartialDeliveryError när
        bara några mottagare vägras.
        """
        recipients = _unique_recipients(
            [rcpt for addrs in prepared_recipients(msg).values() for rcpt in addrs]
        )

        outgoing = copy.deepcopy(msg)
        del outgoing["Bcc"]
        _strip_internal_headers(outgoing)
        return await self._deliver(outgoing, recipients)

    async def _deliver(self, msg: Message, all_recipients: list[str]) -> bool:
        """Skicka över SMTP med Bridge-inloggning. Alla skick går hit.

        Skickpolicyn (policy.py: PROTONMAIL_READ_ONLY m.fl.) kontrolleras här,
        innan Bridge kontaktas, så att ingen väg kan skicka förbi den.
        """
        fields = {"recipients": list(all_recipients), "message_id": msg.get("Message-ID")}
        try:
            policy.require_send(self._settings, "skicket")
            policy.check_recipient_count(self._settings, all_recipients)
            policy.check_recipients(self._settings, all_recipients)
            # Sist: bara skick som klarat alla andra kontroller räknas
            self._rate.acquire(self._settings)
            await self._smtp_send(msg, all_recipients)
        except BaseException as exc:
            audit.record(self._settings, "send", result="error", error=exc, **fields)
            raise
        audit.record(self._settings, "send", result="ok", **fields)
        return True

    async def _smtp_send(self, msg: Message, all_recipients: list[str]) -> None:
        """Själva SMTP-skicket; anropas bara från _deliver, efter policyn."""
        logger.info(
            "Skickar e-post (%d mottagare) via SMTP %s:%d",
            len(all_recipients),
            self._settings.smtp_host,
            self._settings.smtp_port,
        )
        async with aiosmtplib.SMTP(
            hostname=self._settings.smtp_host,
            port=self._settings.smtp_port,
            use_tls=True,
            tls_context=self._ssl_ctx,
        ) as client:
            await client.login(self._settings.username, self._settings.password)
            errors, _ = await client.send_message(msg, recipients=all_recipients)

        if errors:
            # Bara antal här; adresserna (även Bcc) står i granskningsloggen (audit.py)
            logger.warning(
                "E-post skickad men %d av %d mottagare vägrades",
                len(errors),
                len(all_recipients),
            )
            raise SMTPPartialDeliveryError(
                refused={
                    addr: (resp.code, resp.message) for addr, resp in errors.items()
                },
                delivered=[a for a in all_recipients if a not in errors],
            )

        logger.info("E-post skickad")
