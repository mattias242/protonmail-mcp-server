# protonmail-mcp

MCP server (MCP Python SDK 2.x, `MCPServer`, stdio; HTTP with `--http`) that gives an LLM access to Proton Mail via
ProtonMail Bridge on localhost: IMAP for reading and changes, SMTP for sending. 29 tools, three resources (with
subscriptions), four prompts and watching for new mail with IMAP IDLE. README.md (public) describes the tools, the
settings and the security model for users; this file is for whoever changes the code.

## Language

Everything written in the repository is in English: commit messages, pull requests, issues and comments on GitHub,
README.md, this file, code comments, internal docstrings, tests and BDD scenarios. Existing Swedish comments, tests and
scenarios are being translated; write new ones in English and translate what you touch.

The exception, for now, is the product text the model and the user see: tool docstrings and titles, parameter
descriptions, prompts, resource texts, `INSTRUCTIONS` and error messages are written in Swedish as the source, with the
English translation in `i18n_en.py` (`PROTONMAIL_LANGUAGE=en`). Switching the source language to English is planned.

## Running locally

```bash
uv sync
cp .env.example .env            # PROTONMAIL_USERNAME + the Bridge password
uv run protonmail-mcp           # stdio transport
uv run protonmail-mcp --http    # HTTP on 127.0.0.1:8765/mcp, requires PROTONMAIL_HTTP_TOKEN (Perplexity)
npx @modelcontextprotocol/inspector uv run protonmail-mcp   # try it interactively
```

`python -m protonmail_mcp` and `python -m protonmail_mcp.server` start the same server with the same tools. `.env` is
read from the project root even when the server is started from another directory; a `.env` in the working directory
wins, and environment variables win over both. Configuration for Claude Desktop and Claude Code
(`uv --directory /path/to/… run protonmail-mcp`) is in README.md.

## Architecture and module map

`server.main()` calls `register_tools()`, which imports the tool modules (the import registers the tools) and registers
resources, subscriptions and prompts; it then sets the language of the server instructions (`apply_language`) and runs
stdio — or, with `--http`, Streamable HTTP via `http_server.py` (one lifespan and one `IMAPClient` for all HTTP
sessions). `lifespan` reads the settings, creates the attachment directory, a shared `IMAPClient` (connects lazily, on
the first call), an `SMTPClient` (connects per send) and starts `MailWatcher` (its own IDLE connections). Logging goes
to stderr only — stdout is the MCP protocol.

