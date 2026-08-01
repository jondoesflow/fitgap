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
