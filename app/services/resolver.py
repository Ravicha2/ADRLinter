"""NameResolver: consolidated name resolution and pattern matching.

Centralizes bare-name resolution (suffix index), pattern matching (exact/wildcard),
and specificity scoring. Replaces the scattered matching.py, resolve_call/resolve_base_class
in treesitter.py, and duplicated fqn_matches_pattern in engine.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from services.fqn import FQN
from services.models import FQNNode


class MatchStatus(Enum):
    EXACT = "exact"
    WILDCARD = "wildcard"
    NO_MATCH = "no_match"


@dataclass(frozen=True)
class MatchReport:
    status: MatchStatus
    matched: tuple[tuple[FQN, MatchStatus], ...] = ()
    specificity: float = 0.0


# ponytail: LLMResolver type for injectable callback, decouples openai from merge logic
LLMResolver = Callable[[str, list[FQNNode], str], str]


def fqn_matches_pattern(fqn: FQN, pattern: str) -> MatchStatus:
    """Pure function: check if a concrete FQN matches a constraint pattern."""
    if str(fqn) == pattern:
        return MatchStatus.EXACT
    if pattern.endswith(".*"):
        prefix = pattern[:-2]
        if str(fqn).startswith(prefix + "."):
            return MatchStatus.WILDCARD
    return MatchStatus.NO_MATCH


def compute_specificity(pattern: str, status: MatchStatus) -> float:
    """Compute specificity from pattern depth and match status."""
    if status == MatchStatus.NO_MATCH:
        return 0.0
    depth = len(pattern.rstrip(".").split("."))
    return float(depth) + (1.0 if status == MatchStatus.EXACT else 0.0)


def _status_priority(status: MatchStatus) -> int:
    return {MatchStatus.EXACT: 2, MatchStatus.WILDCARD: 1}.get(status, 0)


class NameResolver:
    """Suffix-indexed name resolver and pattern matcher.

    Build once from known FQNs, then use for:
    - resolve(): O(1) bare/dotted name lookup (parse time)
    - match(): pattern matching with specificity (merge/detection time)
    """

    def __init__(self, fqns: set[FQN]):
        self._fqns = fqns
        # ponytail: suffix index for O(1) lookup, first match on ambiguity
        self._suffix_index: dict[str, list[FQN]] = {}
        for fqn in fqns:
            parts = fqn.parts
            for i in range(len(parts)):
                suffix = ".".join(parts[i:])
                self._suffix_index.setdefault(suffix, []).append(fqn)

    def __contains__(self, fqn: FQN) -> bool:
        return fqn in self._fqns

    def resolve(self, text: str) -> FQN | None:
        """Resolve a bare or dotted name to a known FQN. O(1) via suffix index."""
        matches = self._suffix_index.get(text)
        if matches:
            return matches[0]
        return None

    def match(self, pattern: str, candidates: set[FQN] | None = None) -> MatchReport:
        """Match a pattern against known FQNs, returning status, matches, and specificity."""
        pool = candidates or self._fqns
        matches: list[tuple[FQN, MatchStatus]] = []
        for fqn in pool:
            status = fqn_matches_pattern(fqn, pattern)
            if status != MatchStatus.NO_MATCH:
                matches.append((fqn, status))

        if not matches:
            return MatchReport(status=MatchStatus.NO_MATCH, matched=(), specificity=0.0)

        best = max(matches, key=lambda x: _status_priority(x[1]))[1]
        matched = tuple(sorted(
            [(fqn, s) for fqn, s in matches if s == best],
            key=lambda x: str(x[0]),
        ))

        return MatchReport(
            status=best,
            matched=matched,
            specificity=compute_specificity(pattern, best),
        )