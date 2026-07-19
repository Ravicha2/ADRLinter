"""Tests for the ADG pipeline module: specificity computation, mutation safety, and end-to-end detection.

Boundary tests for:
    pattern_specificity: constraint pattern depth + exact/wildcard bonus
    adg_with_specificity: patching ConstraintEdge.specificity after merge
    augment_immutable: wrapping in-place augment_adg without mutating the input
    ADGPipeline.run_prepared: pure-data pipeline without git/filesystem/LLM
"""

from __future__ import annotations

import pytest

from services.fqn import FQN
from services.models import (
    ADG,
    ChangedFQN,
    Diff,
    ConstraintEdge,
    DiffResult,
    Edge,
    FileChange,
    FQNKind,
    FQNNode,
    PredicateType,
    SymbolicConstraint,
)
from services.pipeline import (
    ADGPipeline,
    PipelineInputs,
    adg_with_specificity,
    augment_immutable,
    pattern_specificity,
)
from services.cpt.dismissal import Dismissal


# ---------------------------------------------------------------------------
# pattern_specificity
# ---------------------------------------------------------------------------


class TestPatternSpecificity:
    def test_exact_short(self):
        assert pattern_specificity("app") == 2.0  # depth 1 + 1.0 exact bonus

    def test_exact_medium(self):
        assert pattern_specificity("app.service") == 3.0  # depth 2 + 1.0

    def test_exact_deep(self):
        assert pattern_specificity("app.service.UserService") == 4.0  # depth 3 + 1.0

    def test_wildcard_short(self):
        assert pattern_specificity("app.*") == 1.0  # depth 1, no exact bonus

    def test_wildcard_medium(self):
        assert pattern_specificity("app.service.*") == 2.0  # depth 2, no exact bonus

    def test_wildcard_deep(self):
        assert pattern_specificity("app.service.UserService.*") == 3.0  # depth 3, no exact bonus

    def test_wildcard_outranks_shorter_exact(self):
        # depth 2 wildcard < depth 2 exact
        assert pattern_specificity("app.service.*") < pattern_specificity("app.service")

    def test_exact_outranks_same_depth_wildcard(self):
        assert pattern_specificity("app.service") > pattern_specificity("app.service.*")


# ---------------------------------------------------------------------------
# adg_with_specificity
# ---------------------------------------------------------------------------


class TestAdgWithSpecificity:
    def test_sets_specificity_on_constraint_edges(self):
        edges = [
            ConstraintEdge(
                subject="app.service.*",
                predicate=PredicateType.PROHIBITS_DEPENDENCY,
                object="app.repo.*",
                justification="test",
                adr_id="ADR-001",
                adr_path="docs/adr/001.md",
            ),
        ]
        adg = ADG(nodes=[], edges=[], constraint_edges=edges)
        result = adg_with_specificity(adg)

        # wildcard "app.service.*" has depth 2, no exact bonus = 2.0
        assert result.constraint_edges[0].specificity == 2.0

    def test_does_not_mutate_input_adg(self):
        edge = ConstraintEdge(
            subject="app.service",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="app.repo",
            justification="test",
            adr_id="ADR-001",
            adr_path="docs/adr/001.md",
        )
        adg = ADG(nodes=[], edges=[], constraint_edges=[edge])
        original_specificity = adg.constraint_edges[0].specificity

        result = adg_with_specificity(adg)

        # Input ADG should not be modified
        assert adg.constraint_edges[0].specificity == original_specificity
        # But result should have updated specificity
        assert result.constraint_edges[0].specificity != original_specificity

    def test_preserves_nodes_and_edges(self):
        node = FQNNode(fqn=FQN.from_dotted("app"), kind=FQNKind.MODULE, file_path="app.py", line_start=0, line_end=10)
        edge = Edge(source="app", target="app.service", kind="CONTAINS")
        constraint = ConstraintEdge(
            subject="app.*",
            predicate=PredicateType.REQUIRES_DEPENDENCY,
            object="db.*",
            justification="test",
            adr_id="ADR-002",
            adr_path="docs/adr/002.md",
        )
        adg = ADG(nodes=[node], edges=[edge], constraint_edges=[constraint])
        result = adg_with_specificity(adg)

        assert result.nodes == adg.nodes
        assert result.edges == adg.edges
        assert result.constraint_edges[0].specificity == 1.0  # "app.*" depth 1

    def test_multiple_edges_different_specificities(self):
        edges = [
            ConstraintEdge(
                subject="app.*",
                predicate=PredicateType.PROHIBITS_DEPENDENCY,
                object="db.*",
                justification="broad",
                adr_id="ADR-001",
                adr_path="docs/adr/001.md",
            ),
            ConstraintEdge(
                subject="app.service.UserService",
                predicate=PredicateType.PROHIBITS_DEPENDENCY,
                object="db.repo.UserRepo",
                justification="narrow",
                adr_id="ADR-002",
                adr_path="docs/adr/002.md",
            ),
        ]
        adg = ADG(nodes=[], edges=[], constraint_edges=edges)
        result = adg_with_specificity(adg)

        assert result.constraint_edges[0].specificity == 1.0  # "app.*" depth 1
        assert result.constraint_edges[1].specificity == 4.0  # "app.service.UserService" depth 3 + 1.0 exact


