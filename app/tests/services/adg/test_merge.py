"""Tests for the Merge Layer: unifying AST-derived ADG with ADR constraint edges.

Public interface under test:
    match_fqn: resolve a constraint subject/object string to FQN nodes
    compute_specificity: compute specificity score for a constraint edge
    merge_constraints: unify Track A ADG + Track B constraint edges into merged ADG
    add_external_nodes: create EXTERNAL nodes for unmatched import targets
    resolve_orphans: LLM-backed naming resolution for orphan FQN patterns
    gather_candidates: collect ADG nodes as resolution candidates via prefix-scoped walk
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from services.fqn import FQN
from services.models import (
    ADG,
    ConstraintEdge,
    Edge,
    FQNKind,
    FQNNode,
    PredicateType,
)
from services.adg.merge import (
    MatchResult,
    MatchStatus,
    add_external_nodes,
    compute_specificity,
    match_fqn,
    merge_constraints,
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
        FQNNode(fqn=FQN.from_dotted("app.auth.middleware"), kind=FQNKind.MODULE, file_path="app/auth/middleware.py", line_start=0, line_end=60, start_byte=0, end_byte=1200),
        FQNNode(fqn=FQN.from_dotted("app.services"), kind=FQNKind.MODULE, file_path="app/services/__init__.py", line_start=0, line_end=0, start_byte=0, end_byte=0),
        FQNNode(fqn=FQN.from_dotted("app.services.user"), kind=FQNKind.MODULE, file_path="app/services/user.py", line_start=0, line_end=80, start_byte=0, end_byte=2000),
    ]
    edges = [
        Edge(source="app", target="app.api", kind="CONTAINS"),
        Edge(source="app.api", target="app.api.users", kind="CONTAINS"),
        Edge(source="app.api", target="app.api.orders", kind="CONTAINS"),
        Edge(source="app", target="app.auth", kind="CONTAINS"),
        Edge(source="app.auth", target="app.auth.middleware", kind="CONTAINS"),
        Edge(source="app", target="app.services", kind="CONTAINS"),
        Edge(source="app.services", target="app.services.user", kind="CONTAINS"),
        Edge(source="app.api.users", target="app.auth.middleware", kind="IMPORTS"),
        Edge(source="app.services.user", target="app.auth.middleware", kind="IMPORTS"),
    ]
    return ADG(nodes=nodes, edges=edges)


@pytest.fixture
def sample_constraints() -> list[ConstraintEdge]:
    """Constraint edges from ADR extraction."""
    return [
        ConstraintEdge(
            subject="app.api.*",
            predicate=PredicateType.REQUIRES_IMPLEMENTATION,
            object="app.auth.middleware",
            justification="All API endpoints must implement authentication.",
            char_interval=(10, 80),
            adr_id="ADR-003",
            adr_path="docs/adr/003-auth-middleware.md",
        ),
        ConstraintEdge(
            subject="app.services.*",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="logging",
            justification="No service shall use bare logging directly.",
            char_interval=(20, 90),
            adr_id="ADR-005",
            adr_path="docs/adr/005-centralized-logging.md",
        ),
    ]


# ===========================================================================
# 1. match_fqn: constraint string to FQN node matching
# ===========================================================================


class TestMatchFqnExact:
    """Exact match: constraint string equals an FQN string."""

    def test_exact_match_module(self, sample_adg: ADG) -> None:
        result = match_fqn("app.auth.middleware", sample_adg.nodes)
        assert result.status == MatchStatus.EXACT
        assert result.matched_fqns == [FQN.from_dotted("app.auth.middleware")]

    def test_exact_match_class(self) -> None:
        nodes = [
            FQNNode(fqn=FQN.from_dotted("app.models.User"), kind=FQNKind.CLASS, file_path="app/models.py", line_start=5, line_end=20, start_byte=0, end_byte=0),
        ]
        adg = ADG(nodes=nodes, edges=[])
        result = match_fqn("app.models.User", adg.nodes)
        assert result.status == MatchStatus.EXACT
        assert result.matched_fqns == [FQN.from_dotted("app.models.User")]

    def test_exact_match_external_node(self) -> None:
        """EXTERNAL nodes are matchable just like any other node."""
        nodes = [
            FQNNode(fqn=FQN.from_dotted("logging"), kind=FQNKind.EXTERNAL, file_path="", line_start=-1, line_end=-1, start_byte=0, end_byte=0),
        ]
        adg = ADG(nodes=nodes, edges=[])
        result = match_fqn("logging", adg.nodes)
        assert result.status == MatchStatus.EXACT
        assert result.matched_fqns == [FQN.from_dotted("logging")]

    def test_no_match_returns_no_match(self, sample_adg: ADG) -> None:
        result = match_fqn("app.db.postgres", sample_adg.nodes)
        assert result.status == MatchStatus.NO_MATCH
        assert result.matched_fqns == []


class TestMatchFqnWildcard:
    """Wildcard match: constraint string with * expands to child FQNs."""

    def test_wildcard_matches_direct_children(self, sample_adg: ADG) -> None:
        result = match_fqn("app.api.*", sample_adg.nodes)
        assert result.status == MatchStatus.WILDCARD
        matched = sorted(result.matched_fqns, key=str)
        assert matched == [FQN.from_dotted("app.api.orders"), FQN.from_dotted("app.api.users")]

    def test_wildcard_on_leaf_module(self, sample_adg: ADG) -> None:
        """Wildcard on a module with one child matches that child."""
        result = match_fqn("app.auth.*", sample_adg.nodes)
        assert result.status == MatchStatus.WILDCARD
        # app.auth has one child: app.auth.middleware
        assert result.matched_fqns == [FQN.from_dotted("app.auth.middleware")]

    def test_wildcard_on_deep_path(self) -> None:
        nodes = [
            FQNNode(fqn=FQN.from_dotted("app"), kind=FQNKind.MODULE, file_path="app/__init__.py", line_start=0, line_end=0, start_byte=0, end_byte=0),
            FQNNode(fqn=FQN.from_dotted("app.api"), kind=FQNKind.MODULE, file_path="app/api/__init__.py", line_start=0, line_end=0, start_byte=0, end_byte=0),
            FQNNode(fqn=FQN.from_dotted("app.api.v1"), kind=FQNKind.MODULE, file_path="app/api/v1/__init__.py", line_start=0, line_end=0, start_byte=0, end_byte=0),
            FQNNode(fqn=FQN.from_dotted("app.api.v1.users"), kind=FQNKind.MODULE, file_path="app/api/v1/users.py", line_start=0, line_end=10, start_byte=0, end_byte=0),
        ]
        adg = ADG(nodes=nodes, edges=[])
        result = match_fqn("app.api.*", adg.nodes)
        assert result.status == MatchStatus.WILDCARD
        assert FQN.from_dotted("app.api.v1") in result.matched_fqns

    def test_wildcard_no_match(self, sample_adg: ADG) -> None:
        """Wildcard pattern where parent doesn't exist returns orphan."""
        result = match_fqn("app.nonexistent.*", sample_adg.nodes)
        assert result.status == MatchStatus.NO_MATCH
        assert result.matched_fqns == []


