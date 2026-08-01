"""Live smoke tests — hit the real Anthropic API. Skipped without an API key."""

import os

import pytest

from fitgap.classify import Classifier
from fitgap.config import Config
from fitgap.models import Requirement, SourceType, Workspace
from fitgap.redact import RedactionRules

pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set — live smoke test skipped",
)


def test_live_verification_real_and_fake_feature():
    """Live gate: a real feature verifies; a fabricated one must not."""
    import anthropic

    from fitgap.models import (
        Category,
        Classification,
        Confidence,
        Effort,
        VerificationStatus,
    )
    from fitgap.verify import Verifier

    def claim(req_id: str, feature: str, text: str) -> Requirement:
        return Requirement(
            id=req_id,
            text=text,
            source=SourceType.DOCX,
            source_ref="smoke.docx#1",
            classification=Classification(
                category=Category.FIT_OOB,
                proposed_approach=f"Use {feature}.",
                feature_relied_on=feature,
                effort=Effort.S,
                confidence=Confidence.HIGH,
            ),
        )

    workspace = Workspace(
        tool_version="test",
        requirements=[
            claim(
                "REQ-001",
                "Dynamics 365 Sales — lead qualification",
                "Sales users must qualify leads into opportunities.",
            ),
            claim(
                "REQ-002",
                "Dynamics 365 Sales — quantum lead teleportation module",
                "Leads must be teleported instantly between legal entities.",
            ),
        ],
    )
    verifier = Verifier(Config(), RedactionRules(), anthropic.Anthropic())
    verifier.verify_workspace(workspace)

    real, fake = workspace.requirements
    assert real.verification.status == VerificationStatus.VERIFIED
    assert real.verification.citation_url.startswith("https://learn.microsoft.com/")
    # The invented feature must never come back verified.
    assert fake.verification.status == VerificationStatus.UNCONFIRMED
    assert fake.verification.citation_url is None


def test_live_classification_of_one_requirement():
    import anthropic

    workspace = Workspace(
        tool_version="test",
        requirements=[
            Requirement(
                id="REQ-001",
                text=(
                    "Sales users must be able to qualify a lead and have an "
                    "opportunity created automatically."
                ),
                source=SourceType.DOCX,
                source_ref="smoke.docx#1",
            )
        ],
    )
    classifier = Classifier(Config(), RedactionRules(), anthropic.Anthropic())
    classified, missing = classifier.classify_workspace(workspace)
    assert classified == 1
    assert missing == []
    classification = workspace.requirements[0].classification
    assert classification is not None
    assert classification.feature_relied_on
    assert classification.proposed_approach
