"""Tests for CPT engine: BFS traversal, constraint matching, predicate checking,
and detect integration.

Public interface under test:
    bfs_neighborhood: k-hop BFS from changed FQNs
    match_constraints: single-pass constraint matching against neighborhood
    check_structural_predicates: PROHIBITS_* evaluation (no changed_fqn)
    check_change_triggered_predicates: REQUIRES_* evaluation (per changed_fqn)
    detect: full CPT pipeline

Resolution logic tested separately in test_resolution.py.
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
        assert FQN.from_dotted("app.api.users") in neighborhood
        assert FQN.from_dotted("app.auth.middleware") in neighborhood
        assert FQN.from_dotted("app.api") in neighborhood

    def test_two_hop(self, sample_adg: ADG) -> None:
        from services.cpt.engine import bfs_neighborhood

        changed = [_changed_fqn("app.api.users")]
        neighborhood, reachable = bfs_neighborhood(sample_adg, changed, k=2)
        assert FQN.from_dotted("app.auth") in neighborhood
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
        assert FQN.from_dotted("app.api.users") in neighborhood
        assert FQN.from_dotted("app.api.orders") in neighborhood

    def test_reachable_edges_captured(self, sample_adg: ADG) -> None:
        from services.cpt.engine import bfs_neighborhood

        changed = [_changed_fqn("app.api.users")]
        neighborhood, reachable = bfs_neighborhood(sample_adg, changed, k=1)
        import_edge = Edge(source="app.api.users", target="app.auth.middleware", kind="IMPORTS")
        assert import_edge in reachable

    def test_inward_direction_catches_callers(self, sample_adg: ADG) -> None:
        from services.cpt.engine import bfs_neighborhood

        changed = [_changed_fqn("app.auth.middleware")]
        neighborhood, _ = bfs_neighborhood(sample_adg, changed, k=1)
        assert FQN.from_dotted("app.api.users") in neighborhood
        assert FQN.from_dotted("app.services.user") in neighborhood

    def test_all_neighborhood_edges_captured(self, sample_adg: ADG) -> None:
        """Correctness: edges between two neighborhood nodes found via different
        routes must appear in reachable, even if neither was a discovery edge
        during BFS expansion."""
        from services.cpt.engine import bfs_neighborhood

        # With k=2 from app.api.orders, we reach both app.api and app.models.user.
        # The CONTAINS edge app.api -> app.api.orders has both endpoints in
        # neighborhood but was NOT a discovery edge (app.api.orders was the seed).
        changed = [_changed_fqn("app.api.orders")]
        neighborhood, reachable = bfs_neighborhood(sample_adg, changed, k=2)
        contains_edge = Edge(source="app.api", target="app.api.orders", kind="CONTAINS")
        assert contains_edge in reachable


# ===========================================================================
# 2. match_constraints: single-pass matching
# ===========================================================================


class TestMatchConstraints:
    """For each constraint, match neighborhood FQNs against subject/object."""

    def test_neighborhood_fqn_matches_constraint_subject(self, sample_adg: ADG, sample_constraints: list[ConstraintEdge]) -> None:
        from services.cpt.engine import bfs_neighborhood, match_constraints

        adg_with_constraints = ADG(
            nodes=sample_adg.nodes,
            edges=sample_adg.edges,
            constraint_edges=sample_constraints,
        )
        # k=3 so app.models.user (object of ADR-003) is reachable
        changed = [_changed_fqn("app.api.users")]
        neighborhood, _ = bfs_neighborhood(adg_with_constraints, changed, k=3)
        matched = match_constraints(neighborhood, adg_with_constraints)
        matched_adr_ids = {mc.constraint.adr_id for mc in matched.values()}
        assert "ADR-003" in matched_adr_ids
        assert "ADR-004" in matched_adr_ids

    def test_neighborhood_fqn_matches_constraint_object(self, sample_adg: ADG, sample_constraints: list[ConstraintEdge]) -> None:
        from services.cpt.engine import bfs_neighborhood, match_constraints

        adg_with_constraints = ADG(
            nodes=sample_adg.nodes,
            edges=sample_adg.edges,
            constraint_edges=sample_constraints,
        )
        changed = [_changed_fqn("app.api.users")]
        neighborhood, _ = bfs_neighborhood(adg_with_constraints, changed, k=1)
        matched = match_constraints(neighborhood, adg_with_constraints)
        # app.auth.middleware is in neighborhood and matches object of ADR-004
        matched_adr_ids = {mc.constraint.adr_id for mc in matched.values()}
        assert "ADR-004" in matched_adr_ids

    def test_no_constraints_matched(self, sample_adg: ADG) -> None:
        from services.cpt.engine import bfs_neighborhood, match_constraints

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
        matched = match_constraints(neighborhood, adg)
        assert len(matched) == 0

    def test_constraint_with_empty_subject_bucket_is_orphan(self, sample_adg: ADG) -> None:
        from services.cpt.engine import match_constraints

        constraints = [
            ConstraintEdge(
                subject="app.nonexistent.*",
                predicate=PredicateType.PROHIBITS_DEPENDENCY,
                object="app.auth.middleware",
                justification="test",
                adr_id="ADR-999",
                adr_path="docs/adr/999.md",
            ),
        ]
        adg = ADG(nodes=sample_adg.nodes, edges=sample_adg.edges, constraint_edges=constraints)
        # Small neighborhood that has object match but no subject match
        neighborhood = {FQN.from_dotted("app.auth.middleware")}
        matched = match_constraints(neighborhood, adg)
        assert len(matched) == 0


# ===========================================================================
# 3. check_structural_predicates: PROHIBITS_* evaluation
# ===========================================================================


class TestCheckStructuralPredicates:
    """PROHIBITS_* constraints evaluated without changed_fqn."""

    def test_prohibits_dependency_violated(self) -> None:
        from services.cpt.engine import MatchedConstraint, check_structural_predicates, _build_adjacency
        from services.matching import MatchStatus

        constraint = ConstraintEdge(
            subject="app.api.*",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="app.models.*",
            justification="test",
            adr_id="ADR-003",
            adr_path="docs/adr/003.md",
        )
        adjacency = _build_adjacency({
            Edge(source="app.api.users", target="app.models.user", kind="IMPORTS"),
        })
        matched = {
            id(constraint): MatchedConstraint(
                constraint=constraint,
                subject_matches=[(FQN.from_dotted("app.api.users"), MatchStatus.WILDCARD)],
                object_matches=[(FQN.from_dotted("app.models.user"), MatchStatus.WILDCARD)],
            ),
        }
        violations = check_structural_predicates(matched, adjacency)
        assert len(violations) == 1
        assert violations[0].constraint.adr_id == "ADR-003"

    def test_prohibits_dependency_not_violated(self) -> None:
        from services.cpt.engine import MatchedConstraint, check_structural_predicates, _build_adjacency
        from services.matching import MatchStatus

        constraint = ConstraintEdge(
            subject="app.api.*",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="app.models.*",
            justification="test",
            adr_id="ADR-003",
            adr_path="docs/adr/003.md",
        )
        adjacency = _build_adjacency({
            Edge(source="app.api.users", target="app.auth.middleware", kind="IMPORTS"),
        })
        matched = {
            id(constraint): MatchedConstraint(
                constraint=constraint,
                subject_matches=[(FQN.from_dotted("app.api.users"), MatchStatus.WILDCARD)],
                object_matches=[(FQN.from_dotted("app.models.user"), MatchStatus.WILDCARD)],
            ),
        }
        violations = check_structural_predicates(matched, adjacency)
        assert len(violations) == 0

    def test_prohibits_implementation_violated(self) -> None:
        from services.cpt.engine import MatchedConstraint, check_structural_predicates, _build_adjacency
        from services.matching import MatchStatus

        constraint = ConstraintEdge(
            subject="app.*",
            predicate=PredicateType.PROHIBITS_IMPLEMENTATION,
            object="app.auth.middleware",
            justification="test",
            adr_id="ADR-005",
            adr_path="docs/adr/005.md",
        )
        adjacency = _build_adjacency({
            Edge(source="app.api", target="app.auth.middleware", kind="CONTAINS"),
        })
        matched = {
            id(constraint): MatchedConstraint(
                constraint=constraint,
                subject_matches=[(FQN.from_dotted("app.api"), MatchStatus.WILDCARD)],
                object_matches=[(FQN.from_dotted("app.auth.middleware"), MatchStatus.EXACT)],
            ),
        }
        violations = check_structural_predicates(matched, adjacency)
        assert len(violations) == 1


# ===========================================================================
# 4. check_change_triggered_predicates: REQUIRES_* evaluation
# ===========================================================================


class TestCheckChangeTriggeredPredicates:
    """REQUIRES_* constraints evaluated per changed_fqn."""

    def test_requires_dependency_violated(self) -> None:
        from services.cpt.engine import MatchedConstraint, check_change_triggered_predicates, _build_adjacency
        from services.matching import MatchStatus

        constraint = ConstraintEdge(
            subject="app.api.*",
            predicate=PredicateType.REQUIRES_DEPENDENCY,
            object="app.auth.middleware",
            justification="test",
            adr_id="ADR-004",
            adr_path="docs/adr/004.md",
        )
        adjacency = _build_adjacency(set())
        matched = {
            id(constraint): MatchedConstraint(
                constraint=constraint,
                subject_matches=[(FQN.from_dotted("app.api.orders"), MatchStatus.WILDCARD)],
                object_matches=[(FQN.from_dotted("app.auth.middleware"), MatchStatus.EXACT)],
            ),
        }
        changed = [_changed_fqn("app.api.orders")]
        violations = check_change_triggered_predicates(matched, adjacency, changed)
        assert len(violations) == 1
        assert violations[0].constraint.adr_id == "ADR-004"

    def test_requires_dependency_not_violated(self) -> None:
        from services.cpt.engine import MatchedConstraint, check_change_triggered_predicates, _build_adjacency
        from services.matching import MatchStatus

        constraint = ConstraintEdge(
            subject="app.api.*",
            predicate=PredicateType.REQUIRES_DEPENDENCY,
            object="app.auth.middleware",
            justification="test",
            adr_id="ADR-004",
            adr_path="docs/adr/004.md",
        )
        adjacency = _build_adjacency({
            Edge(source="app.api.users", target="app.auth.middleware", kind="IMPORTS"),
        })
        matched = {
            id(constraint): MatchedConstraint(
                constraint=constraint,
                subject_matches=[(FQN.from_dotted("app.api.users"), MatchStatus.WILDCARD)],
                object_matches=[(FQN.from_dotted("app.auth.middleware"), MatchStatus.EXACT)],
            ),
        }
        changed = [_changed_fqn("app.api.users")]
        violations = check_change_triggered_predicates(matched, adjacency, changed)
        assert len(violations) == 0

    def test_requires_implementation_violated(self) -> None:
        from services.cpt.engine import MatchedConstraint, check_change_triggered_predicates, _build_adjacency
        from services.matching import MatchStatus

        constraint = ConstraintEdge(
            subject="app.middleware",
            predicate=PredicateType.REQUIRES_IMPLEMENTATION,
            object="app.auth.middleware",
            justification="test",
            adr_id="ADR-005",
            adr_path="docs/adr/005.md",
        )
        adjacency = _build_adjacency(set())
        matched = {
            id(constraint): MatchedConstraint(
                constraint=constraint,
                subject_matches=[(FQN.from_dotted("app.middleware"), MatchStatus.EXACT)],
                object_matches=[(FQN.from_dotted("app.auth.middleware"), MatchStatus.EXACT)],
            ),
        }
        changed = [_changed_fqn("app.middleware")]
        violations = check_change_triggered_predicates(matched, adjacency, changed)
        assert len(violations) == 1

    def test_requires_implementation_not_violated(self) -> None:
        from services.cpt.engine import MatchedConstraint, check_change_triggered_predicates, _build_adjacency
        from services.matching import MatchStatus

        constraint = ConstraintEdge(
            subject="app.middleware",
            predicate=PredicateType.REQUIRES_IMPLEMENTATION,
            object="app.auth.middleware",
            justification="test",
            adr_id="ADR-005",
            adr_path="docs/adr/005.md",
        )
        adjacency = _build_adjacency({
            Edge(source="app.middleware", target="app.middleware.auth", kind="CONTAINS"),
            Edge(source="app.middleware.auth", target="app.auth.middleware", kind="CALLS"),
        })
        matched = {
            id(constraint): MatchedConstraint(
                constraint=constraint,
                subject_matches=[(FQN.from_dotted("app.middleware"), MatchStatus.EXACT)],
                object_matches=[(FQN.from_dotted("app.auth.middleware"), MatchStatus.EXACT)],
            ),
        }
        changed = [_changed_fqn("app.middleware")]
        violations = check_change_triggered_predicates(matched, adjacency, changed)
        assert len(violations) == 0

    def test_requires_skips_constraint_if_changed_not_in_subject(self) -> None:
        from services.cpt.engine import MatchedConstraint, check_change_triggered_predicates, _build_adjacency
        from services.matching import MatchStatus

        constraint = ConstraintEdge(
            subject="app.api.*",
            predicate=PredicateType.REQUIRES_DEPENDENCY,
            object="app.auth.middleware",
            justification="test",
            adr_id="ADR-004",
            adr_path="docs/adr/004.md",
        )
        adjacency = _build_adjacency(set())
        matched = {
            id(constraint): MatchedConstraint(
                constraint=constraint,
                subject_matches=[(FQN.from_dotted("app.api.users"), MatchStatus.WILDCARD)],
                object_matches=[(FQN.from_dotted("app.auth.middleware"), MatchStatus.EXACT)],
            ),
        }
        # changed_fqn is app.models.user, which is NOT in subject_matches
        changed = [_changed_fqn("app.models.user")]
        violations = check_change_triggered_predicates(matched, adjacency, changed)
        assert len(violations) == 0


# ===========================================================================
# 5. resolve: specificity conflict + deduplication
# ===========================================================================


class TestResolve:
    """Basic resolution: specificity conflicts and deduplication.
    Imports from resolution module; kept here for backward compatibility."""

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
        from services.cpt.resolution import resolve

        v_prohibit = self._make_violation(
            "app.*", PredicateType.PROHIBITS_IMPLEMENTATION, "app.auth.middleware",
            specificity=1.0, matched_fqn="app.api.users", adr_id="ADR-005",
        )
        v_require = self._make_violation(
            "app.middleware", PredicateType.REQUIRES_IMPLEMENTATION, "app.auth.middleware",
            specificity=3.0, matched_fqn="app.middleware", adr_id="ADR-005",
        )
        result = resolve([v_prohibit, v_require])
        result_predicates = {v.constraint.predicate for v in result}
        assert PredicateType.REQUIRES_IMPLEMENTATION in result_predicates
        assert PredicateType.PROHIBITS_IMPLEMENTATION not in result_predicates

    def test_dedup_same_constraint_same_matched_fqn(self) -> None:
        from services.cpt.resolution import resolve

        v1 = self._make_violation(
            "app.api.*", PredicateType.PROHIBITS_DEPENDENCY, "app.models.*",
            specificity=2.5, matched_fqn="app.api.users",
        )
        v2 = self._make_violation(
            "app.api.*", PredicateType.PROHIBITS_DEPENDENCY, "app.models.*",
            specificity=2.5, matched_fqn="app.api.users",
        )
        result = resolve([v1, v2])
        assert len(result) == 1

    def test_different_matched_fqns_not_deduped(self) -> None:
        """Sibling FQNs (not parent-child) stay separate."""
        from services.cpt.resolution import resolve

        v1 = self._make_violation(
            "app.api.*", PredicateType.PROHIBITS_DEPENDENCY, "app.models.*",
            specificity=2.5, matched_fqn="app.api.users",
        )
        v2 = self._make_violation(
            "app.api.*", PredicateType.PROHIBITS_DEPENDENCY, "app.models.*",
            specificity=2.5, matched_fqn="app.api.orders",
        )
        result = resolve([v1, v2])
        assert len(result) == 2

    def test_module_level_dedup_parent_covers_child(self) -> None:
        """Parent FQN violation covers child FQN for same constraint."""
        from services.cpt.resolution import resolve

        v1 = self._make_violation(
            "app.api.*", PredicateType.PROHIBITS_DEPENDENCY, "app.models.*",
            specificity=2.5, matched_fqn="app.api.users",
        )
        v2 = self._make_violation(
            "app.api.*", PredicateType.PROHIBITS_DEPENDENCY, "app.models.*",
            specificity=2.5, matched_fqn="app.api.users.UserListResource",
        )
        result = resolve([v1, v2])
        assert len(result) == 1
        assert str(result[0].matched_fqn) == "app.api.users"

    def test_module_level_dedup_keeps_child_if_parent_absent(self) -> None:
        """Child violation is kept when parent has no violation for that constraint."""
        from services.cpt.resolution import resolve

        v1 = self._make_violation(
            "app.api.*", PredicateType.PROHIBITS_DEPENDENCY, "app.models.*",
            specificity=2.5, matched_fqn="app.api.users.UserListResource",
        )
        result = resolve([v1])
        assert len(result) == 1
        assert str(result[0].matched_fqn) == "app.api.users.UserListResource"

    def test_module_level_dedup_grandchild_covered(self) -> None:
        """Grandchild is covered by grandparent violation."""
        from services.cpt.resolution import resolve

        v1 = self._make_violation(
            "app.api.*", PredicateType.PROHIBITS_DEPENDENCY, "app.models.*",
            specificity=2.5, matched_fqn="app.api.users",
        )
        v2 = self._make_violation(
            "app.api.*", PredicateType.PROHIBITS_DEPENDENCY, "app.models.*",
            specificity=2.5, matched_fqn="app.api.users.UserListResource.get",
        )
        result = resolve([v1, v2])
        assert len(result) == 1

    def test_no_conflict_passes_through(self) -> None:
        from services.cpt.resolution import resolve

        v1 = self._make_violation(
            "app.api.*", PredicateType.PROHIBITS_DEPENDENCY, "app.models.*",
            specificity=2.5, matched_fqn="app.api.users",
        )
        result = resolve([v1])
        assert len(result) == 1


# ===========================================================================
# 6. detect: full CPT pipeline
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
        prohibit_violations = [v for v in result.violations if v.constraint.predicate == PredicateType.PROHIBITS_IMPLEMENTATION]
        require_violations = [v for v in result.violations if v.constraint.predicate == PredicateType.REQUIRES_IMPLEMENTATION]
        assert len(prohibit_violations) == 0


# ===========================================================================
# 7. Data models: Violation and CPTResult exist with correct shape
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
        from services.cpt.engine import CPTResult

        result = CPTResult(
            violations=[],
            orphans=[],
            neighborhood={FQN.from_dotted("app")},
        )
        assert result.violations == []
        assert result.orphans == []
        assert FQN.from_dotted("app") in result.neighborhood

    def test_matched_constraint_fields(self) -> None:
        from services.cpt.engine import MatchedConstraint
        from services.matching import MatchStatus

        constraint = ConstraintEdge(
            subject="app.api.*",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="app.models.*",
            justification="test",
            adr_id="ADR-003",
            adr_path="docs/adr/003.md",
        )
        mc = MatchedConstraint(
            constraint=constraint,
            subject_matches=[(FQN.from_dotted("app.api.users"), MatchStatus.WILDCARD)],
            object_matches=[(FQN.from_dotted("app.models.user"), MatchStatus.WILDCARD)],
        )
        assert mc.constraint.adr_id == "ADR-003"
        assert len(mc.subject_matches) == 1
        assert len(mc.object_matches) == 1