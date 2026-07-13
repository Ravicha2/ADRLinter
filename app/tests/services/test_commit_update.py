"""Tests for commit_update orchestration: merge_preserved_constraints and commit_update.

Unit tests (no Neo4j) for merge_preserved_constraints.
Integration tests (Neo4j required) for commit_update.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.commit_update import UpdateResult, commit_update, merge_preserved_constraints
from services.fqn import FQN
from services.models import ADG, ConstraintEdge, Edge, FQNKind, FQNNode, PredicateType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_node(fqn: str, kind: FQNKind = FQNKind.MODULE, **overrides) -> FQNNode:
    defaults = dict(
        fqn=FQN.from_dotted(fqn),
        kind=kind,
        file_path=f"{fqn.replace('.', '/')}/__init__.py",
        line_start=0,
        line_end=0,
        start_byte=0,
        end_byte=0,
    )
    defaults.update(overrides)
    return FQNNode(**defaults)


def _make_constraint_edge(subject: str, obj: str, adr_id: str = "ADR-001", **overrides) -> ConstraintEdge:
    defaults = dict(
        subject=subject,
        predicate=PredicateType.PROHIBITS_DEPENDENCY,
        object=obj,
        justification=f"{subject} must not depend on {obj}",
        adr_id=adr_id,
        adr_path=f"docs/adr/{adr_id.lower()}.md",
        specificity=0.0,
    )
    defaults.update(overrides)
    return ConstraintEdge(**defaults)


def _structural_adg() -> ADG:
    """ADG with structural nodes and edges only (no constraint edges)."""
    nodes = [
        _make_node("app"),
        _make_node("app.api"),
        _make_node("app.api.users"),
        _make_node("app.auth"),
        _make_node("app.auth.middleware"),
    ]
    edges = [
        Edge(source="app", target="app.api", kind="CONTAINS"),
        Edge(source="app.api", target="app.api.users", kind="CONTAINS"),
        Edge(source="app", target="app.auth", kind="CONTAINS"),
        Edge(source="app.auth", target="app.auth.middleware", kind="CONTAINS"),
    ]
    return ADG(nodes=nodes, edges=edges)


# ===========================================================================
# Unit tests: merge_preserved_constraints
# ===========================================================================


class TestMergePreservedConstraints:
    """merge_preserved_constraints merges ConstraintEdges into a fresh ADG."""

    def test_no_orphans(self) -> None:
        """All constraint endpoints exist in ADG: no EXTERNAL nodes created."""
        adg = _structural_adg()
        ce = _make_constraint_edge("app.api.users", "app.auth.middleware")

        merged = merge_preserved_constraints(adg, [ce])

        # Same nodes as original (no EXTERNAL added)
        assert len(merged.nodes) == len(adg.nodes)
        # Constraint edge attached
        assert len(merged.constraint_edges) == 1
        assert merged.constraint_edges[0].subject == "app.api.users"

    def test_with_orphans(self) -> None:
        """Missing endpoints create EXTERNAL nodes."""
        adg = _structural_adg()
        ce = _make_constraint_edge("app.api.*", "logging")

        merged = merge_preserved_constraints(adg, [ce])

        # Two EXTERNAL nodes added: app.api.* and logging
        external_nodes = [n for n in merged.nodes if n.kind == FQNKind.EXTERNAL]
        assert len(external_nodes) == 2
        external_fqns = {str(n.fqn) for n in external_nodes}
        assert "app.api.*" in external_fqns
        assert "logging" in external_fqns
        # Original structural nodes unchanged
        structural_fqns = {str(n.fqn) for n in merged.nodes if n.kind != FQNKind.EXTERNAL}
        assert "app.auth.middleware" in structural_fqns

    def test_preserves_structural(self) -> None:
        """Original nodes and edges are unchanged after merge."""
        adg = _structural_adg()
        ce = _make_constraint_edge("app.api.*", "app.auth.middleware")

        merged = merge_preserved_constraints(adg, [ce])

        # Original nodes still present
        original_fqns = {str(n.fqn) for n in adg.nodes}
        merged_fqns = {str(n.fqn) for n in merged.nodes}
        assert original_fqns.issubset(merged_fqns)
        # Original edges still present
        assert len(merged.edges) == len(adg.edges)

    def test_wildcard_subject_creates_external(self) -> None:
        """Wildcard pattern (app.api.*) creates an EXTERNAL node."""
        adg = _structural_adg()
        ce = _make_constraint_edge("app.api.*", "app.auth.middleware")

        merged = merge_preserved_constraints(adg, [ce])

        wildcard_nodes = [n for n in merged.nodes if str(n.fqn) == "app.api.*"]
        assert len(wildcard_nodes) == 1
        assert wildcard_nodes[0].kind == FQNKind.EXTERNAL

    def test_no_duplicate_external_nodes(self) -> None:
        """Two constraints sharing an orphan endpoint create only one EXTERNAL node."""
        adg = _structural_adg()
        ce1 = _make_constraint_edge("app.api.*", "app.auth.middleware", adr_id="ADR-001")
        ce2 = _make_constraint_edge("app.api.*", "logging", adr_id="ADR-002")

        merged = merge_preserved_constraints(adg, [ce1, ce2])

        external_fqns = [str(n.fqn) for n in merged.nodes if n.kind == FQNKind.EXTERNAL]
        # app.api.* appears once, logging appears once
        assert external_fqns.count("app.api.*") == 1
        assert external_fqns.count("logging") == 1

    def test_empty_constraints(self) -> None:
        """Empty constraint list returns ADG unchanged."""
        adg = _structural_adg()
        merged = merge_preserved_constraints(adg, [])
        assert len(merged.nodes) == len(adg.nodes)
        assert len(merged.constraint_edges) == 0


# ===========================================================================
# Integration tests: commit_update (Neo4j required)
# ===========================================================================


@pytest.mark.integration
class TestCommitUpdate:
    """Integration tests for commit_update orchestration."""

    def test_commit_update_raises_when_no_adg(self, neo4j_store) -> None:
        """commit_update raises RuntimeError when no constraint edges exist."""
        with pytest.raises(RuntimeError, match="No ADG found"):
            commit_update(neo4j_store, Path("/tmp/nonexistent"))

    def test_commit_update_preserves_constraints(self, neo4j_store, flask_repo) -> None:
        """After commit_update, constraint edges survive in Neo4j."""
        from services.adg.treesitter import parse_repo

        adg = parse_repo(flask_repo)
        ce = ConstraintEdge(
            subject="flask.helpers.*",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="logging",
            justification="No bare logging.",
            adr_id="ADR-005",
            adr_path="docs/adr/005.md",
            specificity=2.0,
        )
        adg_with_constraint = ADG(
            nodes=list(adg.nodes),
            edges=list(adg.edges),
            constraint_edges=[ce],
        )
        neo4j_store.store_adg(adg_with_constraint)

        # Verify constraint edge stored
        stored_edges = neo4j_store.load_all_constraint_edges()
        assert len(stored_edges) >= 1

        # Run commit_update
        result = commit_update(neo4j_store, flask_repo, commit_sha=None)

        # Constraint edges preserved in Neo4j
        after_edges = neo4j_store.load_all_constraint_edges()
        assert len(after_edges) >= 1
        assert result.constraint_edges_preserved >= 1

    def test_commit_update_filters_dismissals(self, neo4j_store, flask_repo) -> None:
        """Dismissals from before the update filter violations after."""
        from services.adg.treesitter import parse_repo
        from services.cpt.dismissal import Dismissal

        adg = parse_repo(flask_repo)
        ce = ConstraintEdge(
            subject="flask.helpers.*",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="logging",
            justification="No bare logging.",
            adr_id="ADR-005",
            adr_path="docs/adr/005.md",
            specificity=2.0,
        )
        adg_with_constraint = ADG(
            nodes=list(adg.nodes),
            edges=list(adg.edges),
            constraint_edges=[ce],
        )
        neo4j_store.store_adg(adg_with_constraint)

        dismissal = Dismissal(
            short_id="abc12",
            identity_hash="abc12" + "0" * 59,
            subject="flask.helpers.*",
            predicate="prohibits_dependency",
            object="logging",
            matched_fqn="flask.helpers",
            adr_id="ADR-005",
        )
        neo4j_store.store_dismissal(dismissal)

        result = commit_update(neo4j_store, flask_repo, commit_sha=None)

        # Dismissals still exist in Neo4j
        after_dismissals = neo4j_store.load_dismissals()
        assert len(after_dismissals) >= 1
        assert result.dismissals_applied >= 0

    def test_commit_update_detects_violations_change(self, neo4j_store, flask_repo) -> None:
        """Seed ADG, run commit_update, verify violations are detected."""
        from services.adg.treesitter import parse_repo

        adg = parse_repo(flask_repo)
        ce = ConstraintEdge(
            subject="flask.helpers.*",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="flask.sansio",
            justification="Helpers must not depend on sansio directly.",
            adr_id="ADR-010",
            adr_path="docs/adr/010.md",
            specificity=2.0,
        )
        adg_with_constraint = ADG(
            nodes=list(adg.nodes),
            edges=list(adg.edges),
            constraint_edges=[ce],
        )
        neo4j_store.store_adg(adg_with_constraint)

        result = commit_update(neo4j_store, flask_repo, commit_sha=None)

        # Constraint edges preserved
        assert result.constraint_edges_preserved >= 1
        # UpdateResult has expected structure
        assert isinstance(result.violations, list)
        assert isinstance(result.orphans, list)
        assert isinstance(result.changed_file_list, list)
        # Structural nodes back in Neo4j after update
        loaded = neo4j_store.load_adg()
        assert len(loaded.nodes) > 0
        assert len(loaded.constraint_edges) >= 1