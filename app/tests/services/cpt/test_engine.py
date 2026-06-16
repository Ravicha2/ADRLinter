"""Tests for CPT engine: BFS traversal, constraint retrieval, predicate checking,
basic resolution, and detect integration.

Public interface under test:
    bfs_neighborhood: k-hop BFS from changed FQNs
    retrieve_constraints: FQN-first matching against constraint patterns
    check_predicates: evaluate constraints against structural facts
    resolve: specificity conflict + dedup
    detect: full CPT pipeline
"""

from __future__ import annotations

import pytest

from services.fqn import FQN
from services.models import (
    ADG,
    ChangedFQN,
    ConstraintEdge,
    DiffResult,
    Edge,
    FileChange,
    FQNKind,
    FQNNode,
    PredicateType,
)


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def sample_adg() -> ADG:
    """A small ADG with structural edges for CPT tests.

    Structure:
        app
        ├── api
        │   ├── users
        │   └── orders
        ├── auth
        │   └── middleware
        ├── middleware
        │   └── auth
        ├── services
        │   └── user
        └── models
            └── user

    Key structural edges:
        app.api.users IMPORTS app.auth.middleware
        app.services.user IMPORTS app.auth.middleware
        app.api.orders CALLS app.models.user
    """
    nodes = [
        FQNNode(fqn=FQN.from_dotted("app"), kind=FQNKind.MODULE, file_path="app/__init__.py", line_start=0, line_end=0, start_byte=0, end_byte=0),
        FQNNode(fqn=FQN.from_dotted("app.api"), kind=FQNKind.MODULE, file_path="app/api/__init__.py", line_start=0, line_end=0, start_byte=0, end_byte=0),
        FQNNode(fqn=FQN.from_dotted("app.api.users"), kind=FQNKind.MODULE, file_path="app/api/users.py", line_start=0, line_end=50, start_byte=0, end_byte=0),
        FQNNode(fqn=FQN.from_dotted("app.api.orders"), kind=FQNKind.MODULE, file_path="app/api/orders.py", line_start=0, line_end=40, start_byte=0, end_byte=0),
        FQNNode(fqn=FQN.from_dotted("app.auth"), kind=FQNKind.MODULE, file_path="app/auth/__init__.py", line_start=0, line_end=0, start_byte=0, end_byte=0),
        FQNNode(fqn=FQN.from_dotted("app.auth.middleware"), kind=FQNKind.MODULE, file_path="app/auth/middleware.py", line_start=0, line_end=60, start_byte=0, end_byte=0),
        FQNNode(fqn=FQN.from_dotted("app.middleware.auth"), kind=FQNKind.MODULE, file_path="app/middleware/auth.py", line_start=0, line_end=55, start_byte=0, end_byte=0),
        FQNNode(fqn=FQN.from_dotted("app.services"), kind=FQNKind.MODULE, file_path="app/services/__init__.py", line_start=0, line_end=0, start_byte=0, end_byte=0),
        FQNNode(fqn=FQN.from_dotted("app.services.user"), kind=FQNKind.MODULE, file_path="app/services/user.py", line_start=0, line_end=80, start_byte=0, end_byte=0),
        FQNNode(fqn=FQN.from_dotted("app.models"), kind=FQNKind.MODULE, file_path="app/models/__init__.py", line_start=0, line_end=0, start_byte=0, end_byte=0),
        FQNNode(fqn=FQN.from_dotted("app.models.user"), kind=FQNKind.MODULE, file_path="app/models/user.py", line_start=0, line_end=30, start_byte=0, end_byte=0),
    ]
    edges = [
        Edge(source="app", target="app.api", kind="CONTAINS"),
        Edge(source="app.api", target="app.api.users", kind="CONTAINS"),
        Edge(source="app.api", target="app.api.orders", kind="CONTAINS"),
        Edge(source="app", target="app.auth", kind="CONTAINS"),
        Edge(source="app.auth", target="app.auth.middleware", kind="CONTAINS"),
        Edge(source="app", target="app.middleware", kind="CONTAINS"),
        Edge(source="app.middleware", target="app.middleware.auth", kind="CONTAINS"),
        Edge(source="app", target="app.services", kind="CONTAINS"),
        Edge(source="app.services", target="app.services.user", kind="CONTAINS"),
        Edge(source="app", target="app.models", kind="CONTAINS"),
        Edge(source="app.models", target="app.models.user", kind="CONTAINS"),
        # cross-module edges
        Edge(source="app.api.users", target="app.auth.middleware", kind="IMPORTS"),
        Edge(source="app.services.user", target="app.auth.middleware", kind="IMPORTS"),
        Edge(source="app.api.orders", target="app.models.user", kind="CALLS"),
    ]
    return ADG(nodes=nodes, edges=edges)


