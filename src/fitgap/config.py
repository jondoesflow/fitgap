"""Load and persist fitgap.yaml — the single config file for the tool."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class AdoConfig(BaseModel):
    organization: str = ""
    project: str = ""
    pat_env_var: str = "AZURE_DEVOPS_PAT"
    area_path: str | None = None
    # Optional full WIQL override; default query is built from area_path.
    wiql: str | None = None


class VerifyConfig(BaseModel):
    # "mcp" = Microsoft Learn MCP connector; "web_search" = API web-search tool
    # restricted to learn.microsoft.com.
    mode: Literal["mcp", "web_search"] = "mcp"

    # Verification dominates run cost: the model researches Learn per claim and
    # the server-side tool loop re-sends everything it has fetched on every
    # round. The knobs below trade cost against confirmation rate — measure
    # with `fitgap benchmark-verify` before changing them.

    #: Model used for verification. None = use the top-level `model`.
    #: Verification is a constrained search-and-cite task, so a cheaper model
    #: may do just as well; the URL liveness guard still rejects bad citations.
    model: str | None = None

    #: MCP only. Expose `microsoft_docs_search` (compact excerpts) but not
    #: `microsoft_docs_fetch` (whole pages, the main driver of input tokens).
    #: Much cheaper, but excerpts may not show PREVIEW/DEPRECATED banners.
    search_only: bool = False

    #: Cache the system prompt and tool definitions across calls and across
    #: each round of the server-side tool loop. No behavioural change.
    cache_prompt: bool = True

    #: web_search mode only: how many searches the model may run per claim.
    max_searches: int = Field(default=5, ge=1, le=10)


class OutputConfig(BaseModel):
    # "register" is the YAML key; the attribute is renamed because it would
    # shadow pydantic's BaseModel.register.
    model_config = ConfigDict(populate_by_name=True)

    workspace: str = "fitgap_workspace/requirements.json"
    register_path: str = Field(
        default="fitgap_workspace/fitgap_register.xlsx", alias="register"
    )


class XlsxMapping(BaseModel):
    """Column mapping for one Excel source file, keyed by header names."""

    sheet: str | None = None  # None = active sheet
    text: str                 # header of the requirement-text column (required)
    id: str | None = None
    priority: str | None = None
    functional_area: str | None = None


class Config(BaseModel):
    model: str = "claude-sonnet-4-6"
    redact_file: str = "redact.yaml"
    dedupe_threshold: int = Field(default=90, ge=50, le=100)
    ado: AdoConfig = Field(default_factory=AdoConfig)
    verify: VerifyConfig = Field(default_factory=VerifyConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    # Saved interactive column mappings, keyed by xlsx file name.
    xlsx_mappings: dict[str, XlsxMapping] = Field(default_factory=dict)


def load_config(path: Path) -> Config:
    """Load fitgap.yaml; a missing file yields all defaults."""
    if not path.exists():
        return Config()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Config.model_validate(raw)


def save_config(config: Config, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(mode="json", exclude_none=True, by_alias=True)
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
