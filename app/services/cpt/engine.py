from __future__ import annotations

from dataclasses import dataclass, field

from services.fqn import FQN
from services.models import ADG, ChangedFQN, ConstraintEdge, DiffResult, Edge, PredicateType
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
        if current == target and current != start:
            return True
        if current in visited:
            continue
        visited.add(current)
        for edge in edges:
            if edge.source == current and edge.kind in kinds and edge.target not in visited:
                queue.append(edge.target)
                if edge.target == target:
                    return True
    
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
            if _reachable(subject_str, object_str, reachable_edges, {"IMPORTS", "CALLS", "INHERITS"}):
                violated = True
                evidence = f"{subject_str} has dependency path to {object_str}"

        elif pred == PredicateType.PROHIBITS_IMPLEMENTATION:
            if _reachable(subject_str, object_str, reachable_edges, {"CONTAINS", "CALLS"}):
                violated = True
                evidence = f"{subject_str} implements {object_str}"

        elif pred == PredicateType.REQUIRES_DEPENDENCY:

            changed_str = str(changed_fqn)
            if subject_str != changed_str and not changed_str.startswith(subject_str + "."):
                continue
            if not _reachable(subject_str, object_str, reachable_edges, {"IMPORTS", "CALLS", "INHERITS"}):
                violated = True
                evidence = f"{subject_str} has no dependency on {object_str}"

        elif pred == PredicateType.REQUIRES_IMPLEMENTATION:
            changed_str = str(changed_fqn)
            if subject_str != changed_str and not changed_str.startswith(subject_str + "."):
                continue
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

def resolve(violations: list[Violation]) -> list[Violation]:
    seen: set[tuple[str, str]] = set()
    deduped: list[Violation] = []

    for violation in violations:

        key = (violation.constraint.subject, violation.constraint.predicate, violation.constraint.object, str(violation.matched_fqn))
        if key not in seen:
            seen.add(key)
            deduped.append(violation)
    
    suppress: set[int] = set()

    for i, violation_i in enumerate(deduped):
        for j, violation_j in enumerate(deduped):
            if i == j or i in suppress or j in suppress:
                continue
            
            if violation_i.constraint.object != violation_j.constraint.object:
                continue

            violation_i_prohibit = violation_i.constraint.predicate.value.startswith("prohibits_")
            violation_i_require = violation_i.constraint.predicate.value.startswith("requires_")
            violation_j_prohibit = violation_j.constraint.predicate.value.startswith("prohibits_")
            violation_j_require = violation_j.constraint.predicate.value.startswith("requires_")

            if violation_i_prohibit and violation_j_require:
                if violation_j.constraint.specificity > violation_i.constraint.specificity:
                    suppress.add(i)
                elif violation_i.constraint.specificity > violation_j.constraint.specificity:
                    suppress.add(j)
            
            elif violation_i_require and violation_j_prohibit:
                if violation_i.constraint.specificity > violation_j.constraint.specificity:
                    suppress.add(j)
                elif violation_j.constraint.specificity > violation_i.constraint.specificity:
                    suppress.add(i)

    return [violation for i, violation in enumerate(deduped) if i not in suppress]

_PRIORITY = {MatchStatus.EXACT: 3, MatchStatus.WILDCARD: 2, MatchStatus.SEGMENT: 1}


def detect(diff_result: DiffResult, adg: ADG, k: int = 3) -> CPTResult:
    neighborhood, reachable = bfs_neighborhood(adg, diff_result.changed_fqns, k)
    retrieved_constraint_edges = retrieve_constraints(neighborhood, adg)

    active: dict[int, ConstraintEdge] = {
        id(constraint_edge.constraint): constraint_edge.constraint for constraint_edge in retrieved_constraint_edges
    }

    all_violations: list[Violation] = []

    for constraint in active.values():
        subject_matches = [
            (fqn, status) for fqn in neighborhood
            if (status := fqn_matches_pattern(fqn, constraint.subject)) != MatchStatus.NO_MATCH
        ]
        object_matches = [
            (fqn, status) for fqn in neighborhood
            if (status := fqn_matches_pattern(fqn, constraint.object)) != MatchStatus.NO_MATCH
        ]

        if not subject_matches or not object_matches:
            continue

        constraint_tuples = [
            (constraint, subject_fqn, object_fqn, subject_status if _PRIORITY[subject_status] >= _PRIORITY[object_status] else object_status)
            for subject_fqn, subject_status in subject_matches
            for object_fqn, object_status in object_matches
        ]

        for changed in diff_result.changed_fqns:
            all_violations.extend(
                check_predicates(constraint_tuples, reachable, changed.fqn, changed.change_type)
            )

    violations = resolve(all_violations)

    active_requires = [constraint for constraint in active.values() if constraint.predicate.value.startswith("requires_")]
    violations = [
        violation for violation in violations
        if not (
            violation.constraint.predicate.value.startswith("prohibits_")
            and any(
                requires.object == violation.constraint.object and requires.specificity > violation.constraint.specificity
                for requires in active_requires
            )
        )
    ]

    orphans = [constraint_edge for constraint_edge in adg.constraint_edges if id(constraint_edge) not in active]

    return CPTResult(violations=violations, orphans=orphans, neighborhood=neighborhood)