@pytest.fixture
def sample_constraints() -> list[ConstraintEdge]:
    """Constraint edges from ADR extraction."""
    return [
        ConstraintEdge(
            subject="app.api.*",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="app.models.*",
            justification="API layer must not directly depend on models.",
            adr_id="ADR-003",
            adr_path="docs/adr/003-layering.md",
            specificity=0.0,
        ),
        ConstraintEdge(
            subject="app.api.*",
            predicate=PredicateType.REQUIRES_DEPENDENCY,
            object="app.auth.middleware",
            justification="All API endpoints must import auth middleware.",
            adr_id="ADR-004",
            adr_path="docs/adr/004-auth.md",
            specificity=0.0,
        ),
        ConstraintEdge(
            subject="app.*",
            predicate=PredicateType.PROHIBITS_IMPLEMENTATION,
            object="app.auth.middleware",
            justification="Only middleware package may implement auth.",
            adr_id="ADR-005",
            adr_path="docs/adr/005-auth-impl.md",
            specificity=0.0,
        ),
        ConstraintEdge(
            subject="app.middleware",
            predicate=PredicateType.REQUIRES_IMPLEMENTATION,
            object="app.auth.middleware",
            justification="Middleware must implement auth handling.",
            adr_id="ADR-005",
            adr_path="docs/adr/005-auth-impl.md",
            specificity=0.0,
        ),
    ]


def _changed_fqn(fqn_str: str, change_type: str = "modified") -> ChangedFQN:
    """Helper to create a ChangedFQN for testing."""
    fqn = FQN.from_dotted(fqn_str)
    return ChangedFQN(
        fqn=fqn,
        change_type=change_type,
        file_path=f"{'/'.join(fqn.parts)}.py",
        enclosing_module=fqn.parent if fqn.parent else fqn,
    )


# ===========================================================================
# 1. bfs_neighborhood: k-hop BFS from changed FQNs
# ===========================================================================


