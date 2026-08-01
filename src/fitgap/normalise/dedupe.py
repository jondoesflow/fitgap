"""Deterministic near-duplicate merging using rapidfuzz (no LLM involved)."""

from __future__ import annotations

import re

from rapidfuzz import fuzz

from fitgap.models import ParsedRequirement, SourceReliability

_NORMALISE_RE = re.compile(r"[^a-z0-9 ]+")


def _normalise(text: str) -> str:
    return _NORMALISE_RE.sub(" ", text.lower()).strip()


def _prefer(a: ParsedRequirement, b: ParsedRequirement) -> ParsedRequirement:
    """Pick the survivor of a duplicate pair: stated beats inferred, then longer text."""
    if a.source_reliability != b.source_reliability:
        return a if a.source_reliability == SourceReliability.STATED else b
    return a if len(a.text) >= len(b.text) else b


def dedupe(
    requirements: list[ParsedRequirement], threshold: int = 90
) -> list[ParsedRequirement]:
    """Merge near-identical requirements; survivors record merged source refs."""
    survivors: list[ParsedRequirement] = []
    for candidate in requirements:
        candidate_norm = _normalise(candidate.text)
        matched = None
        for existing in survivors:
            score = fuzz.token_sort_ratio(candidate_norm, _normalise(existing.text))
            if score >= threshold:
                matched = existing
                break
        if matched is None:
            survivors.append(candidate.model_copy(deep=True))
            continue
        winner = _prefer(matched, candidate)
        loser = candidate if winner is matched else matched
        merged = sorted(
            set(matched.merged_from)
            | set(candidate.merged_from)
            | {loser.source_ref}
        )
        if winner is matched:
            matched.merged_from = merged
        else:
            replacement = candidate.model_copy(deep=True)
            replacement.merged_from = merged
            survivors[survivors.index(matched)] = replacement
    return survivors
