"""PROTONMAIL_ALLOW_DESTRUCTIVE=false: flytt, radering, borttagning och namnbyte vägras.

Läsning, läst/oläst, stjärna, add_label, create_folder, utkast och skick (enligt
PROTONMAIL_SEND_MODE) påverkas inte. Två lager testas, som för skrivskyddet:
1. Verktygen genom MCP-servern (mcp.call_tool) vägras innan Bridge nås.
2. IMAP-funktionerna anropade direkt vägras också, så att ett verktyg som
   glömt policyn inte kan radera.

Koden och testerna i den här ändringen skrevs av Claude (Anthropic) för
https://github.com/mattias242/protonmail-mcp-server/issues/5.
"""
from email.message import EmailMessage
from unittest.mock import patch

import pytest

from protonmail_mcp import imap_bulk, imap_drafts, imap_labels, policy
from tests import sdk_support as sdk
from tests.llm_skick_support import (
    BridgeSpy,
    call_tool,
    make_app,
    make_settings,
    registered_tools,
    spy_imap,
    spy_smtp_patch,
)
from tests.sdk_support import ToolError

DESTRUCTIVE_TOOLS = ["move_email", "delete_email", "delete_folder", "rename_folder", "remove_label"]
UNAFFECTED_TOOLS = [
    "list_emails",
    "mark_email_read",
    "mark_email_unread",
    "flag_email",
    "unflag_email",
    "add_label",
    "create_folder",
    "create_draft",
    "create_reply_draft",
    "create_forward_draft",
    "update_draft",
    "send_email",
    "reply_to_email",
    "forward_email",
    "send_draft",
]


def _draft() -> bytes:
    msg = EmailMessage()
    msg["To"] = "anna@exempel.se"
    msg["Subject"] = "Hej"
    msg.set_content("Hej")
    return msg.as_bytes()


def test_default_allows_destructive():
    assert make_settings().allow_destructive is True


def test_setting_from_environment(monkeypatch):
    from protonmail_mcp.config import Settings

    monkeypatch.setenv("PROTONMAIL_ALLOW_DESTRUCTIVE", "false")
    assert Settings(username="u", password="p", _env_file=None).allow_destructive is False


def test_destructive_tools_are_the_destructive_mailbox_tools_except_update_draft():
    """Listan i policy.py ska vara exakt verktygen med destructiveHint som
    inte skickar (skick styrs av PROTONMAIL_SEND_MODE), utom update_draft,
    som bara ersätter serverns eget utkast."""
    registered_tools()  # registrerar verktygen
    from protonmail_mcp.server import mcp

    annotated = set()
    for tool in sdk.list_tools(mcp):
        hints = sdk.wire(tool.annotations)
        if hints.get("destructiveHint") and not hints.get("openWorldHint"):
            annotated.add(tool.name)
    assert policy.DESTRUCTIVE_TOOLS == annotated - {"update_draft"}
    assert policy.DESTRUCTIVE_TOOLS <= policy.WRITE_TOOLS


# --- Lager 1: verktygen genom MCP-servern -------------------------------------

@pytest.mark.parametrize("tool", DESTRUCTIVE_TOOLS)
async def test_destructive_tool_refused_before_bridge(tool):
    settings = make_settings(allow_destructive=False)
    spy = BridgeSpy()
    with spy_smtp_patch(spy), pytest.raises(ToolError) as exc:
        # Argumenten spelar ingen roll: vägran kommer före tolkningen
        await call_tool(tool, {}, make_app(settings, spy), settings)
    text = str(exc.value)
    assert "PROTONMAIL_ALLOW_DESTRUCTIVE=false" in text
    assert "Inget har ändrats" in text
    assert not spy.contacted, (spy.imap_calls, spy.smtp_connections)


@pytest.mark.parametrize("tool", UNAFFECTED_TOOLS)
def test_other_tools_not_refused(tool):
    policy.check_tool(make_settings(allow_destructive=False), tool)  # kastar inte


@pytest.mark.parametrize("tool", DESTRUCTIVE_TOOLS)
def test_default_does_not_refuse(tool):
    policy.check_tool(make_settings(), tool)  # kastar inte


def test_read_only_takes_precedence():
    settings = make_settings(read_only=True, allow_destructive=False)
    with pytest.raises(ValueError, match="PROTONMAIL_READ_ONLY"):
        policy.check_tool(settings, "delete_email")


@pytest.mark.parametrize("mode", ["send", "draft_only", "disabled"])
def test_independent_of_send_mode(mode):
    settings = make_settings(allow_destructive=False, send_mode=mode)
    with pytest.raises(ValueError, match="PROTONMAIL_ALLOW_DESTRUCTIVE=false"):
        policy.check_tool(settings, "delete_email")


