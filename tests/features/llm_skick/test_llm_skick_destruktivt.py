"""BDD-steg för llm_skick_destruktivt.feature (gemensamma steg i conftest.py)."""
from unittest.mock import AsyncMock

from pytest_bdd import given, parsers, scenarios, then, when

from tests.llm_skick_support import call_tool, make_app, make_settings, record_smtp

from .conftest import _run

scenarios("llm_skick_destruktivt.feature")


@given("servern körs utan begränsningar")
def no_limits(state):
    pass


@given(parsers.parse('Bridge sparar nya utkast i Drafts med UID "{uid}"'))
def bridge_saves_drafts(state, monkeypatch, uid):
    from protonmail_mcp import imap_drafts

    saved = []

    async def store(imap, message, message_id):
        saved.append(message)
        return uid

    async def ensure_connected():
        return None

    monkeypatch.setattr(imap_drafts, "_store_draft", store)
    state["saved_drafts"] = saved

    def setup(app):
        app.imap._ensure_connected = AsyncMock(side_effect=ensure_connected)
    state["app_setup"].append(setup)


@when(parsers.parse('LLM:en raderar UID "{uid}" i "{mailbox}" permanent'))
def llm_deletes_permanently(state, uid, mailbox):
    settings = make_settings(**state["overrides"])
    app = make_app(settings, state["spy"])
    with record_smtp(state["spy"]):
        _run(state, lambda: call_tool(
            "delete_email", {"uid": uid, "mailbox": mailbox, "permanent": True}, app, settings
        ))


@then("ska ett nytt utkast ha sparats i Drafts")
def draft_saved(state):
    assert len(state["saved_drafts"]) == 1


@then("ska anropet inte ha vägrats av spärren mot destruktiva ändringar")
def not_refused_by_destructive_guard(state):
    assert "PROTONMAIL_ALLOW_DESTRUCTIVE" not in str(state["error"] or "")