class TestBfsNeighborhood:
    """BFS traversal from changed FQNs, both directions, k-hop bounded."""

    def test_single_fqn_no_edges(self) -> None:
        from services.cpt.engine import bfs_neighborhood

        adg = ADG(
            nodes=[FQNNode(fqn=FQN.from_dotted("app"), kind=FQNKind.MODULE, file_path="app/__init__.py", line_start=0, line_end=0, start_byte=0, end_byte=0)],
            edges=[],
        )
        changed = [_changed_fqn("app")]
        neighborhood, reachable = bfs_neighborhood(adg, changed, k=3)
        assert FQN.from_dotted("app") in neighborhood
        assert len(reachable) == 0

    def test_one_hop_outward(self, sample_adg: ADG) -> None:
        from services.cpt.engine import bfs_neighborhood

        changed = [_changed_fqn("app.api.users")]
        neighborhood, reachable = bfs_neighborhood(sample_adg, changed, k=1)
        # changed FQN itself is in neighborhood
        assert FQN.from_dotted("app.api.users") in neighborhood
        # 1-hop outward: app.api.users IMPORTS app.auth.middleware
        assert FQN.from_dotted("app.auth.middleware") in neighborhood
        # 1-hop inward: app.api CONTAINS app.api.users
        assert FQN.from_dotted("app.api") in neighborhood

    def test_two_hop(self, sample_adg: ADG) -> None:
        from services.cpt.engine import bfs_neighborhood

        changed = [_changed_fqn("app.api.users")]
        neighborhood, reachable = bfs_neighborhood(sample_adg, changed, k=2)
        # 2-hop from app.api.users:
        # hop 1 outward: app.auth.middleware
        # hop 2 outward: app.auth (CONTAINS app.auth.middleware, inward direction)
        assert FQN.from_dotted("app.auth") in neighborhood
        # hop 1 inward: app.api
        # hop 2 inward: app (CONTAINS app.api)
        assert FQN.from_dotted("app") in neighborhood

    def test_three_hop_limit(self, sample_adg: ADG) -> None:
        from services.cpt.engine import bfs_neighborhood

        changed = [_changed_fqn("app.api.users")]
        neighborhood_k3, _ = bfs_neighborhood(sample_adg, changed, k=3)
        neighborhood_k1, _ = bfs_neighborhood(sample_adg, changed, k=1)
        assert len(neighborhood_k3) >= len(neighborhood_k1)

    def test_multiple_changed_fqns(self, sample_adg: ADG) -> None:
        from services.cpt.engine import bfs_neighborhood

        changed = [_changed_fqn("app.api.users"), _changed_fqn("app.api.orders")]
        neighborhood, reachable = bfs_neighborhood(sample_adg, changed, k=1)
        # Both changed FQNs in neighborhood
        assert FQN.from_dotted("app.api.users") in neighborhood
        assert FQN.from_dotted("app.api.orders") in neighborhood

    def test_reachable_edges_captured(self, sample_adg: ADG) -> None:
        from services.cpt.engine import bfs_neighborhood

        changed = [_changed_fqn("app.api.users")]
        neighborhood, reachable = bfs_neighborhood(sample_adg, changed, k=1)
        # IMPORTS edge from app.api.users to app.auth.middleware should be in reachable
        import_edge = Edge(source="app.api.users", target="app.auth.middleware", kind="IMPORTS")
        assert import_edge in reachable

    def test_inward_direction_catches_callers(self, sample_adg: ADG) -> None:
        from services.cpt.engine import bfs_neighborhood

        changed = [_changed_fqn("app.auth.middleware")]
        neighborhood, _ = bfs_neighborhood(sample_adg, changed, k=1)
        # Inward: who imports/calls app.auth.middleware?
        # app.api.users IMPORTS app.auth.middleware (inward direction)
        # app.services.user IMPORTS app.auth.middleware (inward direction)
        assert FQN.from_dotted("app.api.users") in neighborhood
        assert FQN.from_dotted("app.services.user") in neighborhood


# ===========================================================================
# 2. retrieve_constraints: FQN-first matching against constraint patterns
# ===========================================================================


