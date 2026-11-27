Feature: Felmeddelanden på inställt språk (PROTONMAIL_LANGUAGE)
  Som användare som har servern på engelska
  vill jag förstå varför ett verktyg vägrades eller misslyckades,
  så att jag kan rätta anropet eller inställningen själv.

  Scenario: Standardspråket ger svenska felmeddelanden
    Given servern körs med felmeddelanden utan språkinställning
    And kontot är skrivskyddat
    When LLM:en flyttar ett mejl
    Then ska felet börja med "Kontot är skrivskyddat för den här servern"

  Scenario: På engelska är en vägran engelsk
    Given servern körs med felmeddelanden på språket "en"
    And kontot är skrivskyddat
    When LLM:en flyttar ett mejl
    Then ska felet börja med "The account is read-only for this server"
    And ska felet nämna "PROTONMAIL_READ_ONLY=true"
    And ska felet nämna "move_email"

  Scenario: På engelska är ett valideringsfel engelskt och värdet oförändrat
    Given servern körs med felmeddelanden på språket "en"
    When LLM:en läser ett mejl med UID "12a"
    Then ska felet nämna "Invalid UID"
    And ska felet nämna "12a"

  Scenario: På engelska är felet när Bridge inte går att nå engelskt
    Given servern körs med felmeddelanden på språket "en"
    And Bridge går inte att nå
    When LLM:en listar mapparna
    Then ska felet nämna "Cannot reach ProtonMail Bridge"
    And ska felet nämna "Start the Bridge app"
    And ska felet inte nämna "Kan inte nå"
