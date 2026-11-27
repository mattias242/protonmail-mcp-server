"""BDD-steg för feat_sprak_modell.feature: instruktioner och prompttext per språk."""
import asyncio

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from protonmail_mcp import i18n, i18n_en
from tests import sdk_support as sdk
from tests.llm_kontrakt_support import client_session

scenarios("feat_sprak_modell.feature")


@pytest.fixture
def state():
    return {}


@pytest.fixture(autouse=True)
def restore_language():
    """apply_language ändrar serverns instruktioner; återställ efter varje scenario."""
    from protonmail_mcp import server

    yield
    server.apply_language("sv")


@given("servern startas utan språkinställning")
def no_language(monkeypatch):
    monkeypatch.delenv("PROTONMAIL_LANGUAGE", raising=False)


@given(parsers.parse('servern startas med språket "{language}"'))
def with_language(monkeypatch, language):
    monkeypatch.setenv("PROTONMAIL_LANGUAGE", language)


@given("den svenska texten i säkerhetsreglerna har ändrats sedan översättningen")
def stale_safety(monkeypatch):
    from protonmail_mcp import prompts

    texts = dict(i18n_en.TEXTS)
    del texts[i18n.sv_hash(prompts._SAFETY)]
    monkeypatch.setattr(i18n_en, "TEXTS", texts)


def _start(monkeypatch):
    """Som server.main(): inställningarna läses och språket sätts innan klienten ansluter."""
    from protonmail_mcp import server
    from tests.llm_kontrakt_support import use_test_settings

    settings = use_test_settings(monkeypatch)
    server.apply_language(settings.language)


@when("klienten ansluter")
def connect(state, monkeypatch):
    async def run():
        async with client_session(monkeypatch) as session:
            return sdk.init_info(session)["instructions"]

    _start(monkeypatch)
    state["instructions"] = asyncio.run(run())


@when(parsers.parse('klienten hämtar prompten "{name}" för mappen "{mailbox}"'))
def get_prompt(state, monkeypatch, name, mailbox):
    async def run():
        async with client_session(monkeypatch) as session:
            result = await session.get_prompt(name, {"mailbox": mailbox})
            return result.messages[0].content.text

    _start(monkeypatch)
    state["prompt"] = asyncio.run(run())


@then(parsers.parse('ska instruktionerna börja med "{text}"'))
def instructions_start(state, text):
    assert state["instructions"].startswith(text), state["instructions"][:80]


@then(parsers.parse('ska instruktionerna nämna "{text}"'))
def instructions_mention(state, text):
    assert text in state["instructions"]


@then(parsers.parse('ska prompten börja med "{text}"'))
def prompt_starts(state, text):
    assert state["prompt"].startswith(text.replace('\\"', '"')), state["prompt"][:80]


@then(parsers.parse('ska prompten nämna "{text}"'))
def prompt_mentions(state, text):
    assert text in state["prompt"]
