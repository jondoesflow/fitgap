# Quickstart — command cheat sheet

Every command runs from the project directory (where `fitgap.yaml` lives). Add `-c path/to/fitgap.yaml` to any command to point elsewhere.

## Setup (once)

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp fitgap.example.yaml fitgap.yaml
cp redact.example.yaml redact.yaml
```

Windows shortcut: `.\start-fitgap.ps1` does all of the above.

## Choose your LLM provider (Anthropic is the default)

```bash
fitgap model list                              # all providers + example models, active one marked *
fitgap model use anthropic/claude-sonnet-4-6   # the default — nothing to do if this suits
fitgap model use openai/gpt-5.1                # or switch provider entirely
fitgap model use deepseek/deepseek-chat
fitgap model key deepseek                      # hidden prompt -> OS keyring (.env fallback in CI)
fitgap model status                            # active provider/model + where the key was found
```

Key lookup order at runtime (first hit wins): **environment variable** (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, `MOONSHOT_API_KEY`, `GEMINI_API_KEY`, `MISTRAL_API_KEY`, `XAI_API_KEY`) → **OS keyring** → **`.env` file**. Keys are never stored in `fitgap.yaml`.

The provider choice is a per-engagement compliance decision — every run prints which provider receives the (redacted) text.

## Run the pipeline

```bash
fitgap run brd.docx backlog.xlsx -t workshop.vtt   # full pipeline: ingest -> classify -> verify -> report
fitgap run brd.docx --skip-verify                  # skip Learn verification (claims stay UNCONFIRMED)
```

Or stage by stage (each is resumable and re-runnable):

```bash
fitgap ingest brd.docx backlog.xlsx ado -t workshop.vtt   # -> fitgap_workspace/requirements.json
fitgap classify                                            # taxonomy + confidence (uses the LLM)
fitgap verify                                              # live Learn citations (Anthropic only)
fitgap report                                              # -> fitgap_workspace/fitgap_register.xlsx
```

Notes:

- **No API key needed** for `fitgap ingest` (without `--transcript`) and `fitgap report` — fully offline.
- **`fitgap verify` requires the Anthropic provider** (live Microsoft Learn search uses Anthropic-only API features). On another provider, use `--skip-verify` or switch back for that stage:

  ```bash
  fitgap model use anthropic/claude-sonnet-4-6 && fitgap verify
  ```

## Evaluate before trusting a new model

```bash
fitgap eval              # golden set: classification accuracy + live citation re-check
fitgap eval --no-verify  # classification accuracy only (faster/cheaper)
fitgap benchmark-verify  # compare verify cost knobs on cost AND fidelity (workspace untouched)
```

Gates: ≥ 90% classification accuracy, 100% of asserted citations resolve live. Run this after switching provider/model — the gates are provider-independent.

## Everything else

```bash
fitgap version
fitgap --help            # each subcommand also has --help
```

Full references: [configuration.md](configuration.md) (fitgap.yaml, redact.yaml, env vars), [methodology.md](methodology.md), [development.md](development.md).
