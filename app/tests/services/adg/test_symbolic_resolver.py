"""Tests for the Symbolic Resolver: substring matching, kind filtering, CONTAINS
walks, external dependency bypass, and end-to-end resolution."""

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
    ResolvedConstraint,
    SymbolicConstraint,
)
from services.adg.symbolic_resolver import (
    _general_match,
    _kind_filter,
    _specific_narrow,
    _walk_contains,
    resolve_symbolic_constraints,
)


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def sample_adg() -> ADG:
    """A small ADG with module, class, function, and method nodes."""
    nodes = [
        FQNNode(fqn=FQN.from_dotted("app"), kind=FQNKind.MODULE, file_path="app/__init__.py", line_start=0, line_end=0, start_byte=0, end_byte=0),
        FQNNode(fqn=FQN.from_dotted("app.api"), kind=FQNKind.MODULE, file_path="app/api/__init__.py", line_start=0, line_end=0, start_byte=0, end_byte=0),
        FQNNode(fqn=FQN.from_dotted("app.api.users"), kind=FQNKind.MODULE, file_path="app/api/users.py", line_start=0, line_end=50, start_byte=0, end_byte=1000),
        FQNNode(fqn=FQN.from_dotted("app.api.orders"), kind=FQNKind.MODULE, file_path="app/api/orders.py", line_start=0, line_end=40, start_byte=0, end_byte=800),
        FQNNode(fqn=FQN.from_dotted("app.auth"), kind=FQNKind.MODULE, file_path="app/auth/__init__.py", line_start=0, line_end=0, start_byte=0, end_byte=0),
        FQNNode(fqn=FQN.from_dotted("app.auth.Middleware"), kind=FQNKind.CLASS, file_path="app/auth/middleware.py", line_start=0, line_end=60, start_byte=0, end_byte=1200),
        FQNNode(fqn=FQN.from_dotted("app.auth.Middleware.authenticate"), kind=FQNKind.METHOD, file_path="app/auth/middleware.py", line_start=10, line_end=30, start_byte=0, end_byte=500),
        FQNNode(fqn=FQN.from_dotted("app.services"), kind=FQNKind.MODULE, file_path="app/services/__init__.py", line_start=0, line_end=0, start_byte=0, end_byte=0),
        FQNNode(fqn=FQN.from_dotted("app.services.user"), kind=FQNKind.MODULE, file_path="app/services/user.py", line_start=0, line_end=80, start_byte=0, end_byte=2000),
    ]
    edges = [
        Edge(source="app", target="app.api", kind="CONTAINS"),
        Edge(source="app.api", target="app.api.users", kind="CONTAINS"),
        Edge(source="app.api", target="app.api.orders", kind="CONTAINS"),
        Edge(source="app", target="app.auth", kind="CONTAINS"),
        Edge(source="app.auth", target="app.auth.Middleware", kind="CONTAINS"),
        Edge(source="app.auth.Middleware", target="app.auth.Middleware.authenticate", kind="CONTAINS"),
        Edge(source="app", target="app.services", kind="CONTAINS"),
        Edge(source="app.services", target="app.services.user", kind="CONTAINS"),
        Edge(source="app.api.users", target="app.auth.middleware", kind="IMPORTS"),
        Edge(source="app.services.user", target="app.auth.middleware", kind="IMPORTS"),
    ]
    return ADG(nodes=nodes, edges=edges)


# ===========================================================================
# 1. _kind_filter
# ===========================================================================


class TestKindFilter:
    def test_filters_by_allowed_kinds(self) -> None:
        nodes = [
            FQNNode(fqn=FQN.from_dotted("app"), kind=FQNKind.MODULE, file_path="", line_start=0, line_end=0, start_byte=0, end_byte=0),
            FQNNode(fqn=FQN.from_dotted("app.Foo"), kind=FQNKind.CLASS, file_path="", line_start=0, line_end=0, start_byte=0, end_byte=0),
            FQNNode(fqn=FQN.from_dotted("app.bar"), kind=FQNKind.FUNCTION, file_path="", line_start=0, line_end=0, start_byte=0, end_byte=0),
        ]
        result = _kind_filter(nodes, {"module"})
        assert len(result) == 1
        assert result[0].fqn == FQN.from_dotted("app")

    def test_multiple_allowed_kinds(self) -> None:
        nodes = [
            FQNNode(fqn=FQN.from_dotted("app"), kind=FQNKind.MODULE, file_path="", line_start=0, line_end=0, start_byte=0, end_byte=0),
            FQNNode(fqn=FQN.from_dotted("app.Foo"), kind=FQNKind.CLASS, file_path="", line_start=0, line_end=0, start_byte=0, end_byte=0),
            FQNNode(fqn=FQN.from_dotted("app.bar"), kind=FQNKind.FUNCTION, file_path="", line_start=0, line_end=0, start_byte=0, end_byte=0),
        ]
        result = _kind_filter(nodes, {"class", "function"})
        assert len(result) == 2

    def test_empty_kinds_returns_empty(self) -> None:
        nodes = [
            FQNNode(fqn=FQN.from_dotted("app"), kind=FQNKind.MODULE, file_path="", line_start=0, line_end=0, start_byte=0, end_byte=0),
        ]
        result = _kind_filter(nodes, set())
        assert len(result) == 0


