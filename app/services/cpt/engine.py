from __future__ import annotations

import logging
from dataclasses import dataclass, field

from services.fqn import FQN
from services.models import ADG, ChangedFQN, ConstraintEdge, DiffResult, Edge, PredicateType
from services.cpt.resolution import Violation, resolve, suppress_outweighed_prohibits, suppress_outweighed_requires
from services.resolver import MatchStatus, fqn_matches_pattern
from collections.abc import Iterable
from collections import deque, defaultdict

log = logging.getLogger(__name__)

_PRIORITY = {MatchStatus.EXACT: 3, MatchStatus.WILDCARD: 2}


@dataclass
class CPTResult:
    violations: list[Violation] = field(default_factory=list)
    orphans: list[ConstraintEdge] = field(default_factory=list)
    self_loop_constraints: list[ConstraintEdge] = field(default_factory=list)


@dataclass
class MatchedConstraint:
    constraint: ConstraintEdge
    subject_matches: list[tuple[FQN, MatchStatus]]
    object_matches: list[tuple[FQN, MatchStatus]]


def _build_adjacency(edges: Iterable[Edge]) -> dict[str, list[Edge]]:
    adjacency: dict[str, list[Edge]] = defaultdict(list)
    for edge in edges:
        adjacency[edge.source].append(edge)
    return adjacency


def _reachable_nodes(start: str, adjacency: dict[str, list[Edge]], kinds: set[str]) -> set[str]:
    visited: set[str] = set()
    queue: deque[str] = deque([start])

    while queue:
        current = queue.popleft()
        for edge in adjacency.get(current, ()):
            if edge.kind not in kinds:
                continue
            if edge.target not in visited:
                visited.add(edge.target)
                queue.append(edge.target)

    return visited


def match_constraints(adg: ADG) -> dict[int, MatchedConstraint]:
    matched: dict[int, MatchedConstraint] = {}
    for constraint in adg.constraint_edges:
        subject_matches: list[tuple[FQN, MatchStatus]] = []
        object_matches: list[tuple[FQN, MatchStatus]] = []
        all_fqns = {node.fqn for node in adg.nodes}
        for fqn in all_fqns:
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

        kinds = {"CONTAINS", "IMPORTS", "CALLS", "INHERITS"} if pred == PredicateType.PROHIBITS_DEPENDENCY else {"CONTAINS", "CALLS"}
        label = "has dependency path to" if pred == PredicateType.PROHIBITS_DEPENDENCY else "implements"

        for subject_fqn, subject_status in matched_constraint.subject_matches:
            subject_str = str(subject_fqn)
            reachable = _reachable_nodes(subject_str, adjacency, kinds)
            for object_fqn, object_status in matched_constraint.object_matches:
                higher = subject_status if _PRIORITY[subject_status] >= _PRIORITY[object_status] else object_status
                object_str = str(object_fqn)
                if any(reachable_node == object_str or reachable_node.startswith(object_str + ".") for reachable_node in reachable):
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

            if matched_constraint.constraint.subject.endswith(".*"):
                prefix = matched_constraint.constraint.subject[:-2]
                # Check if the changed FQN falls under this wildcard prefix
                if not (changed_str == prefix or changed_str.startswith(prefix + ".")):
                    continue
                # Package-root BFS finds dependencies through sibling modules
                # For function-level FQNs that lack IMPORTS,
                # walk up to enclosing module first.
                relevant_subjects = [(changed.fqn, MatchStatus.WILDCARD)]
            else:
                relevant_subjects = [
                    (subject_fqn, subject_status) for subject_fqn, subject_status in matched_constraint.subject_matches
                    if subject_fqn == changed.fqn or changed_str.startswith(str(subject_fqn) + ".")
                ]

            if not relevant_subjects:
                continue

            kinds = {"CONTAINS", "IMPORTS", "CALLS", "INHERITS"} if pred == PredicateType.REQUIRES_DEPENDENCY else {"CONTAINS", "CALLS"}
            label = "has no dependency on any module matching" if pred == PredicateType.REQUIRES_DEPENDENCY else "does not implement any module matching"
            for subject_fqn, subject_status in relevant_subjects:
                subject_str = str(subject_fqn)
                reachable = _reachable_nodes(subject_str, adjacency, kinds)
                object_reachable = False
                for object_fqn, _ in matched_constraint.object_matches:
                    object_str = str(object_fqn)
                    for reachable_object_str in reachable:
                        if reachable_object_str == object_str or reachable_object_str.startswith(object_str + "."):
                            object_reachable = True
                            break
                    if object_reachable:
                        break
                if not object_reachable:
                    highest_status = subject_status
                    for _, object_status in matched_constraint.object_matches:
                        if _PRIORITY[object_status] > _PRIORITY[highest_status]:
                            highest_status = object_status

                    violations.append(Violation(
                        constraint=matched_constraint.constraint,
                        changed_fqn=changed.fqn,
                        matched_fqn=subject_fqn,
                        match_status=highest_status,
                        evidence=f"{subject_str} {label} {matched_constraint.constraint.object}",
                        change_type=changed.change_type,
                    ))
    return violations


def detect(diff_result: DiffResult, adg: ADG) -> CPTResult:
    adjacency = _build_adjacency(adg.edges)

    # filter self-loop constraints (subject == object), surface as informational
    self_loop_constraints: list[ConstraintEdge] = [
        constraint for constraint in adg.constraint_edges if constraint.subject == constraint.object
    ]

    if self_loop_constraints:
        log.warning(
            "detect: %d self-loop constraint(s) filtered: %s",
            len(self_loop_constraints),
            [(constraint.adr_id, constraint.subject) for constraint in self_loop_constraints],
        )

    safe_edges = [constraint for constraint in adg.constraint_edges if constraint.subject != constraint.object]
    safe_adg = ADG(nodes=adg.nodes, edges=adg.edges, constraint_edges=safe_edges)
    matched = match_constraints(safe_adg)

    all_violations: list[Violation] = []
    all_violations.extend(check_structural_predicates(matched, adjacency))
    all_violations.extend(check_change_triggered_predicates(matched, adjacency, diff_result.changed_fqns))

    violations = resolve(all_violations)

    active_requires: list[ConstraintEdge] = []
    for match_constraint in matched.values():
        if match_constraint.constraint.predicate.value.startswith("requires_"):
            active_requires.append(match_constraint.constraint)
    violations = suppress_outweighed_prohibits(violations, active_requires)

    active_prohibits: list[ConstraintEdge] = []
    for match_constraint in matched.values():
        if match_constraint.constraint.predicate.value.startswith("prohibits_"):
            active_prohibits.append(match_constraint.constraint)
    violations = suppress_outweighed_requires(violations, active_prohibits)

    orphans: list[ConstraintEdge] = []
    for constraint in adg.constraint_edges:
        if id(constraint) not in matched:
            orphans.append(constraint)

    return CPTResult(violations=violations, orphans=orphans, self_loop_constraints=self_loop_constraints)


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