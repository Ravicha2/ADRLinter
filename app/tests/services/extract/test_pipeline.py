"""Tests for ADR detection in commit pipeline and seed build orchestration.

Public interface under test:
    is_adr_file(file_change: FileChange, adr_dir: str) -> bool
    extract_changed_adrs(diff: CommitDiff, adr_dir: str, config: LangExtractConfig)
        -> list[ExtractionResult]
    extract_all_adrs(repo_path: Path, adr_dir: str, config: LangExtractConfig)
        -> list[ExtractionResult]
    write_constraints(results: list[ExtractionResult], output_path: Path) -> None

Tests for ADRExtractor itself are in test_langextract.py.
These tests cover the orchestration: detecting ADR files in diffs,
routing them to extraction, and persisting results.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.models import (
    CommitDiff,
    ExtractionError,
    ExtractionResult,
    FileChange,
    PredicateType,
    SymbolicConstraint,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ADR_MYSQL_TEXT = """\
# ADR-001: MySQL Storage

## Status: Accepted

## Decision

The app.database.query module is the only permitted interface for database
access. All services must route queries through this interface. Direct MySQL
connections are prohibited for services in the app.services namespace.
"""

ADR_AUTH_TEXT = """\
# ADR-003: Auth Middleware

## Status: Accepted

## Decision

