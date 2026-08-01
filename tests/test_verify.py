import json

from fakes import FakeAnthropic, text_block

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
from fitgap.redact import RedactionRules
from fitgap.verify import UrlCheckResult, Verifier
from fitgap.verify.learn_verifier import _is_learn_host

LEARN_URL = "https://learn.microsoft.com/en-us/dynamics365/sales/manage-opportunities"


def make_workspace(category: Category = Category.FIT_OOB) -> Workspace:
    return Workspace(
        tool_version="test",
        requirements=[
            Requirement(
                id="REQ-001",
                text="Contoso must manage opportunities in a pipeline.",
                source=SourceType.DOCX,
                source_ref="brd.docx#1",
                classification=Classification(
                    category=category,
                    proposed_approach="Use standard opportunity management.",
                    feature_relied_on="Dynamics 365 Sales — opportunity management",
                    effort=Effort.S,
                    confidence=Confidence.HIGH,
                ),
            )
        ],
    )


def json_responder(payload: dict):
    return lambda kwargs: [text_block(json.dumps(payload))]


def ok_checker(url: str) -> UrlCheckResult:
    return UrlCheckResult(ok=True, final_url=url, page_title="Manage opportunities")


def failing_checker(url: str) -> UrlCheckResult:
    return UrlCheckResult(ok=False, reason="citation returned HTTP 404")


RULES = RedactionRules(client_names=["Contoso"])


def verifier(responder, checker, config: Config | None = None) -> Verifier:
    return Verifier(
        config or Config(), RULES, FakeAnthropic(responder), url_checker=checker
    )


def test_confirmed_claim_with_live_url_is_verified():
    workspace = make_workspace()
    counts = verifier(
        json_responder(
            {
                "confirmed": True,
                "citation_url": LEARN_URL,
                "page_title": "Manage opportunities",
                "preview": False,
                "deprecated": False,
                "notes": "Documented capability.",
            }
        ),
        ok_checker,
    ).verify_workspace(workspace)
    verification = workspace.requirements[0].verification
    assert counts["verified"] == 1
    assert verification.status == VerificationStatus.VERIFIED
    assert verification.citation_url == LEARN_URL
    assert verification.retrieved_at is not None


def test_fake_feature_citation_is_downgraded():
    """THE fake-feature guarantee: a citation that does not resolve never survives."""
    workspace = make_workspace()
    counts = verifier(
        json_responder(
            {
                "confirmed": True,
                "citation_url": "https://learn.microsoft.com/en-us/dynamics365/sales/quantum-lead-teleportation",
                "page_title": "Quantum Lead Teleportation",
            }
        ),
        failing_checker,
    ).verify_workspace(workspace)
    verification = workspace.requirements[0].verification
    assert counts["unconfirmed"] == 1
    assert verification.status == VerificationStatus.UNCONFIRMED
    assert verification.citation_url is None
    assert "rejected" in verification.notes


def test_non_learn_domain_is_rejected_before_any_fetch():
    assert not _is_learn_host("https://evil.example.com/learn.microsoft.com/page")
    assert not _is_learn_host("https://docs.microsoft.com.evil.com/x")
    assert _is_learn_host("https://learn.microsoft.com/en-us/whatever")

    workspace = make_workspace()
    from fitgap.verify import check_learn_url

    result = check_learn_url("https://evil.example.com/fake")
    assert not result.ok

    counts = verifier(
        json_responder(
            {"confirmed": True, "citation_url": "https://evil.example.com/fake"}
        ),
        lambda url: check_learn_url(url),  # real guard, no network for bad host
    ).verify_workspace(workspace)
    assert counts["unconfirmed"] == 1


def test_unconfirmed_answer_is_respected():
    workspace = make_workspace()
    verifier(
        json_responder(
            {
                "confirmed": False,
                "citation_url": None,
                "notes": "No Learn page documents native SMS surveys.",
            }
        ),
        ok_checker,
    ).verify_workspace(workspace)
    verification = workspace.requirements[0].verification
    assert verification.status == VerificationStatus.UNCONFIRMED
    assert "SMS" in verification.notes


