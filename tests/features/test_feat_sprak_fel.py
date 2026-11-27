"""BDD-steg för feat_sprak_fel.feature: felmeddelanden per språk genom MCP-servern."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from tests.llm_skick_support import BridgeSpy, call_tool, make_app, make_settings, spy_smtp_patch

scenarios("feat_sprak_fel.feature")


@pytest.fixture
def state():
    return {"overrides": {}, "setup": []}


@given("servern körs med felmeddelanden utan språkinställning")
def no_language(state):
    pass


@given(parsers.parse('servern körs med felmeddelanden på språket "{language}"'))
def with_language(state, language):
    state["overrides"]["language"] = language


@given("kontot är skrivskyddat")
def read_only(state):
    state["overrides"]["read_only"] = True


@given("Bridge går inte att nå")
def unreachable(state):
    def setup(app):
        app.imap.list_mailboxes = AsyncMock(side_effect=ConnectionRefusedError(111, "Connection refused"))
    state["setup"].append(setup)


def _call(state, tool, arguments):
    settings = make_settings(**state["overrides"])
    spy = BridgeSpy()
    app = make_app(settings, spy)
    for setup in state["setup"]:
        setup(app)
    try:
        with spy_smtp_patch(spy):
            asyncio.run(call_tool(tool, arguments, app, settings))
    except Exception as exc:  # noqa: BLE001 — scenariot granskar felet
        state["error"] = str(exc)
    else:
        state["error"] = None


@when("LLM:en flyttar ett mejl")
def move(state):
    _call(state, "move_email", {"uid": "5", "mailbox": "INBOX", "target_mailbox": "Archive"})


@when(parsers.parse('LLM:en läser ett mejl med UID "{uid}"'))
def read_uid(state, uid):
    _call(state, "get_email", {"uid": uid, "mailbox": "INBOX"})


@when("LLM:en listar mapparna")
def list_mailboxes(state):
    _call(state, "list_mailboxes", {})


@then(parsers.parse('ska felet börja med "{text}"'))
def error_starts(state, text):
    assert state["error"] is not None
    assert state["error"].startswith(text), state["error"][:120]


@then(parsers.parse('ska felet nämna "{text}"'))
def error_mentions(state, text):
    assert text in (state["error"] or ""), state["error"]


@then(parsers.parse('ska felet inte nämna "{text}"'))
def error_not_mentions(state, text):
    assert text not in (state["error"] or ""), state["error"]
