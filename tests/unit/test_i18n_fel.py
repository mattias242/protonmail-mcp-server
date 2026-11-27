"""Felmeddelanden genom i18n.t: varje svensk mall i koden har en engelsk motsvarighet.

Mallarna hittas i källkoden: varje anrop i18n.t("…", …) med en strängliteral
som första argument. Testet kräver en post i i18n_en.TEXTS med nyckeln
i18n.sv_hash(mallen) och samma platshållare, och att inga felmeddelanden
byggs med f-strängar direkt i raise ValueError(...) (utom i config.py, som
körs medan inställningarna tolkas och därför inte kan slå upp språket).
"""
import ast
import pathlib
import string

import pytest

from protonmail_mcp import i18n, i18n_en

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "protonmail_mcp"
# config.py: valideringen körs innan get_settings() finns, så språket kan inte
# slås upp där (i18n.current_language läser inställningarna).
EXEMPT = {"config.py"}


def _fields(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def _is_t_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "t"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "i18n"
    )


def _module_constants(tree: ast.Module) -> dict[str, str]:
    """Strängkonstanter på modulnivå (NAMN = "…"), för i18n.t(NAMN)."""
    constants = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            constants[node.targets[0].id] = node.value.value
    return constants


def source_templates() -> dict[str, str]:
    """{plats: svensk mall} för varje i18n.t("literal", …) och i18n.t(KONSTANT, …) i paketet."""
    found = {}
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        constants = _module_constants(tree)
        for node in ast.walk(tree):
            if not (_is_t_call(node) and node.args):
                continue
            arg = node.args[0]
            where = f"{path.relative_to(SRC)}:{node.lineno}:{node.col_offset}"
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                found[where] = arg.value
            elif isinstance(arg, ast.Name) and arg.id in constants:
                found[where] = constants[arg.id]
            elif isinstance(arg, ast.Subscript) and isinstance(arg.value, ast.Name) and arg.value.id in (
                "_HIDDEN_KINDS",  # värdena kontrolleras i test_hidden_kinds_are_translated
                "_TEMPLATES",  # promptarnas mallar kontrolleras i test_i18n_texter.py
            ):
                continue
            else:
                raise AssertionError(f"{where}: i18n.t måste få en strängliteral eller en modulkonstant")
    return found


def test_hidden_kinds_are_translated():
    from protonmail_mcp.smtp_client import _HIDDEN_KINDS

    for sv in _HIDDEN_KINDS.values():
        assert i18n.sv_hash(sv) in i18n_en.TEXTS, sv


def raw_raises() -> list[str]:
    """raise ValueError(f"…"/"…") som inte går genom i18n.t."""
    found = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name in EXEMPT:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Raise)
                and isinstance(node.exc, ast.Call)
                and getattr(node.exc.func, "id", "") == "ValueError"
                and node.exc.args
                and isinstance(node.exc.args[0], (ast.JoinedStr, ast.Constant))
            ):
                found.append(f"{path.relative_to(SRC)}:{node.lineno}")
    return found


def test_error_messages_go_through_i18n():
    assert raw_raises() == [], "felmeddelanden ska byggas med i18n.t(svensk_mall, **värden)"


def test_there_are_error_templates():
    assert len(source_templates()) > 100


@pytest.mark.parametrize("where", sorted(source_templates()))
def test_every_template_is_translated(where):
    sv = source_templates()[where]
    key = i18n.sv_hash(sv)
    assert key in i18n_en.TEXTS, f"{where}: lägg till i i18n_en.TEXTS under {key!r}: {sv[:80]!r}"
    en = i18n_en.TEXTS[key]
    assert _fields(en) == _fields(sv), f"{where}: platshållarna skiljer sig"
    assert en.strip() and en != sv


@pytest.mark.parametrize("where", sorted(source_templates()))
def test_template_formats_with_its_own_fields(where):
    """Mallen går att formatera: inga ensamma klamrar (JSON i texten är dubblerad)."""
    sv = source_templates()[where]
    values = {name: 1 for name in _fields(sv)}  # 1 duger för !r, :g, :o och vanlig text
    if values:
        sv.format(**values)
        i18n_en.TEXTS.get(i18n.sv_hash(sv), sv).format(**values)


def test_no_unused_texts():
    """TEXTS har bara poster för mallar som finns (felmeddelanden, instruktioner, promptar)."""
    from protonmail_mcp import prompts, server

    from protonmail_mcp.smtp_client import _HIDDEN_KINDS

    used = {i18n.sv_hash(t) for t in source_templates().values()}
    used |= {i18n.sv_hash(t) for t in _HIDDEN_KINDS.values()}
    used |= {i18n.sv_hash(server.INSTRUCTIONS), i18n.sv_hash(prompts._SAFETY)}
    used |= {i18n.sv_hash(t) for t in prompts._TEMPLATES.values()}
    assert set(i18n_en.TEXTS) - used == set()
