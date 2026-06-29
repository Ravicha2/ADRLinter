"""Tests for the resolver module: NameResolver, MatchReport, fqn_matches_pattern, compute_specificity.

Boundary tests consolidating logic previously in matching.py and treesitter.py.
"""

from __future__ import annotations

import pytest

from services.fqn import FQN
from services.resolver import (
    MatchReport,
    MatchStatus,
    NameResolver,
    compute_specificity,
    fqn_matches_pattern,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_fqns() -> set[FQN]:
    return {
        FQN.from_dotted("app"),
        FQN.from_dotted("app.api"),
        FQN.from_dotted("app.api.users"),
        FQN.from_dotted("app.api.orders"),
        FQN.from_dotted("app.auth"),
        FQN.from_dotted("app.auth.middleware"),
        FQN.from_dotted("app.services"),
        FQN.from_dotted("app.services.user"),
    }


@pytest.fixture
def resolver(sample_fqns: set[FQN]) -> NameResolver:
    return NameResolver(sample_fqns)


# ===========================================================================
# 1. fqn_matches_pattern: exact match
# ===========================================================================


class TestFqnMatchesPatternExact:
    def test_exact_match_module(self) -> None:
        result = fqn_matches_pattern(FQN.from_dotted("app.auth.middleware"), "app.auth.middleware")
        assert result == MatchStatus.EXACT

    def test_exact_match_root(self) -> None:
        result = fqn_matches_pattern(FQN.from_dotted("app"), "app")
        assert result == MatchStatus.EXACT

    def test_exact_mismatch(self) -> None:
        result = fqn_matches_pattern(FQN.from_dotted("app.auth.middleware"), "app.middleware.auth")
        assert result == MatchStatus.NO_MATCH


# ===========================================================================
# 2. fqn_matches_pattern: wildcard match
# ===========================================================================


class TestFqnMatchesPatternWildcard:
    def test_wildcard_direct_child(self) -> None:
        result = fqn_matches_pattern(FQN.from_dotted("app.api.users"), "app.api.*")
        assert result == MatchStatus.WILDCARD

    def test_wildcard_deep_child(self) -> None:
        result = fqn_matches_pattern(FQN.from_dotted("app.api.v1.users"), "app.api.*")
        assert result == MatchStatus.WILDCARD

    def test_wildcard_not_child(self) -> None:
        result = fqn_matches_pattern(FQN.from_dotted("app.services.user"), "app.api.*")
        assert result == MatchStatus.NO_MATCH

    def test_wildcard_prefix_itself_not_match(self) -> None:
        result = fqn_matches_pattern(FQN.from_dotted("app.api"), "app.api.*")
        assert result != MatchStatus.WILDCARD


# ===========================================================================
# 3. fqn_matches_pattern: no match (segment matching removed)
# ===========================================================================


class TestFqnMatchesPatternNoMatch:
    def test_completely_unrelated(self) -> None:
        result = fqn_matches_pattern(FQN.from_dotted("app.db.postgres"), "app.auth.middleware")
        assert result == MatchStatus.NO_MATCH

    def test_reordered_segments_no_match(self) -> None:
        result = fqn_matches_pattern(FQN.from_dotted("app.middleware.auth"), "app.auth.middleware")
        assert result == MatchStatus.NO_MATCH

    def test_wildcard_no_children_exist(self) -> None:
        result = fqn_matches_pattern(FQN.from_dotted("app.db.postgres"), "app.nonexistent.*")
        assert result == MatchStatus.NO_MATCH


# ===========================================================================
# 4. fqn_matches_pattern: priority
# ===========================================================================


class TestFqnMatchesPatternPriority:
    def test_exact_takes_priority_over_wildcard(self) -> None:
        result = fqn_matches_pattern(FQN.from_dotted("app.api"), "app.api")
        assert result == MatchStatus.EXACT

    def test_wildcard_matches_child(self) -> None:
        result = fqn_matches_pattern(FQN.from_dotted("app.api.users"), "app.api.*")
        assert result == MatchStatus.WILDCARD


# ===========================================================================
# 5. NameResolver.resolve: O(1) bare-name lookup
# ===========================================================================


class TestNameResolverResolve:
    def test_resolve_exact_fqn(self, resolver: NameResolver) -> None:
        assert resolver.resolve("app.auth.middleware") == FQN.from_dotted("app.auth.middleware")

    def test_resolve_bare_name(self, resolver: NameResolver) -> None:
        assert resolver.resolve("middleware") == FQN.from_dotted("app.auth.middleware")

    def test_resolve_partial_dotted(self, resolver: NameResolver) -> None:
        assert resolver.resolve("auth.middleware") == FQN.from_dotted("app.auth.middleware")

    def test_resolve_no_match(self, resolver: NameResolver) -> None:
        assert resolver.resolve("nonexistent") is None

    def test_resolve_root(self, resolver: NameResolver) -> None:
        assert resolver.resolve("app") == FQN.from_dotted("app")

    def test_contains(self, resolver: NameResolver) -> None:
        assert FQN.from_dotted("app.api") in resolver
        assert FQN.from_dotted("nonexistent") not in resolver


# ===========================================================================
# 6. NameResolver.match: pattern matching with specificity
# ===========================================================================


class TestNameResolverMatch:
    def test_exact_match(self, resolver: NameResolver) -> None:
        report = resolver.match("app.auth.middleware")
        assert report.status == MatchStatus.EXACT
        assert report.specificity == 4.0  # depth 3 + exact bonus 1

    def test_wildcard_expansion(self, resolver: NameResolver) -> None:
        report = resolver.match("app.api.*")
        assert report.status == MatchStatus.WILDCARD
        assert report.specificity == 2.0  # depth 2 (.* stripped), wildcard no bonus
        matched_fqns = [fqn for fqn, _ in report.matched]
        assert FQN.from_dotted("app.api.users") in matched_fqns
        assert FQN.from_dotted("app.api.orders") in matched_fqns

    def test_orphan(self, resolver: NameResolver) -> None:
        report = resolver.match("app.db.postgres")
        assert report.status == MatchStatus.NO_MATCH
        assert report.specificity == 0.0
        assert report.matched == ()

    def test_specificity_ordering(self, resolver: NameResolver) -> None:
        exact_report = resolver.match("app.api.users")
        wildcard_report = resolver.match("app.api.*")
        shallow_report = resolver.match("app")
        # exact("app.api.users") = depth 3 + 1.0 = 4.0
        # wildcard("app.api.*") = depth 2 (.* stripped) = 2.0
        # exact("app") = depth 1 + 1.0 = 2.0
        assert exact_report.specificity > wildcard_report.specificity
        assert wildcard_report.specificity >= shallow_report.specificity


# ===========================================================================
# 7. compute_specificity
# ===========================================================================


class TestComputeSpecificity:
    def test_exact_specificity(self) -> None:
        assert compute_specificity("app.api.users", MatchStatus.EXACT) == 4.0

    def test_wildcard_specificity(self) -> None:
        # .* is stripped before depth counting: "app.api" has depth 2
        assert compute_specificity("app.api.*", MatchStatus.WILDCARD) == 2.0

    def test_no_match_specificity(self) -> None:
        assert compute_specificity("app.db.postgres", MatchStatus.NO_MATCH) == 0.0

    def test_shallow_exact(self) -> None:
        assert compute_specificity("app", MatchStatus.EXACT) == 2.0


# ===========================================================================
# 8. MatchStatus enum
# ===========================================================================


class TestMatchStatus:
    def test_values(self) -> None:
        assert MatchStatus.EXACT.value == "exact"
        assert MatchStatus.WILDCARD.value == "wildcard"
        assert MatchStatus.NO_MATCH.value == "no_match"

    def test_no_segment(self) -> None:
        assert not hasattr(MatchStatus, "SEGMENT")