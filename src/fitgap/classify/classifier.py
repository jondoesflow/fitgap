"""Batched, structured classification of requirements via the Anthropic API.

The model is forced onto a ``record_classifications`` tool whose JSON schema
mirrors the Classification model, so responses are validated structurally.
Requirement text is anonymised before it is sent; every substitution is
appended to the workspace redaction log.
"""

from __future__ import annotations

import json

from pydantic import ValidationError

from fitgap.classify.prompts import CLASSIFY_SYSTEM_PROMPT
from fitgap.config import Config
from fitgap.models import (
    Category,
    Classification,
    Confidence,
    Effort,
    FunctionalArea,
    Requirement,
    Workspace,
)
from fitgap.redact import RedactionRules, anonymise

DEFAULT_BATCH_SIZE = 10

CLASSIFY_TOOL = {
    "name": "record_classifications",
    "description": "Record the fit-gap classification for each requirement.",
    "input_schema": {
        "type": "object",
        "properties": {
            "classifications": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "requirement_id": {"type": "string"},
                        "category": {
                            "type": "string",
                            "enum": [c.value for c in Category],
                        },
                        "proposed_approach": {"type": "string"},
                        "feature_relied_on": {"type": "string"},
                        "functional_area": {
                            "type": "string",
                            "enum": [a.value for a in FunctionalArea],
                        },
                        "effort": {
                            "type": "string",
                            "enum": [e.value for e in Effort],
                        },
                        "assumptions": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "confidence": {
                            "type": "string",
                            "enum": [c.value for c in Confidence],
                        },
                    },
                    "required": [
                        "requirement_id",
                        "category",
                        "proposed_approach",
                        "feature_relied_on",
                        "functional_area",
                        "effort",
                        "assumptions",
                        "confidence",
                    ],
                },
            }
        },
        "required": ["classifications"],
    },
}


class ClassificationError(RuntimeError):
    pass


class Classifier:
    def __init__(
        self,
        config: Config,
        rules: RedactionRules,
        client,  # anthropic.Anthropic or a test double
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self.config = config
        self.rules = rules
        self.client = client
        self.batch_size = batch_size

    def classify_workspace(
        self, workspace: Workspace, force: bool = False
    ) -> tuple[int, list[str]]:
        """Classify requirements in place.

        Returns (classified_count, unclassified_ids). Already-classified
        requirements are skipped unless ``force`` is set.
        """
        todo = [
            r
            for r in workspace.requirements
            if force or r.classification is None
        ]
        classified = 0
        missing: list[str] = []
        for start in range(0, len(todo), self.batch_size):
            batch = todo[start : start + self.batch_size]
            classified += self._classify_batch(batch, workspace)
        for requirement in todo:
            if requirement.classification is None:
                missing.append(requirement.id)
        return classified, missing

    def _classify_batch(
        self, batch: list[Requirement], workspace: Workspace
    ) -> int:
        payload = []
        for requirement in batch:
            redacted_text, events = anonymise(
                requirement.text, self.rules, requirement_id=requirement.id
            )
            workspace.redaction_log.extend(events)
            payload.append(
                {
                    "requirement_id": requirement.id,
                    "text": redacted_text,
                    "functional_area_hint": requirement.functional_area.value,
                    "priority": requirement.priority,
                    "source_reliability": requirement.source_reliability.value,
                }
            )

        response = self.client.messages.create(
            model=self.config.model,
            max_tokens=8192,
            system=CLASSIFY_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Classify the following requirements:\n\n"
                        + json.dumps(payload, indent=2)
                    ),
                }
            ],
            tools=[CLASSIFY_TOOL],
            tool_choice={"type": "tool", "name": "record_classifications"},
        )

        tool_input = None
        for block in response.content:
            if getattr(block, "type", None) == "tool_use":
                tool_input = block.input
                break
        if tool_input is None:
            raise ClassificationError(
                "Model response contained no record_classifications tool call."
            )

        by_id = {r.id: r for r in batch}
        applied = 0
        for entry in tool_input.get("classifications", []):
            requirement = by_id.get(entry.get("requirement_id"))
            if requirement is None:
                continue  # hallucinated id — ignore, the caller reports gaps
            try:
                requirement.classification = Classification(
                    category=entry["category"],
                    proposed_approach=entry["proposed_approach"],
                    feature_relied_on=entry["feature_relied_on"],
                    effort=entry["effort"],
                    assumptions=entry.get("assumptions", []),
                    confidence=entry["confidence"],
                )
            except (KeyError, ValidationError) as exc:
                raise ClassificationError(
                    f"Invalid classification for {requirement.id}: {exc}"
                ) from exc
            # The model's functional area wins only where ingest had nothing.
            if requirement.functional_area == FunctionalArea.OTHER:
                area = entry.get("functional_area")
                if area:
                    requirement.functional_area = FunctionalArea(area)
            applied += 1
        return applied