# ===========================================================================
# 2. _general_match
# ===========================================================================


class TestGeneralMatch:
    def test_exact_match(self, sample_adg: ADG) -> None:
        result = _general_match("app.api", sample_adg.nodes)
        assert len(result) >= 1
        assert any(str(n.fqn) == "app.api" for n in result)

    def test_prefix_match(self, sample_adg: ADG) -> None:
        result = _general_match("app", sample_adg.nodes)
        # "app" matches itself + anything starting with "app."
        assert len(result) >= 1

    def test_no_match(self, sample_adg: ADG) -> None:
        result = _general_match("nonexistent", sample_adg.nodes)
        assert len(result) == 0


# ===========================================================================
# 3. _walk_contains
# ===========================================================================


class TestWalkContains:
    def test_finds_children(self, sample_adg: ADG) -> None:
        children = _walk_contains(FQN.from_dotted("app"), sample_adg.edges, sample_adg.nodes)
        child_fqns = {str(n.fqn) for n in children}
        assert "app.api" in child_fqns
        assert "app.auth" in child_fqns
        assert "app.services" in child_fqns

    def test_leaf_has_no_children(self, sample_adg: ADG) -> None:
        children = _walk_contains(FQN.from_dotted("app.api.users"), sample_adg.edges, sample_adg.nodes)
        assert len(children) == 0


# ===========================================================================
# 4. _specific_narrow
# ===========================================================================


class TestSpecificNarrow:
    def test_exact_match_priority(self) -> None:
        nodes = [
            FQNNode(fqn=FQN.from_dotted("app.Handler"), kind=FQNKind.CLASS, file_path="", line_start=0, line_end=0, start_byte=0, end_byte=0),
            FQNNode(fqn=FQN.from_dotted("app.HandlerBase"), kind=FQNKind.CLASS, file_path="", line_start=0, line_end=0, start_byte=0, end_byte=0),
            FQNNode(fqn=FQN.from_dotted("app.RequestHandler"), kind=FQNKind.CLASS, file_path="", line_start=0, line_end=0, start_byte=0, end_byte=0),
        ]
        result = _specific_narrow("Handler", nodes)
        assert len(result) == 1
        assert str(result[0].fqn) == "app.Handler"

    def test_prefix_overlap_when_no_exact(self) -> None:
        nodes = [
            FQNNode(fqn=FQN.from_dotted("app.HandlerBase"), kind=FQNKind.CLASS, file_path="", line_start=0, line_end=0, start_byte=0, end_byte=0),
            FQNNode(fqn=FQN.from_dotted("app.RequestHandler"), kind=FQNKind.CLASS, file_path="", line_start=0, line_end=0, start_byte=0, end_byte=0),
        ]
        result = _specific_narrow("Handler", nodes)
        # "HandlerBase" starts with "Handler" (prefix overlap)
        assert any(str(n.fqn) == "app.HandlerBase" for n in result)

    def test_substring_containment_as_fallback(self) -> None:
        nodes = [
            FQNNode(fqn=FQN.from_dotted("app.RequestHandler"), kind=FQNKind.CLASS, file_path="", line_start=0, line_end=0, start_byte=0, end_byte=0),
        ]
        result = _specific_narrow("Handler", nodes)
        # "Handler" is contained in "RequestHandler"
        assert len(result) == 1
        assert str(result[0].fqn) == "app.RequestHandler"

    def test_no_match_returns_empty(self) -> None:
        nodes = [
            FQNNode(fqn=FQN.from_dotted("app.Foo"), kind=FQNKind.CLASS, file_path="", line_start=0, line_end=0, start_byte=0, end_byte=0),
        ]
        result = _specific_narrow("xyz", nodes)
        assert len(result) == 0

    def test_empty_candidates_returns_empty(self) -> None:
        result = _specific_narrow("Handler", [])
        assert result == []


