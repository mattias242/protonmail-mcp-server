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
cd protonmail-mcp-server
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
      "args": ["run", "--directory", "/path/to/protonmail-mcp-server", "protonmail-mcp"],
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
claude mcp add protonmail -s user -- uv --directory /path/to/protonmail-mcp-server run protonmail-mcp
```

Then set credentials in `~/.claude.json` under the `protonmail` entry's `env` field.

## Available tools (16)

### Reading
| Tool | Description |
|------|-------------|
| `list_mailboxes` | List all folders and labels (with `type: folder\|label`) |
| `get_mailbox_status` | Count messages and unread in a folder |
| `list_emails` | List emails with metadata — paginated (`page`, `page_size`, `has_more`) |
| `get_email` | Fetch full email — supports `body_format` (full/text/stripped) and `max_length` |
| `get_email_headers` | Fetch headers only, no body (fast) |
| `search_emails` | Search by from/subject/date/unread — returns metadata, accepts ISO 8601 dates |

### Sending
| Tool | Description |
|------|-------------|
| `send_email` | Send an email (to, subject, body, cc, bcc, reply_to) |
| `reply_to_email` | Reply — sets Re: prefix, In-Reply-To and References headers automatically |
| `forward_email` | Forward — sets Fwd: prefix and quotes original |

### Management
| Tool | Description |
|------|-------------|
| `mark_email_read` | Mark as read |
| `mark_email_unread` | Mark as unread |
| `move_email` | Move to another folder |
| `delete_email` | Delete an email |

### Folders
| Tool | Description |
|------|-------------|
| `create_folder` | Create a new folder |
| `delete_folder` | Delete a folder (must be empty) |
| `rename_folder` | Rename a folder |

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

## Configuration

All settings use the `PROTONMAIL_` prefix and can be set in `.env` or as environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `PROTONMAIL_USERNAME` | — | Your Proton email address |
| `PROTONMAIL_PASSWORD` | — | Bridge-generated password |
| `PROTONMAIL_IMAP_HOST` | `127.0.0.1` | IMAP host |
| `PROTONMAIL_IMAP_PORT` | `1143` | IMAP port |
| `PROTONMAIL_SMTP_HOST` | `127.0.0.1` | SMTP host |
| `PROTONMAIL_SMTP_PORT` | `1026` | SMTP port |
| `PROTONMAIL_VERIFY_SSL` | `false` | Enable SSL verification |
| `PROTONMAIL_SMTP_CA_CERT` | — | Path to Bridge cert for pinning |

## Technical notes

ProtonMail Bridge v3 exposes:
- IMAP on `127.0.0.1:1143` — plain TCP
- SMTP on `127.0.0.1:1026` — direct TLS with self-signed certificate

The IMAP connection is persistent (one per server session) with automatic reconnect on timeout. SMTP connects per send.

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE).
