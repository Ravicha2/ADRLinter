"""Merge Layer: unify Track A (AST) ADG with Track B (ADR symbolic constraints).

Delegates symbolic resolution to symbolic_resolver, then merges the resulting
ConstraintEdges into the ADG.
"""
from __future__ import annotations

import logging

from services.fqn import FQN
from services.models import (
    ADG,
    FQNKind,
    FQNNode,
    SymbolicConstraint,
)
from services.adg.symbolic_resolver import resolve_symbolic_constraints

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


def merge_constraints(adg: ADG, constraints: list[SymbolicConstraint]) -> ADG:
    """Unify Track A ADG + Track B symbolic constraints into a merged ADG.

    Resolves SymbolicConstraints against ADG nodes, produces ConstraintEdges,
    and adds them to the ADG along with any needed EXTERNAL nodes.
    """
    log.info("merge_constraints: merging %d symbolic constraints into ADG with %d nodes", len(constraints), len(adg.nodes))

    resolved = resolve_symbolic_constraints(constraints, adg)

    constraint_edges = [rc.constraint_edge for rc in resolved]

    # Collect all FQNs from the ADG nodes (including EXTERNAL nodes added
    # during resolution)
    all_adg_nodes = set()
    for rc in resolved:
        all_adg_nodes.add(rc.constraint_edge.subject)
        all_adg_nodes.add(rc.constraint_edge.object)

    known_fqns = {str(n.fqn) for n in adg.nodes}

    # Add EXTERNAL nodes for any remaining orphans
    orphan_fqns = sorted(all_adg_nodes - known_fqns)
    external_nodes = [
        FQNNode(
            fqn=FQN.from_dotted(fqn),
            kind=FQNKind.EXTERNAL,
            file_path="",
            line_start=-1,
            line_end=-1,
        )
        for fqn in orphan_fqns
    ]
    if external_nodes:
        log.info("merge_constraints: adding %d EXTERNAL nodes for orphans: %s", len(external_nodes), orphan_fqns)

    return ADG(
        nodes=adg.nodes + external_nodes,
        edges=adg.edges,
        constraint_edges=adg.constraint_edges + constraint_edges,
    )