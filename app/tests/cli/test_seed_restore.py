"""Tests for cpt seed restore CLI command and fixture deserialization."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, call, patch

import pytest
from typer.testing import CliRunner

from cli.config import RepoConfig
from cli.main import app, _deserialize_fixture, _serialize_fixture
from services.cpt.dismissal import Dismissal
from services.fqn import FQN
from services.models import ADG, ConstraintEdge, DependencyRole, Edge, FQNKind, FQNNode, PredicateType

runner = CliRunner()

_MOCK_REPO_CFG = RepoConfig(id="test-repo", url="/tmp/test-repo", adr_dir="docs/adr")


def _make_fqn_node(fqn: str, kind: FQNKind = FQNKind.MODULE, file_path: str = "app/__init__.py"):
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
):
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
):
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


def _roundtrip_fixture() -> dict:
    """Build a fixture dict via serialize for roundtrip tests."""
    nodes = [_make_fqn_node("app"), _make_fqn_node("app.config")]
    edges = [Edge(source="app", target="app.config", kind="CONTAINS")]
    ces = [_make_constraint_edge()]
    dismissals = [_make_dismissal()]
    adg = ADG(nodes=nodes, edges=edges, constraint_edges=ces)
    return _serialize_fixture(adg, dismissals)


class TestDeserializeFixture:
    """Unit tests for _deserialize_fixture (no Neo4j)."""

    def test_roundtrip_fqn_nodes(self):
        fixture = _roundtrip_fixture()
        adg, _ = _deserialize_fixture(fixture)
        assert len(adg.nodes) == 2
        assert adg.nodes[0].fqn == FQN.from_dotted("app")
        assert adg.nodes[0].kind == FQNKind.MODULE
        assert adg.nodes[0].role == DependencyRole.INTERNAL

    def test_roundtrip_edges(self):
        fixture = _roundtrip_fixture()
        adg, _ = _deserialize_fixture(fixture)
        assert len(adg.edges) == 1
        assert adg.edges[0].source == "app"
        assert adg.edges[0].target == "app.config"
        assert adg.edges[0].kind == "CONTAINS"

    def test_roundtrip_constraint_edges(self):
        fixture = _roundtrip_fixture()
        adg, _ = _deserialize_fixture(fixture)
        assert len(adg.constraint_edges) == 1
        ce = adg.constraint_edges[0]
        assert ce.predicate == PredicateType.PROHIBITS_DEPENDENCY
        assert ce.specificity == 0.0

    def test_roundtrip_dismissals(self):
        fixture = _roundtrip_fixture()
        _, dismissals = _deserialize_fixture(fixture)
        assert len(dismissals) == 1
        d = dismissals[0]
        assert d.subject == "app.service.*"
        assert d.predicate == "prohibits_dependency"
        assert d.dismissed_at == "2026-07-20T00:00:00+00:00"

    def test_empty_fixture(self):
        fixture = _serialize_fixture(ADG(), [])
        adg, dismissals = _deserialize_fixture(fixture)
        assert adg.nodes == []
        assert adg.edges == []
        assert adg.constraint_edges == []
        assert dismissals == []

    def test_serialize_deserialize_idempotent(self):
        """Running serialize→deserialize→serialize produces identical output."""
        fixture = _roundtrip_fixture()
        adg, dismissals = _deserialize_fixture(fixture)
        fixture2 = _serialize_fixture(adg, dismissals)
        assert json.dumps(fixture, sort_keys=True) == json.dumps(fixture2, sort_keys=True)


class TestSeedRestoreCLI:
    """Integration tests for cpt seed restore (mocked GraphStore)."""

    @patch("cli.main.GraphStore")
    @patch("cli.main._get_repo")
    def test_restore_loads_fixture_into_neo4j(self, mock_get_repo, mock_store_cls, tmp_path):
        mock_get_repo.return_value = _MOCK_REPO_CFG
        mock_store = MagicMock()
        mock_store_cls.return_value = mock_store

        fixture = _roundtrip_fixture()
        fixture_path = tmp_path / "fixture.json"
        fixture_path.write_text(json.dumps(fixture))

        result = runner.invoke(app, ["seed", "restore", "--repo", "test-repo", "--input", str(fixture_path)])
        assert result.exit_code == 0, result.output

        mock_store.create_schema.assert_called_once()
        mock_store.clear_all.assert_called_once()
        mock_store.store_adg.assert_called_once()
        store_adg_arg = mock_store.store_adg.call_args[0][0]
        assert len(store_adg_arg.nodes) == 2
        mock_store.store_dismissal.assert_called_once()

    @patch("cli.main.GraphStore")
    @patch("cli.main._get_repo")
    def test_restore_dismissals_stored(self, mock_get_repo, mock_store_cls, tmp_path):
        mock_get_repo.return_value = _MOCK_REPO_CFG
        mock_store = MagicMock()
        mock_store_cls.return_value = mock_store

        d1 = _make_dismissal(identity_hash="a" * 64)
        d2 = _make_dismissal(subject="app.api.*", identity_hash="b" * 64)
        fixture = _serialize_fixture(ADG(), [d1, d2])
        fixture_path = tmp_path / "fixture.json"
        fixture_path.write_text(json.dumps(fixture))

        result = runner.invoke(app, ["seed", "restore", "--repo", "test-repo", "--input", str(fixture_path)])
        assert result.exit_code == 0
        assert mock_store.store_dismissal.call_count == 2

    @patch("cli.main._get_repo")
    def test_restore_missing_file_exits(self, mock_get_repo, tmp_path):
        mock_get_repo.return_value = _MOCK_REPO_CFG
        result = runner.invoke(app, ["seed", "restore", "--repo", "test-repo", "--input", str(tmp_path / "nope.json")])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    @patch("cli.main.GraphStore")
    @patch("cli.main._get_repo")
    def test_restore_empty_fixture(self, mock_get_repo, mock_store_cls, tmp_path):
        mock_get_repo.return_value = _MOCK_REPO_CFG
        mock_store = MagicMock()
        mock_store_cls.return_value = mock_store

        fixture = _serialize_fixture(ADG(), [])
        fixture_path = tmp_path / "fixture.json"
        fixture_path.write_text(json.dumps(fixture))

        result = runner.invoke(app, ["seed", "restore", "--repo", "test-repo", "--input", str(fixture_path)])
        assert result.exit_code == 0
        mock_store.store_adg.assert_called_once()
        store_adg_arg = mock_store.store_adg.call_args[0][0]
        assert len(store_adg_arg.nodes) == 0
        mock_store.store_dismissal.assert_not_called()