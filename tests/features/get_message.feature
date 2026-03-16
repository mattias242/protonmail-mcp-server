Feature: Hämta ett meddelande med UID
  Som en MCP-klient
  vill jag hämta ett fullständigt meddelande via dess UID
  för att läsa innehållet i ett specifikt mail.

  Scenario: Hämta meddelande med giltigt UID
    Given att jag är ansluten till IMAP-servern
    And ett meddelande med UID "456" finns i INBOX
    When jag anropar get_email med uid "456"
    Then ska resultatet innehålla ämnesrad "Test subject"
    And resultatet ska innehålla avsändare "sender@example.com"
    And resultatet ska innehålla brödtext "Hello world!"
