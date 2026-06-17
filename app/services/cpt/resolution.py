from __future__ import annotations

from dataclasses import dataclass

from services.fqn import FQN
from services.matching import MatchStatus
from services.models import ConstraintEdge


@dataclass
class Violation:
    constraint: ConstraintEdge
    changed_fqn: FQN
    matched_fqn: FQN
    match_status: MatchStatus
    evidence: str
    change_type: str


def resolve(violations: list[Violation]) -> list[Violation]:
    """Deduplicate violations and suppress lower-specificity conflicts."""
    seen: set[tuple[str, str]] = set()
    deduped: list[Violation] = []

    for violation in violations:
        key = (violation.constraint.subject, violation.constraint.predicate, violation.constraint.object, str(violation.matched_fqn))
        if key not in seen:
            seen.add(key)
            deduped.append(violation)

    # Module-level dedup: parent matched_fqn covers child for same constraint
    # O(n²) per constraint group, fine for typical violation counts
    by_constraint: dict[tuple, list[int]] = {}
    for i, v in enumerate(deduped):
        ck = (v.constraint.subject, v.constraint.predicate, v.constraint.object)
        by_constraint.setdefault(ck, []).append(i)

    to_remove: set[int] = set()
    for indices in by_constraint.values():
        for i in indices:
            if i in to_remove:
                continue
            i_prefix = str(deduped[i].matched_fqn) + "."
            for j in indices:
                if j != i and j not in to_remove and str(deduped[j].matched_fqn).startswith(i_prefix):
                    to_remove.add(j)

    deduped = [v for i, v in enumerate(deduped) if i not in to_remove]

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


def suppress_outweighed_prohibits(
    violations: list[Violation],
    active_requires: list[ConstraintEdge],
) -> list[Violation]:
    """Remove prohibits violations outweighed by a higher-specificity requires on the same object."""
    return [
        violation for violation in violations
        if not (
            violation.constraint.predicate.value.startswith("prohibits_")
            and any(
                requires.object == violation.constraint.object
                and requires.specificity > violation.constraint.specificity
                for requires in active_requires
            )
        )
    ]