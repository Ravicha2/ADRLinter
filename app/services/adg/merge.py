"""Merge Layer: unify Track A (AST) ADG with Track B (ADR constraint edges)."""

from __future__ import annotations

import logging

from services.fqn import FQN
from services.models import ADG, ConstraintEdge, FQNKind, FQNNode
from services.matching import MatchResult, MatchStatus, compute_specificity, match_fqn
from dataclasses import dataclass

log = logging.getLogger(__name__)

def add_external_nodes(adg: ADG) -> ADG:
    """Create EXTERNAL nodes for import targets not defined in the repo"""
    known_fqns = {str(node.fqn) for node in adg.nodes}
    import_targets = {edge.target for edge in adg.edges if edge.kind == "IMPORTS"}

    external_fqns = sorted(import_targets - known_fqns)
    if external_fqns:
        log.info("add_external_nodes: creating %d EXTERNAL nodes for unresolved imports: %s", len(external_fqns), external_fqns)
    else:
        log.debug("add_external_nodes: no unresolved imports")
    external_nodes = [
        FQNNode(
            fqn=FQN.from_dotted(fqn),
            kind=FQNKind.EXTERNAL,
            file_path="",
            line_start=-1,
            line_end=-1,
        )
        for fqn in external_fqns
    ]

    return ADG(nodes=adg.nodes + external_nodes, edges=adg.edges, constraint_edges=adg.constraint_edges)

def merge_constraints(adg: ADG, constraints: list[ConstraintEdge]) -> ADG:
    """
    Unify Track A ADG + Track B constraint edges into a merged ADG.

    For each constraints
    1. Match subject against known FQN nodes
    2. Compute specificity
    3. Create EXTERNAL nodes for orphan referenconstraint_edges
    """
    log.info("merge_constraints: merging %d constraint edges into ADG with %d nodes", len(constraints), len(adg.nodes))
    adg = add_external_nodes(adg)

    enriched_constraint_edges: list[ConstraintEdge] = []
    orphan_fqns: set[str] = set()

    for constraint_edge in constraints:
        subject_match = match_fqn(constraint_edge.subject, adg.nodes)
        subject_specificity = compute_specificity(constraint_edge, subject_match.status)

        object_match = match_fqn(constraint_edge.object, adg.nodes)

        log.info(
            "merge_constraints: [%s] subject='%s' (%s, spec=%.1f) -> object='%s' (%s)",
            constraint_edge.adr_id, constraint_edge.subject, subject_match.status.value,
            subject_specificity, constraint_edge.object, object_match.status.value,
        )

        if subject_match.status == MatchStatus.NO_MATCH:
            orphan_fqns.add(constraint_edge.subject)
        if object_match.status == MatchStatus.NO_MATCH:
            orphan_fqns.add(constraint_edge.object)

        enriched_constraint_edges.append(ConstraintEdge(
              subject=constraint_edge.subject,
              predicate=constraint_edge.predicate,
              object=constraint_edge.object,
              justification=constraint_edge.justification,
              adr_id=constraint_edge.adr_id,
              adr_path=constraint_edge.adr_path,
              char_interval=constraint_edge.char_interval,
              specificity=subject_specificity,
          ))

    if orphan_fqns:
        log.warning("merge_constraints: %d orphan FQNs (no AST node): %s", len(orphan_fqns), sorted(orphan_fqns))
    else:
        log.info("merge_constraints: all constraint FQNs resolved, no orphans")

    external_nodes = [
        FQNNode(
            fqn=FQN.from_dotted(fqn),
            kind=FQNKind.EXTERNAL,
            file_path="",
            line_start=-1,
            line_end=-1,
        )
        for fqn in sorted(orphan_fqns) if not any(str(node.fqn) == fqn for node in adg.nodes)
    ]

    if external_nodes:
        log.info("merge_constraints: adding %d EXTERNAL nodes for orphans: %s", len(external_nodes), [str(n.fqn) for n in external_nodes])

    return ADG(
        nodes=adg.nodes + external_nodes,
        edges=adg.edges,
        constraint_edges=adg.constraint_edges + enriched_constraint_edges
    )