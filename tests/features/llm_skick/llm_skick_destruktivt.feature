Feature: Spärr mot destruktiva ändringar (PROTONMAIL_ALLOW_DESTRUCTIVE)
  Som användare som låter en LLM arbeta på egen hand med min e-post
  vill jag kunna låta den läsa, märka och skriva utkast men aldrig flytta,
  radera, byta namn på eller ersätta något,
  så att ett mejl med dolda instruktioner inte kan få den att förstöra min brevlåda.

  Scenario Outline: Med spärren vägras varje destruktivt verktyg innan Bridge kontaktas
    Given servern körs med inställningen "allow_destructive" satt till "false"
    When LLM:en anropar verktyget "<verktyg>"
    Then ska anropet vägras med ett fel som nämner "PROTONMAIL_ALLOW_DESTRUCTIVE=false"
    And ska felet nämna "<verktyg>"
    And ska Bridge inte ha kontaktats

    Examples:
      | verktyg       |
      | move_email    |
      | delete_email  |
      | delete_folder |
      | rename_folder |
      | remove_label  |
      | update_draft  |

  Scenario: Med spärren vägras även permanent radering ur Trash
    Given servern körs med inställningen "allow_destructive" satt till "false"
    When LLM:en raderar UID "5" i "Trash" permanent
    Then ska anropet vägras med ett fel som nämner "PROTONMAIL_ALLOW_DESTRUCTIVE=false"
    And ska Bridge inte ha kontaktats

  Scenario: Felet säger vad LLM:en kan göra i stället
    Given servern körs med inställningen "allow_destructive" satt till "false"
    When LLM:en anropar verktyget "update_draft"
    Then ska felet nämna "create_draft"
    And ska felet nämna "Inget har ändrats"

  Scenario Outline: Med spärren fungerar skrivningar som inte förstör något
    Given servern körs med inställningen "allow_destructive" satt till "false"
    When LLM:en anropar verktyget "<verktyg>"
    Then ska anropet inte ha vägrats av spärren mot destruktiva ändringar

    Examples:
      | verktyg         |
      | mark_email_read |
      | flag_email      |
      | add_label       |
      | create_folder   |
      | send_email      |

  Scenario: Med spärren och draft_only får LLM:en läsa och skriva utkast men inte skicka
    Given servern körs med inställningen "allow_destructive" satt till "false"
    And inställningen "send_mode" är satt till "draft_only"
    And Bridge sparar nya utkast i Drafts med UID "12"
    When LLM:en anropar verktyget "create_draft"
    Then ska verktyget svara utan fel
    And ska ett nytt utkast ha sparats i Drafts

  Scenario: Spärren gäller även om ett verktyg skulle glömma den
    Given en IMAP-klient för ett konto med inställningen "allow_destructive" satt till "false"
    When koden direkt i IMAP-klienten flyttar UID "5" i "INBOX" till "Archive"
    Then ska anropet vägras med ett fel som nämner "PROTONMAIL_ALLOW_DESTRUCTIVE=false"
    And ska Bridge inte ha kontaktats

  Scenario: Utan inställningen flyttar och raderar servern som förut
    Given servern körs utan begränsningar
    When LLM:en anropar verktyget "move_email"
    Then ska anropet inte ha vägrats av spärren mot destruktiva ändringar
