"""Integrationstester för SMTPClient mot en riktig ProtonMail Bridge."""

import pytest

from protonmail_mcp.config import get_settings

pytestmark = [pytest.mark.integration]


async def test_send_email_to_self(smtp_client):
    """Skicka ett testmeddelande till sig själv via Bridge."""
    settings = get_settings()
    result = await smtp_client.send_email(
        to="mattias.wahlberg@proton.me",
        subject="Integration test - protonmail-mcp",
        body="Detta är ett automatiserat integrationstest.",
    )
    assert result is True
