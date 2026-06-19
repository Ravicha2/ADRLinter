"""E2E sanity test: seed build via CLI + mock diff → CPT detect → print results.

Run with: uv run python tests/sanity/test_cpt_detect.py
"""

import logging
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[3] / ".env")

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

from cli.config import load_config
from services.adg import parse_repo
from services.adg.merge import merge_constraints
from services.cpt.engine import detect as cpt_detect
from services.extract import extract_all_adrs
from services.models import (
    ChangedFQN,
    DiffResult,
    FileChange,
    FQN,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


# ADR 001 violations (routes depending on models, routes missing services)
# ADR 002 violations (routes missing auth middleware dependency).
MOCK_DIFF = DiffResult(
    commit_sha="deadbeef",
    parent_sha="cafebabe",
    changed_files=[
        FileChange(path="app/routes/users.py", status="modified"),
        FileChange(path="app/routes/auth.py", status="modified"),
    ],
    changed_fqns=[
        ChangedFQN(
            fqn=FQN.from_dotted("app.routes.users"),
            change_type="modified",
            file_path="app/routes/users.py",
            enclosing_module=FQN.from_dotted("app.routes.users"),
        ),
        ChangedFQN(
            fqn=FQN.from_dotted("app.routes.auth"),
            change_type="modified",
            file_path="app/routes/auth.py",
            enclosing_module=FQN.from_dotted("app.routes.auth"),
        ),
    ],
)


def main() -> None:
    config = load_config()
    repo_cfg = config.get_repo("flask")
    repo_path = REPO_ROOT / "repos" / "flask"

    print("=" * 60)
    print("CPT DETECT E2E SMOKE TEST")
    print("=" * 60)

    # Step 1: seed build via CLI (includes Neo4j persist)
    print("\n[seed] running cpt seed build --repo flask")
    result = subprocess.run(
        [sys.executable, "-m", "cli.main", "seed", "build", "--repo", "flask"],
        cwd=REPO_ROOT / "app",
    )
    if result.returncode != 0:
        print(f"[seed] FAILED with exit code {result.returncode}")
        sys.exit(1)
    print("[seed] done")

    # Step 2: build in-memory ADG + constraints (for detect)
    print("\n[detect] parse_repo")
    adg = parse_repo(repo_path)
    print(f"  {len(adg.nodes)} nodes, {len(adg.edges)} edges")

    print("[detect] extract ADR constraints")
    all_constraints = []
    for r in extract_all_adrs(repo_path, repo_cfg.adr_dir, config.langextract):
        all_constraints.extend(r.constraints)
    print(f"  {len(all_constraints)} constraints")

    print("[detect] merge_constraints")
    merged = merge_constraints(adg, all_constraints, config=config.langextract)
    print(f"  {len(merged.constraint_edges)} constraint_edges")

    # Step 3: run CPT detect with mock diff
    print("\n[detect] CPT detect (mock diff)")
    for cf in MOCK_DIFF.changed_fqns:
        print(f"    {cf.change_type:10s} {cf.fqn}")

    cpt_result = cpt_detect(MOCK_DIFF, merged)

    print(f"\n  neighborhood: {len(cpt_result.neighborhood)}")
    print(f"  violations:   {len(cpt_result.violations)}")
    print(f"  orphans:      {len(cpt_result.orphans)}")

    if cpt_result.violations:
        print("\n--- Violations ---")
        for v in cpt_result.violations:
            print(f"  [{v.constraint.adr_id}] {v.constraint.predicate.value}")
            print(f"    subject:  {v.constraint.subject}")
            print(f"    object:   {v.constraint.object}")
            print(f"    changed:  {v.changed_fqn}")
            print(f"    evidence: {v.evidence}")
    else:
        print("\n--- No violations ---")

    if cpt_result.orphans:
        print("\n--- Orphan Constraints ---")
        for o in cpt_result.orphans:
            print(f"  [{o.adr_id}] {o.subject} -[{o.predicate.value}]-> {o.object}")
    else:
        print("\n--- No orphans ---")

    print("\nDone.")


if __name__ == "__main__":
    main()