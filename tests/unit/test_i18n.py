"""PROTONMAIL_LANGUAGE: katalogen i i18n_en.py och översättningen i list-metoderna.

Svenska är källan. Testerna kräver att varje svensk text klienten ser (titel
och beskrivning för verktyg, promptar, resurser och resursmallar, och varje
parameter- och argumentbeskrivning) har en engelsk motsvarighet vars
hashvärde stämmer, och att katalogen inte har poster för sådant som inte
finns. Ändras en svensk text skriver felet ut det nya hashvärdet: uppdatera
översättningen och sätt sv till det.
"""
import asyncio

import pytest
from pydantic import ValidationError

from protonmail_mcp import i18n, i18n_en
from protonmail_mcp._sdk import MCPServer
from protonmail_mcp.server import mcp, register_tools
from tests.llm_kontrakt_support import client_session
from tests.llm_skick_support import make_settings


def _swedish():
    """Basklassens listor: det som registrerats, på svenska."""
    register_tools()

    async def run():
        return (
            await MCPServer.list_tools(mcp),
            await MCPServer.list_prompts(mcp),
            await MCPServer.list_resources(mcp),
            await MCPServer.list_resource_templates(mcp),
        )

    return asyncio.run(run())


def _descriptions(schema) -> list[str]:
    found = []
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key == "description" and isinstance(value, str):
                found.append(value)
            else:
                found.extend(_descriptions(value))
    elif isinstance(schema, list):
        for item in schema:
            found.extend(_descriptions(item))
    return found


def _without_descriptions(schema):
    if isinstance(schema, dict):
        return {k: _without_descriptions(v) for k, v in schema.items() if k != "description"}
    if isinstance(schema, list):
        return [_without_descriptions(v) for v in schema]
    return schema


# --- Inställningen -----------------------------------------------------------------

def test_default_language_is_swedish():
    assert make_settings().language == "sv"


@pytest.mark.parametrize("raw", ["en", "EN", " En "])
def test_language_is_case_and_space_insensitive(raw):
    assert make_settings(language=raw).language == "en"


def test_unknown_language_is_rejected():
    with pytest.raises(ValidationError):
        make_settings(language="fr")


def test_language_from_environment(monkeypatch):
    from protonmail_mcp.config import Settings

    monkeypatch.setenv("PROTONMAIL_LANGUAGE", "en")
    assert Settings(username="u", password="p", _env_file=None).language == "en"


def test_listing_without_valid_settings_uses_default(monkeypatch):
    from protonmail_mcp import config, server

    def broken():
        return config.Settings(_env_file=None)  # saknar användarnamn och lösenord

    monkeypatch.delenv("PROTONMAIL_USERNAME", raising=False)
    monkeypatch.delenv("PROTONMAIL_PASSWORD", raising=False)
    monkeypatch.setattr(config, "get_settings", broken)
    assert server._language() == "sv"


# --- Katalogen: komplett och i takt med den svenska texten ---------------------------

@pytest.mark.parametrize(
    "table, items, key",
    [
        ("TOOLS", 0, lambda t: t.name),
        ("PROMPTS", 1, lambda p: p.name),
        ("RESOURCES", 2, lambda r: str(r.uri)),
        ("TEMPLATES", 3, lambda t: t.uri_template),
    ],
)
def test_catalogue_matches_swedish(table, items, key):
    listed = _swedish()[items]
    catalogue = getattr(i18n_en, table)
    names = {key(item) for item in listed}
    assert set(catalogue) == names, f"{table}: saknas {names - set(catalogue)}, överflödiga {set(catalogue) - names}"
    stale = {
        key(item): i18n.sv_hash(item.title, item.description)
        for item in listed
        if catalogue[key(item)].sv != i18n.sv_hash(item.title, item.description)
    }
    assert not stale, f"svensk text ändrad, uppdatera översättningen i i18n_en.{table} och sätt sv: {stale}"


