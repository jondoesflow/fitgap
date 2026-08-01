# fitgap

A command-line assistant that helps **Dynamics 365 Customer Engagement / Power Platform consultants** perform fit-gap analysis faster and more defensibly.

`fitgap` ingests raw requirements from Word documents, Excel backlogs, Azure DevOps, and workshop transcripts; classifies each requirement against D365 CE and Power Platform capabilities using Microsoft's own Solution Architect fit-gap methodology; **verifies every capability claim against live Microsoft Learn documentation**; and produces an Excel fit-gap register a consultant can review, adjust, and present to a client.

> **This is an augmentation tool.** The consultant always makes the final call. The tool's job is to do the first 80% of the legwork with full transparency about what it is and isn't sure of.

## Design principles

| Principle | What it means in practice |
|---|---|
| **No unverified capability claims** | Every *Fit — OOB* / *Fit — Configuration* / *Extend* row must carry a live `learn.microsoft.com` citation retrieved at analysis time — never from model memory. The cited URL is HTTP-checked by plain code before it is written to the register. Anything that fails is downgraded to **UNCONFIRMED — validate manually** and highlighted amber. |
| **Human-in-the-loop** | The register is a draft: every row has a blank *Consultant review* column and a *Confidence* rating. Low-confidence rows are bold red; UNCONFIRMED rows amber; *Gap — Custom* rows highlighted. |
| **Client data protection** | A configurable anonymisation pass (`redact.yaml`) strips client names, people, emails, and project codenames **before any text is sent to an external API**. Every substitution is logged and summarised in the register. |
| **Deterministic where possible** | Parsing, deduplication, and Excel generation are plain code. Only classification, extraction from transcripts, and verification use the model. |

## Requirements

- Python **3.12+**
- An **Anthropic API key** (for the `classify`, `verify`, `eval`, and transcript-extraction stages)
- Optionally: an **Azure DevOps PAT** with *Work Items (Read)* scope, if pulling from ADO

## Installation

```bash
git clone <this-repo>
cd fitgap
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate
pip install -e ".[dev]"
```

Set your API key:

```bash
# Windows (PowerShell)
$env:ANTHROPIC_API_KEY = "sk-ant-..."
# macOS/Linux
export ANTHROPIC_API_KEY="sk-ant-..."
```

Then copy the example config files and edit them for your engagement:

```bash
cp fitgap.example.yaml fitgap.yaml
cp redact.example.yaml redact.yaml
```

Both real files are gitignored — client-specific settings never leave your machine.

## Quickstart (Windows)

The repo ships a session-setup script that does all of the below in one go — creates the venv on first run, activates it, creates `fitgap.yaml`/`redact.yaml` from the examples, and checks (or prompts for) your API key:

```bash
.\start-fitgap.ps1
```

Then just:

```bash
fitgap run -t Transcript.txt
```

(If PowerShell blocks the script, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.)

## Quickstart (manual)

Generate sample inputs and run the full pipeline:

```bash
python examples/generate_sample_inputs.py sample_data
fitgap run sample_data/contoso_brd.docx sample_data/contoso_backlog.xlsx
```

This produces `fitgap_workspace/fitgap_register.xlsx` — open it in Excel.

Or run the stages individually (each stage is resumable and re-runnable):

```bash
fitgap ingest brd.docx backlog.xlsx ado -t workshop.vtt   # -> requirements.json
fitgap classify                                            # taxonomy + confidence
fitgap verify                                              # live Learn citations
fitgap report                                              # -> fitgap_register.xlsx
```

## Pipeline stages

### 1. Ingest — `fitgap ingest <sources...>`

| Source | How |
|---|---|
| **Word (`.docx`)** | Extracts requirement statements from tables (with ID/priority/area columns when present), numbered lists, and body text under any heading containing "requirement". |
| **Excel (`.xlsx`)** | Column mapping resolved from: saved mapping in `fitgap.yaml` → header auto-detection → interactive prompt. First-run mappings are saved automatically. Use `--no-input` in scripts. |
| **Azure DevOps** | Pass the literal `ado` as a source. Pulls Epics/Features/User Stories via WIQL (configurable), PAT read from the env var named in config. |
| **Transcripts (`.vtt`/`.txt`/`.docx`)** | Pass with `--transcript/-t`. Deterministic cue parsing (timestamp/speaker), then LLM extraction of *implied* requirements — each tagged with the timestamp and speaker that implies it, and marked `source_reliability: inferred`. |

Near-duplicate requirements are merged deterministically (rapidfuzz, threshold configurable); a *stated* requirement always survives over a transcript-*inferred* one, and merged source refs are preserved.

Output: `fitgap_workspace/requirements.json` — the canonical workspace every later stage enriches in place.

### 2. Classify — `fitgap classify`

One structured, batched call per ~10 requirements to the model configured in `fitgap.yaml`. Returns, per requirement: category, a 2–3 sentence proposed approach, the **specific product/feature relied on**, effort t-shirt size with assumptions, and self-assessed confidence. The system prompt makes explicit that *"I'm not certain this feature exists"* is a valid, desirable answer.

