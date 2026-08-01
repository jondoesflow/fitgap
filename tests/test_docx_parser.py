from conftest import (
    DOCX_NUMBERED_1,
    DOCX_NUMBERED_2,
    DOCX_SECTION_BULLET,
    DOCX_SECTION_PARA,
    DOCX_TABLE_ROW_1,
    DOCX_TABLE_ROW_2,
)

from fitgap.ingest import parse_docx
from fitgap.models import SourceReliability, SourceType


def test_extracts_all_requirement_shapes(sample_docx):
    reqs = parse_docx(sample_docx)
    texts = {r.text for r in reqs}
    assert texts == {
        DOCX_SECTION_PARA,
        DOCX_SECTION_BULLET,
        DOCX_NUMBERED_1,
        DOCX_NUMBERED_2,
        DOCX_TABLE_ROW_1,
        DOCX_TABLE_ROW_2,
    }


def test_intro_noise_is_not_extracted(sample_docx):
    reqs = parse_docx(sample_docx)
    assert not any("gathered during workshops" in r.text for r in reqs)


def test_table_rows_carry_id_and_priority(sample_docx):
    reqs = {r.text: r for r in parse_docx(sample_docx)}
    row1 = reqs[DOCX_TABLE_ROW_1]
    assert "R-1" in row1.source_ref
    assert row1.priority == "Must"
    assert row1.source == SourceType.DOCX


def test_source_refs_are_traceable(sample_docx):
    for req in parse_docx(sample_docx):
        assert req.source_ref.startswith(sample_docx.name)
        assert req.source_reliability == SourceReliability.STATED


def test_section_paragraph_ref_includes_heading(sample_docx):
    reqs = {r.text: r for r in parse_docx(sample_docx)}
    assert "Sales Requirements" in reqs[DOCX_SECTION_PARA].source_ref
