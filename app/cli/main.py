from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

import typer
from rich.console import Console
from rich.table import Table

from cli.config import RepoConfig, load_config
from services.adg import parse_repo
from services.cpt import GitAdapter, process_diff
from services.cpt.dismissal import Dismissal, filter_dismissed, violation_short_id
from services.extract import extract_all_adrs
from services.extract.engine import derive_package_context
from services.graph.connector import GraphStore
from services.models import CommitDiff, DiffResult, FQNKind, SymbolicConstraint
from services.commit_update import UpdateResult, commit_update
from services.pipeline import ADGPipeline, PipelineInputs

console = Console()


@dataclass
class DetectionResult:
    cpt_result: "object"  # services.cpt.engine.CPTResult
    commit_diff: CommitDiff
    diff_result: DiffResult
    repo_cfg: RepoConfig
    repo_path: Path


def _setup_logging(verbose: int = 0) -> None:
    level = {0: logging.WARNING, 1: logging.INFO, 2: logging.DEBUG}.get(verbose, logging.DEBUG)
    logging.basicConfig(
        level=level,
        format="%(name)s: %(message)s",
    )

app = typer.Typer(
    name="cpt",
    help="CPT Detection System: validate commits against ADR constraints.",
    no_args_is_help=True,
)

@app.callback()
def main(
    verbose: int = typer.Option(0, "--verbose", "-v", count=True, help="Increase log verbosity (-v=INFO, -vv=DEBUG)"),
) -> None:
    _setup_logging(verbose)

seed_app = typer.Typer(help="Manage ADG seed snapshots.")
app.add_typer(seed_app, name="seed")

def _get_repo(repo: str):
    config = load_config()
    try:
        return config.get_repo(repo)
    except ValueError as e:
        console.print(f"[red]Error:[/] {e}")
        raise typer.Exit(code=1)

def _resolve_repo_path(repo_cfg) -> Path:
    """Resolve the repo URL to a local filesystem path"""
    config_dir = Path(__file__).resolve().parents[2] / "repos"
    path = Path(repo_cfg.url)
    if not path.is_absolute():
        path = config_dir / path
    return path.resolve()


