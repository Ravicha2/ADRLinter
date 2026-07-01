"""NameResolver: bare-name resolution via suffix index.

Also exposes fqn_matches_pattern and MatchStatus for the CPT engine.
"""

from __future__ import annotations

from enum import Enum

from services.fqn import FQN


class MatchStatus(Enum):
    EXACT = "exact"
    WILDCARD = "wildcard"
    NO_MATCH = "no_match"


def fqn_matches_pattern(fqn: FQN, pattern: str) -> MatchStatus:
    """check if a concrete FQN matches a constraint pattern."""
    if str(fqn) == pattern:
        return MatchStatus.EXACT
    if pattern.endswith(".*"):
        prefix = pattern[:-2]
        if str(fqn).startswith(prefix + "."):
            return MatchStatus.WILDCARD
    return MatchStatus.NO_MATCH


class NameResolver:
    """Suffix-indexed name resolver.

    Build once from known FQNs, then use resolve() for O(1) bare/dotted name lookup.
    """

    def __init__(self, fqns: set[FQN]):
        self._fqns = fqns
        self._suffix_index: dict[str, list[FQN]] = {}
        for fqn in fqns:
            parts = fqn.parts
            for i in range(len(parts)):
                suffix = ".".join(parts[i:])
                self._suffix_index.setdefault(suffix, []).append(fqn)

    def __contains__(self, fqn: FQN) -> bool:
        return fqn in self._fqns

    def resolve(self, text: str) -> FQN | None:
        """Resolve a bare or dotted name to a known FQN. via suffix index."""
        matches = self._suffix_index.get(text)
        if matches:
            return matches[0]
        return None