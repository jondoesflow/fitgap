from pathlib import Path

from fitgap.redact import RedactionRules, anonymise, load_rules
from fitgap.redact.anonymise import CustomRule

RULES = RedactionRules(
    client_names=["Contoso", "Contoso Ltd"],
    people=["Jane Doe"],
    codenames=["Project Phoenix"],
    custom=[CustomRule(pattern=r"\bCON-\d{4}\b", replacement="[TICKET]")],
)


def test_client_name_redacted_case_insensitively():
    text, events = anonymise("CONTOSO wants contoso branding everywhere.", RULES)
    assert text == "[CLIENT] wants [CLIENT] branding everywhere."
    assert len(events) == 2
    assert all(e.rule == "client_name" for e in events)


def test_person_and_codename_redacted():
    text, events = anonymise(
        "Jane  Doe leads Project Phoenix delivery.", RULES
    )  # double space still matches
    assert text == "[PERSON] leads [PROJECT] delivery."
    assert {e.rule for e in events} == {"person", "codename"}


def test_email_redacted():
    text, events = anonymise("Contact jane.doe@contoso.com for access.", RULES)
    assert text == "Contact [EMAIL] for access."
    assert events[0].original == "jane.doe@contoso.com"


def test_custom_regex_rule():
    text, events = anonymise("Raised as CON-1234 last sprint.", RULES)
    assert text == "Raised as [TICKET] last sprint."
    assert events[0].rule == "custom"


def test_events_carry_requirement_id():
    _, events = anonymise("Contoso ticket", RULES, requirement_id="REQ-001")
    assert events[0].requirement_id == "REQ-001"


def test_no_partial_word_matches():
    text, events = anonymise("The Contosonian era began.", RULES)
    assert text == "The Contosonian era began."
    assert events == []


def test_missing_rules_file_means_no_redaction(tmp_path: Path):
    rules = load_rules(tmp_path / "does_not_exist.yaml")
    text, events = anonymise("Contoso and jane@x.com stay put? No — emails still go.", rules)
    # No name rules loaded, but email redaction defaults on.
    assert "[EMAIL]" in text
    assert "Contoso" in text


def test_rules_roundtrip_from_yaml(tmp_path: Path):
    rules_file = tmp_path / "redact.yaml"
    rules_file.write_text(
        "client_names: [Contoso]\nredact_emails: false\n", encoding="utf-8"
    )
    rules = load_rules(rules_file)
    text, _ = anonymise("Contoso mail: a@b.com", rules)
    assert text == "[CLIENT] mail: a@b.com"
