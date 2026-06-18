"""Tests for the Merge Layer: unifying AST-derived ADG with ADR constraint edges.

Public interface under test:
    add_external_nodes: create EXTERNAL nodes for unmatched import targets
    merge_constraints: unify Track A ADG + Track B constraint edges into merged ADG
    resolve_orphans: LLM-backed naming resolution for orphan FQN patterns
    gather_candidates: collect ADG nodes as resolution candidates via prefix-scoped walk
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from services.fqn import FQN
from services.models import (
    ADG,
    ConstraintEdge,
    Edge,
    FQNKind,
    FQNNode,
    PredicateType,
)
from services.resolver import MatchStatus, NameResolver
from services.adg.merge import (
    add_external_nodes,
    merge_constraints,
    resolve_orphans,
    gather_candidates,
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
# 1. add_external_nodes: EXTERNAL nodes for unmatched imports
# ===========================================================================


class TestAddExternalNodes:
    """Unmatched import targets become EXTERNAL nodes."""

    def test_adds_external_for_stdlib(self, sample_adg: ADG) -> None:
        edges_with_import = sample_adg.edges + [
            Edge(source="app.services.user", target="logging", kind="IMPORTS"),
        ]
        adg = ADG(nodes=sample_adg.nodes, edges=edges_with_import)
        result = add_external_nodes(adg)
        external_nodes = [n for n in result.nodes if n.kind == FQNKind.EXTERNAL]
        assert len(external_nodes) == 1
        assert external_nodes[0].fqn == FQN.from_dotted("logging")

    def test_does_not_add_external_for_internal_imports(self, sample_adg: ADG) -> None:
        result = add_external_nodes(sample_adg)
        external_nodes = [n for n in result.nodes if n.kind == FQNKind.EXTERNAL]
        assert len(external_nodes) == 0

    def test_external_node_deduplication(self) -> None:
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
# 2. merge_constraints: unifying Track A + Track B
# ===========================================================================


class TestMergeConstraints:
    """Merge Layer combines ADG nodes/edges with constraint edges."""

    def test_merge_adds_constraint_edges_to_adg(self, sample_adg: ADG, sample_constraints: list[ConstraintEdge]) -> None:
        result = merge_constraints(sample_adg, sample_constraints)
        assert len(result.constraint_edges) == 2
        for edge in result.constraint_edges:
            assert edge.specificity > 0.0 or edge.specificity == 0.0

    def test_merge_preserves_structural_nodes_and_edges(self, sample_adg: ADG, sample_constraints: list[ConstraintEdge]) -> None:
        result = merge_constraints(sample_adg, sample_constraints)
        structural = [n for n in result.nodes if n.kind != FQNKind.EXTERNAL]
        assert len(structural) == len(sample_adg.nodes)
        assert len(result.edges) == len(sample_adg.edges)

    def test_merge_adds_external_for_orphan_references(self, sample_adg: ADG) -> None:
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
        result = merge_constraints(sample_adg, [])
        assert len(result.constraint_edges) == 0
        assert len(result.nodes) == len(sample_adg.nodes)
        assert len(result.edges) == len(sample_adg.edges)


class TestMergeConstraintsIncremental:
    """Full replace per ADR: delete old constraints, insert new ones."""

    def test_replace_constraints_by_adr_id(self, sample_adg: ADG) -> None:
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

        remaining = [ce for ce in merged.constraint_edges if ce.adr_id != "ADR-003"]
        adg_after_delete = ADG(
            nodes=merged.nodes,
            edges=merged.edges,
            constraint_edges=remaining,
        )
        result = merge_constraints(adg_after_delete, new_constraints)
        assert len(result.constraint_edges) == 2
        assert all(ce.adr_id == "ADR-003" for ce in result.constraint_edges)

    def test_other_adr_constraints_preserved(self, sample_adg: ADG) -> None:
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

        adr5_edges = [ce for ce in result.constraint_edges if ce.adr_id == "ADR-005"]
        assert len(adr5_edges) == 1
        assert adr5_edges[0].predicate is PredicateType.PROHIBITS_DEPENDENCY


# ===========================================================================
# 3. FQNKind.EXTERNAL
# ===========================================================================


class TestFQNKindExternal:
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

    def test_external_node_in_adg(self) -> None:
        nodes = [
            FQNNode(fqn=FQN.from_dotted("app"), kind=FQNKind.MODULE, file_path="app/__init__.py", line_start=0, line_end=0, start_byte=0, end_byte=0),
            FQNNode(fqn=FQN.from_dotted("logging"), kind=FQNKind.EXTERNAL, file_path="", line_start=-1, line_end=-1, start_byte=0, end_byte=0),
        ]
        adg = ADG(nodes=nodes, edges=[])
        external = [n for n in adg.nodes if n.kind == FQNKind.EXTERNAL]
        assert len(external) == 1
        assert external[0].fqn == FQN.from_dotted("logging")


# ===========================================================================
# 4. ADG.constraint_edges field
# ===========================================================================


class TestADGConstraintEdges:
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
# 5. ConstraintEdge.specificity field
# ===========================================================================


class TestConstraintEdgeSpecificity:
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
# 6. gather_candidates: prefix-scoped walk of ADG nodes
# ===========================================================================


class TestGatherCandidates:
    def test_candidates_from_partial_prefix_match(self, sample_adg: ADG) -> None:
        candidates = gather_candidates("app.api.*", sample_adg.nodes)
        candidate_fqns = {str(c.fqn) for c in candidates}
        assert "app.api" in candidate_fqns
        assert "app.api.users" in candidate_fqns
        assert "app.api.orders" in candidate_fqns
        assert "app.auth" not in candidate_fqns

    def test_candidates_from_first_segment_mismatch(self, sample_adg: ADG) -> None:
        candidates = gather_candidates("web.handlers.*", sample_adg.nodes)
        candidate_fqns = {str(c.fqn) for c in candidates}
        assert len(candidate_fqns) == len(sample_adg.nodes)

    def test_candidates_from_middle_mismatch(self, sample_adg: ADG) -> None:
        candidates = gather_candidates("app.routes.*", sample_adg.nodes)
        candidate_fqns = {str(c.fqn) for c in candidates}
        assert "app" in candidate_fqns
        assert "app.api" in candidate_fqns
        assert "app.auth" in candidate_fqns
        assert "app.services" in candidate_fqns

    def test_candidates_exact_match_no_orphan(self, sample_adg: ADG) -> None:
        candidates = gather_candidates("app.auth.middleware", sample_adg.nodes)
        assert candidates == []

    def test_candidates_wildcard_exact_prefix_match(self, sample_adg: ADG) -> None:
        candidates = gather_candidates("app.auth.*", sample_adg.nodes)
        candidate_fqns = {str(c.fqn) for c in candidates}
        assert "app.auth" in candidate_fqns
        assert "app.auth.middleware" in candidate_fqns

    def test_candidates_empty_adg(self) -> None:
        candidates = gather_candidates("app.api.*", [])
        assert candidates == []


# ===========================================================================
# 7. resolve_orphans: LLM-backed naming resolution with injectable callback
# ===========================================================================


class TestResolveOrphans:
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
        """LLM callback remaps orphan subject 'app.api.*' to 'app.routes.*'."""
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

        def mock_llm(pattern: str, candidates: list, justification: str) -> str:
            return "app.routes.*"

        resolver = NameResolver({n.fqn for n in routes_adg.nodes})
        remaining_orphans = resolve_orphans(routes_adg, constraints, resolver, llm_resolver=mock_llm)
        assert constraints[0].subject == "app.routes.*"
        assert "app.api.*" not in remaining_orphans

    def test_resolve_remaps_orphan_object(self, routes_adg: ADG) -> None:
        """LLM callback remaps orphan object pattern."""
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

        def mock_llm(pattern: str, candidates: list, justification: str) -> str:
            return "app.auth.middleware"

        resolver = NameResolver({n.fqn for n in routes_adg.nodes})
        remaining_orphans = resolve_orphans(routes_adg, constraints, resolver, llm_resolver=mock_llm)
        assert constraints[0].object == "app.auth.middleware"
        assert "app.auth.guard" not in remaining_orphans

    def test_resolve_no_mapping_leaves_orphan(self, routes_adg: ADG) -> None:
        """When LLM callback returns 'no_mapping', the orphan stays unchanged."""
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

        def mock_llm(pattern: str, candidates: list, justification: str) -> str:
            return "no_mapping"

        resolver = NameResolver({n.fqn for n in routes_adg.nodes})
        remaining_orphans = resolve_orphans(routes_adg, constraints, resolver, llm_resolver=mock_llm)
        assert constraints[0].subject == "app.api.*"
        assert "app.api.*" in remaining_orphans

    def test_resolve_both_sides_orphaned_separate_calls(self, routes_adg: ADG) -> None:
        """When both subject and object are orphaned, two separate LLM calls are made."""
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

        def mock_llm(pattern: str, candidates: list, justification: str) -> str:
            nonlocal call_count
            result = side_effects[call_count]
            call_count += 1
            return result

        resolver = NameResolver({n.fqn for n in routes_adg.nodes})
        remaining_orphans = resolve_orphans(routes_adg, constraints, resolver, llm_resolver=mock_llm)
        assert call_count == 2
        assert constraints[0].subject == "app.routes.*"
        assert constraints[0].object == "app.auth.middleware"

    def test_resolve_in_place_modification(self, routes_adg: ADG) -> None:
        """Remapping modifies ConstraintEdge in-place."""
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

        def mock_llm(pattern: str, candidates: list, justification: str) -> str:
            return "app.routes.*"

        resolver = NameResolver({n.fqn for n in routes_adg.nodes})
        resolve_orphans(routes_adg, constraints, resolver, llm_resolver=mock_llm)
        assert id(constraints[0]) == original_id
        assert constraints[0].subject == "app.routes.*"

    def test_resolve_no_orphans_skips_llm(self, sample_adg: ADG) -> None:
        """When all constraints match ADG nodes, no LLM callback is made."""
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

        call_count = 0
        def mock_llm(pattern: str, candidates: list, justification: str) -> str:
            nonlocal call_count
            call_count += 1
            return "no_mapping"

        resolver = NameResolver({n.fqn for n in sample_adg.nodes})
        remaining_orphans = resolve_orphans(sample_adg, constraints, resolver, llm_resolver=mock_llm)
        assert call_count == 0
        assert constraints[0].subject == "app.api.*"
        assert len(remaining_orphans) == 0

    def test_resolve_no_llm_resolver_tracks_orphans(self, routes_adg: ADG) -> None:
        """Without an LLM resolver, orphans are just tracked, not remapped."""
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

        resolver = NameResolver({n.fqn for n in routes_adg.nodes})
        remaining_orphans = resolve_orphans(routes_adg, constraints, resolver, llm_resolver=None)
        assert "app.api.*" in remaining_orphans
        assert constraints[0].subject == "app.api.*"

    def test_remapped_constraint_rematched(self, routes_adg: ADG) -> None:
        """After remapping, the constraint should match ADG nodes via resolver."""
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

        def mock_llm(pattern: str, candidates: list, justification: str) -> str:
            return "app.routes.*"

        resolver = NameResolver({n.fqn for n in routes_adg.nodes})
        resolve_orphans(routes_adg, constraints, resolver, llm_resolver=mock_llm)
        assert constraints[0].subject == "app.routes.*"

        # After remapping, merge_constraints should match
        result = merge_constraints(routes_adg, constraints)
        report = NameResolver({n.fqn for n in result.nodes}).match(constraints[0].subject)
        assert report.status == MatchStatus.WILDCARD