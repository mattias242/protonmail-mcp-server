"""BDD-tester för att radera ett meddelande."""
import asyncio

from pytest_bdd import scenarios, given, when, then, parsers

from protonmail_mcp.tools.manage import delete_email

scenarios("delete_message.feature")


@given("att jag är ansluten till IMAP-servern", target_fixture="mock_ctx")
def connected_imap(mock_ctx):
    return mock_ctx


@given(parsers.parse('meddelande med UID "{uid}" finns i INBOX'))
def message_exists(mock_ctx, uid):
    mock_ctx.request_context.lifespan_context.imap.delete_message.return_value = True


@when(
    parsers.parse('jag anropar delete_email med uid "{uid}"'), target_fixture="result"
)
def call_delete_email(mock_ctx, uid):
    return asyncio.run(delete_email(mock_ctx, uid=uid, mailbox="INBOX"))


@then("ska borttagningen ha lyckats")
def delete_succeeded(result):
    assert result is True


@then(parsers.parse('IMAP-klienten ska ha anropat delete_message med UID "{uid}"'))
def imap_called_with_uid(mock_ctx, uid):
    mock_ctx.request_context.lifespan_context.imap.delete_message.assert_called_once_with(
        "INBOX", uid
    )