Classification taxonomy (in order of preference — lowest-effort viable option first):

| Category | Meaning |
|---|---|
| **Fit — OOB** | Met by standard D365 CE / Power Platform features with no changes |
| **Fit — Configuration** | Met via supported configuration (settings, business rules, views, security roles, low-code) |
| **Extend — Power Platform** | Power Automate, Power Fx, Power Pages, canvas apps, PCF, Dataverse plugins within supported extensibility |
| **Gap — ISV** | Best met by a known AppSource ISV solution (named) |
| **Gap — Custom** | Requires pro-code custom development |
| **Out of scope / unclear** | Not solvable as written; needs clarification |

### 3. Verify — `fitgap verify`

For every row classified *Fit — OOB*, *Fit — Configuration*, or *Extend*: the model searches **live Microsoft Learn documentation** via the [Microsoft Learn MCP server](https://learn.microsoft.com/api/mcp) (or the API web-search tool restricted to `learn.microsoft.com` — set `verify.mode: web_search`).

Defence in depth, in plain code, not prompts:

- The returned citation URL is **HTTP-fetched at analysis time**; it must be on `learn.microsoft.com` and return 200 after redirects.
- Any failure — fabricated URL, dead page, off-domain redirect, model uncertainty, API error — downgrades the row to **UNCONFIRMED — validate manually**. A fabricated citation can never reach the register.
- Learn pages indicating **preview** or **deprecated** status set warning flags shown in the register.

### 4. Report — `fitgap report`

Generates the Excel register (`openpyxl`):

- **Register** sheet: Req ID | Source | Requirement | Functional area | Classification | Proposed approach | Feature relied on | Learn citation (live hyperlink) | Preview/deprecated | Confidence | Effort | Assumptions | **Consultant review (blank)** | Notes. Frozen header, filters on.
- **Summary** sheet: counts by classification and functional area, % of claims verified, effort profile — with charts.
- **Assumptions & Limitations** sheet: every assumption the model stated, the redaction log summary, and generation metadata.
- Conditional formatting: UNCONFIRMED rows **amber**, *Gap — Custom* rows **highlighted**, Low-confidence rows **bold red**.

### Evaluation — `fitgap eval`

Runs the real pipeline against `golden/golden_set.yaml` — 25 hand-verified requirements spanning easy OOB cases, deceptive ones that *sound* OOB but aren't, a deprecated-feature trap, genuine gaps, and unclear requirements.

**Release gates** (the tool is not ready for real projects until both pass):

1. Classification accuracy **≥ 90%** (expected or listed-acceptable category)
2. **100%** of asserted citations resolve live — *never asserts a citation that doesn't resolve*

```bash
fitgap eval              # full: classification + verification + citation re-check
fitgap eval --no-verify  # classification accuracy only (faster/cheaper)
```

## Configuration

Single file: `fitgap.yaml` (see [`fitgap.example.yaml`](fitgap.example.yaml) and [docs/configuration.md](docs/configuration.md) for the full reference). Redaction rules: `redact.yaml` (see [`redact.example.yaml`](redact.example.yaml)).

| Env var | Used by |
|---|---|
| `ANTHROPIC_API_KEY` | classify / verify / eval / transcript extraction |
| `AZURE_DEVOPS_PAT` (name configurable) | `fitgap ingest ado` |

## Project structure

```
src/fitgap/
├── cli.py             # typer CLI: ingest / classify / verify / report / run / eval
├── config.py          # fitgap.yaml loader (pydantic)
├── models.py          # canonical Requirement schema + workspace JSON artifact
├── evaluate.py        # golden-set scoring + release gates
├── ingest/            # one parser per source: docx, xlsx, ado, transcript
├── normalise/         # rapidfuzz dedupe + canonical ID assignment
├── redact/            # anonymisation pass + audit log
├── classify/          # batched structured classification (Anthropic API)
├── verify/            # Learn MCP verification + HTTP citation guard
└── report/            # openpyxl register generation
tests/                 # 77+ tests, all offline (API mocked); live smoke tests skip without a key
golden/golden_set.yaml # 25 hand-verified evaluation cases
```

## Documentation

- [docs/configuration.md](docs/configuration.md) — full `fitgap.yaml` / `redact.yaml` / env var reference
- [docs/methodology.md](docs/methodology.md) — the fit-gap methodology, taxonomy, and verification policy
- [docs/development.md](docs/development.md) — architecture, testing strategy, and contribution guide

## Known limitations

- Transcript-derived requirements are inferred from conversation; they are flagged and must be confirmed with the client.
- Effort sizes are indicative t-shirt estimates dependent on stated assumptions.
- Verification confirms that a Learn page documents the capability — it does not confirm licensing, region availability, or your tenant's configuration.
- The register is a draft. Read the Assumptions & Limitations sheet before presenting it.
