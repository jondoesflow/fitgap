"""Load and persist fitgap.yaml — the single config file for the tool.

Writes go through ruamel.yaml round-tripping so user comments and unknown
keys in fitgap.yaml survive every save. API keys are never written here —
see fitgap.llm.keys.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class LLMConfig(BaseModel):
    """Active provider/model — a per-engagement decision (data goes to this
    provider after redaction). Set with ``fitgap model use provider/model``."""

    provider: str = "anthropic"
    model: str = "claude-sonnet-4-6"

    #: Cache the fixed tools+system prefix of the classify and transcript
    #: extraction calls, so it is billed at cache-read rates on every call
    #: after the first. Caching cannot change what the model returns, so this
    #: is on by default. Note the prefix must exceed the model's minimum
    #: cacheable size or the API silently does not cache it — the run summary
    #: reports whether any cache reads actually happened. Verification has its
    #: own switch, ``verify.cache_prompt``.
    cache_prompt: bool = True


class Config(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    redact_file: str = "redact.yaml"
    dedupe_threshold: int = Field(default=90, ge=50, le=100)
    ado: AdoConfig = Field(default_factory=AdoConfig)
    verify: VerifyConfig = Field(default_factory=VerifyConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    # Saved interactive column mappings, keyed by xlsx file name.
    xlsx_mappings: dict[str, XlsxMapping] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_model_key(cls, data):
        """Configs written before multi-provider support have a top-level
        ``model:`` key; map it onto llm.model (provider anthropic)."""
        if isinstance(data, dict) and "model" in data:
            data = dict(data)
            legacy = data.pop("model")
            data.setdefault("llm", {"provider": "anthropic", "model": legacy})
        return data

    @property
    def model(self) -> str:
        """The active model string (kept for the pipeline's existing callers)."""
        return self.llm.model


def _ruamel():
    from ruamel.yaml import YAML

    rt = YAML()
    rt.preserve_quotes = True
    rt.width = 4096
    return rt


def _deep_update(doc, data: dict) -> None:
    """Set keys from ``data`` into the ruamel document, recursing into dicts
    so comments attached to untouched keys survive. Keys present in the file
    but unknown to fitgap are left alone."""
    for key, value in data.items():
        if isinstance(value, dict) and isinstance(doc.get(key), dict):
            _deep_update(doc[key], value)
        else:
            doc[key] = value


def load_config(path: Path) -> Config:
    """Load fitgap.yaml; a missing file yields all defaults."""
    if not path.exists():
        return Config()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Config.model_validate(raw)


def save_config(config: Config, path: Path) -> None:
    rt = _ruamel()
    doc = rt.load(path.read_text(encoding="utf-8")) if path.exists() else None
    if doc is None:
        from ruamel.yaml.comments import CommentedMap

        doc = CommentedMap()
    doc.pop("model", None)  # legacy key, superseded by the llm block
    _deep_update(doc, config.model_dump(mode="json", exclude_none=True, by_alias=True))
    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO()
    rt.dump(doc, buffer)
    path.write_text(buffer.getvalue(), encoding="utf-8")


def set_active_llm(path: Path, provider: str, model: str) -> None:
    """Update only the ``llm:`` block of fitgap.yaml, preserving everything
    else in the file (content, ordering, comments) untouched."""
    rt = _ruamel()
    doc = rt.load(path.read_text(encoding="utf-8")) if path.exists() else None
    if doc is None:
        from ruamel.yaml.comments import CommentedMap

        doc = CommentedMap()
    doc.pop("model", None)  # legacy key, superseded by the llm block
    _deep_update(doc, {"llm": {"provider": provider, "model": model}})
    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO()
    rt.dump(doc, buffer)
    path.write_text(buffer.getvalue(), encoding="utf-8")
