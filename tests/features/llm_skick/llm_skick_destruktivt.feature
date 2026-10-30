Feature: Destruktivspärr (PROTONMAIL_ALLOW_DESTRUCTIVE)
  Som användare som låter en agent arbeta i brevlådan utan uppsikt
  vill jag kunna stänga av flytt, radering, namnbyte och borttagning av etiketter
  men ändå låta agenten läsa, flagga, etikettera och skriva utkast,
  så att ett lurat anrop (promptinjektion) aldrig kan radera mejl, inte ens permanent,
  när skickläget draft_only bara stoppar skick.

  Scenario Outline: Med allow_destructive=false vägras allt som flyttar, raderar, tar bort eller döper om
    Given servern körs med inställningen "allow_destructive" satt till "false"
    When LLM:en anropar verktyget "<verktyg>"
    Then ska anropet vägras med ett fel som nämner "PROTONMAIL_ALLOW_DESTRUCTIVE=false"
    And ska Bridge inte ha kontaktats

    Examples:
      | verktyg       |
      | move_email    |
      | delete_email  |
      | delete_folder |
      | rename_folder |
      | remove_label  |

  Scenario Outline: Med allow_destructive=false går läst/oläst, stjärna, etiketter, nya mappar, utkast och skick igenom
    Given servern körs med inställningen "allow_destructive" satt till "false"
    When LLM:en anropar verktyget "<verktyg>"
    Then ska anropet inte ha vägrats av destruktivspärren

    Examples:
      | verktyg         |
      | list_mailboxes  |
      | mark_email_read |
      | flag_email      |
      | add_label       |
      | create_folder   |
      | create_draft    |
      | update_draft    |
      | send_email      |

  Scenario: Med allow_destructive=false och draft_only får agenten läsa och skriva utkast men inget mer
    Given servern körs med inställningen "allow_destructive" satt till "false"
    And servern körs med inställningen "send_mode" satt till "draft_only"
    And Bridge sparar utkast i Drafts med UID "12"
    When LLM:en anropar verktyget "create_draft"
    Then ska verktyget svara utan fel
    And ska utkastet ha sparats i Drafts

  Scenario: Spärren sitter i IMAP-klienten även om ett verktyg skulle glömma policyn
    Given en IMAP-klient för ett konto med inställningen "allow_destructive" satt till "false"
    When koden direkt i IMAP-klienten flyttar UID "5" i "INBOX" till "Archive"
    Then ska anropet vägras med ett fel som nämner "PROTONMAIL_ALLOW_DESTRUCTIVE=false"
    And ska Bridge inte ha kontaktats

  Scenario: Skrivskyddet går före destruktivspärren
    Given servern körs med inställningen "allow_destructive" satt till "false"
    And servern körs med inställningen "read_only" satt till "true"
    When LLM:en anropar verktyget "delete_email"
    Then ska anropet vägras med ett fel som nämner "PROTONMAIL_READ_ONLY"
    And ska Bridge inte ha kontaktats

  Scenario: Standardläget tillåter flytt och radering som förut
    Given en IMAP-klient för ett konto utan begränsningar
    When koden direkt i IMAP-klienten flyttar UID "5" i "INBOX" till "Archive"
    Then ska anropet inte ha vägrats av destruktivspärren

  Scenario: Med allow_destructive=false ser modellen inte de destruktiva verktygen
    Given servern körs med inställningen "allow_destructive" satt till "false"
    When klienten hämtar verktygslistan
    Then ska verktygslistan sakna "move_email", "delete_email", "delete_folder", "rename_folder" och "remove_label"
    And ska verktygslistan innehålla "add_label", "create_folder", "update_draft" och "send_email"

  Scenario: I standardläget ser modellen alla verktyg
    Given servern körs med inställningen "allow_destructive" satt till "true"
    When klienten hämtar verktygslistan
    Then ska verktygslistan innehålla "move_email", "delete_email", "delete_folder", "rename_folder" och "remove_label"

  Scenario: Ett dolt verktyg vägras ändå om klienten anropar det med namn
    Given servern körs med inställningen "allow_destructive" satt till "false"
    When LLM:en anropar verktyget "delete_email"
    Then ska anropet vägras med ett fel som nämner "PROTONMAIL_ALLOW_DESTRUCTIVE=false"
    And ska Bridge inte ha kontaktats