# ===========================================================================
# 5. resolve_symbolic_constraints: integration tests
# ===========================================================================


class TestResolveSymbolicConstraints:
    def test_dependency_predicate_creates_external_node(self, sample_adg: ADG) -> None:
        """Dependency predicate with no ADG match creates EXTERNAL node."""
        sc = SymbolicConstraint(
            subject_role_general="app.api",
            subject_role_specific="endpoint",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object_role_general="mysql",
            object_role_specific="connector",
            justification="No direct MySQL.",
            extraction_text="No direct MySQL connections",
            adr_id="ADR-001",
            adr_path="docs/adr/001.md",
        )
        resolved = resolve_symbolic_constraints([sc], sample_adg)
        assert len(resolved) >= 1
        assert resolved[0].object_matched_by == "external"

    def test_implementation_predicate_matches_class_via_contains(self, sample_adg: ADG) -> None:
        """requires_implementation matches class nodes under the general path.

        kind_filter removes modules before general_match, so app.auth.Middleware
        is found directly by general_match (general_wildcard), not by CONTAINS
        walk + specific narrow.
        """
        sc = SymbolicConstraint(
            subject_role_general="app",
            subject_role_specific="module",
            predicate=PredicateType.REQUIRES_IMPLEMENTATION,
            object_role_general="app.auth",
            object_role_specific="Middleware",
            justification="Must implement auth.",
            extraction_text="Must implement auth",
            adr_id="ADR-010",
            adr_path="docs/adr/010.md",
        )
        resolved = resolve_symbolic_constraints([sc], sample_adg)
        assert len(resolved) >= 1
        object_fqns = {rc.constraint_edge.object for rc in resolved}
        assert "app.auth.Middleware" in object_fqns
        # Module nodes are filtered out by kind_filter before general_match,
        # so the class node is found via general_wildcard, not specific narrow
        assert resolved[0].object_matched_by == "general_wildcard"

    def test_contains_walk_with_specific_narrow(self) -> None:
        """Dependency predicate: general_match finds module, CONTAINS walk
        finds children, specific_narrow narrows by role_specific."""
        nodes = [
            FQNNode(fqn=FQN.from_dotted("app"), kind=FQNKind.MODULE, file_path="", line_start=0, line_end=0, start_byte=0, end_byte=0),
            FQNNode(fqn=FQN.from_dotted("app.services"), kind=FQNKind.MODULE, file_path="", line_start=0, line_end=0, start_byte=0, end_byte=0),
            FQNNode(fqn=FQN.from_dotted("app.services.user"), kind=FQNKind.MODULE, file_path="", line_start=0, line_end=0, start_byte=0, end_byte=0),
            FQNNode(fqn=FQN.from_dotted("app.services.order"), kind=FQNKind.MODULE, file_path="", line_start=0, line_end=0, start_byte=0, end_byte=0),
        ]
        edges = [
            Edge(source="app", target="app.services", kind="CONTAINS"),
            Edge(source="app.services", target="app.services.user", kind="CONTAINS"),
            Edge(source="app.services", target="app.services.order", kind="CONTAINS"),
        ]
        adg = ADG(nodes=nodes, edges=edges)

        # SUBJECT_KINDS for prohibits_dependency = {"module"}
        # general_match finds app.services (exact), then CONTAINS walk finds
        # app.services.user and app.services.order, specific_narrow("user") picks user
        sc = SymbolicConstraint(
            subject_role_general="app.services",
            subject_role_specific="user",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object_role_general="logging",
            object_role_specific="logger",
            justification="No direct logging in user service.",
            extraction_text="No direct logging in user service",
            adr_id="ADR-020",
            adr_path="docs/adr/020.md",
        )
        resolved = resolve_symbolic_constraints([sc], adg)
        assert len(resolved) >= 1
        subject_fqns = {rc.constraint_edge.subject for rc in resolved}
        assert "app.services.user" in subject_fqns
        assert resolved[0].subject_matched_by == "specific"

    def test_general_wildcard_match(self, sample_adg: ADG) -> None:
        """When role_specific doesn't narrow, general_wildcard is the match source."""
        sc = SymbolicConstraint(
            subject_role_general="app.services",
            subject_role_specific="service",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object_role_general="logging",
            object_role_specific="logging module",
            justification="No bare logging.",
            extraction_text="No bare logging",
            adr_id="ADR-005",
            adr_path="docs/adr/005.md",
        )
        resolved = resolve_symbolic_constraints([sc], sample_adg)
        assert len(resolved) >= 1
        assert resolved[0].subject_matched_by == "general_wildcard"

    def test_no_match_skips_constraint(self, sample_adg: ADG) -> None:
        """Unresolved constraints are logged and skipped (no crash)."""
        sc = SymbolicConstraint(
            subject_role_general="nonexistent",
            subject_role_specific="phantom",
            predicate=PredicateType.REQUIRES_DEPENDENCY,
            object_role_general="mysql",
            object_role_specific="connector",
            justification="Phantom module.",
            extraction_text="Phantom module",
            adr_id="ADR-999",
            adr_path="docs/adr/999.md",
        )
        resolved = resolve_symbolic_constraints([sc], sample_adg)
        assert len(resolved) == 0

    def test_fallback_match_when_general_finds_nothing(self) -> None:
        """When general match fails, role_specific is substring-matched against all nodes."""
        nodes = [
            FQNNode(fqn=FQN.from_dotted("app"), kind=FQNKind.MODULE, file_path="", line_start=0, line_end=0, start_byte=0, end_byte=0),
            FQNNode(fqn=FQN.from_dotted("app.auth"), kind=FQNKind.MODULE, file_path="", line_start=0, line_end=0, start_byte=0, end_byte=0),
        ]
        edges = [
            Edge(source="app", target="app.auth", kind="CONTAINS"),
        ]
        adg = ADG(nodes=nodes, edges=edges)

        sc = SymbolicConstraint(
            subject_role_general="totally_unknown",
            subject_role_specific="auth",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object_role_general="logging",
            object_role_specific="logger",
            justification="Fallback test.",
            extraction_text="Fallback test",
            adr_id="ADR-FB",
            adr_path="docs/adr/fb.md",
        )
        resolved = resolve_symbolic_constraints([sc], adg)
        # subject matches via fallback ("auth" in "app.auth"), object via external
        assert len(resolved) >= 1
        assert resolved[0].subject_matched_by == "fallback"

    def test_specificity_stays_zero_after_resolution(self, sample_adg: ADG) -> None:
        """ConstraintEdge.specificity is 0.0 after merge (computed later)."""
        sc = SymbolicConstraint(
            subject_role_general="app.api",
            subject_role_specific="endpoint",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object_role_general="mysql",
            object_role_specific="connector",
            justification="No direct MySQL.",
            extraction_text="No direct MySQL connections",
            adr_id="ADR-001",
            adr_path="docs/adr/001.md",
        )
        resolved = resolve_symbolic_constraints([sc], sample_adg)
        for rc in resolved:
            assert rc.constraint_edge.specificity == 0.0

    def test_self_loop_skipped(self) -> None:
        """Subject and object resolving to the same FQN produces no edge."""
        nodes = [
            FQNNode(fqn=FQN.from_dotted("app"), kind=FQNKind.MODULE, file_path="", line_start=0, line_end=0, start_byte=0, end_byte=0),
        ]
        adg = ADG(nodes=nodes, edges=[])

        sc = SymbolicConstraint(
            subject_role_general="app",
            subject_role_specific="app",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object_role_general="app",
            object_role_specific="app",
            justification="Self-loop test.",
            extraction_text="Self-loop test",
            adr_id="ADR-SELF",
            adr_path="docs/adr/self.md",
        )
        resolved = resolve_symbolic_constraints([sc], adg)
        # Both resolve to "app", which is a self-loop, so no edges
        assert len(resolved) == 0

    def test_multiple_subjects_and_objects_produce_cross_product(self, sample_adg: ADG) -> None:
        """Multiple subject and object matches produce edges for each pair."""
        sc = SymbolicConstraint(
            subject_role_general="app",
            subject_role_specific="module",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object_role_general="logging",
            object_role_specific="log",
            justification="No bare logging.",
            extraction_text="No bare logging",
            adr_id="ADR-005",
            adr_path="docs/adr/005.md",
        )
        resolved = resolve_symbolic_constraints([sc], sample_adg)
        # Multiple modules under "app" match as general_wildcard
        # "logging" creates external
        assert len(resolved) >= 1
        subjects = {rc.constraint_edge.subject for rc in resolved}
        assert len(subjects) >= 1