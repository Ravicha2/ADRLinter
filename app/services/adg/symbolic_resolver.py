"""Symbolic Constraint Resolver (ADR 008).

Resolves SymbolicConstraints against the ADG via substring matching and
kind-filtered CONTAINS walks, producing ResolvedConstraints ready for merge.
"""
from __future__ import annotations

import logging

from services.fqn import FQN
from services.models import (
    ADG,
    ConstraintEdge,
    FQNKind,
    FQNNode,
    OBJECT_KINDS,
    PredicateType,
    ResolvedConstraint,
    SymbolicConstraint,
    SUBJECT_KINDS,
)

log = logging.getLogger(__name__)


def _kind_filter(nodes: list[FQNNode], kinds: set[str]) -> list[FQNNode]:
    """Keep nodes whose FQNKind matches one of the allowed kinds."""
    return [n for n in nodes if n.kind.value in kinds]


def _general_match(role_general: str, candidates: list[FQNNode]) -> list[FQNNode]:
    """Exact or wildcard match role_general against module FQNs.

    role_general is a bare module name like 'app.services' or 'flask'.
    A node matches if its FQN equals role_general or starts with role_general + '.'.
    """
    matched = []
    for node in candidates:
        fqn_str = str(node.fqn)
        if fqn_str == role_general or fqn_str.startswith(role_general + "."):
            matched.append(node)
    return matched


def _walk_contains(fqn: FQN, edges: list, all_nodes: list[FQNNode]) -> list[FQNNode]:
    """Walk CONTAINS edges from a parent FQN to find direct children."""
    child_fqns = {
        edge.target for edge in edges
        if edge.kind == "CONTAINS" and edge.source == str(fqn)
    }
    return [n for n in all_nodes if str(n.fqn) in child_fqns]


def _specific_narrow(role_specific: str, candidates: list[FQNNode]) -> list[FQNNode]:
    """Substring-match role_specific against the last segment of candidate FQNs.

    Priority: exact > prefix overlap > substring containment.
    """
    if not candidates:
        return []

    exact = []
    prefix = []
    substring = []

    for node in candidates:
        short_name = node.fqn.parts[-1] if node.fqn.parts else ""
        if short_name == role_specific:
            exact.append(node)
        elif short_name.startswith(role_specific) or role_specific.startswith(short_name):
            prefix.append(node)
        elif role_specific in short_name:
            substring.append(node)

    return exact or prefix or substring


def _resolve_side(
    role_general: str,
    role_specific: str,
    kinds: set[str],
    adg: ADG,
) -> tuple[list[FQNNode], str]:
    """Resolve one side (subject or object) of a SymbolicConstraint.

    Returns (matched_nodes, match_source) where match_source is one of:
      "specific" | "general_wildcard" | "fallback" | "no_match"
    """
    kind_filtered = _kind_filter(adg.nodes, kinds)

    # Step 2: general match
    general_matches = _general_match(role_general, kind_filtered)

    if general_matches:
        # Step 3: walk CONTAINS children and narrow by role_specific
        children = []
        for parent in general_matches:
            children.extend(_walk_contains(parent.fqn, adg.edges, adg.nodes))
        children_kind = _kind_filter(children, kinds)

        if children_kind and role_specific:
            narrowed = _specific_narrow(role_specific, children_kind)
            if narrowed:
                return narrowed, "specific"

        # No specific narrowing needed or possible; use general wildcard
        return general_matches, "general_wildcard"

    # Step 5: fallback - substring-match role_specific against all module segments
    if role_specific:
        all_kind = _kind_filter(adg.nodes, kinds)
        fallback = _specific_narrow(role_specific, all_kind)
        if fallback:
            return fallback, "fallback"

    return [], "no_match"


def resolve_symbolic_constraints(
    symbolic: list[SymbolicConstraint], adg: ADG
) -> list[ResolvedConstraint]:
    """Resolve SymbolicConstraints against the ADG into ResolvedConstraints.

    For each SymbolicConstraint:
    1. Kind filter both sides using SUBJECT_KINDS/OBJECT_KINDS
    2. General match role_general against ADG nodes
    3. Walk CONTAINS and specific narrow with role_specific
    4. Fallback: substring match role_specific
    5. No match: skip and log

    External dependencies (dependency predicates with no ADG match) create
    EXTERNAL nodes directly.
    """
    from services.adg.merge import add_external_nodes

    adg = add_external_nodes(adg)
    resolved: list[ResolvedConstraint] = []

    for sc in symbolic:
        pred_value = sc.predicate.value
        subject_kinds = SUBJECT_KINDS.get(pred_value, {"module"})
        object_kinds = OBJECT_KINDS.get(pred_value, {"module"})

        subject_nodes, subject_source = _resolve_side(
            sc.subject_role_general, sc.subject_role_specific, subject_kinds, adg,
        )
        object_nodes, object_source = _resolve_side(
            sc.object_role_general, sc.object_role_specific, object_kinds, adg,
        )

        # External dependency shortcut: if object has no ADG match and this is
        # a dependency predicate, create an EXTERNAL node
        if not object_nodes and pred_value in ("requires_dependency", "prohibits_dependency"):
            ext_fqn = FQN.from_dotted(sc.object_role_general)
            ext_node = FQNNode(
                fqn=ext_fqn,
                kind=FQNKind.EXTERNAL,
                file_path="",
                line_start=-1,
                line_end=-1,
            )
            adg = ADG(
                nodes=adg.nodes + [ext_node],
                edges=adg.edges,
                constraint_edges=adg.constraint_edges,
            )
            object_nodes = [ext_node]
            object_source = "external"

        if not subject_nodes:
            log.warning(
                "resolve: [%s] subject '%s'/%s matched nothing, skipping",
                sc.adr_id, sc.subject_role_general, sc.subject_role_specific,
            )
            continue

        if not object_nodes:
            log.warning(
                "resolve: [%s] object '%s'/%s matched nothing, skipping",
                sc.adr_id, sc.object_role_general, sc.object_role_specific,
            )
            continue

        # ponytail: simplest resolution - one edge per (subject, object) pair
        subject_fqns = sorted({str(n.fqn) for n in subject_nodes})
        object_fqns = sorted({str(n.fqn) for n in object_nodes})

        for subj_fqn in subject_fqns:
            for obj_fqn in object_fqns:
                # Skip self-loops
                if subj_fqn == obj_fqn:
                    continue
                edge = ConstraintEdge(
                    subject=subj_fqn,
                    predicate=sc.predicate,
                    object=obj_fqn,
                    justification=sc.justification,
                    adr_id=sc.adr_id,
                    adr_path=sc.adr_path,
                )
                resolved.append(ResolvedConstraint(
                    constraint_edge=edge,
                    subject_matched_by=subject_source,
                    object_matched_by=object_source,
                ))

        log.info(
            "resolve: [%s] %s/%s -[%s]-> %s/%s  (subjects=%s, objects=%s)",
            sc.adr_id,
            sc.subject_role_general, sc.subject_role_specific,
            sc.predicate.value,
            sc.object_role_general, sc.object_role_specific,
            subject_source, object_source,
        )

    return resolved