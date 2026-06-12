"""Smoke test: run the seed-build pipeline against the flask repo and print output.

No assertions, no pytest, just visual inspection of logs and data.
Run with: uv run python tests/sanity/test_seed_build.py
"""

import logging

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

from pathlib import Path

from services.adg import parse_repo
from services.adg.merge import merge_constraints
from services.models import ConstraintEdge, FQNKind, PredicateType

REPO_ROOT = Path(__file__).resolve().parents[3]
FLASK_REPO = REPO_ROOT / "repos" / "flask"

FLASK_CONSTRAINTS = [
    ConstraintEdge(
        subject="app.routes.*",
        predicate=PredicateType.PROHIBITS_DEPENDENCY,
        object="app.models.*",
        justification="Routes must not import models directly.",
        adr_id="ADR-001",
        adr_path="docs/adr/001-layered-architecture.md",
    ),
    ConstraintEdge(
        subject="app.routes.*",
        predicate=PredicateType.REQUIRES_IMPLEMENTATION,
        object="app.middleware.auth",
        justification="All routes must use auth middleware.",
        adr_id="ADR-002",
        adr_path="docs/adr/002-auth-middleware-required.md",
    ),
]


def main() -> None:
    print("=" * 60)
    print("SEED BUILD SMOKE TEST")
    print("=" * 60)

    # Step 1: parse repo
    print("\n--- Step 1: parse_repo ---")
    adg = parse_repo(FLASK_REPO)
    print(f"  nodes: {len(adg.nodes)}")
    print(f"  edges: {len(adg.edges)}")
    for node in sorted(adg.nodes, key=lambda n: str(n.fqn)):
        print(f"    {node.kind.value:8s} {node.fqn}")
    for edge in adg.edges:
        print(f"    {edge.source} -[{edge.kind}]-> {edge.target}")

    # Step 2: merge constraints
    print("\n--- Step 2: merge_constraints ---")
    merged = merge_constraints(adg, FLASK_CONSTRAINTS)
    print(f"  constraint_edges: {len(merged.constraint_edges)}")
    for ce in merged.constraint_edges:
        print(f"    [{ce.adr_id}] {ce.subject} -[{ce.predicate.value}]-> {ce.object}  specificity={ce.specificity}")

    external = [n for n in merged.nodes if n.kind == FQNKind.EXTERNAL]
    print(f"  EXTERNAL nodes: {len(external)}")
    for n in external:
        print(f"    {n.fqn}")

    structural = [n for n in merged.nodes if n.kind != FQNKind.EXTERNAL]
    print(f"  structural nodes: {len(structural)} (original: {len(adg.nodes)})")

    print("\nDone.")


if __name__ == "__main__":
    main()