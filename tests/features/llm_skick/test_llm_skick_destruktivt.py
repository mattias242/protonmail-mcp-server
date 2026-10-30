"""BDD-steg för llm_skick_destruktivt.feature (gemensamma steg i conftest.py).

Koden och testerna i den här ändringen skrevs av Claude (Anthropic) för
https://github.com/mattias242/protonmail-mcp-server/issues/5.
"""
import asyncio
import re
from unittest.mock import AsyncMock, patch

from pytest_bdd import given, parsers, scenarios, then, when

from tests.llm_skick_support import make_settings, registered_tools

scenarios("llm_skick_destruktivt.feature")


def _names(text: str) -> list[str]:
    return re.findall(r'"([^"]+)"', text)


@given("en IMAP-klient för ett konto utan begränsningar")
def imap_without_limits(state):
    pass


@given(parsers.parse('Bridge sparar utkast i Drafts med UID "{uid}"'))
def bridge_saves_drafts(state, monkeypatch, uid):
    from protonmail_mcp import imap_drafts

    saved = []

    async def store(imap, message, message_id):
        saved.append(message)
        return uid

    async def ensure_connected():
        return None

    monkeypatch.setattr(imap_drafts, "_store_draft", store)
    state["saved_drafts"] = saved

    def setup(app):
        app.imap._ensure_connected = AsyncMock(side_effect=ensure_connected)
    state["app_setup"].append(setup)


@then("ska utkastet ha sparats i Drafts")
def draft_saved(state):
    assert len(state["saved_drafts"]) == 1


@then("ska anropet inte ha vägrats av destruktivspärren")
def not_refused_by_destructive_guard(state):
    # Anropet kan ändå falla på något annat (attrappen vägrar Bridge-kontakt,
    # argument saknas), men aldrig på PROTONMAIL_ALLOW_DESTRUCTIVE
    assert "PROTONMAIL_ALLOW_DESTRUCTIVE" not in str(state["error"] or "")


@when("klienten hämtar verktygslistan")
def client_lists_tools(state):
    """tools/list som en klient får det, med scenariots inställningar."""
    from protonmail_mcp import config
    from protonmail_mcp.server import mcp

    registered_tools()
    settings = make_settings(**state["overrides"])
    with patch.object(config, "get_settings", return_value=settings):
        state["tools"] = {tool.name for tool in asyncio.run(mcp.list_tools())}


@then(parsers.parse("ska verktygslistan sakna {names}"))
def tools_missing(state, names):
    for name in _names(names):
        assert name not in state["tools"], f"{name} syns i verktygslistan"


@then(parsers.parse("ska verktygslistan innehålla {names}"))
def tools_present(state, names):
    for name in _names(names):
        assert name in state["tools"], f"{name} saknas i verktygslistan"
