# protonmail-mcp

MCP-server för ProtonMail via lokal Bridge. Låter Claude läsa, söka och skicka e-post direkt i din ProtonMail-brevlåda.

## Krav

- [ProtonMail Bridge](https://proton.me/mail/bridge) installerad och inloggad
- Python 3.10+
- [uv](https://docs.astral.sh/uv/)

## Installation

```bash
git clone <repo>
cd protonmailmcp
uv sync
cp .env.example .env
```

Fyll i `.env`:

```bash
PROTONMAIL_USERNAME=user@proton.me
PROTONMAIL_PASSWORD=bridge-generated-password  # från Bridge-appen, inte ditt vanliga lösenord
```

## Använda med Claude Desktop

Lägg till i `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "protonmail": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/protonmailmcp", "protonmail-mcp"],
      "env": {
        "PROTONMAIL_USERNAME": "user@proton.me",
        "PROTONMAIL_PASSWORD": "bridge-password"
      }
    }
  }
}
```

## Tillgängliga verktyg

| Verktyg | Beskrivning |
|---------|-------------|
| `list_mailboxes` | Lista alla mappar/etiketter |
| `list_emails` | Lista e-post med metadata (paginerat) |
| `get_email` | Hämta fullständigt e-post via UID |
| `search_emails` | Sök med from/subject/datum/oläst-filter |
| `send_email` | Skicka e-post |
| `mark_email_read` | Markera som läst |
| `mark_email_unread` | Markera som oläst |
| `move_email` | Flytta till annan mapp |
| `delete_email` | Ta bort e-post |
| `get_mailbox_status` | Räkna meddelanden/olästa |

## Testa

```bash
npx @modelcontextprotocol/inspector uv run protonmail-mcp
```

Öppna `http://localhost:6274` → klicka Connect → Tools.

## Tekniska detaljer

Bridge v3 på macOS exponerar:
- IMAP på `127.0.0.1:1143` — plain TCP (STARTTLS tillgängligt men ej obligatoriskt)
- SMTP på `127.0.0.1:1026` — direkt TLS med self-signed cert

IMAP-anslutningen är persistent (en per serversession). SMTP öppnas per utskick.
