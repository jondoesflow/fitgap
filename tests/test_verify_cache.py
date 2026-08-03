"""Per-claim verification cache: identical claims verify once per run."""

import json

from fakes import FakeAnthropic, text_block
from test_verify import LEARN_URL, RULES, failing_checker, json_responder, ok_checker

from fitgap.config import Config
from fitgap.models import (
    Category,
    Classification,
    Confidence,
    Effort,
    Requirement,
    SourceType,
    VerificationStatus,
    Workspace,
)
from fitgap.verify import Verifier

FEATURE = "Dynamics 365 Sales — opportunity management"
APPROACH = "Use standard opportunity management."


def requirement(req_id: str, feature: str = FEATURE, approach: str = APPROACH) -> Requirement:
    return Requirement(
        id=req_id,
        text=f"Requirement {req_id} needs the capability.",
        source=SourceType.DOCX,
        source_ref=f"brd.docx#{req_id}",
        classification=Classification(
            category=Category.FIT_OOB,
            proposed_approach=approach,
            feature_relied_on=feature,
            effort=Effort.S,
            confidence=Confidence.HIGH,
        ),
    )


def workspace_of(*reqs: Requirement) -> Workspace:
    return Workspace(tool_version="test", requirements=list(reqs))


CONFIRMED = {
    "confirmed": True,
    "citation_url": LEARN_URL,
    "page_title": "Manage opportunities",
    "notes": "Documented capability.",
}


def test_identical_claims_verified_once_and_share_citation():
    workspace = workspace_of(
        requirement("REQ-001"),
        requirement("REQ-002"),
        requirement("REQ-003"),
    )
    fake = FakeAnthropic(json_responder(CONFIRMED))
    counts = Verifier(Config(), RULES, fake, url_checker=ok_checker).verify_workspace(
        workspace
    )
    assert len(fake.calls) == 1  # one API call for three identical claims
    assert counts["verified"] == 3
    assert counts["reused"] == 2
    for req in workspace.requirements:
        assert req.verification.status == VerificationStatus.VERIFIED
        assert req.verification.citation_url == LEARN_URL
    # Each row owns its own Verification object, not a shared reference.
    assert workspace.requirements[0].verification is not workspace.requirements[1].verification


def test_claim_key_normalises_case_and_whitespace():
    workspace = workspace_of(
        requirement("REQ-001"),
        requirement("REQ-002", feature="  dynamics 365 Sales —  Opportunity Management "),
    )
    fake = FakeAnthropic(json_responder(CONFIRMED))
    counts = Verifier(Config(), RULES, fake, url_checker=ok_checker).verify_workspace(
        workspace
    )
    assert len(fake.calls) == 1
    assert counts["reused"] == 1


def test_different_claims_are_verified_separately():
    workspace = workspace_of(
        requirement("REQ-001"),
        requirement("REQ-002", feature="Power Automate — approvals"),
        requirement("REQ-003", approach="Configure a business process flow."),
    )
    fake = FakeAnthropic(json_responder(CONFIRMED))
    counts = Verifier(Config(), RULES, fake, url_checker=ok_checker).verify_workspace(
        workspace
    )
    assert len(fake.calls) == 3
    assert counts["reused"] == 0


def test_honest_not_confirmed_is_reused():
    """A genuine 'not confirmed' verdict holds for every row making the claim."""
    workspace = workspace_of(requirement("REQ-001"), requirement("REQ-002"))
    fake = FakeAnthropic(
        json_responder(
            {"confirmed": False, "citation_url": None, "notes": "No Learn page found."}
        )
    )
    counts = Verifier(Config(), RULES, fake, url_checker=ok_checker).verify_workspace(
        workspace
    )
    assert len(fake.calls) == 1
    assert counts["unconfirmed"] == 2
    assert counts["reused"] == 1


def test_dead_citation_verdict_is_reused():
    workspace = workspace_of(requirement("REQ-001"), requirement("REQ-002"))
    fake = FakeAnthropic(json_responder(CONFIRMED))
    counts = Verifier(
        Config(), RULES, fake, url_checker=failing_checker
    ).verify_workspace(workspace)
    assert len(fake.calls) == 1
    assert counts["unconfirmed"] == 2
    assert "rejected" in workspace.requirements[1].verification.notes


