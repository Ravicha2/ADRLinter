"""Tests for ConstraintEdge, ExtractionError, ExtractionResult, and PredicateType.

Public interface under test:
    PredicateType: enum with PROHIBITS_DEPENDENCY, REQUIRES_IMPLEMENTATION,
                         REQUIRES_DEPENDENCY, PROHIBITS_IMPLEMENTATION
    ConstraintEdge: dataclass with subject, predicate, object, justification,
                     char_interval, adr_id, adr_path
    ExtractionError: dataclass with message, adr_path, error_type
    ExtractionResult: dataclass with constraints and errors
"""

from __future__ import annotations

import pytest

from services.models import ConstraintEdge, ExtractionError, ExtractionResult, PredicateType


# ===========================================================================
# 1. PredicateType enum
# ===========================================================================


class TestPredicateType:
    """PredicateType has four values for ADR constraint predicates."""

    def test_prohibits_dependency_value(self) -> None:
        assert PredicateType.PROHIBITS_DEPENDENCY.value == "prohibits_dependency"

    def test_requires_implementation_value(self) -> None:
        assert PredicateType.REQUIRES_IMPLEMENTATION.value == "requires_implementation"

    def test_requires_dependency_value(self) -> None:
        assert PredicateType.REQUIRES_DEPENDENCY.value == "requires_dependency"

    def test_prohibits_implementation_value(self) -> None:
        assert PredicateType.PROHIBITS_IMPLEMENTATION.value == "prohibits_implementation"

    def test_enum_membership(self) -> None:
        """PredicateType has exactly four members."""
        assert len(PredicateType) == 4

    def test_from_value(self) -> None:
        """PredicateType can be constructed from its string value."""
        assert PredicateType("prohibits_dependency") is PredicateType.PROHIBITS_DEPENDENCY
        assert PredicateType("requires_implementation") is PredicateType.REQUIRES_IMPLEMENTATION
        assert PredicateType("requires_dependency") is PredicateType.REQUIRES_DEPENDENCY
        assert PredicateType("prohibits_implementation") is PredicateType.PROHIBITS_IMPLEMENTATION

    def test_invalid_value_raises(self) -> None:
        """Invalid predicate string raises ValueError."""
        with pytest.raises(ValueError):
            PredicateType("invalid_predicate")


# ===========================================================================
# 2. ConstraintEdge construction
# ===========================================================================


class TestConstraintEdgeConstruction:
    """ConstraintEdge holds an ADR-sourced constraint with traceability."""

    def test_prohibits_dependency_constraint(self) -> None:
        edge = ConstraintEdge(
            subject="app.services.*",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="app.db.mysql",
            justification="Direct MySQL connections are prohibited for services.",
            char_interval=(45, 120),
            adr_id="ADR-001",
            adr_path="docs/adr/ADR-001-mysql-storage.md",
        )
        assert edge.subject == "app.services.*"
        assert edge.predicate is PredicateType.PROHIBITS_DEPENDENCY
        assert edge.object == "app.db.mysql"
        assert edge.adr_id == "ADR-001"

    def test_requires_implementation_constraint(self) -> None:
        edge = ConstraintEdge(
            subject="app.api.*",
            predicate=PredicateType.REQUIRES_IMPLEMENTATION,
            object="app.auth.middleware",
            justification="All API endpoints must implement authentication.",
            char_interval=(10, 80),
            adr_id="ADR-003",
            adr_path="docs/adr/ADR-003-auth-middleware.md",
        )
        assert edge.predicate is PredicateType.REQUIRES_IMPLEMENTATION
        assert edge.object == "app.auth.middleware"

    def test_requires_dependency_constraint(self) -> None:
        edge = ConstraintEdge(
            subject="app.api.*",
            predicate=PredicateType.REQUIRES_DEPENDENCY,
            object="app.auth.middleware",
            justification="All API endpoints must use the auth middleware.",
            char_interval=(10, 80),
            adr_id="ADR-004",
            adr_path="docs/adr/ADR-004-auth-required.md",
        )
        assert edge.predicate is PredicateType.REQUIRES_DEPENDENCY
        assert edge.object == "app.auth.middleware"

    def test_prohibits_implementation_constraint(self) -> None:
        edge = ConstraintEdge(
            subject="app.services.*",
            predicate=PredicateType.PROHIBITS_IMPLEMENTATION,
            object="app.auth.middleware",
            justification="No service shall implement its own authentication logic.",
            char_interval=(20, 90),
            adr_id="ADR-005",
            adr_path="docs/adr/ADR-005-auth-centralized.md",
        )
        assert edge.predicate is PredicateType.PROHIBITS_IMPLEMENTATION
        assert edge.subject == "app.services.*"

    def test_nonexistent_fqn_object(self) -> None:
        """ConstraintEdge accepts FQNs that don't exist in the codebase yet.

        ADRs describe architectural intent. An ADR can mandate
        'app.db.postgres MUST be implemented' before that module exists.
        """
        edge = ConstraintEdge(
            subject="app.services.new.*",
            predicate=PredicateType.REQUIRES_IMPLEMENTATION,
            object="app.db.postgres",
            justification="New services must use PostgreSQL.",
            char_interval=(0, 50),
            adr_id="ADR-004",
            adr_path="docs/adr/ADR-004-postgres-migration.md",
        )
        assert edge.object == "app.db.postgres"

    def test_wildcard_subject_and_object(self) -> None:
        """Both subject and object can contain wildcards."""
        edge = ConstraintEdge(
            subject="app.services.*",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="app.services.<other_service>.*",
            justification="Services must not depend on other services.",
            char_interval=(0, 60),
            adr_id="ADR-005",
            adr_path="docs/adr/ADR-005-microservices-boundary.md",
        )
        assert ".*" in edge.subject
        assert ".*" in edge.object

    def test_char_interval_as_tuple(self) -> None:
        """char_interval is a tuple of (start, end) character positions."""
        edge = ConstraintEdge(
            subject="app.api.*",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="app.auth.impl",
            justification="No direct auth implementation.",
            char_interval=(100, 200),
            adr_id="ADR-003",
            adr_path="docs/adr/ADR-003-auth-middleware.md",
        )
        assert edge.char_interval == (100, 200)
        assert isinstance(edge.char_interval, tuple)

    def test_char_interval_none_allowed(self) -> None:
        """char_interval is optional; None means no source traceability."""
        edge = ConstraintEdge(
            subject="app.api.*",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="app.auth.impl",
            justification="No direct auth implementation.",
            char_interval=None,
            adr_id="ADR-003",
            adr_path="docs/adr/ADR-003-auth-middleware.md",
        )
        assert edge.char_interval is None


