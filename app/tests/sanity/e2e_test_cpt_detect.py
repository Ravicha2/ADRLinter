"""E2E sanity test: seed build via CLI + mock diff → CPT detect → print results.

Run with: uv run python tests/sanity/test_cpt_detect.py
"""

import logging
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[3] / ".env")

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

from cli.config import load_config
from services.adg import parse_repo
from services.adg.merge import merge_constraints
from services.cpt.engine import detect as cpt_detect
from services.cpt.diff_processor import augment_adg, process_diff
from services.extract import extract_all_adrs
from services.models import (
    CommitDiff,
    FileChange,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
FLASK_REPO = REPO_ROOT / "repos" / "flask"


def build_mock_diff() -> CommitDiff:
    """Build a mock CommitDiff using real repo source + a small modification."""
    users_path = "app/routes/users.py"
    auth_path = "app/routes/auth.py"

    # Real source (unchanged "parent" version)
    users_old = (FLASK_REPO / users_path).read_bytes()
    auth_old = (FLASK_REPO / auth_path).read_bytes()

    # "New" version: add a function to users.py (triggers ADR 001: route depends on models)
    users_new = users_old + b"\n\ndef create_user_route(data):\n    user = User.create(data)\n    return jsonify(user)\n"
    # "New" version: add a function to auth.py
    auth_new = auth_old + b"\n\ndef refresh_token_route():\n    return jsonify({'token': 'new'})\n"

    return CommitDiff(
        commit_sha="deadbeef",
        parent_sha="cafebabe",
        changed_files=[
            FileChange(path=users_path, status="modified"),
            FileChange(path=auth_path, status="modified"),
        ],
        file_contents={
            users_path: users_new,
            auth_path: auth_new,
        },
        parent_contents={
            users_path: users_old,
            auth_path: auth_old,
        },
    )


def main() -> None:
    config = load_config()
    repo_cfg = config.get_repo("flask")
    repo_path = FLASK_REPO

    print("=" * 60)
    print("CPT DETECT E2E SMOKE TEST")
    print("=" * 60)

    # Step 1: seed build via CLI (includes Neo4j persist)
    print("\n[seed] running cpt seed build --repo flask")
    from typer.testing import CliRunner
    from cli.main import app
    runner = CliRunner()
    result = runner.invoke(app, ["seed", "build", "--repo", "flask"])
    if result.exit_code != 0:
        print(f"[seed] FAILED with exit code {result.exit_code}")
        if result.exception:
            import traceback
            traceback.print_exception(type(result.exception), result.exception, result.exception.__traceback__)
        print(result.output)
        raise SystemExit(1)
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

    # Step 3: parse mock code into changed FQNs via diff_processor
    print("\n[detect] process mock diff -> changed FQNs")
    mock_diff = build_mock_diff()
    diff_result = process_diff(mock_diff)
    for cf in diff_result.changed_fqns:
        print(f"    {cf.change_type:10s} {cf.fqn}")

    # Step 4: augment ADG with new code so BFS can expand from changed FQNs
    print("[detect] augment ADG with new code from diff")
    before_nodes = len(merged.nodes)
    before_edges = len(merged.edges)
    augment_adg(merged, mock_diff)
    print(f"  {len(merged.nodes) - before_nodes} new nodes, {len(merged.edges) - before_edges} new edges")

    # Step 5: run CPT detect
    print("\n[detect] CPT detect")
    cpt_result = cpt_detect(diff_result, merged)

    print(f"  neighborhood: {len(cpt_result.neighborhood)}")
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