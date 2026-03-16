Feature: Lista brevlådor
  Som en MCP-klient
  vill jag lista alla mappar i brevlådan
  för att se vilka mappar som finns tillgängliga.

  Scenario: Lista alla brevlådor
    Given att jag är ansluten till IMAP-servern
    When jag anropar list_mailboxes
    Then ska resultatet vara en lista med brevlådor
    And varje brevlåda ska ha ett namn
