"""Tests for constraint models and PredicateType.

Public interface under test:
    PredicateType: enum with PROHIBITS_DEPENDENCY, REQUIRES_IMPLEMENTATION,
                         REQUIRES_DEPENDENCY, PROHIBITS_IMPLEMENTATION
    SUBJECT_KINDS / OBJECT_KINDS: kind filters per predicate
    ConstraintEdge: dataclass with subject, predicate, object, justification,
                     adr_id, adr_path
    SymbolicConstraint: dataclass with 7 extracted fields + adr metadata
    ResolvedConstraint: dataclass with constraint_edge + match tracking
    ExtractionError: dataclass with message, adr_path, error_type
    ExtractionResult: dataclass with constraints (SymbolicConstraint) and errors
"""

from __future__ import annotations

import pytest

from services.models import (
    ConstraintEdge,
    ExtractionError,
    ExtractionResult,
    OBJECT_KINDS,
    PredicateType,
    ResolvedConstraint,
    SUBJECT_KINDS,
    SymbolicConstraint,
)


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
            adr_id="ADR-005",
            adr_path="docs/adr/ADR-005-microservices-boundary.md",
        )
        assert ".*" in edge.subject
        assert ".*" in edge.object


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
                adr_id="ADR-001",
            )

    def test_self_loop_rejected(self) -> None:
        """subject == object is a self-loop and must be rejected."""
        with pytest.raises(ValueError, match="subject and object must differ"):
            ConstraintEdge(
                subject="app.auth.middleware",
                predicate=PredicateType.REQUIRES_IMPLEMENTATION,
                object="app.auth.middleware",
                justification="Only app.auth.middleware may implement authentication.",
                adr_id="ADR-010",
                adr_path="docs/adr/010.md",
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
            message="Extraction missing fields",
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
            SymbolicConstraint(
                subject_role_general="app.services",
                subject_role_specific="service",
                predicate=PredicateType.PROHIBITS_DEPENDENCY,
                object_role_general="mysql",
                object_role_specific="connector",
                justification="Direct MySQL connections prohibited.",
                extraction_text="Direct MySQL connections are prohibited",
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
            SymbolicConstraint(
                subject_role_general="app.services",
                subject_role_specific="service",
                predicate=PredicateType.PROHIBITS_DEPENDENCY,
                object_role_general="mysql",
                object_role_specific="connector",
                justification="Direct MySQL connections prohibited.",
                extraction_text="Direct MySQL connections are prohibited",
                adr_id="ADR-001",
                adr_path="docs/adr/ADR-001-mysql-storage.md",
            ),
        ]
        errors = [
            ExtractionError(
                message="Extraction skipped, malformed",
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
                message="Extraction missing fields",
                adr_path="docs/adr/ADR-002.md",
                error_type="malformed_extraction",
            ),
        ]
        result = ExtractionResult(constraints=[], errors=errors)
        assert len(result.errors) == 2


# ===========================================================================
# 6. SUBJECT_KINDS and OBJECT_KINDS
# ===========================================================================


class TestSubjectKinds:
    """SUBJECT_KINDS maps predicate values to allowed FQNKind sets."""

    def test_dependency_predicates_allow_module_only(self) -> None:
        assert SUBJECT_KINDS["requires_dependency"] == {"module"}
        assert SUBJECT_KINDS["prohibits_dependency"] == {"module"}

    def test_implementation_predicates_allow_module_and_class(self) -> None:
        assert SUBJECT_KINDS["requires_implementation"] == {"module", "class"}
        assert SUBJECT_KINDS["prohibits_implementation"] == {"module", "class"}

    def test_covers_all_predicates(self) -> None:
        for pred in PredicateType:
            assert pred.value in SUBJECT_KINDS


class TestObjectKinds:
    """OBJECT_KINDS maps predicate values to allowed FQNKind sets."""

    def test_dependency_predicates_allow_module_only(self) -> None:
        assert OBJECT_KINDS["requires_dependency"] == {"module"}
        assert OBJECT_KINDS["prohibits_dependency"] == {"module"}

    def test_implementation_predicates_allow_class_function_method(self) -> None:
        expected = {"class", "function", "method"}
        assert OBJECT_KINDS["requires_implementation"] == expected
        assert OBJECT_KINDS["prohibits_implementation"] == expected

    def test_covers_all_predicates(self) -> None:
        for pred in PredicateType:
            assert pred.value in OBJECT_KINDS


# ===========================================================================
# 7. SymbolicConstraint
# ===========================================================================


class TestSymbolicConstraintConstruction:
    """SymbolicConstraint holds 7 extracted fields plus ADR metadata."""

    def test_full_construction(self) -> None:
        sc = SymbolicConstraint(
            subject_role_general="app.services",
            subject_role_specific="service",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object_role_general="mysql",
            object_role_specific="connector",
            justification="No direct MySQL connections.",
            extraction_text="Direct MySQL connections are prohibited",
            adr_id="ADR-001",
            adr_path="docs/adr/ADR-001-mysql-storage.md",
        )
        assert sc.subject_role_general == "app.services"
        assert sc.subject_role_specific == "service"
        assert sc.predicate is PredicateType.PROHIBITS_DEPENDENCY
        assert sc.object_role_general == "mysql"
        assert sc.object_role_specific == "connector"
        assert sc.justification == "No direct MySQL connections."
        assert sc.extraction_text == "Direct MySQL connections are prohibited"
        assert sc.adr_id == "ADR-001"

    def test_requires_implementation(self) -> None:
        sc = SymbolicConstraint(
            subject_role_general="app.api",
            subject_role_specific="endpoint",
            predicate=PredicateType.REQUIRES_IMPLEMENTATION,
            object_role_general="app.auth",
            object_role_specific="authentication logic",
            justification="All API endpoints must implement auth.",
            extraction_text="All API endpoints shall implement authentication",
            adr_id="ADR-003",
            adr_path="docs/adr/003-auth.md",
        )
        assert sc.predicate is PredicateType.REQUIRES_IMPLEMENTATION

    def test_exclusion_pattern_two_constraints(self) -> None:
        """Exclusion pattern produces two SymbolicConstraints."""
        general = SymbolicConstraint(
            subject_role_general="app",
            subject_role_specific="module",
            predicate=PredicateType.PROHIBITS_IMPLEMENTATION,
            object_role_general="app.auth",
            object_role_specific="authentication logic",
            justification="No module outside app.auth shall implement auth.",
            extraction_text="No module outside app.auth",
            adr_id="ADR-005",
            adr_path="docs/adr/005.md",
        )
        specific = SymbolicConstraint(
            subject_role_general="app.auth",
            subject_role_specific="auth module",
            predicate=PredicateType.REQUIRES_IMPLEMENTATION,
            object_role_general="app.auth",
            object_role_specific="authentication logic",
            justification="Only app.auth shall implement auth.",
            extraction_text="Only app.auth",
            adr_id="ADR-005",
            adr_path="docs/adr/005.md",
        )
        assert general.predicate is PredicateType.PROHIBITS_IMPLEMENTATION
        assert specific.predicate is PredicateType.REQUIRES_IMPLEMENTATION


class TestSymbolicConstraintValidation:
    """SymbolicConstraint rejects empty required fields."""

    def test_empty_subject_role_general_rejected(self) -> None:
        with pytest.raises(ValueError, match="subject_role_general"):
            SymbolicConstraint(
                subject_role_general="",
                subject_role_specific="service",
                predicate=PredicateType.PROHIBITS_DEPENDENCY,
                object_role_general="mysql",
                object_role_specific="connector",
                justification="Test",
                extraction_text="test text",
                adr_id="ADR-001",
                adr_path="docs/adr/001.md",
            )

    def test_empty_object_role_general_rejected(self) -> None:
        with pytest.raises(ValueError, match="object_role_general"):
            SymbolicConstraint(
                subject_role_general="app.services",
                subject_role_specific="service",
                predicate=PredicateType.PROHIBITS_DEPENDENCY,
                object_role_general="",
                object_role_specific="connector",
                justification="Test",
                extraction_text="test text",
                adr_id="ADR-001",
                adr_path="docs/adr/001.md",
            )

    def test_empty_justification_rejected(self) -> None:
        with pytest.raises(ValueError, match="justification"):
            SymbolicConstraint(
                subject_role_general="app.services",
                subject_role_specific="service",
                predicate=PredicateType.PROHIBITS_DEPENDENCY,
                object_role_general="mysql",
                object_role_specific="connector",
                justification="",
                extraction_text="test text",
                adr_id="ADR-001",
                adr_path="docs/adr/001.md",
            )

    def test_empty_extraction_text_rejected(self) -> None:
        with pytest.raises(ValueError, match="extraction_text"):
            SymbolicConstraint(
                subject_role_general="app.services",
                subject_role_specific="service",
                predicate=PredicateType.PROHIBITS_DEPENDENCY,
                object_role_general="mysql",
                object_role_specific="connector",
                justification="Test",
                extraction_text="",
                adr_id="ADR-001",
                adr_path="docs/adr/001.md",
            )

    def test_missing_adr_id_rejected(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            SymbolicConstraint(
                subject_role_general="app.services",
                subject_role_specific="service",
                predicate=PredicateType.PROHIBITS_DEPENDENCY,
                object_role_general="mysql",
                object_role_specific="connector",
                justification="Test",
                extraction_text="test text",
                adr_path="docs/adr/001.md",
            )

    def test_missing_adr_path_rejected(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            SymbolicConstraint(
                subject_role_general="app.services",
                subject_role_specific="service",
                predicate=PredicateType.PROHIBITS_DEPENDENCY,
                object_role_general="mysql",
                object_role_specific="connector",
                justification="Test",
                extraction_text="test text",
                adr_id="ADR-001",
            )


# ===========================================================================
# 8. ResolvedConstraint
# ===========================================================================


class TestResolvedConstraint:
    """ResolvedConstraint wraps a ConstraintEdge with match-source tracking."""

    def test_construction(self) -> None:
        edge = ConstraintEdge(
            subject="app.services.*",
            predicate=PredicateType.PROHIBITS_DEPENDENCY,
            object="mysql.connector",
            justification="No direct MySQL.",
            adr_id="ADR-001",
            adr_path="docs/adr/001.md",
        )
        rc = ResolvedConstraint(
            constraint_edge=edge,
            subject_matched_by="general_wildcard",
            object_matched_by="external",
        )
        assert rc.constraint_edge is edge
        assert rc.subject_matched_by == "general_wildcard"
        assert rc.object_matched_by == "external"

    def test_match_sources(self) -> None:
        """All documented match sources are valid strings."""
        edge = ConstraintEdge(
            subject="app.api.*",
            predicate=PredicateType.REQUIRES_IMPLEMENTATION,
            object="app.auth.middleware",
            justification="test",
            adr_id="ADR-003",
            adr_path="docs/adr/003.md",
        )
        for source in ("specific", "general_wildcard", "fallback", "human"):
            rc = ResolvedConstraint(
                constraint_edge=edge,
                subject_matched_by=source,
                object_matched_by=source,
            )
            assert rc.subject_matched_by == source
            assert rc.object_matched_by == source