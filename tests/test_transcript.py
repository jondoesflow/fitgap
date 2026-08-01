import json

import pytest
from fakes import FakeAnthropic, tool_use_block

from fitgap.config import Config
from fitgap.ingest.transcript import (
    TranscriptExtractor,
    _chunk,
    Cue,
    parse_transcript,
    parse_txt_lines,
    parse_vtt,
)
from fitgap.models import FunctionalArea, SourceReliability, SourceType
from fitgap.redact import RedactionRules

VTT_CONTENT = """WEBVTT

NOTE This is a teams export
confidential

1
00:00:05.000 --> 00:00:09.000
<v Jane Doe>Welcome everyone, let's get started.</v>

2
00:14:02.500 --> 00:14:10.000
<v Ops Lead>Every quote over 50k needs director approval before it goes out.</v>

3
00:14:12.000 --> 00:14:20.000
Sales Rep: And we waste hours rekeying orders into the ERP.
This continuation line belongs to the same cue.

4
00:15:01.000 --> 00:15:04.000
Untagged remark without a speaker.
"""

TXT_CONTENT = [
    "[00:02:10] Facilitator: What slows the team down today?",
    "[00:02:44] Ops Lead: Chasing approvals for big quotes by email.",
    "Plain line without timestamp or speaker.",
]


def test_parse_vtt_cues(tmp_path):
    path = tmp_path / "workshop.vtt"
    path.write_text(VTT_CONTENT, encoding="utf-8")
    cues = parse_vtt(path)
    assert [c.timestamp for c in cues] == ["00:00:05", "00:14:02", "00:14:12", "00:15:01"]
    assert cues[1].speaker == "Ops Lead"
    assert "director approval" in cues[1].text
    assert cues[2].speaker == "Sales Rep"
    assert "continuation line" in cues[2].text  # merged into the same cue
    assert cues[3].speaker == ""
    assert not any("teams export" in c.text for c in cues)  # NOTE block skipped


def test_parse_txt_lines():
    cues = parse_txt_lines(TXT_CONTENT)
    assert cues[0] == Cue("00:02:10", "Facilitator", "What slows the team down today?")
    assert cues[1].speaker == "Ops Lead"
    assert cues[2] == Cue("", "", "Plain line without timestamp or speaker.")


def test_parse_transcript_docx(tmp_path):
    from docx import Document

    path = tmp_path / "workshop.docx"
    doc = Document()
    doc.add_paragraph("[00:05:00] Jane Doe: We need mobile access for engineers.")
    doc.save(str(path))
    cues = parse_transcript(path)
    assert cues[0].timestamp == "00:05:00"
    assert cues[0].speaker == "Jane Doe"


def test_unsupported_transcript_type(tmp_path):
    path = tmp_path / "audio.mp3"
    path.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported transcript"):
        parse_transcript(path)


def test_chunking_respects_char_limit():
    cues = [Cue("00:00:01", "A", "x" * 100) for _ in range(100)]
    chunks = _chunk(cues, char_limit=1000)
    assert len(chunks) > 1
    assert sum(len(c) for c in chunks) == 100


def extraction_responder(kwargs):
    return [
        tool_use_block(
            "record_extracted_requirements",
            {
                "requirements": [
                    {
                        "text": "The system must route quotes over 50k to a director for approval.",
                        "timestamp": "00:14:02",
                        "speaker": "Ops Lead",
                        "functional_area": "Sales",
                    },
                    {
                        "text": "Orders must flow to the ERP without manual rekeying.",
                        "timestamp": "00:14:12",
                        "speaker": "Sales Rep",
                        "functional_area": "Integration",
                    },
                ]
            },
        )
    ]


def test_extractor_builds_inferred_requirements(tmp_path):
    path = tmp_path / "workshop.vtt"
    path.write_text(VTT_CONTENT, encoding="utf-8")
    extractor = TranscriptExtractor(
        Config(), RedactionRules(people=["Jane Doe"]), FakeAnthropic(extraction_responder)
    )
    reqs, events = extractor.extract(path)
    assert len(reqs) == 2
    first = reqs[0]
    assert first.source == SourceType.TRANSCRIPT
    assert first.source_reliability == SourceReliability.INFERRED
    assert first.source_ref == "workshop.vtt#00:14:02 (Ops Lead)"
    assert first.functional_area == FunctionalArea.SALES
    assert events  # Jane Doe was redacted from the outgoing chunk


def test_transcript_text_is_redacted_before_send(tmp_path):
    path = tmp_path / "workshop.vtt"
    path.write_text(VTT_CONTENT, encoding="utf-8")
    fake = FakeAnthropic(extraction_responder)
    TranscriptExtractor(
        Config(), RedactionRules(people=["Jane Doe"]), fake
    ).extract(path)
    sent = json.dumps(fake.calls[0]["messages"])
    assert "Jane Doe" not in sent
    assert "[PERSON]" in sent


def test_duplicate_extractions_collapse(tmp_path):
    def responder(kwargs):
        blocks = extraction_responder(kwargs)
        blocks[0].input["requirements"].append(
            {
                "text": "The system must route quotes over 50k to a director for approval.",
                "timestamp": "00:20:00",
                "speaker": "Ops Lead",
            }
        )
        return blocks

    path = tmp_path / "workshop.vtt"
    path.write_text(VTT_CONTENT, encoding="utf-8")
    reqs, _ = TranscriptExtractor(
        Config(), RedactionRules(), FakeAnthropic(responder)
    ).extract(path)
    assert len(reqs) == 2  # exact-duplicate text collapsed within the file
