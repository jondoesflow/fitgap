"""API-key resolution and storage.

A CLI process cannot persistently set an environment variable in its parent
shell, so keys are stored out-of-band and resolved at runtime with this
precedence (first hit wins):

1. Real environment variable (e.g. ANTHROPIC_API_KEY)
2. OS keyring (Windows Credential Manager / macOS Keychain / Secret Service)
3. A git-ignored ``.env`` file next to fitgap.yaml (0600, headless fallback)

Keys never appear in config YAML, logs, error messages, or status output —
only a masked tail (last 4 characters) is ever shown.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from fitgap.llm.registry import ProviderSpec

KEYRING_SERVICE = "fitgap"


@dataclass
class ResolvedKey:
    key: str | None
    source: str | None       # human-readable, e.g. "environment variable X"
    source_kind: str | None  # "env" | "keyring" | "envfile" | None


def mask_key(key: str) -> str:
    """Show only the last 4 characters — never enough to reconstruct a key."""
    return "…" + key[-4:] if len(key) >= 8 else "…"


def env_file_path(config_dir: Path) -> Path:
    return config_dir / ".env"


def _read_env_file(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return entries
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        entries[name.strip()] = value.strip().strip("'\"")
    return entries


def _restrict_to_owner(path: Path) -> bool:
    """Try to make ``path`` readable only by its owner.

    Returns whether the OS actually enforces it. ``os.chmod`` applies POSIX
    mode bits on Linux and macOS, but on Windows it only toggles the
    read-only attribute — group and other bits are ignored — so the caller
    must not report the file as permission-restricted there.
    """
    try:
        os.chmod(path, 0o600)
    except OSError:
        return False
    return os.name == "posix"


def _keyring_get(spec: ProviderSpec) -> str | None:
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, spec.name)
    except Exception:
        # No backend / locked keyring / not installed — treat as no key.
        return None


def resolve_key(spec: ProviderSpec, config_dir: Path) -> ResolvedKey:
    value = os.environ.get(spec.env_var)
    if value:
        return ResolvedKey(value, f"environment variable {spec.env_var}", "env")
    value = _keyring_get(spec)
    if value:
        return ResolvedKey(value, "OS keyring", "keyring")
    path = env_file_path(config_dir)
    value = _read_env_file(path).get(spec.env_var) if path.exists() else None
    if value:
        return ResolvedKey(value, f".env file ({path})", "envfile")
    return ResolvedKey(None, None, None)


def store_key(spec: ProviderSpec, key: str, config_dir: Path) -> tuple[str, list[str]]:
    """Store a key; returns (where it was stored, warnings to show the user)."""
    try:
        import keyring

        keyring.set_password(KEYRING_SERVICE, spec.name, key)
        # Some headless systems expose a keyring API with no working backend;
        # only trust it if the key reads back.
        if keyring.get_password(KEYRING_SERVICE, spec.name) == key:
            return "OS keyring", []
    except Exception:
        pass

    path = env_file_path(config_dir)
    entries = _read_env_file(path) if path.exists() else {}
    entries[spec.env_var] = key
    config_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# API keys written by 'fitgap model key' — never commit this file.\n"
        + "".join(f"{name}={value}\n" for name, value in entries.items()),
        encoding="utf-8",
    )
    restricted = _restrict_to_owner(path)
    _ensure_env_gitignored(config_dir)
    warnings = [
        "OS keyring unavailable — key stored in a plaintext .env file "
        + (f"({path}, permissions 0600)." if restricted else f"({path})."),
        "The file is git-ignored; prefer a real environment variable or "
        "OS keyring where available.",
    ]
    if not restricted:
        # Never imply protection the OS is not applying: on Windows os.chmod
        # only toggles the read-only attribute, so the POSIX mode bits this
        # would set are not enforced and the file is as readable as its
        # directory. Say so, and point at the safer options.
        warnings.append(
            "This platform cannot restrict the file to your account, so "
            "anyone who can read the folder can read the key — prefer "
            "Windows Credential Manager (used automatically when the keyring "
            "works) or an environment variable."
        )
    return f".env file ({path})", warnings


def _ensure_env_gitignored(project_dir: Path) -> None:
    gitignore = project_dir / ".gitignore"
    existing = (
        gitignore.read_text(encoding="utf-8").splitlines()
        if gitignore.exists()
        else []
    )
    if any(line.strip() in (".env", "/.env") for line in existing):
        return
    with gitignore.open("a", encoding="utf-8") as handle:
        if existing and existing[-1].strip():
            handle.write("\n")
        handle.write("# Local API keys (fitgap model key) — never commit\n.env\n")