def test_transient_api_failure_is_not_cached():
    """An API error on one row must not be propagated to same-claim rows."""
    calls = {"n": 0}

    def flaky(kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom: API unavailable")
        return [text_block(json.dumps(CONFIRMED))]

    workspace = workspace_of(requirement("REQ-001"), requirement("REQ-002"))
    counts = Verifier(
        Config(), RULES, FakeAnthropic(flaky), url_checker=ok_checker
    ).verify_workspace(workspace)
    assert calls["n"] == 2  # second row retried instead of reusing the failure
    assert counts["reused"] == 0
    assert workspace.requirements[0].verification.status == VerificationStatus.UNCONFIRMED
    assert workspace.requirements[1].verification.status == VerificationStatus.VERIFIED


def test_garbage_output_is_not_cached():
    calls = {"n": 0}

    def flaky(kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return [text_block("I could not decide, sorry!")]
        return [text_block(json.dumps(CONFIRMED))]

    workspace = workspace_of(requirement("REQ-001"), requirement("REQ-002"))
    Verifier(
        Config(), RULES, FakeAnthropic(flaky), url_checker=ok_checker
    ).verify_workspace(workspace)
    assert calls["n"] == 2
    assert workspace.requirements[1].verification.status == VerificationStatus.VERIFIED


def test_verify_prompt_carries_search_budget():
    from fitgap.verify.learn_verifier import VERIFY_SYSTEM_PROMPT

    assert "at most three tool calls" in VERIFY_SYSTEM_PROMPT
    assert "never answer from memory" in VERIFY_SYSTEM_PROMPT  # accuracy rule intact


# --- verify cost knobs (merged from main) flow through the LLM abstraction ---


def test_cache_prompt_default_wraps_system_with_cache_control():
    fake = FakeAnthropic(json_responder(CONFIRMED))
    Verifier(Config(), RULES, fake, url_checker=ok_checker).verify_workspace(
        workspace_of(requirement("REQ-001"))
    )
    system = fake.calls[0]["system"]
    assert isinstance(system, list)
    assert system[0]["cache_control"] == {"type": "ephemeral"}


def test_cache_prompt_off_sends_plain_system():
    config = Config.model_validate({"verify": {"cache_prompt": False}})
    fake = FakeAnthropic(json_responder(CONFIRMED))
    Verifier(config, RULES, fake, url_checker=ok_checker).verify_workspace(
        workspace_of(requirement("REQ-001"))
    )
    assert isinstance(fake.calls[0]["system"], str)


def test_search_only_restricts_mcp_toolset_to_docs_search():
    config = Config.model_validate({"verify": {"search_only": True}})
    fake = FakeAnthropic(json_responder(CONFIRMED))
    Verifier(config, RULES, fake, url_checker=ok_checker).verify_workspace(
        workspace_of(requirement("REQ-001"))
    )
    call = fake.calls[0]
    assert call["betas"] == ["mcp-client-2025-11-20"]
    toolset = call["tools"][0]
    assert toolset["type"] == "mcp_toolset"
    assert toolset["default_config"] == {"enabled": False}
    assert toolset["configs"] == [{"name": "microsoft_docs_search", "enabled": True}]


def test_verify_model_override_applies_to_verify_calls_only():
    config = Config.model_validate({"verify": {"model": "claude-haiku-4-5"}})
    fake = FakeAnthropic(json_responder(CONFIRMED))
    Verifier(config, RULES, fake, url_checker=ok_checker).verify_workspace(
        workspace_of(requirement("REQ-001"))
    )
    assert fake.calls[0]["model"] == "claude-haiku-4-5"
    assert config.model == "claude-sonnet-4-6"  # run model untouched


def test_max_searches_caps_web_search_mode():
    config = Config.model_validate(
        {"verify": {"mode": "web_search", "max_searches": 2}}
    )
    fake = FakeAnthropic(json_responder(CONFIRMED))
    Verifier(config, RULES, fake, url_checker=ok_checker).verify_workspace(
        workspace_of(requirement("REQ-001"))
    )
    assert fake.calls[0]["tools"][0]["max_uses"] == 2