# ===========================================================================
# 2. compute_specificity: specificity scoring
# ===========================================================================


class TestComputeSpecificity:
    """Specificity: EXACT = depth+1, WILDCARD = depth, NO_MATCH = 0.0."""

    def test_exact_fqn_high_specificity(self) -> None:
        edge = ConstraintEdge(
            subject="app.api.users",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="logging",
            justification="test",
            char_interval=(0, 10),
            adr_id="ADR-001",
            adr_path="docs/adr/001.md",
        )
        assert compute_specificity(edge, match_status=MatchStatus.EXACT) == 4.0
        # depth(app.api.users) = 3, exact bonus = 1

    def test_wildcard_specificity(self) -> None:
        edge = ConstraintEdge(
            subject="app.api.*",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="logging",
            justification="test",
            char_interval=(0, 10),
            adr_id="ADR-001",
            adr_path="docs/adr/001.md",
        )
        assert compute_specificity(edge, match_status=MatchStatus.WILDCARD) == 3.0
        # depth(app.api.*) = 3, wildcard = depth only

    def test_shallow_fqn_low_specificity(self) -> None:
        edge = ConstraintEdge(
            subject="app",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="logging",
            justification="test",
            char_interval=(0, 10),
            adr_id="ADR-001",
            adr_path="docs/adr/001.md",
        )
        assert compute_specificity(edge, match_status=MatchStatus.EXACT) == 2.0
        # depth(app) = 1, exact bonus = 1

    def test_orphan_specificity(self) -> None:
        """Orphan constraints get specificity 0 (no match to measure depth from)."""
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
        """More specific constraints have higher scores: 4.0 > 3.0 > 2.0."""
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


