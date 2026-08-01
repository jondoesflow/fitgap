from fitgap.models import ParsedRequirement, SourceReliability, SourceType
from fitgap.normalise import dedupe


def req(text: str, ref: str, reliability=SourceReliability.STATED) -> ParsedRequirement:
    return ParsedRequirement(
        text=text,
        source=SourceType.DOCX,
        source_ref=ref,
        source_reliability=reliability,
    )


def test_near_identical_requirements_merge():
    result = dedupe(
        [
            req("Quotes must be generated as branded PDF documents.", "a.docx#1"),
            req("Quotes must be generated as branded PDF documents", "b.xlsx#2"),
        ]
    )
    assert len(result) == 1
    assert result[0].merged_from == ["b.xlsx#2"]


def test_distinct_requirements_survive():
    result = dedupe(
        [
            req("Agents must see a unified timeline of customer interactions.", "a#1"),
            req("The solution must support appointment scheduling for field technicians.", "a#2"),
        ]
    )
    assert len(result) == 2
    assert all(not r.merged_from for r in result)


def test_stated_beats_inferred():
    result = dedupe(
        [
            req(
                "Send quote approval emails to managers automatically",
                "transcript#00:14",
                SourceReliability.INFERRED,
            ),
            req("Send quote approval emails to managers automatically.", "brd.docx#7"),
        ]
    )
    assert len(result) == 1
    assert result[0].source_reliability == SourceReliability.STATED
    assert result[0].source_ref == "brd.docx#7"
    assert result[0].merged_from == ["transcript#00:14"]


def test_threshold_is_respected():
    a = "Users can export contacts to Excel."
    b = "Users can export invoices to their accounting system nightly."
    assert len(dedupe([req(a, "1"), req(b, "2")], threshold=90)) == 2
