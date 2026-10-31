Feature: Språket i det klienten ser (PROTONMAIL_LANGUAGE)
  Som användare som inte läser svenska
  vill jag kunna få verktygens titlar och beskrivningar, promptarna, resurserna,
  serverinstruktionerna och felmeddelandena på engelska
  så att jag förstår vad ett verktyg gör när klienten ber mig godkänna ett anrop,
  medan verktygens och parametrarnas namn är desamma och svenska förblir standard.

  Scenario: Standardspråket är svenska
    Given ingen PROTONMAIL_LANGUAGE är satt
    When servern startar
    Then ska verktyget "move_email" ha titeln "Flytta mejl"
    And ska instruktionerna börja med "E-post i Proton Mail"

  Scenario: Med PROTONMAIL_LANGUAGE=en ser klienten verktygen på engelska
    Given PROTONMAIL_LANGUAGE är "en"
    When servern startar
    Then ska verktyget "move_email" ha titeln "Move email"
    And ska beskrivningen av "move_email" börja med "Move an email to another mailbox"
    And ska parametern "uid" i "move_email" beskrivas på engelska
    And ska instruktionerna börja med "Email in Proton Mail"
    And ska verktygsnamnen vara desamma som på svenska

  Scenario: Promptar och resurser på engelska
    Given PROTONMAIL_LANGUAGE är "en"
    When servern startar
    Then ska prompten "svara_pa_mejl" ha titeln "Reply to an email (draft)"
    And ska prompten "svara_pa_mejl" ge en text som börjar med "Write a reply to the email"
    And ska resursen "mailboxes" ha titeln "Folders and labels"

  Scenario: Felmeddelanden på engelska
    Given PROTONMAIL_LANGUAGE är "en"
    And servern är skrivskyddad
    When servern startar
    And LLM:en anropar verktyget "delete_email"
    Then ska felet nämna "read-only for this server"
    And ska felet inte innehålla några svenska bokstäver

  Scenario: Ett okänt språk stoppar servern
    Given PROTONMAIL_LANGUAGE är "de"
    Then ska inställningarna avvisas med ett fel som nämner "language"
