from __future__ import annotations

from dataclasses import dataclass, field

from services.fqn import FQN
from services.models import ADG, ChangedFQN, ConstraintEdge, DiffResult, Edge, PredicateType
from services.cpt.resolution import Violation, resolve, suppress_outweighed_prohibits
from services.matching import MatchStatus, fqn_matches_pattern
from collections import deque, defaultdict

_PRIORITY = {MatchStatus.EXACT: 3, MatchStatus.WILDCARD: 2, MatchStatus.SEGMENT: 1}

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
    

def _build_adjacency(edges: set[Edge]) -> dict[str, list[Edge]]:
    adjacency: dict[str, list[Edge]] = defaultdict(list)
    for edge in edges:
        adjacency[edge.source].append(edge)
    return adjacency


def _reachable(start: str, target: str, adjacency: dict[str, list[Edge]], kinds: set[str]) -> bool:
    visited: set[str] = {start}
    queue: deque[str] = deque([start])

    while queue:
        current = queue.popleft()
        for edge in adjacency.get(current, ()):
            if edge.kind not in kinds:
                continue
            if edge.target == target:
                return True
            if edge.target not in visited:
                visited.add(edge.target)
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
    adjacency: dict[str, list[Edge]],
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
            if _reachable(subject_str, object_str, adjacency, {"IMPORTS", "CALLS", "INHERITS"}):
                violated = True
                evidence = f"{subject_str} has dependency path to {object_str}"

        elif pred == PredicateType.PROHIBITS_IMPLEMENTATION:
            if _reachable(subject_str, object_str, adjacency, {"CONTAINS", "CALLS"}):
                violated = True
                evidence = f"{subject_str} implements {object_str}"

        elif pred == PredicateType.REQUIRES_DEPENDENCY:
            changed_str = str(changed_fqn)
            if subject_str != changed_str and not changed_str.startswith(subject_str + "."):
                continue
            if not _reachable(subject_str, object_str, adjacency, {"IMPORTS", "CALLS", "INHERITS"}):
                violated = True
                evidence = f"{subject_str} has no dependency on {object_str}"

        elif pred == PredicateType.REQUIRES_IMPLEMENTATION:
            changed_str = str(changed_fqn)
            if subject_str != changed_str and not changed_str.startswith(subject_str + "."):
                continue
            if not _reachable(subject_str, object_str, adjacency, {"CONTAINS", "CALLS"}):
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

def detect(diff_result: DiffResult, adg: ADG, k: int = 3) -> CPTResult:
    neighborhood, reachable = bfs_neighborhood(adg, diff_result.changed_fqns, k)
    adjacency = _build_adjacency(reachable)
    retrieved_constraint_edges = retrieve_constraints(neighborhood, adg)

    active: dict[int, ConstraintEdge] = {}
    for constraint_edge in retrieved_constraint_edges:
        active[id(constraint_edge.constraint)] = constraint_edge.constraint

    all_violations: list[Violation] = []

    for constraint in active.values():
        subject_matches: list[tuple[FQN, MatchStatus]] = []
        for fqn in neighborhood:
            status = fqn_matches_pattern(fqn, constraint.subject)
            if status != MatchStatus.NO_MATCH:
                subject_matches.append((fqn, status))

        object_matches: list[tuple[FQN, MatchStatus]] = []
        for fqn in neighborhood:
            status = fqn_matches_pattern(fqn, constraint.object)
            if status != MatchStatus.NO_MATCH:
                object_matches.append((fqn, status))

        if not subject_matches or not object_matches:
            continue

        constraint_tuples: list[tuple[ConstraintEdge, FQN, FQN, MatchStatus]] = []
        for subject_fqn, subject_status in subject_matches:
            for object_fqn, object_status in object_matches:
                higher_status = subject_status if _PRIORITY[subject_status] >= _PRIORITY[object_status] else object_status
                constraint_tuples.append((constraint, subject_fqn, object_fqn, higher_status))

        for changed in diff_result.changed_fqns:
            all_violations.extend(
                check_predicates(constraint_tuples, adjacency, changed.fqn, changed.change_type)
            )

    violations = resolve(all_violations)

    active_requires: list[ConstraintEdge] = []
    for constraint in active.values():
        if constraint.predicate.value.startswith("requires_"):
            active_requires.append(constraint)
    violations = suppress_outweighed_prohibits(violations, active_requires)

    orphans: list[ConstraintEdge] = []
    for constraint_edge in adg.constraint_edges:
        if id(constraint_edge) not in active:
            orphans.append(constraint_edge)

    return CPTResult(violations=violations, orphans=orphans, neighborhood=neighborhood)


if __name__ == "__main__":
    from services.models import ADG, ChangedFQN, ConstraintEdge, DiffResult, Edge, PredicateType

    adg = ADG(
        nodes=[],
        edges=[
            Edge(source="app.service.UserService", target="app.repo.UserRepo", kind="CALLS"),
            Edge(source="app.service.UserService", target="app.repo.UserRepo", kind="IMPORTS"),
        ],
        constraint_edges=[
            ConstraintEdge(
                subject="app.service.*",
                predicate=PredicateType.PROHIBITS_DEPENDENCY,
                object="app.repo.*",
                justification="Services must not depend on repositories directly",
                adr_id="ADR-001",
                adr_path="docs/adr/001.md",
            ),
        ],
    )

    diff = DiffResult(
        commit_sha="abc123",
        changed_fqns=[
            ChangedFQN(
                fqn=FQN.from_dotted_safe("app.service.UserService"),
                change_type="modified",
                file_path="app/service.py",
                enclosing_module=FQN.from_dotted_safe("app.service"),
            ),
        ],
    )

    result = detect(diff, adg)
    for v in result.violations:
        print(f"  {v.constraint.predicate.value}: {v.evidence}")
    print(f"Neighborhood: {result.neighborhood}")