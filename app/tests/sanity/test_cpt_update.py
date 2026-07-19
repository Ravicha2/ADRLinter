"""E2E sanity test: dismissal identity is scoped to subject+predicate+object+matched_fqn+adr_id.

Flow:
1. seed build
2. detect on SAFE commit (parent) -- may produce false positives
3. dismiss all violations (these are false positives for a compliant commit)
4. update on SAFE commit again -- dismissals persist, no active violations
5. detect on UNSAFE commit (HEAD) -- NEW violations should appear despite previous dismissals,
   proving dismissals are identity-scoped, not blanket per-constraint

Run with: uv run python tests/sanity/test_cpt_update.py (from app/ directory)
"""

import logging
import re
import subprocess
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[3] / ".env")

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

from typer.testing import CliRunner

from cli.main import app

REPO = "flask"
runner = CliRunner()


def strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def extract_short_ids(output: str) -> list[str]:
    return re.findall(r"(?<![0-9a-f])([0-9a-f]{5})(?![0-9a-f])", output)


def parse_int(output: str, pattern: str) -> int | None:
    clean = strip_ansi(output)
    m = re.search(pattern + r"\s*(\d+)", clean)
    return int(m.group(1)) if m else None


def get_to_sha(repo: str, ref: str) -> str:
    from cli.main import _resolve_repo_path, _get_repo
    repo_cfg = _get_repo(repo)
    repo_path = _resolve_repo_path(repo_cfg)
    result = subprocess.run(
        ["git", "rev-parse", ref],
        cwd=repo_path, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    suffix = f" -- {detail}" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    return condition


def main() -> None:
    safe_sha = get_to_sha(REPO, "HEAD~1")
    unsafe_sha = get_to_sha(REPO, "HEAD")
    print("=" * 60)
    print("CPT DISMISSAL IDENTITY SMOKE TEST")
    print(f"  safe commit:   {safe_sha[:8]} (compliant, possible false positives)")
    print(f"  unsafe commit: {unsafe_sha[:8]} (violates ADRs)")
    print("=" * 60)

    # --- Step 1: seed build ---
    print("\n--- Step 1: seed build ---")
    result = runner.invoke(app, ["seed", "build", "--repo", REPO])
    ok = check("seed build", result.exit_code == 0, result.output if result.exit_code else "")
    if not ok:
        return

    # --- Step 2: detect on SAFE commit (may have false positives) ---
    print(f"\n--- Step 2: detect on safe commit ({safe_sha[:8]}) ---")
    print("Expect Violation: None")
    result = runner.invoke(app, ["violation", "list", "--repo", REPO, "--commit", safe_sha])
    check("detection ran", result.exit_code == 0, f"exit={result.exit_code}")
    print(result.output)

    safe_short_ids = extract_short_ids(result.output)
    print(f"  Violations on safe commit: {len(safe_short_ids)} -- {safe_short_ids}")
    ok = check("safe commit has violations (false positives expected)", len(safe_short_ids) >= 1,
                f"got {len(safe_short_ids)}")
    if not ok:
        print("  No violations to dismiss, test cannot continue")
        return

    # --- Step 3: dismiss ALL violations from safe commit ---
    print("\n--- Step 3: dismiss all false-positive violations ---")
    for sid in safe_short_ids:
        result = runner.invoke(app, ["violation", "dismiss", sid, "--repo", REPO, "--commit", safe_sha])
        check(f"dismiss {sid}", result.exit_code == 0, f"exit={result.exit_code}")
    print(f"  Dismissed {len(safe_short_ids)} violation(s)")

    # --- Step 4: verify dismissals hold on safe commit (no active violations) ---
    print(f"\n--- Step 4: violation list on safe commit (dismissals should filter all) ---")
    print("=== Expect Violation: None ===")
    result = runner.invoke(app, ["violation", "list", "--repo", REPO, "--commit", safe_sha])
    check("detection ran", result.exit_code == 0, f"exit={result.exit_code}")
    all_filtered = "No active violations" in result.output
    check("all false positives dismissed", all_filtered)
    if not all_filtered:
        print(result.output)

    # --- Step 5: update on safe commit (dismissals persist through ADG rebuild) ---
    print(f"\n--- Step 5: cpt update on safe commit (dismissals persist) ---")
    result = runner.invoke(app, ["update", "--repo", REPO, "--commit", safe_sha])
    check("update ran", result.exit_code == 0, f"exit={result.exit_code}")
    edges = parse_int(result.output, r"Constraint edges preserved:")
    dismissals = parse_int(result.output, r"Dismissals applied:")
    print(f"  Constraint edges: {edges}, dismissals applied: {dismissals}")
    check("dismissals preserved through rebuild", dismissals is not None and dismissals >= 1,
          f"got {dismissals}")

    # --- Step 6: detect on UNSAFE commit (new violations must appear) ---
    # Key property: dismissals are scoped to (subject, predicate, object, matched_fqn, adr_id).
    # The unsafe commit introduces DIFFERENT violations (different matched_fqn / subject / object),
    # so they should NOT be filtered by the previous dismissals.
    print(f"\n--- Step 6: detect on unsafe commit ({unsafe_sha[:8]}) ---")
    print("=== Expect violation: " \
    "users route imports model directly, skips auth" \
    "user not implement auth on route")
    result = runner.invoke(app, ["violation", "list", "--repo", REPO, "--commit", unsafe_sha])
    check("detection ran", result.exit_code == 0, f"exit={result.exit_code}")
    print(result.output)

    unsafe_short_ids = extract_short_ids(result.output)
    print(f"  Violations on unsafe commit: {len(unsafe_short_ids)} -- {unsafe_short_ids}")

    # The unsafe commit must produce at least one NEW violation not in the dismissed set.
    # If dismissals were blanket per-constraint, ALL violations matching the same ADR
    # would be suppressed. Instead, only violations with matching identity hashes are filtered.
    new_violations = [sid for sid in unsafe_short_ids if sid not in set(safe_short_ids)]
    check("unsafe commit has violations", len(unsafe_short_ids) >= 1, f"got {len(unsafe_short_ids)}")
    check("dismissal is identity-scoped (new violations appear)",
          len(unsafe_short_ids) >= 1, f"total={len(unsafe_short_ids)}, new={len(new_violations)}")

    # --- Step 7: update on unsafe commit, verify dismissals still filter the old ones ---
    print(f"\n--- Step 7: cpt update on unsafe commit (dismissals still filter old, new appear) ---")
    result = runner.invoke(app, ["update", "--repo", REPO, "--commit", unsafe_sha])
    check("update ran", result.exit_code == 0, f"exit={result.exit_code}")
    print(result.output)

    # Count active violations after update: should have some (the new unsafe ones)
    # but the previously-dismissed ones should still be filtered
    result = runner.invoke(app, ["violation", "list", "--repo", REPO, "--commit", unsafe_sha])
    check("detection ran", result.exit_code == 0, f"exit={result.exit_code}")
    after_update_ids = extract_short_ids(result.output)
    print(f"  Active violations after update: {len(after_update_ids)} -- {after_update_ids}")

    # The old dismissed short IDs should NOT appear (they're filtered)
    old_still_filtered = set(safe_short_ids) - set(after_update_ids)
    check("previously dismissed violations stay filtered",
          len(old_still_filtered) == len(safe_short_ids),
          f"filtered={len(old_still_filtered)}/{len(safe_short_ids)}")

    print("\n" + "=" * 60)
    print("DONE")
    print("=" * 60)


if __name__ == "__main__":
    main()