# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the version is 0.x, a minor release may contain breaking changes; they are always listed first.

## [Unreleased]

### Added

- **`PROTONMAIL_ALLOW_DESTRUCTIVE=false` hides and refuses moving, deleting, renaming and label removal** (`move_email`, `delete_email` including `permanent=true`, `delete_folder`, `rename_folder`, `remove_label`): the tools are left out of the tool list, so the model never sees them, and refused if a client calls them by name. Reading, read/unread, stars, `add_label`, `create_folder`, drafts and sending (per `PROTONMAIL_SEND_MODE`) keep working; `update_draft` may still move its old version to Trash. Together with `PROTONMAIL_SEND_MODE=draft_only` this gives an unattended agent read and draft access without irreversible changes, which `draft_only` alone did not: it only stops sending. The refusal is enforced both before the tool runs and in the IMAP write functions, and is audited like other refusals. The server's instructions and the `triagera_inkorgen`/`rensa_nyhetsbrev` prompts still mention `move_email`/`delete_email`; with the tools hidden the model simply cannot use them. Default `true`, so nothing changes unless set. Fixes [#5](https://github.com/mattias242/protonmail-mcp-server/issues/5). Implemented by Claude (Anthropic).
- **HTTP mode for clients that only take a URL (Perplexity):** `protonmail-mcp --http` serves Streamable HTTP on `http://127.0.0.1:8765/mcp`. Every request needs the API key in `PROTONMAIL_HTTP_TOKEN` (at least 32 characters, as `Authorization: Bearer` or `X-API-Key`); without it the server does not start. New settings `PROTONMAIL_HTTP_TOKEN`, `PROTONMAIL_HTTP_HOST`, `PROTONMAIL_HTTP_PORT` and `PROTONMAIL_HTTP_ALLOWED_HOSTS`. The README describes running it behind Cloudflare Tunnel and Cloudflare Access. Without `--http` nothing changes: stdio is still the default, and unknown command-line arguments are still ignored.

## [0.4.0] - 2026-10-15

The server now runs on version 2 of the MCP Python SDK. Tools, parameters, resources, prompts and error messages are unchanged; the README has new installation and upgrade instructions.

### Breaking changes / upgrade notes

- **Requires `mcp` 2.3 or newer** (`mcp>=2.3,<3`). Upgrade with `uv tool install --force --python 3.12 "git+https://github.com/mattias242/protonmail-mcp-server@v0.4.0"`, then restart Claude Desktop or reconnect the server in Claude Code (`/mcp`). An install from a clone needs `git pull && uv sync`.
- **Resource subscriptions need a client on an MCP protocol version before 2026-07-28** (`resources/subscribe`), which Claude Code and Claude Desktop use today. Clients on 2026-07-28 use `subscriptions/listen`, which is not supported yet; the server does not advertise subscriptions to them. Everything else works on both protocol versions.

### Changed

- **The server runs on the MCP Python SDK 2.x** (`mcp>=2.3,<3`, `MCPServer` instead of `FastMCP`). Tools, parameters, schemas, resources, prompts and error texts are unchanged; a recorded contract of everything a client sees (`tests/snapshots/mcp_kontrakt.json`) is identical before and after, except that the empty `experimental` capability is no longer sent.
- **Tool descriptions are the same on every Python version.** On Python 3.12 they kept the docstring's indentation; Python 3.13+ removed it.
- **A tool refused by policy** (`PROTONMAIL_READ_ONLY`, `PROTONMAIL_SEND_MODE`) is logged as an expected refusal instead of with a stack trace. The client sees the same text.
- **Resource errors** no longer write a stack trace to stderr; the client gets the same text as before.

### Documentation

- **Installation instructions** in the README: install as a tool with `uv tool install` (recommended) or run from a clone, how to add the server to Claude Code and Claude Desktop, how to upgrade and uninstall, and troubleshooting.

## [0.3.1] - 2026-10-15

A bug-fix release. v0.3.0 could not be installed without the lock file, and a final review against a real ProtonMail Bridge found ways that deletion, labels and replies could report or do the wrong thing.

### Breaking changes / upgrade notes

No tool parameters changed. Two answers can look different:

