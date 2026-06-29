# Issue #35 Design Specification: Per-Source BFS and REQUIRES Semantics

## Goal

Replace the inefficient per-pair BFS reachability check (`_reachable(start, target, adjacency, kinds)`) with a per-source BFS (`_reachable_nodes(start, adjacency, kinds)`). Update `check_structural_predicates` (`PROHIBITS`) and `check_change_triggered_predicates` (`REQUIRES`) to utilize the new reachability set. Fix the `REQUIRES` semantics bug so that exactly one violation is produced per subject when zero matching objects are reachable, using the constraint's object pattern in the evidence string.

## Context & Problem Statement

1. **Performance Bottleneck (`PROHIBITS`)**: Currently, `_reachable` evaluates reachability for every `(subject, object)` pair independently, resulting in `|S| × |O|` full BFS traversals.
2. **Semantic Flaw (`REQUIRES`)**: Currently, `REQUIRES` evaluates every `(subject, object)` pair individually. When a wildcard object pattern matches multiple FQNs in the codebase, `check_change_triggered_predicates` emits a violation for every unreachable object match, even if the subject successfully depends on one of the matching objects.
3. **Baseline Test Inconsistency**: In Issue #34, the k-hop bubble was removed, switching CPT to operate on the full ADG. This caused `test_detect_specificity_resolution` to fail because `app.auth` (matching `app.*`) correctly triggers a `PROHIBITS` violation under full-ADG evaluation, whereas the test asserted `len(prohibit_violations) == 0`.

## Architecture & Data Flow

### 1. Per-Source Reachability Function
Replace `_reachable` in `app/services/cpt/engine.py` with `_reachable_nodes(start: str, adjacency: dict[str, list[Edge]], kinds: set[str]) -> set[str]`.
- **Logic**: Performs a single BFS traversal starting from `start`, traversing edges whose `kind` is in `kinds`.
- **Return**: Returns a `set[str]` containing all reachable node FQN strings (excluding `start` itself, unless a self-loop or cycle exists).

### 2. `check_structural_predicates` (`PROHIBITS`)
- For each `subject_fqn`, call `_reachable_nodes` once to get `reachable_set`.
- For each `object_fqn`, check `if str(object_fqn) in reachable_set`.
- If reachable, emit a `Violation` with evidence `"{subject_str} {label} {object_str}"`.
- **Result**: Exactly the same `(subject, object)` violation pairs are emitted as before, but with `|S|` BFS traversals instead of `|S| × |O|`.

### 3. `check_change_triggered_predicates` (`REQUIRES`)
- For each `subject_fqn` in `relevant_subjects`, call `_reachable_nodes` once to get `reachable_set`.
- Check if `any(str(object_fqn) in reachable_set for object_fqn, _ in matched_constraint.object_matches)`.
- If no matching objects are reachable, determine the highest match status among `subject_status` and all `object_status` values (using `_PRIORITY`).
- Emit exactly one `Violation` per `(changed.fqn, constraint, subject_fqn)` with evidence `f"{subject_str} {label} any module matching {matched_constraint.constraint.object}"`.

### 4. Test Updates
- **`TestCheckStructuralPredicates`**: Update unit tests to align with `_reachable_nodes` behavior and verify `PROHIBITS` functionality.
- **`TestCheckChangeTriggeredPredicates`**: Update unit tests to verify the new `REQUIRES` semantics (one violation when zero objects reachable) and the new evidence string format.
- **`TestDetect.test_detect_specificity_resolution`**: Update the test assertion to specifically verify that `app.middleware` has 0 `PROHIBITS` violations (proving successful specificity resolution), while allowing `app.auth` to retain its valid structural violation.

## Error Handling & Edge Cases

- **Empty Object Matches**: Handled by existing `match_constraints` logic (filtered as orphans).
- **Self-Loops / Cycles**: Handled correctly by `visited` set tracking within `_reachable_nodes`.
- **Match Status Priority**: Correctly computes the highest priority across the subject match status and all object match statuses for the single emitted `REQUIRES` violation.

## Verification Plan

- Run `uv run pytest` to ensure all 405 tests pass successfully (excluding the 10 Neo4j integration tests which require a running Neo4j instance, and the flaky LLM eval test).
- Verify that `test_detect_specificity_resolution`, `TestCheckStructuralPredicates`, and `TestCheckChangeTriggeredPredicates` pass flawlessly.
