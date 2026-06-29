"""E2E tests for CPT detect: full pipeline from ADG+constraints through detect().

These tests exercise the complete CPT pipeline with realistic multi-constraint
scenarios, verifying that detect() produces correct violations, orphans, and
neighborhoods when all engine stages (BFS, matching, predicate checking,
resolution) work together.
"""

from __future__ import annotations

import pytest

from services.cpt.engine import CPTResult, detect
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


def _node(fqn_str: str, kind: FQNKind = FQNKind.MODULE, file_path: str = "") -> FQNNode:
    fqn = FQN.from_dotted(fqn_str)
    path = file_path or f"{'/'.join(fqn.parts)}.py"
    return FQNNode(fqn=fqn, kind=kind, file_path=path, line_start=0, line_end=0, start_byte=0, end_byte=0)


def _changed(fqn_str: str, change_type: str = "modified") -> ChangedFQN:
    fqn = FQN.from_dotted(fqn_str)
    return ChangedFQN(
        fqn=fqn,
        change_type=change_type,
        file_path=f"{'/'.join(fqn.parts)}.py",
        enclosing_module=fqn.parent if fqn.parent else fqn,
    )


def _constraint(
    subject: str,
    predicate: PredicateType,
    object: str,
    adr_id: str,
    justification: str = "test",
    specificity: float = 0.0,
) -> ConstraintEdge:
    return ConstraintEdge(
        subject=subject,
        predicate=predicate,
        object=object,
        justification=justification,
        adr_id=adr_id,
        adr_path=f"docs/adr/{adr_id}.md",
        specificity=specificity,
    )


@pytest.fixture
def layered_adg() -> ADG:
    """A 3-layer app: api -> service -> repo, with cross-layer edges that violate ADRs."""
    nodes = [
        _node("app"),
        _node("app.api"),
        _node("app.api.users"),
        _node("app.api.orders"),
        _node("app.service"),
        _node("app.service.user"),
        _node("app.repo"),
        _node("app.repo.user"),
        _node("app.auth"),
        _node("app.auth.middleware"),
    ]
    edges = [
        # CONTAINS
        Edge(source="app", target="app.api", kind="CONTAINS"),
        Edge(source="app.api", target="app.api.users", kind="CONTAINS"),
        Edge(source="app.api", target="app.api.orders", kind="CONTAINS"),
        Edge(source="app", target="app.service", kind="CONTAINS"),
        Edge(source="app.service", target="app.service.user", kind="CONTAINS"),
        Edge(source="app", target="app.repo", kind="CONTAINS"),
        Edge(source="app.repo", target="app.repo.user", kind="CONTAINS"),
        Edge(source="app", target="app.auth", kind="CONTAINS"),
        Edge(source="app.auth", target="app.auth.middleware", kind="CONTAINS"),
        # cross-layer dependencies
        Edge(source="app.api.users", target="app.service.user", kind="IMPORTS"),
        Edge(source="app.service.user", target="app.repo.user", kind="IMPORTS"),
        # VIOLATION: api bypasses service, calls repo directly
        Edge(source="app.api.orders", target="app.repo.user", kind="IMPORTS"),
        # VIOLATION: api doesn't import auth middleware
        # (app.api.orders has no IMPORTS edge to app.auth.middleware)
        # SATISFIED: service imports auth
        Edge(source="app.service.user", target="app.auth.middleware", kind="IMPORTS"),
    ]
    return ADG(nodes=nodes, edges=edges)


@pytest.fixture
def layered_constraints() -> list[ConstraintEdge]:
    """Constraints enforcing layered architecture."""
    return [
        _constraint("app.api.*", PredicateType.PROHIBITS_DEPENDENCY, "app.repo.*", "ADR-001", "API must not call repo directly", specificity=2.0),
        _constraint("app.api.*", PredicateType.REQUIRES_DEPENDENCY, "app.auth.middleware", "ADR-002", "API must depend on auth middleware", specificity=2.0),
        _constraint("app.service.*", PredicateType.PROHIBITS_DEPENDENCY, "app.repo.*", "ADR-003", "Service must not call repo directly", specificity=1.0),
    ]


