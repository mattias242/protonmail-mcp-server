"""Gemensamma steg för llm-skick: begränsningar av vad en LLM får göra.

Verktygen anropas genom MCP-servern (mcp.call_tool) mot den riktiga
IMAPClient och SMTPClient; varje försök att nå Bridge registreras.
"""
import asyncio
from unittest.mock import AsyncMock

import pytest
from pytest_bdd import given, parsers, then, when

from protonmail_mcp.smtp_client import SMTPClient
from tests.llm_skick_support import BridgeSpy, call_tool, make_app, make_settings, record_smtp, spy_imap
from tests.sdk_support import ToolError

# Rimliga argument per verktyg. Vägran ska komma innan argumenten ens tolkas,
# men scenarierna blir lättare att läsa med verkliga anrop.
TOOL_ARGS = {
    "list_mailboxes": {},
    "move_email": {"uid": "5", "mailbox": "INBOX", "target_mailbox": "Archive"},
    "delete_email": {"uid": "5", "mailbox": "INBOX"},
    "mark_email_read": {"uid": "5", "mailbox": "INBOX"},
    "flag_email": {"uid": "5", "mailbox": "INBOX"},
    "add_label": {"uid": "5", "mailbox": "INBOX", "label": "Arbete"},
    "remove_label": {"uid": "5", "mailbox": "INBOX", "label": "Arbete"},
    "create_folder": {"name": "Folders/Ny"},
    "delete_folder": {"name": "Folders/Ny"},
    "rename_folder": {"old_name": "Folders/Ny", "new_name": "Folders/Nyare"},
    "create_draft": {"to": "anna@exempel.se", "subject": "Hej", "body": "Hej Anna"},
    "create_reply_draft": {"uid": "5", "mailbox": "INBOX", "body": "Tack"},
    "create_forward_draft": {"uid": "5", "mailbox": "INBOX", "to": "anna@exempel.se"},
    "update_draft": {"uid": "7", "mailbox": "Drafts", "body": "Ny text"},
    "send_draft": {"uid": "7", "mailbox": "Drafts"},
    "send_email": {"to": "anna@exempel.se", "subject": "Hej", "body": "Hej Anna"},
    "reply_to_email": {"uid": "5", "mailbox": "INBOX", "body": "Tack"},
    "forward_email": {"uid": "5", "mailbox": "INBOX", "to": "anna@exempel.se"},
}


@pytest.fixture
def state():
    return {"overrides": {}, "spy": BridgeSpy(), "app_setup": [], "result": None, "error": None}


def _settings(state):
    return make_settings(**state["overrides"])


def _run(state, coro_factory):
    try:
        state["result"] = asyncio.run(coro_factory())
    except Exception as exc:  # noqa: BLE001 — scenariot granskar felet
        state["error"] = exc


@given(parsers.parse('servern körs med inställningen "{key}" satt till "{value}"'))
@given(parsers.parse('inställningen "{key}" är satt till "{value}"'))
@given(parsers.parse('en IMAP-klient för ett konto med inställningen "{key}" satt till "{value}"'))
@given(parsers.parse('en SMTP-klient för ett konto med inställningen "{key}" satt till "{value}"'))
def set_option(state, key, value):
    state["overrides"][key] = value


@given("en SMTP-klient för ett konto utan begränsningar")
def no_limits(state):
    pass


@given(parsers.parse('Bridge har mappen "{name}"'))
def bridge_has_mailbox(state, name):
    def setup(app):
        app.imap.list_mailboxes = AsyncMock(return_value=[{"name": name, "type": "folder"}])
    state["app_setup"].append(setup)


@when(parsers.parse('LLM:en anropar verktyget "{tool}"'))
def llm_calls_tool(state, tool):
    settings = _settings(state)
    app = make_app(settings, state["spy"])
    for setup in state["app_setup"]:
        setup(app)
    with record_smtp(state["spy"]) as captured:
        state["captured"] = captured
        _run(state, lambda: call_tool(tool, TOOL_ARGS[tool], app, settings))


@when(parsers.parse('koden direkt i IMAP-klienten flyttar UID "{uid}" i "{mailbox}" till "{target}"'))
def imap_move_directly(state, uid, mailbox, target):
    imap = spy_imap(_settings(state), state["spy"])
    _run(state, lambda: imap.move_message(mailbox, uid, target))


@when(parsers.parse('koden direkt i SMTP-klienten skickar ett mejl till "{to}"'))
def smtp_send_directly(state, to):
    smtp = SMTPClient(_settings(state))
    with record_smtp(state["spy"]) as captured:
        state["captured"] = captured
        _run(state, lambda: smtp.send_email(to=to, subject="Hej", body="Hej"))


@then(parsers.parse('ska anropet vägras med ett fel som nämner "{text}"'))
def refused_with(state, text):
    error = state["error"]
    # Ett ValueError inifrån verktyget når klienten som ToolError med samma text
    cause = error
    while isinstance(cause, ToolError) and cause.__cause__ is not None:
        cause = cause.__cause__
    assert isinstance(cause, ValueError), f"väntade ValueError, fick {error!r} (resultat {state['result']!r})"
    assert text in str(error)


@then(parsers.parse('ska felet nämna "{text}"'))
def error_mentions(state, text):
    assert text in str(state["error"])


@then("ska Bridge inte ha kontaktats")
def bridge_not_contacted(state):
    spy = state["spy"]
    assert not spy.contacted, f"IMAP: {spy.imap_calls}, SMTP-anslutningar: {spy.smtp_connections}"


@then("ska verktyget svara utan fel")
def tool_ok(state):
    assert state["error"] is None, repr(state["error"])


@then(parsers.parse('ska mejlet ha skickats till "{addresses}"'))
def sent_to(state, addresses):
    assert state["error"] is None, repr(state["error"])
    expected = [a.strip() for a in addresses.split(",")]
    assert state["captured"].recipients == expected


@then("ska inget mejl ha skickats")
def nothing_sent(state):
    assert state["captured"].sent == []