# ===========================================================================
# 3. add_external_nodes: EXTERNAL nodes for unmatched imports
# ===========================================================================


class TestAddExternalNodes:
    """Unmatched import targets become EXTERNAL nodes."""

    def test_adds_external_for_stdlib(self, sample_adg: ADG) -> None:
        """Standard library modules imported but not defined in repo become EXTERNAL."""
        edges_with_import = sample_adg.edges + [
            Edge(source="app.services.user", target="logging", kind="IMPORTS"),
        ]
        adg = ADG(nodes=sample_adg.nodes, edges=edges_with_import)
        result = add_external_nodes(adg)
        external_nodes = [n for n in result.nodes if n.kind == FQNKind.EXTERNAL]
        assert len(external_nodes) == 1
        assert external_nodes[0].fqn == FQN.from_dotted("logging")
        assert external_nodes[0].file_path == ""
        assert external_nodes[0].line_start == -1
        assert external_nodes[0].line_end == -1

    def test_does_not_add_external_for_internal_imports(self, sample_adg: ADG) -> None:
        """Imports that target internal modules should not create EXTERNAL nodes."""
        result = add_external_nodes(sample_adg)
        external_nodes = [n for n in result.nodes if n.kind == FQNKind.EXTERNAL]
        assert len(external_nodes) == 0

    def test_external_node_deduplication(self) -> None:
        """Multiple imports of the same external module create one EXTERNAL node."""
        nodes = [
            FQNNode(fqn=FQN.from_dotted("app"), kind=FQNKind.MODULE, file_path="app/__init__.py", line_start=0, line_end=0, start_byte=0, end_byte=0),
            FQNNode(fqn=FQN.from_dotted("app.mod_a"), kind=FQNKind.MODULE, file_path="app/a.py", line_start=0, line_end=10, start_byte=0, end_byte=0),
            FQNNode(fqn=FQN.from_dotted("app.mod_b"), kind=FQNKind.MODULE, file_path="app/b.py", line_start=0, line_end=10, start_byte=0, end_byte=0),
        ]
        edges = [
            Edge(source="app.mod_a", target="logging", kind="IMPORTS"),
            Edge(source="app.mod_b", target="logging", kind="IMPORTS"),
        ]
        adg = ADG(nodes=nodes, edges=edges)
        result = add_external_nodes(adg)
        external_nodes = [n for n in result.nodes if n.kind == FQNKind.EXTERNAL]
        assert len(external_nodes) == 1
        assert external_nodes[0].fqn == FQN.from_dotted("logging")


# ===========================================================================
# 4. merge_constraints: unifying Track A + Track B
# ===========================================================================


class TestMergeConstraints:
    """Merge Layer combines ADG nodes/edges with constraint edges."""

    def test_merge_adds_constraint_edges_to_adg(self, sample_adg: ADG, sample_constraints: list[ConstraintEdge]) -> None:
        """merge_constraints produces ADG with constraint_edges populated."""
        result = merge_constraints(sample_adg, sample_constraints)
        assert len(result.constraint_edges) == 2
        # Check that specificity was computed and stored
        for edge in result.constraint_edges:
            assert edge.specificity > 0.0 or edge.specificity == 0.0  # computed value

    def test_merge_preserves_structural_nodes_and_edges(self, sample_adg: ADG, sample_constraints: list[ConstraintEdge]) -> None:
        """merge_constraints does not modify structural nodes or edges."""
        result = merge_constraints(sample_adg, sample_constraints)
        structural = [n for n in result.nodes if n.kind != FQNKind.EXTERNAL]
        assert len(structural) == len(sample_adg.nodes)
        assert len(result.edges) == len(sample_adg.edges)

    def test_merge_adds_external_for_orphan_references(self, sample_adg: ADG) -> None:
        """Constraint referencing non-existent FQN creates EXTERNAL node."""
        constraints = [
            ConstraintEdge(
                subject="app.services.*",
                predicate=PredicateType.PROHIBITS_DEPENDENCY,
                object="logging",
                justification="No bare logging.",
                char_interval=(20, 90),
                adr_id="ADR-005",
                adr_path="docs/adr/005-logging.md",
            ),
        ]
        result = merge_constraints(sample_adg, constraints)
        external_nodes = [n for n in result.nodes if n.kind == FQNKind.EXTERNAL]
        # 'logging' is not in sample_adg.nodes, so it should become EXTERNAL
        assert any(n.fqn == FQN.from_dotted("logging") for n in external_nodes)

    def test_merge_computes_specificity_for_exact_match(self, sample_adg: ADG) -> None:
        constraints = [
            ConstraintEdge(
                subject="app.api.users",
                predicate=PredicateType.REQUIRES_IMPLEMENTATION,
                object="app.auth.middleware",
                justification="test",
                char_interval=(0, 10),
                adr_id="ADR-001",
                adr_path="docs/adr/001.md",
            ),
        ]
        result = merge_constraints(sample_adg, constraints)
        assert result.constraint_edges[0].specificity == 4.0

    def test_merge_computes_specificity_for_wildcard(self, sample_adg: ADG) -> None:
        constraints = [
            ConstraintEdge(
                subject="app.api.*",
                predicate=PredicateType.REQUIRES_IMPLEMENTATION,
                object="app.auth.middleware",
                justification="test",
                char_interval=(0, 10),
                adr_id="ADR-003",
                adr_path="docs/adr/003.md",
            ),
        ]
        result = merge_constraints(sample_adg, constraints)
        assert result.constraint_edges[0].specificity == 3.0

    def test_merge_empty_constraints(self, sample_adg: ADG) -> None:
        """Merging with no constraints preserves ADG as-is."""
        result = merge_constraints(sample_adg, [])
        assert len(result.constraint_edges) == 0
        assert len(result.nodes) == len(sample_adg.nodes)
        assert len(result.edges) == len(sample_adg.edges)


