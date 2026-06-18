from __future__ import annotations

from dataclasses import dataclass, field

from services.fqn import FQN
from services.models import ADG, ChangedFQN, ConstraintEdge, DiffResult, Edge, PredicateType
from services.cpt.resolution import Violation, resolve, suppress_outweighed_prohibits
from services.resolver import MatchStatus, fqn_matches_pattern
from collections import deque, defaultdict

_PRIORITY = {MatchStatus.EXACT: 3, MatchStatus.WILDCARD: 2}


@dataclass
class CPTResult:
    violations: list[Violation] = field(default_factory=list)
    orphans: list[ConstraintEdge] = field(default_factory=list)
    neighborhood: set[FQN] = field(default_factory=set)


@dataclass
class MatchedConstraint:
    constraint: ConstraintEdge
    subject_matches: list[tuple[FQN, MatchStatus]]
    object_matches: list[tuple[FQN, MatchStatus]]


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


def bfs_neighborhood(adg: ADG, changed_fqns: list[ChangedFQN], k: int = 3) -> tuple[set[FQN], set[Edge]]:
    by_source: dict[str, list[Edge]] = defaultdict(list)
    by_target: dict[str, list[Edge]] = defaultdict(list)
    for edge in adg.edges:
        by_source[edge.source].append(edge)
        by_target[edge.target].append(edge)

    neighborhood: set[FQN] = set()
    current_hop: set[FQN] = {changed_fqn.fqn for changed_fqn in changed_fqns}
    neighborhood |= current_hop

    for _ in range(k):
        next_hop: set[FQN] = set()
        for fqn in current_hop:
            fqn_str = str(fqn)
            for edge in by_source.get(fqn_str, ()):
                target_fqn = FQN.from_dotted_safe(edge.target)
                if target_fqn and target_fqn not in neighborhood:
                    next_hop.add(target_fqn)
            for edge in by_target.get(fqn_str, ()):
                source_fqn = FQN.from_dotted_safe(edge.source)
                if source_fqn and source_fqn not in neighborhood:
                    next_hop.add(source_fqn)
        if not next_hop:
            break
        neighborhood |= next_hop
        current_hop = next_hop

    # Collect ALL edges whose both endpoints are in neighborhood
    neighborhood_strs = {str(f) for f in neighborhood}
    reachable: set[Edge] = set()
    for edge in adg.edges:
        if edge.source in neighborhood_strs and edge.target in neighborhood_strs:
            reachable.add(edge)

    return neighborhood, reachable


def match_constraints(neighborhood: set[FQN], adg: ADG) -> dict[int, MatchedConstraint]:
    matched: dict[int, MatchedConstraint] = {}
    for constraint in adg.constraint_edges:
        subject_matches: list[tuple[FQN, MatchStatus]] = []
        object_matches: list[tuple[FQN, MatchStatus]] = []
        for fqn in neighborhood:
            subj_status = fqn_matches_pattern(fqn, constraint.subject)
            if subj_status != MatchStatus.NO_MATCH:
                subject_matches.append((fqn, subj_status))
            obj_status = fqn_matches_pattern(fqn, constraint.object)
            if obj_status != MatchStatus.NO_MATCH:
                object_matches.append((fqn, obj_status))
        # Skip constraints where either bucket is empty (orphan)
        if subject_matches and object_matches:
            matched[id(constraint)] = MatchedConstraint(
                constraint=constraint,
                subject_matches=subject_matches,
                object_matches=object_matches,
            )
    return matched


