"""Tests for the matching module: fqn_matches_pattern and match_fqn.

Public interface under test:
    fqn_matches_pattern: check if a concrete FQN matches a constraint pattern
    match_fqn: resolve a constraint pattern against ADG nodes (refactored to use fqn_matches_pattern)
    compute_specificity: specificity scoring for constraint edges
"""

from __future__ import annotations

import pytest

from services.fqn import FQN
from services.models import (
    ADG,
    ConstraintEdge,
    Edge,
    FQNKind,
    FQNNode,
    PredicateType,
)


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def sample_nodes() -> list[FQNNode]:
    """A small set of ADG nodes for matching tests."""
    return [
        FQNNode(fqn=FQN.from_dotted("app"), kind=FQNKind.MODULE, file_path="app/__init__.py", line_start=0, line_end=0, start_byte=0, end_byte=0),
        FQNNode(fqn=FQN.from_dotted("app.api"), kind=FQNKind.MODULE, file_path="app/api/__init__.py", line_start=0, line_end=0, start_byte=0, end_byte=0),
        FQNNode(fqn=FQN.from_dotted("app.api.users"), kind=FQNKind.MODULE, file_path="app/api/users.py", line_start=0, line_end=50, start_byte=0, end_byte=1000),
        FQNNode(fqn=FQN.from_dotted("app.api.orders"), kind=FQNKind.MODULE, file_path="app/api/orders.py", line_start=0, line_end=40, start_byte=0, end_byte=800),
        FQNNode(fqn=FQN.from_dotted("app.auth"), kind=FQNKind.MODULE, file_path="app/auth/__init__.py", line_start=0, line_end=0, start_byte=0, end_byte=0),
        FQNNode(fqn=FQN.from_dotted("app.auth.middleware"), kind=FQNKind.MODULE, file_path="app/auth/middleware.py", line_start=0, line_end=60, start_byte=0, end_byte=1200),
        FQNNode(fqn=FQN.from_dotted("app.services"), kind=FQNKind.MODULE, file_path="app/services/__init__.py", line_start=0, line_end=0, start_byte=0, end_byte=0),
        FQNNode(fqn=FQN.from_dotted("app.services.user"), kind=FQNKind.MODULE, file_path="app/services/user.py", line_start=0, line_end=80, start_byte=0, end_byte=2000),
    ]


# ===========================================================================
# 1. fqn_matches_pattern: exact match
# ===========================================================================


class TestFqnMatchesPatternExact:
    """Exact match: FQN string equals pattern string."""

    def test_exact_match_module(self) -> None:
        from services.matching import fqn_matches_pattern, MatchStatus

        result = fqn_matches_pattern(FQN.from_dotted("app.auth.middleware"), "app.auth.middleware")
        assert result == MatchStatus.EXACT

    def test_exact_match_root(self) -> None:
        from services.matching import fqn_matches_pattern, MatchStatus

        result = fqn_matches_pattern(FQN.from_dotted("app"), "app")
        assert result == MatchStatus.EXACT

    def test_exact_mismatch(self) -> None:
        from services.matching import fqn_matches_pattern, MatchStatus

        result = fqn_matches_pattern(FQN.from_dotted("app.auth.middleware"), "app.middleware.auth")
        assert result != MatchStatus.EXACT


# ===========================================================================
# 2. fqn_matches_pattern: wildcard match
# ===========================================================================


class TestFqnMatchesPatternWildcard:
    """Wildcard match: pattern ends with .*, FQN is a child."""

    def test_wildcard_direct_child(self) -> None:
        from services.matching import fqn_matches_pattern, MatchStatus

        result = fqn_matches_pattern(FQN.from_dotted("app.api.users"), "app.api.*")
        assert result == MatchStatus.WILDCARD

    def test_wildcard_deep_child(self) -> None:
        from services.matching import fqn_matches_pattern, MatchStatus

        result = fqn_matches_pattern(FQN.from_dotted("app.api.v1.users"), "app.api.*")
        assert result == MatchStatus.WILDCARD

    def test_wildcard_not_child(self) -> None:
        from services.matching import fqn_matches_pattern, MatchStatus

        result = fqn_matches_pattern(FQN.from_dotted("app.services.user"), "app.api.*")
        assert result != MatchStatus.WILDCARD

    def test_wildcard_prefix_itself_not_match(self) -> None:
        from services.matching import fqn_matches_pattern, MatchStatus

        # app.api should not match app.api.* (it IS the prefix, not a child)
        result = fqn_matches_pattern(FQN.from_dotted("app.api"), "app.api.*")
        assert result != MatchStatus.WILDCARD


# ===========================================================================
# 3. fqn_matches_pattern: segment match (concrete)
# ===========================================================================


