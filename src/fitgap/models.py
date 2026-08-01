"""Canonical requirement schema shared by every pipeline stage.

The workspace JSON file (``fitgap_workspace/requirements.json``) is the single
artifact each stage enriches: ingest creates it, classify fills in
``classification``, verify fills in ``verification``, report reads it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class SourceType(str, Enum):
    DOCX = "docx"
    XLSX = "xlsx"
    ADO = "ado"
    TRANSCRIPT = "transcript"


class SourceReliability(str, Enum):
    STATED = "stated"      # requirement was written down explicitly
    INFERRED = "inferred"  # derived from conversation — always double-check


class FunctionalArea(str, Enum):
    SALES = "Sales"
    CUSTOMER_SERVICE = "Customer Service"
    FIELD_SERVICE = "Field Service"
    MARKETING = "Marketing/CI-Journeys"
    PLATFORM = "Platform/Dataverse"
    INTEGRATION = "Integration"
    REPORTING = "Reporting"
    SECURITY = "Security"
    OTHER = "Other"


class Category(str, Enum):
    """Classification taxonomy in order of preference (lowest effort first)."""

    FIT_OOB = "Fit — OOB"
    FIT_CONFIG = "Fit — Configuration"
    EXTEND_PP = "Extend — Power Platform"
    GAP_ISV = "Gap — ISV"
    GAP_CUSTOM = "Gap — Custom"
    OUT_OF_SCOPE = "Out of scope / unclear"


#: Categories whose capability claims must be verified against Microsoft Learn.
CATEGORIES_REQUIRING_VERIFICATION = frozenset(
    {Category.FIT_OOB, Category.FIT_CONFIG, Category.EXTEND_PP}
)


class Effort(str, Enum):
    S = "S"
    M = "M"
    L = "L"
    XL = "XL"


class Confidence(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    UNCONFIRMED = "unconfirmed"    # rendered as "UNCONFIRMED — validate manually"
    NOT_REQUIRED = "not_required"  # Gap/out-of-scope rows carry no capability claim


class Classification(BaseModel):
    category: Category
    proposed_approach: str
    feature_relied_on: str
    effort: Effort
    assumptions: list[str] = Field(default_factory=list)
    confidence: Confidence


class Verification(BaseModel):
    status: VerificationStatus
    citation_url: str | None = None
    page_title: str | None = None
    retrieved_at: datetime | None = None
    preview_flag: bool = False
    deprecated_flag: bool = False
    notes: str | None = None


class ParsedRequirement(BaseModel):
    """A requirement as it comes out of a parser, before IDs are assigned."""

    text: str
    source: SourceType
    source_ref: str
    functional_area: FunctionalArea = FunctionalArea.OTHER
    priority: str | None = None
    source_reliability: SourceReliability = SourceReliability.STATED
    merged_from: list[str] = Field(default_factory=list)


class Requirement(BaseModel):
    id: str
    text: str
    source: SourceType
    source_ref: str
    functional_area: FunctionalArea = FunctionalArea.OTHER
    priority: str | None = None
    source_reliability: SourceReliability = SourceReliability.STATED
    merged_from: list[str] = Field(default_factory=list)
    classification: Classification | None = None
    verification: Verification | None = None


class RedactionEvent(BaseModel):
    rule: str          # e.g. "client_name", "person", "codename", "email", "custom"
    original: str
    replacement: str
    requirement_id: str | None = None


class Workspace(BaseModel):
    """The on-disk pipeline state."""

    tool_version: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    requirements: list[Requirement] = Field(default_factory=list)
    redaction_log: list[RedactionEvent] = Field(default_factory=list)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Workspace":
        return cls.model_validate_json(path.read_text(encoding="utf-8"))
