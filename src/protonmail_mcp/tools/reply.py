from mcp.server.fastmcp import Context

from ..server import mcp
from ..email_parser import parse_email
from . import _get_app


@mcp.tool()
async def reply_to_email(
    ctx: Context,
    uid: str,
    body: str,
    mailbox: str = "INBOX",
    body_html: str | None = None,
) -> bool:
    """Svara på ett e-post. Sätter In-Reply-To, References och Re:-prefix automatiskt.

    Args:
        uid: UID för e-posten att svara på
        body: Svarstexten i klartext
        mailbox: Mappnamn (standard: INBOX)
        body_html: Valfri HTML-version av svarstexten
    """
    app = _get_app(ctx)
    raw = await app.imap.get_message(mailbox, uid)
    if raw is None:
        return False

    original = parse_email(raw, body_format="full")

    # Sätt Re:-prefix om det inte redan finns
    orig_subject = original["subject"]
    if orig_subject.lower().startswith("re:"):
        subject = orig_subject
    else:
        subject = f"Re: {orig_subject}"

    # Reply-to: svara till avsändaren
    reply_to_addr = original["from"]

    # Bygg In-Reply-To och References headers
    message_id = original["message_id"]
    additional_headers: dict[str, str] = {}
    if message_id:
        additional_headers["In-Reply-To"] = message_id
        # References: kedja vidare befintliga references + original message-id
        existing_refs = dict(original.get("headers", {})).get("References", "")
        if existing_refs:
            additional_headers["References"] = f"{existing_refs} {message_id}"
        else:
            additional_headers["References"] = message_id

    return await app.smtp.send_email(
        to=reply_to_addr,
        subject=subject,
        body=body,
        body_html=body_html,
        additional_headers=additional_headers,
    )


@mcp.tool()
async def forward_email(
    ctx: Context,
    uid: str,
    to: str | list[str],
    body: str = "",
    mailbox: str = "INBOX",
) -> bool:
    """Vidarebefordra ett e-post. Sätter Fwd:-prefix och citerar originalet.

    Args:
        uid: UID för e-posten att vidarebefordra
        to: Mottagaradress(er)
        body: Valfri text att lägga till ovanför det vidarebefordrade meddelandet
        mailbox: Mappnamn (standard: INBOX)
    """
    app = _get_app(ctx)
    raw = await app.imap.get_message(mailbox, uid)
    if raw is None:
        return False

    original = parse_email(raw, body_format="full")

    subject = f"Fwd: {original['subject']}"

    # Bygg vidarebefordrad body med citat
    orig_body = original.get("body_plain", "")
    forwarded = (
        f"{body}\n\n"
        f"---------- Forwarded message ----------\n"
        f"From: {original['from']}\n"
        f"Date: {original['date']}\n"
        f"Subject: {original['subject']}\n\n"
        f"{orig_body}"
    )

    return await app.smtp.send_email(
        to=to,
        subject=subject,
        body=forwarded,
    )