# ===========================================================================
# 3. ConstraintEdge validation
# ===========================================================================


class TestConstraintEdgeValidation:
    """ConstraintEdge rejects invalid or missing fields."""

    def test_empty_subject_rejected(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            ConstraintEdge(
                subject="",
                predicate=PredicateType.PROHIBITS_DEPENDENCY,
                object="app.db.mysql",
                justification="Test",
                char_interval=(0, 10),
                adr_id="ADR-001",
                adr_path="docs/adr/ADR-001.md",
            )

    def test_empty_object_rejected(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            ConstraintEdge(
                subject="app.services.*",
                predicate=PredicateType.PROHIBITS_DEPENDENCY,
                object="",
                justification="Test",
                char_interval=(0, 10),
                adr_id="ADR-001",
                adr_path="docs/adr/ADR-001.md",
            )

    def test_empty_justification_rejected(self) -> None:
        """Justification must be non-empty; it provides auditability."""
        with pytest.raises((ValueError, TypeError)):
            ConstraintEdge(
                subject="app.services.*",
                predicate=PredicateType.PROHIBITS_DEPENDENCY,
                object="app.db.mysql",
                justification="",
                char_interval=(0, 10),
                adr_id="ADR-001",
                adr_path="docs/adr/ADR-001.md",
            )

    def test_missing_adr_id_rejected(self) -> None:
        """adr_id is required for traceability back to the source ADR."""
        with pytest.raises((ValueError, TypeError)):
            ConstraintEdge(
                subject="app.services.*",
                predicate=PredicateType.PROHIBITS_DEPENDENCY,
                object="app.db.mysql",
                justification="Test justification",
                char_interval=(0, 10),
                adr_path="docs/adr/ADR-001.md",
            )

    def test_missing_adr_path_rejected(self) -> None:
        """adr_path is required for traceability."""
        with pytest.raises((ValueError, TypeError)):
            ConstraintEdge(
                subject="app.services.*",
                predicate=PredicateType.PROHIBITS_DEPENDENCY,
                object="app.db.mysql",
                justification="Test justification",
                char_interval=(0, 10),
                adr_id="ADR-001",
            )

    def test_inverted_char_interval_rejected(self) -> None:
        """char_interval with end <= start is rejected."""
        with pytest.raises(ValueError, match="end must be > start"):
            ConstraintEdge(
                subject="app.services.*",
                predicate=PredicateType.PROHIBITS_DEPENDENCY,
                object="app.db.mysql",
                justification="Test",
                char_interval=(200, 100),
                adr_id="ADR-001",
                adr_path="docs/adr/ADR-001.md",
            )

    def test_negative_char_interval_start_rejected(self) -> None:
        """char_interval with negative start is rejected."""
        with pytest.raises(ValueError, match="start must be >= 0"):
            ConstraintEdge(
                subject="app.services.*",
                predicate=PredicateType.PROHIBITS_DEPENDENCY,
                object="app.db.mysql",
                justification="Test",
                char_interval=(-5, 10),
                adr_id="ADR-001",
                adr_path="docs/adr/ADR-001.md",
            )


# ===========================================================================
# 4. ExtractionError
# ===========================================================================


class TestExtractionError:
    """ExtractionError captures failures from the langextract module."""

    def test_api_failure_error(self) -> None:
        err = ExtractionError(
            message="Ollama API returned 429 rate limit",
            adr_path="docs/adr/ADR-001.md",
            error_type="api_failure",
        )
        assert err.message == "Ollama API returned 429 rate limit"
        assert err.adr_path == "docs/adr/ADR-001.md"
        assert err.error_type == "api_failure"

    def test_malformed_extraction_error(self) -> None:
        err = ExtractionError(
            message="Extraction missing char_interval",
            adr_path="docs/adr/ADR-002.md",
            error_type="malformed_extraction",
        )
        assert err.error_type == "malformed_extraction"

    def test_parse_failure_error(self) -> None:
        err = ExtractionError(
            message="Invalid predicate: 'requires'",
            adr_path="docs/adr/ADR-003.md",
            error_type="parse_failure",
        )
        assert "Invalid predicate" in err.message


# ===========================================================================
# 5. ExtractionResult
# ===========================================================================


class TestExtractionResult:
    """ExtractionResult holds both valid constraints and errors from extraction."""

    def test_successful_extraction(self) -> None:
        """All constraints extracted, no errors."""
        constraints = [
            ConstraintEdge(
                subject="app.services.*",
                predicate=PredicateType.PROHIBITS_DEPENDENCY,
                object="app.db.mysql",
                justification="Direct MySQL connections prohibited.",
                char_interval=(45, 120),
                adr_id="ADR-001",
                adr_path="docs/adr/ADR-001-mysql-storage.md",
            ),
        ]
        result = ExtractionResult(constraints=constraints, errors=[])
        assert len(result.constraints) == 1
        assert len(result.errors) == 0

    def test_extraction_with_only_errors(self) -> None:
        """API failure returns empty constraints with error details."""
        errors = [
            ExtractionError(
                message="Ollama API returned 402 quota exceeded",
                adr_path="docs/adr/ADR-001.md",
                error_type="api_failure",
            ),
        ]
        result = ExtractionResult(constraints=[], errors=errors)
        assert len(result.constraints) == 0
        assert len(result.errors) == 1
        assert result.errors[0].error_type == "api_failure"

    def test_partial_extraction(self) -> None:
        """Some constraints extracted, some malformed and reported as errors."""
        constraints = [
            ConstraintEdge(
                subject="app.services.*",
                predicate=PredicateType.PROHIBITS_DEPENDENCY,
                object="app.db.mysql",
                justification="Direct MySQL connections prohibited.",
                char_interval=(45, 120),
                adr_id="ADR-001",
                adr_path="docs/adr/ADR-001-mysql-storage.md",
            ),
        ]
        errors = [
            ExtractionError(
                message="Extraction missing char_interval, skipped",
                adr_path="docs/adr/ADR-001-mysql-storage.md",
                error_type="malformed_extraction",
            ),
        ]
        result = ExtractionResult(constraints=constraints, errors=errors)
        assert len(result.constraints) == 1
        assert len(result.errors) == 1

    def test_no_constraints_found_is_valid(self) -> None:
        """An ADR with no enforceable constraints is not an error."""
        result = ExtractionResult(constraints=[], errors=[])
        assert len(result.constraints) == 0
        assert len(result.errors) == 0

    def test_multiple_errors(self) -> None:
        """Multiple failures are all reported."""
        errors = [
            ExtractionError(
                message="Invalid predicate: 'requires'",
                adr_path="docs/adr/ADR-002.md",
                error_type="parse_failure",
            ),
            ExtractionError(
                message="Extraction missing char_interval",
                adr_path="docs/adr/ADR-002.md",
                error_type="malformed_extraction",
            ),
        ]
        result = ExtractionResult(constraints=[], errors=errors)
        assert len(result.errors) == 2