| Module | Responsibility |
|--------|----------------|
| `__init__.py` | `__version__` from the package metadata; the only source of the version is `version` in pyproject.toml (the server reports it in serverInfo) |
| `_sdk.py` | The only place that imports the MCP SDK's server parts (`MCPServer`, `Context`, `ToolError`, `Resource`, `ResourceTemplate`, `ToolAnnotations`); other modules import from here |
| `__main__.py` | `python -m protonmail_mcp` starts `server.main()` |
| `http_server.py` | HTTP mode (`protonmail-mcp --http`): Streamable HTTP on `/mcp` behind the API key (`ApiKeyProtected`), Host check against `PROTONMAIL_HTTP_ALLOWED_HOSTS`, start with uvicorn |
| `server.py` | The server instance `mcp` (`_GuardedMCPServer` refuses tools by policy before they run and translates what it lists), `INSTRUCTIONS` for the model, `apply_language`, `lifespan`, `register_tools()`, `main()` and the listener that turns new mail into resource notifications |
| `config.py` | `Settings` (pydantic-settings, prefix `PROTONMAIL_`), parsing of `ALLOWED_RECIPIENTS` (IDNA2008) and `WATCH_MAILBOXES` |
| `policy.py` | Read-only, send mode, the guard against destructive changes, recipient list, recipient and hourly limits; the `write_operation` decorator, the tool lists `WRITE_TOOLS`/`DRAFT_TOOLS`/`SEND_TOOLS`/`DESTRUCTIVE_TOOLS` and `hidden_tools` (what `tools/list` leaves out) |
| `i18n.py` | `PROTONMAIL_LANGUAGE`: translates titles and descriptions when the server lists tools, prompts and resources, and fixed texts and error messages with `t()` (`sv_hash`, `current_language`, fail-closed to Swedish) |
| `i18n_en.py` | The English catalogue: `TOOLS`, `PROMPTS`, `RESOURCES`, `TEMPLATES` (with the hash of the Swedish text), `FIELDS` and `TEXTS` (templates for `i18n.t`, keyed by the hash of the Swedish template) |
| `audit.py` | Audit log: one JSON line per send and per writing IMAP operation, masked on stderr, complete in `PROTONMAIL_AUDIT_LOG` |
| `imap_client.py` | `IMAPClient`: the shared connection with a lock (`_serialized`), `_BridgeProtocol`, validation and quoting, reading, search, flags, moves, deletion, folders |
| `imap_bulk.py` | Bulk operations (1–100 UIDs in one folder): flags, moves, deletion with a result per UID (`ok`/`failed`) |
| `imap_labels.py` | Labels (`add_label` with UID COPY, `remove_label` via X-Pm-Internal-Id in the label view) and stars (`\Flagged`); the identity comparison `_find_in_label` that moves, deletion and replies also use |
| `imap_drafts.py` | Drafts in Drafts: APPEND, replacement (the old one to Trash) and removal after sending |
| `imap_threads.py` | Threads from the Message-ID/In-Reply-To/References chain in All Mail, with limits on searches, IDs and time |
| `imap_idle.py` | `MailWatcher`: one IDLE connection per watched folder (`_WatchProtocol`), high-water marks, event queue, listeners as separate tasks, reconnection with growing back-off, recovery after a UIDVALIDITY change |
| `mailbox_names.py` | Modified UTF-7 for folder names (RFC 3501 §5.1.3) and `describe_mailbox` (type, selectable, special_use) |
| `email_parser.py` | RFC 822 → dict for the tools: decoding, body_format, attachment lists, sanitising and `suspicious_content` |
| `html_text.py` | HTML → visible text; skips hidden elements and reports them; fail-closed |
| `sanitize.py` | Removal of invisible and misleading characters, address display with `[U+XXXX]`, injection heuristics, `UNTRUSTED_CONTENT_NOTICE` |
| `attachments.py` | The attachment directory: file name sanitising, safe saving, checks of files to send, the block on secrets |
| `smtp_client.py` | `SMTPClient`: builds messages, validates addresses, the single delivery path `_deliver` |
| `resources.py` | The resources `mailboxes://`, `mailbox://{mailbox}`, `email://{mailbox}/{uid}` and the URI encoding |
| `subscriptions.py` | `resources/subscribe`, the registry of subscribing sessions, `notify_resource_updated` and `TOOL_CHANGES` (which resources each writing tool changes) |
| `prompts.py` | The prompts `sammanfatta_olasta`, `triagera_inkorgen`, `svara_pa_mejl`, `rensa_nyhetsbrev` (`_TEMPLATES`, `_SAFETY`) |
| `tools/__init__.py` | The `tool(...)` decorator with mandatory classification and the presets `READ_ONLY`, `SETS_STATE`, `ADDS`, `DESTRUCTIVE`, `SENDS`; the after-call hook `notify_after` that sends resource notifications |
| `tools/_errors.py` | `with_error_messages`/`describe_error`: exceptions → understandable errors, password masked |
| `tools/_types.py` | Parameter types `Mailbox`, `OptionalStr`, `AttachmentArg` |
| `tools/_uids.py` | Parameter types `Uid` and `UidArg` (one UID or a list of 1–100) |
| `tools/mailboxes.py` | `list_mailboxes`, `get_mailbox_status` |
| `tools/messages.py` | `list_emails`, `get_email`, `get_email_headers` |
| `tools/search.py` | `search_emails` |
| `tools/threads.py` | `get_thread` |
| `tools/send.py` | `send_email` |
| `tools/reply.py` | `reply_to_email`, `forward_email` and the builders `build_reply`/`build_forward` that the drafts reuse |
| `tools/drafts.py` | `create_draft`, `create_reply_draft`, `create_forward_draft`, `update_draft`, `send_draft` |
| `tools/attachments.py` | `get_attachment` |
| `tools/manage.py` | `mark_email_read`, `mark_email_unread`, `move_email`, `delete_email` |
| `tools/labels.py` | `add_label`, `remove_label`, `flag_email`, `unflag_email` |
| `tools/folders.py` | `create_folder`, `delete_folder`, `rename_folder` |
| `tools/new_mail.py` | `check_new_emails`, `wait_for_new_email` |

