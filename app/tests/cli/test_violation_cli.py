"""Tests for violation list and dismiss CLI commands."""
from __future__ import annotations

from unittest.mock import MagicMock, patch
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli.config import RepoConfig
from cli.main import app, DetectionResult
from services.cpt.dismissal import Dismissal, violation_short_id
from services.cpt.engine import CPTResult
from services.cpt.resolution import Violation
from services.fqn import FQN
from services.models import ConstraintEdge, DiffResult, PredicateType
from services.resolver import MatchStatus

runner = CliRunner()


def _make_constraint(
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


def _make_violation(
    subject: str = "app.service.*",
    predicate: PredicateType = PredicateType.PROHIBITS_DEPENDENCY,
    object: str = "app.repo.*",
    matched_fqn: str = "app.service.UserService",
    adr_id: str = "ADR-001",
) -> Violation:
    return Violation(
        constraint=_make_constraint(subject, predicate, object, adr_id),
        changed_fqn=FQN.from_dotted_safe("app.service.UserService"),
        matched_fqn=FQN.from_dotted_safe(matched_fqn),
        match_status=MatchStatus.EXACT,
        evidence="test evidence",
        change_type="structural",
    )


def _make_cpt_result(violations: list[Violation] | None = None) -> CPTResult:
    if violations is None:
        violations = [
            _make_violation(adr_id="ADR-001"),
            _make_violation(matched_fqn="app.service.OrderService", adr_id="ADR-002"),
        ]
    return CPTResult(violations=violations, orphans=[], self_loop_constraints=[])


_MOCK_REPO_CFG = RepoConfig(id="test-repo", url="/tmp/test-repo", adr_dir="docs/adr")


def _make_detection_result(violations: list[Violation] | None = None) -> DetectionResult:
    cpt_result = _make_cpt_result(violations)
    mock_diff = MagicMock()
    mock_diff.to_sha = "abc123def456"
    mock_diff.from_sha = "parent123"
    return DetectionResult(
        cpt_result=cpt_result,
        diff=mock_diff,
        diff_result=DiffResult(to_sha="abc123def456", changed_files=[], changed_fqns=[]),
        repo_cfg=_MOCK_REPO_CFG,
        repo_path=Path("/tmp/test-repo"),
    )


class TestViolationList:
    """Test cpt violation list --repo <id>."""

    @patch("cli.main.GraphStore")
    @patch("cli.main._run_detection")
    def test_list_shows_active_violations_with_short_ids(self, mock_detect, mock_store_cls):
        dr = _make_detection_result()
        mock_detect.return_value = dr

        mock_store = MagicMock()
        mock_store.load_dismissals.return_value = []
        mock_store_cls.return_value = mock_store

        result = runner.invoke(app, ["violation", "list", "--repo", "test-repo"])
        assert result.exit_code == 0
        for v in dr.cpt_result.violations:
            assert violation_short_id(v) in result.output

    @patch("cli.main.GraphStore")
    @patch("cli.main._run_detection")
    def test_list_filters_dismissed(self, mock_detect, mock_store_cls):
        v1 = _make_violation(adr_id="ADR-001")
        dr = _make_detection_result(violations=[v1])
        mock_detect.return_value = dr

        dismissal = Dismissal.from_violation(v1)
        mock_store = MagicMock()
        mock_store.load_dismissals.return_value = [dismissal]
        mock_store_cls.return_value = mock_store

        result = runner.invoke(app, ["violation", "list", "--repo", "test-repo"])
        assert result.exit_code == 0
        assert "No active violations" in result.output

    @patch("cli.main.GraphStore")
    @patch("cli.main._run_detection")
    def test_list_shows_dismissed_count(self, mock_detect, mock_store_cls):
        v1 = _make_violation(adr_id="ADR-001")
        v2 = _make_violation(matched_fqn="app.service.OrderService", adr_id="ADR-002")
        dr = _make_detection_result(violations=[v1, v2])
        mock_detect.return_value = dr

        dismissal = Dismissal.from_violation(v1)
        mock_store = MagicMock()
        mock_store.load_dismissals.return_value = [dismissal]
        mock_store_cls.return_value = mock_store

        result = runner.invoke(app, ["violation", "list", "--repo", "test-repo"])
        assert "1 violation(s) dismissed" in result.output

    @patch("cli.main.GraphStore")
    @patch("cli.main._run_detection")
    def test_list_no_violations(self, mock_detect, mock_store_cls):
        dr = _make_detection_result(violations=[])
        mock_detect.return_value = dr
        mock_store = MagicMock()
        mock_store.load_dismissals.return_value = []
        mock_store_cls.return_value = mock_store

        result = runner.invoke(app, ["violation", "list", "--repo", "test-repo"])
        assert result.exit_code == 0
        assert "No active violations" in result.output


class TestViolationDismiss:
    """Test cpt violation dismiss <short_id> --repo <id>."""

    @patch("cli.main.GraphStore")
    @patch("cli.main._run_detection")
    def test_dismiss_by_short_id(self, mock_detect, mock_store_cls):
        v1 = _make_violation(adr_id="ADR-001")
        dr = _make_detection_result(violations=[v1])
        mock_detect.return_value = dr

        mock_store = MagicMock()
        mock_store_cls.return_value = mock_store

        short_id = violation_short_id(v1)
        result = runner.invoke(app, ["violation", "dismiss", short_id, "--repo", "test-repo"])
        assert result.exit_code == 0
        mock_store.connect.assert_called_once()
        mock_store.store_dismissal.assert_called_once()
        assert "Dismissed" in result.output

    @patch("cli.main.GraphStore")
    @patch("cli.main._run_detection")
    def test_dismiss_unknown_short_id_fails(self, mock_detect, mock_store_cls):
        dr = _make_detection_result()
        mock_detect.return_value = dr
        mock_store = MagicMock()
        mock_store_cls.return_value = mock_store

        result = runner.invoke(app, ["violation", "dismiss", "zzzzz", "--repo", "test-repo"])
        assert result.exit_code == 1
        assert "No violation" in result.output

    @patch("cli.main.GraphStore")
    @patch("cli.main._run_detection")
    def test_dismiss_stores_correct_fields(self, mock_detect, mock_store_cls):
        v1 = _make_violation(adr_id="ADR-001")
        dr = _make_detection_result(violations=[v1])
        mock_detect.return_value = dr

        mock_store = MagicMock()
        mock_store_cls.return_value = mock_store

        short_id = violation_short_id(v1)
        result = runner.invoke(app, ["violation", "dismiss", short_id, "--repo", "test-repo"])
        assert result.exit_code == 0

        call_args = mock_store.store_dismissal.call_args[0][0]
        assert isinstance(call_args, Dismissal)
        assert call_args.short_id == short_id
        assert call_args.subject == v1.constraint.subject
        assert call_args.predicate == v1.constraint.predicate.value


class TestSeedBuildWipesDismissals:
    """Per ADR 012: seed rebuild wipes all dismissals."""

    @patch("cli.main.GraphStore")
    @patch("cli.main.ADGPipeline")
    @patch("cli.main.extract_all_adrs")
    @patch("cli.main.derive_package_context")
    @patch("cli.main.parse_repo")
    @patch("cli.main._get_repo")
    @patch("cli.main.load_config")
    def test_seed_build_calls_delete_all_dismissals(
        self, mock_config, mock_get_repo, mock_parse, mock_derive,
        mock_extract, mock_pipeline_cls, mock_store_cls
    ):
        from services.models import ADG as ADGModel

        mock_config.return_value = MagicMock()
        mock_get_repo.return_value = _MOCK_REPO_CFG

        with patch.object(Path, "exists", return_value=True):
            mock_parse.return_value = ADGModel(nodes=[], edges=[], constraint_edges=[])
            mock_derive.return_value = {}
            mock_extract.return_value = []
            mock_pipeline = MagicMock()
            mock_pipeline.build_seed.return_value = ADGModel(nodes=[], edges=[], constraint_edges=[])
            mock_pipeline_cls.return_value = mock_pipeline

            mock_store = MagicMock()
            mock_store.delete_all_dismissals.return_value = 3
            mock_store_cls.return_value = mock_store

            result = runner.invoke(app, ["seed", "build", "--repo", "test-repo"])
            assert result.exit_code == 0
            mock_store.delete_all_dismissals.assert_called_once()
            assert "3" in result.output