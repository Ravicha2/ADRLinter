from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

import typer
from rich.console import Console
from rich.table import Table

from cli.config import load_config
from services.adg import parse_repo
from services.models import FQNKind, SymbolicConstraint
from services.cpt import GitAdapter, process_diff
from services.extract import extract_all_adrs
from services.extract.engine import derive_package_context
from services.graph.connector import GraphStore
from services.models import DiffResult, FQNKind
from services.pipeline import ADGPipeline, PipelineInputs

console = Console()

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

@app.command()
def detect(
    repo: str = typer.Option(..., "--repo", "-r", help="Repository ID from repos.yaml"),
    commit: str | None = typer.Option(None, "--commit", "-c", help="Commit SHA (default: HEAD)"),
) -> None:
    """Run CPT violation detection on a repository."""
    config = load_config()
    repo_cfg = _get_repo(repo)
    repo_path = _resolve_repo_path(repo_cfg)
    
    if not repo_path.exists():
        console.print(f"[red]Error:[/] Repository path does not exist: {repo_path}")
        raise typer.Exit(code=1)
    
    console.print(f"[bold]Detecting[/] violations in [cyan]{repo}[/] (commit: {commit or 'HEAD'})")

    # 1. Fetch the commit diff from git
    adapter = GitAdapter()
    try:
        commit_diff = adapter.get_commit_diff(repo_path, commit_sha=commit)
    except ValueError as e:
        console.print(f"[red]Git error:[/] {e}")
        raise typer.Exit(code=1)

    # 2. Process the diff to idenity changed FQN
    result: DiffResult = process_diff(commit_diff)

    # 3. Display diff result
    console.print()
    console.print(f"[bold]Commit:[/] {commit_diff.commit_sha[:6]}...")
    if commit_diff.parent_sha is not None:
        console.print(f"[bold]Parent:[/] {commit_diff.parent_sha[:6]}...")

    if result.changed_files:
        file_table = Table(title="Changed Files", show_lines=True)
        file_table.add_column("Path", style="cyan")
        file_table.add_column("Status", style="green")
        for file_changed in result.changed_files:
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
    else:
        console.print("[dim]No FQN changes detected.[/]")

    # 4. Build ADG and run CPT detection
    console.print()
    console.print("[bold]Building ADG...[/]")
    adg = parse_repo(repo_path)
    package_context = derive_package_context(adg)

    console.print("[bold]Extracting ADR constraints...[/]")
    all_constraints: list[SymbolicConstraint] = []
    for ext_result in extract_all_adrs(repo_path, repo_cfg.adr_dir, config.langextract, package_context=package_context):
        all_constraints.extend(ext_result.constraints)

    # 5. Pipeline: merge, compute specificity, augment, detect
    pipeline = ADGPipeline()
    pipeline_inputs = PipelineInputs(
        adg=adg,
        constraints=all_constraints,
        diff_result=result,
        commit_diff=commit_diff,
        project_root=repo_path,
    )
    cpt_result = pipeline.run_prepared(pipeline_inputs)

    console.print(f"  ADG: {len(adg.nodes)} nodes, {len(adg.edges)} edges, {len(all_constraints)} constraints")

    # 6. Display violations
    if cpt_result.violations:
        v_table = Table(title="Violations", show_lines=True)
        v_table.add_column("ADR", style="cyan")
        v_table.add_column("Predicate", style="bold red")
        v_table.add_column("Subject", style="yellow")
        v_table.add_column("Object", style="yellow")
        v_table.add_column("Changed FQN", style="green")
        v_table.add_column("Evidence", style="dim")
        for v in cpt_result.violations:
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

    if cpt_result.orphans:
        o_table = Table(title="Orphan Constraints (no neighborhood match)", show_lines=True)
        o_table.add_column("ADR", style="cyan")
        o_table.add_column("Predicate", style="dim")
        o_table.add_column("Subject", style="dim")
        o_table.add_column("Object", style="dim")
        for c in cpt_result.orphans:
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