"""fitgap command-line interface."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import typer

from fitgap import __version__
from fitgap.config import load_config, save_config
from fitgap.ingest.docx_parser import parse_docx
from fitgap.ingest.xlsx_parser import parse_xlsx, resolve_mapping
from fitgap.models import ParsedRequirement, SourceReliability, Workspace
from fitgap.normalise import build_workspace, dedupe
from fitgap.redact import load_rules

app = typer.Typer(
    name="fitgap",
    help="Fit-gap analysis assistant for D365 CE / Power Platform consultants.",
    no_args_is_help=True,
)


@app.command()
def version() -> None:
    """Show the fitgap version."""
    typer.echo(f"fitgap {__version__}")


@app.command()
def ingest(
    paths: list[Path] = typer.Argument(
        ..., help="Requirement sources: .docx and/or .xlsx files."
    ),
    config_path: Path = typer.Option(
        Path("fitgap.yaml"), "--config", "-c", help="Path to fitgap.yaml."
    ),
    out: Path | None = typer.Option(
        None, "--out", "-o", help="Workspace JSON output (default from config)."
    ),
    no_input: bool = typer.Option(
        False, "--no-input", help="Never prompt (fail if a column mapping is missing)."
    ),
) -> None:
    """Parse requirement sources, deduplicate, and write the canonical workspace."""
    config = load_config(config_path)
    parsed: list[ParsedRequirement] = []
    config_changed = False

    for path in paths:
        if not path.exists():
            typer.secho(f"Not found: {path}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        suffix = path.suffix.lower()
        if suffix == ".docx":
            items = parse_docx(path)
        elif suffix == ".xlsx":
            saved = config.xlsx_mappings.get(path.name)
            mapping = resolve_mapping(path, saved, interactive=not no_input)
            if saved is None:
                config.xlsx_mappings[path.name] = mapping
                config_changed = True
            items = parse_xlsx(path, mapping)
        else:
            typer.secho(
                f"Unsupported source type '{suffix}': {path}",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1)
        typer.echo(f"  {path.name}: {len(items)} requirement(s)")
        parsed.extend(items)

    if config_changed:
        save_config(config, config_path)
        typer.echo(f"Saved column mapping(s) to {config_path}")

    before = len(parsed)
    deduped = dedupe(parsed, threshold=config.dedupe_threshold)
    merged_away = before - len(deduped)

    workspace = build_workspace(deduped)
    out_path = out or Path(config.output.workspace)
    workspace.save(out_path)

    by_source = Counter(req.source.value for req in workspace.requirements)
    inferred = sum(
        1
        for req in workspace.requirements
        if req.source_reliability == SourceReliability.INFERRED
    )
    typer.echo(
        f"\nIngested {len(workspace.requirements)} requirement(s) "
        f"({merged_away} near-duplicate(s) merged)."
    )
    for source, count in sorted(by_source.items()):
        typer.echo(f"  {source}: {count}")
    if inferred:
        typer.echo(f"  transcript-inferred (double-check these): {inferred}")
    typer.echo(f"Workspace written to {out_path}")


@app.command()
def classify(
    config_path: Path = typer.Option(
        Path("fitgap.yaml"), "--config", "-c", help="Path to fitgap.yaml."
    ),
    workspace_path: Path | None = typer.Option(
        None, "--workspace", "-w", help="Workspace JSON (default from config)."
    ),
    batch_size: int = typer.Option(10, "--batch-size", min=1, max=25),
    force: bool = typer.Option(
        False, "--force", help="Re-classify requirements that already have a result."
    ),
) -> None:
    """Classify each requirement against the fit-gap taxonomy (uses the Anthropic API)."""
    import anthropic

    from fitgap.classify import Classifier

    config = load_config(config_path)
    ws_path = workspace_path or Path(config.output.workspace)
    if not ws_path.exists():
        typer.secho(
            f"Workspace not found: {ws_path} — run 'fitgap ingest' first.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    workspace = Workspace.load(ws_path)

    rules = load_rules(Path(config.redact_file))
    if not (rules.client_names or rules.people or rules.codenames or rules.custom):
        typer.secho(
            f"Note: no name-based redaction rules loaded from {config.redact_file} "
            "(emails are still redacted by default).",
            fg=typer.colors.YELLOW,
        )

    classifier = Classifier(
        config, rules, anthropic.Anthropic(), batch_size=batch_size
    )
    typer.echo(f"Classifying with {config.model}...")
    classified, missing = classifier.classify_workspace(workspace, force=force)
    workspace.save(ws_path)

    counts = Counter(
        r.classification.category.value
        for r in workspace.requirements
        if r.classification
    )
    low_confidence = sum(
        1
        for r in workspace.requirements
        if r.classification and r.classification.confidence.value == "Low"
    )
    redacted = len(workspace.redaction_log)
    typer.echo(f"\nClassified {classified} requirement(s):")
    for category, count in counts.most_common():
        typer.echo(f"  {category}: {count}")
    if low_confidence:
        typer.secho(
            f"  Low confidence (review carefully): {low_confidence}",
            fg=typer.colors.YELLOW,
        )
    if missing:
        typer.secho(
            f"  NOT classified (model skipped, re-run classify): {', '.join(missing)}",
            fg=typer.colors.RED,
        )
    if redacted:
        typer.echo(f"Redaction log entries: {redacted}")
    typer.echo(f"Workspace updated: {ws_path}")


@app.command()
def report(
    config_path: Path = typer.Option(
        Path("fitgap.yaml"), "--config", "-c", help="Path to fitgap.yaml."
    ),
    workspace_path: Path | None = typer.Option(
        None, "--workspace", "-w", help="Workspace JSON (default from config)."
    ),
    out: Path | None = typer.Option(
        None, "--out", "-o", help="Register .xlsx output (default from config)."
    ),
) -> None:
    """Generate the Excel fit-gap register from the workspace."""
    from fitgap.report import generate_register

    config = load_config(config_path)
    ws_path = workspace_path or Path(config.output.workspace)
    if not ws_path.exists():
        typer.secho(
            f"Workspace not found: {ws_path} — run 'fitgap ingest' first.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    workspace = Workspace.load(ws_path)
    out_path = out or Path(config.output.register_path)
    generate_register(workspace, config, out_path)

    unclassified = sum(1 for r in workspace.requirements if r.classification is None)
    typer.echo(
        f"Register written to {out_path} "
        f"({len(workspace.requirements)} requirement(s))."
    )
    if unclassified:
        typer.secho(
            f"Warning: {unclassified} requirement(s) unclassified — "
            "run 'fitgap classify' for a complete register.",
            fg=typer.colors.YELLOW,
        )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