`tests/unit/test_dokumentation.py` and `tests/features/dokumentation.feature` require that the table above lists exactly
the modules in `src/protonmail_mcp/`, and that README's tables list exactly the tools, resources, prompts and settings
the code has. If you add something, update the tables in the same commit.

## Design principles (apply to all future changes)

- **Fail-closed.** If something cannot be decided safely: refuse, or give less — never more. Examples: HTML that cannot
  be parsed gives less text, an ambiguous address header in a draft refuses the send, an attachment directory that
  cannot be verified is not used, a refused SELECT discards the connection instead of carrying on in the wrong folder.
- **Always read with `BODY.PEEK`.** `BODY[]` marks the email as read. No reading tool, resource or watcher may change
  flags.
- **IMAP via the client's helpers.** `await imap._select(mailbox)` (checks the response; Bridge stays in the previous
  folder after a failed SELECT, so the connection is discarded; a `\Noselect` container gives an error),
  `await imap._uid(...)` (UID commands with a timeout), `await imap._require_uid(mailbox, uid)` (UID FETCH before every
  write, so that a UID from the wrong folder gives an error instead of changing the wrong email). Validate with
  `_validate_mailbox`, `_validate_uid` and `_imap_quoted`, quote folder names with `_quote_mailbox` (which also encodes
  modified UTF-7) — before `_ensure_connected`, so that invalid values never reach Bridge. Everything that uses the
  shared connection runs under `_serialized`, which also discards the connection on a timeout or cancellation. Time
  limits in the IMAP modules are set with `_with_timeout` (`asyncio.timeout`): a cancellation from outside must always
  get through, even when it arrives just as Bridge answers (`tests/unit/test_efterfix_l7_avbrott*.py`).
- **No new methods on `IMAPClient` for new features.** Write them as module-level functions in a module of their own
  (`imap_drafts.py`, `imap_labels.py` …) with `IMAPClient` as the first argument, decorated with `_serialized`.
- **Writing IMAP functions are decorated with `policy.write_operation(action)`** (closest to the function, under
  `_serialized`; `draft=True` for drafts, `destructive=True` for what moves, deletes, renames or replaces something).
  It checks `READ_ONLY`/`SEND_MODE`/`ALLOW_DESTRUCTIVE` before anything is validated or sent and writes the audit line,
  also for refused calls. New writing tools also go in the right list in `policy.py` so that the server refuses them
  before they run; `tests/unit/test_llm_skick_skrivskydd.py` requires every registered tool to be classified, and
  `tests/unit/test_llm_skick_destruktivt.py` that `DESTRUCTIVE_TOOLS` is exactly the tools with `destructive=True`
  except the send tools. With `ALLOW_DESTRUCTIVE=false` those tools are also left out of `tools/list`
  (`policy.hidden_tools`, fail-closed when the settings cannot be read); tests that list tools through the server
  therefore need test settings (`use_test_settings`). `remove_sent_draft` (after `send_draft`) is not destructive: the sent draft is in Sent, and the
  send mode governs the send.
- **A single send path.** All SMTP goes through `SMTPClient._deliver` (via `send_email` or `send_prepared_message`),
  which checks the send policy, the recipient list, the recipient limit and the hourly limit before `_smtp_send`. Never
  call aiosmtplib anywhere else.
- **Tools are registered with `@tool(title, **CLASSIFICATION)`** from `tools/__init__.py` — never `mcp.tool` directly.
  The classification (read_only, destructive, idempotent, open_world) must be given; MCP's own defaults are
  destructive + open world. Add the tool to the table in `tests/unit/test_llm_kontrakt_annoteringar.py`, to README's
  tool table and import the module in `server.register_tools()`. A writing tool must also be classified in
  `subscriptions.TOOL_CHANGES` (which resources a successful call changes) or `TOOLS_WITHOUT_MAILBOX_CHANGES`;
  `tests/unit/test_efterfix_resurser_verktygsnotiser.py` fails otherwise. Strictly typed parameters
  (`Annotated`/`Field` with limits, `Literal`, `Mailbox`, `OptionalStr`, `Uid`/`UidArg`); `mailbox` is required for
  everything that starts from a UID. The docstring (Swedish, see Language) says what happens in Proton, what does not
  happen and which errors can occur.
