import json

from typer.testing import CliRunner

from fitgap.cli import app
from fitgap.models import Workspace

runner = CliRunner()


def test_ingest_docx_and_xlsx_end_to_end(sample_docx, sample_xlsx, tmp_path):
    out = tmp_path / "requirements.json"
    config = tmp_path / "fitgap.yaml"
    result = runner.invoke(
        app,
        [
            "ingest",
            str(sample_docx),
            str(sample_xlsx),
            "--config",
            str(config),
            "--out",
            str(out),
            "--no-input",
        ],
    )
    assert result.exit_code == 0, result.output
    workspace = Workspace.load(out)

    # 6 docx + 3 xlsx, minus the cross-source duplicate quote requirement.
    assert len(workspace.requirements) == 8
    ids = [r.id for r in workspace.requirements]
    assert ids == [f"REQ-{i:03d}" for i in range(1, 9)]

    merged = [r for r in workspace.requirements if r.merged_from]
    assert len(merged) == 1
    assert "sample_backlog.xlsx" in merged[0].merged_from[0]

    # Auto-detected xlsx mapping was persisted for next time.
    saved = config.read_text(encoding="utf-8")
    assert "sample_backlog.xlsx" in saved
    assert "Requirement" in saved


def test_ingest_rejects_unknown_extension(tmp_path):
    bad = tmp_path / "reqs.pdf"
    bad.write_text("x", encoding="utf-8")
    result = runner.invoke(app, ["ingest", str(bad), "--no-input"])
    assert result.exit_code == 1


def test_ingest_missing_file_fails(tmp_path):
    result = runner.invoke(app, ["ingest", str(tmp_path / "nope.docx")])
    assert result.exit_code == 1


def test_workspace_json_is_valid_and_versioned(sample_docx, tmp_path):
    out = tmp_path / "ws.json"
    result = runner.invoke(
        app,
        ["ingest", str(sample_docx), "--config", str(tmp_path / "c.yaml"), "--out", str(out), "--no-input"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["tool_version"]
    assert all(r["classification"] is None for r in data["requirements"])