class TestRetrieveConstraints:
    """For each FQN in neighborhood, match against constraint subject/object."""

    def test_neighborhood_fqn_matches_constraint_subject(self, sample_adg: ADG, sample_constraints: list[ConstraintEdge]) -> None:
        from services.cpt.engine import bfs_neighborhood, retrieve_constraints

        adg_with_constraints = ADG(
            nodes=sample_adg.nodes,
            edges=sample_adg.edges,
            constraint_edges=sample_constraints,
        )
        changed = [_changed_fqn("app.api.users")]
        neighborhood, _ = bfs_neighborhood(adg_with_constraints, changed, k=1)
        retrieved = retrieve_constraints(neighborhood, adg_with_constraints)
        # app.api.users matches "app.api.*" subject on ADR-003 and ADR-004
        retrieved_adr_ids = {r.constraint.adr_id for r in retrieved}
        assert "ADR-003" in retrieved_adr_ids
        assert "ADR-004" in retrieved_adr_ids

    def test_neighborhood_fqn_matches_constraint_object(self, sample_adg: ADG, sample_constraints: list[ConstraintEdge]) -> None:
        from services.cpt.engine import bfs_neighborhood, retrieve_constraints

        adg_with_constraints = ADG(
            nodes=sample_adg.nodes,
            edges=sample_adg.edges,
            constraint_edges=sample_constraints,
        )
        changed = [_changed_fqn("app.api.users")]
        neighborhood, _ = bfs_neighborhood(adg_with_constraints, changed, k=1)
        retrieved = retrieve_constraints(neighborhood, adg_with_constraints)
        # app.auth.middleware is in neighborhood and matches object of ADR-004
        retrieved_adr_ids = {r.constraint.adr_id for r in retrieved}
        assert "ADR-004" in retrieved_adr_ids

    def test_no_constraints_matched(self, sample_adg: ADG) -> None:
        from services.cpt.engine import bfs_neighborhood, retrieve_constraints

        constraints = [
            ConstraintEdge(
                subject="app.nonexistent.*",
                predicate=PredicateType.PROHIBITS_DEPENDENCY,
                object="app.also.nonexistent",
                justification="test",
                adr_id="ADR-999",
                adr_path="docs/adr/999.md",
            ),
        ]
        adg = ADG(nodes=sample_adg.nodes, edges=sample_adg.edges, constraint_edges=constraints)
        changed = [_changed_fqn("app.api.users")]
        neighborhood, _ = bfs_neighborhood(adg, changed, k=1)
        retrieved = retrieve_constraints(neighborhood, adg)
        assert len(retrieved) == 0

    def test_segment_match_retrieves_constraint(self, sample_adg: ADG) -> None:
        from services.cpt.engine import bfs_neighborhood, retrieve_constraints

        constraints = [
            ConstraintEdge(
                subject="app.middleware.auth",
                predicate=PredicateType.REQUIRES_IMPLEMENTATION,
                object="app.auth",
                justification="test",
                adr_id="ADR-010",
                adr_path="docs/adr/010.md",
            ),
        ]
        adg = ADG(nodes=sample_adg.nodes, edges=sample_adg.edges, constraint_edges=constraints)
        # app.middleware.auth exists as a node, should segment-match to "app.middleware.auth"
        changed = [_changed_fqn("app.middleware.auth")]
        neighborhood, _ = bfs_neighborhood(adg, changed, k=1)
        retrieved = retrieve_constraints(neighborhood, adg)
        assert any(r.constraint.adr_id == "ADR-010" for r in retrieved)


# ===========================================================================
# 3. check_predicates: evaluate constraints against structural facts
# ===========================================================================


