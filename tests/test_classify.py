import json

import pytest
from fakes import FakeAnthropic, text_block, tool_use_block

from fitgap.classify import Classifier
from fitgap.classify.classifier import ClassificationError
from fitgap.config import Config
from fitgap.models import (
    Category,
    Classification,
    FunctionalArea,
    Requirement,
    SourceType,
    Workspace,
)
from fitgap.redact import RedactionRules


def make_workspace(n: int) -> Workspace:
    return Workspace(
        tool_version="test",
        requirements=[
            Requirement(
                id=f"REQ-{i:03d}",
                text=f"Contoso needs capability number {i} for its sales team.",
                source=SourceType.DOCX,
                source_ref=f"brd.docx#para{i}",
            )
            for i in range(1, n + 1)
        ],
    )


def classification_entry(req_id: str, **overrides) -> dict:
    entry = {
        "requirement_id": req_id,
        "category": "Fit — OOB",
        "proposed_approach": "Use the standard sales pipeline.",
        "feature_relied_on": "Dynamics 365 Sales — opportunity management",
        "functional_area": "Sales",
        "effort": "S",
        "assumptions": ["Standard sales process is acceptable."],
        "confidence": "High",
    }
    entry.update(overrides)
    return entry


def echo_responder(kwargs):
    """Classify every requirement id found in the request payload."""
    payload = json.loads(kwargs["messages"][0]["content"].split(":\n\n", 1)[1])
    return [
        tool_use_block(
            "record_classifications",
            {
                "classifications": [
                    classification_entry(item["requirement_id"]) for item in payload
                ]
            },
        )
    ]


RULES = RedactionRules(client_names=["Contoso"])


def test_classifications_are_applied():
    workspace = make_workspace(3)
    classifier = Classifier(Config(), RULES, FakeAnthropic(echo_responder))
    classified, missing = classifier.classify_workspace(workspace)
    assert classified == 3
    assert missing == []
    for req in workspace.requirements:
        assert req.classification.category == Category.FIT_OOB
        assert req.functional_area == FunctionalArea.SALES


def test_client_name_never_reaches_the_api():
    workspace = make_workspace(2)
    fake = FakeAnthropic(echo_responder)
    Classifier(Config(), RULES, fake).classify_workspace(workspace)
    for call in fake.calls:
        request_text = json.dumps(call["messages"])
        assert "Contoso" not in request_text
        assert "[CLIENT]" in request_text
    assert workspace.redaction_log
    assert workspace.redaction_log[0].requirement_id == "REQ-001"


def test_batching_splits_calls():
    workspace = make_workspace(12)
    fake = FakeAnthropic(echo_responder)
    classified, _ = Classifier(
        Config(), RULES, fake, batch_size=5
    ).classify_workspace(workspace)
    assert classified == 12
    assert len(fake.calls) == 3


def test_already_classified_skipped_unless_forced():
    workspace = make_workspace(2)
    workspace.requirements[0].classification = Classification(
        category=Category.GAP_CUSTOM,
        proposed_approach="x",
        feature_relied_on="x",
        effort="L",
        confidence="Low",
    )
    fake = FakeAnthropic(echo_responder)
    classified, _ = Classifier(Config(), RULES, fake).classify_workspace(workspace)
    assert classified == 1
    assert workspace.requirements[0].classification.category == Category.GAP_CUSTOM

    classified, _ = Classifier(Config(), RULES, fake).classify_workspace(
        workspace, force=True
    )
    assert classified == 2
    assert workspace.requirements[0].classification.category == Category.FIT_OOB


def test_hallucinated_id_ignored_and_missing_reported():
    def responder(kwargs):
        return [
            tool_use_block(
                "record_classifications",
                {"classifications": [classification_entry("REQ-999")]},
            )
        ]

    workspace = make_workspace(1)
    classified, missing = Classifier(
        Config(), RULES, FakeAnthropic(responder)
    ).classify_workspace(workspace)
    assert classified == 0
    assert missing == ["REQ-001"]
    assert workspace.requirements[0].classification is None


def test_invalid_category_fails_loudly():
    def responder(kwargs):
        return [
            tool_use_block(
                "record_classifications",
                {
                    "classifications": [
                        classification_entry("REQ-001", category="Fits nicely")
                    ]
                },
            )
        ]

    # Schema validation catches the bad enum, retries once (repair), then
    # fails loudly with an error naming the model and the offending value.
    fake = FakeAnthropic(responder)
    with pytest.raises(ClassificationError, match="claude-sonnet-4-6") as excinfo:
        Classifier(Config(), RULES, fake).classify_workspace(make_workspace(1))
    assert "Fits nicely" in str(excinfo.value)
    assert len(fake.calls) == 2  # original attempt + one repair retry


def test_missing_tool_call_fails_loudly():
    responder = lambda kwargs: [text_block("I refuse to use tools.")]
    with pytest.raises(ClassificationError, match="tool call"):
        Classifier(Config(), RULES, FakeAnthropic(responder)).classify_workspace(
            make_workspace(1)
        )


def test_ingest_area_wins_over_model_area():
    workspace = make_workspace(1)
    workspace.requirements[0].functional_area = FunctionalArea.SECURITY

    def responder(kwargs):
        return [
            tool_use_block(
                "record_classifications",
                {
                    "classifications": [
                        classification_entry("REQ-001", functional_area="Sales")
                    ]
                },
            )
        ]

    Classifier(Config(), RULES, FakeAnthropic(responder)).classify_workspace(workspace)
    assert workspace.requirements[0].functional_area == FunctionalArea.SECURITY