def test_every_field_description_is_translated():
    tools, prompts, _, _ = _swedish()
    swedish = {d for tool in tools for d in _descriptions(tool.input_schema)}
    swedish |= {arg.description for prompt in prompts for arg in prompt.arguments or [] if arg.description}
    hashes = {i18n.sv_hash(text): text for text in swedish}
    missing = {h: text for h, text in hashes.items() if h not in i18n_en.FIELDS}
    assert not missing, f"lägg till i i18n_en.FIELDS: {missing}"
    assert set(i18n_en.FIELDS) <= set(hashes), "FIELDS har poster för beskrivningar som inte finns längre"


@pytest.mark.parametrize("table", ["TOOLS", "PROMPTS", "RESOURCES", "TEMPLATES"])
def test_translations_are_not_empty_or_swedish_copies(table):
    tools, prompts, resources, templates = _swedish()
    swedish = {
        "TOOLS": {t.name: (t.title, t.description) for t in tools},
        "PROMPTS": {p.name: (p.title, p.description) for p in prompts},
        "RESOURCES": {str(r.uri): (r.title, r.description) for r in resources},
        "TEMPLATES": {t.uri_template: (t.title, t.description) for t in templates},
    }[table]
    for key, entry in getattr(i18n_en, table).items():
        assert entry.title.strip() and entry.description.strip(), key
        assert entry.title != swedish[key][0], f"{key}: titeln är oöversatt"
        assert entry.description != swedish[key][1], f"{key}: beskrivningen är oöversatt"


# --- Översättningen ----------------------------------------------------------------

def test_swedish_listing_is_untouched():
    tools, prompts, resources, templates = _swedish()
    assert i18n.translate_tools(tools, "sv") is tools
    assert i18n.translate_prompts(prompts, "sv") is prompts
    assert i18n.translate_resources(resources, "sv") is resources
    assert i18n.translate_templates(templates, "sv") is templates


def test_english_keeps_names_and_schemas():
    tools = _swedish()[0]
    translated = i18n.translate_tools(tools, "en")
    assert [t.name for t in translated] == [t.name for t in tools]
    for before, after in zip(tools, translated):
        assert _without_descriptions(after.input_schema) == _without_descriptions(before.input_schema)
        assert after.output_schema == before.output_schema
        assert after.annotations.model_dump(exclude={"title"}) == before.annotations.model_dump(exclude={"title"})
        assert after.title == after.annotations.title == i18n_en.TOOLS[before.name].title


def test_english_does_not_change_registered_tools():
    tools = _swedish()[0]
    i18n.translate_tools(tools, "en")
    assert [t.title for t in _swedish()[0]] == [t.title for t in tools]


def test_stale_entry_falls_back_to_swedish(monkeypatch):
    tools = _swedish()[0]
    entries = dict(i18n_en.TOOLS)
    entries["send_email"] = entries["send_email"]._replace(sv="000000000000")
    monkeypatch.setattr(i18n_en, "TOOLS", entries)
    by_name = {t.name: t for t in i18n.translate_tools(tools, "en")}
    original = {t.name: t for t in tools}["send_email"]
    assert by_name["send_email"].title == original.title
    assert by_name["send_email"].description == original.description
    assert by_name["send_draft"].title == "Send draft"


async def test_get_prompt_description_follows_language(monkeypatch):
    monkeypatch.setenv("PROTONMAIL_LANGUAGE", "en")
    async with client_session(monkeypatch) as session:
        result = await session.get_prompt("rensa_nyhetsbrev", {"mailbox": "INBOX"})
    assert result.description == i18n_en.PROMPTS["rensa_nyhetsbrev"].description


async def test_get_prompt_description_is_swedish_by_default(monkeypatch):
    monkeypatch.delenv("PROTONMAIL_LANGUAGE", raising=False)
    async with client_session(monkeypatch) as session:
        result = await session.get_prompt("rensa_nyhetsbrev", {"mailbox": "INBOX"})
    assert result.description.startswith("Hitta nyhetsbrev")
