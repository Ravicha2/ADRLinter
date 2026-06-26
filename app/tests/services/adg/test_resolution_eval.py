"""Evaluation tests for symbolic naming resolution.

These tests verify that NameResolver correctly matches FQN patterns
against the ADG node set, producing the expected match status and specificity.

Run:  uv run pytest -m llm_eval tests/services/adg/test_resolution_eval.py -v
Skip: pytest --ignore=tests/services/adg/test_resolution_eval.py  (default)
"""

from __future__ import annotations

import pytest

from services.fqn import FQN
from services.models import (
    ADG,
    ConstraintEdge,
    FQNKind,
    FQNNode,
    PredicateType,
)
from services.resolver import NameResolver, MatchStatus

# Skip entire module if no API key configured
pytestmark = pytest.mark.llm_eval


# ===========================================================================
# Helpers
# ===========================================================================


def _make_nodes(fqn_strs: list[str]) -> list[FQNNode]:
    """Create MODULE FQNNodes from dotted strings."""
    return [
        FQNNode(
            fqn=FQN.from_dotted(s),
            kind=FQNKind.MODULE,
            file_path=f"{'/'.join(FQN.from_dotted(s).parts)}.py",
            line_start=0,
            line_end=0,
            start_byte=0,
            end_byte=0,
        )
        for s in fqn_strs
    ]


def _make_constraint(
    subject: str,
    predicate: PredicateType,
    object: str,
    justification: str,
    adr_id: str = "ADR-001",
) -> ConstraintEdge:
    return ConstraintEdge(
        subject=subject,
        predicate=predicate,
        object=object,
        justification=justification,
        adr_id=adr_id,
        adr_path=f"docs/adr/{adr_id.lower()}.md",
    )


# ===========================================================================
# Tests: NameResolver matching accuracy
# ===========================================================================


@pytest.mark.llm_eval
def test_exact_match_recognized() -> None:
    """An exact FQN in the ADG should match with EXACT status."""
    nodes = _make_nodes(["app", "app.routes", "app.routes.users", "app.auth", "app.auth.middleware"])
    resolver = NameResolver({n.fqn for n in nodes})

    report = resolver.match("app.routes")
    assert report.status == MatchStatus.EXACT
    assert report.specificity >= 2.0


@pytest.mark.llm_eval
def test_wildcard_match_recognized() -> None:
    """A wildcard pattern should match child FQNs with WILDCARD status."""
    nodes = _make_nodes(["app", "app.routes", "app.routes.users", "app.auth"])
    resolver = NameResolver({n.fqn for n in nodes})

    report = resolver.match("app.routes.*")
    assert report.status == MatchStatus.WILDCARD


@pytest.mark.llm_eval
def test_no_match_for_unknown_pattern() -> None:
    """A pattern that doesn't match any node should report NO_MATCH."""
    nodes = _make_nodes(["app", "app.routes"])
    resolver = NameResolver({n.fqn for n in nodes})

    report = resolver.match("app.billing.*")
    assert report.status == MatchStatus.NO_MATCH
    assert report.specificity == 0.0