def _run_detection(repo: str, commit: str | None) -> DetectionResult:
    """Shared detection pipeline. Loads ADG from Neo4j (seeded via `seed build`)."""
    from services.cpt.engine import CPTResult

    repo_cfg = _get_repo(repo)
    repo_path = _resolve_repo_path(repo_cfg)

    if not repo_path.exists():
        console.print(f"[red]Error:[/] Repository path does not exist: {repo_path}")
        raise typer.Exit(code=1)

    adapter = GitAdapter()
    try:
        commit_diff = adapter.get_commit_diff(repo_path, commit_sha=commit)
    except ValueError as e:
        console.print(f"[red]Git error:[/] {e}")
        raise typer.Exit(code=1)

    diff_result: DiffResult = process_diff(commit_diff)

    # ponytail: load seeded ADG from Neo4j instead of re-parsing repo + re-extracting ADRs
    store = GraphStore(
        uri=os.getenv("NEO4J_URI", "bolt://neo4j:7687"),
        user=os.getenv("NEO4J_USER", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD", "password"),
        database=os.getenv("NEO4J_DATABASE", "neo4j"),
    )
    store.connect()
    adg = store.load_adg()
    store.close()

    pipeline = ADGPipeline()
    pipeline_inputs = PipelineInputs(
        adg=adg,
        constraints=[],  # constraints already in ADG from seed
        diff_result=diff_result,
        commit_diff=commit_diff,
    )
    cpt_result = pipeline.run_prepared(pipeline_inputs)

    return DetectionResult(
        cpt_result=cpt_result,
        commit_diff=commit_diff,
        diff_result=diff_result,
        repo_cfg=repo_cfg,
        repo_path=repo_path,
    )


violation_app = typer.Typer(help="List and dismiss CPT violations.")
app.add_typer(violation_app, name="violation")

@app.command()
def detect(
    repo: str = typer.Option(..., "--repo", "-r", help="Repository ID from repos.yaml"),
    commit: str | None = typer.Option(None, "--commit", "-c", help="Commit SHA (default: HEAD)"),
) -> None:
    """Run CPT violation detection on a repository."""
    dr = _run_detection(repo, commit)

    console.print(f"[bold]Detecting[/] violations in [cyan]{repo}[/] (commit: {commit or 'HEAD'})")
    console.print()
    console.print(f"[bold]Commit:[/] {dr.commit_diff.commit_sha[:6]}...")
    if dr.commit_diff.parent_sha is not None:
        console.print(f"[bold]Parent:[/] {dr.commit_diff.parent_sha[:6]}...")

    if dr.diff_result.changed_files:
        file_table = Table(title="Changed Files", show_lines=True)
        file_table.add_column("Path", style="cyan")
        file_table.add_column("Status", style="green")
        for file_changed in dr.diff_result.changed_files:
            status_style = {
                "added": "green",
                "modified": "yellow",
                "deleted": "red",
                "renamed": "blue",
            }.get(file_changed.status, "white")
            path_display = file_changed.path
            if file_changed.old_path:
                path_display = f"{file_changed.old_path} -> {file_changed.path}"
            file_table.add_row(path_display, f"[{status_style}]{file_changed.status}[/{status_style}]")
        console.print(file_table)

    if dr.diff_result.changed_fqns:
        fqn_table = Table(title="Changed FQNs", show_lines=True)
        fqn_table.add_column("FQN", style="cyan")
        fqn_table.add_column("Change", style="green")
        fqn_table.add_column("File", style="dim")
        fqn_table.add_column("Enclosing Class", style="dim")
        fqn_table.add_column("Module", style="dim")
        for changed_fqn in dr.diff_result.changed_fqns:
            change_style = {
                "added": "green",
                "modified": "yellow",
                "deleted": "red",
            }.get(changed_fqn.change_type, "white")
            fqn_table.add_row(
                str(changed_fqn.fqn),
                f"[{change_style}]{changed_fqn.change_type}[/{change_style}]",
                changed_fqn.file_path,
                str(changed_fqn.enclosing_class) if changed_fqn.enclosing_class is not None else "-",
                str(changed_fqn.enclosing_module),
            )
        console.print(fqn_table)
    else:
        console.print("[dim]No FQN changes detected.[/]")

    # Display violations
    if dr.cpt_result.violations:
        v_table = Table(title="Violations", show_lines=True)
        v_table.add_column("ADR", style="cyan")
        v_table.add_column("Predicate", style="bold red")
        v_table.add_column("Subject", style="yellow")
        v_table.add_column("Object", style="yellow")
        v_table.add_column("Changed FQN", style="green")
        v_table.add_column("Evidence", style="dim")
        for v in dr.cpt_result.violations:
            v_table.add_row(
                v.constraint.adr_id,
                v.constraint.predicate.value,
                v.constraint.subject,
                v.constraint.object,
                str(v.changed_fqn),
                v.evidence,
            )
        console.print(v_table)
    else:
        console.print("[bold green]No violations found.[/]")

    if dr.cpt_result.orphans:
        o_table = Table(title="Orphan Constraints (no neighborhood match)", show_lines=True)
        o_table.add_column("ADR", style="cyan")
        o_table.add_column("Predicate", style="dim")
        o_table.add_column("Subject", style="dim")
        o_table.add_column("Object", style="dim")
        for c in dr.cpt_result.orphans:
            o_table.add_row(c.adr_id, c.predicate.value, c.subject, c.object)
        console.print(o_table)


@app.command()
def update(
    repo: str = typer.Option(..., "--repo", "-r", help="Repository ID from repos.yaml"),
    commit: str | None = typer.Option(None, "--commit", "-c", help="Commit SHA (default: HEAD)"),
) -> None:
    """Update ADG with commit changes: full structural rebuild preserving constraints and dismissals."""
    repo_cfg = _get_repo(repo)
    repo_path = _resolve_repo_path(repo_cfg)

    if not repo_path.exists():
        console.print(f"[red]Error:[/] Repository path does not exist: {repo_path}")
        raise typer.Exit(code=1)

    store = GraphStore(
        uri=os.getenv("NEO4J_URI", "bolt://neo4j:7687"),
        user=os.getenv("NEO4J_USER", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD", "password"),
        database=os.getenv("NEO4J_DATABASE", "neo4j"),
    )
    store.connect()

    try:
        result = commit_update(store, repo_path, commit_sha=commit)
    except RuntimeError as e:
        console.print(f"[red]Error:[/] {e}")
        store.close()
        raise typer.Exit(code=1)

    store.close()

    console.print(f"[bold]Updating[/] [cyan]{repo}[/] (commit: {commit or 'HEAD'})")
    console.print()
    console.print(f"[bold]Commit:[/] {result.commit_sha[:6]}...")
    if result.parent_sha is not None:
        console.print(f"[bold]Parent:[/] {result.parent_sha[:6]}...")

    console.print(f"  Constraint edges preserved: {result.constraint_edges_preserved}")
    console.print(f"  Dismissals applied: {result.dismissals_applied}")

    if result.changed_file_list:
        file_table = Table(title="Changed Files", show_lines=True)
        file_table.add_column("Path", style="cyan")
        file_table.add_column("Status", style="green")
        for file_changed in result.changed_file_list:
            status_style = {
                "added": "green",
                "modified": "yellow",
                "deleted": "red",
                "renamed": "blue",
            }.get(file_changed.status, "white")
            path_display = file_changed.path
            if file_changed.old_path:
                path_display = f"{file_changed.old_path} -> {file_changed.path}"
            file_table.add_row(path_display, f"[{status_style}]{file_changed.status}[/{status_style}]")
        console.print(file_table)

    if result.changed_fqns:
        fqn_table = Table(title="Changed FQNs", show_lines=True)
        fqn_table.add_column("FQN", style="cyan")
        fqn_table.add_column("Change", style="green")
        fqn_table.add_column("File", style="dim")
        fqn_table.add_column("Enclosing Class", style="dim")
        fqn_table.add_column("Module", style="dim")
        for changed_fqn in result.changed_fqns:
            change_style = {
                "added": "green",
                "modified": "yellow",
                "deleted": "red",
            }.get(changed_fqn.change_type, "white")
            fqn_table.add_row(
                str(changed_fqn.fqn),
                f"[{change_style}]{changed_fqn.change_type}[/{change_style}]",
                changed_fqn.file_path,
                str(changed_fqn.enclosing_class) if changed_fqn.enclosing_class is not None else "-",
                str(changed_fqn.enclosing_module),
            )
        console.print(fqn_table)

    if result.violations:
        v_table = Table(title="Active Violations", show_lines=True)
        v_table.add_column("Short ID", style="bold cyan")
        v_table.add_column("ADR", style="cyan")
        v_table.add_column("Predicate", style="bold red")
        v_table.add_column("Subject", style="yellow")
        v_table.add_column("Object", style="yellow")
        v_table.add_column("Changed FQN", style="green")
        v_table.add_column("Evidence", style="dim")
        for v in result.violations:
            v_table.add_row(
                violation_short_id(v),
                v.constraint.adr_id,
                v.constraint.predicate.value,
                v.constraint.subject,
                v.constraint.object,
                str(v.changed_fqn),
                v.evidence,
            )
        console.print(v_table)
    else:
        console.print("[bold green]No active violations.[/]")

    if result.orphans:
        o_table = Table(title="Orphan Constraints (no neighborhood match)", show_lines=True)
        o_table.add_column("ADR", style="cyan")
        o_table.add_column("Predicate", style="dim")
        o_table.add_column("Subject", style="dim")
        o_table.add_column("Object", style="dim")
        for c in result.orphans:
            o_table.add_row(c.adr_id, c.predicate.value, c.subject, c.object)
        console.print(o_table)


@app.command()
def report(
    repo: str = typer.Option(..., "--repo", "-r", help="Repository ID from repos.yaml"),
) -> None:
    """View stored violation reports for a repository."""
    _get_repo(repo)
    console.print(f"[bold]Fetching[/] reports for [cyan]{repo}[/]")
    console.print("[dim]Not implemented yet.[/]")


@violation_app.command("list")
def violation_list(
    repo: str = typer.Option(..., "--repo", "-r", help="Repository ID from repos.yaml"),
    commit: str | None = typer.Option(None, "--commit", "-c", help="Commit SHA (default: HEAD)"),
) -> None:
    """List violations, filtering out dismissed ones."""
    console.print(f"[bold]Detecting[/] violations in [cyan]{repo}[/] (commit: {commit or 'HEAD'})")
    dr = _run_detection(repo, commit)
    console.print(f"  Found {len(dr.cpt_result.violations)} violation(s) before dismissal filter")

    store = GraphStore(
        uri=os.getenv("NEO4J_URI", "bolt://neo4j:7687"),
        user=os.getenv("NEO4J_USER", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD", "password"),
        database=os.getenv("NEO4J_DATABASE", "neo4j"),
    )
    store.connect()
    dismissals = store.load_dismissals()
    store.close()
    console.print(f"  Loaded {len(dismissals)} dismissal(s) from Neo4j")

    active = filter_dismissed(dr.cpt_result.violations, dismissals)

    if active:
        table = Table(title="Active Violations", show_lines=True)
        table.add_column("Short ID", style="bold cyan")
        table.add_column("ADR", style="cyan")
        table.add_column("Predicate", style="bold red")
        table.add_column("Subject", style="yellow")
        table.add_column("Object", style="yellow")
        table.add_column("Changed FQN", style="green")
        table.add_column("Evidence", style="dim")
        for v in active:
            table.add_row(
                violation_short_id(v),
                v.constraint.adr_id,
                v.constraint.predicate.value,
                v.constraint.subject,
                v.constraint.object,
                str(v.changed_fqn),
                v.evidence,
            )
        console.print(table)
    else:
        console.print("[bold green]No active violations.[/]")

    dismissed_count = len(dr.cpt_result.violations) - len(active)
    if dismissed_count:
        console.print(f"[dim]{dismissed_count} violation(s) dismissed.[/]")


@violation_app.command("dismiss")
def violation_dismiss(
    short_id: str = typer.Argument(..., help="Short ID (5 hex chars) of the violation to dismiss"),
    repo: str = typer.Option(..., "--repo", "-r", help="Repository ID from repos.yaml"),
    commit: str | None = typer.Option(None, "--commit", "-c", help="Commit SHA (default: HEAD)"),
) -> None:
    """Dismiss a violation by its short ID. Violations are ephemeral; must match current detection results."""
    console.print(f"[bold]Detecting[/] violations in [cyan]{repo}[/] (commit: {commit or 'HEAD'})")
    dr = _run_detection(repo, commit)

    match = None
    for v in dr.cpt_result.violations:
        if violation_short_id(v) == short_id:
            match = v
            break

    if match is None:
        console.print(f"[red]Error:[/] No violation with short_id '{short_id}' found in current detection results")
        raise typer.Exit(code=1)

    dismissal = Dismissal.from_violation(match)

    store = GraphStore(
        uri=os.getenv("NEO4J_URI", "bolt://neo4j:7687"),
        user=os.getenv("NEO4J_USER", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD", "password"),
        database=os.getenv("NEO4J_DATABASE", "neo4j"),
    )
    store.connect()
    store.store_dismissal(dismissal)
    store.close()

    console.print(f"[green]Dismissed[/] violation {short_id}: {match.constraint.predicate.value} "
                  f"{match.constraint.subject} -> {match.constraint.object}")


@seed_app.command("build")
def seed_build(
    repo: str = typer.Option(..., "--repo", "-r", help="Repository ID from repos.yaml"),
) -> None:
    """Build an ADG seed snapshot from scratch."""
    config = load_config()
    repo_cfg = config.get_repo(repo)
    repo_path = _resolve_repo_path(repo_cfg)

    if not repo_path.exists():
        console.print(f"[red]Error:[/] Repository path does not exist: {repo_path}")
        raise typer.Exit(code=1)

    # parse repo into adg
    console.print("[bold]Step 1:[/] Parsing repository structure...")
    adg = parse_repo(repo_path)
    console.print(f"  Found {len(adg.nodes)} nodes, {len(adg.edges)} edges")
    package_context = derive_package_context(adg)

    # extract ADR constraints
    console.print("[bold]Step 2:[/] Extracting ADR constraints...")
    results = extract_all_adrs(repo_path, repo_cfg.adr_dir, config.langextract, package_context=package_context)
    all_constraints: list[SymbolicConstraint] = []
    total_errors = 0
    for result in results:
        all_constraints.extend(result.constraints)
        total_errors += len(result.errors)
    console.print(f"  Extracted {len(all_constraints)} constraints ({total_errors} errors)")

    # Merge and compute specificity
    console.print("[bold]Step 3:[/] Merging ADG with constraints...")
    pipeline = ADGPipeline()
    merged = pipeline.build_seed(adg, all_constraints, project_root=repo_path)
    external_count = sum(1 for n in merged.nodes if n.kind == FQNKind.EXTERNAL)
    console.print(f"  {len(merged.constraint_edges)} constraint edges, {external_count} EXTERNAL nodes")

    # Persist to Neo4j
    console.print("[bold]Step 4:[/] Persisting to Neo4j...")
    store = GraphStore(
        uri=os.getenv("NEO4J_URI", "bolt://neo4j:7687"),
        user=os.getenv("NEO4J_USER", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD", "password"),
        database=os.getenv("NEO4J_DATABASE", "neo4j"),
    )
    store.connect()
    store.create_schema()
    deleted = store.delete_all_dismissals()  # ADR 012: seed rebuild wipes dismissals
    console.print(f"  Cleared {deleted} previous dismissal(s)")
    store.store_adg(merged)
    store.close()
    console.print(f"[bold green]Done[/] Seed built for [cyan]{repo}[/]")

@seed_app.command("restore")
def seed_restore(
    repo: str = typer.Option(..., "--repo", "-r", help="Repository ID from repos.yaml"),
) -> None:
    """Restore an ADG seed snapshot into Neo4j."""
    _get_repo(repo)
    console.print(f"[bold]Restoring[/] seed for [cyan]{repo}[/]")
    console.print("[dim]Not implemented yet.[/]")


@seed_app.command("list")
def seed_list() -> None:
    """List available seed snapshots."""
    config = load_config()
    if not config.repos:
        console.print("[yellow]No repos configured in repos.yaml[/]")
        return
    for repo in config.repos:
        console.print(f"  [cyan]{repo.id}[/] ({repo.size}) - {repo.url}")