class TestCheckPredicates:
    """Each PredicateType checked against reachable edges."""

    def test_prohibits_dependency_violated(self) -> None:
        from services.cpt.engine import check_predicates
        from services.matching import MatchStatus

        constraint = ConstraintEdge(
            subject="app.api.*",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="app.models.*",
            justification="test",
            adr_id="ADR-003",
            adr_path="docs/adr/003.md",
        )
        reachable_edges = {
            Edge(source="app.api.users", target="app.models.user", kind="IMPORTS"),
        }
        violations = check_predicates(
            constraints_with_matches=[(constraint, FQN.from_dotted("app.api.users"), FQN.from_dotted("app.models.user"), MatchStatus.WILDCARD)],
            reachable_edges=reachable_edges,
            changed_fqn=FQN.from_dotted("app.api.users"),
            change_type="modified",
        )
        assert len(violations) == 1
        assert violations[0].constraint.adr_id == "ADR-003"

    def test_prohibits_dependency_not_violated(self) -> None:
        from services.cpt.engine import check_predicates
        from services.matching import MatchStatus

        constraint = ConstraintEdge(
            subject="app.api.*",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="app.models.*",
            justification="test",
            adr_id="ADR-003",
            adr_path="docs/adr/003.md",
        )
        reachable_edges = {
            Edge(source="app.api.users", target="app.auth.middleware", kind="IMPORTS"),
        }
        violations = check_predicates(
            constraints_with_matches=[(constraint, FQN.from_dotted("app.api.users"), FQN.from_dotted("app.models.user"), MatchStatus.WILDCARD)],
            reachable_edges=reachable_edges,
            changed_fqn=FQN.from_dotted("app.api.users"),
            change_type="modified",
        )
        assert len(violations) == 0

    def test_requires_dependency_violated(self) -> None:
        from services.cpt.engine import check_predicates
        from services.matching import MatchStatus

        constraint = ConstraintEdge(
            subject="app.api.*",
            predicate=PredicateType.REQUIRES_DEPENDENCY,
            object="app.auth.middleware",
            justification="test",
            adr_id="ADR-004",
            adr_path="docs/adr/004.md",
        )
        reachable_edges: set[Edge] = set()
        violations = check_predicates(
            constraints_with_matches=[(constraint, FQN.from_dotted("app.api.orders"), FQN.from_dotted("app.auth.middleware"), MatchStatus.WILDCARD)],
            reachable_edges=reachable_edges,
            changed_fqn=FQN.from_dotted("app.api.orders"),
            change_type="modified",
        )
        assert len(violations) == 1
        assert violations[0].constraint.adr_id == "ADR-004"

    def test_requires_dependency_not_violated(self) -> None:
        from services.cpt.engine import check_predicates
        from services.matching import MatchStatus

        constraint = ConstraintEdge(
            subject="app.api.*",
            predicate=PredicateType.REQUIRES_DEPENDENCY,
            object="app.auth.middleware",
            justification="test",
            adr_id="ADR-004",
            adr_path="docs/adr/004.md",
        )
        reachable_edges = {
            Edge(source="app.api.users", target="app.auth.middleware", kind="IMPORTS"),
        }
        violations = check_predicates(
            constraints_with_matches=[(constraint, FQN.from_dotted("app.api.users"), FQN.from_dotted("app.auth.middleware"), MatchStatus.WILDCARD)],
            reachable_edges=reachable_edges,
            changed_fqn=FQN.from_dotted("app.api.users"),
            change_type="modified",
        )
        assert len(violations) == 0

    def test_prohibits_implementation_violated(self) -> None:
        from services.cpt.engine import check_predicates
        from services.matching import MatchStatus

        constraint = ConstraintEdge(
            subject="app.*",
            predicate=PredicateType.PROHIBITS_IMPLEMENTATION,
            object="app.auth.middleware",
            justification="test",
            adr_id="ADR-005",
            adr_path="docs/adr/005.md",
        )
        reachable_edges = {
            Edge(source="app.api", target="app.auth.middleware", kind="CONTAINS"),
        }
        violations = check_predicates(
            constraints_with_matches=[(constraint, FQN.from_dotted("app.api"), FQN.from_dotted("app.auth.middleware"), MatchStatus.WILDCARD)],
            reachable_edges=reachable_edges,
            changed_fqn=FQN.from_dotted("app.api"),
            change_type="modified",
        )
        assert len(violations) == 1

    def test_requires_implementation_violated(self) -> None:
        from services.cpt.engine import check_predicates
        from services.matching import MatchStatus

        constraint = ConstraintEdge(
            subject="app.middleware",
            predicate=PredicateType.REQUIRES_IMPLEMENTATION,
            object="app.auth.middleware",
            justification="test",
            adr_id="ADR-005",
            adr_path="docs/adr/005.md",
        )
        reachable_edges: set[Edge] = set()
        violations = check_predicates(
            constraints_with_matches=[(constraint, FQN.from_dotted("app.middleware"), FQN.from_dotted("app.auth.middleware"), MatchStatus.EXACT)],
            reachable_edges=reachable_edges,
            changed_fqn=FQN.from_dotted("app.middleware"),
            change_type="modified",
        )
        assert len(violations) == 1

    def test_requires_implementation_not_violated(self) -> None:
        from services.cpt.engine import check_predicates
        from services.matching import MatchStatus

        constraint = ConstraintEdge(
            subject="app.middleware",
            predicate=PredicateType.REQUIRES_IMPLEMENTATION,
            object="app.auth.middleware",
            justification="test",
            adr_id="ADR-005",
            adr_path="docs/adr/005.md",
        )
        reachable_edges = {
            Edge(source="app.middleware", target="app.middleware.auth", kind="CONTAINS"),
            Edge(source="app.middleware.auth", target="app.auth.middleware", kind="CALLS"),
        }
        violations = check_predicates(
            constraints_with_matches=[(constraint, FQN.from_dotted("app.middleware"), FQN.from_dotted("app.auth.middleware"), MatchStatus.EXACT)],
            reachable_edges=reachable_edges,
            changed_fqn=FQN.from_dotted("app.middleware"),
            change_type="modified",
        )
        assert len(violations) == 0


