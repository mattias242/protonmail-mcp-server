# protonmail-mcp

MCP-server som exponerar ProtonMail Bridge (IMAP/SMTP på localhost) som MCP-verktyg.

## Köra lokalt

```bash
uv sync
cp .env.example .env   # fyll i username + password
uv run protonmail-mcp  # stdio-transport
```

## Testa med Inspector

```bash
npx @modelcontextprotocol/inspector uv run protonmail-mcp
```

## Bridge-anslutning

ProtonMail Bridge v3 på macOS:
- **IMAP port 1143** — plain TCP (inte SSL). Bridge erbjuder STARTTLS men kräver det ej.
- **SMTP port 1026** — direkt SSL med self-signed cert (`CERT_NONE` + `check_hostname=False`).

Försök att ansluta med `IMAP4_SSL` mot port 1143 ger `SSL: RECORD_LAYER_FAILURE`.

## Kända begränsningar

- `aioimaplib` 2.0.1 stödjer inte `uid("search")` — använd `search()` + `fetch()` för att hämta UID:n.
- `search("ALL")` på stora brevlådor (>1000 meddelanden) orsakar rekursionsfel — `list_messages` använder `SELECT EXISTS` + direktintervall.
- `BODY[]` (ej PEEK) markerar meddelanden som lästa — använd alltid `BODY.PEEK[]` för läsoperationer.

## Projektstruktur

```
src/protonmail_mcp/
├── server.py        # FastMCP + lifespan (lazy IMAP-anslutning)
├── config.py        # pydantic-settings, prefix PROTONMAIL_
├── imap_client.py   # Persistent IMAP4 (plain), UID-baserat
├── smtp_client.py   # Per-send SMTP med direkt SSL
├── email_parser.py  # RFC822 → dict, body_format, max_length
└── tools/           # 16 MCP-verktyg
    ├── mailboxes.py # list_mailboxes, get_mailbox_status
    ├── messages.py  # list_emails, get_email, get_email_headers
    ├── search.py    # search_emails
    ├── send.py      # send_email
    ├── reply.py     # reply_to_email, forward_email
    ├── manage.py    # mark_read/unread, move_email, delete_email
    └── folders.py   # create_folder, delete_folder, rename_folder
```

## Arbetssätt — TDD + BDD

Vi följer strikt **RED → GREEN → BLUE**-cykeln för varje ändring:

1. **RED** — Skriv ett misslyckat test som beskriver önskat beteende. Verifiera att det misslyckas.
2. **GREEN** — Skriv minimal kod för att få testet att gå igenom. Commita vid GREEN.
3. **BLUE** (refactor) — Städa upp utan att ändra beteende. Alla tester gröna efter refactor.

**Commit vid varje GREEN.**

### Teststruktur

```
tests/
├── unit/          # Snabba enhetstester (ingen Bridge-anslutning) — 155 tester
├── integration/   # Tester mot riktig Bridge (kräver .env, markerade integration)
└── features/      # BDD-scenarion (pytest-bdd)
```

### Köra tester

```bash
uv run pytest tests/unit/ tests/features/   # snabba, alltid — 155 tester
uv run pytest tests/integration/ -m integration  # kräver Bridge igång
uv run pytest                                # allt
```

## Säkerhet

- UID-parametrar valideras mot `^\d+$` (förhindrar sequence-set-injektion)
- Mailbox-parametrar valideras mot blocklist-regex (förhindrar IMAP-injektion)
- IMAP search-strängar escapas per RFC 3501
- E-postadresser och ämnesrad valideras för CRLF-injektion
- Credentials läses från `.env` eller miljövariabler — lägg aldrig `.env` i git
- `PROTONMAIL_SMTP_CA_CERT` kan peka på Bridge-certifikat för cert pinning

## API-design

### Pagination
`list_emails` använder page-baserad pagination:
```python
list_emails(mailbox="INBOX", page=1, page_size=20)
# → {"messages": [...], "total": 42, "page": 1, "pages": 3, "has_more": True}
```

### Mailboxes med type
`list_mailboxes` returnerar type-fält:
```json
[{"name": "INBOX", "type": "folder"}, {"name": "Labels/Arbete", "type": "label"}]
```

### body_format i get_email
```python
get_email(uid="123", body_format="text", max_length=500)
# body_format: "full" | "text" | "stripped"
# max_length: int (trunkerar med hint) | None (ingen trunkering)
```

### search_emails returnerar metadata
```python
search_emails(mailbox="INBOX", unseen=True)
# → [{"uid": "123", "from": "...", "subject": "...", "date": "...", "flags": []}]
```

### ISO 8601-datum
`search_emails` accepterar `since`/`before` i formaten YYYY-MM-DD eller DD-Mon-YYYY.

## Claude Desktop-konfiguration

```json
{
  "mcpServers": {
    "protonmail": {
      "command": "uv",
      "args": ["run", "--directory", "/Users/mattiaswahlberg/ai/protonmailmcp", "protonmail-mcp"],
      "env": {
        "PROTONMAIL_USERNAME": "user@proton.me",
        "PROTONMAIL_PASSWORD": "bridge-password"
      }
    }
  }
}
```
