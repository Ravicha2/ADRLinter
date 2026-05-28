from __future__ import annotations

import typer
from rich.console import Console

from cli.config import load_config

console = Console()


def _get_repo(repo: str):
    config = load_config()
    try:
        return config.get_repo(repo)
    except ValueError as e:
        console.print(f"[red]Error:[/] {e}")
        raise typer.Exit(code=1)


app = typer.Typer(
    name="cpt",
    help="CPT Detection System: validate commits against ADR constraints.",
    no_args_is_help=True,
)

seed_app = typer.Typer(help="Manage ADG seed snapshots.")
app.add_typer(seed_app, name="seed")


@app.command()
def detect(
    repo: str = typer.Option(..., "--repo", "-r", help="Repository ID from repos.yaml"),
    commit: str | None = typer.Option(None, "--commit", "-c", help="Commit SHA (default: HEAD)"),
) -> None:
    """Run CPT violation detection on a repository."""
    repo_cfg = _get_repo(repo)
    console.print(f"[bold]Detecting[/] violations in [cyan]{repo}[/] (commit: {commit or 'HEAD'})")
    console.print(f"  Repo URL : {repo_cfg.url}")
    console.print(f"  ADR dir  : {repo_cfg.adr_dir}")
    console.print(f"  Size     : {repo_cfg.size}")
    console.print("[dim]Not implemented yet.[/]")


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