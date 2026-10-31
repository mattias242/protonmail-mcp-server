import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

import idna
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# <repo>/src/protonmail_mcp/config.py → <repo>/.env. Läses även när servern
# startas från en annan katalog (t.ex. uv run --project <repo>).
PROJECT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


# Domän efter normalisering: punycode-etiketter, minst två, inga tomma led
_DOMAIN_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)(?:\.(?!-)[a-z0-9-]{1,63}(?<!-))+$")
_LOCAL_RE = re.compile(r'^[^@\s<>,"]+$')


def idna_domain(domain: str) -> str:
    """En domän med icke-ASCII-tecken som punycode, enligt IDNA2008 (UTS #46).

    Pythons codec "idna" följer IDNA2003 och skriver om ß till ss och ς till
    σ, men straße.de och strasse.de är olika registrerade domäner. Här används
    UTS #46 utan övergångsmappning (transitional=False), som webbläsare och
    registren (DENIC, .gr) gör. Samma funktion används för tillåtelselistan
    och för RCPT TO, så att de alltid ger samma domän. UnicodeError
    (idna.IDNAError) för en ogiltig domän.
    """
    return idna.encode(domain, uts46=True, transitional=False).decode("ascii")


def normalize_domain(domain: str) -> str | None:
    """Domänen i gemener och som punycode, eller None om den är ogiltig."""
    value = domain.strip().lower()
    if not value.isascii():
        try:
            value = idna_domain(value)
        except UnicodeError:
            return None
    return value if _DOMAIN_RE.match(value) else None


@lru_cache(maxsize=8)
def parse_allowed_recipients(raw: str) -> tuple[tuple[str, str], ...]:
    """Tolka PROTONMAIL_ALLOWED_RECIPIENTS till (lokal del, domän)-par.

    En domänpost (@exempel.se) får tom lokal del. Domänen normaliseras till
    gemener och punycode; den lokala delen behålls exakt. ValueError för en
    post som inte är en adress eller en domän.
    """
    rules: list[tuple[str, str]] = []
    for entry in (part.strip() for part in raw.split(",")):
        if not entry:
            continue
        local, at, domain = entry.rpartition("@")
        normalized = normalize_domain(domain) if at else None
        if normalized is None or (local and not _LOCAL_RE.match(local)) or (not local and not entry.startswith("@")):
            raise ValueError(
                f"Ogiltig post i PROTONMAIL_ALLOWED_RECIPIENTS: {entry!r}. Ange adresser "
                "(anna@exempel.se) och/eller domäner (@exempel.se), åtskilda med kommatecken."
            )
        rules.append((local, normalized))
    return tuple(rules)


# Varje bevakad mapp är en egen IMAP-anslutning till Bridge
MAX_WATCH_MAILBOXES = 5
# Samma regel som imap_client._validate_mailbox (importeras inte härifrån:
# imap_client importerar config): inga kontrolltecken eller ensamma surrogater
_WATCH_MAILBOX_RE = re.compile(r'[^\x00-\x1F\x7F\uD800-\uDFFF]{1,255}')


@lru_cache(maxsize=8)
def parse_watch_mailboxes(raw: str) -> tuple[str, ...]:
    """Tolka PROTONMAIL_WATCH_MAILBOXES till en lista med unika mappnamn.

    Kommaseparerat, blanktecken runt namnen tas bort. INBOX jämförs utan
    hänsyn till skiftläge (RFC 3501) och skrivs alltid "INBOX"; övriga namn
    behålls exakt. ValueError för ett ogiltigt namn, en tom lista eller fler
    än MAX_WATCH_MAILBOXES mappar.
    """
    names: list[str] = []
    for entry in (part.strip() for part in raw.split(",")):
        if not entry:
            continue
        if not _WATCH_MAILBOX_RE.fullmatch(entry):
            raise ValueError(f"Ogiltigt mappnamn i PROTONMAIL_WATCH_MAILBOXES: {entry!r}")
        if entry.upper() == "INBOX":
            entry = "INBOX"
        if entry not in names:
            names.append(entry)
    if not names:
        raise ValueError(
            "PROTONMAIL_WATCH_MAILBOXES måste innehålla minst en mapp. Stäng av "
            "bevakningen med PROTONMAIL_WATCH_ENABLED=false i stället."
        )
    if len(names) > MAX_WATCH_MAILBOXES:
        raise ValueError(
            f"PROTONMAIL_WATCH_MAILBOXES får innehålla högst {MAX_WATCH_MAILBOXES} mappar "
            f"(fick {len(names)}): varje mapp är en egen anslutning till Bridge."
        )
    return tuple(names)