async def test_with_draft_only_the_agent_can_still_write_drafts(monkeypatch):
    """Profilen för en agent utan uppsikt: läsa och skriva utkast, inget oåterkalleligt."""
    spy = BridgeSpy()
    imap = spy_imap(make_settings(allow_destructive=False, send_mode="draft_only"), spy)

    async def connected():
        return None

    async def store(imap, message, message_id):
        return "12"

    imap._ensure_connected = connected
    monkeypatch.setattr(imap_drafts, "_store_draft", store)
    assert await imap_drafts.append_draft(imap, _draft(), "<a@b>") == "12"


# --- Lager 2: IMAP-funktionerna direkt ----------------------------------------

IMAP_DESTRUCTIVE = {
    "IMAPClient.move_message": lambda imap: imap.move_message("INBOX", "5", "Archive"),
    "IMAPClient.delete_message": lambda imap: imap.delete_message("INBOX", "5"),
    "IMAPClient.delete_message permanent": lambda imap: imap.delete_message("Trash", "5", permanent=True),
    "IMAPClient.delete_folder": lambda imap: imap.delete_folder("Folders/Ny"),
    "IMAPClient.delete_folder force": lambda imap: imap.delete_folder("Folders/Ny", force=True),
    "IMAPClient.rename_folder": lambda imap: imap.rename_folder("Folders/Ny", "Folders/Nyare"),
    "imap_bulk.move": lambda imap: imap_bulk.move(imap, "INBOX", ["5", "6"], "Archive"),
    "imap_bulk.delete": lambda imap: imap_bulk.delete(imap, "INBOX", ["5", "6"]),
    "imap_bulk.delete permanent": lambda imap: imap_bulk.delete(imap, "Trash", ["5", "6"], permanent=True),
    "imap_labels.remove_label": lambda imap: imap_labels.remove_label(imap, "INBOX", ["5"], "Arbete"),
}

IMAP_UNAFFECTED = {
    "IMAPClient.set_flags": lambda imap: imap.set_flags("INBOX", "5", r"(\Seen)", add=True),
    "IMAPClient.create_folder": lambda imap: imap.create_folder("Folders/Ny"),
    "imap_bulk.set_flags": lambda imap: imap_bulk.set_flags(imap, "INBOX", ["5", "6"], r"(\Seen)", add=False),
    "imap_labels.add_label": lambda imap: imap_labels.add_label(imap, "INBOX", ["5"], "Arbete"),
    "imap_labels.set_starred": lambda imap: imap_labels.set_starred(imap, "INBOX", ["5"], True),
    "imap_drafts.append_draft": lambda imap: imap_drafts.append_draft(imap, _draft(), "<a@b>"),
    # update_draft lägger den gamla versionen i Trash; send_draft tar bort sitt
    # skickade utkast. Båda är serverns egen städning och vägras inte.
    "imap_drafts.replace_draft": lambda imap: imap_drafts.replace_draft(imap, "7", _draft(), "<a@b>"),
    "imap_drafts.remove_sent_draft": lambda imap: imap_drafts.remove_sent_draft(imap, "7"),
}


@pytest.mark.parametrize("name", sorted(IMAP_DESTRUCTIVE))
async def test_imap_destructive_refused_without_contact(name):
    spy = BridgeSpy()
    imap = spy_imap(make_settings(allow_destructive=False), spy)
    with pytest.raises(ValueError, match="PROTONMAIL_ALLOW_DESTRUCTIVE=false"):
        await IMAP_DESTRUCTIVE[name](imap)
    assert spy.imap_calls == []


@pytest.mark.parametrize("name", sorted(IMAP_UNAFFECTED))
async def test_imap_other_writes_not_refused(name):
    spy = BridgeSpy()
    imap = spy_imap(make_settings(allow_destructive=False), spy)
    try:
        await IMAP_UNAFFECTED[name](imap)
    except Exception as exc:  # noqa: BLE001 — attrappen vägrar Bridge-kontakt, men policyn får inte vägra
        assert "PROTONMAIL_ALLOW_DESTRUCTIVE" not in str(exc)
    assert spy.imap_calls, "anropet nådde aldrig attrappen"


async def test_imap_refusal_does_not_validate_first():
    """Vägran kommer först, även för ett ogiltigt anrop — inget tolkas eller skickas."""
    spy = BridgeSpy()
    imap = spy_imap(make_settings(allow_destructive=False), spy)
    with pytest.raises(ValueError, match="PROTONMAIL_ALLOW_DESTRUCTIVE=false"):
        await imap.delete_message("INBOX", "inte-ett-uid", permanent=True)
    assert spy.imap_calls == []


async def test_refusal_is_audited():
    spy = BridgeSpy()
    settings = make_settings(allow_destructive=False)
    imap = spy_imap(settings, spy)
    with patch("protonmail_mcp.audit.record") as record, pytest.raises(ValueError):
        await imap.delete_message("Trash", "5", permanent=True)
    record.assert_called_once()
    args, kwargs = record.call_args
    assert args[1] == "delete_message"
    assert kwargs["result"] == "error"
    assert "PROTONMAIL_ALLOW_DESTRUCTIVE=false" in str(kwargs["error"])
