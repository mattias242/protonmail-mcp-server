"""PROTONMAIL_ALLOW_DESTRUCTIVE=false: the destructive tools are left out of tools/list.

The model should never see the tools the guard refuses; called by name anyway,
they are refused as before. The tools stay registered (the name guard, the
documentation and annotation tests still see them); only the list is filtered.

Ported from pull request #6 by Mitch Lindgren (commit 7b9ee5e), adapted to the
current main, where DESTRUCTIVE_TOOLS also contains update_draft.
"""
import asyncio
from unittest.mock import patch

import pytest

from protonmail_mcp import config, policy
from protonmail_mcp.server import mcp
from tests import sdk_support as sdk
from tests.llm_skick_support import make_settings, registered_tools


def _listed(settings) -> set[str]:
    registered_tools()
    with patch.object(config, "get_settings", return_value=settings):
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
    """The name guard in call_tool and the tests that read the registry still see them."""
    _listed(make_settings(allow_destructive=False))
    assert policy.DESTRUCTIVE_TOOLS <= {tool.name for tool in sdk.list_tools(mcp)}


async def test_tool_list_hides_when_settings_cannot_be_read():
    registered_tools()
    with patch.object(config, "get_settings", side_effect=ValueError("no .env")):
        listed = {tool.name for tool in await mcp.list_tools()}
    assert listed & policy.DESTRUCTIVE_TOOLS == set()
    assert "list_emails" in listed


@pytest.mark.parametrize("tool", sorted(policy.DESTRUCTIVE_TOOLS))
def test_every_hidden_tool_is_also_refused_by_name(tool):
    settings = make_settings(allow_destructive=False)
    assert tool in policy.hidden_tools(settings)
    with pytest.raises(ValueError, match="PROTONMAIL_ALLOW_DESTRUCTIVE=false"):
        policy.check_tool(settings, tool)