class TestFqnMatchesPatternSegmentConcrete:
    """Segment match: Jaccard overlap on dot-split segments, both non-wildcard."""

    def test_segment_reorder_jaccard_1(self) -> None:
        from services.matching import fqn_matches_pattern, MatchStatus

        # app.auth.middleware vs app.middleware.auth: same segments, different order
        result = fqn_matches_pattern(FQN.from_dotted("app.middleware.auth"), "app.auth.middleware")
        assert result == MatchStatus.SEGMENT

    def test_segment_near_miss_high_jaccard(self) -> None:
        from services.matching import fqn_matches_pattern, MatchStatus

        # app.auth.middleware vs app.auth.handler: 2/4 overlap (below 0.9)
        result = fqn_matches_pattern(FQN.from_dotted("app.auth.handler"), "app.auth.middleware")
        assert result != MatchStatus.SEGMENT

    def test_segment_different_depth_no_match(self) -> None:
        from services.matching import fqn_matches_pattern, MatchStatus

        # app.auth vs app.auth.middleware: very different segment counts
        result = fqn_matches_pattern(FQN.from_dotted("app.auth"), "app.auth.middleware")
        assert result != MatchStatus.SEGMENT

    def test_segment_multiset_preserves_duplicates(self) -> None:
        from services.matching import fqn_matches_pattern, MatchStatus

        # app.service.service.handler vs app.handler.service.service:
        # multiset Jaccard should be 1.0 (same segments with same counts)
        result = fqn_matches_pattern(FQN.from_dotted("app.handler.service.service"), "app.service.service.handler")
        assert result == MatchStatus.SEGMENT

    def test_segment_multiset_catches_false_positive(self) -> None:
        from services.matching import fqn_matches_pattern, MatchStatus

        # app.service.service.handler vs app.service.handler:
        # multiset: {app:1, service:2, handler:1} vs {app:1, service:1, handler:1}
        # intersection min counts: {app:1, service:1, handler:1} = 3
        # union max counts: {app:1, service:2, handler:1} = 4
        # Jaccard = 3/4 = 0.75, below 0.9 threshold
        result = fqn_matches_pattern(FQN.from_dotted("app.service.handler"), "app.service.service.handler")
        assert result != MatchStatus.SEGMENT


# ===========================================================================
# 4. fqn_matches_pattern: segment match (wildcard)
# ===========================================================================


class TestFqnMatchesPatternSegmentWildcard:
    """Segment match on wildcard patterns: Jaccard on prefix segments after stripping .*."""

    def test_wildcard_segment_reorder(self) -> None:
        from services.matching import fqn_matches_pattern, MatchStatus

        # Pattern: app.middleware.user.model.*
        # FQN: app.middleware.model.user.service
        # Prefix segments: {app, middleware, user, model} vs {app, middleware, model, user}
        # Jaccard = 4/4 = 1.0, and FQN is a child
        result = fqn_matches_pattern(
            FQN.from_dotted("app.middleware.model.user.service"),
            "app.middleware.user.model.*",
        )
        assert result == MatchStatus.SEGMENT

    def test_wildcard_segment_not_child(self) -> None:
        from services.matching import fqn_matches_pattern, MatchStatus

        # Pattern: app.middleware.user.model.*
        # FQN: app.middleware.model.user (same depth as prefix, not a child)
        result = fqn_matches_pattern(
            FQN.from_dotted("app.middleware.model.user"),
            "app.middleware.user.model.*",
        )
        assert result != MatchStatus.SEGMENT

    def test_wildcard_segment_low_jaccard(self) -> None:
        from services.matching import fqn_matches_pattern, MatchStatus

        # Pattern: app.api.routes.handler.*
        # FQN: app.middleware.model.user.service
        # Prefix segments: {app, api, routes, handler} vs {app, middleware, model, user}
        # Intersection: {app} = 1, Union = 7, Jaccard ~ 0.14
        result = fqn_matches_pattern(
            FQN.from_dotted("app.middleware.model.user.service"),
            "app.api.routes.handler.*",
        )
        assert result != MatchStatus.SEGMENT


# ===========================================================================
# 5. fqn_matches_pattern: no match
# ===========================================================================


class TestFqnMatchesPatternNoMatch:
    """No matching layer succeeds."""

    def test_completely_unrelated(self) -> None:
        from services.matching import fqn_matches_pattern, MatchStatus

        result = fqn_matches_pattern(FQN.from_dotted("app.db.postgres"), "app.auth.middleware")
        assert result == MatchStatus.NO_MATCH

    def test_wildcard_no_children_exist(self) -> None:
        from services.matching import fqn_matches_pattern, MatchStatus

        # The FQN itself doesn't match the wildcard pattern
        # (standard wildcard already checked and failed, segment wildcard also fails)
        result = fqn_matches_pattern(FQN.from_dotted("app.db.postgres"), "app.nonexistent.*")
        assert result == MatchStatus.NO_MATCH


# ===========================================================================
# 6. fqn_matches_pattern: layer priority
# ===========================================================================


class TestFqnMatchesPatternPriority:
    """Exact > Wildcard > Segment. Earlier layers short-circuit."""

    def test_exact_takes_priority_over_wildcard(self) -> None:
        from services.matching import fqn_matches_pattern, MatchStatus

        # A pattern "app.api" matches the FQN "app.api" exactly,
        # even though "app.api" could theoretically be a parent of "app.api.*"
        result = fqn_matches_pattern(FQN.from_dotted("app.api"), "app.api")
        assert result == MatchStatus.EXACT

    def test_wildcard_takes_priority_over_segment(self) -> None:
        from services.matching import fqn_matches_pattern, MatchStatus

        # "app.api.*" with FQN "app.api.users" matches via wildcard first
        result = fqn_matches_pattern(FQN.from_dotted("app.api.users"), "app.api.*")
        assert result == MatchStatus.WILDCARD


