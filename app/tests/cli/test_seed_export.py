"""Tests for cpt seed export CLI command and fixture serialization."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from cli.config import RepoConfig
from cli.main import app, _serialize_fixture
from services.cpt.dismissal import Dismissal
from services.fqn import FQN
from services.models import ADG, ConstraintEdge, DependencyRole, Edge, FQNKind, PredicateType

runner = CliRunner()

_MOCK_REPO_CFG = RepoConfig(id="test-repo", url="/tmp/test-repo", adr_dir="docs/adr")


def _make_fqn_node(fqn: str, kind: FQNKind = FQNKind.MODULE, file_path: str = "app/__init__.py"):
    from services.models import FQNNode
    return FQNNode(
        fqn=FQN.from_dotted(fqn),
        kind=kind,
        file_path=file_path,
        line_start=1,
        line_end=5,
        start_byte=0,
        end_byte=42,
        role=DependencyRole.INTERNAL,
    )


def _make_constraint_edge(
    subject: str = "app.service.*",
    predicate: PredicateType = PredicateType.PROHIBITS_DEPENDENCY,
    object: str = "app.repo.*",
    adr_id: str = "ADR-001",
) -> ConstraintEdge:
    return ConstraintEdge(
        subject=subject,
        predicate=predicate,
        object=object,
        justification="test constraint",
        adr_id=adr_id,
        adr_path="docs/adr/001.md",
    )


def _make_dismissal(
    subject: str = "app.service.*",
    predicate: str = "prohibits_dependency",
    identity_hash: str = "a" * 64,
) -> Dismissal:
    return Dismissal(
        short_id=identity_hash[:5],
        identity_hash=identity_hash,
        subject=subject,
        predicate=predicate,
        object="app.repo.*",
        matched_fqn="app.service.UserService",
        adr_id="ADR-001",
        dismissed_at="2026-07-20T00:00:00+00:00",
    )


class TestSerializeFixture:
    """Unit tests for _serialize_fixture (no Neo4j)."""

    def test_fqn_nodes_sorted_by_fqn(self):
        nodes = [
            _make_fqn_node("app.models.user"),
            _make_fqn_node("app"),
            _make_fqn_node("app.config"),
        ]
        adg = ADG(nodes=nodes, edges=[], constraint_edges=[])
        result = _serialize_fixture(adg, [])
        fqns = [n["fqn"] for n in result["fqn_nodes"]]
        assert fqns == ["app", "app.config", "app.models.user"]

    def test_structural_edges_sorted(self):
        adg = ADG(
            nodes=[_make_fqn_node("app")],
            edges=[
                Edge(source="app", target="app.config", kind="CONTAINS"),
                Edge(source="app", target="app.models", kind="CONTAINS"),
                Edge(source="app.config", target="app", kind="IMPORTS"),
            ],
            constraint_edges=[],
        )
        result = _serialize_fixture(adg, [])
        edges = [(e["source"], e["target"], e["kind"]) for e in result["structural_edges"]]
        assert edges == sorted(edges)

    def test_constraint_edges_enum_values(self):
        ce = _make_constraint_edge(predicate=PredicateType.REQUIRES_IMPLEMENTATION)
        adg = ADG(nodes=[], edges=[], constraint_edges=[ce])
        result = _serialize_fixture(adg, [])
        assert result["constraint_edges"][0]["predicate"] == "requires_implementation"

    def test_dismissals_sorted_by_identity_hash(self):
        d1 = _make_dismissal(identity_hash="b" * 64)
        d2 = _make_dismissal(identity_hash="a" * 64)
        result = _serialize_fixture(ADG(), [d1, d2])
        assert result["dismissals"][0]["identity_hash"] < result["dismissals"][1]["identity_hash"]

    def test_deterministic_json(self):
        nodes = [_make_fqn_node("app"), _make_fqn_node("app.config")]
        ce = _make_constraint_edge()
        adg = ADG(nodes=nodes, edges=[], constraint_edges=[ce])
        d = _make_dismissal()
        output1 = json.dumps(_serialize_fixture(adg, [d]), sort_keys=True)
        output2 = json.dumps(_serialize_fixture(adg, [d]), sort_keys=True)
        assert output1 == output2


class TestSeedExportCLI:
    """Integration tests for cpt seed export (mocked GraphStore)."""

    @patch("cli.main.GraphStore")
    @patch("cli.main._get_repo")
    def test_export_writes_json_file(self, mock_get_repo, mock_store_cls, tmp_path):
        mock_get_repo.return_value = _MOCK_REPO_CFG
        mock_store = MagicMock()
        mock_store.load_adg.return_value = ADG(
            nodes=[_make_fqn_node("app")],
            edges=[Edge(source="app", target="app.config", kind="CONTAINS")],
            constraint_edges=[_make_constraint_edge()],
        )
        mock_store.load_dismissals.return_value = []
        mock_store_cls.return_value = mock_store

        output = tmp_path / "fixture.json"
        result = runner.invoke(app, ["seed", "export", "--repo", "test-repo", "--output", str(output)])
        assert result.exit_code == 0, result.output

        data = json.loads(output.read_text())
        assert "fqn_nodes" in data
        assert "structural_edges" in data
        assert "constraint_edges" in data
        assert "dismissals" in data
        assert data["fqn_nodes"][0]["fqn"] == "app"

    @patch("cli.main.GraphStore")
    @patch("cli.main._get_repo")
    def test_export_includes_dismissals(self, mock_get_repo, mock_store_cls, tmp_path):
        mock_get_repo.return_value = _MOCK_REPO_CFG
        mock_store = MagicMock()
        mock_store.load_adg.return_value = ADG()
        d1 = _make_dismissal(identity_hash="a" * 64)
        d2 = _make_dismissal(identity_hash="b" * 64, subject="app.api.*")
        mock_store.load_dismissals.return_value = [d1, d2]
        mock_store_cls.return_value = mock_store

        output = tmp_path / "fixture.json"
        result = runner.invoke(app, ["seed", "export", "--repo", "test-repo", "--output", str(output)])
        assert result.exit_code == 0

        data = json.loads(output.read_text())
        assert len(data["dismissals"]) == 2

    @patch("cli.main.GraphStore")
    @patch("cli.main._get_repo")
    def test_export_creates_parent_directories(self, mock_get_repo, mock_store_cls, tmp_path):
        mock_get_repo.return_value = _MOCK_REPO_CFG
        mock_store = MagicMock()
        mock_store.load_adg.return_value = ADG()
        mock_store.load_dismissals.return_value = []
        mock_store_cls.return_value = mock_store

        output = tmp_path / "nested" / "dir" / "fixture.json"
        result = runner.invoke(app, ["seed", "export", "--repo", "test-repo", "--output", str(output)])
        assert result.exit_code == 0
        assert output.exists()