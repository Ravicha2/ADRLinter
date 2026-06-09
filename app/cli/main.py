from __future__ import annotations
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from cli.config import load_config
from services.cpt import GitAdapter, process_diff
from services.models import DiffResult

console = Console()

app = typer.Typer(
    name="cpt",
    help="CPT Detection System: validate commits against ADR constraints.",
    no_args_is_help=True,
)

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

    # 3. Display result
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
    repo_cfg = _get_repo(repo)
    console.print(f"[bold]Building[/] seed for [cyan]{repo}[/]")
    console.print(f"  Repo URL : {repo_cfg.url}")
    console.print("[dim]Not implemented yet.[/]")


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