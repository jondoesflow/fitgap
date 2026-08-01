import pytest
from conftest import XLSX_ROWS

from fitgap.config import XlsxMapping
from fitgap.ingest.xlsx_parser import parse_xlsx, resolve_mapping
from fitgap.models import FunctionalArea, SourceType


def test_auto_detects_mapping(sample_xlsx):
    mapping = resolve_mapping(sample_xlsx, None, interactive=False)
    assert mapping.text == "Requirement"
    assert mapping.id == "ID"
    assert mapping.priority == "Priority"
    assert mapping.functional_area == "Functional Area"


def test_parses_rows_with_metadata(sample_xlsx):
    mapping = resolve_mapping(sample_xlsx, None, interactive=False)
    reqs = parse_xlsx(sample_xlsx, mapping)
    assert len(reqs) == len(XLSX_ROWS)
    first = reqs[0]
    assert first.text == XLSX_ROWS[0][1]
    assert first.priority == "Must"
    assert first.functional_area == FunctionalArea.CUSTOMER_SERVICE
    assert first.source == SourceType.XLSX
    assert "R-10" in first.source_ref


def test_saved_mapping_is_used_verbatim(sample_xlsx):
    mapping = XlsxMapping(text="Requirement")
    reqs = parse_xlsx(sample_xlsx, mapping)
    assert len(reqs) == len(XLSX_ROWS)
    assert all(r.priority is None for r in reqs)


def test_undetectable_headers_fail_loudly_when_non_interactive(headerless_xlsx):
    with pytest.raises(ValueError, match="Could not detect"):
        resolve_mapping(headerless_xlsx, None, interactive=False)


def test_missing_mapped_column_fails_loudly(sample_xlsx):
    with pytest.raises(ValueError, match="not found"):
        parse_xlsx(sample_xlsx, XlsxMapping(text="Nonexistent"))
