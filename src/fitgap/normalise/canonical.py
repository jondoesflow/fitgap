"""Assign canonical IDs and build the workspace artifact."""

from __future__ import annotations

from fitgap import __version__
from fitgap.models import ParsedRequirement, Requirement, Workspace


def build_workspace(parsed: list[ParsedRequirement]) -> Workspace:
    requirements = [
        Requirement(id=f"REQ-{index:03d}", **item.model_dump())
        for index, item in enumerate(parsed, start=1)
    ]
    return Workspace(tool_version=__version__, requirements=requirements)