- **`unflag_email` can answer with an object for a single UID**: when the star is removed but cleaning up the Starred view fails, the answer is `{"ok", "failed", "note"}` instead of `true`, with `note` explaining what was left.
- **Permanent deletion can take about 50 seconds** when Bridge answers `Code=2501`, because the server checks that every email in the batch is really gone. Other tools are no longer blocked while it waits.

### Fixed

- **Installation**: `mcp` is now limited to `<2`. `uv tool install` and `pip` ignore `uv.lock`, so v0.3.0 installed that way pulled in mcp 2.x and did not start (`ModuleNotFoundError: mcp.server.fastmcp`).
- **Permanent deletion after `Code=2501`** is only reported as done when the email is gone from All Mail. Bridge stops a batch at the first already deleted email and leaves the others in All Mail without a folder; they are now put back in Trash and deleted again, or reported in `failed` with an explanation, never in `ok`.
- **The same Proton email is recognised by `X-Pm-Internal-Id`** instead of Message-ID, date and size, which are not unique. This affects the "already in target" check in `move_email` and `delete_email`, `remove_label`, and the Starred view. Without the id, several identical emails in the target are reported as `failed` instead of "already there".
- **`unflag_email` no longer removes the star from another email** with the same Message-ID, date and size.
- **The IMAP lock is released while waiting** after `Code=2501`, so other tools, resource reads and `wait_for_new_email` keep working during the wait.
- **Best-effort steps survive timeouts and dropped connections**: a failure in the Starred clean-up or in the All Mail check no longer turns a successful `unflag_email` into an error or discards the result of a permanent deletion.

### Security

- **`reply_to_email` and `create_reply_draft` treat a forged "own" email as a received one.** An email with your own address in `From` (which the sender controls) and a lookalike address in `To` was treated as sent by you, so the reply went to the lookalike address. An email now counts as your own only when Bridge marked it `X-Pm-Origin: internal` or the same Proton email (`X-Pm-Internal-Id`) is in Sent.

## [0.3.0] - 2026-10-15

A large release: 13 new tools (29 in total), MCP resources, subscriptions and prompts, new mail via IMAP IDLE, a send policy with audit log, protection against prompt injection, and more than a hundred bug fixes found in a systematic review against a real ProtonMail Bridge.

### Breaking changes / upgrade notes

