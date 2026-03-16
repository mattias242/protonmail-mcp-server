Feature: Skicka ett mail
  Som en MCP-klient
  vill jag kunna skicka e-post via SMTP
  för att kommunicera med andra.

  Scenario: Skicka ett enkelt mail
    Given att jag är ansluten till SMTP-servern
    When jag skickar ett mail till "bob@example.com" med ämne "Hej" och brödtext "Hejsan Bob"
    Then ska mailet ha skickats framgångsrikt
    And SMTP-klienten ska ha anropats med rätt mottagare "bob@example.com"