# ===========================================================================
# 7. match_fqn: refactored to use fqn_matches_pattern
# ===========================================================================


class TestMatchFqnRefactored:
    """match_fqn still works after refactor to use fqn_matches_pattern internally."""

    def test_exact_match(self, sample_nodes: list[FQNNode]) -> None:
        from services.matching import match_fqn, MatchStatus

        result = match_fqn("app.auth.middleware", sample_nodes)
        assert result.status == MatchStatus.EXACT
        assert result.matched_fqns == [FQN.from_dotted("app.auth.middleware")]

    def test_wildcard_expansion(self, sample_nodes: list[FQNNode]) -> None:
        from services.matching import match_fqn, MatchStatus

        result = match_fqn("app.api.*", sample_nodes)
        assert result.status == MatchStatus.WILDCARD
        matched = sorted(result.matched_fqns, key=str)
        assert matched == [FQN.from_dotted("app.api.orders"), FQN.from_dotted("app.api.users")]

    def test_orphan(self, sample_nodes: list[FQNNode]) -> None:
        from services.matching import match_fqn, MatchStatus

        result = match_fqn("app.db.postgres", sample_nodes)
        assert result.status == MatchStatus.NO_MATCH
        assert result.matched_fqns == []

    def test_segment_match_collects_matching_nodes(self, sample_nodes: list[FQNNode]) -> None:
        from services.matching import match_fqn, MatchStatus

        # "app.middleware.auth" should segment-match to node "app.auth.middleware"
        result = match_fqn("app.middleware.auth", sample_nodes)
        assert result.status == MatchStatus.SEGMENT
        assert FQN.from_dotted("app.auth.middleware") in result.matched_fqns

    def test_segment_match_also_catches_exact_reorder(self, sample_nodes: list[FQNNode]) -> None:
        from services.matching import match_fqn, MatchStatus

        # "app.auth.middleware" has an exact node, so exact wins
        result = match_fqn("app.auth.middleware", sample_nodes)
        assert result.status == MatchStatus.EXACT


# ===========================================================================
# 8. compute_specificity (in matching module after refactor)
# ===========================================================================


class TestComputeSpecificity:
    """Specificity scoring, including segment match bonus."""

    def test_exact_specificity(self) -> None:
        from services.matching import compute_specificity, MatchStatus

        edge = ConstraintEdge(
            subject="app.api.users",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="logging",
            justification="test",
            char_interval=(0, 10),
            adr_id="ADR-001",
            adr_path="docs/adr/001.md",
        )
        # depth(app.api.users) = 3, exact bonus = 1
        assert compute_specificity(edge, match_status=MatchStatus.EXACT) == 4.0

    def test_wildcard_specificity(self) -> None:
        from services.matching import compute_specificity, MatchStatus

        edge = ConstraintEdge(
            subject="app.api.*",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="logging",
            justification="test",
            char_interval=(0, 10),
            adr_id="ADR-001",
            adr_path="docs/adr/001.md",
        )
        # depth(app.api.*) = 3, no exact bonus, wildcard penalty removed per ADR 006
        assert compute_specificity(edge, match_status=MatchStatus.WILDCARD) == 3.0

    def test_segment_specificity_with_jaccard(self) -> None:
        from services.matching import compute_specificity, MatchStatus

        edge = ConstraintEdge(
            subject="app.auth.middleware",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="logging",
            justification="test",
            char_interval=(0, 10),
            adr_id="ADR-001",
            adr_path="docs/adr/001.md",
        )
        # depth = 3, jaccard_score = 1.0 (perfect reorder)
        # specificity = 3 + 1.0 = 4.0
        assert compute_specificity(edge, match_status=MatchStatus.SEGMENT, jaccard_score=1.0) == 4.0

    def test_segment_specificity_partial_jaccard(self) -> None:
        from services.matching import compute_specificity, MatchStatus

        edge = ConstraintEdge(
            subject="app.auth.middleware",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="logging",
            justification="test",
            char_interval=(0, 10),
            adr_id="ADR-001",
            adr_path="docs/adr/001.md",
        )
        # depth = 3, jaccard_score = 0.9
        # specificity = 3 + 0.9 = 3.9
        assert compute_specificity(edge, match_status=MatchStatus.SEGMENT, jaccard_score=0.9) == 3.9

    def test_orphan_specificity_zero(self) -> None:
        from services.matching import compute_specificity, MatchStatus

        edge = ConstraintEdge(
            subject="app.db.postgres",
            predicate=PredicateType.REQUIRES_IMPLEMENTATION,
            object="app.auth.middleware",
            justification="test",
            char_interval=(0, 10),
            adr_id="ADR-001",
            adr_path="docs/adr/001.md",
        )
        assert compute_specificity(edge, match_status=MatchStatus.NO_MATCH) == 0.0