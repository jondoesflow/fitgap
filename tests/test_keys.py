"""Key resolution precedence, storage fallback, and key hygiene."""

import sys
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from fitgap.cli import app
from fitgap.llm.keys import mask_key, resolve_key, store_key
from fitgap.llm.registry import PROVIDERS

runner = CliRunner()
OPENAI = PROVIDERS["openai"]

ENV_KEY = "sk-env-key-1234"
KEYRING_KEY = "sk-keyring-key-5678"
FILE_KEY = "sk-file-key-9012"


class FakeKeyringModule:
    def __init__(self, store=None, broken=False):
        self.store = store if store is not None else {}
        self.broken = broken

    def get_password(self, service, username):
        if self.broken:
            raise RuntimeError("no keyring backend")
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        if self.broken:
            raise RuntimeError("no keyring backend")
        self.store[(service, username)] = password


@pytest.fixture
def no_env(monkeypatch):
    for spec in PROVIDERS.values():
        monkeypatch.delenv(spec.env_var, raising=False)


@pytest.fixture
def fake_keyring(monkeypatch):
    module = FakeKeyringModule()
    monkeypatch.setitem(sys.modules, "keyring", module)
    return module


@pytest.fixture
def broken_keyring(monkeypatch):
    monkeypatch.setitem(sys.modules, "keyring", FakeKeyringModule(broken=True))


def test_env_var_beats_keyring_beats_env_file(
    tmp_path, monkeypatch, no_env, fake_keyring
):
    fake_keyring.store[("fitgap", "openai")] = KEYRING_KEY
    (tmp_path / ".env").write_text(f"OPENAI_API_KEY={FILE_KEY}\n", encoding="utf-8")

    monkeypatch.setenv("OPENAI_API_KEY", ENV_KEY)
    resolved = resolve_key(OPENAI, tmp_path)
    assert (resolved.key, resolved.source_kind) == (ENV_KEY, "env")
    assert "OPENAI_API_KEY" in resolved.source

    monkeypatch.delenv("OPENAI_API_KEY")
    resolved = resolve_key(OPENAI, tmp_path)
    assert (resolved.key, resolved.source_kind) == (KEYRING_KEY, "keyring")

    del fake_keyring.store[("fitgap", "openai")]
    resolved = resolve_key(OPENAI, tmp_path)
    assert (resolved.key, resolved.source_kind) == (FILE_KEY, "envfile")

    (tmp_path / ".env").unlink()
    resolved = resolve_key(OPENAI, tmp_path)
    assert resolved.key is None and resolved.source is None


def test_store_key_prefers_keyring(tmp_path, no_env, fake_keyring):
    where, warnings = store_key(OPENAI, KEYRING_KEY, tmp_path)
    assert where == "OS keyring"
    assert warnings == []
    assert not (tmp_path / ".env").exists()


def test_store_key_falls_back_to_env_file(tmp_path, no_env, broken_keyring):
    where, warnings = store_key(OPENAI, FILE_KEY, tmp_path)
    env_file = tmp_path / ".env"
    assert ".env" in where
    assert warnings  # the plaintext warning is mandatory
    assert env_file.exists()
    assert oct(env_file.stat().st_mode & 0o777) == "0o600"
    assert f"OPENAI_API_KEY={FILE_KEY}" in env_file.read_text(encoding="utf-8")
    # .env must be git-ignored, adding the entry if missing.
    assert ".env" in (tmp_path / ".gitignore").read_text(encoding="utf-8").split()


def test_store_key_appends_gitignore_only_once(tmp_path, no_env, broken_keyring):
    (tmp_path / ".gitignore").write_text("*.pyc\n.env\n", encoding="utf-8")
    store_key(OPENAI, FILE_KEY, tmp_path)
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8").count(".env") == 1


def test_mask_key_shows_only_tail():
    assert mask_key("sk-proj-abcdefgh1234") == "…1234"
    assert mask_key("short") == "…"


def test_model_status_reports_true_source_and_masks_key(
    tmp_path, monkeypatch, no_env, fake_keyring
):
    config = tmp_path / "fitgap.yaml"
    config.write_text(
        "llm:\n  provider: openai\n  model: gpt-5.1\n", encoding="utf-8"
    )
    monkeypatch.setenv("OPENAI_API_KEY", ENV_KEY)
    result = runner.invoke(app, ["model", "status", "-c", str(config)])
    assert result.exit_code == 0, result.output
    assert "openai" in result.output
    assert "environment variable OPENAI_API_KEY" in result.output
    assert "…1234" in result.output
    assert ENV_KEY not in result.output  # never echo the key

    # Same again, but resolved from the keyring: status must say so honestly.
    monkeypatch.delenv("OPENAI_API_KEY")
    fake_keyring.store[("fitgap", "openai")] = KEYRING_KEY
    result = runner.invoke(app, ["model", "status", "-c", str(config)])
    assert "OS keyring" in result.output
    assert "…5678" in result.output
    assert KEYRING_KEY not in result.output


def test_model_key_command_hidden_prompt_and_no_echo(
    tmp_path, monkeypatch, no_env, fake_keyring
):
    monkeypatch.setattr("getpass.getpass", lambda prompt: "sk-secret-key-4242")
    config = tmp_path / "fitgap.yaml"
    result = runner.invoke(app, ["model", "key", "openai", "-c", str(config)])
    assert result.exit_code == 0, result.output
    assert fake_keyring.store[("fitgap", "openai")] == "sk-secret-key-4242"
    assert "sk-secret-key-4242" not in result.output
    assert "…4242" in result.output


def test_no_key_material_in_auth_failure_output(tmp_path, monkeypatch, no_env):
    """Deliberately trigger an auth failure and inspect everything surfaced."""
    from fakes import FakeOpenAI

    from fitgap.llm.openai_compat import OpenAICompatClient

    secret = "sk-live-key-do-not-leak-7777"

    def unauthorized(kwargs):
        raise RuntimeError("Error code: 401 - Incorrect API key provided")

    client = OpenAICompatClient(
        PROVIDERS["deepseek"], "deepseek-chat", api_key=secret,
        sdk_client=FakeOpenAI(unauthorized),
    )
    with pytest.raises(RuntimeError) as excinfo:
        client.structured(
            system="s", user="u", tool_name="t", tool_description="d",
            schema={"type": "object", "properties": {}, "required": []},
        )
    assert secret not in str(excinfo.value)
    import traceback

    formatted = "".join(
        traceback.format_exception(type(excinfo.value), excinfo.value, excinfo.tb)
    )
    assert secret not in formatted


def test_missing_key_failure_names_env_var_and_key_command(
    tmp_path, monkeypatch, no_env, broken_keyring
):
    config = tmp_path / "fitgap.yaml"
    config.write_text(
        "llm:\n  provider: deepseek\n  model: deepseek-chat\n", encoding="utf-8"
    )
    result = runner.invoke(app, ["classify", "-c", str(config)])
    assert result.exit_code == 1
    assert "DEEPSEEK_API_KEY" in result.output
    assert "fitgap model key deepseek" in result.output
    assert "offline" in result.output
