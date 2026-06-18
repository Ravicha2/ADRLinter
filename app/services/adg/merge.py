"""Merge Layer: unify Track A (AST) ADG with Track B (ADR constraint edges)."""

from __future__ import annotations

import logging
from typing import Callable

from services.fqn import FQN
from services.models import ADG, ConstraintEdge, FQNKind, FQNNode
from services.resolver import LLMResolver, MatchStatus, NameResolver
from dataclasses import dataclass
from services.extract.config import LangExtractConfig


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

def gather_candidates(pattern:str, nodes: list[FQNNode]) -> list[FQNNode]:
    """
    Collect ADG nodes as resolution candidates via prefix-scoped walk

    - Walk segment by segment until first mismatch
    - Collect all descendant of the longest matching prefix
    - if pattern matched exactly return [] (no resolution needed).
    """
    node_fqns = {str(node.fqn) for node in nodes}
    if pattern in node_fqns:
        return []

    # strip wildcard suffix for prefix matching
    prefix_pattern = pattern[:-2] if pattern.endswith(".*") else pattern
    segments = prefix_pattern.split(".")

    # walk segments to find longest matching prefix
    longest_prefix = ""
    for i in range(len(segments)):
        candidate_prefix = ".".join(segments[:i+1])
        if candidate_prefix in node_fqns:
            longest_prefix = candidate_prefix
        else:
            break

    if longest_prefix:
        candidate_nodes = []
        for node in nodes:
            if str(node.fqn) == longest_prefix or str(node.fqn).startswith(longest_prefix + "."):
                candidate_nodes.append(node)
        return candidate_nodes

    # no prefix matched, return all nodes as candidates
    return list(nodes)

def _call_resolution_llm(pattern: str, candidates: list[FQNNode], justification: str, config: LangExtractConfig) -> str:
    """Call LLM to remap an orphaned FQN pattern. return remapped or 'no_mapping'"""
    import openai

    candidate_fqns = "\n".join(f"- {node.fqn}" for node in candidates)
    prompt = (
        f'An ADR constraint references FQN pattern "{pattern}" '
        f"but this pattern doesn't match any node in the codebase.\n\n"
        f"Candidate FQNs from the codebase:\n{candidate_fqns}\n\n"
        f"Constraint justification: {justification}\n\n"
        f'Reply with the full remapped pattern (e.g. "app.routes.*") '
        f'or "no_mapping" if no mapping exists. Reply with ONLY the pattern string.'
    )

    client = openai.OpenAI(base_url=config.model_url, api_key=config.api_key)
    response = client.chat.completions.create(
        model=config.model_id,
        messages=[{"role": "user", "content": prompt}],
        temperature=config.temperature,
    )

    return response.choices[0].message.content.strip()

def _make_llm_resolver(config: LangExtractConfig) -> LLMResolver:
    """Build an LLM resolver callable from config, decoupling openai from merge logic."""
    def resolver(pattern: str, candidates: list[FQNNode], justification: str) -> str:
        return _call_resolution_llm(pattern, candidates, justification, config)
    return resolver

def resolve_orphans(adg: ADG, constraints: list[ConstraintEdge], resolver: NameResolver, llm_resolver: LLMResolver | None = None) -> set[str]:
    """
    LLM-backed naming resolution for orphan FQN patterns.

    identify orphans, gather candidates, call LLM, remap in-place
    return set of remaining orphan FQNs.
    """
    remaining_orphans: set[str] = set()

    for constraint in constraints:
        for side in ("subject", "object"):
            pattern = getattr(constraint, side)
            report = resolver.match(pattern)
            if report.status != MatchStatus.NO_MATCH:
                continue

            if llm_resolver is None:
                remaining_orphans.add(pattern)
                continue

            candidates = gather_candidates(pattern, adg.nodes)
            if not candidates:
                remaining_orphans.add(pattern)
                continue

            remapped = llm_resolver(pattern, candidates, constraint.justification)
            if remapped == "no_mapping":
                remaining_orphans.add(pattern)
                continue

            setattr(constraint, side, remapped)

    return remaining_orphans

def merge_constraints(adg: ADG, constraints: list[ConstraintEdge], config: LangExtractConfig | None = None) -> ADG:
    """
    Unify Track A ADG + Track B constraint edges into a merged ADG.

    For each constraints
    1. Match subject against known FQN nodes
    2. Compute specificity
    3. Create EXTERNAL nodes for orphan referenconstraint_edges
    """
    log.info("merge_constraints: merging %d constraint edges into ADG with %d nodes", len(constraints), len(adg.nodes))
    adg = add_external_nodes(adg)
    resolver = NameResolver({n.fqn for n in adg.nodes})

    if config is not None:
        resolve_orphans(adg, constraints, resolver, llm_resolver=_make_llm_resolver(config))

    enriched_constraint_edges: list[ConstraintEdge] = []
    orphan_fqns: set[str] = set()

    for constraint_edge in constraints:
        subject_report = resolver.match(constraint_edge.subject)
        object_report = resolver.match(constraint_edge.object)

        log.info(
            "merge_constraints: [%s] subject='%s' (%s, spec=%.1f) -> object='%s' (%s)",
            constraint_edge.adr_id, constraint_edge.subject, subject_report.status.value,
            subject_report.specificity, constraint_edge.object, object_report.status.value,
        )

        if subject_report.status == MatchStatus.NO_MATCH:
            orphan_fqns.add(constraint_edge.subject)
        if object_report.status == MatchStatus.NO_MATCH:
            orphan_fqns.add(constraint_edge.object)

        enriched_constraint_edges.append(ConstraintEdge(
              subject=constraint_edge.subject,
              predicate=constraint_edge.predicate,
              object=constraint_edge.object,
              justification=constraint_edge.justification,
              adr_id=constraint_edge.adr_id,
              adr_path=constraint_edge.adr_path,
              char_interval=constraint_edge.char_interval,
              specificity=subject_report.specificity,
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