"""PROTONMAIL_ALLOW_DESTRUCTIVE=false: de destruktiva verktygen syns inte i tools/list.

Modellen ska aldrig se move_email, delete_email, delete_folder, rename_folder
och remove_label när de är avstängda; anropas de ändå med namn vägras de som
förut. Verktygen är fortfarande registrerade (namnspärren, dokumentations-
och annoteringstesterna ser dem), bara utelämnade ur listan.

Koden och testerna i den här ändringen skrevs av Claude (Anthropic) för
https://github.com/mattias242/protonmail-mcp-server/issues/5.
"""
from unittest.mock import patch

import pytest

from protonmail_mcp import config, policy
from protonmail_mcp.server import mcp
from tests import sdk_support as sdk
from tests.llm_kontrakt_support import client_session, text_of
from tests.llm_skick_support import make_settings, registered_tools


def _listed(settings) -> set[str]:
    registered_tools()
    with patch.object(config, "get_settings", return_value=settings):
        import asyncio

        return {tool.name for tool in asyncio.run(mcp.list_tools())}


def test_hidden_tools_empty_by_default():
    assert policy.hidden_tools(make_settings()) == frozenset()


def test_hidden_tools_when_destructive_disabled():
    assert policy.hidden_tools(make_settings(allow_destructive=False)) == policy.DESTRUCTIVE_TOOLS


def test_hidden_tools_without_settings_is_fail_closed():
    assert policy.hidden_tools(None) == policy.DESTRUCTIVE_TOOLS


def test_tool_list_hides_destructive_tools():
    listed = _listed(make_settings(allow_destructive=False))
    assert listed & policy.DESTRUCTIVE_TOOLS == set()
    assert listed == registered_tools() - policy.DESTRUCTIVE_TOOLS


def test_tool_list_complete_by_default():
    assert _listed(make_settings()) == registered_tools()


def test_tools_stay_registered_when_hidden():
    """Namnspärren i call_tool och testerna som läser registret ser dem fortfarande."""
    _listed(make_settings(allow_destructive=False))
    assert policy.DESTRUCTIVE_TOOLS <= {tool.name for tool in sdk.list_tools(mcp)}


async def test_tool_list_hides_when_settings_cannot_be_read():
    registered_tools()
    with patch.object(config, "get_settings", side_effect=ValueError("ingen .env")):
        listed = {tool.name for tool in await mcp.list_tools()}
    assert listed & policy.DESTRUCTIVE_TOOLS == set()
    assert "list_emails" in listed


async def test_client_does_not_see_hidden_tools_and_is_refused_by_name(monkeypatch):
    """Över ett riktigt klientpass: tools/list saknar verktygen, tools/call vägras."""
    monkeypatch.setenv("PROTONMAIL_ALLOW_DESTRUCTIVE", "false")
    async with client_session(monkeypatch) as session:
        names = {tool.name for tool in (await session.list_tools()).tools}
        assert names & policy.DESTRUCTIVE_TOOLS == set()
        assert {"list_emails", "add_label", "create_draft", "update_draft", "send_email"} <= names
        try:
            result = await session.call_tool("delete_email", {"uid": "5", "mailbox": "INBOX"})
        except sdk.MCPError as exc:
            text = sdk.error_message(exc)
        else:
            assert sdk.wire(result)["isError"]
            text = text_of(result)
        assert "PROTONMAIL_ALLOW_DESTRUCTIVE=false" in text


async def test_client_sees_every_tool_by_default(monkeypatch):
    monkeypatch.delenv("PROTONMAIL_ALLOW_DESTRUCTIVE", raising=False)
    async with client_session(monkeypatch) as session:
        names = {tool.name for tool in (await session.list_tools()).tools}
    assert names == registered_tools()


@pytest.mark.parametrize("tool", sorted(policy.DESTRUCTIVE_TOOLS))
def test_every_hidden_tool_is_also_refused_by_name(tool):
    settings = make_settings(allow_destructive=False)
    assert tool in policy.hidden_tools(settings)
    with pytest.raises(ValueError, match="PROTONMAIL_ALLOW_DESTRUCTIVE=false"):
        policy.check_tool(settings, tool)
