"""E2E sanity test: seed build via CLI + mock diff → CPT detect → print results.

Run with: uv run python tests/sanity/test_cpt_detect.py
"""

import logging
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[3] / ".env")

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

import sys
from cli.config import load_config
from cli.main import _resolve_repo_path, app
from services.adg import parse_repo
from services.adg.merge import merge_constraints
from services.cpt.engine import detect as cpt_detect
from services.cpt.diff_processor import augment_adg, process_diff
from services.extract import extract_all_adrs
from services.extract.engine import derive_package_context
from services.models import (
    CommitDiff,
    FileChange,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def build_mock_diff(repo_name: str, repo_path: Path) -> CommitDiff:
    """Build a mock CommitDiff using real repo source + a small modification."""
    if repo_name == "flask":
        # ==============================================================================
        # EXPECTED VIOLATIONS FOR 'flask' REPO:
        # 1. [001] prohibits_dependency (app.routes.* -> app.models.*):
        #    - Evidence: create_user_route directly imports/uses User.create from models.
        # 2. [001] requires_dependency (app.routes.* -> app.services.*):
        #    - Evidence: create_user_route does not call or depend on the required service layer.
        # ==============================================================================
        users_path = "app/routes/users.py"
        auth_path = "app/routes/auth.py"

        users_old = (repo_path / users_path).read_bytes()
        auth_old = (repo_path / auth_path).read_bytes()

        users_new = users_old + b"\n\ndef create_user_route(data):\n    user = User.create(data)\n    return jsonify(user)\n"
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

    elif repo_name == "django":
        # ==============================================================================
        # EXPECTED VIOLATIONS FOR 'django' REPO:
        # 1. [001] requires_dependency (users.* -> project.*):
        #    - Context: LangExtract maps the ADR 001 constraints between the two top-level packages 'users' and 'project'.
        #    - Evidence: The newly added view function `users.views.create_user_view` does not import or depend on the required `project.*` modules.
        # ==============================================================================
        views_path = "users/views.py"
        views_old = (repo_path / views_path).read_bytes()
        views_new = views_old + b"\n\ndef create_user_view(request):\n    user = User.objects.create(name='test')\n    return JsonResponse({'id': user.id})\n"

        return CommitDiff(
            commit_sha="deadbeef",
            parent_sha="cafebabe",
            changed_files=[
                FileChange(path=views_path, status="modified"),
            ],
            file_contents={
                views_path: views_new,
            },
            parent_contents={
                views_path: views_old,
            },
        )

    # ==============================================================================
    # EXPECTED VIOLATIONS FOR 'openlobby' (OR OTHER REPOS):
    # For openlobby or any other repo, we dynamically find a couple of .py files to modify.
    # 1. [0010] prohibits_dependency (openlobby.* -> flask):
    #    - Evidence: We inject `import flask` into the first file, violating ADR 0010 (Replace Flask with Django).
    # 2. [0010, 0008, 0012] requires_dependency:
    #    - Evidence: If the modified files lack dependencies on required modules (django, pytest, postgresql), those missing dependencies will also be flagged.
    # ==============================================================================
    _INFRA_NAMES = {"__init__.py", "settings.py", "urls.py", "wsgi.py", "asgi.py",
                     "apps.py", "admin.py", "conftest.py", "setup.py", "manage.py"}
    py_files = sorted(
        [
            p.relative_to(repo_path) for p in repo_path.rglob("*.py")
            if p.is_file()
            and "tests" not in p.parts
            and "venv" not in p.parts
            and p.name not in _INFRA_NAMES
            # Exclude root-level scripts — their FQNs fall outside the
            # constrained package namespace and won't trigger violations.
            and p.parent != repo_path
        ],
        # Prefer deeper files (actual app code over infra), then sort
        # alphabetically for determinism.
        key=lambda p: (-len(p.parts), str(p)),
    )
    if not py_files:
        return CommitDiff(commit_sha="deadbeef", parent_sha="cafebabe", changed_files=[], file_contents={}, parent_contents={})

    file1_path = str(py_files[0])
    file1_old = (repo_path / file1_path).read_bytes()
    file1_new = file1_old + b"\n\n# mock diff modification\nimport flask\n\ndef mock_additional_function():\n    pass\n"

    changed_files = [FileChange(path=file1_path, status="modified")]
    file_contents = {file1_path: file1_new}
    parent_contents = {file1_path: file1_old}

    if len(py_files) > 1:
        file2_path = str(py_files[1])
        file2_old = (repo_path / file2_path).read_bytes()
        file2_new = file2_old + b"\n\n# second mock modification\nimport django\n\ndef mock_second_function():\n    pass\n"
        changed_files.append(FileChange(path=file2_path, status="modified"))
        file_contents[file2_path] = file2_new
        parent_contents[file2_path] = file2_old

    return CommitDiff(
        commit_sha="deadbeef",
        parent_sha="cafebabe",
        changed_files=changed_files,
        file_contents=file_contents,
        parent_contents=parent_contents,
    )


def main() -> None:
    repo_name = sys.argv[1] if len(sys.argv) > 1 else "flask"
    config = load_config()
    repo_cfg = config.get_repo(repo_name)
    repo_path = _resolve_repo_path(repo_cfg)

    print("=" * 60)
    print(f"CPT DETECT E2E SMOKE TEST: {repo_name}")
    print("=" * 60)

    # Step 1: seed build via CLI (includes Neo4j persist)
    print(f"\n[seed] running cpt seed build --repo {repo_name}")
    from typer.testing import CliRunner
    runner = CliRunner()
    result = runner.invoke(app, ["seed", "build", "--repo", repo_name])
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
    package_context = derive_package_context(adg)

    print("[detect] extract ADR constraints")
    all_constraints = []
    for r in extract_all_adrs(repo_path, repo_cfg.adr_dir, config.langextract, package_context=package_context):
        all_constraints.extend(r.constraints)
    print(f"  {len(all_constraints)} constraints")

    print("[detect] merge_constraints")
    merged = merge_constraints(adg, all_constraints)
    print(f"  {len(merged.constraint_edges)} constraint_edges")

    # Step 3: parse mock code into changed FQNs via diff_processor
    print("\n[detect] process mock diff -> changed FQNs")
    mock_diff = build_mock_diff(repo_name, repo_path)
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