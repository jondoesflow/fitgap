"""Progress callbacks fire with correct counts on every slow stage."""

import json

from fakes import FakeAnthropic, text_block, tool_use_block
from test_classify import RULES, echo_responder, make_workspace
from test_verify import LEARN_URL, json_responder, ok_checker

from fitgap.classify import Classifier
from fitgap.config import Config
from fitgap.ingest.transcript import TranscriptExtractor
from fitgap.models import Category
from fitgap.redact import RedactionRules
from fitgap.verify import Verifier


def test_classify_reports_progress_per_batch():
    workspace = make_workspace(12)
    seen = []
    Classifier(Config(), RULES, FakeAnthropic(echo_responder), batch_size=5).classify_workspace(
        workspace, on_progress=lambda done, total: seen.append((done, total))
    )
    assert seen == [(0, 12), (5, 12), (10, 12), (12, 12)]


def test_verify_reports_progress_per_requirement():
    from test_verify import make_workspace as make_verify_workspace

    workspace = make_verify_workspace()
    workspace.requirements *= 1  # single claim
    seen = []
    Verifier(
        Config(),
        RedactionRules(),
        FakeAnthropic(json_responder({"confirmed": True, "citation_url": LEARN_URL})),
        url_checker=ok_checker,
    ).verify_workspace(
        workspace, on_progress=lambda done, total: seen.append((done, total))
    )
    assert seen == [(0, 1), (1, 1)]


def test_transcript_extraction_reports_progress_per_chunk(tmp_path):
    lines = "\n".join(
        f"[00:0{i}:00] Speaker: This is workshop discussion line number {i} with enough text."
        for i in range(1, 200)
    )
    path = tmp_path / "long.txt"
    path.write_text(lines, encoding="utf-8")

    def responder(kwargs):
        return [tool_use_block("record_extracted_requirements", {"requirements": []})]

    seen = []
    TranscriptExtractor(Config(), RedactionRules(), FakeAnthropic(responder)).extract(
        path, on_progress=lambda done, total: seen.append((done, total))
    )
    total = seen[0][1]
    assert total >= 2  # long transcript must produce multiple chunks
    assert seen[0] == (0, total)
    assert seen[-1] == (total, total)
    assert [s[0] for s in seen] == list(range(0, total + 1))