- **Python 3.12 or newer is required** (`requires-python = ">=3.12"`). Python 3.10 and 3.11 are no longer supported; CI runs 3.12, 3.13 and 3.14.
- **`mailbox` is required** wherever a tool acts on a UID, because UIDs are only unique within one mailbox: `mark_email_read`, `mark_email_unread`, `move_email`, `delete_email`, `reply_to_email`, `forward_email`, `get_email` and `get_email_headers` (and all new UID-based tools). Pass the `mailbox` field from the response the UID came from. `list_emails`, `search_emails` and `get_mailbox_status` still default to `INBOX`, and so do the new `check_new_emails` and `wait_for_new_email`.
- **`delete_email` moves to Trash** instead of expunging. Permanent deletion needs `permanent=True` and is only allowed when `mailbox` is `Trash` (or `Drafts`); it expunges only that UID. Deleting from a label view (`Labels/…`, Starred) or All Mail is refused — pass the folder the email is in.
- **`search_emails` returns an object** instead of a list: `{"mailbox", "messages", "total", "has_more", "complete", "note"}`. Results are capped by the new `max_results` parameter (1–200, default 50; previously up to 200); when `complete` is false, `total` is a lower bound and `note` explains why.
- **`list_mailboxes` entries changed**: `{"name", "type", "selectable", "special_use"}`. `type` is now `system` (INBOX, Drafts, Sent, Starred, Archive, Spam, Trash, All Mail), `folder` (`Folders/…`), `label` (`Labels/…`) or `container` (`Folders`, `Labels`; `selectable: false`). System folders were previously reported as `folder`.
- **Mailbox names are human-readable** (`Labels/Näs`), not modified UTF-7 (`Labels/N&AOQ-s`). All tools encode names themselves, so pass names exactly as `list_mailboxes` returns them; an already encoded name is encoded twice and will not be found.
- **`body_html` is `null` with `body_format="text"` or `"stripped"`**; the new `has_html` says whether the email has HTML. Raw HTML is only returned with `body_format="full"`. Text from HTML-only emails is still in `body_plain`.
- **Bulk operations**: `mark_email_read`, `mark_email_unread`, `move_email`, `delete_email`, `add_label`, `remove_label`, `flag_email` and `unflag_email` accept a list of up to 100 UIDs from the same mailbox and then return `{"ok": [...], "failed": [{"uid", "error"}]}`. A single UID still returns `true`, except when `move_email`/`delete_email` skip an email that is already in Trash (`{"ok", "failed", "note"}`).
- **Errors instead of empty answers**: `get_email` and `get_email_headers` raise an error for a UID that does not exist (previously `null`), `reply_to_email` and `forward_email` raise instead of returning `false`, all write tools (`mark_email_read`/`mark_email_unread`, `move_email`, `delete_email`, `create_folder`, `rename_folder`, `delete_folder`) raise instead of returning `false`, and IMAP `NO`/`BAD` answers to LIST, STATUS, SEARCH and FETCH raise instead of returning empty results. Errors are actionable messages in Swedish.
- **Folder tools need a prefix**: `create_folder`, `rename_folder` and `delete_folder` require `Folders/<name>` or `Labels/<name>`; renaming between the two is refused. `delete_folder` refuses a folder that contains email or has sub-folders unless `force=True`.
- **`move_email` refuses moves that Bridge would turn into data loss**: source equal to target, to or from All Mail, INBOX ↔ Sent, Starred as target (use `flag_email`), and to Trash/Drafts from a label view.
- **`get_email_headers` keys** use underscores (`message_id`, `reply_to` instead of `message-id`, `reply-to`) and are sanitized like `get_email`.
- **`get_email` address fields**: display names with special characters are quoted (`"Öberg, Åsa" <asa@example.com>`), the new `addresses` field gives `[name, address]` pairs, and for duplicate headers in `headers` the first occurrence now wins.
- **Reading no longer marks email as read**: `get_email`, `get_attachment` and the reading of the original in replies and forwards use `BODY.PEEK` (0.2.0's `get_email` set `\Seen`). Call `mark_email_read` explicitly.
- **Replies quote the original by default**: `reply_to_email` adds the quoted original unless `quote_original=False`.
- **`list_emails` `page_size` is limited to 1–100** (`page` ≥ 1); larger values are a validation error.
- **New start-up side effects**: the server opens one extra IMAP IDLE connection per watched folder (`PROTONMAIL_WATCH_ENABLED=true`, `PROTONMAIL_WATCH_MAILBOXES=INBOX` by default), creates `PROTONMAIL_ATTACHMENT_DIR` (`~/Downloads/protonmail-mcp`, mode 0700), and writes an audit line to stderr for every send and write.
- **New default send limits**: `PROTONMAIL_MAX_RECIPIENTS=50` unique recipients per send and `PROTONMAIL_MAX_SENDS_PER_HOUR=30`. Set either to `0` to disable it. More than 1000 address entries are always refused.
- **`uv run pytest` no longer runs integration tests**; run them explicitly with `uv run pytest -m integration` (needs a running Bridge).

### Added

- **Drafts**: `create_draft`, `create_reply_draft`, `create_forward_draft`, `update_draft` (keeps threading and attachments, moves the old draft to Trash) and `send_draft` (sends exactly the recipients `get_email` shows, then removes the draft from Drafts; `draft_deleted` and `warning` in the response say if that failed). Drafts are real Proton drafts that can be edited in Proton's apps.
- **Attachments**: `get_attachment` saves an attachment to `PROTONMAIL_ATTACHMENT_DIR` (default `~/Downloads/protonmail-mcp`) or returns it inline up to `PROTONMAIL_ATTACHMENT_MAX_INLINE_BYTES`; `send_email`, `reply_to_email` and `forward_email` take an `attachments` list of files from that directory (at most 20 files, 25 MB).
- **Labels, stars and bulk operations**: `add_label`, `remove_label`, `flag_email` and `unflag_email`; bulk mode with up to 100 UIDs for the read/unread, star, move, delete and label tools.
- **Search filters**: `to_addr`, `cc_addr`, `text`, `body`, `flagged`, `answered`, `larger_than`, `smaller_than`, `has_attachment` (from BODYSTRUCTURE) and `max_results`. Search hits include `to`, `cc` and `message_id`.
- **`get_thread`**: the whole conversation from All Mail, following `Message-ID`, `In-Reply-To` and `References`.
- **New mail via IMAP IDLE** on dedicated connections (`PROTONMAIL_WATCH_ENABLED`, `PROTONMAIL_WATCH_MAILBOXES`, at most 5 folders), with the tools `check_new_emails` and `wait_for_new_email`.
- **MCP resources**: `mailboxes://`, `mailbox://{mailbox}` (status and the 20 newest emails) and `email://{mailbox}/{uid}`.
- **Resource subscriptions and notifications**: `notifications/resources/updated` for new mail in watched folders and after every successful write tool, for the resources it changed.
- **Prompts**: `sammanfatta_olasta`, `triagera_inkorgen`, `svara_pa_mejl` and `rensa_nyhetsbrev`.
- **Send policy**: `PROTONMAIL_READ_ONLY`, `PROTONMAIL_SEND_MODE` (`send`, `draft_only`, `disabled`), `PROTONMAIL_ALLOWED_RECIPIENTS`, `PROTONMAIL_MAX_RECIPIENTS` and `PROTONMAIL_MAX_SENDS_PER_HOUR`, enforced before a tool runs, on every IMAP write and on the single SMTP delivery path.
- **Audit log**: one JSON line per send and per write operation, including refused ones; masked on stderr, complete in `PROTONMAIL_AUDIT_LOG` (mode 0600).
- **MCP tool annotations and titles** on all tools (read-only, destructive, idempotent, open-world), so clients can ask for confirmation before destructive or sending tools.
- **Server instructions** sent at initialization: UIDs are per mailbox, prefer drafts, email content is untrusted, deletion goes to Trash, bulk limits.
- **`date_iso`**: the `Date` header in ISO 8601 next to the raw `date` in `get_email`, listings, search and threads.
- **Prompt-injection signals** in `get_email`: `untrusted_content_notice`, `sanitized`, `suspicious_content` and `hidden_html` (see Security).
- **`python -m protonmail_mcp`** starts the server, like the `protonmail-mcp` command.
- **`PROTONMAIL_IMAP_TIMEOUT`** (default 30 s) for IMAP commands.
- The server reports its own version in `serverInfo`.
- `CHANGELOG.md`.

### Changed

- `move_email` uses a single `UID MOVE` instead of COPY + STORE + EXPUNGE, checks that the UID exists first and reports Bridge's answer.
- Every UID-based write checks that the UID exists in the given mailbox before changing anything, so a UID from the wrong mailbox is an error instead of a change to another email.
- One error handler for all tools: network, login and SMTP errors become actionable Swedish messages, unexpected errors are logged with stack trace to stderr only, and partial deliveries say who received the email.
- `reply_to_email` honours `Reply-To`, and gains `reply_all`, `cc`, `bcc` and `quote_original` (on by default); replying to your own email goes to the original recipients. Subjects get exactly one `Re:`/`Fwd:` whatever language the earlier prefixes were in.
- `forward_email` forwards inline with the original HTML part and all attachments byte for byte, and gains `cc` and `bcc`.
- `get_email` converts HTML to text properly (blocks, lists, quotes as `> `), leaves out hidden HTML, lists attachments with `index` and `part`, and adds `addresses`, `in_reply_to`, `references`, `has_html` and `date_iso`. `body_format` and `max_length` are validated in the schema.
- `list_emails` validates `page` (≥ 1) and `page_size` (1–100), returns an empty page past the end, and every listed email carries its `mailbox`.
- `subject`, `from_addr`, `to_addr` and `cc_addr` in `search_emails` are matched locally against decoded headers, so words with å, ä, ö and RFC 2047-encoded headers are found; `text`/`body` words with non-ASCII letters are also searched in quoted-printable form.
- Parameters of the same kind have the same type in every tool; UIDs may be sent as numbers or strings.
- `.env` is read from the project root even when the server is started from another directory.
- Tool descriptions describe what happens in Proton, what does not happen and which errors can occur.
- CI runs on Python 3.12, 3.13 and 3.14 with `fail-fast: false`.

### Removed

- Support for Python 3.10 and 3.11, and the compatibility code for them.

### Fixed

Grouped by area, most serious first. Fixes to features that are new in this release are not listed separately.

**IMAP correctness and Bridge semantics**

- `delete_email` ran `EXPUNGE` on the whole mailbox, which permanently removed every message flagged `\Deleted` there, not just the requested one; deletion now moves to Trash and permanent deletion expunges only the given UID.
- `move_email` was not atomic (COPY + STORE + EXPUNGE) and could expunge other messages; it now uses `UID MOVE`.
- Moving or deleting an email to Trash that is already there (for example the Sent copy of an email to yourself shortly after the INBOX copy was deleted) made Bridge delete it permanently (`Code=2501`); such emails are now detected and skipped with a `note`.
- Permanent deletion that Bridge refuses with `Code=2501` but carries out later is now recognised (waits up to 45 s), and drafts left orphaned in All Mail are cleaned up.
- Reading and listing no longer mark emails as read (`BODY.PEEK`).
- Unsolicited FETCH responses no longer show up as phantom emails in listings and search.
- `get_mailbox_status` read the message count from the mailbox name in some cases; it now parses the STATUS parentheses.
- Mailbox names containing `* ` are listed correctly.
- The containers `Folders` and `Labels` gave an empty mailbox instead of an error.
- `NO`/`BAD` answers to LIST, STATUS, SEARCH and FETCH no longer turn into empty results.
- `set_flags` checks that the UID exists and what Bridge answered.
- Folder and label name collisions give an error that names the existing folder or label.

**Connection and timeouts**

- Parallel tool calls are serialized on the shared connection; previously they could fail (`LOGIN illegal in state SELECTED`) or run in each other's selected mailbox.
- A failed, slow or cancelled LOGIN no longer leaves the client stuck.
- A clear error immediately when Bridge is not running, and fast reconnection after Bridge restarts.
- Old connections are closed on reconnect and logout (connection leak).
- `UID STORE`/`COPY`/`MOVE`/`EXPUNGE` and the pre-write UID check have timeouts, and a timed-out or cancelled operation discards the connection.
- A connection that drops mid-command gives a `ConnectionError` at once instead of after the full timeout.
- Reconnection only happens in known non-authenticated states.

**Mailbox names**

- Mailbox names are decoded from and encoded to modified UTF-7, so folders and labels with å, ä, ö work in every tool.
- Names with backslashes, quotes, `%`, `*` and `..` are accepted and quoted correctly; validation matches the whole value and runs before Bridge is contacted.
- `list_mailboxes` reports `type`, `selectable` and `special_use` from Bridge's own attributes.

**Parsing and character encoding**

- Unknown or unusual charsets no longer crash parsing; raw 8-bit (UTF-8) headers are decoded, also in `get_email_headers`; `date` and `message_id` are always strings.
- New MIME traversal: several text parts are joined, a single non-text part is an attachment, forwarded `message/rfc822` is listed as an attachment, and inline text parts with a file name count as body text.
- Names in address lists are decoded and quoted, so replies go to the right address; the address part is never decoded.
- A whitespace-only text part no longer hides the HTML fallback; only `-- ` starts a signature in `stripped` mode.
- Header names are case-insensitive; CR/LF and other line breaks are removed from decoded header values; `email.header.Header` objects are serialized.
- Whitespace inside a quoted local part is kept; an unclosed `<head>` is closed at the first content.

**SMTP, replies and forwarding**

- Only the display name is encoded in address headers; IDN domains are sent as punycode using IDNA2008 (`straße.de` no longer becomes `strasse.de`).
- No empty `To:` header when sending only to Bcc; `Message-ID` uses the account's domain instead of the host name; long subjects keep their spaces.
- Recipients that the server refuses are reported (`SMTPPartialDeliveryError`) instead of being ignored.
- Duplicate recipients are sent once; addresses that cannot be built are refused as invalid.
- `References` is read case-insensitively; `In-Reply-To` is used when `References` is missing.
- Replying to an email you sent to yourself goes to your own address.
- The string `"null"` in `body_html`, `reply_to` or a search term is a value, not a missing parameter.

**Server and configuration**

- `python -m protonmail_mcp.server` started a server with no tools.
- `serverInfo.version` reported the version of the `mcp` library instead of this server's.

### Security

- **Commands ran in the wrong mailbox after a failed SELECT.** Bridge keeps the previous mailbox selected when SELECT fails, so a misspelt mailbox name could make a flag change, move or delete hit a message with the same UID in another folder. SELECT is now checked and the connection discarded on failure.
- **IMAP command injection via search parameters** (CR/LF and other control characters) is blocked; dates are parsed strictly and every search value is quoted.
- **Prompt-injection protection**: invisible and misleading characters (Unicode tag characters, zero-width characters, bidi controls, variation selectors, excess combining marks) are removed from subjects, names, bodies, HTML text, headers and attachment names; hidden HTML text (`display:none`, zero font size, background-coloured or off-screen text, simple `<style>` rules and more) is left out of the text the model sees, with a fail-closed HTML parser; email content is marked untrusted and typical injection phrases (English and Swedish) are flagged in `suspicious_content`; `Message-ID`, `In-Reply-To` and `References` are shown only as ASCII `<msg-id>`s.
- **Addresses with hidden characters**: invisible characters in an address are shown as visible `[U+XXXX]` codes instead of being removed, and every send and draft path refuses recipients with hidden characters or the visible `[U+` marking.
- **Recipient confusion**: encoded display names can no longer be read as extra or different recipients, reply-all no longer picks up addresses that only appear in comments, and `send_draft` refuses drafts whose recipient headers are duplicated or ambiguous and sends exactly the recipients `get_email` shows.
- **Attachment directory**: path traversal, symlinks (also as the directory itself), hard links, FIFOs and files swapped between check and use (also when Linux reuses the inode number) are refused; files that look like secrets (`.ssh`, `.gnupg`, `.aws`, `.netrc`, `.env*`, `id_*` keys, `*.pem`, `*.key` other than Keynote files, keychains, private keys by content) cannot be sent; sender-supplied file names are reduced to one clean name; saved files are never overwritten and get the `com.apple.quarantine` attribute on macOS.
- **Recipient addresses, including Bcc, stay out of stderr**, which clients such as Claude Desktop save to a log file: the audit line there carries only a count, and addresses in error texts are masked, including quoted local parts. Full lines go only to `PROTONMAIL_AUDIT_LOG`.
- **The Bridge password is masked** in all errors and logs, also URL-encoded and after whitespace normalisation, and configuration validation errors no longer echo input values.
- **Proton's internal `X-Pm-*` headers** are stripped from drafts sent with `send_draft`.
- **The new-mail high-water mark cannot be poisoned**: `since_uid` never moves it, so a crafted value cannot hide future email.
- **Prompt arguments** refuse line breaks (including U+2028/U+2029) and control characters.
- **Denial of service through crafted email**: linear-time handling of line breaks, RFC 2047 words, HTML-to-text (also nested quotes), header unfolding, address validation and reply threading; limits on header size, MIME parts and recipient entries.
- Dependency updates: `cryptography` 46.0.6 (CVE-2026-34073), and in #4 `aiosmtplib` 5.1.2 (GHSA-vxj7-4xrp-5vr4, STARTTLS response injection — not reachable here since Bridge uses direct TLS), `cryptography` 50.0.0, `mcp` 1.28.1, `starlette` 1.3.1, `pydantic-settings` 2.14.2, `idna` 3.15 and others.

## [0.2.0] - 2026-03-16

Security hardening and six new tools.

### Added

- `reply_to_email`, `forward_email` and `get_email_headers`.
- `create_folder`, `delete_folder` and `rename_folder`.
- `body_format` (`text`, `full`, `stripped`) and `max_length` in `get_email`.
- Page-based pagination (`page`, `page_size`) in `list_emails`.
- A `type` field (`folder`/`label`) in `list_mailboxes`.
- Search metadata, ISO dates and automatic reconnection.
- Configurable TLS certificate pinning for SMTP (`PROTONMAIL_VERIFY_SSL`, `PROTONMAIL_SMTP_CA_CERT`).

### Security

- Mailbox name validation.
- Protection against email header injection.

## [0.1.0] - 2026-03-16

Initial release.

### Added

- MCP server (stdio) for ProtonMail Bridge with ten tools: `list_mailboxes`, `get_mailbox_status`, `list_emails`, `get_email`, `search_emails`, `mark_email_read`, `mark_email_unread`, `move_email`, `delete_email` and `send_email`.
- Configuration through `PROTONMAIL_*` environment variables or `.env`.
- Unit, BDD and integration tests, GPL v3 licence and community files.

[Unreleased]: https://github.com/mattias242/protonmail-mcp-server/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/mattias242/protonmail-mcp-server/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/mattias242/protonmail-mcp-server/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/mattias242/protonmail-mcp-server/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/mattias242/protonmail-mcp-server/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/mattias242/protonmail-mcp-server/releases/tag/v0.1.0
