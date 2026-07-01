"""Tests for the resolver module: NameResolver, fqn_matches_pattern, MatchStatus."""

from __future__ import annotations

import pytest

from services.fqn import FQN
from services.resolver import (
    MatchStatus,
    NameResolver,
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
# 6. MatchStatus enum
# ===========================================================================


class TestMatchStatus:
    def test_values(self) -> None:
        assert MatchStatus.EXACT.value == "exact"
        assert MatchStatus.WILDCARD.value == "wildcard"
        assert MatchStatus.NO_MATCH.value == "no_match"

    def test_no_segment(self) -> None:
        assert not hasattr(MatchStatus, "SEGMENT")