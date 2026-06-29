# 9. Sound CPT Reachability

Date: 2026-06-26

## Status

Accepted

## Context

The k-hop BFS bubble (`bfs_neighborhood`, `k=3`) restricts CPT to a subgraph around changed FQNs. This restriction is unsound:

- **False negatives for PROHIBITS.** A `prohibits_dependency` whose subject scope contains the changed FQN but whose object scope sits 4+ hops away is filtered out before reachability evaluation. A real violation at distance 4 is never detected.
- **False positives for REQUIRES.** If the only path from a subject to its required object passes through a node outside the bubble, the in-bubble reachability check returns False and a spurious violation fires.

Additionally, the current per-pair BFS (`_reachable`) runs `|S| × |O|` calls per PROHIBITS constraint and `|changed| × |relevant_subjects| × |O|` per REQUIRES evaluation, which is correct but wasteful. REQUIRES also has a semantic bug: it emits one violation per (subject, object) pair where no path exists, producing redundant violations when a wildcard object pattern matches multiple FQNs. The correct semantics is one violation per (subject, constraint) when zero objects are reachable.

## Decision

### 1. Remove k-hop bubble

Delete `bfs_neighborhood` and the `k` parameter from `detect()`. Constraint matching and reachability operate on the full ADG node set and edge list, not a localized subgraph.

### 2. Full-ADG constraint matching

`match_constraints` iterates over all ADG node FQNs, not just those in a neighborhood. A constraint is matched if both its subject and object patterns match at least one FQN in the codebase, regardless of topological distance from changed code.

### 3. Full-ADG reachability

`_build_adjacency` receives the full `adg.edges` list. Reachability checks traverse the entire code graph.

### 4. PROHIBITS: per-source BFS, full traversal

For each subject match, run one BFS and check which object matches are in the reachable set. Emit one violation per (subject, object) pair where a path exists. Same pairs as the current code, but `|S|` BFS calls instead of `|S| × |O|`.

### 5. REQUIRES: per-source BFS, one violation when no object reachable

For each (changed_fqn, constraint) pair with relevant subjects, run one BFS per relevant subject and check whether any object match is in the reachable set. Emit one violation per (changed_fqn, constraint, relevant_subject) when zero objects are reachable.

### 6. REQUIRES evidence format

Evidence string uses the constraint's object pattern: `"app.api.users has no dependency on any module matching app.auth.*"`, not a specific unreachable FQN.

### 7. Remove CPTResult.neighborhood

The `neighborhood` field is removed from `CPTResult`. Debug/audit logging can be added later.

## Supersedes

- **ADR 006 section 2** ("Traversal: both directions, 3-hop limit"): k-hop traversal removed entirely. Reachability uses the full ADG.
- **ADR 005 section 1** (Phase 1: k-hop BFS): Phase 1 no longer produces a neighborhood. The two-phase structure (AST walk + constraint evaluation) is preserved, but Phase 1 now provides the full ADG, not a localized subgraph.

### 8. Open-graph ADG: edges to external FQNs

IMPORTS/CALLS/INHERITS edges now point to FQNs that may not exist as nodes in the ADG (e.g., `flask`, `django`). Previously, `_record_import` and `walk_imports` only created edges when the target FQN was in `known_fqns`. This filter was removed so that `prohibits_dependency` can detect violations like `import flask` where the target is an external library.

External FQNs that appear as import targets but are not in the ADG node set are represented as `FQNKind.EXTERNAL` nodes with `file_path=""`, `line_start=-1`, `line_end=-1`.

### 9. Synthetic parent-package nodes and CONTAINS edges

`parse_repo` now creates virtual parent-package FQN nodes (e.g., `app` from `app.routes.users`) and CONTAINS edges linking them to their children. These nodes have `file_path=""` and zero-valued line/byte fields; they represent package structure, not source files.

This is required for prefix-BFS: when a constraint subject is `app.routes.*`, BFS starts from the `app.routes` module node and walks CONTAINS edges down to `app.routes.users`, `app.routes.auth`, etc., then follows their IMPORTS/CALLS edges. Without these nodes, `app.routes` may not exist in the ADG (only leaf modules like `app.routes.users` do), and the traversal cannot start.

`augment_adg` duplicates this logic for diff-time augmentation, creating the same synthetic nodes and CONTAINS edges for files added/modified by a commit.

### 10. CONTAINS in BFS edge kinds

Both `check_structural_predicates` and `check_change_triggered_predicates` include `CONTAINS` in their BFS edge kind sets:

- `PROHIBITS_DEPENDENCY`: `{CONTAINS, IMPORTS, CALLS, INHERITS}` (was `{IMPORTS, CALLS, INHERITS}`)
- `REQUIRES_DEPENDENCY`: `{CONTAINS, IMPORTS, CALLS, INHERITS}` (was `{IMPORTS, CALLS, INHERITS}`)
- `REQUIRES_IMPLEMENTATION` / `PROHIBITS_IMPLEMENTATION`: `{CONTAINS, CALLS}` (unchanged semantics, explicit CONTAINS added)

This enables BFS to descend through package hierarchy before following dependency edges, which is necessary for prefix-based subjects.

## Consequences

- Soundness: no false negatives from bubble exclusion, no false positives from bubble-truncated paths.
- Performance: full-ADG matching and reachability on large codebases is unmeasured. This is accepted for the correctness milestone; optimization (indexing, 2-hop) is deferred.
- API: `detect()` loses the `k` parameter. `CPTResult` loses `neighborhood`. `match_constraints` loses the `neighborhood` parameter.
- ADG semantics: the graph is now open (edges to external FQNs), not closed (edges only between known FQNs). Downstream consumers must handle `FQNKind.EXTERNAL` nodes.
- Synthetic nodes: package-level FQNs with `file_path=""` are virtual and must not be confused with real source modules.