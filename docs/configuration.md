# Configuration reference

`fitgap` reads a single config file, `fitgap.yaml`, from the working directory (override with `--config/-c` on every command). A missing file means all defaults. Copy [`fitgap.example.yaml`](../fitgap.example.yaml) to get started.

Both `fitgap.yaml` and `redact.yaml` are **gitignored by default** — they carry engagement-specific data that should never be committed.

## fitgap.yaml

```yaml
llm:
  provider: anthropic        # anthropic | openai | deepseek | kimi | gemini | mistral | xai
  model: claude-sonnet-4-6
redact_file: redact.yaml
dedupe_threshold: 90

ado:
  organization: contoso
  project: CRM-Replacement
  pat_env_var: AZURE_DEVOPS_PAT
  area_path: CRM-Replacement\Requirements   # optional
  # wiql: SELECT [System.Id] FROM WorkItems WHERE ...   # optional full override

verify:
  mode: mcp          # mcp | web_search

output:
  workspace: fitgap_workspace/requirements.json
  register: fitgap_workspace/fitgap_register.xlsx

xlsx_mappings:
  backlog.xlsx:
    sheet: null       # null = active sheet
    text: Requirement Description
    id: Ref
    priority: Priority (MoSCoW)
    functional_area: Workstream
```

### Fields

| Key | Default | Notes |
|---|---|---|
| `llm.provider` | `anthropic` | Active LLM provider — a per-engagement compliance decision. Set with `fitgap model use <provider>/<model>`; validated against the provider registry. |
| `llm.model` | `claude-sonnet-4-6` | Model id at the active provider, used for classify / verify / transcript extraction. Unknown model strings are accepted with a warning. |
| `model` *(legacy)* | — | Configs written before multi-provider support used a top-level `model:` key; it still loads (as `anthropic` + that model) and is migrated to the `llm:` block on the next write. |
| `redact_file` | `redact.yaml` | Path to the anonymisation rules. Missing file = only default email redaction. |
| `dedupe_threshold` | `90` | rapidfuzz `token_sort_ratio` (50–100) above which two requirements merge. Lower = more aggressive merging. |
| `ado.organization` / `ado.project` | empty | Required for `fitgap ingest ado`. |
| `ado.pat_env_var` | `AZURE_DEVOPS_PAT` | **Name** of the environment variable holding your PAT. The PAT value never lives in config. Scope needed: *Work Items (Read)*. |
| `ado.area_path` | none | Scopes the default WIQL with `[System.AreaPath] UNDER '...'`. |
| `ado.wiql` | none | Full WIQL override; when set, `area_path` is ignored. Default query pulls Epics / Features / User Stories not in state *Removed*. |
| `verify.mode` | `mcp` | `mcp` uses the Microsoft Learn MCP server attached to the API call. `web_search` uses the Anthropic web-search tool restricted with `allowed_domains: ["learn.microsoft.com"]` — use it if the MCP route proves unreliable. |
| `output.workspace` | `fitgap_workspace/requirements.json` | The canonical pipeline artifact. |
| `output.register` | `fitgap_workspace/fitgap_register.xlsx` | The Excel deliverable. |
| `xlsx_mappings` | `{}` | Saved column mappings keyed by file name. Written automatically the first time a file is ingested (auto-detected or prompted). Edit by hand if headers change. |

## redact.yaml

Anonymisation rules applied to requirement/transcript text **before any external API call**. Every substitution is recorded in the workspace redaction log and summarised on the register's Assumptions & Limitations sheet.

```yaml
client_names:        # -> [CLIENT]
  - Contoso
  - Contoso Ltd
people:              # -> [PERSON]
  - Jane Doe
codenames:           # -> [PROJECT]
  - Project Phoenix
redact_emails: true  # -> [EMAIL]  (default: true)
custom:              # arbitrary regex rules
  - pattern: "\\bCON-\\d{4}\\b"
    replacement: "[TICKET]"
```

Matching is case-insensitive, whole-word, and tolerant of extra whitespace inside names. Emails are redacted **before** name rules so a client name inside an email address can never leave a partially redacted address behind. The original (unredacted) text stays in your local workspace JSON — only the redacted form is sent.

## The `fitgap model` command group

```bash
fitgap model list                 # providers + example model strings, active one marked
fitgap model use openai/gpt-5.1   # switch provider/model (written to fitgap.yaml under llm:)
fitgap model key openai           # hidden prompt -> OS keyring (or gitignored 0600 .env fallback)
fitgap model status               # active provider/model + where the key was found (masked tail)
```

Writes to `fitgap.yaml` preserve existing content and comments (ruamel.yaml round-trip). `fitgap verify` requires the Anthropic provider (its live Learn search uses Anthropic-only API features); on other providers use `--skip-verify` or switch back for the verify stage.

## Environment variables

| Variable | Required for | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | `classify`, `verify`, `eval`, `ingest --transcript` when `llm.provider: anthropic` (default) | Standard Anthropic SDK variable. |
| `OPENAI_API_KEY` / `DEEPSEEK_API_KEY` / `MOONSHOT_API_KEY` / `GEMINI_API_KEY` / `MISTRAL_API_KEY` / `XAI_API_KEY` | The same stages, when the matching provider is active | Only the active provider's key is needed. |
| `AZURE_DEVOPS_PAT` (or the name set in `ado.pat_env_var`) | `ingest ado` | PAT with Work Items (Read) scope. |

Key resolution precedence (first hit wins; `fitgap model status` reports the true source): **real environment variable → OS keyring → `.env` file** next to `fitgap.yaml`. The `.env` fallback is written by `fitgap model key` only when no OS keyring is available, with `0600` permissions and a `.gitignore` entry. API keys are never stored in `fitgap.yaml` and never appear in logs or output (masked to the last 4 characters).

## CLI flags worth knowing

| Flag | Command | Purpose |
|---|---|---|
| `--no-input` | `ingest`, `run` | Never prompt; fail loudly if an xlsx mapping can't be auto-detected. For CI/scripts. |
| `--transcript / -t` | `ingest`, `run` | Repeatable; marks the file as a workshop transcript (LLM extraction, `inferred` reliability). |
| `--force` | `classify`, `verify` | Redo rows that already have results (default is resume-friendly skip). |
| `--skip-verify` | `run` | Produce a register without Learn verification — every claim reads UNCONFIRMED. |
| `--no-verify` | `eval` | Score classification only (cheaper); skips the citation-integrity gate. |
| `--workspace / -w` | `classify`, `verify`, `report` | Point at a non-default workspace JSON. |
