"""Integrationstester för IMAPClient mot en riktig ProtonMail Bridge."""

import pytest

pytestmark = [pytest.mark.integration]


async def test_connect_disconnect(imap_client):
    """Verifiera att connect/disconnect fungerar utan fel."""
    # imap_client-fixturen har redan anslutit — om vi når hit lyckades connect
    assert imap_client._client is not None
    # disconnect sker i fixturen teardown


async def test_list_mailboxes(imap_client):
    """Verifiera att list_mailboxes returnerar minst en brevlåda (INBOX)."""
    mailboxes = await imap_client.list_mailboxes()
    assert isinstance(mailboxes, list)
    assert len(mailboxes) > 0
    names = [m["name"] for m in mailboxes]
    assert any("INBOX" in n.upper() for n in names)


async def test_list_messages(imap_client):
    """Verifiera att list_messages på INBOX returnerar en lista."""
    messages = await imap_client.list_messages("INBOX", limit=5)
    assert isinstance(messages, list)
    # Vi kräver inte att det finns meddelanden, men strukturen ska vara rätt
    for msg in messages:
        assert "uid" in msg
        assert "subject" in msg


async def test_search_unseen(imap_client):
    """Verifiera att search_messages med unseen=True returnerar en lista av UID:n."""
    uids = await imap_client.search_messages("INBOX", unseen=True)
    assert isinstance(uids, list)
    for uid in uids:
        assert uid.isdigit()
