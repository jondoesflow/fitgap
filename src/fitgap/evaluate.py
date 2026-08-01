"""Golden-set evaluation harness.

Runs the real classification (and optionally verification) pipeline against
``golden/golden_set.yaml`` and scores it. Release gates:

* classification accuracy >= 90% (expected or listed-acceptable category), and
* 100% of verified citations resolve live ("never asserts a citation that
  doesn't resolve").
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from fitgap.models import (
    Category,
    FunctionalArea,
    Requirement,
    SourceType,
    VerificationStatus,
    Workspace,
)
from fitgap.verify import check_learn_url

ACCURACY_GATE = 0.90


class GoldenEntry(BaseModel):
    id: str
    text: str
    functional_area: FunctionalArea = FunctionalArea.OTHER
    expected: Category
    acceptable: list[Category] = Field(default_factory=list)
    notes: str | None = None

    def is_correct(self, actual: Category) -> bool:
        return actual == self.expected or actual in self.acceptable


class GoldenSet(BaseModel):
    requirements: list[GoldenEntry]


def load_golden_set(path: Path) -> GoldenSet:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return GoldenSet.model_validate(raw)


def golden_workspace(golden: GoldenSet) -> Workspace:
    return Workspace(
        tool_version="eval",
        requirements=[
            Requirement(
                id=entry.id,
                text=entry.text,
                source=SourceType.DOCX,
                source_ref=f"golden_set.yaml#{entry.id}",
                functional_area=entry.functional_area,
            )
            for entry in golden.requirements
        ],
    )


@dataclass
class CategoryScore:
    total: int = 0
    correct: int = 0

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0


@dataclass
class EvalResult:
    total: int = 0
    correct: int = 0
    by_category: dict[str, CategoryScore] = field(default_factory=dict)
    mismatches: list[tuple[str, str, str]] = field(default_factory=list)  # id, expected, actual
    unclassified: list[str] = field(default_factory=list)
    citations_checked: int = 0
    citations_resolving: int = 0
    dead_citations: list[tuple[str, str]] = field(default_factory=list)  # id, url

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def citations_pass(self) -> bool:
        return self.citations_checked == self.citations_resolving

    @property
    def gates_met(self) -> bool:
        return self.accuracy >= ACCURACY_GATE and self.citations_pass


def score_classifications(golden: GoldenSet, workspace: Workspace) -> EvalResult:
    result = EvalResult()
    by_id = {r.id: r for r in workspace.requirements}
    scores: dict[str, CategoryScore] = defaultdict(CategoryScore)
    for entry in golden.requirements:
        result.total += 1
        score = scores[entry.expected.value]
        score.total += 1
        requirement = by_id.get(entry.id)
        if requirement is None or requirement.classification is None:
            result.unclassified.append(entry.id)
            result.mismatches.append((entry.id, entry.expected.value, "(unclassified)"))
            continue
        actual = requirement.classification.category
        if entry.is_correct(actual):
            result.correct += 1
            score.correct += 1
        else:
            result.mismatches.append((entry.id, entry.expected.value, actual.value))
    result.by_category = dict(scores)
    return result


def score_citations(workspace: Workspace, result: EvalResult) -> EvalResult:
    """Re-check every citation the pipeline asserted; all must resolve live."""
    for requirement in workspace.requirements:
        verification = requirement.verification
        if (
            verification is None
            or verification.status != VerificationStatus.VERIFIED
            or not verification.citation_url
        ):
            continue
        result.citations_checked += 1
        if check_learn_url(verification.citation_url).ok:
            result.citations_resolving += 1
        else:
            result.dead_citations.append(
                (requirement.id, verification.citation_url)
            )
    return result
