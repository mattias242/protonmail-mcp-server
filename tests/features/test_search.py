"""BDD-tester för att söka olästa mail."""
import asyncio

from pytest_bdd import scenarios, given, when, then, parsers

from protonmail_mcp.tools.search import search_emails

scenarios("search.feature")


@given("att jag är ansluten till IMAP-servern", target_fixture="mock_ctx")
def connected_imap(mock_ctx):
    return mock_ctx


@given(
    parsers.parse('INBOX innehåller olästa meddelanden med UID "{uid1}" och "{uid2}"'),
    target_fixture="expected_uids",
)
def inbox_has_unseen(mock_ctx, uid1, uid2):
    uids = [uid1, uid2]
    mock_ctx.request_context.lifespan_context.imap.search_messages.return_value = uids
    return uids


@when("jag anropar search_emails med unseen=true", target_fixture="result")
def call_search_unseen(mock_ctx):
    return asyncio.run(search_emails(mock_ctx, mailbox="INBOX", unseen=True))


@then("ska resultatet vara en lista med UID:n")
def result_is_uid_list(result):
    assert isinstance(result, list)
    assert len(result) > 0
    for uid in result:
        assert isinstance(uid, str)
        assert uid.isdigit()


@then(parsers.parse('listan ska innehålla "{uid1}" och "{uid2}"'))
def list_contains_uids(result, uid1, uid2):
    assert uid1 in result
    assert uid2 in result
