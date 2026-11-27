"""Språket i det klienten visar för människan (PROTONMAIL_LANGUAGE).

Klienterna (Claude Desktop, Claude Code, MCP Inspector) visar verktygens
titlar och beskrivningar när de listar verktyg och när de ber användaren
godkänna ett anrop, och promptarnas och resursernas titlar i sina väljare.
Med PROTONMAIL_LANGUAGE=en byts de texterna mot katalogen i i18n_en.py när
servern listar dem (server._GuardedMCPServer). Verktygen registreras som
förut på svenska; namnen (move_email, sammanfatta_olasta …) ändras aldrig.

Fasta texter till modellen (serverinstruktionerna, promptarnas text) går
genom t(): den svenska mallen är källan och nyckeln (dess hashvärde) i
i18n_en.TEXTS. Inte översatt än: felmeddelanden och svaren från verktygen.

Fail-closed: en post vars hashvärde inte stämmer med den svenska texten
används inte, så att klienten aldrig får en engelsk beskrivning av ett
beteende som har ändrats. Då visas den svenska.
"""
from __future__ import annotations

import copy
import hashlib
from typing import Any

from . import i18n_en

LANGUAGES = ("sv", "en")
DEFAULT_LANGUAGE = "sv"


def current_language() -> str:
    """PROTONMAIL_LANGUAGE. Utan giltiga inställningar (t.ex. inget användarnamn)
    gäller standardspråket; lifespan rapporterar själva felet."""
    from pydantic import ValidationError

    from . import config

    try:
        language = getattr(config.get_settings(), "language", DEFAULT_LANGUAGE)
    except ValidationError:
        return DEFAULT_LANGUAGE
    return language if language in LANGUAGES else DEFAULT_LANGUAGE


def t(template: str, /, **values: Any) -> str:
    """En fast text på det inställda språket, med värdena insatta (str.format).

    template är den svenska mallen. Den engelska hämtas ur i18n_en.TEXTS med
    mallens hashvärde; saknas den (ny eller ändrad svensk text) används den
    svenska. Utan värden formateras inget, så klamrar i texten står kvar.
    Värdena sätts in som de är och tolkas aldrig som mall.
    """
    text = template
    if current_language() == "en":
        text = i18n_en.TEXTS.get(sv_hash(template), template)
    return text.format(**values) if values else text


def sv_hash(*parts: str | None) -> str:
    """Kort hashvärde av den svenska texten (titel och beskrivning, eller ett fält)."""
    text = "\x00".join(part or "" for part in parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _entry(table: dict[str, i18n_en.Entry], key: str, title: str | None, description: str | None):
    entry = table.get(key)
    if entry is None or entry.sv != sv_hash(title, description):
        return None
    return entry


def _translate_fields(schema: Any) -> Any:
    """Kopia av ett JSON-schema där kända svenska beskrivningar är översatta."""
    if isinstance(schema, dict):
        out = {}
        for key, value in schema.items():
            if key == "description" and isinstance(value, str):
                out[key] = i18n_en.FIELDS.get(sv_hash(value), value)
            else:
                out[key] = _translate_fields(value)
        return out
    if isinstance(schema, list):
        return [_translate_fields(item) for item in schema]
    return copy.deepcopy(schema)


def _english(language: Any) -> bool:
    return language == "en"


def translate_tools(tools: list[Any], language: Any) -> list[Any]:
    """Verktygslistan (MCP Tool) på språket; namn och scheman i övrigt oförändrade."""
    if not _english(language):
        return tools
    result = []
    for tool in tools:
        update: dict[str, Any] = {"input_schema": _translate_fields(tool.input_schema)}
        entry = _entry(i18n_en.TOOLS, tool.name, tool.title, tool.description)
        if entry is not None:
            update["title"] = entry.title
            update["description"] = entry.description
            if tool.annotations is not None and tool.annotations.title == tool.title:
                update["annotations"] = tool.annotations.model_copy(update={"title": entry.title})
        result.append(tool.model_copy(update=update))
    return result


def translate_prompts(prompts: list[Any], language: Any) -> list[Any]:
    if not _english(language):
        return prompts
    result = []
    for prompt in prompts:
        update: dict[str, Any] = {}
        entry = _entry(i18n_en.PROMPTS, prompt.name, prompt.title, prompt.description)
        if entry is not None:
            update["title"] = entry.title
            update["description"] = entry.description
        if prompt.arguments:
            update["arguments"] = [
                arg.model_copy(update={"description": i18n_en.FIELDS.get(sv_hash(arg.description), arg.description)})
                if arg.description else arg
                for arg in prompt.arguments
            ]
        result.append(prompt.model_copy(update=update))
    return result


def prompt_description(name: str, title: str | None, description: str | None, language: Any) -> str | None:
    """Beskrivningen i svaret på prompts/get."""
    if not _english(language):
        return description
    entry = _entry(i18n_en.PROMPTS, name, title, description)
    return description if entry is None else entry.description


def _translate_listed(items: list[Any], table: dict[str, i18n_en.Entry], key_of) -> list[Any]:
    result = []
    for item in items:
        entry = _entry(table, key_of(item), item.title, item.description)
        if entry is None:
            result.append(item)
        else:
            result.append(item.model_copy(update={"title": entry.title, "description": entry.description}))
    return result


def translate_resources(resources: list[Any], language: Any) -> list[Any]:
    if not _english(language):
        return resources
    return _translate_listed(resources, i18n_en.RESOURCES, lambda r: str(r.uri))


def translate_templates(templates: list[Any], language: Any) -> list[Any]:
    if not _english(language):
        return templates
    return _translate_listed(templates, i18n_en.TEMPLATES, lambda t: t.uri_template)
