"""Tests for Neo4j graph store: schema, CRUD, and ADG persistence.

These tests require a running Neo4j instance. Mark with @pytest.mark.integration
to skip in environments without Neo4j.

Public interface under test:
    GraphStore: manages Neo4j connection and operations
    create_schema: set up indexes and constraints
    store_adg: write an ADG (nodes + edges + constraint_edges) to Neo4j
    load_adg: read an ADG from Neo4j
    delete_nodes_by_file: remove all nodes/edges for a file path
    delete_constraints_by_adr: remove all constraint edges for an ADR ID
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
def sample_adg_with_constraints() -> ADG:
    """ADG with structural nodes/edges and constraint edges."""
    nodes = [
        FQNNode(fqn=FQN.from_dotted("app"), kind=FQNKind.MODULE, file_path="app/__init__.py", line_start=0, line_end=0, start_byte=0, end_byte=0),
        FQNNode(fqn=FQN.from_dotted("app.api"), kind=FQNKind.MODULE, file_path="app/api/__init__.py", line_start=0, line_end=0, start_byte=0, end_byte=0),
        FQNNode(fqn=FQN.from_dotted("app.api.users"), kind=FQNKind.MODULE, file_path="app/api/users.py", line_start=0, line_end=50, start_byte=0, end_byte=1000),
        FQNNode(fqn=FQN.from_dotted("app.auth"), kind=FQNKind.MODULE, file_path="app/auth/__init__.py", line_start=0, line_end=0, start_byte=0, end_byte=0),
        FQNNode(fqn=FQN.from_dotted("app.auth.middleware"), kind=FQNKind.MODULE, file_path="app/auth/middleware.py", line_start=0, line_end=60, start_byte=0, end_byte=1200),
        FQNNode(fqn=FQN.from_dotted("logging"), kind=FQNKind.EXTERNAL, file_path="", line_start=-1, line_end=-1, start_byte=0, end_byte=0),
    ]
    edges = [
        Edge(source="app", target="app.api", kind="CONTAINS"),
        Edge(source="app.api", target="app.api.users", kind="CONTAINS"),
        Edge(source="app", target="app.auth", kind="CONTAINS"),
        Edge(source="app.auth", target="app.auth.middleware", kind="CONTAINS"),
        Edge(source="app.api.users", target="app.auth.middleware", kind="IMPORTS"),
        Edge(source="app.api.users", target="logging", kind="IMPORTS"),
    ]
    constraints = [
        ConstraintEdge(
            subject="app.api.*",
            predicate=PredicateType.REQUIRES_IMPLEMENTATION,
            object="app.auth.middleware",
            justification="All API endpoints must implement authentication.",
            char_interval=(10, 80),
            adr_id="ADR-003",
            adr_path="docs/adr/003-auth-middleware.md",
            specificity=2.5,
        ),
        ConstraintEdge(
            subject="app.services.*",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="logging",
            justification="No service shall use bare logging directly.",
            char_interval=(20, 90),
            adr_id="ADR-005",
            adr_path="docs/adr/005-centralized-logging.md",
            specificity=2.5,
        ),
    ]
    return ADG(nodes=nodes, edges=edges, constraint_edges=constraints)


# ===========================================================================
# 1. Schema creation
# ===========================================================================


@pytest.mark.integration
class TestSchema:
    """Neo4j schema: unique constraint on fqn, index on file_path."""

    def test_unique_constraint_on_fqn(self, neo4j_store) -> None:
        """Creating a node with duplicate fqn should fail."""
        neo4j_store.create_schema()
        neo4j_store.store_node(FQNNode(
            fqn=FQN.from_dotted("app.api"),
            kind=FQNKind.MODULE,
            file_path="app/api/__init__.py",
            line_start=0, line_end=0, start_byte=0, end_byte=0,
        ))
        with pytest.raises(Exception):
            neo4j_store.store_node(FQNNode(
                fqn=FQN.from_dotted("app.api"),
                kind=FQNKind.MODULE,
                file_path="app/api/__init__.py",
                line_start=0, line_end=0, start_byte=0, end_byte=0,
            ))

    def test_index_on_file_path(self, neo4j_store) -> None:
        """Nodes can be queried by file_path efficiently."""
        neo4j_store.create_schema()
        node = FQNNode(
            fqn=FQN.from_dotted("app.api.users"),
            kind=FQNKind.MODULE,
            file_path="app/api/users.py",
            line_start=0, line_end=50, start_byte=0, end_byte=1000,
        )
        neo4j_store.store_node(node)
        found = neo4j_store.find_nodes_by_file("app/api/users.py")
        assert len(found) >= 1
        assert found[0].fqn == FQN.from_dotted("app.api.users")


# ===========================================================================
# 2. Node CRUD
# ===========================================================================


@pytest.mark.integration
class TestNodeCRUD:
    """Store and retrieve FQNNodes from Neo4j."""

    def test_store_and_load_module_node(self, neo4j_store) -> None:
        node = FQNNode(
            fqn=FQN.from_dotted("flask"),
            kind=FQNKind.MODULE,
            file_path="flask/__init__.py",
            line_start=0, line_end=100, start_byte=0, end_byte=5000,
        )
        neo4j_store.store_node(node)
        loaded = neo4j_store.load_node(FQN.from_dotted("flask"))
        assert loaded is not None
        assert loaded.fqn == node.fqn
        assert loaded.kind == FQNKind.MODULE
        assert loaded.file_path == "flask/__init__.py"

    def test_store_and_load_external_node(self, neo4j_store) -> None:
        """EXTERNAL nodes are stored with sentinel values for file_path/lines."""
        node = FQNNode(
            fqn=FQN.from_dotted("logging"),
            kind=FQNKind.EXTERNAL,
            file_path="",
            line_start=-1, line_end=-1, start_byte=0, end_byte=0,
        )
        neo4j_store.store_node(node)
        loaded = neo4j_store.load_node(FQN.from_dotted("logging"))
        assert loaded is not None
        assert loaded.kind == FQNKind.EXTERNAL
        assert loaded.file_path == ""

    def test_load_nonexistent_node_returns_none(self, neo4j_store) -> None:
        result = neo4j_store.load_node(FQN.from_dotted("nonexistent.module"))
        assert result is None


# ===========================================================================
# 3. Edge CRUD
# ===========================================================================


@pytest.mark.integration
class TestEdgeCRUD:
    """Store and retrieve structural edges from Neo4j."""

    def test_store_and_load_contains_edge(self, neo4j_store) -> None:
        parent = FQNNode(
            fqn=FQN.from_dotted("app"), kind=FQNKind.MODULE,
            file_path="app/__init__.py", line_start=0, line_end=0, start_byte=0, end_byte=0,
        )
        child = FQNNode(
            fqn=FQN.from_dotted("app.api"), kind=FQNKind.MODULE,
            file_path="app/api/__init__.py", line_start=0, line_end=0, start_byte=0, end_byte=0,
        )
        neo4j_store.store_node(parent)
        neo4j_store.store_node(child)
        neo4j_store.store_edge(Edge(source="app", target="app.api", kind="CONTAINS"))

        edges = neo4j_store.load_edges_from(FQN.from_dotted("app"))
        contains_edges = [e for e in edges if e.kind == "CONTAINS"]
        assert len(contains_edges) == 1
        assert contains_edges[0].target == "app.api"


# ===========================================================================
# 4. Constraint edge CRUD
# ===========================================================================


@pytest.mark.integration
class TestConstraintEdgeCRUD:
    """Store and retrieve constraint edges from Neo4j."""

    def test_store_and_load_constraint_edge(self, neo4j_store) -> None:
        neo4j_store.store_node(FQNNode(
            fqn=FQN.from_dotted("app.api.users"), kind=FQNKind.MODULE,
            file_path="app/api/users.py", line_start=0, line_end=50, start_byte=0, end_byte=0,
        ))
        neo4j_store.store_node(FQNNode(
            fqn=FQN.from_dotted("app.auth.middleware"), kind=FQNKind.MODULE,
            file_path="app/auth/middleware.py", line_start=0, line_end=60, start_byte=0, end_byte=0,
        ))
        ce = ConstraintEdge(
            subject="app.api.users",
            predicate=PredicateType.REQUIRES_IMPLEMENTATION,
            object="app.auth.middleware",
            justification="Auth required.",
            char_interval=(10, 80),
            adr_id="ADR-003",
            adr_path="docs/adr/003.md",
            specificity=4.0,
        )
        neo4j_store.store_constraint_edge(ce)

        constraints = neo4j_store.load_constraint_edges(adr_id="ADR-003")
        assert len(constraints) == 1
        assert constraints[0].subject == "app.api.users"
        assert constraints[0].predicate == PredicateType.REQUIRES_IMPLEMENTATION
        assert constraints[0].object == "app.auth.middleware"
        assert constraints[0].specificity == 4.0

    def test_delete_constraints_by_adr_id(self, neo4j_store) -> None:
        """Full replace per ADR: delete all constraints for a given ADR."""
        neo4j_store.store_node(FQNNode(
            fqn=FQN.from_dotted("app.api.users"), kind=FQNKind.MODULE,
            file_path="app/api/users.py", line_start=0, line_end=50, start_byte=0, end_byte=0,
        ))
        neo4j_store.store_node(FQNNode(
            fqn=FQN.from_dotted("logging"), kind=FQNKind.EXTERNAL,
            file_path="", line_start=-1, line_end=-1, start_byte=0, end_byte=0,
        ))

        ce1 = ConstraintEdge(
            subject="app.api.users", predicate=PredicateType.REQUIRES_IMPLEMENTATION,
            object="app.auth.middleware", justification="Auth.",
            char_interval=(10, 80), adr_id="ADR-003", adr_path="docs/adr/003.md", specificity=4.0,
        )
        ce2 = ConstraintEdge(
            subject="app.services.*", predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="logging", justification="No bare logging.",
            char_interval=(20, 90), adr_id="ADR-005", adr_path="docs/adr/005.md", specificity=2.5,
        )
        neo4j_store.store_constraint_edge(ce1)
        neo4j_store.store_constraint_edge(ce2)

        # Delete only ADR-003 constraints
        neo4j_store.delete_constraints_by_adr("ADR-003")

        # ADR-005 constraint survives
        remaining = neo4j_store.load_all_constraint_edges()
        assert len(remaining) == 1
        assert remaining[0].adr_id == "ADR-005"


# ===========================================================================
# 5. Full ADG store and load
# ===========================================================================


@pytest.mark.integration
class TestADGPersistence:
    """Store and load a complete ADG (nodes, edges, constraint_edges)."""

    def test_store_and_load_full_adg(self, neo4j_store, sample_adg_with_constraints: ADG) -> None:
        neo4j_store.store_adg(sample_adg_with_constraints)
        loaded = neo4j_store.load_adg()

        assert len(loaded.nodes) == len(sample_adg_with_constraints.nodes)
        assert len(loaded.edges) == len(sample_adg_with_constraints.edges)
        assert len(loaded.constraint_edges) == len(sample_adg_with_constraints.constraint_edges)

        # Verify all nodes are present
        loaded_fqns = {str(n.fqn) for n in loaded.nodes}
        expected_fqns = {str(n.fqn) for n in sample_adg_with_constraints.nodes}
        assert loaded_fqns == expected_fqns

    def test_delete_nodes_by_file_path(self, neo4j_store, sample_adg_with_constraints: ADG) -> None:
        """Delete-and-reinsert: remove all nodes for a file, then re-add."""
        neo4j_store.store_adg(sample_adg_with_constraints)

        # Delete nodes for app/api/users.py
        neo4j_store.delete_nodes_by_file("app/api/users.py")

        loaded = neo4j_store.load_adg()
        # app.api.users node is gone
        assert not any(n.fqn == FQN.from_dotted("app.api.users") for n in loaded.nodes)
        # Other nodes remain
        assert any(n.fqn == FQN.from_dotted("app.api") for n in loaded.nodes)