- **Errors are `ValueError` with an understandable text** that says what happened and what the model can do, built
  with `i18n.t("Swedish template with {name}", name=value)` (never an f-string directly in `raise ValueError`, except in
  `config.py`, which runs before the language can be read). Phrases that end up in errors
  (`_require_ok(resp, i18n.t("Hämtningen av …"))`) also go through `i18n.t`; Swedish inflection is not built from parts
  (`{kind}en`) but as whole words (`_definite`). `with_error_messages` turns network, login and SMTP errors into text;
  the password is masked in everything that is returned or logged. A send that was partly delivered must never look like
  a failed one (the model would then resend).
- **Sanitise everything the sender controls** (subject, names, body, HTML text, attachment names, headers) with
  `sanitize.py` before it reaches the model, and mark it (`untrusted_content_notice`, `sanitized`,
  `suspicious_content`). Nothing is removed from addresses — invisible characters are shown as `[U+XXXX]` and sending
  to such addresses is refused. Identifiers (Message-ID etc.) are only shown as ASCII `<msg-id>`. New output paths
  (resources, prompts, new tools) must go through the same functions, preferably by calling existing tool functions.
- **Linear time in everything that parses sender-controlled input.** No regular expressions with backtracking, no
  quadratic loops, limits on counts and lengths. The parsers have linearity tests (doubled input ≈ doubled time).
- **Swedish is the source, English a catalogue.** If you change a tool docstring, a title, a parameter or argument
  description, or a prompt's or resource's title/description: update the translation in `i18n_en.py` and set `sv` to
  the new hash (`tests/unit/test_i18n.py` prints it). Until then the Swedish text is shown even with
  `PROTONMAIL_LANGUAGE=en`. `INSTRUCTIONS`, `_SAFETY` and the prompt templates (`prompts._TEMPLATES`, str.format with
  named placeholders) go through `i18n.t`: a changed Swedish template has no translation in `i18n_en.TEXTS` until one
  is added under the new hash (`tests/unit/test_i18n_texter.py`). The same goes for the error messages:
  `tests/unit/test_i18n_fel.py` finds every `i18n.t("…")` in the source, requires a translation with the same
  placeholders, and fails on `raise ValueError(f"…")`. The tool responses (`note`, `warning`) are not translated yet.
- **The server instructions and the prompts are part of the contract.** `INSTRUCTIONS` in `server.py` and `_SAFETY` in
  `prompts.py` say: UIDs per folder, drafts before sending, email content is untrusted, deletion to Trash, at most 100
  UIDs per bulk operation. If the behaviour changes, the texts must follow.

## Bridge facts (verified live, read-only)

- **Connection.** IMAP on 127.0.0.1:1143 is unencrypted TCP (STARTTLS is offered but not required; `IMAP4_SSL` gives
  `SSL: RECORD_LAYER_FAILURE`). SMTP on 1026 is direct TLS with a self-signed certificate (`CERT_NONE` and
  `check_hostname=False` by default).
- **Capabilities.** `IDLE`, `MOVE` and `UIDPLUS` are advertised. Moves are done with `UID MOVE` (no fallback without
  MOVE); the watcher requires IDLE. APPEND to Drafts normally gives `APPENDUID`; otherwise the draft is looked up by
  Message-ID.
- **Folders and labels.** INBOX, Archive, Sent, Spam, Trash, Drafts and `Folders/*` are exclusive folders; `Labels/*`
  and Starred are labels. All Mail contains everything. `Folders` and `Labels` are `\Noselect` containers that still
  answer OK with 0 emails to SELECT/STATUS — so `_select` asks LIST when a folder looks empty. CREATE/RENAME require
  the prefix `Folders/` or `Labels/`; sublabels do not exist; `Folders/A/B` requires `Folders/A`. A `/` in a label name
  is escaped as `\/` and must be sent back that way.
