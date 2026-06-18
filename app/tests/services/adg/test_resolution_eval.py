"""Evaluation tests for LLM-backed naming resolution.

These tests call the actual LLM to verify prompt quality and resolution accuracy.
They are NOT unit tests: they cost money, are non-deterministic, and must be run
explicitly with `pytest -m llm_eval`.

Each test case has:
  1. An orphaned FQN pattern (from mock ADR extraction)
  2. Candidate FQNs (from mock ADG nodes)
  3. The constraint justification
  4. The expected remapped pattern

Run:  uv run pytest -m llm_eval tests/services/adg/test_resolution_eval.py -v
Skip: pytest --ignore=tests/services/adg/test_resolution_eval.py  (default)
"""

from __future__ import annotations

import os

import pytest

from services.fqn import FQN
from services.models import (
    ADG,
    ConstraintEdge,
    FQNKind,
    FQNNode,
    PredicateType,
)

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
        char_interval=(0, len(justification)),
        adr_id=adr_id,
        adr_path=f"docs/adr/{adr_id.lower()}.md",
    )


def _get_config():
    """Get LLM config; skip test if key is missing."""
    from services.extract.config import LangExtractConfig

    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        pytest.skip("OPENROUTER_API_KEY not set, skipping LLM eval")
    return LangExtractConfig()


# ===========================================================================
# Eval cases
# ===========================================================================


EVAL_CASES = [
    # (id, orphan_pattern, side, candidate_fqns, justification, expected_remap)
    #
    # side: "subject" or "object" — which side of the constraint is orphaned

    # Case 1: Flask-style naming mismatch
    # ADR says "api", code says "routes"
    (
        "flask_api_vs_routes",
        "app.api.*",
        "subject",
        [
            "app",
            "app.routes",
            "app.routes.users",
            "app.routes.orders",
            "app.auth",
            "app.auth.middleware",
            "app.models",
            "app.models.user",
        ],
        "All API endpoints must implement authentication through app.auth.middleware.",
        "app.routes.*",
    ),

    # Case 2: Same-depth synonym without wildcard
    (
        "api_vs_routes_concrete",
        "app.api",
        "subject",
        [
            "app",
            "app.routes",
            "app.routes.users",
            "app.auth",
            "app.auth.middleware",
        ],
        "The API layer shall not depend on the database directly.",
        "app.routes",
    ),

    # Case 3: Hierarchy mismatch — ADR says one level, code uses another
    (
        "handler_vs_controller",
        "app.handlers.*",
        "subject",
        [
            "app",
            "app.controllers",
            "app.controllers.user_controller",
            "app.controllers.order_controller",
            "app.services",
        ],
        "Handlers must use the service layer for business logic.",
        "app.controllers.*",
    ),

    # Case 4: Object side orphaned — auth middleware naming
    (
        "auth_guard_vs_middleware",
        "app.auth.guard",
        "object",
        [
            "app",
            "app.routes",
            "app.auth",
            "app.auth.middleware",
        ],
        "All routes must authenticate through the auth guard.",
        "app.auth.middleware",
    ),

    # Case 5: Forward declaration (should return no_mapping)
    # The ADR references a module that genuinely doesn't exist yet
    (
        "forward_declaration_no_match",
        "app.billing.*",
        "subject",
        [
            "app",
            "app.routes",
            "app.auth",
            "app.auth.middleware",
        ],
        "Billing module must use the payment gateway for transactions.",
        "no_mapping",
    ),

    # Case 6: Multiple reasonable candidates, LLM must pick best
    (
        "ambiguous_api_mapping",
        "app.api.v1.*",
        "subject",
        [
            "app",
            "app.routes",
            "app.routes.v1",
            "app.routes.v1.users",
            "app.routes.v1.orders",
            "app.services",
            "app.services.user",
        ],
        "API v1 endpoints must validate requests through the schema module.",
        "app.routes.v1.*",
    ),
]


