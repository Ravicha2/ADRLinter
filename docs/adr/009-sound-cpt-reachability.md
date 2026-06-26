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

## Consequences

- Soundness: no false negatives from bubble exclusion, no false positives from bubble-truncated paths.
- Performance: full-ADG matching and reachability on large codebases is unmeasured. This is accepted for the correctness milestone; optimization (indexing, 2-hop) is deferred.
- API: `detect()` loses the `k` parameter. `CPTResult` loses `neighborhood`. `match_constraints` loses the `neighborhood` parameter.