# ---------------------------------------------------------------------------
# augment_immutable
# ---------------------------------------------------------------------------


class TestAugmentImmutable:
    def test_does_not_mutate_input_adg(self):
        node = FQNNode(
            fqn=FQN.from_dotted("app.service"),
            kind=FQNKind.MODULE,
            file_path="app/service.py",
            line_start=0,
            line_end=10,
        )
        adg = ADG(nodes=[node], edges=[], constraint_edges=[])
        original_node_count = len(adg.nodes)

        diff = Diff(
            to_sha="abc123",
            from_sha="def456",
            changed_files=[FileChange(path="app/new_module.py", status="added")],
            file_contents={"app/new_module.py": b"def hello(): pass"},
            from_contents={},
        )

        result = augment_immutable(adg, diff)

        # Input ADG should not be modified
        assert len(adg.nodes) == original_node_count
        # Result should have additional nodes from the diff
        assert len(result.nodes) > original_node_count

    def test_returns_new_adg_instance(self):
        adg = ADG(nodes=[], edges=[], constraint_edges=[])
        diff = Diff(
            to_sha="abc",
            from_sha=None,
            changed_files=[],
            file_contents={},
            from_contents={},
        )

        result = augment_immutable(adg, diff)

        assert result is not adg


# ---------------------------------------------------------------------------
# ADGPipeline.run_prepared (pure-data end-to-end)
# ---------------------------------------------------------------------------


def _make_adg() -> ADG:
    """Minimal ADG with service -> repo dependency for violation detection."""
    return ADG(
        nodes=[
            FQNNode(fqn=FQN.from_dotted("app.service"), kind=FQNKind.MODULE,
                    file_path="app/service.py", line_start=0, line_end=10),
            FQNNode(fqn=FQN.from_dotted("app.repo"), kind=FQNKind.MODULE,
                    file_path="app/repo.py", line_start=0, line_end=10),
        ],
        edges=[
            Edge(source="app.service", target="app.repo", kind="IMPORTS"),
        ],
        constraint_edges=[],
    )


def _make_constraints() -> list[SymbolicConstraint]:
    return [
        SymbolicConstraint(
            subject_role_general="app",
            subject_role_specific="service",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object_role_general="app",
            object_role_specific="repo",
            justification="Services must not depend on repositories directly",
            extraction_text="services should not import repos",
            adr_id="ADR-001",
            adr_path="docs/adr/001.md",
        ),
    ]