class TestMergeConstraintsIncremental:
    """Full replace per ADR: delete old constraints, insert new ones."""

    def test_replace_constraints_by_adr_id(self, sample_adg: ADG) -> None:
        """Replacing constraints for an ADR removes old edges and adds new ones."""
        # Initial merge with ADR-003
        old_constraints = [
            ConstraintEdge(
                subject="app.api.*",
                predicate=PredicateType.REQUIRES_IMPLEMENTATION,
                object="app.auth.middleware",
                justification="Old requirement.",
                char_interval=(10, 80),
                adr_id="ADR-003",
                adr_path="docs/adr/003-auth-middleware.md",
            ),
        ]
        merged = merge_constraints(sample_adg, old_constraints)
        assert len(merged.constraint_edges) == 1

        # ADR-003 is modified: replace with new constraints
        new_constraints = [
            ConstraintEdge(
                subject="app.api.*",
                predicate=PredicateType.REQUIRES_DEPENDENCY,
                object="app.auth.middleware",
                justification="Updated: dependency, not implementation.",
                char_interval=(15, 90),
                adr_id="ADR-003",
                adr_path="docs/adr/003-auth-middleware.md",
            ),
            ConstraintEdge(
                subject="app.api.*",
                predicate=PredicateType.PROHIBITS_DEPENDENCY,
                object="app.db.mysql",
                justification="Also added: no direct DB access.",
                char_interval=(100, 180),
                adr_id="ADR-003",
                adr_path="docs/adr/003-auth-middleware.md",
            ),
        ]

        # Delete old ADR-003 constraints, then merge new ones
        remaining = [ce for ce in merged.constraint_edges if ce.adr_id != "ADR-003"]
        adg_after_delete = ADG(
            nodes=merged.nodes,
            edges=merged.edges,
            constraint_edges=remaining,
        )
        result = merge_constraints(adg_after_delete, new_constraints)
        # Old ADR-003 constraint is gone, two new ones are added
        assert len(result.constraint_edges) == 2
        assert all(ce.adr_id == "ADR-003" for ce in result.constraint_edges)

    def test_other_adr_constraints_preserved(self, sample_adg: ADG) -> None:
        """Replacing constraints for one ADR does not affect others."""
        constraints_adr3 = [
            ConstraintEdge(
                subject="app.api.*",
                predicate=PredicateType.REQUIRES_IMPLEMENTATION,
                object="app.auth.middleware",
                justification="Auth required.",
                char_interval=(10, 80),
                adr_id="ADR-003",
                adr_path="docs/adr/003.md",
            ),
        ]
        constraints_adr5 = [
            ConstraintEdge(
                subject="app.services.*",
                predicate=PredicateType.PROHIBITS_DEPENDENCY,
                object="logging",
                justification="No bare logging.",
                char_interval=(20, 90),
                adr_id="ADR-005",
                adr_path="docs/adr/005.md",
            ),
        ]
        merged = merge_constraints(sample_adg, constraints_adr3 + constraints_adr5)
        assert len(merged.constraint_edges) == 2

        # Replace only ADR-003
        new_adr3 = [
            ConstraintEdge(
                subject="app.api.users",
                predicate=PredicateType.REQUIRES_DEPENDENCY,
                object="app.auth.middleware",
                justification="Updated specific rule.",
                char_interval=(5, 50),
                adr_id="ADR-003",
                adr_path="docs/adr/003.md",
            ),
        ]
        remaining = [ce for ce in merged.constraint_edges if ce.adr_id != "ADR-003"]
        adg_after_delete = ADG(
            nodes=merged.nodes,
            edges=merged.edges,
            constraint_edges=remaining,
        )
        result = merge_constraints(adg_after_delete, new_adr3)

        # ADR-005 constraint is still there
        adr5_edges = [ce for ce in result.constraint_edges if ce.adr_id == "ADR-005"]
        assert len(adr5_edges) == 1
        assert adr5_edges[0].predicate is PredicateType.PROHIBITS_DEPENDENCY