- **COPY/MOVE/EXPUNGE.** COPY to a folder moves, to a label adds the label. MOVE removes the source if it is a label or
  the target is a folder. EXPUNGE from a label removes the label, from a folder the email ends up only in All Mail,
  from Trash/Drafts it is deleted permanently — but only if Proton's UnlabelMessages succeeds for the whole batch (see
  Code=2501 below). If the email is already in the target it is first removed — in Trash/Drafts that is a permanent
  deletion. Everything to/from All Mail and INBOX ↔ Sent is refused. Therefore: deletion = `UID MOVE` to Trash;
  permanent only `UID STORE \Deleted` + `UID EXPUNGE <uid>` in Trash/Drafts; never EXPUNGE without a UID.
- **One email, several views.** An email to yourself is ONE Proton email with both Inbox and Sent (not two): it shows
  in INBOX and Sent with a UID each but the same Message-ID, time and size, and once in All Mail. If a draft is sent to
  yourself, the draft itself becomes the sent email (same Proton id and Message-ID).
- **The email's identity = `X-Pm-Internal-Id`.** Bridge sets the header (Proton's message id, 88 characters of
  base64url) on every email in every view (INBOX, Sent, Trash, Spam, All Mail; never repeated), and the same Proton
  email has the same value in every view. `SEARCH HEADER X-Pm-Internal-Id "<id>"` works and gives exactly that email.
  Message-ID + time + size is NOT enough: live there are two different emails in Trash with the same three values.
  Everything that looks up "the same email" in another view (`imap_labels._find_in_label`: the L7 check,
  `remove_label`, Starred, the All Mail check after 2501, replies to your own emails) therefore compares the id;
  without an id the three values are the fallback, and several hits are then ambiguous and give an error instead of ok.
- **`X-Pm-Origin`.** `internal` on emails sent inside Proton (all your own emails in Sent, and emails from other Proton
  accounts), `external` on emails from outside, `import` on imported ones. `reply_to_email` counts an email as your own
  sent one only if From is the account AND (exactly one `X-Pm-Origin: internal` or the same id is in Sent) — From can
  be forged.
- **The views lag 20–40 s.** Bridge fetches events from Proton every 20–40 seconds (`EventPeriod` 20 s +
  `EventJitter` 20 s in `internal/user/user.go`). Views that depend on Proton's state — Starred, Sent, All Mail and a
  folder the email was just moved out of via another view — show the old state for that long. `-FLAGS \Flagged`
  removes the star in Proton at once but the email stays in the Starred view; `unflag_email` therefore removes it from
  there with UID EXPUNGE in Starred (best effort: errors, also timeouts, are reported in `note`). EXPUNGE in Starred
  removes the star in Proton, so only UIDs with the same X-Pm-Internal-Id that already lack `\Flagged` are removed.
- **Code=2501 ("Message does not exist").** If an email is moved to Trash that is already there — typically the Sent
  copy of an email to yourself within 20–40 s after the INBOX copy was put in Trash — Bridge answers `NO labeling
  messages: … (Code=2501)`, but the email has then been deleted permanently. `move_email`/`delete_email` therefore
  first look the emails up in Trash/Drafts via X-Pm-Internal-Id (`imap_bulk.already_in_target`, one SEARCH per 20 ids)
  and send nothing for those already there; they count as ok with an explanation in `note`. If it cannot be decided (no
  id and several identical emails in the target) the email is not moved and ends up in `failed`. On permanent deletion
  Bridge can answer `NO failed to unlabel messages … (Code=2501)`; the emails then disappear from the folder at the
  next event fetch. `permanent_delete_found` waits up to 45 s (`_GONE_TIMEOUT`) — without the IMAP lock between the
  checks, so that other tools are not blocked — and counts a vanished email as deleted only when it is not in All Mail
  either (see the next point).
- **Orphaned emails after 2501.** Bridge (`connector.go`, `removeMessagesFromMailboxWithUnlabelCallOnFolders`) does
  UnlabelMessages for the whole batch and aborts on the error BEFORE DeleteMessage. The email that was already deleted
  in Proton gives 2501; all other emails in the batch — ordinary ones as well as drafts, nothing in the code applies
  only to drafts — lose Trash but are not deleted, and remain invisible in All Mail without a folder (seen live for a
  draft). `permanent_delete_found` therefore fetches the identity while the emails are visible, looks up the vanished
  ones in All Mail, puts those that remain back in Trash with UID COPY and deletes them there with UID STORE
  `\Deleted` + UID EXPUNGE. If it cannot be decided (no identity, the search is refused or hangs, several identical
  ones) the email becomes `failed` with an explanation, never `ok`. `send_draft` removes its draft without waiting.
