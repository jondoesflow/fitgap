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
from fitgap.llm import StructuredOutputError, as_llm_client
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
        client,  # fitgap.llm.LLMClient, anthropic.Anthropic, or a test double
        batch_size: int = DEFAULT_BATCH_SIZE,
        usage_tracker=None,  # fitgap.usage.UsageTracker
    ) -> None:
        self.config = config
        self.rules = rules
        self.llm = as_llm_client(client, model=config.model)
        self.batch_size = batch_size
        self.usage_tracker = usage_tracker

    def classify_workspace(
        self,
        workspace: Workspace,
        force: bool = False,
        on_progress=None,  # callable(done, total) for progress display
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
        done = 0
        missing: list[str] = []
        if on_progress:
            on_progress(0, len(todo))
        for start in range(0, len(todo), self.batch_size):
            batch = todo[start : start + self.batch_size]
            classified += self._classify_batch(batch, workspace)
            done += len(batch)
            if on_progress:
                on_progress(done, len(todo))
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

        try:
            tool_input = self.llm.structured(
                system=CLASSIFY_SYSTEM_PROMPT,
                user=(
                    "Classify the following requirements:\n\n"
                    + json.dumps(payload, indent=2)
                ),
                tool_name=CLASSIFY_TOOL["name"],
                tool_description=CLASSIFY_TOOL["description"],
                schema=CLASSIFY_TOOL["input_schema"],
                max_tokens=8192,
                stage="classify",
                tracker=self.usage_tracker,
            )
        except StructuredOutputError as exc:
            raise ClassificationError(str(exc)) from exc

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
