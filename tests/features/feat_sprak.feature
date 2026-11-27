Feature: Språk i det klienten visar (PROTONMAIL_LANGUAGE)
  Som användare som inte läser svenska
  vill jag se verktygens titlar och beskrivningar på engelska i klienten,
  så att jag förstår vad ett verktyg tänker göra innan jag godkänner anropet.

  Scenario: Standardspråket är svenska
    Given servern körs utan språkinställning
    When klienten listar verktygen
    Then ska verktyget "move_email" ha titeln "Flytta mejl"
    And ska annoteringen för "move_email" ha titeln "Flytta mejl"

  Scenario: På engelska får verktygen engelsk titel och beskrivning
    Given servern körs med språket "en"
    When klienten listar verktygen
    Then ska verktyget "move_email" ha titeln "Move email"
    And ska annoteringen för "move_email" ha titeln "Move email"
    And ska beskrivningen av "delete_email" börja med "Delete an email by moving it to the trash"
    And ska inget verktyg ha bytt namn

  Scenario: På engelska är parameterbeskrivningarna engelska
    Given servern körs med språket "en"
    When klienten listar verktygen
    Then ska parametern "uid" i "mark_email_read" beskrivas med "or a list of up to 100 UIDs"

  Scenario: På engelska får promptarna engelska titlar men behåller sina namn
    Given servern körs med språket "en"
    When klienten listar promptarna
    Then ska prompten "sammanfatta_olasta" ha titeln "Summarise unread emails"
    And ska argumentet "mailbox" i "sammanfatta_olasta" beskrivas med "Folder name"

  Scenario: På engelska får resurserna och resursmallarna engelska titlar
    Given servern körs med språket "en"
    When klienten listar resurserna
    Then ska resursen "mailboxes://" ha titeln "Folders and labels"
    And ska resursmallen "email://{mailbox}/{uid}" ha titeln "Email (text)"

  Scenario: En översättning som inte följt med en ändrad svensk text används inte
    Given servern körs med språket "en"
    And den svenska beskrivningen av "move_email" har ändrats sedan översättningen
    When klienten listar verktygen
    Then ska verktyget "move_email" ha titeln "Flytta mejl"
    And ska verktyget "delete_email" ha titeln "Delete email"
