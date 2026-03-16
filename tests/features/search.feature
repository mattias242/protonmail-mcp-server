Feature: Söka olästa mail
  Som en MCP-klient
  vill jag kunna söka efter olästa mail
  för att snabbt hitta meddelanden jag inte har läst.

  Scenario: Söka olästa mail i INBOX
    Given att jag är ansluten till IMAP-servern
    And INBOX innehåller olästa meddelanden med UID "101" och "102"
    When jag anropar search_emails med unseen=true
    Then ska resultatet vara en lista med UID:n
    And listan ska innehålla "101" och "102"