def check_structural_predicates(
    matched_constraints: dict[int, MatchedConstraint],
    adjacency: dict[str, list[Edge]],
) -> list[Violation]:
    """PROHIBITS_*: evaluate once per constraint, no changed_fqn needed."""
    violations: list[Violation] = []
    for matched_constraint in matched_constraints.values():
        pred = matched_constraint.constraint.predicate

        if pred not in (PredicateType.PROHIBITS_DEPENDENCY, PredicateType.PROHIBITS_IMPLEMENTATION):
            continue

        kinds = {"IMPORTS", "CALLS", "INHERITS"} if pred == PredicateType.PROHIBITS_DEPENDENCY else {"CONTAINS", "CALLS"}
        label = "has dependency path to" if pred == PredicateType.PROHIBITS_DEPENDENCY else "implements"

        for subject_fqn, subject_status in matched_constraint.subject_matches:
            for object_fqn, object_status in matched_constraint.object_matches:
                higher = subject_status if _PRIORITY[subject_status] >= _PRIORITY[object_status] else object_status
                subject_str = str(subject_fqn)
                object_str = str(object_fqn)
                if _reachable(subject_str, object_str, adjacency, kinds):
                    violations.append(Violation(
                        constraint=matched_constraint.constraint,
                        changed_fqn=subject_fqn, 
                        matched_fqn=subject_fqn,
                        match_status=higher,
                        evidence=f"{subject_str} {label} {object_str}",
                        change_type="structural",
                    ))
    return violations


def check_change_triggered_predicates(
    matched_constraints: dict[int, MatchedConstraint],
    adjacency: dict[str, list[Edge]],
    changed_fqns: list[ChangedFQN],
) -> list[Violation]:
    """REQUIRES_*: evaluate per changed_fqn, pre-filtered by subject_matches."""
    violations: list[Violation] = []
    for changed in changed_fqns:
        changed_str = str(changed.fqn)
        for matched_constraint in matched_constraints.values():
            pred = matched_constraint.constraint.predicate
            if pred not in (PredicateType.REQUIRES_DEPENDENCY, PredicateType.REQUIRES_IMPLEMENTATION):
                continue

            relevant_subjects = [
                (subject_fqn, subject_status) for subject_fqn, subject_status in matched_constraint.subject_matches
                if subject_fqn == changed.fqn or changed_str.startswith(str(subject_fqn) + ".")
            ]

            if not relevant_subjects:
                continue

            kinds = {"IMPORTS", "CALLS", "INHERITS"} if pred == PredicateType.REQUIRES_DEPENDENCY else {"CONTAINS", "CALLS"}
            label = "has no dependency on" if pred == PredicateType.REQUIRES_DEPENDENCY else "does not implement"

            for subject_fqn, subject_status in relevant_subjects:
                for object_fqn, object_status in matched_constraint.object_matches:
                    higher = subject_status if _PRIORITY[subject_status] >= _PRIORITY[object_status] else object_status
                    subject_str = str(subject_fqn)
                    object_str = str(object_fqn)
                    if not _reachable(subject_str, object_str, adjacency, kinds):
                        violations.append(Violation(
                            constraint=matched_constraint.constraint,
                            changed_fqn=changed.fqn,
                            matched_fqn=subject_fqn,
                            match_status=higher,
                            evidence=f"{subject_str} {label} {object_str}",
                            change_type=changed.change_type,
                        ))
    return violations


def detect(diff_result: DiffResult, adg: ADG, k: int = 3) -> CPTResult:
    neighborhood, reachable = bfs_neighborhood(adg, diff_result.changed_fqns, k)
    adjacency = _build_adjacency(reachable)
    matched = match_constraints(neighborhood, adg)

    all_violations: list[Violation] = []
    all_violations.extend(check_structural_predicates(matched, adjacency))
    all_violations.extend(check_change_triggered_predicates(matched, adjacency, diff_result.changed_fqns))

    violations = resolve(all_violations)

    active_requires: list[ConstraintEdge] = [
        mc.constraint for mc in matched.values()
        if mc.constraint.predicate.value.startswith("requires_")
    ]
    violations = suppress_outweighed_prohibits(violations, active_requires)

    orphans: list[ConstraintEdge] = [
        c for c in adg.constraint_edges if id(c) not in matched
    ]

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