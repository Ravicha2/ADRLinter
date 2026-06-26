"""Tests for the Merge Layer: symbolic constraint resolution and ADG merge.

Public interface under test:
    add_external_nodes: create EXTERNAL nodes for unmatched import targets
    resolve_symbolic_constraints: resolve SymbolicConstraints against ADG
    merge_constraints: unify Track A ADG + Track B symbolic constraints into merged ADG
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
    ResolvedConstraint,
    SymbolicConstraint,
)
from services.adg.merge import add_external_nodes, merge_constraints
from services.adg.symbolic_resolver import resolve_symbolic_constraints


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
def sample_symbolic_constraints() -> list[SymbolicConstraint]:
    """Symbolic constraints from ADR extraction."""
    return [
        SymbolicConstraint(
            subject_role_general="app.api",
            subject_role_specific="endpoint",
            predicate=PredicateType.REQUIRES_IMPLEMENTATION,
            object_role_general="app.auth",
            object_role_specific="authentication middleware",
            justification="All API endpoints must implement authentication.",
            extraction_text="All API endpoints shall implement authentication",
            adr_id="ADR-003",
            adr_path="docs/adr/003-auth-middleware.md",
        ),
        SymbolicConstraint(
            subject_role_general="app.services",
            subject_role_specific="service",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object_role_general="logging",
            object_role_specific="bare logging",
            justification="No service shall use bare logging directly.",
            extraction_text="No service shall use bare logging",
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
# 2. resolve_symbolic_constraints: symbolic resolution
# ===========================================================================


class TestResolveSymbolicConstraints:
    """Resolve SymbolicConstraints against ADG nodes."""

    def test_resolve_exact_match(self, sample_adg: ADG) -> None:
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
        assert resolved[0].constraint_edge.subject.startswith("app.api")
        assert resolved[0].constraint_edge.predicate is PredicateType.PROHIBITS_DEPENDENCY
        assert resolved[0].object_matched_by == "external"

    def test_resolve_general_wildcard(self, sample_adg: ADG) -> None:
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

    def test_resolve_no_match_skips(self, sample_adg: ADG) -> None:
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

    def test_external_dependency_creates_external_node(self, sample_adg: ADG) -> None:
        sc = SymbolicConstraint(
            subject_role_general="app.services",
            subject_role_specific="service",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object_role_general="mysql",
            object_role_specific="MySQL connector",
            justification="No direct MySQL.",
            extraction_text="No direct MySQL connections",
            adr_id="ADR-001",
            adr_path="docs/adr/001.md",
        )
        resolved = resolve_symbolic_constraints([sc], sample_adg)
        assert len(resolved) >= 1
        assert resolved[0].object_matched_by == "external"

    def test_resolve_implementation_predicate_matches_class(self) -> None:
        """requires_implementation should match class/function/method nodes."""
        nodes = [
            FQNNode(fqn=FQN.from_dotted("app"), kind=FQNKind.MODULE, file_path="app/__init__.py", line_start=0, line_end=0, start_byte=0, end_byte=0),
            FQNNode(fqn=FQN.from_dotted("app.auth"), kind=FQNKind.MODULE, file_path="app/auth/__init__.py", line_start=0, line_end=0, start_byte=0, end_byte=0),
            FQNNode(fqn=FQN.from_dotted("app.auth.Middleware"), kind=FQNKind.CLASS, file_path="app/auth/middleware.py", line_start=0, line_end=60, start_byte=0, end_byte=1200),
        ]
        edges = [
            Edge(source="app", target="app.auth", kind="CONTAINS"),
            Edge(source="app.auth", target="app.auth.Middleware", kind="CONTAINS"),
        ]
        adg = ADG(nodes=nodes, edges=edges)

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
        resolved = resolve_symbolic_constraints([sc], adg)
        assert len(resolved) >= 1
        # Object should match the class node
        object_fqns = {rc.constraint_edge.object for rc in resolved}
        assert "app.auth.Middleware" in object_fqns


# ===========================================================================
# 3. merge_constraints: unifying Track A + Track B (now via SymbolicConstraint)
# ===========================================================================


class TestMergeConstraints:
    """Merge Layer combines ADG nodes/edges with resolved constraint edges."""

    def test_merge_adds_constraint_edges_to_adg(self, sample_adg: ADG, sample_symbolic_constraints: list[SymbolicConstraint]) -> None:
        result = merge_constraints(sample_adg, sample_symbolic_constraints)
        assert len(result.constraint_edges) >= 2

    def test_merge_preserves_structural_nodes_and_edges(self, sample_adg: ADG, sample_symbolic_constraints: list[SymbolicConstraint]) -> None:
        result = merge_constraints(sample_adg, sample_symbolic_constraints)
        structural = [n for n in result.nodes if n.kind != FQNKind.EXTERNAL]
        assert len(structural) == len(sample_adg.nodes)
        assert len(result.edges) == len(sample_adg.edges)

    def test_merge_adds_external_for_orphan_references(self, sample_adg: ADG) -> None:
        sc = SymbolicConstraint(
            subject_role_general="app.services",
            subject_role_specific="service",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object_role_general="logging",
            object_role_specific="bare logging",
            justification="No bare logging.",
            extraction_text="No bare logging",
            adr_id="ADR-005",
            adr_path="docs/adr/005-logging.md",
        )
        result = merge_constraints(sample_adg, [sc])
        external_nodes = [n for n in result.nodes if n.kind == FQNKind.EXTERNAL]
        assert any(n.fqn == FQN.from_dotted("logging") for n in external_nodes)

    def test_merge_empty_constraints(self, sample_adg: ADG) -> None:
        result = merge_constraints(sample_adg, [])
        assert len(result.constraint_edges) == 0
        assert len(result.nodes) == len(sample_adg.nodes)
        assert len(result.edges) == len(sample_adg.edges)


class TestMergeConstraintsIncremental:
    """Full replace per ADR: delete old constraints, insert new ones."""

    def test_replace_constraints_by_adr_id(self, sample_adg: ADG) -> None:
        old_constraints = [
            SymbolicConstraint(
                subject_role_general="app.api",
                subject_role_specific="endpoint",
                predicate=PredicateType.PROHIBITS_DEPENDENCY,
                object_role_general="mysql",
                object_role_specific="MySQL connector",
                justification="Old: no direct MySQL.",
                extraction_text="No direct MySQL",
                adr_id="ADR-003",
                adr_path="docs/adr/003-auth-middleware.md",
            ),
        ]
        merged = merge_constraints(sample_adg, old_constraints)
        assert len(merged.constraint_edges) >= 1

        new_constraints = [
            SymbolicConstraint(
                subject_role_general="app.api",
                subject_role_specific="endpoint",
                predicate=PredicateType.REQUIRES_DEPENDENCY,
                object_role_general="app.auth",
                object_role_specific="auth middleware",
                justification="Updated: dependency, not implementation.",
                extraction_text="Updated dependency",
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
        assert len(result.constraint_edges) >= 1

    def test_other_adr_constraints_preserved(self, sample_adg: ADG) -> None:
        constraints_adr3 = [
            SymbolicConstraint(
                subject_role_general="app.api",
                subject_role_specific="endpoint",
                predicate=PredicateType.REQUIRES_IMPLEMENTATION,
                object_role_general="app.auth",
                object_role_specific="authentication middleware",
                justification="Auth required.",
                extraction_text="Auth required",
                adr_id="ADR-003",
                adr_path="docs/adr/003.md",
            ),
        ]
        constraints_adr5 = [
            SymbolicConstraint(
                subject_role_general="app.services",
                subject_role_specific="service",
                predicate=PredicateType.PROHIBITS_DEPENDENCY,
                object_role_general="logging",
                object_role_specific="bare logging",
                justification="No bare logging.",
                extraction_text="No bare logging",
                adr_id="ADR-005",
                adr_path="docs/adr/005.md",
            ),
        ]
        merged = merge_constraints(sample_adg, constraints_adr3 + constraints_adr5)
        assert len(merged.constraint_edges) >= 2

        new_adr3 = [
            SymbolicConstraint(
                subject_role_general="app.api",
                subject_role_specific="endpoint",
                predicate=PredicateType.REQUIRES_DEPENDENCY,
                object_role_general="app.auth",
                object_role_specific="auth middleware",
                justification="Updated specific rule.",
                extraction_text="Updated rule",
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
        assert len(adr5_edges) >= 1
        assert adr5_edges[0].predicate is PredicateType.PROHIBITS_DEPENDENCY


# ===========================================================================
# 4. FQNKind.EXTERNAL
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
# 5. ADG.constraint_edges field
# ===========================================================================


class TestADGConstraintEdges:
    def test_adg_has_constraint_edges_field(self) -> None:
        adg = ADG(nodes=[], edges=[])
        assert hasattr(adg, "constraint_edges")
        assert adg.constraint_edges == []

    def test_adg_with_constraint_edges(self) -> None:
        edges = [
            ConstraintEdge(
                subject="app.api.*",
                predicate=PredicateType.REQUIRES_IMPLEMENTATION,
                object="app.auth.middleware",
                justification="Auth required.",
                adr_id="ADR-003",
                adr_path="docs/adr/003.md",
            ),
            ConstraintEdge(
                subject="app.services.*",
                predicate=PredicateType.PROHIBITS_DEPENDENCY,
                object="logging",
                justification="No bare logging.",
                adr_id="ADR-005",
                adr_path="docs/adr/005.md",
            ),
        ]
        adg = ADG(nodes=[], edges=[], constraint_edges=edges)
        assert len(adg.constraint_edges) == 2

    def test_adg_default_constraint_edges_empty(self) -> None:
        adg = ADG(nodes=[], edges=[])
        assert adg.constraint_edges == []


# ===========================================================================
# 6. ConstraintEdge.specificity field
# ===========================================================================


class TestConstraintEdgeSpecificity:
    def test_specificity_default_zero(self) -> None:
        edge = ConstraintEdge(
            subject="app.api.*",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="logging",
            justification="test",
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
            adr_id="ADR-001",
            adr_path="docs/adr/001.md",
            specificity=3.0,
        )
        assert edge.specificity == 3.0