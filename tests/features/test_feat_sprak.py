"""BDD-steg för feat_sprak.feature: PROTONMAIL_LANGUAGE som klienten ser det."""
import asyncio

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from protonmail_mcp import i18n_en
from tests.llm_kontrakt_support import client_session

scenarios("feat_sprak.feature")


@pytest.fixture
def state():
    return {}


@given("servern körs utan språkinställning")
def no_language(monkeypatch):
    monkeypatch.delenv("PROTONMAIL_LANGUAGE", raising=False)


@given(parsers.parse('servern körs med språket "{language}"'))
def with_language(monkeypatch, language):
    monkeypatch.setenv("PROTONMAIL_LANGUAGE", language)


@given(parsers.parse('den svenska beskrivningen av "{name}" har ändrats sedan översättningen'))
def stale_translation(monkeypatch, name):
    tools = dict(i18n_en.TOOLS)
    tools[name] = tools[name]._replace(sv="000000000000")
    monkeypatch.setattr(i18n_en, "TOOLS", tools)


def _list(monkeypatch, method):
    async def run():
        async with client_session(monkeypatch) as session:
            if method == "resources":
                return (await session.list_resources()).resources, (
                    await session.list_resource_templates()
                ).resource_templates
            return getattr(await getattr(session, f"list_{method}")(), method)

    return asyncio.run(run())


@when("klienten listar verktygen")
def list_tools(state, monkeypatch):
    state["tools"] = {t.name: t for t in _list(monkeypatch, "tools")}


@when("klienten listar promptarna")
def list_prompts(state, monkeypatch):
    state["prompts"] = {p.name: p for p in _list(monkeypatch, "prompts")}


@when("klienten listar resurserna")
def list_resources(state, monkeypatch):
    resources, templates = _list(monkeypatch, "resources")
    state["resources"] = {str(r.uri): r for r in resources}
    state["templates"] = {t.uri_template: t for t in templates}


@then(parsers.parse('ska verktyget "{name}" ha titeln "{title}"'))
def tool_title(state, name, title):
    assert state["tools"][name].title == title


@then(parsers.parse('ska annoteringen för "{name}" ha titeln "{title}"'))
def annotation_title(state, name, title):
    assert state["tools"][name].annotations.title == title


@then(parsers.parse('ska beskrivningen av "{name}" börja med "{text}"'))
def description_starts(state, name, text):
    assert state["tools"][name].description.startswith(text)


@then("ska inget verktyg ha bytt namn")
def names_unchanged(state):
    assert set(state["tools"]) == set(i18n_en.TOOLS)


@then(parsers.parse('ska parametern "{param}" i "{name}" beskrivas med "{text}"'))
def param_description(state, param, name, text):
    assert text in state["tools"][name].input_schema["properties"][param]["description"]


@then(parsers.parse('ska prompten "{name}" ha titeln "{title}"'))
def prompt_title(state, name, title):
    assert state["prompts"][name].title == title


@then(parsers.parse('ska argumentet "{arg}" i "{name}" beskrivas med "{text}"'))
def prompt_argument(state, arg, name, text):
    (argument,) = [a for a in state["prompts"][name].arguments if a.name == arg]
    assert text in argument.description


@then(parsers.parse('ska resursen "{uri}" ha titeln "{title}"'))
def resource_title(state, uri, title):
    assert state["resources"][uri].title == title


@then(parsers.parse('ska resursmallen "{template}" ha titeln "{title}"'))
def template_title(state, template, title):
    assert state["templates"][template].title == title
