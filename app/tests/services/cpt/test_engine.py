"""Tests for CPT engine: constraint matching, predicate checking,
and detect integration.

Public interface under test:
    match_constraints: single-pass constraint matching against all ADG nodes
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
# 1. match_constraints: single-pass matching against all ADG nodes
# ===========================================================================


class TestMatchConstraints:
    """For each constraint, match all ADG node FQNs against subject/object."""

    def test_adg_fqn_matches_constraint_subject(self, sample_adg: ADG, sample_constraints: list[ConstraintEdge]) -> None:
        from services.cpt.engine import match_constraints

        adg_with_constraints = ADG(
            nodes=sample_adg.nodes,
            edges=sample_adg.edges,
            constraint_edges=sample_constraints,
        )
        matched = match_constraints(adg_with_constraints)
        matched_adr_ids = {mc.constraint.adr_id for mc in matched.values()}
        assert "ADR-003" in matched_adr_ids
        assert "ADR-004" in matched_adr_ids

    def test_adg_fqn_matches_constraint_object(self, sample_adg: ADG, sample_constraints: list[ConstraintEdge]) -> None:
        from services.cpt.engine import match_constraints

        adg_with_constraints = ADG(
            nodes=sample_adg.nodes,
            edges=sample_adg.edges,
            constraint_edges=sample_constraints,
        )
        matched = match_constraints(adg_with_constraints)
        # app.auth.middleware is in ADG nodes and matches object of ADR-004
        matched_adr_ids = {mc.constraint.adr_id for mc in matched.values()}
        assert "ADR-004" in matched_adr_ids

    def test_no_constraints_matched(self, sample_adg: ADG) -> None:
        from services.cpt.engine import match_constraints

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
        matched = match_constraints(adg)
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
        matched = match_constraints(adg)
        assert len(matched) == 0


# ===========================================================================
# 2. check_structural_predicates: PROHIBITS_* evaluation
# ===========================================================================


class TestCheckStructuralPredicates:
    """PROHIBITS_* constraints evaluated without changed_fqn."""

    def test_reachable_nodes(self) -> None:
        from services.cpt.engine import _reachable_nodes, _build_adjacency
        from services.models import Edge

        adjacency = _build_adjacency({
            Edge(source="app.api.users", target="app.auth.middleware", kind="IMPORTS"),
            Edge(source="app.auth.middleware", target="app.models.user", kind="IMPORTS"),
        })
        reachable = _reachable_nodes("app.api.users", adjacency, {"IMPORTS"})
        assert reachable == {"app.auth.middleware", "app.models.user"}

    def test_prohibits_dependency_violated(self) -> None:
        from services.cpt.engine import MatchedConstraint, check_structural_predicates, _build_adjacency
        from services.resolver import MatchStatus

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
        from services.resolver import MatchStatus

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
        from services.resolver import MatchStatus

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
# 3. check_change_triggered_predicates: REQUIRES_* evaluation
# ===========================================================================


class TestCheckChangeTriggeredPredicates:
    """REQUIRES_* constraints evaluated per changed_fqn."""

    def test_requires_dependency_violated(self) -> None:
        from services.cpt.engine import MatchedConstraint, check_change_triggered_predicates, _build_adjacency
        from services.resolver import MatchStatus

        constraint = ConstraintEdge(
            subject="app.api.*",
            predicate=PredicateType.REQUIRES_DEPENDENCY,
            object="app.auth.middleware",
            justification="test",
            adr_id="ADR-004",
            adr_path="docs/adr/004.md",
        )
        # Empty adjacency: BFS from prefix 'app.api' reaches nothing → violation
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
        # BFS starts from the changed FQN, not the wildcard prefix
        assert violations[0].evidence == "app.api.orders has no dependency on any module matching app.auth.middleware"

    def test_requires_dependency_not_violated(self) -> None:
        from services.cpt.engine import MatchedConstraint, check_change_triggered_predicates, _build_adjacency
        from services.resolver import MatchStatus

        constraint = ConstraintEdge(
            subject="app.api.*",
            predicate=PredicateType.REQUIRES_DEPENDENCY,
            object="app.auth.middleware",
            justification="test",
            adr_id="ADR-004",
            adr_path="docs/adr/004.md",
        )
        # BFS from prefix 'app.api' needs CONTAINS edge to reach child modules
        adjacency = _build_adjacency({
            Edge(source="app.api", target="app.api.users", kind="CONTAINS"),
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
        from services.resolver import MatchStatus

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
        assert violations[0].evidence == "app.middleware does not implement any module matching app.auth.middleware"

    def test_requires_wildcard_multiple_objects_semantics(self) -> None:
        from services.cpt.engine import MatchedConstraint, check_change_triggered_predicates, _build_adjacency
        from services.resolver import MatchStatus

        constraint = ConstraintEdge(
            subject="app.api.*",
            predicate=PredicateType.REQUIRES_DEPENDENCY,
            object="app.auth.*",
            justification="test",
            adr_id="ADR-004",
            adr_path="docs/adr/004.md",
        )
        # Case 1: Zero objects reachable -> exactly 1 violation emitted
        # BFS starts from prefix 'app.api'
        adjacency_empty = _build_adjacency(set())
        matched = {
            id(constraint): MatchedConstraint(
                constraint=constraint,
                subject_matches=[(FQN.from_dotted("app.api.orders"), MatchStatus.WILDCARD)],
                object_matches=[
                    (FQN.from_dotted("app.auth.a"), MatchStatus.WILDCARD),
                    (FQN.from_dotted("app.auth.b"), MatchStatus.WILDCARD),
                ],
            ),
        }
        changed = [_changed_fqn("app.api.orders")]
        violations_empty = check_change_triggered_predicates(matched, adjacency_empty, changed)
        assert len(violations_empty) == 1
        assert violations_empty[0].evidence == "app.api.orders has no dependency on any module matching app.auth.*"

        # Case 2: One object reachable via prefix -> 0 violations emitted
        # Need CONTAINS edge from prefix to child so BFS can reach the IMPORTS target
        adjacency_partial = _build_adjacency({
            Edge(source="app.api", target="app.api.orders", kind="CONTAINS"),
            Edge(source="app.api.orders", target="app.auth.a", kind="IMPORTS"),
        })
        violations_partial = check_change_triggered_predicates(matched, adjacency_partial, changed)
        assert len(violations_partial) == 0

    def test_requires_implementation_not_violated(self) -> None:
        from services.cpt.engine import MatchedConstraint, check_change_triggered_predicates, _build_adjacency
        from services.resolver import MatchStatus

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
        from services.resolver import MatchStatus

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
# 4. resolve: specificity conflict + deduplication
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
        from services.resolver import MatchStatus

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
        result = detect(diff, adg)
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
        result = detect(diff, adg)
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
        result = detect(diff, adg)
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
        result = detect(diff, adg)
        assert len(result.orphans) >= 1
        assert any(c.adr_id == "ADR-999" for c in result.orphans)

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
        result = detect(diff, adg)
        prohibit_violations = [v for v in result.violations if v.constraint.predicate == PredicateType.PROHIBITS_IMPLEMENTATION]
        require_violations = [v for v in result.violations if v.constraint.predicate == PredicateType.REQUIRES_IMPLEMENTATION]
        # app.middleware has its prohibit suppressed by requires, while app.auth retains its valid structural prohibit under full-ADG evaluation
        middleware_prohibits = [v for v in prohibit_violations if str(v.matched_fqn) == "app.middleware"]
        assert len(middleware_prohibits) == 0


# ===========================================================================
# 6. Data models: Violation and CPTResult exist with correct shape
# ===========================================================================


class TestCptDataModels:
    """Violation and CPTResult dataclass shape."""

    def test_violation_fields(self) -> None:
        from services.cpt.engine import Violation
        from services.resolver import MatchStatus

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
            self_loop_constraints=[],
        )
        assert result.violations == []
        assert result.orphans == []
        assert result.self_loop_constraints == []

    def test_matched_constraint_fields(self) -> None:
        from services.cpt.engine import MatchedConstraint
        from services.resolver import MatchStatus

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


# ===========================================================================
# 7. Self-loop constraint: subject == object produces no false-positive violations
# ===========================================================================


class TestSelfLoopConstraint:
    """Regression: exclusion-pattern extraction where owner and object resolve to
    the same FQN (e.g. 'only app.auth.middleware may implement authentication')
    must not produce nonsensical violations like 'X does not implement X'."""

    @staticmethod
    def _make_self_loop(**overrides: str) -> ConstraintEdge:
        """Construct a self-loop constraint by creating a valid one then mutating
        in-place to bypass __post_init__ validation (simulates deserialization)."""
        c = ConstraintEdge(
            subject=overrides.get("subject", "app.auth.middleware"),
            predicate=PredicateType.REQUIRES_IMPLEMENTATION,
            object="__placeholder__",
            justification=overrides.get("justification", "Self-loop constraint."),
            adr_id=overrides.get("adr_id", "ADR-010"),
            adr_path="docs/adr/010.md",
        )
        c.object = overrides.get("subject", "app.auth.middleware")
        return c

    def test_detect_surfaces_self_loop_constraint(self, sample_adg: ADG) -> None:
        from services.cpt.engine import detect

        self_loop = self._make_self_loop(
            subject="app.auth.middleware",
            adr_id="ADR-010",
        )
        adg = ADG(
            nodes=sample_adg.nodes,
            edges=sample_adg.edges,
            constraint_edges=[self_loop],
        )
        diff = DiffResult(
            commit_sha="abc123",
            parent_sha="def456",
            changed_files=[FileChange(path="app/auth/middleware.py", status="modified")],
            changed_fqns=[_changed_fqn("app.auth.middleware")],
        )
        result = detect(diff, adg)
        # Self-loop constraint must not produce violations
        assert len(result.violations) == 0
        # Self-loop constraint surfaced for human review
        assert len(result.self_loop_constraints) == 1
        assert result.self_loop_constraints[0].adr_id == "ADR-010"

    def test_detect_mixed_self_loop_and_normal(self, sample_adg: ADG) -> None:
        from services.cpt.engine import detect

        self_loop = self._make_self_loop(
            subject="app.auth.middleware",
            justification="Self-loop: no one but auth middleware implements auth.",
            adr_id="ADR-010",
        )
        normal = ConstraintEdge(
            subject="app.api.*",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="app.models.*",
            justification="API must not depend on models.",
            adr_id="ADR-003",
            adr_path="docs/adr/003.md",
        )
        adg = ADG(
            nodes=sample_adg.nodes,
            edges=sample_adg.edges,
            constraint_edges=[self_loop, normal],
        )
        diff = DiffResult(
            commit_sha="abc123",
            parent_sha="def456",
            changed_files=[FileChange(path="app/api/users.py", status="modified")],
            changed_fqns=[_changed_fqn("app.api.users")],
        )
        result = detect(diff, adg)
        # Normal constraint still processed
        assert any(v.constraint.adr_id == "ADR-003" for v in result.violations)
        # Self-loop filtered out, surfaced separately
        assert len(result.self_loop_constraints) == 1
        assert result.self_loop_constraints[0].adr_id == "ADR-010"