class Settings(BaseSettings):
    imap_host: str = "127.0.0.1"
    imap_port: int = 1143
    smtp_host: str = "127.0.0.1"
    smtp_port: int = 1026
    # Sekunder per IMAP-kommando. aioimaplib har 10 s som standard, men en
    # SEARCH i All Mail tar 5–9 s mot Bridge på ett konto med ~30 000 mejl.
    imap_timeout: float = Field(default=30.0, gt=0)
    username: str
    password: str
    verify_ssl: bool = False
    smtp_ca_cert: str | None = None
    # All filåtkomst för bilagor (get_attachment, attachments-parametern)
    # begränsas till den här katalogen. Den skapas med rättigheterna 0700
    # när servern startar. Måste vara absolut (eller börja med ~): Claude
    # Desktop startar ofta servern med arbetskatalogen /.
    attachment_dir: Path = Field(default=Path("~/Downloads/protonmail-mcp"), validate_default=True)
    # Största bilaga som get_attachment returnerar direkt (save=False), i byte
    attachment_max_inline_bytes: int = Field(default=1024 * 1024, gt=0)

    # --- Begränsningar av vad servern (och därmed en LLM) får göra ---
    # Kontrolleras centralt (policy.py): i IMAP-klientens skrivfunktioner,
    # i SMTP-klientens leverans och i MCP-servern före varje verktyg.
    # Alla vägringar är fel som når klienten; inget skickas till Bridge.
    #
    # PROTONMAIL_READ_ONLY=true: allt som ändrar kontot vägras — flytt,
    # radering, läst/oläst, stjärna, etiketter, mappar, utkast och skick.
    # Läsande verktyg fungerar som vanligt. Standard: false.
    read_only: bool = False
    # PROTONMAIL_SEND_MODE:
    #   "send" (standard) — som tidigare: verktygen får skicka.
    #   "draft_only" — send_email, reply_to_email, forward_email och send_draft
    #       vägras; utkast (create_draft, create_reply_draft,
    #       create_forward_draft, update_draft) går bra, så att användaren
    #       själv granskar och skickar från Proton.
    #   "disabled" — även utkasten vägras.
    send_mode: Literal["send", "draft_only", "disabled"] = "send"
    # PROTONMAIL_LANGUAGE: språket i det klienten ser — verktygens titlar och
    # beskrivningar, promptar, resurser, serverinstruktionerna och alla fel-
    # och förklaringstexter (i18n.py). "sv" (standard) eller "en". Verktygens
    # och parametrarnas namn är alltid desamma. Fel som uppstår innan
    # inställningarna lästs (den här filen, http_server.py) är på svenska.
    language: Literal["sv", "en"] = "sv"
    # PROTONMAIL_ALLOWED_RECIPIENTS: kommaseparerad lista med adresser
    # (anna@exempel.se) och/eller domäner (@exempel.se). När den är satt måste
    # VARJE mottagare i ett skick (To, Cc och Bcc, även i send_draft och
    # vidarebefordran) matcha en post, annars vägras hela skicket och felet
    # anger vilka adresser som inte är tillåtna. Domänen jämförs utan hänsyn
    # till skiftläge och som punycode (exämple.se = xn--exmple-cua.se); en
    # domänpost gäller bara den domänen, inte underdomäner. Delen före @ i en
    # adresspost jämförs exakt. Tom (standard) = alla mottagare tillåtna.
    allowed_recipients: str = ""
    # PROTONMAIL_MAX_RECIPIENTS: högst så många (unika) mottagare per skick,
    # To, Cc och Bcc tillsammans. Samma adress (oavsett skiftläge) räknas och
    # får mejlet en gång. 0 = ingen gräns. Standard: 50. Oavsett gräns vägras
    # fler än 1000 adressposter (smtp_client.MAX_RECIPIENT_ENTRIES).
    max_recipients: int = Field(default=50, ge=0)
    # PROTONMAIL_MAX_SENDS_PER_HOUR: högst så många skick under en glidande
    # timme (räknas i minnet, nollställs när servern startas om). Felet säger
    # när nästa skick går. Vägrade skick räknas inte; ett skick som nådde
    # Bridge räknas även om det misslyckades. 0 = ingen gräns. Standard: 30.
    max_sends_per_hour: int = Field(default=30, ge=0)
    # PROTONMAIL_AUDIT_LOG: fil som granskningsloggen (audit.py) också skrivs
    # till, en JSON-rad per skick och per skrivande IMAP-operation (tid,
    # verktyg, åtgärd, mapp, UID:n, mottagaradresser, Message-ID, resultat —
    # aldrig ämne, brödtext, bilagor eller lösenord). Raderna loggas alltid
    # på INFO till stderr; med den här satt även till filen, som skapas med
    # rättigheterna 0600 och aldrig skrivs genom en symlänk. Absolut sökväg
    # (eller ~). Standard: ingen fil.
    audit_log: Path | None = None

    # --- Bevakning av nya mejl (imap_idle.py) ---
    # PROTONMAIL_WATCH_ENABLED: bevaka mapparna med IMAP IDLE i egna
    # anslutningar till Bridge (en per mapp, aldrig verktygens delade), så
    # att check_new_emails och wait_for_new_email ser nya mejl direkt. Är den
    # avstängd frågar verktygen Bridge i stället. Standard: true.
    watch_enabled: bool = True
    # PROTONMAIL_WATCH_MAILBOXES: kommaseparerade mappar att bevaka, högst
    # MAX_WATCH_MAILBOXES (5). Standard: INBOX.
    watch_mailboxes: str = "INBOX"

    # --- HTTP-läget (protonmail-mcp --http, http_server.py) ---
    # Bara för klienter som inte kan starta ett lokalt program (Perplexity),
    # bakom Cloudflare Tunnel och Cloudflare Access. Utan --http används
    # inget av detta; stdio är standard.
    # PROTONMAIL_HTTP_TOKEN: API-nyckeln som varje anrop måste ha, som
    # "Authorization: Bearer <nyckel>" eller "X-API-Key: <nyckel>". Krävs i
    # HTTP-läget, minst 32 tecken; utan den startar servern inte.
    http_token: SecretStr | None = None
    # PROTONMAIL_HTTP_HOST: adressen servern lyssnar på. Standard: 127.0.0.1
    # (bara den här datorn; Cloudflare Tunnel ansluter lokalt).
    http_host: str = "127.0.0.1"
    # PROTONMAIL_HTTP_PORT: porten. Standard: 8765.
    http_port: int = Field(default=8765, ge=1, le=65535)
    # PROTONMAIL_HTTP_ALLOWED_HOSTS: kommaseparerade publika värdnamn
    # (protonmail-mcp.exempel.se) som Host-rubriken får ha utöver localhost.
    # Andra värdnamn nekas (skydd mot DNS rebinding). Standard: inga.
    http_allowed_hosts: str = ""

    model_config = SettingsConfigDict(
        env_prefix="PROTONMAIL_",
        # Senare filer vinner: en .env i arbetskatalogen går före projektrotens
        env_file=(PROJECT_ENV_FILE, ".env"),
        # Annars skriver pydantic input_value={'password': ...} i felet,
        # och lifespan loggar det till stderr (som Claude Desktop sparar).
        hide_input_in_errors=True,
    )

    @field_validator("send_mode", "language", mode="before")
    @classmethod
    def _normalize_choice(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("allowed_recipients", mode="after")
    @classmethod
    def _check_allowed_recipients(cls, value: str) -> str:
        parse_allowed_recipients(value)  # ValueError för en felskriven post
        return value

    @field_validator("watch_mailboxes", mode="after")
    @classmethod
    def _check_watch_mailboxes(cls, value: str) -> str:
        parse_watch_mailboxes(value)  # ValueError för en felskriven lista
        return value

    @property
    def watch_mailbox_list(self) -> list[str]:
        """De bevakade mapparna (PROTONMAIL_WATCH_MAILBOXES), tolkade."""
        return list(parse_watch_mailboxes(self.watch_mailboxes))

    @field_validator("audit_log", mode="after")
    @classmethod
    def _check_audit_log(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        value = value.expanduser()
        if not value.is_absolute():
            raise ValueError("PROTONMAIL_AUDIT_LOG måste vara en absolut sökväg (eller börja med ~)")
        return value

    @field_validator("attachment_dir", mode="after")
    @classmethod
    def _expand_home(cls, value: Path) -> Path:
        value = value.expanduser()
        if not value.is_absolute():
            raise ValueError(
                "PROTONMAIL_ATTACHMENT_DIR måste vara en absolut sökväg (eller börja med ~)"
            )
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