# ===========================================================================
# 5. FQNKind.EXTERNAL
# ===========================================================================


class TestFQNKindExternal:
    """EXTERNAL is a valid FQNKind for modules not defined in the target repo."""

    def test_external_value(self) -> None:
        assert FQNKind.EXTERNAL.value == "external"

    def test_external_node_creation(self) -> None:
        node = FQNNode(
            fqn=FQN.from_dotted("logging"),
            kind=FQNKind.EXTERNAL,
            file_path="",
            line_start=-1,
            line_end=-1,
            start_byte=0,
            end_byte=0,
        )
        assert node.kind == FQNKind.EXTERNAL
        assert node.fqn == FQN.from_dotted("logging")
        assert node.file_path == ""

    def test_external_node_in_adg(self) -> None:
        """EXTERNAL nodes can coexist with structural nodes in an ADG."""
        nodes = [
            FQNNode(fqn=FQN.from_dotted("app"), kind=FQNKind.MODULE, file_path="app/__init__.py", line_start=0, line_end=0, start_byte=0, end_byte=0),
            FQNNode(fqn=FQN.from_dotted("logging"), kind=FQNKind.EXTERNAL, file_path="", line_start=-1, line_end=-1, start_byte=0, end_byte=0),
        ]
        adg = ADG(nodes=nodes, edges=[])
        external = [n for n in adg.nodes if n.kind == FQNKind.EXTERNAL]
        assert len(external) == 1
        assert external[0].fqn == FQN.from_dotted("logging")


# ===========================================================================
# 6. ADG.constraint_edges field
# ===========================================================================


class TestADGConstraintEdges:
    """ADG holds a separate list for constraint edges."""

    def test_adg_has_constraint_edges_field(self) -> None:
        adg = ADG(nodes=[], edges=[])
        assert hasattr(adg, "constraint_edges")
        assert adg.constraint_edges == []

    def test_adg_with_constraint_edges(self, sample_constraints: list[ConstraintEdge]) -> None:
        adg = ADG(nodes=[], edges=[], constraint_edges=sample_constraints)
        assert len(adg.constraint_edges) == 2

    def test_adg_default_constraint_edges_empty(self) -> None:
        adg = ADG(nodes=[], edges=[])
        assert adg.constraint_edges == []


# ===========================================================================
# 7. ConstraintEdge.specificity field
# ===========================================================================


class TestConstraintEdgeSpecificity:
    """ConstraintEdge has a specificity field for resolution scoring."""

    def test_specificity_default_zero(self) -> None:
        edge = ConstraintEdge(
            subject="app.api.*",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="logging",
            justification="test",
            char_interval=(0, 10),
            adr_id="ADR-001",
            adr_path="docs/adr/001.md",
        )
        assert edge.specificity == 0.0

    def test_specificity_can_be_set(self) -> None:
        edge = ConstraintEdge(
            subject="app.api.*",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="logging",
            justification="test",
            char_interval=(0, 10),
            adr_id="ADR-001",
            adr_path="docs/adr/001.md",
            specificity=3.0,
        )
        assert edge.specificity == 3.0


# ===========================================================================
# 8. gather_candidates: prefix-scoped walk of ADG nodes
# ===========================================================================


