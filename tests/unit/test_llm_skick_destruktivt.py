"""PROTONMAIL_ALLOW_DESTRUCTIVE: spärren mot det som flyttar, raderar eller ersätter.

Samma två lager som skrivskyddet (test_llm_skick_skrivskydd.py):
1. Verktygen genom MCP-servern: de destruktiva vägras innan något når Bridge.
   Vilka verktyg som är destruktiva avgörs av deras MCP-klassning
   (destructiveHint, utom skicken som PROTONMAIL_SEND_MODE styr), så att ett
   nytt destruktivt verktyg inte kan glömmas i policy.DESTRUCTIVE_TOOLS.
2. Skrivfunktionerna i IMAP-klienten anropade direkt: även ett verktyg som
   glömt policyn kan inte flytta, radera, byta namn eller ersätta.
"""
import logging
from email.message import EmailMessage

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

GUARD = "PROTONMAIL_ALLOW_DESTRUCTIVE=false"


def _draft() -> bytes:
    msg = EmailMessage()
    msg["To"] = "anna@exempel.se"
    msg["Subject"] = "Hej"
    msg.set_content("Hej")
    return msg.as_bytes()


# --- Inställningen -----------------------------------------------------------------

def test_default_allows_destructive():
    assert make_settings().allow_destructive is True


@pytest.mark.parametrize("raw, expected", [("false", False), ("0", False), ("true", True)])
def test_from_environment(monkeypatch, raw, expected):
    from protonmail_mcp.config import Settings

    monkeypatch.setenv("PROTONMAIL_ALLOW_DESTRUCTIVE", raw)
    assert Settings(username="u", password="p", _env_file=None).allow_destructive is expected


# --- Vilka verktyg spärren gäller --------------------------------------------------

def _destructive_by_annotation() -> set[str]:
    from protonmail_mcp.server import mcp

    registered_tools()  # registrerar verktygen
    return {
        tool.name
        for tool in sdk.list_tools(mcp)
        if tool.annotations is not None and sdk.wire(tool.annotations).get("destructiveHint")
    }


def test_destructive_tools_follow_the_annotations():
    expected = _destructive_by_annotation() - policy.SEND_TOOLS
    assert policy.DESTRUCTIVE_TOOLS == expected, (
        "ett verktyg med destructiveHint ska finnas i policy.DESTRUCTIVE_TOOLS (och tvärtom)"
    )


def test_destructive_tools_are_the_expected_ones():
    assert policy.DESTRUCTIVE_TOOLS == {
        "move_email", "delete_email", "delete_folder", "rename_folder", "remove_label", "update_draft",
    }


def test_destructive_tools_are_write_or_draft_tools():
    assert policy.DESTRUCTIVE_TOOLS <= policy.WRITE_TOOLS | policy.DRAFT_TOOLS


# --- Lager 1: verktygen genom MCP-servern -----------------------------------------

@pytest.mark.parametrize("tool", sorted(policy.DESTRUCTIVE_TOOLS))
async def test_destructive_tool_refused_before_bridge(tool):
    settings = make_settings(allow_destructive=False)
    spy = BridgeSpy()
    with spy_smtp_patch(spy), pytest.raises(ToolError) as exc:
        # Argumenten spelar ingen roll: vägran kommer före tolkningen
        await call_tool(tool, {}, make_app(settings, spy), settings)
    text = str(exc.value)
    assert GUARD in text
    assert tool in text
    assert "Inget har ändrats" in text
    assert not spy.contacted


@pytest.mark.parametrize(
    "tool",
    sorted((policy.WRITE_TOOLS | policy.DRAFT_TOOLS | policy.SEND_TOOLS) - policy.DESTRUCTIVE_TOOLS),
)
def test_other_writes_drafts_and_sends_pass(tool):
    policy.check_tool(make_settings(allow_destructive=False), tool)  # kastar inte


@pytest.mark.parametrize("tool", sorted(policy.DESTRUCTIVE_TOOLS))
def test_destructive_tools_pass_by_default(tool):
    policy.check_tool(make_settings(), tool)  # kastar inte


def test_read_only_is_reported_first():
    settings = make_settings(read_only=True, allow_destructive=False)
    with pytest.raises(ValueError, match="PROTONMAIL_READ_ONLY"):
        policy.check_tool(settings, "delete_email")


def test_disabled_send_mode_is_reported_first_for_update_draft():
    settings = make_settings(send_mode="disabled", allow_destructive=False)
    with pytest.raises(ValueError, match="PROTONMAIL_SEND_MODE=disabled"):
        policy.check_tool(settings, "update_draft")


def test_error_points_to_what_is_still_allowed():
    with pytest.raises(ValueError) as exc:
        policy.check_tool(make_settings(allow_destructive=False), "update_draft")
    text = str(exc.value)
    assert "create_draft" in text
    assert "Proton" in text


def test_mock_settings_are_not_treated_as_refusing():
    """En attrapp (MagicMock) som settings ska inte räknas som avstängd."""
    from unittest.mock import MagicMock

    policy.check_tool(MagicMock(read_only=False, send_mode="send"), "delete_email")  # kastar inte


# --- Lager 2: skrivfunktionerna i IMAP-klienten -----------------------------------