- **Star = `\Flagged`**, globally in every view. A move to Starred would remove the email from the source folder.
- **Folder names in modified UTF-7.** LIST names are decoded; `_quote_mailbox` always encodes. The tools therefore only
  take readable names (`Labels/Näs Samfällighet`), and the encoded form becomes double-encoded.
- **UIDs are per view.** STATUS counts per view and the labels overlap. `UID FETCH` of a non-existent UID answers OK
  without data; `FETCH 1:n` outside the view is refused.
- **Search.** Bridge searches in undecoded headers (RFC 2047 words do not match) and the text parts are stored
  quoted-printable, so subject/sender/recipients are filtered locally against decoded values and words with åäö in
  text/body are also searched in QP form. `HEADER Message-ID` and `HEADER X-Pm-Internal-Id` are substring matches —
  check exactly locally. A SEARCH in All Mail takes 3–8 s (~30,000 emails).
- **Queued updates.** Bridge (Gluon) sends `* n EXISTS` in the response to the *next* command in the selected folder
  and never again; aioimaplib drops such lines outside IDLE. The watcher therefore searches after every IDLE round and
  again if an EXISTS came in a response. Bridge disconnects an IDLE after 29 minutes; it is renewed every 25.
- **Empty folders** answer `NO` (no such message) to `UID SEARCH UID n:*` instead of an empty response. The watcher and
  the new-mail tools therefore do not search when SELECT shows 0 EXISTS and interpret exactly that response as no
  emails.
- **A failed SELECT** leaves the previous folder selected (contrary to RFC 3501); therefore the connection is discarded.
- **internalid references.** Bridge puts `<X-Pm-Internal-Id@protonmail.internalid>` in References. It points to the
  email itself (`get_thread` skips it). On SMTP Bridge looks such references up: an ordinary email becomes the
  parentID (thread and answered mark), a draft ends up in draftsToDelete and is deleted after the send. Therefore
  `update_draft` removes the old draft's own reference but keeps the others, and `send_draft` keeps References but
  removes the `X-Pm-*` headers.

## Test strategy

- **BDD first.** Scenarios (Given/When/Then in `.feature` files under `tests/features/`) describe the desired behaviour
  in the language of the domain; the steps are in the `test_*.py` next to them. Write new scenarios in English
  (existing ones are in Swedish and are being translated). Then **TDD**: unit tests in `tests/unit/` that fail (RED),
  the smallest change (GREEN), clean up. A bug that cannot be made to fail in a test is not fixed.
- **New tests in new files** (named after the change) to avoid clashes; change existing tests only for a deliberate
  change of behaviour, and say so in the commit message.
- **The test guard.** `tests/conftest.py` blocks connections to ports 1143 and 1026 in every test not marked
  `integration` (`ConnectionRefusedError "… blockerad i tester"`). If you see that error, a fake has fallen through —
  fix the fake. A bare `AsyncMock()` as `_client` is not enough: it needs `select`/`login` that answer OK and
  `protocol.state` (see `_imap_mock()` in `tests/unit/test_imap_client.py`).
- **Fake servers in `tests/`** instead of mocks where the semantics matter: `fix_flytta_bridge.py`
  (moves/deletion/folders), `feat_etiketter_bridge.py` (labels; X-Pm-Internal-Id with `internal_ids = True`),
  `efterfix_live_bridge.py` (Code=2501 on permanent deletion as in connector.go: only `phantoms` are deleted, the others
  remain in All Mail; Starred that lags), `efterfix_slut_bridge.py` (X-Pm-Internal-Id switched on, twin emails with the
  same Message-ID/time/size), `efterfix_l7_bridge.py` (one email in INBOX and Sent, a move to Trash of an email that is
  already there deletes permanently), `feat_utkast_bridge.py` (drafts), `feat_sok_fakebridge.py` and
  `fix_sok_fakebox.py` (SEARCH/FETCH like Gluon), `fix_imapkarna_fakeimap.py` (a hanging and refusing Bridge),
  `efterfix_imap_avbrott.py` and `ny_mejl_fakeidle.py` (real TCP servers: cancellation, IDLE), `fix_svar_support.py`
  (captures SMTP), `llm_kontrakt_support.py` and `mcp_resurser_support.py` (a real MCP session in memory).