# ===========================================================================
# 4. resolve: specificity conflict + deduplication
# ===========================================================================


class TestResolve:
    """Basic resolution: specificity conflicts and deduplication."""

    def _make_violation(
        self,
        subject: str,
        predicate: PredicateType,
        object: str,
        specificity: float,
        matched_fqn: str,
        adr_id: str = "ADR-001",
    ) -> "Violation":
        from services.cpt.engine import Violation
        from services.matching import MatchStatus

        constraint = ConstraintEdge(
            subject=subject,
            predicate=predicate,
            object=object,
            justification="test",
            adr_id=adr_id,
            adr_path=f"docs/adr/{adr_id}.md",
            specificity=specificity,
        )
        return Violation(
            constraint=constraint,
            changed_fqn=FQN.from_dotted("app.api.users"),
            matched_fqn=FQN.from_dotted(matched_fqn),
            match_status=MatchStatus.EXACT,
            evidence="test evidence",
            change_type="modified",
        )

    def test_specificity_higher_wins(self) -> None:
        from services.cpt.engine import resolve

        # app.* PROHIBITS_IMPLEMENTATION app.auth (low specificity)
        # app.middleware REQUIRES_IMPLEMENTATION app.auth (high specificity)
        v_prohibit = self._make_violation(
            "app.*", PredicateType.PROHIBITS_IMPLEMENTATION, "app.auth.middleware",
            specificity=1.0, matched_fqn="app.api.users", adr_id="ADR-005",
        )
        v_require = self._make_violation(
            "app.middleware", PredicateType.REQUIRES_IMPLEMENTATION, "app.auth.middleware",
            specificity=3.0, matched_fqn="app.middleware", adr_id="ADR-005",
        )
        result = resolve([v_prohibit, v_require])
        # Higher specificity wins: REQUIRES stays, PROHIBITS is suppressed
        result_predicates = {v.constraint.predicate for v in result}
        assert PredicateType.REQUIRES_IMPLEMENTATION in result_predicates
        assert PredicateType.PROHIBITS_IMPLEMENTATION not in result_predicates

    def test_dedup_same_constraint_same_matched_fqn(self) -> None:
        from services.cpt.engine import resolve

        v1 = self._make_violation(
            "app.api.*", PredicateType.PROHIBITS_DEPENDENCY, "app.models.*",
            specificity=2.5, matched_fqn="app.api.users",
        )
        v2 = self._make_violation(
            "app.api.*", PredicateType.PROHIBITS_DEPENDENCY, "app.models.*",
            specificity=2.5, matched_fqn="app.api.users",
        )
        result = resolve([v1, v2])
        # Same (constraint, matched_fqn) should be deduped to 1
        assert len(result) == 1

    def test_different_matched_fqns_not_deduped(self) -> None:
        from services.cpt.engine import resolve

        v1 = self._make_violation(
            "app.api.*", PredicateType.PROHIBITS_DEPENDENCY, "app.models.*",
            specificity=2.5, matched_fqn="app.api.users",
        )
        v2 = self._make_violation(
            "app.api.*", PredicateType.PROHIBITS_DEPENDENCY, "app.models.*",
            specificity=2.5, matched_fqn="app.api.orders",
        )
        result = resolve([v1, v2])
        # Different matched FQNs are separate violations
        assert len(result) == 2

    def test_no_conflict_passes_through(self) -> None:
        from services.cpt.engine import resolve

        v1 = self._make_violation(
            "app.api.*", PredicateType.PROHIBITS_DEPENDENCY, "app.models.*",
            specificity=2.5, matched_fqn="app.api.users",
        )
        result = resolve([v1])
        assert len(result) == 1