DESTRUCTIVE_IMAP_CALLS = {
    "move_message": lambda imap: imap.move_message("INBOX", "5", "Archive"),
    "delete_message": lambda imap: imap.delete_message("INBOX", "5"),
    "delete_message_permanent": lambda imap: imap.delete_message("Trash", "5", permanent=True),
    "delete_folder": lambda imap: imap.delete_folder("Folders/A"),
    "rename_folder": lambda imap: imap.rename_folder("Folders/A", "Folders/B"),
    "bulk_move": lambda imap: imap_bulk.move(imap, "INBOX", ["5", "6"], "Archive"),
    "bulk_delete": lambda imap: imap_bulk.delete(imap, "INBOX", ["5"]),
    "bulk_delete_permanent": lambda imap: imap_bulk.delete(imap, "Trash", ["5"], permanent=True),
    "remove_label": lambda imap: imap_labels.remove_label(imap, "INBOX", ["5"], "Labels/A"),
    "replace_draft": lambda imap: imap_drafts.replace_draft(imap, "7", _draft(), "<a@b>"),
}


@pytest.mark.parametrize("name", sorted(DESTRUCTIVE_IMAP_CALLS))
async def test_imap_refuses_destructive_without_contact(name):
    spy = BridgeSpy()
    imap = spy_imap(make_settings(allow_destructive=False), spy)
    with pytest.raises(ValueError, match=GUARD):
        await DESTRUCTIVE_IMAP_CALLS[name](imap)
    assert spy.imap_calls == []


@pytest.mark.parametrize("name", sorted(DESTRUCTIVE_IMAP_CALLS))
async def test_imap_unlocked_path_is_also_refused(name):
    """Även anrop via __wrapped__ (utan _serialized-låset) går genom policyn."""
    spy = BridgeSpy()
    imap = spy_imap(make_settings(allow_destructive=False), spy)
    functions = {
        "move_message": lambda: type(imap).move_message.__wrapped__(imap, "INBOX", "5", "Archive"),
        "delete_message": lambda: type(imap).delete_message.__wrapped__(imap, "INBOX", "5"),
        "delete_message_permanent": lambda: type(imap).delete_message.__wrapped__(imap, "Trash", "5", True),
        "delete_folder": lambda: type(imap).delete_folder.__wrapped__(imap, "Folders/A"),
        "rename_folder": lambda: type(imap).rename_folder.__wrapped__(imap, "Folders/A", "Folders/B"),
        "bulk_move": lambda: imap_bulk.move.__wrapped__(imap, "INBOX", ["5"], "Archive"),
        "bulk_delete": lambda: imap_bulk.delete.__wrapped__(imap, "INBOX", ["5"]),
        "bulk_delete_permanent": lambda: imap_bulk.delete.__wrapped__(imap, "Trash", ["5"], True),
        "remove_label": lambda: imap_labels.remove_label.__wrapped__(imap, "INBOX", ["5"], "Labels/A"),
        "replace_draft": lambda: imap_drafts.replace_draft.__wrapped__(imap, "7", _draft(), "<a@b>"),
    }
    with pytest.raises(ValueError, match=GUARD):
        await functions[name]()
    assert spy.imap_calls == []


# Skrivningar som inte förstör något: spärren släpper igenom dem, så de når
# (attrappens) Bridge. remove_sent_draft hör till skicket, som skickläget styr:
# det skickade utkastet finns kvar i Sent.
OTHER_IMAP_CALLS = {
    "set_flags": lambda imap: imap.set_flags("INBOX", "5", r"(\Seen)", True),
    "bulk_set_flags": lambda imap: imap_bulk.set_flags(imap, "INBOX", ["5"], r"(\Seen)", True),
    "set_starred": lambda imap: imap_labels.set_starred(imap, "INBOX", ["5"], False),
    "add_label": lambda imap: imap_labels.add_label(imap, "INBOX", ["5"], "Labels/A"),
    "create_folder": lambda imap: imap.create_folder("Folders/A"),
    "append_draft": lambda imap: imap_drafts.append_draft(imap, _draft(), "<a@b>"),
    "remove_sent_draft": lambda imap: imap_drafts.remove_sent_draft(imap, "7"),
}


@pytest.mark.parametrize("name", sorted(OTHER_IMAP_CALLS))
async def test_imap_lets_other_writes_through(name):
    spy = BridgeSpy()
    imap = spy_imap(make_settings(allow_destructive=False), spy)
    with pytest.raises(AssertionError, match="Bridge"):
        await OTHER_IMAP_CALLS[name](imap)
    assert spy.imap_calls  # nådde Bridge, alltså inte vägrad av policyn


async def test_refusal_is_audited(caplog):
    import json

    caplog.set_level(logging.INFO, logger="protonmail_mcp.audit")
    spy = BridgeSpy()
    imap = spy_imap(make_settings(allow_destructive=False), spy)
    with pytest.raises(ValueError, match=GUARD):
        await imap.delete_message("Trash", "5", permanent=True)
    entries = [
        json.loads(r.getMessage())
        for r in caplog.records
        if r.name == "protonmail_mcp.audit" and r.levelno == logging.INFO
    ]
    (entry,) = entries
    assert entry["action"] == "delete_message"
    assert entry["result"] == "error"
    assert entry["permanent"] is True
    assert GUARD in entry["error"]
