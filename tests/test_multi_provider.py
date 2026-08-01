"""Cross-provider guarantees: redaction-before-send, verify gating, offline."""

import json

import pytest
from fakes import FakeOpenAI, chat_tool_call_message
from typer.testing import CliRunner

from fitgap.cli import app
from fitgap.classify import Classifier
from fitgap.config import Config
from fitgap.llm.openai_compat import OpenAICompatClient
from fitgap.llm.registry import PROVIDERS
from fitgap.redact import RedactionRules
from test_classify import classification_entry, make_workspace

runner = CliRunner()


@pytest.fixture
def no_provider_keys(monkeypatch):
    for spec in PROVIDERS.values():
        monkeypatch.delenv(spec.env_var, raising=False)


def echo_chat_responder(kwargs):
    payload = json.loads(kwargs["messages"][-1]["content"].split(":\n\n", 1)[1])
    return chat_tool_call_message(
        {
            "classifications": [
                classification_entry(item["requirement_id"]) for item in payload
            ]
        }
    )


@pytest.mark.parametrize(
    "provider", [p for p in PROVIDERS if PROVIDERS[p].sdk == "openai-compat"]
)
def test_redaction_happens_before_any_provider_call(provider):
    """The compliance guarantee: anonymisation is provider-independent —
    client names are redacted from the outgoing request for every provider."""
    spec = PROVIDERS[provider]
    fake = FakeOpenAI(echo_chat_responder)
    config = Config.model_validate(
        {"llm": {"provider": provider, "model": spec.default_model}}
    )
    workspace = make_workspace(2)
    Classifier(
        config,
        RedactionRules(client_names=["Contoso"]),
        OpenAICompatClient(spec, spec.default_model, sdk_client=fake),
    ).classify_workspace(workspace)
    assert fake.calls
    for call in fake.calls:
        sent = json.dumps(call["messages"])
        assert "Contoso" not in sent
        assert "[CLIENT]" in sent
    assert workspace.redaction_log


def test_verify_refuses_non_anthropic_provider(tmp_path, no_provider_keys):
    config_path = tmp_path / "fitgap.yaml"
    config_path.write_text(
        "llm:\n  provider: openai\n  model: gpt-5.1\n", encoding="utf-8"
    )
    result = runner.invoke(app, ["verify", "-c", str(config_path)])
    assert result.exit_code == 1
    assert "Anthropic-only" in result.output
    assert "--skip-verify" in result.output


def test_run_without_skip_verify_fails_fast_on_non_anthropic(
    tmp_path, no_provider_keys, sample_docx
):
    config_path = tmp_path / "fitgap.yaml"
    config_path.write_text(
        "llm:\n  provider: deepseek\n  model: deepseek-chat\n", encoding="utf-8"
    )
    result = runner.invoke(app, ["run", str(sample_docx), "-c", str(config_path)])
    assert result.exit_code == 1
    assert "--skip-verify" in result.output


def test_offline_ingest_and_report_need_no_keys(
    tmp_path, no_provider_keys, sample_docx, monkeypatch
):
    """The fully offline path keeps working with no key present anywhere."""
    import sys

    class NoKeyring:
        def get_password(self, *a):
            raise RuntimeError("no backend")

        def set_password(self, *a):
            raise RuntimeError("no backend")

    monkeypatch.setitem(sys.modules, "keyring", NoKeyring())

    config_path = tmp_path / "fitgap.yaml"
    workspace = tmp_path / "ws.json"
    register = tmp_path / "register.xlsx"
    result = runner.invoke(
        app,
        ["ingest", str(sample_docx), "-c", str(config_path), "-o", str(workspace), "--no-input"],
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(
        app,
        ["report", "-c", str(config_path), "-w", str(workspace), "-o", str(register)],
    )
    assert result.exit_code == 0, result.output
    assert register.exists()


def test_run_summary_names_the_active_provider(tmp_path):
    """Compliance: every LLM run summary states which provider received data."""
    from fitgap.usage import UsageTracker
    from fitgap.cli import _print_cost
    from types import SimpleNamespace

    config = Config.model_validate(
        {"llm": {"provider": "kimi", "model": "kimi-latest"}}
    )
    tracker = UsageTracker()
    tracker.record(
        "classify",
        SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=10, output_tokens=5,
                cache_read_input_tokens=0, cache_creation_input_tokens=0,
            )
        ),
    )
    import typer
    from typer.testing import CliRunner as _CR

    capture = typer.Typer()

    @capture.command()
    def show():
        _print_cost(tracker, config, "LLM usage (classify):")

    result = _CR().invoke(capture, [])
    assert "Kimi (Moonshot AI)" in result.output
    assert "kimi-latest" in result.output