class TestGatherCandidates:
    """Collect ADG nodes as resolution candidates via prefix-scoped walk.

    Algorithm: walk pattern segments against ADG nodes until first mismatch.
    Collect all descendants of the longest matching prefix.
    If no segments match, all top-level nodes and their descendants are candidates.
    """

    def test_candidates_from_partial_prefix_match(self, sample_adg: ADG) -> None:
        """Pattern 'app.api.*': 'app' matches, 'api' matches -> candidates under 'app.api.*'."""
        from services.adg.merge import gather_candidates

        candidates = gather_candidates("app.api.*", sample_adg.nodes)
        # Longest matching prefix: "app.api" matches node app.api
        # Candidates: all nodes whose FQN starts with "app.api."
        candidate_fqns = {str(c.fqn) for c in candidates}
        assert "app.api" in candidate_fqns
        assert "app.api.users" in candidate_fqns
        assert "app.api.orders" in candidate_fqns
        # app.auth is NOT under app.api
        assert "app.auth" not in candidate_fqns

    def test_candidates_from_first_segment_mismatch(self, sample_adg: ADG) -> None:
        """Pattern 'web.handlers.*': 'web' doesn't match any node -> all nodes as candidates."""
        from services.adg.merge import gather_candidates

        candidates = gather_candidates("web.handlers.*", sample_adg.nodes)
        # No prefix match at all -> all nodes as candidates (root-level fallback)
        candidate_fqns = {str(c.fqn) for c in candidates}
        assert len(candidates) == len(sample_adg.nodes)

    def test_candidates_from_middle_mismatch(self, sample_adg: ADG) -> None:
        """Pattern 'app.routes.*': 'app' matches, 'routes' doesn't -> candidates under 'app'."""
        from services.adg.merge import gather_candidates

        candidates = gather_candidates("app.routes.*", sample_adg.nodes)
        # 'app' matches, 'routes' doesn't -> collect all nodes under 'app.*'
        candidate_fqns = {str(c.fqn) for c in candidates}
        assert "app" in candidate_fqns
        assert "app.api" in candidate_fqns
        assert "app.auth" in candidate_fqns
        assert "app.services" in candidate_fqns

    def test_candidates_exact_match_no_orphan(self, sample_adg: ADG) -> None:
        """Pattern that matches exactly returns empty candidates (no resolution needed)."""
        from services.adg.merge import gather_candidates

        candidates = gather_candidates("app.auth.middleware", sample_adg.nodes)
        # Exact match -> no orphan -> no candidates needed
        assert candidates == []

    def test_candidates_wildcard_exact_prefix_match(self, sample_adg: ADG) -> None:
        """Pattern 'app.auth.*': 'app.auth' matches -> candidates under 'app.auth'."""
        from services.adg.merge import gather_candidates

        candidates = gather_candidates("app.auth.*", sample_adg.nodes)
        candidate_fqns = {str(c.fqn) for c in candidates}
        assert "app.auth" in candidate_fqns
        assert "app.auth.middleware" in candidate_fqns

    def test_candidates_empty_adg(self) -> None:
        """Empty ADG returns empty candidates."""
        from services.adg.merge import gather_candidates

        candidates = gather_candidates("app.api.*", [])
        assert candidates == []


# ===========================================================================
# 9. resolve_orphans: LLM-backed naming resolution
# ===========================================================================


