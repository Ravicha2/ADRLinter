"""Smoke test: run the seed-build pipeline against the flask repo and print output.

No assertions, no pytest, just visual inspection of logs and data.
Run with: uv run python tests/sanity/test_seed_build.py
"""

import logging

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[3] / ".env")

from cli.config import load_config
from cli.main import _resolve_repo_path
from services.adg import parse_repo
from services.extract import extract_all_adrs
from services.extract.engine import derive_package_context
from services.models import FQNKind, SymbolicConstraint, PredicateType
from services.pipeline import ADGPipeline

REPO_ROOT = Path(__file__).resolve().parents[3]

FLASK_CONSTRAINTS = [
    SymbolicConstraint(
        subject_role_general="app.routes",
        subject_role_specific="app.routes.*",
        predicate=PredicateType.PROHIBITS_DEPENDENCY,
        object_role_general="app.models",
        object_role_specific="app.models.*",
        justification="Route handlers must not import anything from models directly.",
        extraction_text="Route handlers must not import anything from models directly.",
        adr_id="ADR-001",
        adr_path="docs/adr/001-layered-architecture.md",
    ),
    SymbolicConstraint(
        subject_role_general="app.routes",
        subject_role_specific="app.routes.*",
        predicate=PredicateType.REQUIRES_IMPLEMENTATION,
        object_role_general="app.middleware",
        object_role_specific="app.middleware.auth",
        justification="Every route handler must apply the @require_auth decorator from auth middleware.",
        extraction_text="Every route handler must apply the @require_auth decorator from auth middleware.",
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
    config = load_config()
    repo_cfg = config.get_repo("flask")
    repo_path = _resolve_repo_path(repo_cfg)
    adg = parse_repo(repo_path)
    print(f"  nodes: {len(adg.nodes)}")
    print(f"  edges: {len(adg.edges)}")
    for node in sorted(adg.nodes, key=lambda n: str(n.fqn)):
        print(f"    {node.kind.value:8s} {node.fqn}")
    for edge in adg.edges:
        print(f"    {edge.source} -[{edge.kind}]-> {edge.target}")

    # Step 2: build seed via pipeline (merge + specificity)
    print("\n--- Step 2: build_seed ---")
    pipeline = ADGPipeline()
    merged = pipeline.build_seed(adg, FLASK_CONSTRAINTS, project_root=repo_path)
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