- **Running.** `uv run pytest` runs the unit and BDD tests (integration is deselected in `pyproject.toml`).
  `uv run pytest -m integration` runs against the real Bridge and only reads (requires `.env`); the SMTP test sends a
  real email to your own address and only runs with `PROTONMAIL_ALLOW_SEND=1`. Do no write operations against the real
  Bridge during development — read-only checks (LIST/STATUS/SELECT/SEARCH/FETCH BODY.PEEK) are fine.
- **Python 3.12 or newer** (`requires-python = ">=3.12"`). CI runs the unit and BDD tests on 3.12, 3.13 and 3.14; run
  both the default version and the lowest locally:
  `uv run --python 3.12 --isolated --with pytest --with pytest-asyncio --with pytest-bdd --with pytest-mock --with-editable . pytest tests/unit tests/features -q`.

## Way of working

- **Trunk-based.** Small commits straight to `main`; commit and push after every green change. No long-lived branches —
  if something is half done, hide it behind a setting.
- **Conventional Commits in English**, e.g. `fix(imap): check the SELECT response`, `feat(drafts): …`,
  `docs(readme): …`. Pull request titles follow the same form: pull requests are squash-merged, so the title becomes
  the commit message on `main`.
- **Stage files by name**, never `git add -A`/`git add .`; check with `git diff --cached --name-only`.
- **New dependencies** only when really needed, via `uv add`.
- Update README.md and this file in the same commit as the behaviour changes; the documentation tests fail otherwise.

## Known limitations

- Body search (`text`/`body`) with åäö finds about 95 %: words that quoted-printable wraps in the middle, words in
  base64 parts and HTML entities (`&ouml;`) are missed.
- pydantic's validation errors (a missing or wrongly typed parameter) are in English; they occur in the SDK before the
  tool function and `with_error_messages` run.
- With `PROTONMAIL_LANGUAGE=en` these are still Swedish: the tool responses (`note`, `warning`), the errors in the
  settings at start-up (`config.py`) and the log lines on stderr.
- Homoglyphs (Cyrillic `а` for `a` etc.) are not removed, and `suspicious_content` does not catch every phrasing
  (`1gn0re`, "SYSTEM: you must" …). Hidden elements via `details`, `aria-hidden`, `noscript`, svg text and
  `transform:scale(0)` are not recognised.
- Notifications (`notifications/resources/updated`) are given for new mail in watched folders and after successful
  writing tools in this server. Other clients' changes to flags, labels, moves and folders are not seen (only new mail
  in `PROTONMAIL_WATCH_MAILBOXES`), and `rename_folder` does not notify subfolders that are renamed with the parent.
- Subscriptions only exist for clients on protocols before 2026-07-28 (`resources/subscribe`, which Claude Code and
  Claude Desktop use). `subscriptions/listen` (2026-07-28) is not supported yet: the server is created with
  `subscriptions=False`, so it advertises no subscriptions to such clients. Tools, resources and prompts work on both
  protocols.
- The high-water marks for new mail and the hourly send counter live in memory and are reset on restart. If
  `check_new_emails` is cancelled after the mark has moved but before the response reached the client, those emails no
  longer count as new.
- Private APIs that can break when dependencies are upgraded (the tests cover them): `MCPServer`'s
  `_resource_manager._templates` (resource templates with their own errors) and `_lowlevel_server` (handlers for
  `resources/subscribe`, and `instructions`, which `server.apply_language` sets at start-up),
  `ServerSession._connection` (one session per connection; its public `exit_stack` forgets the session on
  disconnect) and the class swap to `_BridgeProtocol` on the aioimaplib connection. The tests reach the SDK only via
  `tests/sdk_support.py` and the server only via `_sdk.py`, so an SDK change mainly changes those two.
  `tests/snapshots/mcp_kontrakt.json` is the reference for everything a client sees (schemas, errors, capabilities);
  rewrite it with `PROTONMAIL_UPDATE_SNAPSHOT=1` only deliberately and explain every changed field in the commit
  message.
- aioimaplib: `uid("search", …)` is not supported — use `uid_search()` or `search()` + FETCH of UID. `list_emails` uses
  EXISTS + sequence ranges instead of `SEARCH ALL`, and FETCH is done in chunks of at most 200.