# ===========================================================================
# Tests
# ===========================================================================


@pytest.mark.llm_eval
@pytest.mark.parametrize(
    "case_id, orphan_pattern, side, candidate_fqns, justification, expected",
    EVAL_CASES,
    ids=[c[0] for c in EVAL_CASES],
)
def test_resolution_remaps_correctly(
    case_id: str,
    orphan_pattern: str,
    side: str,
    candidate_fqns: list[str],
    justification: str,
    expected: str,
) -> None:
    """Call actual LLM to remap an orphan FQN pattern to the correct ADG node."""
    from services.adg.merge import gather_candidates, _call_resolution_llm

    config = _get_config()
    nodes = _make_nodes(candidate_fqns)
    adg = ADG(nodes=nodes, edges=[])

    candidates = gather_candidates(orphan_pattern, adg.nodes)

    # If the pattern already matches, no resolution needed — skip
    if not candidates:
        pytest.skip(f"Pattern {orphan_pattern} already matches a node, no resolution needed")

    result = _call_resolution_llm(orphan_pattern, candidates, justification, config)
    assert result == expected, (
        f"{case_id}: expected {expected!r}, got {result!r} "
        f"(pattern={orphan_pattern!r}, candidates={[str(c.fqn) for c in candidates][:5]})"
    )


@pytest.mark.llm_eval
def test_resolution_subject_and_object_both_orphaned() -> None:
    """Both subject and object are orphaned: two LLM calls, both should remap."""
    from services.adg.merge import resolve_orphans, _make_llm_resolver
    from services.resolver import NameResolver

    config = _get_config()

    nodes = _make_nodes([
        "app",
        "app.routes",
        "app.routes.users",
        "app.auth",
        "app.auth.middleware",
    ])
    adg = ADG(nodes=nodes, edges=[])
    resolver = NameResolver({n.fqn for n in adg.nodes})

    constraints = [
        ConstraintEdge(
            subject="app.api.*",
            predicate=PredicateType.REQUIRES_DEPENDENCY,
            object="app.auth.guard",
            justification="All API endpoints must use the auth guard for authentication.",
            char_interval=(10, 80),
            adr_id="ADR-002",
            adr_path="docs/adr/002.md",
        ),
    ]

    remaining = resolve_orphans(adg, constraints, resolver, llm_resolver=_make_llm_resolver(config))

    assert constraints[0].subject == "app.routes.*", (
        f"Subject remap failed: got {constraints[0].subject!r}, expected 'app.routes.*'"
    )
    assert constraints[0].object == "app.auth.middleware", (
        f"Object remap failed: got {constraints[0].object!r}, expected 'app.auth.middleware'"
    )


@pytest.mark.llm_eval
def test_resolution_no_match_stays_orphan() -> None:
    """Forward-declaration orphan should not be remapped to an unrelated node."""
    from services.adg.merge import resolve_orphans, _make_llm_resolver
    from services.resolver import NameResolver

    config = _get_config()

    nodes = _make_nodes([
        "app",
        "app.routes",
        "app.auth",
    ])
    adg = ADG(nodes=nodes, edges=[])
    resolver = NameResolver({n.fqn for n in adg.nodes})

    constraints = [
        ConstraintEdge(
            subject="app.billing.*",
            predicate=PredicateType.REQUIRES_DEPENDENCY,
            object="app.payments.gateway",
            justification="Billing must use the payment gateway for all transactions.",
            char_interval=(10, 80),
            adr_id="ADR-099",
            adr_path="docs/adr/099.md",
        ),
    ]

    remaining = resolve_orphans(adg, constraints, resolver, llm_resolver=_make_llm_resolver(config))

    # Both sides should remain orphaned (no reasonable mapping exists)
    assert "app.billing.*" in remaining or constraints[0].subject == "app.billing.*"
    assert "app.payments.gateway" in remaining or constraints[0].object == "app.payments.gateway"