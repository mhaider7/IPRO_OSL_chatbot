"""The system prompt's contact placeholders and fixed facts (C3-lite)."""
from config.loader import fill_contacts, load_config


def test_contact_placeholders_are_filled_from_the_settings():
    text = fill_contacts("Desk: {contact_email}, {contact_phone}. Keep {other} and {{x}}.", "a@b.edu", "312.000.0000")
    assert text == "Desk: a@b.edu, 312.000.0000. Keep {other} and {{x}}."


def test_loaded_prompt_has_the_welcome_desk_contact_and_no_placeholders():
    cfg = load_config()
    assert "{contact_" not in cfg.system_prompt
    assert cfg.contact_email in cfg.system_prompt and cfg.contact_phone in cfg.system_prompt
    assert "osl@" not in cfg.system_prompt   # osl@ is unverified; only the escalation draft uses it


def test_prompt_lists_the_fixed_312_links():
    prompt = load_config().system_prompt
    for url in ("https://312.iit.edu", "https://312.iit.edu/club_signup?view=all", "https://312.iit.edu/events"):
        assert url in prompt
