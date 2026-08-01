from pathlib import Path

from fitgap.config import Config, XlsxMapping, load_config, save_config


def test_missing_config_yields_defaults(tmp_path: Path):
    config = load_config(tmp_path / "absent.yaml")
    assert config.model == "claude-sonnet-4-6"
    assert config.verify.mode == "mcp"
    assert config.dedupe_threshold == 90


def test_roundtrip(tmp_path: Path):
    path = tmp_path / "fitgap.yaml"
    config = Config()
    config.ado.organization = "contoso"
    config.xlsx_mappings["backlog.xlsx"] = XlsxMapping(text="Requirement", id="ID")
    save_config(config, path)

    loaded = load_config(path)
    assert loaded.ado.organization == "contoso"
    assert loaded.xlsx_mappings["backlog.xlsx"].text == "Requirement"
    assert loaded.xlsx_mappings["backlog.xlsx"].priority is None


def test_partial_yaml_fills_defaults(tmp_path: Path):
    path = tmp_path / "fitgap.yaml"
    path.write_text("model: claude-sonnet-4-6\nado:\n  organization: acme\n", encoding="utf-8")
    config = load_config(path)
    assert config.ado.organization == "acme"
    assert config.output.register_path.endswith(".xlsx")
