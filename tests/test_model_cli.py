"""The `fitgap model` command group and config YAML round-tripping."""

from typer.testing import CliRunner

from fitgap.cli import app
from fitgap.config import Config, XlsxMapping, load_config, save_config

runner = CliRunner()

COMMENTED_YAML = """\
# Engagement config for Contoso CRM replacement — do not commit.
model: claude-sonnet-4-6

# Anonymisation rules applied before any text is sent to an external API.
redact_file: redact.yaml

dedupe_threshold: 85  # tuned down for this client's noisy backlog

ado:
  organization: contoso   # keep in sync with the PAT scope
  project: CRM-Replacement

custom_client_note: keep me  # unknown keys must survive writes
"""


def test_model_use_updates_llm_and_preserves_comments(tmp_path):
    config_path = tmp_path / "fitgap.yaml"
    config_path.write_text(COMMENTED_YAML, encoding="utf-8")

    result = runner.invoke(
        app, ["model", "use", "deepseek/deepseek-chat", "-c", str(config_path)]
    )
    assert result.exit_code == 0, result.output
    assert "deepseek/deepseek-chat" in result.output
    assert "Compliance note" in result.output

    text = config_path.read_text(encoding="utf-8")
    # Comments and unknown keys survive; the legacy top-level model key is
    # superseded by the llm block.
    assert "# Engagement config for Contoso CRM replacement" in text
    assert "tuned down for this client's noisy backlog" in text
    assert "keep in sync with the PAT scope" in text
    assert "custom_client_note: keep me" in text
    assert not any(line.startswith("model:") for line in text.splitlines())

    config = load_config(config_path)
    assert config.llm.provider == "deepseek"
    assert config.llm.model == "deepseek-chat"
    assert config.dedupe_threshold == 85
    assert config.ado.organization == "contoso"


def test_model_use_unknown_model_warns_but_proceeds(tmp_path):
    config_path = tmp_path / "fitgap.yaml"
    result = runner.invoke(
        app, ["model", "use", "openai/gpt-99-ultra", "-c", str(config_path)]
    )
    assert result.exit_code == 0, result.output
    assert "not in the known-models list" in result.output
    assert load_config(config_path).llm.model == "gpt-99-ultra"


def test_model_use_unknown_provider_fails(tmp_path):
    config_path = tmp_path / "fitgap.yaml"
    result = runner.invoke(
        app, ["model", "use", "closedai/gpt-5", "-c", str(config_path)]
    )
    assert result.exit_code == 1
    assert "Unknown provider" in result.output
    assert not config_path.exists()


def test_model_use_requires_provider_slash_model(tmp_path):
    result = runner.invoke(
        app, ["model", "use", "claude-sonnet-4-6", "-c", str(tmp_path / "f.yaml")]
    )
    assert result.exit_code == 1
    assert "provider" in result.output


def test_model_list_marks_active(tmp_path):
    config_path = tmp_path / "fitgap.yaml"
    config_path.write_text(
        "llm:\n  provider: mistral\n  model: mistral-large-latest\n", encoding="utf-8"
    )
    result = runner.invoke(app, ["model", "list", "-c", str(config_path)])
    assert result.exit_code == 0, result.output
    assert "Active: mistral/mistral-large-latest" in result.output
    assert "* mistral" in result.output
    for provider in ("anthropic", "openai", "deepseek", "kimi", "gemini", "xai"):
        assert f"\n  {provider}" in result.output or f" {provider} " in result.output


def test_legacy_top_level_model_key_still_loads():
    config = Config.model_validate({"model": "claude-opus-4-6"})
    assert config.llm.provider == "anthropic"
    assert config.llm.model == "claude-opus-4-6"
    assert config.model == "claude-opus-4-6"


def test_save_config_roundtrip_preserves_comments_and_llm(tmp_path):
    config_path = tmp_path / "fitgap.yaml"
    config_path.write_text(COMMENTED_YAML, encoding="utf-8")
    config = load_config(config_path)
    config.xlsx_mappings["backlog.xlsx"] = XlsxMapping(text="Requirement")
    save_config(config, config_path)

    text = config_path.read_text(encoding="utf-8")
    assert "# Engagement config for Contoso CRM replacement" in text
    assert "custom_client_note: keep me" in text
    reloaded = load_config(config_path)
    assert reloaded.llm.provider == "anthropic"
    assert reloaded.llm.model == "claude-sonnet-4-6"
    assert reloaded.xlsx_mappings["backlog.xlsx"].text == "Requirement"
    assert reloaded.dedupe_threshold == 85
