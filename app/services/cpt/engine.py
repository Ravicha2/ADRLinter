from __future__ import annotations

from dataclasses import dataclass, field

from services.fqn import FQN
from services.models import ADG, ChangedFQN, ConstraintEdge, Edge, PredicateType
from services.matching import MatchStatus, fqn_matches_pattern
from collections import deque

@dataclass
class Violation:
    constraint: ConstraintEdge
    changed_fqn: FQN
    matched_fqn: FQN
    match_status: MatchStatus
    evidence: str
    change_type: str

@dataclass
class CPTResult:
    violations: list[Violation] = field(default_factory=list)
    orphans: list[ConstraintEdge] = field(default_factory=list)
    neighborhood: set[FQN] = field(default_factory=set)

@dataclass
class RetrievedConstraint:
    constraint: ConstraintEdge
    matched_fqn: FQN
    match_status: MatchStatus
    

def _reachable(start:str, target:str, edges: set[Edge], kinds: set[str]) -> bool:
    visited: set[str] = set()
    queue = deque([start])
    while queue:
        current = queue.popleft()
        if current == target:
            return True
        if current in visited:
            continue
        visited.add(current)
        for edge in edges:
            if edge.source == current and edge.kind in kinds and edge.target not in visited:
                queue.append(edge.target)
    
    return False


def bfs_neighborhood(adg: ADG, changed_fqns: list[ChangedFQN], k:int=3) -> tuple[set[FQN], set[Edge]]:
    neighborhood: set[FQN] = set()
    reachable: set[Edge] = set()
    current_hop: set[FQN] = {changed_fqn.fqn for changed_fqn in changed_fqns}
    neighborhood |= current_hop

    for _ in range(k):
        next_hop: set[FQN] = set()
        for fqn in current_hop:
            fqn_str = str(fqn)
            for edge in adg.edges:
                if edge.source == fqn_str:
                    target_fqn = FQN.from_dotted_safe(edge.target)
                    if target_fqn and target_fqn not in neighborhood:
                        next_hop.add(target_fqn)
                        reachable.add(edge)
                
                if edge.target == fqn_str:
                    source_fqn = FQN.from_dotted_safe(edge.source)
                    if source_fqn and source_fqn not in neighborhood:
                        next_hop.add(source_fqn)
                        reachable.add(edge)
        if not next_hop:
            break
        neighborhood |= next_hop
        current_hop = next_hop

    return neighborhood, reachable

def retrieve_constraints(neighborhood: set[FQN], adg: ADG) -> list[RetrievedConstraint]:
    results: list[RetrievedConstraint] = []
    for constraint in adg.constraint_edges:
        for fqn in neighborhood:
            subject = fqn_matches_pattern(fqn, constraint.subject)
            object = fqn_matches_pattern(fqn, constraint.object)
            status = subject if subject != MatchStatus.NO_MATCH else object
            if status != MatchStatus.NO_MATCH:
                results.append(RetrievedConstraint(
                    constraint=constraint, matched_fqn=fqn, match_status=status,
                ))
    return results

def check_predicates(
    constraints_with_matches: list[tuple[ConstraintEdge, FQN, FQN, MatchStatus]],
    reachable_edges: set[Edge],
    changed_fqn: FQN,
    change_type: str,
)-> list[Violation]:
    violations: list[Violation] = []
    for constraint, subject_fqn, object_fqn, match_status in constraints_with_matches:
        subject_str = str(subject_fqn)
        object_str = str(object_fqn)
        pred = constraint.predicate
        violated = False
        evidence = ""

        if pred == PredicateType.PROHIBITS_DEPENDENCY:
            if _reachable(subject_str, object_str, reachable_edges, {"IMPORTS", "CALLS", "INHERIT"}):
                violated = True
                evidence = f"{subject_str} has dependency path to {object_str}"

        elif pred == PredicateType.PROHIBITS_IMPLEMENTATION:
            if _reachable(subject_str, object_str, reachable_edges, {"CONTAINS", "CALLS"}):
                violated = True
                evidence = f"{subject_str} implements {object_str}"

        elif pred == PredicateType.REQUIRES_DEPENDENCY:
            if not _reachable(subject_str, object_str, reachable_edges, {"IMPORTS", "CALLS", "INHERIT"}):
                violated = True
                evidence = f"{subject_str} has no dependency on {object_str}"

        elif pred == PredicateType.REQUIRES_IMPLEMENTATION:
            if not _reachable(subject_str, object_str, reachable_edges, {"CONTAINS", "CALLS"}):
                violated = True
                evidence = f"{subject_str} does not implement {object_str}"
        if violated:
            violations.append(Violation(
                constraint=constraint,
                changed_fqn=changed_fqn,
                matched_fqn=subject_fqn,
                match_status=match_status,
                evidence=evidence,
                change_type=change_type,
            ))
    return violations
        