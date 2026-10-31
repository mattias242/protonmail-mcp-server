"""BDD: PROTONMAIL_LANGUAGE ger klienten verktyg, promptar, resurser, instruktioner och fel på engelska.

Koden och testerna i den här ändringen skrevs av Claude (Anthropic) för
https://github.com/mattias242/protonmail-mcp-server/issues/7.
"""
import asyncio
import re

import pytest
from pydantic import ValidationError
from pytest_bdd import given, parsers, scenarios, then, when

from protonmail_mcp import config
from protonmail_mcp.server import lifespan, mcp, register_tools
from tests import sdk_support as sdk
from tests.llm_kontrakt_support import client_session, text_of

scenarios("feat_sprak.feature")


@pytest.fixture
def state(tmp_path):
    return {"overrides": {"imap_port": 1, "attachment_dir": tmp_path, "watch_enabled": False}}


def _settings(state):
    # Inställningarna läses som vid start: miljön (PROTONMAIL_LANGUAGE) och inga .env-filer
    return config.Settings(_env_file=None, username="u", password="p", **state["overrides"])


@given("ingen PROTONMAIL_LANGUAGE är satt")
def no_language(monkeypatch):
    monkeypatch.delenv("PROTONMAIL_LANGUAGE", raising=False)


@given(parsers.parse('PROTONMAIL_LANGUAGE är "{language}"'))
def language_set(monkeypatch, language):
    monkeypatch.setenv("PROTONMAIL_LANGUAGE", language)


@given("servern är skrivskyddad")
def read_only(state):
    state["overrides"]["read_only"] = True


@when("servern startar")
def server_starts(monkeypatch, state):
    """lifespan som vid en riktig start: inställningarna läses och språket sätts."""
    register_tools()
    settings = _settings(state)
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    state["settings"] = settings

    async def run():
        async with lifespan(mcp):
            state["tools"] = {t.name: t for t in await mcp.list_tools()}
            state["prompts"] = {p.name: p for p in await mcp.list_prompts()}
            state["resources"] = {r.name: r for r in await mcp.list_resources()}
            state["instructions"] = mcp.instructions

    asyncio.run(run())


@when(parsers.parse('LLM:en anropar verktyget "{tool}"'))
def llm_calls(monkeypatch, state, tool):
    async def run():
        async with client_session(monkeypatch) as session:
            monkeypatch.setattr(config, "get_settings", lambda: state["settings"])
            try:
                result = await session.call_tool(tool, {"uid": "5", "mailbox": "INBOX"})
            except sdk.MCPError as exc:
                return sdk.error_message(exc)
            assert sdk.wire(result)["isError"]
            return text_of(result)

    state["error"] = asyncio.run(run())


@then(parsers.parse('ska verktyget "{tool}" ha titeln "{title}"'))
def tool_title(state, tool, title):
    assert state["tools"][tool].title == title
    assert state["tools"][tool].annotations.title == title


@then(parsers.parse('ska beskrivningen av "{tool}" börja med "{text}"'))
def tool_description(state, tool, text):
    assert state["tools"][tool].description.startswith(text)


@then(parsers.parse('ska parametern "{param}" i "{tool}" beskrivas på engelska'))
def param_in_english(state, param, tool):
    description = state["tools"][tool].input_schema["properties"][param]["description"]
    assert description.startswith("The email's UID in mailbox")
    assert not re.search("[åäöÅÄÖ]", description)


@then(parsers.parse('ska instruktionerna börja med "{text}"'))
def instructions_start(state, text):
    assert state["instructions"].startswith(text)


@then("ska verktygsnamnen vara desamma som på svenska")
def same_names(state):
    assert set(state["tools"]) == {t.name for t in sdk.list_tools(mcp)}
    assert "move_email" in state["tools"] and "delete_email" in state["tools"]


@then(parsers.parse('ska prompten "{prompt}" ha titeln "{title}"'))
def prompt_title(state, prompt, title):
    assert state["prompts"][prompt].title == title


@then(parsers.parse('ska prompten "{prompt}" ge en text som börjar med "{text}"'))
def prompt_text(state, prompt, text):
    result = asyncio.run(mcp.get_prompt(prompt, {"uid": "5", "mailbox": "INBOX"}))
    rendered = " ".join(m.content.text for m in result.messages)
    assert rendered.startswith(text)


@then(parsers.parse('ska resursen "{resource}" ha titeln "{title}"'))
def resource_title(state, resource, title):
    assert state["resources"][resource].title == title


@then(parsers.parse('ska felet nämna "{text}"'))
def error_mentions(state, text):
    assert text in state["error"], state["error"]


@then("ska felet inte innehålla några svenska bokstäver")
def error_without_swedish(state):
    assert not re.search("[åäöÅÄÖ]", state["error"]), state["error"]


@then(parsers.parse('ska inställningarna avvisas med ett fel som nämner "{text}"'))
def settings_refused(state, text):
    with pytest.raises(ValidationError, match=text):
        _settings(state)
