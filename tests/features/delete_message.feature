Feature: Radera ett meddelande
  Som en MCP-klient
  vill jag kunna ta bort ett meddelande via dess UID
  för att rensa min brevlåda.

  Scenario: Radera meddelande med giltigt UID
    Given att jag är ansluten till IMAP-servern
    And meddelande med UID "99" finns i INBOX
    When jag anropar delete_email med uid "99"
    Then ska borttagningen ha lyckats
    And IMAP-klienten ska ha anropat delete_message med UID "99"
