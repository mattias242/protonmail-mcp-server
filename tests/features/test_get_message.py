"""BDD-tester för att hämta ett meddelande med UID."""
import asyncio

from pytest_bdd import scenarios, given, when, then, parsers

from protonmail_mcp.tools.messages import get_email

scenarios("get_message.feature")


@given("att jag är ansluten till IMAP-servern", target_fixture="mock_ctx")
def connected_imap(mock_ctx):
    return mock_ctx


@given(parsers.parse('ett meddelande med UID "{uid}" finns i INBOX'))
def message_exists(mock_ctx, uid, sample_raw_email):
    mock_ctx.request_context.lifespan_context.imap.get_message.return_value = (
        sample_raw_email
    )


@when(parsers.parse('jag anropar get_email med uid "{uid}"'), target_fixture="result")
def call_get_email(mock_ctx, uid):
    return asyncio.run(get_email(mock_ctx, uid=uid, mailbox="INBOX"))


@then(parsers.parse('ska resultatet innehålla ämnesrad "{subject}"'))
def result_has_subject(result, subject):
    assert result is not None
    assert result["subject"] == subject


@then(parsers.parse('resultatet ska innehålla avsändare "{from_addr}"'))
def result_has_from(result, from_addr):
    assert result["from"] == from_addr


@then(parsers.parse('resultatet ska innehålla brödtext "{body_text}"'))
def result_has_body(result, body_text):
    assert body_text in result["body_plain"]
