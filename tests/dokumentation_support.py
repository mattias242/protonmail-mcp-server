"""Jämför dokumentationen (README.md, CLAUDE.md) med koden.

README:s tabeller över verktyg, resurser, promptar och inställningar läses
som Markdown och jämförs med det servern faktiskt registrerar och med
config.Settings. CLAUDE.md:s modulkarta jämförs med filerna i
src/protonmail_mcp/. Läggs ett verktyg, en inställning eller en modul till
utan att dokumentationen följer med faller testerna.

Bara rader vars första cell börjar med ett `kodord` räknas som tabellrader,
så att rubrikrader och vanlig text inte stör.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from unittest.mock import patch

from pydantic_core import PydanticUndefined

from tests.sdk_support import wire

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
CLAUDE_MD = ROOT / "CLAUDE.md"
PACKAGE = ROOT / "src" / "protonmail_mcp"

# Rubrikerna i README.md som tabellerna står under (nivå 2, "## ...")
TOOLS_SECTION = "Tools"
RESOURCES_SECTION = "Resources and subscriptions"
PROMPTS_SECTION = "Prompts"
SETTINGS_SECTION = "Configuration"
# Rubriken i CLAUDE.md som modulkartan står under
MODULE_MAP_SECTION = "Arkitektur och modulkarta"

# Effektkolumnen i README:s verktygstabeller
READ_ONLY = "read-only"
WRITES = "writes"
DESTRUCTIVE = "destructive"
SENDS = "sends"

_ROW_RE = re.compile(r"^\|\s*`([^`]+)`\s*\|(.*)\|\s*$")
_FIRST_CODE_RE = re.compile(r"`([^`]*)`")


def section(text: str, heading: str, level: int = 2) -> str:
    """Texten under rubriken (exakt rubriktext) fram till nästa rubrik på samma eller högre nivå."""
    marker = "#" * level + " "
    lines = text.splitlines()
    out: list[str] = []
    inside = False
    for line in lines:
        is_heading = re.match(r"^(#{1,%d}) " % level, line) is not None
        if inside and is_heading:
            break
        if line.strip() == marker + heading:
            inside = True
            continue
        if inside:
            out.append(line)
    if not inside:
        raise AssertionError(f"Rubriken {marker}{heading} saknas")
    return "\n".join(out)


def table_rows(text: str) -> list[tuple[str, list[str]]]:
    """(kodordet i första cellen, övriga celler) för varje tabellrad som börjar med `kodord`."""
    rows = []
    for line in text.splitlines():
        m = _ROW_RE.match(line.strip())
        if m:
            rows.append((m.group(1), [cell.strip() for cell in m.group(2).split("|")]))
    return rows


def documented_tools(readme: str) -> dict[str, str]:
    """Verktygsnamn → effekt (sista kolumnen) ur README:s verktygstabeller."""
    tools: dict[str, str] = {}
    for name, cells in table_rows(section(readme, TOOLS_SECTION)):
        if name in tools:
            raise AssertionError(f"{name} står flera gånger i verktygstabellerna")
        tools[name] = cells[-1].strip("* ")
    return tools


def effect(annotations: Any) -> str:
    """Effekten som README ska ange, ur verktygets MCP-annoteringar."""
    hints = wire(annotations)
    if hints.get("openWorldHint"):
        return SENDS
    if hints.get("readOnlyHint"):
        return READ_ONLY
    if hints.get("destructiveHint"):
        return DESTRUCTIVE
    return WRITES


async def registered_tools() -> dict[str, str]:
    """Verktygsnamn → effekt för det servern registrerar.

    Med standardinställningarna: tools/list utelämnar verktyg som
    inställningarna stänger av (policy.hidden_tools), och README ska lista
    allt servern har.
    """
    from protonmail_mcp import config, server

    server.register_tools()
    settings = config.Settings(username="u", password="p", _env_file=None)
    with patch.object(config, "get_settings", return_value=settings):
        tools = await server.mcp.list_tools()
    return {t.name: effect(t.annotations) for t in tools}


async def registered_resources() -> set[str]:
    from protonmail_mcp import server

    server.register_tools()
    uris = {str(r.uri) for r in await server.mcp.list_resources()}
    return uris | {wire(t)["uriTemplate"] for t in await server.mcp.list_resource_templates()}


async def registered_prompts() -> set[str]:
    from protonmail_mcp import server

    server.register_tools()
    return {p.name for p in await server.mcp.list_prompts()}


def documented_names(readme: str, heading: str) -> set[str]:
    return {name for name, _ in table_rows(section(readme, heading))}


def _render_default(value: Any) -> str:
    if value is PydanticUndefined:
        return "required"
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def expected_settings() -> dict[str, str]:
    """PROTONMAIL_*-variabel → standardvärdet som README ska ange, ur config.Settings."""
    from protonmail_mcp.config import Settings

    prefix = Settings.model_config["env_prefix"]
    return {
        prefix + name.upper(): _render_default(field.default)
        for name, field in Settings.model_fields.items()
    }


def documented_settings(readme: str) -> dict[str, str]:
    """PROTONMAIL_*-variabel → standardvärde (andra kolumnen) ur README:s inställningstabell.

    Standardvärdet är det första `kodordet` i cellen, annars hela cellen
    (t.ex. — eller required).
    """
    settings: dict[str, str] = {}
    for name, cells in table_rows(section(readme, SETTINGS_SECTION)):
        if not name.startswith("PROTONMAIL_"):
            continue
        m = _FIRST_CODE_RE.search(cells[0])
        settings[name] = m.group(1) if m else cells[0]
    return settings


def source_modules() -> set[str]:
    """Alla moduler i paketet, som sökvägar relativt src/protonmail_mcp/ (t.ex. tools/send.py)."""
    return {
        p.relative_to(PACKAGE).as_posix()
        for p in PACKAGE.rglob("*.py")
        if "__pycache__" not in p.parts
    }


def documented_modules(claude_md: str) -> set[str]:
    """Modulerna i CLAUDE.md:s modulkarta: rader som börjar med `x.py` eller `tools/x.py`."""
    return {name for name, _ in table_rows(section(claude_md, MODULE_MAP_SECTION))}
