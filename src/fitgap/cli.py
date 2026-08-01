"""fitgap command-line interface."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import typer

from fitgap import __version__
from fitgap.config import Config, load_config, save_config
from fitgap.ingest.docx_parser import parse_docx
from fitgap.ingest.xlsx_parser import parse_xlsx, resolve_mapping
from fitgap.model_cli import model_app
from fitgap.models import ParsedRequirement, SourceReliability, Workspace
from fitgap.normalise import build_workspace, dedupe
from fitgap.redact import load_rules
from fitgap.usage import UsageTracker

app = typer.Typer(
    name="fitgap",
    help="Fit-gap analysis assistant for D365 CE / Power Platform consultants.",
    no_args_is_help=True,
)
app.add_typer(model_app)


def _provider_label(config: Config) -> str:
    from fitgap.llm.registry import PROVIDERS

    spec = PROVIDERS.get(config.llm.provider)
    return spec.label if spec else config.llm.provider


def _print_cost(tracker: UsageTracker | None, config: Config, title: str) -> None:
    if tracker is None or not tracker.stages:
        return
    typer.secho(f"\n{title}", fg=typer.colors.CYAN)
    # Compliance: the run summary always names the provider that received data.
    typer.echo(f"  provider: {_provider_label(config)} ({config.llm.model})")
    for line in tracker.summary_lines(config.model):
        typer.echo(line)


def _progress_bar():
    """Live progress display with percentage and ETA (rich ships with typer)."""
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
        TimeRemainingColumn,
    )

    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TextColumn("eta"),
        TimeRemainingColumn(),
    )


def _build_llm(config: Config, config_path: Path):
    """Resolve the active provider's API key and build its client.

    Fails with clear guidance (naming the exact env var and the ``model key``
    command) instead of an SDK traceback, and announces the provider so
    nobody sends client-derived content somewhere they didn't consciously
    choose."""
    from fitgap.llm.factory import make_client
    from fitgap.llm.keys import resolve_key
    from fitgap.llm.registry import UnknownProviderError, get_provider

    try:
        spec = get_provider(config.llm.provider)
    except UnknownProviderError as exc:
        typer.secho(
            f"{exc.args[0]} Fix the llm.provider value in fitgap.yaml or run "
            "'fitgap model use <provider>/<model>'.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    resolved = resolve_key(spec, config_path.parent)
    if resolved.key is None:
        typer.secho(
            f"No API key found for the active provider '{spec.name}' "
            f"({spec.label}) — this stage sends redacted requirement text to "
            f"{spec.label}.\n"
            f"Set the environment variable {spec.env_var}:\n"
            f'  PowerShell:   $env:{spec.env_var} = "..."\n'
            f'  bash/zsh:     export {spec.env_var}="..."\n'
            f"or store it once with:  fitgap model key {spec.name}\n"
            f"Keys: {spec.keys_url}\n"
            "No key needed for the offline stages: 'fitgap ingest' (without "
            "--transcript) and 'fitgap report' work fully offline.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    typer.secho(
        f"LLM provider: {spec.label} ({config.llm.model}) — redacted content "
        f"will be sent to {spec.label} (key from {resolved.source}).",
        fg=typer.colors.MAGENTA,
    )
    return make_client(spec.name, config.llm.model, resolved.key)


def _require_learn_search(config: Config) -> None:
    """The verify stage needs Anthropic-only live-search API features."""
    from fitgap.llm.registry import PROVIDERS

    spec = PROVIDERS.get(config.llm.provider)
    if spec is None or spec.supports_learn_search:
        return  # unknown provider fails later in _build_llm with guidance
    typer.secho(
        "'fitgap verify' needs live Microsoft Learn search, which uses "
        "Anthropic-only API features (MCP connector / web search). Active "
        f"provider: {spec.name}.\n"
        "Either switch for the verify stage:  fitgap model use anthropic/claude-sonnet-4-6\n"
        "or skip verification with --skip-verify (claims stay UNCONFIRMED).",
        fg=typer.colors.RED,
        err=True,
    )
    raise typer.Exit(code=1)


@app.command()
def version() -> None:
    """Show the fitgap version."""
    typer.echo(f"fitgap {__version__}")


@app.command()
def ingest(
    paths: list[Path] = typer.Argument(
        None,
        help="Requirement sources: .docx / .xlsx files, or the literal 'ado' "
        "to pull work items from Azure DevOps (configured in fitgap.yaml). "
        "May be omitted when using --transcript.",
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
    transcripts: list[Path] = typer.Option(
        [],
        "--transcript",
        "-t",
        help="Workshop transcript (.vtt/.txt/.docx) — implied requirements are "
        "extracted with the LLM and marked source_reliability: inferred. "
        "Repeatable.",
    ),
) -> None:
    """Parse requirement sources, deduplicate, and write the canonical workspace."""
    paths = paths or []
    if not paths and not transcripts:
        typer.secho(
            "No sources given. Pass .docx/.xlsx files, 'ado', and/or "
            "--transcript <file>.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    config = load_config(config_path)
    parsed: list[ParsedRequirement] = []
    transcript_redactions = []
    config_changed = False
    tracker = UsageTracker()

    for transcript_path in transcripts:
        if not transcript_path.exists():
            typer.secho(
                f"Not found: {transcript_path}", fg=typer.colors.RED, err=True
            )
            raise typer.Exit(code=1)
    llm_client = _build_llm(config, config_path) if transcripts else None
    for transcript_path in transcripts:
        from fitgap.ingest.transcript import TranscriptExtractor

        rules = load_rules(Path(config.redact_file))
        extractor = TranscriptExtractor(
            config, rules, llm_client, usage_tracker=tracker
        )
        with _progress_bar() as progress:
            task = progress.add_task(
                f"Extracting from {transcript_path.name} ({config.model})",
                total=None,
            )
            items, events = extractor.extract(
                transcript_path,
                on_progress=lambda done, total: progress.update(
                    task, completed=done, total=total
                ),
            )
        transcript_redactions.extend(events)
        typer.echo(
            f"  {transcript_path.name}: {len(items)} inferred requirement(s)"
        )
        parsed.extend(items)

    for path in paths:
        if str(path).lower() == "ado":
            from fitgap.ingest.ado import AdoError, fetch_ado_requirements

            try:
                items = fetch_ado_requirements(config.ado)
            except AdoError as exc:
                typer.secho(str(exc), fg=typer.colors.RED, err=True)
                raise typer.Exit(code=1)
            typer.echo(
                f"  Azure DevOps ({config.ado.organization}/{config.ado.project}): "
                f"{len(items)} requirement(s)"
            )
            parsed.extend(items)
            continue
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
    workspace.redaction_log.extend(transcript_redactions)
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
    _print_cost(tracker, config, "LLM usage (ingest):")
    return tracker


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
    """Classify each requirement against the fit-gap taxonomy (uses the configured LLM provider)."""
    from fitgap.classify import Classifier

    config = load_config(config_path)
    llm_client = _build_llm(config, config_path)
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

    tracker = UsageTracker()
    classifier = Classifier(
        config,
        rules,
        llm_client,
        batch_size=batch_size,
        usage_tracker=tracker,
    )
    with _progress_bar() as progress:
        task = progress.add_task(f"Classifying ({config.model})", total=None)
        classified, missing = classifier.classify_workspace(
            workspace,
            force=force,
            on_progress=lambda done, total: progress.update(
                task, completed=done, total=total
            ),
        )
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
    _print_cost(tracker, config, "LLM usage (classify):")
    return tracker


@app.command()
def verify(
    config_path: Path = typer.Option(
        Path("fitgap.yaml"), "--config", "-c", help="Path to fitgap.yaml."
    ),
    workspace_path: Path | None = typer.Option(
        None, "--workspace", "-w", help="Workspace JSON (default from config)."
    ),
    force: bool = typer.Option(
        False, "--force", help="Re-verify rows that already have a verified citation."
    ),
) -> None:
    """Verify every capability claim against live Microsoft Learn documentation."""
    from fitgap.verify import Verifier

    config = load_config(config_path)
    _require_learn_search(config)
    llm_client = _build_llm(config, config_path)
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

    tracker = UsageTracker()
    verifier = Verifier(config, rules, llm_client, usage_tracker=tracker)
    with _progress_bar() as progress:
        task = progress.add_task(
            f"Verifying against Microsoft Learn ({config.verify.mode} mode)",
            total=None,
        )
        counts = verifier.verify_workspace(
            workspace,
            force=force,
            on_progress=lambda done, total: progress.update(
                task, completed=done, total=total
            ),
        )
    workspace.save(ws_path)

    typer.echo(f"\n  Verified with live citation: {counts['verified']}")
    if counts["unconfirmed"]:
        typer.secho(
            f"  UNCONFIRMED — validate manually: {counts['unconfirmed']}",
            fg=typer.colors.YELLOW,
        )
    typer.echo(f"  No claim to verify (gaps/out of scope): {counts['not_required']}")
    if counts["skipped"]:
        typer.secho(
            f"  Skipped (unclassified): {counts['skipped']}", fg=typer.colors.RED
        )
    preview = sum(
        1
        for r in workspace.requirements
        if r.verification and r.verification.preview_flag
    )
    deprecated = sum(
        1
        for r in workspace.requirements
        if r.verification and r.verification.deprecated_flag
    )
    if preview:
        typer.secho(f"  PREVIEW features relied on: {preview}", fg=typer.colors.YELLOW)
    if deprecated:
        typer.secho(
            f"  DEPRECATED features relied on: {deprecated}", fg=typer.colors.RED
        )
    typer.echo(f"Workspace updated: {ws_path}")
    _print_cost(tracker, config, "LLM usage (verify):")
    return tracker


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


@app.command()
def run(
    paths: list[Path] = typer.Argument(
        None,
        help=".docx/.xlsx files and/or the literal 'ado'. "
        "May be omitted when using --transcript.",
    ),
    config_path: Path = typer.Option(
        Path("fitgap.yaml"), "--config", "-c", help="Path to fitgap.yaml."
    ),
    transcripts: list[Path] = typer.Option(
        [], "--transcript", "-t", help="Workshop transcripts (.vtt/.txt/.docx)."
    ),
    no_input: bool = typer.Option(False, "--no-input"),
    skip_verify: bool = typer.Option(
        False, "--skip-verify", help="Skip Learn verification (claims stay UNCONFIRMED)."
    ),
) -> None:
    """Full pipeline: ingest -> classify -> verify -> report."""
    # Fail fast before spending anything: a provider without live Learn
    # search cannot run the verify stage.
    if not skip_verify:
        _require_learn_search(load_config(config_path))
    run_tracker = UsageTracker()
    run_tracker.merge(
        ingest(
            paths=paths,
            config_path=config_path,
            out=None,
            no_input=no_input,
            transcripts=transcripts,
        )
    )
    typer.echo("")
    run_tracker.merge(
        classify(
            config_path=config_path, workspace_path=None, batch_size=10, force=False
        )
    )
    typer.echo("")
    if skip_verify:
        typer.secho(
            "Verification skipped — all capability claims will read "
            "'UNCONFIRMED — validate manually' in the register.",
            fg=typer.colors.YELLOW,
        )
    else:
        run_tracker.merge(
            verify(config_path=config_path, workspace_path=None, force=False)
        )
        typer.echo("")
    report(config_path=config_path, workspace_path=None, out=None)
    _print_cost(
        run_tracker,
        load_config(config_path),
        "=== Total LLM usage for this run ===",
    )


@app.command("eval")
def eval_cmd(
    golden_path: Path = typer.Option(
        Path("golden/golden_set.yaml"), "--golden", help="Golden set YAML."
    ),
    config_path: Path = typer.Option(
        Path("fitgap.yaml"), "--config", "-c", help="Path to fitgap.yaml."
    ),
    verify_citations: bool = typer.Option(
        True,
        "--verify/--no-verify",
        help="Also run Learn verification and re-check every asserted citation.",
    ),
    out: Path | None = typer.Option(
        None, "--out", help="Optionally save the evaluated workspace JSON."
    ),
) -> None:
    """Run the pipeline against the golden set and score it against the gates."""
    from fitgap.classify import Classifier
    from fitgap.evaluate import (
        ACCURACY_GATE,
        golden_workspace,
        load_golden_set,
        score_citations,
        score_classifications,
    )
    from fitgap.redact import RedactionRules

    if not golden_path.exists():
        typer.secho(f"Golden set not found: {golden_path}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    config = load_config(config_path)
    if verify_citations:
        _require_learn_search(config)
    client = _build_llm(config, config_path)
    golden = load_golden_set(golden_path)
    workspace = golden_workspace(golden)

    typer.echo(
        f"Classifying {len(golden.requirements)} golden requirements with "
        f"{config.model}..."
    )
    tracker = UsageTracker()
    # Golden set text is synthetic — no redaction rules needed.
    classifier = Classifier(
        config, RedactionRules(redact_emails=False), client, usage_tracker=tracker
    )
    classifier.classify_workspace(workspace)

    result = score_classifications(golden, workspace)

    if verify_citations:
        from fitgap.verify import Verifier

        typer.echo("Verifying capability claims against Microsoft Learn...")
        Verifier(
            config,
            RedactionRules(redact_emails=False),
            client,
            usage_tracker=tracker,
        ).verify_workspace(workspace)
        result = score_citations(workspace, result)

    if out:
        workspace.save(out)
        typer.echo(f"Evaluated workspace saved to {out}")

    typer.echo(
        f"\nClassification accuracy: {result.correct}/{result.total} "
        f"({result.accuracy:.0%}) — gate {ACCURACY_GATE:.0%}"
    )
    for category, score in sorted(result.by_category.items()):
        typer.echo(f"  {category}: {score.correct}/{score.total}")
    if result.mismatches:
        typer.echo("\nMismatches:")
        for req_id, expected, actual in result.mismatches:
            typer.secho(
                f"  {req_id}: expected '{expected}', got '{actual}'",
                fg=typer.colors.YELLOW,
            )
    if verify_citations:
        typer.echo(
            f"\nCitation integrity: {result.citations_resolving}/"
            f"{result.citations_checked} asserted citations resolve live "
            f"(gate: 100%)"
        )
        for req_id, url in result.dead_citations:
            typer.secho(f"  DEAD CITATION {req_id}: {url}", fg=typer.colors.RED)

    _print_cost(tracker, config, "LLM usage (eval):")
    if result.gates_met:
        typer.secho("\nGATES MET — ready for real projects.", fg=typer.colors.GREEN)
    else:
        typer.secho(
            "\nGATES NOT MET — do not use on real projects yet.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
