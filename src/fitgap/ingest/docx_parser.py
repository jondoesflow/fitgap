"""Extract requirement statements from Word documents.

Three extraction strategies, applied in document order:

1. **Tables** anywhere in the document whose header row contains a
   requirement/description column. Optional ID / priority / functional-area
   columns are picked up when present.
2. **Numbered lists** anywhere in the document (numbered-list style, or plain
   paragraphs that start with "1." / "1)" numbering).
3. **Headed requirement sections**: under any heading whose text contains
   "requirement", every body paragraph and bullet is treated as a requirement.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

from docx import Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph

from fitgap.models import FunctionalArea, ParsedRequirement, SourceType

MIN_REQUIREMENT_LEN = 10
NUMBERED_RE = re.compile(r"^\s*\d+[.)]\s+(?P<text>.+)$")

TEXT_HEADERS = ("requirement", "description", "user story")
ID_HEADERS = ("id", "ref", "req id", "reference", "req#")
PRIORITY_HEADERS = ("priority", "moscow")
AREA_HEADERS = ("functional area", "area", "module", "workstream")

_AREA_LOOKUP = {area.value.lower(): area for area in FunctionalArea}


def _match_area(value: str | None) -> FunctionalArea:
    if not value:
        return FunctionalArea.OTHER
    return _AREA_LOOKUP.get(value.strip().lower(), FunctionalArea.OTHER)


def _iter_blocks(doc) -> Iterator[Paragraph | Table]:
    """Yield paragraphs and tables in true document order."""
    for child in doc.element.body.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, doc)
        elif isinstance(child, CT_Tbl):
            yield Table(child, doc)


def _style_name(paragraph: Paragraph) -> str:
    try:
        return paragraph.style.name or ""
    except AttributeError:
        return ""


def _find_column(headers: list[str], candidates: tuple[str, ...]) -> int | None:
    for idx, header in enumerate(headers):
        cleaned = header.strip().lower()
        if any(cleaned == c or c in cleaned for c in candidates):
            return idx
    return None


def _parse_table(
    table: Table, table_index: int, file_name: str
) -> list[ParsedRequirement]:
    rows = table.rows
    if len(rows) < 2:
        return []
    headers = [cell.text for cell in rows[0].cells]
    text_col = _find_column(headers, TEXT_HEADERS)
    if text_col is None:
        return []
    id_col = _find_column(headers, ID_HEADERS)
    priority_col = _find_column(headers, PRIORITY_HEADERS)
    area_col = _find_column(headers, AREA_HEADERS)

    results: list[ParsedRequirement] = []
    for row_index, row in enumerate(rows[1:], start=2):
        cells = [cell.text.strip() for cell in row.cells]
        text = cells[text_col] if text_col < len(cells) else ""
        if len(text) < MIN_REQUIREMENT_LEN:
            continue
        ref = f"{file_name}#table{table_index}-row{row_index}"
        if id_col is not None and cells[id_col]:
            ref += f" ({cells[id_col]})"
        results.append(
            ParsedRequirement(
                text=text,
                source=SourceType.DOCX,
                source_ref=ref,
                priority=cells[priority_col] or None if priority_col is not None else None,
                functional_area=_match_area(
                    cells[area_col] if area_col is not None else None
                ),
            )
        )
    return results


def parse_docx(path: Path) -> list[ParsedRequirement]:
    doc = Document(str(path))
    file_name = path.name
    results: list[ParsedRequirement] = []
    seen_texts: set[str] = set()

    def add(text: str, ref: str) -> None:
        text = text.strip()
        if len(text) < MIN_REQUIREMENT_LEN or text in seen_texts:
            return
        seen_texts.add(text)
        results.append(
            ParsedRequirement(text=text, source=SourceType.DOCX, source_ref=ref)
        )

    current_heading = ""
    in_requirements_section = False
    table_index = 0
    para_index = 0

    for block in _iter_blocks(doc):
        if isinstance(block, Table):
            table_index += 1
            for req in _parse_table(block, table_index, file_name):
                if req.text not in seen_texts:
                    seen_texts.add(req.text)
                    results.append(req)
            continue

        para_index += 1
        style = _style_name(block)
        text = block.text.strip()

        if style.startswith("Heading") or style == "Title":
            current_heading = text
            in_requirements_section = "requirement" in text.lower()
            continue
        if not text:
            continue

        ref = f"{file_name}#para{para_index}"
        if current_heading:
            ref += f" ({current_heading})"

        numbered_match = NUMBERED_RE.match(text)
        if "List Number" in style:
            add(text, ref)
        elif numbered_match:
            add(numbered_match.group("text"), ref)
        elif in_requirements_section:
            # Bullets and plain body text under a requirements heading.
            add(text, ref)

    return results
