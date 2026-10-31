from scripts.tahuti_avatar import card_details


def test_card_details_carry_locked_trust_fields():
    card = card_details(
        "cua:run_command:foreground",
        "Run the calculator command",
        {"action": "run_command", "mode": "foreground"},
    )
    assert card["who"] == "Tahuti — your AI Controller"
    assert card["what"] == "Run the calculator command"
    assert card["where"] == "Your active Linux desktop"
    assert card["when"] == "Immediately after you choose Yes"
    assert "run_command" in card["how"]
    assert "Nothing runs" in card["effect"]


def test_card_details_does_not_invent_missing_action():
    card = card_details("tahuti:custom:background", "Do a bounded task")
    assert card["what"] == "Do a bounded task"
    assert card["pattern_key"] == "tahuti:custom:background"



def test_card_details_include_why_and_honour_extra_why():
    card = card_details(
        "cua:screenshot:foreground",
        "Capture the screen",
        {"action": "screenshot", "mode": "foreground"},
    )
    assert card["why"]  # locked trust field: present and never empty
    custom = card_details(
        "cua:screenshot:foreground",
        "Capture the screen",
        {"why": "You asked for a screen check"},
    )
    assert custom["why"] == "You asked for a screen check"


def test_card_details_action_from_extra_without_colon_key():
    card = card_details(
        "customaction",
        "Do a bounded task",
        {"action": "screenshot", "mode": "background"},
    )
    assert "screenshot" in card["how"]
