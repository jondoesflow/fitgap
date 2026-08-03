"""The ``fitgap model`` command group — switch providers/models, manage keys.

The active provider is a per-engagement compliance decision: data residency
and processing terms differ by provider, and some client contracts will not
permit certain providers. That is why ``model use`` prints the provider and
the pipeline names it in every run summary.
"""

from __future__ import annotations

import getpass
from pathlib import Path

import typer

from fitgap.config import load_config, set_active_llm
from fitgap.llm.keys import mask_key, resolve_key, store_key
from fitgap.llm.registry import (
    PROVIDERS,
    UnknownProviderError,
    get_provider,
    parse_model_string,
)

model_app = typer.Typer(
    name="model",
    help="Show or switch the active LLM provider/model and manage API keys.",
    no_args_is_help=True,
)

CONFIG_OPTION = typer.Option(
    Path("fitgap.yaml"), "--config", "-c", help="Path to fitgap.yaml."
)


@model_app.command("list")
def list_models(config_path: Path = CONFIG_OPTION) -> None:
    """List supported providers and example model strings (active one marked)."""
    config = load_config(config_path)
    active = f"{config.llm.provider}/{config.llm.model}"
    typer.echo(f"Active: {active}\n")
    for spec in PROVIDERS.values():
        marker = "*" if spec.name == config.llm.provider else " "
        typer.secho(
            f"{marker} {spec.name}  ({spec.label}, key: {spec.env_var})",
            fg=typer.colors.CYAN if marker == "*" else None,
        )
        for model in spec.known_models:
            model_marker = " <- active" if f"{spec.name}/{model}" == active else ""
            typer.echo(f"    {spec.name}/{model}{model_marker}")
        if not spec.supports_learn_search:
            typer.echo(
                "    (no live Learn verification — use --skip-verify or switch "
                "to anthropic for the verify stage)"
            )
    typer.echo(
        "\nKnown-model lists are examples, not a whitelist — 'fitgap model use' "
        "accepts newer model strings with a warning."
    )


@model_app.command("use")
def use_model(
    model_string: str = typer.Argument(
        ..., help="Target as <provider>/<model>, e.g. anthropic/claude-sonnet-4-6."
    ),
    config_path: Path = CONFIG_OPTION,
) -> None:
    """Set the active provider/model (written to fitgap.yaml under llm:)."""
    try:
        spec, model = parse_model_string(model_string)
    except (ValueError, UnknownProviderError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    if model not in spec.known_models:
        typer.secho(
            f"Warning: '{model}' is not in the known-models list for "
            f"{spec.label} ({', '.join(spec.known_models)}). Proceeding anyway — "
            "new models ship faster than registries update.",
            fg=typer.colors.YELLOW,
        )
    set_active_llm(config_path, spec.name, model)
    typer.secho(
        f"Active model set to {spec.name}/{model} (written to {config_path}).",
        fg=typer.colors.GREEN,
    )
    typer.echo(
        f"Compliance note: redacted requirement text will now be sent to "
        f"{spec.label}. Confirm this provider is permitted for the current "
        "engagement."
    )
    if not spec.supports_learn_search:
        typer.secho(
            "Note: live Microsoft Learn verification (fitgap verify) requires "
            "Anthropic. With this provider, run with --skip-verify or switch "
            "back for the verify stage.",
            fg=typer.colors.YELLOW,
        )
    resolved = resolve_key(spec, config_path.parent)
    if resolved.key is None:
        typer.echo(
            f"No API key found for {spec.name}. Set {spec.env_var} or run: "
            f"fitgap model key {spec.name}"
        )


@model_app.command("key")
def set_key(
    provider: str = typer.Argument(..., help="Provider name, e.g. openai."),
    config_path: Path = CONFIG_OPTION,
) -> None:
    """Store an API key for a provider (hidden prompt; OS keyring or .env)."""
    try:
        spec = get_provider(provider)
    except UnknownProviderError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    key = getpass.getpass(f"API key for {spec.label} (input hidden): ").strip()
    if not key:
        typer.secho("No key entered — nothing stored.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    where, warnings = store_key(spec, key, config_path.parent)
    for warning in warnings:
        typer.secho(warning, fg=typer.colors.YELLOW)
    typer.secho(
        f"Key for {spec.name} ({mask_key(key)}) stored in {where}.",
        fg=typer.colors.GREEN,
    )
    typer.echo(
        f"Note: a real {spec.env_var} environment variable always takes "
        "precedence over stored keys."
    )


@model_app.command("status")
def status(config_path: Path = CONFIG_OPTION) -> None:
    """Show the active provider/model and where its API key was found."""
    config = load_config(config_path)
    try:
        spec = get_provider(config.llm.provider)
    except UnknownProviderError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Provider: {spec.name} ({spec.label})")
    typer.echo(f"Model:    {config.llm.model}")
    resolved = resolve_key(spec, config_path.parent)
    if resolved.key:
        typer.echo(f"API key:  {mask_key(resolved.key)} from {resolved.source}")
    else:
        typer.secho(
            f"API key:  NOT FOUND — set {spec.env_var} or run "
            f"'fitgap model key {spec.name}' ({spec.keys_url})",
            fg=typer.colors.RED,
        )
    typer.echo(
        "Live Learn verification: "
        + ("supported" if spec.supports_learn_search else "not supported (use --skip-verify)")
    )
