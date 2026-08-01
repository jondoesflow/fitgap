from typer.testing import CliRunner

from fitgap.cli import app
from fitgap.models import Requirement, SourceType, Workspace

runner = CliRunner()


def test_classify_without_api_key_gives_guidance(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    ws_path = tmp_path / "ws.json"
    Workspace(
        tool_version="test",
        requirements=[
            Requirement(
                id="REQ-001", text="Something long enough.",
                source=SourceType.DOCX, source_ref="x#1",
            )
        ],
    ).save(ws_path)
    result = runner.invoke(app, ["classify", "--workspace", str(ws_path)])
    assert result.exit_code == 1
    assert "ANTHROPIC_API_KEY" in result.output
    assert "console.anthropic.com" in result.output


def test_verify_without_api_key_gives_guidance(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = runner.invoke(app, ["verify", "--workspace", str(tmp_path / "none.json")])
    assert result.exit_code == 1
    assert "ANTHROPIC_API_KEY" in result.output