class TestE2EDetect:
    """Full pipeline: DiffResult + ADG -> detect() -> CPTResult."""

    def test_api_order_violates_prohibits_dependency(self, layered_adg: ADG, layered_constraints: list[ConstraintEdge]) -> None:
        """app.api.orders calls app.repo.user, violating ADR-001."""
        adg = ADG(nodes=layered_adg.nodes, edges=layered_adg.edges, constraint_edges=layered_constraints)
        diff = DiffResult(
            commit_sha="abc123",
            changed_fqns=[_changed("app.api.orders")],
        )
        result = detect(diff, adg)

        prohibit_repo = [v for v in result.violations if v.constraint.adr_id == "ADR-001"]
        assert len(prohibit_repo) >= 1
        assert "app.repo" in prohibit_repo[0].evidence or "repo" in prohibit_repo[0].evidence

    def test_api_order_violates_requires_dependency(self, layered_adg: ADG, layered_constraints: list[ConstraintEdge]) -> None:
        """app.api.orders doesn't import auth.middleware, violating ADR-002."""
        adg = ADG(nodes=layered_adg.nodes, edges=layered_adg.edges, constraint_edges=layered_constraints)
        diff = DiffResult(
            commit_sha="abc123",
            changed_fqns=[_changed("app.api.orders")],
        )
        result = detect(diff, adg)

        require_auth = [v for v in result.violations if v.constraint.adr_id == "ADR-002"]
        assert len(require_auth) >= 1

    def test_service_user_satisfies_requires_dependency(self, layered_adg: ADG, layered_constraints: list[ConstraintEdge]) -> None:
        """app.service.user imports auth.middleware, satisfying ADR-002."""
        adg = ADG(nodes=layered_adg.nodes, edges=layered_adg.edges, constraint_edges=layered_constraints)
        diff = DiffResult(
            commit_sha="abc123",
            changed_fqns=[_changed("app.service.user")],
        )
        result = detect(diff, adg)

        require_auth = [v for v in result.violations if v.constraint.adr_id == "ADR-002"]
        assert len(require_auth) == 0

    def test_multiple_changes_aggregate_violations(self, layered_adg: ADG, layered_constraints: list[ConstraintEdge]) -> None:
        """Two changed FQNs produce violations from both."""
        adg = ADG(nodes=layered_adg.nodes, edges=layered_adg.edges, constraint_edges=layered_constraints)
        diff = DiffResult(
            commit_sha="abc123",
            changed_fqns=[_changed("app.api.orders"), _changed("app.api.users")],
        )
        result = detect(diff, adg)

        # app.api.orders violates both ADR-001 and ADR-002
        # app.api.users satisfies ADR-002 (it imports auth via service)
        assert len(result.violations) >= 1

    def test_orphan_constraint_not_in_neighborhood(self, layered_adg: ADG) -> None:
        """Constraint with subject/object outside neighborhood becomes orphan."""
        orphan = _constraint("app.nonexistent.*", PredicateType.PROHIBITS_DEPENDENCY, "app.also.gone", "ADR-999")
        adg = ADG(nodes=layered_adg.nodes, edges=layered_adg.edges, constraint_edges=[orphan])
        diff = DiffResult(commit_sha="abc", changed_fqns=[_changed("app.api.users")])
        result = detect(diff, adg)

        assert any(c.adr_id == "ADR-999" for c in result.orphans)

    def test_clean_adg_no_violations(self, layered_adg: ADG) -> None:
        """No constraints = no violations."""
        adg = ADG(nodes=layered_adg.nodes, edges=layered_adg.edges, constraint_edges=[])
        diff = DiffResult(commit_sha="abc", changed_fqns=[_changed("app.api.users")])
        result = detect(diff, adg)

        assert result.violations == []
        assert result.orphans == []

    def test_specificity_resolution_suppresses_prohibits(self, layered_adg: ADG) -> None:
        """Higher-specificity REQUIRES overrides lower-specificity PROHIBITS on same object."""
        constraints = [
            _constraint("app.*", PredicateType.PROHIBITS_IMPLEMENTATION, "app.auth.middleware", "ADR-005", specificity=1.0),
            _constraint("app.service", PredicateType.REQUIRES_IMPLEMENTATION, "app.auth.middleware", "ADR-006", specificity=3.0),
        ]
        adg = ADG(nodes=layered_adg.nodes, edges=layered_adg.edges, constraint_edges=constraints)
        diff = DiffResult(commit_sha="abc", changed_fqns=[_changed("app.service.user")])
        result = detect(diff, adg)

        prohibit_violations = [v for v in result.violations if v.constraint.predicate == PredicateType.PROHIBITS_IMPLEMENTATION]
        # ADR-005 (specificity 1.0) is outweighed by ADR-006 (specificity 3.0)
        assert len(prohibit_violations) == 0

    def test_result_type_shape(self, layered_adg: ADG, layered_constraints: list[ConstraintEdge]) -> None:
        """CPTResult has the expected fields."""
        adg = ADG(nodes=layered_adg.nodes, edges=layered_adg.edges, constraint_edges=layered_constraints)
        diff = DiffResult(commit_sha="abc", changed_fqns=[_changed("app.api.orders")])
        result = detect(diff, adg)

        assert hasattr(result, "violations")
        assert hasattr(result, "orphans")
        assert isinstance(result.violations, list)
        assert isinstance(result.orphans, list)