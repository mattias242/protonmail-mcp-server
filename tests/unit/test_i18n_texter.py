"""i18n.t och TEXTS: fasta texter till modellen (instruktioner, promptar) per språk.

Varje svensk mall som går genom i18n.t ska ha en engelsk motsvarighet i
i18n_en.TEXTS med samma platshållare. Nyckeln är hashvärdet av den svenska
mallen, så en ändrad svensk text saknar översättning tills den uppdaterats
(och visas då på svenska).
"""
import string

import pytest

from protonmail_mcp import i18n, i18n_en, prompts, server


def _fields(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def model_templates() -> dict[str, str]:
    """Alla svenska mallar till modellen som ska ha en engelsk motsvarighet."""
    found = {"INSTRUCTIONS": server.INSTRUCTIONS, "_SAFETY": prompts._SAFETY}
    found.update({f"prompt:{name}": text for name, text in prompts._TEMPLATES.items()})
    return found


# --- i18n.t -------------------------------------------------------------------------

def test_swedish_template_is_returned_formatted(monkeypatch):
    monkeypatch.setattr(i18n, "current_language", lambda: "sv")
    assert i18n.t("Hej {namn}", namn="Anna") == "Hej Anna"


def test_english_template_is_used_when_known(monkeypatch):
    monkeypatch.setattr(i18n, "current_language", lambda: "en")
    monkeypatch.setattr(i18n_en, "TEXTS", {i18n.sv_hash("Hej {namn}"): "Hello {namn}"})
    assert i18n.t("Hej {namn}", namn="Anna") == "Hello Anna"


def test_unknown_template_falls_back_to_swedish(monkeypatch):
    monkeypatch.setattr(i18n, "current_language", lambda: "en")
    monkeypatch.setattr(i18n_en, "TEXTS", {})
    assert i18n.t("Hej {namn}", namn="Anna") == "Hej Anna"


def test_without_values_braces_are_kept(monkeypatch):
    monkeypatch.setattr(i18n, "current_language", lambda: "sv")
    assert i18n.t('svaret {"ok": [...]}') == 'svaret {"ok": [...]}'


def test_values_are_not_interpreted_as_template(monkeypatch):
    monkeypatch.setattr(i18n, "current_language", lambda: "sv")
    assert i18n.t("mapp {m}", m="{x} {0}") == "mapp {x} {0}"


def test_current_language_without_valid_settings_is_default(monkeypatch):
    from protonmail_mcp import config

    monkeypatch.delenv("PROTONMAIL_USERNAME", raising=False)
    monkeypatch.delenv("PROTONMAIL_PASSWORD", raising=False)
    monkeypatch.setattr(config, "get_settings", lambda: config.Settings(_env_file=None))
    assert i18n.current_language() == "sv"


# --- Katalogen ----------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(model_templates()))
def test_every_model_template_is_translated(name):
    sv = model_templates()[name]
    key = i18n.sv_hash(sv)
    assert key in i18n_en.TEXTS, f"{name}: lägg till i i18n_en.TEXTS med nyckeln {key!r}"
    en = i18n_en.TEXTS[key]
    assert _fields(en) == _fields(sv), f"{name}: platshållarna skiljer sig"
    assert en.strip() and en != sv


# --- Instruktionerna ----------------------------------------------------------------

def test_apply_language_sets_and_restores_instructions():
    try:
        server.apply_language("en")
        assert server.mcp.instructions == i18n_en.TEXTS[i18n.sv_hash(server.INSTRUCTIONS)]
    finally:
        server.apply_language("sv")
    assert server.mcp.instructions == server.INSTRUCTIONS


def test_english_instructions_keep_the_safety_contract():
    """Samma regler som de svenska: UID per mapp, utkast, opålitligt innehåll, Trash, 100 UID:n."""
    en = i18n_en.TEXTS[i18n.sv_hash(server.INSTRUCTIONS)]
    for text in ("UIDs are per folder", "draft", "untrusted", "Trash", "100 UIDs"):
        assert text in en
    for tool in ("create_draft", "send_email", "delete_email", "permanent=True"):
        assert tool in en


def test_english_safety_rules_name_the_same_tools():
    sv, en = prompts._SAFETY, i18n_en.TEXTS[i18n.sv_hash(prompts._SAFETY)]
    tools = {w.strip("(),.") for w in sv.split() if "_" in w and w.strip("(),.").isidentifier()}
    for tool in tools:
        assert tool in en, tool
