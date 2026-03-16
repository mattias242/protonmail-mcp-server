"""BDD-tester för att lista brevlådor."""
import asyncio

from pytest_bdd import scenarios, given, when, then

from protonmail_mcp.tools.mailboxes import list_mailboxes

scenarios("mailboxes.feature")


@given("att jag är ansluten till IMAP-servern", target_fixture="mock_ctx")
def connected_imap(mock_ctx):
    return mock_ctx


@when("jag anropar list_mailboxes", target_fixture="result")
def call_list_mailboxes(mock_ctx):
    mock_ctx.request_context.lifespan_context.imap.list_mailboxes.return_value = [
        {"name": "INBOX"},
        {"name": "Sent"},
        {"name": "Drafts"},
    ]
    return asyncio.run(list_mailboxes(mock_ctx))


@then("ska resultatet vara en lista med brevlådor")
def result_is_list(result):
    assert isinstance(result, list)
    assert len(result) > 0


@then("varje brevlåda ska ha ett namn")
def each_mailbox_has_name(result):
    for mailbox in result:
        assert "name" in mailbox
        assert isinstance(mailbox["name"], str)
        assert len(mailbox["name"]) > 0
