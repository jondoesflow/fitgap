# Development guide

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -e ".[dev]"
pytest
```

Python 3.12+ required. Runtime dependencies are deliberately minimal: `typer`, `pydantic`, `python-docx`, `openpyxl`, `anthropic`, `requests`, `pyyaml`, `rapidfuzz`. Dev-only: `pytest`. **Do not add dependencies without discussion.**

## Architecture

The pipeline is a series of independent stages that all enrich one on-disk artifact — the workspace JSON (`fitgap_workspace/requirements.json`, schema in [`src/fitgap/models.py`](../src/fitgap/models.py)):

```
ingest  ->  requirements[] created           (parsers + dedupe, no LLM)
classify -> requirement.classification set    (LLM, batched, structured tool output)
verify   -> requirement.verification set      (LLM search + deterministic URL guard)
report   -> reads everything, writes .xlsx    (no LLM)
```

Because stages are resumable (`classify`/`verify` skip rows that already have results unless `--force`), a consultant can re-run any stage cheaply after editing the workspace or config.

Key boundaries:

- **`ingest/`** — one module per source. Parsers return `ParsedRequirement` (no IDs); `normalise/canonical.py` assigns `REQ-###` IDs. If you add a source, return `ParsedRequirement`s with a traceable `source_ref` and the correct `source_reliability`.
- **`redact/`** — called at every point where text leaves the machine (classification batches, verification prompts, transcript chunks). If you add an LLM call, you must anonymise inputs and append the events to `workspace.redaction_log`.
- **`classify/` and `ingest/transcript.py`** — force a tool call (`tool_choice`) whose JSON schema mirrors the pydantic models, so outputs are structurally validated. Invalid enum values raise `ClassificationError` rather than silently degrading.
- **`verify/learn_verifier.py`** — the citation guard (`check_learn_url`) is deliberately plain `requests` code. Never weaken it: host must be exactly `learn.microsoft.com`, status 200, checked at analysis time. All failure paths produce `UNCONFIRMED`, never an exception that aborts the run, and never a stored bad URL.
- **`report/excel.py`** — pure function of (workspace, config) → workbook. The "UNCONFIRMED — validate manually" display string is the single source of truth (`UNCONFIRMED_DISPLAY`) used by both the citation column and the conditional-formatting rule.

## Testing strategy

All tests run **offline** — the Anthropic client is replaced by `tests/fakes.py::FakeAnthropic`, whose `responder(kwargs) -> content blocks` contract makes it trivial to script model behaviour per test (including tool-refusal, hallucinated IDs, and garbage output). The same fake serves `client.messages` and `client.beta.messages` (MCP calls).

- Parser fixtures (`.docx`, `.xlsx`, `.vtt`) are **generated programmatically** in `tests/conftest.py` — no binaries in the repo.
- `tests/test_verify.py::test_fake_feature_citation_is_downgraded` is the guard-rail test required by the spec: a confirmed-sounding answer with a citation that fails the liveness check must come back UNCONFIRMED with no stored URL.
- `tests/test_live_smoke.py` contains real-API smoke tests (one classification; one real-vs-fake-feature verification). They **skip automatically** when `ANTHROPIC_API_KEY` is unset, so CI stays offline.

Run everything:

```bash
pytest -q
```

## The golden set

`golden/golden_set.yaml` holds 25 hand-verified classifications with optional `acceptable` alternatives where competent architects could defensibly disagree. Rules:

- Entries are curated by hand and verified by a human against current Microsoft documentation — never generated.
- Don't tune prompts against individual entries. If an entry is genuinely wrong (Microsoft shipped/retired something), fix the entry with a dated note.
- Keep the category spread: easy OOB, configuration, extension, deceptive-sounds-OOB, deprecated traps, genuine gaps, unclear.

`fitgap eval` gates: ≥ 90% classification accuracy; 100% of asserted citations must re-resolve. `fitgap eval --no-verify` scores classification only.

## Conventions

- `src/` layout; import as `fitgap.*`.
- pydantic v2 models everywhere data crosses a boundary; enums for every closed vocabulary (the em-dash category names are canonical — they match the methodology and the register).
- CLI messages: successes plain, warnings yellow, failures red + non-zero exit code. Fail loudly; never silently degrade a guarantee.
- Windows is a first-class platform (the primary user runs Windows) — mind path handling and avoid POSIX-only assumptions.
