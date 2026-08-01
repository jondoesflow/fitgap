"""Anonymisation pass applied to requirement text before any external API call.

Rules come from a user-supplied redact.yaml. Every substitution is recorded as
a RedactionEvent so the consultant can audit exactly what left the machine.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from fitgap.models import RedactionEvent

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


class CustomRule(BaseModel):
    pattern: str
    replacement: str = "[REDACTED]"


class RedactionRules(BaseModel):
    client_names: list[str] = Field(default_factory=list)
    people: list[str] = Field(default_factory=list)
    codenames: list[str] = Field(default_factory=list)
    redact_emails: bool = True
    custom: list[CustomRule] = Field(default_factory=list)


def load_rules(path: Path) -> RedactionRules:
    """Load redact.yaml; a missing file means no redaction rules."""
    if not path.exists():
        return RedactionRules()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return RedactionRules.model_validate(raw)


def _name_pattern(name: str) -> re.Pattern[str]:
    # Whole-word, case-insensitive match; internal whitespace is flexible.
    escaped = r"\s+".join(re.escape(part) for part in name.split())
    return re.compile(rf"\b{escaped}\b", re.IGNORECASE)


def anonymise(
    text: str, rules: RedactionRules, requirement_id: str | None = None
) -> tuple[str, list[RedactionEvent]]:
    """Return (redacted_text, events).

    Emails are redacted before name rules so a client name inside an email
    address can't leave a partially-redacted address behind.
    """
    events: list[RedactionEvent] = []

    def apply(pattern: re.Pattern[str], replacement: str, rule: str, txt: str) -> str:
        def sub(match: re.Match[str]) -> str:
            events.append(
                RedactionEvent(
                    rule=rule,
                    original=match.group(0),
                    replacement=replacement,
                    requirement_id=requirement_id,
                )
            )
            return replacement

        return pattern.sub(sub, txt)

    for custom in rules.custom:
        text = apply(re.compile(custom.pattern), custom.replacement, "custom", text)
    if rules.redact_emails:
        text = apply(EMAIL_RE, "[EMAIL]", "email", text)
    for name in rules.client_names:
        text = apply(_name_pattern(name), "[CLIENT]", "client_name", text)
    for name in rules.people:
        text = apply(_name_pattern(name), "[PERSON]", "person", text)
    for name in rules.codenames:
        text = apply(_name_pattern(name), "[PROJECT]", "codename", text)

    return text, events
