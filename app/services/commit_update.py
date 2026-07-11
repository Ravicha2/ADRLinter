"""Commit update orchestration: full structural rebuild preserving constraints and dismissals.

Per ADR 013: wipe structural data, re-parse repo, re-insert constraints,
recompute specificity, re-run CPT detection, filter dismissals.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from services.adg.treesitter import parse_repo
from services.cpt.dismissal import Dismissal, filter_dismissed
from services.cpt.engine import CPTResult, Violation, detect as cpt_detect
from services.cpt.diff_processor import process_diff
from services.cpt.git_adapter import GitAdapter
from services.graph.connector import GraphStore
from services.models import ADG, CommitDiff, ConstraintEdge, DiffResult, FQNKind, FQNNode
from services.fqn import FQN
from services.pipeline import adg_with_specificity, augment_immutable

log = logging.getLogger(__name__)


@dataclass
class UpdateResult:
    violations: list[Violation]
    orphans: list[ConstraintEdge]
    self_loop_constraints: list[ConstraintEdge]
    changed_files: int
    dismissals_applied: int
    constraint_edges_preserved: int


def merge_preserved_constraints(adg: ADG, constraint_edges: list[ConstraintEdge], project_root: Path | None = None) -> ADG:
    """Merge preserved constraint edges into a fresh ADG.

    Creates EXTERNAL nodes for any constraint endpoint FQN not present in
    the ADG. Returns a new ADG with constraint_edges attached.

    project_root is accepted for API compatibility but not used here;
    this function replaces the LLM-based merge_constraints step with
    a direct merge of already-resolved constraint edges.
    """
    existing_fqns = {str(n.fqn) for n in adg.nodes}
    new_nodes: list[FQNNode] = []
    seen: set[str] = set()

    for ce in constraint_edges:
        for fqn_str in (ce.subject, ce.object):
            if fqn_str not in existing_fqns and fqn_str not in seen:
                new_nodes.append(FQNNode(
                    fqn=FQN.from_dotted(fqn_str),
                    kind=FQNKind.EXTERNAL,
                    file_path="",
                    line_start=-1,
                    line_end=-1,
                    start_byte=0,
                    end_byte=0,
                ))
                seen.add(fqn_str)

    return ADG(
        nodes=adg.nodes + new_nodes,
        edges=list(adg.edges),
        constraint_edges=list(constraint_edges),
    )


def commit_update(
    store: GraphStore,
    repo_path: Path,
    commit_sha: str | None = None,
) -> UpdateResult:
    """Orchestrate the full commit update flow per ADR 013.

    1. Guard: constraint edges must exist (user must run seed build first)
    2. Load dismissals
    3. Wipe structural data (constraint edges removed, caller re-inserts)
    4. Re-parse repo
    5. Store structural nodes + edges
    6. Re-insert constraint edges (MERGE finds real code nodes)
    7. Merge preserved constraints in-memory
    8. Compute specificity
    9. Get commit diff + process
    10. CPT detect
    11. Filter dismissals
    """
    # 1. Guard: constraint edges must exist
    constraint_edges = store.load_all_constraint_edges()
    if not constraint_edges:
        raise RuntimeError("No ADG found. Run 'seed build' first.")

    # 2. Load dismissals (survive the wipe because :Dismissal, not :FQNNode)
    dismissals = store.load_dismissals()

    # 3. Wipe structural data
    store.delete_structural_data()

    # 4. Re-parse repo
    adg = parse_repo(repo_path)

    # 5. Store structural nodes + edges (MERGE upgrades EXTERNAL placeholders)
    for node in adg.nodes:
        store.store_node(node)
    for edge in adg.edges:
        store.store_edge(edge)

    # 6. Re-insert constraint edges (MERGE finds real code nodes, EXTERNAL for orphans)
    for ce in constraint_edges:
        store.store_constraint_edge(ce)

    # 7. Merge preserved constraints in-memory (adds EXTERNAL nodes for orphans)
    merged = merge_preserved_constraints(adg, constraint_edges)

    # 8. Compute specificity
    merged = adg_with_specificity(merged)

    # 9. Get commit diff + process
    commit_diff = GitAdapter().get_commit_diff(repo_path, commit_sha=commit_sha)
    diff_result = process_diff(commit_diff)
    merged = augment_immutable(merged, commit_diff)

    # 10. CPT detect
    cpt_result = cpt_detect(diff_result, merged)

    # 11. Filter dismissals
    active_violations = filter_dismissed(cpt_result.violations, dismissals)

    return UpdateResult(
        violations=active_violations,
        orphans=cpt_result.orphans,
        self_loop_constraints=cpt_result.self_loop_constraints,
        changed_files=len(diff_result.changed_files),
        dismissals_applied=len(cpt_result.violations) - len(active_violations),
        constraint_edges_preserved=len(constraint_edges),
    )