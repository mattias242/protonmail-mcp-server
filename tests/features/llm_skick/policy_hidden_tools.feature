Feature: Destructive tools are hidden from the model (PROTONMAIL_ALLOW_DESTRUCTIVE=false)
  As a user who lets an LLM work on my mailbox unattended
  I want the switched-off tools not to appear in the tool list at all,
  so that the model never plans with tools it is not allowed to use.

  Based on the design in pull request #6 by Mitch Lindgren.

  Scenario: With the guard on, the model does not see the destructive tools
    Given the server runs with allow_destructive set to "false"
    When the client lists the tools
    Then the tool list lacks "move_email", "delete_email", "delete_folder", "rename_folder", "remove_label" and "update_draft"
    And the tool list contains "add_label", "create_folder", "create_draft" and "send_email"

  Scenario: By default the model sees every tool
    Given the server runs with allow_destructive set to "true"
    When the client lists the tools
    Then the tool list contains "move_email", "delete_email", "delete_folder", "rename_folder", "remove_label" and "update_draft"

  Scenario: A hidden tool is still refused when the client calls it by name
    Given the server runs with allow_destructive set to "false"
    When the client calls "delete_email" by name
    Then the call is refused with an error that mentions "PROTONMAIL_ALLOW_DESTRUCTIVE=false"

  Scenario: Hidden tools are listed in English too, without the destructive ones
    Given the server runs with allow_destructive set to "false"
    And the language is "en"
    When the client lists the tools
    Then the tool list lacks "move_email" and "delete_email"
    And the tool "add_label" has the title "Add label"
