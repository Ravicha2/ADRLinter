"""Tests for the matching module: fqn_matches_pattern, match_fqn, compute_specificity.

Public interface under test:
    fqn_matches_pattern: check if a concrete FQN matches a constraint pattern
    match_fqn: resolve a constraint pattern against ADG nodes
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
        assert result == MatchStatus.NO_MATCH


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
        assert result == MatchStatus.NO_MATCH

    def test_wildcard_prefix_itself_not_match(self) -> None:
        from services.matching import fqn_matches_pattern, MatchStatus

        # app.api should not match app.api.* (it IS the prefix, not a child)
        result = fqn_matches_pattern(FQN.from_dotted("app.api"), "app.api.*")
        assert result != MatchStatus.WILDCARD


# ===========================================================================
# 3. fqn_matches_pattern: no match (segment matching removed, superseded by resolution layer)
# ===========================================================================


class TestFqnMatchesPatternNoMatch:
    """No matching layer succeeds."""

    def test_completely_unrelated(self) -> None:
        from services.matching import fqn_matches_pattern, MatchStatus

        result = fqn_matches_pattern(FQN.from_dotted("app.db.postgres"), "app.auth.middleware")
        assert result == MatchStatus.NO_MATCH

    def test_reordered_segments_no_match(self) -> None:
        """Segment matching is removed; reordered FQN segments are NO_MATCH.

        Previously, app.middleware.auth would segment-match app.auth.middleware
        via Jaccard. Now this is NO_MATCH and handled by the LLM resolution layer.
        """
        from services.matching import fqn_matches_pattern, MatchStatus

        result = fqn_matches_pattern(FQN.from_dotted("app.middleware.auth"), "app.auth.middleware")
        assert result == MatchStatus.NO_MATCH

    def test_wildcard_no_children_exist(self) -> None:
        from services.matching import fqn_matches_pattern, MatchStatus

        result = fqn_matches_pattern(FQN.from_dotted("app.db.postgres"), "app.nonexistent.*")
        assert result == MatchStatus.NO_MATCH

    def test_near_miss_no_match(self) -> None:
        """Near-miss segment overlap below threshold is NO_MATCH (no segment layer)."""
        from services.matching import fqn_matches_pattern, MatchStatus

        result = fqn_matches_pattern(FQN.from_dotted("app.auth.handler"), "app.auth.middleware")
        assert result == MatchStatus.NO_MATCH


# ===========================================================================
# 4. fqn_matches_pattern: layer priority
# ===========================================================================


class TestFqnMatchesPatternPriority:
    """Exact > Wildcard > No Match. Earlier layers short-circuit."""

    def test_exact_takes_priority_over_wildcard(self) -> None:
        from services.matching import fqn_matches_pattern, MatchStatus

        # A pattern "app.api" matches the FQN "app.api" exactly
        result = fqn_matches_pattern(FQN.from_dotted("app.api"), "app.api")
        assert result == MatchStatus.EXACT

    def test_wildcard_matches_child(self) -> None:
        from services.matching import fqn_matches_pattern, MatchStatus

        # "app.api.*" with FQN "app.api.users" matches via wildcard
        result = fqn_matches_pattern(FQN.from_dotted("app.api.users"), "app.api.*")
        assert result == MatchStatus.WILDCARD


# ===========================================================================
# 5. match_fqn: resolve pattern against ADG nodes
# ===========================================================================


class TestMatchFqn:
    """match_fqn resolves a pattern against ADG nodes."""

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

    def test_reordered_is_orphan(self, sample_nodes: list[FQNNode]) -> None:
        """Segment matching removed; reordered FQN is orphan (resolved by LLM layer)."""
        from services.matching import match_fqn, MatchStatus

        result = match_fqn("app.middleware.auth", sample_nodes)
        assert result.status == MatchStatus.NO_MATCH

    def test_exact_match_priority(self, sample_nodes: list[FQNNode]) -> None:
        """Exact match takes priority over wildcard."""
        from services.matching import match_fqn, MatchStatus

        result = match_fqn("app.auth.middleware", sample_nodes)
        assert result.status == MatchStatus.EXACT


# ===========================================================================
# 6. compute_specificity (simplified: no SEGMENT, no jaccard_score)
# ===========================================================================


class TestComputeSpecificity:
    """Specificity scoring: EXACT = depth+1, WILDCARD = depth, NO_MATCH = 0.0."""

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
        # depth(app.api.*) = 3, wildcard = depth only
        assert compute_specificity(edge, match_status=MatchStatus.WILDCARD) == 3.0

    def test_shallow_fqn_low_specificity(self) -> None:
        from services.matching import compute_specificity, MatchStatus

        edge = ConstraintEdge(
            subject="app",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="logging",
            justification="test",
            char_interval=(0, 10),
            adr_id="ADR-001",
            adr_path="docs/adr/001.md",
        )
        # depth(app) = 1, exact bonus = 1
        assert compute_specificity(edge, match_status=MatchStatus.EXACT) == 2.0

    def test_orphan_specificity(self) -> None:
        """Orphan constraints get specificity 0 (no match to measure depth from)."""
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

    def test_specificity_ordering(self) -> None:
        """More specific constraints have higher scores: exact > wildcard > shallow exact."""
        from services.matching import compute_specificity, MatchStatus

        exact = ConstraintEdge(
            subject="app.api.users",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="logging",
            justification="test",
            char_interval=(0, 10),
            adr_id="ADR-001",
            adr_path="docs/adr/001.md",
        )
        wildcard = ConstraintEdge(
            subject="app.api.*",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="logging",
            justification="test",
            char_interval=(0, 10),
            adr_id="ADR-002",
            adr_path="docs/adr/002.md",
        )
        shallow = ConstraintEdge(
            subject="app",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="logging",
            justification="test",
            char_interval=(0, 10),
            adr_id="ADR-003",
            adr_path="docs/adr/003.md",
        )
        assert compute_specificity(exact, MatchStatus.EXACT) > compute_specificity(wildcard, MatchStatus.WILDCARD)
        assert compute_specificity(wildcard, MatchStatus.WILDCARD) > compute_specificity(shallow, MatchStatus.EXACT)
        # 4.0 > 3.0 > 2.0

    def test_no_jaccard_score_parameter(self) -> None:
        """compute_specificity does not accept jaccard_score (SEGMENT removed)."""
        from services.matching import compute_specificity, MatchStatus

        edge = ConstraintEdge(
            subject="app.api",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="logging",
            justification="test",
            char_interval=(0, 10),
            adr_id="ADR-001",
            adr_path="docs/adr/001.md",
        )
        # Should not accept jaccard_score parameter
        with pytest.raises(TypeError):
            compute_specificity(edge, match_status=MatchStatus.WILDCARD, jaccard_score=0.9)


# ===========================================================================
# 7. MatchStatus: SEGMENT removed
# ===========================================================================


class TestMatchStatusNoSegment:
    """MatchStatus no longer has SEGMENT; superseded by LLM resolution layer."""

    def test_match_status_values(self) -> None:
        from services.matching import MatchStatus

        assert MatchStatus.EXACT.value == "exact"
        assert MatchStatus.WILDCARD.value == "wildcard"
        assert MatchStatus.NO_MATCH.value == "no_match"

    def test_match_status_has_no_segment(self) -> None:
        from services.matching import MatchStatus

        assert not hasattr(MatchStatus, "SEGMENT")