def test_gap_rows_marked_not_required():
    workspace = make_workspace(Category.GAP_CUSTOM)
    fake = FakeAnthropic(json_responder({"confirmed": True}))
    counts = Verifier(
        Config(), RULES, fake, url_checker=ok_checker
    ).verify_workspace(workspace)
    assert counts["not_required"] == 1
    assert fake.calls == []  # no API call for rows without a claim
    assert (
        workspace.requirements[0].verification.status
        == VerificationStatus.NOT_REQUIRED
    )


def test_garbage_model_output_downgrades():
    workspace = make_workspace()
    verifier(
        lambda kwargs: [text_block("I could not decide, sorry!")], ok_checker
    ).verify_workspace(workspace)
    assert (
        workspace.requirements[0].verification.status
        == VerificationStatus.UNCONFIRMED
    )


def test_api_exception_downgrades_not_crashes():
    def exploding(kwargs):
        raise RuntimeError("boom: API unavailable")

    workspace = make_workspace()
    verifier(exploding, ok_checker).verify_workspace(workspace)
    verification = workspace.requirements[0].verification
    assert verification.status == VerificationStatus.UNCONFIRMED
    assert "boom" in verification.notes


def test_preview_flag_from_model_and_title():
    workspace = make_workspace()
    verifier(
        json_responder(
            {"confirmed": True, "citation_url": LEARN_URL, "preview": True}
        ),
        ok_checker,
    ).verify_workspace(workspace)
    assert workspace.requirements[0].verification.preview_flag

    workspace2 = make_workspace()
    verifier(
        json_responder({"confirmed": True, "citation_url": LEARN_URL}),
        lambda url: UrlCheckResult(
            ok=True, final_url=url, page_title="Copilot features (preview)"
        ),
    ).verify_workspace(workspace2)
    assert workspace2.requirements[0].verification.preview_flag


def test_client_name_redacted_in_verify_calls():
    workspace = make_workspace()
    fake = FakeAnthropic(
        json_responder({"confirmed": True, "citation_url": LEARN_URL})
    )
    Verifier(Config(), RULES, fake, url_checker=ok_checker).verify_workspace(workspace)
    request_text = json.dumps(fake.calls[0]["messages"])
    assert "Contoso" not in request_text
    assert "[CLIENT]" in request_text
    assert workspace.redaction_log


def test_mcp_mode_attaches_learn_mcp_server():
    workspace = make_workspace()
    fake = FakeAnthropic(json_responder({"confirmed": True, "citation_url": LEARN_URL}))
    Verifier(Config(), RULES, fake, url_checker=ok_checker).verify_workspace(workspace)
    call = fake.calls[0]
    assert call["mcp_servers"][0]["url"] == "https://learn.microsoft.com/api/mcp"


def test_web_search_mode_locks_to_learn_domain():
    config = Config()
    config.verify.mode = "web_search"
    workspace = make_workspace()
    fake = FakeAnthropic(json_responder({"confirmed": True, "citation_url": LEARN_URL}))
    Verifier(config, RULES, fake, url_checker=ok_checker).verify_workspace(workspace)
    call = fake.calls[0]
    assert "mcp_servers" not in call
    assert call["tools"][0]["allowed_domains"] == ["learn.microsoft.com"]


def test_already_verified_skipped_unless_force():
    workspace = make_workspace()
    responder = json_responder({"confirmed": True, "citation_url": LEARN_URL})
    fake = FakeAnthropic(responder)
    v = Verifier(Config(), RULES, fake, url_checker=ok_checker)
    v.verify_workspace(workspace)
    assert len(fake.calls) == 1
    v.verify_workspace(workspace)  # second run — nothing to do
    assert len(fake.calls) == 1
    v.verify_workspace(workspace, force=True)
    assert len(fake.calls) == 2