# ===========================================================================
# 5. detect: full CPT pipeline
# ===========================================================================


class TestDetect:
    """Integration: detect(diff_result, adg) -> CPTResult."""

    def test_detect_prohibits_dependency_violation(self, sample_adg: ADG, sample_constraints: list[ConstraintEdge]) -> None:
        from services.cpt.engine import CPTResult, detect

        adg = ADG(
            nodes=sample_adg.nodes,
            edges=sample_adg.edges,
            constraint_edges=sample_constraints,
        )
        diff = DiffResult(
            commit_sha="abc123",
            parent_sha="def456",
            changed_files=[FileChange(path="app/api/orders.py", status="modified")],
            changed_fqns=[_changed_fqn("app.api.orders")],
        )
        result = detect(diff, adg, k=3)
        assert isinstance(result, CPTResult)
        # app.api.orders CALLS app.models.user, violating "app.api.* PROHIBITS_DEPENDENCY app.models.*"
        prohibit_violations = [v for v in result.violations if v.constraint.predicate == PredicateType.PROHIBITS_DEPENDENCY]
        assert len(prohibit_violations) >= 1

    def test_detect_requires_dependency_satisfied(self, sample_adg: ADG, sample_constraints: list[ConstraintEdge]) -> None:
        from services.cpt.engine import detect

        adg = ADG(
            nodes=sample_adg.nodes,
            edges=sample_adg.edges,
            constraint_edges=sample_constraints,
        )
        diff = DiffResult(
            commit_sha="abc123",
            parent_sha="def456",
            changed_files=[FileChange(path="app/api/users.py", status="modified")],
            changed_fqns=[_changed_fqn("app.api.users")],
        )
        result = detect(diff, adg, k=3)
        # app.api.users already IMPORTS app.auth.middleware, so ADR-004 is satisfied
        require_violations = [v for v in result.violations if v.constraint.predicate == PredicateType.REQUIRES_DEPENDENCY and v.constraint.adr_id == "ADR-004"]
        assert len(require_violations) == 0

    def test_detect_no_violations_clean(self, sample_adg: ADG) -> None:
        from services.cpt.engine import detect

        adg = ADG(nodes=sample_adg.nodes, edges=sample_adg.edges, constraint_edges=[])
        diff = DiffResult(
            commit_sha="abc123",
            parent_sha="def456",
            changed_files=[FileChange(path="app/api/users.py", status="modified")],
            changed_fqns=[_changed_fqn("app.api.users")],
        )
        result = detect(diff, adg, k=3)
        assert len(result.violations) == 0

    def test_detect_orphans_reported(self, sample_adg: ADG) -> None:
        from services.cpt.engine import detect

        orphan_constraint = ConstraintEdge(
            subject="app.nonexistent",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="also.nonexistent",
            justification="test",
            adr_id="ADR-999",
            adr_path="docs/adr/999.md",
        )
        adg = ADG(
            nodes=sample_adg.nodes,
            edges=sample_adg.edges,
            constraint_edges=[orphan_constraint],
        )
        diff = DiffResult(
            commit_sha="abc123",
            parent_sha="def456",
            changed_files=[FileChange(path="app/api/users.py", status="modified")],
            changed_fqns=[_changed_fqn("app.api.users")],
        )
        result = detect(diff, adg, k=3)
        assert len(result.orphans) >= 1
        assert any(c.adr_id == "ADR-999" for c in result.orphans)

    def test_detect_neighborhood_in_result(self, sample_adg: ADG, sample_constraints: list[ConstraintEdge]) -> None:
        from services.cpt.engine import detect

        adg = ADG(
            nodes=sample_adg.nodes,
            edges=sample_adg.edges,
            constraint_edges=sample_constraints,
        )
        diff = DiffResult(
            commit_sha="abc123",
            parent_sha="def456",
            changed_files=[FileChange(path="app/api/users.py", status="modified")],
            changed_fqns=[_changed_fqn("app.api.users")],
        )
        result = detect(diff, adg, k=3)
        assert len(result.neighborhood) > 0
        assert FQN.from_dotted("app.api.users") in result.neighborhood

    def test_detect_specificity_resolution(self, sample_adg: ADG) -> None:
        from services.cpt.engine import detect

        # Two conflicting constraints from same ADR
        constraints = [
            ConstraintEdge(
                subject="app.*",
                predicate=PredicateType.PROHIBITS_IMPLEMENTATION,
                object="app.auth.middleware",
                justification="Only middleware may implement auth.",
                adr_id="ADR-005",
                adr_path="docs/adr/005.md",
                specificity=1.0,
            ),
            ConstraintEdge(
                subject="app.middleware",
                predicate=PredicateType.REQUIRES_IMPLEMENTATION,
                object="app.auth.middleware",
                justification="Middleware must implement auth.",
                adr_id="ADR-005",
                adr_path="docs/adr/005.md",
                specificity=2.0,
            ),
        ]
        adg = ADG(
            nodes=sample_adg.nodes,
            edges=sample_adg.edges,
            constraint_edges=constraints,
        )
        diff = DiffResult(
            commit_sha="abc123",
            parent_sha="def456",
            changed_files=[FileChange(path="app/middleware/auth.py", status="modified")],
            changed_fqns=[_changed_fqn("app.middleware.auth")],
        )
        result = detect(diff, adg, k=3)
        # REQUIRES_IMPLEMENTATION (higher specificity) should win over PROHIBITS_IMPLEMENTATION
        prohibit_violations = [v for v in result.violations if v.constraint.predicate == PredicateType.PROHIBITS_IMPLEMENTATION]
        require_violations = [v for v in result.violations if v.constraint.predicate == PredicateType.REQUIRES_IMPLEMENTATION]
        # PROHIBITS should be suppressed by higher specificity REQUIRES
        assert len(prohibit_violations) == 0


