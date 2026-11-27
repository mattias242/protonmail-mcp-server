"""BDD steps for policy_hidden_tools.feature (a real in-memory MCP session).

Based on the tests in pull request #6 by Mitch Lindgren.
"""
import asyncio
import re

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from tests import sdk_support as sdk
from tests.llm_kontrakt_support import client_session, text_of

scenarios("policy_hidden_tools.feature")


def _names(text: str) -> list[str]:
    return re.findall(r'"([^"]+)"', text)


@pytest.fixture
def ctx():
    return {}


@given(parsers.parse('the server runs with allow_destructive set to "{value}"'))
def allow_destructive(monkeypatch, value):
    monkeypatch.setenv("PROTONMAIL_ALLOW_DESTRUCTIVE", value)


@given(parsers.parse('the language is "{language}"'))
def language(monkeypatch, language):
    monkeypatch.setenv("PROTONMAIL_LANGUAGE", language)


@when("the client lists the tools")
def list_tools(ctx, monkeypatch):
    async def run():
        async with client_session(monkeypatch) as session:
            return (await session.list_tools()).tools

    ctx["tools"] = {tool.name: tool for tool in asyncio.run(run())}


@when(parsers.parse('the client calls "{tool}" by name'))
def call_by_name(ctx, monkeypatch, tool):
    async def run():
        async with client_session(monkeypatch) as session:
            try:
                result = await session.call_tool(tool, {"uid": "5", "mailbox": "INBOX"})
            except sdk.MCPError as exc:
                return sdk.error_message(exc)
            assert sdk.wire(result)["isError"]
            return text_of(result)

    ctx["error"] = asyncio.run(run())


@then(parsers.parse("the tool list lacks {names}"))
def lacks(ctx, names):
    for name in _names(names):
        assert name not in ctx["tools"], name


@then(parsers.parse("the tool list contains {names}"))
def contains(ctx, names):
    for name in _names(names):
        assert name in ctx["tools"], name


@then(parsers.parse('the tool "{name}" has the title "{title}"'))
def title(ctx, name, title):
    assert ctx["tools"][name].title == title


@then(parsers.parse('the call is refused with an error that mentions "{text}"'))
def refused(ctx, text):
    assert text in ctx["error"], ctx["error"]