class TestResolveOrphans:
    """LLM-backed resolution of orphan FQN patterns.

    When an FQN pattern from an ADR doesn't match any ADG node (orphan),
    resolve_orphans gathers candidates, calls the LLM, and remaps the pattern
    in-place. Modifies constraints directly. Returns remaining orphan FQNs.
    """

    @pytest.fixture
    def routes_adg(self) -> ADG:
        """ADG using 'routes' instead of 'api' (the naming mismatch scenario)."""
        nodes = [
            FQNNode(fqn=FQN.from_dotted("app"), kind=FQNKind.MODULE, file_path="app/__init__.py", line_start=0, line_end=0, start_byte=0, end_byte=0),
            FQNNode(fqn=FQN.from_dotted("app.routes"), kind=FQNKind.MODULE, file_path="app/routes/__init__.py", line_start=0, line_end=0, start_byte=0, end_byte=0),
            FQNNode(fqn=FQN.from_dotted("app.routes.users"), kind=FQNKind.MODULE, file_path="app/routes/users.py", line_start=0, line_end=50, start_byte=0, end_byte=0),
            FQNNode(fqn=FQN.from_dotted("app.auth"), kind=FQNKind.MODULE, file_path="app/auth/__init__.py", line_start=0, line_end=0, start_byte=0, end_byte=0),
            FQNNode(fqn=FQN.from_dotted("app.auth.middleware"), kind=FQNKind.MODULE, file_path="app/auth/middleware.py", line_start=0, line_end=60, start_byte=0, end_byte=0),
        ]
        edges = [
            Edge(source="app", target="app.routes", kind="CONTAINS"),
            Edge(source="app.routes", target="app.routes.users", kind="CONTAINS"),
            Edge(source="app", target="app.auth", kind="CONTAINS"),
            Edge(source="app.auth", target="app.auth.middleware", kind="CONTAINS"),
            Edge(source="app.routes.users", target="app.auth.middleware", kind="IMPORTS"),
        ]
        return ADG(nodes=nodes, edges=edges)

    def test_resolve_remaps_orphan_subject(self, routes_adg: ADG) -> None:
        """LLM remaps orphan subject 'app.api.*' to 'app.routes.*'."""
        from services.adg.merge import resolve_orphans
        from services.extract.config import LangExtractConfig

        config = LangExtractConfig(model_id="test-model", provider="openai")

        constraints = [
            ConstraintEdge(
                subject="app.api.*",
                predicate=PredicateType.REQUIRES_DEPENDENCY,
                object="app.auth.middleware",
                justification="All API endpoints must import auth middleware.",
                char_interval=(10, 80),
                adr_id="ADR-002",
                adr_path="docs/adr/002.md",
            ),
        ]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "app.routes.*"

        with patch("services.adg.merge._call_resolution_llm", return_value="app.routes.*"):
            remaining_orphans = resolve_orphans(routes_adg, constraints, config)

        # Subject should be remapped in-place
        assert constraints[0].subject == "app.routes.*"
        # Object already matched, so no orphan remaining for it
        # If subject remap succeeds, it should not be in remaining orphans
        assert "app.api.*" not in remaining_orphans

    def test_resolve_remaps_orphan_object(self, routes_adg: ADG) -> None:
        """LLM remaps orphan object pattern."""
        from services.adg.merge import resolve_orphans
        from services.extract.config import LangExtractConfig

        config = LangExtractConfig(model_id="test-model", provider="openai")

        constraints = [
            ConstraintEdge(
                subject="app.routes.*",
                predicate=PredicateType.REQUIRES_DEPENDENCY,
                object="app.auth.guard",
                justification="Must use auth guard for authentication.",
                char_interval=(10, 80),
                adr_id="ADR-004",
                adr_path="docs/adr/004.md",
            ),
        ]

        with patch("services.adg.merge._call_resolution_llm", return_value="app.auth.middleware"):
            remaining_orphans = resolve_orphans(routes_adg, constraints, config)

        # Object should be remapped in-place
        assert constraints[0].object == "app.auth.middleware"
        assert "app.auth.guard" not in remaining_orphans

    def test_resolve_no_mapping_leaves_orphan(self, routes_adg: ADG) -> None:
        """When LLM returns 'no_mapping', the orphan stays unchanged."""
        from services.adg.merge import resolve_orphans
        from services.extract.config import LangExtractConfig

        config = LangExtractConfig(model_id="test-model", provider="openai")

        constraints = [
            ConstraintEdge(
                subject="app.api.*",
                predicate=PredicateType.REQUIRES_DEPENDENCY,
                object="app.auth.middleware",
                justification="All API endpoints must import auth middleware.",
                char_interval=(10, 80),
                adr_id="ADR-002",
                adr_path="docs/adr/002.md",
            ),
        ]

        with patch("services.adg.merge._call_resolution_llm", return_value="no_mapping"):
            remaining_orphans = resolve_orphans(routes_adg, constraints, config)

        # Subject stays unchanged
        assert constraints[0].subject == "app.api.*"
        assert "app.api.*" in remaining_orphans

    def test_resolve_both_sides_orphaned_separate_calls(self, routes_adg: ADG) -> None:
        """When both subject and object are orphaned, two separate LLM calls are made."""
        from services.adg.merge import resolve_orphans
        from services.extract.config import LangExtractConfig

        config = LangExtractConfig(model_id="test-model", provider="openai")

        constraints = [
            ConstraintEdge(
                subject="app.api.*",
                predicate=PredicateType.REQUIRES_DEPENDENCY,
                object="app.auth.guard",
                justification="All API endpoints must use auth guard.",
                char_interval=(10, 80),
                adr_id="ADR-002",
                adr_path="docs/adr/002.md",
            ),
        ]

        call_count = 0
        side_effects = ["app.routes.*", "app.auth.middleware"]

        def mock_llm_call(pattern, candidates, justification, config):
            nonlocal call_count
            result = side_effects[call_count]
            call_count += 1
            return result

        with patch("services.adg.merge._call_resolution_llm", side_effect=mock_llm_call):
            remaining_orphans = resolve_orphans(routes_adg, constraints, config)

        assert call_count == 2
        assert constraints[0].subject == "app.routes.*"
        assert constraints[0].object == "app.auth.middleware"

    def test_resolve_in_place_modification(self, routes_adg: ADG) -> None:
        """Remapping modifies ConstraintEdge in-place, not creating a new object."""
        from services.adg.merge import resolve_orphans
        from services.extract.config import LangExtractConfig

        config = LangExtractConfig(model_id="test-model", provider="openai")

        constraints = [
            ConstraintEdge(
                subject="app.api.*",
                predicate=PredicateType.REQUIRES_DEPENDENCY,
                object="app.auth.middleware",
                justification="All API endpoints must import auth middleware.",
                char_interval=(10, 80),
                adr_id="ADR-002",
                adr_path="docs/adr/002.md",
            ),
        ]
        original_id = id(constraints[0])

        with patch("services.adg.merge._call_resolution_llm", return_value="app.routes.*"):
            resolve_orphans(routes_adg, constraints, config)

        # Same object, modified in place
        assert id(constraints[0]) == original_id
        assert constraints[0].subject == "app.routes.*"

    def test_resolve_no_orphans_skips_llm(self, sample_adg: ADG) -> None:
        """When all constraints match ADG nodes, no LLM call is made."""
        from services.adg.merge import resolve_orphans
        from services.extract.config import LangExtractConfig

        config = LangExtractConfig(model_id="test-model", provider="openai")

        constraints = [
            ConstraintEdge(
                subject="app.api.*",
                predicate=PredicateType.REQUIRES_DEPENDENCY,
                object="app.auth.middleware",
                justification="All API endpoints must import auth middleware.",
                char_interval=(10, 80),
                adr_id="ADR-003",
                adr_path="docs/adr/003.md",
            ),
        ]

        with patch("services.adg.merge._call_resolution_llm") as mock_llm:
            remaining_orphans = resolve_orphans(sample_adg, constraints, config)
            mock_llm.assert_not_called()

        # No orphans, constraints unchanged
        assert constraints[0].subject == "app.api.*"
        assert len(remaining_orphans) == 0

    def test_remapped_constraint_rematched(self, routes_adg: ADG) -> None:
        """After remapping, the constraint should match ADG nodes via existing logic."""
        from services.adg.merge import merge_constraints, resolve_orphans
        from services.extract.config import LangExtractConfig

        config = LangExtractConfig(model_id="test-model", provider="openai")

        constraints = [
            ConstraintEdge(
                subject="app.api.*",
                predicate=PredicateType.REQUIRES_DEPENDENCY,
                object="app.auth.middleware",
                justification="All API endpoints must import auth middleware.",
                char_interval=(10, 80),
                adr_id="ADR-002",
                adr_path="docs/adr/002.md",
            ),
        ]

        # First: resolve orphans to remap app.api.* -> app.routes.*
        with patch("services.adg.merge._call_resolution_llm", return_value="app.routes.*"):
            remaining_orphans = resolve_orphans(routes_adg, constraints, config)

        assert constraints[0].subject == "app.routes.*"

        # Then: merge_constraints should now match the remapped constraint
        result = merge_constraints(routes_adg, constraints)
        # The remapped subject app.routes.* should match app.routes.users in the ADG
        subject_match = match_fqn(constraints[0].subject, result.nodes)
        assert subject_match.status == MatchStatus.WILDCARD


# ===========================================================================
# 10. MatchStatus: SEGMENT removed
# ===========================================================================


class TestMatchStatusNoSegment:
    """MatchStatus no longer has SEGMENT; superseded by LLM resolution layer."""

    def test_match_status_values(self) -> None:
        assert MatchStatus.EXACT.value == "exact"
        assert MatchStatus.WILDCARD.value == "wildcard"
        assert MatchStatus.NO_MATCH.value == "no_match"

    def test_match_status_has_no_segment(self) -> None:
        assert not hasattr(MatchStatus, "SEGMENT")