# ===========================================================================
# 6. Data models: Violation and CPTResult exist with correct shape
# ===========================================================================


class TestCptDataModels:
    """Violation and CPTResult dataclass shape."""

    def test_violation_fields(self) -> None:
        from services.cpt.engine import Violation
        from services.matching import MatchStatus

        constraint = ConstraintEdge(
            subject="app.api.*",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="app.models.*",
            justification="test",
            adr_id="ADR-003",
            adr_path="docs/adr/003.md",
        )
        v = Violation(
            constraint=constraint,
            changed_fqn=FQN.from_dotted("app.api.users"),
            matched_fqn=FQN.from_dotted("app.api.users"),
            match_status=MatchStatus.WILDCARD,
            evidence="app.api.users IMPORTS app.models.user",
            change_type="modified",
        )
        assert v.constraint.adr_id == "ADR-003"
        assert v.changed_fqn == FQN.from_dotted("app.api.users")
        assert v.matched_fqn == FQN.from_dotted("app.api.users")
        assert v.match_status == MatchStatus.WILDCARD
        assert v.evidence == "app.api.users IMPORTS app.models.user"
        assert v.change_type == "modified"

    def test_cpt_result_fields(self) -> None:
        from services.cpt.engine import CPTResult, Violation
        from services.matching import MatchStatus

        result = CPTResult(
            violations=[],
            orphans=[],
            neighborhood={FQN.from_dotted("app")},
        )
        assert result.violations == []
        assert result.orphans == []
        assert FQN.from_dotted("app") in result.neighborhood