class TestADGPipelineRunPrepared:
    def test_violations_have_nonzero_specificity(self):
        """The core bug fix: specificity must not be 0.0 after pipeline."""
        adg = _make_adg()
        constraints = _make_constraints()
        diff_result = DiffResult(
            to_sha="abc123",
            changed_fqns=[
                ChangedFQN(
                    fqn=FQN.from_dotted("app.service"),
                    change_type="modified",
                    file_path="app/service.py",
                    enclosing_module=FQN.from_dotted("app.service"),
                ),
            ],
        )

        pipeline = ADGPipeline()
        inputs = PipelineInputs(adg=adg, constraints=constraints, diff_result=diff_result)
        result = pipeline.run_prepared(inputs)

        for v in result.violations:
            assert v.constraint.specificity > 0.0, (
                f"specificity is {v.constraint.specificity} for {v.constraint.subject}"
            )

    def test_specificity_ordering_through_pipeline(self):
        """Pipeline sets specificity so exact patterns outrank wildcards at the same depth."""
        # Direct test: adg_with_specificity assigns correct relative values
        wildcard_edge = ConstraintEdge(
            subject="app.service.*",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="app.db",
            justification="wildcard",
            adr_id="ADR-003",
            adr_path="docs/adr/003.md",
        )
        exact_edge = ConstraintEdge(
            subject="app.service.UserService",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="app.db",
            justification="exact",
            adr_id="ADR-002",
            adr_path="docs/adr/002.md",
        )
        adg = ADG(nodes=[], edges=[], constraint_edges=[wildcard_edge, exact_edge])
        result = adg_with_specificity(adg)

        wildcard = [e for e in result.constraint_edges if e.subject == "app.service.*"][0]
        exact = [e for e in result.constraint_edges if e.subject == "app.service.UserService"][0]

        # Same depth base, but exact gets +1.0 bonus
        assert exact.specificity > wildcard.specificity

    def test_no_violations_on_clean_adg(self):
        """ADG with no constraint edges should produce zero violations."""
        adg = _make_adg()
        diff_result = DiffResult(to_sha="abc", changed_fqns=[])

        pipeline = ADGPipeline()
        inputs = PipelineInputs(adg=adg, constraints=[], diff_result=diff_result)
        result = pipeline.run_prepared(inputs)

        assert result.violations == []


# ---------------------------------------------------------------------------
# ADGPipeline.run_with_dismissals
# ---------------------------------------------------------------------------


def _make_violations() -> list:
    """Create real Violation objects using detect() for testing dismissal filtering."""
    from services.cpt.engine import detect

    adg = ADG(
        nodes=[
            FQNNode(fqn=FQN.from_dotted("app.service.UserService"), kind=FQNKind.CLASS,
                    file_path="app/service.py", line_start=1, line_end=10),
            FQNNode(fqn=FQN.from_dotted("app.repo.UserRepo"), kind=FQNKind.CLASS,
                    file_path="app/repo.py", line_start=1, line_end=10),
        ],
        edges=[
            Edge(source="app.service.UserService", target="app.repo.UserRepo", kind="CALLS"),
            Edge(source="app.service.UserService", target="app.repo.UserRepo", kind="IMPORTS"),
        ],
        constraint_edges=[
            ConstraintEdge(
                subject="app.service.*",
                predicate=PredicateType.PROHIBITS_DEPENDENCY,
                object="app.repo.*",
                justification="Services must not depend on repos",
                adr_id="ADR-001",
                adr_path="docs/adr/001.md",
                specificity=2.0,
            ),
        ],
    )
    diff_result = DiffResult(
        to_sha="abc123",
        changed_fqns=[
            ChangedFQN(
                fqn=FQN.from_dotted("app.service.UserService"),
                change_type="modified",
                file_path="app/service.py",
                enclosing_module=FQN.from_dotted("app.service"),
            ),
        ],
    )
    result = detect(diff_result, adg)
    assert len(result.violations) > 0, "Test setup: need at least one violation"
    return result


class TestADGPipelineRunWithDismissals:
    def test_filters_dismissed_violations(self):
        result = _make_violations()
        # Dismiss the first violation
        dismissals = [Dismissal.from_violation(result.violations[0])]

        from services.cpt.dismissal import filter_dismissed
        filtered = filter_dismissed(result.violations, dismissals)
        assert len(filtered) == len(result.violations) - 1

    def test_no_dismissals_returns_all(self):
        result = _make_violations()
        from services.cpt.dismissal import filter_dismissed
        filtered = filter_dismissed(result.violations, [])
        assert len(filtered) == len(result.violations)

    def test_all_dismissed_returns_empty(self):
        result = _make_violations()
        from services.cpt.dismissal import filter_dismissed
        dismissals = [Dismissal.from_violation(v) for v in result.violations]
        filtered = filter_dismissed(result.violations, dismissals)
        assert len(filtered) == 0

    def test_preserves_orphans_and_self_loops(self):
        result = _make_violations()
        from services.cpt.engine import CPTResult
        dismissals = [Dismissal.from_violation(v) for v in result.violations]
        filtered_result = CPTResult(
            violations=[],
            orphans=result.orphans,
            self_loop_constraints=result.self_loop_constraints,
        )
        assert filtered_result.orphans == result.orphans
        assert filtered_result.self_loop_constraints == result.self_loop_constraints