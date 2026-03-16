# protonmail-mcp-server

[![CI](https://github.com/mattias242/protonmail-mcp-server/actions/workflows/ci.yml/badge.svg)](https://github.com/mattias242/protonmail-mcp-server/actions/workflows/ci.yml)

A [Model Context Protocol](https://modelcontextprotocol.io) server that lets Claude read, search, and send email through a locally running [ProtonMail Bridge](https://proton.me/mail/bridge).

## Requirements

- [ProtonMail Bridge](https://proton.me/mail/bridge) installed and signed in
- Python 3.10+
- [uv](https://docs.astral.sh/uv/)

## Installation

```bash
git clone https://github.com/mattias242/protonmail-mcp-server
cd protonmail-mcp
uv sync
cp .env.example .env
```

Edit `.env`:

```bash
PROTONMAIL_USERNAME=user@proton.me
PROTONMAIL_PASSWORD=bridge-generated-password  # from the Bridge app, not your Proton password
```

## Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

```json
{
  "mcpServers": {
    "protonmail": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/protonmail-mcp", "protonmail-mcp"],
      "env": {
        "PROTONMAIL_USERNAME": "user@proton.me",
        "PROTONMAIL_PASSWORD": "bridge-password"
      }
    }
  }
}
```

## Claude Code

```bash
claude mcp add protonmail -s user -- uv --directory /path/to/protonmail-mcp run protonmail-mcp
```

Then set credentials in `~/.claude.json` under the `protonmail` entry's `env` field.

## Available tools

| Tool | Description |
|------|-------------|
| `list_mailboxes` | List all folders/labels |
| `list_emails` | List emails with metadata (paginated) |
| `get_email` | Fetch full email content by UID |
| `search_emails` | Search by from/subject/date/unread |
| `send_email` | Send an email |
| `mark_email_read` | Mark as read |
| `mark_email_unread` | Mark as unread |
| `move_email` | Move to another folder |
| `delete_email` | Delete an email |
| `get_mailbox_status` | Count messages and unread |

## Testing

```bash
# Unit and BDD tests (no Bridge required)
uv run pytest tests/unit/ tests/features/ -v

# Integration tests (requires running Bridge + .env)
uv run pytest tests/integration/ -m integration -v
```

Or test interactively with the MCP Inspector:

```bash
npx @modelcontextprotocol/inspector uv run protonmail-mcp
```

Open `http://localhost:6274` → Connect → Tools.

## Technical notes

ProtonMail Bridge v3 exposes:
- IMAP on `127.0.0.1:1143` — plain TCP
- SMTP on `127.0.0.1:1026` — direct TLS with self-signed certificate

The IMAP connection is persistent (one per server session). SMTP connects per send.

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE).