All API endpoints must implement authentication through the app.auth.middleware
component. No other module is permitted to implement authentication logic.
"""

PYTHON_SOURCE = b"class User:\n    def find(self):\n        pass\n"

MAKEFILE_SOURCE = b".PHONY: test\ntest:\n\tpytest\n"


def _make_constraint(
    subject_role_general: str = "app.services",
    subject_role_specific: str = "service",
    predicate: PredicateType = PredicateType.PROHIBITS_DEPENDENCY,
    object_role_general: str = "app.db",
    object_role_specific: str = "MySQL connector",
    adr_id: str = "ADR-001",
    adr_path: str = "docs/adr/ADR-001-mysql-storage.md",
) -> SymbolicConstraint:
    return SymbolicConstraint(
        subject_role_general=subject_role_general,
        subject_role_specific=subject_role_specific,
        predicate=predicate,
        object_role_general=object_role_general,
        object_role_specific=object_role_specific,
        justification="Test constraint",
        extraction_text="test extraction text",
        adr_id=adr_id,
        adr_path=adr_path,
    )


# ===========================================================================
# 1. is_adr_file: detecting ADR files in a diff
# ===========================================================================


class TestIsAdrFile:
    """is_adr_file identifies changed files that are ADR documents."""

    def test_adr_file_under_adr_dir(self) -> None:
        """A .md file under the configured adr_dir is an ADR."""
        from services.extract import is_adr_file

        change = FileChange(path="docs/adr/ADR-001-mysql-storage.md", status="modified")
        assert is_adr_file(change, adr_dir="docs/adr") is True

    def test_adr_file_in_nested_subdir(self) -> None:
        """An ADR file in a nested directory under adr_dir is detected."""
        from services.extract import is_adr_file

        change = FileChange(path="docs/adr/decisions/ADR-005.md", status="added")
        assert is_adr_file(change, adr_dir="docs/adr") is True

    def test_non_adr_markdown(self) -> None:
        """A .md file outside adr_dir is not an ADR."""
        from services.extract import is_adr_file

        change = FileChange(path="README.md", status="modified")
        assert is_adr_file(change, adr_dir="docs/adr") is False

    def test_python_file_not_adr(self) -> None:
        """A .py file is never an ADR regardless of directory."""
        from services.extract import is_adr_file

        change = FileChange(path="docs/adr/ADR-001.py", status="modified")
        assert is_adr_file(change, adr_dir="docs/adr") is False

    def test_adr_file_in_different_dir(self) -> None:
        """A .md file not under the configured adr_dir is not an ADR."""
        from services.extract import is_adr_file

        change = FileChange(path="other/ADR-001.md", status="modified")
        assert is_adr_file(change, adr_dir="docs/adr") is False

    def test_adr_dir_as_root_prefix(self) -> None:
        """Files starting with the adr_dir path are detected as ADRs."""
        from services.extract import is_adr_file

        # adr_dir is a prefix, so "docs/adr/arch/ADR-010.md" matches
        change = FileChange(path="docs/adr/arch/ADR-010.md", status="added")
        assert is_adr_file(change, adr_dir="docs/adr") is True

    def test_wrong_prefix_not_matched(self) -> None:
        """A file like 'docs/adraft/foo.md' must not match adr_dir='docs/adr'."""
        from services.extract import is_adr_file

        change = FileChange(path="docs/adraft/foo.md", status="modified")
        assert is_adr_file(change, adr_dir="docs/adr") is False


# ===========================================================================
# 2. extract_changed_adrs: incremental extraction from diff
# ===========================================================================


class TestExtractChangedAdrs:
    """extract_changed_adrs processes only ADR files changed in a commit."""

    @patch("services.extract.pipeline.ADRExtractor")
    def test_extracts_from_adr_files_in_diff(
        self, mock_extractor_cls: MagicMock
    ) -> None:
        """Only .md files under adr_dir in the diff are extracted."""
        from services.extract import LangExtractConfig, extract_changed_adrs

        mock_extractor = MagicMock()
        mock_extractor.extract_constraints.return_value = ExtractionResult(
            constraints=[_make_constraint()],
            errors=[],
        )
        mock_extractor_cls.return_value = mock_extractor

        diff = CommitDiff(
            commit_sha="abc123",
            parent_sha="abc122",
            changed_files=[
                FileChange(path="docs/adr/ADR-001-mysql-storage.md", status="modified"),
                FileChange(path="app/services/user.py", status="modified"),
            ],
            file_contents={
                "docs/adr/ADR-001-mysql-storage.md": ADR_MYSQL_TEXT.encode(),
                "app/services/user.py": PYTHON_SOURCE,
            },
            parent_contents={},
        )

        config = LangExtractConfig(api_key_env="TEST_API_KEY")
        results = extract_changed_adrs(diff, adr_dir="docs/adr", config=config)

        # Only the ADR file should be extracted
        assert len(results) == 1
        assert results[0].constraints[0].adr_id == "ADR-001"

    @patch("services.extract.pipeline.ADRExtractor")
    def test_no_adr_files_returns_empty(self, mock_extractor_cls: MagicMock) -> None:
        """A diff with no ADR files returns an empty list."""
        from services.extract import LangExtractConfig, extract_changed_adrs

        diff = CommitDiff(
            commit_sha="abc123",
            parent_sha="abc122",
            changed_files=[
                FileChange(path="app/services/user.py", status="modified"),
                FileChange(path="Makefile", status="modified"),
            ],
            file_contents={
                "app/services/user.py": PYTHON_SOURCE,
                "Makefile": MAKEFILE_SOURCE,
            },
            parent_contents={},
        )

        config = LangExtractConfig(api_key_env="TEST_API_KEY")
        results = extract_changed_adrs(diff, adr_dir="docs/adr", config=config)

        assert results == []

    @patch("services.extract.pipeline.ADRExtractor")
    def test_multiple_adr_files_in_diff(
        self, mock_extractor_cls: MagicMock
    ) -> None:
        """Multiple ADR files in the diff are all extracted."""
        from services.extract import LangExtractConfig, extract_changed_adrs

        mock_extractor = MagicMock()
        mock_extractor.extract_constraints.side_effect = [
            ExtractionResult(constraints=[_make_constraint(adr_id="ADR-001")], errors=[]),
            ExtractionResult(
                constraints=[
                    _make_constraint(
                        subject_role_general="app.api",
                        subject_role_specific="endpoint",
                        predicate=PredicateType.REQUIRES_IMPLEMENTATION,
                        object_role_general="app.auth",
                        object_role_specific="auth middleware",
                        adr_id="ADR-003",
                        adr_path="docs/adr/ADR-003-auth-middleware.md",
                    )
                ],
                errors=[],
            ),
        ]
        mock_extractor_cls.return_value = mock_extractor

        diff = CommitDiff(
            commit_sha="abc123",
            parent_sha="abc122",
            changed_files=[
                FileChange(path="docs/adr/ADR-001-mysql-storage.md", status="modified"),
                FileChange(path="docs/adr/ADR-003-auth-middleware.md", status="added"),
            ],
            file_contents={
                "docs/adr/ADR-001-mysql-storage.md": ADR_MYSQL_TEXT.encode(),
                "docs/adr/ADR-003-auth-middleware.md": ADR_AUTH_TEXT.encode(),
            },
            parent_contents={},
        )

        config = LangExtractConfig(api_key_env="TEST_API_KEY")
        results = extract_changed_adrs(diff, adr_dir="docs/adr", config=config)

        assert len(results) == 2
        assert mock_extractor.extract_constraints.call_count == 2

    @patch("services.extract.pipeline.ADRExtractor")
    def test_rejected_adr_skipped_in_diff(
        self, mock_extractor_cls: MagicMock
    ) -> None:
        """Rejected ADRs in a diff are skipped without calling the extractor."""
        from services.extract import LangExtractConfig, extract_changed_adrs

        mock_extractor = MagicMock()
        mock_extractor_cls.return_value = mock_extractor

        rejected_adr_text = b"# ADR-002: Use MongoDB\n\n## Status\n\nRejected\n\n## Decision\n\nNo.\n"

        diff = CommitDiff(
            commit_sha="abc123",
            parent_sha="abc122",
            changed_files=[
                FileChange(path="docs/adr/ADR-001-mysql-storage.md", status="modified"),
                FileChange(path="docs/adr/ADR-002-mongodb.md", status="added"),
            ],
            file_contents={
                "docs/adr/ADR-001-mysql-storage.md": ADR_MYSQL_TEXT.encode(),
                "docs/adr/ADR-002-mongodb.md": rejected_adr_text,
            },
            parent_contents={},
        )

        config = LangExtractConfig(api_key_env="TEST_API_KEY")
        results = extract_changed_adrs(diff, adr_dir="docs/adr", config=config)

        # Only the accepted ADR is extracted; the rejected one is skipped
        assert len(results) == 1
        mock_extractor.extract_constraints.assert_called_once()


# ===========================================================================
# 3. extract_all_adrs: seed build extraction
# ===========================================================================


class TestExtractAllAdrs:
    """extract_all_adrs processes all ADR files in a directory."""

    @patch("services.extract.pipeline.ADRExtractor")
    def test_extracts_all_adr_files(self, mock_extractor_cls: MagicMock) -> None:
        """All ADR-*.md files in adr_dir are extracted."""
        from services.extract import LangExtractConfig, extract_all_adrs

        mock_extractor = MagicMock()
        mock_extractor.extract_from_directory.return_value = [
            ExtractionResult(constraints=[_make_constraint()], errors=[]),
        ]
        mock_extractor_cls.return_value = mock_extractor

        config = LangExtractConfig(api_key_env="TEST_API_KEY")
        results = extract_all_adrs(
            repo_path=Path("/fake/repo"),
            adr_dir="docs/adr",
            config=config,
        )

        assert len(results) >= 1

    @patch("services.extract.pipeline.ADRExtractor")
    def test_empty_adr_dir_returns_empty(self, mock_extractor_cls: MagicMock) -> None:
        """An adr_dir with no ADR files returns empty results."""
        from services.extract import LangExtractConfig, extract_all_adrs

        mock_extractor = MagicMock()
        mock_extractor.extract_from_directory.return_value = []
        mock_extractor_cls.return_value = mock_extractor

        config = LangExtractConfig(api_key_env="TEST_API_KEY")

        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            results = extract_all_adrs(
                repo_path=Path(tmpdir),
                adr_dir="docs/adr",
                config=config,
            )
            assert results == []


# ===========================================================================
# 4. write_constraints: JSON persistence
# ===========================================================================


class TestWriteConstraints:
    """write_constraints serializes ExtractionResults to a JSON file."""

    def test_writes_valid_constraints(self, tmp_path: Path) -> None:
        """SymbolicConstraints are serialized to JSON with all fields."""
        from services.extract import write_constraints

        results = [
            ExtractionResult(
                constraints=[
                    _make_constraint(
                        subject_role_general="app.services",
                        predicate=PredicateType.PROHIBITS_DEPENDENCY,
                        object_role_general="app.db",
                    ),
                ],
                errors=[],
            ),
        ]

        output_path = tmp_path / "seeds" / "flask" / "constraints.json"
        write_constraints(results, output_path)

        assert output_path.exists()
        data = json.loads(output_path.read_text())
        assert "constraints" in data
        assert "errors" in data
        assert len(data["constraints"]) == 1
        assert data["constraints"][0]["subject_role_general"] == "app.services"
        assert data["constraints"][0]["predicate"] == "prohibits_dependency"

    def test_writes_multiple_results(self, tmp_path: Path) -> None:
        """Multiple ExtractionResults from different ADRs are all written."""
        from services.extract import write_constraints

        results = [
            ExtractionResult(
                constraints=[
                    _make_constraint(adr_id="ADR-001", adr_path="docs/adr/ADR-001.md"),
                ],
                errors=[],
            ),
            ExtractionResult(
                constraints=[
                    _make_constraint(
                        subject_role_general="app.api",
                        subject_role_specific="endpoint",
                        predicate=PredicateType.REQUIRES_IMPLEMENTATION,
                        object_role_general="app.auth",
                        object_role_specific="auth middleware",
                        adr_id="ADR-003",
                        adr_path="docs/adr/ADR-003.md",
                    ),
                ],
                errors=[],
            ),
        ]

        output_path = tmp_path / "seeds" / "flask" / "constraints.json"
        write_constraints(results, output_path)

        data = json.loads(output_path.read_text())
        assert len(data["constraints"]) == 2

    def test_includes_errors_in_output(self, tmp_path: Path) -> None:
        """ExtractionErrors are included in the JSON output for traceability."""
        from services.extract import write_constraints

        results = [
            ExtractionResult(
                constraints=[],
                errors=[
                    ExtractionError(
                        message="Ollama API returned 429 rate limit",
                        adr_path="docs/adr/ADR-001.md",
                        error_type="api_failure",
                    ),
                ],
            ),
        ]

        output_path = tmp_path / "seeds" / "flask" / "constraints.json"
        write_constraints(results, output_path)

        data = json.loads(output_path.read_text())
        assert len(data["errors"]) == 1
        assert data["errors"][0]["error_type"] == "api_failure"

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        """write_constraints creates the output directory if it doesn't exist."""
        from services.extract import write_constraints

        results = [
            ExtractionResult(constraints=[_make_constraint()], errors=[]),
        ]

        # Deep nested path that doesn't exist
        output_path = tmp_path / "seeds" / "flask" / "constraints.json"
        assert not output_path.parent.exists()

        write_constraints(results, output_path)

        assert output_path.exists()

    def test_empty_results_writes_empty_object(self, tmp_path: Path) -> None:
        """An empty list of results writes a JSON object with empty arrays."""
        from services.extract import write_constraints

        output_path = tmp_path / "seeds" / "flask" / "constraints.json"
        write_constraints([], output_path)

        data = json.loads(output_path.read_text())
        assert data == {"constraints": [], "errors": []}