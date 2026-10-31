# protonmail-mcp-server

[![CI](https://github.com/mattias242/protonmail-mcp-server/actions/workflows/ci.yml/badge.svg)](https://github.com/mattias242/protonmail-mcp-server/actions/workflows/ci.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

A [Model Context Protocol](https://modelcontextprotocol.io) server that lets Claude (or any MCP client) read, search, organise, draft and send email through a locally running [ProtonMail Bridge](https://proton.me/mail/bridge).

It is built for being driven by an LLM: every email is treated as untrusted input, nothing is ever marked as read by accident, deletion goes to Trash, drafts are the preferred way to "send", and an operator can restrict what the model may do (read-only mode, draft-only mode, recipient allow-list, rate limits, audit log).

What you get:

- **29 tools** — reading, search and conversations, sending and replying, drafts, attachments, labels and stars, bulk operations, folders, and new-mail checks.
- **MCP resources** for mailboxes and individual emails, with **subscriptions** that notify the client about new mail.
- **Prompts** for common tasks (summarise unread mail, triage, draft a reply, clean up newsletters).
- **New mail via IMAP IDLE** on dedicated connections, without polling.

Tool descriptions, server instructions, prompts and error messages are in Swedish by default; set `PROTONMAIL_LANGUAGE=en` to get them in English. Models handle either language fine, and the tool and parameter names are always English.

What changed in each release is in [CHANGELOG.md](CHANGELOG.md). Upgrading from 0.2.0? Version 0.3.0 has breaking changes (Python 3.12+, `mailbox` required for UID-based tools, new `search_emails` and `list_mailboxes` response formats and more) — read its upgrade notes first.

## Requirements

- [ProtonMail Bridge](https://proton.me/mail/bridge) v3, installed and signed in
- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)

## Installation

There are two ways to install the server. Most people want the first.

| | Install as a tool | Run from a clone |
|---|---|---|
| For | Using the server with Claude | Changing the code, running the tests |
| Command | `protonmail-mcp` on your `PATH` | `uv --directory /path/to/clone run protonmail-mcp` |
| Credentials | In the client's MCP configuration (`env`) | In `.env` in the clone |
| Upgrade | Re-run the install command with the new tag | `git pull && uv sync` |

Before either: install and start [ProtonMail Bridge](https://proton.me/mail/bridge), sign in, and copy the **Bridge password** for your account from the Bridge app (it is not your Proton password).

### Option 1: install as a tool (recommended)

**1. Install** a released version with uv. uv installs it in its own environment with the dependency versions it was tested with, and puts `protonmail-mcp` in `~/.local/bin`:

```bash
uv tool install --python 3.12 "git+https://github.com/mattias242/protonmail-mcp-server@v0.4.0"
```

If uv says `~/.local/bin` is not on your `PATH`, run `uv tool update-shell` and open a new terminal. Check the installation with `uv tool list`; it should show `protonmail-mcp v0.4.0`.

**2. Add it to your client** with your Proton address and the Bridge password. Use the full path to the command (`which protonmail-mcp`, usually `/Users/<you>/.local/bin/protonmail-mcp` on macOS), because Claude Desktop does not read your shell's `PATH`.

Claude Code:

```bash
claude mcp add protonmail -s user \
  -e PROTONMAIL_USERNAME=user@proton.me \
  -e PROTONMAIL_PASSWORD=bridge-generated-password \
  -- /Users/<you>/.local/bin/protonmail-mcp
```

Claude Desktop — add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows), then restart Claude Desktop:

```json
{
  "mcpServers": {
    "protonmail": {
      "command": "/Users/<you>/.local/bin/protonmail-mcp",
      "env": {
        "PROTONMAIL_USERNAME": "user@proton.me",
        "PROTONMAIL_PASSWORD": "bridge-generated-password"
      }
    }
  }
}
```

Any other setting (see [Configuration](#configuration)) goes in the same `env` object, or as another `-e` before `--`. The client stores these values in its configuration file in plain text, readable by your user account.

**3. Check it works:** in Claude Code, run `/mcp` and look for `protonmail` as connected. In either client, ask Claude to list your mail folders; it should call `list_mailboxes` and show the folders from Bridge.

**Upgrading** from an earlier version: run the install command for the new version with `--force`, then restart Claude Desktop, or reconnect the server in Claude Code with `/mcp`. Your client configuration stays as it is.

```bash
uv tool install --force --python 3.12 "git+https://github.com/mattias242/protonmail-mcp-server@v0.4.0"
```

Read the upgrade notes for every version you skip in [CHANGELOG.md](CHANGELOG.md) first. To remove the server: `uv tool uninstall protonmail-mcp` and delete it from the client's configuration.

### Option 2: run from a clone (development)

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

The server reads `.env` from the project root even when it is started from another directory; a `.env` in the current working directory takes precedence, and environment variables override both. Keep `.env` out of version control (it is in `.gitignore`).

Claude Code:

```bash
claude mcp add protonmail -s user -- uv --directory /path/to/protonmail-mcp-server run protonmail-mcp
```

Claude Desktop:

```json
{
  "mcpServers": {
    "protonmail": {
      "command": "uv",
      "args": ["--directory", "/path/to/protonmail-mcp-server", "run", "protonmail-mcp"]
    }
  }
}
```

Replace `/path/to/protonmail-mcp-server` with the absolute path to your clone. If Claude Desktop cannot find `uv`, use its full path (`which uv`) as `"command"`. The server can also be started with `python -m protonmail_mcp` (or `python -m protonmail_mcp.server`) inside the project environment.

To try the server interactively, use the MCP Inspector and open the URL it prints:

```bash
npx @modelcontextprotocol/inspector uv --directory /path/to/protonmail-mcp-server run protonmail-mcp
```

### Option 3: clients that only take a URL (Perplexity)

Some clients cannot start a local program and only connect to an MCP server by URL; the Perplexity app is one. For them the server has an HTTP mode (Streamable HTTP):

```bash
PROTONMAIL_USERNAME=user@proton.me \
PROTONMAIL_PASSWORD=bridge-generated-password \
PROTONMAIL_HTTP_TOKEN="$(openssl rand -hex 32)" \
PROTONMAIL_HTTP_ALLOWED_HOSTS=protonmail-mcp.example.com \
protonmail-mcp --http
```

It listens on `http://127.0.0.1:8765/mcp` and refuses every request without the API key, sent as `Authorization: Bearer <key>` or `X-API-Key: <key>`. It does not start without a key of at least 32 characters. `Host` headers other than localhost and the names in `PROTONMAIL_HTTP_ALLOWED_HOSTS` are refused.

Perplexity calls the server from its cloud, so your mailbox becomes reachable from the internet. Put it behind [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/) and [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/), never a plain port forward:

1. Run `cloudflared` on the same computer as Bridge with an ingress rule from your hostname to `http://127.0.0.1:8765`.
2. In Cloudflare Zero Trust, create a service token and a self-hosted Access application for the hostname whose policy has the action **Service Auth** and includes that token.
3. In Perplexity, add a custom connector: MCP server URL `https://<hostname>/mcp`, transport Streamable HTTP, authentication **API Key** with `PROTONMAIL_HTTP_TOKEN`, and network access **Cloudflare Access** with the service token's client ID and secret.

Anything the model in that client does reaches your real mailbox, and everything it reads is sent to that client's cloud. Consider `PROTONMAIL_SEND_MODE=draft_only` or `PROTONMAIL_READ_ONLY=true` for the HTTP server; it is a separate process with its own settings, so Claude can keep full access.

### Troubleshooting

- **The server does not start, or `/mcp` shows it as failed**: run the command from the client configuration in a terminal with the same `PROTONMAIL_*` variables. The error is printed on stderr (for example a missing `PROTONMAIL_USERNAME`).
- **"Kan inte nå ProtonMail Bridge"** (cannot reach Bridge): Bridge is not running or not signed in, or `PROTONMAIL_IMAP_HOST`/`PROTONMAIL_IMAP_PORT` do not match the ports shown in Bridge's settings (default IMAP 1143, SMTP 1026).
- **Login fails**: use the password Bridge shows for the account, not your Proton password.
- **`ModuleNotFoundError: mcp.server.fastmcp`**: an old version (0.3.0) installed against mcp 2.x. Upgrade as above.

## Tools

UIDs are per mailbox (see [Security model](#security-model)). Every tool that takes a `uid` also requires the `mailbox` the UID came from; listing and search tools default to `INBOX`. Tools marked "up to 100 UIDs" accept a single UID or a list and return `{"ok": [...], "failed": [{"uid", "error"}]}` for a list.

Effect is taken from each tool's MCP annotations: **read-only** changes nothing; **writes** changes the mailbox (or, for `get_attachment`, a local file) but loses nothing; **destructive** moves, removes or replaces something; **sends** delivers email to other people and cannot be undone.

### Reading

| Tool | What it does | Effect |
|------|--------------|--------|
| `list_mailboxes` | All folders and labels with `name`, `type` (`system`, `folder`, `label`, `container`), `selectable` and `special_use` | read-only |
| `get_mailbox_status` | Number of messages and unread messages in a folder | read-only |
| `list_emails` | Metadata for the emails in a folder, newest first, paginated (`page`, `page_size` 1–100) | read-only |
| `get_email` | One email: decoded headers, body (`body_format` `text`/`stripped`/`full`, `max_length`), attachment list, plus the safety fields described below | read-only |
| `get_email_headers` | Raw header values (not RFC 2047-decoded) without the body — fast | read-only |

### Search and conversations

| Tool | What it does | Effect |
|------|--------------|--------|
| `search_emails` | Filter by sender, recipient, Cc, subject, date range, full text, body, unread, starred, answered, size and attachments (all filters combined with AND); returns metadata, newest first, up to 200 hits | read-only |
| `get_thread` | The whole conversation an email belongs to, rebuilt from `Message-ID`/`In-Reply-To`/`References` across All Mail (received and sent) | read-only |

### Sending and replying

| Tool | What it does | Effect |
|------|--------------|--------|
| `send_email` | Send a new email (to, cc, bcc, reply-to, optional HTML, attachments from the attachment directory) | **sends** |
| `reply_to_email` | Reply (or reply all) with `Re:`, `In-Reply-To`, `References` and the original quoted | **sends** |
| `forward_email` | Forward with `Fwd:`, the original inline (text and HTML) and all its attachments | **sends** |

### Drafts

Drafts are the recommended workflow: the model writes, a human reviews in Proton (or approves `send_draft`).

| Tool | What it does | Effect |
|------|--------------|--------|
| `create_draft` | Save a new email as a draft in Drafts — built exactly like `send_email`, nothing is sent | writes |
| `create_reply_draft` | Save a reply as a draft (same recipients, threading and quoting as `reply_to_email`) | writes |
| `create_forward_draft` | Save a forward as a draft (same content and attachments as `forward_email`) | writes |
| `update_draft` | Replace a draft with a new version; keeps threading headers and attachments, moves the old version to Trash | destructive |
| `send_draft` | Send a draft exactly as it is stored in Drafts (including edits made in Proton) to the recipients `get_email` shows, then remove it | **sends** |

### Attachments

| Tool | What it does | Effect |
|------|--------------|--------|
| `get_attachment` | Save an attachment to the attachment directory (`save=true`, default) or return it inline as text/base64 up to `PROTONMAIL_ATTACHMENT_MAX_INLINE_BYTES`; the mailbox is not changed | writes |

Files to *send* are given as names inside the attachment directory via the `attachments` parameter of `send_email`, `reply_to_email` and `forward_email` (at most 20 files and 25 MB in total).

### Managing, labels and bulk operations

| Tool | What it does | Effect |
|------|--------------|--------|
| `mark_email_read` | Mark as read — up to 100 UIDs | writes |
| `mark_email_unread` | Mark as unread — up to 100 UIDs | writes |
| `flag_email` | Star (sets `\Flagged`, which is Proton's Starred) — up to 100 UIDs | writes |
| `unflag_email` | Remove the star — up to 100 UIDs | writes |
| `add_label` | Add a label (`Labels/…`); the email stays in its folder — up to 100 UIDs | writes |
| `remove_label` | Remove a label without touching the email otherwise — up to 100 UIDs | destructive |
| `move_email` | Move to another folder (INBOX, Archive, Spam, Trash, `Folders/…`) — up to 100 UIDs | destructive |
| `delete_email` | Move to Trash; `permanent=true` deletes for good, only from Trash (or Drafts) — up to 100 UIDs | destructive |

### Folders

| Tool | What it does | Effect |
|------|--------------|--------|
| `create_folder` | Create a folder (`Folders/Name`) or label (`Labels/Name`) — the prefix is required | writes |
| `delete_folder` | Delete your own folder or label; refused if it holds mail or subfolders unless `force=true` | destructive |
| `rename_folder` | Rename your own folder or label (same prefix on both names) | destructive |

### New mail

| Tool | What it does | Effect |
|------|--------------|--------|
| `check_new_emails` | Emails that arrived since the previous call (a per-folder high-water mark) or since `since_uid`; returns immediately | read-only |
| `wait_for_new_email` | Wait up to `timeout_seconds` (1–300) for the next email; returns at once if mail is already waiting | read-only |

Watched folders (`PROTONMAIL_WATCH_MAILBOXES`, default `INBOX`) are answered from an in-memory event queue fed by IMAP IDLE (`"source": "watcher"`). Other folders are checked with a UID search on demand (`"source": "imap"`); there, the first call for a folder only sets the high-water mark. High-water marks live in the server process and start over when it restarts. If Bridge resyncs a folder (new UIDVALIDITY), the next call says so in `note`, and for a watched folder it shows the emails that arrived during the outage, selected by arrival time. If Bridge hangs, `wait_for_new_email` gives up a few seconds after `timeout_seconds` with an error instead of waiting forever.

## Resources and subscriptions

The mailbox is also exposed as read-only MCP resources, as JSON. They go through the same code as the corresponding tools, so validation, sanitising, untrusted-content marking and error messages are identical, and nothing is marked as read.

| URI | Content |
|-----|---------|
| `mailboxes://` | All folders and labels (like `list_mailboxes`), each with the `uri` of its mailbox resource |
| `mailbox://{mailbox}` | Message and unread counts plus metadata for the 20 newest emails, each with the `uri` of its email resource |
| `email://{mailbox}/{uid}` | One email as text (like `get_email` with `body_format="text"` and `max_length=20000`) |

**Mailbox names in URIs are percent-encoded as UTF-8** (RFC 3986, the same as RFC 6570 expansion of `{mailbox}`): everything except `A–Z a–z 0–9 - . _ ~` is encoded, including `/` and spaces. `Labels/Näs Samfällighet` becomes `mailbox://Labels%2FN%C3%A4s%20Samf%C3%A4llighet`, and email 42 in `Folders/A/B` is `email://Folders%2FA%2FB/42`. When reading, unencoded `/` (`mailbox://Folders/A/B`) and lower-case percent codes are accepted too.

**Subscriptions.** The server supports `resources/subscribe` (MCP protocol versions before 2026-07-28, which Claude Code and Claude Desktop use) and sends `notifications/resources/updated` in two cases:

1. **New mail in a watched folder** (`PROTONMAIL_WATCH_MAILBOXES`, default `INBOX`): the IDLE watcher notifies `mailbox://{mailbox}`.
2. **After a successful write tool in this server**, for the resources that call changed:
   - marking read/unread, starring and labels: the folder and `email://{mailbox}/{uid}` (labels also the label's `mailbox://`)
   - `move_email`: source folder, target folder and the email; `delete_email`: the folder, the email and Trash (unless `permanent=true`)
   - `create_folder`, `delete_folder`, `rename_folder`: `mailboxes://` and the affected `mailbox://`
   - draft tools: `mailbox://Drafts` (`update_draft` also the old draft and Trash, `send_draft` the old draft and Sent)
   - `send_email`, `reply_to_email`, `forward_email`: `mailbox://Sent`

   With a list of UIDs only the UIDs that succeeded are notified; a failed call notifies nothing. Notifications are sent in the background, so a tool's answer never waits for them.

Clients on protocol version 2026-07-28 use `subscriptions/listen`, which the server does not support yet; it advertises no subscriptions to them. Tools, resources and prompts work on both protocol versions.

**Changes made by other clients** (Proton's apps, the web, another IMAP client) are only seen as new mail in watched folders; flags, moves and folder changes made elsewhere produce no notification. Subscriptions are accepted on every valid resource URI and compared in canonical form, so `mailbox://Folders/A/B` and `mailbox://Folders%2FA%2FB` are the same resource, and `INBOX` is case-insensitive.

## Prompts

| Prompt | Arguments | What it does |
|--------|-----------|--------------|
| `sammanfatta_olasta` | `mailbox` (default INBOX), `max_antal` | Summarise the unread emails in a folder without marking them as read |
| `triagera_inkorgen` | `mailbox` | Go through a folder and suggest actions (archive, label, reply) — nothing is done without confirmation |
| `svara_pa_mejl` | `uid`, `mailbox`, `ton` (tone) | Read an email and its thread and write a reply **draft** (not sent) |
| `rensa_nyhetsbrev` | `mailbox` | Find newsletters and suggest unsubscribing or archiving — confirmation required |

Every prompt repeats the safety rules: UIDs are per mailbox, email content is untrusted, prefer drafts, at most 100 UIDs per bulk call. The prompt texts follow `PROTONMAIL_LANGUAGE`, like the tool descriptions.

## Configuration

All settings use the `PROTONMAIL_` prefix and can be set in `.env` or as environment variables. Invalid values stop the server at start-up with an error that names the setting (the password is never echoed).

| Variable | Default | Description |
|----------|---------|-------------|
| `PROTONMAIL_USERNAME` | required | Your Proton email address (also the sender of everything sent) |
| `PROTONMAIL_PASSWORD` | required | The Bridge-generated password, not your Proton password |
| `PROTONMAIL_IMAP_HOST` | `127.0.0.1` | Bridge IMAP host |
| `PROTONMAIL_IMAP_PORT` | `1143` | Bridge IMAP port (plain TCP on localhost) |
| `PROTONMAIL_SMTP_HOST` | `127.0.0.1` | Bridge SMTP host |
| `PROTONMAIL_SMTP_PORT` | `1026` | Bridge SMTP port (direct TLS) |
| `PROTONMAIL_IMAP_TIMEOUT` | `30` | Seconds per IMAP command (> 0). A search in All Mail can take 5–9 s on a large account |
| `PROTONMAIL_VERIFY_SSL` | `false` | Verify the SMTP TLS certificate (Bridge uses a self-signed one) |
| `PROTONMAIL_SMTP_CA_CERT` | — | Path to the exported Bridge certificate; with `VERIFY_SSL=true` it pins that certificate |
| `PROTONMAIL_ATTACHMENT_DIR` | `~/Downloads/protonmail-mcp` | The only directory attachments are saved to or sent from. Must be absolute (or start with `~`); created with mode 0700 at start-up |
| `PROTONMAIL_ATTACHMENT_MAX_INLINE_BYTES` | `1048576` | Largest attachment (bytes, 1 MiB) that `get_attachment(save=false)` returns inline |
| `PROTONMAIL_READ_ONLY` | `false` | `true` refuses everything that changes the account: moving, deleting, flags, labels, folders, drafts and sending |
| `PROTONMAIL_SEND_MODE` | `send` | `send`; `draft_only` refuses `send_email`, `reply_to_email`, `forward_email` and `send_draft` but allows drafts; `disabled` refuses drafts too |
| `PROTONMAIL_LANGUAGE` | `sv` | Language of everything the client shows: tool titles and descriptions, prompts, resources, server instructions and error messages. `sv` (Swedish) or `en` (English). Tool and parameter names never change; errors raised before the settings are read (an invalid setting, HTTP start-up) are Swedish |
| `PROTONMAIL_ALLOWED_RECIPIENTS` | — | Comma-separated addresses (`anna@example.com`) and/or domains (`@example.com`). When set, every To/Cc/Bcc recipient of every send must match, or nothing is sent. Domains are case-insensitive and compared as punycode; a domain does not cover its subdomains. Empty = everyone |
| `PROTONMAIL_MAX_RECIPIENTS` | `50` | Maximum unique recipients per send (To, Cc and Bcc together); `0` = no limit. More than 1000 address entries are always refused |
| `PROTONMAIL_MAX_SENDS_PER_HOUR` | `30` | Maximum sends in a sliding hour, counted in memory (reset on restart); `0` = no limit. The error says when the next send is possible |
| `PROTONMAIL_AUDIT_LOG` | — | File (absolute path or `~`) that also receives the audit log, one JSON line per send and per write operation |
| `PROTONMAIL_WATCH_ENABLED` | `true` | Watch folders for new mail with IMAP IDLE |
| `PROTONMAIL_WATCH_MAILBOXES` | `INBOX` | Comma-separated folders to watch, at most 5 (each is its own Bridge connection) |
| `PROTONMAIL_HTTP_TOKEN` | — | HTTP mode only (`--http`): the API key every request must carry, as `Authorization: Bearer` or `X-API-Key`. Required there, at least 32 characters |
| `PROTONMAIL_HTTP_HOST` | `127.0.0.1` | HTTP mode only: address to listen on |
| `PROTONMAIL_HTTP_PORT` | `8765` | HTTP mode only: port to listen on |
| `PROTONMAIL_HTTP_ALLOWED_HOSTS` | — | HTTP mode only: comma-separated public host names (no scheme or port) accepted in the `Host` header besides localhost; others are refused |

## Security model

**Email content is untrusted.** Subjects, display names, bodies, attachment names and attachment contents are written by whoever sent the email, and a model reading them can be tricked into acting on instructions hidden there (prompt injection). The server reduces the risk but cannot remove it: keep a human in the loop for anything that sends, and prefer the draft tools. The server's instructions and every prompt tell the model to follow only the user's own instructions, never instructions found in an email.

**UIDs are per mailbox.** The same number in another folder is a different email. Every tool that reads, changes or sends based on a UID therefore requires `mailbox`, and every response that contains a UID also says which mailbox it belongs to. Tools validate that the UID exists in that mailbox before changing anything.

**Nothing is marked as read by reading.** All fetches use `BODY.PEEK`.

**Deleting is recoverable by default.** `delete_email` moves to Trash, like Proton's own apps. Permanent deletion needs `permanent=true` and `mailbox="Trash"` (or Drafts), and expunges only that UID. Moves that Bridge would turn into data loss are refused before anything is sent: source and target the same folder, moves to or from All Mail, INBOX ↔ Sent, Starred as a target, and moving or deleting to Trash/Drafts from a label view.

**Input validation.** Mailbox names (1–255 characters, no control characters) are always quoted and modified-UTF-7 encoded; UIDs must be digits; search values may not contain control characters and are quoted. Addresses are checked for header injection and for hidden characters (see below) before anything is sent.

**Protection against prompt injection.**
- *Sanitising:* invisible and misleading characters are removed from subjects, names, bodies, HTML text and attachment names — Unicode tag characters ("ASCII smuggling"), zero-width characters, bidi controls ("Trojan Source"), variation selectors (except emoji presentation), other format and control characters, and long runs of combining marks. `sanitized` reports how many characters were removed per field.
- *Addresses are never altered:* invisible characters in an address are shown as visible codes such as `[U+200B]`, and sending to such an address (or to anything containing `[U+`) is refused.
- *Marking:* `get_email`, `get_email_headers`, `get_attachment(save=false)` and the email resource carry an `untrusted_content_notice`; `Message-ID`, `In-Reply-To` and `References` are shown only as ASCII `<msg-id>`s.
- *Flagging:* `suspicious_content` lists fixed warnings (never the sender's text) when it finds a typical injection phrase (English and Swedish) in the visible or hidden text, names or attachment names, removed tag or bidi characters, invisible characters in an address, HTML that could not be parsed safely, or — with `body_format="full"` — hidden elements in the raw HTML. List and search rows get it too. It is a heuristic warning, not a filter.
- *Hidden HTML:* text the reader would not see — `display:none`, `visibility:hidden`, zero font size, transparent or background-coloured text, off-screen positioning, near-zero opacity, clipping, and simple `<style>` rules — is left out of the text the model gets; `hidden_html` counts it. Raw HTML is only returned with `body_format="full"`. The HTML parser is fail-closed: if it cannot interpret something safely it returns less text, never more.

**Attachment directory.** All file access for attachments stays in `PROTONMAIL_ATTACHMENT_DIR`. The directory is refused if it is a symlink, lies under a symlink (except system ones owned by root), is owned by someone else or is writable by others. Sender-supplied file names are reduced to one clean name; existing files are never overwritten. Files to send must resolve inside the directory, are opened without following symlinks and re-checked against what was validated; hard links, non-regular files and anything that looks like a secret (`.ssh`, `.gnupg`, `.aws`, `.netrc`, `.env*`, `id_*` keys, `*.pem`, `*.key` other than Keynote files, keychains, or any file containing a private key) are refused. Saved files get the `com.apple.quarantine` attribute on macOS.

**Send policy and audit log.** `PROTONMAIL_READ_ONLY`, `PROTONMAIL_SEND_MODE`, `PROTONMAIL_ALLOWED_RECIPIENTS`, `PROTONMAIL_MAX_RECIPIENTS` and `PROTONMAIL_MAX_SENDS_PER_HOUR` are enforced centrally in three places: the MCP server refuses a tool by name before it runs, every IMAP write is wrapped in a policy check, and every send goes through a single SMTP delivery path that checks the policy before contacting Bridge. A refusal is an error that reaches the client; nothing is sent. Every send and every write operation (including refused ones) produces an audit line with time, tool, action, mailbox, UIDs, recipients, Message-ID and result — never subject, body, attachments or password. On stderr the recipients are replaced by a count and addresses in error texts are masked; the full line goes to `PROTONMAIL_AUDIT_LOG` if set (created with mode 0600, never written through a symlink). If Bridge accepts only some recipients, the error says who got the email and who did not, so the model does not resend to everyone.

**HTTP mode.** Off unless started with `--http`. Every request needs the API key (compared in constant time); a refused request gets 401 without reaching the MCP server, and the log names the headers it had, never their values. The server listens on localhost by default and checks the `Host` header against localhost and `PROTONMAIL_HTTP_ALLOWED_HOSTS` (DNS rebinding protection). Reaching it from the internet is meant to go through Cloudflare Tunnel and Cloudflare Access; the API key still protects the mailbox if the Access rule is missing.

**Secrets.** The Bridge password is masked in all errors and logs, the HTTP API key is never logged, and configuration errors never echo input values.

## Proton Bridge semantics in brief

- **Folders vs labels.** INBOX, Archive, Sent, Spam, Trash, Drafts and `Folders/…` are folders: an email is in exactly one. `Labels/…` are labels: an email can have many. Use `move_email` for folders and `add_label`/`remove_label` for labels. `Folders` and `Labels` themselves are containers (`selectable: false`). Sub-folders (`Folders/A/B`) need the parent to exist; sub-labels do not exist. A `/` inside a label name is escaped as `\/`.
- **Starred is a flag.** Starring sets `\Flagged`, which is the same as the Starred view everywhere; use `flag_email`/`unflag_email`, not a move.
- **All Mail** contains every email. Moves to or from it are refused; `get_thread` searches there, so its UIDs are All Mail UIDs.
- **Counts overlap.** `get_mailbox_status` counts per view, and because labels overlap, sums across folders double-count.
- **Searching with å, ä, ö.** Bridge only matches undecoded headers and stores text parts quoted-printable, so `subject`, `from_addr`, `to_addr` and `cc_addr` are matched locally against decoded headers (for the 1000 newest candidates; `complete` and `note` say when not everything was checked), and `text`/`body` words with non-ASCII letters are also searched in their quoted-printable form.
- **Drafts.** A draft created here is a real Proton draft that you can edit and send in Proton's apps. When `send_draft` sends it, Bridge files the sent email in Sent and the draft is removed.
- **Mailbox names** are shown decoded (å, ä, ö); pass them back exactly as `list_mailboxes` returns them.

## Testing

```bash
uv run pytest
```

runs the unit and BDD tests (`tests/unit`, `tests/features`). They never contact Bridge: connections to ports 1143 and 1026 are blocked in every non-integration test.

Integration tests talk to a running Bridge and are only run on request:

```bash
uv run pytest -m integration
```

They need `.env` (or `PROTONMAIL_USERNAME`/`PROTONMAIL_PASSWORD`) and only read: LIST, STATUS, SELECT, SEARCH and `BODY.PEEK` fetches. The one test that sends a real email (`tests/integration/test_smtp.py`, to your own address) is skipped unless you also set `PROTONMAIL_ALLOW_SEND=1`.

CI runs the unit and BDD tests on Python 3.12, 3.13 and 3.14.

## Known limitations

- Body search (`text`/`body`) with å, ä, ö finds about 95 % of matches: a word that quoted-printable happens to wrap mid-word is missed, as are words in base64-encoded parts or written as HTML entities (`&ouml;`).
- Validation errors for missing or wrongly typed parameters come from pydantic, in English, before the tool runs; all other errors are in Swedish.
- Homoglyphs (e.g. Cyrillic `а` for Latin `a`) are visible characters and are not removed, and `suspicious_content` does not catch every phrasing — it is a warning, not a guarantee.
- Notifications for changes made by other clients only exist as new mail in watched folders (`PROTONMAIL_WATCH_MAILBOXES`, at most 5). Flags, labels, moves and folder changes made outside this server are not reported, and `rename_folder` does not notify sub-folders renamed along with their parent.
- High-water marks for new mail and the hourly send counter are kept in memory and reset when the server restarts. If a `check_new_emails` call is interrupted after the mark moved but before the client got the answer, those emails no longer count as new (they are still listed by `list_emails`).
- `get_thread` only follows `Message-ID`/`In-Reply-To`/`References`; a reply without them is not part of the thread. Each search in All Mail takes a few seconds, so one call is capped at ten searches, 200 IDs and 60 s.
- What Proton does with sub-folders when a folder is deleted with `force=true` is unknown.

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE).
