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

Försök att ansluta med `IMAP4_SSL` mot port 1143 ger `SSL: RECORD_LAYER_FAILURE` (Bridge skickar plaintext-hello, inte SSL-handshake).

## Kända begränsningar

- `aioimaplib` 2.0.1 stödjer inte `uid("search")` — använd `search()` + `fetch()` för att hämta UID:n.
- `search("ALL")` på stora brevlådor (>1000 meddelanden) orsakar rekursionsfel i aioimaplibens parser — `list_messages` använder istället `SELECT EXISTS` + direktintervall.
- `body_plain` är tom för HTML-only-mail — det är korrekt beteende.

## Projektstruktur

```
src/protonmail_mcp/
├── server.py        # FastMCP + lifespan (lazy IMAP-anslutning)
├── config.py        # pydantic-settings, prefix PROTONMAIL_
├── imap_client.py   # Persistent IMAP4 (plain), UID-baserat
├── smtp_client.py   # Per-send SMTP med direkt SSL
├── email_parser.py  # RFC822 → dict
└── tools/           # 10 MCP-verktyg
```

## Säkerhet

- UID-parametrar valideras mot `^\d+$` (förhindrar sequence-set-injektion)
- IMAP search-strängar escapas per RFC 3501 (förhindrar quoted-string-injektion)
- Credentials läses från `.env` eller miljövariabler — lägg aldrig `.env` i git

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
