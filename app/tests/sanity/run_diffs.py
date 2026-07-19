"""Run CPT detect on synthetic diff JSON files using the seeded Neo4j ADG.

Usage: uv run python run_diffs.py <diff_dir>
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from rich.console import Console
from rich.table import Table

from services.cpt import process_diff
from services.graph.connector import GraphStore
from services.models import Diff, FileChange
from services.pipeline import ADGPipeline, PipelineInputs

console = Console()

DIFF_DIR = Path(sys.argv[1] if len(sys.argv) > 1 else "../dataset-ADRLinter/small/incoming_diff")


def load_diff(path: Path) -> Diff:
    data = json.loads(path.read_text())
    return Diff(
        to_sha=data["commit_sha"],
        from_sha=data.get("parent_sha"),
        changed_files=[
            FileChange(path=f["path"], status=f["status"], old_path=f.get("old_path"))
            for f in data["changed_files"]
        ],
        file_contents={k: v.encode() for k, v in data.get("file_contents", {}).items()},
        from_contents={k: v.encode() for k, v in data.get("parent_contents", {}).items()},
    )


def main() -> None:
    store = GraphStore(
        uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        user=os.getenv("NEO4J_USER", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD", "password"),
        database=os.getenv("NEO4J_DATABASE", "neo4j"),
    )
    store.connect()
    console.print("[bold]Loading ADG from Neo4j...[/]")
    adg = store.load_adg()
    console.print(f"  {len(adg.nodes)} nodes, {len(adg.edges)} edges, {len(adg.constraint_edges)} constraints")
    for c in adg.constraint_edges:
        if "0010" in c.adr_id or "0012" in c.adr_id:
            console.print(f"  Constraint: {c.adr_id} | {c.subject} | {c.predicate.value} | {c.object}")
    store.close()

    for diff_path in sorted(DIFF_DIR.glob("*.json")):
        console.print()
        console.print(f"[bold cyan]=== {diff_path.name} ===[/]")
        data = json.loads(diff_path.read_text())
        console.print(f"[dim]{data.get('description', '')}[/]")
        console.print(f"[dim]expected_violation={data.get('expected_violation')} expected_adrs={data.get('expected_adrs')}[/]")

        diff = load_diff(diff_path)
        diff_result = process_diff(diff)

        if not diff_result.changed_fqns:
            console.print("[yellow]No changed FQNs detected.[/]")
            continue

        fqn_table = Table(title="Changed FQNs", show_lines=True)
        fqn_table.add_column("FQN", style="cyan")
        fqn_table.add_column("Change", style="green")
        for cf in diff_result.changed_fqns:
            fqn_table.add_row(str(cf.fqn), cf.change_type)
        console.print(fqn_table)

        # Run pipeline (merge + specificity + augment + detect)
        pipeline = ADGPipeline()
        pipeline_inputs = PipelineInputs(
            adg=adg,
            constraints=[],  # constraints already in ADG from seed
            diff_result=diff_result,
            diff=diff,
        )
        cpt_result = pipeline.run_prepared(pipeline_inputs)


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
            o_table = Table(title="Orphan Constraints", show_lines=True)
            o_table.add_column("ADR", style="cyan")
            o_table.add_column("Predicate", style="dim")
            o_table.add_column("Subject", style="dim")
            o_table.add_column("Object", style="dim")
            for c in cpt_result.orphans:
                o_table.add_row(c.adr_id, c.predicate.value, c.subject, c.object)
            console.print(o_table)


if __name__ == "__main__":
    main()