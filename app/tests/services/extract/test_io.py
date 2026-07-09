"""Tests for ADR status parsing in io.py.

Public interface under test:
    parse_adr_status(adr_text: str) -> ADRStatus
"""

from __future__ import annotations

from services.extract.io import parse_adr_status
from services.models import ADRStatus


# ---------------------------------------------------------------------------
# Sample ADR texts
# ---------------------------------------------------------------------------

ADR_ACCEPTED = """\
# ADR-001: MySQL Storage

## Status

Accepted

## Decision

The database.query module is the only permitted interface.
"""

ADR_REJECTED = """\
# ADR-002: Use MongoDB

## Status

Rejected

## Decision

We considered MongoDB but decided against it.
"""

ADR_SUPERSEDED = """\
# ADR-006: Use Flask

## Status

Superceded by [10. Replace Flask with Django](0010-replace-flask-with-django.md)

## Decision

We will use Flask for the web framework.
"""

ADR_SUPERSEDED_CORRECT_SPELLING = """\
# ADR-007: Naming Resolution Layer

## Status

superseded by ADR8

## Decision

Original naming resolution approach replaced.
"""

ADR_ACCEPTED_WITH_PARENTHETICAL = """\
# ADR-005: Two-Phase CPT

## Status

Accepted (Phase 1 k-hop BFS superseded by ADR 009)

## Decision

Two-phase CPT detection with segment matching.
"""

ADR_NO_STATUS = """\
# ADR-004: Code Style

## Decision

We will use Black for code formatting.
"""

ADR_EMPTY_STATUS = """\
# ADR-010: Something

## Status

## Decision

No status value provided.
"""

ADR_UNKNOWN_STATUS = """\
# ADR-011: Deprecated Approach

## Status

Deprecated

## Decision

This approach is deprecated.
"""


class TestParseAdrStatus:
    """parse_adr_status returns the correct ADRStatus for various Status values."""

    def test_accepted(self) -> None:
        assert parse_adr_status(ADR_ACCEPTED) is ADRStatus.ACCEPTED

    def test_rejected(self) -> None:
        assert parse_adr_status(ADR_REJECTED) is ADRStatus.REJECTED

    def test_superseded_misspelling(self) -> None:
        """'Superceded' (common misspelling) maps to SUPERSEDED."""
        assert parse_adr_status(ADR_SUPERSEDED) is ADRStatus.SUPERSEDED

    def test_superseded_correct_spelling(self) -> None:
        assert parse_adr_status(ADR_SUPERSEDED_CORRECT_SPELLING) is ADRStatus.SUPERSEDED

    def test_accepted_with_parenthetical(self) -> None:
        """Status 'Accepted (superseded by ...)' starts with 'accepted'."""
        assert parse_adr_status(ADR_ACCEPTED_WITH_PARENTHETICAL) is ADRStatus.ACCEPTED

    def test_missing_status_section(self) -> None:
        """No ## Status section defaults to ACCEPTED."""
        assert parse_adr_status(ADR_NO_STATUS) is ADRStatus.ACCEPTED

    def test_empty_status_value(self) -> None:
        """## Status with no value on next line defaults to ACCEPTED."""
        assert parse_adr_status(ADR_EMPTY_STATUS) is ADRStatus.ACCEPTED

    def test_unknown_status_defaults_to_accepted(self) -> None:
        """Unrecognized status values fall through to ACCEPTED."""
        assert parse_adr_status(ADR_UNKNOWN_STATUS) is ADRStatus.ACCEPTED

    def test_case_insensitive_rejected(self) -> None:
        text = "# ADR\n\n## Status\n\nREJECTED\n\n## Decision\n\nNo.\n"
        assert parse_adr_status(text) is ADRStatus.REJECTED

    def test_case_insensitive_superceded(self) -> None:
        text = "# ADR\n\n## Status\n\nSUPERCEDED by ADR 010\n\n## Decision\n\nNo.\n"
        assert parse_adr_status(text) is ADRStatus.SUPERSEDED

    def test_status_with_markdown_link(self) -> None:
        """Status value containing markdown links is parsed correctly."""
        text = "# ADR\n\n## Status\n\nSuperceded by [ADR 010](010.md)\n\n## Decision\n\nNo.\n"
        assert parse_adr_status(text) is ADRStatus.SUPERSEDED