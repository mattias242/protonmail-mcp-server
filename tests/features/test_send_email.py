"""BDD-tester för att skicka e-post."""
import asyncio

from pytest_bdd import scenarios, given, when, then, parsers

from protonmail_mcp.tools.send import send_email

scenarios("send_email.feature")


@given("att jag är ansluten till SMTP-servern", target_fixture="mock_ctx")
def connected_smtp(mock_ctx):
    mock_ctx.request_context.lifespan_context.smtp.send_email.return_value = True
    return mock_ctx


@when(
    parsers.parse(
        'jag skickar ett mail till "{to}" med ämne "{subject}" och brödtext "{body}"'
    ),
    target_fixture="result",
)
def send_mail(mock_ctx, to, subject, body):
    return asyncio.run(send_email(mock_ctx, to=to, subject=subject, body=body))


@then("ska mailet ha skickats framgångsrikt")
def send_succeeded(result):
    assert result is True


@then(parsers.parse('SMTP-klienten ska ha anropats med rätt mottagare "{to}"'))
def smtp_called_with_recipient(mock_ctx, to):
    mock_ctx.request_context.lifespan_context.smtp.send_email.assert_called_once_with(
        to=to,
        subject="Hej",
        body="Hejsan Bob",
        body_html=None,
        cc=None,
        bcc=None,
        reply_to=None,
    )
